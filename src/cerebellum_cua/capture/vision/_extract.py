"""Lazy, guarded image extractors — the ONLY code path needing cv2 / tesseract.

These functions run the real perception libraries against a live image. Every
heavy import (``cv2``, ``pytesseract``) is lazy and guarded, so importing this
module never requires them; a missing library or system binary degrades to an
empty result rather than an error. The pure fusion algorithm in
:mod:`cerebellum_cua.capture.vision.detect` consumes their output, so the
algorithm itself is unit-testable with injected fakes and never touches these.
"""

from __future__ import annotations

from typing import Any


def run_ocr(image: Any) -> list[dict[str, Any]]:
    """Run Tesseract on ``image`` -> list of ``{bbox, text, conf}``; [] if absent."""
    if image is None:
        return []
    try:
        import pytesseract  # noqa: PLC0415
        from pytesseract import Output  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - missing binary or lib -> no OCR
        return []
    try:
        data = pytesseract.image_to_data(image, output_type=Output.DICT)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for i in range(len(data.get("text", []))):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = 0.0
        out.append({
            "bbox": (data["left"][i], data["top"][i],
                     data["width"][i], data["height"][i]),
            "text": text,
            "conf": max(0.0, conf) / 100.0,
        })
    return out


def run_cv(image: Any) -> list[tuple[int, int, int, int]]:
    """Find rectangle-ish contours via OpenCV -> ``(l,t,w,h)`` boxes; [] if absent."""
    if image is None:
        return []
    try:
        import cv2  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if _is_color(image) else image
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(
            edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )
    except Exception:  # noqa: BLE001
        return []
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        try:
            x, y, w, h = cv2.boundingRect(contour)
        except Exception:  # noqa: BLE001
            continue
        boxes.append((int(x), int(y), int(w), int(h)))
    return boxes


def load_image(path: str) -> Any:
    """Load a PNG into an OpenCV ndarray (lazy/guarded); ``None`` if cv2 absent."""
    try:
        import cv2  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        return cv2.imread(path)
    except Exception:  # noqa: BLE001
        return None


def _is_color(image: Any) -> bool:
    """True when the image array has a colour channel dimension."""
    shape = getattr(image, "shape", None)
    return bool(shape and len(shape) == 3)


__all__ = ["run_ocr", "run_cv", "load_image"]
