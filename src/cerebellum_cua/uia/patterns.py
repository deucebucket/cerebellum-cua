"""UIA control-pattern extraction (spec Part 3, ``pattern_map`` + ``_safe_get_property``).

``PATTERN_MAP`` maps the canonical Cerebellum CUA pattern names to the
``uiautomation`` accessor method names (``GetInvokePattern`` etc.). A pattern is
"supported" when its getter returns a non-``None`` object. ``safe_get_property``
reads a named control attribute defensively, and ``extract_patterns`` probes
every mapped pattern, attaching toggle state / current value where applicable.

No ``uiautomation`` import: controls are duck-typed and accessed defensively.
"""

from __future__ import annotations

from typing import Any

# Canonical pattern name -> ``uiautomation`` accessor method name. Pattern support
# is "getter returns non-None"; there is no integer PatternId in this library.
PATTERN_MAP: dict[str, str] = {
    "invoke": "GetInvokePattern",
    "value": "GetValuePattern",
    "range_value": "GetRangeValuePattern",
    "scroll": "GetScrollPattern",
    "expand_collapse": "GetExpandCollapsePattern",
    "toggle": "GetTogglePattern",
    "selection": "GetSelectionPattern",
    "grid": "GetGridPattern",
    "table": "GetTablePattern",
    "window": "GetWindowPattern",
}

# Map the small set of PropertyId integers callers still pass to the real
# ``uiautomation`` Control attribute names (the library reads them live).
_PROPERTY_NAMES: dict[int, str] = {
    30003: "ControlType",
    30005: "Name",
    30012: "ClassName",
    30011: "AutomationId",
    30001: "RuntimeId",
    30007: "BoundingRectangle",
    30017: "FrameworkId",
    30024: "IsEnabled",
    30009: "IsKeyboardFocusable",
    30008: "HasKeyboardFocus",
    30022: "IsOffscreen",
    30016: "IsContentElement",
    30002: "ProcessId",
    30020: "NativeWindowHandle",
}

# ValueValue / ToggleToggleState are not plain attributes; they live on patterns.
_VALUE_VALUE_PROPERTY_ID = 30045
_TOGGLE_STATE_PROPERTY_ID = 30086


def _get_pattern(control: Any, getter_name: str) -> Any:
    """Call a ``Get*Pattern`` accessor, returning the pattern or ``None``."""
    getter = getattr(control, getter_name, None)
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - any COM/attribute failure -> unsupported
        return None


def safe_get_property(element: Any, prop: Any, default: Any = None) -> Any:
    """Read a UIA control property defensively, falling back to ``default``.

    ``prop`` may be the attribute name (e.g. ``"Name"``) or one of the legacy
    PropertyId integers callers still pass; integers are mapped to their real
    ``uiautomation`` attribute name. ``RuntimeId`` resolves via ``GetRuntimeId``.
    The ValueValue / ToggleToggleState pseudo-properties resolve through their
    owning patterns.
    """
    try:
        if prop == _VALUE_VALUE_PROPERTY_ID:
            pattern = _get_pattern(element, "GetValuePattern")
            val = getattr(pattern, "Value", None) if pattern is not None else None
            return val if val is not None else default
        if prop == _TOGGLE_STATE_PROPERTY_ID:
            pattern = _get_pattern(element, "GetTogglePattern")
            val = getattr(pattern, "ToggleState", None) if pattern is not None else None
            return val if val is not None else default

        name = _PROPERTY_NAMES.get(prop, prop) if isinstance(prop, int) else prop
        if name == "RuntimeId":
            val = element.GetRuntimeId()
            return val if val is not None else default

        val = getattr(element, name, None)
        if val is None or (isinstance(val, str) and val == ""):
            return default
        return val
    except Exception:  # noqa: BLE001 - any COM/attribute failure -> default
        return default


def extract_patterns(element: Any) -> dict[str, dict[str, Any]]:
    """Probe every mapped pattern, returning ``{name: {"supported": bool, ...}}``.

    A pattern is supported when its ``Get*Pattern`` accessor returns non-``None``.
    For a supported ``toggle`` the toggle state is attached; for a supported
    ``value`` the current value is attached. Each probe is individually guarded so
    one failing pattern never aborts the rest.
    """
    out: dict[str, dict[str, Any]] = {}
    for pname, getter_name in PATTERN_MAP.items():
        try:
            pattern = _get_pattern(element, getter_name)
            supported = pattern is not None
            entry: dict[str, Any] = {"supported": supported}
            if supported and pname == "toggle":
                entry["state"] = getattr(pattern, "ToggleState", 0)
            if supported and pname == "value":
                entry["current"] = getattr(pattern, "Value", None)
            out[pname] = entry
        except Exception:  # noqa: BLE001 - treat probe failure as unsupported
            out[pname] = {"supported": False}
    return out
