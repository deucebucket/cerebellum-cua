"""Epoch diffing: minimal patch between two snapshots (powers get_snapshot_diff).

A UI mutation produces a new :class:`~cerebellum_cua.model.Snapshot` with a fresh
epoch. To avoid re-serializing the whole tree, the gateway sends only the delta.
``diff_snapshots`` matches elements across epochs by stable identity and reports
which rows were added, removed, or modified, plus per-field patches.

Matching strategy (most stable first):
  1. ``uia_runtime_id_hash`` — provider-stable while the session lives.
  2. ``metadata['composite_key']`` (Failure-3 key), or a recomputed equivalent.

Pure logic: stdlib + ``cerebellum_cua.model`` / ``.identity`` only. Deterministic.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.matrix.identity import composite_key
from cerebellum_cua.model import Element, Snapshot

# Element fields whose change makes a row "modified". row_id is excluded (it is an
# addressing index, not content) and is reported separately in each patch.
_TRACKED_FIELDS: tuple[str, ...] = (
    "name",
    "class_name",
    "automation_id",
    "control_type",
    "is_interactive",
    "is_content",
    "framework_id",
)


def _composite_for(element: Element) -> str:
    """Stable composite key for an element, recomputing if metadata lacks one."""
    cached = element.metadata.get("composite_key") if element.metadata else None
    if cached:
        return str(cached)
    parent = element.metadata.get("parent_row_id") if element.metadata else None
    return composite_key(
        element.name,
        element.class_name,
        element.control_type,
        element.bounding_rect,
        parent,
    )


def _identity(element: Element) -> str:
    """Cross-epoch match key: runtime-id hash if present, else composite key."""
    if element.uia_runtime_id_hash:
        return "rid:" + element.uia_runtime_id_hash
    return "ck:" + _composite_for(element)


def _index(snapshot: Snapshot) -> dict[str, Element]:
    """Map identity -> element. Later duplicates win (last-write traversal order)."""
    return {_identity(el): el for el in snapshot.elements}


def _field_changes(old: Element, new: Element) -> dict[str, dict[str, Any]]:
    """Per-field {old, new} diff over tracked scalar fields + rect + properties."""
    changes: dict[str, dict[str, Any]] = {}

    for fname in _TRACKED_FIELDS:
        old_val = getattr(old, fname)
        new_val = getattr(new, fname)
        if old_val != new_val:
            changes[fname] = {"old": old_val, "new": new_val}

    old_rect = old.bounding_rect.to_dict()
    new_rect = new.bounding_rect.to_dict()
    if old_rect != new_rect:
        changes["bounding_rect"] = {"old": old_rect, "new": new_rect}

    if old.properties != new.properties:
        changes["properties"] = {"old": old.properties, "new": new.properties}

    if old.patterns != new.patterns:
        changes["patterns"] = {"old": old.patterns, "new": new.patterns}

    return changes


def diff_snapshots(old: Snapshot, new: Snapshot) -> dict[str, Any]:
    """Compute the minimal delta from ``old`` to ``new``.

    Returns::

        {
          "added_row_ids":    [int, ...],   # rows present only in new
          "removed_row_ids":  [int, ...],   # rows present only in old
          "modified_row_ids": [int, ...],   # matched but field(s) changed
          "patches": [ {"row_id": int, "changes": {field: {old, new}}}, ... ],
        }

    Row ids in ``added``/``modified`` are *new*-snapshot ids; ``removed`` ids are
    *old*-snapshot ids. ``patches`` carry the new row_id.
    """
    old_by_id = _index(old)
    new_by_id = _index(new)

    old_keys = set(old_by_id)
    new_keys = set(new_by_id)

    added_row_ids = sorted(new_by_id[k].row_id for k in (new_keys - old_keys))
    removed_row_ids = sorted(old_by_id[k].row_id for k in (old_keys - new_keys))

    modified_row_ids: list[int] = []
    patches: list[dict[str, Any]] = []

    for key in new_keys & old_keys:
        new_el = new_by_id[key]
        changes = _field_changes(old_by_id[key], new_el)
        if changes:
            modified_row_ids.append(new_el.row_id)
            patches.append({"row_id": new_el.row_id, "changes": changes})

    modified_row_ids.sort()
    patches.sort(key=lambda p: p["row_id"])

    return {
        "added_row_ids": added_row_ids,
        "removed_row_ids": removed_row_ids,
        "modified_row_ids": modified_row_ids,
        "patches": patches,
    }
