"""Unit tests for the cli/engine layer — Linux-testable, no COM.

These drive the composition root end to end without ever touching live UIA:
snapshots are seeded from FAKE walked element dicts via ``build_snapshot`` and
registered through the engine, then ``get_element`` / ``load_children`` are
exercised *through* ``engine.handle_line`` (the real JSONL protocol). The live
capture / invoke paths are asserted to raise a clean typed error (not a bare
ImportError) on this non-Windows host.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from cerebellum_cua.cli import CuaEngine
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


@pytest.fixture()
def engine() -> Any:
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    yield eng
    eng.close()


def _call(engine: CuaEngine, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    line = json.dumps({"msg_id": "m", "operation": operation, "payload": payload})
    return json.loads(engine.handle_line(line))


# --- seeding + read-path through handle_line ----------------------------
def test_register_seed_returns_build_result(engine: CuaEngine) -> None:
    result = engine.register_seed(_window_with_button(epoch=1))
    assert result["status"] == "success"
    assert result["epoch"] == 1
    assert result["total_elements"] == 3
    assert result["snapshot_id"] >= 1
    assert 0 in result["root_elements"]


def test_get_element_through_handle_line_carries_semantics(engine: CuaEngine) -> None:
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    resp = _call(engine, "get_element", {"snapshot_id": sid, "row_id": 1})
    assert resp["error"] is None
    element = resp["payload"]["element"]
    assert element["row_id"] == 1
    assert element["name"] == "Save"
    concepts = {c["domain_concept"] for c in element["semantics"]}
    assert "action_button" in concepts


def test_load_children_through_handle_line(engine: CuaEngine) -> None:
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    resp = _call(engine, "load_children", {"snapshot_id": sid, "parent_row_id": 0})
    assert resp["error"] is None
    children = resp["payload"]["children"]
    assert [c["row_id"] for c in children] == [1, 2]


def test_get_element_unknown_row_returns_typed_error(engine: CuaEngine) -> None:
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    resp = _call(engine, "get_element", {"snapshot_id": sid, "row_id": 999})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1002  # ELEMENT_NOT_FOUND


# --- diff across two seeded epochs --------------------------------------
def test_snapshot_diff_across_epochs(engine: CuaEngine) -> None:
    engine.register_seed(_window_with_button(epoch=1, button_name="Save"))
    engine.register_seed(_window_with_button(epoch=2, button_name="Saved"))
    resp = _call(engine, "get_snapshot_diff", {"from_epoch": 1, "to_epoch": 2})
    assert resp["error"] is None
    payload = resp["payload"]
    # The renamed button drops its old identity and gains a new one.
    assert payload["added_row_ids"] == [1]
    assert payload["removed_row_ids"] == [1]


def test_snapshot_diff_unknown_epoch_raises_snapshot_not_found(
    engine: CuaEngine,
) -> None:
    engine.register_seed(_window_with_button(epoch=1))
    resp = _call(engine, "get_snapshot_diff", {"from_epoch": 1, "to_epoch": 42})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1001  # SNAPSHOT_NOT_FOUND


# --- live paths fail cleanly when a backend is unavailable (no crash) ----
def test_build_matrix_unavailable_backend_returns_clean_typed_error(
    engine: CuaEngine,
) -> None:
    # Force the UIA backend, which cannot run on this Linux host: we must get a
    # clean typed error (1006), never a bare ImportError or a segfault.
    resp = _call(
        engine,
        "build_matrix",
        {"target": {"exe_regex": "notepad"}, "capture_backend": "uia"},
    )
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006  # capture backend unavailable
    assert resp["error"]["details"]["reason"] == "capture_unavailable"


def test_invoke_action_unreacquirable_returns_clean_typed_error(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # Force a usable AT-SPI backend whose reacquire fails: must surface as a clean
    # typed 1006, never a crash. (The seeded element carries no atspi_path.)
    import cerebellum_cua.capture as cap

    class _StubBackend:
        def reacquire(self, identity: dict[str, Any]) -> None:
            return None

    monkeypatch.setattr(cap, "get_capture_backend", lambda kind="auto": _StubBackend())
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    resp = _call(engine, "invoke_action", {"snapshot_id": sid, "row_id": 1})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006
    assert resp["error"]["details"]["reason"] == "reacquire_failed"


def test_invoke_action_routes_to_backend_invoke(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    import cerebellum_cua.capture as cap

    seen: dict[str, Any] = {}

    class _Live:
        pass

    class _StubBackend:
        def reacquire(self, identity: dict[str, Any]) -> Any:
            seen["identity"] = identity
            return _Live()

        def invoke(self, live: Any, action: str, **params: Any) -> bool:
            seen["action"] = action
            seen["params"] = params
            return True

    monkeypatch.setattr(cap, "get_capture_backend", lambda kind="auto": _StubBackend())
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    resp = _call(
        engine,
        "invoke_action",
        {"snapshot_id": sid, "row_id": 1, "action": "set_text", "value": "hi"},
    )
    assert resp["error"] is None
    assert resp["payload"]["success"] is True
    assert resp["payload"]["action"] == "set_text"
    assert resp["payload"]["affected_rows"] == [1]
    assert seen["action"] == "set_text"
    assert seen["params"] == {"value": "hi"}
    assert "name" in seen["identity"]


def test_invoke_action_click_point_uses_synthetic_input(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    import cerebellum_cua.capture.input as inp

    clicks: list[tuple[int, int, str, bool]] = []

    def _fake_click(
        self: Any, x: int, y: int, button: str = "left", double: bool = False,
        abort: Any = None,
    ) -> bool:
        clicks.append((x, y, button, double))
        return True

    monkeypatch.setattr(inp.SyntheticInput, "click", _fake_click)
    resp = _call(
        engine, "invoke_action", {"action": "click_point", "x": 12, "y": 34}
    )
    assert resp["error"] is None
    assert resp["payload"] == {"success": True, "action": "click_point"}
    assert clicks == [(12, 34, "left", False)]


def test_invoke_action_synthetic_unavailable_returns_typed_error(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    import cerebellum_cua.capture.input as inp

    def _boom(self: Any, *a: Any, **k: Any) -> bool:
        raise inp.SyntheticInputError("no ydotool")

    monkeypatch.setattr(inp.SyntheticInput, "key", _boom)
    resp = _call(engine, "invoke_action", {"action": "key", "value": "ctrl+s"})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006
    assert resp["error"]["details"]["reason"] == "synthetic_input_unavailable"


# --- protocol contract ---------------------------------------------------
def test_unknown_operation_yields_9999(engine: CuaEngine) -> None:
    resp = _call(engine, "frobnicate", {})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 9999
    assert resp["error"]["message"] == "UNKNOWN_OPERATION"


def test_handle_line_malformed_json_yields_error(engine: CuaEngine) -> None:
    resp = json.loads(engine.handle_line("{not json"))
    assert resp["error"]["code"] == 9999
    assert resp["error"]["message"] == "MALFORMED_REQUEST"


# --- engine wiring -------------------------------------------------------
def test_handlers_dict_exposes_five_operations(engine: CuaEngine) -> None:
    assert set(engine.handlers) == {
        "build_matrix",
        "get_element",
        "load_children",
        "invoke_action",
        "get_snapshot_diff",
    }


def test_engine_is_context_manager() -> None:
    with CuaEngine(db_dsn=None, secret=SECRET) as eng:
        result = eng.register_seed(_window_with_button(epoch=1))
        assert result["status"] == "success"
