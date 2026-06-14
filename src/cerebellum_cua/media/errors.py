"""Typed error for the adjacent media pipeline.

Kept separate from the core ``cerebellum_cua.errors`` taxonomy: the media module
is a sibling capability, not part of the UI capture/control core, so it does not
participate in the JSONL protocol's numeric error codes. A single typed
exception is enough for callers to distinguish "a media tool was missing or
failed" from ordinary programming errors.
"""

from __future__ import annotations


class MediaError(RuntimeError):
    """Raised when a media tool (ffmpeg/ffprobe/whisper) is missing or fails."""


__all__ = ["MediaError"]
