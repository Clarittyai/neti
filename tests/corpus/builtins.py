"""Claude Code's own built-in tools, and their argument shapes.

**These are the tools most agent calls in the world actually are**, and no `tools/list` anywhere
reports them. They are not an MCP server; they are the harness's own built-ins, which is precisely
why `PreToolUse` exists and why the hook is the only seam that can see them. So the survey in
`eval/` cannot reach them and this is authored by hand.

Authored from the documented tool inputs rather than captured from a live session, and that is the
one soft spot in the corpus — a parameter renamed upstream would not fail anything here until
somebody noticed. It is still worth having: `Glob`, `Grep`, `Read`, `Edit` and `Write` are the
calls `examples/coding-agent.yaml` gates by name, and until now nothing checked that the matcher
would propose those gates on its own if it met the tools cold.

`Bash` is included deliberately, with the expectation that **nothing in it is gated**. Sizing a
shell command means parsing a grammar rather than reading a value, which SCOPE.md NC-09 and NC-10
decline to do. A corpus that quietly left `Bash` out would be hiding the most-used tool there is;
listing it and gating none of it is the honest form of the same statement.
"""

from __future__ import annotations

from typing import Any

__all__ = ["BUILTINS"]


def _obj(**props: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in props.items()},
    }


BUILTINS: tuple[dict[str, Any], ...] = (
    {
        "name": "Read",
        "description": "Read a file from the local filesystem.",
        "schema": _obj(file_path="string", offset="number", limit="number"),
    },
    {
        "name": "Write",
        "description": "Write a file to the local filesystem.",
        "schema": _obj(file_path="string", content="string"),
    },
    {
        "name": "Edit",
        "description": "Perform an exact string replacement in a file.",
        "schema": _obj(
            file_path="string",
            old_string="string",
            new_string="string",
            replace_all="boolean",
        ),
    },
    {
        # The one where a short argument is the whole repository. `**/*` is eleven characters.
        "name": "Glob",
        "description": "Fast file pattern matching against any codebase size.",
        "schema": _obj(pattern="string", path="string"),
    },
    {
        # `pattern` here matches *content*, and must not be gated as a set of paths. `path` is the
        # parameter that bounds this call, and is.
        "name": "Grep",
        "description": "A powerful search tool built on ripgrep.",
        "schema": _obj(
            pattern="string",
            path="string",
            glob="string",
            output_mode="string",
            head_limit="number",
        ),
    },
    {
        "name": "Bash",
        "description": "Execute a bash command.",
        "schema": _obj(
            command="string",
            description="string",
            timeout="number",
            run_in_background="boolean",
        ),
    },
    {
        "name": "NotebookEdit",
        "description": "Replace the contents of a cell in a Jupyter notebook.",
        "schema": _obj(
            notebook_path="string",
            cell_id="string",
            new_source="string",
            cell_type="string",
            edit_mode="string",
        ),
    },
    {
        "name": "WebFetch",
        "description": "Fetch content from a URL and process it with a model.",
        "schema": _obj(url="string", prompt="string"),
    },
    {
        "name": "WebSearch",
        "description": "Search the web.",
        "schema": _obj(query="string"),
    },
    {
        "name": "Task",
        "description": "Launch a new agent to handle a multi-step task.",
        "schema": _obj(description="string", prompt="string", subagent_type="string"),
    },
)
