"""Capture driver: turns a backend's pre-order node stream into matrix rows.

Backends emit ``(CapturedElement, depth, parent_key)`` and know nothing about
matrix row ids. The driver assigns dense 0-based row ids in yield order and
resolves each node's ``parent_key`` to the parent's already-assigned row id,
producing exactly the ``(element_data, depth, parent_row_id)`` tuples that
``cerebellum_cua.matrix.build_snapshot`` consumes.

This keeps row-id bookkeeping in one place instead of duplicated per backend.
"""

from __future__ import annotations

import time
from collections.abc import Hashable, Iterator
from typing import Any

from cerebellum_cua.capture.base import CaptureBackend, CapturedElement
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.model import Snapshot


def walk_to_rows(
    backend: CaptureBackend, target: dict[str, Any], config: MatrixConfig
) -> Iterator[tuple[dict[str, Any], int, int | None]]:
    """Drive ``backend.iter_tree`` and yield build_snapshot-ready row tuples."""
    row_of: dict[Hashable, int] = {}
    # Identity key for nodes whose parent_key is their own native id: we also
    # register each element under its own key so children can resolve it. The
    # backend chooses keys; we map (key -> row_id) as we assign rows.
    next_row = 0
    for element, depth, parent_key in backend.iter_tree(target, config):
        parent_row = row_of.get(parent_key) if parent_key is not None else None
        current_row = next_row
        next_row += 1
        # Register this node under both its parent_key-style identity (native id)
        # and runtime id so later children can find it regardless of which the
        # backend uses as their parent_key.
        self_key = _self_key(element)
        if self_key is not None:
            row_of[self_key] = current_row
        yield element.to_element_data(), depth, parent_row


def _self_key(element: CapturedElement) -> Hashable | None:
    """Best-effort stable key a child might use to reference this element."""
    if element.native_ref is not None:
        return id(element.native_ref)
    if element.runtime_id is not None:
        return tuple(element.runtime_id)
    if element.uia_runtime_id_hash:
        return element.uia_runtime_id_hash
    return None


def capture_snapshot(
    backend: CaptureBackend,
    target: dict[str, Any],
    config: MatrixConfig,
    epoch: int,
) -> Snapshot:
    """Capture the live tree into a fully-built Snapshot, timing the build."""
    start = time.perf_counter()
    walked = list(walk_to_rows(backend, target, config))
    snapshot = build_snapshot(walked, epoch, target=target, config=config)
    snapshot.build_duration_ms = int((time.perf_counter() - start) * 1000)
    snapshot.metadata.setdefault("capture_backend", backend.name)
    return snapshot
