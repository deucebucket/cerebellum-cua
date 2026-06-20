"""Image-token estimates for the demo's a11y-vs-pixels comparison.

Pure, dependency-free. Uses Anthropic's documented image-token heuristic,
``tokens ~= (width * height) / 750`` (https://docs.anthropic.com/), as a coarse,
clearly-labeled ESTIMATE of what a vision model spends to ingest an image at a
given resolution. Pairs with ``gateway.budget.estimate_tokens`` (the JSON/text
estimator) so a tutorial can price both the structured a11y response and an
equivalent screenshot from real dimensions.
"""

from __future__ import annotations

#: Pixels per image token (Anthropic's documented heuristic).
_PX_PER_TOKEN = 750


def image_tokens(width: int, height: int) -> int:
    """Estimated image tokens for a ``width`` x ``height`` frame (>=1 if non-empty)."""
    px = max(0, int(width)) * max(0, int(height))
    if px <= 0:
        return 0
    return max(1, round(px / _PX_PER_TOKEN))


def bbox_image_tokens(bbox: tuple[int, int, int, int]) -> int:
    """Estimated image tokens for an element crop ``(x, y, w, h)``."""
    _x, _y, w, h = bbox
    return image_tokens(w, h)


__all__ = ["image_tokens", "bbox_image_tokens"]
