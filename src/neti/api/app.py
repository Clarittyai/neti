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

    @app.post("/api/connect")
    def connect() -> dict[str, Any]:
        """Verify the credential by actually using it.

        A connect button that only stores a secret has proved nothing. This resolves the tenant's
        reachable maximum, which is the same call the inventory makes, so "connected" means "we
        successfully counted something" rather than "the form submitted".
        """
        resolver = st.engine.resolvers.get("entra.principals")
        if resolver is None:
            raise HTTPException(500, "entra.principals resolver is not registered")
        probe = resolver.reachable_max(st.engine.ctx)
        st.connected = probe.state.name == "RESOLVED"
        return {
            "connected": st.connected,
            "mode": st.mode,
            "tenant": st.tenant_label,
            "directory_size": probe.magnitude,
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
                    "magnitudes": [
                        {"pointer": c["pointer"], "magnitude": c["magnitude"], "unit": c["unit"]}
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
        st = state()
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
