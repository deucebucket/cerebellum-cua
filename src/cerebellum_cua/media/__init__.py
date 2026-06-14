"""Adjacent MEDIA pipeline: understand and edit video token-cheaply.

A sibling capability to the UI capture/control core, not part of it. The goal is
to reason about and edit video *without watching pixels frame-by-frame*. The
pipeline is:

``probe`` (container metadata) -> scene/motion event detection
-> :func:`segments_from_timestamps` (cluster events into segments)
-> :func:`cut_list` (the structured list the LLM reasons over)
-> :func:`render` (xfade-stitch the kept segments).

The defining case is the silent visual event: a clip where one object crosses an
otherwise-static frame. Scene/frame-difference detection flags the moving
frames, clustering collapses them into a few segments, and the LLM only ever sees
the cut-list — never the raw video.

All ffmpeg/ffprobe/whisper access is guarded; this package imports on a host
without any of them and only raises :class:`MediaError` on use.
"""

from __future__ import annotations

from cerebellum_cua.media.edit import (
    build_trim_concat_xfade_cmd,
    cut_list,
    render,
)
from cerebellum_cua.media.errors import MediaError
from cerebellum_cua.media.events import (
    detect_motion_segments,
    detect_scene_cuts,
    parse_showinfo,
    segments_from_timestamps,
)
from cerebellum_cua.media.probe import parse_ffprobe, probe
from cerebellum_cua.media.transcript import transcribe

__all__ = [
    "probe",
    "parse_ffprobe",
    "detect_scene_cuts",
    "detect_motion_segments",
    "parse_showinfo",
    "segments_from_timestamps",
    "cut_list",
    "build_trim_concat_xfade_cmd",
    "render",
    "transcribe",
    "MediaError",
]
