"""Unit tests for vision detection / classification / wireframe — no cv2/tesseract.

The detection ALGORITHM is exercised by injecting fake OCR results and fake
contour boxes straight into :func:`detect_regions`, so neither OpenCV nor
Tesseract (nor a display) is needed. ``classify`` and ``render_ascii`` are pure.
"""

from __future__ import annotations

from cerebellum_cua.capture.base import CapturedElement
from cerebellum_cua.capture.vision._ascii import render_ascii
from cerebellum_cua.capture.vision._classify import classify
from cerebellum_cua.capture.vision.detect import DetectedRegion, detect_regions
from cerebellum_cua.model import BoundingRect, ControlType


class _FakeImage:
    """Stand-in image exposing only a numpy-style ``shape`` for area filtering."""

    def __init__(self, width: int, height: int) -> None:
        self.shape = (height, width, 3)


# --- detect_regions: merging text into boxes ----------------------------------
def test_text_inside_box_becomes_labeled_box() -> None:
    image = _FakeImage(1000, 800)
    cv = [(100, 100, 120, 40)]  # a button-ish box
    ocr = [{"bbox": (110, 108, 60, 20), "text": "OK", "conf": 0.9}]
    regions = detect_regions(image, ocr=ocr, cv=cv)
    labeled = [r for r in regions if r.kind == "labeled_box"]
    assert len(labeled) == 1
    assert labeled[0].text == "OK"
    assert labeled[0].bbox == (100, 100, 120, 40)
    # The standalone text region was absorbed, not duplicated.
    assert not any(r.kind == "text" and r.text == "OK" for r in regions)


def test_text_outside_any_box_stays_standalone() -> None:
    image = _FakeImage(1000, 800)
    cv = [(100, 100, 120, 40)]
    ocr = [{"bbox": (500, 500, 80, 20), "text": "Hello", "conf": 0.8}]
    regions = detect_regions(image, ocr=ocr, cv=cv)
    standalone = [r for r in regions if r.text == "Hello"]
    assert len(standalone) == 1
    assert standalone[0].kind == "text"


def test_text_attaches_to_smallest_containing_box() -> None:
    image = _FakeImage(1000, 800)
    cv = [(0, 0, 400, 400), (50, 50, 100, 40)]  # outer panel + inner button
    ocr = [{"bbox": (60, 58, 50, 20), "text": "Save", "conf": 0.95}]
    regions = detect_regions(image, ocr=ocr, cv=cv)
    inner = [r for r in regions if r.bbox == (50, 50, 100, 40)]
    assert inner and inner[0].text == "Save"


# --- detect_regions: filtering + dedup ----------------------------------------
def test_sliver_boxes_without_text_are_filtered() -> None:
    image = _FakeImage(1000, 800)
    cv = [(10, 10, 3, 3), (10, 10, 200, 60)]  # a 3px sliver + a real box
    regions = detect_regions(image, ocr=[], cv=cv)
    assert all(r.bbox != (10, 10, 3, 3) for r in regions)
    assert any(r.bbox == (10, 10, 200, 60) for r in regions)


def test_full_canvas_box_is_dropped() -> None:
    image = _FakeImage(1000, 800)
    cv = [(0, 0, 1000, 800), (100, 100, 200, 60)]
    regions = detect_regions(image, ocr=[], cv=cv)
    assert all(r.bbox != (0, 0, 1000, 800) for r in regions)


def test_near_duplicate_boxes_are_deduped() -> None:
    image = _FakeImage(1000, 800)
    cv = [(100, 100, 200, 60), (101, 101, 199, 59)]  # ~identical
    regions = detect_regions(image, ocr=[], cv=cv)
    boxes = [r for r in regions if 95 <= r.bbox[0] <= 105]
    assert len(boxes) == 1


def test_dedup_prefers_labeled_region() -> None:
    image = _FakeImage(1000, 800)
    cv = [(100, 100, 200, 60), (101, 101, 199, 59)]
    ocr = [{"bbox": (110, 110, 40, 20), "text": "Go", "conf": 0.9}]
    regions = detect_regions(image, ocr=ocr, cv=cv)
    surviving = [r for r in regions if 95 <= r.bbox[0] <= 105]
    assert len(surviving) == 1
    assert surviving[0].text == "Go"


def test_empty_ocr_tokens_are_ignored() -> None:
    regions = detect_regions(None, ocr=[{"bbox": (1, 1, 5, 5), "text": "  "}], cv=[])
    assert regions == []


def test_results_sorted_top_to_bottom_left_to_right() -> None:
    image = _FakeImage(1000, 800)
    cv = [(300, 300, 50, 50), (10, 10, 50, 50), (10, 200, 50, 50)]
    regions = detect_regions(image, ocr=[], cv=cv)
    tops = [r.bbox[1] for r in regions]
    assert tops == sorted(tops)


# --- classify -----------------------------------------------------------------
def test_classify_wide_short_text_is_button() -> None:
    assert classify((0, 0, 120, 36), "OK", {"kind": "labeled_box"}) == int(
        ControlType.BUTTON
    )


def test_classify_large_box_depth0_is_window() -> None:
    assert classify((0, 0, 800, 600), "", {"kind": "box", "depth": 0}) == int(
        ControlType.WINDOW
    )


def test_classify_large_box_depth1_is_pane() -> None:
    assert classify((0, 0, 800, 600), "", {"kind": "box", "depth": 1}) == int(
        ControlType.PANE
    )


def test_classify_text_only_is_text() -> None:
    assert classify((0, 0, 200, 20), "some label here", {"kind": "text"}) == int(
        ControlType.TEXT
    )


def test_classify_tall_thin_is_scrollbar() -> None:
    assert classify((0, 0, 16, 400), "", {"kind": "box"}) == int(
        ControlType.SCROLL_BAR
    )


def test_classify_empty_wide_box_is_edit() -> None:
    ct = classify((0, 0, 300, 30), "", {"kind": "box", "empty": True})
    assert ct == int(ControlType.EDIT)


def test_classify_ambiguous_falls_back_to_custom() -> None:
    assert classify((0, 0, 0, 0), "", {}) == int(ControlType.CUSTOM)


# --- render_ascii -------------------------------------------------------------
def _elem(left: int, top: int, w: int, h: int, name: str) -> CapturedElement:
    return CapturedElement(
        control_type=int(ControlType.PANE),
        name=name,
        bounding_rect=BoundingRect(left=left, top=top, width=w, height=h),
    )


def test_render_ascii_is_bounded_grid() -> None:
    elements = [_elem(0, 0, 800, 600, "Window"), _elem(50, 50, 120, 40, "Save")]
    art = render_ascii(elements, cols=40, rows=12)
    lines = art.split("\n")
    assert len(lines) == 12
    assert all(len(line) == 40 for line in lines)


def test_render_ascii_includes_truncated_labels() -> None:
    elements = [_elem(0, 0, 800, 600, "MainWindow")]
    art = render_ascii(elements, cols=60, rows=16)
    assert "Main" in art
    assert "+" in art and "|" in art and "-" in art


def test_render_ascii_empty_returns_blank_grid() -> None:
    art = render_ascii([], cols=20, rows=5)
    lines = art.split("\n")
    assert len(lines) == 5
    assert all(line.strip() == "" for line in lines)


def test_detected_region_dataclass_defaults() -> None:
    region = DetectedRegion(bbox=(1, 2, 3, 4))
    assert region.text == "" and region.kind == "box" and region.confidence == 0.0
