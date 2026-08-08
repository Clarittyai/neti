"""The console's HTTP API.

Deliberately boring. Every endpoint is a thin wrapper over something that already exists and is
already tested — `Engine.gate`, `build_inventory`, `build_report`, `propose`, `verify_chain`,
`build_scorecard`. The API adds no judgement of its own, which is what lets the console claim that
what it shows is what the gate does.

Bound to localhost, no auth, single process. It is a fully operating local console — it resolves
real magnitudes, enforces real verdicts and seals a real chain — and it is not a control plane:
there is no org, no shared policy and nobody else to ask for an approval. That is what the hosted
version adds, and it is a difference in reach rather than in whether this one works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from neti.api.state import ConsoleState, build_state
from neti.api.static import mount_console
from neti.api.trace import TraceCollector
from neti.core.record import verify_chain
from neti.core.types import ProposedCall
from neti.core.verdict import Mode
from neti.eval.scenarios import SCENARIOS
from neti.eval.scorecard import build_scorecard, scorecard_json
from neti.insight.inventory import build_inventory
from neti.insight.propose import propose
from neti.insight.report import build_report
from neti.resolvers.base import ResolveContext
from neti.store.jsonl import read_records

__all__ = ["create_app"]


class GateRequest(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    session_id: str | None = None


class ModeRequest(BaseModel):
    mode: str


class DecideRequest(BaseModel):
    granted: bool
    decided_by: str
    reason: str | None = None


def create_app(
    state: ConsoleState | None = None, *, serve_console: bool = True, **kw: Any
) -> FastAPI:
    st = state or build_state(**kw)
    app = FastAPI(title="neti console", docs_url="/api/docs")

    # The Next dev server is on another port. Localhost-only, so this is not a real boundary.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3100", "http://127.0.0.1:3100"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.console = st

    # ---------------------------------------------------------------- state

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return st.as_json()

    @app.get("/api/start")
    def start() -> dict[str, Any]:
        """Where this install is in the walkthrough, re-read every time it is asked.

        Deliberately does not build the inventory: that walks the filesystem, and this endpoint is
        polled every few seconds so a step can tick itself while somebody is watching. The reachable
        number is already on the overview, from the endpoint that pays for it once.
        """
        from neti.insight.onboarding import start_state

        return start_state(
            st.policy,
            policy_path=st.config_path,
            decisions=len(_records(st)),
        ).as_json()

    @app.post("/api/connect")
    def connect() -> dict[str, Any]:
        """Verify the credential by actually using it.

        A connect button that only stores a secret has proved nothing. This resolves the tenant's
        reachable maximum, which is the same call the inventory makes, so "connected" means "we
        successfully counted something" rather than "the form submitted".
        """
        # The resolvers THIS policy binds, not `entra.principals`.
        #
        # This probed Entra unconditionally, so a coding-agent policy — gating `fs.paths`, needing
        # no directory and no credential at all — could never become connected. The live gate
        # therefore opened on "Not connected yet" and stopped, for an install whose gate was working
        # perfectly. Same Entra-centric assumption as the old "Demo tenant" badge and the console
        # that ignored `providers:`.
        wanted = sorted(st.policy.bound_resolvers())
        probes = [
            (name, st.engine.resolvers[name].reachable_max(st.engine.ctx))
            for name in wanted
            if name in st.engine.resolvers
        ]
        st.connected = bool(probes) and all(p.state.name == "RESOLVED" for _, p in probes)
        # The failure worth reporting is the one that stopped the connection; otherwise anything.
        probe = next((p for _, p in probes if p.state.name != "RESOLVED"), None) or (
            probes[0][1] if probes else None
        )
        if probe is None:
            raise HTTPException(500, "this policy binds no resolver that can be probed")

        # `directory_size` stays what its name says: the directory. Reporting whichever resolver
        # happened to sort first would have made it the *filesystem* count on a policy that binds
        # both, under a key every caller reads as "how big is the tenant".
        directory = dict(probes).get("entra.principals")
        return {
            "connected": st.connected,
            "mode": st.mode,
            "tenant": st.tenant_label,
            "directory_size": directory.magnitude if directory else None,
            "reason": None if st.connected else probe.evidence.get("reason"),
        }

    @app.post("/api/mode")
    def set_mode(req: ModeRequest) -> dict[str, Any]:
        """Flip between observe and enforce at runtime.

        Not a convenience — it is the most honest thing the console can show. In observe mode the
        gate computes and records the same verdict and forwards the call anyway, which is what makes
        installing it reversible. Flipping to enforce and re-running the identical call demonstrates
        that the decision was already being made correctly the whole time, and that enforcement
        changes only whether it is acted on.
        """
        wanted = req.mode.strip().lower()
        if wanted not in ("observe", "enforce"):
            raise HTTPException(400, "mode must be 'observe' or 'enforce'")
        st.set_mode(Mode.ENFORCE if wanted == "enforce" else Mode.OBSERVE)
        return {"mode": st.policy.mode.name.lower()}

    @app.post("/api/disconnect")
    def disconnect() -> dict[str, Any]:
        st.connected = False
        return {"connected": False}

    # ---------------------------------------------------------------- the gate

    @app.post("/api/gate")
    def gate(req: GateRequest) -> dict[str, Any]:
        """Fire one tool call through the real engine and return the verdict, the trace and the
        record. This is the endpoint the whole console is built around."""
        if not st.connected:
            raise HTTPException(409, "not connected — connect a provider first")

        collector = TraceCollector()
        result = st.engine.gate(
            ProposedCall(tool=req.tool, args=req.args, session_id=req.session_id),
            observe=collector,
        )
        st.sink.write(result.record)

        return {
            "verdict": result.decision.verdict.name.lower(),
            "rule": result.decision.rule,
            "proceeds": result.proceeds,
            "mode": st.policy.mode.name.lower(),
            "denial": None if result.proceeds else st.engine.denial_payload(result),
            "trace": collector.as_json(),
            "decision_id": result.record.decision_id,
            "record": result.record.model_dump(mode="json", by_alias=True),
        }

    # ---------------------------------------------------------------- reads

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        """Which model `neti suggest` could reach, and from where.

        **No key value crosses this boundary in either direction.** There is no field to type one
        into and nothing here reads one — only whether the variable the SDK will look at is set.
        A key pasted into a browser is a key in a process that did not need it, and "nothing extra
        holds your secrets" is the claim this whole feature rests on.
        """
        from neti.insight.providers import provider_statuses

        return {
            "providers": [
                {
                    "id": p.id,
                    "label": p.label,
                    "ready": p.ready,
                    "detail": p.detail,
                    "env": p.env,
                    "command": p.command,
                    "installs": p.installs,
                    "leaves_machine": p.leaves_machine,
                    "runners": [
                        {"id": r.id, "label": r.label, "base_url": r.base_url, "start": r.start}
                        for r in p.runners
                    ],
                }
                for p in provider_statuses()
            ]
        }

    @app.post("/api/models/probe")
    def probe_endpoint(body: dict[str, Any]) -> dict[str, Any]:
        """`GET {base_url}/models` against an address the operator typed, and nothing else.

        The part people actually get wrong is reachability — a runner on the wrong port, a company
        gateway behind a proxy, a model id that is not loaded. Answering it here beats telling
        somebody to go and curl it, and it stays a read: no completion is run, so a cold 30B model
        does not take minutes and a metered gateway is not billed for a connectivity check.
        """
        from neti.insight.providers import probe

        url = str(body.get("base_url") or "").strip()
        if not url:
            raise HTTPException(400, "base_url is required")
        return probe(url).as_json()

    @app.get("/api/targets")
    def targets() -> dict[str, Any]:
        """What the live gate can offer to fire, per gated tool, off this machine.

        The tool list was hardcoded Entra names once and got fixed; the target list was the Entra
        *fixture* and did not, so a policy binding no directory got an empty dropdown and a dead
        button on the page that exists to demonstrate the product.
        """
        from neti.insight.targets import targets_for

        fixture = st.as_json().get("fixture")
        out: dict[str, dict[str, Any]] = {}
        for tool in st.policy.tools:
            gates = st.policy.gate_specs(tool)
            if not gates:
                continue
            pointer = next(iter(gates))
            resolvers = [gate.resolver for gate in gates.values()]
            out[tool] = {
                "pointer": pointer,
                # The argument name, so the console stops guessing it. It was
                # `tool.includes("group") ? "group" : "to"` — Entra-shaped, and wrong for every
                # filesystem tool there is: a fired `Bash` call carried `to` rather than `command`,
                # so the gate saw no target at all and routed the whole thing through
                # `on_unresolved`. The policy has known the answer the entire time.
                "arg": pointer.lstrip("/").split("/")[0],
                "targets": [
                    t.as_json()
                    for t in targets_for(
                        tool, resolvers, providers=st.policy.providers, fixture=fixture
                    )
                ],
            }
        return {"targets": out}

    @app.get("/api/inventory")
    def inventory() -> dict[str, Any]:
        rows = build_inventory(st.policy, st.engine.resolvers, ResolveContext())
        return {
            "rows": [
                {
                    "tool": r.tool,
                    "param": r.pointer,
                    "resolver": r.resolver,
                    "reachable": r.reachable.magnitude,
                    "unit": r.reachable.unit.value,
                    "direction": r.reachable.direction.value,
                    "has_ceiling": r.has_ceiling,
                    "block_at": r.block_at,
                    "risk": r.risk,
                }
                for r in sorted(rows, key=lambda r: -(r.reachable.magnitude or 0))
            ]
        }

    @app.get("/api/decisions")
    def decisions(limit: int = 50) -> dict[str, Any]:
        records = _records(st)
        return {
            "total": len(records),
            "decisions": [
                {
                    "decision_id": r.decision_id,
                    "decided_at": r.decided_at,
                    "tool": r.tool,
                    "verdict": r.verdict,
                    "rule": r.rule,
                    "mode": r.mode,
                    "session_id": r.session_id,
                    # The list hand-picks fields, so a new one has to be added here deliberately —
                    # and this is the one that must never be dropped. A synthetic decision rendering
                    # beside a measured one, with the same confident magnitude and nothing to tell
                    # them apart, is the defect `neti.decision.v2` exists to close, one layer up.
                    "synthetic": r.synthetic,
                    # What the agent said it was doing. `Bash` and `Task` both carry a
                    # `description`, and it has been inside the chained payload all along — sealed,
                    # tamper-evident, and never shown. Beside the magnitude it becomes the finding:
                    # "clean up build artifacts", 22,794 objects.
                    #
                    # Recorded, never trusted. It is evidence for a human and an input to nothing.
                    "said": (r.args.get("description") or None)
                    if isinstance(r.args, dict)
                    else None,
                    # Why it was stopped, when the reason was not a number. A row reading
                    # "Blocked · 1 object" is a row nobody can act on.
                    "sensitive": list(r.sensitive),
                    "provenance": r.provenance,
                    "magnitudes": [
                        {
                            "pointer": c["pointer"],
                            "magnitude": c["magnitude"],
                            "unit": c["unit"],
                            # Why there is no number, when there is no number. Without this the row
                            # renders every unresolved cause as "Could not size" — which is true of
                            # `npm test` and true of `cat list.txt | xargs rm`, and putting them
                            # under one chip is the exact conflation `on_unsized_risk` removed one
                            # layer down. A console that re-merges them undoes the fix.
                            "destructive": c.get("destructive"),
                            "reason": c.get("reason"),
                        }
                        for c in r.causes
                    ],
                }
                for r in reversed(records[-limit:])
            ],
        }

    @app.get("/api/decisions/{decision_id}")
    def decision(decision_id: str) -> dict[str, Any]:
        for r in _records(st):
            if r.decision_id == decision_id:
                dumped: dict[str, Any] = r.model_dump(mode="json", by_alias=True)
                return dumped
        raise HTTPException(404, f"no decision {decision_id}")

    @app.get("/api/policy")
    def policy() -> dict[str, Any]:
        return {
            "digest": st.policy.digest(),
            "mode": st.policy.mode.name.lower(),
            "unknown_tool": st.policy.unknown_tool.name.lower(),
            "tools": {
                tool: {
                    pointer: {
                        "resolver": spec.resolver,
                        "unit": spec.unit.value if spec.unit else None,
                        "bands": [
                            {"above": b.above, "verdict": b.verdict.name.lower()}
                            for b in spec.bands
                        ],
                        "on_unresolved": spec.on_unresolved.name.lower(),
                        "has_ceiling": spec.has_ceiling,
                    }
                    for pointer, spec in st.policy.gate_specs(tool).items()
                }
                for tool in sorted(st.policy.tools)
                if st.policy.gate_specs(tool)
            },
            # The other two axes. The page listed ceilings and nothing else, so an operator with a
            # `sensitive:` block could not see it in their own console — and a rule you cannot see
            # is one you cannot check, which is most of what this page is for.
            "sensitive": [
                {"match": r.match, "verdict": r.verdict.name.lower(), "why": r.why}
                for r in st.policy.sensitive
            ],
            "provenance": {
                "untrusted": list(st.policy.provenance.untrusted),
                "tools": sorted(st.policy.provenance.tools),
                "bands": [
                    {"above": b.above, "verdict": b.verdict.name.lower()}
                    for b in st.policy.provenance.bands
                ],
            },
            # The fourth axis, and the only one that is a place rather than a property of the call.
            # Listed here for the same reason `sensitive` is: `neti start` writes it without being
            # asked, and a rule an operator cannot find is one they cannot check or remove.
            "outside_root": {
                "verdict": None
                if st.policy.outside_root is None
                else st.policy.outside_root.name.lower(),
                # Resolved, not as written. A policy that says `root: .` is answering "outside
                # what?" with a character the reader cannot check against their own disk.
                "root": str(
                    Path((st.policy.providers.get("fs") or {}).get("root") or ".").resolve()
                ),
            },
            "session_budgets": [
                {
                    "tools": sorted(r.tools),
                    "unit": r.unit.value,
                    "bands": [
                        {"above": b.above, "verdict": b.verdict.name.lower()} for b in r.bands
                    ],
                }
                for r in st.policy.session_budgets
            ],
        }

    @app.post("/api/policy/ceiling")
    def set_ceiling(body: dict[str, Any]) -> dict[str, Any]:
        """Declare a ceiling, in two calls.

        `apply: false` — the default — plans the edit and returns the diff, and writes nothing. A
        policy decides what an agent may do and its ceilings are the product's only claim, so it
        gets the same contract `neti install` gives `.claude/settings.json`: show the change, back
        the file up, and only write when somebody says so.

        The number is never invented. `config/policy.py` opens by saying nothing computed becomes a
        ceiling on its own, and this writes exactly what it was handed — the console shows the
        observed distribution beside the field so a person can choose, which is not the same thing.
        """
        from neti.insight.edit_policy import PolicyEditError, apply_ceiling, plan_ceiling

        try:
            edit = plan_ceiling(
                st.config_path,
                tool=str(body["tool"]),
                pointer=str(body["pointer"]),
                bands=list(body.get("bands") or []),
            )
        except KeyError as exc:
            raise HTTPException(400, f"missing {exc}") from exc
        except PolicyEditError as exc:
            raise HTTPException(400, str(exc)) from exc

        written = False
        backup: str | None = None
        if bool(body.get("apply")):
            try:
                saved = apply_ceiling(edit)
            except OSError as exc:
                raise HTTPException(500, f"could not write {edit.path}: {exc}") from exc
            written = True
            backup = None if saved is None else str(saved)
            # Reload, so the console stops describing a policy that is no longer on disk. The
            # digest changes with the ceiling, which is correct and is why the engine is rebuilt
            # around it rather than mutated: a record has to say which policy produced it.
            st.reload()

        return {
            "path": str(edit.path),
            "diff": edit.diff(),
            "replaced": edit.replaced,
            "warnings": edit.warnings,
            "changed": edit.changed,
            "applied": written,
            "backup": backup,
            "digest": st.policy.digest(),
        }

    @app.post("/api/policy/sensitive")
    def set_sensitive(body: dict[str, Any]) -> dict[str, Any]:
        """Rewrite the whole off-limits list, in two calls like every other edit here.

        The whole list rather than one rule: these are a handful of lines somebody reads top to
        bottom, order decides which fires, and add and remove are the same operation on the same
        block. It is also what keeps the YAML optional — an operator who never opens the file can
        still see, add and remove every rule that stops a call.
        """
        from neti.insight.edit_policy import PolicyEditError, apply_sensitive, plan_sensitive

        try:
            edit = plan_sensitive(st.config_path, list(body.get("rules") or []))
        except PolicyEditError as exc:
            raise HTTPException(400, str(exc)) from exc

        written = False
        backup: str | None = None
        if bool(body.get("apply")):
            try:
                saved = apply_sensitive(edit)
            except OSError as exc:
                raise HTTPException(500, f"could not write {edit.path}: {exc}") from exc
            written = True
            backup = None if saved is None else str(saved)
            st.reload()

        return {
            "path": str(edit.path),
            "diff": edit.diff(),
            "changed": edit.changed,
            "applied": written,
            "backup": backup,
            "digest": st.policy.digest(),
        }

    @app.post("/api/policy/outside_root")
    def set_outside_root(body: dict[str, Any]) -> dict[str, Any]:
        """Set or clear the location axis, in two calls like every other edit here.

        An empty verdict removes the line rather than writing `allow`. Turning a rule off and
        declaring that something is permitted are different statements, and only one of them is
        what an operator means when they switch this off.
        """
        from neti.insight.edit_policy import (
            PolicyEditError,
            apply_outside_root,
            plan_outside_root,
        )

        try:
            edit = plan_outside_root(st.config_path, str(body.get("verdict") or ""))
        except PolicyEditError as exc:
            raise HTTPException(400, str(exc)) from exc

        written = False
        backup: str | None = None
        if bool(body.get("apply")):
            try:
                saved = apply_outside_root(edit)
            except OSError as exc:
                raise HTTPException(500, f"could not write {edit.path}: {exc}") from exc
            written = True
            backup = None if saved is None else str(saved)
            st.reload()

        return {
            "path": str(edit.path),
            "diff": edit.diff(),
            "changed": edit.changed,
            "applied": written,
            "backup": backup,
            "digest": st.policy.digest(),
        }

    @app.get("/api/report")
    def report() -> dict[str, Any]:
        summary = build_report(_records(st))
        return {
            "decisions": summary.decisions,
            "verdicts": summary.verdicts,
            "distributions": [
                {
                    "tool": d.tool,
                    "pointer": d.pointer,
                    "unit": d.unit,
                    "n": d.n,
                    "p50": d.p50,
                    "p95": d.p95,
                    "p99": d.p99,
                    "max": d.maximum,
                    "unresolved": d.unresolved,
                    "magnitudes": d.magnitudes,
                    "over_ceiling": [
                        {"decision_id": i, "observed": o, "ceiling": c}
                        for i, o, c in d.over_ceiling
                    ],
                }
                for d in summary.ordered
            ],
            "proposals": [
                {
                    "tool": p.tool,
                    "pointer": p.pointer,
                    "unit": p.unit,
                    "n": p.n,
                    "normal": p.normal,
                    "observed_max": p.observed_max,
                    "confirm_above": p.confirm_above,
                    "block_above": p.block_above,
                    "rationale": p.rationale,
                    "would_block": p.would_block,
                    "would_confirm": p.would_confirm,
                    "examples": p.examples,
                    "actionable": p.actionable,
                }
                for p in propose(summary)
            ],
        }

    @app.get("/api/audit/verify")
    def audit() -> dict[str, Any]:
        records = _records(st)
        ok, bad = verify_chain(records)
        return {
            "ok": ok,
            "broken_at": bad,
            "count": len(records),
            "head": records[-1].record_digest if records else None,
            "links": [
                {
                    "decision_id": r.decision_id,
                    "decided_at": r.decided_at,
                    "tool": r.tool,
                    "verdict": r.verdict,
                    "prev_digest": r.prev_digest,
                    "record_digest": r.record_digest,
                }
                for r in records
            ],
        }

    @app.get("/api/scorecard")
    def scorecard() -> Any:
        import json

        summary = build_report(_records(st)) if _records(st) else None
        return json.loads(scorecard_json(build_scorecard(summary, st.policy)))

    # ---------------------------------------------------------------- team (paid)

    # The console talks to the control plane over HTTP like any other client. It does not import
    # `neti_cloud` and could not — that is the licence boundary, and it is why these endpoints are
    # a proxy rather than a direct call into a store.

    @app.get("/api/org")
    def org() -> dict[str, Any]:
        """Whether this machine is attached to a control plane.

        Answers honestly when it is configured but unreachable, because the console showing an empty
        inbox for a server that is down would be the worst possible lie for an approvals screen.
        """
        from neti.cloud import load_credentials, org_client

        creds = load_credentials()
        if creds is None or not creds.configured:
            return {"attached": False, "reason": "no control plane — run `neti login`"}

        client = org_client()
        assert client is not None
        try:
            reachable = client.health()
        finally:
            client.close()
        return {
            "attached": True,
            "org": creds.org,
            "url": creds.url,
            "reachable": reachable,
            "reason": None if reachable else "the control plane is not answering",
        }

    @app.get("/api/approvals")
    def approvals(state: str | None = None) -> dict[str, Any]:
        from neti.approvals import ApproverError
        from neti.cloud import org_client

        client = org_client()
        if client is None:
            return {"attached": False, "approvals": []}
        try:
            return {"attached": True, "approvals": client.approvals(state)}
        except ApproverError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            client.close()

    @app.post("/api/approvals/{approval_id}/decide")
    def decide(approval_id: str, req: DecideRequest) -> dict[str, Any]:
        from neti.approvals import ApproverError
        from neti.cloud import org_client

        client = org_client()
        if client is None:
            raise HTTPException(409, "not attached to a control plane — run `neti login`")
        try:
            return client.decide(
                approval_id, granted=req.granted, decided_by=req.decided_by, reason=req.reason
            )
        except ApproverError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            client.close()

    # ---------------------------------------------------------------- scenarios

    @app.get("/api/scenarios")
    def scenarios() -> dict[str, Any]:
        """Only the scenarios this policy can actually run.

        The shipped scenarios drive Entra tools. Offering "Offboard the Q3 contractors" to somebody
        whose policy gates `Glob`, `Read` and `delete_files` is showing them a story about a tool
        they do not have — mock data wearing the clothes of their own console. A scenario is offered
        only when the policy gates the tool it drives, and a filesystem install therefore sees none
        of them and drives the gate with its own tools instead.
        """
        gated = set(st.policy.tools)
        runnable = [s for s in SCENARIOS.values() if all(step.tool in gated for step in s.steps)]
        return {"scenarios": [s.as_json() for s in runnable]}

    @app.get("/api/scenarios/{scenario_id}")
    def scenario(scenario_id: str) -> dict[str, Any]:
        found = SCENARIOS.get(scenario_id)
        if found is None:
            raise HTTPException(404, f"no scenario {scenario_id}")
        return found.as_json()

    # Last, and at the root. FastAPI matches in registration order, so every `/api/...` route above
    # still wins.
    if serve_console:

        @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        def api_not_found(rest: str) -> None:
            """Keep unmatched API paths JSON.

            Without this, an unknown `/api/...` path falls through to the console mount below and is
            answered with the 404 *page* — so a client debugging a typo'd endpoint gets a wall of
            HTML and a Content-Type that tells it nothing. This is registered after every real route
            and before the mount, which is the only window where it is both reachable and harmless.
            """
            raise HTTPException(404, f"no API route /api/{rest}")

        mount_console(app)

    return app


def _records(st: ConsoleState) -> list[Any]:
    """Flush pending writes before reading, so the console never shows a decision it just made as
    missing. The sink is deliberately off the hot path; that is invisible at demo volumes and
    surprising if a read races it."""
    try:
        return list(read_records(st.records_path))
    except FileNotFoundError:
        return []
