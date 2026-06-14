"""Representation operations: compact cipher legend, annotated + wireframe views.

These three read-only operations turn a stored snapshot into cheaper or more
visual representations for an agent:

* ``read_legend`` — a token-saving shorthand (one short code per distinct concept
  + a one-time ``code -> meaning`` legend), regenerated fresh each call.
* ``wireframe`` — a glanceable ASCII layout map built from the stored elements.
* ``annotate`` — a set-of-marks overlay of element boxes (+ legend codes) drawn
  onto a screenshot for visual grounding / docs.

They are kept here (rather than in :mod:`cerebellum_cua.cli.handlers`) to honour the
~300-line-per-module cap. Each takes the bound :class:`OperationHandlers` so it can
reuse its engine/storage access and snapshot resolution. Heavy/optional deps
(OpenCV, the screenshot grabber) stay lazy and guarded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.errors import UIAAccessDeniedError

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def representation_ops(handlers: OperationHandlers) -> dict[str, Handler]:
    """Return the read-only representation ops bound to ``handlers``.

    Registered into the engine's operation table by
    :meth:`cerebellum_cua.cli.handlers.OperationHandlers.as_dict`.
    """
    return {
        "read_legend": lambda p: read_legend(handlers, p),
        "wireframe": lambda p: wireframe(handlers, p),
        "annotate": lambda p: annotate(handlers, p),
    }

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.handlers import OperationHandlers
    from cerebellum_cua.model import Element


def _hydrated_elements(handlers: OperationHandlers, snapshot_id: int) -> list[Element]:
    """Fetch a snapshot's elements with their semantics attached.

    Mirrors ``skills.builtin._load_elements`` so concept-based labeling sees the
    same domain concepts the resolver does.
    """
    storage = handlers._engine.storage
    elements = storage.get_all_elements(snapshot_id)
    for element in elements:
        element.semantics = storage.get_semantic_concepts(snapshot_id, element.row_id)
    return elements


def read_legend(handlers: OperationHandlers, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a fresh compact code legend for a snapshot's elements.

    A token-saving shorthand: instead of repeating full labels, assign one short
    code per distinct concept (top semantic, else control type) plus a one-time
    ``code -> meaning`` legend. Regenerated fresh each call — nothing is
    persisted. Payload: ``{snapshot_id?: int}`` (default latest). Returns
    :func:`cerebellum_cua.legend.build_legend`'s ``{"legend", "elements", "count"}``.
    """
    from cerebellum_cua.legend import build_legend  # noqa: PLC0415 - lazy

    elements = _hydrated_elements(handlers, handlers._snapshot_id(payload))
    return build_legend(elements)


def wireframe(handlers: OperationHandlers, payload: dict[str, Any]) -> dict[str, Any]:
    """Render a snapshot's elements as a compact ASCII wireframe.

    A glanceable text layout map (boxes + truncated labels) built from stored
    elements — no live capture. Payload: ``{snapshot_id?: int}`` (default latest).
    Returns ``{"text": str}``.
    """
    from cerebellum_cua.capture.base import CapturedElement  # noqa: PLC0415
    from cerebellum_cua.capture.vision._ascii import render_ascii  # noqa: PLC0415

    elements = handlers._engine.storage.get_all_elements(handlers._snapshot_id(payload))
    captured = [
        CapturedElement(
            control_type=e.control_type,
            name=e.name,
            bounding_rect=e.bounding_rect,
        )
        for e in elements
    ]
    return {"text": render_ascii(captured)}


def annotate(handlers: OperationHandlers, payload: dict[str, Any]) -> dict[str, Any]:
    """Draw element boxes + legend codes onto a screenshot (set-of-marks).

    Grabs a fresh screenshot (or uses a provided ``path``), then overlays each of
    the snapshot's element bounding boxes with a short label (the compact legend
    code from :func:`cerebellum_cua.legend.build_legend`, else the row_id), saving to
    ``out_path``. Payload: ``{snapshot_id?: int, path?: str, out_path?: str,
    display?}``. Returns ``annotate_image``'s ``{"path", "width", "height",
    "count"}``. On a missing grabber or unavailable OpenCV a typed ``1006``
    (``UIA_ACCESS_DENIED``) is raised.
    """
    from cerebellum_cua.capture.screenshot import (  # noqa: PLC0415
        ScreenshotError,
        default_screenshot_path,
        grab_screenshot,
    )
    from cerebellum_cua.compose.annotate import (  # noqa: PLC0415
        AnnotateError,
        annotate_image,
    )
    from cerebellum_cua.legend import build_legend  # noqa: PLC0415

    snapshot_id = handlers._snapshot_id(payload)
    elements = handlers._engine.storage.get_all_elements(snapshot_id)
    codes = {r["row_id"]: r["code"] for r in build_legend(elements)["elements"]}

    src = payload.get("path")
    out_path = str(payload.get("out_path") or default_screenshot_path())
    try:
        if src is None:
            src = grab_screenshot(
                default_screenshot_path(), display=payload.get("display")
            )["path"]
        return annotate_image(str(src), elements, out_path, legend=codes)
    except (ScreenshotError, AnnotateError) as exc:
        raise UIAAccessDeniedError(
            reason="annotate_unavailable", detail=str(exc)
        ) from exc


__all__ = ["representation_ops", "read_legend", "wireframe", "annotate"]
