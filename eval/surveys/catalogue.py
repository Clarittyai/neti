"""The MCP servers people actually install, as their clients actually launch them.

Chosen to be representative rather than flattering: the reference servers, the archived ones that
are still all over people's configs, and the third-party ones that show up in real `.mcp.json`
files. If a server is here only because `neti` happens to size it well, this survey is worthless.

**Placeholder credentials.** Several servers read a token from the environment at startup. Where
one is needed, a plainly fake value is supplied and the entry is marked `placeholder_credential`,
because `tools/list` does not authenticate and the question this survey asks — *what does this
server expose, and can `neti` size any of it* — is answerable without a real account. A server that
refuses to start anyway is recorded as `could not launch`, with its own words, rather than dropped.

**Remote servers are here to be counted, not launched.** `find_clients` skips any entry without a
`command` — an HTTP or SSE server is not ours to spawn. That is correct behaviour and also a
coverage fact worth stating out loud, since a growing share of real servers are remote, so the
entries are carried with `transport="remote"` and reported as out of reach of `neti init` by
construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["CATALOGUE", "Candidate"]


@dataclass(frozen=True)
class Candidate:
    name: str
    """The key an operator would put in `mcpServers`."""

    argv: tuple[str, ...]
    """Exactly what the client runs. Empty for a remote server."""

    layer: str
    """Which `src/neti/eval/stack.py` layer this server's writes land in."""

    note: str
    env: dict[str, str] = field(default_factory=dict)
    placeholder_credential: bool = False
    transport: str = "stdio"
    archived: bool = False
    """Still in people's configs long after the repository stopped being maintained."""


# `{dir}` and `{db}` are filled in by the survey with a throwaway path, the way a client config
# carries a real one.
CATALOGUE: tuple[Candidate, ...] = (
    # ------------------------------------------------------------------ reference, maintained
    Candidate(
        name="filesystem",
        argv=("npx", "-y", "@modelcontextprotocol/server-filesystem", "{dir}"),
        layer="filesystem",
        note="the server `examples/coding-agent.yaml` is written against",
    ),
    Candidate(
        name="memory",
        argv=("npx", "-y", "@modelcontextprotocol/server-memory"),
        layer="filesystem",
        note="a knowledge graph in a local JSON file",
    ),
    Candidate(
        name="sequentialthinking",
        argv=("npx", "-y", "@modelcontextprotocol/server-sequential-thinking"),
        layer="none",
        note="pure reasoning scaffold; touches nothing, so nothing to size",
    ),
    Candidate(
        name="everything",
        argv=("npx", "-y", "@modelcontextprotocol/server-everything"),
        layer="none",
        note="the protocol test server — every feature, no real target",
    ),
    Candidate(
        name="git",
        argv=("uvx", "mcp-server-git", "--repository", "{dir}"),
        layer="source control",
        note="local git operations, including resets and checkouts",
    ),
    Candidate(
        name="fetch",
        argv=("uvx", "mcp-server-fetch"),
        layer="none",
        note="reads one URL at a time",
    ),
    Candidate(
        name="time",
        argv=("uvx", "mcp-server-time"),
        layer="none",
        note="carried as a control: nothing here should ever be gated",
    ),
    # ---------------------------------------------------------- reference, archived but installed
    Candidate(
        name="github",
        argv=("npx", "-y", "@modelcontextprotocol/server-github"),
        layer="source control",
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_placeholder_not_a_real_token"},
        placeholder_credential=True,
        archived=True,
        note="`github.repos` and `github.files` ship and are live-verified against this surface",
    ),
    Candidate(
        name="postgres",
        argv=(
            "npx",
            "-y",
            "@modelcontextprotocol/server-postgres",
            "postgresql://placeholder@127.0.0.1:5432/placeholder",
        ),
        layer="database",
        archived=True,
        note="`db.rows` ships for exactly this",
    ),
    Candidate(
        name="sqlite",
        argv=("uvx", "mcp-server-sqlite", "--db-path", "{db}"),
        layer="database",
        archived=True,
        note="`db.rows` speaks sqlite through the stdlib, no extra needed",
    ),
    Candidate(
        name="slack",
        argv=("npx", "-y", "@modelcontextprotocol/server-slack"),
        layer="messaging",
        env={"SLACK_BOT_TOKEN": "xoxb-placeholder", "SLACK_TEAM_ID": "T0PLACEHOLDER"},
        placeholder_credential=True,
        archived=True,
        note="`stack.py` calls messaging uncovered; this is the server that makes that concrete",
    ),
    Candidate(
        name="gdrive",
        argv=("npx", "-y", "@modelcontextprotocol/server-gdrive"),
        layer="SaaS records",
        placeholder_credential=True,
        archived=True,
        note="RESOLVER_CONTRACT cites Drive's `incompleteSearch` as the canonical PARTIAL case",
    ),
    Candidate(
        name="puppeteer",
        argv=("npx", "-y", "@modelcontextprotocol/server-puppeteer"),
        layer="none",
        archived=True,
        note="browser control — a grammar, not a value",
    ),
    Candidate(
        name="brave-search",
        argv=("npx", "-y", "@modelcontextprotocol/server-brave-search"),
        layer="none",
        env={"BRAVE_API_KEY": "placeholder"},
        placeholder_credential=True,
        archived=True,
        note="read-only search",
    ),
    # ------------------------------------------------------------------ third party
    Candidate(
        name="playwright",
        argv=("npx", "-y", "@playwright/mcp@latest"),
        layer="none",
        note="the maintained browser server most configs now carry",
    ),
    Candidate(
        name="notion",
        argv=("npx", "-y", "@notionhq/notion-mcp-server"),
        layer="SaaS records",
        env={"NOTION_TOKEN": "ntn_placeholder"},
        placeholder_credential=True,
        note="pages and databases — the `records` unit nothing ships a resolver for",
    ),
    Candidate(
        name="stripe",
        argv=("npx", "-y", "@stripe/mcp", "--tools=all", "--api-key=sk_test_placeholder"),
        layer="SaaS records",
        placeholder_credential=True,
        note="refunds and subscription cancellations are countable and irreversible",
    ),
    Candidate(
        name="context7",
        argv=("npx", "-y", "@upstash/context7-mcp"),
        layer="none",
        note="documentation lookup",
    ),
    Candidate(
        name="chrome-devtools",
        argv=("npx", "-y", "chrome-devtools-mcp@latest"),
        layer="none",
        note="browser automation and tracing",
    ),
    # ------------------------------------------------------------------ remote, not ours to launch
    Candidate(
        name="linear",
        argv=(),
        transport="remote",
        layer="SaaS records",
        note="https://mcp.linear.app/sse — `find_clients` skips it: no `command` to run",
    ),
    Candidate(
        name="sentry",
        argv=(),
        transport="remote",
        layer="SaaS records",
        note="https://mcp.sentry.dev/mcp — same",
    ),
    Candidate(
        name="atlassian",
        argv=(),
        transport="remote",
        layer="SaaS records",
        note="https://mcp.atlassian.com/v1/sse — same",
    ),
)
