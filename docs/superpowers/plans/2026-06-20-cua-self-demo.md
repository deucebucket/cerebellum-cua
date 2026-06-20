# CUA Self-Recorded Demo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship focused (region/element) screenshots, token-annotated captioned tutorials, and a zero-flake gedit navigation flow, then produce a self-recorded demo as editable clips with a fair three-way token comparison.

**Architecture:** Four codeable subsystems (focused screenshots on the existing `screenshot` op; an image-token helper; tutorial runner/caption token instrumentation; a pure clip-cutting planner), then an empirical phase that hardens the navigation in the rig and records/cuts the artifact. Everything rides the existing capture/tutorial seams — no parallel systems.

**Tech Stack:** Python 3.10+, pytest, ruff, mypy. ffmpeg/grim/scrot/import (grabbers, in the rig). podman rig (`scripts/run-vm.sh`). No new runtime deps.

## Global Constraints

- Python ≥ 3.10; keep `from __future__ import annotations`. (verbatim from repo)
- No new runtime dependencies; heavy/optional imports stay lazy + guarded.
- TDD: failing test first, watch it fail, minimal code, watch it pass, commit.
- `ruff check src tests` clean; `mypy src` introduces no NEW errors (the pre-existing `uia/patterns.py:90` error is out of scope).
- Backward compatible: `screenshot` with no `region`/`row_id` behaves exactly as today.
- No fabricated numbers. Token figures are real estimates from `estimate_tokens` / the documented image-token formula, always labeled "estimate".
- Each clip begins from a settled state; a clip is only kept if its action `verified`.
- Commit after every green step. Branch: `feat/cua-self-demo`.

---

## Phase A — Focused (region/element) screenshots

### Task A1: Region cropping in `grab_screenshot`

**Files:**
- Modify: `src/cerebellum_cua/capture/screenshot.py`
- Test: `tests/unit/test_screenshot.py`

**Interfaces:**
- Produces: `grab_screenshot(path: str, display: str | None = None, region: tuple[int,int,int,int] | None = None) -> dict` — `region` is `(x, y, w, h)`; when set, the grab is cropped to it via per-grabber geometry. Result gains `"region": [x,y,w,h] | None` and `"region_applied": bool`.
- Produces: `_x11_grabbers(path, display, region)` and `_wayland_grabbers(path, region)` build geometry-aware argv.

- [ ] **Step 1: Write failing tests** (create `tests/unit/test_screenshot.py` if absent; else append)

```python
from cerebellum_cua.capture import screenshot as shot


def test_x11_grabbers_full_screen_has_no_geometry():
    cands = shot._x11_grabbers("/tmp/x.png", ":9", None)
    ff = dict(cands)["ffmpeg"]
    assert "-video_size" not in ff
    assert "-i" in ff and ":9" in ff


def test_x11_ffmpeg_region_sets_video_size_and_offset():
    cands = shot._x11_grabbers("/tmp/x.png", ":9", (10, 20, 100, 40))
    ff = dict(cands)["ffmpeg"]
    assert "-video_size" in ff
    assert "100x40" in ff
    # offset is appended to the display input: ":9+10,20"
    assert any(a == ":9+10,20" for a in ff)


def test_x11_scrot_region_uses_dash_a():
    cands = shot._x11_grabbers("/tmp/x.png", ":9", (10, 20, 100, 40))
    sc = dict(cands)["scrot"]
    assert "-a" in sc and "10,20,100,40" in sc


def test_x11_import_region_uses_crop():
    cands = shot._x11_grabbers("/tmp/x.png", ":9", (10, 20, 100, 40))
    im = dict(cands)["import"]
    assert "-crop" in im and "100x40+10+20" in im


def test_wayland_grim_region_uses_geometry():
    cands = shot._wayland_grabbers("/tmp/x.png", (10, 20, 100, 40))
    gr = dict(cands)["grim"]
    assert "-g" in gr and "10,20 100x40" in gr
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_screenshot.py -q`
Expected: FAIL (`_x11_grabbers` takes 2 args, not 3 / region kwarg unknown).

- [ ] **Step 3: Implement geometry-aware grabbers**

In `src/cerebellum_cua/capture/screenshot.py`, change the signature and grabber builders:

```python
def grab_screenshot(
    path: str,
    display: str | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> dict:
    """Capture the screen (or a ``region`` of it) to ``path`` (PNG).

    ``region`` is ``(x, y, w, h)`` in screen pixels; when given, the grab is
    cropped to it at capture time. Returns ``{"path", "width", "height",
    "region", "region_applied"}``.
    """
    region_applied = region is not None
    candidates = _candidate_grabbers(path, display, region)
    if not candidates:
        raise ScreenshotError(
            "no screenshot grabber available: install one of ffmpeg, "
            "imagemagick (import), scrot (X11), or grim / spectacle (Wayland)."
        )
    errors: list[str] = []
    for tool, argv in candidates:
        if shutil.which(tool) is None:
            continue
        # spectacle has no headless region mode -> fall back to full, flag it.
        applied = region_applied and tool != "spectacle"
        try:
            _run_grabber(argv)
        except ScreenshotError as exc:
            errors.append(f"{tool}: {exc}")
            continue
        width, height = _png_dimensions(path)
        return {
            "path": path, "width": width, "height": height,
            "region": list(region) if region else None,
            "region_applied": bool(applied),
        }
    detail = "; ".join(errors) if errors else "no candidate tool was on PATH"
    raise ScreenshotError(f"all screenshot grabbers failed ({detail}).")


def _candidate_grabbers(
    path: str, display: str | None, region: tuple[int, int, int, int] | None
) -> list[tuple[str, list[str]]]:
    if _is_wayland():
        return _wayland_grabbers(path, region)
    return _x11_grabbers(path, display, region)


def _x11_grabbers(
    path: str, display: str | None, region: tuple[int, int, int, int] | None
) -> list[tuple[str, list[str]]]:
    disp = display or os.environ.get("DISPLAY") or ":0"
    if region is not None:
        x, y, w, h = region
        ffmpeg = [
            "ffmpeg", "-y", "-f", "x11grab",
            "-video_size", f"{w}x{h}", "-i", f"{disp}+{x},{y}",
            "-frames:v", "1", path,
        ]
        imp = ["import", "-window", "root",
               "-crop", f"{w}x{h}+{x}+{y}", path]
        scrot = ["scrot", "-a", f"{x},{y},{w},{h}", "--overwrite", path]
    else:
        ffmpeg = ["ffmpeg", "-y", "-f", "x11grab", "-i", disp,
                  "-frames:v", "1", path]
        imp = ["import", "-window", "root", path]
        scrot = ["scrot", "--overwrite", path]
    return [("ffmpeg", ffmpeg), ("import", imp), ("scrot", scrot)]


def _wayland_grabbers(
    path: str, region: tuple[int, int, int, int] | None
) -> list[tuple[str, list[str]]]:
    if region is not None:
        x, y, w, h = region
        grim = ["grim", "-g", f"{x},{y} {w}x{h}", path]
    else:
        grim = ["grim", path]
    # spectacle has no reliable headless region capture: full-screen fallback.
    return [("grim", grim), ("spectacle", ["spectacle", "-b", "-n", "-o", path])]
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/unit/test_screenshot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/capture/screenshot.py tests/unit/test_screenshot.py
git commit -m "feat(screenshot): region cropping via per-grabber geometry"
```

### Task A2: `screenshot` op accepts `region` / `row_id`

**Files:**
- Modify: `src/cerebellum_cua/cli/handlers.py` (the `screenshot` method)
- Test: `tests/unit/test_engine.py`

**Interfaces:**
- Consumes: `grab_screenshot(..., region=...)` (A1), `engine.storage.get_element(snapshot_id, row_id) -> Element | None`, `Element.bounding_rect` (`.left/.top/.width/.height`).
- Produces: `screenshot` op payload accepts `region: [x,y,w,h]` OR `row_id` (+ optional `snapshot_id`); response echoes `region`/`region_applied`.

- [ ] **Step 1: Write failing test** (append to `tests/unit/test_engine.py`)

```python
def test_screenshot_row_id_crops_to_element_bbox(
    engine: CuaEngine, monkeypatch: Any
) -> None:
    import cerebellum_cua.capture.screenshot as shot

    seen: dict[str, Any] = {}

    def _fake_grab(path: str, display: Any = None, region: Any = None) -> dict:
        seen["region"] = region
        return {"path": path, "width": 40, "height": 20,
                "region": list(region) if region else None, "region_applied": True}

    monkeypatch.setattr(shot, "grab_screenshot", _fake_grab)
    sid = engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    # row 1 is the button; _elem default bbox is {left:0,top:0,width:40,height:20}
    resp = _call(engine, "screenshot", {"snapshot_id": sid, "row_id": 1})
    assert resp["error"] is None
    assert seen["region"] == (0, 0, 40, 20)
    assert resp["payload"]["region"] == [0, 0, 40, 20]
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_engine.py::test_screenshot_row_id_crops_to_element_bbox -q`
Expected: FAIL (region is `None`; row_id ignored).

- [ ] **Step 3: Implement** — replace the body of `screenshot` in `handlers.py`:

```python
    def screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Grab a screenshot (optionally cropped to a region/element) + dims.

        Scope (optional, mutually exclusive): ``region`` ``[x,y,w,h]`` crops to an
        explicit rect; ``row_id`` (+ optional ``snapshot_id``) crops to that
        element's stored ``bounding_rect``. No scope -> full screen (unchanged).
        """
        from cerebellum_cua.capture.screenshot import (  # noqa: PLC0415
            ScreenshotError,
            default_screenshot_path,
            grab_screenshot,
        )

        path = str(payload.get("path") or default_screenshot_path())
        display = payload.get("display")
        region = self._resolve_region(payload)
        try:
            return grab_screenshot(path, display=display, region=region)
        except ScreenshotError as exc:
            raise UIAAccessDeniedError(
                reason="screenshot_unavailable", detail=str(exc)
            ) from exc

    def _resolve_region(
        self, payload: dict[str, Any]
    ) -> tuple[int, int, int, int] | None:
        """Resolve an optional capture region from ``region`` or ``row_id``."""
        region = payload.get("region")
        if region is not None:
            x, y, w, h = (int(v) for v in region)
            return (x, y, w, h)
        if payload.get("row_id") is None:
            return None
        snapshot_id = self._snapshot_id(payload)
        element = self._engine.storage.get_element(
            snapshot_id, int(payload["row_id"])
        )
        if element is None:
            raise ElementNotFoundError(
                snapshot_id=snapshot_id, row_id=int(payload["row_id"])
            )
        r = element.bounding_rect
        return (int(r.left), int(r.top), int(r.width), int(r.height))
```

- [ ] **Step 4: Run, verify PASS** (and the existing screenshot test still passes)

Run: `python -m pytest tests/unit/test_engine.py -q -k screenshot`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/cli/handlers.py tests/unit/test_engine.py
git commit -m "feat(screenshot): op crops to region or element row_id bbox"
```

### Task A3: Expose `region`/`row_id` on the MCP `screenshot` tool

**Files:**
- Modify: `src/cerebellum_cua/mcp/_tools.py` (`screenshot_tool`)
- Test: `tests/unit/test_mcp_server.py`

**Interfaces:**
- Consumes: A2's `screenshot` op payload keys.
- Produces: MCP `screenshot(path=None, display=None, region=None, row_id=None, snapshot_id=None)`.

- [ ] **Step 1: Write failing test** (append to `tests/unit/test_mcp_server.py`)

```python
def test_screenshot_tool_forwards_row_id_region(server: Any, monkeypatch: Any) -> None:
    import cerebellum_cua.capture.screenshot as shot

    seen: dict[str, Any] = {}

    def _fake_grab(path: str, display: Any = None, region: Any = None) -> dict:
        seen["region"] = region
        return {"path": path, "width": 40, "height": 20,
                "region": list(region) if region else None, "region_applied": True}

    monkeypatch.setattr(shot, "grab_screenshot", _fake_grab)
    sid = server.cua_engine.register_seed(_window_with_button(epoch=1))["snapshot_id"]
    screenshot = _tools_by_name(server)["screenshot"].fn
    result = screenshot(row_id=1, snapshot_id=sid)
    assert seen["region"] == (0, 0, 40, 20)
    assert result["region"] == [0, 0, 40, 20]
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_mcp_server.py::test_screenshot_tool_forwards_row_id_region -q`
Expected: FAIL (`screenshot()` got an unexpected keyword `row_id`).

- [ ] **Step 3: Implement** — replace `screenshot_tool` inner fn in `_tools.py`:

```python
    def screenshot(
        path: str | None = None,
        display: str | None = None,
        region: list[int] | None = None,
        row_id: int | None = None,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Grab ONE screenshot and return its path + dimensions. The opt-in visual
        escape hatch (custom-drawn/canvas UIs, or to verify state) — NOT part of
        build_matrix, no analysis. Scope it cheaply: region=[x,y,w,h] or
        row_id (+snapshot_id) crops to just that element's box (far fewer image
        tokens than a full frame); no scope = full screen. For a structured element
        list from pixels use build_matrix(capture_backend="vision").
        """
        payload: dict[str, Any] = {}
        for key, val in (
            ("path", path), ("display", display), ("region", region),
            ("row_id", row_id), ("snapshot_id", snapshot_id),
        ):
            if val is not None:
                payload[key] = val
        return _dispatch(engine, "screenshot", payload)
```

- [ ] **Step 4: Run, verify PASS** (+ description-quality test still green)

Run: `python -m pytest tests/unit/test_mcp_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/mcp/_tools.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): screenshot tool exposes region/row_id focused capture"
```

---

## Phase B — Image-token helper + tutorial instrumentation

### Task B1: `tutorial/tokens.py` image-token estimate

**Files:**
- Create: `src/cerebellum_cua/tutorial/tokens.py`
- Test: `tests/unit/test_tutorial_tokens.py`

**Interfaces:**
- Produces: `image_tokens(width: int, height: int) -> int` — `round(width*height/750)`, ≥1 for any non-empty image, 0 for zero-area. Documented Anthropic image-token estimate.
- Produces: `bbox_image_tokens(bbox: tuple[int,int,int,int]) -> int` — image tokens for an element crop `(x,y,w,h)` (uses w,h).

- [ ] **Step 1: Write failing tests**

```python
from cerebellum_cua.tutorial.tokens import bbox_image_tokens, image_tokens


def test_full_frame_estimate_matches_formula():
    # 1280x800 -> 1024000/750 -> ~1365
    assert image_tokens(1280, 800) == 1365


def test_focused_crop_is_much_smaller():
    assert bbox_image_tokens((0, 0, 120, 36)) == round(120 * 36 / 750)
    assert bbox_image_tokens((0, 0, 120, 36)) < image_tokens(1280, 800)


def test_zero_area_is_zero_and_tiny_is_at_least_one():
    assert image_tokens(0, 100) == 0
    assert image_tokens(1, 1) == 1
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_tutorial_tokens.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Image-token estimates for the demo's a11y-vs-pixels comparison.

Pure, dependency-free. Uses Anthropic's documented image-token heuristic,
``tokens ~= (width * height) / 750`` (https://docs.anthropic.com/), as a coarse,
clearly-labeled ESTIMATE of what a vision model spends to ingest an image at a
given resolution. Pairs with ``gateway.budget.estimate_tokens`` (the JSON/text
estimator) so a tutorial can price both the structured a11y response and an
equivalent screenshot from real dimensions.
"""

from __future__ import annotations

#: Pixels per image token (Anthropic's documented heuristic).
_PX_PER_TOKEN = 750


def image_tokens(width: int, height: int) -> int:
    """Estimated image tokens for a ``width`` x ``height`` frame (>=1 if non-empty)."""
    px = max(0, int(width)) * max(0, int(height))
    if px <= 0:
        return 0
    return max(1, round(px / _PX_PER_TOKEN))


def bbox_image_tokens(bbox: tuple[int, int, int, int]) -> int:
    """Estimated image tokens for an element crop ``(x, y, w, h)``."""
    _x, _y, w, h = bbox
    return image_tokens(w, h)


__all__ = ["image_tokens", "bbox_image_tokens"]
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/unit/test_tutorial_tokens.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/tutorial/tokens.py tests/unit/test_tutorial_tokens.py
git commit -m "feat(tutorial): pure image-token estimate helper"
```

### Task B2: Runner records per-step tokens, perceived element, and totals

**Files:**
- Modify: `src/cerebellum_cua/tutorial/runner.py`
- Test: `tests/unit/test_tutorial_runner.py` (create or append)

**Interfaces:**
- Consumes: `gateway.budget.estimate_tokens(obj) -> int`.
- Produces: each timeline entry gains `"tokens": int` (estimated tokens of the step result; 0 for pause) and `"perceived": str` (best-effort element label from the result, else ""). The returned dict gains `"totals": {"a11y_tokens": int}`.

- [ ] **Step 1: Write failing test**

```python
from typing import Any

from cerebellum_cua.tutorial import Tutorial, run_tutorial


class _FakeEngine:
    def __init__(self) -> None:
        self.handlers = {
            "run_skill": lambda p: {"success": True, "action": "click",
                                    "affected_rows": [1], "name": "Open"},
            "read_text": lambda p: {"texts": [{"row_id": 1, "text": "Open",
                                               "bbox": [0, 0, 40, 20]}], "count": 1},
        }


def _clock():
    t = {"n": 0.0}
    def tick() -> float:
        t["n"] += 1.0
        return t["n"]
    return tick


def test_runner_records_tokens_and_totals():
    tut = Tutorial.from_dict({"title": "t", "steps": [
        {"caption": "click open", "action": "skill", "name": "click",
         "args": {"target": "Open"}, "hold": 1.0},
        {"caption": "read", "action": "op", "name": "read_text",
         "args": {}, "hold": 1.0},
    ]})
    out = run_tutorial(_FakeEngine(), tut, clock=_clock())
    tl = out["timeline"]
    assert tl[0]["tokens"] > 0           # real estimate of the skill result
    assert tl[1]["tokens"] > 0
    assert out["totals"]["a11y_tokens"] == tl[0]["tokens"] + tl[1]["tokens"]
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_tutorial_runner.py -q`
Expected: FAIL (`tokens` / `totals` absent).

- [ ] **Step 3: Implement** — in `runner.py`:

Add import at top: `from cerebellum_cua.gateway.budget import estimate_tokens`.

In `_run_step`, after computing `result`, capture tokens + perceived; return them. Replace the `_run_step` result section and add helpers:

```python
def _run_step(
    engine: CuaEngine, step: TutorialStep, clock: Clock, origin: float
) -> dict[str, Any]:
    """Execute one step and build its timeline entry (never raises)."""
    start = clock() - origin
    ok = True
    summary = ""
    tokens = 0
    perceived = ""
    try:
        result = _dispatch(engine, step)
        ok = _step_ok(result)
        summary = _summarize(result)
        tokens = estimate_tokens(result) if result is not None else 0
        perceived = _perceived(result)
    except Exception as exc:  # noqa: BLE001 - a step must never crash the run
        ok = False
        summary = f"error: {type(exc).__name__}: {exc}"
    end = max(clock() - origin, start + step.hold)
    return {
        "caption": step.caption,
        "start": round(start, 3),
        "end": round(end, 3),
        "ok": ok,
        "tokens": tokens,
        "perceived": perceived,
        "result_summary": summary,
    }


def _perceived(result: Any) -> str:
    """Best-effort label of the element a step acted on/perceived."""
    if not isinstance(result, dict):
        return ""
    for key in ("name", "target", "resolved", "title"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""
```

And in `run_tutorial`, accumulate totals:

```python
    origin = clock()
    timeline: list[dict[str, Any]] = []
    success = True
    for step in tutorial.steps:
        entry = _run_step(engine, step, clock, origin)
        timeline.append(entry)
        success = success and bool(entry["ok"])
    totals = {"a11y_tokens": sum(e["tokens"] for e in timeline)}
    return {"title": tutorial.title, "timeline": timeline,
            "success": success, "totals": totals}
```

- [ ] **Step 4: Run, verify PASS** (+ existing tutorial tests)

Run: `python -m pytest tests/unit/test_tutorial_runner.py tests/unit -q -k tutorial`
Expected: PASS. (If an existing test asserts exact timeline keys, update it to allow the new keys.)

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/tutorial/runner.py tests/unit/test_tutorial_runner.py
git commit -m "feat(tutorial): record per-step tokens, perceived element, totals"
```

### Task B3: Captions compose a stat line + closing summary card

**Files:**
- Modify: `src/cerebellum_cua/tutorial/captions.py`
- Test: `tests/unit/test_tutorial_captions.py` (create or append)

**Interfaces:**
- Consumes: timeline entries with `caption`, `start`, `end`, optional `perceived`/`tokens`/`shot_tokens`/`full_tokens`; optional `totals`.
- Produces: `compose_caption(entry: dict) -> str` (the multi-line on-screen text) and `summary_card(totals: dict) -> str`. `build_drawtext_filter` uses `compose_caption` for each entry's text (behavior for plain entries unchanged: a bare caption with no stats renders just the caption).

- [ ] **Step 1: Write failing tests**

```python
from cerebellum_cua.tutorial.captions import compose_caption, summary_card


def test_compose_caption_plain_is_just_the_caption():
    assert compose_caption({"caption": "hello"}) == "hello"


def test_compose_caption_includes_perceived_and_three_way_tokens():
    text = compose_caption({
        "caption": "Click Open", "perceived": "BUTTON 'Open'",
        "tokens": 420, "shot_tokens": 6, "full_tokens": 1365,
    })
    assert "Click Open" in text
    assert "BUTTON 'Open'" in text
    assert "420" in text and "1365" in text and "6" in text


def test_summary_card_shows_totals_and_ratio():
    card = summary_card({"a11y_tokens": 1240, "shot_tokens": 720,
                         "full_tokens": 5460})
    assert "1240" in card and "5460" in card
    assert "x" in card.lower()  # ratio like "4.4x"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_tutorial_captions.py -q`
Expected: FAIL (functions missing).

- [ ] **Step 3: Implement** — add to `captions.py`:

```python
def compose_caption(entry: dict[str, Any]) -> str:
    """Build the on-screen text for one timeline entry.

    A plain entry renders just its caption. When stats are present, append a
    perceived line and a three-way token line (a11y matrix vs focused shot vs
    full shot). Numbers are estimates produced upstream.
    """
    lines = [str(entry.get("caption", ""))]
    perceived = str(entry.get("perceived", "")).strip()
    if perceived:
        lines.append(f"perceived: {perceived}")
    a11y = entry.get("tokens")
    if a11y is not None and (entry.get("full_tokens") is not None):
        shot = entry.get("shot_tokens")
        full = entry.get("full_tokens")
        shot_part = f" · focused ~{shot}" if shot is not None else ""
        lines.append(f"matrix ~{a11y} tok{shot_part} · full shot ~{full}")
    return "\n".join(lines)


def summary_card(totals: dict[str, Any]) -> str:
    """Closing card: three-way totals + the matrix-vs-full ratio."""
    a11y = int(totals.get("a11y_tokens", 0))
    shot = int(totals.get("shot_tokens", 0))
    full = int(totals.get("full_tokens", 0))
    ratio = (full / a11y) if a11y else 0.0
    return (
        "perceived via the accessibility tree — no pixels\n"
        f"a11y matrix ~{a11y} tok · focused shots ~{shot} · "
        f"full screenshots ~{full}\n"
        f"~{ratio:.1f}x cheaper than full screenshots (estimates)"
    )
```

Then make `build_drawtext_filter` route each entry's text through `compose_caption`: find where it reads the caption (e.g. `entry["caption"]`) and replace with `compose_caption(entry)`. Keep the existing escaping/`enable=between(...)` logic.

- [ ] **Step 4: Run, verify PASS** (+ existing caption tests)

Run: `python -m pytest tests/unit/test_tutorial_captions.py tests/unit -q -k caption`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/tutorial/captions.py tests/unit/test_tutorial_captions.py
git commit -m "feat(tutorial): three-way token captions + summary card"
```

---

## Phase C — Clip-cutting planner

### Task C1: Pure segment planner + manifest

**Files:**
- Create: `src/cerebellum_cua/tutorial/clips.py`
- Test: `tests/unit/test_tutorial_clips.py`

**Interfaces:**
- Produces: `plan_segments(timeline: list[dict], pad: float = 0.0) -> list[dict]` — each `{index, label, start, end}`, contiguous, derived from timeline boundaries; a clip spans from its step's `start` to the next step's `start` (last to its own `end`), so every cut lands on a settled boundary.
- Produces: `ffmpeg_cut_argv(src: str, seg: dict, out: str) -> list[str]` — stream-copy cut for one segment.
- Produces: `build_manifest(timeline: list[dict], totals: dict, segments: list[dict]) -> dict` — the edit-list/provenance record.

- [ ] **Step 1: Write failing tests**

```python
from cerebellum_cua.tutorial.clips import (
    build_manifest, ffmpeg_cut_argv, plan_segments,
)


def _tl():
    return [
        {"caption": "intro", "start": 0.0, "end": 2.0, "tokens": 0,
         "perceived": "", "ok": True},
        {"caption": "click", "start": 2.0, "end": 5.0, "tokens": 420,
         "perceived": "BUTTON 'Open'", "ok": True},
        {"caption": "read", "start": 5.0, "end": 7.5, "tokens": 90,
         "perceived": "", "ok": True},
    ]


def test_plan_segments_are_contiguous_and_boundary_aligned():
    segs = plan_segments(_tl())
    assert [s["index"] for s in segs] == [0, 1, 2]
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 2.0
    assert segs[1]["start"] == 2.0 and segs[1]["end"] == 5.0
    assert segs[2]["end"] == 7.5  # last uses its own end


def test_ffmpeg_cut_argv_is_stream_copy():
    seg = {"index": 1, "label": "click", "start": 2.0, "end": 5.0}
    argv = ffmpeg_cut_argv("master.mp4", seg, "01-click.mp4")
    assert argv[:2] == ["ffmpeg", "-y"]
    assert "-ss" in argv and "2.0" in argv
    assert "-to" in argv and "5.0" in argv
    assert "-c" in argv and "copy" in argv
    assert argv[-1] == "01-click.mp4"


def test_manifest_carries_verified_and_tokens():
    tl = _tl()
    segs = plan_segments(tl)
    man = build_manifest(tl, {"a11y_tokens": 510}, segs)
    assert man["totals"]["a11y_tokens"] == 510
    assert len(man["clips"]) == 3
    assert man["clips"][1]["perceived"] == "BUTTON 'Open'"
    assert man["clips"][1]["verified"] is True
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_tutorial_clips.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Cut a captioned master recording into editable per-segment clips.

Pure planning (segment boundaries, ffmpeg argv, manifest) so it is fully
unit-testable; the actual ffmpeg invocation is a thin guarded wrapper. Segments
are derived from the tutorial timeline so every cut lands on a settled step
boundary — each clip starts from a quiet frame and the clips concatenate back
into the master.
"""

from __future__ import annotations

from typing import Any


def plan_segments(timeline: list[dict[str, Any]], pad: float = 0.0) -> list[dict[str, Any]]:
    """Contiguous segments from timeline step boundaries (settled cut points)."""
    segs: list[dict[str, Any]] = []
    n = len(timeline)
    for i, entry in enumerate(timeline):
        start = float(entry["start"])
        end = float(timeline[i + 1]["start"]) if i + 1 < n else float(entry["end"])
        label = _slug(entry.get("caption", f"step{i}"))
        segs.append({"index": i, "label": label,
                     "start": round(start, 3), "end": round(end + pad, 3)})
    return segs


def ffmpeg_cut_argv(src: str, seg: dict[str, Any], out: str) -> list[str]:
    """Stream-copy cut of one segment (lossless, keyframe-aligned)."""
    return [
        "ffmpeg", "-y", "-ss", str(seg["start"]), "-to", str(seg["end"]),
        "-i", src, "-c", "copy", out,
    ]


def build_manifest(
    timeline: list[dict[str, Any]], totals: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Edit-list + provenance: one record per clip with stats and verified flag."""
    clips = []
    for seg in segments:
        e = timeline[seg["index"]]
        clips.append({
            "index": seg["index"],
            "file": f"{seg['index']:02d}-{seg['label']}.mp4",
            "caption": e.get("caption", ""),
            "start": seg["start"], "end": seg["end"],
            "perceived": e.get("perceived", ""),
            "tokens": e.get("tokens", 0),
            "verified": bool(e.get("verified", e.get("ok", False))),
        })
    return {"totals": totals, "clips": clips}


def _slug(text: str) -> str:
    """Filesystem-safe lowercase slug from a caption (first few words)."""
    keep = [c.lower() if c.isalnum() else "-" for c in str(text)]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return "-".join(slug.split("-")[:4]) or "step"


__all__ = ["plan_segments", "ffmpeg_cut_argv", "build_manifest"]
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/unit/test_tutorial_clips.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cerebellum_cua/tutorial/clips.py tests/unit/test_tutorial_clips.py
git commit -m "feat(tutorial): pure clip-segment planner + manifest"
```

### Task C2: Full suite + lint gate for Phases A–C

- [ ] **Step 1: Run everything**

Run: `python -m pytest -q && ruff check src tests && mypy src`
Expected: all green; `mypy` shows only the pre-existing `uia/patterns.py:90` error.

- [ ] **Step 2: Commit any lint fixups** (if needed)

```bash
git add -A && git commit -m "chore: lint/type fixups for focused-shot + tutorial tokens"
```

---

## Phase D — Zero-flake navigation hardening (rig)

> This phase is empirical: the fixes depend on what the probe finds. Use
> superpowers:systematic-debugging for each failure. Do NOT author the final flow
> until the gate passes.

### Task D1: Probe harness

**Files:**
- Create: `scripts/probe_flow.py` (rig demo script: drives candidate steps, prints a per-step report)

**Interfaces:**
- Produces: a script that, given a flow JSON, runs each step through a live engine in the rig and prints `{"step": i, "action": ..., "ok": bool, "perceived": str, "verified": bool}` per line, plus a final `ALL_OK true/false`.

- [ ] **Step 1: Implement the harness** (no unit test — it is an in-rig tool; its output IS the test)

```python
"""In-rig probe: run a candidate flow and report per-step success + verification.

Run via the rig: DEMO=/work/scripts/probe_flow.py FLOW=/work/<flow>.json
"""
from __future__ import annotations

import json
import os

from cerebellum_cua.cli.engine import CuaEngine
from cerebellum_cua.tutorial import Tutorial, run_tutorial


def main() -> None:
    flow = os.environ.get("FLOW", "/work/examples/tutorials/gedit_drive.json")
    tut = Tutorial.from_dict(json.load(open(flow)))
    eng = CuaEngine(db_dsn="/rig/out/probe.db", secret="x",
                    capture_backend_kind="atspi", visible_cursor=True,
                    verify_actions=True)
    try:
        out = run_tutorial(eng, tut)
        for i, e in enumerate(out["timeline"]):
            print(json.dumps({"step": i, "caption": e["caption"], "ok": e["ok"],
                              "perceived": e.get("perceived", ""),
                              "tokens": e.get("tokens", 0)}))
        print(f"ALL_OK {out['success']}")
    finally:
        eng.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Author an initial candidate flow** `examples/tutorials/gedit_drive.json` with settle pauses + `verify: true` on actions (intro pause → type_into → pause/settle → click Open/Save → pause → open Menu → re-capture → click a menu item → focused screenshot → read_text → outro pause). Use the §3 step list; exact targets are placeholders to be corrected by the probe.

- [ ] **Step 3: Run the probe in the rig**

Run: `RECORD_SECONDS=2 APP=gedit DEMO=/work/scripts/probe_flow.py FLOW=/work/examples/tutorials/gedit_drive.json bash scripts/run-vm.sh`
Capture the per-step JSON + `ALL_OK` from the demo log.

- [ ] **Step 4: Commit the harness + initial flow**

```bash
git add scripts/probe_flow.py examples/tutorials/gedit_drive.json
git commit -m "test(rig): navigation probe harness + initial gedit flow"
```

### Task D2: Root-cause and fix each flake (loop until gate passes)

For EACH step the probe reports `ok:false` (or `verified` false / wrong `perceived`):

- [ ] **Step 1:** Reproduce in the rig; read the actual error/resolution. (systematic-debugging Phase 1)
- [ ] **Step 2:** Identify root cause. Most likely candidates and where:
  - Popover items not perceived after a menu opens → the flow needs a `build_matrix` re-capture step after the menu click; if `run_skill`'s click doesn't re-capture, fix the skill/flow so resolution sees fresh elements. Files: `src/cerebellum_cua/skills/*`, the flow JSON.
  - Resolver picks the wrong element (e.g. two "Menu" buttons) → tighten the target query / matching. Files: `src/cerebellum_cua/skills/` resolver.
  - `reacquire` returns stale/None after the tree changed → fix re-acquisition. Files: `src/cerebellum_cua/capture/atspi/_reacquire.py`.
- [ ] **Step 3:** Write a failing unit test reproducing the root cause with fakes (no rig). 
- [ ] **Step 4:** Implement the minimal fix; run the unit test to green.
- [ ] **Step 5:** Re-run the probe in the rig; confirm that step now `ok:true` + `verified`.
- [ ] **Step 6:** Commit (`fix(<area>): <root cause> (regression test added)`).

**Gate:** Re-run the probe **5 times consecutively**; every run must print `ALL_OK True` with every action verified. Record the 5 logs as evidence (paste into the PR). Only then proceed to Phase E.

---

## Phase E — Author, record, assemble, document

### Task E1: Finalize the verified flow JSON

- [ ] Lock `examples/tutorials/gedit_drive.json` to the exact targets the probe proved, with settle `pause` steps before each action (clean cut points) and `verify: true` on each action. Add the focused-screenshot step (`op screenshot` with the acted row's `row_id`) and the closing pause. Commit.

### Task E2: Record the captioned master in the rig

- [ ] **Step 1:** Run the tutorial under the rig record path to produce `demo.mp4` + the timeline JSON:

Run: `RECORD_SECONDS=<fit> APP=gedit DEMO=/work/scripts/run-tutorial.py … bash scripts/run-vm.sh` (the runner prints the timeline; the rig records `demo.mp4`).
- [ ] **Step 2:** Enrich the timeline with `shot_tokens`/`full_tokens` using `tutorial.tokens` (focused = `bbox_image_tokens` of the acted element; full = `image_tokens(1280,800)`), and compute `totals` (a11y/shot/full). 
- [ ] **Step 3:** Burn captions with `tutorial.captions.burn_captions` (per-step `compose_caption` + the closing `summary_card`) onto `demo.mp4` → `docs/assets/cua-drive.mp4`.
- [ ] **Step 4:** Commit the master + the enriched timeline JSON.

### Task E3: Cut clips, manifest, and gif

- [ ] **Step 1:** Using `tutorial.clips.plan_segments` + `ffmpeg_cut_argv`, cut `docs/assets/cua-drive.mp4` into `docs/assets/clips/NN-<label>.mp4`.
- [ ] **Step 2:** Write `docs/assets/clips/manifest.json` via `build_manifest` (carries each clip's caption, times, perceived, tokens, verified).
- [ ] **Step 3:** Generate `docs/assets/cua-drive.gif` from the master (capped fps/width for repo size, e.g. `fps=10,scale=900:-1`).
- [ ] **Step 4:** Commit assets + manifest.

### Task E4: Verify the artifact looks good

- [ ] **Step 1:** Extract the FIRST frame of every clip (`ffmpeg -i clip -frames:v 1 first.png`) and Read each: confirm a settled state (no mid-action), legible caption, correct numbers.
- [ ] **Step 2:** Extract mid-action frames of the action clips; confirm the action is visibly happening and matches the caption.
- [ ] **Step 3:** Confirm `manifest.json` shows `verified:true` for every action clip and tokens match the enriched timeline.
- [ ] **Step 4:** Concatenate the clips (`ffmpeg concat`) and diff duration vs master to confirm seamless reassembly.
- [ ] **Step 5:** SendUserFile the gif + master + a couple of key frames for sign-off. Do not proceed until approved.

### Task E5: Written explainer + wiring

- [ ] **Step 1:** Add a "Self-recorded demo" section to `docs/TUTORIALS.md`: what the video shows, that CUA drove AND recorded it via the rig, the three-way token tally (with the "estimate" caveats from spec §6), and how the clips/manifest map to an edit.
- [ ] **Step 2:** Add a README line + embed `cua-drive.gif`.
- [ ] **Step 3:** Add a CHANGELOG entry (focused screenshots; tutorial token captions; the demo).
- [ ] **Step 4:** Final gate: `python -m pytest -q && ruff check src tests && mypy src` all green. Commit.

---

## Self-Review

- **Spec coverage:** §3 flow → D1/E1; §4 zero-flake → D2 gate; §5 token instrumentation → B1/B2/B3; §6 honest comparison → B3 copy + E5 explainer; §7 deliverables → A/E; §8 verification → E4; §9 components → A1–A3,B,C,E; §10 testing → tests in every task; §12 focused screenshots → A1–A3; §13 clips/clean-starts/verified → C1 + E1 (settle pauses) + E3/E4 + manifest `verified`. All covered.
- **Placeholders:** the only deferred specifics are the exact gedit targets (D2/E1) — inherent to the empirical phase, gated by 5 clean probe runs; not a code placeholder.
- **Type consistency:** `image_tokens`/`bbox_image_tokens` (B1) consumed in E2; `plan_segments`/`ffmpeg_cut_argv`/`build_manifest` (C1) consumed in E3; timeline `tokens`/`perceived` (B2) consumed by `compose_caption` (B3) and `build_manifest` (C1). Consistent.
