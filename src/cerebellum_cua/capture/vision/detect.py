"""Pure region detection — turn an image into structured layout candidates.

This module derives the SAME kind of structured element stream the a11y backends
produce, but from a screenshot instead of an accessibility tree. The output is a
list of :class:`DetectedRegion` (a bounding box + OCR text + a coarse ``kind`` +
confidence), never raw pixels, so downstream token budgets stay intact.

Two perception sources are fused:

* **OCR** (Tesseract via ``pytesseract``) supplies text and word/line boxes.
* **Edge / rectangle detection** (OpenCV contours) supplies candidate boxes for
  windows, panels, buttons and borders that carry no text.

Both heavy libraries are imported lazily and guarded, so importing this module
never requires ``cv2``, ``pytesseract`` or ``numpy`` to be installed. The fusion
ALGORITHM itself (filtering, merging OCR text into enclosing boxes, dedup) is a
pure function that accepts already-extracted ``ocr`` / ``cv`` results, so it is
fully unit-testable with injected fakes — no real OpenCV or Tesseract needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cerebellum_cua.capture.vision._extract import load_image, run_cv, run_ocr

#: Boxes smaller than this on either side are hit-test / decorative noise.
_MIN_SIDE = 8
#: Boxes covering more than this fraction of the image are treated as the canvas.
_MAX_AREA_FRAC = 0.99
#: IoU above which two boxes are considered the same region (dedup).
_DEDUP_IOU = 0.85
#: A text box this fraction inside a rect is treated as that rect's label.
_CONTAIN_FRAC = 0.6


@dataclass(slots=True)
class DetectedRegion:
    """One structured region detected from a screenshot.

    Attributes:
        bbox: ``(left, top, width, height)`` in image pixel coordinates.
        text: OCR text associated with the region (may be empty).
        kind: Coarse provenance/shape tag — ``"text"`` (OCR-only),
            ``"box"`` (rectangle/contour) or ``"labeled_box"`` (a box that
            absorbed an OCR text label).
        confidence: Detector confidence in ``[0, 1]``.
    """

    bbox: tuple[int, int, int, int]
    text: str = ""
    kind: str = "box"
    confidence: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


def detect_regions(
    image: Any,
    ocr: list[dict[str, Any]] | None = None,
    cv: list[tuple[int, int, int, int]] | None = None,
) -> list[DetectedRegion]:
    """Detect structured regions in ``image``, fusing OCR + rectangle boxes.

    Args:
        image: An image object. Only its ``shape``/size is read (for area
            filtering); real pixels are only touched by the lazy extractors. May
            be ``None`` when both ``ocr`` and ``cv`` are injected.
        ocr: Pre-extracted OCR results to inject (each ``{bbox, text, conf}``).
            When ``None`` the OCR extractor runs against ``image`` (guarded).
        cv: Pre-extracted contour boxes to inject (``(l, t, w, h)`` tuples). When
            ``None`` the OpenCV extractor runs against ``image`` (guarded).

    Returns:
        Fused, filtered, deduped regions sorted top-to-bottom, left-to-right.
    """
    img_w, img_h = _image_size(image)
    ocr_results = ocr if ocr is not None else run_ocr(image)
    cv_boxes = cv if cv is not None else run_cv(image)

    text_regions = [_text_region(r) for r in ocr_results if _ocr_ok(r)]
    box_regions = [
        DetectedRegion(bbox=_as_bbox(b), kind="box", confidence=0.5)
        for b in cv_boxes
    ]

    merged = _merge_text_into_boxes(box_regions, text_regions)
    filtered = [r for r in merged if _keep(r, img_w, img_h)]
    deduped = _dedup(filtered)
    deduped.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return deduped


# --- fusion algorithm (pure, unit-testable) -----------------------------------
def _merge_text_into_boxes(
    boxes: list[DetectedRegion], texts: list[DetectedRegion]
) -> list[DetectedRegion]:
    """Fold each OCR text into its enclosing box; keep unmatched text standalone.

    A text region is attached to the SMALLEST box that geometrically contains it
    (most-specific label wins). The box becomes ``labeled_box`` and inherits the
    text; its confidence is bumped by the text's. Text not inside any box is kept
    as a ``text`` region in its own right.
    """
    result = [_copy(b) for b in boxes]
    for text in texts:
        host = _smallest_container(result, text)
        if host is None:
            result.append(_copy(text))
            continue
        host.text = (f"{host.text} {text.text}".strip() if host.text else text.text)
        host.kind = "labeled_box"
        host.confidence = min(1.0, max(host.confidence, 0.5) + text.confidence * 0.5)
    return result


def _smallest_container(
    boxes: list[DetectedRegion], text: DetectedRegion
) -> DetectedRegion | None:
    """Return the smallest box that mostly contains ``text``, or ``None``."""
    best: DetectedRegion | None = None
    best_area = None
    for box in boxes:
        if _contains(box.bbox, text.bbox, _CONTAIN_FRAC):
            area = box.bbox[2] * box.bbox[3]
            if best_area is None or area < best_area:
                best, best_area = box, area
    return best


def _keep(region: DetectedRegion, img_w: int, img_h: int) -> bool:
    """Size / noise filter: drop slivers and the full-canvas catch-all box."""
    _, _, w, h = region.bbox
    if w < _MIN_SIDE or h < _MIN_SIDE:
        # Keep a small region only if it carries text worth surfacing.
        return bool(region.text)
    if img_w > 0 and img_h > 0:
        if (w * h) >= _MAX_AREA_FRAC * (img_w * img_h):
            return False
    return True


def _dedup(regions: list[DetectedRegion]) -> list[DetectedRegion]:
    """Drop near-duplicate boxes (IoU >= threshold), preferring labeled ones."""
    kept: list[DetectedRegion] = []
    for region in sorted(regions, key=_dedup_rank):
        if any(_iou(region.bbox, k.bbox) >= _DEDUP_IOU for k in kept):
            continue
        kept.append(region)
    return kept


def _dedup_rank(region: DetectedRegion) -> tuple[int, float]:
    """Sort key so labeled / higher-confidence regions survive dedup first."""
    has_text = 1 if region.text else 0
    return (-has_text, -region.confidence)


# --- geometry helpers ---------------------------------------------------------
def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int],
              frac: float) -> bool:
    """True iff at least ``frac`` of ``inner``'s area lies within ``outer``."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    inner_area = iw * ih
    if inner_area <= 0:
        return False
    ix1, iy1 = max(ox, ix), max(oy, iy)
    ix2, iy2 = min(ox + ow, ix + iw), min(oy + oh, iy + ih)
    if ix2 <= ix1 or iy2 <= iy1:
        return False
    overlap = (ix2 - ix1) * (iy2 - iy1)
    return overlap >= frac * inner_area


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two ``(l, t, w, h)`` boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _as_bbox(box: Any) -> tuple[int, int, int, int]:
    """Coerce a 4-sequence into an int ``(l, t, w, h)`` tuple."""
    left, top, width, height = box
    return (int(left), int(top), int(width), int(height))


def _copy(region: DetectedRegion) -> DetectedRegion:
    """Shallow copy so merge never mutates an injected fake in place."""
    return DetectedRegion(
        bbox=region.bbox, text=region.text, kind=region.kind,
        confidence=region.confidence, meta=dict(region.meta),
    )


def _text_region(result: dict[str, Any]) -> DetectedRegion:
    """Build a ``text`` region from one OCR result dict."""
    return DetectedRegion(
        bbox=_as_bbox(result["bbox"]),
        text=str(result.get("text", "")).strip(),
        kind="text",
        confidence=float(result.get("conf", 0.0)),
    )


def _ocr_ok(result: dict[str, Any]) -> bool:
    """Filter out empty / whitespace-only OCR tokens."""
    return bool(str(result.get("text", "")).strip())


def _image_size(image: Any) -> tuple[int, int]:
    """Best-effort (width, height); 0,0 when unknown (skips area filter)."""
    if image is None:
        return (0, 0)
    shape = getattr(image, "shape", None)
    if shape and len(shape) >= 2:
        return (int(shape[1]), int(shape[0]))  # numpy ndarray: (rows, cols, ...)
    size = getattr(image, "size", None)
    if isinstance(size, (tuple, list)) and len(size) >= 2:
        return (int(size[0]), int(size[1]))  # PIL: (width, height)
    return (0, 0)


__all__ = ["DetectedRegion", "detect_regions", "load_image"]
