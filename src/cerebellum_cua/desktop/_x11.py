"""X11 window-state backend: wmctrl (preferred) / xdotool, enriched via xprop.

This module owns the X11 specifics so :mod:`cerebellum_cua.desktop.windows` stays a
thin display-server-aware orchestrator. The output-parsing functions
(:func:`parse_wmctrl`, :func:`parse_net_wm_state`, :func:`parse_active_window`,
:func:`parse_xdotool_geometry`) are PURE — they take captured stdout text and
return data, so they are unit-tested without a live WM. Everything is guarded:
a missing tool yields ``[]``/``None`` rather than an exception.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from cerebellum_cua.model import BoundingRect

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.desktop.windows import WindowState

#: How long any single WM query subprocess may run before it is abandoned.
_QUERY_TIMEOUT_S = 10

#: Map of ``_NET_WM_STATE`` atoms to the normalized state token we expose.
_NET_STATE_FLAGS: dict[str, str] = {
    "_NET_WM_STATE_MAXIMIZED_VERT": "maximized",
    "_NET_WM_STATE_MAXIMIZED_HORZ": "maximized",
    "_NET_WM_STATE_HIDDEN": "minimized",
    "_NET_WM_STATE_FULLSCREEN": "fullscreen",
    "_NET_WM_STATE_SHADED": "shaded",
}


def x11_tool() -> str | None:
    """Return the preferred X11 window tool on PATH (``wmctrl`` or ``xdotool``)."""
    if shutil.which("wmctrl"):
        return "wmctrl"
    if shutil.which("xdotool"):
        return "xdotool"
    return None


def list_x11() -> list[WindowState]:
    """List windows via wmctrl (preferred) or xdotool, enriched with xprop."""
    tool = x11_tool()
    if tool == "wmctrl":
        return _list_wmctrl()
    if tool == "xdotool":
        return _list_xdotool()
    return []


def _list_wmctrl() -> list[WindowState]:
    """Run ``wmctrl -l -G -p`` and enrich each window with xprop state/active."""
    stdout = _run(["wmctrl", "-l", "-G", "-p"])
    if stdout is None:
        return []
    windows = parse_wmctrl(stdout)
    active = _active_window_id()
    for window in windows:
        if active is not None and window.id == active:
            window.active = True
        state = _xprop_state(window.id)
        if state is not None:
            window.state = state
    return windows


def _active_window_id() -> str | None:
    """Read the root ``_NET_ACTIVE_WINDOW`` atom and normalize to a 0x window id."""
    stdout = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if stdout is None:
        return None
    return parse_active_window(stdout)


def _xprop_state(window_id: str) -> list[str] | None:
    """Read ``_NET_WM_STATE`` for one window and normalize to state tokens."""
    stdout = _run(["xprop", "-id", window_id, "_NET_WM_STATE"])
    if stdout is None:
        return None
    return parse_net_wm_state(stdout)


def _list_xdotool() -> list[WindowState]:
    """Fallback enumeration via xdotool (no pid/workspace, geometry per-window)."""
    from cerebellum_cua.desktop.windows import WindowState  # noqa: PLC0415 - cycle

    stdout = _run(["xdotool", "search", "--onlyvisible", "--name", ""])
    if stdout is None:
        return []
    active = _run(["xdotool", "getactivewindow"])
    active_id = active.strip() if active else None
    windows: list[WindowState] = []
    for raw in stdout.splitlines():
        wid = raw.strip()
        if not wid:
            continue
        title = (_run(["xdotool", "getwindowname", wid]) or "").strip()
        geom = _run(["xdotool", "getwindowgeometry", "--shell", wid]) or ""
        windows.append(
            WindowState(
                id=hex_id(wid),
                title=title,
                bounds=parse_xdotool_geometry(geom),
                active=(wid == active_id),
            )
        )
    return windows


def hex_id(raw: str) -> str:
    """Normalize a decimal/hex window id to a ``0x``-prefixed lowercase hex id."""
    raw = raw.strip()
    try:
        value = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return raw
    return f"0x{value:08x}"


# --- pure parsers (unit-tested without a live WM) ------------------------
def parse_wmctrl(stdout: str) -> list[WindowState]:
    """Parse ``wmctrl -l -G -p`` output into WindowState rows.

    Each line is::

        0x03600007  0 12345 100 200 800 600 host Window Title Here

    columns: window-id, desktop, pid, x, y, width, height, client-host, then the
    title (which may contain spaces). A desktop of ``-1`` (sticky/all) maps to
    ``workspace=None``. Lines that do not parse are skipped.
    """
    from cerebellum_cua.desktop.windows import WindowState  # noqa: PLC0415 - cycle

    windows: list[WindowState] = []
    for line in stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        wid, desktop, pid, x, y, w, h = parts[:7]
        title = parts[8] if len(parts) > 8 else ""
        try:
            desktop_i = int(desktop)
            pid_i = int(pid)
            rect = BoundingRect(
                left=int(x), top=int(y), width=int(w), height=int(h)
            )
        except ValueError:
            continue
        windows.append(
            WindowState(
                id=hex_id(wid),
                title=title,
                pid=pid_i or None,
                bounds=rect,
                workspace=None if desktop_i < 0 else desktop_i,
            )
        )
    return windows


def parse_net_wm_state(stdout: str) -> list[str]:
    """Parse ``xprop _NET_WM_STATE`` output into normalized state tokens.

    Example input::

        _NET_WM_STATE(ATOM) = _NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ

    Returns a deduplicated, order-preserving subset of ``maximized`` /
    ``minimized`` / ``fullscreen`` / ``shaded``. An empty / ``not found`` line
    yields ``[]``.
    """
    if "=" not in stdout:
        return []
    _, _, rhs = stdout.partition("=")
    tokens: list[str] = []
    for atom in rhs.split(","):
        mapped = _NET_STATE_FLAGS.get(atom.strip())
        if mapped and mapped not in tokens:
            tokens.append(mapped)
    return tokens


def parse_active_window(stdout: str) -> str | None:
    """Parse ``xprop -root _NET_ACTIVE_WINDOW`` into a ``0x`` window id.

    Example::

        _NET_ACTIVE_WINDOW(WINDOW): window id # 0x3600007

    Returns ``None`` when no id is present (e.g. ``0x0`` / unset).
    """
    marker = "# "
    idx = stdout.rfind(marker)
    if idx == -1:
        return None
    candidate = stdout[idx + len(marker):].split(",")[0].strip()
    normalized = hex_id(candidate)
    if normalized in ("0x00000000", "0x0"):
        return None
    return normalized


def parse_xdotool_geometry(stdout: str) -> BoundingRect:
    """Parse ``xdotool getwindowgeometry --shell`` (``X=`` / ``Y=`` / ...) output."""
    vals: dict[str, int] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        try:
            vals[key.strip()] = int(value.strip())
        except ValueError:
            continue
    return BoundingRect(
        left=vals.get("X", 0),
        top=vals.get("Y", 0),
        width=vals.get("WIDTH", 0),
        height=vals.get("HEIGHT", 0),
    )


def _run(argv: list[str]) -> str | None:
    """Run a WM query subprocess; return stdout, or ``None`` on any failure."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_QUERY_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


__all__ = [
    "x11_tool",
    "list_x11",
    "hex_id",
    "parse_wmctrl",
    "parse_net_wm_state",
    "parse_active_window",
    "parse_xdotool_geometry",
]
