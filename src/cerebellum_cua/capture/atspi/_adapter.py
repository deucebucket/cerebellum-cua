"""Adapter from a live ``Atspi.Accessible`` to the duck-typed convert surface.

The real ``gi`` bindings differ from our test fakes in two spots: ``get_extents``
needs a coord-type argument, and ``get_state_set`` returns an ``Atspi.StateSet``
(not an iterable of names). :class:`LiveAdapter` normalizes both and proxies the
rest, so all the mapping logic in ``_convert`` stays pure and bus-free.

No ``gi``/``Atspi`` import lives here: the adapter only forwards method calls to
the live object the backend hands it, so importing this module is safe anywhere.
"""

from __future__ import annotations

from typing import Any


class LiveAdapter:
    """Wrap a live ``Atspi.Accessible`` into the duck-typed surface convert reads."""

    __slots__ = ("_acc", "_coord")

    def __init__(self, accessible: Any, coord_screen: Any) -> None:
        self._acc = accessible
        self._coord = coord_screen

    @property
    def raw(self) -> Any:
        """The wrapped live accessible (convert reads native_ref identity here)."""
        return self._acc

    def get_name(self) -> str:
        return self._acc.get_name()

    def get_role_name(self) -> str:
        return self._acc.get_role_name()

    def get_attributes(self) -> dict[str, str]:
        attrs = self._acc.get_attributes()
        return dict(attrs) if attrs else {}

    def get_state_set(self) -> list[str]:
        try:
            ss = self._acc.get_state_set()
            return [s.value_nick for s in ss.get_states()]
        except Exception:  # noqa: BLE001
            return []

    def get_interfaces(self) -> list[str]:
        try:
            return list(self._acc.get_interfaces())
        except Exception:  # noqa: BLE001
            return []

    def get_extents(self) -> Any:
        return self._acc.get_extents(self._coord)

    def get_index_in_parent(self) -> int:
        return self._acc.get_index_in_parent()

    def get_parent(self) -> Any:
        return self._acc.get_parent()

    def get_text(self, start: int, end: int) -> str:
        """Read the Text-interface buffer via ``Atspi.Text.get_text``.

        Imported lazily so this module stays importable without ``gi``. Returns
        an empty string if the bindings or the interface are unavailable.
        """
        try:
            import gi  # noqa: PLC0415

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: PLC0415
        except (ImportError, ValueError):
            return ""
        return Atspi.Text.get_text(self._acc, start, end) or ""

    def get_caret_offset(self) -> int:
        """Read the Text-interface caret offset via ``Atspi.Text``."""
        try:
            import gi  # noqa: PLC0415

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: PLC0415
        except (ImportError, ValueError):
            return -1
        return int(Atspi.Text.get_caret_offset(self._acc))
