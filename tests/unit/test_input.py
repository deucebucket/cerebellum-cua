"""Unit tests for the synthetic-input fallback — no real ydotool, no XTEST.

The Atspi route is forced off and the ``ydotool`` subprocess is monkeypatched so
the exact argv built for click/type/key is asserted, plus the clean typed error
raised when no input method is available.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from cerebellum_cua.capture.abort import AbortedByUser
from cerebellum_cua.capture.input import SyntheticInput, SyntheticInputError


class _Recorder:
    """Captures ydotool argv lists and reports a chosen exit code."""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def run(self, args: list[str], **kwargs: Any) -> Any:
        self.calls.append(list(args))

        class _Result:
            pass

        r = _Result()
        r.returncode = self.returncode  # type: ignore[attr-defined]
        r.stdout = ""  # type: ignore[attr-defined]
        r.stderr = "boom"  # type: ignore[attr-defined]
        return r


def _ydotool_input(monkeypatch: Any, rec: _Recorder, present: bool = True) -> SyntheticInput:
    import cerebellum_cua.capture.input as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/ydotool" if present else None)
    monkeypatch.setattr(mod.subprocess, "run", rec.run)
    # Force the ydotool route (skip Atspi entirely). "instant" keeps the argv
    # shape one-move-then-click for these backend tests; motion is covered in
    # test_motion.py.
    return SyntheticInput(prefer_ydotool=True, speed="instant")


def test_click_builds_mousemove_then_click(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    assert si.click(100, 200) is True
    assert rec.calls[0] == [
        "ydotool", "mousemove", "--absolute", "-x", "100", "-y", "200",
    ]
    assert rec.calls[1] == ["ydotool", "click", "0xC0"]


def test_double_click_repeats(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    si.click(5, 5, double=True)
    assert rec.calls[1] == ["ydotool", "click", "--repeat", "2", "0xC0"]


def test_right_click_uses_right_code(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    si.click(1, 1, button="right")
    assert rec.calls[1] == ["ydotool", "click", "0xC1"]


def test_type_text_builds_type_call(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    assert si.type_text("hi there") is True
    assert rec.calls[0] == ["ydotool", "type", "--", "hi there"]


def test_key_combo_builds_press_release(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    assert si.key("ctrl+s") is True
    # ctrl=29, s=31: press 29,31 then release 31,29.
    assert rec.calls[0] == ["ydotool", "key", "29:1", "31:1", "31:0", "29:0"]


def test_unmappable_key_combo_raises(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    with pytest.raises(SyntheticInputError):
        si.key("ctrl+somethingweird")


def test_no_input_method_raises_clean_error(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec, present=False)
    with pytest.raises(SyntheticInputError) as exc:
        si.click(1, 1)
    assert "ydotool" in str(exc.value)


def test_nonzero_exit_raises(monkeypatch: Any) -> None:
    rec = _Recorder(returncode=1)
    si = _ydotool_input(monkeypatch, rec)
    with pytest.raises(SyntheticInputError):
        si.type_text("x")


def test_drag_emits_press_glide_release_in_order(monkeypatch: Any) -> None:
    """Instant drag: move-to-start, press, move-to-end, release — that order."""
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    assert si.drag(10, 20, 110, 220) is True
    assert rec.calls == [
        ["ydotool", "mousemove", "--absolute", "-x", "10", "-y", "20"],   # to start
        ["ydotool", "click", "0x40"],                                     # press left
        ["ydotool", "mousemove", "--absolute", "-x", "110", "-y", "220"],  # to end
        ["ydotool", "click", "0x00"],                                     # release left
    ]


def test_drag_right_button_uses_right_codes(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    si.drag(0, 0, 5, 5, button="right")
    assert rec.calls[1] == ["ydotool", "click", "0x41"]   # right press
    assert rec.calls[3] == ["ydotool", "click", "0x01"]   # right release


def test_human_drag_glides_with_many_moves(monkeypatch: Any) -> None:
    """Human drag glides both segments: more than the 4 instant calls."""
    rec = _Recorder()
    import cerebellum_cua.capture.input as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _n: "/usr/bin/ydotool")
    monkeypatch.setattr(mod.subprocess, "run", rec.run)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    si = SyntheticInput(prefer_ydotool=True, speed="human", steps=8)
    assert si.drag(0, 0, 100, 0) is True
    moves = [c for c in rec.calls if c[1] == "mousemove" and "--absolute" in c]
    # 8 glide steps to start + 8 to end (well above the 2 instant moves).
    assert len(moves) == 16
    presses = [c for c in rec.calls if c[:2] == ["ydotool", "click"]]
    assert presses == [["ydotool", "click", "0x40"], ["ydotool", "click", "0x00"]]


def test_scroll_emits_wheel_down(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    assert si.scroll(50, 60, dy=3) is True
    assert rec.calls[0] == ["ydotool", "mousemove", "--absolute", "-x", "50", "-y", "60"]
    # positive dy = down; one wheel event with y=3.
    assert rec.calls[1] == ["ydotool", "mousemove", "--wheel", "-x", "0", "-y", "3"]


def test_scroll_up_is_negative_dy(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    si.scroll(0, 0, dy=-2)
    assert rec.calls[1] == ["ydotool", "mousemove", "--wheel", "-x", "0", "-y", "-2"]


def test_scroll_horizontal_axis(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _ydotool_input(monkeypatch, rec)
    si.scroll(0, 0, dx=4)
    assert rec.calls[1] == ["ydotool", "mousemove", "--wheel", "-x", "4", "-y", "0"]


def test_abort_mid_drag_releases_and_raises(monkeypatch: Any) -> None:
    """An abort during the held glide releases the button, then raises."""
    si = SyntheticInput(prefer_ydotool=True, speed="human", steps=30)
    abort = threading.Event()
    events: list[str] = []

    def _move(x: int, y: int) -> bool:
        events.append(f"move:{x},{y}")
        # Trip the abort partway through the *second* (held) glide.
        if len([e for e in events if e.startswith("move")]) == 33:
            abort.set()
        return True

    monkeypatch.setattr(si, "_move_abs", _move)
    monkeypatch.setattr(si, "_press", lambda x, y, b: events.append("press") or True)
    monkeypatch.setattr(si, "_release", lambda x, y, b: events.append("release") or True)
    import cerebellum_cua.capture.input as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    with pytest.raises(AbortedByUser):
        si.drag(0, 0, 500, 500, abort=abort)
    # Button was pressed, then released on abort (never left held), then raised.
    assert "press" in events
    assert "release" in events
    assert events.index("press") < events.index("release")


def test_atspi_route_used_when_available(monkeypatch: Any) -> None:
    """When not preferring ydotool and Atspi.generate_mouse_event works, use it."""
    import cerebellum_cua.capture.input as mod

    sent: list[tuple[int, int, str]] = []

    class _FakeAtspi:
        @staticmethod
        def generate_mouse_event(x: int, y: int, sync: str) -> None:
            sent.append((x, y, sync))

    monkeypatch.setattr(SyntheticInput, "_atspi", staticmethod(lambda: _FakeAtspi))
    # ydotool absent: if Atspi were skipped, this would raise.
    monkeypatch.setattr(mod.shutil, "which", lambda _n: None)
    # instant: one absolute move ("abs") then one atomic click ("b1c"), no
    # glide path and no press/release decomposition.
    si = SyntheticInput(prefer_ydotool=False, speed="instant")
    assert si.click(7, 8) is True
    assert sent == [(7, 8, "abs"), (7, 8, "b1c")]


# --- #54: coordinate/raw input must not touch AT-SPI by default ---------------
def test_default_coordinate_click_never_uses_atspi(monkeypatch: Any) -> None:
    # Atspi.generate_*_event aborts the PROCESS (uncatchable C dbind abort) when
    # the a11y registry is broken. By default, synthetic input must NOT go there.
    import cerebellum_cua.capture.input as mod

    monkeypatch.delenv("CEREBELLUM_ATSPI_INPUT", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")  # non-Wayland: old code used Atspi
    rec = _Recorder()
    monkeypatch.setattr(mod.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(mod.subprocess, "run", rec.run)

    def _boom(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("AT-SPI input must not be used by default (#54)")

    monkeypatch.setattr(SyntheticInput, "_atspi_click", _boom)
    monkeypatch.setattr(SyntheticInput, "_atspi_move", _boom)
    si = SyntheticInput(speed="instant")
    assert si.click(10, 20) is True
    assert rec.calls  # a CLI tool was invoked instead


def test_atspi_input_is_opt_in_via_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("CEREBELLUM_ATSPI_INPUT", "1")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    si = SyntheticInput(speed="instant")
    assert si._prefer_ydotool is False  # Atspi route allowed when opted in


# --- #54 part 2: xdotool X11 CLI path (no AT-SPI, no daemon) -------------------
def _xdotool_only(monkeypatch: Any, rec: _Recorder) -> SyntheticInput:
    import cerebellum_cua.capture.input as mod

    monkeypatch.delenv("CEREBELLUM_ATSPI_INPUT", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(
        mod.shutil, "which", lambda n: "/usr/bin/xdotool" if n == "xdotool" else None)
    monkeypatch.setattr(mod.subprocess, "run", rec.run)
    return SyntheticInput(speed="instant")


def test_xdotool_click_builds_mousemove_then_click(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _xdotool_only(monkeypatch, rec)
    assert si.click(15, 25) is True
    assert ["xdotool", "mousemove", "15", "25"] in rec.calls
    assert ["xdotool", "click", "1"] in rec.calls


def test_xdotool_key_uses_plus_syntax(monkeypatch: Any) -> None:
    rec = _Recorder()
    si = _xdotool_only(monkeypatch, rec)
    assert si.key("ctrl+s") is True
    assert ["xdotool", "key", "ctrl+s"] in rec.calls


def test_no_cli_tool_and_no_atspi_raises_clean_error(monkeypatch: Any) -> None:
    import cerebellum_cua.capture.input as mod
    from cerebellum_cua.capture.input import SyntheticInputError

    monkeypatch.delenv("CEREBELLUM_ATSPI_INPUT", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(mod.shutil, "which", lambda _n: None)  # no tools at all
    si = SyntheticInput(speed="instant")
    with pytest.raises(SyntheticInputError):
        si.click(1, 2)
