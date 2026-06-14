"""AT-SPI element re-acquisition from a stored identity.

After a snapshot is persisted, a :class:`CapturedElement`'s live ``native_ref`` is
gone (it is never serialized). To act on an element later, the backend re-finds it
from the stable child-index path captured in ``metadata["atspi_path"]``: a
root-first chain of ``get_index_in_parent()`` values from the desktop down to the
node.

These helpers are pure tree-walk + verification logic; the backend supplies the
live ``Atspi`` module and the convert/adapter callables, so this module stays
binding-free and unit-testable against fakes.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.base import CapturedElement


def walk_path(node: Any, path: list[int]) -> Any:
    """Descend ``node`` along the child-index ``path``; ``None`` on any miss.

    Each step takes ``node.get_child_at_index(idx)`` after bounds-checking
    ``get_child_count()``. Any out-of-range index, ``None`` node, or live-bus
    error yields ``None`` rather than raising.
    """
    for idx in path:
        if node is None or idx < 0:
            return None
        try:
            if idx >= node.get_child_count():
                return None
            node = node.get_child_at_index(idx)
        except Exception:  # noqa: BLE001
            return None
    return node


def matches(element: CapturedElement, identity: dict[str, Any]) -> bool:
    """Loosely verify a re-acquired element against the stored identity.

    The role (from ``role``/``atspi_role``) must match when supplied; the name
    must match when both the stored and live names are non-empty. Empty stored
    fields are treated as "don't care" so a renamed-but-same-slot widget still
    re-acquires.
    """
    role = identity.get("role") or identity.get("atspi_role")
    if role and element.metadata.get("atspi_role") != role:
        return False
    name = identity.get("name")
    return not (name and element.name and element.name != name)


def reacquire_path(identity: dict[str, Any]) -> list[int] | None:
    """Extract a clean integer ``atspi_path`` from ``identity``, or ``None``."""
    path = identity.get("atspi_path")
    if not isinstance(path, list) or not path:
        return None
    try:
        return [int(i) for i in path]
    except (TypeError, ValueError):
        return None
