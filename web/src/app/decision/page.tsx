"use client";

/**
 * The evidence for one decision — forked from clarity-platform's `RunDossierCard`, which is already
 * an approve-or-deny-with-proof card and needed less changing than anything else in the repo.
 *
 * Without this screen a block is an assertion. With it, a security reviewer can answer their own
 * question: what was measured, by which request, against which declared ceiling, under which
 * policy, and does the digest still check out. That is why the raw record is here too, collapsed —
 * available without being the first thing anyone sees.
 *
 * The decision id arrives as `?id=` rather than as a path segment. A dynamic route cannot be
 * statically exported without knowing every id in advance, and these ids are decisions that have not
 * happened yet — so the route stays static, and `neti console` can serve the whole console as files
 * out of the Python package on one port, with no Node runtime anywhere near a customer.
 */

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ChevronDown, ChevronRight, Copy, Eye, ShieldCheck } from "lucide-react";

import { Failed, Loading, Page, useAsync } from "@/components/Page";
import { EmptyState } from "@/components/ui/empty-state";
import { VerdictPill } from "@/components/Verdict";
import { api, type Cause } from "@/lib/api";
import { cn, n } from "@/lib/utils";

export default function DecisionPage() {
  // `useSearchParams` suspends during prerender, so this boundary is required rather than tidy.
  return (
    <Suspense
      fallback={
        <Page title="Decision" width="narrow">
          <Loading />
        </Page>
      }
    >
      <Decision />
    </Suspense>
  );
}

function Decision() {
  const id = useSearchParams().get("id") ?? "";
  const { data, error, loading, reload } = useAsync(
    () => (id ? api.decision(id) : Promise.resolve(null)),
    [id],
  );
  const [raw, setRaw] = useState(false);

  if (!id) {
    return (
      <Page title="Decision" width="narrow">
        <EmptyState
            size="inline"
          title="No decision named"
          description="This page shows the evidence behind one decision. Pick one from the list."
          secondary={<Link href="/decisions" className="text-accent hover:underline">All decisions</Link>}
        />
      </Page>
    );
  }

  return (
    <Page
      title="Decision"
      lede={data ? `${data.tool} · ${new Date(data.decided_at).toLocaleString()}` : undefined}
      width="narrow"
      actions={
        <Link
          href="/decisions"
          className="glass-button inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-sm"
        >
          <ArrowLeft className="h-4 w-4" /> All decisions
        </Link>
      }
    >
      {loading && !data ? <Loading /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        <div className="space-y-4">
          <div className="border-t border-border py-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {/* A flag proceeds, so the old `else` branch called it "allowed" — the one word
                    this verdict exists to not be. It ran *and* somebody is meant to look at it,
                    and a headline that says "allowed" is read as "nothing to see here". */}
                <h2 className="text-base font-semibold">
                  {data.verdict === "block"
                    ? "This call was stopped"
                    : data.verdict === "confirm"
                      ? "This call needs a human"
                      : data.verdict === "flag"
                        ? "This call ran, and was flagged for you"
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
                  // A cause the gate could not size *and knew was destructive* keeps its own
                  // verdict. "Could not size" is the right label for `npm test` and a calming lie
                  // for `cat list.txt | xargs rm`; collapsing both to it here would undo, in the
                  // one view built for reading a single decision, the distinction the record
                  // exists to carry.
                  data.causes.length > 0 &&
                  data.causes.every((c) => c.magnitude === null) &&
                  !data.causes.some((c) => c.destructive)
                    ? "unknown"
                    : data.verdict
                }
                size="lg"
              />
            </div>
            {data.mode === "observe" ? (
              <p className="mt-3 bg-muted/50 px-3 py-2 text-[13px] leading-relaxed text-muted-foreground">
                Recorded in <strong className="font-medium text-foreground">observe</strong> mode —
                the verdict was reached but the call was forwarded anyway. Enforcement changes
                whether a decision is acted on, not what it is.
              </p>
            ) : null}
          </div>

          <section className="border-t border-border py-5">
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

          <section className="border-t border-border py-5">
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
              <Field label="source" value={data.synthetic ? "built-in tenant" : "provider"} />
            </dl>
            {data.synthetic ? (
              <p className="mt-3 bg-muted/50 px-3 py-2 text-[13px] leading-relaxed text-muted-foreground">
                The magnitudes above came from the{" "}
                <strong className="font-medium text-foreground">built-in tenant</strong>, not from a
                provider. They are exact, confident and invented — this demonstrates behaviour and
                is not a finding about anything. The marker is inside the record&apos;s digest, so it
                is evidence of where the number came from rather than a label anyone can remove.
              </p>
            ) : null}
            <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
              The policy digest is part of the record because a verdict means nothing without the
              ceilings that produced it. Change the policy and the digest changes with it.
            </p>
          </section>

          <div className="border-t border-border py-5">
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
                  className="glass-button absolute right-2 top-2 rounded-full p-1.5"
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
  const risky = unsizeable && Boolean(cause.destructive);
  return (
    <div
      className={cn(
        " border p-4",
        cause.verdict === "block"
          ? "border-[hsl(var(--verdict-block))]/30 bg-[hsl(var(--verdict-block))]/[0.04]"
          : "border-border/60",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <code className="font-mono text-xs text-muted-foreground">{cause.pointer}</code>
        <VerdictPill verdict={unsizeable && !risky ? "unknown" : cause.verdict} />
      </div>

      {risky ? (
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          <strong className="font-medium text-foreground">
            This destroys something, and its size was not readable from the argument.
          </strong>{" "}
          Recognised as <code className="font-mono">{cause.destructive}</code>; no number because{" "}
          <code className="font-mono">{cause.reason}</code>. The declared{" "}
          <code className="font-mono">on_unsized_risk</code> policy applied. The gate blocks on
          numbers and does not have one here, so the call was recorded and surfaced rather than
          stopped — widening the parser is the only thing that turns this into a ceiling.
        </p>
      ) : unsizeable ? (
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          Could not be sized, and nothing about it looked destructive. The declared{" "}
          <code className="font-mono">on_unresolved</code> policy applied — the gate does not guess,
          and a failed lookup is never read as zero.
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
