"use client";

/**
 * The front door.
 *
 * Connecting *proves* the credential by using it — it resolves the directory's size, which is the
 * same call the inventory makes. A connect button that only stores a secret has demonstrated
 * nothing, and the first time it mattered you would find out at the wrong moment.
 *
 * The demo/live distinction is stated plainly rather than softened. A console that let a viewer
 * believe fixture numbers were a finding about their own directory would be committing exactly the
 * overclaim the rest of this codebase is built to avoid.
 */

import { useState } from "react";
import Link from "next/link";
import { Check, KeyRound, Loader2, Plug, ShieldCheck } from "lucide-react";

import { Failed, Page } from "@/components/Page";
import { Install } from "@/components/Install";
import { useConsole } from "@/components/ConsoleProvider";
import { api } from "@/lib/api";
import { cn, n } from "@/lib/utils";

export default function ConnectPage() {
  const { state, connect, refresh } = useConsole();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<number | null>(null);

  const demo = state?.mode === "demo";
  const connected = state?.connected ?? false;

  const onConnect = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.connect();
      setSize(result.directory_size);
      if (!result.connected) setError(result.reason ?? "the directory could not be read");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page
      title="Connect"
      lede="Two connections, in this order: the directory the gate asks how big something is, and the agent whose calls it sits in front of."
    >
      <h2 className="mb-3 text-sm font-semibold">The directory</h2>
      <div className="glass-card max-w-3xl rounded-2xl p-6">
        <div className="flex items-start gap-4">
          <span
            className={cn(
              "grid h-11 w-11 flex-shrink-0 place-items-center rounded-xl",
              connected
                ? "bg-[hsl(var(--verdict-allow))]/10 text-[hsl(var(--verdict-allow))]"
                : "bg-accent/10 text-accent",
            )}
          >
            {connected ? <Check className="h-5 w-5" strokeWidth={2.5} /> : <Plug className="h-5 w-5" />}
          </span>

          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold">Microsoft 365 · Entra ID</h2>
            <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
              {connected ? (
                <>
                  Connected to <strong className="font-medium text-foreground">{state?.tenant}</strong>
                  {size !== null ? ` — ${n(size)} principals in the directory.` : "."}
                </>
              ) : (
                "Reads group membership and application assignments. Nothing else, and nothing is written."
              )}
            </p>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              {connected ? (
                <>
                  <Link
                    href="/gate"
                    className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent-600"
                  >
                    Go to the live gate
                  </Link>
                  <button
                    onClick={async () => {
                      await api.disconnect();
                      await refresh();
                    }}
                    className="glass-button rounded-lg px-3 py-2 text-sm text-muted-foreground"
                  >
                    Disconnect
                  </button>
                </>
              ) : (
                <button
                  onClick={() => void onConnect()}
                  disabled={busy}
                  className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent-600 disabled:opacity-60"
                >
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
                  {busy ? "Verifying" : "Connect"}
                </button>
              )}
            </div>
          </div>
        </div>

        {error ? (
          <div className="mt-4">
            <Failed error={error} onRetry={() => void onConnect()} />
          </div>
        ) : null}
      </div>

      <div className="mt-4 grid max-w-3xl gap-4 sm:grid-cols-2">
        <div className="glass-card rounded-2xl p-5">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            <KeyRound className="h-4 w-4 text-muted-foreground" /> What it asks for
          </h3>
          <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
            One application permission —{" "}
            <code className="font-mono text-[12px]">GroupMember.Read.All</code> — which is
            Microsoft&apos;s own documented least-privilege choice for the count endpoint. Read-only.
            No mailbox, no files, no write scope of any kind.
          </p>
        </div>
        <div className="glass-card rounded-2xl p-5">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" /> How connecting is verified
          </h3>
          <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
            By using the credential, not by storing it. Connecting counts the directory, so
            &ldquo;connected&rdquo; means a real read succeeded rather than a form submitted.
          </p>
        </div>
      </div>

      {demo ? (
        <div className="mt-4 max-w-3xl rounded-2xl border border-dashed border-border p-5">
          <h3 className="text-sm font-semibold">You are on the demo tenant</h3>
          <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">
            There are no credentials in the environment, so the gate is talking to a fixture with
            known contents rather than a real directory. The engine, the decision procedure and the
            records are identical either way — only the numbers differ. Export{" "}
            <code className="font-mono text-[12px]">NETI_TENANT_ID</code>,{" "}
            <code className="font-mono text-[12px]">NETI_CLIENT_ID</code> and{" "}
            <code className="font-mono text-[12px]">NETI_CLIENT_SECRET</code> and restart{" "}
            <code className="font-mono text-[12px]">neti serve</code> to point it at Microsoft.
          </p>

          {state?.fixture ? (
            <>
              <p className="mt-4 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                What the fixture declares
              </p>
              <table className="mt-2 w-full text-[13px]">
                <tbody>
                  {state.fixture.map((g) => (
                    <tr key={g.id} className="border-b border-border/40 last:border-0">
                      <td className="py-1.5 pr-3">{g.name}</td>
                      <td className="tnum py-1.5 pr-3 text-right">{n(g.members)}</td>
                      <td className="py-1.5 text-xs text-muted-foreground">
                        {g.kind === "dynamic_distribution"
                          ? "Exchange dynamic distribution — Graph cannot count it"
                          : `${g.apps} app assignment${g.apps === 1 ? "" : "s"}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
                Published on purpose: the resolver has to independently arrive at these numbers, so
                you can check its answers rather than trust them.
              </p>
            </>
          ) : null}
        </div>
      ) : null}

      <div className="mt-10">
        <Install />
      </div>
    </Page>
  );
}
