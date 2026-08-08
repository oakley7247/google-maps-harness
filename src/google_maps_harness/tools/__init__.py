# =============================================================================
# tools/__init__.py — imports every tool module, which is what registers the
# tools with the MCP server.
#
# Part of: google-maps-harness. Called by: server.py, with one import. Calls:
# the four modules below, for their import side effects.
# A module missing from this list is a tool nobody can call, and nothing else
# would notice — so `tests/test_tools.py` asserts the registered surface
# against the list each module declares.
# =============================================================================
"""Register every tool group with the MCP server."""

from . import environment, geocoding, places, routes

__all__ = ["environment", "geocoding", "places", "routes"]
