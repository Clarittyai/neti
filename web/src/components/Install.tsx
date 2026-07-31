"use client";

/**
 * How the gate actually gets in front of an agent.
 *
 * There are only two seams that exist today, and this shows both rather than pretending the first
 * one is enough:
 *
 * 1. MCP over stdio — every local server in Claude Code, Claude Desktop and Cursor is a `command`
 *    the client launches. The install is that command becoming an argument to ours. Nothing about
 *    the server changes and nothing about the agent changes.
 * 2. The harness's own built-in tools, which no proxy can see at all. Claude Code's `PreToolUse`
 *    hook is the only place a third party can stop one.
 *
 * The before/after framing is deliberate. "Add these four lines" is a much weaker claim than
 * showing the operator their existing config with one line changed, and the second is what makes
 * "no agent rewrite" checkable rather than assertable.
 */

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";

interface Target {
  id: string;
  label: string;
  where: string;
  blurb: string;
  before?: string;
  after: string;
}

const TARGETS: Target[] = [
  {
    id: "mcp",
    label: "An MCP server",
    where: ".mcp.json · claude_desktop_config.json · ~/.cursor/mcp.json",
    blurb:
      "Whatever command launches the server becomes an argument to the gate. The client keeps talking to the same name, over the same transport, to a process that now resolves before it forwards.",
    before: `{
  "mcpServers": {
    "entra": {
      "command": "npx",
      "args": ["-y", "@acme/entra-mcp"]
    }
  }
}`,
    after: `{
  "mcpServers": {
    "entra": {
      "command": "neti",
      "args": ["gate", "--stdio", "--",
               "npx", "-y", "@acme/entra-mcp"]
    }
  }
}`,
  },
  {
    id: "hook",
    label: "Built-in tools",
    where: ".claude/settings.json",
    blurb:
      "The calls no proxy sees — the harness's own tools. A pass emits nothing at all, so your existing permission rules keep working exactly as they did; only an oversized call gets an answer.",
    after: `{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "neti hook" }
        ]
      }
    ]
  }
}`,
  },
  {
    id: "sdk",
    label: "Your own tool loop",
    where: "wherever you dispatch a tool call",
    blurb:
      "For agents that speak neither MCP nor a hook — an Anthropic or OpenAI function-calling loop, a LangChain tool. The denial comes back as a value because your next line hands it to the model, and a specific number is what makes it retry with a narrower scope instead of giving up. This is the seam you can forget to use, which is why the other two come first.",
    after: `from neti import Preflight

pf = Preflight.from_config("neti.yaml")

for block in message.content:
    if block.type == "tool_use":
        out = pf.dispatch(
            block.name, block.input,
            lambda: TOOLS[block.name](**block.input),
        )`,
  },
  {
    id: "http",
    label: "A remote server",
    where: "wherever the client's server URL lives",
    blurb:
      "For MCP over HTTP: run the gate beside the client and point the client at it instead of at the server.",
    after: `neti gate --upstream https://mcp.internal/rpc

# then, in the client:
#   https://mcp.internal/rpc  ->  http://127.0.0.1:8722`,
  },
];

export function Install() {
  const [active, setActive] = useState(TARGETS[0].id);
  const target = TARGETS.find((t) => t.id === active) ?? TARGETS[0];

  return (
    <section>
      <h2 className="text-sm font-semibold">Put the gate in front of your agent</h2>
      <p className="mt-1 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
        No SDK, no code change, no redeploy. The agent does not learn that the gate exists — it
        learns that a call was too big, which is a thing it already knows how to handle.
      </p>

      <div className="glass-card mt-3 rounded-xl p-4">
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          Or skip the reading:{" "}
          <code className="font-mono text-[12px] text-foreground">neti init</code> finds the MCP
          servers already configured on the machine, asks each one what tools it exposes, and writes
          the policy — with every ceiling left blank, because those come from your traffic a week
          later and not from a generator.
        </p>
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {TARGETS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActive(t.id)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
              t.id === active
                ? "bg-accent/10 text-accent"
                : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="glass-card mt-3 rounded-2xl p-5">
        <p className="font-mono text-[11px] text-muted-foreground">{target.where}</p>
        <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-muted-foreground">
          {target.blurb}
        </p>

        <div
          className={cn(
            "mt-4 grid gap-4",
            target.before ? "lg:grid-cols-2" : "grid-cols-1",
          )}
        >
          {target.before ? <Snippet label="before" code={target.before} muted /> : null}
          <Snippet label={target.before ? "after" : "add this"} code={target.after} />
        </div>
      </div>
    </section>
  );
}

function Snippet({ label, code, muted }: { label: string; code: string; muted?: boolean }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
          {label}
        </span>
        {!muted ? (
          <button
            onClick={() => {
              void navigator.clipboard?.writeText(code);
              setCopied(true);
              setTimeout(() => setCopied(false), 1400);
            }}
            className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          >
            {copied ? (
              <>
                <Check className="h-3 w-3 text-[hsl(var(--verdict-allow))]" /> copied
              </>
            ) : (
              <>
                <Copy className="h-3 w-3" /> copy
              </>
            )}
          </button>
        ) : null}
      </div>
      <pre
        className={cn(
          "overflow-x-auto rounded-xl border p-3.5 font-mono text-[11.5px] leading-relaxed",
          muted
            ? "border-border/50 bg-muted/30 text-muted-foreground"
            : "border-accent/25 bg-accent/[0.05]",
        )}
      >
        {code}
      </pre>
    </div>
  );
}
