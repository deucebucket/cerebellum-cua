"""MCP server wrapper exposing the five JSONL operations as MCP tools.

This package is a thin adapter over :class:`~cerebellum_cua.cli.engine.CuaEngine`:
it registers ``build_matrix``, ``get_element``, ``load_children``,
``invoke_action`` and ``get_snapshot_diff`` as Model Context Protocol tools, each
calling the matching engine handler and returning its response payload. No
operation logic is duplicated here.

The optional ``mcp`` dependency is imported lazily inside the builder functions,
so ``import cerebellum_cua.mcp`` succeeds on a host without the ``[mcp]`` extra —
mirroring how the ``uia`` / ``atspi`` capture backends defer their OS-specific
imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.fastmcp import FastMCP

__all__ = ["build_server", "run_stdio"]


def build_server(db_dsn: str | None, secret: str, **engine_kwargs: Any) -> FastMCP:
    """Build and return a configured MCP server (see :mod:`.server`)."""
    from cerebellum_cua.mcp.server import build_server as _build

    return _build(db_dsn, secret, **engine_kwargs)


def run_stdio(db_dsn: str | None, secret: str, **engine_kwargs: Any) -> None:
    """Build the server and run it over stdio (see :mod:`.server`)."""
    from cerebellum_cua.mcp.server import run_stdio as _run

    _run(db_dsn, secret, **engine_kwargs)
