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


# --- read_text aggregation ----------------------------------------------
def _screen_with_text(epoch: int) -> Snapshot:
    """root window(0) -> [terminal text(1, text_content), label(2, name only)]."""
    walked = [
        (_elem("Konsole", 50032), 0, None),
        (
            _elem(
                "buffer",
                50020,
                properties={"text_content": "$ ls\nfile.txt"},
                bounding_rect={"left": 5, "top": 30, "width": 600, "height": 400},
            ),
            1,
            0,
        ),
        (
            _elem(
                "Status: ready",
                50020,
                bounding_rect={"left": 5, "top": 440, "width": 200, "height": 18},
            ),
            1,
            0,
        ),
    ]
    return build_snapshot(walked, epoch=epoch)


def test_read_text_returns_texts_with_bboxes(engine: CuaEngine) -> None:
    sid = engine.register_seed(_screen_with_text(epoch=1))["snapshot_id"]
    resp = _call(engine, "read_text", {"snapshot_id": sid})
    assert resp["error"] is None
    payload = resp["payload"]
    by_row = {t["row_id"]: t for t in payload["texts"]}
    # row 1 prefers text_content over its name "buffer".
    assert by_row[1]["text"] == "$ ls\nfile.txt"
    assert by_row[1]["bbox"] == [5, 30, 600, 400]
    # row 2 has no text_content, so its name is used.
    assert by_row[2]["text"] == "Status: ready"
    assert by_row[2]["bbox"] == [5, 440, 200, 18]
    # the root window has a name too, so count includes it.
    assert payload["count"] == 3


def test_read_text_defaults_to_latest_snapshot(engine: CuaEngine) -> None:
    engine.register_seed(_screen_with_text(epoch=1))
    engine.register_seed(_screen_with_text(epoch=2))
    resp = _call(engine, "read_text", {})
    assert resp["error"] is None
    # latest snapshot still has its three texts.
    assert resp["payload"]["count"] == 3


def test_read_text_empty_when_nothing_has_text(engine: CuaEngine) -> None:
    walked = [
        (_elem("", 50026, bounding_rect={"left": 0, "top": 0, "width": 10, "height": 10}), 0, None),
    ]
    sid = engine.register_seed(build_snapshot(walked, epoch=1))["snapshot_id"]
    resp = _call(engine, "read_text", {"snapshot_id": sid})
    assert resp["error"] is None
    assert resp["payload"]["texts"] == []
    assert resp["payload"]["count"] == 0


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


def _patch_backends(monkeypatch: Any, *, vision_ok: bool) -> None:
    """Stub the capture seam: the OS-default backend always fails; vision is
    available iff ``vision_ok`` and yields a single element when used."""
    import cerebellum_cua.capture as cap
    from cerebellum_cua.capture.base import CapturedElement, CaptureNotAvailable
    from cerebellum_cua.model import BoundingRect, ControlType

    class _FailBackend:
        name = "atspi"

        def is_available(self) -> bool:
            return False

        def iter_tree(self, target: Any, config: Any) -> Any:
            raise CaptureNotAvailable("a11y bus unreachable")
            yield  # pragma: no cover - marks this a generator

    class _VisionStub:
        name = "vision"

        def is_available(self) -> bool:
            return vision_ok

        def iter_tree(self, target: Any, config: Any) -> Any:
            yield (
                CapturedElement(
                    control_type=int(ControlType.BUTTON),
                    name="OK",
                    bounding_rect=BoundingRect(left=0, top=0, width=10, height=10),
                ),
                0,
                None,
            )

    def _get(kind: str = "auto") -> Any:
        return _VisionStub() if kind == "vision" else _FailBackend()

    monkeypatch.setattr(cap, "get_capture_backend", _get)


def test_build_matrix_auto_degrades_to_vision_when_a11y_bus_down(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # auto mode + unreachable a11y bus, but vision is available -> the primary op
    # must succeed via the vision backend rather than hard-failing (issue #50).
    _patch_backends(monkeypatch, vision_ok=True)
    resp = _call(engine, "build_matrix", {"target": {}})
    assert resp["error"] is None
    assert resp["payload"]["status"] == "success"
    assert resp["payload"]["capture_backend"] == "vision"
    assert resp["payload"]["degraded"] is True
    assert resp["payload"]["total_elements"] == 1


def test_build_matrix_auto_no_fallback_gives_actionable_error(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # auto mode, neither backend usable -> a clean 1006 whose message states the
    # exact remediation for BOTH the a11y bus and the vision backend.
    _patch_backends(monkeypatch, vision_ok=False)
    resp = _call(engine, "build_matrix", {"target": {}})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006
    assert resp["error"]["details"]["reason"] == "capture_unavailable"
    detail = resp["error"]["details"]["detail"].lower()
    assert "org.a11y.bus" in detail
    assert "vision" in detail


def test_build_matrix_explicit_backend_does_not_degrade(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # A pinned backend must NOT silently switch to vision: the caller asked for a
    # specific data shape, so an unavailable pinned backend stays an error.
    _patch_backends(monkeypatch, vision_ok=True)
    resp = _call(engine, "build_matrix", {"capture_backend": "atspi"})
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006


def _patch_empty_atspi(monkeypatch: Any, diagnostics: dict[str, Any]) -> None:
    """Stub a *successful* atspi capture that yields zero elements, reporting the
    given registry/root diagnostics (the 'bus reachable but empty' case)."""
    import cerebellum_cua.capture as cap

    class _EmptyAtspi:
        name = "atspi"

        def is_available(self) -> bool:
            return True

        def iter_tree(self, target: Any, config: Any) -> Any:
            return iter(())

        def last_capture_diagnostics(self) -> dict[str, Any]:
            return diagnostics

    monkeypatch.setattr(cap, "get_capture_backend", lambda kind="auto": _EmptyAtspi())


def test_empty_atspi_capture_flags_registry_empty(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # Bus reachable but the a11y registry exposes 0 apps: a 0-element capture must
    # be flagged as empty-with-cause, not pass as a bare silent success.
    _patch_empty_atspi(monkeypatch, {"registry_app_count": 0, "matched_root_count": 0})
    resp = _call(engine, "build_matrix", {"target": {}})
    assert resp["error"] is None
    p = resp["payload"]
    assert p["total_elements"] == 0
    assert p["status"] == "success"
    assert p["diagnostics"]["empty"] is True
    assert p["diagnostics"]["reason"] == "atspi_registry_empty"
    assert "QT_ACCESSIBILITY" in p["diagnostics"]["hint"]


def test_empty_atspi_capture_flags_target_mismatch(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # Apps exist on the registry but none matched the requested target.
    _patch_empty_atspi(monkeypatch, {"registry_app_count": 3, "matched_root_count": 0})
    resp = _call(engine, "build_matrix", {"target": {"app_name": "Nope"}})
    p = resp["payload"]
    assert p["diagnostics"]["reason"] == "no_root_matched_target"


def test_empty_atspi_capture_flags_all_filtered(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # Roots matched but produced no elements (no accessible content / over-filter).
    _patch_empty_atspi(monkeypatch, {"registry_app_count": 1, "matched_root_count": 1})
    resp = _call(engine, "build_matrix", {"target": {}})
    p = resp["payload"]
    assert p["diagnostics"]["reason"] == "all_elements_filtered"


def test_nonempty_capture_has_no_diagnostics(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # A capture that yields elements must NOT carry an empty-capture diagnostic.
    _patch_backends(monkeypatch, vision_ok=True)
    resp = _call(engine, "build_matrix", {"target": {}})  # degrades to vision -> 1 elem
    assert resp["payload"]["total_elements"] == 1
    assert "diagnostics" not in resp["payload"]


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


def _stub_invoke_backend(monkeypatch: Any) -> None:
    """Patch the capture seam so element actions re-acquire + invoke cleanly."""
    import cerebellum_cua.capture as cap

    class _StubBackend:
        def reacquire(self, identity: dict[str, Any]) -> Any:
            return object()

        def invoke(self, live: Any, action: str, **params: Any) -> bool:
            return True

    monkeypatch.setattr(cap, "get_capture_backend", lambda kind="auto": _StubBackend())


def test_visible_cursor_glides_to_element_center(monkeypatch: Any) -> None:
    import cerebellum_cua.capture.input as inp

    moves: list[tuple[int, int]] = []

    def _fake_move(self: Any, x: int, y: int, abort: Any = None) -> bool:
        moves.append((x, y))
        return True

    monkeypatch.setattr(inp.SyntheticInput, "move", _fake_move)
    _stub_invoke_backend(monkeypatch)
    eng = CuaEngine(db_dsn=None, secret=SECRET, visible_cursor=True)
    try:
        sid = eng.register_seed(_window_with_button(epoch=1))["snapshot_id"]
        resp = _call(eng, "invoke_action", {"snapshot_id": sid, "row_id": 1})
    finally:
        eng.close()
    assert resp["error"] is None
    assert resp["payload"]["success"] is True
    # The seeded element rect is left=0, top=0, width=40, height=20 -> center 20,10.
    assert moves == [(20, 10)]


def test_visible_cursor_off_does_not_move(monkeypatch: Any) -> None:
    import cerebellum_cua.capture.input as inp

    moves: list[tuple[int, int]] = []
    monkeypatch.setattr(
        inp.SyntheticInput, "move",
        lambda self, x, y, abort=None: moves.append((x, y)) or True,
    )
    _stub_invoke_backend(monkeypatch)
    eng = CuaEngine(db_dsn=None, secret=SECRET, visible_cursor=False)
    try:
        sid = eng.register_seed(_window_with_button(epoch=1))["snapshot_id"]
        resp = _call(eng, "invoke_action", {"snapshot_id": sid, "row_id": 1})
    finally:
        eng.close()
    assert resp["payload"]["success"] is True
    assert moves == []


def test_visible_cursor_glide_failure_does_not_break_action(
    monkeypatch: Any,
) -> None:
    import cerebellum_cua.capture.input as inp

    def _boom(self: Any, *a: Any, **k: Any) -> bool:
        raise RuntimeError("no display / no ydotool")

    monkeypatch.setattr(inp.SyntheticInput, "move", _boom)
    _stub_invoke_backend(monkeypatch)
    eng = CuaEngine(db_dsn=None, secret=SECRET, visible_cursor=True)
    try:
        sid = eng.register_seed(_window_with_button(epoch=1))["snapshot_id"]
        resp = _call(eng, "invoke_action", {"snapshot_id": sid, "row_id": 1})
    finally:
        eng.close()
    assert resp["error"] is None
    assert resp["payload"]["success"] is True


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
def test_handlers_dict_exposes_operations(engine: CuaEngine) -> None:
    assert set(engine.handlers) == {
        "build_matrix",
        "get_element",
        "load_children",
        "invoke_action",
        "get_snapshot_diff",
        "screenshot",
        "read_text",
        "run_skill",
        "list_windows",
        "read_legend",
        "annotate",
        "wireframe",
        "elevate",
    }


def test_engine_is_context_manager() -> None:
    with CuaEngine(db_dsn=None, secret=SECRET) as eng:
        result = eng.register_seed(_window_with_button(epoch=1))
        assert result["status"] == "success"


def test_screenshot_row_id_crops_to_element_bbox(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    import cerebellum_cua.capture.screenshot as shot

    seen: dict[str, Any] = {}

    def _fake_grab(path: str, display: Any = None, region: Any = None) -> dict:
        seen["region"] = region
        return {"path": path, "width": 40, "height": 20,
                "region": list(region) if region else None, "region_applied": True}

    monkeypatch.setattr(shot, "grab_screenshot", _fake_grab)
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    # row 1 is the button; _elem default bbox is left:0 top:0 width:40 height:20.
    resp = _call(engine, "screenshot", {"snapshot_id": sid, "row_id": 1})
    assert resp["error"] is None
    assert seen["region"] == (0, 0, 40, 20)
    assert resp["payload"]["region"] == [0, 0, 40, 20]


def test_screenshot_explicit_region_passed_through(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    import cerebellum_cua.capture.screenshot as shot

    seen: dict[str, Any] = {}

    def _fake_grab(path: str, display: Any = None, region: Any = None) -> dict:
        seen["region"] = region
        return {"path": path, "width": 5, "height": 6, "region": list(region),
                "region_applied": True}

    monkeypatch.setattr(shot, "grab_screenshot", _fake_grab)
    resp = _call(engine, "screenshot", {"region": [3, 4, 5, 6]})
    assert resp["error"] is None
    assert seen["region"] == (3, 4, 5, 6)
