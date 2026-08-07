"""Declaring a ceiling without destroying the file you declared it in.

The console's policy page was read-only, and every row on a fresh install said *no ceiling —
resolves and records, cannot block*. That is an accurate description of a gate that cannot yet do
the thing the product is for, shown to somebody with no obvious way to change it.

**Why this is a text edit and not a YAML round trip.** `examples/coding-agent.yaml` is more comment
than configuration, and the comments are most of its value — why `Bash` gates two hooks, why
`on_unresolved: allow` is the difference between a gate people keep and one they remove on Friday,
what is deliberately not there. `yaml.safe_load` then `yaml.dump` would parse all of that away and
write back a correct file nobody could read again. So this finds the gate block in the raw text and
splices lines into it, leaving every other byte exactly as it was.

**Why it plans before it writes.** `insight/install.py` established the rule for a file somebody
else owns: merge rather than replace, be idempotent, show the diff, back it up. A policy is that
file twice over — it decides what an agent may do, and its ceilings are the product's only claim.
So this returns a plan with the before and after, and writing is a second, explicit call.

The one thing it will not do is choose a number. `neti propose` prints suggestions from observed
traffic for a human to edit in, and `config/policy.py` opens by saying nothing computed ever becomes
a ceiling on its own. This is the same rule: it writes the number it was handed, and nothing here
ever invents one.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neti.core.verdict import Verdict

__all__ = [
    "CeilingEdit",
    "PolicyEditError",
    "PresetEdit",
    "SensitiveEdit",
    "apply_ceiling",
    "apply_preset",
    "apply_sensitive",
    "plan_ceiling",
    "plan_preset",
    "plan_sensitive",
]

_VERDICTS = {v.name.lower() for v in Verdict}


class PolicyEditError(ValueError):
    """The edit cannot be made safely. Always fatal: a policy half-edited is worse than unedited,
    because the operator believes the ceiling is there."""


@dataclass
class CeilingEdit:
    path: Path
    tool: str
    pointer: str
    bands: list[dict[str, Any]]
    before: str = ""
    after: str = ""
    replaced: bool = False
    """True when the gate already declared bands and this replaces them. Surfaced because
    overwriting a ceiling somebody committed is a different act from adding a first one."""

    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self) -> str:
        """Unified, so what is added and what is replaced can both be read before agreeing."""
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=str(self.path),
                tofile=f"{self.path} (proposed)",
                n=4,
            )
        )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find_block(lines: list[str], key: str, start: int, end: int, depth: int) -> tuple[int, int]:
    """`(index of the `key:` line, index one past its block)`, searched within `[start, end)`.

    Depth-aware, because `/file_path` appears under four different tools and matching the first one
    would silently write the ceiling onto the wrong gate — a failure that produces a valid file and
    the wrong behaviour, which is the worst kind this can have.
    """
    want = f"{' ' * depth}{key}:"
    for i in range(start, end):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.rstrip().startswith(want) and _indent(line) == depth:
            for j in range(i + 1, end):
                nxt = lines[j]
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    continue
                if _indent(nxt) <= depth:
                    return i, j
            return i, end
    raise PolicyEditError(f"{key!r} is not in this policy at the expected level")


def plan_ceiling(
    path: str | Path, *, tool: str, pointer: str, bands: list[dict[str, Any]]
) -> CeilingEdit:
    """Work out the edit without making it."""
    target = Path(path)
    try:
        before = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyEditError(f"cannot read {target}: {exc}") from exc

    clean = _validated(bands)
    lines = before.splitlines(keepends=True)

    tools_at, tools_end = _find_block(lines, "tools", 0, len(lines), 0)
    tool_depth = _child_depth(lines, tools_at + 1, tools_end, default=2)
    tool_at, tool_end = _find_block(lines, tool, tools_at + 1, tools_end, tool_depth)

    gate_depth = _child_depth(lines, tool_at + 1, tool_end, default=tool_depth + 2)
    gate_at, gate_end = _find_block(lines, "gate", tool_at + 1, tool_end, gate_depth)

    pointer_depth = _child_depth(lines, gate_at + 1, gate_end, default=gate_depth + 2)
    ptr_at, ptr_end = _find_block(lines, pointer, gate_at + 1, gate_end, pointer_depth)

    edit = CeilingEdit(path=target, tool=tool, pointer=pointer, bands=clean, before=before)

    # Three of the shipped gates are written as one-line flow mappings —
    # `/file_path: { resolver: fs.paths, on_unresolved: allow }` — and a block `bands:` spliced
    # underneath one is invalid YAML. `_verify` caught that and refused to write, which is the net
    # doing its job; this is the edit doing its job instead. Flow in, flow out: their formatting is
    # not ours to normalise.
    if _is_flow(lines[ptr_at], pointer):
        after_line, replaced = _flow_with_bands(lines[ptr_at], clean)
        edit.replaced = replaced
        if replaced:
            edit.warnings.append(
                f"{tool} {pointer} already declared a ceiling; this replaces it. The policy digest "
                "changes with it, so records written before and after are not comparable."
            )
        edit.after = "".join([*lines[:ptr_at], after_line, *lines[ptr_at + 1 :]])
        _verify(edit)
        return edit

    body_depth = _child_depth(lines, ptr_at + 1, ptr_end, default=pointer_depth + 2)
    pad = " " * body_depth
    rendered = [f"{pad}bands:\n"] + [
        f"{pad}  - {{ above: {b['above']}, verdict: {b['verdict']} }}\n" for b in clean
    ]

    # An existing `bands:` block is replaced in place, which keeps the surrounding keys and their
    # comments where the author put them. Appending a second one would produce a file YAML reads as
    # a duplicate key — valid to the parser, and not what anybody meant.
    try:
        bands_at, bands_end = _find_block(lines, "bands", ptr_at + 1, ptr_end, body_depth)
    except PolicyEditError:
        # No bands yet. Insert after `resolver:`, which is the order every shipped example uses —
        # what reads it, then what it is measured against, then what to do when it cannot be.
        at = ptr_at + 1
        for i in range(ptr_at + 1, ptr_end):
            if lines[i].lstrip().startswith("resolver:"):
                at = i + 1
                break
        after_lines = lines[:at] + rendered + lines[at:]
    else:
        edit.replaced = True
        after_lines = lines[:bands_at] + rendered + lines[bands_end:]
        edit.warnings.append(
            f"{tool} {pointer} already declared a ceiling; this replaces it. The policy digest "
            "changes with it, so records written before and after are not comparable."
        )

    edit.after = "".join(after_lines)
    _verify(edit)
    return edit


def _is_flow(line: str, pointer: str) -> bool:
    """`/x: { ... }` on one line, rather than a block opened underneath it."""
    _, _, rest = line.partition(f"{pointer}:")
    return rest.strip().startswith("{")


def _split_top_level(text: str) -> list[str]:
    """Commas at brace depth zero. `{ a: 1, b: [{ c: 2, d: 3 }] }` has two entries, not four."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _flow_with_bands(line: str, bands: list[dict[str, Any]]) -> tuple[str, bool]:
    """The same one-line mapping with a `bands:` entry added or replaced."""
    open_at = line.index("{")
    close_at = line.rindex("}")
    entries = [e.strip() for e in _split_top_level(line[open_at + 1 : close_at]) if e.strip()]

    rendered = (
        "bands: ["
        + ", ".join(f"{{ above: {b['above']}, verdict: {b['verdict']} }}" for b in bands)
        + "]"
    )

    replaced = False
    kept: list[str] = []
    for entry in entries:
        if entry.split(":", 1)[0].strip() == "bands":
            replaced = True
            kept.append(rendered)
        else:
            kept.append(entry)
    if not replaced:
        # After `resolver`, for the same reason as the block form: what reads it, then what it is
        # measured against, then what to do when it cannot be.
        at = next(
            (i + 1 for i, e in enumerate(kept) if e.split(":", 1)[0].strip() == "resolver"),
            len(kept),
        )
        kept.insert(at, rendered)

    return f"{line[:open_at]}{{ {', '.join(kept)} }}{line[close_at + 1 :]}", replaced


def _child_depth(lines: list[str], start: int, end: int, *, default: int) -> int:
    """The indentation of the first real child in a block, so this follows the file's own style
    rather than imposing two spaces on somebody who writes four."""
    for i in range(start, end):
        if lines[i].strip() and not lines[i].lstrip().startswith("#"):
            return _indent(lines[i])
    return default


def _validated(bands: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Reject a ceiling that cannot mean anything before it reaches the file.

    A band whose `above` is not an integer, or whose verdict is not one of the four, would load as a
    `PolicyError` at the next `neti hook` — which exits 0 and says so on stderr, so the session
    would run entirely ungated with the reason somewhere nobody reads. Catching it here is the
    difference between a rejected form and a silently disabled gate.
    """
    if not bands:
        raise PolicyEditError("a ceiling needs at least one band")

    clean: list[dict[str, Any]] = []
    for band in bands:
        raw: Any = band.get("above")
        verdict = str(band.get("verdict", "")).lower()
        try:
            above = int(raw)
        except (TypeError, ValueError) as exc:
            raise PolicyEditError(f"`above` must be a whole number, got {raw!r}") from exc
        if above < 0:
            raise PolicyEditError("`above` cannot be negative — a magnitude is a count")
        if verdict not in _VERDICTS:
            raise PolicyEditError(
                f"unknown verdict {verdict!r}: expected one of {', '.join(sorted(_VERDICTS))}"
            )
        clean.append({"above": above, "verdict": verdict})

    ordered = sorted(clean, key=lambda b: int(b["above"]))
    if len({int(b["above"]) for b in ordered}) != len(ordered):
        raise PolicyEditError("two bands cannot share the same `above`")
    return ordered


def _verify(edit: CeilingEdit) -> None:
    """Load the result before offering it, and check it says what was asked.

    A splice that produced valid YAML meaning something else would be the worst outcome available
    here: the console would report success, the file would parse, and the ceiling would be attached
    to the wrong gate or silently absent. Cheap to rule out, so it is ruled out.
    """
    from neti.config.policy import PolicyError, Policy  # noqa: I001
    import yaml

    try:
        data = yaml.safe_load(edit.after) or {}
        policy = Policy.model_validate(_normalised(data))
    except (yaml.YAMLError, PolicyError, ValueError) as exc:
        raise PolicyEditError(
            f"the edit would not load as a policy ({exc}). Nothing was written."
        ) from exc

    spec = policy.gate_specs(edit.tool).get(edit.pointer)
    if spec is None:
        raise PolicyEditError(
            f"after the edit, {edit.tool} {edit.pointer} is not gated. Nothing was written."
        )
    # Compared by content, not by order: `GateSpec` sorts its bands on load, so requiring the
    # model's ordering here would fail on a correct edit.
    written: list[dict[str, Any]] = sorted(
        ({"above": int(b.above), "verdict": b.verdict.name.lower()} for b in spec.bands),
        key=lambda b: int(b["above"]),
    )
    if written != edit.bands:
        raise PolicyEditError(
            f"the edit did not land where it was aimed: {edit.tool} {edit.pointer} reads "
            f"{written} rather than {edit.bands}. Nothing was written."
        )


def _normalised(data: dict[str, Any]) -> dict[str, Any]:
    from neti.config.policy import _normalise

    return _normalise(data)


@dataclass
class SensitiveEdit:
    """A rewrite of the whole top-level `sensitive:` list.

    Whole-list rather than per-rule, and that is the right granularity: these are a handful of lines
    a person reads top to bottom, order decides which one fires, and "add one" and "remove one" are
    the same operation on the same block. A per-rule splice would need to find a list item by
    matching its text, which is the fragile thing this module exists to avoid.
    """

    path: Path
    rules: list[dict[str, Any]]
    before: str = ""
    after: str = ""
    replaced: bool = False

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=str(self.path),
                tofile=f"{self.path} (proposed)",
                n=4,
            )
        )


def _render_sensitive(rules: list[dict[str, Any]]) -> list[str]:
    out = ["sensitive:\n"]
    for r in rules:
        why = str(r.get("why") or "").strip()
        tail = f", why: {why}" if why else ""
        out.append(f'  - {{ match: "{r["match"]}", verdict: {r["verdict"]}{tail} }}\n')
    return out


def plan_sensitive(path: str | Path, rules: list[dict[str, Any]]) -> SensitiveEdit:
    """Work out a rewrite of the top-level `sensitive:` block without making it.

    An empty list removes the block entirely rather than leaving `sensitive:` with nothing under it,
    which YAML reads as `None` and which looks like a setting somebody meant to fill in.
    """
    target = Path(path)
    try:
        before = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyEditError(f"cannot read {target}: {exc}") from exc

    clean = _validated_rules(rules)
    lines = before.splitlines(keepends=True)
    edit = SensitiveEdit(path=target, rules=clean, before=before)
    rendered = [*_render_sensitive(clean), "\n"] if clean else []

    try:
        at, end = _find_block(lines, "sensitive", 0, len(lines), 0)
    except PolicyEditError:
        # No block yet. Immediately above `tools:`, which is where a reader looks for the things
        # that are not per-tool, and where the shipped example already comments them.
        try:
            tools_at, _ = _find_block(lines, "tools", 0, len(lines), 0)
        except PolicyEditError as exc:
            raise PolicyEditError(
                "this policy has no `tools:` block, so there is nowhere obvious to put "
                "`sensitive:`. Nothing was written."
            ) from exc
        after_lines = [*lines[:tools_at], *rendered, *lines[tools_at:]]
    else:
        edit.replaced = True
        # Trailing blank lines belong to the separation between blocks, not to this one.
        while end < len(lines) and not lines[end].strip():
            end += 1
        after_lines = [*lines[:at], *rendered, *lines[end:]]

    edit.after = "".join(after_lines)
    _verify_sensitive(edit)
    return edit


def _validated_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject a rule that cannot mean anything before it reaches the file.

    Same reasoning as `_validated`: a policy that will not load is not a rejected form, it is a
    disabled gate — `neti hook` exits 0 on a policy error, so the next session runs entirely
    ungated with the reason on stderr where nothing reads it.
    """
    clean: list[dict[str, Any]] = []
    for rule in rules:
        match = str(rule.get("match") or "").strip()
        verdict = str(rule.get("verdict") or "confirm").lower()
        if not match:
            raise PolicyEditError("a sensitive rule needs a `match`")
        if verdict not in _VERDICTS:
            raise PolicyEditError(
                f"unknown verdict {verdict!r}: expected one of {', '.join(sorted(_VERDICTS))}"
            )
        if '"' in match:
            raise PolicyEditError(f"a `match` cannot contain a double quote: {match!r}")
        why = str(rule.get("why") or "").strip()
        clean.append({"match": match, "verdict": verdict, "why": why})
    return clean


def _verify_sensitive(edit: SensitiveEdit) -> None:
    """Load the result before offering it, and check it says what was asked."""
    import yaml

    from neti.config.policy import Policy, PolicyError

    try:
        data = yaml.safe_load(edit.after) or {}
        policy = Policy.model_validate(_normalised(data))
    except (yaml.YAMLError, PolicyError, ValueError) as exc:
        raise PolicyEditError(
            f"the edit would not load as a policy ({exc}). Nothing was written."
        ) from exc

    written = [
        {"match": r.match, "verdict": r.verdict.name.lower(), "why": r.why}
        for r in policy.sensitive
    ]
    if written != edit.rules:
        raise PolicyEditError(
            f"the edit did not land where it was aimed: the policy reads {written} rather than "
            f"{edit.rules}. Nothing was written."
        )


def apply_sensitive(edit: SensitiveEdit) -> Path | None:
    """Write it, backing up what was there. Same contract as `apply_ceiling`."""
    if not edit.changed:
        return None
    backup = edit.path.with_suffix(edit.path.suffix + ".bak")
    backup.write_text(edit.before, encoding="utf-8")
    edit.path.write_text(edit.after, encoding="utf-8")
    return backup


def apply_ceiling(edit: CeilingEdit) -> Path | None:
    """Write it, backing up what was there. Returns the backup path, or `None` if nothing changed.

    Same contract as `insight/install.py`: this is a file somebody owns and an agent depends on, so
    the previous version survives the edit.
    """
    if not edit.changed:
        return None
    backup = edit.path.with_suffix(edit.path.suffix + ".bak")
    backup.write_text(edit.before, encoding="utf-8")
    edit.path.write_text(edit.after, encoding="utf-8")
    return backup


# --------------------------------------------------------------------------- the whole day-zero set


@dataclass
class PresetEdit:
    """Every day-zero change to a policy, spliced and written **once**.

    Not a loop over `plan_ceiling`. That was the first shape and it is wrong in a way worth
    recording: each call reads, writes and backs up, so nine gated parameters would leave a `.bak`
    holding the *eighth* intermediate version rather than what the operator started with. The one
    file somebody would reach for after a bad edit would be the one file that was no longer their
    original.

    So: one read, every splice, one verification of the result, one write, one backup.
    """

    path: Path
    bands: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    enforce: bool
    before: str = ""
    after: str = ""
    gates: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=str(self.path),
                tofile=f"{self.path} (proposed)",
                n=3,
            )
        )


def _all_gates(lines: list[str]) -> list[tuple[str, str]]:
    """Every `(tool, pointer)` this policy gates, read from the text rather than the model.

    From the text because this runs mid-splice, between edits, when the model would have to be
    re-parsed to be asked.
    """
    tools_at, tools_end = _find_block(lines, "tools", 0, len(lines), 0)
    tool_depth = _child_depth(lines, tools_at + 1, tools_end, default=2)

    found: list[tuple[str, str]] = []
    for i in range(tools_at + 1, tools_end):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or _indent(line) != tool_depth:
            continue
        tool = line.strip().rstrip(":")
        if not tool.endswith(":") and ":" not in tool:
            pass
        tool = tool.split(":")[0]
        try:
            _, tool_end = _find_block(lines, tool, tools_at + 1, tools_end, tool_depth)
            gate_at, gate_end = _find_block(
                lines, "gate", i + 1, tool_end, _child_depth(lines, i + 1, tool_end, default=4)
            )
        except PolicyEditError:
            continue
        ptr_depth = _child_depth(lines, gate_at + 1, gate_end, default=6)
        for j in range(gate_at + 1, gate_end):
            row = lines[j]
            if not row.strip() or row.lstrip().startswith("#") or _indent(row) != ptr_depth:
                continue
            pointer = row.strip().split(":")[0]
            if pointer.startswith("/"):
                found.append((tool, pointer))
    return found


def plan_preset(
    path: str | Path,
    *,
    bands: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    enforce: bool = True,
) -> PresetEdit:
    """Work out the whole day-zero edit without making it."""
    target = Path(path)
    try:
        before = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyEditError(f"cannot read {target}: {exc}") from exc

    clean_bands = _validated(bands) if bands else []
    clean_rules = _validated_rules(rules)
    text = before

    if clean_rules:
        spliced = plan_sensitive(target, clean_rules)
        text = spliced.after

    gates = _all_gates(text.splitlines(keepends=True))
    if clean_bands:
        for tool, pointer in gates:
            # A gate that already declares a ceiling is left alone. The preset is what to do when
            # nobody has decided yet; overwriting somebody's committed number with ours would be
            # the opposite of the whole argument for having it.
            if _has_bands(text, tool, pointer):
                continue
            text = _with_bands(text, tool=tool, pointer=pointer, bands=clean_bands)

    if enforce:
        text = _enforcing(text)

    edit = PresetEdit(
        path=target,
        bands=clean_bands,
        rules=clean_rules,
        enforce=enforce,
        before=before,
        after=text,
        gates=gates,
    )
    _verify_preset(edit)
    return edit


def _has_bands(text: str, tool: str, pointer: str) -> bool:
    import yaml

    from neti.config.policy import Policy

    try:
        policy = Policy.model_validate(_normalised(yaml.safe_load(text) or {}))
    except Exception:
        return False
    spec = policy.gate_specs(tool).get(pointer)
    return bool(spec and spec.bands)


def _with_bands(text: str, *, tool: str, pointer: str, bands: list[dict[str, Any]]) -> str:
    """`plan_ceiling`'s splice, on text rather than a path, so it can be composed."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = Path(fh.name)
    try:
        return plan_ceiling(tmp, tool=tool, pointer=pointer, bands=bands).after
    finally:
        tmp.unlink(missing_ok=True)


def _enforcing(text: str) -> str:
    """`mode: observe` -> `mode: enforce`, keeping whatever comment sits beside it."""
    out = []
    done = False
    for line in text.splitlines(keepends=True):
        if not done and line.lstrip().startswith("mode:") and "observe" in line:
            out.append(line.replace("observe", "enforce", 1))
            done = True
        else:
            out.append(line)
    return "".join(out)


def _verify_preset(edit: PresetEdit) -> None:
    """Load the result and check every piece landed, before offering it to anybody."""
    import yaml

    from neti.config.policy import Policy, PolicyError

    try:
        policy = Policy.model_validate(_normalised(yaml.safe_load(edit.after) or {}))
    except (yaml.YAMLError, PolicyError, ValueError) as exc:
        raise PolicyEditError(
            f"the preset would not load as a policy ({exc}). Nothing was written."
        ) from exc

    written = [
        {"match": r.match, "verdict": r.verdict.name.lower(), "why": r.why}
        for r in policy.sensitive
    ]
    if written != edit.rules:
        raise PolicyEditError("the off-limits rules did not land. Nothing was written.")

    if edit.enforce and policy.mode.name.lower() != "enforce":
        raise PolicyEditError("the policy is still observing after the edit. Nothing was written.")

    if edit.bands:
        gated = [(t, p) for t, s in policy.tools.items() for p in s.gate]
        without = [f"{t}{p}" for t, p in gated if not policy.gate_specs(t)[p].bands]
        if without:
            raise PolicyEditError(
                f"these gates still have no ceiling after the preset: {', '.join(without)}. "
                "Nothing was written."
            )


def apply_preset(edit: PresetEdit) -> Path | None:
    """Write it, backing up what was there. One backup, holding the original."""
    if not edit.changed:
        return None
    backup = edit.path.with_suffix(edit.path.suffix + ".bak")
    backup.write_text(edit.before, encoding="utf-8")
    edit.path.write_text(edit.after, encoding="utf-8")
    return backup
