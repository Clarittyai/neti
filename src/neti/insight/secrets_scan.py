"""Which sensitive-target rules are worth declaring *here*.

`sensitive:` closes the half of NC-02 that magnitude never reaches — `.env` is one object and under
every ceiling anybody would write. It shipped commented out in the example policy and mentioned in a
changelog, which is the same as not shipping it: a capability nobody can find is a capability nobody
has. This repository has caught itself doing that four times.

So `neti propose`, which is already the command that answers *what should I declare*, also answers
it for this axis. The rule it follows is the one `insight/targets.py` follows:

**Real, or absent.** Every rule proposed here matches something that exists on this machine, right
now. Offering `**/*.pem` to a repository with no certificate in it is offering a rule that can never
fire — dead config that reads as configured, which is the failure this project keeps finding.

**Never applied.** A fragment to read and paste, like every ceiling. `config/policy.py` opens by
saying nothing computed becomes a ceiling on its own, and a `verdict: block` on somebody's
credentials is a stronger claim than a number, not a weaker one.

The list below is deliberately short and boring. It is not a secret scanner and does not read file
*contents* — that is a different product with different failure modes, and one that reads your files
to tell you about them is a harder thing to trust than one that looks at their names.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["KNOWN", "Candidate", "scan"]


@dataclass(frozen=True)
class Known:
    match: str
    verdict: str
    why: str
    names: tuple[str, ...] = ()
    """Bare filenames that count as a hit. Cheaper than globbing and easier to read."""

    dirs: tuple[str, ...] = ()


KNOWN: tuple[Known, ...] = (
    Known(
        "**/.env*",
        "confirm",
        "credentials live here",
        names=(".env", ".env.local", ".env.production", ".env.development"),
    ),
    Known("**/*.pem", "confirm", "private keys"),
    Known("**/*.key", "confirm", "private keys"),
    Known("**/.ssh/**", "block", "nothing here is an agent's business", dirs=(".ssh",)),
    Known("**/.aws/**", "block", "cloud credentials", dirs=(".aws",)),
    Known(
        "**/.git/**",
        "confirm",
        "rewriting history is not reversible",
        dirs=(".git",),
    ),
    Known("**/secrets/**", "block", "named for what it holds", dirs=("secrets",)),
    Known("**/id_rsa*", "block", "an SSH private key", names=("id_rsa", "id_ed25519")),
)


@dataclass(frozen=True)
class Candidate:
    match: str
    verdict: str
    why: str
    example: str
    """The thing found here that makes this rule worth declaring. Printed, because a rule with a
    real example beside it is one somebody accepts or rejects on evidence."""

    def as_yaml(self) -> str:
        return f'  - {{ match: "{self.match}", verdict: {self.verdict}, why: {self.why} }}'


def scan(root: str | Path, *, cap: int = 20_000) -> list[Candidate]:
    """Sensitive-target rules that would match something under `root`.

    One bounded walk, names only, no file contents read and nothing opened. `cap` exists for the
    same reason `fs.paths` has one — this runs on a developer's machine against a tree that might
    contain a `node_modules`, and a proposal command that hangs is a proposal command nobody runs.
    """
    base = Path(root)
    seen: dict[str, str] = {}
    looked = 0

    for current, dirnames, filenames in os.walk(base):
        # Descend into `.git` and `.ssh` far enough to know they are there, never through them.
        # Their contents are thousands of files and the rule is about the directory.
        here = Path(current)
        for rule in KNOWN:
            for name in rule.dirs:
                if name in dirnames and rule.match not in seen:
                    seen[rule.match] = str((here / name).relative_to(base))
        dirnames[:] = [
            d for d in dirnames if d not in {".git", ".ssh", ".aws", "node_modules", ".venv"}
        ]

        for filename in filenames:
            looked += 1
            for rule in KNOWN:
                if rule.match in seen:
                    continue
                hit = filename in rule.names or (
                    rule.match.startswith("**/*.")
                    and filename.endswith(rule.match.removeprefix("**/*"))
                )
                if hit:
                    seen[rule.match] = str((here / filename).relative_to(base))
        if looked > cap:
            break

    order = {rule.match: i for i, rule in enumerate(KNOWN)}
    return sorted(
        (
            Candidate(match=r.match, verdict=r.verdict, why=r.why, example=seen[r.match])
            for r in KNOWN
            if r.match in seen
        ),
        key=lambda c: order[c.match],
    )


def render(candidates: list[Candidate]) -> str:
    """The fragment, with what was found beside each rule."""
    if not candidates:
        return ""
    lines = [
        "Gated on WHAT, not how many. A ceiling cannot reach a single file, and each of these",
        "matches something that is really here:",
        "",
    ]
    width = max(len(c.match) for c in candidates)
    for c in candidates:
        lines.append(f"  {c.match:<{width}}  found: {c.example}")
    lines += ["", "# paste into your policy, above `tools:`", "sensitive:"]
    lines += [c.as_yaml() for c in candidates]
    return "\n".join(lines)
