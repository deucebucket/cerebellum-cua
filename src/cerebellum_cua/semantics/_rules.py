"""Rule-predicate evaluation for semantic mappings.

A mapping's ``mapping_rules`` dict is a conjunction of predicate clauses. This
module evaluates that dict against a plain :class:`~cerebellum_cua.model.Element`,
returning a *rule-fit* score in ``[0.0, 1.0]``: ``1.0`` when every clause matches,
``0.0`` when any required clause fails, and a special sentinel for the ``exclude``
directive. No COM, no DB — pure data inspection so any layer/test can use it.

Supported rule keys (unknown keys are ignored / treated as neutral):

* ``name_contains_any`` / ``name_contains`` — case-insensitive substring on name.
* ``automation_id_contains_any`` — case-insensitive substring on automation_id.
* ``patterns`` — each named pattern must be present + ``supported`` truthy.
* ``is_enabled`` / ``is_keyboard_focusable`` — bool match against properties.
* ``is_content`` — bool match against ``element.is_content``.
* ``framework`` / ``framework_any`` — case-insensitive framework_id match.
* ``name_length_lt`` / ``name_length_eq`` — int bounds on ``len(name)``.
* ``child_count_gt`` / ``child_count_eq`` — int bounds on the child-stub count.
* ``parent_control_type`` — matched only when a parent context is supplied;
  neutral (passes) otherwise.
* ``has_children_hint`` — bool match against ``children_stub.has_children``.
* ``exclude`` — when truthy, signals the element should be suppressed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cerebellum_cua.model import Element

# Sentinel returned by ``evaluate_rules`` when a mapping's ``exclude`` directive
# fires. The caller emits a suppression concept rather than a normal match.
EXCLUDE_FIT = -1.0

# Concept name used to represent an excluded/suppressed element.
EXCLUDE_CONCEPT_PREFIX = "exclude:"


def _name(element: Element) -> str:
    return (element.name or "").lower()


def _automation_id(element: Element) -> str:
    return (element.automation_id or "").lower()


def _framework(element: Element) -> str:
    return (element.framework_id or "").lower()


def _pattern_supported(element: Element, pattern_name: str) -> bool:
    entry = element.patterns.get(pattern_name)
    if isinstance(entry, dict):
        return bool(entry.get("supported"))
    return bool(entry)


def _prop_bool(element: Element, key: str) -> bool:
    return bool(element.properties.get(key))


def _check_contains_any(haystack: str, needles: Any) -> bool:
    return any(str(n).lower() in haystack for n in needles)


def _check_patterns(element: Element, names: Any) -> bool:
    return all(_pattern_supported(element, str(n)) for n in names)


def _check_framework_any(element: Element, options: Any) -> bool:
    fw = _framework(element)
    return any(str(o).lower() == fw for o in options)


def _check_parent_control_type(parent: Element | None, expected: Any) -> bool:
    # Neutral when no parent context is available (treat as a pass).
    if parent is None:
        return True
    return parent.control_type == int(expected)


# Each handler answers True (clause satisfied) or False (clause failed). Handlers
# take (element, value, parent) so the dispatch table stays uniform.
def _h_name_contains_any(el: Element, v: Any, _p: Element | None) -> bool:
    return _check_contains_any(_name(el), v)


def _h_name_contains(el: Element, v: Any, _p: Element | None) -> bool:
    return str(v).lower() in _name(el)


def _h_automation_id_contains_any(el: Element, v: Any, _p: Element | None) -> bool:
    return _check_contains_any(_automation_id(el), v)


def _h_patterns(el: Element, v: Any, _p: Element | None) -> bool:
    return _check_patterns(el, v)


def _h_is_enabled(el: Element, v: Any, _p: Element | None) -> bool:
    return _prop_bool(el, "is_enabled") == bool(v)


def _h_is_keyboard_focusable(el: Element, v: Any, _p: Element | None) -> bool:
    return _prop_bool(el, "is_keyboard_focusable") == bool(v)


def _h_is_content(el: Element, v: Any, _p: Element | None) -> bool:
    return bool(el.is_content) == bool(v)


def _h_framework(el: Element, v: Any, _p: Element | None) -> bool:
    return _framework(el) == str(v).lower()


def _h_framework_any(el: Element, v: Any, _p: Element | None) -> bool:
    return _check_framework_any(el, v)


def _h_name_length_lt(el: Element, v: Any, _p: Element | None) -> bool:
    return len(el.name or "") < int(v)


def _h_name_length_eq(el: Element, v: Any, _p: Element | None) -> bool:
    return len(el.name or "") == int(v)


def _h_child_count_gt(el: Element, v: Any, _p: Element | None) -> bool:
    return el.children_stub.count > int(v)


def _h_child_count_eq(el: Element, v: Any, _p: Element | None) -> bool:
    return el.children_stub.count == int(v)


def _h_parent_control_type(el: Element, v: Any, p: Element | None) -> bool:
    return _check_parent_control_type(p, v)


def _h_has_children_hint(el: Element, v: Any, _p: Element | None) -> bool:
    return bool(el.children_stub.has_children) == bool(v)


_HANDLERS = {
    "name_contains_any": _h_name_contains_any,
    "name_contains": _h_name_contains,
    "automation_id_contains_any": _h_automation_id_contains_any,
    "patterns": _h_patterns,
    "is_enabled": _h_is_enabled,
    "is_keyboard_focusable": _h_is_keyboard_focusable,
    "is_content": _h_is_content,
    "framework": _h_framework,
    "framework_any": _h_framework_any,
    "name_length_lt": _h_name_length_lt,
    "name_length_eq": _h_name_length_eq,
    "child_count_gt": _h_child_count_gt,
    "child_count_eq": _h_child_count_eq,
    "parent_control_type": _h_parent_control_type,
    "has_children_hint": _h_has_children_hint,
}


def evaluate_rules(
    element: Element,
    rules: dict[str, Any],
    parent: Element | None = None,
) -> float:
    """Score ``rules`` against ``element``.

    Returns the rule-fit in ``[0.0, 1.0]`` (currently boolean: all clauses pass
    -> ``1.0``, any required clause fails -> ``0.0``), or :data:`EXCLUDE_FIT`
    when the rule carries a truthy ``exclude`` directive. An empty rule set is a
    neutral match (``1.0``). Unknown keys are ignored.
    """
    if rules.get("exclude"):
        return EXCLUDE_FIT
    for key, value in rules.items():
        handler = _HANDLERS.get(key)
        if handler is None:
            continue  # unknown key -> neutral
        if not handler(element, value, parent):
            return 0.0
    return 1.0
