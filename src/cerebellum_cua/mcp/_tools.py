"""Adapter callables bridging MCP tool arguments to engine operation handlers.

Each builder returns a plain function whose typed parameters mirror the JSONL
payload fields of one operation (see ``docs/PROTOCOL.md``). The function packs
its arguments into a payload dict, calls ``engine.handlers[op](payload)``, and
returns the handler's response payload dict. A raised
:class:`~cerebellum_cua.errors.MatrixUIError` is caught and converted to a
structured ``{"error": {...}}`` dict so the MCP client receives a clean result
instead of a transport-level failure.

The MCP tool surface is kept at **parity with the JSONL protocol**: every engine
operation is exposed as a tool, so an agent connecting over MCP sees the whole
capability — perception, reading, acting, and visual grounding — not a subset.
The docstring of each inner function IS the tool description the agent reads when
choosing a tool (the server registers it via ``description=tool_fn.__doc__``), so
the docstrings are written for that audience: what the tool does and where it
fits in the perceive -> drill-in -> act loop.

No operation logic lives here — these are argument-shaping shims over the same
handlers the JSONL protocol dispatches against.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cerebellum_cua.errors import MatrixUIError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cerebellum_cua.cli.engine import CuaEngine

#: A bound tool callable returning the operation's response payload dict.
ToolFn = Callable[..., dict[str, Any]]


def _dispatch(engine: CuaEngine, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call one engine handler, converting domain errors to a structured dict."""
    try:
        return engine.handlers[operation](payload)
    except MatrixUIError as exc:
        return {"error": exc.to_dict()}


def build_matrix_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``build_matrix`` tool."""

    def build_matrix(
        target: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        capture_backend: str | None = None,
    ) -> dict[str, Any]:
        """Capture the live accessibility tree into a versioned snapshot — the
        START of the perception loop. Returns top-level context only (snapshot_id,
        epoch, total_elements, root_elements); drill into children with
        get_element / load_children rather than receiving the whole tree at once.

        target selects what to capture (empty = whole desktop; keys: exe_regex,
        title_regex, app_name, pid, hwnd) — prefer targeting one window (see
        list_windows) to keep it cheap. config tunes the walk (e.g. max_depth).
        capture_backend forces "uia"/"atspi"/"vision" instead of "auto"; use
        "vision" for canvas/game/custom-drawn UIs with no a11y tree.

        The response reports the capture_backend actually used and a degraded flag
        (true when auto fell back to vision). If total_elements is 0 it carries a
        diagnostics object explaining why (e.g. an empty a11y registry) — do NOT
        read 0 elements as "the screen is blank".
        """
        payload: dict[str, Any] = {"target": target or {}, "config": config or {}}
        if capture_backend is not None:
            payload["capture_backend"] = capture_backend
        return _dispatch(engine, "build_matrix", payload)

    return build_matrix


def get_element_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``get_element`` tool."""

    def get_element(
        row_id: int,
        snapshot_id: int | None = None,
        include_relationships: bool = True,
        include_semantics: bool = True,
        include_children_stub: bool = True,
    ) -> dict[str, Any]:
        """Hydrate ONE element by its dense 0-based row_id from a snapshot
        (default: the latest). Returns the element's name, control type, geometry,
        properties, semantic concepts, relationships, and a stub of its children
        (with a lazy_token to expand them via load_children). Use this to inspect a
        specific element found via build_matrix / load_children before acting on
        it. snapshot_id defaults to the most recent capture.
        """
        payload: dict[str, Any] = {
            "row_id": row_id,
            "include_relationships": include_relationships,
            "include_semantics": include_semantics,
            "include_children_stub": include_children_stub,
        }
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        return _dispatch(engine, "get_element", payload)

    return get_element


def load_children_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``load_children`` tool."""

    def load_children(
        parent_row_id: int = 0,
        snapshot_id: int | None = None,
        lazy_token: str | None = None,
        max_depth: int = 2,
        include_properties: bool = True,
        include_semantics: bool = True,
    ) -> dict[str, Any]:
        """Expand one accordion node, returning its direct children hydrated — the
        token-bounded way to walk DEEPER into a tree without pulling the whole
        thing. Pass the parent's row_id (0 = roots) and the lazy_token from that
        node's children stub (get_element / a prior load_children). max_depth
        controls how many levels to expand in this call. This is the core
        drill-in step between build_matrix and invoke_action.
        """
        payload: dict[str, Any] = {
            "parent_row_id": parent_row_id,
            "max_depth": max_depth,
            "include_properties": include_properties,
            "include_semantics": include_semantics,
        }
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        if lazy_token is not None:
            payload["lazy_token"] = lazy_token
        return _dispatch(engine, "load_children", payload)

    return load_children


def invoke_action_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``invoke_action`` tool."""

    def invoke_action(
        row_id: int | None = None,
        snapshot_id: int | None = None,
        action: str = "invoke",
        value: str | None = None,
        params: dict[str, Any] | None = None,
        x: int | None = None,
        y: int | None = None,
        x2: int | None = None,
        y2: int | None = None,
        dx: int | None = None,
        dy: int | None = None,
        button: str | None = None,
        double: bool | None = None,
        verify: bool | None = None,
    ) -> dict[str, Any]:
        """ACT on the live desktop. Two forms:

        - Element action: pass row_id (+ optional snapshot_id) and an action —
          "invoke"/"click" (default), "set_text" (with value), "toggle", "select",
          "set_value" (value), "expand"/"collapse". The element is re-acquired on
          the live tree, so capture it first with build_matrix.
        - Coordinate / raw input (no row_id, bypasses the a11y tree): action
          "click_point" (x, y, optional button/double), "drag" (x, y, x2, y2),
          "scroll" (x, y, dx, dy), "type" (value = text to type), "key" (value =
          a key/chord, e.g. "ctrl+s"). Useful for vision-located or non-accessible
          targets.

        Set verify=true to re-capture afterward and confirm the change landed
        (adds verified/effect to the result). For high-level intents like "click
        the Save button" prefer run_skill, which resolves the target by
        description for you.
        """
        payload: dict[str, Any] = {"action": action}
        for key, val in (
            ("row_id", row_id), ("snapshot_id", snapshot_id), ("value", value),
            ("params", params), ("x", x), ("y", y), ("x2", x2), ("y2", y2),
            ("dx", dx), ("dy", dy), ("button", button), ("double", double),
            ("verify", verify),
        ):
            if val is not None:
                payload[key] = val
        return _dispatch(engine, "invoke_action", payload)

    return invoke_action


def get_snapshot_diff_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``get_snapshot_diff`` tool."""

    def get_snapshot_diff(from_epoch: int, to_epoch: int) -> dict[str, Any]:
        """Diff two captured epochs (added / removed / changed row ids) so an agent
        can SEE WHAT CHANGED between two build_matrix snapshots instead of
        re-reading the whole tree — e.g. to confirm an action's effect, or to
        track a UI updating over time. Pass the two epoch numbers returned by
        build_matrix.
        """
        payload = {"from_epoch": from_epoch, "to_epoch": to_epoch}
        return _dispatch(engine, "get_snapshot_diff", payload)

    return get_snapshot_diff


def list_windows_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``list_windows`` tool."""

    def list_windows(backend: str | None = None) -> dict[str, Any]:
        """List top-level windows straight from the window manager / compositor —
        the cheap, authoritative "what is open" view. Often the FIRST call: pick
        the window you want, then build_matrix with a matching target (title_regex
        / pid) to capture just that window. Returns {windows: [{title, pid,
        geometry, active, ...}], backend, count}; an empty list means no usable
        window source (fall back to screenshot + build_matrix). Optional backend
        forces "x11"/"kwin"/"wlroots" (default "auto").
        """
        payload: dict[str, Any] = {}
        if backend is not None:
            payload["backend"] = backend
        return _dispatch(engine, "list_windows", payload)

    return list_windows


def screenshot_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``screenshot`` tool."""

    def screenshot(
        path: str | None = None,
        display: str | None = None,
        region: list[int] | None = None,
        row_id: int | None = None,
        snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Grab ONE screenshot and return its file path + dimensions. The opt-in
        visual escape hatch — use it when the accessibility tree is insufficient
        (custom-drawn / canvas UIs) or to confirm state. NOT part of build_matrix;
        performs no analysis (no OCR, no elements). Scope it cheaply:
        region=[x,y,w,h] OR row_id (+snapshot_id) crops the grab to just that
        element's box — far fewer image tokens than a full frame; no scope = full
        screen. For a structured element list from pixels, use build_matrix with
        capture_backend="vision". Optional display overrides the X11 display.
        """
        payload: dict[str, Any] = {}
        for key, val in (
            ("path", path), ("display", display), ("region", region),
            ("row_id", row_id), ("snapshot_id", snapshot_id),
        ):
            if val is not None:
                payload[key] = val
        return _dispatch(engine, "screenshot", payload)

    return screenshot


def read_text_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``read_text`` tool."""

    def read_text(snapshot_id: int | None = None) -> dict[str, Any]:
        """Aggregate every on-screen text run and its bounding box from a snapshot
        (default: latest) — a fast way to READ what the screen says without walking
        the tree element by element. Returns {texts: [{row_id, text, bbox:[left,
        top,width,height]}], count}. Works for any backend (AT-SPI text buffers or
        vision OCR). Run build_matrix first to produce the snapshot.
        """
        payload: dict[str, Any] = {}
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        return _dispatch(engine, "read_text", payload)

    return read_text


def run_skill_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``run_skill`` tool."""

    def run_skill(
        skill: str,
        args: dict[str, Any] | None = None,
        snapshot_id: int | None = None,
        capture: bool = False,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a HIGH-LEVEL skill that resolves a target BY DESCRIPTION and acts —
        the easiest way to act without manually walking the tree. skill is the
        name (e.g. "click", "type_into", "open", "focus"); args carries the
        skill's fields, typically a natural description plus inputs, e.g.
        run_skill("type_into", {"into": "search", "text": "hello"}) or
        run_skill("click", {"target": "Save"}). Set capture=true (or pass target)
        to build_matrix first so resolution works from a cold start. snapshot_id
        pins resolution to a specific snapshot. An unknown skill returns
        {success: false, reason: "unknown_skill"}.
        """
        payload: dict[str, Any] = {"skill": skill, "args": args or {}, "capture": capture}
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        if target is not None:
            payload["target"] = target
        return _dispatch(engine, "run_skill", payload)

    return run_skill


def elevate_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``elevate`` tool."""

    def elevate(
        method: str | None = None, command: list[str] | None = None
    ) -> dict[str, Any]:
        """Answer a privilege-escalation prompt (polkit / sudo / UAC) as part of a
        task. method is "auto" (default), "polkit", "sudo", or "uac"; command is
        an optional argv to run elevated. The elevation password is NEVER taken
        here — it is sourced only from the .env / environment
        (CEREBELLUM_ELEVATION_PASSWORD); leave it unset to disable elevation.
        Returns an ElevationResult dict (the password never appears in any field);
        Windows UAC always reports needs_human.
        """
        payload: dict[str, Any] = {}
        if method is not None:
            payload["method"] = method
        if command is not None:
            payload["command"] = command
        return _dispatch(engine, "elevate", payload)

    return elevate


def read_legend_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``read_legend`` tool."""

    def read_legend(snapshot_id: int | None = None) -> dict[str, Any]:
        """Build a compact CIPHER LEGEND for a snapshot (default: latest): one
        short code per distinct concept plus a one-time code->meaning map, so you
        can reason about many elements cheaply instead of repeating long labels.
        Returns {legend, elements, count}. Token-saving view; nothing is
        persisted. Run build_matrix first.
        """
        payload: dict[str, Any] = {}
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        return _dispatch(engine, "read_legend", payload)

    return read_legend


def wireframe_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``wireframe`` tool."""

    def wireframe(snapshot_id: int | None = None) -> dict[str, Any]:
        """Render a snapshot (default: latest) as a glanceable ASCII WIREFRAME —
        boxes + truncated labels laid out by geometry — to grasp the overall
        layout at a glance without reading every element. Returns {text}. Built
        from stored elements, no live capture. Run build_matrix first.
        """
        payload: dict[str, Any] = {}
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        return _dispatch(engine, "wireframe", payload)

    return wireframe


def annotate_tool(engine: CuaEngine) -> ToolFn:
    """Builder for the ``annotate`` tool."""

    def annotate(
        snapshot_id: int | None = None,
        path: str | None = None,
        out_path: str | None = None,
        display: str | None = None,
    ) -> dict[str, Any]:
        """Draw a SET-OF-MARKS overlay: each element's bounding box + a short
        legend code rendered onto a screenshot, for visual grounding (point a
        human at it, or feed a vision model boxes it can name). snapshot_id default
        latest; path uses an existing screenshot instead of grabbing one; out_path
        sets the output PNG. Returns {path, width, height, count}; needs a
        screenshot grabber + OpenCV (typed 1006 if absent). Run build_matrix first.
        """
        payload: dict[str, Any] = {}
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        if path is not None:
            payload["path"] = path
        if out_path is not None:
            payload["out_path"] = out_path
        if display is not None:
            payload["display"] = display
        return _dispatch(engine, "annotate", payload)

    return annotate


#: operation name -> builder. Registered as MCP tools at parity with the JSONL
#: protocol; each inner function's docstring is the tool description.
TOOL_BUILDERS: dict[str, Callable[[CuaEngine], ToolFn]] = {
    "build_matrix": build_matrix_tool,
    "get_element": get_element_tool,
    "load_children": load_children_tool,
    "invoke_action": invoke_action_tool,
    "get_snapshot_diff": get_snapshot_diff_tool,
    "list_windows": list_windows_tool,
    "screenshot": screenshot_tool,
    "read_text": read_text_tool,
    "run_skill": run_skill_tool,
    "elevate": elevate_tool,
    "read_legend": read_legend_tool,
    "wireframe": wireframe_tool,
    "annotate": annotate_tool,
}
