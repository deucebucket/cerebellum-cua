"""Unit tests for the semantics layer — plain ``Element`` instances, no COM/DB.

Pins the seed mappings and the ``match_element`` matcher: control-type gating,
rule-predicate evaluation, exclude suppression, confidence ordering, and parity
of :data:`SEED_MAPPINGS` with the SQL seed (20 rows).
"""

from __future__ import annotations

from cerebellum_cua.model import ChildStub, Element, SemanticConcept
from cerebellum_cua.semantics import SEED_MAPPINGS, SemanticMapping, match_element


def _pattern(*names: str) -> dict[str, dict[str, object]]:
    """Build a ``patterns`` dict marking each named pattern supported."""
    return {n: {"supported": True} for n in names}


def test_seed_has_20_entries() -> None:
    assert len(SEED_MAPPINGS) == 20
    assert all(isinstance(m, SemanticMapping) for m in SEED_MAPPINGS)


def test_seed_control_types_are_real_uia_constants() -> None:
    # Spot-check the corrected ints: Edit=50004, Window=50032, Button=50000.
    by_concept = {m.domain_concept: m for m in SEED_MAPPINGS}
    assert by_concept["text_input"].uia_control_type == 50004
    assert by_concept["window"].uia_control_type == 50032
    assert by_concept["action_button"].uia_control_type == 50000


def test_button_submit_is_action_button() -> None:
    el = Element(
        row_id=1,
        control_type=50000,
        name="Submit",
        patterns=_pattern("invoke"),
        properties={"is_enabled": True},
    )
    concepts = match_element(el)
    names = [c.domain_concept for c in concepts]
    assert "action_button" in names
    assert "cancel_button" not in names
    top = concepts[0]
    assert top.domain_concept == "action_button"
    assert top.confidence == 0.94


def test_button_cancel_is_cancel_button() -> None:
    el = Element(
        row_id=2,
        control_type=50000,
        name="Cancel",
        patterns=_pattern("invoke"),
    )
    concepts = match_element(el)
    names = [c.domain_concept for c in concepts]
    assert names == ["cancel_button"]
    assert concepts[0].confidence == 0.91


def test_edit_with_focus_and_username_is_text_input() -> None:
    el = Element(
        row_id=3,
        control_type=50004,
        name="",
        automation_id="username",
        patterns=_pattern("value"),
        properties={"is_keyboard_focusable": True},
    )
    concepts = match_element(el)
    assert [c.domain_concept for c in concepts] == ["text_input"]
    assert concepts[0].confidence == 0.89


def test_edit_without_focus_does_not_match() -> None:
    el = Element(
        row_id=4,
        control_type=50004,
        automation_id="username",
        patterns=_pattern("value"),
        properties={"is_keyboard_focusable": False},
    )
    assert match_element(el) == []


def test_window_is_window() -> None:
    el = Element(row_id=5, control_type=50032, name="Main Window", is_content=False)
    concepts = match_element(el)
    assert [c.domain_concept for c in concepts] == ["window"]
    assert concepts[0].confidence == 0.97


def test_window_with_content_does_not_match() -> None:
    el = Element(row_id=6, control_type=50032, is_content=True)
    assert match_element(el) == []


def test_non_matching_control_type_yields_nothing() -> None:
    # A Text element (50020) has no seed mapping.
    el = Element(row_id=7, control_type=50020, name="hello")
    assert match_element(el) == []


def test_confidence_sorted_descending() -> None:
    # Button "Save" matches action_button (0.94) but not cancel_button.
    el = Element(
        row_id=8,
        control_type=50000,
        name="Save",
        patterns=_pattern("invoke"),
        properties={"is_enabled": True},
    )
    concepts = match_element(el)
    confidences = [c.confidence for c in concepts]
    assert confidences == sorted(confidences, reverse=True)


def test_action_button_requires_enabled() -> None:
    el = Element(
        row_id=9,
        control_type=50000,
        name="Submit",
        patterns=_pattern("invoke"),
        properties={"is_enabled": False},
    )
    assert match_element(el) == []


def test_parent_control_type_neutral_without_parent() -> None:
    # list_item rule requires parent_control_type=50008; with no parent it passes.
    el = Element(row_id=10, control_type=50007, name="Row 1")
    concepts = match_element(el)
    assert [c.domain_concept for c in concepts] == ["list_item"]


def test_parent_control_type_matches_with_parent() -> None:
    parent = Element(row_id=0, control_type=50008)
    el = Element(row_id=11, control_type=50007, name="Row 1")
    concepts = match_element(el, parent=parent)
    assert [c.domain_concept for c in concepts] == ["list_item"]


def test_parent_control_type_mismatch_with_parent() -> None:
    parent = Element(row_id=0, control_type=50000)  # wrong parent type
    el = Element(row_id=12, control_type=50007, name="Row 1")
    assert match_element(el, parent=parent) == []


def test_scroll_bar_is_excluded() -> None:
    el = Element(row_id=13, control_type=50014, name="Vertical")
    concepts = match_element(el)
    assert len(concepts) == 1
    assert concepts[0].domain_concept == "exclude:scroll_bar"
    assert concepts[0].confidence == 0.70


def test_menu_bar_child_count_gt() -> None:
    el = Element(
        row_id=14,
        control_type=50010,
        name="MenuBar",
        children_stub=ChildStub(has_children=True, count=3),
    )
    concepts = match_element(el)
    assert [c.domain_concept for c in concepts] == ["menu_bar"]


def test_menu_bar_too_few_children_does_not_match() -> None:
    el = Element(
        row_id=15,
        control_type=50010,
        children_stub=ChildStub(has_children=True, count=1),
    )
    assert match_element(el) == []


def test_returns_semantic_concept_instances() -> None:
    el = Element(
        row_id=16,
        control_type=50000,
        name="OK",
        patterns=_pattern("invoke"),
        properties={"is_enabled": True},
    )
    concepts = match_element(el)
    assert all(isinstance(c, SemanticConcept) for c in concepts)
