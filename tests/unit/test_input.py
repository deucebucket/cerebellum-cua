"""Unit tests for the synthetic-input fallback — no real ydotool, no XTEST.

The Atspi route is forced off and the ``ydotool`` subprocess is monkeypatched so
the exact argv built for click/type/key is asserted, plus the clean typed error
raised when no input method is available.
"""

from __future__ import annotations

from typing import Any

import pytest

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
    # Force the ydotool route (skip Atspi entirely).
    return SyntheticInput(prefer_ydotool=True)


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
    si = SyntheticInput(prefer_ydotool=False)
    assert si.click(7, 8) is True
    assert sent == [(7, 8, "b1c")]
