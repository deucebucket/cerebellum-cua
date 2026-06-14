"""AT-SPI (X11 / XTEST) synthetic-input primitives.

Split out of :mod:`cerebellum_cua.capture.input` to keep that module under the
~300-line cap. This holds the one responsibility of driving input through the
``Atspi.generate_mouse_event`` / ``generate_keyboard_event`` calls, which work
under X11 via XTEST and are no-ops/unavailable under most Wayland compositors.

:class:`AtspiInputMixin` is mixed into ``SyntheticInput``; every method returns
``False`` (rather than raising) when the GI Atspi bindings are absent, so the
caller can fall back to ``ydotool``. Each ``import gi`` is lazy/guarded so this
module imports on any host. ``self.click_pause`` is supplied by the host class.
"""

from __future__ import annotations

import time
from typing import Any

# Atspi mouse-event sync strings for a button + press/release/click.
_ATSPI_MOUSE = {
    ("left", False): "b1c", ("left", True): "b1d",
    ("right", False): "b3c", ("middle", False): "b2c",
}
_ATSPI_PRESS = {"left": "b1p", "right": "b3p", "middle": "b2p"}
_ATSPI_RELEASE = {"left": "b1r", "right": "b3r", "middle": "b2r"}


class AtspiInputMixin:
    """AT-SPI XTEST mouse/keyboard primitives for :class:`SyntheticInput`."""

    click_pause: float  # provided by the host class

    @staticmethod
    def _atspi() -> Any:
        try:
            import gi  # noqa: PLC0415

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: PLC0415
        except (ImportError, ValueError):
            return None
        return Atspi

    def _atspi_gen_mouse(self, x: int, y: int, sync: str) -> bool:
        atspi = self._atspi()
        gen = getattr(atspi, "generate_mouse_event", None) if atspi else None
        if gen is None:
            return False
        try:
            gen(int(x), int(y), sync)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _atspi_move(self, x: int, y: int) -> bool:
        return self._atspi_gen_mouse(x, y, "abs")

    def _atspi_click(self, x: int, y: int, button: str, double: bool) -> bool:
        sync = _ATSPI_MOUSE.get((button, double)) or _ATSPI_MOUSE[("left", False)]
        return self._atspi_gen_mouse(x, y, sync)

    def _atspi_press_release(self, x: int, y: int, button: str) -> bool:
        press = _ATSPI_PRESS.get(button, _ATSPI_PRESS["left"])
        release = _ATSPI_RELEASE.get(button, _ATSPI_RELEASE["left"])
        if not self._atspi_gen_mouse(x, y, press):
            return False
        time.sleep(self.click_pause)
        return self._atspi_gen_mouse(x, y, release)

    def _atspi_type(self, text: str) -> bool:
        atspi = self._atspi()
        gen = getattr(atspi, "generate_keyboard_event", None) if atspi else None
        kind = getattr(atspi, "KeySynthType", None) if atspi else None
        if gen is None or kind is None:
            return False
        try:
            gen(0, text, kind.STRING)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _atspi_key(self, combo: str) -> bool:
        atspi = self._atspi()
        gen = getattr(atspi, "generate_keyboard_event", None) if atspi else None
        kind = getattr(atspi, "KeySynthType", None) if atspi else None
        if gen is None or kind is None:
            return False
        try:
            gen(0, combo, kind.SYM)
            return True
        except Exception:  # noqa: BLE001
            return False
