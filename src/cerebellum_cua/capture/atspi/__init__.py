"""Linux AT-SPI2 capture backend package.

Importing this package is safe on ANY host: all ``gi``/``Atspi`` imports are
lazy and live inside ``AtspiCaptureBackend`` methods, never at module top. The
role map, conversion, and predicate modules are pure (no bindings) so the whole
mapping surface is unit-testable without a live a11y bus.
"""

from __future__ import annotations

from cerebellum_cua.capture.atspi.backend import AtspiCaptureBackend

__all__ = ["AtspiCaptureBackend"]
