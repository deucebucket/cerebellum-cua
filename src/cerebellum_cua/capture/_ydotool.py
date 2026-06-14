"""ydotool (Wayland/uinput) argv builders for synthetic input.

Split out of :mod:`cerebellum_cua.capture.input` to keep that module under the
~300-line cap. This holds the one responsibility of turning a logical action
(click/press/release/wheel/key) into the ``ydotool`` argv list, plus the keycode
and button tables describing the wire format.

:class:`YdotoolInputMixin` is mixed into ``SyntheticInput``; each builder calls
``self._ydotool(args)`` — the guarded subprocess runner the host class provides
(so its ``shutil``/``subprocess`` references stay patchable in one place). The
:class:`SyntheticInputError` raised on an unmappable combo lives here and is
re-exported by the host module. Importing this module has no side effects.
"""

from __future__ import annotations

# ydotool keycode aliases for the modifiers/keys we name in a combo string. These
# are Linux input-event keycodes (linux/input-event-codes.h), what ydotool expects.
_YDOTOOL_KEYCODES: dict[str, int] = {
    "ctrl": 29, "control": 29, "leftctrl": 29,
    "shift": 42, "leftshift": 42,
    "alt": 56, "leftalt": 56,
    "meta": 125, "super": 125, "win": 125, "cmd": 125,
    "enter": 28, "return": 28,
    "tab": 15, "esc": 1, "escape": 1, "space": 57,
    "backspace": 14, "delete": 111, "del": 111,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
}

# ydotool click codes: down+up combined.
_YDOTOOL_CLICK = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}
# ydotool press-only / release-only codes (high bit set = down, clear = up).
_YDOTOOL_DOWN = {"left": "0x40", "right": "0x41", "middle": "0x42"}
_YDOTOOL_UP = {"left": "0x00", "right": "0x01", "middle": "0x02"}


class SyntheticInputError(RuntimeError):
    """Raised when no synthetic-input method is available on this host."""


class YdotoolInputMixin:
    """ydotool CLI mouse/keyboard primitives for :class:`SyntheticInput`."""

    def _ydotool(self, args: list[str]) -> bool:
        """Guarded ``ydotool`` subprocess runner; provided by the host class."""
        raise NotImplementedError  # pragma: no cover - overridden by SyntheticInput

    def _ydotool_move(self, x: int, y: int) -> bool:
        return self._ydotool(
            ["mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))]
        )

    def _ydotool_click(self, button: str, double: bool) -> bool:
        code = _YDOTOOL_CLICK.get(button, "0xC0")
        repeat = ["--repeat", "2"] if double else []
        return self._ydotool(["click", *repeat, code])

    def _ydotool_press(self, button: str) -> bool:
        return self._ydotool(["click", _YDOTOOL_DOWN.get(button, _YDOTOOL_DOWN["left"])])

    def _ydotool_release(self, button: str) -> bool:
        return self._ydotool(["click", _YDOTOOL_UP.get(button, _YDOTOOL_UP["left"])])

    def _ydotool_wheel(self, dx: int, dy: int) -> bool:
        return self._ydotool(["mousemove", "--wheel", "-x", str(int(dx)), "-y", str(int(dy))])

    def _ydotool_key(self, combo: str) -> bool:
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        codes = [_YDOTOOL_KEYCODES.get(p) for p in parts]
        if not codes or any(c is None for c in codes):
            raise SyntheticInputError(f"unmappable key combo for ydotool: {combo!r}")
        press = [f"{c}:1" for c in codes]
        release = [f"{c}:0" for c in reversed(codes)]
        return self._ydotool(["key", *press, *release])
