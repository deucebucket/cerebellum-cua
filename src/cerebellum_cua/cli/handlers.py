"""The five JSONL v4.2 operation handlers, bound to a live engine.

Each handler takes the request *payload* dict and returns the response *payload*
dict (the :class:`~cerebellum_cua.gateway.Protocol` wraps the envelope and serializes
any raised :class:`~cerebellum_cua.errors.MatrixUIError`). Handlers delegate read paths
to the :class:`~cerebellum_cua.gateway.Accordion`, the diff to
:func:`~cerebellum_cua.matrix.diff_snapshots`, and live capture/invoke to the (Windows-
only) uia layer — which raises a clear typed error on Linux rather than crashing.

The handlers are bound to an engine via :class:`OperationHandlers`; the engine
exposes the ``handlers`` dict the protocol dispatches against.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.config import MatrixConfig
from cerebellum_cua.errors import (
    ElementNotFoundError,
    MatrixUIError,
    SnapshotNotFoundError,
    UIAAccessDeniedError,
)
from cerebellum_cua.matrix import diff_snapshots
from cerebellum_cua.model import Snapshot
from cerebellum_cua.semantics import SEED_MAPPINGS, match_element

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

Handler = Callable[[dict[str, Any]], dict[str, Any]]

_NO_CAPTURE = (
    "Capture backend {kind!r} is not available on this host, and no usable "
    "fallback was found. Remediation:\n"
    "- UIA (Windows): install 'uiautomation' and run on Windows.\n"
    "- AT-SPI (Linux): the a11y bus must be reachable. Verify with `gdbus call "
    "--session --dest org.a11y.Bus --object-path /org/a11y/bus --method "
    "org.a11y.Bus.GetAddress`; export QT_ACCESSIBILITY=1 for Qt/KDE apps, ensure "
    "at-spi2-core is installed, and start apps AFTER the bus is up. See "
    "scripts/setup-linux.sh and docs/INSTALL.md.\n"
    "- Vision (any OS): install OpenCV ('opencv-python') and Tesseract "
    "('pytesseract' + the system 'tesseract' binary) plus a screen grabber; then "
    "the 'auto' backend can degrade to it. Pass capture_backend:'vision' to force "
    "it. Check `available_backends()` for what this host can actually run."
)


def _empty_capture_diagnostics(snapshot: Snapshot, backend: str | None) -> dict[str, Any]:
    """Explain a 0-element capture so it isn't mistaken for a blank screen.

    Uses the backend's per-capture diagnostics (how many application roots the
    registry exposed vs. matched the target) to pick an accurate reason: an empty
    a11y registry, a target that matched no app, or roots that yielded no
    elements — each with concrete remediation.
    """
    diag = snapshot.metadata.get("capture_diagnostics") or {}
    info: dict[str, Any] = {"empty": True, "capture_backend": backend}
    if backend == "atspi":
        apps = diag.get("registry_app_count")
        matched = diag.get("matched_root_count")
        if apps == 0:
            info["reason"] = "atspi_registry_empty"
            info["hint"] = (
                "The AT-SPI accessibility registry exposed 0 applications, so there "
                "was nothing to capture. This almost always means apps are not "
                "publishing their a11y trees — not that the screen is empty. On "
                "Qt/KDE export QT_ACCESSIBILITY=1 before launching apps; ensure "
                "at-spi2-core is running and that apps were STARTED AFTER the a11y "
                "bus; Wayland apps must support accessibility. See "
                "scripts/setup-linux.sh and docs/INSTALL.md."
            )
        elif matched == 0 and apps:
            info["reason"] = "no_root_matched_target"
            info["hint"] = (
                f"The registry exposed {apps} application(s) but none matched the "
                "requested target. Relax the target (app_name/pid/title_regex) or "
                "pass an empty target to capture the whole desktop; call "
                "list_windows to see what is open."
            )
        else:
            info["reason"] = "all_elements_filtered"
            info["hint"] = (
                f"{matched} application root(s) were walked but produced no "
                "elements — they may expose no accessible content, or the "
                "depth/visibility filter excluded everything. Try a larger "
                "config.max_depth, or the screenshot/vision path for "
                "non-accessible UIs."
            )
    else:
        info["reason"] = "no_elements"
        info["hint"] = (
            "Capture succeeded but produced 0 elements — the target may expose no "
            "accessible content. Verify the target, or use the 'screenshot'/"
            "'vision' path for custom-drawn or canvas UIs."
        )
    return info


class OperationHandlers:
    """Bundle of the five operation handlers closed over a :class:`CuaEngine`."""

    def __init__(self, engine: CuaEngine) -> None:
        self._engine = engine

    def as_dict(self) -> dict[str, Handler]:
        """Return the operation -> handler mapping the protocol dispatches against."""
        from cerebellum_cua.cli.representation import (  # noqa: PLC0415 - avoid cycle
            representation_ops,
        )

        return {
            "build_matrix": self.build_matrix,
            "get_element": self.get_element,
            "load_children": self.load_children,
            "invoke_action": self.invoke_action,
            "get_snapshot_diff": self.get_snapshot_diff,
            "screenshot": self.screenshot,
            "read_text": self.read_text,
            "run_skill": self.run_skill,
            "list_windows": self.list_windows,
            "elevate": self.elevate,
            **representation_ops(self),
        }

    # --- build_matrix ----------------------------------------------------
    def build_matrix(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Capture the live tree, persist it, enrich semantics, register the epoch.

        The capture backend is selected by OS via the universal capture seam
        ("auto" -> UIA on Windows, AT-SPI on Linux), overridable with the
        ``capture_backend`` payload key.
        """
        # Lazy import: backends pull in OS-specific libs only when actually used.
        from cerebellum_cua.capture import (  # noqa: PLC0415
            CaptureNotAvailable,
            capture_snapshot,
            get_capture_backend,
        )

        eng = self._engine
        target = dict(payload.get("target") or {})
        config = MatrixConfig.from_dict(payload.get("config") or {})
        requested = str(payload.get("capture_backend") or eng.capture_backend_kind)
        epoch = eng.next_epoch()
        degraded = False
        try:
            backend = get_capture_backend(requested)
            snapshot = capture_snapshot(backend, target, config, epoch)
            used = backend.name
        except (CaptureNotAvailable, ImportError) as exc:
            # In "auto" mode the caller asked for "whatever works", so degrade to
            # the vision backend when it is genuinely available rather than failing
            # the primary op outright (issue #50). A pinned backend never degrades:
            # the caller chose a specific data shape and must be told it's missing.
            fallback = self._vision_fallback(requested, target, config, epoch)
            if fallback is None:
                raise UIAAccessDeniedError(
                    reason="capture_unavailable",
                    detail=_NO_CAPTURE.format(kind=requested),
                ) from exc
            snapshot, used = fallback
            degraded = True
        snapshot_id = eng.persist(snapshot)
        eng.record_capture(target, config, used)
        self._enrich_semantics(snapshot, snapshot_id)
        return self._build_result(snapshot, snapshot_id, used, degraded)

    def _vision_fallback(
        self, requested: str, target: dict[str, Any], config: MatrixConfig, epoch: int
    ) -> tuple[Snapshot, str] | None:
        """Capture via vision when ``auto`` degrades; ``None`` if not applicable.

        Only fires for ``requested == "auto"`` and only when the vision backend
        reports itself available, so it never silently substitutes for a pinned
        backend and never throws past the caller's error path.
        """
        if requested != "auto":
            return None
        from cerebellum_cua.capture import (  # noqa: PLC0415
            CaptureNotAvailable,
            capture_snapshot,
            get_capture_backend,
        )

        try:
            vision = get_capture_backend("vision")
            if not vision.is_available():
                return None
            snapshot = capture_snapshot(vision, target, config, epoch)
        except (CaptureNotAvailable, ImportError):
            return None
        return snapshot, vision.name

    def register_snapshot(self, snapshot: Snapshot, snapshot_id: int) -> dict[str, Any]:
        """Persist enrichment for an already-built snapshot (test/seed entry point)."""
        self._enrich_semantics(snapshot, snapshot_id)
        return self._build_result(snapshot, snapshot_id)

    def _build_result(
        self,
        snapshot: Snapshot,
        snapshot_id: int,
        backend_used: str | None = None,
        degraded: bool = False,
    ) -> dict[str, Any]:
        roots = [
            e.row_id
            for e in snapshot.elements
            if int(e.metadata.get("depth", 0) or 0) <= 1
        ]
        backend = backend_used or snapshot.metadata.get("capture_backend")
        result: dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "epoch": snapshot.epoch,
            "total_elements": snapshot.total_elements,
            "build_duration_ms": snapshot.build_duration_ms,
            "degraded_branches": snapshot.degraded_branches,
            "root_elements": roots,
            "capture_backend": backend,
            "degraded": degraded,
            "status": "success",
        }
        if snapshot.total_elements == 0:
            # A 0-element capture is rarely a genuinely empty screen — far more
            # often the a11y tree is exposing nothing. Surface a cause + remedy so
            # the agent doesn't read it as "the screen is blank" (issue follow-up).
            result["diagnostics"] = _empty_capture_diagnostics(snapshot, backend)
        return result

    def _enrich_semantics(self, snapshot: Snapshot, snapshot_id: int) -> None:
        """Match every element and write its concepts into the link table."""
        eng = self._engine
        by_row = {e.row_id: e for e in snapshot.elements}
        for element in snapshot.elements:
            parent_row = element.metadata.get("parent_row_id")
            parent = by_row.get(parent_row) if parent_row is not None else None
            for concept in match_element(element, SEED_MAPPINGS, parent):
                if concept.domain_concept.startswith("exclude:"):
                    continue
                mapping_id = eng.ensure_mapping(
                    element.control_type, concept.domain_concept, concept.confidence
                )
                try:
                    eng.link_semantic(
                        snapshot_id, element.row_id, mapping_id, concept.confidence
                    )
                except ElementNotFoundError:  # pragma: no cover - defensive
                    continue

    # --- get_element / load_children (gateway delegation) ----------------
    def get_element(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a single hydrated element via the accordion."""
        snapshot_id = self._snapshot_id(payload)
        return self._engine.accordion.get_element(
            snapshot_id,
            int(payload["row_id"]),
            include_relationships=bool(payload.get("include_relationships", True)),
            include_semantics=bool(payload.get("include_semantics", True)),
            include_children_stub=bool(payload.get("include_children_stub", True)),
        )

    def load_children(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Expand one accordion node (validates the lazy token server-side)."""
        snapshot_id = self._snapshot_id(payload)
        return self._engine.accordion.load_children(
            snapshot_id,
            int(payload.get("parent_row_id", 0)),
            lazy_token=payload.get("lazy_token"),
            max_depth=int(payload.get("max_depth", 2)),
            include_properties=bool(payload.get("include_properties", True)),
            include_semantics=bool(payload.get("include_semantics", True)),
        )

    # --- invoke_action (live action via the capture seam) ----------------
    def invoke_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a live action: element actions (re-acquired via the capture
        backend) or coordinate/raw-input forms (synthetic input).

        Element forms take ``row_id`` (+ optional ``snapshot_id``), an ``action``
        (default ``"invoke"``), and optional ``value``/``params``. Coordinate forms
        are ``{"action":"click_point","x","y"}``, ``{"action":"drag","x","y","x2",
        "y2"}``, ``{"action":"scroll","x","y","dx","dy"}``, ``{"action":"type",
        "value"}``, and ``{"action":"key","value"}``. On an unavailable backend, a
        failed re-acquire, or an unsupported action, a typed
        :class:`~cerebellum_cua.errors.UIAAccessDeniedError` (code 1006) is raised — never a
        bare crash.

        When verification is enabled (engine ``verify_actions`` or payload
        ``"verify": true``) the tree is re-captured afterward and the response
        gains ``verified`` / ``effect`` / ``observed_change`` (or ``verified``
        null + ``reason`` if re-capture was impossible). See
        :mod:`cerebellum_cua.cli.verify`.
        """
        from cerebellum_cua.cli.invoke import perform_action  # noqa: PLC0415 - lazy
        from cerebellum_cua.cli.verify import (  # noqa: PLC0415 - lazy
            should_verify,
            verify_action,
        )

        eng = self._engine
        verify = should_verify(eng, payload)
        before = eng.snapshots_latest() if verify else None
        result = perform_action(eng, payload)
        if verify and result.get("success"):
            result.update(verify_action(eng, before))
        return result

    # --- screenshot (opt-in on-demand visual capture) -------------------
    def screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Grab a screenshot (optionally cropped to a region/element) + dims.

        The opt-in, on-demand half of hybrid vision — NOT part of ``build_matrix``.
        Scope is optional and mutually exclusive: ``region`` ``[x,y,w,h]`` crops to
        an explicit rect; ``row_id`` (+ optional ``snapshot_id``) crops to that
        element's stored ``bounding_rect`` (cheap "look at just this widget");
        ``window_id`` captures one X11/Xwayland window's real pixels (the reliable
        path under a Wayland compositor, where a root grab is black). No scope ->
        full screen. Other keys: ``path`` (destination PNG; temp if absent),
        ``display`` (X11 override). A full-screen grab that comes back all-black is
        rejected with a typed ``1006`` rather than returned as a silent success; a
        host with no grabber likewise raises ``1006`` instead of crashing.
        """
        from cerebellum_cua.capture.screenshot import (  # noqa: PLC0415
            ScreenshotError,
            default_screenshot_path,
            grab_screenshot,
        )

        path = str(payload.get("path") or default_screenshot_path())
        display = payload.get("display")
        region = self._resolve_region(payload)
        window_id = payload.get("window_id")
        try:
            return grab_screenshot(
                path, display=display, region=region,
                window_id=str(window_id) if window_id is not None else None,
            )
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

    # --- list_windows (authoritative desktop window state) --------------
    def list_windows(self, payload: dict[str, Any]) -> dict[str, Any]:
        """List top-level windows straight from the WM/compositor.

        The cheap, authoritative desktop layer (which windows exist, which is
        active, geometry/state/workspace) — read from the WM, not inferred from
        the a11y tree. Optional ``backend`` forces ``x11``/``kwin``/``wlroots``;
        default ``auto`` picks by display server. Returns ``{"windows": [...],
        "backend": str|null, "count": int}`` — an empty list (``backend`` null)
        on a host with no usable backend rather than an error.
        """
        from cerebellum_cua.desktop import windows as wmod  # noqa: PLC0415 - lazy

        backend = str(payload.get("backend") or "auto")
        windows = wmod.list_windows(backend)
        used = wmod.available(backend) if windows or backend == "auto" else backend
        return {
            "windows": [w.to_dict() for w in windows],
            "backend": used,
            "count": len(windows),
        }

    # --- elevate (answer a privilege-escalation prompt) -----------------
    def elevate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drive a privilege-escalation prompt (polkit / sudo / UAC).

        Payload: ``{method?: "auto"|"polkit"|"sudo"|"uac", command?: [str, ...]}``.
        The elevation password is **never** accepted via the payload — it is
        sourced only from the ``.env`` / environment config
        (``CEREBELLUM_ELEVATION_PASSWORD``); leave it unset to disable
        elevation. ``auto`` drives a visible polkit dialog, else runs ``command``
        under sudo, else reports a human is needed. Windows UAC always reports
        ``needs_human`` (it runs on the secure desktop). Returns an
        ``ElevationResult`` dict — the password never appears in any field.
        """
        from cerebellum_cua.elevation import elevate_op  # noqa: PLC0415 - lazy

        return elevate_op(self._engine, payload)

    # --- read_text (aggregate on-screen text + coords) ------------------
    def read_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Aggregate every on-screen text run + its bbox from a snapshot.

        Reads from storage (no live capture), so it works for any backend: AT-SPI
        elements carry exact text in ``properties["text_content"]`` while vision
        elements carry OCR text in ``name``. Payload: ``{snapshot_id?: int}``
        (default latest). Returns ``{"texts": [{"row_id", "text",
        "bbox":[left,top,width,height]}], "count": int}`` for every element that
        has visible text, preferring ``text_content`` over ``name``.
        """
        snapshot_id = self._snapshot_id(payload)
        texts: list[dict[str, Any]] = []
        for element in self._engine.storage.get_all_elements(snapshot_id):
            text = element.properties.get("text_content") or element.name
            if not text:
                continue
            rect = element.bounding_rect
            texts.append(
                {
                    "row_id": element.row_id,
                    "text": text,
                    "bbox": [rect.left, rect.top, rect.width, rect.height],
                }
            )
        return {"texts": texts, "count": len(texts)}

    # --- run_skill (resolve + act + optional verify) ---------------------
    def run_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one named skill: resolve a target, act on it, return the result.

        Payload: ``{skill: str, args: dict, snapshot_id?: int, capture?: bool}``.
        When ``capture`` is true (or nothing is persisted yet) a fresh
        ``build_matrix`` runs first, so resolution works from a cold start (e.g.
        'open my computer'). ``snapshot_id`` (if given) is threaded into the
        skill args so it resolves against that specific snapshot. The response is
        the skill result dict; an unknown skill yields
        ``{"success": False, "reason": "unknown_skill"}``.
        """
        from cerebellum_cua.skills import run_skill  # noqa: PLC0415 - lazy

        eng = self._engine
        capture = bool(payload.get("capture"))
        if capture or eng.storage.get_last_snapshot_id() is None:
            self.build_matrix({"target": payload.get("target") or {}})
        name = str(payload.get("skill") or "")
        args = dict(payload.get("args") or {})
        if payload.get("snapshot_id") is not None:
            args.setdefault("snapshot_id", int(payload["snapshot_id"]))
        return run_skill(eng, name, args)

    # --- get_snapshot_diff (in-memory epoch history) ---------------------
    def get_snapshot_diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Diff two seeded epochs from the engine's in-memory snapshot history."""
        old = self._engine.snapshot_for_epoch(int(payload["from_epoch"]))
        new = self._engine.snapshot_for_epoch(int(payload["to_epoch"]))
        return diff_snapshots(old, new)

    # --- internals -------------------------------------------------------
    def _snapshot_id(self, payload: dict[str, Any]) -> int:
        """Resolve the snapshot id from the payload or the latest persisted one."""
        sid = payload.get("snapshot_id")
        if sid is not None:
            return int(sid)
        last = self._engine.storage.get_last_snapshot_id()
        if last is None:
            raise SnapshotNotFoundError(reason="no_snapshot_persisted")
        return last


__all__ = ["OperationHandlers", "Handler", "MatrixUIError"]
