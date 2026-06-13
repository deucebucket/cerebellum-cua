"""AT-SPI inclusion predicate — the Linux analogue of ``uia.should_include``.

Where the UIA predicate reads a live COM element, the AT-SPI walk has already
extracted the cheap, stable facts (control type, name, class, rect, state names)
during conversion, so this predicate works on plain values. It applies the same
config-driven noise pruning as the UIA path: offscreen / not-showing elements,
zero- or one-pixel rects, excluded control types, noise-substring names, and the
global depth cap. Structural anchors (windows, shallow nodes) are always kept.

Pure function, no ``gi``/``Atspi`` import — unit-testable with literal inputs.
"""

from __future__ import annotations

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import BoundingRect, ControlType

_WINDOW = int(ControlType.WINDOW)
_PANE = int(ControlType.PANE)

# State names (lowercase) the predicate inspects.
_STATE_SHOWING = "showing"
_STATE_VISIBLE = "visible"


def _is_offscreen(rect: BoundingRect) -> bool:
    """A rect fully in negative space / off the virtual desktop origin."""
    return rect.right <= 0 or rect.bottom <= 0


def atspi_should_include(
    element_ct: int,
    name: str,
    class_name: str,
    rect: BoundingRect,
    states: set[str],
    depth: int,
    config: MatrixConfig,
) -> bool:
    """Return True iff an AT-SPI element belongs in the canonical matrix.

    Args:
        element_ct: Canonical :class:`ControlType` int (already mapped from role).
        name: Element name.
        class_name: Element class/toolkit name.
        rect: Screen-coordinate :class:`BoundingRect`.
        states: Active AT-SPI state names (lowercase).
        depth: 0-based depth from the traversal root.
        config: Active :class:`MatrixConfig` supplying inclusion knobs.
    """
    # Hard global depth guard.
    if depth > config.max_depth:
        return False

    # ControlType fast-path exclusions (scrollbar, spinner, status bar, ...).
    if element_ct in config.excluded_control_types:
        return False

    # Structural anchors are always kept (cheap, before any noisier checks).
    if depth <= 1 or element_ct == _WINDOW:
        return True

    # Visibility / offscreen handling.
    if not config.include_invisible:
        # A node carrying explicit state but lacking "showing" is hidden chrome.
        if states and _STATE_SHOWING not in states and _STATE_VISIBLE not in states:
            return False
    if not config.include_offscreen and _is_offscreen(rect):
        return False

    # Zero- / one-pixel rects are hit-test or decorative noise.
    if not config.include_zero_sized:
        if rect.width <= 1 or rect.height <= 1:
            # Keep a tiny rect only if it carries an identity worth surfacing.
            if not name:
                return False

    # Noise-substring filter over name + class.
    if not config.force_include_noise:
        haystack = f"{name} {class_name}".lower()
        for needle in config.noise_substrings:
            if needle and needle in haystack:
                return False

    # Interactive-only mode prunes non-interactive deep panes.
    if config.interactive_only and depth > 1:
        interactive = bool(name) and element_ct != _PANE
        if not interactive:
            return False

    # Anonymous, empty filler panes deep in the tree are noise.
    if element_ct == _PANE and not name and not config.include_anonymous_leaves:
        if depth > 1:
            return False

    return True
