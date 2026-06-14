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
  (``ydotool mousemove`` / ``ydotool click`` / ``ydotool type`` / ``ydotool
  key``). ``ydotool`` needs its daemon (``ydotoold``) running and uinput
  permissions.
* If neither method is available, raise :class:`SyntheticInputError`.

**Motion profile.** Actions are human-observable by default: the cursor *glides*
to the target along an ease-in-out path over ``move_duration`` seconds across
``steps`` increments, clicks are decomposed into move/pause/press/hold/release,
and typing is paced per character. The ``"instant"`` profile bypasses all
interpolation and sleeps (one move, immediate click/type) for headless runs and
fast tests. Coordinate input requires XTEST (X11) or ydotool (Wayland).

Every import is lazy/guarded so importing this module succeeds on any host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

from cerebellum_cua.capture._atspi_input import AtspiInputMixin
from cerebellum_cua.capture._motion import interpolate_path

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

# A sensible starting point for the first glide when no prior position is known.
_DEFAULT_ORIGIN = (960, 540)


def _aborted(abort: threading.Event | None) -> bool:
    """True if an abort event has been supplied and is set."""
    return abort is not None and abort.is_set()


class SyntheticInputError(RuntimeError):
    """Raised when no synthetic-input method is available on this host."""


class SyntheticInput(AtspiInputMixin):
    """Best-effort, human-paced synthetic mouse/keyboard input.

    Motion is tunable via the constructor:

    * ``speed`` — ``"human"`` (animated, default) or ``"instant"`` (one jump,
      no sleeps; for headless/fast paths).
    * ``move_duration`` — seconds a glide spans (human mode).
    * ``steps`` — interpolation increments per glide (human mode).
    * ``click_pause`` — pause after arriving before pressing, and the press hold.
    * ``key_delay`` — per-character delay for paced typing (seconds).

    All motion methods accept an optional ``abort`` :class:`threading.Event`;
    when it is set mid-motion the method stops immediately and raises
    :class:`~cerebellum_cua.capture.abort.AbortedByUser`.
    """

    def __init__(
        self,
        prefer_ydotool: bool | None = None,
        speed: str = "human",
        move_duration: float = 0.5,
        steps: int = 30,
        click_pause: float = 0.08,
        key_delay: float = 0.012,
    ) -> None:
        # On Wayland, XTEST-backed Atspi events do not work, so prefer ydotool.
        if prefer_ydotool is None:
            prefer_ydotool = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        self._prefer_ydotool = prefer_ydotool
        self.speed = speed
        self.move_duration = float(move_duration)
        self.steps = max(1, int(steps))
        self.click_pause = float(click_pause)
        self.key_delay = float(key_delay)
        #: Last cursor position we drove the pointer to (None until first move).
        self._last_pos: tuple[int, int] | None = None

    @property
    def instant(self) -> bool:
        """True when no interpolation/sleeps should be emitted."""
        return self.speed == "instant"

    # --- public API ------------------------------------------------------
    def move(
        self, x: int, y: int, abort: threading.Event | None = None
    ) -> bool:
        """Glide the cursor to ``(x, y)``; returns True on a sent move."""
        return self._glide(int(x), int(y), abort)

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        double: bool = False,
        abort: threading.Event | None = None,
    ) -> bool:
        """Click at ``(x, y)``: glide, pause, press, hold, release.

        In ``instant`` mode this collapses to one move + one atomic click.
        """
        self._glide(int(x), int(y), abort)
        if self.instant:
            return self._atomic_click(x, y, button, double)
        return self._natural_click(x, y, button, double, abort)

    def type_text(
        self, text: str, abort: threading.Event | None = None
    ) -> bool:
        """Type ``text`` into whatever currently has focus (paced per char)."""
        if self.instant or self.key_delay <= 0:
            if not self._prefer_ydotool and self._atspi_type(text):
                return True
            return self._ydotool(["type", "--", text])
        return self._paced_type(text, abort)

    def key(self, combo: str) -> bool:
        """Send a key combo like ``"ctrl+s"`` (modifiers joined with ``+``)."""
        if not self._prefer_ydotool and self._atspi_key(combo):
            return True
        return self._ydotool_key(combo)

    # --- motion: cursor glide -------------------------------------------
    def _glide(
        self, x: int, y: int, abort: threading.Event | None
    ) -> bool:
        """Move the pointer to ``(x, y)`` along an eased path, honoring abort."""
        from cerebellum_cua.capture.abort import AbortedByUser  # noqa: PLC0415

        target = (x, y)
        if self.instant:
            self._move_abs(x, y)
            self._last_pos = target
            return True

        start = self._last_pos if self._last_pos is not None else _DEFAULT_ORIGIN
        path = interpolate_path(start, target, self.steps)
        per_step = self.move_duration / max(1, len(path))
        for px, py in path:
            if _aborted(abort):
                raise AbortedByUser("user took over during cursor move")
            self._move_abs(px, py)
            self._last_pos = (px, py)
            if per_step > 0:
                time.sleep(per_step)
        self._last_pos = target
        return True

    def _move_abs(self, x: int, y: int) -> bool:
        """Emit one absolute pointer move via the active backend."""
        if not self._prefer_ydotool and self._atspi_move(x, y):
            return True
        return self._ydotool(
            ["mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))]
        )

    # --- clicks ----------------------------------------------------------
    def _atomic_click(self, x: int, y: int, button: str, double: bool) -> bool:
        """A single combined click (instant mode / no decomposition)."""
        if not self._prefer_ydotool and self._atspi_click(x, y, button, double):
            return True
        return self._ydotool_click(button, double)

    def _natural_click(
        self,
        x: int,
        y: int,
        button: str,
        double: bool,
        abort: threading.Event | None,
    ) -> bool:
        """Move-settle-press-hold-release, approximating a human click."""
        from cerebellum_cua.capture.abort import AbortedByUser  # noqa: PLC0415

        if _aborted(abort):
            raise AbortedByUser("user took over before click")
        time.sleep(self.click_pause)
        if not self._prefer_ydotool and self._atspi_press_release(x, y, button):
            if double:
                self._atspi_press_release(x, y, button)
            return True
        return self._ydotool_click(button, double)

    # --- paced typing ----------------------------------------------------
    def _paced_type(
        self, text: str, abort: threading.Event | None
    ) -> bool:
        """Type with a per-character delay; ydotool uses ``--key-delay`` (ms)."""
        from cerebellum_cua.capture.abort import AbortedByUser  # noqa: PLC0415

        if not self._prefer_ydotool and self._atspi():
            for ch in text:
                if _aborted(abort):
                    raise AbortedByUser("user took over during typing")
                if not self._atspi_type(ch):
                    break
                time.sleep(self.key_delay)
            else:
                return True
        if _aborted(abort):
            raise AbortedByUser("user took over during typing")
        delay_ms = str(int(self.key_delay * 1000))
        return self._ydotool(["type", "--key-delay", delay_ms, "--", text])

    # --- ydotool route ---------------------------------------------------
    def _ydotool_click(self, button: str, double: bool) -> bool:
        code = _YDOTOOL_CLICK.get(button, "0xC0")
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
