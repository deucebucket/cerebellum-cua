"""Turn a tutorial timeline into burned-in on-screen captions via ffmpeg.

The pure :func:`build_drawtext_filter` returns an ffmpeg ``-vf`` chain that draws
each caption centered along the bottom of the frame, shown only during its
``[start, end]`` window via ``enable='between(t,start,end)'``. It performs no I/O,
so a test can assert the filter text (intervals + escaping) without ffmpeg
installed. :func:`burn_captions` runs that filter behind a :func:`shutil.which`
guard, raising a typed :class:`~cerebellum_cua.tutorial.errors.TutorialError`
when ffmpeg is missing.

ffmpeg's ``drawtext`` has two escaping layers that both bite here: the filtergraph
parser (``\\``, ``:``, ``'``, ``[``, ``]``, ``,``, ``;``) and drawtext's own text
parser (``%`` for strftime-style expansion, plus literal newlines). We escape both
so an arbitrary caption renders verbatim.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from cerebellum_cua.tutorial.errors import TutorialError

#: Encoding a captioned overlay is bounded but can be slow on long clips.
_BURN_TIMEOUT_S = 1800

#: Bottom margin (px) from the frame edge to the caption baseline box.
_BOTTOM_MARGIN = 48


def _escape_drawtext_text(text: str) -> str:
    """Escape one caption for ffmpeg drawtext's text *and* filtergraph parsers.

    Order matters: escape backslashes first, then the characters that the
    filtergraph tokenizer treats specially, then drawtext-specific sequences.
    """
    out = text.replace("\\", "\\\\")
    # Drawtext text-expansion / literal characters.
    out = out.replace("%", "\\%")
    out = out.replace("\n", "\\n")
    # Filtergraph option delimiters that would otherwise end the value.
    for ch in (":", "'", "[", "]", ",", ";"):
        out = out.replace(ch, "\\" + ch)
    return out


def compose_caption(entry: dict[str, Any]) -> str:
    """Build the on-screen text for one timeline entry.

    A plain entry renders just its caption. When stats are present, append a
    ``perceived`` line and a three-way token line (a11y matrix vs focused shot vs
    full shot). All numbers are estimates produced upstream.
    """
    lines = [str(entry.get("caption", ""))]
    perceived = str(entry.get("perceived", "")).strip()
    if perceived:
        lines.append(f"perceived: {perceived}")
    a11y = entry.get("tokens")
    full = entry.get("full_tokens")
    if a11y and full:  # a perceive/act step (a pause has zero cost)
        shot = entry.get("shot_tokens")
        shot_part = f" · focused ~{shot}" if shot is not None else ""
        lines.append(f"matrix ~{a11y} tok{shot_part} · full shot ~{full}")
    return "\n".join(lines)


def summary_card(totals: dict[str, Any]) -> str:
    """Closing card: three-way totals + the matrix-vs-full-shot ratio."""
    a11y = int(totals.get("a11y_tokens", 0))
    shot = int(totals.get("shot_tokens", 0))
    full = int(totals.get("full_tokens", 0))
    ratio = (full / a11y) if a11y else 0.0
    return (
        "perceived via the accessibility tree — no pixels\n"
        f"a11y matrix ~{a11y} tok · focused shots ~{shot} · "
        f"full screenshots ~{full}\n"
        f"~{ratio:.1f}x cheaper than full screenshots (estimates)"
    )


def build_drawtext_filter(timeline: list[dict[str, Any]], *, fontsize: int = 28) -> str:
    """Build the ffmpeg ``drawtext`` chain that shows each caption in its window.

    Args:
        timeline: The :func:`~cerebellum_cua.tutorial.runner.run_tutorial`
            timeline (each entry needs ``caption``, ``start``, ``end``).
        fontsize: Caption font size in pixels.

    Returns:
        A comma-joined chain of ``drawtext=...`` filters, one per captioned step,
        each gated by ``enable='between(t,start,end)'``. An empty timeline yields
        the no-op ``null`` filter.
    """
    filters: list[str] = []
    for entry in timeline:
        text = _escape_drawtext_text(compose_caption(entry))
        start = float(entry["start"])
        end = float(entry["end"])
        filters.append(
            "drawtext="
            f"text='{text}':"
            f"fontsize={fontsize}:"
            "fontcolor=white:"
            "box=1:boxcolor=black@0.6:boxborderw=12:"
            "x=(w-text_w)/2:"
            f"y=h-text_h-{_BOTTOM_MARGIN}:"
            f"enable='between(t,{start:g},{end:g})'"
        )
    return ",".join(filters) if filters else "null"


def burn_captions(
    in_path: str,
    timeline: list[dict[str, Any]],
    out_path: str,
    *,
    fontsize: int = 28,
) -> str:
    """Burn the timeline's captions into ``in_path``, writing ``out_path``.

    Args:
        in_path: Source (un-captioned) recording.
        timeline: The run_tutorial timeline to overlay.
        out_path: Destination path for the captioned video.
        fontsize: Caption font size in pixels.

    Returns:
        ``out_path`` on success.

    Raises:
        TutorialError: If ffmpeg is missing or the encode fails.
    """
    if shutil.which("ffmpeg") is None:
        raise TutorialError("ffmpeg not found: install FFmpeg to burn captions.")
    vf = build_drawtext_filter(timeline, fontsize=fontsize)
    argv = ["ffmpeg", "-y", "-i", in_path, "-vf", vf, out_path]
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_BURN_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TutorialError(f"ffmpeg invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise TutorialError(
            f"ffmpeg exit {result.returncode}: {(result.stderr or '').strip()[-400:]}"
        )
    return out_path


__all__ = ["build_drawtext_filter", "burn_captions", "compose_caption", "summary_card"]
