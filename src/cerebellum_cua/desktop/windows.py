"""Authoritative desktop window-state source from the WM/compositor.

The accessibility tree can *infer* which windows exist, but the window manager
already knows authoritatively: which top-level windows exist, which is active,
which are maximized / minimized / fullscreen / shaded, their geometry, and which
workspace they sit on. Reading that directly is cheaper and more reliable than
re-deriving it from the a11y tree, and it complements the a11y/vision layers
(use it to see the desktop layout, then drill into one window with build_matrix).

This module is a thin, display-server-aware orchestrator; the X11 specifics and
the pure stdout parsers live in :mod:`cerebellum_cua.desktop._x11`. Everything is
guarded — importing this module never pulls in a WM tool, and the public
functions return ``[]`` / ``None`` rather than crash on a host that lacks one.

* **X11** (``DISPLAY`` set / ``XDG_SESSION_TYPE`` != ``wayland``): prefer
  ``wmctrl -l -G -p`` merged with ``xprop`` for state/active; fall back to
  ``xdotool`` when ``wmctrl`` is absent.
* **kwin** (Wayland + KDE): KWin exposes no stable window-list method over plain
  D-Bus across versions — a full list needs a loaded KWin script. This backend is
  a documented no-op that returns ``[]`` rather than fabricate data.
* **wlroots**: ``wlr-foreign-toplevel-management`` needs a Wayland client lib (no
  CLI), so this is note-only and returns ``[]``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from cerebellum_cua.desktop._x11 import (
    list_x11,
    parse_active_window,
    parse_net_wm_state,
    parse_wmctrl,
    parse_xdotool_geometry,
    x11_tool,
)
from cerebellum_cua.model import BoundingRect


class WindowStateError(RuntimeError):
    """Raised when a requested window backend is unusable or a query fails hard."""


@dataclass(slots=True)
class WindowState:
    """One top-level window as reported by the WM/compositor.

    ``state`` holds a subset of ``maximized`` / ``minimized`` / ``fullscreen`` /
    ``shaded``. ``workspace`` is the 0-based desktop index, or ``None`` when the
    window is sticky / on all desktops / unknown.
    """

    id: str
    title: str = ""
    app: str = ""
    pid: int | None = None
    bounds: BoundingRect = field(default_factory=BoundingRect)
    active: bool = False
    state: list[str] = field(default_factory=list)
    workspace: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "app": self.app,
            "pid": self.pid,
            "bounds": self.bounds.to_dict(),
            "active": self.active,
            "state": list(self.state),
            "workspace": self.workspace,
        }


# --- backend selection ---------------------------------------------------
def _is_wayland() -> bool:
    """True when the session is Wayland (so X11 WM tools won't work)."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _kwin_reachable() -> bool:
    """True when this looks like a KWin (KDE) Wayland session with a D-Bus tool."""
    if os.environ.get("KDE_FULL_SESSION") or os.environ.get("KDE_SESSION_VERSION"):
        return shutil.which("gdbus") is not None or shutil.which("qdbus") is not None
    return False


def available(backend: str = "auto") -> str | None:
    """Return the usable backend name (``"x11"``) or ``None``.

    ``backend`` forces a specific backend ("x11", "kwin", "wlroots") or selects
    automatically ("auto"). A backend is "usable" only when its required tools
    are on PATH; kwin/wlroots enumeration is unimplemented, so they report
    ``None`` even when the session matches (we never claim to enumerate what we
    cannot).
    """
    if backend == "x11":
        return "x11" if x11_tool() else None
    if backend in ("kwin", "wlroots"):
        return None
    # auto
    if not _is_wayland() and x11_tool():
        return "x11"
    return None


def list_windows(backend: str = "auto") -> list[WindowState]:
    """List top-level windows from the WM/compositor.

    ``backend``: ``"auto"`` (pick by display server), ``"x11"``, ``"kwin"``, or
    ``"wlroots"``. Returns ``[]`` (never raises) when no usable backend exists, so
    callers can treat "no window source" the same as "no windows". A typed
    :class:`WindowStateError` is only raised when a chosen backend is present but
    its query fails in a way the caller should know about.
    """
    chosen = backend
    if backend == "auto":
        chosen = "kwin" if (_is_wayland() and _kwin_reachable()) else "x11"
    if chosen == "x11":
        return [] if _is_wayland() else list_x11()
    if chosen == "kwin":
        return _list_kwin()
    if chosen == "wlroots":
        return _list_wlroots()
    return []


# --- kwin / wlroots backends (documented no-ops) -------------------------
def _list_kwin() -> list[WindowState]:
    """KWin (Wayland) window enumeration — currently unsupported, returns ``[]``.

    KWin exposes scripting and per-window D-Bus calls, but no stable
    ``listWindows`` method over plain D-Bus across versions. A full, honest
    enumeration requires loading a small KWin script that walks
    ``workspace.windowList()`` and emits JSON back over a service. Until that
    script ships we return ``[]`` rather than fabricate window data.

    TODO(#23): ship a KWin script (loaded via
    ``org.kde.KWin.Scripting.loadScript``) that serializes ``workspace`` windows
    and bridge its output here through ``gdbus``/``qdbus``.
    """
    return []


def _list_wlroots() -> list[WindowState]:
    """wlroots compositor enumeration — unsupported (no CLI), returns ``[]``.

    ``wlr-foreign-toplevel-management`` is a Wayland protocol with no command-line
    client; consuming it needs a Wayland client library binding, which this
    pure-subprocess module deliberately avoids. Returns ``[]``.
    """
    return []


__all__ = [
    "WindowState",
    "WindowStateError",
    "list_windows",
    "available",
    "parse_wmctrl",
    "parse_net_wm_state",
    "parse_active_window",
    "parse_xdotool_geometry",
]
