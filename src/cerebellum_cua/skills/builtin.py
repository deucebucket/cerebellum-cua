"""Built-in skills: resolve a target, act on it, return a structured result.

Each skill is a function ``(engine, **args) -> dict`` that composes the existing
layers — it loads the latest snapshot's elements (via storage), resolves one
with :mod:`cerebellum_cua.skills.resolver`, and drives it through the engine's
``invoke_action`` handler (which already re-acquires the live element and runs
the optional verify loop). Skills never reimplement actions or verification.

Every skill returns a dict containing at least ``{"skill", "resolved_row_id",
"success"}`` plus, on a resolved action, the keys from the underlying
``invoke_action`` result (``action``, ``affected_rows``, ``verified`` when
verification is enabled, …). When resolution fails the skill returns
``{"skill", "success": False, "reason": "not_found", "query": {...}}`` — it
never raises.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.errors import SnapshotNotFoundError
from cerebellum_cua.model import Element
from cerebellum_cua.skills.resolver import find_one

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

Skill = Callable[..., dict[str, Any]]


def _load_elements(engine: CuaEngine, snapshot_id: int | None) -> list[Element]:
    """Return a snapshot's elements with their semantics hydrated for matching.

    Resolves the snapshot id (explicit, else latest persisted) and fetches every
    element, attaching each one's domain concepts so ``semantic`` queries work.
    Raises :class:`~cerebellum_cua.errors.SnapshotNotFoundError` when nothing is
    persisted yet.
    """
    sid = snapshot_id
    if sid is None:
        sid = engine.storage.get_last_snapshot_id()
        if sid is None:
            raise SnapshotNotFoundError(reason="no_snapshot_persisted")
    elements = engine.storage.get_all_elements(int(sid))
    for element in elements:
        element.semantics = engine.storage.get_semantic_concepts(
            int(sid), element.row_id
        )
    return elements


def _resolve(
    engine: CuaEngine, query: dict[str, Any]
) -> tuple[int | None, Element | None]:
    """Resolve a single element for ``query`` against the latest snapshot."""
    snapshot_id = query.get("snapshot_id")
    elements = _load_elements(engine, snapshot_id)
    element = find_one(elements, query)
    sid = snapshot_id
    if sid is None:
        sid = engine.storage.get_last_snapshot_id()
    return (int(sid) if sid is not None else None, element)


def _not_found(skill: str, query: dict[str, Any]) -> dict[str, Any]:
    """Build the uniform not-found result for a skill that could not resolve."""
    return {
        "skill": skill,
        "resolved_row_id": None,
        "success": False,
        "reason": "not_found",
        "query": dict(query),
    }


def _invoke(
    engine: CuaEngine,
    skill: str,
    snapshot_id: int | None,
    element: Element,
    action: str,
    **extra: Any,
) -> dict[str, Any]:
    """Run one action through the engine's invoke_action handler + wrap it."""
    payload: dict[str, Any] = {"row_id": element.row_id, "action": action}
    if snapshot_id is not None:
        payload["snapshot_id"] = snapshot_id
    payload.update(extra)
    result = engine.handlers["invoke_action"](payload)
    return {"skill": skill, "resolved_row_id": element.row_id, **result}


def click(engine: CuaEngine, **query: Any) -> dict[str, Any]:
    """Resolve one element by ``query`` and invoke its primary action ("click").

    The capture backend maps "click" to the element's invoke pattern; this is the
    skill for buttons, menu items, links, list items, and the like.
    """
    snapshot_id, element = _resolve(engine, query)
    if element is None:
        return _not_found("click", query)
    return _invoke(engine, "click", snapshot_id, element, "click")


def type_into(engine: CuaEngine, value: str, **query: Any) -> dict[str, Any]:
    """Resolve a field by ``query`` and set its text to ``value``.

    Uses the "set_text" action (a single atomic value assignment). If that action
    is unsupported by the backend the skill falls back to clicking the field and
    typing ``value`` as raw keystrokes.
    """
    snapshot_id, element = _resolve(engine, query)
    if element is None:
        return _not_found("type_into", query)
    result = _invoke(
        engine, "type_into", snapshot_id, element, "set_text", value=value
    )
    if result.get("success"):
        return result
    # Fallback: focus the field, then type the value as raw keystrokes.
    _invoke(engine, "type_into", snapshot_id, element, "click")
    typed = engine.handlers["invoke_action"]({"action": "type", "value": value})
    return {
        "skill": "type_into",
        "resolved_row_id": element.row_id,
        "fallback": "click_then_type",
        **typed,
    }


def open(engine: CuaEngine, **query: Any) -> dict[str, Any]:
    """Resolve a launcher/menu-item/desktop-icon by name and invoke it.

    Functionally an alias of :func:`click` named for intent — opening apps,
    menus, files. Resolution works against the latest snapshot, so call the
    ``run_skill`` operation with ``capture: true`` to build a fresh tree first
    (e.g. resolving a desktop icon from a cold start).
    """
    snapshot_id, element = _resolve(engine, query)
    if element is None:
        return _not_found("open", query)
    return _invoke(engine, "open", snapshot_id, element, "click")


def focus(engine: CuaEngine, **query: Any) -> dict[str, Any]:
    """Resolve an element and give it focus (via a click)."""
    snapshot_id, element = _resolve(engine, query)
    if element is None:
        return _not_found("focus", query)
    return _invoke(engine, "focus", snapshot_id, element, "click")


def read(engine: CuaEngine, **query: Any) -> dict[str, Any]:
    """Resolve an element and return its text — no action is performed.

    Prefers ``properties["text_content"]`` and falls back to the element name.
    """
    _snapshot_id, element = _resolve(engine, query)
    if element is None:
        return _not_found("read", query)
    text = element.properties.get("text_content") or element.name or ""
    return {
        "skill": "read",
        "resolved_row_id": element.row_id,
        "success": True,
        "text": text,
    }


#: The built-in skill registry: name -> callable. Extend by adding a function
#: above and an entry here (the ``run_skill`` operation dispatches against it).
SKILLS: dict[str, Skill] = {
    "click": click,
    "type_into": type_into,
    "open": open,
    "focus": focus,
    "read": read,
}


def run_skill(engine: CuaEngine, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch ``name`` against :data:`SKILLS` with ``args`` as kwargs.

    Returns the skill result dict. On an unknown skill returns
    ``{"skill": name, "success": False, "reason": "unknown_skill"}`` rather than
    raising, so a bad name degrades gracefully like a failed resolution.
    """
    skill = SKILLS.get(name)
    if skill is None:
        return {"skill": name, "success": False, "reason": "unknown_skill"}
    return skill(engine, **dict(args or {}))
