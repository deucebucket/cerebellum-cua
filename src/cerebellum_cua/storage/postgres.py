"""PostgreSQL v4.2 implementation of ``StorageBackend`` (production backend).

Mirrors the canonical schema shipped as package data in
``cerebellum_cua/storage/schema/cerebellum_cua_v42_schema.sql`` and the reference
persistence logic from the spec (the design spec, Section 5). ``psycopg2`` is
imported lazily inside ``connect`` so importing this module never fails on a host
without the optional ``postgres`` extra installed.

Persistence only — no COM, no policy. JWT encoding lives in the gateway layer; the
already-built token string is merely stored/validated here with a server-side TTL.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from cerebellum_cua.errors import ElementNotFoundError
from cerebellum_cua.model import Element, Relationship, SemanticConcept, Snapshot
from cerebellum_cua.storage import _rowmap as rm
from cerebellum_cua.storage.base import StorageBackend

_SCHEMA_FILE = "cerebellum_cua_v42_schema.sql"
_SCHEMA_PACKAGE = "cerebellum_cua.storage.schema"
_PARENT = rm.PARENT_EDGE_CODE


def load_schema_ddl() -> str:
    """Return the canonical v4.2 DDL text from packaged data.

    Reads the schema via :mod:`importlib.resources` so it works from an installed
    wheel (no repo-relative ``sql/`` exists there), falling back to a filesystem
    lookup for source/editable checkouts.
    """
    try:
        return resources.files(_SCHEMA_PACKAGE).joinpath(_SCHEMA_FILE).read_text("utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return _find_schema_path().read_text(encoding="utf-8")


def _find_schema_path() -> Path:
    """Locate the canonical DDL on disk (source checkouts), preferring package data."""
    here = Path(__file__).resolve()
    packaged = here.parent / "schema" / _SCHEMA_FILE
    if packaged.is_file():
        return packaged
    for base in here.parents:
        candidate = base / "sql" / _SCHEMA_FILE
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Could not locate {_SCHEMA_FILE} near {here}")


class PostgresBackend(StorageBackend):
    """psycopg2-backed production persistence for the matrix engine."""

    def __init__(self, dsn: str, schema_path: str | Path | None = None) -> None:
        self.dsn = dsn
        self._schema_path = Path(schema_path) if schema_path else None
        self._conn: Any = None
        self._extras: Any = None

    # --- lifecycle -------------------------------------------------------
    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            import psycopg2
            import psycopg2.extras as extras
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "PostgresBackend requires psycopg2. Install with "
                "`pip install -e \".[postgres]\"`."
            ) from exc
        self._extras = extras
        self._conn = psycopg2.connect(self.dsn)

    def _cursor(self) -> Any:
        return self._conn.cursor(cursor_factory=self._extras.RealDictCursor)

    def init_schema(self) -> None:
        if self._schema_path is not None:
            ddl = Path(self._schema_path).read_text(encoding="utf-8")
        else:
            ddl = load_schema_ddl()
        with self._conn.cursor() as cur:
            cur.execute(ddl)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- write path ------------------------------------------------------
    def persist_snapshot(self, snapshot: Snapshot) -> int:
        target = snapshot.target or {}
        cur = self._cursor()
        try:
            cur.execute(
                """INSERT INTO matrix_snapshots
                   (epoch, target_exe, target_window_title, target_pid,
                    matrix_version, total_elements, build_duration_ms,
                    degraded_branches, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    snapshot.epoch,
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
            snapshot_id = int(cur.fetchone()["id"])
            self._extras.execute_values(
                cur,
                """INSERT INTO elements
                   (snapshot_id, matrix_row_id, uia_runtime_id_hash, control_type,
                    name, class_name, automation_id, bounding_rect, properties,
                    patterns, is_interactive, is_content, framework_id, metadata)
                   VALUES %s""",
                [rm.element_to_row(snapshot_id, e) for e in snapshot.elements],
                page_size=500,
            )
            if snapshot.relationships:
                self._extras.execute_values(
                    cur,
                    """INSERT INTO relationships
                       (snapshot_id, from_row_id, to_row_id, relationship_code,
                        weight, metadata)
                       VALUES %s""",
                    [rm.relationship_to_row(snapshot_id, r) for r in snapshot.relationships],
                    page_size=1000,
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()
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
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO matrix_patches
                   (snapshot_id, epoch, patch_type, affected_row_ids, patch_json)
                   VALUES (%s, %s, %s, %s, %s)""",
                (snapshot_id, epoch, patch_type, list(affected_row_ids),
                 rm.dumps(patch_json)),
            )
        self._conn.commit()

    # --- read path -------------------------------------------------------
    def get_last_snapshot_id(self) -> int | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT id FROM matrix_snapshots ORDER BY epoch DESC LIMIT 1"
            )
            row = cur.fetchone()
        return int(row["id"]) if row else None

    def get_element(self, snapshot_id: int, row_id: int) -> Element | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM elements WHERE snapshot_id = %s AND matrix_row_id = %s",
                (snapshot_id, row_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        el = rm.row_to_element(row)
        el.semantics = self.get_semantic_concepts(snapshot_id, row_id)
        return el

    def get_all_elements(self, snapshot_id: int) -> list[Element]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM elements WHERE snapshot_id = %s ORDER BY matrix_row_id",
                (snapshot_id,),
            )
            rows = cur.fetchall()
        return [rm.row_to_element(r) for r in rows]

    def get_children(self, snapshot_id: int, parent_row_id: int) -> list[Element]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT e.* FROM elements e
                   JOIN relationships r
                     ON e.matrix_row_id = r.to_row_id AND e.snapshot_id = r.snapshot_id
                   WHERE r.snapshot_id = %s AND r.from_row_id = %s
                     AND r.relationship_code = %s
                   ORDER BY e.matrix_row_id""",
                (snapshot_id, parent_row_id, _PARENT),
            )
            rows = cur.fetchall()
        return [rm.row_to_element(r) for r in rows]

    def get_relationships(
        self, snapshot_id: int, from_row_id: int
    ) -> list[Relationship]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT * FROM relationships
                   WHERE snapshot_id = %s AND from_row_id = %s
                   ORDER BY relationship_code, to_row_id""",
                (snapshot_id, from_row_id),
            )
            rows = cur.fetchall()
        return [rm.row_to_relationship(r) for r in rows]

    def count_children(self, snapshot_id: int, parent_row_id: int) -> int:
        with self._cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS cnt FROM relationships
                   WHERE snapshot_id = %s AND from_row_id = %s
                     AND relationship_code = %s""",
                (snapshot_id, parent_row_id, _PARENT),
            )
            row = cur.fetchone()
        return int(row["cnt"])

    def get_semantic_concepts(
        self, snapshot_id: int, row_id: int
    ) -> list[SemanticConcept]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT sm.domain_concept, esl.applied_confidence
                   FROM element_semantic_links esl
                   JOIN semantic_mappings sm ON esl.mapping_id = sm.id
                   JOIN elements e ON esl.element_id = e.id
                   WHERE e.snapshot_id = %s AND e.matrix_row_id = %s
                   ORDER BY esl.applied_confidence DESC""",
                (snapshot_id, row_id),
            )
            rows = cur.fetchall()
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
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO lazy_load_tokens
                   (token, snapshot_id, parent_row_id, max_depth, child_count,
                    expires_at)
                   VALUES (%s, %s, %s, %s, %s,
                           CURRENT_TIMESTAMP + (%s || ' seconds')::interval)
                   ON CONFLICT (token) DO NOTHING""",
                (token, snapshot_id, parent_row_id, max_depth, child_count,
                 str(ttl_seconds)),
            )
        self._conn.commit()

    def lazy_token_valid(self, token: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                """SELECT 1 AS ok FROM lazy_load_tokens
                   WHERE token = %s AND expires_at > CURRENT_TIMESTAMP""",
                (token,),
            )
            return cur.fetchone() is not None

    # --- helper used by the semantics layer ------------------------------
    def link_semantic(
        self, snapshot_id: int, row_id: int, mapping_id: int, confidence: float
    ) -> None:
        """Attach a semantic mapping to an element (write side of the link table)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT id FROM elements WHERE snapshot_id = %s AND matrix_row_id = %s",
                (snapshot_id, row_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ElementNotFoundError(snapshot_id=snapshot_id, row_id=row_id)
            cur.execute(
                """INSERT INTO element_semantic_links
                   (element_id, mapping_id, applied_confidence)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (element_id, mapping_id) DO NOTHING""",
                (int(row["id"]), mapping_id, confidence),
            )
        self._conn.commit()
