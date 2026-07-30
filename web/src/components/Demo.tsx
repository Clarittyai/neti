"use client";

import { Card } from "@/components/ui/card";
import { cn, n } from "@/lib/utils";
import demo from "@/data/demo.json";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Check,
  Eye,
  FileSearch,
  Hash,
  ShieldCheck,
  Terminal,
  X,
} from "lucide-react";

/**
 * The narrative runs backwards from the thing that matters.
 *
 * Act 1 is the blocked call, because "41,203 people were about to lose access and the gate stopped
 * it" earns the thirty seconds needed to explain observe mode. Acts 2 and 3 then work backwards to
 * where the ceiling came from. Act 4 is what it does not do, kept in the demo on purpose: a
 * security audience that finds the gap itself stops believing the rest.
 *
 * Every figure comes from `neti demo`, which runs the real decision path against a synthetic
 * tenant. Nothing here is written by hand, so the page cannot drift from the product.
 */

const a1 = demo.act1_blocked;
const a2 = demo.act2_inventory;
const a3 = demo.act3_observe;
const a4 = demo.act4_scope;

function Section({
  eyebrow,
  title,
  lede,
  children,
}: {
  eyebrow: string;
  title: string;
  lede?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mx-auto w-full max-w-5xl px-6 py-16 sm:py-20">
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h2>
      {lede ? (
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-muted-foreground">{lede}</p>
      ) : null}
      <div className="mt-8">{children}</div>
    </section>
  );
}

function Stat({
  value,
  label,
  tone = "default",
}: {
  value: string;
  label: string;
  tone?: "default" | "danger" | "ok";
}) {
  return (
    <div>
      <div
        className={cn(
          "text-3xl font-semibold tabular-nums tracking-tight sm:text-4xl",
          tone === "danger" && "text-destructive",
          tone === "ok" && "text-emerald-600 dark:text-emerald-400"
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-sm text-muted-foreground">{label}</div>
    </div>
  );
}

export default function Demo() {
  return (
    <main className="min-h-screen">
      {/* ---------------------------------------------------------------- act 1 */}
      <div className="border-b border-border bg-gradient-to-b from-accent/[0.07] to-transparent">
        <div className="mx-auto w-full max-w-5xl px-6 pb-20 pt-16 sm:pt-24">
          <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <ShieldCheck className="h-4 w-4 text-accent" />
            neti — a preflight gate for agent tool calls
          </div>

          <h1 className="mt-6 max-w-3xl text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl">
            An agent asked to do one thing.
            <br />
            <span className="text-destructive">It was about to do {n(a1.meta.resolved)}.</span>
          </h1>

          <p className="mt-6 max-w-2xl text-[17px] leading-relaxed text-muted-foreground">
            Your authorization layer checked whether the agent may call{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-[13px]">{a1.tool}</code> and
            whether <code className="rounded bg-muted px-1.5 py-0.5 text-[13px]">{a1.argument}</code>{" "}
            is on the allowlist. Both yes. Nobody asked how big it was.
          </p>

          <Card className="mt-10 overflow-hidden p-0">
            <div className="border-b border-border/70 bg-muted/40 px-5 py-3">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Terminal className="h-3.5 w-3.5" />
                the proposed tool call
              </div>
              <code className="mt-2 block font-mono text-sm">
                {a1.tool}(<span className="text-accent">group</span>:{" "}
                <span className="text-emerald-700 dark:text-emerald-400">
                  &quot;{a1.argument}&quot;
                </span>
                )
              </code>
            </div>

            <div className="grid gap-px bg-border/70 sm:grid-cols-2">
              {a1.causes.map((c) => (
                <div key={c.pointer} className="bg-card p-5">
                  <div className="flex items-baseline justify-between gap-3">
                    <code className="font-mono text-xs text-muted-foreground">{c.pointer}</code>
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                        c.verdict === "block"
                          ? "bg-destructive/10 text-destructive"
                          : "bg-muted text-muted-foreground"
                      )}
                    >
                      {c.verdict}
                    </span>
                  </div>
                  <div className="mt-2 text-3xl font-semibold tabular-nums tracking-tight text-destructive">
                    {n(c.magnitude)}
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {c.unit} — your declared ceiling was {n(c.ceiling)}
                  </div>
                </div>
              ))}
            </div>

            <div className="flex items-start gap-3 border-t border-border/70 bg-destructive/[0.04] px-5 py-4">
              <Ban className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <div>
                <div className="text-sm font-medium">
                  The tool never ran. {a1.reached_the_server.length === 0 ? "Nothing" : "Nothing"}{" "}
                  reached the server.
                </div>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  {a1.agent_message}
                </p>
              </div>
            </div>
          </Card>

          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            <Card className="p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                The same call, a smaller group
              </div>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                <code className="text-[13px]">{a1.contrast.argument}</code> resolves to{" "}
                {n(a1.contrast.members)} principal and no application assignments. It passed
                untouched and reached the server. The gate is not a blanket ban on the tool — it is a
                question about this call.
              </p>
            </Card>
            <Card className="p-5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Hash className="h-4 w-4 text-accent" />
                Why the agent can recover
              </div>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                The denial is an MCP tool result, not a protocol error. A protocol error kills the
                run; this is something the model reads, so it re-plans to a narrower target instead
                of the whole session dying.
              </p>
            </Card>
          </div>
        </div>
      </div>

      {/* ---------------------------------------------------------------- act 2 */}
      <Section
        eyebrow="hour one"
        title="Where the ceiling came from — first, what could this reach?"
        lede="Before any traffic and before any ceiling is declared, neti reads what the agent's credential can touch in a single call. No integration, no configuration, no agent changes."
      >
        <Card className="overflow-hidden p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/70 bg-muted/40 text-left">
                <th className="px-5 py-3 font-medium text-muted-foreground">tool</th>
                <th className="px-5 py-3 font-medium text-muted-foreground">parameter</th>
                <th className="px-5 py-3 text-right font-medium text-muted-foreground">
                  max reachable
                </th>
                <th className="px-5 py-3 font-medium text-muted-foreground">status</th>
              </tr>
            </thead>
            <tbody>
              {a2.rows.map((r) => (
                <tr key={`${r.tool}${r.param}`} className="border-b border-border/50 last:border-0">
                  <td className="px-5 py-3 font-mono text-[13px]">{r.tool}</td>
                  <td className="px-5 py-3 font-mono text-[13px] text-muted-foreground">
                    {r.param}
                  </td>
                  <td className="px-5 py-3 text-right font-semibold tabular-nums">
                    {n(r.reachable)}{" "}
                    <span className="font-normal text-muted-foreground">{r.unit}</span>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">
                    {r.has_ceiling ? (
                      <span className="text-foreground">capped</span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-amber-700 dark:text-amber-400">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        no ceiling declared
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <p className="mt-4 max-w-3xl text-sm leading-relaxed text-muted-foreground">{a2.caveat}</p>
      </Section>

      {/* ---------------------------------------------------------------- act 3 */}
      <Section
        eyebrow="week one"
        title="Then a week of watching, and a number worth declaring"
        lede="Observe mode is a pass-through proxy. It records a verdict for every call and blocks nothing, so the worst case of installing it is one extra hop."
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Card className="p-5">
            <Stat value={n(a3.decisions)} label="calls observed" />
            <div className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Eye className="h-3.5 w-3.5" />
              none of them blocked
            </div>
          </Card>
          <Card className="p-5">
            <Stat value={n(a3.distribution.p95)} label={`p95 ${a3.distribution.unit}`} />
            <div className="mt-3 text-xs text-muted-foreground">
              p50 {n(a3.distribution.p50)} · normal work is small
            </div>
          </Card>
          <Card className="p-5">
            <Stat value={n(a3.distribution.max)} label="largest single call" tone="danger" />
            <div className="mt-3 text-xs text-muted-foreground">
              four orders of magnitude above normal
            </div>
          </Card>
        </div>

        <Card className="mt-4 p-6">
          <div className="text-sm font-medium">neti propose</div>
          <div className="mt-4 flex flex-wrap items-center gap-3 font-mono text-sm">
            <span className="rounded-lg bg-amber-500/10 px-3 py-1.5 text-amber-700 dark:text-amber-400">
              confirm above {n(a3.proposal.confirm_above)}
            </span>
            <span className="rounded-lg bg-destructive/10 px-3 py-1.5 text-destructive">
              block above {n(a3.proposal.block_above)}
            </span>
          </div>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            {a3.proposal.rationale}
          </p>
          <div className="mt-4 rounded-xl border border-border bg-muted/40 p-4">
            <div className="text-sm font-medium">
              Over the observed week this would have blocked {n(a3.proposal.would_block)} calls
            </div>
            <div className="mt-1 font-mono text-sm tabular-nums text-muted-foreground">
              {a3.proposal.examples.map((e) => n(e)).join(" · ")} {a3.distribution.unit}
            </div>
            <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
              Arithmetic is not reviewable; consequences are. This is the line an operator actually
              checks before committing a number.
            </p>
          </div>
          <p className="mt-4 border-t border-border/70 pt-4 text-sm leading-relaxed text-muted-foreground">
            <strong className="font-medium text-foreground">Not a learned baseline.</strong>{" "}
            {a3.determinism_note}
          </p>
        </Card>
      </Section>

      {/* ---------------------------------------------------------------- act 4 */}
      <Section
        eyebrow="the honest part"
        title={`What it does not do — ${a4.caught} of ${a4.total} real incidents`}
        lede="A scorecard that reports only what a product catches is marketing. These are the questions you will be asked, answered before they are asked."
      >
        <div className="space-y-3">
          {a4.incidents.map((i) => (
            <Card key={i.id} className="p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  {i.caught ? (
                    <Check className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                  ) : (
                    <X className="h-4 w-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="font-mono text-[13px]">{i.id}</span>
                  <span className="text-xs text-muted-foreground">{i.actor}</span>
                </div>
                <div className="font-semibold tabular-nums">
                  {n(i.magnitude)}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    {i.unit}
                    {i.gated_unit && i.gated_unit !== i.unit ? ` (gated: ${i.gated_unit})` : ""}
                  </span>
                </div>
              </div>
              <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{i.note}</p>
            </Card>
          ))}
        </div>

        <Card className="mt-6 p-6">
          <div className="flex items-center gap-2 text-sm font-medium">
            <FileSearch className="h-4 w-4 text-muted-foreground" />
            Known blind spots, shipped as part of the product
          </div>
          <ul className="mt-4 grid gap-2 sm:grid-cols-2">
            {Object.entries(a4.blind_spots).map(([id, text]) => (
              <li key={id} className="text-sm leading-relaxed text-muted-foreground">
                <span className="font-mono text-xs text-foreground">{id}</span> {text}
              </li>
            ))}
          </ul>
        </Card>

        <Card className="mt-4 border-amber-500/30 p-6">
          <div className="text-sm font-medium">Not yet measured</div>
          <ul className="mt-3 space-y-2">
            {a4.unmeasured.map((u) => (
              <li key={u} className="flex gap-2 text-sm leading-relaxed text-muted-foreground">
                <ArrowRight className="mt-1 h-3.5 w-3.5 shrink-0" />
                {u}
              </li>
            ))}
          </ul>
        </Card>
      </Section>

      <footer className="border-t border-border bg-muted/30">
        <div className="mx-auto w-full max-w-5xl px-6 py-10">
          <p className="text-sm leading-relaxed text-muted-foreground">
            <strong className="font-medium text-foreground">{demo.disclaimer}</strong>
          </p>
          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
            Regenerate with <code className="text-[11px]">neti demo -o web/src/data/demo.json</code>.
            Against a real tenant, the same commands produce the same page with your numbers.
          </p>
        </div>
      </footer>
    </main>
  );
}
