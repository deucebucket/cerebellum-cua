"""Assert the bounded-per-turn property of the accordion gateway (issue #8).

Reuses the helpers in ``scripts/benchmark_tokens.py`` rather than duplicating the
synthetic-tree + measurement logic. Small, deterministic sizes keep this fast
enough for the default Linux run; ``@pytest.mark.benchmark`` only tags it so a
perf-only selection can include it explicitly.

The claim under test is the design property, not a comparison to any tool: the
``estimate_tokens`` of ``get_initial_context`` (and a single expansion) stays
bounded as the element count grows, while the full-matrix serialization grows
with the tree.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_tokens.py"
_spec = importlib.util.spec_from_file_location("benchmark_tokens", _SCRIPT)
assert _spec is not None and _spec.loader is not None
bench = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("benchmark_tokens", bench)
_spec.loader.exec_module(bench)

#: Small, fast sizes; the property must already be visible across this spread.
SIZES = (10, 100, 1000)

#: Sane ceiling for a single per-turn response (well above the ~91 observed,
#: comfortably under a small-LLM context window).
INITIAL_CONTEXT_CEILING = 600


@pytest.fixture(scope="module")
def measured() -> dict[int, dict[str, int]]:
    """Run the benchmark once for the test sizes, keyed by element count."""
    return {row["elements"]: row for row in bench.run(SIZES)}


def test_sizes_are_exact(measured: dict[int, dict[str, int]]) -> None:
    """The synthetic builder hits the requested element counts exactly."""
    assert sorted(measured) == sorted(SIZES)


def test_initial_context_stays_under_ceiling(
    measured: dict[int, dict[str, int]]
) -> None:
    """Initial-context tokens never exceed the per-turn ceiling at any size."""
    for size in SIZES:
        assert measured[size]["initial_context"] <= INITIAL_CONTEXT_CEILING


def test_initial_context_does_not_grow_with_tree(
    measured: dict[int, dict[str, int]]
) -> None:
    """Initial context is roughly flat: 100x more elements stays near-constant."""
    small = measured[10]["initial_context"]
    large = measured[1000]["initial_context"]
    assert large <= small * 2


def test_full_matrix_grows_far_faster_than_initial_context(
    measured: dict[int, dict[str, int]]
) -> None:
    """Full-matrix growth must dwarf initial-context growth from 10 -> 1000."""
    full_growth = measured[1000]["full_matrix"] / measured[10]["full_matrix"]
    ctx_growth = measured[1000]["initial_context"] / measured[10]["initial_context"]
    assert full_growth > 20
    assert full_growth > ctx_growth * 10


def test_single_expansion_is_bounded(
    measured: dict[int, dict[str, int]]
) -> None:
    """A single load_children expansion stays bounded as the tree grows."""
    small = measured[10]["load_children"]
    large = measured[1000]["load_children"]
    assert large <= small * 6
    assert large < measured[1000]["full_matrix"]


def test_table_renders(measured: dict[int, dict[str, int]]) -> None:
    """The table formatter produces one header + one row per measured size."""
    rows = [measured[s] for s in SIZES]
    table = bench.format_table(rows)
    assert "full_matrix" in table
    assert len(table.splitlines()) == len(SIZES) + 2
