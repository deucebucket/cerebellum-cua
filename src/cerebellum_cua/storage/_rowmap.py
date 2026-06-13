"""Row <-> dataclass mapping helpers shared by the SQLite and Postgres backends.

Both backends store the same logical shape (the v4.2 schema); they differ only in
the driver and in whether JSON columns arrive as ``str`` (SQLite TEXT) or already
parsed (Postgres JSONB via RealDictCursor). These helpers normalize that so the
backends stay thin and the row->``Element``/``Relationship`` translation lives in
exactly one place.

Persistence only: no COM, no policy. Pure data transforms.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cerebellum_cua.model import (
    BoundingRect,
    ChildStub,
    Element,
    Relationship,
    SemanticConcept,
)

# Edge codes that denote a direct parent -> child link in the adjacency matrix.
PARENT_EDGE_CODE = 1  # RelationshipCode.PARENT_OF


def loads(value: Any, default: Any) -> Any:
    """Decode a JSON column that may be a str (SQLite) or already parsed (PG)."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        if not value:
            return default
        return json.loads(value)
    return value


def dumps(value: Any) -> str:
    """Encode a Python object to a JSON string for a TEXT/JSONB column."""
    return json.dumps(value if value is not None else {})


def element_to_row(snapshot_id: int, el: Element) -> tuple[Any, ...]:
    """Flatten an ``Element`` to the positional tuple used by bulk insert.

    Column order matches the ``elements`` insert in both backends:
    (snapshot_id, matrix_row_id, uia_runtime_id_hash, control_type, name,
     class_name, automation_id, bounding_rect, properties, patterns,
     is_interactive, is_content, framework_id, metadata)
    """
    return (
        snapshot_id,
        el.row_id,
        el.uia_runtime_id_hash,
        el.control_type,
        el.name,
        el.class_name,
        el.automation_id,
        dumps(el.bounding_rect.to_dict()),
        dumps(el.properties),
        dumps(el.patterns),
        bool(el.is_interactive),
        bool(el.is_content),
        el.framework_id,
        dumps(el.metadata),
    )


def relationship_to_row(snapshot_id: int, rel: Relationship) -> tuple[Any, ...]:
    """Flatten a ``Relationship`` to the positional tuple used by bulk insert."""
    return (
        snapshot_id,
        rel.from_row_id,
        rel.to_row_id,
        rel.relationship_code,
        rel.weight,
        dumps(rel.metadata),
    )


def row_to_element(row: Mapping[str, Any]) -> Element:
    """Reconstruct an ``Element`` from a result row (dict-like)."""
    return Element(
        row_id=int(row["matrix_row_id"]),
        control_type=int(row["control_type"]),
        name=row["name"] or "",
        class_name=row["class_name"] or "",
        automation_id=row["automation_id"] or "",
        uia_runtime_id_hash=row["uia_runtime_id_hash"] or "",
        bounding_rect=BoundingRect.from_dict(loads(row["bounding_rect"], {})),
        properties=loads(row["properties"], {}),
        patterns=loads(row["patterns"], {}),
        is_interactive=bool(row["is_interactive"]),
        is_content=bool(row["is_content"]),
        framework_id=row["framework_id"] or "",
        metadata=loads(row["metadata"], {}),
        children_stub=ChildStub(),
    )


def row_to_relationship(row: Mapping[str, Any]) -> Relationship:
    """Reconstruct a ``Relationship`` from a result row (dict-like)."""
    return Relationship(
        from_row_id=int(row["from_row_id"]),
        to_row_id=int(row["to_row_id"]),
        relationship_code=int(row["relationship_code"]),
        weight=float(row["weight"]),
        metadata=loads(row["metadata"], {}),
    )


def row_to_semantic(row: Mapping[str, Any]) -> SemanticConcept:
    """Reconstruct a ``SemanticConcept`` from a join row."""
    return SemanticConcept(
        domain_concept=row["domain_concept"],
        confidence=float(row["applied_confidence"]),
    )
