# Changelog

## Unreleased

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

## 0.1.0 — first release

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
