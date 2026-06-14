"""Scene-cut and motion-segment detection — the core of the silent case.

The goal is to find *where in time something happens* without the LLM ever
watching pixels. We ask ffmpeg's ``select`` filter to flag frames whose
scene-change score exceeds a threshold, parse the ``showinfo`` log for their
timestamps, then cluster those timestamps into a small set of padded, merged
segments. The LLM only ever reasons over that segment list.

As with :mod:`cerebellum_cua.media.probe`, the side-effectful ffmpeg call and the
pure parsing/clustering logic are split so the latter is unit-testable with no
binary present:

* :func:`parse_showinfo` — extract ``pts_time`` values from ffmpeg stderr.
* :func:`segments_from_timestamps` — cluster timestamps into segments.

The silent-visual-event case (an object crossing an otherwise-static frame) is
exactly what scene/frame-difference detection catches: the static background
produces no events, the moving object produces a run of them, and
:func:`segments_from_timestamps` collapses that run into one segment.

Future optimization (noted, not required): motion can also be read directly from
codec motion vectors via ffmpeg ``-flags2 +export_mvs`` (and the ``codecview``
filter), avoiding a full decode+difference pass. Small/fast objects may need a
lower ``threshold`` and tighter ``pad`` — see docs/MEDIA.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from cerebellum_cua.media.errors import MediaError

#: Matches ``pts_time:12.345`` tokens in ffmpeg ``showinfo`` stderr output.
_PTS_TIME_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")

#: Detection passes can take a while on long clips; cap generously.
_DETECT_TIMEOUT_S = 600


def parse_showinfo(stderr_text: str) -> list[float]:
    """Extract ascending ``pts_time`` timestamps from ffmpeg ``showinfo`` output.

    Args:
        stderr_text: The stderr ffmpeg writes when the ``showinfo`` filter runs.

    Returns:
        Timestamps (seconds) of the selected frames, sorted and de-duplicated.
    """
    times = {round(float(m), 6) for m in _PTS_TIME_RE.findall(stderr_text)}
    return sorted(times)


def segments_from_timestamps(
    timestamps: list[float],
    fps: float,
    duration: float,
    pad: float = 0.5,
    merge_gap: float = 1.0,
) -> list[tuple[float, float]]:
    """Cluster event timestamps into padded, merged ``(start, end)`` segments.

    Each timestamp is widened by ``pad`` on both sides; overlapping or
    near-touching windows (within ``merge_gap``) are merged into one segment.
    Results are clamped to ``[0, duration]``.

    Args:
        timestamps: Event times (seconds), e.g. scene cuts or motion frames.
        fps: Source frame rate; reserved for future per-frame snapping. Unused
            for clamping but kept in the signature so callers pass real context.
        duration: Total media duration (seconds), the upper clamp bound.
        pad: Seconds of context to add before and after each event.
        merge_gap: Segments separated by a gap no larger than this are merged.

    Returns:
        A list of non-overlapping ``(start, end)`` tuples in ascending order.
        Empty input yields an empty list.
    """
    if not timestamps:
        return []
    upper = duration if duration > 0 else max(timestamps) + pad
    windows: list[tuple[float, float]] = []
    for t in sorted(timestamps):
        start = max(0.0, t - pad)
        end = min(upper, t + pad)
        if end > start:
            windows.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in windows:
        if merged and start - merged[-1][1] <= merge_gap:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def detect_scene_cuts(path: str, threshold: float = 0.3) -> list[float]:
    """Return timestamps (seconds) where the scene-change score exceeds ``threshold``.

    Runs ``ffmpeg -i path -vf select='gt(scene,threshold)',showinfo -f null -``
    and parses the ``showinfo`` stderr for ``pts_time`` values.

    Args:
        path: Source media path.
        threshold: Scene-change sensitivity in ``[0, 1]``; lower catches subtler
            changes (and small/fast objects) at the cost of more events.

    Returns:
        Ascending scene-cut timestamps.

    Raises:
        MediaError: If ``ffmpeg`` is missing or the pass fails.
    """
    vf = f"select='gt(scene,{threshold})',showinfo"
    stderr = _run_ffmpeg_detect(path, vf)
    return parse_showinfo(stderr)


def detect_motion_segments(
    path: str,
    threshold: float = 0.3,
    pad: float = 0.5,
    merge_gap: float = 1.0,
) -> list[tuple[float, float]]:
    """Derive contiguous ``(start, end)`` segments where visual activity occurs.

    High-level convenience that probes the media for ``fps``/``duration``,
    detects scene cuts as the activity signal, and clusters them via
    :func:`segments_from_timestamps`. For a silent clip with one moving object,
    this collapses the burst of change-events into the few segments worth keeping.

    Args:
        path: Source media path.
        threshold: Scene-change sensitivity passed to :func:`detect_scene_cuts`.
        pad: Context padding per event (see :func:`segments_from_timestamps`).
        merge_gap: Maximum gap to merge adjacent segments across.

    Returns:
        A list of ``(start, end)`` segments in ascending order.

    Raises:
        MediaError: If ``ffmpeg``/``ffprobe`` is missing or a pass fails.
    """
    from cerebellum_cua.media.probe import probe

    meta = probe(path)
    cuts = detect_scene_cuts(path, threshold=threshold)
    return segments_from_timestamps(
        cuts, fps=meta["fps"], duration=meta["duration"],
        pad=pad, merge_gap=merge_gap,
    )


def _run_ffmpeg_detect(path: str, vf: str) -> str:
    """Run a video-filter analysis pass and return ffmpeg's stderr text.

    Uses ``-f null -`` so nothing is encoded; ``showinfo`` writes to stderr.
    """
    if shutil.which("ffmpeg") is None:
        raise MediaError("ffmpeg not found: install FFmpeg to detect events.")
    argv = ["ffmpeg", "-i", path, "-vf", vf, "-an", "-f", "null", "-"]
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_DETECT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError(f"ffmpeg invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise MediaError(
            f"ffmpeg exit {result.returncode}: {(result.stderr or '').strip()[-400:]}"
        )
    return result.stderr


__all__ = [
    "detect_scene_cuts",
    "detect_motion_segments",
    "parse_showinfo",
    "segments_from_timestamps",
]
