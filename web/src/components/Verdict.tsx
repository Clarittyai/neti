/**
 * Verdict marks.
 *
 * One rule, enforced by making it the only way to render a verdict: **never colour alone.** Every
 * mark carries an icon and a label, because roughly one reader in twelve cannot separate the red
 * from the emerald, and because a screenshot of this console will end up in a deck printed in
 * greyscale.
 *
 * The palette is reserved. These four hues do not appear anywhere else in the product — not as a
 * chart series, not as a brand surface, not as a hover state — so that seeing one always means the
 * same thing.
 */

import { Ban, Check, Flag, Hand, HelpCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Verdict } from "@/lib/api";

type Tone = Verdict | "unknown";

const SPEC: Record<
  Tone,
  { label: string; Icon: typeof Ban; fg: string; bg: string; ring: string }
> = {
  block: {
    label: "Blocked",
    Icon: Ban,
    fg: "text-[hsl(var(--verdict-block))]",
    bg: "bg-[hsl(var(--verdict-block))]/10",
    ring: "ring-[hsl(var(--verdict-block))]/30",
  },
  confirm: {
    label: "Needs approval",
    Icon: Hand,
    fg: "text-[hsl(var(--verdict-confirm))]",
    bg: "bg-[hsl(var(--verdict-confirm))]/10",
    ring: "ring-[hsl(var(--verdict-confirm))]/30",
  },
  allow: {
    label: "Allowed",
    Icon: Check,
    fg: "text-[hsl(var(--verdict-allow))]",
    bg: "bg-[hsl(var(--verdict-allow))]/10",
    ring: "ring-[hsl(var(--verdict-allow))]/30",
  },
  // FLAG is deliberately colourless. A fourth hue would spend the channel on the rarest verdict,
  // and outline + icon separates it perfectly well.
  flag: {
    label: "Flagged",
    Icon: Flag,
    fg: "text-muted-foreground",
    bg: "bg-transparent",
    ring: "ring-border",
  },
  unknown: {
    label: "Could not size",
    Icon: HelpCircle,
    fg: "text-muted-foreground",
    bg: "hatched",
    ring: "ring-border",
  },
};

export function VerdictPill({
  verdict,
  size = "sm",
  className,
}: {
  verdict: Tone;
  size?: "sm" | "lg";
  className?: string;
}) {
  const { label, Icon, fg, bg, ring } = SPEC[verdict];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full ring-1 ring-inset font-semibold",
        size === "lg" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-[11px]",
        fg,
        bg,
        ring,
        className,
      )}
    >
      <Icon className={size === "lg" ? "h-4 w-4" : "h-3 w-3"} strokeWidth={2.5} />
      {label}
    </span>
  );
}

/** The big plate that lands at the end of the theatre. */
export function VerdictPlate({ verdict, rule }: { verdict: Tone; rule?: string }) {
  const { label, Icon, fg, bg, ring } = SPEC[verdict];
  return (
    <div className={cn(" px-5 py-4 ring-1 ring-inset", bg, ring)}>
      <div className={cn("flex items-center gap-2.5", fg)}>
        <Icon className="h-6 w-6" strokeWidth={2.5} />
        <span className="text-xl font-semibold tracking-tight">{label}</span>
      </div>
      {rule ? (
        <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">{rule}</p>
      ) : null}
    </div>
  );
}

export function verdictTone(verdict: Verdict, magnitude: number | null | undefined): Tone {
  // An unsizeable target is not a mild verdict — it is a different statement, and it gets the
  // hatched treatment rather than borrowing amber.
  return magnitude === null || magnitude === undefined ? "unknown" : verdict;
}
