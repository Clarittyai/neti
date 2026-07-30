"use client";

/**
 * Overview — the hour-one artifact plus whatever has happened since.
 *
 * The reachability table is the finding that needs no traffic and no declared ceilings: *your agent
 * holds a credential that can, in one call, reach 52,400 principals*. It is a statement about
 * capability rather than about an incident, and the page says so — a sharp reader will make that
 * distinction themselves, and it is better made for them.
 *
 * The one chart here is a log-scale strip plot, chosen because the data's job is to show that normal
 * work is tiny and the outliers are enormous. On a linear axis every ordinary call collapses onto
 * the axis and the picture says nothing. One series, so no legend; the ceiling is a rule, not a
 * second series.
 */

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import { Empty, Failed, Loading, Page, Stat, useAsync } from "@/components/Page";
import { api, type InventoryRow } from "@/lib/api";
import { cn, n } from "@/lib/utils";

interface Distribution {
  tool: string;
  pointer: string;
  unit: string;
  n: number;
  p50: number;
  p95: number;
  max: number;
  magnitudes: number[];
  over_ceiling: { decision_id: string; observed: number; ceiling: number }[];
}
interface Report {
  decisions: number;
  verdicts: Record<string, number>;
  distributions: Distribution[];
}

export default function OverviewPage() {
  const inventory = useAsync(() => api.inventory());
  const report = useAsync(() => api.report() as Promise<unknown> as Promise<Report>);

  const rows = inventory.data?.rows ?? [];
  const uncapped = rows.filter((r) => !r.has_ceiling);
  const dists = (report.data?.distributions ?? []).filter((d) => d.n > 0);

  return (
    <Page
      title="Overview"
      lede="What your agents can reach, and what they have actually done."
    >
      {inventory.error ? (
        <Failed error={inventory.error} onRetry={inventory.reload} />
      ) : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          value={n(Math.max(0, ...rows.map((r) => r.reachable ?? 0)))}
          label="reachable in one call"
          hint="The largest set a single gated call could touch. Capability, not an incident."
        />
        <Stat value={n(report.data?.decisions ?? 0)} label="decisions recorded" />
        <Stat
          value={n(report.data?.verdicts?.block ?? 0)}
          label="would have been blocked"
          tone={(report.data?.verdicts?.block ?? 0) > 0 ? "block" : undefined}
        />
      </div>

      {uncapped.length > 0 ? (
        <div className="mt-4 flex flex-wrap items-center gap-2.5 rounded-xl border border-[hsl(var(--verdict-confirm))]/30 bg-[hsl(var(--verdict-confirm))]/[0.06] px-4 py-3 text-[13px]">
          <AlertTriangle className="h-4 w-4 flex-shrink-0 text-[hsl(var(--verdict-confirm))]" />
          <span className="text-muted-foreground">
            {uncapped.length} gated parameter{uncapped.length === 1 ? "" : "s"} have no ceiling
            declared. They resolve and record, but they cannot block.
          </span>
          <Link href="/policy" className="ml-auto font-medium text-accent hover:underline">
            Policy
          </Link>
        </div>
      ) : null}

      <section className="mt-8">
        <h2 className="text-sm font-semibold">What each tool can reach</h2>
        <p className="mt-1 text-[13px] text-muted-foreground">
          No traffic required — this is read straight from the directory.
        </p>
        {inventory.loading && !inventory.data ? (
          <div className="mt-3">
            <Loading label="Reading the directory" />
          </div>
        ) : (
          <div className="glass-card mt-3 overflow-x-auto rounded-2xl">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                  <th className="px-5 py-3 font-medium">tool</th>
                  <th className="px-5 py-3 font-medium">parameter</th>
                  <th className="px-5 py-3 text-right font-medium">max reachable</th>
                  <th className="px-5 py-3 font-medium">status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <InventoryRowView key={`${r.tool}${r.param}`} row={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 max-w-3xl text-[11px] leading-relaxed text-muted-foreground">
          These are upper bounds on capability, never measurements of a call, and the decision
          procedure refuses to allow on one — a bound can prove something is too big, not that it is
          small enough.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-semibold">Observed magnitudes</h2>
        {report.loading && !report.data ? (
          <div className="mt-3">
            <Loading />
          </div>
        ) : dists.length === 0 ? (
          <div className="mt-3">
            <Empty
              title="No traffic yet"
              body="Run the scenario and the distribution of what your agents actually touch appears here."
              action={
                <Link href="/gate" className="text-sm font-medium text-accent hover:underline">
                  Go to the live gate
                </Link>
              }
            />
          </div>
        ) : (
          // One plot per gated parameter rather than one combined axis: principals and apps are
          // different units, and putting two units on one scale is the dual-axis mistake wearing
          // a different hat.
          dists.map((d) => <StripPlot key={`${d.tool}${d.pointer}`} dist={d} />)
        )}
      </section>
    </Page>
  );
}

function InventoryRowView({ row }: { row: InventoryRow }) {
  return (
    <tr className="border-b border-border/40 last:border-0">
      <td className="px-5 py-3 font-mono text-[13px]">{row.tool}</td>
      <td className="px-5 py-3 font-mono text-[13px] text-muted-foreground">{row.param}</td>
      <td className="tnum px-5 py-3 text-right font-semibold">
        {row.reachable === null ? "—" : n(row.reachable)}{" "}
        <span className="font-normal text-muted-foreground">{row.unit}</span>
      </td>
      <td className="px-5 py-3 text-[13px]">
        {row.has_ceiling ? (
          <span className="text-muted-foreground">
            blocks above <span className="tnum">{n(row.block_at)}</span>
          </span>
        ) : (
          <span className="text-[hsl(var(--verdict-confirm))]">no ceiling declared</span>
        )}
      </td>
    </tr>
  );
}

/**
 * Log scale, because the story is four orders of magnitude wide. One series, one hue, the ceiling
 * as a rule rather than a second series — a chart that needs a legend to say "this line is the
 * limit" has already failed.
 */
function StripPlot({ dist }: { dist: Distribution }) {
  const ceiling = dist.over_ceiling[0]?.ceiling ?? null;
  const values = dist.magnitudes.filter((v) => v > 0);
  if (!values.length) return null;

  const max = Math.max(...values, ceiling ?? 0);
  // Inset by a mark's width at each end, so the largest value — the one anyone came to look at —
  // sits inside the plot rather than half-clipped by the card edge.
  const pos = (v: number) =>
    2 + (Math.log10(Math.max(v, 1)) / Math.log10(Math.max(max, 10))) * 96;

  return (
    <div className="glass-card mt-3 rounded-2xl p-5">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[13px]">
        <span className="font-mono">{dist.tool}</span>
        <span className="font-mono text-muted-foreground">{dist.pointer}</span>
        <span className="tnum ml-auto text-muted-foreground">
          n={n(dist.n)} · p50 {n(dist.p50)} · p95 {n(dist.p95)} · max {n(dist.max)}
        </span>
      </div>

      <div className="relative mt-6 h-16">
        <div className="absolute inset-x-0 top-8 h-px bg-border" />
        {ceiling !== null ? (
          <div
            className="absolute bottom-0 top-0 w-px bg-[hsl(var(--verdict-block))]/60"
            style={{ left: `${pos(ceiling)}%` }}
          >
            <span className="tnum absolute -top-1 left-1.5 whitespace-nowrap text-[10px] text-[hsl(var(--verdict-block))]">
              ceiling {n(ceiling)}
            </span>
          </div>
        ) : null}
        {values.map((v, i) => (
          <span
            key={`${v}-${i}`}
            className={cn(
              "absolute top-8 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card",
              ceiling !== null && v > ceiling
                ? "bg-[hsl(var(--verdict-block))]"
                : "bg-accent/70",
            )}
            style={{ left: `${pos(v)}%` }}
            title={`${n(v)} ${dist.unit}`}
          />
        ))}
        <div className="tnum absolute inset-x-0 bottom-0 flex justify-between text-[10px] text-muted-foreground">
          <span>1</span>
          <span>{n(max)}</span>
        </div>
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
        Log scale — the range is four orders of magnitude wide, and on a linear axis every ordinary
        call would collapse onto the left edge. Each dot is one gated call.
      </p>
    </div>
  );
}
