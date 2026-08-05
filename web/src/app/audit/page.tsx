"use client";

/**
 * The audit chain.
 *
 * The highest credibility per line of code in the whole console: a table and a button that runs the
 * real `verify_chain` over the real records. Nothing is mocked and nothing is cached — pressing
 * Verify re-reads the file and recomputes every digest.
 *
 * The chain is also where this product recently had a genuine bug: a fresh process appending to an
 * existing file wrote `prev_digest: null` mid-chain, and verification correctly called it a break —
 * a break caused by a restart rather than by tampering, which is the worst possible false alarm an
 * audit surface can raise. Hence the explicit "continues across restarts" note below: it is a claim
 * worth making because it was once untrue.
 */

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, Link2, RefreshCw, ShieldAlert } from "lucide-react";

import { Failed, Loading, Page, Stat, useAsync } from "@/components/Page";
import { EmptyState } from "@/components/ui/empty-state";
import { ChainScene } from "@/components/live/scenes/ChainScene";
import { VerdictPill } from "@/components/Verdict";
import { api } from "@/lib/api";
import { cn, n } from "@/lib/utils";

export default function AuditPage() {
  const { data, error, loading, reload } = useAsync(() => api.audit());
  const [verifying, setVerifying] = useState(false);

  const verify = async () => {
    setVerifying(true);
    reload();
    // Held deliberately: recomputing 20 digests is instantaneous, and a result that appears with no
    // perceptible work reads as a button that did nothing rather than a check that passed.
    setTimeout(() => setVerifying(false), 550);
  };

  return (
    <Page
      title="Audit"
      lede="Every decision is sealed into a hash chain. Verifying recomputes each link from the stored record — it is not a stored result."
      // One accent action per view (DESIGN.md). While the chain is empty the empty state owns the
      // CTA, so the header hides its own — two accent buttons on one screen is two answers to
      // "what should I do here", and there is nothing to verify before anything is recorded.
      actions={
        data && data.count > 0 ? (
          <button
            onClick={() => void verify()}
            disabled={verifying || loading}
            className="inline-flex items-center gap-2 rounded-full bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent-600 disabled:opacity-60"
          >
            <RefreshCw className={cn("h-4 w-4", verifying && "animate-spin")} />
            {verifying ? "Verifying" : "Verify chain"}
          </button>
        ) : null
      }
    >
      {loading && !data ? <Loading label="Reading records" /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        data.count === 0 ? (
          <EmptyState
            size="page"
            scene={<ChainScene />}
            title="No decisions recorded yet"
            description="Run something through the gate and every verdict lands here, sealed to the one before it."
            action={{ label: "Go to the live gate", href: "/gate" }}
          />
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <div
                className={cn(
                  "panel rounded-2xl p-5 ring-1 ring-inset",
                  data.ok
                    ? "ring-[hsl(var(--verdict-allow))]/30"
                    : "ring-[hsl(var(--verdict-block))]/40",
                )}
              >
                <div
                  className={cn(
                    "flex items-center gap-2 text-lg font-semibold",
                    data.ok
                      ? "text-[hsl(var(--verdict-allow))]"
                      : "text-[hsl(var(--verdict-block))]",
                  )}
                >
                  {data.ok ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : (
                    <ShieldAlert className="h-5 w-5" />
                  )}
                  {data.ok ? "Chain intact" : "Chain broken"}
                </div>
                <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
                  {data.ok
                    ? "Every record's digest matches its predecessor. The chain continues across process restarts."
                    : `First mismatch at ${data.broken_at}. A record was altered, removed or reordered after it was written.`}
                </p>
              </div>
              <Stat value={n(data.count)} label="sealed decisions" />
              <div className="panel rounded-2xl p-5">
                <div className="text-sm text-muted-foreground">head</div>
                <code className="mt-1 block break-all font-mono text-xs">{data.head}</code>
                <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                  Publish this and any retroactive edit to any earlier record becomes detectable.
                </p>
              </div>
            </div>

            <div className="panel mt-6 overflow-hidden rounded-2xl">
              <div className="border-b border-border/50 px-5 py-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                The chain, oldest first
              </div>
              <ol>
                {data.links.map((link, i) => (
                  <li
                    key={link.decision_id}
                    className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border/40 px-5 py-3 last:border-0"
                  >
                    <span className="tnum w-8 flex-shrink-0 text-xs text-muted-foreground">
                      {i + 1}
                    </span>
                    <VerdictPill verdict={link.verdict} />
                    <span className="font-mono text-[13px]">{link.tool}</span>
                    <Link
                      href={`/decision?id=${link.decision_id}`}
                      className="font-mono text-[11px] text-accent hover:underline"
                    >
                      {link.decision_id.slice(0, 8)}
                    </Link>
                    <span className="ml-auto flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                      <span title={link.prev_digest ?? "genesis — the first record in this file"}>
                        {link.prev_digest ? link.prev_digest.slice(0, 8) : "genesis"}
                      </span>
                      <Link2 className="h-3 w-3" />
                      <span className="text-foreground">{link.record_digest.slice(0, 8)}</span>
                    </span>
                  </li>
                ))}
              </ol>
            </div>

            <p className="mt-4 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
              Each digest covers the previous digest plus the canonical form of the whole decision —
              the tool, the arguments, every resolved magnitude, every ceiling breached, and the
              policy that produced it. Chained fields carry no floating point and no locally
              generated timestamp, so a third party can recompute this from the stored records and
              get the same answer.
            </p>
          </>
        )
      ) : null}
    </Page>
  );
}
