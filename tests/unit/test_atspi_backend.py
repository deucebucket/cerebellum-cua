"""Unit tests for the AT-SPI capture backend — NO live a11y bus required.

``FakeAtspi`` is a plain-object accessible implementing exactly the duck-typed
surface ``_convert``/``backend._walk`` read (``get_name``, ``get_role_name``,
``get_attributes``, ``get_state_set``, ``get_interfaces``, ``get_extents``,
``get_index_in_parent``, ``get_parent``, ``get_child_count``,
``get_child_at_index``). The backend's tree-acquisition is monkeypatched to hand
back a fake root, so the whole pre-order walk is exercised on Linux CI without
ever importing ``gi`` or touching a real bus.
"""

from __future__ import annotations

from typing import Any

from cerebellum_cua.capture.atspi import AtspiCaptureBackend
from cerebellum_cua.capture.atspi._convert import convert
from cerebellum_cua.capture.atspi._predicate import atspi_should_include
from cerebellum_cua.capture.atspi.roles import (
    control_type_for,
    interactive_type_for,
)
from cerebellum_cua.capture.driver import walk_to_rows
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import BoundingRect, ControlType


class _Extents:
    def __init__(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.width, self.height = x, y, w, h


class FakeAtspi:
    """Duck-typed fake AT-SPI accessible (plain object, no bindings)."""

    def __init__(
        self,
        role: str = "push button",
        name: str = "",
        attributes: dict[str, str] | None = None,
        states: set[str] | None = None,
        interfaces: set[str] | None = None,
        extents: _Extents | None = None,
        children: list[FakeAtspi] | None = None,
        text: str | None = None,
        caret: int | None = None,
    ) -> None:
        self._role = role
        self._name = name
        self._attrs = attributes or {}
        self._states = states or set()
        self._interfaces = interfaces or set()
        self._extents = extents or _Extents(0, 0, 100, 30)
        self._children = children or []
        self._text = text
        self._caret = caret
        self._parent: FakeAtspi | None = None
        for i, c in enumerate(self._children):
            c._parent = self
            c._index = i
        self._index = 0
        self.action_calls: list[int] = []

    # --- duck-typed surface read by _convert / _walk ---
    def get_name(self) -> str:
        return self._name

    def get_role_name(self) -> str:
        return self._role

    def get_attributes(self) -> dict[str, str]:
        return dict(self._attrs)

    def get_state_set(self) -> set[str]:
        return set(self._states)

    def get_interfaces(self) -> set[str]:
        return set(self._interfaces)

    def get_extents(self, coord: Any = None) -> _Extents:
        return self._extents

    def get_index_in_parent(self) -> int:
        return self._index

    def get_parent(self) -> FakeAtspi | None:
        return self._parent

    # --- Text interface surface read by _convert.read_text_content ---
    def get_text(self, start: int, end: int) -> str:
        return self._text or ""

    def get_caret_offset(self) -> int:
        return self._caret if self._caret is not None else 0

    def get_child_count(self) -> int:
        return len(self._children)

    def get_child_at_index(self, i: int) -> FakeAtspi:
        return self._children[i]

    # --- action surface used by invoke ---
    def get_n_actions(self) -> int:
        return 1

    def get_action_name(self, i: int) -> str:
        return "click"

    def do_action(self, i: int) -> bool:
        self.action_calls.append(i)
        return True


def _cfg(**kw: Any) -> MatrixConfig:
    return MatrixConfig(**kw)


# --------------------------------------------------------------------------- #
# roles
# --------------------------------------------------------------------------- #
def test_role_mapping_canonical_cases() -> None:
    assert control_type_for("push button") == int(ControlType.BUTTON)
    assert control_type_for("check box") == int(ControlType.CHECK_BOX)
    assert control_type_for("radio button") == int(ControlType.RADIO_BUTTON)
    assert control_type_for("entry") == int(ControlType.EDIT)
    assert control_type_for("text") == int(ControlType.EDIT)
    assert control_type_for("menu item") == int(ControlType.MENU_ITEM)
    assert control_type_for("page tab") == int(ControlType.TAB_ITEM)
    assert control_type_for("page tab list") == int(ControlType.TAB)
    assert control_type_for("frame") == int(ControlType.WINDOW)
    assert control_type_for("dialog") == int(ControlType.WINDOW)
    assert control_type_for("panel") == int(ControlType.PANE)
    assert control_type_for("filler") == int(ControlType.PANE)
    assert control_type_for("table cell") == int(ControlType.DATA_ITEM)
    assert control_type_for("link") == int(ControlType.HYPERLINK)
    assert control_type_for("label") == int(ControlType.TEXT)
    assert control_type_for("document frame") == int(ControlType.DOCUMENT)


def test_role_mapping_unknown_falls_back_to_custom() -> None:
    assert control_type_for("totally bogus role") == int(ControlType.CUSTOM)
    assert control_type_for("") == int(ControlType.CUSTOM)
    assert control_type_for(None) == int(ControlType.CUSTOM)


def test_interactive_type_for() -> None:
    assert interactive_type_for("entry", {"Text", "EditableText"}) == "editable"
    assert interactive_type_for("push button", {"Action"}) == "actionable"
    assert interactive_type_for("label", set()) == ""
    assert interactive_type_for("text", {"Text"}) == "editable"


# --------------------------------------------------------------------------- #
# _convert
# --------------------------------------------------------------------------- #
def test_convert_maps_role_rect_and_identity() -> None:
    acc = FakeAtspi(
        role="push button",
        name="Save",
        attributes={"toolkit": "gtk", "class": "GtkButton", "id": "save-btn"},
        states={"enabled", "sensitive", "focusable", "showing", "visible"},
        interfaces={"Action"},
        extents=_Extents(10, 20, 80, 24),
    )
    el = convert(acc)
    assert el.control_type == int(ControlType.BUTTON)
    assert el.name == "Save"
    assert el.class_name == "GtkButton"
    assert el.automation_id == "save-btn"
    assert el.framework_id == "gtk"
    assert el.bounding_rect == BoundingRect(left=10, top=20, width=80, height=24, dpi=96)
    assert el.native_ref is acc


def test_convert_properties_and_patterns() -> None:
    acc = FakeAtspi(
        role="check box",
        name="Enable",
        attributes={"toolkit": "qt"},
        states={"enabled", "focusable", "focused", "checked", "showing"},
        interfaces={"Action"},
    )
    el = convert(acc)
    assert el.properties["is_enabled"] is True
    assert el.properties["is_focusable"] is True
    assert el.properties["is_focused"] is True
    assert el.properties["is_showing"] is True
    assert el.properties["toolkit"] == "qt"
    assert el.patterns["invoke"] is True
    assert el.patterns["toggle"] is True  # checked state present
    assert el.is_interactive is True


def test_convert_value_and_expand_patterns() -> None:
    acc = FakeAtspi(
        role="entry",
        name="",
        interfaces={"Text", "EditableText", "Value"},
        states={"expandable", "expanded", "showing"},
    )
    el = convert(acc)
    assert el.patterns["value"] is True
    assert el.patterns["range_value"] is True
    assert el.patterns["expand_collapse"] is True


def test_convert_runtime_id_from_index_chain() -> None:
    leaf = FakeAtspi(role="label", name="x")
    mid = FakeAtspi(role="panel", children=[leaf])
    FakeAtspi(role="frame", children=[mid])  # wires parent chain leaf<-mid<-frame
    el = convert(leaf)
    # frame index 0, mid index 0, leaf index 0 -> [0, 0, 0]
    assert el.runtime_id == [0, 0, 0]


def test_convert_is_content_for_text() -> None:
    assert convert(FakeAtspi(role="label", name="hi")).is_content is True
    assert convert(FakeAtspi(role="push button", name="ok")).is_content is False


# --------------------------------------------------------------------------- #
# _convert text_content (Text interface)
# --------------------------------------------------------------------------- #
def test_convert_captures_text_content_when_text_interface() -> None:
    acc = FakeAtspi(
        role="text",
        interfaces={"Text", "EditableText"},
        text="line one\nline two",
        caret=4,
    )
    el = convert(acc)
    assert el.properties["text_content"] == "line one\nline two"
    assert el.properties["caret_offset"] == 4
    assert "text_truncated" not in el.properties


def test_convert_text_content_respects_cap_and_truncation_flag() -> None:
    acc = FakeAtspi(role="text", interfaces={"Text"}, text="x" * 50)
    el = convert(acc, text_max_chars=10)
    assert el.properties["text_content"] == "x" * 10
    assert el.properties["text_truncated"] is True


def test_convert_no_text_content_without_text_interface() -> None:
    # FakeAtspi always exposes get_text, but with no Text interface the buffer
    # must never be read into properties.
    acc = FakeAtspi(role="push button", name="OK", interfaces={"Action"}, text="hidden")
    el = convert(acc)
    assert "text_content" not in el.properties
    assert "caret_offset" not in el.properties


def test_convert_empty_text_buffer_sets_no_text_content() -> None:
    acc = FakeAtspi(role="text", interfaces={"Text"}, text="")
    el = convert(acc)
    assert "text_content" not in el.properties


# --------------------------------------------------------------------------- #
# _predicate
# --------------------------------------------------------------------------- #
def test_predicate_prunes_offscreen() -> None:
    cfg = _cfg()
    rect = BoundingRect(left=-50, top=-50, width=10, height=10)  # right/bottom <= 0
    assert not atspi_should_include(
        int(ControlType.BUTTON), "Btn", "", rect, {"showing"}, depth=4, config=cfg
    )


def test_predicate_keeps_offscreen_when_configured() -> None:
    cfg = _cfg(include_offscreen=True)
    rect = BoundingRect(left=-50, top=-50, width=10, height=10)
    assert atspi_should_include(
        int(ControlType.BUTTON), "Btn", "", rect, {"showing"}, depth=4, config=cfg
    )


def test_predicate_prunes_noise_substring() -> None:
    cfg = _cfg()
    rect = BoundingRect(left=0, top=0, width=20, height=200)
    assert not atspi_should_include(
        int(ControlType.PANE), "vertical scroll", "", rect, {"showing"},
        depth=3, config=cfg,
    )


def test_predicate_prunes_excluded_control_type() -> None:
    cfg = _cfg()
    rect = BoundingRect(left=0, top=0, width=20, height=200)
    # SCROLL_BAR (50014) is in the default excluded set.
    assert not atspi_should_include(
        int(ControlType.SCROLL_BAR), "sb", "", rect, {"showing"}, depth=3, config=cfg
    )


def test_predicate_prunes_tiny_anonymous_rect() -> None:
    cfg = _cfg()
    rect = BoundingRect(left=5, top=5, width=1, height=1)
    assert not atspi_should_include(
        int(ControlType.IMAGE), "", "", rect, {"showing"}, depth=4, config=cfg
    )


def test_predicate_always_keeps_window_and_shallow() -> None:
    cfg = _cfg()
    rect = BoundingRect(left=-10, top=-10, width=1, height=1)
    assert atspi_should_include(
        int(ControlType.WINDOW), "", "", rect, set(), depth=6, config=cfg
    )
    assert atspi_should_include(
        int(ControlType.PANE), "", "", rect, set(), depth=0, config=cfg
    )


def test_predicate_prunes_hidden_not_showing() -> None:
    cfg = _cfg()
    rect = BoundingRect(left=0, top=0, width=50, height=20)
    assert not atspi_should_include(
        int(ControlType.BUTTON), "Hidden", "", rect, {"enabled"}, depth=3, config=cfg
    )


def test_predicate_depth_cap() -> None:
    cfg = _cfg(max_depth=5)
    rect = BoundingRect(left=0, top=0, width=50, height=20)
    assert not atspi_should_include(
        int(ControlType.BUTTON), "Deep", "", rect, {"showing"}, depth=6, config=cfg
    )


# --------------------------------------------------------------------------- #
# backend.iter_tree (fake tree, no bus)
# --------------------------------------------------------------------------- #
def _make_fake_tree() -> FakeAtspi:
    showing = {"showing", "visible", "enabled"}
    btn = FakeAtspi(
        role="push button", name="OK", states=showing | {"focusable"},
        interfaces={"Action"}, extents=_Extents(10, 60, 80, 24),
    )
    label = FakeAtspi(
        role="label", name="Username", states=showing, extents=_Extents(10, 20, 80, 18),
    )
    scrollbar = FakeAtspi(  # should be pruned (excluded control type)
        role="scroll bar", name="vscroll", states=showing,
        extents=_Extents(300, 0, 16, 300),
    )
    panel = FakeAtspi(
        role="panel", name="Form", states=showing,
        extents=_Extents(0, 0, 320, 240), children=[label, btn, scrollbar],
    )
    frame = FakeAtspi(
        role="frame", name="My App", states=showing,
        extents=_Extents(0, 0, 320, 260), children=[panel],
    )
    return frame


class _StubBackend(AtspiCaptureBackend):
    """Backend with bus-touching bits stubbed for a fake-tree walk."""

    def __init__(self, root: FakeAtspi) -> None:
        self._root = root

    def _atspi(self) -> Any:  # type: ignore[override]
        class _Coord:
            SCREEN = "screen"

        class _FakeAtspiModule:
            CoordType = _Coord

        return _FakeAtspiModule()

    def _roots(self, atspi: Any, target: dict[str, Any]) -> list[Any]:  # type: ignore[override]
        return [self._root]


def test_iter_tree_yields_pre_order_with_parent_keys() -> None:
    root = _make_fake_tree()
    backend = _StubBackend(root)
    nodes = list(backend.iter_tree({}, _cfg()))

    names = [(el.name, depth) for el, depth, _ in nodes]
    # Pre-order: frame(0) -> panel(1) -> label(2), button(2). scrollbar pruned.
    assert ("My App", 0) in names
    assert ("Form", 1) in names
    assert ("Username", 2) in names
    assert ("OK", 2) in names
    assert all(n[0] != "vscroll" for n in names)

    # Root parent_key is None.
    root_node = next(n for n in nodes if n[0].name == "My App")
    assert root_node[2] is None
    # Children reference their parent's id(native_ref).
    panel_node = next(n for n in nodes if n[0].name == "Form")
    label_node = next(n for n in nodes if n[0].name == "Username")
    assert panel_node[2] == id(root_node[0].native_ref)
    assert label_node[2] == id(panel_node[0].native_ref)


def test_walk_to_rows_dense_rows_and_parent_links() -> None:
    root = _make_fake_tree()
    backend = _StubBackend(root)
    rows = list(walk_to_rows(backend, {}, _cfg()))

    # Dense 0-based: 4 kept elements (frame, panel, label, button).
    assert len(rows) == 4
    depths = [d for _, d, _ in rows]
    parents = [p for _, _, p in rows]
    # First row (frame) is the root: depth 0, no parent.
    assert depths[0] == 0
    assert parents[0] is None
    # panel parent_row == 0 (frame); label & button parent_row == 1 (panel).
    assert parents[1] == 0
    assert parents[2] == 1
    assert parents[3] == 1


class _FakeApp:
    def __init__(self, name: str, pid: int = 1) -> None:
        self._name = name
        self._pid = pid

    def get_name(self) -> str:
        return self._name

    def get_process_id(self) -> int:
        return self._pid


class _FakeDesktop:
    def __init__(self, apps: list[Any]) -> None:
        self._apps = apps

    def get_child_count(self) -> int:
        return len(self._apps)

    def get_child_at_index(self, i: int) -> Any:
        return self._apps[i]


def _fake_atspi_module(apps: list[Any]) -> Any:
    class _Mod:
        @staticmethod
        def get_desktop(_i: int) -> Any:
            return _FakeDesktop(apps)

    return _Mod()


def test_roots_records_registry_counts_when_empty() -> None:
    # The 'bus reachable but registry empty' case: 0 apps -> diagnostics say so.
    backend = AtspiCaptureBackend()
    roots = backend._roots(_fake_atspi_module([]), {})
    assert roots == []
    assert backend.last_capture_diagnostics() == {
        "registry_app_count": 0,
        "matched_root_count": 0,
    }


def test_roots_records_registry_counts_with_target_mismatch() -> None:
    backend = AtspiCaptureBackend()
    apps = [_FakeApp("Files"), _FakeApp("Editor")]
    roots = backend._roots(_fake_atspi_module(apps), {"app_name": "Nope"})
    assert roots == []
    diag = backend.last_capture_diagnostics()
    assert diag == {"registry_app_count": 2, "matched_root_count": 0}


def test_roots_records_registry_counts_when_matched() -> None:
    backend = AtspiCaptureBackend()
    apps = [_FakeApp("Files"), _FakeApp("Editor")]
    roots = backend._roots(_fake_atspi_module(apps), {"app_name": "Editor"})
    assert len(roots) == 1
    diag = backend.last_capture_diagnostics()
    assert diag == {"registry_app_count": 2, "matched_root_count": 1}


def test_last_capture_diagnostics_none_before_capture() -> None:
    assert AtspiCaptureBackend().last_capture_diagnostics() is None


def test_invoke_uses_action_interface() -> None:
    btn = FakeAtspi(role="push button", name="OK", interfaces={"Action"})
    el = convert(btn)
    backend = AtspiCaptureBackend()
    assert backend.invoke(el, "click") is True
    assert btn.action_calls == [0]


# --------------------------------------------------------------------------- #
# is_available degrades gracefully (no segfault, no live bus)
# --------------------------------------------------------------------------- #
def test_is_available_false_when_bus_unreachable(monkeypatch: Any) -> None:
    import cerebellum_cua.capture.atspi.backend as be

    # Force the bus probe to report unreachable; must return False, not crash.
    monkeypatch.setattr(be, "_probe_a11y_bus", lambda: False)
    backend = AtspiCaptureBackend()
    assert backend.is_available() is False


def test_iter_tree_raises_when_bus_unreachable(monkeypatch: Any) -> None:
    import cerebellum_cua.capture.atspi.backend as be
    from cerebellum_cua.capture.base import CaptureNotAvailable

    monkeypatch.setattr(be, "_probe_a11y_bus", lambda: False)
    backend = AtspiCaptureBackend()
    raised = False
    try:
        list(backend.iter_tree({}, _cfg()))
    except CaptureNotAvailable:
        raised = True
    assert raised


def test_import_is_safe_without_bindings() -> None:
    # Importing the package + constructing the backend must never touch gi.
    backend = AtspiCaptureBackend()
    assert backend.name == "atspi"
