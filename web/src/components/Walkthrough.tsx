"use client";

/**
 * The first-run walkthrough — what this is, and how to make it work with what you actually run.
 *
 * The console used to open on a large number nobody asked for, two zeros and a warning about
 * undeclared ceilings. All true, and none of it answers the only two questions somebody has on
 * their first run. The answer existed, in `docs/TUTORIAL.md`, which is not in the product.
 *
 * Three things make this a *live* tutorial rather than instructions with a tick box:
 *
 * 1. **It reads their machine.** The paths are their paths, the servers are their servers, and the
 *    command under "put the gate in front of your agent" is the one that works here — including
 *    `--user` versus project scope, and the MCP wrap form for each server actually configured.
 *    `insight/onboarding.py` does that reading; nothing is hardcoded on this side.
 * 2. **It ticks itself while you watch.** Polling, not a refresh button. Run `neti install` in
 *    another window and the step completes under the cursor a few seconds later — which is also
 *    the proof that the check is real rather than a flag somebody set.
 * 3. **It is never done-and-gone.** State is derived every time it is asked, so uninstalling the
 *    hook un-ticks step three. A checklist that lies about your machine is worse than none.
 *
 * Structure per DESIGN.md: a rail, not cards. The numbers are legitimate here — this genuinely is
 * a sequence, and the order carries information (you cannot band a ceiling you have not measured).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Check, Plug } from "lucide-react";

import { CommandLine } from "@/components/CommandLine";
import { api, type Harness, type StartState, type StartStep } from "@/lib/api";
import { cn } from "@/lib/utils";

/** How often the walkthrough re-reads the machine. Fast enough that running a command in another
 *  window completes a step while you are still looking at it; slow enough to be free. */
const POLL_MS = 3000;

export function Walkthrough({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<StartState | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () => {
      api
        .start()
        .then((d) => live && setData(d))
        .catch(() => {
          /* A poll that fails leaves the last good state on screen. A walkthrough that blanks
             itself because one request timed out has lost the reader's place for no reason. */
        });
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  if (!data) return null;

  const current = data.steps.find((s) => !s.done) ?? null;
  const doneCount = data.steps.filter((s) => s.done).length;

  if (compact) return <Rail data={data} doneCount={doneCount} />;

  return (
    <section aria-labelledby="walkthrough-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-accent">
            Getting started
          </p>
          <h2 id="walkthrough-heading" className="mt-1 text-2xl font-semibold tracking-tight">
            {data.complete
              ? "The gate is doing its whole job"
              : "neti sizes a tool call before it runs"}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {data.complete ? (
              <>
                Every call your agent makes is resolved against a declared ceiling and sealed into a
                chain you can re-verify. Nothing below is left to do.
              </>
            ) : (
              <>
                Authorization answers <em>may you</em>, sandboxing answers <em>where</em>, approval
                answers <em>did a human say yes</em>. None of them answers <strong>how big</strong>.
                Five steps, read live off this machine — they tick themselves as you go.
              </>
            )}
          </p>
        </div>
        <p className="tnum text-xs text-muted-foreground">
          {doneCount} of {data.steps.length} done
        </p>
      </div>

      <ol className="mt-6">
        {data.steps.map((step, i) => (
          <StepRow
            key={step.id}
            step={step}
            index={i + 1}
            current={current?.id === step.id}
            harnesses={step.id === "install" ? data.harnesses : []}
          />
        ))}
      </ol>
    </section>
  );
}

/** The one-line version, for a page that already has traffic on it. */
function Rail({ data, doneCount }: { data: StartState; doneCount: number }) {
  if (data.complete) return null;
  const next = data.steps.find((s) => !s.done);
  return (
    <Link
      href="/start"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border py-2.5 text-sm transition-colors hover:bg-foreground/[0.03]"
    >
      <span className="tnum text-xs text-muted-foreground">
        {doneCount}/{data.steps.length}
      </span>
      <span className="font-medium">Next: {next?.title}</span>
      <span className="truncate text-xs text-muted-foreground">{next?.detail}</span>
      <ArrowRight className="ml-auto h-4 w-4 text-muted-foreground" />
    </Link>
  );
}

function StepRow({
  step,
  index,
  current,
  harnesses,
}: {
  step: StartStep;
  index: number;
  current: boolean;
  harnesses: Harness[];
}) {
  return (
    <li className="flex gap-4 border-t border-border py-4">
      <Marker done={step.done} index={index} current={current} />
      <div className="min-w-0 flex-1">
        <h3
          className={cn(
            "text-sm font-semibold",
            step.done && !current ? "text-muted-foreground" : "text-foreground",
          )}
        >
          {step.title}
        </h3>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted-foreground">{step.detail}</p>

        {/* The *why* is only spent on the step you are actually on. Five paragraphs of rationale
            at once is a wall; one, next to the thing you are about to do, is an explanation. */}
        {current ? (
          <>
            <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
              {step.why}
            </p>
            {/* One command or the other, never both. When there are doors to list, each row
                carries the command for *that* door — a step-level copy of whichever one happened
                to sort first is the same string twice, two hundred pixels apart, and the reader
                has to work out whether they differ. */}
            {harnesses.length > 0 ? (
              <Harnesses rows={harnesses} />
            ) : step.command ? (
              <CommandLine text={step.command} />
            ) : null}
            {step.doc ? (
              <Link
                href={step.doc}
                className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-medium text-accent hover:underline"
              >
                Open {step.doc}
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            ) : null}
          </>
        ) : null}
      </div>
    </li>
  );
}

function Marker({ done, index, current }: { done: boolean; index: number; current: boolean }) {
  const still = useReducedMotion();
  return (
    <div className="pt-0.5">
      <motion.span
        // The tick animates when a step completes under the cursor — the one moment in this
        // component where motion carries information rather than decorating it.
        key={done ? "done" : "todo"}
        initial={still || !done ? false : { scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.2 }}
        className={cn(
          "tnum flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold ring-1 ring-inset",
          done
            ? "bg-accent/10 text-accent ring-accent/30"
            : current
              ? "bg-foreground/[0.06] text-foreground ring-border"
              : "text-muted-foreground ring-border",
        )}
      >
        {done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : index}
      </motion.span>
    </div>
  );
}


/** Every door on this machine, named. This is the "based on what you use" half — a generic snippet
 *  is what makes somebody close the tab and go back to the README. */
function Harnesses({ rows }: { rows: Harness[] }) {
  return (
    <div className="mt-4">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        <Plug className="h-3 w-3" />
        Found on this machine
      </p>
      <ul className="mt-2">
        {rows.map((h) => (
          <li key={`${h.kind}:${h.label}:${h.where}`} className="border-t border-border py-2.5">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="text-[13px] font-medium">{h.label}</span>
              <span className="rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-inset ring-border">
                {h.kind === "hook" ? "built-in tools" : "MCP server"}
              </span>
              {h.gated ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-accent">
                  <Check className="h-3 w-3" strokeWidth={3} />
                  gated
                </span>
              ) : null}
            </div>
            {/* `break-all`, because this is an absolute path and a path has no spaces in it. On a
                375px screen a `<p>` with nothing to break on does not wrap, it overflows — and the
                whole page scrolls sideways to accommodate one filename. */}
            <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
              {h.where}
            </p>
            <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{h.detail}</p>
            {h.command ? <CommandLine text={h.command} /> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
