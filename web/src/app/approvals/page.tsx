"use client";

/**
 * The reviewer's inbox — the screen the paid tier exists for.
 *
 * A `CONFIRM` verdict means *a person other than the agent's operator should decide this one*. This
 * is where that person decides, and the design has one job: make the decision answerable.
 *
 * "Approve send_email?" is unanswerable — nobody can say yes or no to that. "send_email resolves to
 * **500 recipients**, above the ceiling of 50" answers itself. So the magnitude is the largest thing
 * on the card, above the tool name, above everything. It is the entire reason a human is being asked
 * rather than a policy engine.
 *
 * Two smaller decisions worth defending:
 *
 * **Approve is not the primary button.** Both actions are the same visual weight. A reviewer holding
 * down the obvious blue button forty times a day is a rubber stamp, and a rubber stamp is worse than
 * no approval step because it launders a decision nobody made.
 *
 * **A pending request shows what it is still bound to.** The approved magnitude becomes a ceiling on
 * redemption, so the number on this card is not a description — it is the limit the reviewer is
 * agreeing to. Saying that on screen is the difference between approving a call and approving a
 * blank cheque.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Check, Clock, Inbox, ShieldQuestion, X } from "lucide-react";

import { Failed, Loading, Page, useAsync } from "@/components/Page";
import { UserCheck } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, api, type ApprovalRow, type OrgState } from "@/lib/api";
import { cn, n } from "@/lib/utils";

const REFRESH_MS = 4000;

export default function ApprovalsPage() {
  const org = useAsync(() => api.org());
  const [rows, setRows] = useState<ApprovalRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [who, setWho] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await api.approvals();
      setRows(result.approvals);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  // A reviewer leaves this page open. Polling rather than a socket because the inbox is the source
  // of truth and a stale one is only ever a few seconds stale — a push channel would be a second
  // delivery path to keep correct for no gain a human could perceive.
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const decide = async (row: ApprovalRow, granted: boolean) => {
    setBusy(row.id);
    try {
      await api.decide(row.id, granted, who.trim() || "console", undefined);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const attached = org.data?.attached ?? false;
  const pending = (rows ?? []).filter((r) => r.state === "pending");
  const settled = (rows ?? []).filter((r) => r.state !== "pending");

  return (
    <Page
      title="Approvals"
      lede="Calls a human has to decide. The number is the decision — everything else is context."
      actions={
        attached ? (
          <input
            value={who}
            onChange={(e) => setWho(e.target.value)}
            placeholder="your name, for the record"
            className="glass-button w-56 rounded-lg px-3 py-2 text-sm placeholder:text-muted-foreground/70"
          />
        ) : null
      }
    >
      {org.loading && !org.data ? <Loading label="Looking for a control plane" /> : null}
      {org.data && !attached ? <NotAttached reason={org.data.reason} /> : null}
      {org.data?.attached && org.data.reachable === false ? (
        <Failed
          error={`${org.data.url} is not answering. Until it does, a CONFIRM stops the call — exactly as it would with no control plane at all.`}
          onRetry={org.reload}
        />
      ) : null}
      {error ? <Failed error={error} onRetry={() => void load()} /> : null}

      {attached ? (
        <div className="mt-4 space-y-6">
          <section>
            <h2 className="flex items-center gap-1.5 text-sm font-semibold">
              <Inbox className="h-4 w-4 text-muted-foreground" />
              Waiting
              {pending.length > 0 ? (
                <span className="tnum rounded-full bg-[hsl(var(--verdict-confirm))]/15 px-2 py-0.5 text-[11px] font-medium text-[hsl(var(--verdict-confirm))]">
                  {pending.length}
                </span>
              ) : null}
            </h2>

            <div className="mt-3 space-y-3">
              <AnimatePresence initial={false}>
                {pending.map((row) => (
                  <motion.div
                    key={row.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, height: 0, marginBottom: 0 }}
                    transition={{ duration: 0.18 }}
                    // No `layout`: combined with the four-second poll re-rendering this list, the
                    // layout animation left decided cards stranded on screen next to the "nothing
                    // waiting" empty state — which reads as the approval not having registered.
                  >
                    <PendingCard
                      row={row}
                      busy={busy === row.id}
                      onDecide={(granted) => void decide(row, granted)}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>

              {rows !== null && pending.length === 0 && busy === null ? (
                <EmptyState
            size="section"
            icon={UserCheck}
                  title="Nothing waiting"
                  description="A call whose resolved magnitude lands in a confirm band appears here, with the number a reviewer needs to answer it."
                />
              ) : null}
              {rows === null && !error ? <Loading label="Reading the inbox" /> : null}
            </div>
          </section>

          {settled.length > 0 ? (
            <section>
              <h2 className="text-sm font-semibold">Already decided</h2>
              <div className="mt-3 space-y-2">
                {settled.map((row) => (
                  <SettledRow key={row.id} row={row} />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
    </Page>
  );
}

function NotAttached({ reason }: { reason?: string | null }) {
  return (
    // A section, not a dashed plate (DESIGN.md). This explains the state of the install; it
    // belongs to the page rather than floating on it.
    <div className="border-t border-border py-8">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold">
        <ShieldQuestion className="h-4 w-4 text-muted-foreground" />
        This install has nobody to ask
      </h2>
      <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
        A <strong className="font-medium text-foreground">confirm</strong> band means the call should
        be decided by a person other than whoever is running the agent. Without a control plane there
        is no such person to reach, so the gate stops the call and says so — which is correct, and is
        exactly what it will keep doing if you never attach one.
      </p>
      <pre className="mt-4 max-w-xl overflow-x-auto rounded-xl border border-accent/25 bg-accent/[0.05] p-3.5 font-mono text-[11.5px] leading-relaxed">
        {`neti-cloud serve --key $KEY          # the control plane
neti login --url http://… --key $KEY  # this machine
neti gate --stdio --org -- <server>   # escalate a confirm`}
      </pre>
      <p className="mt-3 text-[11px] text-muted-foreground">
        {reason ? `${reason}. ` : ""}
        Everything else on this console keeps working exactly as it does now.{" "}
        <Link href="/connect" className="text-accent hover:underline">
          The tiers, in full
        </Link>
      </p>
    </div>
  );
}

function PendingCard({
  row,
  busy,
  onDecide,
}: {
  row: ApprovalRow;
  busy: boolean;
  onDecide: (granted: boolean) => void;
}) {
  const tool = row.evidence.tool ?? "a tool call";
  const ceiling = row.evidence.ceiling;

  return (
    <div className="glass-card rounded-2xl border-l-2 border-l-[hsl(var(--verdict-confirm))] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {/* The number first and largest: it is what makes this a decision rather than a prompt. */}
          {row.approved_magnitude === null ? (
            <p className="hatched inline-block rounded px-2 py-1 text-lg font-semibold">
              could not be sized
            </p>
          ) : (
            <p className="tnum text-3xl font-semibold tracking-tight">
              {n(row.approved_magnitude)}{" "}
              <span className="text-lg font-normal text-muted-foreground">
                {row.unit ?? "items"}
              </span>
            </p>
          )}
          <p className="mt-1 font-mono text-[13px] text-muted-foreground">
            {tool}
            {ceiling != null ? (
              <span className="tnum"> · declared ceiling {n(ceiling)}</span>
            ) : null}
          </p>
        </div>

        <div className="flex flex-shrink-0 gap-2">
          {/* Same weight, deliberately. A one-click primary is how an approval step becomes a
              rubber stamp, and a rubber stamp launders a decision nobody made. */}
          <button
            onClick={() => onDecide(false)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[hsl(var(--verdict-block))]/40 px-4 py-2 text-sm font-medium text-[hsl(var(--verdict-block))] transition-colors hover:bg-[hsl(var(--verdict-block))]/10 disabled:opacity-50"
          >
            <X className="h-4 w-4" /> Deny
          </button>
          <button
            onClick={() => onDecide(true)}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[hsl(var(--verdict-allow))]/40 px-4 py-2 text-sm font-medium text-[hsl(var(--verdict-allow))] transition-colors hover:bg-[hsl(var(--verdict-allow))]/10 disabled:opacity-50"
          >
            <Check className="h-4 w-4" /> Approve
          </button>
        </div>
      </div>

      <p className="mt-4 border-t border-border/40 pt-3 text-[11px] leading-relaxed text-muted-foreground">
        Approving authorises <strong className="font-medium text-foreground">this call once</strong>.
        The grant is bound to these exact arguments under this exact policy, is spent on redemption,
        and is refused if the target grows past{" "}
        {row.approved_magnitude === null ? "what was approved" : n(row.approved_magnitude)} before
        the agent retries.
      </p>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-muted-foreground">
        <span>{row.id}</span>
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" /> expires {new Date(row.expires_at).toLocaleTimeString()}
        </span>
        {row.evidence.decision_id ? (
          <Link
            href={`/decision?id=${row.evidence.decision_id}`}
            className="text-accent hover:underline"
          >
            the evidence
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function SettledRow({ row }: { row: ApprovalRow }) {
  const tone =
    row.state === "granted"
      ? "text-[hsl(var(--verdict-allow))]"
      : row.state === "denied"
        ? "text-[hsl(var(--verdict-block))]"
        : "text-muted-foreground";

  return (
    <div className="glass-card flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-3 text-[13px]">
      <span className={cn("font-medium capitalize", tone)}>{row.state}</span>
      <span className="font-mono">{row.evidence.tool ?? "—"}</span>
      <span className="tnum text-muted-foreground">
        {row.approved_magnitude === null ? "unsizeable" : `${n(row.approved_magnitude)} ${row.unit ?? ""}`}
      </span>
      <span className="ml-auto text-xs text-muted-foreground">
        {row.decided_by ? `by ${row.decided_by}` : row.reason ?? ""}
        {row.redeemed ? " · spent" : ""}
      </span>
    </div>
  );
}
