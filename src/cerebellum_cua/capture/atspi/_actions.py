"""AT-SPI action execution helpers (Action / EditableText / Value / Selection).

Pure, binding-free functions that drive a *live* AT-SPI accessible through the
GObject-Introspection interface pattern ``Atspi.<Interface>.<method>(acc, ...)``.

The real ``gi`` bindings expose interface methods as *module functions* taking the
accessible as the first argument (e.g. ``Atspi.Action.do_action(acc, i)``), not as
bound methods on the accessible. To stay import-safe (no ``gi`` at module scope)
and unit-testable with plain fakes, every call is routed through
:func:`_iface_call`, which tries, in order:

1. a bound method on the accessible itself (``acc.do_action(i)``) — what the fakes
   and pyatspi expose;
2. the ``Atspi.<Interface>.<method>`` module function with ``acc`` prepended — what
   the live gi bindings expose.

Any missing interface or unknown action raises :class:`ActionNotSupported` rather
than crashing the caller.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.base import ActionNotSupported

# Action names (case-insensitive) we treat as "activate this element".
_ACTIVATE_NAMES = ("click", "press", "activate", "jump")


def _atspi() -> Any:
    """Lazily import the ``Atspi`` GI module, or return ``None`` if unavailable."""
    try:
        import gi  # noqa: PLC0415

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # noqa: PLC0415
    except (ImportError, ValueError):
        return None
    return Atspi


def _iface_call(acc: Any, iface: str, method: str, *args: Any) -> Any:
    """Call ``method`` on ``acc`` via a bound method or the GI interface function.

    Tries ``acc.<method>(*args)`` first (fakes / pyatspi), then
    ``Atspi.<iface>.<method>(acc, *args)`` (live gi bindings). Raises
    :class:`ActionNotSupported` if neither route exists or the call fails.
    """
    bound = getattr(acc, method, None)
    if callable(bound):
        try:
            return bound(*args)
        except Exception as exc:  # noqa: BLE001
            raise ActionNotSupported(f"{iface}.{method} failed: {exc}") from exc
    atspi = _atspi()
    iface_obj = getattr(atspi, iface, None) if atspi is not None else None
    fn = getattr(iface_obj, method, None) if iface_obj is not None else None
    if not callable(fn):
        raise ActionNotSupported(f"element exposes no {iface}.{method}")
    try:
        return fn(acc, *args)
    except Exception as exc:  # noqa: BLE001
        raise ActionNotSupported(f"{iface}.{method} failed: {exc}") from exc


def _has_iface(acc: Any, iface: str, probe_method: str) -> bool:
    """True if ``acc`` supports ``iface`` (bound method or GI function present)."""
    if callable(getattr(acc, probe_method, None)):
        return True
    atspi = _atspi()
    iface_obj = getattr(atspi, iface, None) if atspi is not None else None
    return callable(getattr(iface_obj, probe_method, None))


def do_action(acc: Any, action: str) -> bool:
    """Invoke a named (or first activating) action via the Action interface.

    Picks the action whose name matches the requested ``action`` or any of the
    activate aliases (click/press/activate/jump), else index 0.
    """
    if not _has_iface(acc, "Action", "get_n_actions"):
        raise ActionNotSupported("element exposes no Action interface")
    n = int(_iface_call(acc, "Action", "get_n_actions"))
    want = action.lower()
    target_idx = 0
    for i in range(n):
        try:
            name = (_iface_call(acc, "Action", "get_action_name", i) or "").lower()
        except ActionNotSupported:
            continue
        if name == want:
            target_idx = i
            break
        if want in ("invoke", "click", "press") and name in _ACTIVATE_NAMES:
            target_idx = i
    return bool(_iface_call(acc, "Action", "do_action", target_idx))


def set_text(acc: Any, text: str) -> bool:
    """Set element text via ``Atspi.EditableText.set_text_contents``."""
    if not _has_iface(acc, "EditableText", "set_text_contents"):
        raise ActionNotSupported("element is not editable text")
    return bool(_iface_call(acc, "EditableText", "set_text_contents", text))


def set_value(acc: Any, value: float) -> bool:
    """Set a numeric value via ``Atspi.Value.set_current_value``."""
    if not _has_iface(acc, "Value", "set_current_value"):
        raise ActionNotSupported("element exposes no Value interface")
    return bool(_iface_call(acc, "Value", "set_current_value", float(value)))


def select(acc: Any) -> bool:
    """Select ``acc`` within its parent's Selection interface, else via Action.

    Tries the parent's ``Selection.select_child`` using this element's
    index-in-parent; falls back to the element's own select/activate action.
    """
    parent = _parent_of(acc)
    if parent is not None and _has_iface(parent, "Selection", "select_child"):
        idx = _index_in_parent(acc)
        if idx >= 0:
            return bool(_iface_call(parent, "Selection", "select_child", idx))
    return do_action(acc, "select")


def _parent_of(acc: Any) -> Any:
    getter = getattr(acc, "get_parent", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return None


def _index_in_parent(acc: Any) -> int:
    getter = getattr(acc, "get_index_in_parent", None)
    if not callable(getter):
        return -1
    try:
        return int(getter())
    except Exception:  # noqa: BLE001
        return -1
