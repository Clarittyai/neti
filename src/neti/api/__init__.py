"""The console API.

Nothing in the rest of the package may import from here — `neti.api` depends on `neti`, never the
other way round. The gate, the CLI and the tests must all keep working with FastAPI uninstalled.
"""
