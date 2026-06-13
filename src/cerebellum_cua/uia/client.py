"""Live UIA client facade (the only place ``uiautomation`` is imported).

``UiaClient`` lazily imports ``uiautomation`` the first time live capture is
requested; on Linux (or any host without the optional ``[uia]`` extra) that
import is deferred until use and raises a clear, actionable ImportError — so
``import cerebellum_cua.uia`` never fails on a non-Windows dev host.

It also defines the CacheRequest-style property prefetch list (the properties
every traversal needs cached up front, per the spec's CacheRequest note) and a
``control_type_name`` mapping back to :class:`model.ControlType` names.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.model import ControlType

# Real Microsoft UIA PropertyId constants prefetched into the cache request so a
# single COM round-trip populates everything should_include / extraction read.
CACHE_PROPERTY_IDS: tuple[int, ...] = (
    30003,  # ControlType
    30005,  # Name
    30012,  # ClassName
    30011,  # AutomationId
    30001,  # RuntimeId
    30007,  # BoundingRectangle
    30022,  # IsOffscreen
    30024,  # IsEnabled
    30009,  # IsKeyboardFocusable
    30008,  # HasKeyboardFocus
    30017,  # FrameworkId
    30045,  # ValueValue
    30086,  # ToggleToggleState
    30016,  # IsContentElement
)

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

    def build_cache_request(self) -> Any:
        """Construct a CacheRequest preloading :data:`CACHE_PROPERTY_IDS`."""
        auto = self.auto
        request = auto.CreateCacheRequest()
        for prop_id in CACHE_PROPERTY_IDS:
            request.AddProperty(prop_id)
        return request

    def get_root(self) -> Any:
        """Return the live desktop root element."""
        return self.auto.GetRootElement()

    def from_handle(self, hwnd: int) -> Any:
        """Return the control owning the given native window handle (HWND)."""
        return self.auto.ControlFromHandle(hwnd)

    def from_pid(self, pid: int) -> Any:
        """Return the first top-level window owned by the given process id."""
        auto = self.auto
        process_id_property = 30002  # ProcessId PropertyId
        tree_scope_children = 2
        return auto.GetRootElement().FindFirst(
            tree_scope_children,
            auto.CreatePropertyCondition(process_id_property, pid),
        )
