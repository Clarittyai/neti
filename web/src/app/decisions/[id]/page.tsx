"use client";

/**
 * The evidence for one decision — forked from clarity-platform's `RunDossierCard`, which is already
 * an approve-or-deny-with-proof card and needed less changing than anything else in the repo.
 *
 * Without this screen a block is an assertion. With it, a security reviewer can answer their own
 * question: what was measured, by which request, against which declared ceiling, under which
 * policy, and does the digest still check out. That is why the raw record is here too, collapsed —
 * available without being the first thing anyone sees.
 */

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ChevronDown, ChevronRight, Copy, Eye, ShieldCheck } from "lucide-react";

import { Failed, Loading, Page, useAsync } from "@/components/Page";
import { VerdictPill } from "@/components/Verdict";
import { api, type Cause } from "@/lib/api";
import { cn, n } from "@/lib/utils";

export default function DecisionPage() {
  // `useParams` rather than the `params` prop: on this Next version `params` is a plain object, so
  // `use(params)` throws at render — and it is a promise on the next one. Reading it from the router
  // is correct on both.
  const { id } = useParams<{ id: string }>();
  const { data, error, loading, reload } = useAsync(() => api.decision(id), [id]);
  const [raw, setRaw] = useState(false);

  return (
    <Page
      title="Decision"
      lede={data ? `${data.tool} · ${new Date(data.decided_at).toLocaleString()}` : undefined}
      width="narrow"
      actions={
        <Link
          href="/decisions"
          className="glass-button inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm"
        >
          <ArrowLeft className="h-4 w-4" /> All decisions
        </Link>
      }
    >
      {loading && !data ? <Loading /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        <div className="space-y-4">
          <div className="glass-card rounded-2xl p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-base font-semibold">
                  {data.verdict === "block"
                    ? "This call was stopped"
                    : data.verdict === "confirm"
                      ? "This call needs a human"
                      : "This call was allowed"}
                </h2>
                <p className="mt-1 font-mono text-[13px] text-muted-foreground">
                  {data.tool}({Object.entries(data.args)
                    .map(([k, v]) => `${k}: "${String(v)}"`)
                    .join(", ")})
                </p>
              </div>
              <VerdictPill
                verdict={
                  data.causes.length > 0 && data.causes.every((c) => c.magnitude === null)
                    ? "unknown"
                    : data.verdict
                }
                size="lg"
              />
            </div>
            {data.mode === "observe" ? (
              <p className="mt-3 rounded-lg bg-muted/50 px-3 py-2 text-[13px] leading-relaxed text-muted-foreground">
                Recorded in <strong className="font-medium text-foreground">observe</strong> mode —
                the verdict was reached but the call was forwarded anyway. Enforcement changes
                whether a decision is acted on, not what it is.
              </p>
            ) : null}
          </div>

          <section className="glass-card rounded-2xl p-5">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <Eye className="h-3.5 w-3.5" /> What it measured
            </h3>
            <div className="mt-3 space-y-3">
              {data.causes.map((cause) => (
                <CauseCard key={cause.pointer} cause={cause} />
              ))}
              {data.causes.length === 0 ? (
                <p className="text-[13px] text-muted-foreground">
                  This tool is not gated, so nothing was measured. An ungated tool is out of scope,
                  not denied.
                </p>
              ) : null}
            </div>
          </section>

          <section className="glass-card rounded-2xl p-5">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              <ShieldCheck className="h-3.5 w-3.5" /> Provenance
            </h3>
            <dl className="mt-3 grid gap-x-6 gap-y-2 text-[13px] sm:grid-cols-2">
              <Field label="decision" value={data.decision_id} mono />
              <Field label="rule" value={data.rule} mono />
              <Field label="policy" value={data.policy_digest} mono />
              <Field label="code" value={data.code_version} mono />
              <Field label="previous" value={data.prev_digest ?? "genesis"} mono />
              <Field label="this record" value={data.record_digest} mono />
            </dl>
            <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
              The policy digest is part of the record because a verdict means nothing without the
              ceilings that produced it. Change the policy and the digest changes with it.
            </p>
          </section>

          <div className="glass-card rounded-2xl p-5">
            <button
              onClick={() => setRaw((r) => !r)}
              className="flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              {raw ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              The record as stored
            </button>
            {raw ? (
              <div className="relative mt-3">
                <button
                  onClick={() => void navigator.clipboard?.writeText(JSON.stringify(data, null, 2))}
                  className="glass-button absolute right-2 top-2 rounded-md p-1.5"
                  title="Copy"
                >
                  <Copy className="h-3.5 w-3.5" />
                </button>
                <pre className="max-h-[420px] overflow-auto rounded-lg bg-muted/60 p-3 font-mono text-[11px] leading-relaxed">
                  {JSON.stringify(data, null, 2)}
                </pre>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </Page>
  );
}

function CauseCard({ cause }: { cause: Cause }) {
  const unsizeable = cause.magnitude === null;
  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        cause.verdict === "block"
          ? "border-[hsl(var(--verdict-block))]/30 bg-[hsl(var(--verdict-block))]/[0.04]"
          : "border-border/60",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <code className="font-mono text-xs text-muted-foreground">{cause.pointer}</code>
        <VerdictPill verdict={unsizeable ? "unknown" : cause.verdict} />
      </div>

      {unsizeable ? (
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          Could not be sized. The declared <code className="font-mono">on_unresolved</code> policy
          applied — the gate does not guess, and a failed lookup is never read as zero.
        </p>
      ) : (
        <>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="tnum text-2xl font-semibold tracking-tight">{n(cause.magnitude)}</span>
            <span className="text-sm text-muted-foreground">{cause.unit}</span>
            {cause.ceiling !== null ? (
              <span className="tnum ml-auto text-xs text-muted-foreground">
                ceiling {n(cause.ceiling)}
              </span>
            ) : null}
          </div>

          {Object.keys(cause.breakdown).length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
              {Object.entries(cause.breakdown).map(([k, v]) => (
                <span key={k} className="tnum">
                  {k}: {n(v)}
                </span>
              ))}
            </div>
          ) : null}

          {cause.breaches.length > 0 ? (
            <ul className="mt-3 space-y-1">
              {cause.breaches.map((b) => (
                <li key={b.source} className="tnum font-mono text-[11px] text-muted-foreground">
                  {b.source} {n(b.observed)} &gt; {n(b.above)} → {b.verdict}
                </li>
              ))}
            </ul>
          ) : null}

          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
            {cause.direction} · {cause.consistency} consistency
            {cause.resolved_at ? ` · read ${new Date(cause.resolved_at).toLocaleTimeString()}` : ""}
            {cause.over_block_possible
              ? " · upper bound, so the true figure may be lower than the ceiling"
              : ""}
          </p>
        </>
      )}
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className={cn("truncate", mono && "font-mono text-[11px]")} title={value}>
        {value}
      </dd>
    </div>
  );
}
