"use client";

/**
 * A command, with a copy button. One implementation, because there were about to be three.
 *
 * Extracted when `/models` needed exactly what `Walkthrough` already had. A second copy would have
 * drifted within a release — the first one to grow a wrap, a tooltip or a different radius wins,
 * and then nothing matches.
 */

import { useEffect, useRef, useState } from "react";
import { Check, Copy, Terminal } from "lucide-react";

import { cn } from "@/lib/utils";

export function CommandLine({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => clearTimeout(timer.current ?? undefined), []);

  return (
    <div className={cn("flex items-center gap-2 bg-muted/60 px-3 py-2", className)}>
      <Terminal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-[13px]">
        {text}
      </code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(text);
          setCopied(true);
          timer.current = setTimeout(() => setCopied(false), 1400);
        }}
        className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium text-muted-foreground ring-1 ring-inset ring-border transition-colors hover:text-foreground"
      >
        {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
