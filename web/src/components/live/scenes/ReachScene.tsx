"use client";

/**
 * ReachScene — what one call could touch, before any call has been made.
 *
 * A scatter of objects settles, a count lands on them, and a ceiling line sits above it. That is
 * the overview's whole first-run claim: a number you can have on day one, with no traffic, because
 * it is read from the directory rather than measured from history.
 *
 * One idea on one axis, per DESIGN.md: the count rises, the ceiling does not move.
 */
import { motion } from "framer-motion";

import { Stage } from "../Stage";
import { EASE, useSceneRoot, useTurn } from "../kernel";

const W = 340;
const H = 150;
const STEPS = [12, 96, 480, 1680];
const TURN_MS = 1600;

/** Deterministic, so the composition is the same every render and in every screenshot. */
const DOTS = Array.from({ length: 44 }, (_, i) => ({
  x: 26 + ((i * 47) % (W - 60)),
  y: 58 + ((i * 29) % 74),
  d: (i % 7) * 0.06,
}));

export function ReachScene() {
  const { ref, live } = useSceneRoot();
  // Frozen frame settles on the last step: a still scene should read as "counted", not "counting".
  const turn = useTurn(STEPS.length, live, TURN_MS);
  const step = live ? turn : STEPS.length - 1;
  const shown = STEPS[step] ?? 0;
  const share = shown / STEPS[STEPS.length - 1]!;

  return (
    <div ref={ref} aria-hidden>
      <Stage width={W} height={H}>
        {/* The ceiling. Fixed, because a ceiling is something you declared, not something we found. */}
        <svg className="absolute inset-0" width={W} height={H} viewBox={`0 0 ${W} ${H}`} fill="none">
          <line
            x1={20}
            y1={38}
            x2={W - 20}
            y2={38}
            stroke="hsl(var(--verdict-confirm))"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            opacity={0.7}
          />
        </svg>
        <span className="absolute right-5 top-[18px] text-[10px] font-medium uppercase tracking-wide text-[hsl(var(--verdict-confirm))]">
          ceiling
        </span>

        {DOTS.map((dot, i) => (
          <motion.span
            key={i}
            className="absolute h-1.5 w-1.5 rounded-full bg-accent"
            style={{ left: dot.x, top: dot.y }}
            initial={{ opacity: 0, scale: 0.4 }}
            animate={{ opacity: i / DOTS.length <= share ? 0.85 : 0.12, scale: 1 }}
            transition={{ duration: 0.5, delay: dot.d, ease: EASE }}
          />
        ))}

        <motion.div
          key={shown}
          className="tnum absolute left-1/2 top-[92px] -translate-x-1/2 text-2xl font-semibold tracking-tight text-foreground"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: EASE }}
        >
          {shown.toLocaleString()}
        </motion.div>
        <div className="absolute left-1/2 top-[124px] -translate-x-1/2 text-[11px] text-muted-foreground">
          objects reachable
        </div>
      </Stage>
    </div>
  );
}
