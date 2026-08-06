"use client";

/**
 * The featured band at the top of the overview — what a second machine adds.
 *
 * Shaped like the App Store's featured row: one large slide at a time, an eyebrow, a headline you
 * can read across the room, a live visual, and a single action. It auto-advances and the dots say
 * where you are.
 *
 * **No plate.** The App Store puts its features on rounded cards; neti's rules do not (DESIGN.md:
 * do not default to cards, no shadows). The prominence here comes from scale and space instead —
 * big type, a wide scene, and a rule under the band separating it from your own numbers. It reads
 * as featured without becoming the one card on a page that has none.
 *
 * Each slide carries one of the live scenes, so the band is showing the product working rather
 * than describing it. The pairing is deliberate: the scene depicts the thing the slide claims.
 *
 * The honest framing matters more than the pitch, and the rest of this repository is why: the local
 * install is not a demo, a trial or a reduced mode. So no slide says "unlock", "upgrade" or "pro" —
 * every one names something a single machine genuinely cannot do, which is the same rule
 * (*can one machine do this?*) that decides what is free in LICENSING.md.
 */
import { useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUpRight } from "lucide-react";

import { EASE, useLiveGate, useTurn } from "./live/kernel";
import { ChainScene } from "./live/scenes/ChainScene";
import { ConnectScene } from "./live/scenes/ConnectScene";
import { GateScene } from "./live/scenes/GateScene";
import { ReachScene } from "./live/scenes/ReachScene";

const WEBSITE = "https://neti-security.github.io/neti/";

/** Each one is something one machine cannot do — not a feature withheld from it. */
const SLIDES = [
  {
    eyebrow: "Approvals",
    title: "A confirm that reaches a human",
    body: "On one machine there is nobody to ask, so a confirm stops the call. Hosted, it goes to somebody who can answer — and their answer is recorded next to the decision.",
    scene: <GateScene />,
  },
  {
    eyebrow: "Fleet policy",
    title: "One policy across every machine",
    body: "Ceilings you reviewed once, enforced everywhere an agent runs, instead of a neti.yaml per laptop drifting apart at its own pace.",
    scene: <ReachScene />,
  },
  {
    eyebrow: "Central records",
    title: "Every machine's decisions in one chain",
    body: "The record chain is per machine by construction. Hosted, they land together — so “what did our agents touch this week” becomes a question with an answer.",
    scene: <ChainScene />,
  },
  {
    eyebrow: "Detection catalogue",
    title: "Gates for the tools no rule claims",
    body: "The rule table gates what it can prove and declines the rest in writing. The hosted catalogue is the adjudicated answer key, growing as tools are read rather than as somebody remembers to write a rule.",
    scene: <ConnectScene />,
  },
];

const TURN_MS = 7000;

export function CloudSlides() {
  const ref = useRef<HTMLDivElement>(null);
  const live = useLiveGate(ref);
  const turn = useTurn(SLIDES.length, live, TURN_MS);
  const slide = SLIDES[turn] ?? SLIDES[0]!;

  return (
    <div ref={ref} className="border-b border-border pb-8">
      <AnimatePresence mode="wait">
        <motion.div
          key={slide.title}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.45, ease: EASE }}
          className="grid items-center gap-8 md:grid-cols-[1.1fr_1fr]"
        >
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-accent">
              {slide.eyebrow}
            </p>
            <h2 className="mt-2 max-w-lg text-2xl font-semibold leading-tight tracking-tight text-foreground sm:text-[28px]">
              {slide.title}
            </h2>
            <p className="mt-3 max-w-lg text-sm leading-relaxed text-muted-foreground">
              {slide.body}
            </p>
          </div>

          {/* The visual. A spotlight rather than a frame — the scene sits in light, not on a
              plate, exactly as it does inside an empty state. */}
          <div className="relative">
            <div
              aria-hidden
              className="pointer-events-none absolute left-1/2 top-2 h-40 w-64 -translate-x-1/2 bg-accent/10 blur-3xl"
            />
            <div className="relative mx-auto max-w-sm">{slide.scene}</div>
          </div>
        </motion.div>
      </AnimatePresence>

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <a
          href={WEBSITE}
          target="_blank"
          rel="noreferrer"
          className="inline-flex min-h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent/90"
        >
          neti.security
          <ArrowUpRight className="h-4 w-4" />
        </a>

        {/* Position, not navigation: dots you cannot press are honest about being a rotation
            rather than pretending to be a control nobody uses. */}
        <div className="flex items-center gap-1.5" aria-hidden>
          {SLIDES.map((s, i) => (
            <span
              key={s.title}
              className={
                i === turn
                  ? "h-1.5 w-4 rounded-full bg-accent transition-all"
                  : "h-1.5 w-1.5 rounded-full bg-border transition-all"
              }
            />
          ))}
        </div>

        <p className="text-xs text-muted-foreground">
          Everything below stays free. The rule is whether one machine can do it.
        </p>
      </div>
    </div>
  );
}
