"""``CuaEngine`` — the composition root that wires every layer together.

This is the lean top of the dependency stack. It owns nothing but wiring:

  storage (:func:`~cerebellum_cua.storage.get_backend`)
    + codec (:class:`~cerebellum_cua.gateway.LazyTokenCodec`)
    + accordion (:class:`~cerebellum_cua.gateway.Accordion`)
    + protocol (:class:`~cerebellum_cua.gateway.Protocol`)
    + handlers (:class:`~cerebellum_cua.cli.handlers.OperationHandlers`)
    + an in-memory ``epoch -> Snapshot`` history (powers get_snapshot_diff)

Live capture is delegated to the :mod:`cerebellum_cua.capture` seam; operation dispatch
lives in :mod:`cerebellum_cua.cli.handlers` to honour the 300-line cap. The engine
is usable as a context manager so ``with CuaEngine(...) as engine:`` opens the
backend (connect + init_schema) and closes it on exit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cerebellum_cua.cli.handlers import OperationHandlers
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.errors import SnapshotNotFoundError
from cerebellum_cua.gateway import Accordion, LazyTokenCodec, Protocol
from cerebellum_cua.gateway.budget import TokenBudget
from cerebellum_cua.model import Snapshot
from cerebellum_cua.storage import get_backend


class CuaEngine:
    """Wire storage + gateway + uia + semantics into one JSONL-driven engine."""

    def __init__(
        self,
        db_dsn: str | None,
        secret: str,
        config: MatrixConfig | None = None,
        capture_backend_kind: str = "auto",
        max_response_tokens: int | None = None,
        user_takeover_guard: bool = True,
        visible_cursor: bool = False,
    ) -> None:
        self.config = config or MatrixConfig()
        #: which capture backend build_matrix uses ("auto"|"uia"|"atspi").
        self.capture_backend_kind = capture_backend_kind
        #: when True, coordinate/raw-input actions arm an AbortWatcher so real
        #: user activity (key/mouse/panic key) cancels in-progress synthetic
        #: input. Degrades to a no-op where evdev/`/dev/input` is unavailable.
        self.user_takeover_guard = user_takeover_guard
        #: when True, element actions first glide the visible cursor to the
        #: element's rect center (purely for on-screen realism). The semantic
        #: action still runs through the a11y API; the glide is best-effort and
        #: silently skipped if no synthetic-input backend is usable. Default
        #: False so headless/test paths are unaffected — the mode manager turns
        #: it on for desktop/vm sessions.
        self.visible_cursor = visible_cursor
        self.storage = get_backend(db_dsn)
        self.storage.connect()
        self.storage.init_schema()

        self.codec = LazyTokenCodec(secret)
        #: ``None`` leaves the accordion's budget unbounded (default behavior):
        #: responses are still measured/annotated, never rejected.
        self.budget = TokenBudget(max_response_tokens)
        self.accordion = Accordion(self.storage, self.codec, self.budget)
        self.protocol = Protocol()

        self._handlers = OperationHandlers(self)
        self.handlers = self._handlers.as_dict()

        # In-memory epoch history (powers get_snapshot_diff without re-querying).
        self._snapshots: dict[int, Snapshot] = {}
        self._mapping_ids: dict[tuple[int, str], int] = {}
        self.current_epoch: int = self._initial_epoch()

    # --- epoch / snapshot history ---------------------------------------
    def _initial_epoch(self) -> int:
        """Continue numbering past any persisted snapshot, else start at 0."""
        last = self.storage.get_last_snapshot_id()
        return 0 if last is None else last

    def next_epoch(self) -> int:
        """Allocate the next epoch number (monotonically increasing)."""
        self.current_epoch += 1
        return self.current_epoch

    def persist(self, snapshot: Snapshot) -> int:
        """Persist a snapshot and register it in the in-memory epoch history."""
        snapshot_id = self.storage.persist_snapshot(snapshot)
        snapshot.snapshot_id = snapshot_id
        self._snapshots[snapshot.epoch] = snapshot
        return snapshot_id

    def register_seed(self, snapshot: Snapshot) -> dict[str, Any]:
        """Persist + enrich a pre-built snapshot (test/seed path, no live capture).

        Mirrors what ``build_matrix`` does after capture: assign+register the
        epoch, persist, and run semantic enrichment. Returns the build result.
        """
        if snapshot.epoch > self.current_epoch:
            self.current_epoch = snapshot.epoch
        snapshot_id = self.persist(snapshot)
        return self._handlers.register_snapshot(snapshot, snapshot_id)

    def snapshot_for_epoch(self, epoch: int) -> Snapshot:
        """Return the in-memory snapshot for ``epoch`` or raise if unknown."""
        snapshot = self._snapshots.get(epoch)
        if snapshot is None:
            raise SnapshotNotFoundError(epoch=epoch)
        return snapshot

    # --- semantic mapping upsert (write helper for enrichment) ----------
    def ensure_mapping(self, control_type: int, concept: str, confidence: float) -> int:
        """Return the ``semantic_mappings.id`` for a concept, inserting if absent.

        The SQLite dev schema ships the mapping table empty, so enrichment must
        materialize a mapping row before it can link to it. Results are memoized
        per ``(control_type, concept)`` for the engine's lifetime.
        """
        key = (control_type, concept)
        cached = self._mapping_ids.get(key)
        if cached is not None:
            return cached
        mapping_id = self._upsert_mapping(control_type, concept, confidence)
        self._mapping_ids[key] = mapping_id
        return mapping_id

    def link_semantic(
        self, snapshot_id: int, row_id: int, mapping_id: int, confidence: float
    ) -> None:
        """Write a semantic link via the backend's ``link_semantic`` helper.

        ``link_semantic`` is an extra write helper present on both concrete
        backends (SQLite/Postgres) but not on the abstract ``StorageBackend``
        surface, so the engine adapts to it here behind a typed signature.
        """
        self.storage.link_semantic(  # type: ignore[attr-defined]
            snapshot_id, row_id, mapping_id, confidence
        )

    def _upsert_mapping(self, control_type: int, concept: str, confidence: float) -> int:
        """Backend-agnostic find-or-create against the semantic_mappings table."""
        conn = self.storage._conn  # type: ignore[attr-defined]
        is_sqlite = type(self.storage).__name__ == "SQLiteBackend"
        ph = "?" if is_sqlite else "%s"
        now = datetime.now(timezone.utc).isoformat()
        sel = (
            "SELECT id FROM semantic_mappings "
            f"WHERE uia_control_type = {ph} AND domain_concept = {ph} LIMIT 1"
        )
        if is_sqlite:
            row = conn.execute(sel, (control_type, concept)).fetchone()
            if row is not None:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO semantic_mappings "
                "(uia_control_type, domain_concept, confidence, created_at) "
                f"VALUES ({ph}, {ph}, {ph}, {ph})",
                (control_type, concept, confidence, now),
            )
            conn.commit()
            return int(cur.lastrowid)
        return self._upsert_mapping_pg(conn, control_type, concept, confidence)

    @staticmethod
    def _upsert_mapping_pg(
        conn: Any, control_type: int, concept: str, confidence: float
    ) -> int:  # pragma: no cover - exercised only on a Postgres host
        """Postgres branch of :meth:`_upsert_mapping`."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM semantic_mappings "
                "WHERE uia_control_type = %s AND domain_concept = %s LIMIT 1",
                (control_type, concept),
            )
            row = cur.fetchone()
            if row is not None:
                return int(row[0])
            cur.execute(
                "INSERT INTO semantic_mappings "
                "(uia_control_type, domain_concept, confidence) "
                "VALUES (%s, %s, %s) RETURNING id",
                (control_type, concept, confidence),
            )
            mapping_id = int(cur.fetchone()[0])
        conn.commit()
        return mapping_id

    # --- capture backend access (for live invoke) -----------------------
    def get_capture_backend(self, kind: str | None = None) -> Any:
        """Return the capture backend for ``kind`` (defaults to the engine's).

        Lazily imported so the OS-specific backend libs load only when invoked.
        Raises ``CaptureNotAvailable`` if the requested backend cannot run here.
        """
        from cerebellum_cua.capture import get_capture_backend  # noqa: PLC0415

        return get_capture_backend(kind or self.capture_backend_kind)

    # --- protocol entry point -------------------------------------------
    def handle_line(self, raw_line: str) -> str:
        """Dispatch one JSONL request line and return the response line."""
        return self.protocol.handle_line(raw_line, self.handlers)

    # --- lifecycle -------------------------------------------------------
    def close(self) -> None:
        """Release the storage backend."""
        self.storage.close()

    def __enter__(self) -> CuaEngine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
