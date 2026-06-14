"""Drive a Linux polkit authentication dialog through the engine.

A polkit prompt is just another GUI dialog with a password field and an
Authenticate button, so it is driven with the layers that already exist — no new
action primitives. The flow is:

1. ``build_matrix`` to capture the live tree (the auth dialog is on top).
2. :func:`cerebellum_cua.skills.find_one` to locate the password field (an
   ``EDIT`` control) in the captured snapshot.
3. ``invoke_action`` with ``set_text`` to fill the password into that field.
4. ``invoke_action`` with ``click`` on the Authenticate/OK button.

The engine is injected, so this is fully mockable: a test passes a stub engine
exposing ``handlers`` and ``storage`` and asserts the password was set into the
field and the confirm button clicked — without any real desktop.

The password is fed only into the ``set_text`` payload; it is never logged and
never placed into the returned :class:`ElevationResult`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cerebellum_cua.elevation.base import CONFIRM_LABELS, ElevationResult
from cerebellum_cua.skills.resolver import find_one

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

#: Raw UIA control-type for an editable text field (password input).
_EDIT_CONTROL_TYPE = 50004


def _load_elements(engine: CuaEngine, snapshot_id: int) -> list[Any]:
    """Fetch a snapshot's elements with their semantics hydrated for matching."""
    elements = engine.storage.get_all_elements(snapshot_id)
    for element in elements:
        element.semantics = engine.storage.get_semantic_concepts(
            snapshot_id, element.row_id
        )
    return elements


def _find_confirm(elements: list[Any]) -> Any | None:
    """Find the Authenticate/OK confirm button by name among ``elements``."""
    for label in CONFIRM_LABELS:
        match = find_one(elements, {"name": label})
        if match is not None:
            return match
    # Fall back to a substring match on the strongest label.
    return find_one(elements, {"name_contains": "authenticate"})


def drive_polkit(engine: CuaEngine, password: str) -> ElevationResult:
    """Fill and submit a visible polkit auth dialog using ``password``.

    Args:
        engine: A live (or mocked) engine exposing ``handlers`` (with
            ``build_matrix`` and ``invoke_action``) and ``storage``.
        password: The elevation password to enter. Never logged or returned.

    Returns:
        An :class:`ElevationResult` describing the outcome. ``success`` is True
        only when both the field was filled and the confirm button clicked. The
        ``detail`` string is always redacted.
    """
    build = engine.handlers["build_matrix"]({"target": {}})
    snapshot_id = int(build["snapshot_id"])
    elements = _load_elements(engine, snapshot_id)

    field = find_one(elements, {"role": _EDIT_CONTROL_TYPE})
    if field is None:
        return ElevationResult(
            success=False,
            method="polkit",
            needs_human=True,
            detail="no password field found in the auth dialog",
            extra={"snapshot_id": snapshot_id},
        )

    invoke = engine.handlers["invoke_action"]
    set_res = invoke(
        {"snapshot_id": snapshot_id, "row_id": field.row_id,
         "action": "set_text", "value": password}
    )
    if not set_res.get("success"):
        return ElevationResult(
            success=False,
            method="polkit",
            needs_human=True,
            detail="could not enter the password into the field",
            extra={"snapshot_id": snapshot_id, "field_row_id": field.row_id},
        )

    confirm = _find_confirm(elements)
    if confirm is None:
        return ElevationResult(
            success=False,
            method="polkit",
            needs_human=True,
            detail="password entered but no Authenticate button found",
            extra={"snapshot_id": snapshot_id},
        )

    click_res = invoke(
        {"snapshot_id": snapshot_id, "row_id": confirm.row_id, "action": "click"}
    )
    if not click_res.get("success"):
        return ElevationResult(
            success=False,
            method="polkit",
            detail="clicked Authenticate but the action reported failure",
            extra={"snapshot_id": snapshot_id, "confirm_row_id": confirm.row_id},
        )
    return ElevationResult(
        success=True,
        method="polkit",
        detail="entered password and clicked Authenticate",
        extra={"snapshot_id": snapshot_id, "confirm_row_id": confirm.row_id},
    )


__all__ = ["drive_polkit"]
