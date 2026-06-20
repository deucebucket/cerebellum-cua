"""xdotool (X11 CLI) synthetic-input argv builders.

Split out of :mod:`cerebellum_cua.capture.input` to keep that module focused. X11
coordinate/keyboard input via the ``xdotool`` CLI — a safe alternative to AT-SPI
XTEST (which can abort the process when the a11y registry is broken; see issue
#54) and to ydotool (which needs the ``ydotoold`` daemon + uinput access).

:class:`XdotoolInputMixin` is mixed into ``SyntheticInput``; each builder calls
``self._xdotool(args)`` — the guarded subprocess runner the host class provides.
Importing this module has no side effects.
"""

from __future__ import annotations

# xdotool mouse button numbers: 1=left, 2=middle, 3=right; 4/5 = wheel up/down,
# 6/7 = wheel left/right.
_XDOTOOL_BUTTON = {"left": "1", "middle": "2", "right": "3"}


class XdotoolInputMixin:
    """xdotool CLI mouse/keyboard primitives for :class:`SyntheticInput`."""

    def _xdotool(self, args: list[str]) -> bool:
        """Guarded ``xdotool`` subprocess runner; provided by the host class."""
        raise NotImplementedError  # pragma: no cover - overridden by SyntheticInput

    def _xdotool_move(self, x: int, y: int) -> bool:
        return self._xdotool(["mousemove", str(int(x)), str(int(y))])

    def _xdotool_click(self, button: str, double: bool) -> bool:
        b = _XDOTOOL_BUTTON.get(button, "1")
        args = ["click", "--repeat", "2", b] if double else ["click", b]
        return self._xdotool(args)

    def _xdotool_press(self, button: str) -> bool:
        return self._xdotool(["mousedown", _XDOTOOL_BUTTON.get(button, "1")])

    def _xdotool_release(self, button: str) -> bool:
        return self._xdotool(["mouseup", _XDOTOOL_BUTTON.get(button, "1")])

    def _xdotool_wheel(self, dx: int, dy: int) -> bool:
        ok = True
        for _ in range(abs(dy)):
            ok = self._xdotool(["click", "5" if dy > 0 else "4"]) and ok
        for _ in range(abs(dx)):
            ok = self._xdotool(["click", "7" if dx > 0 else "6"]) and ok
        return ok

    def _xdotool_type(self, text: str, delay_ms: int = 0) -> bool:
        return self._xdotool(["type", "--delay", str(int(delay_ms)), "--", text])

    def _xdotool_key(self, combo: str) -> bool:
        # xdotool keysym syntax uses '+' natively, e.g. "ctrl+s".
        return self._xdotool(["key", combo.replace(" ", "")])
