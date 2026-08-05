# neti — design rules

The design language is [Claritty's](https://github.com/) — same tokens, same neutrals, same
structural instincts — with one primary of neti's own. This file exists because that was true once
and then stopped being true: the console re-skinned itself to violet while the website kept
Claritty's indigo, the console filled up with cards the website never had, and every "nothing here
yet" moment got hand-rolled a different way. None of that was disagreement. There was simply nothing
written down to disagree with.

So these are rules, not preferences, and the ones that can be machine-checked are checked by
`tests/property/test_design_rules_hold.py`. A rule nobody enforces is a comment.

---

## Sources of truth

| what | where |
|---|---|
| tokens | `web/src/app/globals.css` — the console's `:root` / `.dark` blocks |
| the same tokens, standalone | `site/page.html` — the landing page has no build step |
| empty states | `web/src/components/ui/empty-state.tsx` |
| live scenes | `web/src/components/live/` |

If a primitive exists, use it. If a token exists, use it. Adding a second way to do something that
already has a way is how this file came to be needed.

**Copied from Claritty, not imported.** neti is a standalone repository and a build-time dependency
on the Claritty monorepo would undo that. The cost is that the two can drift; the tests are what
catch it.

---

## Colour

**The primary is `#3B82F6`.** It is Claritty's `--info` and its dark-mode `--primary` — a real
Claritty colour, deliberately *not* Claritty's brand accent `#5B7FFF`, which keeps meaning Claritty.

The neutrals are Claritty's, unchanged: `#0F0F10` background, `#1A1A1C` surface, `#27272A` border,
`#F8FAFC` foreground, `#94A3B8` muted, and `--surface-deep` `#0d1117` for code, which is identical in
light and dark on purpose so a code block never changes under you.

**The verdict scale is reserved.** Three colours, each meaning exactly one thing, everywhere:

| token | colour | means, and only ever means |
|---|---|---|
| `--verdict-block` | `#EF4444` | a blocked call |
| `--verdict-confirm` | `#F59E0B` | a declared ceiling, or a call waiting on a human |
| `--verdict-allow` | `#10B981` | a call that proceeded, or a human grant |

Nothing else may use those hues. Not a chart series, not a hover state, not a logo. A reader who has
learned that red means blocked must never be shown a red that means something else.

**Identity is never carried by colour alone.** Every verdict ships with an icon and a label, so the
scale survives colour blindness, a monochrome print and a screenshot in a bug report.

**Measured, not asserted.** The primary clears a **ΔE2000 floor of 15** against all three reserved
verdicts — measured minimum **45.4**, against `#EF4444`. `test_design_rules_hold.py` recomputes this,
so the claim fails the build rather than aging in a comment. It replaces a header comment that
claimed a validator had been run against a validator that was never committed.

**Tokens only, never a hex.** `bg-accent`, `text-muted-foreground`, `border-border`. A hardcoded
`#3B82F6` in a component is a light-mode-only bug waiting to happen, and it is the exact mistake that
let the two surfaces drift apart.

---

## Structure — do not default to cards

**A number does not need a box.** Structure comes from hairline rules, spacing and alignment. The
console's overview was three bordered rounded rectangles stacked on top of a bordered table, which is
four boxes to say three numbers.

A border earns its place only when the thing inside is genuinely detachable from the page — a
popover, a dialog, a menu. Everything else is a section: a heading, some space, a `border-t` if two
things need separating.

Also out:

- **No shadows.** Not on buttons, not on panels. Depth is not this product's idea.
- **No hover motion.** No `hover:scale`, no `hover:-translate`. A background or border transition is
  enough, and tap-scale is fine on touch.
- **No fake window chrome.** No pretend traffic lights, no simulated title bars around a terminal.
- **No card grids.** A grid of equal boxes is what a page looks like when nobody decided what
  matters most.
- **One accent action per view.** If an empty state carries the CTA, the page header hides its own.

---

## Empty states

Every "there is nothing here yet" moment renders through **`EmptyState`**. Never hand-roll one.

- **`page`** — a first-run moment. Full-bleed, centred, usually with a `scene`.
- **`section`** — a block inside a populated page. Icon, not scene.
- **`inline`** — one quiet line. No icon, no CTA.

**No border, no card, no dashed box.** An empty state is open space with something living in it, not
a plate. The `/gate` page shipped a dashed rectangle for months; that is the anti-pattern.

**`scene` is reserved for first-run surfaces** — the overview, the gate, the audit trail, connect.
Everywhere else takes a lucide `icon` on the canonical plate (`rounded-2xl bg-accent/10 text-accent`).
Never both.

**No false CTA.** If the user can do something, say so and offer it. If there is genuinely nothing to
do, leave it actionless — an invented button is worse than none.

**Say what is true.** The voice is the product's: state the fact and the consequence, and name the
next command. The model is `/gate`'s own line —

> Nothing resolves until a provider is connected — the gate refuses to guess rather than inventing a
> number.

No "Oops". No "Nothing to see here". No exclamation marks.

---

## Motion

Live scenes follow the kernel in `web/src/components/live/kernel.tsx`, and its three rules:

1. **Motion is gated.** A loop runs only when the user has not asked for reduced motion, the scene is
   on screen, and the tab is visible. A CSS `prefers-reduced-motion` rule cannot stop a JS timer.
2. **Reduced motion still renders a composed final frame** — never a blank box. A scene that
   disappears for the people who most need it to hold still is not accessible, it is absent.
3. **Timers only.** No `requestAnimationFrame`, no canvas, no network assets.

**One idea on one axis.** A scene that needs squinting at is worse than no scene, and a label that
truncates defeats the whole thing. Keep labels short enough that they never wrap.

Scenes are `aria-hidden` and authored inside `<Stage>`, which scales the composition down on a narrow
screen instead of clipping it.

---

## Pre-flight, before shipping any UI

1. Both themes. Not "it looks fine in dark".
2. 375px wide. `Stage` exists because of this.
3. Reduced motion on — every scene still says something.
4. Keyboard: every interactive thing reachable, with a visible focus ring.
5. No new hex, no new card, no new empty state that is not `EmptyState`.
6. If you changed `web/`, **rebuild the console**: its build output is committed at
   `src/neti/console/`, and a token change nobody rebuilt is a change nobody ships.

---

## Anti-patterns that have actually shipped here

| ❌ what happened | ✅ what should have |
|---|---|
| The console re-skinned to violet `#8B5CF6` while `site/page.html` kept `#5B7FFF`, so neti had two identities and one of them was Claritty's | One `--accent`, `#3B82F6`, asserted equal in both files by a test |
| `globals.css` claimed the accent "was CHOSEN BY RUNNING THE PALETTE VALIDATOR" — no validator existed anywhere in the repo, and its cited number could not be reproduced | The measurement lives in a test that recomputes it |
| `/gate`'s empty state was a dashed rounded rectangle | `EmptyState`, open space, a live scene |
| The overview was three bordered boxes stacked on a bordered table | Sections separated by rules |
| `Empty` in `Page.tsx`, a dashed box in `gate/page.tsx`, and nothing on seven other pages | One `EmptyState` primitive |

*Keep this current.* When a design decision gets made, or a mistake gets caught twice, it goes in
the table — that is the part of this file that actually changes behaviour.
