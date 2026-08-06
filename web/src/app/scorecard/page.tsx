"use client";

/**
 * What this catches, what it misses, and what nobody has measured yet.
 *
 * This screen exists because the plan makes it a non-negotiable, and it is the one a security
 * audience will trust most — everything else in the console is the product working. `neti score`
 * puts the honest number at 3 of 7, and the four incidents it cannot catch are printed here with
 * their reasons and their sources, in the same weight as the three it can.
 *
 * The coverage figure is deliberately not dressed up. No progress ring, no "43%", no green. A
 * fraction under a label, and the misses immediately below it — a reader who came here to find the
 * catch should find it stated before they can ask.
 */

import { AlertTriangle, ArrowUpRight, Check, CircleSlash, Clock } from "lucide-react";

import { Failed, Loading, Page, Stat, useAsync } from "@/components/Page";
import { api, type Coverage, type Incident } from "@/lib/api";
import { cn, n } from "@/lib/utils";

const BUCKETS: { key: Coverage; title: string; blurb: string }[] = [
  {
    key: "caught",
    title: "Caught",
    blurb: "One resolution sizes the call before it runs, in a unit a ceiling can be declared in.",
  },
  {
    key: "needs_resolver",
    title: "Missed — no resolver",
    blurb: "The shape fits, but the resolver that would size it does not exist yet.",
  },
  {
    key: "needs_budget",
    title: "Missed per call — needs a session budget",
    blurb:
      "Each call is small and the total is not. A per-call ceiling is structurally blind to this; only a declared cumulative budget sees it.",
  },
  {
    key: "out_of_scope",
    title: "Structurally invisible",
    blurb: "Consequence is not cardinality. Nothing about this approach will ever catch it.",
  },
];

export default function ScorecardPage() {
  const { data, error, loading, reload } = useAsync(() => api.scorecard());

  return (
    <Page
      title="Scorecard"
      lede="What the gate catches, what it misses, and what has not been measured. The misses are listed in the same detail as the catches."
    >
      {loading && !data ? <Loading label="Scoring" /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        <div className="space-y-8">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat
              value={`${data.coverage.caught} of ${data.coverage.total}`}
              label="incidents this would have caught"
              hint="Counted against a corpus assembled from public reports. Four are misses, and each one says why below."
            />
            <Stat
              value={`${Math.round(data.friction.interrupt_rate * 100)}%`}
              label="of gated calls were interrupted"
              hint={`${data.friction.stopped} of ${data.friction.calls} in this session — ${data.friction.blocked} blocked, ${data.friction.confirmed} sent for approval. A scripted session, not a measurement of your own traffic.`}
            />
            <Stat
              value={n(data.unresolved_parameters)}
              label="parameters that could not be sized"
              hint="A failed lookup is never read as zero — the declared on_unresolved policy decides."
              tone={data.unresolved_parameters > 0 ? "confirm" : undefined}
            />
          </div>

          {/* `space-y` between rows that each carry a `border-t` is what made this look cut: the
              rule floats detached 8px above its own content, so every incident read as a slab that
              had lost its top. A list is continuous — one rule between neighbours, none floating —
              and the space goes where it means something, above the bucket heading. */}
          <div>
            {BUCKETS.map((b) => {
              const rows = data.incidents[b.key] ?? [];
              if (!rows.length) return null;
              return (
                <section key={b.key} className="mt-10 first:mt-0">
                  <div className="flex flex-wrap items-baseline gap-x-3">
                    <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight">
                      <BucketIcon coverage={b.key} />
                      {b.title}
                    </h2>
                    <span className="tnum text-xs text-muted-foreground">
                      {rows.length} {rows.length === 1 ? "incident" : "incidents"}
                    </span>
                  </div>
                  <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
                    {b.blurb}
                  </p>
                  <div className="mt-4 border-b border-border">
                    {rows.map((i) => (
                      <IncidentCard key={i.id} incident={i} />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>

          <section className="pt-2">
            <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight">
              <CircleSlash className="h-4 w-4 text-muted-foreground" />
              What it does not see, by construction
            </h2>
            <p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
              These are not bugs and they are not roadmap. They are the boundary of the claim, and
              they are written down so nobody has to discover them during an incident.
            </p>
            <div className="mt-4 grid gap-x-10 gap-y-3 border-t border-border pt-4 sm:grid-cols-2">
              {Object.entries(data.known_blind_spots).map(([id, text]) => (
                <div key={id} className="flex gap-2.5 text-[13px]">
                  <span className="flex-shrink-0 font-mono text-[11px] leading-5 text-muted-foreground">
                    {id}
                  </span>
                  <span className="leading-relaxed text-muted-foreground">{text}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="pt-2">
            <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight">
              <Clock className="h-4 w-4 text-muted-foreground" />
              Not yet measured
            </h2>
            <p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
              Anything requiring a real tenant is unmeasured and labelled as such. Every latency
              figure in the design documents is modelled, and no published Graph percentile exists to
              model it against.
            </p>
            <ul className="mt-4 space-y-2.5 border-t border-border pt-4">
              {data.not_yet_measured.map((m) => {
                const blocked = /REQUIRES|UNVERIFIED/.test(m);
                return (
                  <li key={m} className="flex gap-2.5 text-[13px] leading-relaxed">
                    <span
                      className={cn(
                        "mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full",
                        blocked ? "bg-[hsl(var(--verdict-confirm))]" : "bg-muted-foreground/50",
                      )}
                      aria-hidden
                    />
                    <span className={blocked ? "" : "text-muted-foreground"}>{m}</span>
                  </li>
                );
              })}
            </ul>
          </section>

          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Produced by <code className="font-mono">neti score</code> against the same incident
            corpus the CLI reads. Nothing on this page is written by hand for the console.
          </p>
        </div>
      ) : null}
    </Page>
  );
}

function BucketIcon({ coverage }: { coverage: Coverage }) {
  if (coverage === "caught")
    return <Check className="h-4 w-4 text-[hsl(var(--verdict-allow))]" strokeWidth={2.5} />;
  if (coverage === "out_of_scope")
    return <CircleSlash className="h-4 w-4 text-muted-foreground" />;
  return <AlertTriangle className="h-4 w-4 text-[hsl(var(--verdict-confirm))]" />;
}

function IncidentCard({ incident }: { incident: Incident }) {
  const caught = incident.coverage === "caught";
  return (
    <div
      className={cn(
        "border-t border-border py-5",
        // Only the catches get an accent edge. Tinting a miss would be the console editorialising
        // about a row whose whole job is to say "this one gets past us". `pl-4` because a rule
        // with text flush against it reads as a crop rather than a marker.
        caught ? "border-l-2 border-l-[hsl(var(--verdict-allow))] pl-4" : "pl-[18px]",
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-medium">{incident.actor}</span>
        <span className="text-xs text-muted-foreground">{incident.date}</span>
        {incident.magnitude !== null ? (
          <span className="tnum ml-auto text-sm font-semibold">
            {n(incident.magnitude)}{" "}
            <span className="font-normal text-muted-foreground">{incident.unit}</span>
          </span>
        ) : (
          <span className="ml-auto text-xs text-muted-foreground">magnitude never established</span>
        )}
      </div>

      <p className="mt-1.5 font-mono text-[13px] leading-relaxed">{incident.what_one_call_did}</p>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span>{incident.authorized ? "authorized · every upstream check said yes" : "unauthorized"}</span>
        <span>reversible: {incident.reversible}</span>
        {incident.gated_unit ? <span>gate sees: {incident.gated_unit}</span> : null}
      </div>

      <p className="mt-3 border-t border-border/40 pt-3 text-[13px] leading-relaxed text-muted-foreground">
        {incident.note}
      </p>

      {incident.source.startsWith("http") ? (
        <a
          href={incident.source}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
        >
          source <ArrowUpRight className="h-3 w-3" />
        </a>
      ) : (
        <p className="mt-2 text-[11px] text-muted-foreground">{incident.source}</p>
      )}
    </div>
  );
}
