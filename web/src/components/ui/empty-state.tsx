"use client";

/**
 * EmptyState — the one way to say "there is nothing here yet".
 *
 * Before this, the console had `Empty` in `Page.tsx`, a hand-rolled dashed rectangle in
 * `gate/page.tsx`, and nothing at all on seven other pages. That is the same drift clarity-platform
 * recorded before it built this primitive, and the fix is the same one: a single component, so a
 * new page cannot invent a ninth treatment.
 *
 * Three densities:
 *   page    — a first-run moment. Full-bleed, centred, usually with a `scene`.
 *   section — a block inside a populated page. Icon, not scene.
 *   inline  — one quiet line (a search miss, an empty sub-list). No icon, no CTA.
 *
 * **No border, no card, no dashed box.** An empty state is open space with something living in it,
 * not a plate — see DESIGN.md. The dashed rectangle this replaces is listed there as an
 * anti-pattern, by name.
 *
 * `scene` is reserved for first-run surfaces (the overview, the gate, the audit trail, connect);
 * everywhere else takes a lucide `icon`. Passing both is a mistake — the scene wins.
 *
 * ONE accent action per view. A page whose empty state carries the CTA hides its own header button
 * while empty.
 */
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type EmptyStateAction = {
  label: string;
  icon?: LucideIcon;
  onClick?: () => void;
  /** Renders the CTA as a link instead of a button. */
  href?: string;
};

export function EmptyState({
  size = "section",
  scene,
  icon: Icon,
  title,
  description,
  action,
  secondary,
  className,
}: {
  size?: "page" | "section" | "inline";
  /** A live scene from `components/live/scenes` — takes the place of the icon. */
  scene?: React.ReactNode;
  icon?: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: EmptyStateAction;
  /** A quieter follow-up ("or read what it would gate"). */
  secondary?: React.ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion() ?? false;

  // A search miss does not deserve a stage and a headline — just say it.
  if (size === "inline") {
    return (
      <p className={cn("py-3 text-center text-xs text-muted-foreground", className)}>
        {title}
        {description ? <span className="mt-0.5 block">{description}</span> : null}
      </p>
    );
  }

  const isPage = size === "page";

  return (
    <div
      className={cn(
        "relative flex flex-col items-center text-center",
        isPage ? "min-h-[46vh] justify-center px-6 py-10" : "px-6 py-12",
        className,
      )}
    >
      {scene ? (
        <div className="relative mb-6 w-full max-w-sm">
          {/* Soft spotlight — the scene sits in light, not on a plate. */}
          <div
            aria-hidden
            className="pointer-events-none absolute left-1/2 top-4 h-40 w-72 -translate-x-1/2 rounded-full bg-accent/10 blur-3xl"
          />
          <div className="relative">{scene}</div>
        </div>
      ) : Icon ? (
        <motion.div
          initial={reduce ? false : { opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.35, ease: [0.25, 0.46, 0.45, 0.94] }}
          className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/10 text-accent"
        >
          <Icon className="h-7 w-7" />
        </motion.div>
      ) : null}

      <motion.div
        initial={reduce ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: scene ? 0.3 : 0.1, duration: 0.45 }}
        className="relative flex flex-col items-center"
      >
        <h3 className={cn("font-semibold text-foreground", isPage ? "text-lg" : "text-base")}>
          {title}
        </h3>

        {description ? (
          <p className="mt-1.5 max-w-sm text-[13px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}

        {action ? (
          <div className="mt-6">
            {action.href ? (
              <Link
                href={action.href}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent/90"
              >
                {action.icon ? <action.icon className="h-4 w-4" /> : null}
                {action.label}
              </Link>
            ) : (
              <button
                onClick={action.onClick}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent/90"
              >
                {action.icon ? <action.icon className="h-4 w-4" /> : null}
                {action.label}
              </button>
            )}
          </div>
        ) : null}

        {secondary ? <div className="mt-3 text-xs text-muted-foreground">{secondary}</div> : null}
      </motion.div>
    </div>
  );
}
