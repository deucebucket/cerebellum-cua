"""Pure layout helpers for the vision backend — containment + re-match geometry.

These functions carry no I/O and no heavy imports, so the backend's hierarchy
derivation (parent-by-smallest-container, nesting depth) and its re-acquisition
scoring (bbox IoU + text match) are unit-testable in isolation. The backend wires
them around the screenshot/detect pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from cerebellum_cua.capture.base import CapturedElement, CaptureNode
from cerebellum_cua.model import BoundingRect

#: Minimum bbox side (px) kept by the noise filter — mirrors detect's slivers.
MIN_SIDE = 8
#: Containment fraction at which a region is treated as a child of an outer box.
PARENT_FRAC = 0.85


def keep_element(element: CapturedElement) -> bool:
    """should_include-style size/noise filter for a vision element."""
    rect = element.bounding_rect
    if rect.width < MIN_SIDE or rect.height < MIN_SIDE:
        return bool(element.name)
    return True


def yield_with_containment(
    elements: list[CapturedElement],
) -> Iterator[CaptureNode]:
    """Yield elements parent-before-child using geometric containment.

    Each element's parent is the SMALLEST other element that geometrically
    contains it (most-specific enclosure). Depth is the containment nesting level.
    Each element is keyed by a stable per-walk index via ``runtime_id`` so the
    driver's ``_self_key`` (runtime-id tuple, since ``native_ref`` is ``None``)
    resolves the ``parent_key`` to the parent's assigned row.
    """
    for i, element in enumerate(elements):
        element.runtime_id = [i]
    parents = containment_parents(elements)
    depths = containment_depths(parents)

    order = sorted(
        range(len(elements)),
        key=lambda i: (depths[i], -elements[i].bounding_rect.area),
    )
    for i in order:
        parent_idx = parents[i]
        parent_key = (parent_idx,) if parent_idx is not None else None
        yield elements[i], depths[i], parent_key


def containment_parents(elements: list[CapturedElement]) -> list[int | None]:
    """For each element, the index of the smallest element that contains it."""
    parents: list[int | None] = [None] * len(elements)
    for i, child in enumerate(elements):
        best_area = None
        for j, outer in enumerate(elements):
            if i == j or not _rect_contains(outer.bounding_rect, child.bounding_rect):
                continue
            area = outer.bounding_rect.area
            if area <= child.bounding_rect.area:
                continue
            if best_area is None or area < best_area:
                parents[i], best_area = j, area
    return parents


def containment_depths(parents: list[int | None]) -> list[int]:
    """Resolve each element's nesting depth from the parent-index chain."""
    depths: list[int] = []
    for i in range(len(parents)):
        depth, cur, guard = 0, parents[i], 0
        while cur is not None and guard < len(parents) + 1:
            depth += 1
            cur = parents[cur]
            guard += 1
        depths.append(depth)
    return depths


def center(rect: BoundingRect) -> tuple[int, int]:
    """Centre point of a bounding rect, in screen pixels."""
    return (rect.left + rect.width // 2, rect.top + rect.height // 2)


def identity_bbox(identity: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """Extract a stored ``bbox`` from an identity dict (top-level or metadata)."""
    raw = identity.get("bbox")
    if raw is None and isinstance(identity.get("metadata"), dict):
        raw = identity["metadata"].get("bbox")
    if not raw or len(raw) < 4:
        return None
    try:
        return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
    except (TypeError, ValueError):
        return None


def match_score(
    element: CapturedElement,
    target_bbox: tuple[int, int, int, int] | None,
    target_name: str,
) -> float:
    """Combined IoU + text-match score in ``[0, 2]`` for re-acquisition."""
    score = 0.0
    if target_bbox is not None:
        rect = element.bounding_rect
        cur = (rect.left, rect.top, rect.width, rect.height)
        score += _iou(cur, target_bbox)
    if target_name and element.name.strip().lower() == target_name:
        score += 1.0
    return score


def _rect_contains(outer: BoundingRect, inner: BoundingRect) -> bool:
    """True iff ``PARENT_FRAC`` of ``inner``'s area lies inside ``outer``."""
    if inner.area <= 0:
        return False
    x1, y1 = max(outer.left, inner.left), max(outer.top, inner.top)
    x2, y2 = min(outer.right, inner.right), min(outer.bottom, inner.bottom)
    if x2 <= x1 or y2 <= y1:
        return False
    overlap = (x2 - x1) * (y2 - y1)
    return overlap >= PARENT_FRAC * inner.area


def _iou(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> float:
    """IoU of two ``(l, t, w, h)`` boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


__all__ = [
    "MIN_SIDE",
    "PARENT_FRAC",
    "keep_element",
    "yield_with_containment",
    "containment_parents",
    "containment_depths",
    "center",
    "identity_bbox",
    "match_score",
]
