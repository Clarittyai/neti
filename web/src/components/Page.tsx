"use client";

/**
 * Page furniture, extracted rather than copy-pasted.
 *
 * clarity-platform repeats its page header shape by hand on every screen, which is why its headers
 * have quietly drifted apart. Six screens is enough for that to start, so the shape lives here once.
 *
 * `Loading` and `Failed` matter more than they look. A console whose screens go blank while fetching
 * reads as broken, and one that swallows a dead API reads as *lying* — which is the worse failure
 * for a product whose entire claim is that what you see is what the gate did.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { motion } from "framer-motion";

import { cn } from "@/lib/utils";

export function Page({
  title,
  lede,
  actions,
  children,
  width = "wide",
}: {
  title: string;
  lede?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  width?: "wide" | "narrow";
}) {
  return (
    <div
      className={cn(
        // The bottom padding clears the pinned mode chip, which floats over everything and would
        // otherwise sit on the last line of any page scrolled to its end.
        "mx-auto w-full px-4 pb-24 pt-8 sm:px-6 lg:px-8",
        width === "wide" ? "max-w-7xl" : "max-w-3xl",
      )}
    >
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
      >
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight md:text-3xl">{title}</h1>
          {lede ? (
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground md:text-base">{lede}</p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </motion.div>
      <div className="mt-8">{children}</div>
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-border/60 bg-card px-4 py-8 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}…
    </div>
  );
}

export function Failed({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2.5 rounded-xl border border-[hsl(var(--verdict-block))]/30 bg-[hsl(var(--verdict-block))]/[0.06] px-4 py-4 text-sm">
      <AlertTriangle className="h-4 w-4 flex-shrink-0 text-[hsl(var(--verdict-block))]" />
      <span className="min-w-0 flex-1 text-muted-foreground">{error}</span>
      {onRetry ? (
        <button onClick={onRetry} className="font-medium text-accent hover:underline">
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Empty({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-border px-5 py-10 text-center">
      <p className="text-sm font-semibold">{title}</p>
      <p className="mx-auto mt-1 max-w-sm text-[13px] leading-relaxed text-muted-foreground">
        {body}
      </p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function Stat({
  value,
  label,
  hint,
  tone,
}: {
  value: string;
  label: string;
  hint?: string;
  tone?: "block" | "confirm" | "allow";
}) {
  return (
    <div className="glass-card rounded-2xl p-5">
      <div
        className={cn(
          "tnum text-3xl font-semibold tracking-tight",
          tone === "block" && "text-[hsl(var(--verdict-block))]",
          tone === "confirm" && "text-[hsl(var(--verdict-confirm))]",
          tone === "allow" && "text-[hsl(var(--verdict-allow))]",
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-sm text-muted-foreground">{label}</div>
      {hint ? <div className="mt-2 text-[11px] text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

/** One place that turns a fetch into the three states every screen needs. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const run = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    fn()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => run(), [run]);
  return { data, error, loading, reload: run };
}
