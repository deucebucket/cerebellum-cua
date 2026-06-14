"""Unit tests for AT-SPI action execution + element re-acquisition.

These extend the duck-typed fakes from ``test_atspi_backend`` with Action,
EditableText, and Value surfaces so the backend's ``invoke`` routing and
``reacquire`` path-walk are exercised on Linux CI with no live a11y bus.
"""

from __future__ import annotations

from typing import Any

import pytest

from cerebellum_cua.capture.atspi import AtspiCaptureBackend
from cerebellum_cua.capture.atspi._convert import convert
from cerebellum_cua.capture.base import ActionNotSupported


class _ActionAccessible:
    """Fake accessible exposing Action/EditableText/Value as bound methods."""

    def __init__(
        self,
        role: str = "push button",
        name: str = "",
        action_names: list[str] | None = None,
        editable: bool = False,
        valued: bool = False,
    ) -> None:
        self._role = role
        self._name = name
        self._action_names = action_names or ["click"]
        self._editable = editable
        self._valued = valued
        self.action_calls: list[int] = []
        self.text_set: str | None = None
        self.value_set: float | None = None
        self._parent: Any = None
        self._index = 0

    # convert surface
    def get_name(self) -> str:
        return self._name

    def get_role_name(self) -> str:
        return self._role

    def get_attributes(self) -> dict[str, str]:
        return {}

    def get_state_set(self) -> set[str]:
        return set()

    def get_interfaces(self) -> set[str]:
        ifaces = {"Action"}
        if self._editable:
            ifaces |= {"Text", "EditableText"}
        if self._valued:
            ifaces |= {"Value"}
        return ifaces

    def get_extents(self, coord: Any = None) -> Any:
        class _E:
            x = y = 0
            width = height = 10

        return _E()

    def get_index_in_parent(self) -> int:
        return self._index

    def get_parent(self) -> Any:
        return self._parent

    # Action interface (gi: Atspi.Action.<method>(acc, ...))
    def get_n_actions(self) -> int:
        return len(self._action_names)

    def get_action_name(self, i: int) -> str:
        return self._action_names[i]

    def do_action(self, i: int) -> bool:
        self.action_calls.append(i)
        return True

    # EditableText interface
    def set_text_contents(self, text: str) -> bool:
        if not self._editable:
            raise AttributeError("not editable")
        self.text_set = text
        return True

    # Value interface
    def set_current_value(self, value: float) -> bool:
        if not self._valued:
            raise AttributeError("no value")
        self.value_set = value
        return True


def _live(acc: _ActionAccessible) -> Any:
    el = convert(acc)
    el.native_ref = acc
    return el


# --------------------------------------------------------------------------- #
# invoke routing
# --------------------------------------------------------------------------- #
def test_invoke_click_routes_to_action() -> None:
    acc = _ActionAccessible(action_names=["click"])
    assert AtspiCaptureBackend().invoke(_live(acc), "click") is True
    assert acc.action_calls == [0]


def test_invoke_picks_named_action() -> None:
    acc = _ActionAccessible(action_names=["focus", "activate", "menu"])
    assert AtspiCaptureBackend().invoke(_live(acc), "activate") is True
    assert acc.action_calls == [1]


def test_invoke_set_text_routes_to_editable_text() -> None:
    acc = _ActionAccessible(role="entry", editable=True)
    assert AtspiCaptureBackend().invoke(_live(acc), "set_text", value="hello") is True
    assert acc.text_set == "hello"


def test_invoke_set_value_routes_to_value() -> None:
    acc = _ActionAccessible(role="slider", valued=True)
    assert AtspiCaptureBackend().invoke(_live(acc), "set_value", value=0.5) is True
    assert acc.value_set == 0.5


def test_invoke_toggle_routes_to_action() -> None:
    acc = _ActionAccessible(action_names=["toggle"])
    assert AtspiCaptureBackend().invoke(_live(acc), "toggle") is True
    assert acc.action_calls == [0]


def test_invoke_unknown_action_raises() -> None:
    acc = _ActionAccessible()
    with pytest.raises(ActionNotSupported):
        AtspiCaptureBackend().invoke(_live(acc), "teleport")


def test_invoke_set_text_on_non_editable_raises() -> None:
    acc = _ActionAccessible(role="push button", editable=False)
    with pytest.raises(ActionNotSupported):
        AtspiCaptureBackend().invoke(_live(acc), "set_text", value="x")


def test_invoke_no_native_ref_raises() -> None:
    el = convert(_ActionAccessible())
    el.native_ref = None
    with pytest.raises(ActionNotSupported):
        AtspiCaptureBackend().invoke(el, "click")


# --------------------------------------------------------------------------- #
# reacquire: walks a child-index path on a fake desktop
# --------------------------------------------------------------------------- #
class _Node:
    """Minimal fake accessible for the reacquire tree-walk."""

    def __init__(
        self, role: str = "panel", name: str = "", children: list[Any] | None = None
    ) -> None:
        self._role = role
        self._name = name
        self._children = children or []

    def get_child_count(self) -> int:
        return len(self._children)

    def get_child_at_index(self, i: int) -> Any:
        return self._children[i]

    # convert surface (reacquire converts the found node)
    def get_name(self) -> str:
        return self._name

    def get_role_name(self) -> str:
        return self._role

    def get_attributes(self) -> dict[str, str]:
        return {}

    def get_state_set(self) -> set[str]:
        return set()

    def get_interfaces(self) -> set[str]:
        return set()

    def get_extents(self, coord: Any = None) -> Any:
        class _E:
            x = y = 0
            width = height = 10

        return _E()

    def get_index_in_parent(self) -> int:
        return 0

    def get_parent(self) -> Any:
        return None


class _FakeAtspiModule:
    def __init__(self, desktop: _Node) -> None:
        self._desktop = desktop

        class _Coord:
            SCREEN = "screen"

        self.CoordType = _Coord

    def get_desktop(self, i: int) -> _Node:
        return self._desktop


def _backend_with_desktop(desktop: _Node) -> AtspiCaptureBackend:
    backend = AtspiCaptureBackend()
    backend._atspi = lambda: _FakeAtspiModule(desktop)  # type: ignore[method-assign]
    return backend


def test_reacquire_walks_index_path() -> None:
    target = _Node(role="push button", name="OK")
    panel = _Node(role="panel", children=[_Node(role="label"), target])
    app = _Node(role="frame", children=[panel])
    desktop = _Node(role="desktop frame", children=[app])
    backend = _backend_with_desktop(desktop)

    # desktop -> app(0) -> panel(0) -> target(1)
    found = backend.reacquire({"atspi_path": [0, 0, 1], "name": "OK"})
    assert found is not None
    assert found.name == "OK"
    assert found.native_ref is target


def test_reacquire_returns_none_on_name_mismatch() -> None:
    target = _Node(role="push button", name="Cancel")
    app = _Node(role="frame", children=[target])
    desktop = _Node(role="desktop frame", children=[app])
    backend = _backend_with_desktop(desktop)

    found = backend.reacquire({"atspi_path": [0, 0], "name": "OK"})
    assert found is None


def test_reacquire_returns_none_on_out_of_range_path() -> None:
    app = _Node(role="frame", children=[])
    desktop = _Node(role="desktop frame", children=[app])
    backend = _backend_with_desktop(desktop)

    found = backend.reacquire({"atspi_path": [0, 5]})
    assert found is None


def test_reacquire_returns_none_without_path() -> None:
    backend = _backend_with_desktop(_Node())
    assert backend.reacquire({}) is None
    assert backend.reacquire({"atspi_path": []}) is None


def test_convert_stores_atspi_path_in_metadata() -> None:
    leaf = _Node(role="label", name="x")
    el = convert(leaf)
    assert el.metadata["atspi_path"] == el.runtime_id
