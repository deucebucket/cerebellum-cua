"""Turn time segments into an edited video with crossfade transitions.

The pure :func:`build_trim_concat_xfade_cmd` returns the ffmpeg ``argv`` that
trims each kept segment and stitches them together with ``xfade`` video
crossfades (and ``afade``/``acrossfade`` audio if requested). It performs no I/O,
so a test can assert the *shape* of the filter graph (one trim per segment, the
right number of ``xfade`` nodes) without ffmpeg installed. :func:`render` runs
that command behind a :func:`shutil.which` guard; :func:`cut_list` produces the
structured ``[{start, end, duration}]`` the agent reasons over — the only video
artifact the LLM ever sees.

Filter-graph strategy: each segment becomes a ``trim``+``setpts`` video chain
``[vN]``. Consecutive chains are folded left-to-right with ``xfade``; the
crossfade ``offset`` is the running output duration minus ``trans_dur`` so the
next clip begins its fade exactly as the previous one ends.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from cerebellum_cua.media.errors import MediaError

#: Encoding can be slow on long edits; allow a generous ceiling.
_RENDER_TIMEOUT_S = 1800


def cut_list(segments: list[tuple[float, float]]) -> list[dict[str, float]]:
    """Return the structured ``[{start, end, duration}]`` the agent reasons over.

    Args:
        segments: ``(start, end)`` pairs in seconds.

    Returns:
        One dict per segment with ``start``, ``end``, and ``duration`` (all
        floats rounded to milliseconds).
    """
    return [
        {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        }
        for start, end in segments
    ]


def build_trim_concat_xfade_cmd(
    in_path: str,
    segments: list[tuple[float, float]],
    out_path: str,
    *,
    transition: str = "fade",
    trans_dur: float = 0.5,
    with_audio: bool = False,
) -> list[str]:
    """Build the ffmpeg ``argv`` to trim ``segments`` and xfade-concatenate them.

    Args:
        in_path: Source video path.
        segments: ``(start, end)`` pairs to keep, in playback order.
        out_path: Destination path for the rendered edit.
        transition: ``xfade`` transition name (e.g. ``"fade"``, ``"wipeleft"``).
        trans_dur: Crossfade duration in seconds.
        with_audio: If true, also build an audio chain with ``acrossfade``.

    Returns:
        The complete ffmpeg ``argv`` list.

    Raises:
        MediaError: If ``segments`` is empty.
    """
    if not segments:
        raise MediaError("cannot build an edit command from zero segments.")

    filter_lines: list[str] = []
    for i, (start, end) in enumerate(segments):
        filter_lines.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]"
        )
    if with_audio:
        for i, (start, end) in enumerate(segments):
            filter_lines.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]"
            )

    last_v, last_a, running = _fold_transitions(
        segments, filter_lines, transition, trans_dur, with_audio
    )

    argv = ["ffmpeg", "-y", "-i", in_path, "-filter_complex", ";".join(filter_lines)]
    argv += ["-map", last_v]
    if with_audio and last_a is not None:
        argv += ["-map", last_a]
    argv += [out_path]
    return argv


def _fold_transitions(
    segments: list[tuple[float, float]],
    filter_lines: list[str],
    transition: str,
    trans_dur: float,
    with_audio: bool,
) -> tuple[str, str | None, float]:
    """Fold per-segment chains left-to-right with xfade/acrossfade.

    Appends the transition filter lines in place and returns the final video
    label, the final audio label (or None), and the running output duration.
    """
    durations = [end - start for start, end in segments]
    last_v = "[v0]"
    last_a = "[a0]" if with_audio else None
    running = durations[0]
    for i in range(1, len(segments)):
        offset = max(0.0, running - trans_dur)
        out_v = f"[vx{i}]"
        filter_lines.append(
            f"{last_v}[v{i}]xfade=transition={transition}:"
            f"duration={trans_dur}:offset={offset}{out_v}"
        )
        last_v = out_v
        if with_audio:
            out_a = f"[ax{i}]"
            filter_lines.append(
                f"{last_a}[a{i}]acrossfade=d={trans_dur}{out_a}"
            )
            last_a = out_a
        running = running + durations[i] - trans_dur
    return last_v, last_a, running


def render(
    in_path: str,
    segments: list[tuple[float, float]],
    out_path: str,
    **kw: Any,
) -> str:
    """Render the trimmed, crossfaded edit to ``out_path`` and return it.

    Keyword args are forwarded to :func:`build_trim_concat_xfade_cmd`
    (``transition``, ``trans_dur``, ``with_audio``).

    Raises:
        MediaError: If ``ffmpeg`` is missing, ``segments`` is empty, or encoding
            fails.
    """
    if shutil.which("ffmpeg") is None:
        raise MediaError("ffmpeg not found: install FFmpeg to render edits.")
    argv = build_trim_concat_xfade_cmd(in_path, segments, out_path, **kw)
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_RENDER_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError(f"ffmpeg invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise MediaError(
            f"ffmpeg exit {result.returncode}: {(result.stderr or '').strip()[-400:]}"
        )
    return out_path


__all__ = ["build_trim_concat_xfade_cmd", "render", "cut_list"]
