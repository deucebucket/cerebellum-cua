"""Storage backend contract.

``StorageBackend`` is the seam that lets the engine run against either SQLite
(dev / Linux-testable) or PostgreSQL v4.2 (production) without the rest of the
codebase knowing which. Implementations live in ``sqlite.py`` and ``postgres.py``.

Backends own persistence only. They never touch COM/UIA and never make policy
decisions — they store what the matrix layer produces and answer queries the
gateway layer asks. All methods operate on the shared dataclasses from
``cerebellum_cua.model``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from cerebellum_cua.model import Element, Relationship, SemanticConcept, Snapshot


class StorageBackend(ABC):
    """Abstract persistence layer for snapshots, edges, tokens and patches."""

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    def connect(self) -> None:
        """Open the underlying connection/pool. Idempotent."""

    @abstractmethod
    def init_schema(self) -> None:
        """Create tables/indexes if absent. Must be safe to run repeatedly."""

    @abstractmethod
    def close(self) -> None:
        """Release all resources."""

    # --- write path ------------------------------------------------------
    @abstractmethod
    def persist_snapshot(self, snapshot: Snapshot) -> int:
        """Persist a full snapshot atomically; return its assigned snapshot_id."""

    @abstractmethod
    def record_patch(
        self,
        snapshot_id: int,
        epoch: int,
        patch_type: str,
        affected_row_ids: list[int],
        patch_json: dict[str, Any],
    ) -> None:
        """Append an incremental diff to the patch log for replay/CLI sync."""

    # --- read path -------------------------------------------------------
    @abstractmethod
    def get_last_snapshot_id(self) -> int | None:
        """Return the snapshot_id of the highest epoch, or None if empty."""

    @abstractmethod
    def get_element(self, snapshot_id: int, row_id: int) -> Element | None:
        """Fetch a single hydrated element by its dense row_id."""

    @abstractmethod
    def get_children(self, snapshot_id: int, parent_row_id: int) -> list[Element]:
        """Return direct children of ``parent_row_id`` ordered by row_id."""

    @abstractmethod
    def get_relationships(
        self, snapshot_id: int, from_row_id: int
    ) -> list[Relationship]:
        """Return outgoing edges from a given row."""

    @abstractmethod
    def count_children(self, snapshot_id: int, parent_row_id: int) -> int:
        """Cheap count of edges originating at ``parent_row_id`` (grandchild stub)."""

    @abstractmethod
    def get_semantic_concepts(
        self, snapshot_id: int, row_id: int
    ) -> list[SemanticConcept]:
        """Resolve domain concepts linked to an element, highest confidence first."""

    # --- lazy-load token cache ------------------------------------------
    @abstractmethod
    def save_lazy_token(
        self,
        token: str,
        snapshot_id: int,
        parent_row_id: int,
        max_depth: int,
        child_count: int,
        ttl_seconds: int = 300,
    ) -> None:
        """Persist an opaque accordion token with a server-side TTL."""

    @abstractmethod
    def lazy_token_valid(self, token: str) -> bool:
        """True if the token exists and has not expired."""

    def __enter__(self) -> StorageBackend:
        self.connect()
        self.init_schema()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
