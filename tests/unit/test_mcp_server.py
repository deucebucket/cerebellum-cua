"""Unit tests for the MCP server wrapper — Linux-testable, no live transport.

These build the server against an in-memory SQLite engine, seed a snapshot, and
exercise the registered tools' underlying functions directly. No real stdio
transport is started: the adapters are plain callables that route to the engine,
so they can be invoked and asserted in-process.
"""

from __future__ import annotations

from typing import Any

import pytest

from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.model import Snapshot

SECRET = "unit-test-secret"


def _elem(name: str, ct: int, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "control_type": ct,
        "class_name": "Cls",
        "bounding_rect": {"left": 0, "top": 0, "width": 40, "height": 20},
    }
    base.update(extra)
    return base


def _window_with_button(epoch: int, button_name: str = "Save") -> Snapshot:
    """root window(0) -> [action button(1), edit(2)]."""
    walked = [
        (_elem("Main Window", 50032), 0, None),
        (
            _elem(
                button_name,
                50000,
                patterns={"invoke": {"supported": True}},
                properties={"is_enabled": True},
            ),
            1,
            0,
        ),
        (_elem("field", 50004), 1, 0),
    ]
    return build_snapshot(walked, epoch=epoch)


def test_package_imports_cleanly() -> None:
    import cerebellum_cua.mcp as mcp_pkg

    assert hasattr(mcp_pkg, "build_server")
    assert hasattr(mcp_pkg, "run_stdio")


@pytest.fixture()
def server() -> Any:
    from cerebellum_cua.mcp import build_server

    srv = build_server(db_dsn=None, secret=SECRET)
    yield srv
    srv.cua_engine.close()


def _tools_by_name(server: Any) -> dict[str, Any]:
    """Return the server's registered tools keyed by name (sync introspection)."""
    return {t.name: t for t in server._tool_manager.list_tools()}


def test_five_tools_registered(server: Any) -> None:
    names = set(_tools_by_name(server))
    assert names == {
        "build_matrix",
        "get_element",
        "load_children",
        "invoke_action",
        "get_snapshot_diff",
    }


def test_get_element_tool_routes_to_engine(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    get_element = _tools_by_name(server)["get_element"].fn
    result = get_element(row_id=1, snapshot_id=sid)
    assert "error" not in result
    element = result["element"]
    assert element["row_id"] == 1
    assert element["name"] == "Save"
    concepts = {c["domain_concept"] for c in element["semantics"]}
    assert "action_button" in concepts


def test_get_element_unknown_row_returns_structured_error(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    get_element = _tools_by_name(server)["get_element"].fn
    result = get_element(row_id=999, snapshot_id=sid)
    assert result["error"]["code"] == 1002  # ELEMENT_NOT_FOUND
    assert result["error"]["message"] == "ELEMENT_NOT_FOUND"


def test_load_children_tool_routes_to_engine(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    load_children = _tools_by_name(server)["load_children"].fn
    result = load_children(parent_row_id=0, snapshot_id=sid)
    assert "error" not in result
    assert [c["row_id"] for c in result["children"]] == [1, 2]


def test_get_snapshot_diff_tool_routes_to_engine(server: Any) -> None:
    eng = server.cua_engine
    eng.register_seed(_window_with_button(epoch=1, button_name="Save"))
    eng.register_seed(_window_with_button(epoch=2, button_name="Saved"))
    get_snapshot_diff = _tools_by_name(server)["get_snapshot_diff"].fn
    result = get_snapshot_diff(from_epoch=1, to_epoch=2)
    assert "error" not in result
    assert result["added_row_ids"] == [1]
    assert result["removed_row_ids"] == [1]


def test_get_snapshot_diff_unknown_epoch_returns_structured_error(server: Any) -> None:
    server.cua_engine.register_seed(_window_with_button(epoch=1))
    get_snapshot_diff = _tools_by_name(server)["get_snapshot_diff"].fn
    result = get_snapshot_diff(from_epoch=1, to_epoch=42)
    assert result["error"]["code"] == 1001  # SNAPSHOT_NOT_FOUND


def test_build_matrix_tool_unavailable_backend_returns_structured_error(server: Any) -> None:
    build_matrix = _tools_by_name(server)["build_matrix"].fn
    result = build_matrix(target={"exe_regex": "notepad"}, capture_backend="uia")
    assert result["error"]["code"] == 1006  # UIA_ACCESS_DENIED
    assert result["error"]["details"]["reason"] == "capture_unavailable"


def test_invoke_action_tool_on_linux_returns_structured_error(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    invoke_action = _tools_by_name(server)["invoke_action"].fn
    result = invoke_action(row_id=1, snapshot_id=sid)
    assert result["error"]["code"] == 1006  # UIA_ACCESS_DENIED


def test_build_server_without_mcp_raises_clean_error(monkeypatch: Any) -> None:
    """When the 'mcp' package is unimportable, build_server raises a clear error."""
    import builtins

    from cerebellum_cua.mcp.server import McpDependencyError

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mcp.server.fastmcp" or name.startswith("mcp.server.fastmcp"):
            raise ImportError("simulated missing mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from cerebellum_cua.mcp import build_server

    with pytest.raises(McpDependencyError, match=r"pip install -e '\.\[mcp\]'"):
        build_server(db_dsn=None, secret=SECRET)
