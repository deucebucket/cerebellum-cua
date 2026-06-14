"""Sub-checks for the ``should_include`` predicate.

Each helper isolates one stage of the spec's exhaustive filtering predicate
(the design spec, Section 2). They operate on a *duck-typed* UIA control
wrapper: every attribute access is defensive (try/except), because a live COM
element raises ``COMError`` / ``AttributeError`` unpredictably. No
``uiautomation`` import happens here — pattern support is tested via the
library's ``Get*Pattern`` accessors (a pattern is supported when its getter
returns non-``None``).

Returning ``None`` from a stage means "no decision — keep checking"; returning
``True``/``False`` is a terminal verdict the caller must honour.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import ControlType
from cerebellum_cua.uia.patterns import _get_pattern

# ``uiautomation`` accessor method names for interactivity-implying patterns
# (spec probe order preserved). Pattern support = the getter returns non-None.
SCROLL_PATTERN = "GetScrollPattern"

_INTERACTIVE_PATTERNS: tuple[str, ...] = (
    "GetInvokePattern",
    "GetValuePattern",
    "GetRangeValuePattern",
    "GetSelectionPattern",
    "GetTogglePattern",
    "GetExpandCollapsePattern",
    SCROLL_PATTERN,
)

# Browser/Chromium framework ids that trigger depth-capping + Document anchors.
_BROWSER_FRAMEWORKS = {"chrome", "mozilla", "edge"}

# Empty-leaf pruning applies only to these structural container types.
_EMPTY_LEAF_TYPES = {
    int(ControlType.PANE),
    int(ControlType.GROUP),
    int(ControlType.CUSTOM),
}


def check_control_type(element: Any, config: MatrixConfig) -> bool | None:
    """ControlType fast-path exclusion. Returns False to prune, else None."""
    ct = element.ControlType
    if ct in config.excluded_control_types:
        return False
    return None


def check_bounding_rect(element: Any, config: MatrixConfig) -> bool | None:
    """BoundingRectangle sanity + size filter. Returns False to prune, else None."""
    try:
        rect = element.BoundingRectangle
        if rect is None or rect.width() < 1 or rect.height() < 1:
            if not config.include_zero_sized:
                return False
        # Negative coords usually indicate off-desktop / minimized virtual items.
        if (rect.left < -10000 or rect.top < -10000) and not config.include_negative_rect:
            return False
    except (AttributeError, Exception):  # noqa: BLE001 - mirror spec's broad guard
        if not config.include_on_rect_error:
            return False
    return None


def check_visibility(element: Any, config: MatrixConfig) -> bool | None:
    """Offscreen / invisible handling. Returns False to prune, else None.

    Fails open: many custom controls misreport these flags, so any access error
    leaves the element in (spec note).
    """
    try:
        if element.IsOffscreen and not config.include_offscreen:
            return False
        if not element.IsVisible and not config.include_invisible:
            return False
    except (AttributeError, Exception):  # noqa: BLE001
        pass
    return None


def read_identity(element: Any) -> tuple[str, str, str]:
    """Return lower-cased (name, classname, automation_id), failing to empty."""
    try:
        name = (element.Name or "").strip().lower()
        classname = (element.ClassName or "").strip().lower()
        automation_id = (element.AutomationId or "").strip().lower()
        return name, classname, automation_id
    except (AttributeError, Exception):  # noqa: BLE001
        return "", "", ""


def check_noise_substrings(
    name: str, classname: str, config: MatrixConfig
) -> bool | None:
    """Name/ClassName noise-keyword filter. Returns False to prune, else None."""
    subs = config.noise_substrings
    if any(s in name for s in subs) or any(s in classname for s in subs):
        if not config.force_include_noise:
            return False
    return None


def read_framework(element: Any) -> str:
    """Lower-cased FrameworkId, failing to empty string."""
    try:
        return (element.FrameworkId or "").strip().lower()
    except (AttributeError, Exception):  # noqa: BLE001
        return ""


def check_framework(
    element: Any, framework: str, ct: int, depth: int, config: MatrixConfig
) -> bool | None:
    """Framework-specific early exits / forced inclusions.

    Returns False (browser depth cap), True (WinForms DataGrid root), or None.
    """
    try:
        if framework in _BROWSER_FRAMEWORKS:
            if depth > config.browser_max_depth and not config.force_full_browser_tree:
                return False
        if framework == "winform" and ct == int(ControlType.DATA_GRID):
            # Always keep DataGrid root even if empty; children may be virtualized.
            return True
    except (AttributeError, Exception):  # noqa: BLE001
        pass
    return None


def compute_interactive(element: Any) -> bool:
    """Interactive scoring: focusable/enabled/focused OR supports an action pattern."""
    interactive = False
    try:
        if element.IsKeyboardFocusable or element.IsEnabled or element.HasKeyboardFocus:
            interactive = True
        for getter_name in _INTERACTIVE_PATTERNS:
            try:
                if _get_pattern(element, getter_name) is not None:
                    interactive = True
                    break
            except Exception:  # noqa: BLE001 - COMError per-pattern, keep probing
                continue
    except (AttributeError, Exception):  # noqa: BLE001
        pass
    return interactive


def read_child_count(element: Any) -> int:
    """Child count via GetChildren, falling back to GetChildrenCount, else 0."""
    try:
        return len(element.GetChildren())
    except (AttributeError, Exception):  # noqa: BLE001
        try:
            return element.GetChildrenCount()
        except (AttributeError, Exception):  # noqa: BLE001
            return 0


def check_empty_leaf(
    child_count: int,
    name: str,
    automation_id: str,
    interactive: bool,
    ct: int,
    depth: int,
) -> bool | None:
    """Empty-leaf pruning: childless, unlabelled, non-interactive containers."""
    if (
        child_count == 0
        and not name
        and not automation_id
        and not interactive
        and ct in _EMPTY_LEAF_TYPES
        and depth > 2
    ):
        return False
    return None


def read_has_value(element: Any) -> bool:
    """True iff the element exposes a non-empty ValuePattern value (else Name)."""
    try:
        pattern = _get_pattern(element, "GetValuePattern")
        if pattern is not None:
            val = getattr(pattern, "Value", None)
            if val not in (None, ""):
                return True
        name = getattr(element, "Name", None)
        if name not in (None, ""):
            return True
    except (AttributeError, Exception):  # noqa: BLE001
        pass
    return False


def read_is_content_element(element: Any) -> bool | None:
    """IsContentElement flag; None when the attribute is unreadable."""
    try:
        return bool(element.IsContentElement)
    except (AttributeError, Exception):  # noqa: BLE001
        return None
