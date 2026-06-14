"""Pure AT-SPI accessible -> :class:`CapturedElement` conversion.

Everything here operates on a *duck-typed* AT-SPI accessible: an object exposing
the subset of the ``Atspi.Accessible`` surface we read (``get_name``,
``get_role_name``, ``get_attributes``, ``get_state_set``, ``get_extents``,
``get_index_in_parent``, ``getApplication`` and interface-query helpers). That
means NO ``gi``/``Atspi`` import lives in this module, so the conversion logic is
exercised by plain fakes in the unit tests and never depends on a live a11y bus.

The backend (``backend.py``) is the only place that touches the real C bindings;
it adapts a live ``Atspi.Accessible`` into the duck-typed shape these functions
expect (mostly the real object already matches) and calls :func:`convert`.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.atspi.roles import control_type_for, interactive_type_for
from cerebellum_cua.capture.base import CapturedElement
from cerebellum_cua.model import BoundingRect, ControlType

# AT-SPI state names (as produced by our state-set reader) that carry meaning.
_STATE_ENABLED = "enabled"
_STATE_SENSITIVE = "sensitive"
_STATE_FOCUSABLE = "focusable"
_STATE_FOCUSED = "focused"
_STATE_VISIBLE = "visible"
_STATE_SHOWING = "showing"
_STATE_CHECKED = "checked"
_STATE_EXPANDABLE = "expandable"
_STATE_EXPANDED = "expanded"
_STATE_SELECTABLE = "selectable"
_STATE_SELECTED = "selected"

# Roles whose content is primary "data" the agent should treat as content.
_CONTENT_TYPES = {
    int(ControlType.TEXT),
    int(ControlType.DOCUMENT),
    int(ControlType.IMAGE),
    int(ControlType.HYPERLINK),
    int(ControlType.LIST_ITEM),
    int(ControlType.TREE_ITEM),
    int(ControlType.DATA_ITEM),
}


def read_states(accessible: Any) -> set[str]:
    """Return the set of active state *names* (lowercase strings).

    Accepts either an object whose ``get_state_set()`` yields something iterable
    of state names, or one already exposing a ``states`` iterable. Defensive: any
    failure yields an empty set rather than raising.
    """
    try:
        getter = getattr(accessible, "get_state_set", None)
        raw = getter() if getter else getattr(accessible, "states", None)
    except Exception:  # noqa: BLE001 - never let a live-bus hiccup crash convert
        return set()
    if raw is None:
        return set()
    # A real Atspi.StateSet is adapted by the backend into a list[str]; a fake may
    # pass a set/list directly.
    try:
        return {str(s).strip().lower() for s in raw}
    except TypeError:
        return set()


def read_attributes(accessible: Any) -> dict[str, str]:
    """Return the AT-SPI attribute dict (e.g. ``toolkit``, ``class``, ``id``)."""
    try:
        getter = getattr(accessible, "get_attributes", None)
        attrs = getter() if getter else {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(attrs, dict):
        return {}
    return {str(k): str(v) for k, v in attrs.items()}


def read_rect(accessible: Any) -> BoundingRect:
    """Read SCREEN-coordinate extents into a :class:`BoundingRect` (dpi=96)."""
    try:
        ext = accessible.get_extents()
    except Exception:  # noqa: BLE001
        return BoundingRect()
    if ext is None:
        return BoundingRect()
    try:
        return BoundingRect(
            left=int(ext.x),
            top=int(ext.y),
            width=int(ext.width),
            height=int(ext.height),
            dpi=96,
        )
    except (AttributeError, TypeError, ValueError):
        return BoundingRect()


def read_interfaces(accessible: Any) -> set[str]:
    """Return the set of supported AT-SPI interface names (e.g. ``Action``)."""
    try:
        getter = getattr(accessible, "get_interfaces", None)
        ifaces = getter() if getter else getattr(accessible, "interfaces", None)
    except Exception:  # noqa: BLE001
        return set()
    if not ifaces:
        return set()
    try:
        return {str(i) for i in ifaces}
    except TypeError:
        return set()


def _name(accessible: Any) -> str:
    try:
        return str(accessible.get_name() or "")
    except Exception:  # noqa: BLE001
        return ""


def _role_name(accessible: Any) -> str:
    try:
        return str(accessible.get_role_name() or "")
    except Exception:  # noqa: BLE001
        return ""


def _index_chain(accessible: Any) -> list[int]:
    """Build a stable runtime id from the get_index_in_parent ancestor chain.

    Walks parents collecting each node's index-in-parent, root-first. This is the
    AT-SPI analogue of a UIA RuntimeId: stable for the life of a tree, derivable
    without a live handle, and good enough to dedup and re-find a node.
    """
    chain: list[int] = []
    node: Any = accessible
    seen: set[int] = set()
    for _ in range(64):  # bound the walk; trees deeper than this are pathological
        if node is None or id(node) in seen:
            break
        seen.add(id(node))
        try:
            idx = int(node.get_index_in_parent())
        except Exception:  # noqa: BLE001
            idx = -1
        chain.append(idx)
        try:
            node = node.get_parent()
        except Exception:  # noqa: BLE001
            break
    chain.reverse()
    return chain


def _descriptor_chain(accessible: Any) -> tuple[list[dict[str, Any]], str]:
    """Build a role+name descriptor path for robust re-acquisition.

    Walks parents collecting one descriptor per level, root-first, then drops the
    desktop root itself so the chain starts at the desktop's child (the
    application) and ends at ``accessible``. Each descriptor is
    ``{"i": <index_in_parent, may be -1>, "role": <role name>, "name": <name>}``.

    AT-SPI returns ``-1`` for ``get_index_in_parent()`` on some real nodes
    (applications, title bars, fillers) even though they have a parent, so the
    index is only a hint; the role (and name when present) anchor the walk.

    Returns the descriptor list and the application name (top node under the
    desktop), the latter used as a re-acquisition anchor. Both are empty when the
    walk cannot reach the tree.
    """
    levels: list[dict[str, Any]] = []
    node: Any = accessible
    seen: set[int] = set()
    for _ in range(64):  # bound the walk; deeper trees are pathological
        if node is None or id(node) in seen:
            break
        seen.add(id(node))
        try:
            idx = int(node.get_index_in_parent())
        except Exception:  # noqa: BLE001
            idx = -1
        levels.append({"i": idx, "role": _role_name(node), "name": _name(node)})
        try:
            node = node.get_parent()
        except Exception:  # noqa: BLE001
            node = None
    levels.reverse()
    # Drop the desktop root: the re-acquire walk starts AT the desktop and
    # descends into its children, so the first descriptor must be the application.
    if levels:
        levels = levels[1:]
    app_name = levels[0]["name"] if levels else ""
    return levels, app_name


def _derive_patterns(states: set[str], interfaces: set[str]) -> dict[str, Any]:
    """Map supported AT-SPI interfaces/states to canonical pattern flags."""
    patterns: dict[str, Any] = {}
    if "Action" in interfaces:
        patterns["invoke"] = True
    if "Text" in interfaces or "EditableText" in interfaces:
        patterns["value"] = True
    if "Value" in interfaces:
        patterns["range_value"] = True
    if "Selection" in interfaces:
        patterns["selection"] = True
    if "Toggle" in interfaces or _STATE_CHECKED in states:
        patterns["toggle"] = _STATE_CHECKED in states
    if _STATE_EXPANDABLE in states:
        patterns["expand_collapse"] = _STATE_EXPANDED in states
    return patterns


def _derive_properties(
    states: set[str], attrs: dict[str, str]
) -> dict[str, Any]:
    """Build the canonical properties dict from states + attributes."""
    enabled = _STATE_ENABLED in states or _STATE_SENSITIVE in states
    return {
        "is_enabled": enabled,
        "is_focusable": _STATE_FOCUSABLE in states,
        "is_focused": _STATE_FOCUSED in states,
        "is_visible": _STATE_VISIBLE in states,
        "is_showing": _STATE_SHOWING in states,
        "is_selectable": _STATE_SELECTABLE in states,
        "is_selected": _STATE_SELECTED in states,
        "toolkit": attrs.get("toolkit", ""),
        "framework": attrs.get("toolkit", ""),
    }


def convert(accessible: Any) -> CapturedElement:
    """Convert one duck-typed AT-SPI accessible into a :class:`CapturedElement`."""
    role = _role_name(accessible)
    control_type = control_type_for(role)
    attrs = read_attributes(accessible)
    states = read_states(accessible)
    interfaces = read_interfaces(accessible)

    name = _name(accessible)
    class_name = attrs.get("class") or attrs.get("toolkit") or ""
    automation_id = attrs.get("id") or attrs.get("name") or ""
    toolkit = attrs.get("toolkit", "")

    rect = read_rect(accessible)
    properties = _derive_properties(states, attrs)
    patterns = _derive_patterns(states, interfaces)

    interactive_kind = interactive_type_for(role, interfaces)
    is_interactive = bool(interactive_kind) or properties["is_focusable"]
    is_content = control_type in _CONTENT_TYPES

    runtime_id = _index_chain(accessible)

    # ``runtime_id`` (the int index chain) is kept UNCHANGED for dedup/hashing.
    # ``atspi_path`` is a richer per-level descriptor list (role+name+index hint),
    # root-first from the desktop's child (the application) down to this node, so
    # the backend can re-acquire after a DB round-trip even when AT-SPI reports a
    # ``-1`` index mid-path. ``atspi_app`` anchors the walk at the right app.
    descriptors, app_name = _descriptor_chain(accessible)
    return CapturedElement(
        control_type=control_type,
        name=name,
        class_name=class_name,
        automation_id=automation_id,
        runtime_id=runtime_id or None,
        bounding_rect=rect,
        properties=properties,
        patterns=patterns,
        is_interactive=is_interactive,
        is_content=is_content,
        framework_id=toolkit,
        metadata={
            "atspi_role": role,
            "interactive_kind": interactive_kind,
            "atspi_path": descriptors,
            "atspi_app": app_name,
        },
        native_ref=accessible,
    )
