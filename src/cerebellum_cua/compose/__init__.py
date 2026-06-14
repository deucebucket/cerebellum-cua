"""Composite / annotated views built from the structured element matrix.

These renderers take the persisted elements (not a live tree) and draw
set-of-marks overlays onto a screenshot for visual grounding and documentation.
The heavy image dependency (OpenCV) is imported lazily inside
:func:`~cerebellum_cua.compose.annotate.annotate_image`, so this package imports
fine on a host without ``cv2``.
"""

from __future__ import annotations

from cerebellum_cua.compose.annotate import (
    AnnotateError,
    annotate_image,
    annotation_boxes,
)

__all__ = ["AnnotateError", "annotate_image", "annotation_boxes"]
