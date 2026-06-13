"""Unit tests for token-budget accounting (gateway/budget.py + accordion wiring).

Covers the dependency-free estimator and :class:`TokenBudget` surface in
isolation, then wires a budgeted accordion over a seeded SQLite snapshot (mirroring
``test_accordion.py``) to confirm responses carry ``estimated_tokens`` and that a
tiny ceiling raises :class:`TokenBudgetExceededError` (code 1009).
"""

from __future__ import annotations

import pytest

from cerebellum_cua.errors import ERROR_BY_CODE, TokenBudgetExceededError
from cerebellum_cua.gateway.accordion import Accordion
from cerebellum_cua.gateway.budget import (
    ESTIMATED_TOKENS_FIELD,
    TokenBudget,
    estimate_tokens,
)
from cerebellum_cua.gateway.tokens import LazyTokenCodec
from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.storage import get_backend

SECRET = "budget-test-secret"


# --- estimator + TokenBudget surface --------------------------------------
def test_estimate_tokens_small_objects():
    assert estimate_tokens({}) == 1  # "{}" -> 2 chars -> ceil(2/4) == 1
    assert estimate_tokens("") == 1  # '""' -> 2 chars -> ceil(2/4) == 1


def test_estimate_tokens_monotonic_with_size():
    small = {"a": 1}
    big = {"a": 1, "b": list(range(50)), "c": "x" * 200}
    assert estimate_tokens(big) >= estimate_tokens(small)
    # A strict superset of content never estimates fewer tokens.
    bigger = {**big, "d": "y" * 50}
    assert estimate_tokens(bigger) >= estimate_tokens(big)


def test_estimate_tokens_rounds_up():
    # "x" -> '"x"' is 3 chars -> ceil(3/4) == 1.
    assert estimate_tokens("x") == 1


def test_within_unbounded_always_true():
    budget = TokenBudget(max_tokens=None)
    assert budget.within({"big": "x" * 10_000}) is True


def test_within_true_and_false_around_ceiling():
    payload = {"name": "Save", "control_type": 50000}
    size = estimate_tokens(payload)
    assert TokenBudget(max_tokens=size).within(payload) is True
    assert TokenBudget(max_tokens=size - 1).within(payload) is False


def test_annotate_adds_field_without_mutating_input():
    payload = {"snapshot_id": 1, "total_roots": 1}
    annotated = TokenBudget().annotate(payload)
    assert annotated[ESTIMATED_TOKENS_FIELD] == estimate_tokens(payload)
    assert ESTIMATED_TOKENS_FIELD not in payload  # input untouched


def test_measure_matches_estimate_tokens():
    obj = {"k": "v", "n": 7}
    assert TokenBudget().measure(obj) == estimate_tokens(obj)


def test_error_code_1009_registered():
    assert ERROR_BY_CODE[1009] is TokenBudgetExceededError
    assert TokenBudgetExceededError().code == 1009


# --- accordion integration -------------------------------------------------
def _seed_snapshot(backend):
    """Build + persist a small 3-level tree. Returns the snapshot_id."""
    walked = [
        ({"control_type": 50032, "name": "Main Window",
          "class_name": "Win32"}, 0, None),
        ({"control_type": 50011, "name": "File", "automation_id": "FileMenu",
          "is_interactive": True}, 1, 0),
        ({"control_type": 50011, "name": "New", "is_interactive": True}, 2, 1),
        ({"control_type": 50011, "name": "Open...", "is_interactive": True}, 2, 1),
        ({"control_type": 50011, "name": "Edit", "is_interactive": True}, 1, 0),
    ]
    snapshot = build_snapshot(walked, epoch=1009)
    return backend.persist_snapshot(snapshot)


@pytest.fixture()
def env(tmp_path):
    backend = get_backend(str(tmp_path / "matrix.db"))
    backend.connect()
    backend.init_schema()
    sid = _seed_snapshot(backend)
    codec = LazyTokenCodec(SECRET)
    yield backend, codec, sid
    backend.close()


def test_initial_context_carries_estimated_tokens(env):
    backend, codec, sid = env
    accordion = Accordion(backend, codec)  # default unbounded budget
    ctx = accordion.get_initial_context(sid)
    assert ESTIMATED_TOKENS_FIELD in ctx
    assert ctx[ESTIMATED_TOKENS_FIELD] > 0


def test_load_children_carries_estimated_tokens(env):
    backend, codec, sid = env
    accordion = Accordion(backend, codec)
    result = accordion.load_children(sid, parent_row_id=0, max_depth=2)
    assert ESTIMATED_TOKENS_FIELD in result
    assert result[ESTIMATED_TOKENS_FIELD] > 0


def test_get_element_carries_estimated_tokens(env):
    backend, codec, sid = env
    accordion = Accordion(backend, codec)
    out = accordion.get_element(sid, row_id=1)
    assert ESTIMATED_TOKENS_FIELD in out
    assert out[ESTIMATED_TOKENS_FIELD] > 0


def test_tiny_ceiling_raises_1009_on_initial_context(env):
    backend, codec, sid = env
    accordion = Accordion(backend, codec, TokenBudget(max_tokens=1))
    with pytest.raises(TokenBudgetExceededError) as excinfo:
        accordion.get_initial_context(sid)
    assert excinfo.value.code == 1009
    assert excinfo.value.details["max_tokens"] == 1
    assert excinfo.value.details["estimated_tokens"] > 1


def test_tiny_ceiling_raises_1009_on_load_children(env):
    backend, codec, sid = env
    accordion = Accordion(backend, codec, TokenBudget(max_tokens=1))
    with pytest.raises(TokenBudgetExceededError):
        accordion.load_children(sid, parent_row_id=0, max_depth=2)


def test_generous_ceiling_does_not_raise(env):
    backend, codec, sid = env
    accordion = Accordion(backend, codec, TokenBudget(max_tokens=100_000))
    ctx = accordion.get_initial_context(sid)
    assert ctx[ESTIMATED_TOKENS_FIELD] <= 100_000
