"""Vision capture backend — structured elements from a screenshot.

The same :class:`~cerebellum_cua.capture.base.CaptureBackend` contract the a11y
backends honour, but the element stream is derived from a SCREENSHOT instead of an
accessibility tree. This lets the tool drive apps that expose no accessibility
(games, canvas UIs, custom-drawn widgets). Token efficiency is preserved because
the backend emits STRUCTURED elements (coords + OCR text + a type guess), never
raw pixels.

Pipeline: ``grab_screenshot`` -> load image -> :func:`detect_regions` ->
:func:`classify` -> build :class:`CapturedElement`s -> filter noise -> derive a
shallow hierarchy by geometric containment -> yield ``(element, depth,
parent_key)``. Vision elements carry no live a11y handle, so actions are executed
by COORDINATES through :class:`~cerebellum_cua.capture.input.SyntheticInput` at the
bbox centre.

Every heavy import (cv2, pytesseract, the grabber subprocess tools) is lazy and
guarded, so importing this module succeeds on any host with no optional deps.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from typing import Any

from cerebellum_cua.capture.base import (
    ActionNotSupported,
    CaptureBackend,
    CapturedElement,
    CaptureNode,
    CaptureNotAvailable,
)
from cerebellum_cua.capture.vision._layout import (
    center,
    identity_bbox,
    keep_element,
    match_score,
    yield_with_containment,
)
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import BoundingRect

#: IoU + text weighting threshold for accepting a re-detect match.
_REACQUIRE_MIN = 0.4


class VisionCaptureBackend(CaptureBackend):
    """Screenshot-derived structured capture backend (any OS with a grabber)."""

    name = "vision"

    def __init__(self, synthetic_input: Any = None) -> None:
        """Args: ``synthetic_input`` — injectable SyntheticInput (for tests)."""
        self._input = synthetic_input

    # --- availability ----------------------------------------------------
    def is_available(self) -> bool:
        """True iff a screenshot grabber AND OpenCV AND Tesseract are usable.

        Fully guarded: a missing grabber, missing ``cv2``/``pytesseract``, or a
        missing system ``tesseract`` binary all return ``False`` rather than raise.
        """
        return self._has_grabber() and self._has_cv2() and self._has_tesseract()

    @staticmethod
    def _has_grabber() -> bool:
        for tool in ("grim", "spectacle", "ffmpeg", "import", "scrot"):
            if shutil.which(tool) is not None:
                return True
        return False

    @staticmethod
    def _has_cv2() -> bool:
        try:
            import cv2  # noqa: F401, PLC0415
        except Exception:  # noqa: BLE001
            return False
        return True

    @staticmethod
    def _has_tesseract() -> bool:
        try:
            import pytesseract  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return False
        try:
            pytesseract.get_tesseract_version()
        except Exception:  # noqa: BLE001 - lib present, system binary missing
            return False
        return True

    # --- capture ---------------------------------------------------------
    def iter_tree(
        self, target: dict[str, Any], config: MatrixConfig
    ) -> Iterator[CaptureNode]:
        """Grab a screenshot, detect regions, and yield a containment tree.

        Parents (outer rects) are yielded before the boxes they contain so the
        driver can resolve ``parent_key`` to an already-assigned row. ``target``
        is accepted for contract parity but vision always captures the full screen
        (an optional ``screenshot_path`` key overrides the temp file location).
        """
        from cerebellum_cua.capture.screenshot import (  # noqa: PLC0415
            ScreenshotError,
            default_screenshot_path,
            grab_screenshot,
        )
        from cerebellum_cua.capture.vision.detect import (  # noqa: PLC0415
            detect_regions,
            load_image,
        )

        path = target.get("screenshot_path") or default_screenshot_path()
        try:
            grab_screenshot(path)
        except ScreenshotError as exc:
            raise CaptureNotAvailable(f"vision screenshot failed: {exc}") from exc
        image = load_image(path)
        regions = detect_regions(image)
        elements = [self._to_element(r) for r in regions]
        elements = [e for e in elements if keep_element(e)]
        yield from yield_with_containment(elements)

    @staticmethod
    def _to_element(region: Any) -> CapturedElement:
        """Convert a DetectedRegion into a CapturedElement (no native ref)."""
        from cerebellum_cua.capture.vision._classify import classify  # noqa: PLC0415

        left, top, w, h = region.bbox
        ct = classify(region.bbox, region.text, {"kind": region.kind})
        return CapturedElement(
            control_type=ct,
            name=region.text,
            bounding_rect=BoundingRect(left=int(left), top=int(top),
                                       width=int(w), height=int(h), dpi=96),
            is_interactive=bool(region.text),
            metadata={
                "source": "vision",
                "confidence": float(region.confidence),
                "bbox": [int(left), int(top), int(w), int(h)],
                "kind": region.kind,
            },
            native_ref=None,
        )

    # --- reacquire -------------------------------------------------------
    def reacquire(self, identity: dict[str, Any]) -> CapturedElement | None:
        """Re-screenshot, re-detect, and return the best region matching ``identity``.

        ``identity`` carries the stored ``bbox`` (``[l, t, w, h]``) and ``name``.
        The freshly detected region maximising ``IoU + text-match`` wins, provided
        it clears a minimum geometric overlap. Returns ``None`` on any failure or
        when nothing matches well enough — never raises.
        """
        target_bbox = identity_bbox(identity)
        target_name = str(identity.get("name", "")).strip().lower()
        if target_bbox is None and not target_name:
            return None
        try:
            candidates = list(self.iter_tree({}, MatrixConfig()))
        except CaptureNotAvailable:
            return None
        best: CapturedElement | None = None
        best_score = 0.0
        for element, _depth, _parent in candidates:
            score = match_score(element, target_bbox, target_name)
            if score > best_score:
                best, best_score = element, score
        if best is None or best_score < _REACQUIRE_MIN:
            return None
        return best

    # --- actions ---------------------------------------------------------
    def invoke(
        self, element: CapturedElement, action: str = "invoke", **params: Any
    ) -> bool:
        """Act on a vision element by COORDINATES at its bbox centre.

        Routing: ``click``/``invoke``/``press`` -> click; ``set_text`` -> click
        then type the ``value``; ``type`` -> type the ``value``. Anything else
        raises :class:`ActionNotSupported`. An optional ``abort`` ``threading.Event``
        in ``params`` is honoured by the underlying input driver where supported.
        """
        name = action.lower()
        abort = params.get("abort")
        if not isinstance(abort, threading.Event):
            abort = None
        x, y = center(element.bounding_rect)
        si = self._synthetic_input()
        if name in ("click", "invoke", "press"):
            return bool(si.click(x, y, abort=abort))
        if name == "set_text":
            value = str(params.get("value", params.get("text", "")))
            si.click(x, y, abort=abort)
            return bool(si.type_text(value, abort=abort))
        if name == "type":
            value = str(params.get("value", params.get("text", "")))
            return bool(si.type_text(value, abort=abort))
        raise ActionNotSupported(
            f"vision backend does not support action {action!r}"
        )

    def _synthetic_input(self) -> Any:
        """Return the (injected or lazily constructed) SyntheticInput."""
        if self._input is None:
            from cerebellum_cua.capture.input import SyntheticInput  # noqa: PLC0415

            self._input = SyntheticInput()
        return self._input


__all__ = ["VisionCaptureBackend"]
