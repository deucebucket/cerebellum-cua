"""Media container inspection via ``ffprobe``.

The expensive, side-effectful half (running ``ffprobe``) and the pure parsing
half are deliberately separated: :func:`parse_ffprobe` takes the JSON text
``ffprobe`` emits and distills it to the handful of fields the pipeline needs,
so it is unit-testable on a captured sample with no binary present. :func:`probe`
locates ``ffprobe`` with :func:`shutil.which`, runs it, and feeds the output to
the parser; it raises a typed :class:`MediaError` if the tool is missing.

``ffprobe`` (part of FFmpeg) is a *system* tool, not a Python dependency. This
module imports cleanly on a host without it; the error only surfaces on use.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from cerebellum_cua.media.errors import MediaError

#: How long the ``ffprobe`` subprocess may run before it is abandoned.
_PROBE_TIMEOUT_S = 30


def parse_ffprobe(json_text: str) -> dict[str, Any]:
    """Distill ``ffprobe -print_format json`` output to pipeline fields.

    Args:
        json_text: The JSON document ``ffprobe`` writes to stdout, containing
            ``format`` and ``streams`` objects.

    Returns:
        A dict with keys ``duration`` (float seconds), ``fps`` (float, 0.0 if
        unknown), ``width`` / ``height`` (int, 0 if no video), ``vcodec``
        (str or None), and ``has_audio`` (bool).

    Raises:
        MediaError: If the text is not valid JSON.
    """
    try:
        doc = json.loads(json_text)
    except (ValueError, TypeError) as exc:
        raise MediaError(f"ffprobe output was not valid JSON: {exc}") from exc

    streams = doc.get("streams") or []
    fmt = doc.get("format") or {}

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = _as_float(fmt.get("duration"))
    if duration == 0.0 and video is not None:
        duration = _as_float(video.get("duration"))

    fps = 0.0
    width = height = 0
    vcodec: str | None = None
    if video is not None:
        fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        vcodec = video.get("codec_name")

    return {
        "duration": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "vcodec": vcodec,
        "has_audio": has_audio,
    }


def _as_float(value: Any) -> float:
    """Coerce a probe value to float, returning 0.0 when absent/unparseable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_rate(rate: Any) -> float:
    """Parse an ffmpeg ``"num/den"`` rational frame-rate string into fps."""
    if not rate or not isinstance(rate, str):
        return 0.0
    if "/" in rate:
        num, _, den = rate.partition("/")
        d = _as_float(den)
        return _as_float(num) / d if d else 0.0
    return _as_float(rate)


def probe(path: str) -> dict[str, Any]:
    """Inspect ``path`` with ``ffprobe`` and return its distilled metadata.

    Args:
        path: Path to the media file to inspect.

    Returns:
        The dict described in :func:`parse_ffprobe`.

    Raises:
        MediaError: If ``ffprobe`` is not on ``PATH`` or the probe fails.
    """
    if shutil.which("ffprobe") is None:
        raise MediaError(
            "ffprobe not found: install FFmpeg (it provides the ffprobe binary)."
        )
    argv = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_PROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaError(f"ffprobe invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise MediaError(
            f"ffprobe exit {result.returncode}: {(result.stderr or '').strip()}"
        )
    return parse_ffprobe(result.stdout)


__all__ = ["probe", "parse_ffprobe"]
