"""Unit tests for ``build_snapshot`` — plain dict/dataclass fakes, no COM/DB.

Covers dense row_id assignment, mandatory PARENT_OF edge emission, derivable
sibling edges, children_stub counts, runtime-id hashing, and metadata wiring.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.matrix.builder import build_snapshot
from cerebellum_cua.model import RelationshipCode, Snapshot


def _elem(name: str, ct: int = 50000, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "control_type": ct,
        "class_name": "Cls",
        "bounding_rect": {"left": 0, "top": 0, "width": 10, "height": 10},
    }
    base.update(extra)
    return base


def _simple_tree() -> list[tuple[dict[str, Any], int, int | None]]:
    # root(0) -> [a(1), b(2)]; a -> [c(3)]
    return [
        (_elem("root", 50032), 0, None),
        (_elem("a"), 1, 0),
        (_elem("b"), 1, 0),
        (_elem("c"), 2, 1),
    ]


def _edges(snap: Snapshot, code: RelationshipCode) -> set[tuple[int, int]]:
    return {
        (r.from_row_id, r.to_row_id)
        for r in snap.relationships
        if r.relationship_code == int(code)
    }


def test_returns_snapshot_with_epoch() -> None:
    snap = build_snapshot(_simple_tree(), epoch=7)
    assert isinstance(snap, Snapshot)
    assert snap.epoch == 7


def test_dense_zero_based_row_ids() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    assert [e.row_id for e in snap.elements] == [0, 1, 2, 3]
    assert snap.total_elements == 4


def test_row_id_follows_iteration_order() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    assert [e.name for e in snap.elements] == ["root", "a", "b", "c"]


def test_parent_of_edges_mandatory() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    parent_of = _edges(snap, RelationshipCode.PARENT_OF)
    assert parent_of == {(0, 1), (0, 2), (1, 3)}


def test_root_emits_no_parent_edge() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    # row 0 is never a to_row_id of a PARENT_OF edge.
    assert all(r.to_row_id != 0 for r in snap.relationships
               if r.relationship_code == int(RelationshipCode.PARENT_OF))


def test_first_child_edges() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    first_child = _edges(snap, RelationshipCode.FIRST_CHILD_OF)
    # a is first child of root; c is first child of a.
    assert first_child == {(1, 0), (3, 1)}


def test_next_sibling_edges() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    next_sib = _edges(snap, RelationshipCode.NEXT_SIBLING_OF)
    # b follows a under root.
    assert next_sib == {(1, 2)}


def test_children_stub_counts() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    by_name = {e.name: e for e in snap.elements}
    assert by_name["root"].children_stub.has_children is True
    assert by_name["root"].children_stub.count == 2
    assert by_name["a"].children_stub.count == 1
    assert by_name["b"].children_stub.has_children is False
    assert by_name["b"].children_stub.count == 0
    assert by_name["c"].children_stub.has_children is False


def test_parent_weight_is_one() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    for r in snap.relationships:
        if r.relationship_code == int(RelationshipCode.PARENT_OF):
            assert r.weight == 1.0


def test_metadata_carries_config_and_target() -> None:
    cfg = MatrixConfig(max_depth=5)
    target = {"target_exe": "notepad.exe", "target_pid": 1234}
    snap = build_snapshot(_simple_tree(), epoch=2, target=target, config=cfg)
    assert snap.metadata["target"] == target
    assert snap.metadata["config"]["max_depth"] == 5
    assert snap.target == target


def test_build_duration_placeholder_zero() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    assert snap.build_duration_ms == 0


def test_runtime_id_hash_computed_from_array() -> None:
    walked = [(_elem("x", runtime_id=[1, 2, 3]), 0, None)]
    snap = build_snapshot(walked, epoch=1)
    assert len(snap.elements[0].uia_runtime_id_hash) == 32


def test_existing_runtime_hash_preserved() -> None:
    walked = [(_elem("x", uia_runtime_id_hash="deadbeef" * 4), 0, None)]
    snap = build_snapshot(walked, epoch=1)
    assert snap.elements[0].uia_runtime_id_hash == "deadbeef" * 4


def test_composite_key_stamped_into_metadata() -> None:
    snap = build_snapshot(_simple_tree(), epoch=1)
    for e in snap.elements:
        assert len(e.metadata["composite_key"]) == 32


def test_empty_walk_yields_empty_snapshot() -> None:
    snap = build_snapshot([], epoch=9)
    assert snap.total_elements == 0
    assert snap.elements == []
    assert snap.relationships == []
    assert snap.epoch == 9


def test_accepts_dataclass_elements() -> None:
    # Duck-typed via attributes instead of dict access.
    from cerebellum_cua.model import BoundingRect, Element

    root = Element(row_id=-1, control_type=50032, name="r",
                   bounding_rect=BoundingRect(0, 0, 5, 5))
    child = Element(row_id=-1, control_type=50000, name="ch",
                    bounding_rect=BoundingRect(0, 0, 5, 5))
    snap = build_snapshot([(root, 0, None), (child, 1, 0)], epoch=1)
    assert [e.row_id for e in snap.elements] == [0, 1]
    assert (0, 1) in _edges(snap, RelationshipCode.PARENT_OF)
