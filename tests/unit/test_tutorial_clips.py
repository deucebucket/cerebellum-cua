"""Unit tests for the pure clip-segment planner + manifest."""

from __future__ import annotations

from typing import Any

from cerebellum_cua.tutorial.clips import (
    build_manifest,
    ffmpeg_cut_argv,
    plan_segments,
)


def _tl() -> list[dict[str, Any]]:
    return [
        {"caption": "intro", "start": 0.0, "end": 2.0, "tokens": 0,
         "perceived": "", "ok": True},
        {"caption": "click", "start": 2.0, "end": 5.0, "tokens": 420,
         "perceived": "BUTTON 'Open'", "ok": True},
        {"caption": "read", "start": 5.0, "end": 7.5, "tokens": 90,
         "perceived": "", "ok": True},
    ]


def test_plan_segments_are_contiguous_and_boundary_aligned() -> None:
    segs = plan_segments(_tl())
    assert [s["index"] for s in segs] == [0, 1, 2]
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 2.0
    assert segs[1]["start"] == 2.0 and segs[1]["end"] == 5.0
    assert segs[2]["end"] == 7.5  # last uses its own end


def test_ffmpeg_cut_argv_is_stream_copy() -> None:
    seg = {"index": 1, "label": "click", "start": 2.0, "end": 5.0}
    argv = ffmpeg_cut_argv("master.mp4", seg, "01-click.mp4")
    assert argv[:2] == ["ffmpeg", "-y"]
    assert "-ss" in argv and "2.0" in argv
    assert "-to" in argv and "5.0" in argv
    assert "-c" in argv and "copy" in argv
    assert argv[-1] == "01-click.mp4"


def test_manifest_carries_verified_and_tokens() -> None:
    tl = _tl()
    segs = plan_segments(tl)
    man = build_manifest(tl, {"a11y_tokens": 510}, segs)
    assert man["totals"]["a11y_tokens"] == 510
    assert len(man["clips"]) == 3
    assert man["clips"][1]["perceived"] == "BUTTON 'Open'"
    assert man["clips"][1]["verified"] is True
