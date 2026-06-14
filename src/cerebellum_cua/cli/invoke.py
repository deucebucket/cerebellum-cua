"""Live action execution through the capture seam (element + coordinate forms).

This is the action half of issue #3: drive the PC like a user without screenshots.
It routes ``invoke_action`` requests to the right place:

* **Element actions** load the persisted :class:`~cerebellum_cua.model.Element`, rebuild
  its re-acquisition identity from ``metadata`` (the AT-SPI child-index path stored
  at capture), ask the capture backend to re-find a live element, then invoke the
  semantic action on it (click / set_text / toggle / set_value / select / …).
* **Coordinate / raw-input forms** (``click_point`` / ``type`` / ``key``) bypass the
  accessibility tree and synthesize input via :class:`~cerebellum_cua.capture.input.SyntheticInput`.

Everything is import-safe on any host: capture/backend imports are lazy and any
"cannot run here" condition surfaces as a typed :class:`~cerebellum_cua.errors.UIAAccessDeniedError`
rather than a bare crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cerebellum_cua.errors import ElementNotFoundError, UIAAccessDeniedError
from cerebellum_cua.model import Element

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

# Coordinate / raw-input actions that bypass the a11y tree entirely.
_COORDINATE_ACTIONS = frozenset({"click_point", "type", "key"})

_NO_BACKEND = (
    "Live action execution is unavailable on this host: no capture backend could "
    "re-acquire the element (UIA needs Windows + 'uiautomation'; AT-SPI needs a "
    "reachable Linux a11y bus). The element could not be driven."
)

_NO_REACQUIRE = (
    "Could not re-acquire element row {row_id} on the live tree (it may have moved, "
    "closed, or the snapshot is stale). Re-run build_matrix and retry."
)


def perform_action(engine: CuaEngine, payload: dict[str, Any]) -> dict[str, Any]:
    """Execute one ``invoke_action`` request and return its result payload."""
    action = str(payload.get("action") or "invoke")
    if action in _COORDINATE_ACTIONS:
        guard = bool(getattr(engine, "user_takeover_guard", True))
        return _coordinate_action(action, payload, guard)
    return _element_action(engine, action, payload)


def _coordinate_action(
    action: str, payload: dict[str, Any], guard: bool
) -> dict[str, Any]:
    """Synthesize coordinate/raw input (no element, no snapshot needed).

    When ``guard`` is set, an :class:`~cerebellum_cua.capture.abort.AbortWatcher`
    is armed so genuine user input (key/mouse/panic key) cancels the in-progress
    synthetic motion. A takeover returns ``{"success": False, "aborted": True}``
    rather than raising. The watcher degrades to a no-op where evdev or
    ``/dev/input`` is unavailable, so this never blocks or crashes.
    """
    from cerebellum_cua.capture.abort import (  # noqa: PLC0415
        AbortedByUser,
        AbortWatcher,
    )
    from cerebellum_cua.capture.input import (  # noqa: PLC0415
        SyntheticInput,
        SyntheticInputError,
    )

    si = SyntheticInput()
    watcher = AbortWatcher() if guard else None
    abort = watcher.event if watcher is not None else None
    if watcher is not None:
        watcher.start()
    try:
        if action == "click_point":
            ok = si.click(
                int(payload["x"]), int(payload["y"]),
                button=str(payload.get("button", "left")),
                double=bool(payload.get("double", False)),
                abort=abort,
            )
        elif action == "type":
            ok = si.type_text(str(payload.get("value", "")), abort=abort)
        else:  # "key"
            ok = si.key(str(payload["value"]))
    except AbortedByUser:
        return {"success": False, "action": action, "aborted": True}
    except SyntheticInputError as exc:
        raise UIAAccessDeniedError(
            reason="synthetic_input_unavailable", detail=str(exc)
        ) from exc
    finally:
        if watcher is not None:
            watcher.stop()
    return {"success": bool(ok), "action": action}


def _element_action(
    engine: CuaEngine, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Re-acquire the persisted element and invoke a semantic action on it."""
    from cerebellum_cua.capture import (  # noqa: PLC0415
        ActionNotSupported,
        CaptureNotAvailable,
    )

    snapshot_id, row_id = _resolve_target(engine, payload)
    element = engine.storage.get_element(snapshot_id, row_id)
    if element is None:
        raise ElementNotFoundError(snapshot_id=snapshot_id, row_id=row_id)

    try:
        backend = engine.get_capture_backend()
        live = backend.reacquire(_identity(element))
    except (CaptureNotAvailable, ImportError) as exc:
        raise UIAAccessDeniedError(
            reason="capture_unavailable", detail=_NO_BACKEND
        ) from exc
    if live is None:
        raise UIAAccessDeniedError(
            reason="reacquire_failed", detail=_NO_REACQUIRE.format(row_id=row_id)
        )

    params = dict(payload.get("params") or {})
    if "value" in payload:
        params.setdefault("value", payload["value"])
    try:
        ok = backend.invoke(live, action, **params)
    except (ActionNotSupported, CaptureNotAvailable) as exc:
        raise UIAAccessDeniedError(
            reason="action_unsupported", detail=str(exc)
        ) from exc
    if not ok:
        return {"success": False, "action": action}
    return {
        "success": True,
        "action": action,
        "new_epoch": engine.current_epoch + 1,
        "affected_rows": [row_id],
    }


def _identity(element: Element) -> dict[str, Any]:
    """Build the backend re-acquisition identity from a persisted element.

    Keys: ``atspi_path`` (required for AT-SPI re-find), ``name`` and ``role`` for
    the loose verification, plus ``control_type``/``class_name`` as extra context.
    """
    meta = element.metadata or {}
    return {
        "atspi_path": meta.get("atspi_path"),
        "atspi_role": meta.get("atspi_role"),
        "name": element.name,
        "control_type": element.control_type,
        "class_name": element.class_name,
    }


def _resolve_target(engine: CuaEngine, payload: dict[str, Any]) -> tuple[int, int]:
    """Resolve (snapshot_id, row_id), defaulting snapshot to the latest persisted."""
    sid = payload.get("snapshot_id")
    if sid is None:
        sid = engine.storage.get_last_snapshot_id()
        if sid is None:
            raise ElementNotFoundError(reason="no_snapshot_persisted")
    return int(sid), int(payload["row_id"])
