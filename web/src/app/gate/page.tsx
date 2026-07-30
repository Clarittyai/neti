"use client";

/**
 * The live gate — the screen the whole console exists for.
 *
 * Two ways to drive it, and both matter for different audiences. The scenario is the story: a viewer
 * watches an agent do something reasonable and get stopped. The manual control is the proof: a
 * sceptic types their own target and watches the same machinery answer. A demo that only plays a
 * script invites the question "is it just a video", and this is the answer to it.
 *
 * The scenario is *data* fetched from the API and driven from here, one step at a time, through the
 * same `POST /api/gate` the manual control uses. There is no second execution path — a demo with
 * its own code path stops being evidence the moment the two diverge.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight, Loader2, Play, Quote, Send } from "lucide-react";

import { ResolutionTheatre } from "@/components/ResolutionTheatre";
import { VerdictPill } from "@/components/Verdict";
import { useConsole } from "@/components/ConsoleProvider";
import { ApiError, api, type GateResult, type Scenario } from "@/lib/api";
import { cn, n } from "@/lib/utils";

interface Fired {
  narration?: string;
  result: GateResult;
}

export default function GatePage() {
  const { state, connect, setMode } = useConsole();
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioId, setScenarioId] = useState("offboard");
  const [fired, setFired] = useState<Fired[]>([]);
  const [current, setCurrent] = useState<GateResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState("g-eng-all");
  const [tool, setTool] = useState("remove_group_members");

  useEffect(() => {
    api.scenarios().then((r) => setScenarios(r.scenarios)).catch(() => undefined);
  }, []);

  // The second scenario is not a nice-to-have. "The gate could not size this" is a harder claim to
  // make than "the gate blocked this", and a demo that only ever shows the confident answer invites
  // the obvious question about what happens when the directory cannot answer.
  const scenario = scenarios.find((s) => s.id === scenarioId) ?? null;

  const fire = useCallback(
    async (t: string, args: Record<string, string>, narration?: string, session?: string) => {
      try {
        const result = await api.gate(t, args, session);
        setCurrent(result);
        setFired((f) => [{ narration, result }, ...f]);
        return result;
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        return null;
      }
    },
    [],
  );

  const runScenario = useCallback(async () => {
    if (!scenario) return;
    setRunning(true);
    setError(null);
    setFired([]);
    for (const step of scenario.steps) {
      const result = await fire(step.tool, step.args, step.narration, scenario.session_id);
      if (!result) break;
      // Long enough for the theatre to finish its beats before the next call replaces it. This is
      // playback pacing, not latency — the measured timings are on the stages themselves.
      await new Promise((r) => setTimeout(r, 2600));
    }
    setRunning(false);
  }, [scenario, fire]);

  const connected = state?.connected ?? false;
  const enforcing = state?.policy_mode === "enforce";

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight md:text-3xl">Live gate</h1>
            <p className="mt-1 text-sm text-muted-foreground md:text-base">
              Every call below runs through the real engine. Nothing here is pre-recorded.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModeToggle enforcing={enforcing} onChange={(m) => void setMode(m)} />
            {!connected ? (
              <button
                onClick={() => void connect()}
                className="rounded-lg bg-accent px-3.5 py-2 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent-600"
              >
                Connect
              </button>
            ) : null}
          </div>
        </div>
      </motion.div>

      {!connected ? (
        <div className="mt-8 rounded-2xl border border-dashed border-border px-5 py-8 text-center">
          <p className="text-sm font-semibold">Not connected yet</p>
          <p className="mx-auto mt-1 max-w-sm text-[13px] leading-relaxed text-muted-foreground">
            Nothing resolves until a provider is connected — the gate refuses to guess rather than
            inventing a number.{" "}
            <Link href="/connect" className="text-accent hover:underline">
              Connect one
            </Link>
            .
          </p>
        </div>
      ) : (
        <>
          {scenario ? (
            <ScenarioCard
              scenario={scenario}
              scenarios={scenarios}
              running={running}
              done={fired.length >= scenario.steps.length}
              onRun={() => void runScenario()}
              onPick={(id) => {
                setScenarioId(id);
                setFired([]);
                setCurrent(null);
              }}
            />
          ) : null}

          <div className="mt-6">
            <ResolutionTheatre result={current} />
          </div>

          {error ? (
            <p className="mt-4 text-sm text-[hsl(var(--verdict-block))]">{error}</p>
          ) : null}

          <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            <FiredList fired={fired} />
            <ManualCall
              tool={tool}
              target={target}
              disabled={running}
              fixture={state?.fixture ?? null}
              onTool={setTool}
              onTarget={setTarget}
              onFire={() =>
                void fire(tool, { [tool.includes("group") ? "group" : "to"]: target }, undefined, "manual")
              }
            />
          </div>
        </>
      )}
    </div>
  );
}

function ModeToggle({
  enforcing,
  onChange,
}: {
  enforcing: boolean;
  onChange: (m: "observe" | "enforce") => void;
}) {
  return (
    <div className="glass-button flex rounded-lg p-0.5 text-sm">
      {(["observe", "enforce"] as const).map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={cn(
            "rounded-[6px] px-3 py-1.5 font-medium capitalize transition-colors",
            (m === "enforce") === enforcing
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {m}
        </button>
      ))}
    </div>
  );
}

function ScenarioCard({
  scenario,
  scenarios,
  running,
  done,
  onRun,
  onPick,
}: {
  scenario: Scenario;
  scenarios: Scenario[];
  running: boolean;
  done: boolean;
  onRun: () => void;
  onPick: (id: string) => void;
}) {
  return (
    <div className="glass-card mt-6 rounded-2xl p-5">
      {scenarios.length > 1 ? (
        <div className="mb-4 flex flex-wrap gap-1.5 border-b border-border/50 pb-4">
          {scenarios.map((s) => (
            <button
              key={s.id}
              onClick={() => onPick(s.id)}
              disabled={running}
              className={cn(
                "rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors disabled:opacity-50",
                s.id === scenario.id
                  ? "bg-accent/10 text-accent"
                  : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
              )}
            >
              {s.title}
            </button>
          ))}
        </div>
      ) : null}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-base font-semibold">{scenario.title}</h2>
          <p className="mt-2 flex gap-2 text-[13px] italic leading-relaxed text-muted-foreground">
            <Quote className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            {scenario.prompt}
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={running}
          className="inline-flex flex-shrink-0 items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent-600 disabled:opacity-60"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? "Running" : "Run the scenario"}
        </button>
      </div>

      <AnimatePresence>
        {done && !running ? (
          <motion.p
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-4 border-t border-border/50 pt-4 text-[13px] leading-relaxed text-muted-foreground"
          >
            {scenario.moral}
          </motion.p>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function FiredList({ fired }: { fired: Fired[] }) {
  if (!fired.length) {
    return (
      <div className="rounded-2xl border border-dashed border-border px-5 py-8 text-center text-[13px] text-muted-foreground">
        Calls appear here as they are gated.
      </div>
    );
  }
  return (
    <ol className="space-y-2">
      {fired.map(({ narration, result }) => (
        <li key={result.decision_id} className="glass-card rounded-xl px-4 py-3">
          <div className="flex flex-wrap items-center gap-2.5">
            <VerdictPill verdict={result.record.causes.every((c) => c.magnitude === null) && result.record.causes.length > 0 ? "unknown" : result.verdict} />
            <span className="font-mono text-[13px]">{result.record.tool}</span>
            <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
              <span className="tnum">{result.trace.elapsed_ms.toFixed(2)} ms</span>
              <Link
                href={`/decisions/${result.decision_id}`}
                className="inline-flex items-center gap-0.5 text-accent hover:underline"
              >
                Evidence <ChevronRight className="h-3 w-3" />
              </Link>
            </span>
          </div>
          {narration ? (
            <p className="mt-1.5 text-[13px] italic text-muted-foreground">“{narration}”</p>
          ) : null}
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {result.record.causes.map((c) => (
              <span key={c.pointer} className="tnum">
                <span className="font-mono">{c.pointer}</span>{" "}
                {c.magnitude === null ? "unsizeable" : `${n(c.magnitude)} ${c.unit}`}
                {c.ceiling !== null ? ` / ceiling ${n(c.ceiling)}` : ""}
              </span>
            ))}
          </div>
        </li>
      ))}
    </ol>
  );
}

function ManualCall({
  tool,
  target,
  disabled,
  fixture,
  onTool,
  onTarget,
  onFire,
}: {
  tool: string;
  target: string;
  disabled: boolean;
  fixture: { id: string; name: string; members: number }[] | null;
  onTool: (v: string) => void;
  onTarget: (v: string) => void;
  onFire: () => void;
}) {
  return (
    <div className="glass-card h-fit rounded-2xl p-5">
      <h3 className="text-sm font-semibold">Fire your own</h3>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
        Same endpoint, same engine. Pick anything — including a target the gate cannot size.
      </p>

      <label className="mt-4 block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Tool
      </label>
      <select
        value={tool}
        onChange={(e) => onTool(e.target.value)}
        className="glass-button mt-1 w-full rounded-lg px-3 py-2 text-sm"
      >
        <option value="remove_group_members">remove_group_members</option>
        <option value="delete_group">delete_group</option>
        <option value="send_email">send_email</option>
        <option value="read_group">read_group (ungated)</option>
      </select>

      <label className="mt-3 block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Target
      </label>
      <select
        value={target}
        onChange={(e) => onTarget(e.target.value)}
        className="glass-button mt-1 w-full rounded-lg px-3 py-2 text-sm"
      >
        {(fixture ?? []).map((g) => (
          <option key={g.id} value={g.id}>
            {g.name} — {n(g.members)}
          </option>
        ))}
      </select>

      <button
        onClick={onFire}
        disabled={disabled}
        className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold glass-button disabled:opacity-60"
      >
        <Send className="h-4 w-4" /> Send it through the gate
      </button>

      {fixture ? (
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          Sizes shown are what the fixture declares. The resolver has to independently arrive at the
          same number — that is the check you are watching.
        </p>
      ) : null}
    </div>
  );
}
