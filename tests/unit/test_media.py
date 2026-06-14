"""Unit tests for the adjacent media pipeline — no real ffmpeg/whisper.

Every test exercises either a pure function (``parse_ffprobe``,
``parse_showinfo``, ``segments_from_timestamps``, ``build_trim_concat_xfade_cmd``,
``cut_list``) on a captured sample, or a guarded wrapper with ``shutil.which``
monkeypatched to assert the typed ``MediaError`` surfaces when a tool is absent.
"""

from __future__ import annotations

import json

import pytest

from cerebellum_cua.media import (
    MediaError,
    build_trim_concat_xfade_cmd,
    cut_list,
    detect_scene_cuts,
    parse_ffprobe,
    parse_showinfo,
    probe,
    segments_from_timestamps,
    transcribe,
)

# --- parse_ffprobe -------------------------------------------------------------

_SAMPLE_FFPROBE = json.dumps(
    {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30000/1001",
                "duration": "12.512000",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "12.520000"},
    }
)


def test_parse_ffprobe_extracts_fields() -> None:
    meta = parse_ffprobe(_SAMPLE_FFPROBE)
    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["vcodec"] == "h264"
    assert meta["has_audio"] is True
    assert meta["duration"] == pytest.approx(12.52)
    assert meta["fps"] == pytest.approx(30000 / 1001)


def test_parse_ffprobe_no_audio_no_video() -> None:
    meta = parse_ffprobe(json.dumps({"streams": [], "format": {"duration": "5"}}))
    assert meta["has_audio"] is False
    assert meta["width"] == 0
    assert meta["vcodec"] is None
    assert meta["fps"] == 0.0
    assert meta["duration"] == 5.0


def test_parse_ffprobe_bad_json_raises() -> None:
    with pytest.raises(MediaError):
        parse_ffprobe("not json {")


# --- parse_showinfo ------------------------------------------------------------

_SAMPLE_SHOWINFO = """
[Parsed_showinfo_1 @ 0x55] n:0 pts:1234 pts_time:1.234 pos:1
[Parsed_showinfo_1 @ 0x55] n:1 pts:5678 pts_time:5.678 pos:2
[Parsed_showinfo_1 @ 0x55] n:2 pts:5678 pts_time:5.678 pos:2
frame=  3 fps=0.0 q=-1.0 Lsize=N/A time=00:00:09.00
"""


def test_parse_showinfo_extracts_sorted_unique() -> None:
    times = parse_showinfo(_SAMPLE_SHOWINFO)
    assert times == [1.234, 5.678]


def test_parse_showinfo_empty() -> None:
    assert parse_showinfo("no timestamps here") == []


# --- segments_from_timestamps --------------------------------------------------


def test_segments_padding_and_clamp() -> None:
    segs = segments_from_timestamps([5.0], fps=30.0, duration=10.0, pad=0.5)
    assert segs == [(4.5, 5.5)]


def test_segments_clamped_to_duration() -> None:
    segs = segments_from_timestamps([0.1, 9.9], fps=30.0, duration=10.0, pad=0.5)
    assert segs[0][0] == 0.0
    assert segs[-1][1] == 10.0


def test_segments_merge_close_events() -> None:
    # 5.0 and 5.4 windows overlap; merged into one.
    segs = segments_from_timestamps(
        [5.0, 5.4], fps=30.0, duration=30.0, pad=0.5, merge_gap=1.0
    )
    assert segs == [(4.5, 5.9)]


def test_segments_separate_distant_events() -> None:
    segs = segments_from_timestamps(
        [2.0, 20.0], fps=30.0, duration=30.0, pad=0.5, merge_gap=1.0
    )
    assert len(segs) == 2


def test_segments_merge_gap_clusters_run() -> None:
    # A burst of motion frames collapses to one segment (silent-event case).
    burst = [3.0, 3.3, 3.6, 3.9, 4.2]
    segs = segments_from_timestamps(
        burst, fps=30.0, duration=30.0, pad=0.2, merge_gap=0.5
    )
    assert len(segs) == 1
    assert segs[0][0] == pytest.approx(2.8)
    assert segs[0][1] == pytest.approx(4.4)


def test_segments_empty_input() -> None:
    assert segments_from_timestamps([], fps=30.0, duration=10.0) == []


# --- cut_list ------------------------------------------------------------------


def test_cut_list_shape() -> None:
    rows = cut_list([(1.0, 3.5), (10.0, 12.0)])
    assert rows == [
        {"start": 1.0, "end": 3.5, "duration": 2.5},
        {"start": 10.0, "end": 12.0, "duration": 2.0},
    ]


# --- build_trim_concat_xfade_cmd ----------------------------------------------


def test_build_cmd_single_segment_no_xfade() -> None:
    argv = build_trim_concat_xfade_cmd("in.mp4", [(1.0, 3.0)], "out.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("trim=") == 1
    assert "xfade" not in graph
    assert argv[-1] == "out.mp4"


def test_build_cmd_segment_count_drives_filters() -> None:
    segs = [(0.0, 2.0), (5.0, 7.0), (10.0, 12.0)]
    argv = build_trim_concat_xfade_cmd("in.mp4", segs, "out.mp4")
    graph = argv[argv.index("-filter_complex") + 1]
    # one video trim per segment, one xfade per join (N-1).
    assert graph.count("[0:v]trim=") == 3
    assert graph.count("xfade=") == 2
    assert "transition=fade" in graph


def test_build_cmd_with_audio_adds_acrossfade() -> None:
    segs = [(0.0, 2.0), (5.0, 7.0)]
    argv = build_trim_concat_xfade_cmd("in.mp4", segs, "out.mp4", with_audio=True)
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("[0:a]atrim=") == 2
    assert graph.count("acrossfade=") == 1
    assert argv.count("-map") == 2


def test_build_cmd_custom_transition() -> None:
    argv = build_trim_concat_xfade_cmd(
        "in.mp4", [(0.0, 1.0), (2.0, 3.0)], "out.mp4", transition="wipeleft"
    )
    graph = argv[argv.index("-filter_complex") + 1]
    assert "transition=wipeleft" in graph


def test_build_cmd_empty_segments_raises() -> None:
    with pytest.raises(MediaError):
        build_trim_concat_xfade_cmd("in.mp4", [], "out.mp4")


# --- guarded wrappers: MediaError when tools missing ---------------------------


def test_probe_raises_when_ffprobe_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(MediaError):
        probe("clip.mp4")


def test_detect_scene_cuts_raises_when_ffmpeg_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(MediaError):
        detect_scene_cuts("clip.mp4")


def test_render_raises_when_ffmpeg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from cerebellum_cua.media import render

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(MediaError):
        render("in.mp4", [(0.0, 1.0)], "out.mp4")


def test_transcribe_raises_when_backend_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def _no_whisper(name: str, *args: object, **kw: object) -> object:
        if name in {"faster_whisper", "whisper"}:
            raise ImportError(name)
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_whisper)
    with pytest.raises(MediaError):
        transcribe("clip.mp4")
