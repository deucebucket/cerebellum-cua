"""Compact cipher / legend — token-saving shorthand for a single scan.

Repeating full element labels in every LLM turn is wasteful. Instead this module
emits a short code per *distinct concept* in one snapshot plus a one-time legend
mapping ``code -> meaning``, so the agent reads the legend once and then sees
cheap codes (``b0``, ``e1``, …) for the rest of that scan.

This is deliberately **not** a persistent position cache: the legend is a pure
function of one element list and is regenerated fresh every scan. Nothing is
stored or aliased across calls. (Pinned, persistent aliases are a possible future
extension and intentionally out of scope here — see docs/AGENT_INTEGRATION.md.)

The concept key for an element is its top semantic ``domain_concept`` when one is
present, else the :class:`~cerebellum_cua.model.ControlType` name lowercased. Codes
are grouped into single-letter families so the family alone hints at the kind of
control:

    button -> b   edit / text_input -> e   menu_item -> m
    window -> w   link / hyperlink -> l     everything else -> c
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cerebellum_cua.model import ControlType, Element

#: ControlType integer -> the single-letter code family it belongs to.
_CONTROL_FAMILY: dict[int, str] = {
    ControlType.BUTTON: "b",
    ControlType.SPLIT_BUTTON: "b",
    ControlType.RADIO_BUTTON: "b",
    ControlType.CHECK_BOX: "b",
    ControlType.EDIT: "e",
    ControlType.MENU_ITEM: "m",
    ControlType.WINDOW: "w",
    ControlType.HYPERLINK: "l",
}

#: Concept-name substrings -> family, checked when a semantic concept is present
#: (or the control type is unmapped). First match wins; order is significant.
_CONCEPT_FAMILY: tuple[tuple[str, str], ...] = (
    ("button", "b"),
    ("text_input", "e"),
    ("edit", "e"),
    ("menu_item", "m"),
    ("menu", "m"),
    ("window", "w"),
    ("link", "l"),
)


def _concept_key(element: Element) -> str:
    """Return the concept key for ``element`` (top semantic, else control name)."""
    if element.semantics:
        top = max(element.semantics, key=lambda s: s.confidence)
        if top.domain_concept:
            return top.domain_concept.lower()
    try:
        return ControlType(element.control_type).name.lower()
    except ValueError:
        return f"ct{element.control_type}"


def _family_for(element: Element, concept: str) -> str:
    """Pick the single-letter family for an element from concept then control type."""
    for needle, family in _CONCEPT_FAMILY:
        if needle in concept:
            return family
    return _CONTROL_FAMILY.get(element.control_type, "c")


def build_legend(elements: Sequence[Element]) -> dict[str, Any]:
    """Assign a short stable code per distinct concept in this snapshot.

    The mapping is deterministic: elements are processed in ascending ``row_id``
    order, so the first time a concept is seen it claims the next index within its
    family. Codes look like ``b0``, ``b1``, ``e0`` — a one-letter family followed
    by a per-family counter.

    Args:
        elements: The snapshot's elements (any order; sorted internally).

    Returns:
        ``{"legend": {code: meaning}, "elements": [{"row_id", "code"}],
        "count": int}`` where ``legend`` maps each code to its concept meaning,
        ``elements`` lists every input row with its assigned code (row_id order),
        and ``count`` is the number of distinct codes.
    """
    ordered = sorted(elements, key=lambda e: e.row_id)
    legend: dict[str, str] = {}
    code_for_concept: dict[str, str] = {}
    family_counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []

    for element in ordered:
        concept = _concept_key(element)
        code = code_for_concept.get(concept)
        if code is None:
            family = _family_for(element, concept)
            index = family_counts.get(family, 0)
            family_counts[family] = index + 1
            code = f"{family}{index}"
            code_for_concept[concept] = code
            legend[code] = concept
        rows.append({"row_id": element.row_id, "code": code})

    return {"legend": legend, "elements": rows, "count": len(legend)}


__all__ = ["build_legend"]
