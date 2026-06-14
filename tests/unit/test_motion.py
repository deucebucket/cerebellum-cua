"""Unit tests for human-visible synthetic motion — no real ydotool/XTEST/sleep.

The absolute-move primitive and ``time.sleep`` are monkeypatched, so these tests
assert the *shape* of the emitted motion: how many interpolated moves a glide
produces, that they follow an ease-in-out curve, that ``instant`` collapses to a
single move, that typing is paced per character, and that an abort event set
mid-glide stops early and raises :class:`AbortedByUser`.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from cerebellum_cua.capture._motion import interpolate_path, smoothstep
from cerebellum_cua.capture.abort import AbortedByUser
from cerebellum_cua.capture.input import SyntheticInput


class _MoveSink:
    """Records absolute moves and counts sleeps; drives SyntheticInput in tests."""

    def __init__(self) -> None:
        self.moves: list[tuple[int, int]] = []
        self.sleeps: list[float] = []

    def install(self, monkeypatch: Any, si: SyntheticInput) -> None:
        # Capture every absolute move and swallow sleeps (deterministic, fast).
        monkeypatch.setattr(
            si, "_move_abs",
            lambda x, y: (self.moves.append((int(x), int(y))) or True),
        )
        import cerebellum_cua.capture.input as mod

        monkeypatch.setattr(mod.time, "sleep", lambda s: self.sleeps.append(s))


# --- pure curve helpers --------------------------------------------------
def test_smoothstep_endpoints_and_midpoint() -> None:
    assert smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0
    assert smoothstep(0.5) == pytest.approx(0.5)
    # Clamped outside [0, 1].
    assert smoothstep(-1.0) == 0.0
    assert smoothstep(2.0) == 1.0


def test_interpolate_path_lands_on_target_and_eases() -> None:
    path = interpolate_path((0, 0), (100, 0), steps=10)
    assert len(path) == 10
    assert path[-1] == (100, 0)
    xs = [p[0] for p in path]
    # Monotonic non-decreasing along the axis of travel.
    assert all(b >= a for a, b in zip(xs, xs[1:], strict=False))
    # Ease-in-out: first segment shorter than a middle segment (starts slow).
    first_seg = xs[0] - 0
    mid_seg = xs[5] - xs[4]
    assert first_seg < mid_seg


def test_interpolate_single_step_is_one_jump() -> None:
    assert interpolate_path((0, 0), (50, 60), steps=1) == [(50, 60)]


# --- human glide ---------------------------------------------------------
def test_human_click_emits_many_interpolated_moves(monkeypatch: Any) -> None:
    sink = _MoveSink()
    si = SyntheticInput(prefer_ydotool=True, speed="human", steps=20)
    sink.install(monkeypatch, si)
    # Stub the press/release so only glide moves land in the sink.
    monkeypatch.setattr(si, "_natural_click", lambda *a, **k: True)

    si.click(300, 400)
    assert len(sink.moves) == 20
    assert sink.moves[-1] == (300, 400)
    # Path is monotonic toward the target (default origin 960,540 -> 300,400,
    # so x decreases; assert non-increasing, i.e. monotone toward the goal).
    xs = [m[0] for m in sink.moves]
    assert xs == sorted(xs, reverse=True)


def test_instant_click_emits_exactly_one_move(monkeypatch: Any) -> None:
    sink = _MoveSink()
    si = SyntheticInput(prefer_ydotool=True, speed="instant")
    sink.install(monkeypatch, si)
    monkeypatch.setattr(si, "_atomic_click", lambda *a, **k: True)

    si.click(11, 22)
    assert sink.moves == [(11, 22)]
    assert sink.sleeps == []  # no pacing in instant mode


def test_human_glide_remembers_last_position(monkeypatch: Any) -> None:
    sink = _MoveSink()
    si = SyntheticInput(prefer_ydotool=True, speed="human", steps=5)
    sink.install(monkeypatch, si)
    monkeypatch.setattr(si, "_natural_click", lambda *a, **k: True)

    si.click(100, 100)
    sink.moves.clear()
    si.click(200, 100)
    # Second glide starts from the first target (100,100), not the origin.
    assert sink.moves[0][0] > 100
    assert sink.moves[-1] == (200, 100)


# --- paced typing --------------------------------------------------------
def test_paced_type_sleeps_per_character(monkeypatch: Any) -> None:
    sink = _MoveSink()
    si = SyntheticInput(prefer_ydotool=False, speed="human", key_delay=0.02)
    sink.install(monkeypatch, si)
    sent: list[str] = []
    # Force the Atspi per-char path.
    monkeypatch.setattr(SyntheticInput, "_atspi", staticmethod(lambda: object()))
    monkeypatch.setattr(
        si, "_atspi_type", lambda ch: (sent.append(ch) or True)
    )

    assert si.type_text("abc") is True
    assert sent == ["a", "b", "c"]
    assert sink.sleeps == [0.02, 0.02, 0.02]


# --- abort mid-motion ----------------------------------------------------
def test_abort_set_mid_glide_raises_and_stops_early(monkeypatch: Any) -> None:
    si = SyntheticInput(prefer_ydotool=True, speed="human", steps=30)
    abort = threading.Event()

    moves: list[tuple[int, int]] = []

    def _move(x: int, y: int) -> bool:
        moves.append((x, y))
        if len(moves) == 3:  # user takes over after a few steps
            abort.set()
        return True

    monkeypatch.setattr(si, "_move_abs", _move)
    import cerebellum_cua.capture.input as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    with pytest.raises(AbortedByUser):
        si.click(500, 500, abort=abort)
    # Stopped right after the abort tripped — nowhere near the full 30 steps.
    assert len(moves) < 30


def test_abort_set_before_typing_raises(monkeypatch: Any) -> None:
    si = SyntheticInput(prefer_ydotool=True, speed="human", key_delay=0.01)
    monkeypatch.setattr(SyntheticInput, "_atspi", staticmethod(lambda: object()))
    monkeypatch.setattr(si, "_atspi_type", lambda ch: True)
    import cerebellum_cua.capture.input as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    abort = threading.Event()
    abort.set()
    with pytest.raises(AbortedByUser):
        si.type_text("hello", abort=abort)
