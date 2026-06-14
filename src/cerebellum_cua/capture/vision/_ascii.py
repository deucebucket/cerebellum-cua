"""Compact ASCII wireframe of a structured vision layout.

This is a *glanceable text map*, not pixel ASCII-art: each detected element is
drawn as a small labelled box at its scaled position on a fixed character grid,
so a human (or an LLM eyeballing the capture) can see the layout structure at a
glance. It reads :class:`CapturedElement` records (bounding rects + names), scales
them into a ``cols x rows`` grid, and stamps a bordered rectangle with a truncated
label for each.
"""

from __future__ import annotations

from collections.abc import Sequence

from cerebellum_cua.capture.base import CapturedElement


def render_ascii(
    elements: Sequence[CapturedElement], cols: int = 80, rows: int = 24
) -> str:
    """Render ``elements`` as a labelled wireframe on a ``cols x rows`` grid.

    Args:
        elements: Captured elements with populated ``bounding_rect`` / ``name``.
        cols: Grid width in characters (>= 8).
        rows: Grid height in characters (>= 4).

    Returns:
        A multi-line string of exactly ``rows`` lines, each ``cols`` chars wide.
        Boxes are drawn back-to-front (largest first) so small inner boxes show.
    """
    cols = max(8, int(cols))
    rows = max(4, int(rows))
    grid = [[" "] * cols for _ in range(rows)]

    bounds = _content_bounds(elements)
    if bounds is None:
        return _to_text(grid)
    min_x, min_y, span_x, span_y = bounds

    # Largest first so smaller boxes overwrite and remain visible.
    ordered = sorted(elements, key=lambda e: -e.bounding_rect.area)
    for element in ordered:
        rect = element.bounding_rect
        x0 = _scale(rect.left - min_x, span_x, cols)
        y0 = _scale(rect.top - min_y, span_y, rows)
        x1 = _scale(rect.right - min_x, span_x, cols)
        y1 = _scale(rect.bottom - min_y, span_y, rows)
        _draw_box(grid, x0, y0, max(x0 + 1, x1), max(y0 + 1, y1), element.name)
    return _to_text(grid)


def _content_bounds(
    elements: Sequence[CapturedElement],
) -> tuple[int, int, int, int] | None:
    """Return ``(min_x, min_y, span_x, span_y)`` over all rects, or ``None``."""
    rects = [e.bounding_rect for e in elements if e.bounding_rect.area > 0]
    if not rects:
        return None
    min_x = min(r.left for r in rects)
    min_y = min(r.top for r in rects)
    max_x = max(r.right for r in rects)
    max_y = max(r.bottom for r in rects)
    return (min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))


def _scale(value: int, span: int, size: int) -> int:
    """Map a pixel offset into ``[0, size - 1]`` grid cells."""
    pos = int(value / span * (size - 1)) if span > 0 else 0
    return max(0, min(size - 1, pos))


def _draw_box(
    grid: list[list[str]], x0: int, y0: int, x1: int, y1: int, label: str
) -> None:
    """Stamp a bordered rectangle with a truncated label into ``grid``."""
    rows, cols = len(grid), len(grid[0])
    x0, x1 = max(0, x0), min(cols - 1, x1)
    y0, y1 = max(0, y0), min(rows - 1, y1)
    for x in range(x0, x1 + 1):
        grid[y0][x] = "-"
        grid[y1][x] = "-"
    for y in range(y0, y1 + 1):
        grid[y][x0] = "|"
        grid[y][x1] = "|"
    grid[y0][x0] = grid[y0][x1] = grid[y1][x0] = grid[y1][x1] = "+"
    _stamp_label(grid, x0, y0, x1, label)


def _stamp_label(
    grid: list[list[str]], x0: int, y0: int, x1: int, label: str
) -> None:
    """Write a truncated label on the top edge interior, if it fits."""
    text = " ".join(label.split())
    inner = x1 - x0 - 1
    if not text or inner < 2:
        return
    snippet = text[: inner] if len(text) > inner else text
    for i, ch in enumerate(snippet):
        grid[y0][x0 + 1 + i] = ch


def _to_text(grid: list[list[str]]) -> str:
    """Join the grid into a newline-separated block."""
    return "\n".join("".join(row) for row in grid)


__all__ = ["render_ascii"]
