"""Adapter callables bridging MCP tool arguments to engine operation handlers.

Each builder returns a plain function whose typed parameters mirror the JSONL
payload fields of one operation (see ``docs/PROTOCOL.md``). The function packs
its arguments into a payload dict, calls ``engine.handlers[op](payload)``, and
returns the handler's response payload dict. A raised
:class:`~cerebellum_cua.errors.MatrixUIError` is caught and converted to a
structured ``{"error": {...}}`` dict so the MCP client receives a clean result
instead of a transport-level failure.

No operation logic lives here — these are argument-shaping shims over the same
handlers the JSONL protocol dispatches against.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.errors import MatrixUIError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

#: A bound tool callable returning the operation's response payload dict.
ToolFn = Callable[..., dict[str, Any]]


def _dispatch(engine: CuaEngine, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call one engine handler, converting domain errors to a structured dict."""
    try:
        return engine.handlers[operation](payload)
    except MatrixUIError as exc:
        return {"error": exc.to_dict()}


def build_matrix_tool(engine: CuaEngine) -> ToolFn:
    """Capture the live accessibility tree, persist it, and register an epoch."""

    def build_matrix(
        target: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        capture_backend: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"target": target or {}, "config": config or {}}
        if capture_backend is not None:
            payload["capture_backend"] = capture_backend
        return _dispatch(engine, "build_matrix", payload)

    return build_matrix


def get_element_tool(engine: CuaEngine) -> ToolFn:
    """Return one hydrated element by its dense ``row_id``."""

    def get_element(
        row_id: int,
        snapshot_id: int | None = None,
        include_relationships: bool = True,
        include_semantics: bool = True,
        include_children_stub: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "row_id": row_id,
            "include_relationships": include_relationships,
            "include_semantics": include_semantics,
            "include_children_stub": include_children_stub,
        }
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        return _dispatch(engine, "get_element", payload)

    return get_element


def load_children_tool(engine: CuaEngine) -> ToolFn:
    """Expand one accordion node, returning its direct children hydrated."""

    def load_children(
        parent_row_id: int = 0,
        snapshot_id: int | None = None,
        lazy_token: str | None = None,
        max_depth: int = 2,
        include_properties: bool = True,
        include_semantics: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "parent_row_id": parent_row_id,
            "max_depth": max_depth,
            "include_properties": include_properties,
            "include_semantics": include_semantics,
        }
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        if lazy_token is not None:
            payload["lazy_token"] = lazy_token
        return _dispatch(engine, "load_children", payload)

    return load_children


def invoke_action_tool(engine: CuaEngine) -> ToolFn:
    """Re-find an element on the live tree and fire its default action."""

    def invoke_action(
        row_id: int,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"row_id": row_id}
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        return _dispatch(engine, "invoke_action", payload)

    return invoke_action


def get_snapshot_diff_tool(engine: CuaEngine) -> ToolFn:
    """Diff two epochs from the engine's in-memory snapshot history."""

    def get_snapshot_diff(from_epoch: int, to_epoch: int) -> dict[str, Any]:
        payload = {"from_epoch": from_epoch, "to_epoch": to_epoch}
        return _dispatch(engine, "get_snapshot_diff", payload)

    return get_snapshot_diff


#: operation name -> (builder, docstring-bearing fn name) used to register tools.
TOOL_BUILDERS: dict[str, Callable[[CuaEngine], ToolFn]] = {
    "build_matrix": build_matrix_tool,
    "get_element": get_element_tool,
    "load_children": load_children_tool,
    "invoke_action": invoke_action_tool,
    "get_snapshot_diff": get_snapshot_diff_tool,
}
