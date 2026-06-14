"""Unit tests for the desktop window-state source — no live WM.

Captured ``wmctrl -l -G -p`` / ``xprop`` sample strings are fed into the pure
parsers and the WindowState fields are asserted. Backend selection is driven by
monkeypatching ``shutil.which`` + env, and the ``list_windows`` engine operation
is exercised through ``handle_line`` with the lib-level ``list_windows`` mocked.
"""

from __future__ import annotations

import json
from typing import Any

from cerebellum_cua.cli import CuaEngine
from cerebellum_cua.desktop import _x11
from cerebellum_cua.desktop import windows as win
from cerebellum_cua.desktop.windows import (
    WindowState,
    available,
    list_windows,
    parse_active_window,
    parse_net_wm_state,
    parse_wmctrl,
    parse_xdotool_geometry,
)

SECRET = "unit-test-secret"

# Two-window sample: a normal browser + a sticky (desktop -1) panel.
WMCTRL_SAMPLE = (
    "0x03600007  0 12345 100 200 800 600 host Firefox — Mozilla\n"
    "0x01000003 -1 999   0   0  1920 30  host Top Panel\n"
)

NET_WM_STATE_MAXIMIZED = (
    "_NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_VERT, "
    "_NET_WM_STATE_MAXIMIZED_HORZ\n"
)
NET_WM_STATE_FULLSCREEN = "_NET_WM_STATE(ATOM) = _NET_WM_STATE_FULLSCREEN\n"
NET_WM_STATE_EMPTY = "_NET_WM_STATE(ATOM) = \n"
NET_WM_STATE_NONE = "_NET_WM_STATE:  not found.\n"

ACTIVE_WINDOW_SAMPLE = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3600007\n"
ACTIVE_WINDOW_UNSET = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0\n"

XDOTOOL_GEOMETRY = "WINDOW=12345\nX=100\nY=200\nWIDTH=800\nHEIGHT=600\nSCREEN=0\n"


# --- pure parser tests ---------------------------------------------------
def test_parse_wmctrl_fields() -> None:
    rows = parse_wmctrl(WMCTRL_SAMPLE)
    assert len(rows) == 2
    first = rows[0]
    assert first.id == "0x03600007"
    assert first.title == "Firefox — Mozilla"
    assert first.pid == 12345
    assert first.workspace == 0
    assert first.bounds.left == 100
    assert first.bounds.top == 200
    assert first.bounds.width == 800
    assert first.bounds.height == 600


def test_parse_wmctrl_sticky_workspace_is_none() -> None:
    rows = parse_wmctrl(WMCTRL_SAMPLE)
    panel = rows[1]
    assert panel.workspace is None
    assert panel.title == "Top Panel"
    assert panel.pid == 999


def test_parse_wmctrl_skips_garbage_lines() -> None:
    assert parse_wmctrl("not enough cols\n\n") == []


def test_parse_net_wm_state_maximized() -> None:
    assert parse_net_wm_state(NET_WM_STATE_MAXIMIZED) == ["maximized"]


def test_parse_net_wm_state_fullscreen() -> None:
    assert parse_net_wm_state(NET_WM_STATE_FULLSCREEN) == ["fullscreen"]


def test_parse_net_wm_state_empty_and_missing() -> None:
    assert parse_net_wm_state(NET_WM_STATE_EMPTY) == []
    assert parse_net_wm_state(NET_WM_STATE_NONE) == []


def test_parse_active_window() -> None:
    assert parse_active_window(ACTIVE_WINDOW_SAMPLE) == "0x03600007"


def test_parse_active_window_unset_is_none() -> None:
    assert parse_active_window(ACTIVE_WINDOW_UNSET) is None
    assert parse_active_window("no marker here") is None


def test_parse_xdotool_geometry() -> None:
    rect = parse_xdotool_geometry(XDOTOOL_GEOMETRY)
    assert (rect.left, rect.top, rect.width, rect.height) == (100, 200, 800, 600)


# --- backend selection ---------------------------------------------------
def test_available_picks_x11(monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(
        _x11.shutil, "which",
        lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None,
    )
    assert available("auto") == "x11"
    assert available("x11") == "x11"


def test_available_none_when_no_tool(monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(_x11.shutil, "which", lambda name: None)
    assert available("auto") is None
    assert available("x11") is None


def test_available_kwin_and_wlroots_report_none(monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(win.shutil, "which", lambda name: "/usr/bin/gdbus")
    # Even on a KWin session, enumeration is unimplemented -> None (honest).
    assert available("kwin") is None
    assert available("wlroots") is None
    assert available("auto") is None


def test_list_windows_wayland_x11_returns_empty(monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("KDE_FULL_SESSION", raising=False)
    monkeypatch.delenv("KDE_SESSION_VERSION", raising=False)
    assert list_windows("auto") == []


def test_list_windows_kwin_documented_noop() -> None:
    assert list_windows("kwin") == []


def test_list_windows_wlroots_documented_noop() -> None:
    assert list_windows("wlroots") == []


# --- x11 integration via mocked subprocess -------------------------------
def _patch_x11(monkeypatch: Any, outputs: dict[tuple[str, ...], str]) -> None:
    """Make wmctrl/xprop resolvable and route _run by argv prefix to outputs."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(
        _x11.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in {"wmctrl", "xprop"} else None,
    )

    def _fake_run(argv: list[str]) -> str | None:
        for prefix, out in outputs.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return out
        return None

    monkeypatch.setattr(_x11, "_run", _fake_run)


def test_list_x11_merges_state_and_active(monkeypatch: Any) -> None:
    outputs = {
        ("wmctrl", "-l", "-G", "-p"): WMCTRL_SAMPLE,
        ("xprop", "-root", "_NET_ACTIVE_WINDOW"): ACTIVE_WINDOW_SAMPLE,
        ("xprop", "-id", "0x03600007"): NET_WM_STATE_MAXIMIZED,
        ("xprop", "-id", "0x01000003"): NET_WM_STATE_EMPTY,
    }
    _patch_x11(monkeypatch, outputs)
    wins = list_windows("x11")
    assert len(wins) == 2
    active = [w for w in wins if w.active]
    assert len(active) == 1
    assert active[0].id == "0x03600007"
    assert active[0].state == ["maximized"]
    assert wins[1].state == []
    assert wins[1].active is False


def test_list_x11_no_tool_returns_empty(monkeypatch: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(_x11.shutil, "which", lambda name: None)
    assert list_windows("x11") == []


def test_window_state_to_dict_roundtrip() -> None:
    ws = WindowState(id="0x1", title="T", pid=5, active=True, state=["fullscreen"])
    d = ws.to_dict()
    assert d["id"] == "0x1"
    assert d["state"] == ["fullscreen"]
    assert d["active"] is True
    assert set(d["bounds"]) == {"left", "top", "width", "height", "dpi"}


# --- engine operation via handle_line ------------------------------------
def test_engine_list_windows_operation(monkeypatch: Any) -> None:
    sample = [WindowState(id="0x1", title="Editor", pid=42, workspace=0)]
    from cerebellum_cua.desktop import windows as wmod

    monkeypatch.setattr(wmod, "list_windows", lambda backend="auto": sample)
    monkeypatch.setattr(wmod, "available", lambda backend="auto": "x11")
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        line = json.dumps(
            {"msg_id": "m", "operation": "list_windows", "payload": {}}
        )
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["error"] is None
    body = resp["payload"]
    assert body["count"] == 1
    assert body["backend"] == "x11"
    assert body["windows"][0]["title"] == "Editor"


def test_engine_list_windows_no_backend(monkeypatch: Any) -> None:
    from cerebellum_cua.desktop import windows as wmod

    monkeypatch.setattr(wmod, "list_windows", lambda backend="auto": [])
    monkeypatch.setattr(wmod, "available", lambda backend="auto": None)
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        line = json.dumps(
            {"msg_id": "m", "operation": "list_windows", "payload": {}}
        )
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["error"] is None
    assert resp["payload"] == {"windows": [], "backend": None, "count": 0}
