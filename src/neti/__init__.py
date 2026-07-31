"""neti — a preflight gate for agent tool calls.

The public surface is deliberately one class. `Preflight` is the in-process seam, for a tool loop
somebody wrote by hand; the MCP gateway and the Claude Code hook are the two that cannot be
forgotten, and both are reached through the `neti` command rather than through an import.
"""

from neti.preflight import Blocked, Preflight, Verdict

__version__ = "0.1.0"
__all__ = ["Blocked", "Preflight", "Verdict", "__version__"]
