"""Live UIA client facade (the only place ``uiautomation`` is imported).

``UiaClient`` lazily imports ``uiautomation`` the first time live capture is
requested; on Linux (or any host without the optional ``[uia]`` extra) that
import is deferred until use and raises a clear, actionable ImportError — so
``import cerebellum_cua.uia`` never fails on a non-Windows dev host.

The ``uiautomation`` library reads control properties live on each access (there
is no CacheRequest object), so this facade simply exposes the module's root /
handle / pid / focus accessors plus a ``control_type_name`` mapping back to
:class:`model.ControlType` names.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.model import ControlType

# control_type int -> human-readable name, derived from model.ControlType.
_CONTROL_TYPE_NAMES: dict[int, str] = {
    int(member): member.name.title().replace("_", "") for member in ControlType
}

_IMPORT_HINT = (
    "The 'uiautomation' package is required for live UIA capture and is only "
    "installable on Windows 10/11. Install it with: pip install -e '.[uia]'. "
    "On Linux this layer is import-safe but cannot perform live capture."
)


def control_type_name(ct: int) -> str:
    """Map a raw UIA ControlType integer to its human-readable name."""
    return _CONTROL_TYPE_NAMES.get(int(ct), f"Unknown({ct})")


class UiaClient:
    """Thin, lazily-initialized wrapper over the ``uiautomation`` module."""

    def __init__(self) -> None:
        self._auto: Any | None = None

    @property
    def auto(self) -> Any:
        """Return the imported ``uiautomation`` module, importing it on demand.

        Raises:
            ImportError: with an actionable hint when the package is absent
                (e.g. on this Linux dev host).
        """
        if self._auto is None:
            try:
                import uiautomation as auto  # noqa: PLC0415 - intentional lazy import
            except ImportError as exc:  # pragma: no cover - exercised only on Linux
                raise ImportError(_IMPORT_HINT) from exc
            self._auto = auto
        return self._auto

    def get_root(self) -> Any:
        """Return the live desktop root control (a ``PaneControl``)."""
        return self.auto.GetRootControl()

    def get_focused(self) -> Any:
        """Return the control that currently has keyboard focus."""
        return self.auto.GetFocusedControl()

    def from_handle(self, hwnd: int) -> Any:
        """Return the control owning the given native window handle (HWND)."""
        return self.auto.ControlFromHandle(hwnd)

    def from_pid(self, pid: int) -> Any:
        """Return the first top-level window owned by the given process id.

        The ``uiautomation`` library has no condition-based search at the module
        level, so this walks the desktop root's direct children and returns the
        first whose ``ProcessId`` matches.
        """
        root = self.auto.GetRootControl()
        for child in root.GetChildren():
            try:
                if child.ProcessId == pid:
                    return child
            except Exception:  # noqa: BLE001 - skip controls that fault on read
                continue
        return None
