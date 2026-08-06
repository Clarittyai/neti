/**
 * The console's only door to the gate.
 *
 * Every screen goes through here so there is exactly one place that knows the base URL, one error
 * shape, and one set of types. The types mirror the API's responses rather than being derived from
 * them — if the two drift, the compiler says so at the call site rather than the screen going
 * quietly blank.
 */

// Same origin by default, because `neti console` serves this bundle and the API from one process
// on whatever port the operator chose — baking in a port would break the moment they picked another.
// The dev flow, where the UI is on :3100 and the API on :8722, sets NEXT_PUBLIC_NETI_API instead.
const BASE = process.env.NEXT_PUBLIC_NETI_API ?? "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    // The single most likely failure in a demo, and the one worth naming precisely: the console is
    // up and the engine is not. Anything vaguer sends someone hunting through the browser console.
    throw new ApiError(
      0,
      BASE
        ? `Cannot reach the neti API at ${BASE}. Is \`neti serve\` running?`
        : "Cannot reach the neti API. Is `neti console` still running?",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body?.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

const get = <T,>(path: string) => call<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// ---------------------------------------------------------------------------- types

export type Verdict = "allow" | "flag" | "confirm" | "block";
export type Mode = "observe" | "enforce";

export interface FixtureGroup {
  id: string;
  name: string;
  members: number;
  guests: number;
  apps: number;
  kind: string;
}

export interface ConsoleState {
  mode: "demo" | "live";
  connected: boolean;
  tenant: string;
  policy_digest: string;
  policy_mode: Mode;
  gated_tools: string[];
  records: string;
  /** Declared ground truth, published so a viewer can check the resolver rather than trust it. */
  fixture: FixtureGroup[] | null;
}

export interface TraceStage {
  key: "intercept" | "bind" | "assert" | "count" | "compare" | "seal";
  label: string;
  detail: string;
  at_ms: number;
  took_ms: number;
  payload: Record<string, unknown>;
}

export interface Cause {
  pointer: string;
  target: string | null;
  verdict: Verdict;
  rule: string;
  unit: string;
  state: string;
  magnitude: number | null;
  direction: string;
  breakdown: Record<string, number>;
  over_block_possible: boolean;
  ceiling: number | null;
  breaches: { source: string; observed: number; above: number; verdict: Verdict }[];
  consistency: string;
  resolved_at: string | null;
  provider_snapshot: string | null;
}

export interface DecisionRecord {
  schema: string;
  decision_id: string;
  session_id: string | null;
  decided_at: string;
  tool: string;
  args: Record<string, unknown>;
  verdict: Verdict;
  rule: string;
  mode: Mode;
  causes: Cause[];
  budget: Record<string, unknown> | null;
  policy_digest: string;
  code_version: string;
  // True when the magnitudes came from the built-in tenant rather than a provider. Inside the
  // record's digest, so it is evidence of provenance and not a label anyone can strip.
  synthetic: boolean;
  /** What the agent said it was doing. Recorded, never trusted. */
  said?: string | null;
  prev_digest: string | null;
  record_digest: string;
}

export interface GateResult {
  verdict: Verdict;
  rule: string;
  proceeds: boolean;
  mode: Mode;
  denial: {
    unit?: string;
    resolved?: number | null;
    ceiling?: number;
    parameter?: string;
    reason?: string;
    session_total?: number;
    session_ceiling?: number;
  } | null;
  trace: { stages: TraceStage[]; elapsed_ms: number };
  decision_id: string;
  record: DecisionRecord;
}

export interface ScenarioStep {
  tool: string;
  args: Record<string, string>;
  narration: string;
}

export interface Scenario {
  id: string;
  title: string;
  prompt: string;
  moral: string;
  session_id: string;
  steps: ScenarioStep[];
}

export interface InventoryRow {
  tool: string;
  param: string;
  resolver: string;
  reachable: number | null;
  unit: string;
  direction: string;
  has_ceiling: boolean;
  block_at: number | null;
  risk: string;
}

export interface AuditLink {
  decision_id: string;
  decided_at: string;
  tool: string;
  verdict: Verdict;
  prev_digest: string | null;
  record_digest: string;
}

export interface DecisionSummary {
  decision_id: string;
  decided_at: string;
  tool: string;
  verdict: Verdict;
  rule: string;
  mode: Mode;
  session_id: string | null;
  synthetic: boolean;
  /** What the agent said it was doing — `Bash` and `Task` both carry a `description`. Sealed in
   *  the chained record all along. Recorded, never trusted: evidence for a human, input to
   *  nothing. */
  said?: string | null;
  magnitudes: { pointer: string; magnitude: number | null; unit: string }[];
}

export type ApprovalState = "pending" | "granted" | "denied" | "expired";

export interface ApprovalRow {
  id: string;
  digest: string;
  state: ApprovalState;
  approved_magnitude: number | null;
  unit: string | null;
  evidence: {
    tool?: string;
    rule?: string;
    ceiling?: number | null;
    decision_id?: string;
    policy_digest?: string;
  };
  requested_at: string;
  expires_at: string;
  decided_by: string | null;
  decided_at: string | null;
  reason: string | null;
  redeemed: boolean;
}

export interface OrgState {
  attached: boolean;
  org?: string;
  url?: string;
  reachable?: boolean;
  reason?: string | null;
}

export type Coverage = "caught" | "needs_resolver" | "needs_budget" | "out_of_scope";

export interface Incident {
  id: string;
  date: string;
  actor: string;
  what_one_call_did: string;
  magnitude: number | null;
  unit: string;
  authorized: boolean;
  reversible: string;
  source: string;
  coverage: Coverage;
  note: string;
  gated_unit: string | null;
}

export interface Scorecard {
  incidents: Record<Coverage, Incident[]>;
  coverage: { caught: number; total: number };
  friction: {
    calls: number;
    stopped: number;
    confirmed: number;
    blocked: number;
    over_block_possible: number;
    interrupt_rate: number;
  };
  policy: {
    digest: string;
    gated_tools: number;
    gated_params: number;
    params_without_ceiling: number;
  };
  unresolved_parameters: number;
  known_blind_spots: Record<string, string>;
  not_yet_measured: string[];
}

// ---------------------------------------------------------------------------- calls

export const api = {
  state: () => get<ConsoleState>("/api/state"),
  connect: () =>
    post<{ connected: boolean; directory_size: number | null; tenant: string; reason?: string }>(
      "/api/connect",
    ),
  disconnect: () => post<{ connected: boolean }>("/api/disconnect"),
  setMode: (mode: Mode) => post<{ mode: Mode }>("/api/mode", { mode }),

  gate: (tool: string, args: Record<string, unknown>, session_id?: string) =>
    post<GateResult>("/api/gate", { tool, args, session_id }),

  inventory: () => get<{ rows: InventoryRow[] }>("/api/inventory"),
  decisions: () => get<{ total: number; decisions: DecisionSummary[] }>("/api/decisions"),
  decision: (id: string) => get<DecisionRecord>(`/api/decisions/${id}`),
  policy: () => get<Record<string, unknown>>("/api/policy"),
  report: () => get<Record<string, unknown>>("/api/report"),
  scorecard: () => get<Scorecard>("/api/scorecard"),
  audit: () =>
    get<{ ok: boolean; broken_at: string | null; count: number; head: string | null; links: AuditLink[] }>(
      "/api/audit/verify",
    ),
  org: () => get<OrgState>("/api/org"),
  approvals: (state?: string) =>
    get<{ attached: boolean; approvals: ApprovalRow[] }>(
      `/api/approvals${state ? `?state=${state}` : ""}`,
    ),
  decide: (id: string, granted: boolean, decided_by: string, reason?: string) =>
    post<ApprovalRow>(`/api/approvals/${id}/decide`, { granted, decided_by, reason }),

  scenario: (id: string) => get<Scenario>(`/api/scenarios/${id}`),
  scenarios: () => get<{ scenarios: Scenario[] }>("/api/scenarios"),
};
