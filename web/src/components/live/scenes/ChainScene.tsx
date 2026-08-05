"use client";

/**
 * ChainScene — every decision sealed to the one before it.
 *
 * Records land in sequence and each one links to its predecessor. It loops because the chain is not
 * an event, it is the shape of the record: the fourth link is only worth anything because the three
 * behind it still verify.
 */
import { motion } from "framer-motion";

import { Stage } from "../Stage";
import { EASE, useSceneRoot, useTurn } from "../kernel";

const W = 340;
const H = 146;
const LINKS = 4;
const TURN_MS = 1400;
const EDGE = 46;
const GAP = (W - EDGE * 2) / (LINKS - 1);

export function ChainScene() {
  const { ref, live } = useSceneRoot();
  const turn = useTurn(LINKS + 1, live, TURN_MS);
  // Frozen with the chain complete — a still frame of a half-built chain reads as a broken one.
  const sealed = live ? turn : LINKS;

  return (
    <div ref={ref} aria-hidden>
      <Stage width={W} height={H}>
        <svg className="absolute inset-0" width={W} height={H} viewBox={`0 0 ${W} ${H}`} fill="none">
          {Array.from({ length: LINKS - 1 }, (_, i) => (
            <motion.line
              key={i}
              x1={EDGE + i * GAP + 11}
              y1={72}
              x2={EDGE + (i + 1) * GAP - 11}
              y2={72}
              stroke="hsl(var(--accent))"
              strokeWidth={1.5}
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: i + 1 < sealed ? 1 : 0, opacity: i + 1 < sealed ? 0.8 : 0 }}
              transition={{ duration: 0.45, ease: EASE }}
            />
          ))}
        </svg>

        {Array.from({ length: LINKS }, (_, i) => (
          <motion.span
            key={i}
            className="absolute flex h-[22px] w-[22px] items-center justify-center rounded-full border border-accent/40 bg-accent/10"
            style={{ left: EDGE + i * GAP - 11, top: 61 }}
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: i < sealed ? 1 : 0.15, y: 0 }}
            transition={{ duration: 0.4, delay: i * 0.05, ease: EASE }}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          </motion.span>
        ))}

        <div className="absolute left-1/2 top-[104px] -translate-x-1/2 whitespace-nowrap text-[11px] text-muted-foreground">
          {sealed >= LINKS ? "chain intact" : "sealing to the one before"}
        </div>
      </Stage>
    </div>
  );
}
