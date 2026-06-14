"""AT-SPI element re-acquisition from a stored identity.

After a snapshot is persisted, a :class:`CapturedElement`'s live ``native_ref`` is
gone (it is never serialized). To act on an element later, the backend re-finds it
from ``metadata["atspi_path"]``: a root-first list of per-level *descriptors*
(``{"i": <index hint>, "role": <role name>, "name": <name>}``) running from the
desktop's child (the application) down to the node.

The index alone is unreliable: AT-SPI returns ``-1`` from
``get_index_in_parent()`` on real nodes (applications, title bars, fillers) even
when they have a parent, so those ``-1``s appear MID-PATH. The walk therefore uses
the index only as a hint and falls back to matching on role (+name).

These helpers are pure tree-walk + verification logic; the backend supplies the
live root and the convert/adapter callables, so this module stays binding-free and
unit-testable against fakes.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.base import CapturedElement

_MAX_DEPTH = 64


def _role_of(node: Any) -> str:
    try:
        return str(node.get_role_name() or "")
    except Exception:  # noqa: BLE001
        return ""


def _name_of(node: Any) -> str:
    try:
        return str(node.get_name() or "")
    except Exception:  # noqa: BLE001
        return ""


def _child_count(node: Any) -> int:
    try:
        return int(node.get_child_count())
    except Exception:  # noqa: BLE001
        return 0


def _child_at(node: Any, idx: int) -> Any:
    try:
        return node.get_child_at_index(idx)
    except Exception:  # noqa: BLE001
        return None


def _descriptor_matches(node: Any, role: str, name: str) -> bool:
    """True iff ``node``'s role matches ``role`` and (``name`` empty OR name matches)."""
    if node is None:
        return False
    if role and _role_of(node) != role:
        return False
    return not (name and _name_of(node) != name)


def _pick_child(node: Any, desc: dict[str, Any]) -> Any:
    """Choose the child of ``node`` satisfying descriptor ``desc``, or ``None``.

    Tries the index hint ``desc["i"]`` first (when in range and the role matches),
    then scans all children for the first whose role (and name, when given) match.
    """
    role = str(desc.get("role") or "")
    name = str(desc.get("name") or "")
    count = _child_count(node)
    if count <= 0:
        return None
    try:
        hint = int(desc.get("i", -1))
    except (TypeError, ValueError):
        hint = -1
    if 0 <= hint < count:
        cand = _child_at(node, hint)
        if cand is not None and _descriptor_matches(cand, role, name):
            return cand
    for i in range(count):
        cand = _child_at(node, i)
        if cand is not None and _descriptor_matches(cand, role, name):
            return cand
    return None


def walk_descriptors(root: Any, path: list[dict[str, Any]]) -> Any:
    """Descend ``root`` along the descriptor ``path``; ``None`` on any miss.

    At each level the index hint is tried first, then a role/name scan, so a
    mid-path ``-1`` index re-acquires by role/name. Every live call is guarded;
    the walk returns ``None`` rather than raising. The final node loosely matches
    its descriptor by construction.
    """
    node = root
    for desc in path[:_MAX_DEPTH]:
        if node is None:
            return None
        node = _pick_child(node, desc)
    return node


def walk_path(node: Any, path: list[int]) -> Any:
    """Back-compat int-index walk for old snapshots storing a plain ``list[int]``.

    Best-effort on the historical format: a non-negative index is followed
    directly; a ``-1`` (which AT-SPI emits for some real nodes) is skipped by
    descending into the single child when there is exactly one, else bailing.
    """
    for idx in path[:_MAX_DEPTH]:
        if node is None:
            return None
        if idx < 0:
            if _child_count(node) == 1:
                node = _child_at(node, 0)
                continue
            return None
        if not 0 <= idx < _child_count(node):
            return None
        node = _child_at(node, idx)
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


def reacquire_node(root: Any, identity: dict[str, Any]) -> Any:
    """Re-find the live node for ``identity`` under ``root`` (a desktop), or ``None``.

    Consumes ``identity["atspi_path"]``. The new format is a list of descriptor
    dicts walked via :func:`walk_descriptors`; the legacy format is a list of
    plain ints walked via :func:`walk_path`. Anything else yields ``None``.
    """
    path = identity.get("atspi_path")
    if not isinstance(path, list) or not path:
        return None
    if all(isinstance(p, dict) for p in path):
        return walk_descriptors(root, path)
    try:
        ints = [int(i) for i in path]
    except (TypeError, ValueError):
        return None
    return walk_path(root, ints)
