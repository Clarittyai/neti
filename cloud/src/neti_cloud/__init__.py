"""The neti control plane — the paid tier.

BUSL-1.1, see LICENSING.md. Everything here exists because a single machine cannot do it: a second
person approving a call, one policy across a fleet, budgets that survive a restart, audit across
every agent.

This package imports `neti` freely. The reverse never happens, and a test enforces it.
"""

__version__ = "0.1.0"
