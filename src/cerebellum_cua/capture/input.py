"""Synthetic input fallback: coordinate clicks, raw typing, key combos.

When an element has no semantic action to drive (a bare canvas, a custom-drawn
widget, a coordinate the agent computed), the only way to act is to synthesize
input events. This is *best-effort and platform-dependent* by nature — it injects
events into whatever has focus at the screen coordinate, with no accessibility
guarantee that the right thing is hit.

Strategy, display-server aware:

* Try ``Atspi.generate_mouse_event`` / ``Atspi.generate_keyboard_event`` first.
  These work under X11 (via XTEST) but are no-ops or unavailable under most
  Wayland compositors.
* On Wayland (``XDG_SESSION_TYPE=wayland``) or when the Atspi route is
  unavailable/fails, fall back to the ``ydotool`` CLI if it is on ``PATH``
  (``ydotool click`` / ``ydotool type`` / ``ydotool key``). ``ydotool`` needs its
  daemon (``ydotoold``) running and uinput permissions.
* If neither method is available, raise :class:`SyntheticInputError`.

Every import is lazy/guarded so importing this module succeeds on any host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

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

# Atspi mouse-event sync strings for a button + click/press/release.
_ATSPI_MOUSE = {
    ("left", False): "b1c", ("left", True): "b1d",
    ("right", False): "b3c", ("middle", False): "b2c",
}


class SyntheticInputError(RuntimeError):
    """Raised when no synthetic-input method is available on this host."""


class SyntheticInput:
    """Best-effort synthetic mouse/keyboard input (X11 XTEST or ydotool)."""

    def __init__(self, prefer_ydotool: bool | None = None) -> None:
        # On Wayland, XTEST-backed Atspi events do not work, so prefer ydotool.
        if prefer_ydotool is None:
            prefer_ydotool = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        self._prefer_ydotool = prefer_ydotool

    # --- public API ------------------------------------------------------
    def click(
        self, x: int, y: int, button: str = "left", double: bool = False
    ) -> bool:
        """Click at screen coordinate ``(x, y)``. Returns True on a sent event."""
        if not self._prefer_ydotool and self._atspi_click(x, y, button, double):
            return True
        return self._ydotool_click(x, y, button, double)

    def type_text(self, text: str) -> bool:
        """Type ``text`` into whatever currently has focus."""
        if not self._prefer_ydotool and self._atspi_type(text):
            return True
        return self._ydotool(["type", "--", text])

    def key(self, combo: str) -> bool:
        """Send a key combo like ``"ctrl+s"`` (modifiers joined with ``+``)."""
        if not self._prefer_ydotool and self._atspi_key(combo):
            return True
        return self._ydotool_key(combo)

    # --- Atspi (X11 / XTEST) route --------------------------------------
    @staticmethod
    def _atspi() -> Any:
        try:
            import gi  # noqa: PLC0415

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: PLC0415
        except (ImportError, ValueError):
            return None
        return Atspi

    def _atspi_click(self, x: int, y: int, button: str, double: bool) -> bool:
        atspi = self._atspi()
        gen = getattr(atspi, "generate_mouse_event", None) if atspi else None
        if gen is None:
            return False
        sync = _ATSPI_MOUSE.get((button, double)) or _ATSPI_MOUSE[("left", False)]
        try:
            gen(int(x), int(y), sync)
            return True
        except Exception:  # noqa: BLE001
            return False

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

    # --- ydotool route ---------------------------------------------------
    def _ydotool_click(self, x: int, y: int, button: str, double: bool) -> bool:
        # ydotool click codes: 0xC0=left, 0xC1=right, 0xC2=middle (down+up).
        code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
        self._ydotool(["mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))])
        repeat = ["--repeat", "2"] if double else []
        return self._ydotool(["click", *repeat, code])

    def _ydotool_key(self, combo: str) -> bool:
        parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
        codes = [_YDOTOOL_KEYCODES.get(p) for p in parts]
        if not codes or any(c is None for c in codes):
            raise SyntheticInputError(f"unmappable key combo for ydotool: {combo!r}")
        press = [f"{c}:1" for c in codes]
        release = [f"{c}:0" for c in reversed(codes)]
        return self._ydotool(["key", *press, *release])

    @staticmethod
    def _ydotool(args: list[str]) -> bool:
        if shutil.which("ydotool") is None:
            raise SyntheticInputError(
                "no synthetic-input method available: Atspi XTEST events failed or "
                "are unsupported (Wayland) and the 'ydotool' CLI is not on PATH. "
                "Install ydotool + run ydotoold, or use an X11 session."
            )
        try:
            result = subprocess.run(
                ["ydotool", *args], capture_output=True, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SyntheticInputError(f"ydotool invocation failed: {exc}") from exc
        if result.returncode != 0:
            raise SyntheticInputError(
                f"ydotool {args[0]} failed (exit {result.returncode}): "
                f"{(result.stderr or '').strip()}"
            )
        return True
