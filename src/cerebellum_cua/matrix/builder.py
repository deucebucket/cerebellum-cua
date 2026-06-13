"""Snapshot builder: walked elements -> canonical versioned matrix.

``build_snapshot`` consumes an already-extracted, ordered stream of elements and
produces a fully populated :class:`~cerebellum_cua.model.Snapshot`. It is PURE logic —
it never imports the uia or storage layers and never touches COM/DB. The caller
(the uia traversal engine, or a test fake) is responsible for walking the live
tree, running ``should_include``, and handing this layer plain element dicts /
dataclasses in traversal order.

Input contract (``walked``): an iterable of 3-tuples

    (element_data, depth, parent_row_id)

where
  * ``element_data`` is a ``dict`` (loose UIA extract) **or** anything with the
    :class:`~cerebellum_cua.model.Element` attribute surface (duck-typed);
  * ``depth`` is the 0-based traversal depth (root = 0);
  * ``parent_row_id`` is the already-assigned dense row_id of the parent, or
    ``None`` for roots.

Rows are assigned dense 0-based ``row_id`` in iteration order, so callers must
yield parents before children (the row_id they pass as ``parent_row_id`` must
already have been emitted). A mandatory ``PARENT_OF`` edge is emitted for every
parent->child pair; ``FIRST_CHILD_OF`` and ``NEXT_SIBLING_OF`` are added when
cheaply derivable from sibling order.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.matrix.identity import composite_key, runtime_id_hash
from cerebellum_cua.model import (
    BoundingRect,
    ChildStub,
    Element,
    Relationship,
    RelationshipCode,
    Snapshot,
)

WalkedItem = tuple[Any, int, "int | None"]


def _get(data: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or attribute-bearing object uniformly."""
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _coerce_rect(raw: Any) -> BoundingRect:
    """Normalize a rect given as a BoundingRect, dict, or None."""
    if isinstance(raw, BoundingRect):
        return raw
    if isinstance(raw, dict):
        return BoundingRect.from_dict(raw)
    return BoundingRect()


def _to_element(data: Any, row_id: int, parent_row_id: int | None) -> Element:
    """Build a fully-populated :class:`Element` from a loose extract.

    Computes ``uia_runtime_id_hash`` (preferring an existing hash, else hashing a
    raw ``runtime_id`` array) and stamps the Failure-3 composite key into
    ``metadata['composite_key']`` so the diff layer has a stable fallback match.
    """
    rect = _coerce_rect(_get(data, "bounding_rect"))
    control_type = int(_get(data, "control_type", 0) or 0)
    name = _get(data, "name", "") or ""
    class_name = _get(data, "class_name", "") or ""

    rid_hash = _get(data, "uia_runtime_id_hash", "") or ""
    if not rid_hash:
        rid_hash = runtime_id_hash(_get(data, "runtime_id"))

    ckey = composite_key(name, class_name, control_type, rect, parent_row_id)

    metadata = dict(_get(data, "metadata", {}) or {})
    metadata.setdefault("depth", int(_get(data, "depth", 0) or 0))
    metadata["composite_key"] = ckey
    metadata["parent_row_id"] = parent_row_id

    return Element(
        row_id=row_id,
        control_type=control_type,
        name=name,
        class_name=class_name,
        automation_id=_get(data, "automation_id", "") or "",
        uia_runtime_id_hash=rid_hash,
        bounding_rect=rect,
        properties=dict(_get(data, "properties", {}) or {}),
        patterns=dict(_get(data, "patterns", {}) or {}),
        is_interactive=bool(_get(data, "is_interactive", False)),
        is_content=bool(_get(data, "is_content", False)),
        framework_id=_get(data, "framework_id", "") or "",
        children_stub=ChildStub(),
        metadata=metadata,
    )


def build_snapshot(
    walked: Iterable[WalkedItem],
    epoch: int,
    *,
    target: dict[str, Any] | None = None,
    config: MatrixConfig | None = None,
) -> Snapshot:
    """Turn an ordered walk into a fully populated :class:`Snapshot`.

    See the module docstring for the ``walked`` contract. Returns a Snapshot with
    dense elements, PARENT_OF (+ derivable sibling) relationships, populated
    ``children_stub`` counts, ``total_elements``, a ``build_duration_ms``
    placeholder of 0, and ``metadata`` carrying the config + target.
    """
    cfg = config or MatrixConfig()
    elements: list[Element] = []
    relationships: list[Relationship] = []

    # parent_row_id -> ordered list of child row_ids, for sibling edges + counts.
    children_of: dict[int, list[int]] = {}

    next_row = 0
    for item in walked:
        data, depth, parent_row_id = item
        row_id = next_row
        next_row += 1

        element = _to_element(data, row_id, parent_row_id)
        elements.append(element)

        if parent_row_id is not None:
            relationships.append(
                Relationship(
                    from_row_id=parent_row_id,
                    to_row_id=row_id,
                    relationship_code=int(RelationshipCode.PARENT_OF),
                    weight=1.0,
                    metadata={"inference": "uia_traversal"},
                )
            )
            siblings = children_of.setdefault(parent_row_id, [])
            if not siblings:
                # First child under this parent (spec code 2).
                relationships.append(
                    Relationship(
                        from_row_id=row_id,
                        to_row_id=parent_row_id,
                        relationship_code=int(RelationshipCode.FIRST_CHILD_OF),
                        weight=1.0,
                    )
                )
            else:
                # Link to the immediately-preceding sibling (spec code 3).
                relationships.append(
                    Relationship(
                        from_row_id=siblings[-1],
                        to_row_id=row_id,
                        relationship_code=int(RelationshipCode.NEXT_SIBLING_OF),
                        weight=1.0,
                    )
                )
            siblings.append(row_id)

    # Populate each parent's children_stub now that all children are known.
    for parent_id, child_ids in children_of.items():
        stub = elements[parent_id].children_stub
        stub.has_children = bool(child_ids)
        stub.count = len(child_ids)

    snapshot = Snapshot(
        epoch=epoch,
        elements=elements,
        relationships=relationships,
        total_elements=len(elements),
        build_duration_ms=0,
        degraded_branches=0,
        target=dict(target or {}),
        metadata={"config": cfg.to_dict(), "target": dict(target or {})},
    )
    return snapshot
