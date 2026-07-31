"""The policy file: what is gated, by which resolver, against which declared ceilings.

Two properties are load-bearing.

**Extra keys are an error.** A typo in a tool name or a pointer must not be silently ignored,
because a silently-ignored gate is an ungated tool, and the operator would have no way to notice.

**Ceilings are never inferred.** `neti propose` prints suggestions from observed traffic for a human
to edit in; nothing computed ever becomes a ceiling on its own. That is what keeps "absolute limits,
not learned baselines" true, and it is the seam a security reviewer will probe first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from neti.core.budget import BudgetRule
from neti.core.canonical import canonical_bytes
from neti.core.pointer import PointerError, resolve_pointer, split_unit_suffix
from neti.core.types import Band, Ceiling, Frozen, ProposedCall, sorted_bands
from neti.core.units import Unit
from neti.core.verdict import Mode, ModeValue, Verdict, VerdictValue

__all__ = ["GateSpec", "Policy", "PolicyError", "ToolSpec", "load_policy"]


class PolicyError(ValueError):
    """The policy file is not usable. Always fatal at load: a half-understood gate is worse than
    none, because the operator believes they are covered."""


class GateSpec(Frozen):
    """One gated argument: which resolver reads it, and what the operator declared about it."""

    resolver: str
    unit: Unit | None = None
    bands: tuple[Band, ...] = ()
    on_unresolved: VerdictValue = Verdict.BLOCK
    on_unbounded: VerdictValue = Verdict.CONFIRM
    breakdown_bands: dict[str, tuple[Band, ...]] = Field(default_factory=dict)
    required: bool = True
    """Whether an absent argument is a problem. True by default: a gated parameter that the agent
    did not supply is a call we cannot size, not a call with nothing in it."""

    @model_validator(mode="after")
    def _sort_bands(self) -> GateSpec:
        object.__setattr__(self, "bands", sorted_bands(self.bands))
        object.__setattr__(
            self,
            "breakdown_bands",
            {k: sorted_bands(v) for k, v in self.breakdown_bands.items()},
        )
        return self

    def ceiling(self, unit: Unit) -> Ceiling:
        return Ceiling(
            unit=self.unit or unit,
            bands=self.bands,
            on_unresolved=self.on_unresolved,
            on_unbounded=self.on_unbounded,
            breakdown_bands=self.breakdown_bands,
        )

    @property
    def has_ceiling(self) -> bool:
        """A gate with no bands still resolves and records — that is observe-mode value — but it
        can never block. `neti inventory` calls this out rather than letting it look configured."""
        return bool(self.bands) or any(self.breakdown_bands.values())


class ToolSpec(Frozen):
    gate: dict[str, GateSpec] = Field(default_factory=dict)
    sensitive: bool = True


class Policy(Frozen):
    version: int = 1
    mode: ModeValue = Mode.OBSERVE
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tools: dict[str, ToolSpec] = Field(default_factory=dict)
    session_budgets: tuple[BudgetRule, ...] = ()
    unknown_tool: VerdictValue = Verdict.ALLOW
    """An ungated tool is out of scope, not denied (SCOPE.md NC-09). Failing closed on everything
    undeclared would make the gate unusable on day one, and operators would turn it off."""

    on_approval_unavailable: VerdictValue = Verdict.BLOCK
    """What a `CONFIRM` does when no human can be asked.

    Declared rather than inferred, for the same reason `on_unresolved` is: the gate does not get to
    invent a decision when the world does not answer.

    The default is not a safety flourish. A `CONFIRM` already means "this does not proceed without a
    human", so `block` here is *exactly* what an install with no control plane does — which makes
    the whole tier boundary honest: a control plane can only ever make a decision more permissive,
    and only via a named human. Unreachable, absent and unpaid are all the same behaviour, so nobody
    takes on availability risk by paying for approvals.

    It is inside the digest because it changes decisions. Where the approver *lives* does not, and
    lives in `~/.neti/credentials.toml` instead — otherwise the digest would differ per environment
    and stop being a statement about the ceilings."""

    @model_validator(mode="after")
    def _check_pointers(self) -> Policy:
        for tool, spec in self.tools.items():
            for pointer in spec.gate:
                base, _ = split_unit_suffix(pointer)
                if base and not base.startswith("/"):
                    raise PolicyError(
                        f"{tool}: gate key {pointer!r} must be a JSON pointer starting with '/'"
                    )
        return self

    def is_gated(self, tool: str) -> bool:
        return tool in self.tools and bool(self.tools[tool].gate)

    def gate_specs(self, tool: str) -> dict[str, GateSpec]:
        return self.tools[tool].gate if tool in self.tools else {}

    def targets(self, call: ProposedCall) -> list[tuple[str, str | None, GateSpec]]:
        """Extract `(pointer, target, spec)` for each gated argument of this call.

        A target of `None` means the argument was absent. That is deliberately not the same as an
        empty one, and `decide` will route it through `on_unresolved` — we cannot size a call whose
        target we never saw.
        """
        out: list[tuple[str, str | None, GateSpec]] = []
        for pointer, spec in self.gate_specs(call.tool).items():
            try:
                value = resolve_pointer(call.args, pointer)
            except PointerError:
                out.append((pointer, None, spec))
                continue
            out.append((pointer, None if value is None else str(value), spec))
        return out

    def digest(self) -> str:
        """Content address of the effective policy.

        Goes into every decision record: "identical input, identical output" is undefined without
        pinning which policy produced the verdict.
        """
        from hashlib import blake2b

        payload = self.model_dump(mode="json", exclude_none=False)
        return blake2b(canonical_bytes(payload), digest_size=16).hexdigest()


def load_policy(path: str | Path) -> Policy:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path}: top level must be a mapping")

    data = _normalise(data)
    try:
        return Policy.model_validate(data)
    except PolicyError:
        raise
    except Exception as exc:  # pydantic ValidationError
        raise PolicyError(f"{path}: {exc}") from exc


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    """Accept the operator-friendly YAML shape and hand pydantic the model shape.

    The two differences are deliberate ergonomics: bands are written as `{above: N, verdict: block}`
    lists in the order a human thinks of them, and session budgets name tools as a list rather than
    a set.
    """
    out = dict(data)

    budgets = out.pop("session_budgets", []) or []
    rules = []
    for i, rule in enumerate(budgets):
        if not isinstance(rule, dict):
            raise PolicyError(f"session_budgets[{i}] must be a mapping")
        rules.append(
            {
                "tools": frozenset(rule.get("tools", [])),
                "unit": rule.get("unit"),
                "bands": tuple(rule.get("bands", ()) or ()),
                "window": rule.get("window", "session"),
            }
        )
    out["session_budgets"] = tuple(rules)

    defaults = out.pop("defaults", {}) or {}
    if "unknown_tool" in defaults:
        out["unknown_tool"] = defaults["unknown_tool"]
    if "on_approval_unavailable" in defaults:
        out["on_approval_unavailable"] = defaults["on_approval_unavailable"]
    if "on_unresolved" in defaults:
        for spec in (out.get("tools") or {}).values():
            for gate in (spec.get("gate") or {}).values():
                gate.setdefault("on_unresolved", defaults["on_unresolved"])

    return out
