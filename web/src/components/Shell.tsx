"use client";

/**
 * The app shell, forked from clarity-platform's `Sidebar.tsx`.
 *
 * Kept: the hover-expanding 64↔220px rail and its spring, the constant left padding so the icon
 * never shifts as the label appears, and the glass surface.
 *
 * Dropped deliberately: the PWA launch veil, view transitions, and the mobile pill nav. The shell
 * report flags those three as one interlocking system — the pre-paint scripts, the `data-standalone`
 * stamp and the mobile doc-scroll block only work together — and none of it earns its place in a
 * console you drive on a laptop.
 *
 * Added: the mode chip. It is never hidden and never abbreviated, because the console runs on a
 * synthetic fixture by default and a viewer must never have to wonder which they are looking at.
 */

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, type Variants } from "framer-motion";
import { Activity, FileCheck, Gauge, Plug, ScrollText, ShieldCheck, SlidersHorizontal } from "lucide-react";

import { cn } from "@/lib/utils";
import { useConsole } from "@/components/ConsoleProvider";

const NAV = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/gate", label: "Live gate", icon: Activity },
  { href: "/decisions", label: "Decisions", icon: ScrollText },
  { href: "/policy", label: "Policy", icon: SlidersHorizontal },
  { href: "/audit", label: "Audit", icon: FileCheck },
  { href: "/connect", label: "Connect", icon: Plug },
];

// The house springs, unchanged from claritty. The 0.22s delay on collapse is what stops the rail
// flickering when the pointer crosses it on the way somewhere else.
const rail: Variants = {
  expanded: {
    width: 220,
    transition: { type: "spring", stiffness: 300, damping: 30, staggerChildren: 0.03, delayChildren: 0.05 },
  },
  collapsed: {
    width: 64,
    transition: { type: "spring", stiffness: 300, damping: 30, staggerChildren: 0.025, staggerDirection: -1, delay: 0.22 },
  },
};

const item: Variants = {
  expanded: { opacity: 1, x: 0, transition: { type: "spring", stiffness: 400, damping: 25 } },
  collapsed: { opacity: 0, x: -10, transition: { duration: 0.2, ease: "easeIn" } },
};

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [expanded, setExpanded] = useState(false);
  const [animating, setAnimating] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const open = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    setAnimating(true);
    setExpanded(true);
  }, []);

  const close = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setExpanded(false);
      // Keep overflow visible while labels animate out of a 64px box, or they clip mid-flight.
      setTimeout(() => setAnimating(false), 600);
    }, 150);
  }, []);

  return (
    <div className="flex min-h-[100dvh] w-full">
      <motion.aside
        initial="collapsed"
        animate={expanded ? "expanded" : "collapsed"}
        variants={rail}
        onMouseEnter={open}
        onMouseLeave={close}
        className={cn(
          "fixed left-0 top-0 z-40 hidden h-[100dvh] flex-col border-r border-border/50 md:flex",
          "bg-background/95 backdrop-blur-xl transition-colors duration-300",
          !animating && "overflow-hidden",
        )}
      >
        <div className="flex h-16 items-center border-b border-border/50 px-4">
          <Link href="/" className="flex items-center gap-3">
            <span className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-lg bg-accent">
              <ShieldCheck className="h-[18px] w-[18px] text-accent-foreground" strokeWidth={2.5} />
            </span>
            <motion.span variants={item} className="whitespace-nowrap text-[15px] font-semibold tracking-tight">
              neti
            </motion.span>
          </Link>
        </div>

        <nav className="flex-1 space-y-1 px-2 py-4">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg py-2.5 pl-[14px] pr-3 transition-colors duration-150",
                  active
                    ? "bg-accent/10 text-accent"
                    : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                <motion.span variants={item} className="whitespace-nowrap text-sm font-medium">
                  {label}
                </motion.span>
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-border/50 px-2 py-4">
          <ModeChip collapsed={!expanded} />
        </div>
      </motion.aside>

      <main className="min-h-[100dvh] flex-1 md:ml-16">{children}</main>
    </div>
  );
}

/**
 * Which tenant, and whether the gate can actually stop anything.
 *
 * Both facts are load-bearing and both are easy to lose track of mid-demo. "Demo tenant" is stated
 * plainly rather than softened — presenting fixture numbers as a finding about a real directory is
 * exactly the overclaim the rest of this codebase is built to avoid.
 */
function ModeChip({ collapsed }: { collapsed: boolean }) {
  const { state } = useConsole();
  if (!state) return null;

  const demo = state.mode === "demo";
  const enforcing = state.policy_mode === "enforce";

  return (
    <div className="flex items-center gap-3 rounded-lg px-3 py-2">
      <span
        className={cn(
          "h-2 w-2 flex-shrink-0 rounded-full",
          enforcing ? "bg-[hsl(var(--verdict-allow))]" : "bg-muted-foreground/60",
        )}
        aria-hidden
      />
      <motion.div variants={item} className="min-w-0 flex-1 overflow-hidden">
        <p className="truncate text-xs font-medium">{demo ? "Demo tenant" : "Live tenant"}</p>
        <p className="truncate text-[11px] text-muted-foreground">
          {enforcing ? "enforcing" : "observing — nothing is blocked"}
        </p>
      </motion.div>
      {collapsed ? <span className="sr-only">{demo ? "Demo tenant" : "Live tenant"}</span> : null}
    </div>
  );
}
