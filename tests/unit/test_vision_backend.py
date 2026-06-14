"""Unit tests for the vision capture backend — no cv2/tesseract/display.

``grab_screenshot`` and ``detect_regions`` are monkeypatched so ``iter_tree`` runs
fully on CI: the fake detector returns hand-built regions and the backend's
classify/containment/filter logic is exercised end to end. Actions are routed to a
fake :class:`SyntheticInput` to assert coordinate clicks/typing.
"""

from __future__ import annotations

from typing import Any

import pytest

from cerebellum_cua.capture import get_capture_backend
from cerebellum_cua.capture.base import ActionNotSupported, CapturedElement
from cerebellum_cua.capture.driver import walk_to_rows
from cerebellum_cua.capture.vision import VisionCaptureBackend
from cerebellum_cua.capture.vision.detect import DetectedRegion
from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.model import BoundingRect, ControlType


class _FakeInput:
    """Records coordinate clicks / typed text instead of moving a real cursor."""

    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []

    def click(self, x: int, y: int, **_kw: Any) -> bool:
        self.clicks.append((int(x), int(y)))
        return True

    def type_text(self, text: str, **_kw: Any) -> bool:
        self.typed.append(text)
        return True


def _fake_regions() -> list[DetectedRegion]:
    # Outer window contains a button and a label.
    return [
        DetectedRegion(bbox=(0, 0, 800, 600), text="", kind="box", confidence=0.5),
        DetectedRegion(bbox=(50, 50, 120, 36), text="Save", kind="labeled_box",
                       confidence=0.9),
        DetectedRegion(bbox=(50, 200, 300, 20), text="Status: idle", kind="text",
                       confidence=0.8),
    ]


@pytest.fixture
def patched_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the screenshot + detector the backend imports lazily."""
    import cerebellum_cua.capture.screenshot as shot
    import cerebellum_cua.capture.vision.detect as detect

    monkeypatch.setattr(
        shot, "grab_screenshot",
        lambda path, display=None: {"path": path, "width": 800, "height": 600},
    )
    monkeypatch.setattr(shot, "default_screenshot_path", lambda: "/tmp/fake.png")
    monkeypatch.setattr(detect, "load_image", lambda path: object())
    monkeypatch.setattr(detect, "detect_regions", lambda image: _fake_regions())


# --- iter_tree: elements, control types, containment hierarchy ----------------
def test_iter_tree_yields_classified_elements(patched_capture: None) -> None:
    backend = VisionCaptureBackend()
    nodes = list(backend.iter_tree({}, MatrixConfig()))
    by_name = {e.name: (e, d, p) for e, d, p in nodes}
    assert "Save" in by_name and "Status: idle" in by_name
    save_elem = by_name["Save"][0]
    assert save_elem.control_type == int(ControlType.BUTTON)
    assert save_elem.bounding_rect.left == 50
    assert save_elem.metadata["source"] == "vision"
    assert save_elem.metadata["bbox"] == [50, 50, 120, 36]


def test_iter_tree_builds_containment_hierarchy(patched_capture: None) -> None:
    backend = VisionCaptureBackend()
    nodes = list(backend.iter_tree({}, MatrixConfig()))
    depths = {e.name: d for e, d, _p in nodes}
    # The outer window is the root (depth 0); the button + label are nested under it.
    assert depths[""] == 0  # the text-free outer window
    assert depths["Save"] == 1
    assert depths["Status: idle"] == 1
    # Parent yielded before children (pre-order contract).
    order = [e.name for e, _d, _p in nodes]
    assert order.index("") < order.index("Save")


def test_iter_tree_drives_into_matrix_rows(patched_capture: None) -> None:
    """The node stream resolves through the driver into parent-linked rows."""
    backend = VisionCaptureBackend()
    rows = list(walk_to_rows(backend, {}, MatrixConfig()))
    # Three included elements -> three rows; at least one has a parent.
    assert len(rows) == 3
    parents = [parent for _data, _depth, parent in rows]
    assert any(p is not None for p in parents)


# --- is_available: clean False when deps missing ------------------------------
def test_is_available_false_without_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = VisionCaptureBackend()
    monkeypatch.setattr(VisionCaptureBackend, "_has_grabber", staticmethod(lambda: False))
    assert backend.is_available() is False  # no crash even without cv2/tesseract


def test_is_available_requires_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = VisionCaptureBackend()
    monkeypatch.setattr(VisionCaptureBackend, "_has_grabber", staticmethod(lambda: True))
    monkeypatch.setattr(VisionCaptureBackend, "_has_cv2", staticmethod(lambda: True))
    monkeypatch.setattr(VisionCaptureBackend, "_has_tesseract", staticmethod(lambda: False))
    assert backend.is_available() is False


def test_factory_returns_vision_backend() -> None:
    backend = get_capture_backend("vision")
    assert isinstance(backend, VisionCaptureBackend)
    assert backend.name == "vision"


# --- invoke: coordinate routing -----------------------------------------------
def _button() -> CapturedElement:
    return CapturedElement(
        control_type=int(ControlType.BUTTON),
        name="Save",
        bounding_rect=BoundingRect(left=50, top=50, width=120, height=36),
    )


def test_invoke_click_routes_to_center() -> None:
    fake = _FakeInput()
    backend = VisionCaptureBackend(synthetic_input=fake)
    assert backend.invoke(_button(), "click") is True
    assert fake.clicks == [(110, 68)]  # 50+120//2, 50+36//2


def test_invoke_set_text_clicks_then_types() -> None:
    fake = _FakeInput()
    backend = VisionCaptureBackend(synthetic_input=fake)
    assert backend.invoke(_button(), "set_text", value="hello") is True
    assert fake.clicks == [(110, 68)]
    assert fake.typed == ["hello"]


def test_invoke_type_only_types() -> None:
    fake = _FakeInput()
    backend = VisionCaptureBackend(synthetic_input=fake)
    assert backend.invoke(_button(), "type", value="abc") is True
    assert fake.typed == ["abc"]
    assert fake.clicks == []


def test_invoke_unknown_action_raises() -> None:
    backend = VisionCaptureBackend(synthetic_input=_FakeInput())
    with pytest.raises(ActionNotSupported):
        backend.invoke(_button(), "scroll")


# --- reacquire: match by bbox + text on a re-detect ---------------------------
def test_reacquire_matches_by_bbox_and_text(patched_capture: None) -> None:
    backend = VisionCaptureBackend()
    identity = {"bbox": [50, 50, 120, 36], "name": "Save"}
    found = backend.reacquire(identity)
    assert found is not None
    assert found.name == "Save"
    assert found.bounding_rect.left == 50


def test_reacquire_returns_none_on_no_match(patched_capture: None) -> None:
    backend = VisionCaptureBackend()
    identity = {"bbox": [9000, 9000, 10, 10], "name": "Nonexistent"}
    assert backend.reacquire(identity) is None


def test_reacquire_none_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from cerebellum_cua.capture.base import CaptureNotAvailable

    backend = VisionCaptureBackend()

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise CaptureNotAvailable("no grabber")

    monkeypatch.setattr(VisionCaptureBackend, "iter_tree", _raise)
    assert backend.reacquire({"bbox": [0, 0, 10, 10], "name": "x"}) is None
