"""Unit tests for ``diff_snapshots`` — plain fakes, no COM/DB.

Covers added / removed / modified detection, field-level patches, rect and
property change detection, and matching by runtime-id hash vs composite-key
fallback across epochs (including row_id reshuffles).
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.matrix.builder import build_snapshot
from cerebellum_cua.matrix.diff import diff_snapshots
from cerebellum_cua.model import Snapshot


def _elem(name: str, ct: int = 50000, rid: str = "", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "control_type": ct,
        "class_name": "Cls",
        "bounding_rect": {"left": 0, "top": 0, "width": 10, "height": 10},
        "uia_runtime_id_hash": rid,
    }
    base.update(extra)
    return base


def _snap(items: list[tuple[dict[str, Any], int, int | None]], epoch: int) -> Snapshot:
    return build_snapshot(items, epoch=epoch)


def test_no_change_is_empty_diff() -> None:
    tree = [(_elem("root", 50032, rid="r0"), 0, None),
            (_elem("a", rid="r1"), 1, 0)]
    old = _snap(tree, 1)
    new = _snap(tree, 2)
    d = diff_snapshots(old, new)
    assert d["added_row_ids"] == []
    assert d["removed_row_ids"] == []
    assert d["modified_row_ids"] == []
    assert d["patches"] == []


def test_added_element_detected() -> None:
    old = _snap([(_elem("root", 50032, rid="r0"), 0, None)], 1)
    new = _snap([(_elem("root", 50032, rid="r0"), 0, None),
                 (_elem("a", rid="r1"), 1, 0)], 2)
    d = diff_snapshots(old, new)
    assert d["added_row_ids"] == [1]
    assert d["removed_row_ids"] == []


def test_removed_element_detected() -> None:
    old = _snap([(_elem("root", 50032, rid="r0"), 0, None),
                 (_elem("a", rid="r1"), 1, 0)], 1)
    new = _snap([(_elem("root", 50032, rid="r0"), 0, None)], 2)
    d = diff_snapshots(old, new)
    assert d["removed_row_ids"] == [1]
    assert d["added_row_ids"] == []


def test_name_change_detected() -> None:
    old = _snap([(_elem("Save", rid="r1"), 0, None)], 1)
    new = _snap([(_elem("Saved", rid="r1"), 0, None)], 2)
    d = diff_snapshots(old, new)
    assert d["modified_row_ids"] == [0]
    (patch,) = d["patches"]
    assert patch["row_id"] == 0
    assert patch["changes"]["name"] == {"old": "Save", "new": "Saved"}


def test_rect_change_detected() -> None:
    old = _snap([(_elem("a", rid="r1"), 0, None)], 1)
    moved = _elem("a", rid="r1")
    moved["bounding_rect"] = {"left": 500, "top": 500, "width": 10, "height": 10}
    new = _snap([(moved, 0, None)], 2)
    d = diff_snapshots(old, new)
    assert d["modified_row_ids"] == [0]
    assert "bounding_rect" in d["patches"][0]["changes"]


def test_property_change_detected() -> None:
    old = _snap([(_elem("a", rid="r1", properties={"value": "x"}), 0, None)], 1)
    new = _snap([(_elem("a", rid="r1", properties={"value": "y"}), 0, None)], 2)
    d = diff_snapshots(old, new)
    assert d["modified_row_ids"] == [0]
    changes = d["patches"][0]["changes"]
    assert changes["properties"]["old"] == {"value": "x"}
    assert changes["properties"]["new"] == {"value": "y"}


def test_match_by_runtime_id_across_row_reshuffle() -> None:
    # Same element keyed by rid, but its row_id moves (a new sibling inserted first).
    old = _snap([(_elem("root", 50032, rid="r0"), 0, None),
                 (_elem("target", rid="rT"), 1, 0)], 1)
    new = _snap([(_elem("root", 50032, rid="r0"), 0, None),
                 (_elem("inserted", rid="rN"), 1, 0),
                 (_elem("target2", rid="rT"), 1, 0)], 2)
    d = diff_snapshots(old, new)
    # "inserted" (new row 1) is added; target matched by rid despite moving to row 2.
    assert d["added_row_ids"] == [1]
    assert d["modified_row_ids"] == [2]
    assert d["patches"][0]["changes"]["name"] == {"old": "target", "new": "target2"}


def test_fallback_to_composite_key_without_runtime_id() -> None:
    # No runtime ids: identity falls back to the Failure-3 composite key.
    old = _snap([(_elem("root", 50032), 0, None),
                 (_elem("btn"), 1, 0)], 1)
    # Same structural identity (name/class/ct/rect/parent) -> matched, no change.
    new = _snap([(_elem("root", 50032), 0, None),
                 (_elem("btn"), 1, 0)], 2)
    d = diff_snapshots(old, new)
    assert d["modified_row_ids"] == []
    assert d["added_row_ids"] == []
    assert d["removed_row_ids"] == []


def test_composite_key_change_is_add_remove_not_modify() -> None:
    # Without runtime id, a name change changes the composite key -> looks like
    # remove(old) + add(new), not an in-place modify.
    old = _snap([(_elem("Old"), 0, None)], 1)
    new = _snap([(_elem("New"), 0, None)], 2)
    d = diff_snapshots(old, new)
    assert d["added_row_ids"] == [0]
    assert d["removed_row_ids"] == [0]
    assert d["modified_row_ids"] == []


def test_combined_add_remove_modify() -> None:
    old = _snap([(_elem("root", 50032, rid="r0"), 0, None),
                 (_elem("keep", rid="r1"), 1, 0),
                 (_elem("gone", rid="r2"), 1, 0)], 1)
    new = _snap([(_elem("root", 50032, rid="r0"), 0, None),
                 (_elem("keep!", rid="r1"), 1, 0),
                 (_elem("fresh", rid="r3"), 1, 0)], 2)
    d = diff_snapshots(old, new)
    assert d["removed_row_ids"] == [2]      # "gone"
    assert d["added_row_ids"] == [2]        # "fresh" is new row 2
    assert d["modified_row_ids"] == [1]     # "keep" -> "keep!"
