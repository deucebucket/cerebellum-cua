"""Unit tests for the MCP server wrapper — Linux-testable, no live transport.

These build the server against an in-memory SQLite engine, seed a snapshot, and
exercise the registered tools' underlying functions directly. No real stdio
transport is started: the adapters are plain callables that route to the engine,
so they can be invoked and asserted in-process.
"""

from __future__ import annotations

from typing import Any

import pytest

# The MCP wrapper needs the optional `mcp` extra (pip install -e '.[mcp]').
# Skip cleanly when it is absent, like the windows/postgres-marked tests.
pytest.importorskip("mcp")

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


#: Every engine operation must be reachable as an MCP tool (parity with JSONL).
_EXPECTED_TOOLS = {
    "build_matrix",
    "get_element",
    "load_children",
    "invoke_action",
    "get_snapshot_diff",
    "screenshot",
    "read_text",
    "run_skill",
    "list_windows",
    "elevate",
    "read_legend",
    "wireframe",
    "annotate",
}


def test_all_operations_registered(server: Any) -> None:
    # The MCP surface must expose every engine operation, not a subset — an agent
    # that only sees capture/read/diff mistakes the tool for a passive viewer.
    assert set(_tools_by_name(server)) == _EXPECTED_TOOLS


def test_every_tool_has_a_meaningful_description(server: Any) -> None:
    # Agents pick tools from their descriptions; an empty/blank description (the
    # prior bug, where the inner fn had no docstring) is unusable.
    for name, tool in _tools_by_name(server).items():
        desc = (tool.description or "").strip()
        assert len(desc) >= 40, f"tool {name!r} has a too-thin description: {desc!r}"


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


# --- newly exposed operations route through their handlers ------------------
def test_list_windows_tool_routes_to_engine(server: Any) -> None:
    list_windows = _tools_by_name(server)["list_windows"].fn
    result = list_windows()
    assert "error" not in result
    assert "windows" in result and "count" in result


def test_read_text_tool_routes_to_engine(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    read_text = _tools_by_name(server)["read_text"].fn
    result = read_text(snapshot_id=sid)
    assert "error" not in result
    assert result["count"] >= 1
    assert any(t["text"] == "Save" for t in result["texts"])


def test_read_legend_tool_routes_to_engine(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    read_legend = _tools_by_name(server)["read_legend"].fn
    result = read_legend(snapshot_id=sid)
    assert "error" not in result
    assert "elements" in result and "legend" in result


def test_wireframe_tool_routes_to_engine(server: Any) -> None:
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    wireframe = _tools_by_name(server)["wireframe"].fn
    result = wireframe(snapshot_id=sid)
    assert "error" not in result
    assert isinstance(result["text"], str)


def test_run_skill_tool_unknown_skill_routes_to_engine(server: Any) -> None:
    # Seed a snapshot so run_skill resolves without triggering a live build_matrix.
    server.cua_engine.register_seed(_window_with_button(epoch=1))
    run_skill = _tools_by_name(server)["run_skill"].fn
    result = run_skill(skill="definitely_not_a_skill")
    assert result["success"] is False
    assert result["reason"] == "unknown_skill"


def test_screenshot_tool_routes_to_engine(server: Any, monkeypatch: Any) -> None:
    # Patch the grabber so the test never touches a real display.
    import cerebellum_cua.capture.screenshot as shot

    monkeypatch.setattr(
        shot, "grab_screenshot",
        lambda path, display=None, region=None: {
            "path": path, "width": 1920, "height": 1080},
    )
    screenshot = _tools_by_name(server)["screenshot"].fn
    result = screenshot(path="/tmp/x.png")
    assert "error" not in result
    assert result["width"] == 1920 and result["height"] == 1080


def test_elevate_tool_routes_to_engine(server: Any) -> None:
    # No elevation password is configured, so this must resolve to a plain result
    # dict (never escalate); we only assert it routed cleanly.
    elevate = _tools_by_name(server)["elevate"].fn
    result = elevate(method="auto")
    assert isinstance(result, dict)
    assert "error" not in result or result["error"]["code"]  # structured either way


def test_annotate_tool_is_registered_and_routes(server: Any) -> None:
    # annotate needs a grabber + OpenCV which may be absent on CI; assert it routes
    # to a structured result (success dict or typed error), never an exception.
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    annotate = _tools_by_name(server)["annotate"].fn
    result = annotate(snapshot_id=sid, path="/tmp/missing-on-purpose.png")
    assert isinstance(result, dict)


def test_invoke_action_type_form_routes_to_synthetic_input(
    server: Any, monkeypatch: Any
) -> None:
    # The expanded invoke_action must support coordinate/raw-input forms, not just
    # row_id element actions — here a 'type' action drives synthetic typing.
    import cerebellum_cua.capture.input as inp

    typed: list[str] = []

    def _fake_type(self: Any, text: str, abort: Any = None) -> bool:
        typed.append(text)
        return True

    monkeypatch.setattr(inp.SyntheticInput, "type_text", _fake_type)
    invoke_action = _tools_by_name(server)["invoke_action"].fn
    result = invoke_action(action="type", value="hello world")
    assert result["error"] if "error" in result else result["success"] is True
    assert typed == ["hello world"]


def test_invoke_action_element_form_still_works(server: Any) -> None:
    # The element form (row_id) must still route; on Linux with no live backend it
    # surfaces the typed 1006, exactly as before.
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    invoke_action = _tools_by_name(server)["invoke_action"].fn
    result = invoke_action(row_id=1, snapshot_id=sid)
    assert result["error"]["code"] == 1006


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


def test_screenshot_tool_forwards_row_id_region(server: Any, monkeypatch: Any) -> None:
    import cerebellum_cua.capture.screenshot as shot

    seen: dict[str, Any] = {}

    def _fake_grab(path: str, display: Any = None, region: Any = None) -> dict:
        seen["region"] = region
        return {"path": path, "width": 40, "height": 20,
                "region": list(region) if region else None, "region_applied": True}

    monkeypatch.setattr(shot, "grab_screenshot", _fake_grab)
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    screenshot = _tools_by_name(server)["screenshot"].fn
    result = screenshot(row_id=1, snapshot_id=sid)
    assert seen["region"] == (0, 0, 40, 20)
    assert result["region"] == [0, 0, 40, 20]
