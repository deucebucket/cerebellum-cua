"""Matrix logic layer: identity, snapshot building, and epoch diffing.

Pure logic -- no COM, no DB. Turns a stream of walked elements into a versioned
:class:`~cerebellum_cua.model.Snapshot`, computes stable content-addressable identity
(Failure 3), and diffs snapshots across epochs.
"""

from __future__ import annotations

from cerebellum_cua.matrix.builder import build_snapshot
from cerebellum_cua.matrix.diff import diff_snapshots
from cerebellum_cua.matrix.identity import composite_key, runtime_id_hash

__all__ = [
    "build_snapshot",
    "diff_snapshots",
    "composite_key",
    "runtime_id_hash",
]
