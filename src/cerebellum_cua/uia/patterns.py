"""UIA control-pattern extraction (spec Part 3, ``pattern_map`` + ``_safe_get_property``).

``PATTERN_MAP`` maps the canonical Cerebellum CUA pattern names to the raw Microsoft
UIA PatternId integers. ``safe_get_property`` is the spec's cached-then-current
property getter (Failure 9 workaround). ``extract_patterns`` probes each pattern
and records support, plus toggle state / current value where applicable.

No ``uiautomation`` import: elements are duck-typed and accessed defensively.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.uia._predicate_rules import (
    EXPAND_COLLAPSE_PATTERN,
    GRID_PATTERN,
    INVOKE_PATTERN,
    RANGE_VALUE_PATTERN,
    SCROLL_PATTERN,
    SELECTION_PATTERN,
    TABLE_PATTERN,
    TOGGLE_PATTERN,
    VALUE_PATTERN,
    VALUE_VALUE_PROPERTY_ID,
    WINDOW_PATTERN,
)

# Canonical pattern name -> raw UIA PatternId (spec Part 3 pattern_map, full set).
PATTERN_MAP: dict[str, int] = {
    "invoke": INVOKE_PATTERN,
    "value": VALUE_PATTERN,
    "range_value": RANGE_VALUE_PATTERN,
    "scroll": SCROLL_PATTERN,
    "expand_collapse": EXPAND_COLLAPSE_PATTERN,
    "toggle": TOGGLE_PATTERN,
    "selection": SELECTION_PATTERN,
    "grid": GRID_PATTERN,
    "table": TABLE_PATTERN,
    "window": WINDOW_PATTERN,
}

# ToggleToggleState PropertyId (Failure 9 cached-then-current fetch for toggles).
TOGGLE_STATE_PROPERTY_ID = 30086


def safe_get_property(element: Any, prop_id: int, default: Any = None) -> Any:
    """Read a UIA property, cached value first then current, falling to ``default``.

    Workaround for Failure 9 (cached values go stale / report unavailable on
    dynamic content): try ``GetCachedPropertyValue`` first, then immediately fall
    back to ``GetCurrentPropertyValue`` inside the same guard.
    """
    try:
        val = element.GetCachedPropertyValue(prop_id)
        if val is None or (isinstance(val, str) and val == ""):
            val = element.GetCurrentPropertyValue(prop_id)
        return val if val is not None else default
    except Exception:  # noqa: BLE001 - any COM/attribute failure -> default
        return default


def extract_patterns(element: Any) -> dict[str, dict[str, Any]]:
    """Probe every mapped pattern, returning ``{name: {"supported": bool, ...}}``.

    For supported ``toggle`` patterns the toggle state is attached; for supported
    ``value`` patterns the current value is attached. Each probe is individually
    guarded so one failing pattern never aborts the rest.
    """
    out: dict[str, dict[str, Any]] = {}
    for pname, pid in PATTERN_MAP.items():
        try:
            supported = bool(element.SupportsPattern(pid))
            entry: dict[str, Any] = {"supported": supported}
            if supported and pname == "toggle":
                entry["state"] = safe_get_property(element, TOGGLE_STATE_PROPERTY_ID, 0)
            if supported and pname == "value":
                entry["current"] = safe_get_property(
                    element, VALUE_VALUE_PROPERTY_ID, None
                )
            out[pname] = entry
        except Exception:  # noqa: BLE001 - treat probe failure as unsupported
            out[pname] = {"supported": False}
    return out
