"""The one place the version is written.

A leaf module with no imports, because both `neti/__init__.py` and `neti/engine.py` need it and the
package root imports `Preflight`, which imports the engine — reading it from `neti` directly is a
circular import.

It matters more than a version string usually does. `Engine.code_version` is stamped into every
sealed decision record and is part of the answer to *which build decided this*; it used to be a
second literal that agreed with `pyproject.toml` by luck, so the next release would have shipped a
gate recording the version before it.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
