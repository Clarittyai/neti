"""One policy and one set of resolvers per kind of thing an agent touches.

`tests/e2e/test_seam_equivalence.py` proves that every runtime reaches the same verdict about the
same call. It proved it only about Entra: every case in it used `examples/entra.yaml` and the
synthetic tenant, so four of the ten shipped resolvers — `fs.paths`, `db.rows`, `storage.objects`,
`terraform.destroy` — had never crossed a seam boundary at all. The tools a coding agent actually
calls all day were gated by a shipped example that no seam test exercised.

This module is the other axis. A `World` is a policy plus the resolvers that policy binds, and the
seam drivers take one instead of building `examples/entra.yaml` for themselves. Adding a resolver
family means adding a world, and every seam then gets driven against it for free.

**Everything here is offline and deterministic**, which is what lets it live in `tests/` rather than
in `eval/`. Exactly one resolver is stood in for:

- `fs` and `terraform` read real artefacts on disk through the shipped resolvers, unchanged.
- `db` reaches a real sqlite file through the shipped `EnvCountRunner` and `NETI_DATABASE_URL`, so
  the URL parsing and the read-only open are exercised rather than bypassed.
- `entra` uses the synthetic tenant, exactly as the seam table always did.
- `storage` is the sole substitution: the registry binds `S3Lister`, which needs boto3 and an AWS
  account, so it gets a lister with declared prefix sizes instead.

A separate module and not `conftest.py` on purpose. The seam drivers are what a reader comes to
`test_seam_equivalence.py` for, and burying them under sqlite seeding and plan JSON would hide the
thing the file exists to say.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from neti.config.policy import Policy
from neti.core.verdict import Mode
from neti.engine import Engine
from neti.eval.synthetic import default_tenant
from neti.resolvers.base import Resolver
from neti.resolvers.graph_client import ClientCredential, GraphClient
from neti.resolvers.registry import resolvers_for_client
from neti.resolvers.shell import ShellPathsResolver
from neti.resolvers.storage import Listing, ObjectStoreResolver

__all__ = [
    "RESOLVER_WORLDS",
    "SHAPE_WORLDS",
    "WORLDS",
    "Fixtures",
    "World",
    "build_fixtures",
    "build_world",
    "render",
]

CRED = ClientCredential(tenant_id="demo", client_id="demo", client_secret="demo")

RESOLVER_WORLDS = ("entra", "fs", "db", "storage", "terraform", "shell")
"""One per family of thing an agent touches. This is the axis `neti score` reports as M8 coverage.

`entra` is loaded from `examples/entra.yaml` rather than from `POLICIES` below, because the seam
table's five original rows are pinned against that file byte for byte.
"""

SHAPE_WORLDS = ("budget",)
"""Worlds that exist for a *shape* rather than a resolver — `budget` is one file, resolving to 1,
called twice. Kept separate so the coverage number above counts resolver families and not rows."""

WORLDS = (*RESOLVER_WORLDS, *SHAPE_WORLDS)


# ---------------------------------------------------------------------------- artefacts on disk


@dataclass(frozen=True)
class Fixtures:
    """The real files the local resolvers read.

    Built once and treated as read-only. The expanded seam table drives every world through every
    seam, and rebuilding a file tree per parametrised invocation would be thousands of writes for
    no added assurance.
    """

    root: Path
    tree: Path
    """30 `.txt` files, so a glob over it resolves to a number worth banding."""

    db: Path
    plan: Path
    """`terraform show -json` shaped: 7 destroys."""

    small_plan: Path
    """2 creates and no destroys — the allow case, since only destroy and replace are counted."""

    state: Path
    """Valid JSON with no `resource_changes`. What `terraform show -json` prints with no plan file,
    and the document the resolver must refuse rather than read as zero."""

    missing: Path


def build_fixtures(root: Path) -> Fixtures:
    tree = root / "tree"
    tree.mkdir(parents=True)
    for i in range(30):
        (tree / f"f{i}.txt").write_text("x" * 10, encoding="utf-8")

    db = root / "app.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("create table users (id integer primary key, org text)")
        connection.executemany("insert into users (org) values (?)", [("acme",)] * 400)
        connection.commit()
    finally:
        connection.close()

    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "resource_changes": [
                    {"address": f"aws_instance.a{i}", "change": {"actions": ["delete"]}}
                    for i in range(7)
                ]
            }
        ),
        encoding="utf-8",
    )

    small_plan = root / "small-plan.json"
    small_plan.write_text(
        json.dumps(
            {
                "resource_changes": [
                    {"address": f"aws_instance.b{i}", "change": {"actions": ["create"]}}
                    for i in range(2)
                ]
            }
        ),
        encoding="utf-8",
    )

    state = root / "state.json"
    state.write_text(json.dumps({"values": {"root_module": {"resources": []}}}), encoding="utf-8")

    return Fixtures(
        root=root,
        tree=tree,
        db=db,
        plan=plan,
        small_plan=small_plan,
        state=state,
        missing=root / "nowhere",
    )


def render(args: dict[str, Any], fixtures: Fixtures) -> dict[str, Any]:
    """Fill `{tree}`, `{db}`, `{plan}` and friends into a case's arguments.

    The table cannot hold literal paths: they do not exist until a fixture builds them. Same
    placeholder idiom `eval/surveys/catalogue.py` already uses for the throwaway paths it hands to
    a real MCP server.
    """
    slots = {
        "root": fixtures.root,
        "tree": fixtures.tree,
        "db": fixtures.db,
        "plan": fixtures.plan,
        "small_plan": fixtures.small_plan,
        "state": fixtures.state,
        "missing": fixtures.missing,
    }
    return {
        key: value.format(**slots) if isinstance(value, str) else value
        for key, value in args.items()
    }


# ---------------------------------------------------------------------------- the one substitution


class PrefixLister:
    """A bucket whose prefixes have declared sizes.

    `FakeLister` next door answers the same number for every prefix, which is right for a resolver
    test and useless here: this table needs one resolver instance to produce a block *and* an allow,
    or the allow case would need a second world for no reason.
    """

    SIZES: ClassVar[dict[str, int]] = {"prod/": 1_200, "scratch/": 3}

    def list(self, bucket: str, prefix: str, cap: int) -> Listing:
        del bucket
        objects = self.SIZES.get(prefix, 0)
        counted = min(objects, cap)
        return Listing(objects=counted, bytes=counted * 1024, truncated=objects > cap)


# ---------------------------------------------------------------------------- the policies
#
# Authored here rather than loaded from `examples/`, because every band in every shipped example is
# commented out on purpose — an example that declares no ceiling can never produce a block, and
# rewriting their bands from a test would be more machinery than the assertion is worth. What keeps
# these honest is `test_every_world_gates_a_tool_a_shipped_example_gates`: the tool names and
# pointers must be ones an operator would actually copy.

POLICIES: dict[str, dict[str, Any]] = {
    "fs": {
        "version": 1,
        "providers": {"fs": {"root": "."}},
        "tools": {
            "Glob": {
                "gate": {
                    "/pattern": {
                        "resolver": "fs.paths",
                        "bands": [{"above": 10, "verdict": "block"}],
                        "on_unresolved": "allow",
                        "on_unbounded": "confirm",
                    }
                }
            },
            "Read": {
                "gate": {
                    "/file_path": {
                        "resolver": "fs.paths",
                        "bands": [{"above": 10, "verdict": "block"}],
                        "on_unresolved": "allow",
                    }
                }
            },
            "directory_tree": {
                "gate": {
                    "/path": {
                        "resolver": "fs.paths",
                        "bands": [{"above": 10, "verdict": "block"}],
                        "on_unresolved": "block",
                        "on_unbounded": "confirm",
                    }
                }
            },
        },
    },
    "db": {
        "version": 1,
        "tools": {
            "execute_sql": {
                "gate": {
                    "/sql": {
                        "resolver": "db.rows",
                        "bands": [{"above": 10, "verdict": "block"}],
                        "on_unresolved": "block",
                        "on_unbounded": "confirm",
                    }
                }
            },
            # The same resolver under a ceiling nothing can reach, so the LOWER_BOUND branch is the
            # one that fires. This is not a contrivance: every `db.rows` result is a floor, so a
            # resolved statement *under* its ceiling can never be an allow, and `on_unbounded` is
            # the normal case for this resolver rather than an edge of it.
            "query": {
                "gate": {
                    "/sql": {
                        "resolver": "db.rows",
                        "bands": [{"above": 1_000_000, "verdict": "block"}],
                        "on_unresolved": "block",
                        "on_unbounded": "confirm",
                    }
                }
            },
        },
    },
    # The shell, which is where a coding agent actually deletes things. It is also the only world
    # here whose gate produces all three of "sized", "recognised but unsizeable" and "silent" from
    # one tool and one pointer, which is exactly what `on_unsized_risk` had to be driven against.
    "shell": {
        "version": 1,
        "providers": {"fs": {"root": "."}},
        "tools": {
            "Bash": {
                "gate": {
                    "/command": {
                        "resolver": "shell.paths",
                        "bands": [{"above": 10, "verdict": "block"}],
                        "on_unresolved": "allow",
                        "on_unsized_risk": "flag",
                        "on_unbounded": "confirm",
                    }
                }
            }
        },
    },
    "storage": {
        "version": 1,
        "tools": {
            "delete_objects": {
                "gate": {
                    "/uri": {
                        "resolver": "storage.objects",
                        "bands": [{"above": 100, "verdict": "block"}],
                        "on_unresolved": "block",
                        "on_unbounded": "confirm",
                    }
                }
            }
        },
    },
    # Not a resolver family — a *shape*. One file resolves to 1 object, which passes every per-call
    # ceiling there could be, and two of them exceed a declared session total. SCOPE.md NC-01 says
    # per-call resolution is structurally blind to this and that only a declared budget sees it, so
    # a budget that silently fails to accumulate is that mitigation switched off.
    "budget": {
        "version": 1,
        "tools": {
            "Read": {"gate": {"/file_path": {"resolver": "fs.paths", "on_unresolved": "allow"}}}
        },
        "session_budgets": [
            {
                "tools": ["Read"],
                "unit": "objects",
                "window": "session",
                "bands": [{"above": 1, "verdict": "block"}],
            }
        ],
    },
    "terraform": {
        "version": 1,
        "tools": {
            "terraform_apply": {
                "gate": {
                    "/plan": {
                        "resolver": "terraform.destroy",
                        "bands": [{"above": 0, "verdict": "block"}],
                        "on_unresolved": "block",
                        "on_unbounded": "confirm",
                    }
                }
            }
        },
    },
}


@dataclass
class World:
    """A policy and the resolvers it binds — everything a seam driver needs to build a gate.

    So no driver mentions `examples/entra.yaml` or the Graph mock any more, and none of them knows
    which world it is running.
    """

    name: str
    policy: Policy
    resolvers: dict[str, Resolver]
    _engine: Engine | None = None

    def engine(self) -> Engine:
        """One engine per world, built on first use.

        `Engine` is where the session tallies live, so this is the knob that decides whether two
        calls share a session. The table wants a *fresh* world per `outcome()` — otherwise the
        seventh seam would see six other seams' traffic in the session total and reach a different
        verdict for that reason alone — and the budget row wants *one* world driven twice. Both are
        expressed by how many worlds a test builds, rather than by a flag nobody would remember to
        pass.
        """
        if self._engine is None:
            self._engine = Engine(policy=self.policy, resolvers=self.resolvers)
        return self._engine


def build_world(name: str, fixtures: Fixtures, *, config: str | None = None) -> World:
    """Layered over the real registry rather than replacing it.

    `resolvers_for_client` is what ships, so binding anything else here would mean the seam table
    agreed about a stack nobody runs. Only `storage.objects` is swapped, and only because
    `S3Lister` cannot answer without an AWS account.
    """
    from neti.config.policy import load_policy

    policy = load_policy(config) if config is not None else Policy.model_validate(POLICIES[name])
    policy = policy.model_copy(update={"mode": Mode.ENFORCE})

    client = GraphClient(CRED, transport=default_tenant().transport())
    resolvers = resolvers_for_client(client, policy.providers)
    bound = policy.bound_resolvers()
    if "fs.paths" in bound:
        # The root has to be the fixture tree, not the repository: `providers.fs.root` is what
        # bounds the walk, and a root of `.` would make the magnitude depend on the checkout.
        resolvers["fs.paths"] = _fs_resolver(fixtures)
    if "shell.paths" in bound:
        # Same reason, one level up: `shell.paths` delegates every count to a filesystem resolver,
        # so it has to delegate to *this* one or `rm -rf {tree}` would be counted against the
        # checkout and the seam table's magnitudes would move with the repository.
        resolvers["shell.paths"] = ShellPathsResolver(root=str(fixtures.root))
    resolvers["storage.objects"] = ObjectStoreResolver(PrefixLister())
    return World(name=name, policy=policy, resolvers=resolvers)


def _fs_resolver(fixtures: Fixtures) -> Resolver:
    from neti.resolvers.filesystem import FilesystemResolver

    return FilesystemResolver(root=fixtures.root)
