"""Install neti the way a stranger does, walk the whole flow, and check every step.

    just e2e            against the published package on PyPI
    just e2e --local    against this working tree

Every other test in this repository runs from a source checkout, where the repository root is two
directories up and every example, fixture and document is simply *there*. That is not the layout a
customer has, and the difference has already cost this project four defects nobody could see from
the inside: `neti demo --here` failing on the README's opening command, `neti prove` answering "no
policy" bare, `neti init` pointing at a directory that does not exist on an install, and seven
commands answering a raw errno on a first run.

So this builds a virtualenv, installs the package into it, creates a file tree with a **known**
number of files, and drives the documented journey end to end — asserting the numbers rather than
printing them. It is the difference between "it worked when I tried it" and "it works".

The tree is generated rather than borrowed. Pointing at a real repository makes the assertions
machine-dependent, and an e2e check whose expected value changes when somebody runs `npm install`
is one people learn to ignore.

Needs the network for the install and nothing else.

**`--local` runs in CI now**, as the `installed` job. It was marked "never in CI — CI already runs
the suite on three platforms", which was true and beside the point: the suite on three platforms
cannot see this class of defect at all, because it runs from a checkout. What the exemption bought
was a check that only ran when somebody remembered, and its `neti prove` step sat failing, unseen,
through a release. A check nobody runs decays into a claim.

The PyPI form stays manual — `just e2e`, after a release — because that one tests an artifact that
does not exist until it is published.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FILES = 250
"""How many files the fixture tree holds. Every magnitude below is derived from this, so the
assertions stay exact on any machine."""

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


@dataclass
class Result:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def ok(self, step: str, detail: str = "") -> None:
        self.passed.append(step)
        print(f"  {GREEN}✓{OFF} {step}{DIM}{'  ' + detail if detail else ''}{OFF}")

    def bad(self, step: str, why: str) -> None:
        self.failed.append((step, why))
        print(f"  {RED}✗{OFF} {step}\n      {RED}{why}{OFF}")

    def check(self, step: str, condition: bool, detail: str = "", why: str = "") -> bool:
        if condition:
            self.ok(step, detail)
        else:
            self.bad(step, why or detail or "assertion failed")
        return condition


def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=300, **kwargs)  # type: ignore[arg-type]


def build_tree(root: Path) -> int:
    """A tree with a known file count, so every number below can be asserted exactly."""
    (root / "src" / "deep").mkdir(parents=True)
    (root / "docs").mkdir()
    for i in range(FILES - 50):
        (root / "src" / f"m{i}.ts").write_text(f"export const x{i} = {i};\n", encoding="utf-8")
    for i in range(30):
        (root / "src" / "deep" / f"d{i}.ts").write_text("//\n", encoding="utf-8")
    for i in range(20):
        (root / "docs" / f"p{i}.md").write_text("# doc\n", encoding="utf-8")
    return sum(1 for p in root.rglob("*") if p.is_file())


POLICY = """\
version: 1
mode: observe
unknown_tool: allow

providers:
  fs:
    root: {root}

tools:
  Glob:
    gate:
      /pattern:
        resolver: fs.paths
        bands:
          - {{ above: {confirm}, verdict: confirm }}
          - {{ above: {block}, verdict: block }}
        on_unresolved: allow
  Read:
    gate:
      /file_path:
        resolver: fs.paths
        bands: []
        on_unresolved: allow
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true", help="test this working tree, not PyPI")
    parser.add_argument("--keep", action="store_true", help="do not delete the temp directory")
    args = parser.parse_args()

    r = Result()
    source = str(REPO) if args.local else "neti"
    print(f"\n{BOLD}neti — out-of-the-box check{OFF}")
    print(f"{DIM}installing {'this working tree' if args.local else 'neti[all] from PyPI'}{OFF}\n")

    work = Path(tempfile.mkdtemp(prefix="neti-e2e-"))
    try:
        return journey(r, work, source, args.local)
    finally:
        if args.keep:
            print(f"\n{DIM}kept {work}{OFF}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def hook_call(neti: Path, config: Path, project: Path, tool: str, args: dict[str, str]) -> str:
    """One tool call through the real `PreToolUse` hook, as a subprocess.

    A subprocess, not an import, because that is what Claude Code runs — and because the two have
    already answered differently: the session tally lived in memory on the `Engine`, which is
    correct in-process and empty on every call through the hook, so a declared session budget could
    not fire on the integration most people use.

    Returns `allow`, `confirm` or `block`. The hook prints nothing when a call proceeds, and that
    silence is the answer for `allow` and for `flag` alike — a flag proceeds. Anything needing the
    difference has to read the record.
    """
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "verify-install",
        "tool_name": tool,
        "tool_input": args,
    }
    out = subprocess.run(
        [str(neti), "hook", "-c", str(config)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=120,
        cwd=project,
    )
    if out.returncode != 0:
        return f"hook exited {out.returncode}: {out.stderr.strip()[-120:]}"
    if not out.stdout.strip():
        return "allow"
    decision = json.loads(out.stdout).get("hookSpecificOutput", {}).get("permissionDecision")
    return {"allow": "allow", "ask": "confirm", "deny": "block"}.get(str(decision), str(decision))


def day_zero(r: Result, neti: Path, work: Path) -> None:
    """The path every new install actually takes: `neti start`, then real tool calls.

    The journey above drives `neti init` and a hand-written policy, which is the *tuned* path — the
    one somebody reaches after a week. Nothing here covered `neti start`, and nothing anywhere drove
    a single tool call through the installed hook. Every defect found in the 0.3.0 release was in
    that gap, including a credential read that `Read` stopped and `Bash` allowed.
    """
    print(f"\n{BOLD}Day zero — `neti start`, then real tool calls through the hook{OFF}")
    project = work / "dayzero"
    (project / "src").mkdir(parents=True)
    for i in range(30):
        (project / "src" / f"m{i}.ts").write_text("x", encoding="utf-8")
    (project / ".env").write_text("STRIPE_KEY=sk_live_notreal", encoding="utf-8")

    out = run([str(neti), "start"], cwd=project, stdin=subprocess.DEVNULL)
    config = project / "neti.yaml"
    if not r.check(
        "neti start writes a policy",
        config.exists() and out.returncode == 0,
        why=(out.stdout[-300:] + out.stderr[-300:]),
    ):
        return

    # What it protects has to be real, not merely present. A policy file that loads and declares
    # nothing is the state this product shipped in for two releases.
    text = config.read_text(encoding="utf-8")
    r.check(
        "it declares something on its own",
        "outside_root:" in text and "sensitive:" in text and "mode: enforce" in text,
        "outside_root, off-limits rules, enforcing",
        why="a fresh policy that protects nothing is the defect `neti start` exists to fix",
    )

    home = Path.home()
    cases: list[tuple[str, str, dict[str, str], str]] = [
        ("ordinary read is silent", "Read", {"file_path": "src/m1.ts"}, "allow"),
        ("ordinary edit is silent", "Edit", {"file_path": "src/m2.ts"}, "allow"),
        ("running the tests is silent", "Bash", {"command": "npm test"}, "allow"),
        ("git status is silent", "Bash", {"command": "git status"}, "allow"),
        ("the .env is off limits", "Read", {"file_path": ".env"}, "confirm"),
        (
            "the SSH key is out of reach",
            "Read",
            {"file_path": str(home / ".ssh" / "id_rsa")},
            "confirm",
        ),
        (
            "and out of reach through a shell",
            "Bash",
            {"command": f"cp {home}/.ssh/id_rsa /tmp/exfil"},
            "confirm",
        ),
        ("so is the .env, in a pipeline", "Bash", {"command": "cat .env | base64"}, "confirm"),
        ("traversal is still outside", "Read", {"file_path": "../../../etc/passwd"}, "confirm"),
    ]
    for label, tool, args, expected in cases:
        got = hook_call(neti, config, project, tool, args)
        r.check(label, got == expected, got, why=f"expected {expected}, got {got}")

    # The evidence, checked by the installed binary rather than by us.
    records = project / "out" / "decisions.ndjson"
    out = run(
        [str(neti), "verify", "-r", str(records), "-c", str(config), "--mode", "enforce"],
        cwd=project,
    )
    text = out.stdout + out.stderr
    r.check("the chain it wrote verifies", "chain intact" in text, why=text[-300:])
    r.check(
        "every verdict replays from its own record",
        "REPLAY DIFFERENTLY" not in text and "replay to the same verdict" in text,
        next((line.strip() for line in text.splitlines() if "replay to" in line), ""),
        why=text[-300:],
    )

    # Every door, against the policy `neti start` just wrote — which is the policy a new install
    # has, and which `neti prove` refused to run against until today.
    out = run([str(neti), "prove", "-r", str(project / "out" / "proof.ndjson")], cwd=project)
    opened = next((line.strip() for line in out.stdout.splitlines() if "opened here" in line), "")
    r.check(
        "neti prove — every door agrees",
        "agreeing on the verdict" in out.stdout and out.returncode == 0,
        opened.rstrip(","),
        why=(out.stdout + out.stderr)[-400:],
    )
    r.check(
        "and it says why this call was stopped",
        "stopped because" in out.stdout,
        why="the call is chosen from the policy now, so the reason is not guessable",
    )

    # And the question a silent control cannot otherwise answer about itself.
    out = run([str(neti), "status"], cwd=project)
    r.check(
        "neti status sees the traffic",
        "decision(s)" in out.stdout and "nothing yet" not in out.stdout,
        why=out.stdout[-300:],
    )
    r.check(
        "and says the hook is not wired, because it is not",
        out.returncode == 1 and "neti install" in out.stdout,
        "start was not run on a tty, so nothing was installed",
        why=out.stdout[-300:],
    )


def journey(r: Result, work: Path, source: str, local: bool) -> int:
    venv = work / "venv"
    proj = work / "proj"
    tree = work / "tree"
    proj.mkdir()
    tree.mkdir()

    # ---------------------------------------------------------------- install
    started = time.monotonic()
    run([sys.executable, "-m", "venv", str(venv)])
    # `Scripts` on Windows, `bin` everywhere else. Hard-coding `bin` is why this only ever ran on
    # two of the three platforms CI already tests the suite on — and the bug fixed today, `/tmp`
    # against `tempfile.gettempdir()`, existed on exactly one of them.
    binaries = venv / ("Scripts" if os.name == "nt" else "bin")
    pip = binaries / "pip"
    neti = binaries / "neti"
    # The base install first, and this order is the point rather than a detail.
    #
    # This file walked `neti[all]` and nothing else, so every command below ran with every extra
    # present — while README §1 opens with `pip install neti`, no extras, and says in as many words
    # "No extras, no quoting". The two things this tool exists to reconcile were never compared.
    #
    # What that hid: `neti demo --here`, the command the README tells a new reader to run first,
    # died on a base install with `ModuleNotFoundError: No module named 'httpx'` — an import sitting
    # one line above the branch that returns before using it. `neti demo --here runs` is checked
    # forty lines below and passed the whole time, because httpx was always there.
    #
    # So: install what the README says to install, prove the commands the README claims work on it,
    # then add the extras and carry on. A stranger's first command is the one worth checking twice.
    out = run([str(pip), "install", "-q", str(source)])
    if not r.check(
        "pip install neti  (base, as README §1 says)",
        out.returncode == 0,
        f"{time.monotonic() - started:.0f}s",
        why=out.stderr.strip()[-300:],
    ):
        return 1

    base = run([str(neti), "demo", "--here"])
    r.check(
        "neti demo --here runs on a *base* install",
        base.returncode == 0,
        why=(base.stdout[-200:] + base.stderr[-500:]).strip(),
    )

    # `neti[all]` from PyPI, or `/path/to/repo[all]` for a working tree. pip takes the same shape
    # for both, which is the whole reason this can test either without a second code path.
    out = run([str(pip), "install", "-q", f"{source}[all]"])
    if not r.check(
        "pip install neti[all]",
        out.returncode == 0,
        f"{time.monotonic() - started:.0f}s",
        why=out.stderr.strip()[-300:],
    ):
        return 1

    r.check("the `neti` command exists", neti.exists(), why="no entry point was installed")
    version = run([str(neti), "version"]).stdout.strip()
    r.check("neti version", bool(version), version)

    probe = run([str(binaries / "python"), "-c", "import neti; print(neti.__file__)"])
    r.check("import neti", probe.returncode == 0, why=probe.stderr.strip()[-200:])

    pkg = Path(probe.stdout.strip()).parent
    examples = sorted(p.name for p in (pkg / "examples").glob("*.yaml"))
    r.check(
        "example policies ship",
        len(examples) >= 2,
        ", ".join(examples),
        why="the wheel carries no examples, so `neti demo --here` cannot run",
    )
    r.check(
        "the console ships",
        (pkg / "console" / "index.html").exists(),
        why="no built console — `neti console` will refuse (expected on a source install)",
    )

    # ---------------------------------------------------------------- act 1: measure this machine
    count = build_tree(tree)
    out = run([str(neti), "demo", "--here", "--repo", str(tree)])
    r.check("neti demo --here runs", out.returncode == 0, why=out.stdout[-400:] + out.stderr[-400:])
    r.check(
        "it counts the tree exactly",
        f"{count:,}" in out.stdout,
        f"{count:,} files, and `find` agrees",
        why=f"expected {count:,} somewhere in the output",
    )

    # ---------------------------------------------------------------- a policy, from the package
    out = run([str(neti), "init", "--example", "coding-agent"], cwd=proj)
    r.check("neti init --example", (proj / "neti.yaml").exists(), why=out.stderr.strip()[-200:])

    block_at = count // 2
    (proj / "neti.yaml").write_text(
        POLICY.format(root=tree, confirm=block_at // 2, block=block_at), encoding="utf-8"
    )

    out = run([str(neti), "inventory"], cwd=proj)
    r.check(
        "neti inventory",
        f"{count:,}" in out.stdout,
        f"reachable maximum is {count:,}",
        why=out.stdout[-300:],
    )

    # ---------------------------------------------------------------- observe, then enforce
    def hook(tool: str, param: str, value: str) -> subprocess.CompletedProcess[str]:
        return run(
            [str(neti), "hook", "-c", "neti.yaml", "-r", "out/decisions.ndjson"],
            cwd=proj,
            input=json.dumps({"tool_name": tool, "tool_input": {param: value}}),
        )

    for i in range(4):
        hook("Read", "file_path", str(tree / "src" / f"m{i}.ts"))
    hook("Glob", "pattern", f"{tree}/docs/*.md")
    records = proj / "out" / "decisions.ndjson"
    r.check(
        "the hook records every call",
        records.exists() and len(records.read_text(encoding="utf-8").splitlines()) == 5,
        "5 decisions",
        why="records were not written",
    )

    out = run([str(neti), "report"], cwd=proj)
    r.check("neti report", "decisions" in out.stdout, why=out.stdout[-300:])

    (proj / "neti.yaml").write_text(
        (proj / "neti.yaml").read_text(encoding="utf-8").replace("mode: observe", "mode: enforce"),
        encoding="utf-8",
    )
    out = hook("Glob", "pattern", f"{tree}/**/*")
    try:
        decision = json.loads(out.stdout)["hookSpecificOutput"]
    except Exception:
        decision = {}
    r.check(
        "an oversized call is denied",
        decision.get("permissionDecision") == "deny",
        f"ceiling {block_at:,}",
        why=f"expected deny, got {decision.get('permissionDecision')!r}",
    )
    r.check(
        "the denial carries the number",
        f"{block_at:,}" in decision.get("permissionDecisionReason", ""),
        decision.get("permissionDecisionReason", "")[:78],
        why="the agent is told it was blocked but not how big the call was",
    )

    small = hook("Read", "file_path", str(tree / "src" / "m0.ts"))
    r.check(
        "a call that fits stays silent",
        small.stdout.strip() == "",
        why=f"expected no output, got {small.stdout[:100]!r}",
    )

    # ---------------------------------------------------------------- the audit
    #
    # Two assertions per call, not one. A verifier that prints "CHAIN BROKEN" and exits 0 is
    # useless in the place this is meant to run — a cron job, a CI step, a pre-deploy check —
    # because every one of those reads the exit code and nothing else. The break is reported on
    # stderr, so both streams get searched; pinning it to the wrong stream is how a check like this
    # silently stops testing anything.
    out = run([str(neti), "verify"], cwd=proj)
    r.check(
        "neti verify — chain intact",
        "chain intact" in out.stdout + out.stderr and out.returncode == 0,
        why=f"rc={out.returncode} {(out.stdout + out.stderr)[-200:]}",
    )

    lines = records.read_text(encoding="utf-8").splitlines()
    original = list(lines)
    doctored = json.loads(lines[1])
    doctored["verdict"] = "block" if doctored["verdict"] != "block" else "allow"
    lines[1] = json.dumps(doctored)
    records.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = run([str(neti), "verify"], cwd=proj)
    said = out.stdout + out.stderr
    r.check(
        "a tampered record breaks the chain",
        "CHAIN BROKEN" in said,
        said.strip().splitlines()[0][:78] if said.strip() else "",
        why="the chain did not notice an edited verdict — the audit claim does not hold",
    )
    r.check(
        "and says so with a non-zero exit",
        out.returncode != 0,
        f"exit {out.returncode}",
        why=f"exit {out.returncode} — cron and CI read the status, not the words",
    )

    records.write_text("\n".join(original) + "\n", encoding="utf-8")
    out = run([str(neti), "verify"], cwd=proj)
    r.check(
        "restoring it repairs the chain",
        "chain intact" in out.stdout + out.stderr and out.returncode == 0,
        why=f"rc={out.returncode} {(out.stdout + out.stderr)[-200:]}",
    )

    # `neti prove` moved to the day-zero act below, where the policy it needs actually exists.
    # This tuned policy declares ceilings and nothing else, so there is no call it *stops* by
    # identity or location — and every seam driver asserts the call did not reach the tool, so a
    # call that merely flags proves nothing about agreement. This check sat here failing for
    # exactly that reason, unseen, because `just e2e` never runs in CI.

    # ---------------------------------------------------------------- the console
    if (pkg / "console" / "index.html").exists():
        server = subprocess.Popen(
            [str(neti), "console", "--no-open", "--port", "8899"],
            cwd=proj,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            served = {}
            for _ in range(20):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8899/", timeout=2) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, OSError):
                    continue
            for route in ("", "decisions", "audit", "scorecard", "api/decisions"):
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:8899/{route}"
                        + ("/" if route and "api" not in route else ""),
                        timeout=5,
                    ) as response:
                        served[route or "/"] = response.status
                except (urllib.error.URLError, OSError) as exc:
                    served[route or "/"] = str(exc)
            r.check(
                "the console serves every screen",
                all(v == 200 for v in served.values()),
                f"{len(served)} routes",
                why=str(served),
            )
        finally:
            server.terminate()
            server.wait(timeout=10)

    out = run([str(neti), "score"], cwd=proj)
    r.check("neti score", "scorecard" in out.stdout.lower(), why=out.stdout[-200:])

    day_zero(r, neti, work)

    # ---------------------------------------------------------------- verdict
    print()
    if r.failed:
        print(f"{RED}{BOLD}{len(r.failed)} of {len(r.passed) + len(r.failed)} checks failed{OFF}")
        for step, why in r.failed:
            print(f"  {RED}✗{OFF} {step}: {why[:160]}")
        return 1

    print(f"{GREEN}{BOLD}all {len(r.passed)} checks passed{OFF}")
    print(
        f"{DIM}installed from {'this tree' if local else 'PyPI'}, measured a {count}-file tree, "
        f"blocked a call over a {block_at:,} ceiling,\nsealed and re-verified the chain, caught a "
        f"tampered record, served the console, and — from `neti start` alone —\nkept ordinary work "
        f"silent while stopping the .env, the SSH key, and the shell that reached for it.{OFF}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
