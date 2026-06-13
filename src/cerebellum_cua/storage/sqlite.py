"""SQLite implementation of ``StorageBackend`` (dev / Linux test backend).

Self-contained translation of the v4.2 Postgres schema: JSONB columns become
TEXT holding ``json.dumps`` output, BIGSERIAL becomes INTEGER PRIMARY KEY
AUTOINCREMENT, TIMESTAMPTZ becomes TEXT ISO-8601, INTEGER[] becomes JSON text,
GIN indexes collapse to plain indexes. WAL journaling is enabled on connect.

Persistence only — no COM, no policy. The token string is built by the gateway
layer and merely stored/validated here with a server-side TTL.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from cerebellum_cua.errors import ElementNotFoundError
from cerebellum_cua.model import Element, Relationship, SemanticConcept, Snapshot
from cerebellum_cua.storage import _rowmap as rm
from cerebellum_cua.storage._sqlite_ddl import SCHEMA as _SCHEMA
from cerebellum_cua.storage.base import StorageBackend


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteBackend(StorageBackend):
    """File- or memory-backed SQLite persistence for the matrix engine."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    # --- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- write path ------------------------------------------------------
    def persist_snapshot(self, snapshot: Snapshot) -> int:
        now = _now()
        target = snapshot.target or {}
        try:
            cur = self.conn.execute(
                """INSERT INTO matrix_snapshots
                   (epoch, created_at, target_exe, target_window_title, target_pid,
                    matrix_version, total_elements, build_duration_ms,
                    degraded_branches, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.epoch, now,
                    target.get("target_exe"),
                    target.get("target_window_title"),
                    target.get("target_pid"),
                    "4.2",
                    snapshot.total_elements or len(snapshot.elements),
                    snapshot.build_duration_ms,
                    snapshot.degraded_branches,
                    rm.dumps(snapshot.metadata),
                ),
            )
            if cur.lastrowid is None:  # pragma: no cover - sqlite always sets it on INSERT
                raise RuntimeError("INSERT into matrix_snapshots did not return a rowid")
            snapshot_id = int(cur.lastrowid)
            self.conn.executemany(
                """INSERT INTO elements
                   (snapshot_id, matrix_row_id, uia_runtime_id_hash, control_type,
                    name, class_name, automation_id, bounding_rect, properties,
                    patterns, is_interactive, is_content, framework_id, metadata,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [rm.element_to_row(snapshot_id, e) + (now,) for e in snapshot.elements],
            )
            if snapshot.relationships:
                self.conn.executemany(
                    """INSERT INTO relationships
                       (snapshot_id, from_row_id, to_row_id, relationship_code,
                        weight, metadata)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [rm.relationship_to_row(snapshot_id, r) for r in snapshot.relationships],
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        snapshot.snapshot_id = snapshot_id
        return snapshot_id

    def record_patch(
        self,
        snapshot_id: int,
        epoch: int,
        patch_type: str,
        affected_row_ids: list[int],
        patch_json: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """INSERT INTO matrix_patches
               (snapshot_id, epoch, patch_type, affected_row_ids, patch_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id, epoch, patch_type,
                rm.dumps(affected_row_ids), rm.dumps(patch_json), _now(),
            ),
        )
        self.conn.commit()

    # --- read path -------------------------------------------------------
    def get_last_snapshot_id(self) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM matrix_snapshots ORDER BY epoch DESC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None

    def get_element(self, snapshot_id: int, row_id: int) -> Element | None:
        row = self.conn.execute(
            "SELECT * FROM elements WHERE snapshot_id = ? AND matrix_row_id = ?",
            (snapshot_id, row_id),
        ).fetchone()
        if row is None:
            return None
        el = rm.row_to_element(row)
        el.semantics = self.get_semantic_concepts(snapshot_id, row_id)
        return el

    def get_children(self, snapshot_id: int, parent_row_id: int) -> list[Element]:
        rows = self.conn.execute(
            """SELECT e.* FROM elements e
               JOIN relationships r
                 ON e.matrix_row_id = r.to_row_id AND e.snapshot_id = r.snapshot_id
               WHERE r.snapshot_id = ? AND r.from_row_id = ? AND r.relationship_code = ?
               ORDER BY e.matrix_row_id""",
            (snapshot_id, parent_row_id, rm.PARENT_EDGE_CODE),
        ).fetchall()
        return [rm.row_to_element(r) for r in rows]

    def get_relationships(
        self, snapshot_id: int, from_row_id: int
    ) -> list[Relationship]:
        rows = self.conn.execute(
            """SELECT * FROM relationships
               WHERE snapshot_id = ? AND from_row_id = ?
               ORDER BY relationship_code, to_row_id""",
            (snapshot_id, from_row_id),
        ).fetchall()
        return [rm.row_to_relationship(r) for r in rows]

    def count_children(self, snapshot_id: int, parent_row_id: int) -> int:
        row = self.conn.execute(
            """SELECT COUNT(*) AS cnt FROM relationships
               WHERE snapshot_id = ? AND from_row_id = ? AND relationship_code = ?""",
            (snapshot_id, parent_row_id, rm.PARENT_EDGE_CODE),
        ).fetchone()
        return int(row["cnt"])

    def get_semantic_concepts(
        self, snapshot_id: int, row_id: int
    ) -> list[SemanticConcept]:
        rows = self.conn.execute(
            """SELECT sm.domain_concept, esl.applied_confidence
               FROM element_semantic_links esl
               JOIN semantic_mappings sm ON esl.mapping_id = sm.id
               JOIN elements e ON esl.element_id = e.id
               WHERE e.snapshot_id = ? AND e.matrix_row_id = ?
               ORDER BY esl.applied_confidence DESC""",
            (snapshot_id, row_id),
        ).fetchall()
        return [rm.row_to_semantic(r) for r in rows]

    # --- lazy-load token cache ------------------------------------------
    def save_lazy_token(
        self,
        token: str,
        snapshot_id: int,
        parent_row_id: int,
        max_depth: int,
        child_count: int,
        ttl_seconds: int = 300,
    ) -> None:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        self.conn.execute(
            """INSERT INTO lazy_load_tokens
               (token, snapshot_id, parent_row_id, max_depth, created_at,
                expires_at, child_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (token) DO NOTHING""",
            (token, snapshot_id, parent_row_id, max_depth,
             now.isoformat(), expires, child_count),
        )
        self.conn.commit()

    def lazy_token_valid(self, token: str) -> bool:
        row = self.conn.execute(
            "SELECT expires_at FROM lazy_load_tokens WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return False
        return datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)

    # --- helper used by the semantics layer / tests ----------------------
    def link_semantic(
        self, snapshot_id: int, row_id: int, mapping_id: int, confidence: float
    ) -> None:
        """Attach a semantic mapping to an element (write side of the link table)."""
        row = self.conn.execute(
            "SELECT id FROM elements WHERE snapshot_id = ? AND matrix_row_id = ?",
            (snapshot_id, row_id),
        ).fetchone()
        if row is None:
            raise ElementNotFoundError(snapshot_id=snapshot_id, row_id=row_id)
        self.conn.execute(
            """INSERT INTO element_semantic_links
               (element_id, mapping_id, applied_confidence, applied_at)
               VALUES (?, ?, ?, ?) ON CONFLICT (element_id, mapping_id) DO NOTHING""",
            (int(row["id"]), mapping_id, confidence, _now()),
        )
        self.conn.commit()
