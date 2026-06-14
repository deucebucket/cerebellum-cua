#!/usr/bin/env python3
"""Token benchmark for the accordion gateway (issue #8).

Measures the estimated token size of gateway responses against synthetic UI
snapshots of varying element counts, to make the *bounded-per-turn* property
observable: ``get_initial_context`` and a single ``load_children`` expansion stay
roughly flat as the tree grows, while serializing the full matrix grows with it.

All token counts come from :func:`cerebellum_cua.gateway.budget.estimate_tokens`,
a ~4-chars-per-token heuristic over compact JSON. It is an estimate, not a
model-exact tokenizer count. The trees are synthetic, not captured from real
apps; the numbers show the shape of the curve, not a comparison to any tool.

Run::

    PYTHONPATH=src python3 scripts/benchmark_tokens.py
    PYTHONPATH=src python3 scripts/benchmark_tokens.py --sizes 10,50,200,1000
"""

from __future__ import annotations

import argparse
import warnings
from typing import Any

from cerebellum_cua.gateway.accordion import Accordion
from cerebellum_cua.gateway.budget import estimate_tokens
from cerebellum_cua.gateway.tokens import LazyTokenCodec
from cerebellum_cua.matrix.builder import build_snapshot
from cerebellum_cua.model import ControlType
from cerebellum_cua.storage import get_backend
from cerebellum_cua.storage.base import StorageBackend

# The benchmark secret is short by design (not security-relevant); silence the
# PyJWT key-length advisory so the table output stays clean.
warnings.filterwarnings("ignore", message="The HMAC key is")

#: Default element counts to benchmark.
DEFAULT_SIZES = (10, 50, 200, 1000, 5000)

#: HS256 secret for the lazy-token codec (benchmark-only, not security-relevant).
_SECRET = "benchmark-token-secret"

WalkedItem = tuple[dict[str, Any], int, "int | None"]


def _leaf(row_index: int) -> dict[str, Any]:
    """A deterministic interactive leaf (button / edit / text), seeded by index."""
    kinds = (
        (int(ControlType.BUTTON), "OK"),
        (int(ControlType.EDIT), "search field"),
        (int(ControlType.TEXT), "status label"),
    )
    control_type, label = kinds[row_index % len(kinds)]
    return {
        "control_type": control_type,
        "name": f"{label} {row_index}",
        "class_name": "Leaf",
        "automation_id": f"leaf-{row_index}",
        "is_interactive": control_type != int(ControlType.TEXT),
        "is_content": True,
        "bounding_rect": {
            "left": (row_index % 20) * 40,
            "top": (row_index % 30) * 24,
            "width": 80,
            "height": 22,
        },
        "properties": {"text_content": f"{label} {row_index}"},
    }


def build_synthetic_walk(size: int) -> list[WalkedItem]:
    """Build a plausible UI tree of exactly ``size`` elements (parents first).

    Shape: a root pane, a few top-level windows, each holding nested panes, each
    pane holding interactive leaves. Deterministic for a given ``size`` (no
    randomness), so repeated runs and the test suite agree.
    """
    walked: list[WalkedItem] = []
    walked.append(({"control_type": int(ControlType.PANE), "name": "Desktop",
                    "class_name": "Root"}, 0, None))
    if size <= 1:
        return walked[:size]

    n_windows = max(2, min(4, size // 50 + 1))
    remaining = size - 1
    per_window = max(1, remaining // n_windows)

    row = 1
    for w in range(n_windows):
        if row >= size:
            break
        win_row = row
        walked.append(({"control_type": int(ControlType.WINDOW),
                        "name": f"Window {w}", "class_name": "Win32",
                        "is_interactive": True}, 1, 0))
        row += 1
        budget = per_window - 1 if w < n_windows - 1 else size - row
        row = _fill_window(walked, win_row, budget, row, size)
    # Top up to the exact target size with leaves under the last-created pane so
    # the element count matches ``size`` precisely (cleaner benchmark rows). The
    # mid-tree parent measured by load_children is chosen separately, so this
    # padding does not distort the per-expansion estimate.
    pane_rows = [
        i for i, (data, _d, parent) in enumerate(walked)
        if parent is not None and data.get("class_name") == "Pane"
    ]
    pad_parent = pane_rows[-1] if pane_rows else 0
    while len(walked) < size:
        walked.append((_leaf(len(walked)), 3, pad_parent))
    return walked[:size]


def _fill_window(
    walked: list[WalkedItem], win_row: int, budget: int, row: int, size: int
) -> int:
    """Append nested panes + leaves under a window. Returns the next free row id."""
    panes = max(1, budget // 12)
    leaves_per_pane = max(1, (budget - panes) // panes) if panes else budget
    for _ in range(panes):
        if row >= size or budget <= 0:
            break
        pane_row = row
        walked.append(({"control_type": int(ControlType.PANE),
                        "name": f"Pane {pane_row}", "class_name": "Pane"},
                       2, win_row))
        row += 1
        budget -= 1
        for _ in range(leaves_per_pane):
            if row >= size or budget <= 0:
                break
            walked.append((_leaf(row), 3, pane_row))
            row += 1
            budget -= 1
    return row


def _mid_parent_row(walked: list[WalkedItem]) -> int:
    """Pick a mid-tree parent that actually has children (a pane)."""
    pane_rows = [
        i for i, (data, _depth, parent) in enumerate(walked)
        if parent is not None and data.get("class_name") == "Pane"
    ]
    if pane_rows:
        return pane_rows[len(pane_rows) // 2]
    # Fall back to the first element that is some node's parent.
    parents = {p for _d, _depth, p in walked if p is not None}
    return min(parents) if parents else 0


def _seed_backend(size: int) -> tuple[StorageBackend, Accordion, int, list[WalkedItem]]:
    """Persist a synthetic snapshot of ``size`` elements to an in-memory SQLite."""
    walked = build_synthetic_walk(size)
    snapshot = build_snapshot(walked, epoch=size)
    backend = get_backend(None)  # in-memory SQLite
    backend.connect()
    backend.init_schema()
    sid = backend.persist_snapshot(snapshot)
    codec = LazyTokenCodec(_SECRET)
    accordion = Accordion(backend, codec)
    return backend, accordion, sid, walked


def measure_size(size: int) -> dict[str, int]:
    """Return the token estimates for one snapshot size (see module docstring)."""
    backend, accordion, sid, walked = _seed_backend(size)
    try:
        elements = backend.get_all_elements(sid)
        full_matrix = {"elements": [e.to_dict() for e in elements]}

        initial = accordion.get_initial_context(sid)
        mid_parent = _mid_parent_row(walked)
        children = accordion.load_children(sid, mid_parent)
        read_text = {
            "texts": [
                {"row_id": e.row_id,
                 "text": e.properties.get("text_content") or e.name,
                 "bbox": [e.bounding_rect.left, e.bounding_rect.top,
                          e.bounding_rect.width, e.bounding_rect.height]}
                for e in elements
                if e.properties.get("text_content") or e.name
            ]
        }
        single = accordion.get_element(sid, mid_parent)
        return {
            "elements": len(elements),
            "full_matrix": estimate_tokens(full_matrix),
            "initial_context": estimate_tokens(initial),
            "load_children": estimate_tokens(children),
            "read_text": estimate_tokens(read_text),
            "get_element": estimate_tokens(single),
        }
    finally:
        backend.close()


def run(sizes: tuple[int, ...]) -> list[dict[str, int]]:
    """Measure every requested size, ascending."""
    return [measure_size(s) for s in sorted(sizes)]


_COLUMNS = (
    ("elements", "elements"),
    ("full_matrix", "full_matrix"),
    ("initial_context", "initial_ctx"),
    ("load_children", "load_child"),
    ("read_text", "read_text"),
    ("get_element", "get_element"),
)


def format_table(rows: list[dict[str, int]]) -> str:
    """Render the results as a fixed-width text table."""
    header = "".join(f"{label:>14}" for _key, label in _COLUMNS)
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append("".join(f"{row[key]:>14}" for key, _label in _COLUMNS))
    return "\n".join(lines)


def _parse_sizes(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return DEFAULT_SIZES
    return tuple(int(p) for p in raw.split(",") if p.strip())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse ``--sizes`` and print the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        default=None,
        help="comma-separated element counts (default: 10,50,200,1000,5000)",
    )
    args = parser.parse_args(argv)
    rows = run(_parse_sizes(args.sizes))
    print("Token estimates per gateway response (estimate_tokens, ~4 chars/token).")
    print("Synthetic snapshots; counts are heuristic, not tokenizer-exact.\n")
    print(format_table(rows))
    print(
        "\nKey property: initial_ctx and load_child stay ~bounded as elements grow,"
        "\nwhile full_matrix grows with the tree (the accordion keeps per-turn"
        "\ntokens bounded)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
