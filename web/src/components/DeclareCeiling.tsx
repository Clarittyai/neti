"use client";

/**
 * Declaring a ceiling from the console, without the console ever choosing the number.
 *
 * The policy page was read-only, and on a fresh install every row said *no ceiling — resolves and
 * records, cannot block*. That describes a gate that cannot yet do the thing the product exists for,
 * to somebody with no obvious way to change it. The old comment said editing here "would make the
 * number something the console owns" — which is the right worry about the wrong mechanism. What must
 * not happen is a number being *inferred*; a person typing one into a field and reading the diff
 * before it is written is exactly as declared as a person typing it into the file.
 *
 * So three things hold:
 *
 * 1. **Nothing is suggested.** The observed distribution from their own traffic is shown beside the
 *    field — p50, p95, max — because that is what makes a number choosable. It is never prefilled.
 *    `neti propose` is the command that suggests, and it prints a fragment for a human to accept.
 * 2. **The diff comes before the write.** Same contract `neti install` gives `.claude/settings.json`:
 *    read the change, then agree to it. The file is backed up, and every comment survives — the edit
 *    is a text splice, not a YAML round trip, because the shipped policy is mostly comments and they
 *    are most of its value.
 * 3. **The impact is stated in their own recorded calls.** "This would have interrupted 4 of the 37
 *    Glob calls in this file" is the sentence that turns a guess into a decision.
 */

import { useState } from "react";
import { AlertTriangle, Plus, X } from "lucide-react";

import { api, type Verdict } from "@/lib/api";
import { cn, n } from "@/lib/utils";

interface Draft {
  above: string;
  verdict: Verdict;
}

interface Plan {
  path: string;
  diff: string;
  replaced: boolean;
  warnings: string[];
  changed: boolean;
  applied: boolean;
  backup: string | null;
}

export function DeclareCeiling({
  tool,
  pointer,
  observed,
  unit,
  onDone,
  onCancel,
}: {
  tool: string;
  pointer: string;
  /** Every magnitude this gate has actually resolved, from the record file. Empty is a real and
   *  common state — an install with no traffic yet — and the copy says so rather than showing
   *  zeros that look like measurements. */
  observed: number[];
  unit: string | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [bands, setBands] = useState<Draft[]>([{ above: "", verdict: "block" }]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const sorted = [...observed].sort((a, b) => a - b);
  const at = (q: number) =>
    sorted.length ? sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)] : null;

  const payload = bands
    .filter((b) => b.above.trim() !== "")
    .map((b) => ({ above: Number(b.above), verdict: b.verdict }));

  // What this ceiling would have done to calls already in the record file. The lowest band that a
  // magnitude exceeds is the one that decides, and anything at or below every band passes.
  const wouldStop = payload.length
    ? observed.filter((m) => {
        const tripped = payload.filter((b) => m > b.above).map((b) => b.verdict);
        return tripped.includes("block") || tripped.includes("confirm");
      }).length
    : 0;

  const send = (apply: boolean) => {
    setBusy(true);
    setError(null);
    api
      .setCeiling({ tool, pointer, bands: payload, apply })
      .then((p) => {
        setPlan(p as unknown as Plan);
        if (apply) onDone();
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="border-l-2 border-accent bg-accent/[0.03] py-4 pl-4 pr-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-[13px] font-semibold">
          Declare a ceiling for <code className="font-mono">{tool}</code>{" "}
          <code className="font-mono">{pointer}</code>
        </h3>
        <button
          type="button"
          onClick={onCancel}
          className="ml-auto inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground"
        >
          <X className="h-3 w-3" />
          Close
        </button>
      </div>

      {/* Their own distribution, never prefilled into the field. This is what makes a number
          choosable; choosing it is still theirs. */}
      <p className="mt-2 text-[12px] leading-relaxed text-muted-foreground">
        {sorted.length ? (
          <>
            <span className="tnum">{sorted.length}</span> recorded call
            {sorted.length === 1 ? "" : "s"} here — p50{" "}
            <span className="tnum font-medium text-foreground">{n(at(0.5) ?? 0)}</span>, p95{" "}
            <span className="tnum font-medium text-foreground">{n(at(0.95) ?? 0)}</span>, max{" "}
            <span className="tnum font-medium text-foreground">{n(sorted[sorted.length - 1])}</span>
            {unit ? ` ${unit}` : ""}.
          </>
        ) : (
          <>
            No traffic recorded for this gate yet, so there is no distribution to choose against. A
            ceiling set now is a guess — run a week in observe mode first, or set one anyway and
            change it when you know.
          </>
        )}
      </p>

      <div className="mt-3 space-y-2">
        {bands.map((band, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <label className="text-[12px] text-muted-foreground" htmlFor={`above-${i}`}>
              above
            </label>
            <input
              id={`above-${i}`}
              value={band.above}
              inputMode="numeric"
              onChange={(e) =>
                setBands((b) =>
                  b.map((x, j) => (j === i ? { ...x, above: e.target.value.replace(/\D/g, "") } : x)),
                )
              }
              placeholder="0"
              className="tnum w-28 rounded-md border border-border bg-transparent px-2.5 py-1.5 text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            <div className="flex gap-1 rounded-full bg-muted p-0.5">
              {(["flag", "confirm", "block"] as Verdict[]).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() =>
                    setBands((b) => b.map((x, j) => (j === i ? { ...x, verdict: v } : x)))
                  }
                  className={cn(
                    "rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors",
                    band.verdict === v
                      ? "bg-background text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {v}
                </button>
              ))}
            </div>
            {bands.length > 1 ? (
              <button
                type="button"
                onClick={() => setBands((b) => b.filter((_, j) => j !== i))}
                className="rounded-full px-2 py-1 text-[11px] text-muted-foreground ring-1 ring-inset ring-border hover:text-foreground"
              >
                remove
              </button>
            ) : null}
          </div>
        ))}

        <button
          type="button"
          onClick={() => setBands((b) => [...b, { above: "", verdict: "confirm" }])}
          className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground"
        >
          <Plus className="h-3 w-3" />
          Another band
        </button>
      </div>

      {payload.length > 0 && sorted.length > 0 ? (
        <p className="mt-3 text-[12px] text-muted-foreground">
          This would have stopped{" "}
          <span className="tnum font-medium text-foreground">{wouldStop}</span> of{" "}
          <span className="tnum">{sorted.length}</span> recorded call
          {sorted.length === 1 ? "" : "s"} at this gate.
        </p>
      ) : null}

      {error ? (
        <p className="mt-3 flex items-start gap-2 text-[12px] leading-relaxed text-[hsl(var(--verdict-block))]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      ) : null}

      {plan && !plan.applied ? (
        <div className="mt-3">
          {plan.warnings.map((w) => (
            <p
              key={w}
              className="mb-2 flex items-start gap-2 text-[12px] leading-relaxed text-[hsl(var(--verdict-confirm))]"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {w}
            </p>
          ))}
          <p className="text-[11px] text-muted-foreground">
            {plan.path} — every other byte, including every comment, is left exactly as it is. The
            previous version is saved beside it.
          </p>
          <pre className="mt-1.5 overflow-x-auto bg-muted/60 px-3 py-2 font-mono text-[12px] leading-relaxed">
            {plan.diff}
          </pre>
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || payload.length === 0}
          onClick={() => send(false)}
          className="rounded-full px-3.5 py-1.5 text-[12px] font-medium ring-1 ring-inset ring-border transition-colors hover:bg-foreground/[0.04] disabled:opacity-40"
        >
          {busy ? "Working" : "Show me the change"}
        </button>
        {/* Writing is deliberately only reachable after the diff has been produced. */}
        {plan && !plan.applied ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => send(true)}
            className="rounded-full bg-accent px-3.5 py-1.5 text-[12px] font-semibold text-accent-foreground transition-colors hover:bg-accent/90 disabled:opacity-40"
          >
            Write it
          </button>
        ) : null}
      </div>
    </div>
  );
}
