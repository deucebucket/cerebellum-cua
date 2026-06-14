"""Unit tests for the action-verification loop and new coordinate actions.

No live capture/display: the capture re-acquire+invoke seam and the engine's
``recapture`` are monkeypatched, so these assert the verification fields merged
into the ``invoke_action`` response and that drag/scroll/key route through
``perform_action`` to :class:`SyntheticInput`.
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


def _window(epoch: int, button_name: str = "Save") -> Snapshot:
    walked = [
        (_elem("Main Window", 50032), 0, None),
        (_elem(button_name, 50000, patterns={"invoke": {"supported": True}}), 1, 0),
    ]
    return build_snapshot(walked, epoch=epoch)


def _call(engine: CuaEngine, payload: dict[str, Any]) -> dict[str, Any]:
    line = json.dumps({"msg_id": "m", "operation": "invoke_action", "payload": payload})
    return json.loads(engine.handle_line(line))


def _stub_invoke_backend(monkeypatch: Any) -> None:
    """Patch the capture seam so element actions re-acquire + invoke cleanly."""
    import cerebellum_cua.capture as cap

    class _StubBackend:
        def reacquire(self, identity: dict[str, Any]) -> Any:
            return object()

        def invoke(self, live: Any, action: str, **params: Any) -> bool:
            return True

    monkeypatch.setattr(cap, "get_capture_backend", lambda kind="auto": _StubBackend())


@pytest.fixture()
def engine() -> Any:
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    yield eng
    eng.close()


# --- verification: change observed ---------------------------------------
def test_verify_true_reports_observed_change(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    _stub_invoke_backend(monkeypatch)
    sid = engine.register_seed(_window(epoch=1, button_name="Save"))["snapshot_id"]
    # recapture returns a tree where the button was renamed -> a real diff.
    monkeypatch.setattr(engine, "recapture", lambda: _window(epoch=2, button_name="Saved"))
    resp = _call(engine, {"snapshot_id": sid, "row_id": 1, "verify": True})
    payload = resp["payload"]
    assert payload["success"] is True
    assert payload["verified"] is True
    assert payload["effect"] == "changed"
    obs = payload["observed_change"]
    # Renamed button drops its old identity (removed) and gains a new one (added).
    assert obs["added_row_ids"] == [1]
    assert obs["removed_row_ids"] == [1]
    # observed_change carries only id lists, no full element patches.
    assert set(obs) == {"added_row_ids", "removed_row_ids", "modified_row_ids"}


# --- verification: no change (no exception) ------------------------------
def test_verify_no_change_reports_no_change_without_raising(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    _stub_invoke_backend(monkeypatch)
    sid = engine.register_seed(_window(epoch=1))["snapshot_id"]
    # recapture returns an identical tree -> empty diff.
    monkeypatch.setattr(engine, "recapture", lambda: _window(epoch=2))
    resp = _call(engine, {"snapshot_id": sid, "row_id": 1, "verify": True})
    payload = resp["payload"]
    assert resp["error"] is None  # no exception on no-change
    assert payload["success"] is True
    assert payload["verified"] is False
    assert payload["effect"] == "no_change"
    assert payload["observed_change"]["modified_row_ids"] == []


# --- verification: off by default ----------------------------------------
def test_verify_absent_default_path_unchanged(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    _stub_invoke_backend(monkeypatch)
    called: list[bool] = []
    monkeypatch.setattr(engine, "recapture", lambda: called.append(True) or None)
    sid = engine.register_seed(_window(epoch=1))["snapshot_id"]
    resp = _call(engine, {"snapshot_id": sid, "row_id": 1})  # no verify key
    payload = resp["payload"]
    assert payload["success"] is True
    assert "verified" not in payload
    assert "observed_change" not in payload
    assert called == []  # recapture never invoked when verification is off


def test_verify_false_overrides_engine_default(monkeypatch: Any) -> None:
    _stub_invoke_backend(monkeypatch)
    eng = CuaEngine(db_dsn=None, secret=SECRET, verify_actions=True)
    try:
        monkeypatch.setattr(eng, "recapture", lambda: _window(epoch=2, button_name="X"))
        sid = eng.register_seed(_window(epoch=1))["snapshot_id"]
        resp = _call(eng, {"snapshot_id": sid, "row_id": 1, "verify": False})
    finally:
        eng.close()
    assert "verified" not in resp["payload"]


def test_engine_verify_actions_flag_enables_verification(monkeypatch: Any) -> None:
    _stub_invoke_backend(monkeypatch)
    eng = CuaEngine(db_dsn=None, secret=SECRET, verify_actions=True)
    try:
        monkeypatch.setattr(eng, "recapture", lambda: _window(epoch=2, button_name="X"))
        sid = eng.register_seed(_window(epoch=1))["snapshot_id"]
        resp = _call(eng, {"snapshot_id": sid, "row_id": 1})  # no payload verify
    finally:
        eng.close()
    assert resp["payload"]["verified"] is True


# --- verification: re-capture unavailable -> verified=null ---------------
def test_verify_recapture_unavailable_returns_null_with_reason(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    _stub_invoke_backend(monkeypatch)
    sid = engine.register_seed(_window(epoch=1))["snapshot_id"]
    monkeypatch.setattr(engine, "recapture", lambda: None)  # headless / no backend
    resp = _call(engine, {"snapshot_id": sid, "row_id": 1, "verify": True})
    payload = resp["payload"]
    assert payload["verified"] is None
    assert payload["effect"] == "unknown"
    assert payload["reason"] == "recapture_unavailable"
    assert "observed_change" not in payload


# --- coordinate actions route to SyntheticInput --------------------------
def test_drag_routes_to_synthetic_input(engine: CuaEngine, monkeypatch: Any) -> None:
    import cerebellum_cua.capture.input as inp

    drags: list[tuple[int, int, int, int, str]] = []

    def _fake_drag(
        self: Any, x1: int, y1: int, x2: int, y2: int,
        button: str = "left", abort: Any = None,
    ) -> bool:
        drags.append((x1, y1, x2, y2, button))
        return True

    monkeypatch.setattr(inp.SyntheticInput, "drag", _fake_drag)
    resp = _call(
        engine,
        {"action": "drag", "x": 1, "y": 2, "x2": 3, "y2": 4, "button": "left"},
    )
    assert resp["error"] is None
    assert resp["payload"] == {"success": True, "action": "drag"}
    assert drags == [(1, 2, 3, 4, "left")]


def test_scroll_routes_to_synthetic_input(engine: CuaEngine, monkeypatch: Any) -> None:
    import cerebellum_cua.capture.input as inp

    scrolls: list[tuple[int, int, int, int]] = []

    def _fake_scroll(
        self: Any, x: int, y: int, dx: int = 0, dy: int = 0
    ) -> bool:
        scrolls.append((x, y, dx, dy))
        return True

    monkeypatch.setattr(inp.SyntheticInput, "scroll", _fake_scroll)
    resp = _call(engine, {"action": "scroll", "x": 5, "y": 6, "dy": 3})
    assert resp["error"] is None
    assert resp["payload"] == {"success": True, "action": "scroll"}
    assert scrolls == [(5, 6, 0, 3)]


# --- engine recapture helper --------------------------------------------
def test_recapture_returns_none_without_prior_capture(engine: CuaEngine) -> None:
    # No build_matrix has run, so there is no capture context to replay.
    assert engine.last_capture is None
    assert engine.recapture() is None


def test_recapture_unavailable_backend_returns_none(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    from cerebellum_cua.config import MatrixConfig

    # Force a backend that cannot run here -> recapture degrades to None (no raise).
    engine.record_capture({"exe_regex": "x"}, MatrixConfig(), "uia")
    assert engine.recapture() is None


def test_key_routes_to_synthetic_input(engine: CuaEngine, monkeypatch: Any) -> None:
    import cerebellum_cua.capture.input as inp

    combos: list[str] = []
    monkeypatch.setattr(
        inp.SyntheticInput, "key", lambda self, combo: combos.append(combo) or True
    )
    resp = _call(engine, {"action": "key", "value": "ctrl+s"})
    assert resp["error"] is None
    assert resp["payload"] == {"success": True, "action": "key"}
    assert combos == ["ctrl+s"]
