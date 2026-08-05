"use client";

/**
 * Every decision the gate has made.
 *
 * Modelled on clarity-platform's `AutomationExecutions` — status pill, target, timing, drill-in,
 * with real skeleton / error / empty states rather than a blank screen while fetching.
 *
 * One borrowed rule worth keeping, from that file's own comment: an unrecognised status is neutral,
 * never amber. Here amber means "a human has to decide", and a decision the console cannot classify
 * is not that.
 */

import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { Failed, Loading, Page } from "@/components/Page";
import { ListChecks } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { VerdictPill } from "@/components/Verdict";
import { useAsync } from "@/components/Page";
import { api, type DecisionSummary } from "@/lib/api";
import { n } from "@/lib/utils";

export default function DecisionsPage() {
  const { data, error, loading, reload } = useAsync(() => api.decisions());

  return (
    <Page
      title="Decisions"
      lede="Every call the gate has seen, newest first. Each one is replayable from its stored record."
    >
      {loading && !data ? <Loading label="Reading records" /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        data.decisions.length === 0 ? (
          <EmptyState
            size="section"
            icon={ListChecks}
            title="Nothing gated yet"
            description="Decisions appear here the moment a tool call goes through the gate."
            action={{ label: "Go to the live gate", href: "/gate" }}
          />
        ) : (
          <ol className="space-y-2">
            {data.decisions.map((d) => (
              <Row key={d.decision_id} decision={d} />
            ))}
          </ol>
        )
      ) : null}
    </Page>
  );
}

function Row({ decision }: { decision: DecisionSummary }) {
  // A call whose every gated parameter came back unsizeable is a different statement from one that
  // was judged too big, and it gets the hatched treatment rather than borrowing a verdict colour.
  const unsizeable =
    decision.magnitudes.length > 0 && decision.magnitudes.every((m) => m.magnitude === null);

  return (
    <li>
      <Link
        href={`/decision?id=${decision.decision_id}`}
        className="panel flex flex-wrap items-center gap-x-4 gap-y-2 rounded-full px-4 py-3 transition-colors hover:bg-foreground/[0.04]"
      >
        <VerdictPill verdict={unsizeable ? "unknown" : decision.verdict} />
        <span className="font-mono text-[13px]">{decision.tool}</span>

        {/* A row whose magnitude was invented has to say so beside the number, not somewhere a
            reader has to go looking. The console defaults to the built-in tenant whenever there is
            no credential, which is most of the time anybody is looking at it. */}
        {decision.synthetic ? (
          <span
            className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
            title="Magnitudes from the built-in tenant, not from a provider."
          >
            synthetic
          </span>
        ) : null}

        <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {decision.magnitudes.map((m) => (
            <span key={m.pointer} className="tnum">
              {m.magnitude === null ? (
                <span className="hatched rounded px-1.5 py-0.5">unsizeable</span>
              ) : (
                <>
                  {n(m.magnitude)} {m.unit}
                </>
              )}
            </span>
          ))}
        </span>

        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          {decision.mode === "observe" ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium">
              observed
            </span>
          ) : null}
          <time dateTime={decision.decided_at}>{ago(decision.decided_at)}</time>
          <ChevronRight className="h-4 w-4" />
        </span>
      </Link>
    </li>
  );
}

function ago(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
