"use client";

/**
 * Live-scene kernel — the shared motion vocabulary behind every animated empty state.
 *
 * Ported from clarity-platform's `components/live/kernel.tsx`. Copied rather than imported for the
 * same reason the design tokens are: neti is a standalone repository and a build-time dependency on
 * the monorepo would undo that. See DESIGN.md.
 *
 * Three rules, and they are the whole reason this exists rather than each scene rolling its own:
 *
 *   1. Motion is GATED — a loop runs only when the user has not asked for reduced motion, the scene
 *      is on screen, and the tab is visible. A CSS `prefers-reduced-motion` rule cannot stop a JS
 *      timer, so it is checked here.
 *   2. Reduced motion still renders a COMPOSED FINAL FRAME, never a blank box. A scene that
 *      vanishes for the people who most need it to hold still is not accessible, it is absent.
 *   3. No requestAnimationFrame, no canvas, no network assets — timers driving React state, so a
 *      scene is safe in a cold or offline launch.
 */
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

/** The house entry curve. */
export const EASE = [0.25, 0.46, 0.45, 0.94] as const;

/**
 * True only while the scene should actually be animating.
 *
 * False when the user prefers reduced motion, when the scene is scrolled out of view, or when the
 * tab is hidden — so a looping empty state never burns CPU in a background tab.
 */
export function useLiveGate(ref: React.RefObject<HTMLElement | null>): boolean {
  const reduce = useReducedMotion() ?? false;
  const [inView, setInView] = useState(false);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => setInView(entry?.isIntersecting ?? false),
      { threshold: 0.25 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  useEffect(() => {
    const onChange = () => setHidden(document.hidden);
    onChange();
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  return !reduce && inView && !hidden;
}

/** A looping index, advancing every `ms` while `live`. Frozen where it is when not. */
export function useTurn(length: number, live: boolean, ms: number): number {
  const [turn, setTurn] = useState(0);

  useEffect(() => {
    if (!live || length <= 0) return;
    const id = window.setInterval(() => setTurn((t) => (t + 1) % length), ms);
    return () => window.clearInterval(id);
  }, [live, length, ms]);

  return turn;
}

/** The measured width of an element, for `Stage` to scale against. */
export function useMeasuredWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setWidth(entry?.contentRect.width ?? 0);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  return width;
}

/** A stable ref for a scene root, so every scene's boilerplate is one line. */
export function useSceneRoot() {
  const ref = useRef<HTMLDivElement>(null);
  return { ref, live: useLiveGate(ref) };
}
