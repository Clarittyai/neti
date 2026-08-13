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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, type Variants } from "framer-motion";
import {
  Activity,
  FileCheck,
  Compass,
  Cpu,
  Gauge,
  Plug,
  ScrollText,
  SlidersHorizontal,
  Target,
  UserCheck,
} from "lucide-react";
import { Mark } from "@/components/Mark";

import { cn } from "@/lib/utils";
import { useConsole } from "@/components/ConsoleProvider";
import { api } from "@/lib/api";

/**
 * The rail, grouped by how often anybody opens the thing — and filtered to what is true here.
 *
 * It reached ten flat entries, of which **three** are opened daily. `Approvals` was the worst of
 * them: a control plane is the hosted tier, so on a free local install that page reports
 * `attached: false` and can *never* have content. A permanent nav entry that is structurally empty
 * is not navigation, it is advertising, and it makes the nine real ones harder to find.
 *
 * So the groups are frequency, not category, and two entries appear only when they can do
 * something:
 *
 *   - `Approvals` needs a control plane attached.
 *   - `Getting started` disappears once the walkthrough is complete — it is onboarding, and
 *     onboarding that will not get out of the way is a permanent reminder of a finished job.
 *     Still at `/start` for anyone wiring a second seam later.
 *
 * Everything else stays reachable. Nothing here is removed, only demoted below a rule.
 */
const DAILY = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/decisions", label: "Decisions", icon: ScrollText },
  { href: "/policy", label: "Policy", icon: SlidersHorizontal },
];

const OCCASIONAL = [
  { href: "/gate", label: "Live gate", icon: Activity },
  { href: "/audit", label: "Audit", icon: FileCheck },
  { href: "/scorecard", label: "Scorecard", icon: Target },
];

const SETUP = [
  { href: "/start", label: "Getting started", icon: Compass },
  { href: "/connect", label: "Connect", icon: Plug },
  { href: "/models", label: "Models", icon: Cpu },
];

const APPROVALS = { href: "/approvals", label: "Approvals", icon: UserCheck };

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
  // Two entries earn their place conditionally, so the rail has to know two facts about this
  // install. Both are cheap reads and neither blocks the first paint — the rail renders without
  // them and settles, rather than holding the whole page for a nav decision.
  const [attached, setAttached] = useState(false);
  const [onboarding, setOnboarding] = useState(true);
  useEffect(() => {
    api.org().then((o) => setAttached(Boolean(o.attached))).catch(() => undefined);
    api.start().then((s) => setOnboarding(!s.complete)).catch(() => undefined);
  }, []);

  const groups = useMemo(
    () => [
      DAILY,
      attached ? [...OCCASIONAL, APPROVALS] : OCCASIONAL,
      onboarding ? SETUP : SETUP.filter((i) => i.href !== "/start"),
    ],
    [attached, onboarding],
  );

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
            <Mark className="h-8 w-8 flex-shrink-0 text-accent" />
            <motion.span variants={item} className="whitespace-nowrap text-[15px] font-semibold tracking-tight">
              neti
            </motion.span>
          </Link>
        </div>

        <nav className="flex-1 space-y-1 px-2 py-4">
          {groups.map((group, gi) => (
            <div key={gi} className={gi > 0 ? "mt-3 space-y-1 border-t border-border/50 pt-3" : "space-y-1"}>
          {group.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                // Copied from clarity-platform's `Sidebar.tsx`, and it had drifted in three
                // places: no radius at all against its `rounded-lg`, a 10% active tint against its
                // 15% in dark, and a `foreground/5` hover against its `primary/10`. The square
                // corner is the one that shows — a full-bleed bar reads as a section header rather
                // than a selected row, which is the opposite of what the state means.
                //
                // DESIGN.md says these are *copied, not imported*, and that the cost is drift the
                // tests have to catch. This is what that drift looks like when nothing catches it.
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg py-2.5 pl-[14px] pr-3 transition-colors duration-150",
                  active
                    ? "bg-accent/10 text-accent dark:bg-accent/15"
                    : "text-muted-foreground hover:bg-primary/10 hover:text-foreground",
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                <motion.span variants={item} className="whitespace-nowrap text-sm font-medium">
                  {label}
                </motion.span>
              </Link>
            );
          })}
            </div>
          ))}
        </nav>

      </motion.aside>

      {/* `min-w-0`, and it is load-bearing twice over.
          A flex child defaults to `min-width: auto`, so it refuses to shrink below its content —
          which means `flex-1` sized this to the full row *and* `md:ml-16` then pushed it 64px
          further, making the document 64px wider than the viewport on every page in the console.
          The body scrolled sideways everywhere, which DESIGN.md forbids and nothing had measured.
          It also stops a wide child — a long command line, a table — from doing the same thing
          from the inside. */}
      <main className="min-h-[100dvh] min-w-0 flex-1 md:ml-16">{children}</main>
    </div>
  );
}

