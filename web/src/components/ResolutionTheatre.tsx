"use client";

/**
 * The wow moment, and the only component here that really matters.
 *
 * The product is mechanically an integer comparison, which is invisible. So the console's whole job
 * is to make the *resolution* visible: the instant `"engineering-all"` stops being a string on an
 * allowlist and becomes 41,203 people. Everything else on screen is supporting evidence.
 *
 * Three decisions worth defending, because each one is a place where the obvious choice is wrong:
 *
 * 1. **The number does not count up.** RESOLVER_CONTRACT.md rule 2 says "you cannot half-read an
 *    integer". An odometer rolling 0 → 41,203 depicts a progressive enumeration, which is precisely
 *    the thing this product refuses to do — the whole reason it targets Graph's `$count` rather than
 *    Google's pagination. The digits blur-land at once instead. Width is reserved so nothing shifts.
 *
 * 2. **The number is not red.** 41,203 renders in plain foreground. The number is a *fact* produced
 *    by the resolver; the verdict is a *judgement* produced by the policy. Colouring the fact red
 *    conflates the two, and conflating them is exactly what a security architect will pick at.
 *
 * 3. **The ceiling meter is linear, not logarithmic.** On a linear track the ceiling marker sits at
 *    0.5% — a hairline near the left edge — and the fill blows straight through it and runs off the
 *    right under a fade. That reads as "206× over" before anyone reads the label. A log scale would
 *    make 41,203 look like a reasonable overshoot, which is the opposite of the truth.
 *
 * Timings shown are **measured**, not invented: they come from the collector that timestamped its
 * own arrivals inside `Engine.gate`. The playback pacing is a UI choice and is labelled as one.
 */

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Server, Zap } from "lucide-react";

import { cn, n } from "@/lib/utils";
import { VerdictPlate } from "@/components/Verdict";
import type { GateResult, TraceStage } from "@/lib/api";

/** How long each stage waits before the next appears. Presentation only — never a real delay. */
const BEAT_MS = 260;

export function ResolutionTheatre({
  result,
  playbackMs = BEAT_MS,
  onDone,
}: {
  result: GateResult | null;
  playbackMs?: number;
  onDone?: () => void;
}) {
  // Memoised because the `?? []` fallback is a fresh array on every render, which would make every
  // downstream useMemo recompute and defeat the point of having them.
  const stages = useMemo(() => result?.trace.stages ?? [], [result]);
  const [shown, setShown] = useState(0);

  // Step the stages in. Restarting whenever the decision id changes is what lets the same component
  // replay a fresh call without remounting.
  useEffect(() => {
    setShown(0);
    if (!result) return;
    let index = 0;
    const id = setInterval(() => {
      index += 1;
      setShown(index);
      if (index >= stages.length) {
        clearInterval(id);
        onDone?.();
      }
    }, playbackMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result?.decision_id, playbackMs]);

  const counted = useMemo(() => stages.filter((s) => s.key === "count"), [stages]);
  const revealed = stages.slice(0, shown);
  const countsIn = counted.filter((s) => revealed.includes(s));
  const primary = countsIn[0];
  const secondary = countsIn[1];
  const settled = shown >= stages.length && stages.length > 0;

  if (!result) return <TheatreIdle />;

  const magnitude = num(primary?.payload.magnitude);
  const unit = str(primary?.payload.unit);
  const ceiling = firstCeiling(result);

  return (
    <div
      className={cn(
        "panel overflow-hidden transition-shadow duration-500",
        settled && result.verdict === "block" && "ring-[hsl(var(--verdict-block))]/40",
        settled && result.verdict === "allow" && "ring-[hsl(var(--verdict-allow))]/30",
      )}
    >
      <div className="grid gap-px bg-border/40 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.2fr)_minmax(0,1fr)]">
        <CallZone result={result} />
        <StageZone stages={revealed} total={stages.length} />
        <MagnitudeZone
          magnitude={magnitude}
          unit={unit}
          secondary={secondary}
          ceiling={ceiling}
          settled={settled}
          verdict={result.verdict}
          unresolvedReason={
            primary && magnitude === null ? str(primary.payload.state) : undefined
          }
        />
      </div>

      <UpstreamLane result={result} settled={settled} />
    </div>
  );
}

// ---------------------------------------------------------------------------- zones

function CallZone({ result }: { result: GateResult }) {
  const args = result.record.args ?? {};
  const [key, value] = Object.entries(args)[0] ?? ["", ""];
  return (
    <div className="bg-card p-5">
      <ZoneLabel>The call</ZoneLabel>
      <div className="mt-3 font-mono text-sm leading-relaxed">
        <span className="text-foreground">{result.record.tool}</span>
        <span className="text-muted-foreground">(</span>
        <div className="pl-4">
          <span className="text-muted-foreground">{key}: </span>
          {/* The same object leaves here and arrives in the magnitude zone. One layoutId. */}
          <motion.span
            layoutId="target-chip"
            className="inline-block rounded-full bg-accent/15 px-1.5 py-0.5 text-accent ring-1 ring-inset ring-accent/30"
          >
            &quot;{String(value)}&quot;
          </motion.span>
        </div>
        <span className="text-muted-foreground">)</span>
      </div>
      <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
        Authorization already said yes: the tool is permitted and the target is on the allowlist.
        Nothing upstream asked how big it was.
      </p>
    </div>
  );
}

function StageZone({ stages, total }: { stages: TraceStage[]; total: number }) {
  return (
    <div className="bg-card p-5">
      <div className="flex items-baseline justify-between">
        <ZoneLabel>The resolution</ZoneLabel>
        <span className="tnum text-[11px] text-muted-foreground">
          {stages.length}/{total}
        </span>
      </div>
      <ol className="mt-3 space-y-2">
        <AnimatePresence initial={false}>
          {stages.map((stage, i) => (
            <motion.li
              key={`${stage.key}-${i}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
              className="flex gap-2.5"
            >
              <span
                className={cn(
                  "mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full",
                  stage.key === "count" ? "bg-accent" : "bg-muted-foreground/40",
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[13px] font-medium">{stage.label}</span>
                  {/* Measured, not staged. */}
                  <span className="tnum text-[10px] text-muted-foreground">
                    {stage.took_ms < 0.01 ? "<0.01" : stage.took_ms.toFixed(2)} ms
                  </span>
                </div>
                <p className="mt-0.5 break-all font-mono text-[10.5px] leading-relaxed text-muted-foreground">
                  {stage.detail}
                </p>
              </div>
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
    </div>
  );
}

function MagnitudeZone({
  magnitude,
  unit,
  secondary,
  ceiling,
  settled,
  verdict,
  unresolvedReason,
}: {
  magnitude: number | null;
  unit: string | null;
  secondary?: TraceStage;
  ceiling: number | null;
  settled: boolean;
  verdict: GateResult["verdict"];
  unresolvedReason?: string | null;
}) {
  const over = magnitude !== null && ceiling !== null && magnitude > ceiling;
  return (
    <div className="flex flex-col bg-card p-5">
      <ZoneLabel>The magnitude</ZoneLabel>

      <div className="mt-3 flex-1">
        {magnitude === null && !unresolvedReason ? (
          // Width reserved so the digits do not shove the layout when they land.
          <div className="h-[52px] w-40 animate-pulse bg-muted/60" />
        ) : magnitude === null ? (
          <div className="hatched px-3 py-4 ring-1 ring-inset ring-border">
            <p className="text-lg font-semibold">Could not size</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              The gate does not guess, and a failed lookup is never read as zero.
            </p>
          </div>
        ) : (
          <div key={magnitude} className="magnitude-land">
            {/* Plain foreground. The number is a fact; the verdict is a judgement. */}
            <div className="tnum text-[44px] font-semibold leading-none tracking-tight">
              {n(magnitude)}
            </div>
            <div className="mt-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              {unit}
            </div>
            {secondary ? (
              <div className="mt-3 tnum text-sm text-muted-foreground">
                + {n(num(secondary.payload.magnitude))} {str(secondary.payload.unit)}
              </div>
            ) : null}
          </div>
        )}
      </div>

      {magnitude !== null && ceiling !== null ? (
        <CeilingMeter magnitude={magnitude} ceiling={ceiling} over={over} />
      ) : null}

      <AnimatePresence>
        {settled ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 26 }}
            className="mt-4"
          >
            <VerdictPlate verdict={magnitude === null ? "unknown" : verdict} />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

/**
 * Linear on purpose. At a ceiling of 200 against 41,203 the marker sits at half a percent, the fill
 * runs straight off the right edge, and the picture says "not close" without needing the label.
 */
function CeilingMeter({
  magnitude,
  ceiling,
  over,
}: {
  magnitude: number;
  ceiling: number;
  over: boolean;
}) {
  const markerPct = Math.min(100, (ceiling / Math.max(magnitude, 1)) * 100);
  const multiple = magnitude / Math.max(ceiling, 1);
  return (
    <div className="mt-4">
      <div className="relative h-2 overflow-hidden bg-muted">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: "100%" }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: 0.15 }}
          className={cn(
            "absolute inset-y-0 left-0",
            over ? "bg-[hsl(var(--verdict-block))]" : "bg-[hsl(var(--verdict-allow))]",
          )}
          style={{
            maskImage: "linear-gradient(to right, #000 82%, transparent 100%)",
            WebkitMaskImage: "linear-gradient(to right, #000 82%, transparent 100%)",
          }}
        />
        <div
          className="absolute inset-y-0 w-px bg-foreground/70"
          style={{ left: `${markerPct}%` }}
          aria-hidden
        />
      </div>
      <div className="mt-1.5 flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span className="tnum">ceiling {n(ceiling)}</span>
        {over ? (
          <span className="tnum font-semibold text-[hsl(var(--verdict-block))]">
            {multiple >= 10 ? Math.round(multiple) : multiple.toFixed(1)}× over
          </span>
        ) : (
          <span className="tnum">within</span>
        )}
      </div>
    </div>
  );
}

/** Whether the call actually left the building. The strongest single claim on the screen. */
function UpstreamLane({ result, settled }: { result: GateResult; settled: boolean }) {
  const reached = result.proceeds;
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-border/40 bg-muted/20 px-5 py-3 text-xs">
      <span className="flex items-center gap-1.5 text-muted-foreground">
        <Zap className="h-3.5 w-3.5" /> agent
      </span>
      <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="font-medium text-accent">neti</span>
      <ArrowRight
        className={cn("h-3.5 w-3.5", reached ? "text-muted-foreground" : "text-[hsl(var(--verdict-block))]")}
      />
      <span
        className={cn(
          "flex items-center gap-1.5",
          reached ? "text-muted-foreground" : "text-[hsl(var(--verdict-block))] line-through",
        )}
      >
        <Server className="h-3.5 w-3.5" /> Microsoft Graph
      </span>
      {settled ? (
        <span className="ml-auto tnum text-muted-foreground">
          resolved in {result.trace.elapsed_ms.toFixed(2)} ms
          {result.mode === "observe" ? " · observing, call forwarded" : ""}
        </span>
      ) : null}
    </div>
  );
}

function TheatreIdle() {
  return (
    <div className="panel flex min-h-[320px] flex-col items-center justify-center p-10 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full bg-accent/10 text-accent">
        <Zap className="h-5 w-5" />
      </span>
      <p className="mt-3 text-sm font-semibold">Nothing has been gated yet</p>
      <p className="mt-1 max-w-xs text-[13px] leading-relaxed text-muted-foreground">
        Run the scenario, or fire a call of your own, and watch the argument become a number before
        anything executes.
      </p>
    </div>
  );
}

function ZoneLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------- helpers

const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

/** The tightest ceiling the deciding argument breached, for the meter. */
function firstCeiling(result: GateResult): number | null {
  for (const cause of result.record.causes) {
    if (cause.ceiling !== null) return cause.ceiling;
  }
  return null;
}
