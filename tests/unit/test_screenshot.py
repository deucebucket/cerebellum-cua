"""Unit tests for on-demand screenshot capture — no real display/ffmpeg/grim.

``shutil.which`` and ``subprocess.run`` are monkeypatched to simulate each
grabber and a crafted PNG header is written so dimension parsing is exercised
without any image library. The engine ``screenshot`` operation is driven through
``handle_line`` with the grab mocked.
"""

from __future__ import annotations

import json
import struct
from typing import Any

import pytest

from cerebellum_cua.capture import screenshot as shot
from cerebellum_cua.capture.screenshot import ScreenshotError, grab_screenshot
from cerebellum_cua.cli import CuaEngine

SECRET = "unit-test-secret"


def _write_png(path: str, width: int, height: int) -> None:
    """Write a minimal valid-enough PNG (signature + IHDR width/height)."""
    with open(path, "wb") as fh:
        fh.write(shot._PNG_SIGNATURE)
        fh.write(struct.pack(">I", 13))  # IHDR length
        fh.write(b"IHDR")
        fh.write(struct.pack(">II", width, height))
        fh.write(b"\x08\x02\x00\x00\x00")  # bit depth/color/etc. (ignored)


def _patch_tools(monkeypatch: Any, present: set[str]) -> list[list[str]]:
    """Make only ``present`` tools resolvable; record argv of each run call."""
    calls: list[list[str]] = []

    monkeypatch.setattr(
        shot.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in present else None,
    )

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))
        # The last argument is always the destination PNG path.
        _write_png(argv[-1], 1920, 1080)

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr(shot.subprocess, "run", _fake_run)
    return calls


def test_x11_prefers_ffmpeg(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    calls = _patch_tools(monkeypatch, present={"ffmpeg", "import", "scrot"})
    out = grab_screenshot(str(tmp_path / "s.png"))
    assert out["width"] == 1920
    assert out["height"] == 1080
    assert calls[0][0] == "ffmpeg"
    assert "x11grab" in calls[0]
    assert ":0" in calls[0]


def test_x11_falls_back_to_import(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("DISPLAY", raising=False)
    calls = _patch_tools(monkeypatch, present={"import", "scrot"})
    out = grab_screenshot(str(tmp_path / "s.png"), display=":1")
    assert out["width"] == 1920
    assert calls[0][:3] == ["import", "-window", "root"]


def test_x11_display_override_used(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    calls = _patch_tools(monkeypatch, present={"ffmpeg"})
    grab_screenshot(str(tmp_path / "s.png"), display=":7")
    assert ":7" in calls[0]


def test_wayland_prefers_grim(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    calls = _patch_tools(monkeypatch, present={"grim", "spectacle"})
    out = grab_screenshot(str(tmp_path / "s.png"))
    assert calls[0][0] == "grim"
    assert out["height"] == 1080


def test_wayland_falls_back_to_spectacle(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    calls = _patch_tools(monkeypatch, present={"spectacle"})
    grab_screenshot(str(tmp_path / "s.png"))
    assert calls[0][0] == "spectacle"
    assert calls[0][1:4] == ["-b", "-n", "-o"]


def test_no_tool_raises(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    _patch_tools(monkeypatch, present=set())
    with pytest.raises(ScreenshotError):
        grab_screenshot(str(tmp_path / "s.png"))


def test_failing_tool_tries_next(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(
        shot.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "scrot"} else None,
    )
    calls: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))

        class _Result:
            stdout = ""
            stderr = "boom"

        r = _Result()
        # ffmpeg fails, scrot succeeds.
        if argv[0] == "ffmpeg":
            r.returncode = 1  # type: ignore[attr-defined]
        else:
            r.returncode = 0  # type: ignore[attr-defined]
            _write_png(argv[-1], 800, 600)
        return r

    monkeypatch.setattr(shot.subprocess, "run", _fake_run)
    out = grab_screenshot(str(tmp_path / "s.png"))
    assert [c[0] for c in calls] == ["ffmpeg", "scrot"]
    assert out["width"] == 800


def test_bad_png_raises(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(shot.shutil, "which", lambda name: "/usr/bin/scrot")

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        with open(argv[-1], "wb") as fh:
            fh.write(b"not a png")

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr(shot.subprocess, "run", _fake_run)
    with pytest.raises(ScreenshotError):
        grab_screenshot(str(tmp_path / "s.png"))


def test_png_dimensions_parsing(tmp_path: Any) -> None:
    p = tmp_path / "dim.png"
    _write_png(str(p), 640, 480)
    assert shot._png_dimensions(str(p)) == (640, 480)


# --- engine operation through handle_line --------------------------------
def test_engine_screenshot_operation(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(
        shot, "grab_screenshot",
        lambda path, display=None: {"path": path, "width": 100, "height": 50},
    )
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        target = str(tmp_path / "shot.png")
        line = json.dumps(
            {"msg_id": "m", "operation": "screenshot", "payload": {"path": target}}
        )
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["error"] is None
    assert resp["payload"] == {"path": target, "width": 100, "height": 50}


def test_engine_screenshot_unavailable_returns_typed_error(
    monkeypatch: Any,
) -> None:
    def _boom(path: str, display: str | None = None) -> dict:
        raise ScreenshotError("no grabber")

    monkeypatch.setattr(shot, "grab_screenshot", _boom)
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        line = json.dumps(
            {"msg_id": "m", "operation": "screenshot", "payload": {}}
        )
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006
    assert resp["error"]["details"]["reason"] == "screenshot_unavailable"
