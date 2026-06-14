"""Unit tests for composite / annotated views (#28) — no real cv2/display.

Three halves:

* :func:`cerebellum_cua.compose.annotate.annotation_boxes` is pure geometry/label
  logic, tested directly over hand-built Elements;
* :func:`cerebellum_cua.compose.annotate.annotate_image` is tested both with a fake
  ``cv2`` module injected (asserting the rectangle/label/save calls) and on the
  cv2-missing path (typed :class:`AnnotateError`);
* the ``annotate`` and ``wireframe`` operations are driven through a seeded engine
  via ``handle_line`` with the grabber/annotator monkeypatched.
"""

from __future__ import annotations

import builtins
import json
import sys
from typing import Any

import pytest

from cerebellum_cua.cli import CuaEngine
from cerebellum_cua.compose import annotate as ann
from cerebellum_cua.compose.annotate import (
    AnnotateError,
    annotate_image,
    annotation_boxes,
)
from cerebellum_cua.matrix import build_snapshot
from cerebellum_cua.model import BoundingRect, ControlType, Element

SECRET = "unit-test-secret"


def _el(row_id: int, *, left: int, top: int, w: int, h: int) -> Element:
    return Element(
        row_id=row_id,
        control_type=ControlType.BUTTON,
        bounding_rect=BoundingRect(left=left, top=top, width=w, height=h),
    )


# --- annotation_boxes (pure) ---------------------------------------------
def test_boxes_label_from_legend_else_row_id() -> None:
    els = [_el(0, left=1, top=2, w=10, h=20), _el(5, left=3, top=4, w=6, h=8)]
    boxes = annotation_boxes(els, legend={0: "b0"})
    assert boxes[0] == {"row_id": 0, "label": "b0", "bbox": [1, 2, 10, 20]}
    # row_id 5 has no legend code -> label falls back to the row_id as a string.
    assert boxes[1] == {"row_id": 5, "label": "5", "bbox": [3, 4, 6, 8]}


def test_boxes_skip_zero_area() -> None:
    els = [
        _el(0, left=0, top=0, w=0, h=10),  # zero width -> skipped
        _el(1, left=0, top=0, w=10, h=0),  # zero height -> skipped
        _el(2, left=1, top=1, w=5, h=5),
    ]
    boxes = annotation_boxes(els)
    assert [b["row_id"] for b in boxes] == [2]


def test_boxes_empty_legend_default() -> None:
    boxes = annotation_boxes([_el(7, left=0, top=0, w=4, h=4)])
    assert boxes[0]["label"] == "7"


# --- annotate_image with a fake cv2 --------------------------------------
class _FakeImage:
    shape = (1080, 1920, 3)


class _FakeCv2:
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 16

    def __init__(self) -> None:
        self.rectangles: list[Any] = []
        self.texts: list[Any] = []
        self.written: list[str] = []

    def imread(self, path: str) -> Any:
        return _FakeImage()

    def rectangle(self, img: Any, p1: Any, p2: Any, color: Any, thick: int) -> None:
        self.rectangles.append((p1, p2))

    def putText(self, img: Any, text: str, org: Any, *rest: Any) -> None:
        self.texts.append((text, org))

    def imwrite(self, path: str, img: Any) -> bool:
        self.written.append(path)
        return True


def test_annotate_image_draws_and_saves(monkeypatch: Any, tmp_path: Any) -> None:
    fake = _FakeCv2()
    monkeypatch.setattr(ann, "_load_cv2", lambda: fake)
    els = [_el(0, left=10, top=20, w=30, h=40), _el(1, left=5, top=5, w=2, h=2)]
    out = annotate_image(
        "src.png", els, str(tmp_path / "out.png"), legend={0: "b0", 1: "b1"}
    )
    assert out["width"] == 1920
    assert out["height"] == 1080
    assert out["count"] == 2
    assert out["path"] == str(tmp_path / "out.png")
    # Two rectangles drawn at (left,top)-(left+w,top+h).
    assert fake.rectangles[0] == ((10, 20), (40, 60))
    assert [t[0] for t in fake.texts] == ["b0", "b1"]
    assert fake.written == [str(tmp_path / "out.png")]


def test_annotate_image_labels_off(monkeypatch: Any, tmp_path: Any) -> None:
    fake = _FakeCv2()
    monkeypatch.setattr(ann, "_load_cv2", lambda: fake)
    annotate_image("src.png", [_el(0, left=0, top=0, w=4, h=4)],
                   str(tmp_path / "o.png"), labels=False)
    assert fake.texts == []


def test_annotate_image_unreadable_source(monkeypatch: Any, tmp_path: Any) -> None:
    class _NoneCv2(_FakeCv2):
        def imread(self, path: str) -> Any:
            return None

    monkeypatch.setattr(ann, "_load_cv2", lambda: _NoneCv2())
    with pytest.raises(AnnotateError):
        annotate_image("missing.png", [], str(tmp_path / "o.png"))


def test_load_cv2_missing_raises(monkeypatch: Any) -> None:
    # Force `import cv2` to fail and assert the typed error.
    monkeypatch.setitem(sys.modules, "cv2", None)
    real_import = builtins.__import__

    def _no_cv2(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "cv2":
            raise ImportError("no cv2")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_cv2)
    with pytest.raises(AnnotateError):
        ann._load_cv2()


# --- engine operations through handle_line -------------------------------
def _form_snapshot(epoch: int = 1) -> Any:
    def _d(name: str, ct: int) -> dict[str, Any]:
        return {
            "name": name,
            "control_type": ct,
            "bounding_rect": {"left": 0, "top": 0, "width": 40, "height": 20},
        }

    walked = [
        (_d("Main Window", ControlType.WINDOW), 0, None),
        (_d("Save", ControlType.BUTTON), 1, 0),
    ]
    return build_snapshot(walked, epoch=epoch)


def test_wireframe_operation() -> None:
    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        eng.register_seed(_form_snapshot())
        line = json.dumps({"msg_id": "m", "operation": "wireframe", "payload": {}})
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["error"] is None
    text = resp["payload"]["text"]
    assert isinstance(text, str)
    assert "+" in text  # boxes are drawn


def test_annotate_operation(monkeypatch: Any, tmp_path: Any) -> None:
    import cerebellum_cua.capture.screenshot as shot
    import cerebellum_cua.compose.annotate as compose_ann

    monkeypatch.setattr(
        shot, "grab_screenshot",
        lambda path, display=None: {"path": "/tmp/raw.png", "width": 100, "height": 50},
    )
    captured: dict[str, Any] = {}

    def _fake_annotate(image_path: str, elements: Any, out_path: str, **kw: Any) -> dict:
        captured["image_path"] = image_path
        captured["out_path"] = out_path
        captured["legend"] = kw.get("legend")
        return {"path": out_path, "width": 100, "height": 50, "count": len(list(elements))}

    # Patch the symbol the handler imports lazily from the source module.
    monkeypatch.setattr(compose_ann, "annotate_image", _fake_annotate)

    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        eng.register_seed(_form_snapshot())
        out_target = str(tmp_path / "out.png")
        line = json.dumps(
            {"msg_id": "m", "operation": "annotate", "payload": {"out_path": out_target}}
        )
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["error"] is None
    assert resp["payload"]["count"] == 2
    assert resp["payload"]["path"] == out_target
    assert captured["image_path"] == "/tmp/raw.png"
    assert captured["out_path"] == out_target
    assert isinstance(captured["legend"], dict)


def test_annotate_operation_grabber_missing(monkeypatch: Any) -> None:
    from cerebellum_cua.capture.screenshot import ScreenshotError

    def _boom(path: str, display: str | None = None) -> dict:
        raise ScreenshotError("no grabber")

    import cerebellum_cua.capture.screenshot as shot
    monkeypatch.setattr(shot, "grab_screenshot", _boom)

    eng = CuaEngine(db_dsn=None, secret=SECRET)
    try:
        eng.register_seed(_form_snapshot())
        line = json.dumps({"msg_id": "m", "operation": "annotate", "payload": {}})
        resp = json.loads(eng.handle_line(line))
    finally:
        eng.close()
    assert resp["payload"] is None
    assert resp["error"]["code"] == 1006
    assert resp["error"]["details"]["reason"] == "annotate_unavailable"
