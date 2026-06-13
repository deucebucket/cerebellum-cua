"""Capture seam: pluggable, OS-specific live accessibility-tree backends.

Use ``get_capture_backend()`` to obtain the right backend for the current host
(or force one by name). Backend modules are imported lazily so that importing
``cerebellum_cua.capture`` never pulls in a Windows-only or Linux-only dependency.
"""

from __future__ import annotations

import sys

from cerebellum_cua.capture.base import (
    ActionNotSupported,
    CaptureBackend,
    CapturedElement,
    CaptureNode,
    CaptureNotAvailable,
)
from cerebellum_cua.capture.driver import capture_snapshot, walk_to_rows

__all__ = [
    "ActionNotSupported",
    "CaptureBackend",
    "CaptureNode",
    "CaptureNotAvailable",
    "CapturedElement",
    "capture_snapshot",
    "walk_to_rows",
    "get_capture_backend",
    "available_backends",
]


def get_capture_backend(kind: str = "auto") -> CaptureBackend:
    """Return a capture backend.

    ``kind``: "auto" (pick by OS), "uia" (Windows), or "atspi" (Linux).
    Raises ``CaptureNotAvailable`` if the requested/auto backend cannot run here.
    """
    if kind == "auto":
        kind = "uia" if sys.platform.startswith("win") else "atspi"

    if kind == "uia":
        from cerebellum_cua.capture.uia_backend import UiaCaptureBackend

        return UiaCaptureBackend()
    if kind == "atspi":
        from cerebellum_cua.capture.atspi import AtspiCaptureBackend

        return AtspiCaptureBackend()
    raise CaptureNotAvailable(f"unknown capture backend kind: {kind!r}")


def available_backends() -> list[str]:
    """Names of backends that report themselves runnable on this host."""
    names: list[str] = []
    for kind in ("uia", "atspi"):
        try:
            if get_capture_backend(kind).is_available():
                names.append(kind)
        except Exception:
            continue
    return names
