"""Element resolution for the skills layer (pure, engine-free).

A *skill* is a named high-level action that resolves a target element, acts on
it, and verifies the result. This module owns the **resolve** half: turning a
small query dict into the matching :class:`~cerebellum_cua.model.Element`\\(s) from
a flat list — no engine, no storage, no I/O, so it is trivially unit-testable.

Query fields (all optional, AND-combined):

* ``name`` — exact match, case-insensitive.
* ``name_contains`` — substring of the element name, case-insensitive.
* ``text_contains`` — substring of the name OR ``properties["text_content"]``.
* ``role`` / ``control_type`` — a raw UIA control-type int, or a
  :class:`~cerebellum_cua.model.ControlType` member name (e.g. ``"EDIT"``).
* ``semantic`` — a ``domain_concept`` present in ``element.semantics``.
* ``nth`` — pick the nth match in stable order (default 0).

Matches are returned in a stable order: top-to-bottom then left-to-right by the
element's bounding rect (with ``row_id`` as a final tiebreak), so a given query
always resolves to the same element for an unchanged snapshot.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.model import ControlType, Element

# Query keys this resolver understands; anything else is ignored (forward-safe
# for the future landmark/alias cache in #22).
_KNOWN_KEYS = frozenset(
    {
        "name",
        "name_contains",
        "text_contains",
        "role",
        "control_type",
        "semantic",
        "nth",
    }
)


def _coerce_control_type(value: Any) -> int | None:
    """Coerce a role query value to a raw UIA control-type int, or None.

    Accepts an int (raw constant), a numeric string, or a
    :class:`~cerebellum_cua.model.ControlType` member name (case-insensitive).
    Returns ``None`` for anything unrecognized so the clause simply fails to
    match rather than raising.
    """
    if isinstance(value, bool):  # guard: bools are ints in Python
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    member = ControlType.__members__.get(text.upper())
    return int(member) if member is not None else None


def _element_text(element: Element) -> str:
    """Return the element's name plus any ``text_content`` for text matching."""
    parts = [element.name or ""]
    content = element.properties.get("text_content")
    if content:
        parts.append(str(content))
    return "\n".join(parts)


def _matches(element: Element, query: dict[str, Any]) -> bool:
    """True if ``element`` satisfies every present (AND-combined) query clause."""
    name = element.name or ""
    if "name" in query and name.casefold() != str(query["name"]).casefold():
        return False
    if "name_contains" in query:
        if str(query["name_contains"]).casefold() not in name.casefold():
            return False
    if "text_contains" in query:
        haystack = _element_text(element).casefold()
        if str(query["text_contains"]).casefold() not in haystack:
            return False
    role = query.get("role", query.get("control_type"))
    if role is not None:
        wanted = _coerce_control_type(role)
        if wanted is None or element.control_type != wanted:
            return False
    if "semantic" in query:
        concepts = {c.domain_concept for c in element.semantics}
        if str(query["semantic"]) not in concepts:
            return False
    return True


def _sort_key(element: Element) -> tuple[int, int, int]:
    """Stable top-to-bottom, left-to-right ordering key (row_id breaks ties)."""
    rect = element.bounding_rect
    return (rect.top, rect.left, element.row_id)


def find_elements(elements: list[Element], query: dict[str, Any]) -> list[Element]:
    """Return every element matching ``query``, in stable visual order.

    ``query`` is the AND-combination of the fields documented at module level;
    unknown keys are ignored. ``nth`` is **not** applied here — this returns the
    full ordered match list. An empty/None query returns all elements sorted.
    """
    query = {k: v for k, v in (query or {}).items() if k in _KNOWN_KEYS}
    matched = [e for e in elements if _matches(e, query)]
    matched.sort(key=_sort_key)
    return matched


def find_one(elements: list[Element], query: dict[str, Any]) -> Element | None:
    """Return the ``nth`` matching element (default 0), or None if out of range.

    ``nth`` is read from ``query`` (default 0); negative indices count from the
    end like normal Python indexing. Returns ``None`` when no match exists at
    that position rather than raising.
    """
    nth = int((query or {}).get("nth", 0) or 0)
    matched = find_elements(elements, query)
    if not matched:
        return None
    try:
        return matched[nth]
    except IndexError:
        return None
