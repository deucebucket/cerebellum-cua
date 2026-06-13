"""Breadth-first UIA tree traversal (spec Part 3 ``build_matrix`` traversal loop).

``walk`` yields plain ``(element, depth, parent_element)`` tuples for every node
that passes :func:`should_include`. It does NOT build the Snapshot — assembling
``Element`` dataclasses, row ids, and relationships is the matrix layer's job.

Workarounds applied during traversal:
  * Failure 4 (recursive-walk latency): prefer a single ``GetChildren`` per node
    (a FindAll-with-TrueCondition over direct children) instead of per-property
    COM round-trips while descending.
  * Failure 7 (MSAA-proxy reparenting / cycles): a visited-set keyed by RuntimeId
    hash dedups elements so a reparented node is never walked twice.
  * Failure 2 (virtualized subtrees): List / DataGrid / Document containers are
    stabilized (children forced to realize) before their children are read.

No ``uiautomation`` import here; ``root`` is a duck-typed live element.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import ControlType
from cerebellum_cua.uia.patterns import safe_get_property
from cerebellum_cua.uia.predicate import should_include
from cerebellum_cua.uia.stabilize import stabilize_virtualized

# RuntimeId PropertyId, used to derive a stable visited-set key (Failure 7).
RUNTIME_ID_PROPERTY_ID = 30001

# Containers whose children may be virtualized and must be stabilized first.
_VIRTUALIZED_TYPES = {
    int(ControlType.LIST),
    int(ControlType.DATA_GRID),
    int(ControlType.DOCUMENT),
}


def _runtime_key(element: Any) -> Any:
    """Stable dedup key: RuntimeId tuple if readable, else object id fallback."""
    rid = safe_get_property(element, RUNTIME_ID_PROPERTY_ID, None)
    if rid:
        try:
            return tuple(rid)
        except TypeError:
            return rid
    return id(element)


def _children_of(element: Any) -> list[Any]:
    """Return direct children, stabilizing virtualized containers first."""
    try:
        ct = element.ControlType
    except (AttributeError, Exception):  # noqa: BLE001
        ct = None
    if ct in _VIRTUALIZED_TYPES:
        stabilize_virtualized(element)
    try:
        return list(element.GetChildren())
    except (AttributeError, Exception):  # noqa: BLE001
        return []


def walk(
    root: Any, config: MatrixConfig
) -> Iterator[tuple[Any, int, Any | None]]:
    """Breadth-first walk yielding ``(element, depth, parent)`` for kept nodes.

    Args:
        root: The duck-typed live root/target element to descend from.
        config: Active :class:`MatrixConfig` (depth caps + inclusion knobs).

    Yields:
        ``(element, depth, parent_element)`` for each element passing
        :func:`should_include`. The root is yielded with ``parent=None``.
    """
    if root is None:
        return

    # Queue of (element, depth, parent_element); FIFO -> breadth-first.
    queue: list[tuple[Any, int, Any | None]] = [(root, 0, None)]
    visited: set[Any] = set()

    while queue:
        element, depth, parent = queue.pop(0)

        key = _runtime_key(element)
        if key in visited:
            continue
        visited.add(key)

        if not should_include(element, depth, None, {}, config):
            continue

        yield element, depth, parent

        if depth >= config.max_depth:
            continue

        for child in _children_of(element):
            queue.append((child, depth + 1, element))
