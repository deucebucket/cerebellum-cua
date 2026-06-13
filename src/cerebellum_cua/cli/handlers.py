"""The five JSONL v4.2 operation handlers, bound to a live engine.

Each handler takes the request *payload* dict and returns the response *payload*
dict (the :class:`~cerebellum_cua.gateway.Protocol` wraps the envelope and serializes
any raised :class:`~cerebellum_cua.errors.MatrixUIError`). Handlers delegate read paths
to the :class:`~cerebellum_cua.gateway.Accordion`, the diff to
:func:`~cerebellum_cua.matrix.diff_snapshots`, and live capture/invoke to the (Windows-
only) uia layer — which raises a clear typed error on Linux rather than crashing.

The handlers are bound to an engine via :class:`OperationHandlers`; the engine
exposes the ``handlers`` dict the protocol dispatches against.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.errors import (
    ElementNotFoundError,
    MatrixUIError,
    SnapshotNotFoundError,
    UIAAccessDeniedError,
)
from cerebellum_cua.matrix import diff_snapshots
from cerebellum_cua.model import Snapshot
from cerebellum_cua.semantics import SEED_MAPPINGS, match_element

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

Handler = Callable[[dict[str, Any]], dict[str, Any]]

_NOT_ON_LINUX = (
    "Live UIA {what} requires Windows 10/11 with the 'uiautomation' package "
    "(pip install -e '.[uia]'). This host cannot perform live capture."
)

_NO_CAPTURE = (
    "Capture backend {kind!r} is not available on this host. UIA needs Windows + "
    "'uiautomation'; AT-SPI needs a reachable Linux a11y bus (org.a11y.Bus) with "
    "the GI Atspi bindings. Check `available_backends()`."
)


class OperationHandlers:
    """Bundle of the five operation handlers closed over a :class:`CuaEngine`."""

    def __init__(self, engine: CuaEngine) -> None:
        self._engine = engine

    def as_dict(self) -> dict[str, Handler]:
        """Return the operation -> handler mapping the protocol dispatches against."""
        return {
            "build_matrix": self.build_matrix,
            "get_element": self.get_element,
            "load_children": self.load_children,
            "invoke_action": self.invoke_action,
            "get_snapshot_diff": self.get_snapshot_diff,
        }

    # --- build_matrix ----------------------------------------------------
    def build_matrix(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Capture the live tree, persist it, enrich semantics, register the epoch.

        The capture backend is selected by OS via the universal capture seam
        ("auto" -> UIA on Windows, AT-SPI on Linux), overridable with the
        ``capture_backend`` payload key.
        """
        # Lazy import: backends pull in OS-specific libs only when actually used.
        from cerebellum_cua.capture import (  # noqa: PLC0415
            CaptureNotAvailable,
            capture_snapshot,
            get_capture_backend,
        )

        eng = self._engine
        target = dict(payload.get("target") or {})
        config = MatrixConfig.from_dict(payload.get("config") or {})
        kind = str(payload.get("capture_backend") or eng.capture_backend_kind)
        epoch = eng.next_epoch()
        try:
            backend = get_capture_backend(kind)
            snapshot = capture_snapshot(backend, target, config, epoch)
        except (CaptureNotAvailable, ImportError) as exc:
            raise UIAAccessDeniedError(
                reason="capture_unavailable",
                detail=_NO_CAPTURE.format(kind=kind),
            ) from exc
        snapshot_id = eng.persist(snapshot)
        self._enrich_semantics(snapshot, snapshot_id)
        return self._build_result(snapshot, snapshot_id)

    def register_snapshot(self, snapshot: Snapshot, snapshot_id: int) -> dict[str, Any]:
        """Persist enrichment for an already-built snapshot (test/seed entry point)."""
        self._enrich_semantics(snapshot, snapshot_id)
        return self._build_result(snapshot, snapshot_id)

    def _build_result(self, snapshot: Snapshot, snapshot_id: int) -> dict[str, Any]:
        roots = [
            e.row_id
            for e in snapshot.elements
            if int(e.metadata.get("depth", 0) or 0) <= 1
        ]
        return {
            "snapshot_id": snapshot_id,
            "epoch": snapshot.epoch,
            "total_elements": snapshot.total_elements,
            "build_duration_ms": snapshot.build_duration_ms,
            "degraded_branches": snapshot.degraded_branches,
            "root_elements": roots,
            "status": "success",
        }

    def _enrich_semantics(self, snapshot: Snapshot, snapshot_id: int) -> None:
        """Match every element and write its concepts into the link table."""
        eng = self._engine
        by_row = {e.row_id: e for e in snapshot.elements}
        for element in snapshot.elements:
            parent_row = element.metadata.get("parent_row_id")
            parent = by_row.get(parent_row) if parent_row is not None else None
            for concept in match_element(element, SEED_MAPPINGS, parent):
                if concept.domain_concept.startswith("exclude:"):
                    continue
                mapping_id = eng.ensure_mapping(
                    element.control_type, concept.domain_concept, concept.confidence
                )
                try:
                    eng.link_semantic(
                        snapshot_id, element.row_id, mapping_id, concept.confidence
                    )
                except ElementNotFoundError:  # pragma: no cover - defensive
                    continue

    # --- get_element / load_children (gateway delegation) ----------------
    def get_element(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a single hydrated element via the accordion."""
        snapshot_id = self._snapshot_id(payload)
        return self._engine.accordion.get_element(
            snapshot_id,
            int(payload["row_id"]),
            include_relationships=bool(payload.get("include_relationships", True)),
            include_semantics=bool(payload.get("include_semantics", True)),
            include_children_stub=bool(payload.get("include_children_stub", True)),
        )

    def load_children(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Expand one accordion node (validates the lazy token server-side)."""
        snapshot_id = self._snapshot_id(payload)
        return self._engine.accordion.load_children(
            snapshot_id,
            int(payload.get("parent_row_id", 0)),
            lazy_token=payload.get("lazy_token"),
            max_depth=int(payload.get("max_depth", 2)),
            include_properties=bool(payload.get("include_properties", True)),
            include_semantics=bool(payload.get("include_semantics", True)),
        )

    # --- invoke_action (Windows-only live invoke) ------------------------
    def invoke_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke an element's default action on the live tree (Windows only)."""
        from cerebellum_cua.cli.invoke import invoke_element  # noqa: PLC0415 - lazy COM

        eng = self._engine
        snapshot_id = self._snapshot_id(payload)
        row_id = int(payload["row_id"])
        element = eng.storage.get_element(snapshot_id, row_id)
        if element is None:
            raise ElementNotFoundError(snapshot_id=snapshot_id, row_id=row_id)
        try:
            ok = invoke_element(element, eng.uia)
        except ImportError as exc:
            raise UIAAccessDeniedError(
                reason="uia_unavailable",
                detail=_NOT_ON_LINUX.format(what="invoke_action"),
            ) from exc
        if not ok:
            return {"success": False}
        return {
            "success": True,
            "new_epoch": eng.current_epoch + 1,
            "affected_rows": [row_id],
        }

    # --- get_snapshot_diff (in-memory epoch history) ---------------------
    def get_snapshot_diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Diff two seeded epochs from the engine's in-memory snapshot history."""
        old = self._engine.snapshot_for_epoch(int(payload["from_epoch"]))
        new = self._engine.snapshot_for_epoch(int(payload["to_epoch"]))
        return diff_snapshots(old, new)

    # --- internals -------------------------------------------------------
    def _snapshot_id(self, payload: dict[str, Any]) -> int:
        """Resolve the snapshot id from the payload or the latest persisted one."""
        sid = payload.get("snapshot_id")
        if sid is not None:
            return int(sid)
        last = self._engine.storage.get_last_snapshot_id()
        if last is None:
            raise SnapshotNotFoundError(reason="no_snapshot_persisted")
        return last


__all__ = ["OperationHandlers", "Handler", "MatrixUIError"]
