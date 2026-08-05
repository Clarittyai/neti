"use client";

/**
 * What the hosted tier adds, as a slow rotation at the foot of the overview.
 *
 * The honest framing matters more than the pitch here, and the rest of this repository is the
 * reason: the local install is not a demo, a trial or a reduced mode. It resolves real magnitudes,
 * enforces real verdicts and seals a real chain. So this does not say "unlock" or "upgrade" or
 * "pro" — every slide names something a single machine genuinely cannot do, which is the same rule
 * (`can one machine do this?`) that decides what is free in LICENSING.md.
 *
 * A slideshow rather than a grid of feature cards, for two reasons. DESIGN.md forbids the card
 * grid. And a grid asks you to read four sales pitches at once, where a rotation asks you to read
 * one — at the bottom of a page whose actual job is your own numbers.
 *
 * Motion follows the live-scene kernel: gated on reduced motion, visibility and viewport, and a
 * composed frame when it is off rather than a blank strip.
 */
import { useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUpRight } from "lucide-react";

import { EASE, useLiveGate, useTurn } from "./live/kernel";

const WEBSITE = "https://neti-security.github.io/neti/";

/** Each one is a thing one machine cannot do, not a feature withheld from it. */
const SLIDES = [
  {
    title: "A confirm that reaches a human",
    body: "On one machine there is nobody to ask, so a confirm stops the call. Hosted, it goes to somebody who can answer, and their answer is recorded next to the decision.",
  },
  {
    title: "One policy across a fleet",
    body: "Ceilings you reviewed once, enforced on every machine that runs an agent — instead of a neti.yaml per laptop drifting apart at its own pace.",
  },
  {
    title: "Every machine's decisions in one place",
    body: "The chain is per machine by construction. Hosted, the records land together, so 'what did our agents touch this week' is a question with an answer.",
  },
  {
    title: "A reviewed detection catalogue",
    body: "The rule table gates what it can prove and declines the rest in writing. The hosted catalogue is the adjudicated answer key, growing as tools are read rather than as anyone remembers to write a rule.",
  },
];

const TURN_MS = 6000;

export function CloudSlides() {
  const ref = useRef<HTMLDivElement>(null);
  const live = useLiveGate(ref);
  const turn = useTurn(SLIDES.length, live, TURN_MS);
  const slide = SLIDES[turn] ?? SLIDES[0]!;

  return (
    <div ref={ref} className="mt-10 border-t border-border pt-6">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        What a second machine adds
      </p>

      <div className="mt-4 min-h-[104px] max-w-2xl">
        <AnimatePresence mode="wait">
          <motion.div
            key={slide.title}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.4, ease: EASE }}
          >
            <h3 className="text-base font-semibold text-foreground">{slide.title}</h3>
            <p className="mt-1.5 text-[13px] leading-relaxed text-muted-foreground">{slide.body}</p>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-4">
        <a
          href={WEBSITE}
          target="_blank"
          rel="noreferrer"
          className="inline-flex min-h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-semibold text-accent-foreground transition-colors hover:bg-accent/90"
        >
          neti.security
          <ArrowUpRight className="h-4 w-4" />
        </a>

        {/* Position, not navigation: four dots you cannot press are honest about being a rotation
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
          Everything on this page stays free. The rule is whether one machine can do it.
        </p>
      </div>
    </div>
  );
}
