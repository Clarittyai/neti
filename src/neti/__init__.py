"""neti — a preflight gate for agent tool calls.

The public surface is deliberately one class. `Preflight` is the in-process seam, for a tool loop
somebody wrote by hand; the MCP gateway and the Claude Code hook are the two that cannot be
forgotten, and both are reached through the `neti` command rather than through an import.
"""

import os as _os
import sys as _sys

# Pydantic scans installed distributions for plugins the first time a model is built, and imports
# whatever it finds. Nothing here uses one — but `logfire` registers itself as a pydantic plugin,
# and it arrives transitively with several agent stacks, so a machine that has it pays 148ms of
# somebody else's observability import **on every tool call**: 268ms per call against 120ms
# without. Measured on a real repository, over a workday's worth of traffic.
#
# Only for our own command, and the check is deliberately narrow. `import neti` inside somebody's
# application must not switch off a mechanism their application may be using — this is a decision
# about the `neti` process, so it is conditioned on being that process.
if _os.path.basename(_sys.argv[0] or "") in {"neti", "neti.exe"}:
    _os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")

from neti._version import __version__
from neti.preflight import Blocked, Preflight, Verdict

__all__ = ["Blocked", "Preflight", "Verdict", "__version__"]
