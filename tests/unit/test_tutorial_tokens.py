"""Unit tests for the tutorial image-token estimate helper."""

from __future__ import annotations

from cerebellum_cua.tutorial.tokens import bbox_image_tokens, image_tokens


def test_full_frame_estimate_matches_formula() -> None:
    # 1280x800 -> 1024000/750 -> ~1365
    assert image_tokens(1280, 800) == 1365


def test_focused_crop_is_much_smaller() -> None:
    assert bbox_image_tokens((0, 0, 120, 36)) == round(120 * 36 / 750)
    assert bbox_image_tokens((0, 0, 120, 36)) < image_tokens(1280, 800)


def test_zero_area_is_zero_and_tiny_is_at_least_one() -> None:
    assert image_tokens(0, 100) == 0
    assert image_tokens(1, 1) == 1
