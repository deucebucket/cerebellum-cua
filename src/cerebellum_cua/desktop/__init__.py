"""Desktop window-state layer: authoritative window info from the WM/compositor.

This complements the a11y/vision capture seams. Where ``build_matrix`` walks the
*inside* of a window, this layer reads the *desktop arrangement* (which windows
exist, which is active, their geometry/state/workspace) straight from the window
manager — cheaper and more authoritative than inferring it from the a11y tree.

Everything here is guarded/lazy: importing this package never depends on a window
manager tool being installed, and :func:`list_windows` returns ``[]`` rather than
crashing on a host with no usable backend.
"""

from __future__ import annotations

from cerebellum_cua.desktop.windows import (
    WindowState,
    WindowStateError,
    available,
    list_windows,
)

__all__ = ["WindowState", "WindowStateError", "available", "list_windows"]
