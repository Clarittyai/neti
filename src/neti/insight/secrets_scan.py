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


FIXTURE_DIRS = frozenset(
    {"test", "tests", "testing", "fixtures", "fixture", "testdata", "__fixtures__", "spec"}
)
"""Directories whose contents are, by convention, committed sample data.

**A private key checked into a public repository is not a private key.** Run day zero on `psf/
requests` and the scan proposes `**/*.pem` and `**/*.key`, because it found seven of them — all
under `tests/certs/`, all published on GitHub, all there so the TLS suite has something to hand a
socket. The rule that follows interrupts an agent every time it opens the test suite it was asked
to work on, and protects nothing at all. Same for `flask`, whose only `.env` is
`tests/test_apps/.env`, a fixture that holds `SECRET_KEY=config`.

That is the noise that gets a control switched off, and no synthetic tree produces it — every
generated fixture in this repository's own suite has uniform, invented files. It took cloning four
real projects to see.

The limitation, stated rather than hidden: a genuine credential living under `tests/` is not
proposed. This is a scan that offers rules to a human, and its contract is *real, or absent* — a
rule whose every match is committed sample data is not real."""


def _fixture(relative: Path) -> bool:
    """Whether this path lies under a directory that holds test data by convention."""
    return any(part.lower() in FIXTURE_DIRS for part in relative.parts[:-1])


KNOWN: tuple[Known, ...] = (
    Known(
        "**/.env*",
        "confirm",
        "credentials live here",
        names=(".env", ".env.local", ".env.production", ".env.development"),
    ),
    Known("**/*.pem", "confirm", "private keys"),
    Known("**/*.key", "confirm", "private keys"),
    Known("**/*.p12", "confirm", "a certificate bundle, private key included"),
    Known("**/*.pfx", "confirm", "a certificate bundle, private key included"),
    Known("**/*.jks", "confirm", "a Java keystore"),
    Known(
        "**/.npmrc",
        "confirm",
        "registry auth tokens live here",
        names=(".npmrc",),
    ),
    Known("**/.pypirc", "confirm", "package index credentials", names=(".pypirc",)),
    Known("**/.netrc", "block", "machine credentials in plain text", names=(".netrc",)),
    Known("**/_netrc", "block", "machine credentials in plain text", names=("_netrc",)),
    Known("**/.ssh/**", "block", "nothing here is an agent's business", dirs=(".ssh",)),
    Known("**/.aws/**", "block", "cloud credentials", dirs=(".aws",)),
    Known("**/.kube/**", "block", "cluster credentials", dirs=(".kube",)),
    Known("**/.gnupg/**", "block", "private keyring", dirs=(".gnupg",)),
    Known("**/.docker/**", "confirm", "registry auth is kept here", dirs=(".docker",)),
    Known(
        "**/terraform.tfstate*",
        "confirm",
        "state holds every value the plan applied, secrets included",
        names=("terraform.tfstate", "terraform.tfstate.backup"),
    ),
    Known(
        "**/.git/**",
        "confirm",
        "rewriting history is not reversible",
        dirs=(".git",),
    ),
    Known("**/secrets/**", "block", "named for what it holds", dirs=("secrets",)),
    # One entry per key type, and not one `**/id_rsa*` rule listing all four names.
    #
    # It was the latter, and the rule could not match three of the files that justified it: an
    # `id_ed25519` outside `.ssh/` proposed `**/id_rsa*`, which does not match `id_ed25519`. That is
    # precisely the dead config this module exists to avoid — a rule offered *because* of a file it
    # can never fire on. `test_every_known_rule_matches_its_own_evidence` now makes the whole class
    # unrepresentable rather than fixing the one instance.
    Known("**/id_rsa*", "block", "an SSH private key", names=("id_rsa",)),
    Known("**/id_ed25519*", "block", "an SSH private key", names=("id_ed25519",)),
    Known("**/id_ecdsa*", "block", "an SSH private key", names=("id_ecdsa",)),
    Known("**/id_dsa*", "block", "an SSH private key", names=("id_dsa",)),
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
        """One rule, as a line somebody pastes.

        `why` is quoted because this is a YAML *flow* mapping: an unquoted comma inside it ends the
        value and turns the remainder into a key, so `why: a bundle, private key included` parsed as
        a rule with a stray field and the whole fragment stopped loading. Every `why` shipped before
        happened to contain no comma, which is not a property anybody was maintaining on purpose.
        """
        why = self.why.replace('"', "'")
        return f'  - {{ match: "{self.match}", verdict: {self.verdict}, why: "{why}" }}'


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
                if not hit:
                    continue
                where = (here / filename).relative_to(base)
                if not _fixture(where):
                    # A fixture never becomes the evidence for a rule, which also means the example
                    # printed beside a rule that *is* proposed is always the real file — the one
                    # that makes the case for it.
                    seen[rule.match] = str(where)
        if looked > cap:
            break

    # Rules whose only evidence was a fixture are absent, not downgraded. There is no weaker verdict
    # that would help: `flag` on somebody's test certificates is still a line in their log every
    # time they run the suite.
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
