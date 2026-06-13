"""Seed semantic mappings + the element matcher.

Maps raw UIA elements to high-level domain concepts (``action_button``,
``text_input``, ``menu_item``, …) via heuristic rules. :data:`SEED_MAPPINGS`
mirrors the 20 ``INSERT INTO semantic_mappings`` rows in
``sql/cerebellum_cua_v42_schema.sql`` verbatim — the ``uia_control_type`` ints are the
REAL Microsoft UIA ControlTypeId constants (see that file's deviation note), not
the spec PDF's scrambled enum.

``match_element`` evaluates each mapping whose control type matches the element,
checks the rule predicates (see :mod:`cerebellum_cua.semantics._rules`), and emits a
:class:`~cerebellum_cua.model.SemanticConcept` per match, sorted by confidence.

Imports only ``cerebellum_cua.model`` + stdlib — no uia/storage/gateway dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cerebellum_cua.model import Element, SemanticConcept
from cerebellum_cua.semantics._rules import (
    EXCLUDE_CONCEPT_PREFIX,
    EXCLUDE_FIT,
    evaluate_rules,
)


@dataclass(slots=True)
class SemanticMapping:
    """One heuristic rule: control type + concept + confidence + predicate dict."""

    uia_control_type: int
    domain_concept: str
    confidence: float
    mapping_rules: dict[str, Any] = field(default_factory=dict)
    source: str = "heuristic"
    version: str = "v4.2"


# The 20 seed rows, verbatim from sql/cerebellum_cua_v42_schema.sql (Section 3 seed).
# control_type ints are real Microsoft UIA constants; confidences/concepts/rules
# match the SQL exactly. Order preserved for parity with the INSERT statement.
SEED_MAPPINGS: list[SemanticMapping] = [
    SemanticMapping(
        50000,
        "action_button",
        0.94,
        {
            "name_contains_any": [
                "submit", "ok", "login", "save", "apply", "confirm", "send",
            ],
            "patterns": ["invoke"],
            "is_enabled": True,
        },
    ),
    SemanticMapping(
        50000,
        "cancel_button",
        0.91,
        {
            "name_contains_any": ["cancel", "close", "dismiss", "abort"],
            "patterns": ["invoke"],
        },
    ),
    SemanticMapping(
        50004,
        "text_input",
        0.89,
        {
            "patterns": ["value"],
            "is_keyboard_focusable": True,
            "automation_id_contains_any": [
                "username", "password", "email", "search",
            ],
        },
    ),
    SemanticMapping(50002, "checkbox", 0.93, {"patterns": ["toggle"]}),
    SemanticMapping(
        50013,
        "radio_option",
        0.90,
        {"patterns": ["selection", "toggle"], "name_length_lt": 60},
    ),
    SemanticMapping(
        50003,
        "combo_box",
        0.88,
        {"patterns": ["expand_collapse", "value"]},
    ),
    SemanticMapping(50007, "list_item", 0.87, {"parent_control_type": 50008}),
    SemanticMapping(
        50008,
        "list_view",
        0.92,
        {"patterns": ["selection", "scroll"], "child_count_gt": 0},
    ),
    SemanticMapping(
        50011,
        "menu_item",
        0.95,
        {"patterns": ["invoke"], "parent_control_type": 50010},
    ),
    SemanticMapping(50010, "menu_bar", 0.91, {"child_count_gt": 1}),
    SemanticMapping(
        50028,
        "data_grid",
        0.93,
        {
            "patterns": ["grid", "table", "selection"],
            "framework_any": ["winform", "wpf"],
        },
    ),
    SemanticMapping(
        50029,
        "data_grid_row",
        0.89,
        {"parent_control_type": 50028, "patterns": ["selection"]},
    ),
    SemanticMapping(
        50019,
        "tab_item",
        0.90,
        {"patterns": ["selection"], "parent_control_type": 50018},
    ),
    SemanticMapping(
        50018,
        "tab_control",
        0.88,
        {"patterns": ["selection"], "child_count_gt": 1},
    ),
    SemanticMapping(
        50024,
        "tree_item",
        0.91,
        {"patterns": ["expand_collapse", "invoke"], "has_children_hint": True},
    ),
    SemanticMapping(50032, "window", 0.97, {"is_content": False}),
    SemanticMapping(
        50030,
        "document",
        0.85,
        {"framework_any": ["chrome", "edge", "mozilla"]},
    ),
    SemanticMapping(50014, "scroll_bar", 0.70, {"exclude": True}),
    SemanticMapping(
        50033,
        "pane_container",
        0.75,
        {"name_length_eq": 0, "child_count_eq": 0},
    ),
    SemanticMapping(
        50005,
        "hyperlink",
        0.92,
        {"patterns": ["invoke"], "name_contains": "http"},
    ),
]


def match_element(
    element: Element,
    mappings: list[SemanticMapping] = SEED_MAPPINGS,
    parent: Element | None = None,
) -> list[SemanticConcept]:
    """Infer domain concepts for ``element`` from the heuristic ``mappings``.

    Each mapping whose ``uia_control_type`` equals ``element.control_type`` has
    its rule predicates evaluated. On a match a :class:`SemanticConcept` is
    emitted with ``confidence * rule_fit``. An ``exclude`` rule emits a
    suppression concept (``exclude:<concept>``, confidence = mapping confidence)
    so callers can act on it. Results are sorted by confidence descending.

    ``parent`` supplies optional context for ``parent_control_type`` rules; when
    omitted those clauses are treated as neutral (passing).
    """
    concepts: list[SemanticConcept] = []
    for mapping in mappings:
        if mapping.uia_control_type != element.control_type:
            continue
        fit = evaluate_rules(element, mapping.mapping_rules, parent)
        if fit == EXCLUDE_FIT:
            concepts.append(
                SemanticConcept(
                    f"{EXCLUDE_CONCEPT_PREFIX}{mapping.domain_concept}",
                    mapping.confidence,
                )
            )
            continue
        if fit <= 0.0:
            continue
        concepts.append(
            SemanticConcept(mapping.domain_concept, mapping.confidence * fit)
        )
    concepts.sort(key=lambda c: c.confidence, reverse=True)
    return concepts
