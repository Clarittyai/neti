"use client";

/**
 * GateScene — the product in one loop: a call arrives, it is sized, and the number decides.
 *
 * Two calls travel the same rail toward the same ceiling. The small one passes under it. The large
 * one stops at it and is turned back. Nothing here is about who asked — that is the point the gate
 * makes, and the reason this scene has no model in it.
 */
import { AnimatePresence, motion } from "framer-motion";

import { Stage } from "../Stage";
import { EASE, useSceneRoot, useTurn } from "../kernel";

const W = 340;
const H = 156;
const TURN_MS = 2400;
const GATE_X = 232;

/** Short by design — a label that truncates defeats the scene (DESIGN.md). */
const CALLS = [
  { size: 41, blocked: false },
  { size: 1680, blocked: true },
];

export function GateScene() {
  const { ref, live } = useSceneRoot();
  const turn = useTurn(CALLS.length, live, TURN_MS);
  // Frozen on the blocked call: a still frame should show the thing worth stopping.
  const call = CALLS[live ? turn : 1]!;

  return (
    <div ref={ref} aria-hidden>
      <Stage width={W} height={H}>
        <svg className="absolute inset-0" width={W} height={H} viewBox={`0 0 ${W} ${H}`} fill="none">
          <line x1={20} y1={86} x2={W - 24} y2={86} stroke="hsl(var(--border))" strokeWidth={1} />
          <line
            x1={GATE_X}
            y1={44}
            x2={GATE_X}
            y2={122}
            stroke="hsl(var(--verdict-confirm))"
            strokeWidth={1.5}
            strokeDasharray="4 4"
          />
        </svg>
        <span className="absolute left-[196px] top-[26px] text-[10px] font-medium uppercase tracking-wide text-[hsl(var(--verdict-confirm))]">
          ceiling 300
        </span>

        <AnimatePresence mode="wait">
          <motion.div
            key={`${call.size}-${turn}`}
            className="absolute top-[74px]"
            initial={{ left: 20, opacity: 0 }}
            animate={{ left: call.blocked ? GATE_X - 46 : W - 62, opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.1, ease: EASE }}
          >
            <span
              className={
                call.blocked
                  ? "tnum bg-[hsl(var(--verdict-block))]/15 px-2.5 py-1 text-xs font-semibold text-[hsl(var(--verdict-block))]"
                  : "tnum bg-[hsl(var(--verdict-allow))]/15 px-2.5 py-1 text-xs font-semibold text-[hsl(var(--verdict-allow))]"
              }
            >
              {call.size.toLocaleString()}
            </span>
          </motion.div>
        </AnimatePresence>

        <motion.div
          key={call.blocked ? "stopped" : "through"}
          className="absolute left-1/2 top-[122px] -translate-x-1/2 whitespace-nowrap text-[11px] text-muted-foreground"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.9, duration: 0.4 }}
        >
          {call.blocked ? "stopped before it ran" : "under the ceiling, straight through"}
        </motion.div>
      </Stage>
    </div>
  );
}
