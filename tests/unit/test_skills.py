"""Unit tests for the skills/macros layer — Linux-testable, no live display.

Two halves:

* the **resolver** is exercised as a pure function over hand-built
  :class:`~cerebellum_cua.model.Element` lists (name/contains/text/role/semantic/
  nth matching + stable ordering);
* the **built-in skills** + the ``run_skill`` operation are driven through a
  seeded :class:`~cerebellum_cua.cli.CuaEngine` with the capture seam
  monkeypatched, so no live UIA/AT-SPI tree is ever touched.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from cerebellum_cua.cli import CuaEngine
from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.model import (
    BoundingRect,
    ControlType,
    Element,
    SemanticConcept,
    Snapshot,
)
from cerebellum_cua.skills import find_elements, find_one, run_skill
from cerebellum_cua.skills.builtin import click, focus, open, read, type_into

SECRET = "unit-test-secret"


# --- resolver fixtures (pure Elements) ----------------------------------
def _el(
    row_id: int,
    name: str,
    ct: int,
    *,
    top: int = 0,
    left: int = 0,
    text: str | None = None,
    semantics: list[str] | None = None,
) -> Element:
    props: dict[str, Any] = {}
    if text is not None:
        props["text_content"] = text
    return Element(
        row_id=row_id,
        control_type=ct,
        name=name,
        bounding_rect=BoundingRect(left=left, top=top, width=10, height=10),
        properties=props,
        semantics=[SemanticConcept(c, 0.9) for c in (semantics or [])],
    )


def _sample() -> list[Element]:
    return [
        _el(0, "Save", 50000, top=30, left=10, semantics=["action_button"]),
        _el(1, "Cancel", 50000, top=30, left=80, semantics=["cancel_button"]),
        _el(2, "Username", 50004, top=10, left=10, semantics=["text_input"]),
        _el(3, "Save As", 50011, top=60, left=10, text="Save the file as..."),
        _el(4, "save", 50000, top=5, left=5),  # case variant, topmost
    ]


# --- resolver: matching --------------------------------------------------
def test_name_exact_case_insensitive() -> None:
    els = _sample()
    matched = find_elements(els, {"name": "save"})
    assert {e.row_id for e in matched} == {0, 4}


def test_name_contains() -> None:
    els = _sample()
    matched = find_elements(els, {"name_contains": "save"})
    assert {e.row_id for e in matched} == {0, 3, 4}


def test_text_contains_matches_name_or_text_content() -> None:
    els = _sample()
    # "the file" only appears in row 3's text_content, not its name.
    matched = find_elements(els, {"text_contains": "the file"})
    assert [e.row_id for e in matched] == [3]


def test_role_by_int_and_by_name() -> None:
    els = _sample()
    by_int = find_elements(els, {"role": 50004})
    by_name = find_elements(els, {"role": "EDIT"})
    assert [e.row_id for e in by_int] == [2]
    assert [e.row_id for e in by_name] == [2]
    assert int(ControlType.EDIT) == 50004


def test_control_type_alias_key() -> None:
    els = _sample()
    matched = find_elements(els, {"control_type": "MENU_ITEM"})
    assert [e.row_id for e in matched] == [3]


def test_semantic_matching() -> None:
    els = _sample()
    matched = find_elements(els, {"semantic": "text_input"})
    assert [e.row_id for e in matched] == [2]


def test_and_combined_clauses() -> None:
    els = _sample()
    matched = find_elements(els, {"name_contains": "save", "role": "BUTTON"})
    assert {e.row_id for e in matched} == {0, 4}


# --- resolver: ordering + nth -------------------------------------------
def test_ordering_top_to_bottom_left_to_right() -> None:
    els = _sample()
    ordered = find_elements(els, {})
    # by (top, left, row_id): 4(5,5) 2(10,10) 0(30,10) 1(30,80) 3(60,10)
    assert [e.row_id for e in ordered] == [4, 2, 0, 1, 3]


def test_find_one_default_first_in_order() -> None:
    els = _sample()
    # name=save matches rows 0 and 4; row 4 is topmost so it sorts first.
    assert find_one(els, {"name": "save"}).row_id == 4


def test_find_one_nth() -> None:
    els = _sample()
    assert find_one(els, {"name": "save", "nth": 1}).row_id == 0


def test_find_one_nth_out_of_range_returns_none() -> None:
    els = _sample()
    assert find_one(els, {"name": "save", "nth": 5}) is None


def test_find_one_no_match_returns_none() -> None:
    assert find_one(_sample(), {"name": "Nonexistent"}) is None


# --- engine-backed skill tests ------------------------------------------
def _elem_dict(name: str, ct: int, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "control_type": ct,
        "class_name": "Cls",
        "bounding_rect": {"left": 0, "top": 0, "width": 40, "height": 20},
    }
    base.update(extra)
    return base


def _form_snapshot(epoch: int = 1) -> Snapshot:
    """window(0) -> [Save button(1), edit field(2, text_content)]."""
    walked = [
        (_elem_dict("Main Window", 50032), 0, None),
        (
            _elem_dict(
                "Save",
                50000,
                patterns={"invoke": {"supported": True}},
                properties={"is_enabled": True},
            ),
            1,
            0,
        ),
        (
            _elem_dict(
                "field",
                50004,
                properties={"text_content": "hello world"},
            ),
            1,
            0,
        ),
    ]
    return build_snapshot(walked, epoch=epoch)


@pytest.fixture()
def engine() -> Any:
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    yield eng
    eng.close()


def _stub_invoke(engine: CuaEngine, monkeypatch: Any) -> list[dict[str, Any]]:
    """Replace invoke_action handler with a recorder returning success."""
    calls: list[dict[str, Any]] = []

    def _fake(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        return {
            "success": True,
            "action": payload.get("action", "invoke"),
            "affected_rows": [payload.get("row_id")],
        }

    engine.handlers["invoke_action"] = _fake
    return calls


def test_click_resolves_and_invokes(engine: CuaEngine, monkeypatch: Any) -> None:
    engine.register_seed(_form_snapshot())
    calls = _stub_invoke(engine, monkeypatch)
    result = click(engine, name="Save")
    assert result["skill"] == "click"
    assert result["resolved_row_id"] == 1
    assert result["success"] is True
    assert calls[0]["row_id"] == 1
    assert calls[0]["action"] == "click"


def test_type_into_sets_text(engine: CuaEngine, monkeypatch: Any) -> None:
    engine.register_seed(_form_snapshot())
    calls = _stub_invoke(engine, monkeypatch)
    result = type_into(engine, "typed value", role="EDIT")
    assert result["skill"] == "type_into"
    assert result["resolved_row_id"] == 2
    assert result["success"] is True
    assert calls[0]["action"] == "set_text"
    assert calls[0]["value"] == "typed value"


def test_type_into_falls_back_to_click_then_type(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    engine.register_seed(_form_snapshot())
    calls: list[dict[str, Any]] = []

    def _fake(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        action = payload.get("action")
        if action == "set_text":
            return {"success": False, "action": "set_text"}
        return {"success": True, "action": action}

    engine.handlers["invoke_action"] = _fake
    result = type_into(engine, "fallback text", role="EDIT")
    assert result["fallback"] == "click_then_type"
    assert result["success"] is True
    actions = [c["action"] for c in calls]
    assert actions == ["set_text", "click", "type"]
    assert calls[-1]["value"] == "fallback text"


def test_open_resolves_by_name(engine: CuaEngine, monkeypatch: Any) -> None:
    engine.register_seed(_form_snapshot())
    calls = _stub_invoke(engine, monkeypatch)
    result = open(engine, name="Save")
    assert result["skill"] == "open"
    assert result["resolved_row_id"] == 1
    assert calls[0]["action"] == "click"


def test_focus_resolves_and_clicks(engine: CuaEngine, monkeypatch: Any) -> None:
    engine.register_seed(_form_snapshot())
    calls = _stub_invoke(engine, monkeypatch)
    result = focus(engine, role="EDIT")
    assert result["skill"] == "focus"
    assert result["resolved_row_id"] == 2
    assert calls[0]["action"] == "click"


def test_read_returns_text_no_action(engine: CuaEngine, monkeypatch: Any) -> None:
    engine.register_seed(_form_snapshot())
    calls = _stub_invoke(engine, monkeypatch)
    result = read(engine, role="EDIT")
    assert result["skill"] == "read"
    assert result["success"] is True
    assert result["text"] == "hello world"
    assert calls == []  # read performs no action


def test_skill_not_found_returns_success_false(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    engine.register_seed(_form_snapshot())
    _stub_invoke(engine, monkeypatch)
    result = click(engine, name="Nonexistent")
    assert result["success"] is False
    assert result["reason"] == "not_found"
    assert result["resolved_row_id"] is None
    assert result["query"] == {"name": "Nonexistent"}


def test_run_skill_unknown_name() -> None:
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        result = run_skill(eng, "frobnicate", {})
    finally:
        eng.close()
    assert result["success"] is False
    assert result["reason"] == "unknown_skill"


# --- run_skill operation through handle_line ----------------------------
def _call(engine: CuaEngine, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    line = json.dumps({"msg_id": "m", "operation": operation, "payload": payload})
    return json.loads(engine.handle_line(line))


def test_run_skill_operation_resolves_and_acts(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    engine.register_seed(_form_snapshot())
    _stub_invoke(engine, monkeypatch)
    resp = _call(
        engine, "run_skill", {"skill": "click", "args": {"name": "Save"}}
    )
    assert resp["error"] is None
    payload = resp["payload"]
    assert payload["skill"] == "click"
    assert payload["resolved_row_id"] == 1
    assert payload["success"] is True


def test_run_skill_operation_unknown_skill(engine: CuaEngine) -> None:
    engine.register_seed(_form_snapshot())
    resp = _call(engine, "run_skill", {"skill": "nope", "args": {}})
    assert resp["error"] is None
    assert resp["payload"]["success"] is False
    assert resp["payload"]["reason"] == "unknown_skill"


def test_run_skill_capture_builds_first(engine: CuaEngine, monkeypatch: Any) -> None:
    # No snapshot persisted; capture=true must trigger a build_matrix before
    # resolving. We stub build_matrix to seed a snapshot, and invoke_action.
    built: list[bool] = []

    def _fake_build(payload: dict[str, Any]) -> dict[str, Any]:
        built.append(True)
        return engine.register_seed(_form_snapshot())

    engine.handlers["build_matrix"] = _fake_build
    engine._handlers.build_matrix = _fake_build  # type: ignore[method-assign]
    _stub_invoke(engine, monkeypatch)
    resp = _call(
        engine,
        "run_skill",
        {"skill": "click", "args": {"name": "Save"}, "capture": True},
    )
    assert built == [True]
    assert resp["payload"]["success"] is True


def test_click_falls_back_to_coordinates_when_reacquire_fails(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # When the live node can't be re-acquired, click the element's known bbox
    # center instead of hard-failing (robust for menus/popovers/dynamic UIs).
    from cerebellum_cua.errors import UIAAccessDeniedError

    calls: list[dict[str, Any]] = []

    def _fake(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        if payload.get("action") == "click_point":
            return {"success": True, "action": "click_point"}
        raise UIAAccessDeniedError(
            reason="reacquire_failed", detail="gone")

    engine.register_seed(_form_snapshot())
    engine.handlers["invoke_action"] = _fake
    result = click(engine, name="Save")  # Save bbox 0,0,40,20 -> center 20,10
    assert result["success"] is True
    assert result["fallback"] == "coordinate_click"
    assert calls[0]["action"] == "click"            # element action tried first
    assert calls[1] == {"action": "click_point", "x": 20, "y": 10}


def test_click_reraises_non_reacquire_errors(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    # A different failure (e.g. unsupported action) must NOT trigger a blind click.
    import pytest

    from cerebellum_cua.errors import UIAAccessDeniedError

    def _fake(payload: dict[str, Any]) -> dict[str, Any]:
        raise UIAAccessDeniedError(reason="action_unsupported", detail="nope")

    engine.register_seed(_form_snapshot())
    engine.handlers["invoke_action"] = _fake
    with pytest.raises(UIAAccessDeniedError):
        click(engine, name="Save")
