"""Cerebellum CUA: UIA accessibility tree -> versioned relational matrix engine.

Public surface is intentionally small; import from submodules for everything else.
"""

from __future__ import annotations

__version__ = "0.1.0"

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import (
    BoundingRect,
    ChildStub,
    ControlType,
    Element,
    Relationship,
    RelationshipCode,
    SemanticConcept,
    Snapshot,
)

__all__ = [
    "__version__",
    "MatrixConfig",
    "BoundingRect",
    "ChildStub",
    "ControlType",
    "Element",
    "Relationship",
    "RelationshipCode",
    "SemanticConcept",
    "Snapshot",
]
