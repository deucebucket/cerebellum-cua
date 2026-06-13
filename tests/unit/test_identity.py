"""Unit tests for stable composite identity (Failure 3) — no COM, no DB.

Pins the determinism + jitter-tolerance contract of ``composite_key`` and the
shape/stability of ``runtime_id_hash``. Everything here uses plain dataclasses.
"""

from __future__ import annotations

from cerebellum_cua.matrix.identity import composite_key, runtime_id_hash
from cerebellum_cua.model import BoundingRect


def _key(name="OK", cls="Button", ct=50000, rect=None, parent=3):
    rect = rect if rect is not None else BoundingRect(left=100, top=200, width=80, height=24)
    return composite_key(name, cls, ct, rect, parent)


def test_key_is_32_hex_chars() -> None:
    key = _key()
    assert len(key) == 32
    assert all(c in "0123456789abcdef" for c in key)


def test_key_is_deterministic() -> None:
    assert _key() == _key()


def test_key_stable_under_sub_8px_jitter() -> None:
    base = BoundingRect(left=100, top=200, width=80, height=24)
    # Each coordinate nudged by <4px stays within the same 8px bucket.
    jittered = BoundingRect(left=101, top=199, width=82, height=25)
    assert _key(rect=base) == _key(rect=jittered)


def test_key_stable_at_grid_rounding_neighbors() -> None:
    # 101..105 all round to the 104 bucket; assert a within-bucket pair matches.
    a = BoundingRect(left=101, top=197, width=81, height=21)
    b = BoundingRect(left=105, top=203, width=83, height=27)
    assert _key(rect=a) == _key(rect=b)


def test_key_changes_on_large_rect_move() -> None:
    a = BoundingRect(left=100, top=200, width=80, height=24)
    b = BoundingRect(left=400, top=200, width=80, height=24)
    assert _key(rect=a) != _key(rect=b)


def test_key_changes_on_name() -> None:
    assert _key(name="OK") != _key(name="Cancel")


def test_key_changes_on_class_name() -> None:
    assert _key(cls="Button") != _key(cls="Edit")


def test_key_changes_on_control_type() -> None:
    assert _key(ct=50000) != _key(ct=50004)


def test_key_changes_on_parent() -> None:
    assert _key(parent=3) != _key(parent=7)


def test_key_none_parent_distinct_from_zero() -> None:
    assert _key(parent=None) != _key(parent=0)


def test_key_empty_name_coalesces() -> None:
    # None and "" must hash identically (coalesce rule).
    assert _key(name=None) == _key(name="")


def test_key_ignores_dpi() -> None:
    a = BoundingRect(left=100, top=200, width=80, height=24, dpi=96)
    b = BoundingRect(left=100, top=200, width=80, height=24, dpi=192)
    assert _key(rect=a) == _key(rect=b)


def test_key_negative_rect_symmetry() -> None:
    # Negative coords still hash deterministically and bucket symmetrically.
    a = BoundingRect(left=-100, top=-200, width=80, height=24)
    b = BoundingRect(left=-101, top=-199, width=80, height=24)
    assert _key(rect=a) == _key(rect=b)


def test_runtime_id_hash_shape() -> None:
    h = runtime_id_hash([42, 1, 7, 9])
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


def test_runtime_id_hash_deterministic() -> None:
    assert runtime_id_hash([42, 1, 7]) == runtime_id_hash([42, 1, 7])


def test_runtime_id_hash_order_sensitive() -> None:
    assert runtime_id_hash([1, 2, 3]) != runtime_id_hash([3, 2, 1])


def test_runtime_id_hash_empty_is_blank() -> None:
    assert runtime_id_hash([]) == ""
    assert runtime_id_hash(None) == ""
