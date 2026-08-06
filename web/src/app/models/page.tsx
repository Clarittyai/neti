"use client";

/**
 * Where `neti suggest` sends its request — and the proof that it is not here.
 *
 * `neti suggest` is the one thing in this product that talks to a model. It reads the parameters no
 * resolver claims and asks whether any of them name a *set*, which is the difference between a
 * policy that gates eight tools and one that gates the two that mattered. The answer comes back as
 * a commented-out fragment; nothing is active until a person deletes the `#`.
 *
 * Two rules, and they are the reason this page exists rather than a paragraph in the README:
 *
 * **There is no field to type a key into, and there never will be.** The console reports whether
 * the variable the SDK reads is *set*, never what it holds. A key pasted into a browser is a key in
 * a process that did not need it, and the whole pitch to a security team is that nothing extra
 * holds their secrets. Export it in the shell that runs `neti`.
 *
 * **Reachability is the part people actually get wrong**, so that is what this checks. A runner on
 * the wrong port, a gateway behind a proxy, a model id that is not loaded — the probe asks the
 * endpoint what it holds and says what came back. It runs no completion, so a cold 30B model does
 * not take minutes and a metered gateway is not billed for a connectivity check.
 */

import { useState } from "react";
import { Check, KeyRound, Loader2, Server, ShieldCheck } from "lucide-react";

import { CommandLine } from "@/components/CommandLine";
import { Failed, Loading, Page, useAsync } from "@/components/Page";
import { api, type ProbeResult, type ProviderStatus, type Runner } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function ModelsPage() {
  const { data, error, loading, reload } = useAsync(() => api.models());

  return (
    <Page
      title="Models"
      lede="Where `neti suggest` sends its request. Your key, your account, your machine — neti proxies nothing and never sees the answer."
    >
      {loading && !data ? <Loading label="Reading providers" /> : null}
      {error ? <Failed error={error} onRetry={reload} /> : null}

      {data ? (
        <>
          <p className="flex max-w-3xl items-start gap-2.5 border-l-2 border-accent bg-accent/[0.05] py-3 pl-3.5 pr-4 text-[13px] leading-relaxed text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            <span>
              <strong className="font-medium text-foreground">
                There is no field here to type a key into.
              </strong>{" "}
              The console can see whether a variable is set; it never reads what it holds, and
              nothing is stored. Export it in the shell that runs <code className="font-mono">neti</code>{" "}
              — a key pasted into a browser is a key in a process that did not need it.
            </span>
          </p>

          <div className="mt-8">
            {data.providers.map((p) => (
              <Provider key={p.id} provider={p} />
            ))}
          </div>
        </>
      ) : null}
    </Page>
  );
}

function Provider({ provider }: { provider: ProviderStatus }) {
  return (
    <section className="border-t border-border py-6 last:border-b">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-base font-semibold tracking-tight">{provider.label}</h2>
        {provider.leaves_machine ? (
          <span className="text-[11px] text-muted-foreground">leaves this machine</span>
        ) : (
          <span className="text-[11px] font-medium text-accent">nothing leaves this machine</span>
        )}
        <Ready provider={provider} />
      </div>

      <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
        {provider.detail}.
      </p>

      {provider.env ? (
        <div className="mt-3 max-w-3xl">
          <CommandLine text={`export ${provider.env}=...`} />
          {provider.installs ? (
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              The SDK is an extra, so <code className="font-mono">import neti</code> never pulls it
              in: <code className="font-mono">{provider.installs}</code>
            </p>
          ) : null}
        </div>
      ) : null}

      {provider.runners.length > 0 ? <Local runners={provider.runners} /> : null}

      <div className="mt-4 max-w-3xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Then
        </p>
        <CommandLine className="mt-1.5" text={provider.command} />
      </div>
    </section>
  );
}

function Ready({ provider }: { provider: ProviderStatus }) {
  if (!provider.env) return null;
  return provider.ready ? (
    <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-accent">
      <Check className="h-3 w-3" strokeWidth={3} />
      {provider.env} is set
    </span>
  ) : (
    <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <KeyRound className="h-3 w-3" />
      {provider.env} is not set in this shell
    </span>
  );
}

/**
 * The local runners, and your own endpoint.
 *
 * The same row shape for both, because they are the same thing: an address that speaks
 * chat-completions. A self-hosted gateway on a company domain is not a different integration, it is
 * a different URL — and saying so is more useful than an "enterprise" section that implies
 * otherwise.
 */
function Local({ runners }: { runners: Runner[] }) {
  const [custom, setCustom] = useState("");
  const [probing, setProbing] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ProbeResult>>({});

  const check = (url: string) => {
    if (!url.trim()) return;
    setProbing(url);
    api
      .probeModels(url.trim())
      .then((r) => setResults((prev) => ({ ...prev, [url]: r })))
      .catch((e: unknown) =>
        setResults((prev) => ({
          ...prev,
          [url]: {
            reachable: false,
            base_url: url,
            models: [],
            reason: e instanceof Error ? e.message : String(e),
          },
        })),
      )
      .finally(() => setProbing(null));
  };

  return (
    <div className="mt-4 max-w-3xl">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Anything speaking the OpenAI chat-completions API
      </p>

      <ul className="mt-2">
        {runners.map((r) => (
          <li key={r.id} className="border-t border-border py-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="w-24 text-[13px] font-medium">{r.label}</span>
              <code className="font-mono text-[12px] text-accent">{r.base_url}</code>
              <code className="ml-auto font-mono text-[11px] text-muted-foreground">{r.start}</code>
              <Check2
                onClick={() => check(r.base_url)}
                busy={probing === r.base_url}
                result={results[r.base_url]}
              />
            </div>
            <Result result={results[r.base_url]} />
          </li>
        ))}

        {/* Your own endpoint. A gateway on a company domain, a shared inference box, a proxy in
            front of a hosted provider — all the same shape, all one URL. */}
        <li className="border-t border-border py-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
            <span className="w-24 text-[13px] font-medium">Your own</span>
            <input
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && check(custom)}
              placeholder="https://models.yourcompany.com/v1"
              spellCheck={false}
              aria-label="Your own endpoint"
              className="min-w-0 flex-1 rounded-md border border-border bg-transparent px-2.5 py-1.5 font-mono text-[12px] outline-none placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-accent"
            />
            <Check2
              onClick={() => check(custom)}
              busy={probing === custom}
              result={results[custom]}
            />
          </div>
          <Result result={results[custom]} />
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            A self-hosted gateway is not a different integration — it is a different URL. Pass it as{" "}
            <code className="font-mono">--base-url</code>. A server that ignores the Authorization
            header needs no key at all.
          </p>
        </li>
      </ul>
    </div>
  );
}

function Check2({
  onClick,
  busy,
  result,
}: {
  onClick: () => void;
  busy: boolean;
  result?: ProbeResult;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium ring-1 ring-inset transition-colors",
        result?.reachable
          ? "text-accent ring-accent/30"
          : "text-muted-foreground ring-border hover:text-foreground",
      )}
    >
      {busy ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Server className="h-3 w-3" />
      )}
      {busy ? "Checking" : result ? "Check again" : "Check"}
    </button>
  );
}

function Result({ result }: { result?: ProbeResult }) {
  if (!result) return null;
  if (!result.reachable) {
    // The reason, not a bare "unreachable". "Connection refused on 11434" and "404 at /v1/models"
    // send somebody to completely different places.
    return <p className="mt-1.5 text-[12px] text-muted-foreground">{result.reason}</p>;
  }
  if (result.models.length === 0) {
    return (
      <p className="mt-1.5 text-[12px] text-muted-foreground">
        Reachable, and holding no models. The server is up — that is a different problem from it
        being down.
      </p>
    );
  }
  return (
    <p className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-[12px] text-muted-foreground">
      <span className="text-accent">reachable ·</span>
      {result.models.slice(0, 8).map((m) => (
        <code key={m} className="font-mono text-[11px]">
          {m}
        </code>
      ))}
      {result.models.length > 8 ? <span>and {result.models.length - 8} more</span> : null}
    </p>
  );
}
