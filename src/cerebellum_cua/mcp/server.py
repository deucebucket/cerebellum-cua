"""Builds an MCP server exposing every engine operation, backed by one engine.

``build_server`` constructs a single :class:`~cerebellum_cua.cli.engine.CuaEngine`
and registers all operations in :data:`._tools.TOOL_BUILDERS` as MCP tools (at
parity with the JSONL protocol). Every tool routes to ``engine.handlers[op]`` —
no operation logic is reimplemented here. ``run_stdio`` runs the resulting server
over the stdio transport.

The ``mcp`` package is imported lazily inside :func:`build_server`; when it is
absent a clear, typed error is raised pointing at ``pip install -e '.[mcp]'``, so
importing this module never fails on a host without the optional extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.mcp._tools import TOOL_BUILDERS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.fastmcp import FastMCP

_SERVER_NAME = "cerebellum-cua"

_MCP_MISSING = (
    "The 'mcp' package is required to run the cerebellum-cua MCP server. "
    "Install it with: pip install -e '.[mcp]'"
)


class McpDependencyError(RuntimeError):
    """Raised when the optional ``mcp`` dependency is not importable."""


def _load_fastmcp() -> type[FastMCP]:
    """Lazily import :class:`FastMCP`, raising a clear error if it is missing."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise McpDependencyError(_MCP_MISSING) from exc
    return FastMCP


def build_server(db_dsn: str | None, secret: str, **engine_kwargs: Any) -> FastMCP:
    """Return a ``FastMCP`` server with every engine operation registered as a tool.

    The server owns one ``CuaEngine`` (constructed from ``db_dsn`` / ``secret``
    plus any forwarded ``engine_kwargs`` such as ``max_response_tokens``). The
    engine is attached to the server as ``server.cua_engine`` so callers/tests
    can reach it and close it on shutdown.
    """
    fast_mcp = _load_fastmcp()
    engine = CuaEngine(db_dsn, secret, **engine_kwargs)
    server = fast_mcp(_SERVER_NAME)
    for operation, builder in TOOL_BUILDERS.items():
        tool_fn = builder(engine)
        server.add_tool(tool_fn, name=operation, description=tool_fn.__doc__)
    # Expose the engine for lifecycle management and introspection.
    server.cua_engine = engine  # type: ignore[attr-defined]
    return server


def run_stdio(db_dsn: str | None, secret: str, **engine_kwargs: Any) -> None:
    """Build the server and serve it over stdio until the client disconnects."""
    server = build_server(db_dsn, secret, **engine_kwargs)
    try:
        server.run(transport="stdio")
    finally:
        engine = getattr(server, "cua_engine", None)
        if engine is not None:
            engine.close()


__all__ = ["build_server", "run_stdio", "McpDependencyError"]
