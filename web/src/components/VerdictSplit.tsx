"use client";

/**
 * What the gate actually did, as one proportional bar.
 *
 * The overview had three totals and a per-parameter distribution, and nothing that showed the
 * *shape* of a session at a glance: whether the gate is mostly waving traffic through or mostly
 * standing in front of it. That is the first question anybody asks of a policy they just wrote, and
 * answering it took reading three numbers and doing arithmetic.
 *
 * One bar, three segments, in the reserved verdict colours — so the same red that means "blocked"
 * in a decision row means "blocked" here, which is the whole reason those three hues are reserved.
 *
 * Deliberately not a donut. A donut asks you to compare arc lengths; a bar puts the three
 * quantities on one axis where the eye is good at it, and it degrades to a single full-width
 * segment when a session is all one verdict rather than becoming a circle with one colour.
 */
import { cn, n } from "@/lib/utils";

type Verdicts = { allow?: number; confirm?: number; block?: number };

const SEGMENTS = [
  { key: "allow", label: "allowed", tone: "allow", hint: "under every ceiling, straight through" },
  { key: "confirm", label: "needs approval", tone: "confirm", hint: "over a confirm band" },
  { key: "block", label: "blocked", tone: "block", hint: "over a block band, stopped before it ran" },
] as const;

export function VerdictSplit({ verdicts, total }: { verdicts?: Verdicts; total: number }) {
  if (!total) return null;

  const counts = SEGMENTS.map((s) => ({ ...s, count: verdicts?.[s.key] ?? 0 }));
  const seen = counts.reduce((sum, s) => sum + s.count, 0) || 1;

  return (
    <div className="mt-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        What the gate did
      </p>

      {/* The bar. `flex` with per-segment basis rather than a stack of absolutely-positioned
          slivers, so a segment that rounds to under a pixel still holds its place in the row. */}
      <div className="mt-3 flex h-2 w-full overflow-hidden rounded-full bg-secondary">
        {counts.map((s) =>
          s.count > 0 ? (
            <div
              key={s.key}
              style={{ width: `${(s.count / seen) * 100}%` }}
              className={cn(
                s.tone === "allow" && "bg-[hsl(var(--verdict-allow))]",
                s.tone === "confirm" && "bg-[hsl(var(--verdict-confirm))]",
                s.tone === "block" && "bg-[hsl(var(--verdict-block))]",
              )}
              title={`${s.label}: ${s.count}`}
            />
          ) : null,
        )}
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        {counts.map((s) => (
          <div key={s.key}>
            <div className="flex items-baseline gap-2">
              <span
                className={cn(
                  "h-2 w-2 flex-shrink-0 rounded-full",
                  s.tone === "allow" && "bg-[hsl(var(--verdict-allow))]",
                  s.tone === "confirm" && "bg-[hsl(var(--verdict-confirm))]",
                  s.tone === "block" && "bg-[hsl(var(--verdict-block))]",
                )}
                aria-hidden
              />
              <span className="tnum text-lg font-semibold text-foreground">{n(s.count)}</span>
              <span className="text-[13px] text-muted-foreground">{s.label}</span>
            </div>
            <p className="mt-0.5 pl-4 text-[11px] leading-relaxed text-muted-foreground">{s.hint}</p>
          </div>
        ))}
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
        Proportions of {n(seen)} gated call{seen === 1 ? "" : "s"} in this record file. A session
        that is nearly all green is a policy that is not costing anybody anything; one that is
        nearly all red is a ceiling set below how the agent actually works.
      </p>
    </div>
  );
}
