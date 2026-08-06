"use client";

/**
 * ConnectScene — nothing resolves until something is connected.
 *
 * A socket waits, a provider attaches, and a real number arrives. The order is the message: the
 * count is a consequence of the connection, not a default the product ships with. Until then the
 * gate declines to answer rather than inventing one.
 */
import { AnimatePresence, motion } from "framer-motion";

import { Stage } from "../Stage";
import { EASE, useSceneRoot, useTurn } from "../kernel";

const W = 340;
const H = 150;
const TURN_MS = 1500;
/** waiting -> attached -> resolved, then round again. */
const PHASES = 3;

export function ConnectScene() {
  const { ref, live } = useSceneRoot();
  const turn = useTurn(PHASES, live, TURN_MS);
  // Frozen on `resolved`: the still frame should show what connecting gets you.
  const phase = live ? turn : PHASES - 1;

  return (
    <div ref={ref} aria-hidden>
      <Stage width={W} height={H}>
        <svg className="absolute inset-0" width={W} height={H} viewBox={`0 0 ${W} ${H}`} fill="none">
          <motion.line
            x1={92}
            y1={70}
            x2={248}
            y2={70}
            stroke="hsl(var(--accent))"
            strokeWidth={1.5}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: phase >= 1 ? 1 : 0, opacity: phase >= 1 ? 0.85 : 0.15 }}
            transition={{ duration: 0.5, ease: EASE }}
          />
        </svg>

        {/* The gate, always there. */}
        <div className="absolute left-[52px] top-[56px] flex h-7 w-[42px] items-center justify-center border border-border text-[10px] font-medium text-muted-foreground">
          gate
        </div>

        {/* The provider, arriving. */}
        <motion.div
          className="absolute top-[56px] flex h-7 items-center justify-center border px-2.5 text-[10px] font-medium"
          style={{ left: 248 }}
          initial={false}
          animate={{
            opacity: phase >= 1 ? 1 : 0.25,
            borderColor: phase >= 1 ? "hsl(var(--accent))" : "hsl(var(--border))",
            color: phase >= 1 ? "hsl(var(--accent))" : "hsl(var(--muted-foreground))",
          }}
          transition={{ duration: 0.4, ease: EASE }}
        >
          provider
        </motion.div>

        <div className="absolute left-1/2 top-[100px] -translate-x-1/2 text-center">
          <AnimatePresence mode="wait">
            <motion.div
              key={phase}
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -5 }}
              transition={{ duration: 0.3 }}
              className="whitespace-nowrap text-[11px] text-muted-foreground"
            >
              {phase === 0 ? (
                "unresolved — the gate will not guess"
              ) : phase === 1 ? (
                "connected"
              ) : (
                <span className="tnum font-semibold text-foreground">41,203 principals</span>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </Stage>
    </div>
  );
}
