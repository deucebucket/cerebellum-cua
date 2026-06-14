"""Tutorial generation: drive a real task while drawing step captions on screen.

This package authors short, declarative how-tos (:class:`Tutorial` /
:class:`TutorialStep`), runs them against a live engine while recording a
captioned :func:`run_tutorial` timeline, and burns those captions into the
recorded video for documentation (:func:`build_drawtext_filter` /
:func:`burn_captions`). The runner reuses the existing engine handlers (skills
and operations); the caption layer reuses the guarded-ffmpeg style of the media
module. Everything below the recording itself is pure and unit-testable.
"""

from __future__ import annotations

from cerebellum_cua.tutorial.captions import build_drawtext_filter, burn_captions
from cerebellum_cua.tutorial.errors import TutorialError
from cerebellum_cua.tutorial.runner import run_tutorial
from cerebellum_cua.tutorial.spec import Tutorial, TutorialStep

__all__ = [
    "Tutorial",
    "TutorialStep",
    "run_tutorial",
    "build_drawtext_filter",
    "burn_captions",
    "TutorialError",
]
