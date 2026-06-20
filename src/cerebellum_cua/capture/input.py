"""Synthetic input fallback: coordinate clicks, raw typing, key combos.

When an element has no semantic action to drive (a bare canvas, a custom-drawn
widget, a coordinate the agent computed), the only way to act is to synthesize
input events. This is *best-effort and platform-dependent* by nature — it injects
events into whatever has focus at the screen coordinate, with no accessibility
guarantee that the right thing is hit.

Strategy, display-server aware:

* A **CLI tool** is the default: ``xdotool`` on X11, ``ydotool`` on Wayland
  (argv builders live in :mod:`cerebellum_cua.capture._xdotool` /
  :mod:`._ydotool`). ``ydotool`` needs its daemon (``ydotoold``) + uinput;
  ``xdotool`` needs an X11/Xwayland display. The tool is chosen by display
  server and PATH at construction.
* ``Atspi.generate_*_event`` (XTEST) is **opt-in only** (``use_atspi_input`` /
  ``CEREBELLUM_ATSPI_INPUT=1``): it aborts the whole process at the C level when
  the a11y registry is broken (issue #54), and coordinate/raw input never needs
  the a11y tree, so it is not used by default.
* If no method is available, raise :class:`SyntheticInputError` (catchable).

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
from cerebellum_cua.capture._xdotool import XdotoolInputMixin
from cerebellum_cua.capture._ydotool import (
    SyntheticInputError,
    YdotoolInputMixin,
)

__all__ = ["SyntheticInput", "SyntheticInputError"]

# A sensible starting point for the first glide when no prior position is known.
_DEFAULT_ORIGIN = (960, 540)


def _aborted(abort: threading.Event | None) -> bool:
    """True if an abort event has been supplied and is set."""
    return abort is not None and abort.is_set()


class SyntheticInput(AtspiInputMixin, XdotoolInputMixin, YdotoolInputMixin):
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
        *,
        use_atspi_input: bool | None = None,
        speed: str = "human",
        move_duration: float = 0.5,
        steps: int = 30,
        click_pause: float = 0.08,
        key_delay: float = 0.012,
    ) -> None:
        # AT-SPI synthetic input (Atspi.generate_*_event) is OPT-IN: it aborts the
        # whole process at the C level (`dbind` abort) when the a11y registry is
        # unreachable/broken — uncatchable in Python (issue #54). Coordinate/raw
        # input doesn't need the a11y tree, so default to the CLI tools
        # (xdotool/ydotool) and only use the Atspi route when explicitly enabled.
        if use_atspi_input is None:
            if prefer_ydotool is not None:
                use_atspi_input = not prefer_ydotool  # back-compat alias
            else:
                use_atspi_input = os.environ.get("CEREBELLUM_ATSPI_INPUT", "") == "1"
        self._use_atspi_input = bool(use_atspi_input)
        # Kept for the dispatch gates below: ``not _prefer_ydotool`` == "use Atspi".
        self._prefer_ydotool = not self._use_atspi_input
        # CLI input tool: ydotool (uinput, Wayland) or xdotool (X11). When ydotool
        # is explicitly preferred, honour it; otherwise pick by display server.
        self._cli_tool = self._pick_cli_tool(force_ydotool=prefer_ydotool is True)
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
            return self._cli_type(text, 0)
        return self._paced_type(text, abort)

    def key(self, combo: str) -> bool:
        """Send a key combo like ``"ctrl+s"`` (modifiers joined with ``+``)."""
        if not self._prefer_ydotool and self._atspi_key(combo):
            return True
        return self._cli_key(combo)

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        button: str = "left",
        abort: threading.Event | None = None,
    ) -> bool:
        """Drag ``button`` from ``(x1, y1)`` to ``(x2, y2)``.

        Emits, in order: glide to the start, press-and-hold the button, glide to
        the end (the held drag path, honoring ``abort`` between steps), release.
        In ``instant`` mode the two glides collapse to single jumps. Raises
        :class:`~cerebellum_cua.capture.abort.AbortedByUser` if ``abort`` fires
        mid-motion (the button is released first so nothing is left held).
        """
        from cerebellum_cua.capture.abort import AbortedByUser  # noqa: PLC0415

        self._glide(int(x1), int(y1), abort)
        self._press(int(x1), int(y1), button)
        try:
            self._glide(int(x2), int(y2), abort)
        except AbortedByUser:
            self._release(int(x2), int(y2), button)
            raise
        return self._release(int(x2), int(y2), button)

    def scroll(self, x: int, y: int, dx: int = 0, dy: int = 0) -> bool:
        """Scroll the wheel at ``(x, y)`` by ``dx`` horizontal / ``dy`` vertical.

        Positive ``dy`` scrolls down, negative up; positive ``dx`` right, negative
        left. The pointer is positioned first, then one wheel event per non-zero axis.
        """
        self._move_abs(int(x), int(y))
        self._last_pos = (int(x), int(y))
        ok = True
        if dy:
            ok = self._wheel(0, int(dy)) and ok
        if dx:
            ok = self._wheel(int(dx), 0) and ok
        return ok

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
        return self._cli_move(x, y)

    # --- clicks ----------------------------------------------------------
    def _atomic_click(self, x: int, y: int, button: str, double: bool) -> bool:
        """A single combined click (instant mode / no decomposition)."""
        if not self._prefer_ydotool and self._atspi_click(x, y, button, double):
            return True
        return self._cli_click(button, double)

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
        return self._cli_click(button, double)

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
        return self._cli_type(text, int(self.key_delay * 1000))

    # --- press / release / wheel (shared by drag + scroll) ---------------
    def _press(self, x: int, y: int, button: str) -> bool:
        """Press and hold ``button`` at ``(x, y)`` via the active backend."""
        if not self._prefer_ydotool and self._atspi_press(x, y, button):
            return True
        return self._cli_press(button)

    def _release(self, x: int, y: int, button: str) -> bool:
        """Release a held ``button`` at ``(x, y)`` via the active backend."""
        if not self._prefer_ydotool and self._atspi_release(x, y, button):
            return True
        return self._cli_release(button)

    def _wheel(self, dx: int, dy: int) -> bool:
        """Emit one wheel event via the active backend (positive dy = down)."""
        if not self._prefer_ydotool and self._atspi_wheel(dx, dy):
            return True
        return self._cli_wheel(dx, dy)

    # --- CLI tool selection + dispatch (ydotool / xdotool) ---------------
    @staticmethod
    def _pick_cli_tool(force_ydotool: bool) -> str | None:
        """Pick the CLI input tool: ydotool (uinput/Wayland) or xdotool (X11).

        Honours an explicit ydotool preference; otherwise orders by display
        server and returns whichever is installed (``None`` if neither).
        """
        if force_ydotool and shutil.which("ydotool") is not None:
            return "ydotool"
        wayland = (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )
        order = ("ydotool", "xdotool") if wayland else ("xdotool", "ydotool")
        for tool in order:
            if shutil.which(tool) is not None:
                return tool
        return None

    def _cli_move(self, x: int, y: int) -> bool:
        return (self._xdotool_move(x, y) if self._cli_tool == "xdotool"
                else self._ydotool_move(x, y))

    def _cli_click(self, button: str, double: bool) -> bool:
        return (self._xdotool_click(button, double) if self._cli_tool == "xdotool"
                else self._ydotool_click(button, double))

    def _cli_press(self, button: str) -> bool:
        return (self._xdotool_press(button) if self._cli_tool == "xdotool"
                else self._ydotool_press(button))

    def _cli_release(self, button: str) -> bool:
        return (self._xdotool_release(button) if self._cli_tool == "xdotool"
                else self._ydotool_release(button))

    def _cli_wheel(self, dx: int, dy: int) -> bool:
        return (self._xdotool_wheel(dx, dy) if self._cli_tool == "xdotool"
                else self._ydotool_wheel(dx, dy))

    def _cli_key(self, combo: str) -> bool:
        return (self._xdotool_key(combo) if self._cli_tool == "xdotool"
                else self._ydotool_key(combo))

    def _cli_type(self, text: str, delay_ms: int) -> bool:
        if self._cli_tool == "xdotool":
            return self._xdotool_type(text, delay_ms)
        args = (["type", "--key-delay", str(delay_ms), "--", text] if delay_ms
                else ["type", "--", text])
        return self._ydotool(args)

    # --- CLI subprocess runners (argv builders live in _ydotool/_xdotool) -
    @staticmethod
    def _ydotool(args: list[str]) -> bool:
        if shutil.which("ydotool") is None:
            raise SyntheticInputError(
                "no synthetic-input method available: install 'ydotool' (+ run "
                "ydotoold) for Wayland, or 'xdotool' for X11."
            )
        return _run_input_tool("ydotool", args)

    @staticmethod
    def _xdotool(args: list[str]) -> bool:
        if shutil.which("xdotool") is None:
            raise SyntheticInputError(
                "no synthetic-input method available: install 'xdotool' for X11, "
                "or 'ydotool' (+ ydotoold) for Wayland."
            )
        return _run_input_tool("xdotool", args)


def _run_input_tool(tool: str, args: list[str]) -> bool:
    """Run ``tool`` with ``args`` as a guarded subprocess; raise on failure."""
    try:
        result = subprocess.run(
            [tool, *args], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyntheticInputError(f"{tool} invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise SyntheticInputError(
            f"{tool} {args[0]} failed (exit {result.returncode}): "
            f"{(result.stderr or '').strip()}"
        )
    return True
