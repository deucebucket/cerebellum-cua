"""Unit tests for ``should_include`` — no ``uiautomation`` dependency.

``MockControl`` is a plain Python object exposing the duck-typed attributes /
methods the predicate reads (``ControlType``, ``BoundingRectangle``, ``Name``,
``SupportsPattern``, ``GetChildren``, …). Each test pins one branch of the spec's
filtering predicate so a regression in any stage is caught on Linux CI.
"""

from __future__ import annotations

from typing import Any

import pytest

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import ControlType
from cerebellum_cua.uia.predicate import should_include


class _Rect:
    """Minimal BoundingRectangle stub with the spec's method-style accessors."""

    def __init__(self, left: int, top: int, w: int, h: int) -> None:
        self.left = left
        self.top = top
        self._w = w
        self._h = h

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h


class MockControl:
    """Duck-typed UIA control wrapper backed by plain attributes (no COM)."""

    def __init__(
        self,
        control_type: int = int(ControlType.BUTTON),
        name: str = "OK",
        class_name: str = "",
        automation_id: str = "okBtn",
        rect: _Rect | None = None,
        is_offscreen: bool = False,
        is_visible: bool = True,
        is_enabled: bool = True,
        is_keyboard_focusable: bool = True,
        has_keyboard_focus: bool = False,
        framework_id: str = "win32",
        is_content_element: bool = True,
        children: list[Any] | None = None,
        supported_patterns: set[int] | None = None,
        cached_value: Any = None,
        raise_on: str | None = None,
    ) -> None:
        self.ControlType = control_type
        self.Name = name
        self.ClassName = class_name
        self.AutomationId = automation_id
        self.BoundingRectangle = rect if rect is not None else _Rect(10, 10, 80, 24)
        self.IsOffscreen = is_offscreen
        self.IsVisible = is_visible
        self.IsEnabled = is_enabled
        self.IsKeyboardFocusable = is_keyboard_focusable
        self.HasKeyboardFocus = has_keyboard_focus
        self.FrameworkId = framework_id
        self.IsContentElement = is_content_element
        self._children = children or []
        self._patterns = supported_patterns or set()
        self._cached_value = cached_value
        self._raise_on = raise_on

    def SupportsPattern(self, pattern_id: int) -> bool:
        if self._raise_on == "SupportsPattern":
            raise RuntimeError("boom")
        return pattern_id in self._patterns

    def GetChildren(self) -> list[Any]:
        if self._raise_on == "GetChildren":
            raise RuntimeError("boom")
        return list(self._children)

    def GetCachedPropertyValue(self, prop_id: int) -> Any:
        return self._cached_value


@pytest.fixture
def config() -> MatrixConfig:
    return MatrixConfig()


def test_prunes_offscreen(config: MatrixConfig) -> None:
    el = MockControl(is_offscreen=True)
    assert should_include(el, 3, None, {}, config) is False


def test_offscreen_kept_when_configured() -> None:
    cfg = MatrixConfig(include_offscreen=True)
    el = MockControl(is_offscreen=True)
    assert should_include(el, 3, None, {}, cfg) is True


def test_prunes_zero_sized(config: MatrixConfig) -> None:
    el = MockControl(rect=_Rect(0, 0, 0, 0))
    assert should_include(el, 3, None, {}, config) is False


def test_prunes_one_by_one(config: MatrixConfig) -> None:
    el = MockControl(
        control_type=int(ControlType.IMAGE),
        rect=_Rect(5, 5, 1, 1),
        name="",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
        is_content_element=False,
    )
    # 1x1 has width/height >= 1 so passes size gate, but it is an anonymous,
    # non-interactive leaf with no identity/children/value -> pruned.
    assert should_include(el, 4, None, {}, config) is False


def test_prunes_excluded_control_type(config: MatrixConfig) -> None:
    el = MockControl(control_type=int(ControlType.SCROLL_BAR))
    assert should_include(el, 2, None, {}, config) is False


def test_prunes_noise_substring_name(config: MatrixConfig) -> None:
    el = MockControl(name="Vertical Scroll Thumb")
    assert should_include(el, 3, None, {}, config) is False


def test_prunes_noise_substring_classname(config: MatrixConfig) -> None:
    el = MockControl(name="x", class_name="OverlayShadowPane", automation_id="")
    assert should_include(el, 3, None, {}, config) is False


def test_prunes_depth_over_max(config: MatrixConfig) -> None:
    el = MockControl()
    assert should_include(el, config.max_depth + 1, None, {}, config) is False


def test_window_always_included(config: MatrixConfig) -> None:
    # A Window deep in the tree with no name/children is still kept structurally.
    el = MockControl(
        control_type=int(ControlType.WINDOW),
        name="",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
    )
    assert should_include(el, 6, None, {}, config) is True


def test_shallow_depth_always_included(config: MatrixConfig) -> None:
    el = MockControl(name="", automation_id="", is_keyboard_focusable=False)
    assert should_include(el, 1, None, {}, config) is True


def test_interactive_element_kept(config: MatrixConfig) -> None:
    el = MockControl(
        control_type=int(ControlType.BUTTON),
        name="",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
        has_keyboard_focus=False,
        supported_patterns={10000},  # InvokePattern
    )
    assert should_include(el, 4, None, {}, config) is True


def test_interactive_only_prunes_non_interactive() -> None:
    cfg = MatrixConfig(interactive_only=True)
    el = MockControl(
        control_type=int(ControlType.TEXT),
        name="label",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
    )
    assert should_include(el, 3, None, {}, cfg) is False


def test_browser_depth_cap_prunes_deep_chrome(config: MatrixConfig) -> None:
    el = MockControl(framework_id="chrome", name="deep node")
    assert should_include(el, config.browser_max_depth + 1, None, {}, config) is False


def test_browser_document_anchor_kept(config: MatrixConfig) -> None:
    el = MockControl(
        control_type=int(ControlType.DOCUMENT),
        framework_id="chrome",
        name="",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
    )
    assert should_include(el, 3, None, {}, config) is True


def test_empty_anonymous_pane_pruned(config: MatrixConfig) -> None:
    el = MockControl(
        control_type=int(ControlType.PANE),
        name="",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
        children=[],
    )
    assert should_include(el, 5, None, {}, config) is False


def test_element_with_value_kept(config: MatrixConfig) -> None:
    el = MockControl(
        control_type=int(ControlType.EDIT),
        name="",
        automation_id="",
        is_keyboard_focusable=False,
        is_enabled=False,
        cached_value="typed text",
    )
    assert should_include(el, 4, None, {}, config) is True


def test_none_element_excluded(config: MatrixConfig) -> None:
    assert should_include(None, 0, None, {}, config) is False


def test_fail_open_on_exception() -> None:
    cfg = MatrixConfig(fail_open_on_predicate_error=True)

    class Exploding:
        @property
        def ControlType(self) -> int:
            raise RuntimeError("COM blew up")

    assert should_include(Exploding(), 3, None, {}, cfg) is True


def test_fail_closed_when_configured() -> None:
    cfg = MatrixConfig(fail_open_on_predicate_error=False)

    class Exploding:
        @property
        def ControlType(self) -> int:
            raise RuntimeError("COM blew up")

    assert should_include(Exploding(), 3, None, {}, cfg) is False
