"""AT-SPI action execution helpers (Action + EditableText interfaces).

Pure, binding-free functions that drive a *live* AT-SPI accessible's Action and
EditableText interfaces. They probe both the pyatspi-style ``query*`` accessors
and the gi-style ``get_*_iface`` accessors, so they work against whichever shape
the runtime exposes (and against fakes in tests). Any missing interface raises
:class:`ActionNotSupported` rather than crashing the caller.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.base import ActionNotSupported

_ACTIVATE_NAMES = ("click", "press", "activate")


def do_action(acc: Any, action: str) -> bool:
    """Invoke a named (or first activating) action via the Action interface."""
    iface = _resolve_iface(acc, ("queryAction", "get_action_iface"), "Action")
    try:
        n = iface.get_n_actions()
    except Exception as exc:  # noqa: BLE001
        raise ActionNotSupported(f"element exposes no Action interface: {exc}") from exc
    target_idx = 0
    for i in range(n):
        try:
            nm = (iface.get_action_name(i) or "").lower()
        except Exception:  # noqa: BLE001
            continue
        if action.lower() in (nm, "invoke") or nm in _ACTIVATE_NAMES:
            target_idx = i
            if action.lower() == nm:
                break
    return bool(iface.do_action(target_idx))


def set_text(acc: Any, text: str) -> bool:
    """Set element text via the EditableText interface."""
    iface = _resolve_iface(
        acc, ("queryEditableText", "get_editable_text_iface"), "EditableText"
    )
    setter = getattr(iface, "set_text_contents", None)
    if setter is None:
        raise ActionNotSupported("element is not editable text")
    return bool(setter(text))


def _resolve_iface(acc: Any, accessors: tuple[str, ...], label: str) -> Any:
    """Return the requested interface object, or the accessible itself.

    pyatspi exposes ``acc.queryAction()``; gi exposes ``acc.get_action_iface()``;
    in some shapes the methods live directly on the accessible. We try the named
    accessors and fall back to the accessible so callers can duck-type.
    """
    for name in accessors:
        query = getattr(acc, name, None)
        if query is None:
            continue
        try:
            return query()
        except Exception as exc:  # noqa: BLE001
            raise ActionNotSupported(f"no {label} interface: {exc}") from exc
    return acc
