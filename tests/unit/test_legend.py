"""Unit tests for the compact cipher / legend (#22).

Two halves:

* :func:`cerebellum_cua.legend.build_legend` is exercised as a pure function over
  hand-built :class:`~cerebellum_cua.model.Element` lists — correct family codes,
  the ``code -> meaning`` legend, and deterministic ordering;
* the ``read_legend`` operation is driven through a seeded
  :class:`~cerebellum_cua.cli.CuaEngine` via ``handle_line``.
"""

from __future__ import annotations

import json
from typing import Any

from cerebellum_cua.cli import CuaEngine
from cerebellum_cua.legend import build_legend
from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.model import (
    BoundingRect,
    ControlType,
    Element,
    SemanticConcept,
)

SECRET = "unit-test-secret"


def _el(
    row_id: int,
    ct: int,
    *,
    semantics: list[str] | None = None,
    name: str = "x",
) -> Element:
    return Element(
        row_id=row_id,
        control_type=ct,
        name=name,
        bounding_rect=BoundingRect(left=0, top=0, width=10, height=10),
        semantics=[SemanticConcept(c, 0.9) for c in (semantics or [])],
    )


# --- pure build_legend ---------------------------------------------------
def test_families_from_control_type() -> None:
    els = [
        _el(0, ControlType.BUTTON),
        _el(1, ControlType.EDIT),
        _el(2, ControlType.MENU_ITEM),
        _el(3, ControlType.WINDOW),
        _el(4, ControlType.HYPERLINK),
        _el(5, ControlType.IMAGE),  # falls to the catch-all family
    ]
    out = build_legend(els)
    codes = {r["row_id"]: r["code"] for r in out["elements"]}
    assert codes == {0: "b0", 1: "e0", 2: "m0", 3: "w0", 4: "l0", 5: "c0"}
    assert out["legend"]["b0"] == "button"
    assert out["legend"]["e0"] == "edit"
    assert out["legend"]["c0"] == "image"
    assert out["count"] == 6


def test_distinct_concepts_share_and_increment_codes() -> None:
    # Two buttons with the SAME concept -> same code; a different button concept
    # -> a new code in the same family.
    els = [
        _el(0, ControlType.BUTTON, semantics=["action_button"]),
        _el(1, ControlType.BUTTON, semantics=["action_button"]),
        _el(2, ControlType.BUTTON, semantics=["cancel_button"]),
    ]
    out = build_legend(els)
    codes = [r["code"] for r in out["elements"]]
    assert codes == ["b0", "b0", "b1"]
    assert out["legend"] == {"b0": "action_button", "b1": "cancel_button"}
    assert out["count"] == 2


def test_semantic_overrides_control_type_for_family() -> None:
    # A semantic concept containing "text_input" lands in the edit family even
    # though the control type is unmapped (IMAGE).
    out = build_legend([_el(0, ControlType.IMAGE, semantics=["text_input"])])
    assert out["elements"][0]["code"] == "e0"
    assert out["legend"]["e0"] == "text_input"


def test_top_semantic_by_confidence() -> None:
    el = Element(
        row_id=0,
        control_type=ControlType.BUTTON,
        semantics=[
            SemanticConcept("low_concept", 0.2),
            SemanticConcept("primary_concept", 0.95),
        ],
    )
    out = build_legend([el])
    assert out["legend"]["b0"] == "primary_concept"


def test_deterministic_ordering_by_row_id() -> None:
    shuffled = [_el(2, ControlType.MENU_ITEM), _el(0, ControlType.BUTTON), _el(1, ControlType.EDIT)]
    out = build_legend(shuffled)
    assert [r["row_id"] for r in out["elements"]] == [0, 1, 2]
    # First-seen-in-row-order claims b0/e0/m0.
    assert {r["row_id"]: r["code"] for r in out["elements"]} == {
        0: "b0",
        1: "e0",
        2: "m0",
    }


def test_empty_elements() -> None:
    out = build_legend([])
    assert out == {"legend": {}, "elements": [], "count": 0}


# --- read_legend operation through handle_line ---------------------------
def _form_snapshot(epoch: int = 1) -> Any:
    def _d(name: str, ct: int) -> dict[str, Any]:
        return {
            "name": name,
            "control_type": ct,
            "bounding_rect": {"left": 0, "top": 0, "width": 40, "height": 20},
        }

    walked = [
        (_d("Main Window", ControlType.WINDOW), 0, None),
        (_d("Save", ControlType.BUTTON), 1, 0),
        (_d("Cancel", ControlType.BUTTON), 1, 0),
    ]
    return build_snapshot(walked, epoch=epoch)


def test_read_legend_operation() -> None:
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        eng.register_seed(_form_snapshot())
        line = json.dumps({"msg_id": "m", "operation": "read_legend", "payload": {}})
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["error"] is None
    payload = resp["payload"]
    assert payload["count"] >= 1
    assert len(payload["elements"]) == 3
    # All three rows get codes; the two buttons may share or differ by concept.
    codes = {r["code"] for r in payload["elements"]}
    assert any(c.startswith("w") for c in codes)
    assert any(c.startswith("b") for c in codes)
