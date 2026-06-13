"""Unit tests for ``UiaCaptureBackend`` — Linux-testable, no ``uiautomation``.

The backend touches live COM only through ``UiaClient`` and ``sys.platform``
guards. These tests confirm:

  * importing the module succeeds on Linux,
  * ``is_available()`` is False off-Windows,
  * ``iter_tree`` raises ``CaptureNotAvailable`` (not a bare ImportError) off-Windows,
  * with the Windows guard monkeypatched and a fake client feeding duck-typed
    controls, ``iter_tree`` produces well-formed ``CapturedElement`` records with
    correct parent_key wiring, and ``walk_to_rows`` over the fake yields dense
    row ids with correct parent_row_id.

``FakeControl`` mirrors the ``MockControl`` style from ``test_predicate.py`` but
adds the cached-property accessor + pattern probing the backend reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from cerebellum_cua.capture import get_capture_backend, walk_to_rows
from cerebellum_cua.capture.base import (
    ActionNotSupported,
    CapturedElement,
    CaptureNotAvailable,
)
from cerebellum_cua.capture.uia_backend import UiaCaptureBackend, _element_to_captured
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import ControlType

# UIA PropertyId -> attribute map the FakeControl resolves for safe_get_property.
_PROP_ATTR: dict[int, str] = {
    30003: "ControlType",
    30005: "Name",
    30012: "ClassName",
    30011: "AutomationId",
    30001: "RuntimeId",
    30007: "BoundingRectangle",
    30017: "FrameworkId",
    30024: "IsEnabled",
    30009: "IsKeyboardFocusable",
    30008: "HasKeyboardFocus",
    30022: "IsOffscreen",
    30016: "IsContentElement",
}


class FakeControl:
    """Duck-typed UIA control backed by plain attributes (no COM)."""

    def __init__(
        self,
        control_type: int = int(ControlType.BUTTON),
        name: str = "OK",
        class_name: str = "",
        automation_id: str = "okBtn",
        runtime_id: list[int] | None = None,
        rect: tuple[int, int, int, int] = (10, 10, 90, 34),
        framework_id: str = "win32",
        is_enabled: bool = True,
        is_keyboard_focusable: bool = True,
        is_content_element: bool = True,
        supported_patterns: set[int] | None = None,
        value: Any = None,
        children: list[FakeControl] | None = None,
    ) -> None:
        self._props: dict[int, Any] = {
            30003: control_type,
            30005: name,
            30012: class_name,
            30011: automation_id,
            30001: runtime_id or [1, id(self) % 100000],
            30007: list(rect),
            30017: framework_id,
            30024: is_enabled,
            30009: is_keyboard_focusable,
            30008: False,
            30022: False,
            30016: is_content_element,
        }
        self._patterns = supported_patterns or set()
        self._value = value
        self._children = children or []
        self.invoked = False
        # Predicate reads these as bare attributes.
        self.ControlType = control_type
        self._rect = rect

    # --- predicate.py duck-typed surface -------------------------------------
    class _Rect:
        def __init__(self, r: tuple[int, int, int, int]) -> None:
            self._r = r

        @property
        def left(self) -> int:
            return self._r[0]

        @property
        def top(self) -> int:
            return self._r[1]

        def width(self) -> int:
            return self._r[2] - self._r[0]

        def height(self) -> int:
            return self._r[3] - self._r[1]

    @property
    def BoundingRectangle(self) -> _Rect:
        return FakeControl._Rect(self._rect)

    @property
    def Name(self) -> str:
        return self._props[30005]

    @property
    def ClassName(self) -> str:
        return self._props[30012]

    @property
    def AutomationId(self) -> str:
        return self._props[30011]

    @property
    def FrameworkId(self) -> str:
        return self._props[30017]

    @property
    def IsOffscreen(self) -> bool:
        return self._props[30022]

    @property
    def IsContentElement(self) -> bool:
        return self._props[30016]

    # --- patterns / property getters the backend uses ------------------------
    def SupportsPattern(self, pid: int) -> bool:
        return pid in self._patterns

    def GetPattern(self, pid: int) -> Any:
        if pid not in self._patterns:
            return None
        return _FakePattern(self)

    def GetCachedPropertyValue(self, prop_id: int) -> Any:
        if prop_id == 30045:  # ValueValue
            return self._value
        return self._props.get(prop_id)

    def GetChildren(self) -> list[FakeControl]:
        return list(self._children)


class _FakePattern:
    """Stand-in pattern object exposing Invoke / SetValue / Toggle."""

    def __init__(self, owner: FakeControl) -> None:
        self._owner = owner

    def Invoke(self) -> None:
        self._owner.invoked = True

    def SetValue(self, value: Any) -> None:
        self._owner._value = value

    def Toggle(self) -> None:
        self._owner.invoked = True


class FakeUiaClient:
    """Returns a preset root tree; satisfies the ``UiaClient`` surface used here."""

    def __init__(self, root: FakeControl) -> None:
        self._root = root

    def get_root(self) -> FakeControl:
        return self._root

    def from_handle(self, hwnd: int) -> FakeControl:
        return self._root

    def from_pid(self, pid: int) -> FakeControl:
        return self._root


_INVOKE_PID = 10000  # InvokePattern PatternId (matches uia._predicate_rules).


def _sample_tree() -> FakeControl:
    """A 3-node tree: window -> button, that all pass should_include."""
    button = FakeControl(
        control_type=int(ControlType.BUTTON),
        name="Save",
        automation_id="saveBtn",
        supported_patterns={_INVOKE_PID},
    )
    return FakeControl(
        control_type=int(ControlType.WINDOW),
        name="App",
        automation_id="",
        class_name="AppFrame",
        children=[button],
    )


# --- Linux-host contract -----------------------------------------------------


def test_import_and_factory_succeed_on_linux() -> None:
    backend = get_capture_backend("uia")
    assert isinstance(backend, UiaCaptureBackend)
    assert backend.name == "uia"


def test_is_available_false_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cerebellum_cua.capture.uia_backend.sys.platform", "linux")
    assert UiaCaptureBackend().is_available() is False


def test_iter_tree_raises_capture_not_available_off_windows() -> None:
    backend = UiaCaptureBackend(client=FakeUiaClient(_sample_tree()))
    with pytest.raises(CaptureNotAvailable):
        list(backend.iter_tree({}, MatrixConfig()))


def test_invoke_raises_capture_not_available_off_windows() -> None:
    backend = UiaCaptureBackend()
    el = CapturedElement(control_type=int(ControlType.BUTTON))
    with pytest.raises(CaptureNotAvailable):
        backend.invoke(el, "invoke")


# --- iter_tree with Windows guard monkeypatched ------------------------------


def _force_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cerebellum_cua.capture.uia_backend._on_windows", lambda: True
    )


def test_iter_tree_emits_captured_elements(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_windows(monkeypatch)
    backend = UiaCaptureBackend(client=FakeUiaClient(_sample_tree()))
    nodes = list(backend.iter_tree({}, MatrixConfig()))

    assert [n[1] for n in nodes] == [0, 1]  # depths: window, then button
    window, depth0, parent0 = nodes[0]
    button, depth1, parent1 = nodes[1]

    assert isinstance(window, CapturedElement)
    assert window.control_type == int(ControlType.WINDOW)
    assert window.name == "App"
    assert window.class_name == "AppFrame"
    assert parent0 is None  # root has no parent

    assert button.control_type == int(ControlType.BUTTON)
    assert button.name == "Save"
    assert button.automation_id == "saveBtn"
    assert button.is_interactive is True  # InvokePattern supported
    assert button.patterns["invoke"]["supported"] is True
    # parent_key of the button == id() of the window's native element.
    assert parent1 == id(window.native_ref)


def test_captured_element_property_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_windows(monkeypatch)
    button = FakeControl(name="Save", supported_patterns={_INVOKE_PID})
    captured = _element_to_captured(button)
    for key in (
        "is_enabled",
        "is_keyboard_focusable",
        "has_keyboard_focus",
        "value",
        "framework_id",
        "provider_description",
        "native_window_handle",
        "process_id",
        "is_offscreen",
        "is_content_element",
    ):
        assert key in captured.properties
    assert captured.native_ref is button


def test_walk_to_rows_produces_dense_row_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_windows(monkeypatch)
    backend = UiaCaptureBackend(client=FakeUiaClient(_sample_tree()))
    rows = list(walk_to_rows(backend, {}, MatrixConfig()))

    # Two nodes -> rows 0 (window, no parent) and 1 (button, parent row 0).
    assert len(rows) == 2
    win_data, win_depth, win_parent = rows[0]
    btn_data, btn_depth, btn_parent = rows[1]
    assert (win_depth, win_parent) == (0, None)
    assert (btn_depth, btn_parent) == (1, 0)
    assert win_data["control_type"] == int(ControlType.WINDOW)
    assert btn_data["name"] == "Save"


# --- invoke dispatch (Windows-forced) ----------------------------------------


def test_invoke_fires_invoke_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_windows(monkeypatch)
    native = FakeControl(supported_patterns={_INVOKE_PID})
    el = CapturedElement(control_type=int(ControlType.BUTTON), native_ref=native)
    assert UiaCaptureBackend().invoke(el, "invoke") is True
    assert native.invoked is True


def test_invoke_unknown_action_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_windows(monkeypatch)
    native = FakeControl(supported_patterns={_INVOKE_PID})
    el = CapturedElement(control_type=int(ControlType.BUTTON), native_ref=native)
    with pytest.raises(ActionNotSupported):
        UiaCaptureBackend().invoke(el, "frobnicate")


def test_invoke_unsupported_pattern_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_windows(monkeypatch)
    native = FakeControl(supported_patterns=set())  # no InvokePattern
    el = CapturedElement(control_type=int(ControlType.BUTTON), native_ref=native)
    with pytest.raises(ActionNotSupported):
        UiaCaptureBackend().invoke(el, "invoke")
