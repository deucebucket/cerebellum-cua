"""Set-of-marks annotation: draw element boxes + short labels onto a screenshot.

Given the structured elements of a snapshot (and, optionally, the compact legend
codes from :mod:`cerebellum_cua.legend`), this overlays each element's bounding
rectangle and a small label onto a screenshot. The result is a single annotated
image useful for visual grounding (the LLM sees marked, numbered targets) and for
documentation.

The geometry/label decision is a pure function (:func:`annotation_boxes`) so it is
fully unit-testable without any image library. Only the actual drawing needs
OpenCV (``cv2``, already a ``[vision]`` extra), which is imported lazily inside
:func:`annotate_image`; if it is unavailable a typed :class:`AnnotateError` is
raised rather than crashing on import.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cerebellum_cua.model import Element


class AnnotateError(RuntimeError):
    """Raised when annotation cannot proceed (e.g. OpenCV is unavailable)."""


def annotation_boxes(
    elements: Sequence[Element],
    legend: Mapping[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Compute what to draw for each element: its label and pixel box.

    Args:
        elements: Snapshot elements; those with a zero-area rect are skipped.
        legend: Optional ``{row_id: code}`` map (from
            :func:`cerebellum_cua.legend.build_legend`). When a row has a code it is
            used as the label, otherwise the ``row_id`` is used.

    Returns:
        A list (input order, skipping empty rects) of
        ``{"row_id": int, "label": str, "bbox": [left, top, width, height]}``.
    """
    legend = legend or {}
    boxes: list[dict[str, Any]] = []
    for element in elements:
        rect = element.bounding_rect
        if rect.width <= 0 or rect.height <= 0:
            continue
        label = legend.get(element.row_id)
        if not label:
            label = str(element.row_id)
        boxes.append(
            {
                "row_id": element.row_id,
                "label": label,
                "bbox": [rect.left, rect.top, rect.width, rect.height],
            }
        )
    return boxes


def annotate_image(
    image_path: str,
    elements: Sequence[Element],
    out_path: str,
    *,
    legend: Mapping[int, str] | None = None,
    labels: bool = True,
) -> dict[str, Any]:
    """Draw each element's box (+ short label) onto ``image_path``, save to ``out_path``.

    Args:
        image_path: Source screenshot to read.
        elements: Snapshot elements to mark (set-of-marks).
        out_path: Destination path for the annotated image.
        legend: Optional ``{row_id: code}`` map; codes are used as labels when set.
        labels: When False, draw the rectangles only (no label text).

    Returns:
        ``{"path": str, "width": int, "height": int, "count": int}`` — the saved
        path, its dimensions, and how many boxes were drawn.

    Raises:
        AnnotateError: If OpenCV is unavailable or the source image cannot be read.
    """
    cv2 = _load_cv2()
    image = cv2.imread(image_path)
    if image is None:
        raise AnnotateError(f"could not read source image {image_path!r}")
    height, width = image.shape[:2]

    boxes = annotation_boxes(elements, legend)
    for box in boxes:
        left, top, w, h = box["bbox"]
        cv2.rectangle(image, (left, top), (left + w, top + h), (0, 255, 0), 2)
        if labels:
            cv2.putText(
                image,
                box["label"],
                (left + 2, max(top + 14, 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )

    if not cv2.imwrite(out_path, image):
        raise AnnotateError(f"could not write annotated image {out_path!r}")
    return {
        "path": out_path,
        "width": int(width),
        "height": int(height),
        "count": len(boxes),
    }


def _load_cv2() -> Any:
    """Import OpenCV lazily, raising a typed error when it is unavailable."""
    try:
        import cv2  # noqa: PLC0415 - guarded optional dependency
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise AnnotateError(
            "OpenCV (cv2) is required to draw annotations; install the "
            "'[vision]' extra (pip install opencv-python-headless)."
        ) from exc
    return cv2


__all__ = ["AnnotateError", "annotate_image", "annotation_boxes"]
