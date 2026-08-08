"use client";

/**
 * The declared ceilings.
 *
 * Ceilings can be declared from here, and the number is still never the console's.
 *
 * This page was read-only, on the reasoning that editing here "would make the number something the
 * console owns". That is the right worry about the wrong mechanism. What must never happen is a
 * number being *inferred* — `config/policy.py` opens by saying nothing computed becomes a ceiling on
 * its own. A person typing one into a field, beside their own observed distribution, and reading the
 * diff before agreeing to it, is exactly as declared as the same person typing it into the file.
 *
 * What the read-only version actually produced was a fresh install where every row said "no ceiling
 * — resolves and records, cannot block", which is a gate that cannot yet do the thing the product is
 * for, shown to somebody with no obvious way to change it.
 *
 * The session-budget section is not decoration either. A per-call gate is structurally blind to
 * 4,000 individual sends — each resolves to 1 and passes every per-call ceiling — and only a
 * declared cumulative budget sees the pattern. That is SCOPE.md NC-01, and showing it here is how
 * the console admits the hole rather than hiding it.
 */

import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import { DeclareCeiling } from "@/components/DeclareCeiling";
import { Failed, Loading, Page, useAsync } from "@/components/Page";
import { api, type DecisionSummary } from "@/lib/api";
import { cn, n } from "@/lib/utils";

interface Band {
  above: number;
  verdict: "allow" | "flag" | "confirm" | "block";
}
interface Gate {
  resolver: string;
  unit: string | null;
  bands: Band[];
  on_unresolved: string;
  has_ceiling: boolean;
}
interface SensitiveRule {
  match: string;
  verdict: Band["verdict"];
  why: string;
}
interface PolicyShape {
  digest: string;
  mode: string;
  unknown_tool: string;
  tools: Record<string, Record<string, Gate>>;
  sensitive: SensitiveRule[];
  provenance: { untrusted: string[]; tools: string[]; bands: Band[] };
  session_budgets: { tools: string[]; unit: string; bands: Band[] }[];
  outside_root: { verdict: string | null; root: string | null };
}

export default function PolicyPage() {
  const { data, error, loading, reload } = useAsync(
    () => api.policy() as Promise<unknown> as Promise<PolicyShape>,
  );
  // The recorded traffic, so a gate can show the distribution its ceiling would be compared
  // against. Read here rather than inside each row: one request, not one per gate.
  const decisions = useAsync(() => api.decisions());
  const [editing, setEditing] = useState<string | null>(null);

  const observedFor = (tool: string, pointer: string): number[] =>
    (decisions.data?.decisions ?? [])
      .filter((d: DecisionSummary) => d.tool === tool)
      .flatMap((d: DecisionSummary) =>
        d.magnitudes.filter((m) => m.pointer === pointer && m.magnitude !== null),
      )
      .map((m) => m.magnitude as number);

  return (
    <Page
      title="Policy"
      lede="The ceilings you declared. Every verdict is a comparison against a number on this page — nothing here is learned or inferred."
      // The policy digest, not a control. It looked like a button and did nothing when pressed,
      // which is the same lie as a pill that is not pressable.
      actions={
        data ? (
          <code className="rounded-lg border border-border px-3 py-2 font-mono text-xs text-muted-foreground">
            {data.digest.slice(0, 16)}
          </code>
        ) : null
      }
    >
      {loading && !data ? <Loading /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        <div className="space-y-6">
          <div className="space-y-4">
            {Object.entries(data.tools).map(([tool, gates]) => (
              <div key={tool} className="overflow-hidden border-t border-border">
                <div className="flex flex-wrap items-center gap-3 border-b border-border/50 px-5 py-3">
                  <span className="font-mono text-sm font-medium">{tool}</span>
                  <span className="text-xs text-muted-foreground">
                    {Object.keys(gates).length} gated parameter
                    {Object.keys(gates).length === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="divide-y divide-border/40">
                  {Object.entries(gates).map(([pointer, gate]) => {
                    const key = `${tool}${pointer}`;
                    return (
                      <div key={key}>
                        <GateRow
                          pointer={pointer}
                          gate={gate}
                          onDeclare={() => setEditing(editing === key ? null : key)}
                          open={editing === key}
                        />
                        {editing === key ? (
                          <DeclareCeiling
                            tool={tool}
                            pointer={pointer}
                            unit={gate.unit}
                            observed={observedFor(tool, pointer)}
                            onCancel={() => setEditing(null)}
                            onDone={() => {
                              setEditing(null);
                              reload();
                            }}
                          />
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* The second axis. Listed before budgets because it is the one that fires on a call
              of size 1, which is the case every reader of this page has been told magnitude
              cannot reach. */}
          <section>
            <h2 className="text-base font-semibold tracking-tight">
              Gated on what it is, not how big
            </h2>
            <p className="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
              A ceiling cannot reach a single file. These match the target itself, whatever its
              size, and join with the ceilings above — worst verdict wins, so a rule written badly
              costs a confirmation and never a silent allow.
            </p>
            {/* Editable, because the whole point of this page is that the YAML is optional. An
                operator who never opens the file still has to be able to see, add and remove every
                rule that can stop a call — `neti start` writes these without being asked, and a
                rule you cannot remove from where you found it is a rule you disable by uninstalling
                the product. */}
            <OffLimits rules={data.sensitive} onChange={reload} />
          </section>

          {/* The third. Not a property of the call at all — a property of the session it is in. */}
          <section>
            <h2 className="text-base font-semibold tracking-tight">Downstream of untrusted input</h2>
            <p className="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
              A prompt injection is small at both ends, so magnitude never sees it. Once a session
              has read one of these, every later call in it is judged against the tighter ceiling
              below as well as its own.
            </p>
            <div className="mt-4 border-t border-border">
              {data.provenance.untrusted.concat(data.provenance.tools).map((u) => (
                <div key={u} className="border-b border-border py-3">
                  <code className="font-mono text-[13px]">{u}</code>
                </div>
              ))}
              {data.provenance.untrusted.length + data.provenance.tools.length === 0 ? (
                <p className="border-b border-border py-3 text-[13px] text-muted-foreground">
                  None declared. Nothing this agent reads is treated as untrusted.
                </p>
              ) : (
                <div className="flex flex-wrap items-center gap-2 py-3">
                  <span className="text-[13px] text-muted-foreground">then, for every later call:</span>
                  {data.provenance.bands.map((b) => (
                    <BandChip key={b.above} band={b} />
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* The fourth. Where the target is, which is the only axis that reaches the files most
              worth protecting — they are outside the tree the day-zero scan can walk. */}
          <section>
            <h2 className="text-base font-semibold tracking-tight">Outside this directory</h2>
            <p className="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
              <code className="font-mono">~/.ssh/id_rsa</code> is one file, under every ceiling
              anybody would write, and outside the project a scan can see. Neither size nor a
              name-matching rule reaches it. This one is about where the target is, after symlinks
              are resolved.
            </p>
            <div className="panel mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
              {data.outside_root.verdict ? (
                <>
                  {/* A root of "." is what the policy file says and means nothing to a reader
                      looking for where the boundary is. The absolute path is the answer to the
                      question this row is asked. */}
                  <span className="font-mono text-[13px]">{data.outside_root.root ?? "this directory"}</span>
                  <span className="text-xs text-muted-foreground">anything outside it</span>
                  <span className="ml-auto">
                    {/* Labelled, because the default chip reads "confirm above 0" — and there is
                        no number here at all. That is the point of this axis. */}
                    <BandChip
                      band={{ above: 0, verdict: data.outside_root.verdict as Band["verdict"] }}
                      label={data.outside_root.verdict}
                    />
                  </span>
                </>
              ) : (
                <p className="text-[13px] text-muted-foreground">
                  Not declared. A target anywhere on this machine is judged only by its size.
                </p>
              )}
            </div>
          </section>

          <section>
            <h2 className="text-base font-semibold tracking-tight">Session budgets</h2>
            <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
              A per-call ceiling cannot see four thousand individual sends — each one resolves to 1
              and passes. These are cumulative totals per session, and they are declared rather than
              learned, which is what keeps the gate a static comparison.
            </p>
            <div className="mt-3 space-y-2">
              {data.session_budgets.map((b) => (
                <div
                  key={`${b.unit}-${b.tools.join()}`}
                  className="panel flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3"
                >
                  <span className="font-mono text-[13px]">{b.tools.join(", ")}</span>
                  <span className="text-xs text-muted-foreground">cumulative {b.unit}</span>
                  <span className="ml-auto flex flex-wrap gap-2">
                    {b.bands.map((band) => (
                      <BandChip key={band.above} band={band} />
                    ))}
                  </span>
                </div>
              ))}
              {data.session_budgets.length === 0 ? (
                <p className="text-[13px] text-muted-foreground">None declared.</p>
              ) : null}
            </div>
          </section>

          <section className="border-t border-border py-5">
            <h2 className="text-sm font-semibold">Defaults</h2>
            <dl className="mt-3 space-y-2 text-[13px]">
              <div className="flex flex-wrap gap-2">
                <dt className="text-muted-foreground">An undeclared tool</dt>
                <dd className="font-medium">{data.unknown_tool}s</dd>
                <dd className="w-full text-[11px] leading-relaxed text-muted-foreground">
                  Out of scope, not denied. Failing closed on everything undeclared would make the
                  gate unusable on its first day, and it would simply be switched off.
                </dd>
              </div>
            </dl>
          </section>
        </div>
      ) : null}
    </Page>
  );
}

function OffLimits({
  rules,
  onChange,
}: {
  rules: SensitiveRule[];
  onChange: () => void;
}) {
  const [match, setMatch] = useState("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const write = (next: SensitiveRule[]) => {
    setBusy(true);
    setError(null);
    api
      .setSensitive({ rules: next, apply: true })
      .then(() => {
        setMatch("");
        setWhy("");
        onChange();
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mt-4 border-t border-border">
      {rules.map((r) => (
        <div
          key={r.match}
          className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-border py-3"
        >
          <code className="font-mono text-[13px]">{r.match}</code>
          {r.why ? <span className="text-[13px] text-muted-foreground">{r.why}</span> : null}
          <span className="ml-auto flex items-center gap-3">
            <BandChip band={{ above: 0, verdict: r.verdict }} label={r.verdict} />
            <button
              type="button"
              disabled={busy}
              onClick={() => write(rules.filter((x) => x.match !== r.match))}
              className="rounded-full px-2.5 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground disabled:opacity-40"
            >
              Remove
            </button>
          </span>
        </div>
      ))}
      {rules.length === 0 ? (
        <p className="border-b border-border py-3 text-[13px] text-muted-foreground">
          None. `neti propose` suggests the ones that match something really on this disk.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 py-3">
        <input
          value={match}
          onChange={(e) => setMatch(e.target.value)}
          placeholder="**/*.pem"
          spellCheck={false}
          aria-label="Pattern to put off limits"
          className="w-48 rounded-md border border-border bg-transparent px-2.5 py-1.5 font-mono text-[12px] outline-none placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-accent"
        />
        <input
          value={why}
          onChange={(e) => setWhy(e.target.value)}
          placeholder="why it matters — the agent is told this"
          aria-label="Why"
          className="min-w-0 flex-1 rounded-md border border-border bg-transparent px-2.5 py-1.5 text-[12px] outline-none placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-accent"
        />
        <button
          type="button"
          disabled={busy || !match.trim()}
          onClick={() =>
            write([...rules, { match: match.trim(), verdict: "confirm", why: why.trim() }])
          }
          className="rounded-full px-3 py-1.5 text-[12px] font-medium ring-1 ring-inset ring-border transition-colors hover:bg-foreground/[0.04] disabled:opacity-40"
        >
          Put off limits
        </button>
      </div>

      {error ? (
        <p className="pb-3 text-[12px] text-[hsl(var(--verdict-block))]">{error}</p>
      ) : null}
    </div>
  );
}

function GateRow({
  pointer,
  gate,
  onDeclare,
  open,
}: {
  pointer: string;
  gate: Gate;
  onDeclare: () => void;
  open: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
      <code className="font-mono text-[13px] text-muted-foreground">{pointer}</code>
      <span className="text-xs text-muted-foreground">{gate.resolver}</span>
      {gate.unit ? (
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
          {gate.unit}
        </span>
      ) : null}

      <span className="ml-auto flex flex-wrap items-center gap-2">
        {gate.bands.length === 0 ? (
          <span className="text-xs text-[hsl(var(--verdict-confirm))]">
            no ceiling — resolves and records, cannot block
          </span>
        ) : (
          gate.bands.map((band) => <BandChip key={band.above} band={band} />)
        )}
        <span className="hatched rounded px-2 py-0.5 text-[10px] text-muted-foreground">
          unsizeable → {gate.on_unresolved}
        </span>
        <button
          type="button"
          onClick={onDeclare}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset transition-colors",
            open
              ? "text-accent ring-accent/40"
              : "text-muted-foreground ring-border hover:text-foreground",
          )}
        >
          <SlidersHorizontal className="h-3 w-3" />
          {gate.bands.length === 0 ? "Declare a ceiling" : "Change"}
        </button>
      </span>
    </div>
  );
}

function BandChip({ band, label }: { band: Band; label?: string }) {
  return (
    <span
      className={cn(
        "tnum rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        band.verdict === "block" &&
          "bg-[hsl(var(--verdict-block))]/10 text-[hsl(var(--verdict-block))] ring-[hsl(var(--verdict-block))]/30",
        band.verdict === "confirm" &&
          "bg-[hsl(var(--verdict-confirm))]/10 text-[hsl(var(--verdict-confirm))] ring-[hsl(var(--verdict-confirm))]/30",
        (band.verdict === "allow" || band.verdict === "flag") &&
          "bg-muted text-muted-foreground ring-border",
      )}
    >
      {label ?? `${band.verdict} above ${n(band.above)}`}
    </span>
  );
}
