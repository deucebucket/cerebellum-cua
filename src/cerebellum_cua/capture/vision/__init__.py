"""Vision capture backend package — structured elements from a screenshot.

Importing this package is safe on ANY host: the heavy detection libraries
(``cv2``, ``pytesseract``, ``numpy``) and the screenshot grabber are imported
lazily inside :class:`VisionCaptureBackend` methods, never at module top, so
``import cerebellum_cua.capture.vision`` succeeds with no optional deps. The
detection / classification / wireframe modules are pure and unit-testable with
injected fakes.
"""

from __future__ import annotations

from cerebellum_cua.capture.vision.backend import VisionCaptureBackend

__all__ = ["VisionCaptureBackend"]
