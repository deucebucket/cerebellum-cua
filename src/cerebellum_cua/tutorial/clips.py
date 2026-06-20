"""Cut a captioned master recording into editable per-segment clips.

Pure planning (segment boundaries, ffmpeg argv, manifest) so it is fully
unit-testable; the actual ffmpeg invocation is a thin guarded wrapper elsewhere.
Segments are derived from the tutorial timeline so every cut lands on a settled
step boundary — each clip starts from a quiet frame and the clips concatenate
back into the master.
"""

from __future__ import annotations

from typing import Any


def plan_segments(
    timeline: list[dict[str, Any]], pad: float = 0.0
) -> list[dict[str, Any]]:
    """Contiguous segments from timeline step boundaries (settled cut points).

    A clip spans from its step's ``start`` to the NEXT step's ``start`` (the last
    clip to its own ``end``), so cuts land on settled boundaries.
    """
    segs: list[dict[str, Any]] = []
    n = len(timeline)
    for i, entry in enumerate(timeline):
        start = float(entry["start"])
        end = float(timeline[i + 1]["start"]) if i + 1 < n else float(entry["end"])
        label = _slug(entry.get("caption", f"step{i}"))
        segs.append({
            "index": i, "label": label,
            "start": round(start, 3), "end": round(end + pad, 3),
        })
    return segs


def ffmpeg_cut_argv(src: str, seg: dict[str, Any], out: str) -> list[str]:
    """Stream-copy cut of one segment (lossless, keyframe-aligned)."""
    return [
        "ffmpeg", "-y", "-ss", str(seg["start"]), "-to", str(seg["end"]),
        "-i", src, "-c", "copy", out,
    ]


def build_manifest(
    timeline: list[dict[str, Any]],
    totals: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Edit-list + provenance: one record per clip with stats and verified flag."""
    clips = []
    for seg in segments:
        entry = timeline[seg["index"]]
        clips.append({
            "index": seg["index"],
            "file": f"{seg['index']:02d}-{seg['label']}.mp4",
            "caption": entry.get("caption", ""),
            "start": seg["start"], "end": seg["end"],
            "perceived": entry.get("perceived", ""),
            "tokens": entry.get("tokens", 0),
            "verified": bool(entry.get("verified", entry.get("ok", False))),
        })
    return {"totals": totals, "clips": clips}


def _slug(text: str) -> str:
    """Filesystem-safe lowercase slug from a caption (first few words)."""
    keep = [c.lower() if c.isalnum() else "-" for c in str(text)]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return "-".join(slug.split("-")[:4]) or "step"


__all__ = ["plan_segments", "ffmpeg_cut_argv", "build_manifest"]
