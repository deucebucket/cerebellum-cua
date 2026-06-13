"""Element -> protocol-dict hydration helpers for the accordion.

The accordion reads :class:`~cerebellum_cua.model.Element` dataclasses from storage
and emits the flat JSON shape the v4.2 JSONL contract specifies (spec Section 4:
``build_matrix.root_elements`` / ``load_children.children`` / ``get_element``).
These helpers keep that mapping in one place so :mod:`accordion` stays focused on
orchestration (token issuance, depth gating, storage calls).
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.model import ChildStub, Element, SemanticConcept


def stub_to_dict(stub: ChildStub) -> dict[str, Any]:
    """Serialize a children_stub, normalizing ``lazy_token`` presence."""
    return {
        "has_children": stub.has_children,
        "count": stub.count,
        "lazy_token": stub.lazy_token,
    }


def semantics_to_list(concepts: list[SemanticConcept]) -> list[dict[str, Any]]:
    """Serialize semantic concepts to the ``{domain_concept, confidence}`` list."""
    return [c.to_dict() for c in concepts]


def element_to_dict(
    element: Element,
    *,
    include_properties: bool = True,
    include_patterns: bool = True,
    semantics: list[SemanticConcept] | None = None,
    children_stub: ChildStub | None = None,
) -> dict[str, Any]:
    """Convert one :class:`Element` to its protocol JSON object.

    ``include_properties`` / ``include_patterns`` gate the corresponding maps to
    ``{}`` when the request opts out (bandwidth control). ``semantics`` and
    ``children_stub`` are injected by the accordion (resolved from storage and a
    freshly issued lazy token, respectively); when ``children_stub`` is ``None``
    the element's own (un-tokenized) stub is used.
    """
    stub = children_stub if children_stub is not None else element.children_stub
    resolved_semantics = (
        semantics if semantics is not None else element.semantics
    )
    return {
        "row_id": element.row_id,
        "name": element.name,
        "control_type": element.control_type,
        "automation_id": element.automation_id,
        "bounding_rect": element.bounding_rect.to_dict(),
        "properties": dict(element.properties) if include_properties else {},
        "patterns": dict(element.patterns) if include_patterns else {},
        "is_interactive": element.is_interactive,
        "is_content": element.is_content,
        "semantics": semantics_to_list(resolved_semantics),
        "children_stub": stub_to_dict(stub),
    }
