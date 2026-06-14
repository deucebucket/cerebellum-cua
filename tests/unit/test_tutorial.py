"""Unit tests for the tutorial-generation module — no live display/ffmpeg.

The runner is tested against a mock engine whose handlers record their calls and
return canned results, with an injected deterministic clock; the assertions check
the *real* runner logic (ordering, offsets, the failure-keeps-going contract),
not the mock's return values. The caption builder is tested as a pure function on
a sample timeline (enable-interval + escaping), and ``burn_captions`` is tested
with ``shutil.which`` monkeypatched so the typed error surfaces with no ffmpeg.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from cerebellum_cua.tutorial import (
    Tutorial,
    TutorialError,
    TutorialStep,
    build_drawtext_filter,
    burn_captions,
    run_tutorial,
)

# --- spec round-trip -----------------------------------------------------------


def test_spec_round_trip() -> None:
    """Tutorial.to_dict -> from_dict reproduces the same model."""
    tut = Tutorial(
        title="demo",
        steps=[
            TutorialStep(caption="hold", action="pause", hold=1.5),
            TutorialStep(
                caption="type it", action="skill", name="type_into",
                args={"value": "x", "role": "EDIT"}, hold=2.0,
            ),
            TutorialStep(caption="shot", action="op", name="screenshot"),
        ],
    )
    again = Tutorial.from_dict(tut.to_dict())
    assert again == tut
    assert again.steps[1].args == {"value": "x", "role": "EDIT"}


def test_spec_rejects_unknown_action() -> None:
    """An unknown step action is rejected at construction, not at run time."""
    with pytest.raises(ValueError):
        TutorialStep(caption="bad", action="teleport")


def test_example_json_parses() -> None:
    """The shipped example tutorial parses via Tutorial.from_dict."""
    with open("examples/tutorials/gedit_basics.json", encoding="utf-8") as fh:
        tut = Tutorial.from_dict(json.load(fh))
    assert tut.title == "gedit basics"
    assert [s.action for s in tut.steps] == ["pause", "skill", "op"]
    assert tut.steps[1].name == "type_into"


# --- run_tutorial against a mock engine + injected clock -----------------------


class _Clock:
    """A deterministic clock yielding successive preset values."""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = list(ticks)
        self._i = 0

    def __call__(self) -> float:
        value = self._ticks[self._i]
        self._i += 1
        return value


class _MockEngine:
    """Minimal engine stand-in exposing a ``handlers`` dict and call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.handlers = {
            "run_skill": self._run_skill,
            "screenshot": self._screenshot,
            "boom": self._boom,
        }

    def _run_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("run_skill", payload))
        return {"skill": payload["skill"], "success": True, "resolved_row_id": 7}

    def _screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("screenshot", payload))
        return {"path": "/tmp/x.png", "width": 800, "height": 600}

    def _boom(self, _payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kaboom")


def test_run_tutorial_ordered_timeline_with_offsets() -> None:
    """Timeline is ordered, offsets are origin-relative, and dispatch is real."""
    engine = _MockEngine()
    tut = Tutorial(
        title="t",
        steps=[
            TutorialStep(caption="hold", action="pause", hold=2.0),
            TutorialStep(
                caption="type", action="skill", name="type_into",
                args={"value": "hi", "role": "EDIT"}, hold=1.0,
            ),
            TutorialStep(caption="shot", action="op", name="screenshot", hold=1.0),
        ],
    )
    # Two clock reads per step (start, end), plus the origin read.
    clock = _Clock([100.0, 100.0, 102.5, 110.0, 110.5, 120.0, 121.0])
    result = run_tutorial(engine, tut, clock=clock)

    assert result["title"] == "t"
    assert result["success"] is True
    captions = [e["caption"] for e in result["timeline"]]
    assert captions == ["hold", "type", "shot"]

    # Offsets are relative to the origin (100.0) and monotonically ordered.
    starts = [e["start"] for e in result["timeline"]]
    assert starts == [0.0, 10.0, 20.0]
    # The pause's end honors its hold even though the clock barely advanced.
    assert result["timeline"][0]["end"] == 2.5

    # The runner dispatched the real handlers with the real payload shapes.
    assert engine.calls[0] == (
        "run_skill",
        {"skill": "type_into", "args": {"value": "hi", "role": "EDIT"}},
    )
    assert engine.calls[1][0] == "screenshot"


def test_run_tutorial_failing_step_records_not_ok_and_continues() -> None:
    """A raising step is recorded ok=false; later steps still run; success=false."""
    engine = _MockEngine()
    tut = Tutorial(
        title="t",
        steps=[
            TutorialStep(caption="will fail", action="op", name="boom", hold=1.0),
            TutorialStep(caption="still runs", action="op", name="screenshot", hold=1.0),
        ],
    )
    clock = _Clock([0.0, 0.0, 1.0, 5.0, 6.0])
    result = run_tutorial(engine, tut, clock=clock)

    assert result["success"] is False
    assert result["timeline"][0]["ok"] is False
    assert "RuntimeError" in result["timeline"][0]["result_summary"]
    # The run did not crash: the second step ran and is ok.
    assert result["timeline"][1]["ok"] is True
    assert ("screenshot", {}) in engine.calls


def test_run_tutorial_unknown_op_recorded_not_ok() -> None:
    """An op naming a handler that does not exist is a recorded failure."""
    engine = _MockEngine()
    tut = Tutorial(title="t", steps=[
        TutorialStep(caption="nope", action="op", name="does_not_exist"),
    ])
    clock = _Clock([0.0, 0.0, 2.0])
    result = run_tutorial(engine, tut, clock=clock)
    assert result["timeline"][0]["ok"] is False
    assert result["success"] is False


# --- build_drawtext_filter (pure) ----------------------------------------------

_SAMPLE_TIMELINE = [
    {"caption": "First step", "start": 0.0, "end": 2.5, "ok": True},
    {"caption": "Time: 50% done, c:\\path", "start": 2.5, "end": 5.0, "ok": True},
]


def test_drawtext_enable_intervals() -> None:
    """Each caption is gated by between(t,start,end) over its own window."""
    vf = build_drawtext_filter(_SAMPLE_TIMELINE)
    assert "enable='between(t,0,2.5)'" in vf
    assert "enable='between(t,2.5,5)'" in vf
    # One drawtext node per timeline entry, comma-joined.
    assert vf.count("drawtext=") == 2


def test_drawtext_escaping() -> None:
    """Special chars are escaped for both filtergraph and drawtext parsers."""
    vf = build_drawtext_filter(_SAMPLE_TIMELINE)
    # Colon, percent, comma and backslash from the second caption are escaped.
    assert "Time\\: 50\\% done\\, c\\:\\\\path" in vf
    # The unescaped raw substring must not appear.
    assert "Time: 50%" not in vf


def test_drawtext_empty_timeline_is_noop() -> None:
    """An empty timeline yields the no-op 'null' filter, not an empty string."""
    assert build_drawtext_filter([]) == "null"


def test_drawtext_fontsize_threaded() -> None:
    """The fontsize argument flows into the filter."""
    assert "fontsize=40" in build_drawtext_filter(_SAMPLE_TIMELINE, fontsize=40)


# --- burn_captions guard -------------------------------------------------------


def test_burn_captions_missing_ffmpeg_raises_typed(monkeypatch: Any) -> None:
    """With no ffmpeg on PATH, burn_captions raises the typed TutorialError."""
    monkeypatch.setattr("cerebellum_cua.tutorial.captions.shutil.which", lambda _n: None)
    with pytest.raises(TutorialError):
        burn_captions("in.mp4", _SAMPLE_TIMELINE, "out.mp4")
