/**
 * The console's only door to the gate.
 *
 * Every screen goes through here so there is exactly one place that knows the base URL, one error
 * shape, and one set of types. The types mirror the API's responses rather than being derived from
 * them — if the two drift, the compiler says so at the call site rather than the screen going
 * quietly blank.
 */

const BASE = process.env.NEXT_PUBLIC_NETI_API ?? "http://127.0.0.1:8722";

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
    throw new ApiError(0, `Cannot reach the neti API at ${BASE}. Is \`neti serve\` running?`);
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
  magnitudes: { pointer: string; magnitude: number | null; unit: string }[];
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
  audit: () =>
    get<{ ok: boolean; broken_at: string | null; count: number; head: string | null; links: AuditLink[] }>(
      "/api/audit/verify",
    ),
  scenario: (id: string) => get<Scenario>(`/api/scenarios/${id}`),
  scenarios: () => get<{ scenarios: Scenario[] }>("/api/scenarios"),
};
