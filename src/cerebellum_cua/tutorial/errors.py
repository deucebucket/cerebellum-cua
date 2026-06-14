"""Typed error for the tutorial-generation module.

Kept separate from the core ``cerebellum_cua.errors`` taxonomy: tutorial
generation is a documentation/authoring capability, not part of the UI
capture/control core, so it does not participate in the JSONL protocol's numeric
error codes. A single typed exception lets callers distinguish "a tutorial media
tool was missing or failed" from ordinary programming errors.
"""

from __future__ import annotations


class TutorialError(RuntimeError):
    """Raised when tutorial media tooling (ffmpeg) is missing or fails."""


__all__ = ["TutorialError"]
