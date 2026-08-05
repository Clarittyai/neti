"""Write the runtime-conformance table into README.md from the run that produced it.

The table is generated for the same reason the images are: a hand-maintained compatibility matrix
is a claim nobody diffs. It says "works with CrewAI" long after the version that was true of, and
the one thing a reader wants — *which version, and what exactly was run* — is the part that rots
first.

So the source is `eval/results/conformance.json`, written by `tests/conformance/` when it runs. The
version in each row is the version that was actually installed at the time, read from package
metadata rather than typed. `tests/property/test_media_is_current.py` fails the build when the block
in README.md and the recorded run disagree, so the table cannot drift from the evidence.

    just matrix           # rewrite the block
    just matrix --check   # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results" / "conformance.json"
README = REPO / "README.md"

BEGIN = "<!-- BEGIN CONFORMANCE -->"
END = "<!-- END CONFORMANCE -->"

MARK = {
    "passed": "driven",
    "failed": "**FAILED**",
    "skipped": "not installed here",
    "not_run": "not run here",
}

DEPTH = {
    "agent_loop": "full agent loop",
    "executor": "tool executor only",
}


def table() -> str:
    """The rows, exactly as the run recorded them."""
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = data["runtimes"]

    lines = [
        BEGIN,
        "",
        "| runtime | version | what was driven | depth | |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(rows):
        row = rows[name]
        version = row.get("version") or "—"
        depth = DEPTH.get(str(row.get("depth", "")), str(row.get("depth", "")))
        status = MARK.get(str(row.get("status", "not_run")), str(row.get("status")))
        lines.append(f"| `{name}` | {version} | {row.get('what', '')} | {depth} | {status} |")
    lines.extend(["", END])
    return "\n".join(lines)


def expected() -> str:
    text = README.read_text(encoding="utf-8")
    start, finish = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + table() + text[finish:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 if stale")
    args = parser.parse_args()

    if not RESULTS.exists():
        print(
            f"error: {RESULTS.relative_to(REPO)} is missing. Run `just conformance` first.",
            file=sys.stderr,
        )
        return 2

    want = expected()
    if README.read_text(encoding="utf-8") == want:
        if args.check:
            print("the conformance table is current")
        return 0
    if args.check:
        print(
            "README.md's conformance table no longer matches the recorded run.\n"
            "Run `just conformance` then `just matrix`, and commit both.",
            file=sys.stderr,
        )
        return 1

    README.write_text(want, encoding="utf-8")
    print(f"wrote the conformance table into {README.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
