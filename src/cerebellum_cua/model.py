"""Shared, backend-agnostic data model for Cerebellum CUA.

These dataclasses are the canonical vocabulary used across every layer (uia,
matrix, storage, gateway, cli). They are pure data: no COM, no DB, no I/O. The
field shapes mirror the v4.2 spec (docs/spec, Postgres draft Sections 1, 3, 4).

Keep this module dependency-free so it can be imported anywhere, including on a
non-Windows host with no optional extras installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class RelationshipCode(IntEnum):
    """Directed adjacency-edge codes (spec Section 1 / Section 3 enum)."""

    PARENT_OF = 1
    FIRST_CHILD_OF = 2
    NEXT_SIBLING_OF = 3
    PREVIOUS_SIBLING_OF = 4
    LABELED_BY = 5
    LABEL_FOR = 6
    MEMBER_OF = 7
    CONTAINS_VIA_GEOMETRY = 8
    SCROLLS = 9
    INVOKES = 10


# Raw Microsoft UIA ControlType integer constants used by the spec/protocol.
# (Not exhaustive — the common set referenced in the v4.2 JSONL contract.)
class ControlType(IntEnum):
    BUTTON = 50000
    CALENDAR = 50001
    CHECK_BOX = 50002
    COMBO_BOX = 50003
    EDIT = 50004
    HYPERLINK = 50005
    IMAGE = 50006
    LIST_ITEM = 50007
    LIST = 50008
    MENU = 50009
    MENU_BAR = 50010
    MENU_ITEM = 50011
    PROGRESS_BAR = 50012
    RADIO_BUTTON = 50013
    SCROLL_BAR = 50014
    SLIDER = 50015
    SPINNER = 50016
    STATUS_BAR = 50017
    TAB = 50018
    TAB_ITEM = 50019
    TEXT = 50020
    TOOL_BAR = 50021
    TOOL_TIP = 50022
    TREE = 50023
    TREE_ITEM = 50024
    CUSTOM = 50025
    GROUP = 50026
    THUMB = 50027
    DATA_GRID = 50028
    DATA_ITEM = 50029
    DOCUMENT = 50030
    SPLIT_BUTTON = 50031
    WINDOW = 50032
    PANE = 50033
    HEADER = 50034
    HEADER_ITEM = 50035
    TABLE = 50036
    TITLE_BAR = 50037
    SEPARATOR = 50038


@dataclass(slots=True)
class BoundingRect:
    """Normalized element rectangle. DPI carried for coordinate correctness."""

    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    dpi: int = 96

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def to_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> BoundingRect:
        d = d or {}
        return cls(
            left=int(d.get("left", 0)),
            top=int(d.get("top", 0)),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            dpi=int(d.get("dpi", 96)),
        )


@dataclass(slots=True)
class ChildStub:
    """Lazy-loading accordion stub returned in place of un-hydrated children."""

    has_children: bool = False
    count: int = 0
    lazy_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_children": self.has_children,
            "count": self.count,
            "lazy_token": self.lazy_token,
        }


@dataclass(slots=True)
class SemanticConcept:
    """A domain concept inferred for an element, with confidence in [0, 1]."""

    domain_concept: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"domain_concept": self.domain_concept, "confidence": self.confidence}


@dataclass(slots=True)
class Element:
    """One included UIA element after the should_include predicate.

    ``row_id`` (a.k.a. matrix_row_id) is the dense, 0-based, epoch-stable integer
    used by the adjacency matrix and every CLI command.
    """

    row_id: int
    control_type: int
    name: str = ""
    class_name: str = ""
    automation_id: str = ""
    uia_runtime_id_hash: str = ""
    bounding_rect: BoundingRect = field(default_factory=BoundingRect)
    properties: dict[str, Any] = field(default_factory=dict)
    patterns: dict[str, Any] = field(default_factory=dict)
    is_interactive: bool = False
    is_content: bool = False
    framework_id: str = ""
    semantics: list[SemanticConcept] = field(default_factory=list)
    children_stub: ChildStub = field(default_factory=ChildStub)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "control_type": self.control_type,
            "name": self.name,
            "class_name": self.class_name,
            "automation_id": self.automation_id,
            "uia_runtime_id_hash": self.uia_runtime_id_hash,
            "bounding_rect": self.bounding_rect.to_dict(),
            "properties": self.properties,
            "patterns": self.patterns,
            "is_interactive": self.is_interactive,
            "is_content": self.is_content,
            "framework_id": self.framework_id,
            "semantics": [s.to_dict() for s in self.semantics],
            "children_stub": self.children_stub.to_dict(),
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class Relationship:
    """A single directed edge in the sparse adjacency matrix."""

    from_row_id: int
    to_row_id: int
    relationship_code: int
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_row_id": self.from_row_id,
            "to_row_id": self.to_row_id,
            "relationship_code": self.relationship_code,
            "weight": self.weight,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class Snapshot:
    """An immutable, epoch-versioned capture of the full relational matrix."""

    epoch: int
    elements: list[Element] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    snapshot_id: int | None = None
    total_elements: int = 0
    build_duration_ms: int = 0
    degraded_branches: int = 0
    target: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.total_elements:
            self.total_elements = len(self.elements)
