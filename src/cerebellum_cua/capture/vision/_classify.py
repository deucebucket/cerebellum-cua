"""Heuristic control-type guessing for vision-detected regions.

A screenshot exposes no roles, so the type of each detected region is *inferred*
from its geometry and text using conservative, documented heuristics. The output
is a :class:`cerebellum_cua.model.ControlType` int — the SAME cross-platform
taxonomy the a11y backends emit — so everything downstream stays uniform.

The heuristics deliberately fall back to ``CUSTOM`` whenever the shape is
ambiguous: a wrong-but-confident guess is worse than an honest "unknown" the
agent can still drive by coordinates.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.model import ControlType

#: A region wider than this multiple of its height is "wide and short".
_WIDE_RATIO = 2.5
#: A region taller than this multiple of its width is "tall and thin".
_TALL_RATIO = 3.0
#: Pixel side below which a thin strip reads as a scrollbar rather than a column.
_THIN_SIDE = 28
#: Word count at or below which text reads as a short label (button-like).
_SHORT_WORDS = 3
#: Area (px^2) above which a plain box reads as a window/pane container.
_CONTAINER_AREA = 120_000


def classify(
    bbox: tuple[int, int, int, int],
    text: str,
    shape_hints: dict[str, Any] | None = None,
) -> int:
    """Guess a :class:`ControlType` int for a detected region.

    Args:
        bbox: ``(left, top, width, height)`` in pixels.
        text: OCR text for the region (``""`` for a text-free box).
        shape_hints: Optional extra signals, e.g. ``{"kind": "labeled_box",
            "empty": True, "caret": True, "depth": 0}``. ``kind`` is the
            detector provenance; ``empty``/``caret`` mark input-looking boxes;
            ``depth`` (0 = outermost) helps tag the top container as a WINDOW.

    Returns:
        A ControlType int. Conservative: ambiguous shapes return ``CUSTOM``.
    """
    hints = shape_hints or {}
    _, _, w, h = bbox
    if w <= 0 or h <= 0:
        return int(ControlType.CUSTOM)

    words = text.split()
    has_text = bool(words)
    kind = str(hints.get("kind", ""))
    is_box = kind in ("box", "labeled_box")

    # 1. Tall, thin strips read as scrollbars (before container sizing).
    if h >= _TALL_RATIO * w and w <= _THIN_SIDE:
        return int(ControlType.SCROLL_BAR)
    # Wide, very short text-free strips read as separators/toolbars-ish noise.
    if is_box and not has_text and h <= _THIN_SIDE and w >= _WIDE_RATIO * h:
        return int(ControlType.SEPARATOR)

    # 2. An empty / caret-bearing box that looks like a single-line field -> EDIT.
    if is_box and (hints.get("caret") or (hints.get("empty") and not has_text)):
        if w >= _WIDE_RATIO * h:
            return int(ControlType.EDIT)

    # 3. A large container box -> WINDOW at depth 0, else PANE.
    if is_box and (w * h) >= _CONTAINER_AREA:
        if int(hints.get("depth", 1)) <= 0:
            return int(ControlType.WINDOW)
        return int(ControlType.PANE)

    # 4. A compact box carrying short text reads as a button.
    if is_box and has_text and len(words) <= _SHORT_WORDS and w >= _WIDE_RATIO * h:
        return int(ControlType.BUTTON)
    if kind == "labeled_box" and has_text and len(words) <= _SHORT_WORDS:
        return int(ControlType.BUTTON)

    # 5. Text with no box backing it is just static text.
    if has_text and kind == "text":
        return int(ControlType.TEXT)
    if has_text:
        return int(ControlType.TEXT)

    # 6. Anything else: a plain mid-size box -> PANE; otherwise unknown.
    if is_box:
        return int(ControlType.PANE)
    return int(ControlType.CUSTOM)


__all__ = ["classify"]
