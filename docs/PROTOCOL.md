# JSONL Wire Protocol (v4.2)

`cerebellum-cua` speaks a line-delimited JSON (JSONL) protocol over stdio. The
engine reads one JSON request per line from stdin and writes one JSON response
per line to stdout, flushing after each response. The wire protocol version is
`"4.2"`.

This is a reference. The request/response examples below were produced by running
the engine and capturing its output; field names and shapes are taken from the
code, not paraphrased.

## Transport and framing

- One request = one line of UTF-8 JSON terminated by `\n`.
- One response = one line of UTF-8 JSON terminated by `\n`.
- Blank input lines are skipped.
- The engine flushes stdout after every response.
- EOF on stdin ends the loop.

On startup the engine emits exactly one `engine_ready` **event** before reading
any requests:

```json
{"msg_id":"<uuid>","timestamp":"2026-06-13T16:59:56.633382+00:00","type":"event","operation":"engine_ready","payload":{"version":"4.2","status":"listening"},"error":null}
```

## Message envelope

Every request and response is an envelope with these fields:

| Field       | Type                | Notes                                                        |
|-------------|---------------------|--------------------------------------------------------------|
| `msg_id`    | string              | Caller-supplied id, echoed back on the response. Generated if absent. |
| `timestamp` | string (ISO 8601)   | Set by the engine on responses/events.                       |
| `type`      | string              | `"response"` for replies, `"event"` for `engine_ready` and async events. |
| `operation` | string \| null      | The operation name, echoed back.                             |
| `payload`   | object \| null      | Operation result on success; `null` when `error` is set.     |
| `error`     | object \| null      | `null` on success; an error object on failure.               |

A request envelope carries `msg_id`, `operation`, and `payload` (the engine reads
those three; `type` and `timestamp` on a request are ignored). When `payload` is
absent on a request it is treated as `{}`.

The error object is:

```json
{"code": <int>, "message": "<STRING>", "details": {<object>}}
```

## Operations

There are five core operations: `build_matrix`, `get_element`, `load_children`,
`invoke_action`, `get_snapshot_diff`, plus seven extensions: `screenshot` (opt-in
on-demand visual capture), `read_text` (aggregate on-screen text + coords),
`run_skill` (resolve a target by query, then act on it), `list_windows`
(authoritative desktop/window state from the WM), `read_legend` (a compact code
legend — token-saving shorthand for a snapshot's elements), `annotate` (a
set-of-marks overlay drawn onto a screenshot), and `wireframe` (an ASCII layout
map of a snapshot). An unknown operation returns code `9999`.

### build_matrix

Captures the live accessibility tree for a target, persists it, runs semantic
enrichment, registers a new epoch, and returns a summary. Live capture requires a
working backend (UIA on Windows, AT-SPI on Linux); on a host where the requested
backend cannot run, this returns error `1006` (see Errors below).

The `target` keys select the root (e.g. `exe_regex`, `title_regex`, `pid`,
`hwnd`, `app_name`; an empty target means the whole desktop). The `config` block
is parsed into a `MatrixConfig` (unknown keys are preserved but ignored). An
optional `capture_backend` key forces `"uia"` or `"atspi"` instead of `"auto"`.

Request:

```json
{"msg_id":"1","operation":"build_matrix","payload":{"target":{"exe_regex":"notepad","title_regex":".*Notepad"},"config":{"max_depth":12}}}
```

Success response payload (shape produced by the engine's build path):

```json
{
  "snapshot_id": 1,
  "epoch": 1,
  "total_elements": 3,
  "build_duration_ms": 0,
  "degraded_branches": 0,
  "root_elements": [0, 1, 2],
  "status": "success"
}
```

`root_elements` is the list of row ids at depth <= 1. `build_duration_ms` is the
measured capture+build time (0 in the seeded example above). On a host with no
runnable capture backend the same request returns:

```json
{
  "msg_id": "1",
  "timestamp": "2026-06-13T16:59:56.632136+00:00",
  "type": "response",
  "operation": "build_matrix",
  "payload": null,
  "error": {
    "code": 1006,
    "message": "UIA_ACCESS_DENIED",
    "details": {
      "reason": "capture_unavailable",
      "detail": "Capture backend 'uia' is not available on this host. UIA needs Windows + 'uiautomation'; AT-SPI needs a reachable Linux a11y bus (org.a11y.Bus) with the GI Atspi bindings. Check `available_backends()`."
    }
  }
}
```

### get_element

Returns one hydrated element by its dense `row_id`. Optional payload booleans
`include_relationships` (default true), `include_semantics` (default true), and
`include_children_stub` (default true) gate the corresponding sections.
`snapshot_id` is optional; when omitted the engine uses the latest persisted
snapshot.

Request:

```json
{"msg_id":"2","operation":"get_element","payload":{"snapshot_id":1,"row_id":1}}
```

Response:

```json
{
  "msg_id": "2",
  "timestamp": "2026-06-13T16:59:56.632353+00:00",
  "type": "response",
  "operation": "get_element",
  "payload": {
    "element": {
      "row_id": 1,
      "name": "Save",
      "control_type": 50000,
      "automation_id": "",
      "bounding_rect": {"left": 0, "top": 0, "width": 120, "height": 28, "dpi": 96},
      "properties": {"is_enabled": true},
      "patterns": {"invoke": {"supported": true}},
      "is_interactive": false,
      "is_content": false,
      "semantics": [{"domain_concept": "action_button", "confidence": 0.94}],
      "children_stub": {"has_children": false, "count": 0, "lazy_token": null},
      "relationships": [
        {"to_row_id": 0, "code": 2, "weight": 1.0, "metadata": {}},
        {"to_row_id": 2, "code": 3, "weight": 1.0, "metadata": {}}
      ]
    }
  },
  "error": null
}
```

The `relationships` list is present only when `include_relationships` is true.
Each relationship has `to_row_id`, `code` (the `RelationshipCode` value),
`weight`, and `metadata`.

The `properties` dict carries the element's state flags, and for elements that
support the AT-SPI `Text` interface (terminals, editors, documents) it also
carries the element's exact text:

- `text_content` — the element's full text buffer, captured at build time and
  capped at `text_content_max_chars` chars (default 4000, set in the build
  `config`). Present only when the element exposes `Text`/`EditableText` and has a
  non-empty buffer.
- `text_truncated` — `true` when the buffer exceeded the cap and was clipped.
  Absent otherwise.
- `caret_offset` — the integer caret position within the buffer, when the toolkit
  reports one.

These are populated by the AT-SPI backend; the UIA backend does not set them.

### load_children

Expands one accordion node, returning its direct children hydrated. Payload:
`parent_row_id` (default 0), optional `lazy_token`, `max_depth` (default 2),
`include_properties` (default true), `include_semantics` (default true), and
optional `snapshot_id` (defaults to the latest persisted snapshot).

When a `lazy_token` is supplied it is validated two ways before any children are
returned: the JWT signature/expiry and its binding to this `(snapshot_id,
parent_row_id)` pair, and the server-side token record. A token may be omitted on
the call; if supplied and invalid/expired, the call fails with `1003` or `1004`.

Request (with token):

```json
{"msg_id":"5","operation":"load_children","payload":{"snapshot_id":1,"parent_row_id":0,"lazy_token":"<jwt>","max_depth":2}}
```

Response:

```json
{
  "msg_id": "5",
  "timestamp": "2026-06-13T16:59:56.632939+00:00",
  "type": "response",
  "operation": "load_children",
  "payload": {
    "parent_row_id": 0,
    "children": [
      {
        "row_id": 1,
        "name": "Save",
        "control_type": 50000,
        "automation_id": "",
        "bounding_rect": {"left": 0, "top": 0, "width": 120, "height": 28, "dpi": 96},
        "properties": {"is_enabled": true},
        "patterns": {"invoke": {"supported": true}},
        "is_interactive": false,
        "is_content": false,
        "semantics": [{"domain_concept": "action_button", "confidence": 0.94}],
        "children_stub": {"has_children": false, "count": 0, "lazy_token": null}
      },
      {
        "row_id": 2,
        "name": "Text Editor",
        "control_type": 50004,
        "automation_id": "",
        "bounding_rect": {"left": 0, "top": 0, "width": 120, "height": 28, "dpi": 96},
        "properties": {},
        "patterns": {"value": {"supported": true}},
        "is_interactive": false,
        "is_content": false,
        "semantics": [],
        "children_stub": {"has_children": false, "count": 0, "lazy_token": null}
      }
    ],
    "has_more": false,
    "token_expires_at": "2026-06-13T17:04:56.632931+00:00"
  },
  "error": null
}
```

`has_more` is true only when the parent has more children than the per-call page
limit. `token_expires_at` is the wall-clock expiry of tokens issued in this
response. Each returned child carries its own `children_stub`; when the child has
children and the depth budget (`max_depth > 1`) allows, the stub's `lazy_token`
is a fresh token the agent can pass to a further `load_children` call.

### invoke_action

Executes a live action against the desktop through the capture seam. Two payload
forms are accepted.

**Element actions** re-acquire a persisted element on the live tree (via the
backend's re-find — an AT-SPI child-index path on Linux, or Name + ControlType on
Windows) and run a semantic action on it. Payload keys:

- `row_id` (required) — the matrix row to act on.
- `snapshot_id` (optional) — defaults to the latest persisted snapshot.
- `action` (optional, default `"invoke"`) — one of:
  `invoke` / `click` / `press` (Action interface),
  `set_text` (EditableText, needs `value`),
  `toggle` / `check` (Action),
  `select` (Selection or Action),
  `set_value` (Value, needs a numeric `value`),
  `expand` / `collapse` (Action).
- `value` (optional) — the text or number for `set_text` / `set_value`. Folded
  into `params` for the backend.
- `params` (optional) — extra action parameters passed through to the backend.

**Coordinate / raw-input forms** bypass the accessibility tree and synthesize
input. They are best-effort and platform-dependent (X11 XTEST via `Atspi`, or the
`ydotool` CLI on Wayland). No `row_id` / `snapshot_id` is needed:

- `{"action":"click_point","x":<int>,"y":<int>,"button":"left|right|middle","double":false}`
- `{"action":"drag","x":<int>,"y":<int>,"x2":<int>,"y2":<int>,"button":"left|right|middle"}`
  — press at `(x, y)`, glide to `(x2, y2)` with the button held, release.
- `{"action":"scroll","x":<int>,"y":<int>,"dx":<int>,"dy":<int>}` — wheel scroll at
  `(x, y)`; positive `dy` scrolls down, negative up; positive `dx` right, negative
  left. One wheel event per non-zero axis.
- `{"action":"type","value":"<text>"}`
- `{"action":"key","value":"ctrl+s"}`

By default coordinate input is **human-visible**: the cursor glides to the target
along an ease-in-out path and clicks decompose into move/settle/press/hold/release;
typing is paced per character. The motion profile is configured on the engine /
`SyntheticInput`, not per-request (see AGENT_INTEGRATION.md). A `"speed":"instant"`
profile collapses each action to a single jump with no sleeps for headless/fast
paths.

**User-takeover kill-switch.** When the engine is constructed with
`user_takeover_guard=True` (the default), coordinate/raw-input actions arm a
background watcher that monitors real Linux input devices (`evdev`). If a genuine
keypress, mouse move, or the panic key (`Space`/`Esc`) is detected mid-action, the
synthetic motion stops immediately and the call returns a clean result instead of
fighting the user:

```json
{"success": false, "action": "click_point", "aborted": true}
```

The watcher excludes our own synthetic device (`ydotool`/`uinput`) so automation
never aborts itself, and degrades to a no-op (never blocking, never crashing) when
`evdev` is not installed or `/dev/input` is not readable. The `evdev` dependency is
optional (`pip install -e '.[input]'`) and the watching user needs read access to
`/dev/input` (membership in the `input` group).

Element-action request:

```json
{"msg_id":"6","operation":"invoke_action","payload":{"snapshot_id":1,"row_id":7,"action":"set_text","value":"hello"}}
```

Coordinate-action request:

```json
{"msg_id":"6","operation":"invoke_action","payload":{"action":"click_point","x":420,"y":260}}
```

Success payload (on a host that can perform the action):

```json
{"success": true, "action": "set_text", "new_epoch": 2, "affected_rows": [7]}
```

Coordinate forms return only `success` and `action`:

```json
{"success": true, "action": "click_point"}
```

**Action verification (opt-in).** The driving agent cannot see the screen, so it
must infer whether an action worked from the matrix. Verification re-captures the
tree after a successful action and reports whether the UI observably changed.

It runs when the engine is constructed with `verify_actions=True` **or** the
payload carries `"verify": true` (a payload `verify` value always overrides the
engine default). It runs only after a successful action; a failed or aborted
action is returned unchanged. Verification reuses the existing capture path
(re-capturing the same target/backend/config recorded by the last `build_matrix`)
and `get_snapshot_diff`'s diff logic.

When verification runs, these fields are added to the success payload:

- `verified` — `true` if the UI observably changed, `false` if nothing changed
  (the action likely had no effect — retry or adapt; this is **not** an error),
  or `null` if re-capture was impossible.
- `effect` — `"changed"`, `"no_change"`, or `"unknown"`.
- `observed_change` — present when `verified` is a boolean; compact row-id lists
  only: `{"added_row_ids":[…],"removed_row_ids":[…],"modified_row_ids":[…]}`
  (the full field-level patches are not included).
- `reason` — present when `verified` is `null`: `"no_pre_action_snapshot"` or
  `"recapture_unavailable"` (headless host / no usable backend / no prior
  `build_matrix` to replay).

Verified request and response:

```json
{"msg_id":"7","operation":"invoke_action","payload":{"row_id":7,"action":"click","verify":true}}
```

```json
{"success": true, "action": "click", "new_epoch": 3, "affected_rows": [7],
 "verified": true, "effect": "changed",
 "observed_change": {"added_row_ids": [12], "removed_row_ids": [], "modified_row_ids": [4]}}
```

No-change result (action ran but the UI did not move):

```json
{"success": true, "action": "click",
 "verified": false, "effect": "no_change",
 "observed_change": {"added_row_ids": [], "removed_row_ids": [], "modified_row_ids": []}}
```

Re-capture unavailable:

```json
{"success": true, "action": "click", "verified": null, "effect": "unknown", "reason": "recapture_unavailable"}
```

If the element is found but the action could not be performed:

```json
{"success": false, "action": "set_text"}
```

On a host that cannot perform the action, error `1006` (`UIA_ACCESS_DENIED`) is
returned with a `reason` of `capture_unavailable` (no usable backend),
`reacquire_failed` (element could not be re-found on the live tree),
`action_unsupported` (the element does not support the requested action), or
`synthetic_input_unavailable` (no XTEST/ydotool path for a coordinate form):

```json
{
  "msg_id": "6",
  "type": "response",
  "operation": "invoke_action",
  "payload": null,
  "error": {
    "code": 1006,
    "message": "UIA_ACCESS_DENIED",
    "details": {
      "reason": "reacquire_failed",
      "detail": "Could not re-acquire element row 7 on the live tree (it may have moved, closed, or the snapshot is stale). Re-run build_matrix and retry."
    }
  }
}
```

A `row_id` that does not exist in the snapshot returns `1002`
(`ELEMENT_NOT_FOUND`).

### get_snapshot_diff

Diffs two epochs from the engine's in-memory snapshot history. Payload:
`from_epoch` and `to_epoch`. An epoch not present in the in-memory history
returns `1001` (`SNAPSHOT_NOT_FOUND`).

Request:

```json
{"msg_id":"7","operation":"get_snapshot_diff","payload":{"from_epoch":1,"to_epoch":2}}
```

Response (example where a button was renamed, so its identity changes — the old
row drops out and a new row appears):

```json
{
  "msg_id": "7",
  "timestamp": "2026-06-13T16:59:56.633283+00:00",
  "type": "response",
  "operation": "get_snapshot_diff",
  "payload": {
    "added_row_ids": [1],
    "removed_row_ids": [1],
    "modified_row_ids": [],
    "patches": []
  },
  "error": null
}
```

Payload fields:

- `added_row_ids` — row ids present only in the new snapshot.
- `removed_row_ids` — row ids present only in the old snapshot.
- `modified_row_ids` — row ids matched across epochs whose tracked fields changed.
- `patches` — one entry per modified row: `{"row_id": <new id>, "changes":
  {<field>: {"old": ..., "new": ...}, ...}}`. Tracked fields are `name`,
  `class_name`, `automation_id`, `control_type`, `is_interactive`, `is_content`,
  `framework_id`, plus `bounding_rect`, `properties`, and `patterns`.

### screenshot

Captures a single screenshot of the screen to a file and returns its path and
pixel dimensions. This is an **opt-in** operation: it is not part of
`build_matrix` and is never invoked automatically — capture stays screenshot-free
by default. Use it only when the accessibility tree is insufficient (a
custom-drawn or canvas UI) or to visually verify a result.

Payload keys (both optional):

- `path` — destination PNG path. When omitted, a temp file is created and its
  path is returned.
- `display` — X11 display override (e.g. `":0"`). Defaults to `$DISPLAY`.

The grabber is chosen by display server: on X11, `ffmpeg -f x11grab`, then
ImageMagick `import -window root`, then `scrot`; on Wayland, `grim`, then
`spectacle` (best-effort — the compositor may require permission). Dimensions are
read from the saved PNG header.

Request:

```json
{"msg_id":"10","operation":"screenshot","payload":{"path":"/tmp/desktop.png"}}
```

Response:

```json
{
  "msg_id": "10",
  "type": "response",
  "operation": "screenshot",
  "payload": {"path": "/tmp/desktop.png", "width": 1920, "height": 1080},
  "error": null
}
```

On a host where no screenshot grabber is installed (or all candidates fail), the
call returns error `1006` (`UIA_ACCESS_DENIED`) with a `reason` of
`screenshot_unavailable` rather than crashing:

```json
{
  "msg_id": "10",
  "type": "response",
  "operation": "screenshot",
  "payload": null,
  "error": {
    "code": 1006,
    "message": "UIA_ACCESS_DENIED",
    "details": {
      "reason": "screenshot_unavailable",
      "detail": "no screenshot grabber available: install one of ffmpeg, imagemagick (import), scrot (X11), or grim / spectacle (Wayland)."
    }
  }
}
```

### read_text

Aggregates every on-screen text run, with its bounding box, from a stored
snapshot. It reads from storage only (no live capture), so it works for any
backend: AT-SPI elements carry exact text in `properties.text_content`, while
vision elements carry OCR text in `name`. For each element with visible text it
returns one entry, preferring `text_content` over `name`.

Payload key (optional):

- `snapshot_id` — the snapshot to read. When omitted the engine uses the latest
  persisted snapshot.

Each returned text entry has `row_id`, `text`, and `bbox` (a
`[left, top, width, height]` array in screen pixels). The response is compact by
design — text and coordinates only, never pixels.

Request:

```json
{"msg_id":"11","operation":"read_text","payload":{"snapshot_id":1}}
```

Response:

```json
{
  "msg_id": "11",
  "type": "response",
  "operation": "read_text",
  "payload": {
    "texts": [
      {"row_id": 1, "text": "$ ls\nfile.txt", "bbox": [5, 30, 600, 400]},
      {"row_id": 2, "text": "Status: ready", "bbox": [5, 440, 200, 18]}
    ],
    "count": 2
  },
  "error": null
}
```

When no snapshot has been persisted yet, the call returns error `1001`
(`SNAPSHOT_NOT_FOUND`). When the snapshot exists but nothing has text, `texts` is
empty and `count` is `0`.

### run_skill

Runs a named high-level skill: it resolves a target element from a query, runs an
action on it through `invoke_action` (which re-acquires the live element and
applies the optional verify step), and returns a structured result. This is the
one-call form of `build_matrix` → resolve → `invoke_action`.

Payload keys:

- `skill` — the skill name (`click`, `type_into`, `open`, `focus`, `read`).
- `args` — the skill arguments. The query fields below are AND-combined; unknown
  keys are ignored. `type_into` additionally requires a top-level `value` inside
  `args`.
- `snapshot_id` — optional; resolve against this snapshot (default: latest).
- `capture` — optional; when `true` (or when nothing is persisted yet) a fresh
  `build_matrix` runs before resolving, so skills work from a cold start.

Query fields inside `args` (all optional):

| Field            | Type        | Matches                                          |
|------------------|-------------|--------------------------------------------------|
| `name`           | string      | exact element name, case-insensitive.            |
| `name_contains`  | string      | substring of the name, case-insensitive.         |
| `text_contains`  | string      | substring of name or `properties.text_content`.  |
| `role`           | int\|string | control-type int (`50000`) or name (`"BUTTON"`). |
| `control_type`   | int\|string | alias of `role`.                                 |
| `semantic`       | string      | a domain concept on the element.                 |
| `nth`            | int         | pick the nth match (default `0`), stable order.  |

Request:

```json
{"msg_id":"12","operation":"run_skill","payload":{"skill":"click","args":{"name":"Save"}}}
```

Response (the skill result, carrying the underlying `invoke_action` keys):

```json
{
  "msg_id": "12",
  "type": "response",
  "operation": "run_skill",
  "payload": {
    "skill": "click",
    "resolved_row_id": 7,
    "success": true,
    "action": "click",
    "affected_rows": [7]
  },
  "error": null
}
```

A `type_into` call sets a field's text:

```json
{"msg_id":"13","operation":"run_skill","payload":{"skill":"type_into","args":{"value":"hello","role":"EDIT"}}}
```

When the query matches no element the response is a structured failure (not an
error envelope) and no action is performed:

```json
{
  "msg_id": "14",
  "type": "response",
  "operation": "run_skill",
  "payload": {"skill": "click", "resolved_row_id": null, "success": false, "reason": "not_found", "query": {"name": "Nonexistent"}},
  "error": null
}
```

An unknown skill name returns `{"success": false, "reason": "unknown_skill"}` in
the payload. When verification is enabled (engine `verify_actions`, or `"verify":
true` inside `args`) the result also carries `verified` / `effect` /
`observed_change`, exactly as `invoke_action` does.

### list_windows

Lists top-level windows as the window manager / compositor reports them — which
windows exist, which is active, their geometry, state and workspace. This is the
authoritative, cheap desktop-layout view: it reads from the WM (not the a11y
tree). A common flow is to call `list_windows` first to see the whole desktop,
then `build_matrix` to drill into one window.

Payload key (optional):

- `backend` — force `"x11"`, `"kwin"`, or `"wlroots"`. Defaults to `"auto"`
  (selected by display server).

The response is `{"windows": [...], "backend": str|null, "count": int}`. Each
window object has:

- `id` — window id (X11: `0x`-prefixed hex).
- `title` — window title.
- `app` — application name (empty when the backend does not report one).
- `pid` — owning process id, or `null`.
- `bounds` — `{left, top, width, height, dpi}` in screen pixels.
- `active` — whether this is the focused window.
- `state` — a subset of `maximized` / `minimized` / `fullscreen` / `shaded`.
- `workspace` — 0-based desktop index, or `null` (sticky / all desktops / unknown).

Request:

```json
{"msg_id":"15","operation":"list_windows","payload":{}}
```

Response:

```json
{
  "msg_id": "15",
  "type": "response",
  "operation": "list_windows",
  "payload": {
    "backend": "x11",
    "count": 1,
    "windows": [
      {
        "id": "0x03600007",
        "title": "Firefox",
        "app": "",
        "pid": 12345,
        "bounds": {"left": 100, "top": 200, "width": 800, "height": 600, "dpi": 96},
        "active": true,
        "state": ["maximized"],
        "workspace": 0
      }
    ]
  },
  "error": null
}
```

`list_windows` does not error when no window source is available — on a host with
no usable backend (e.g. a Wayland/KWin or wlroots session, or X11 with neither
`wmctrl` nor `xdotool` installed) it returns an empty list rather than an error
envelope:

```json
{
  "msg_id": "15",
  "type": "response",
  "operation": "list_windows",
  "payload": {"windows": [], "backend": null, "count": 0},
  "error": null
}
```

Backend honesty: X11 is fully supported. KWin (Wayland) window enumeration is not
implemented — KWin exposes no stable window-list method over plain D-Bus, so a
full list needs a loaded KWin script; until that ships the KWin backend returns
`[]` rather than fabricate data. wlroots is note-only (its toplevel-management
protocol has no CLI client). Both return an empty list, never invented windows.

### read_legend

Builds a compact code legend for a snapshot's elements — a token-saving
shorthand. Instead of repeating full labels for every element, each *distinct
concept* (an element's top semantic `domain_concept` when present, else its
`ControlType` name lowercased) is assigned one short code, plus a one-time
`code -> meaning` legend the agent reads once. Codes are grouped by a single-letter
family: `button -> b`, `edit`/`text_input -> e`, `menu_item -> m`, `window -> w`,
`link -> l`, everything else `-> c` (e.g. `b0`, `b1`, `e0`). Ordering is
deterministic (by `row_id`).

This is **not** a persistent position cache: the legend is a pure function of one
snapshot's elements and is regenerated fresh on every call. Nothing is stored or
aliased across calls. (Pinned, persistent aliases are a possible future
extension and intentionally out of scope.)

Payload: `{snapshot_id?: int}` (default: latest persisted snapshot).

```json
{"msg_id":"16","operation":"read_legend","payload":{}}
```

```json
{
  "msg_id": "16",
  "type": "response",
  "operation": "read_legend",
  "payload": {
    "legend": {"w0": "window", "b0": "action_button", "b1": "cancel_button"},
    "elements": [
      {"row_id": 0, "code": "w0"},
      {"row_id": 1, "code": "b0"},
      {"row_id": 2, "code": "b1"}
    ],
    "count": 3
  },
  "error": null
}
```

### annotate

Draws a set-of-marks overlay: each of the snapshot's element bounding boxes is
drawn as a rectangle with a short label onto a screenshot, saved to a file. The
label is the element's `read_legend` code when available, else its `row_id`. By
default a fresh screenshot is grabbed (reusing the `screenshot` grabber); a
pre-existing image can be supplied via `path`. Rectangle drawing uses OpenCV
(`cv2`, the `[vision]` extra), imported lazily.

Payload: `{snapshot_id?: int, path?: str, out_path?: str, display?: str}` —
`path` is a source image (default: grab one), `out_path` is the destination
(default: a temp PNG), `display` overrides the X11 display when grabbing.

```json
{"msg_id":"17","operation":"annotate","payload":{"out_path":"/tmp/marked.png"}}
```

```json
{
  "msg_id": "17",
  "type": "response",
  "operation": "annotate",
  "payload": {"path": "/tmp/marked.png", "width": 1920, "height": 1080, "count": 3},
  "error": null
}
```

On a host where no screenshot grabber is installed, or OpenCV is unavailable, the
call raises code `1006` (`UIA_ACCESS_DENIED`) with `reason`
`annotate_unavailable` rather than crashing:

```json
{
  "msg_id": "17",
  "type": "response",
  "operation": "annotate",
  "payload": null,
  "error": {
    "code": 1006,
    "message": "UIA_ACCESS_DENIED",
    "details": {
      "reason": "annotate_unavailable",
      "detail": "OpenCV (cv2) is required to draw annotations; install the '[vision]' extra (pip install opencv-python-headless)."
    }
  }
}
```

### wireframe

Renders a snapshot's elements as a compact ASCII layout map — a glanceable text
wireframe (bordered boxes with truncated labels on a fixed character grid), built
from stored elements with no live capture.

Payload: `{snapshot_id?: int}` (default: latest persisted snapshot).

```json
{"msg_id":"18","operation":"wireframe","payload":{}}
```

```json
{
  "msg_id": "18",
  "type": "response",
  "operation": "wireframe",
  "payload": {"text": "+------------------+\n|Main Window       |\n| +-----+          |\n| |Save |          |\n| +-----+          |\n+------------------+"},
  "error": null
}
```

## Token accounting

The accordion operations annotate their response payloads with an
`estimated_tokens` field so the token-bounded property of the protocol can be
observed.

| Field              | Type | Notes                                              |
|--------------------|------|----------------------------------------------------|
| `estimated_tokens` | int  | Estimated token size of the response payload.      |

`estimated_tokens` appears on the success payloads of `get_element`,
`load_children`, and the initial-context response. It is computed by serializing
the payload to compact JSON (`json.dumps(separators=(",", ":"),
ensure_ascii=False)`) and dividing the character length by four, rounding up. It
is a heuristic estimate, not a model-exact token count; a real tokenizer could be
substituted without changing the field.

The estimate is measured over the payload before the field is added, so the count
reflects the operation result rather than itself.

### Optional response ceiling

The engine accepts an optional `max_response_tokens` ceiling (default `None` =
off). When `None`, responses are measured and annotated but never rejected — this
is the default behavior. When a ceiling is set and an assembled response's
estimated token count exceeds it, the operation fails with error `1009`
(`TOKEN_BUDGET_EXCEEDED`); its `details` carry `estimated_tokens` and
`max_tokens`.

## Error codes

On a raised domain error the response has `payload: null` and an `error` object.

| Code | Message                   | Raised when                                                        |
|------|---------------------------|-------------------------------------------------------------------|
| 1001 | `SNAPSHOT_NOT_FOUND`      | A referenced snapshot / epoch is not available.                   |
| 1002 | `ELEMENT_NOT_FOUND`       | A `row_id` does not exist in the snapshot.                        |
| 1003 | `INVALID_LAZY_TOKEN`      | A lazy token is malformed, mis-signed, mismatched, or server-side invalid. |
| 1004 | `TOKEN_EXPIRED`           | A lazy token's `exp` has passed.                                  |
| 1005 | `MAX_DEPTH_EXCEEDED`      | A requested traversal depth exceeds the allowed bound.            |
| 1006 | `UIA_ACCESS_DENIED`       | Live capture/invoke cannot run on this host (no backend, missing libs, denied access). |
| 1007 | `DEGRADED_BRANCH`         | A subtree could not be fully captured.                            |
| 1008 | `CONCURRENT_MODIFICATION` | The tree changed underneath an operation.                         |
| 1009 | `TOKEN_BUDGET_EXCEEDED`   | A response's estimated token count exceeds the configured ceiling (only when one is set). |
| 9999 | `UNKNOWN_OPERATION`       | The operation name is not one of the five (also used for `MALFORMED_REQUEST` on unparseable input). |

Notes:
- Code `9999` carries `message: "UNKNOWN_OPERATION"` with `details.operation`
  for an unknown operation, and `message: "MALFORMED_REQUEST"` with
  `details.error` for input that is not valid JSON.
- The base error class uses code `1000` (`MATRIX_UI_ERROR`); it is the parent of
  the codes above and is not normally emitted directly.

Unknown operation example:

```json
{
  "msg_id": "8",
  "type": "response",
  "operation": "frobnicate",
  "payload": null,
  "error": {"code": 9999, "message": "UNKNOWN_OPERATION", "details": {"operation": "frobnicate"}}
}
```

Element-not-found example:

```json
{
  "msg_id": "9",
  "type": "response",
  "operation": "get_element",
  "payload": null,
  "error": {"code": 1002, "message": "ELEMENT_NOT_FOUND", "details": {"snapshot_id": 1, "row_id": 999}}
}
```

## Lazy-token semantics

A lazy token is an opaque, HS256-signed JWT issued by the gateway for one
`(snapshot_id, parent_row_id)` pair so a downstream caller can later expand
exactly that node. The signed payload is `{sid, pid, max_d, iat, exp}` with a
300-second TTL.

- Tokens are minted only for nodes that actually have children and have remaining
  depth budget. A leaf, or a node at the depth limit, carries
  `children_stub.lazy_token: null`.
- When a token is presented to `load_children`, the gateway validates the JWT
  (signature, expiry, and the bound `sid`/`pid`) and then checks the server-side
  token record. A token whose JWT is valid but whose server-side record is
  missing or expired yields `1003`; an expired JWT yields `1004`.
- The same secret (passed as `--secret`) must be used by every process that
  issues and validates tokens for a given snapshot.

## matrix_patch event

The events layer debounces bursts of UIA StructureChanged notifications and
collapses them into a single "patch required" signal per subtree. When wired to
incremental rebuild, this produces an asynchronous `matrix_patch` event envelope
(`type: "event"`) carrying the new epoch and the diff for the affected rows,
emitted on the same stdout stream as responses. The storage layer's `record_patch`
persists each patch as `{snapshot_id, epoch, patch_type, affected_row_ids,
patch_json}` for replay. The async push path is part of the v4.2 contract;
the patch *content* mirrors the `get_snapshot_diff` payload
(`added_row_ids` / `removed_row_ids` / `modified_row_ids` / `patches`).

## elevate operation

`elevate` answers a privilege-escalation prompt as part of a task: a Linux
polkit authentication dialog, an interactive terminal `sudo`, or (on Windows)
the UAC consent dialog. It is opt-in and depends on an elevation password
configured out of band.

Request payload:

```json
{"method": "auto", "command": ["systemctl", "restart", "foo"]}
```

- `method` (optional, default `"auto"`): one of `"auto"`, `"polkit"`, `"sudo"`,
  `"uac"`.
  - `auto` drives a polkit dialog if one is currently visible (detected from the
    WM via `list_windows`); otherwise runs `command` under `sudo`; otherwise
    reports that a human is needed.
  - `polkit` fills a visible polkit dialog's password field and clicks
    Authenticate (reusing `build_matrix` + `invoke_action`).
  - `sudo` runs `command` under `sudo -S`, feeding the password on stdin.
  - `uac` always reports `needs_human` (see the Windows note below).
- `command` (optional): the argv to run elevated (used by `sudo`; context only
  for `uac`).
- The password is **never accepted via the payload**. It is read only from the
  `CEREBELLUM_ELEVATION_PASSWORD` environment variable / `.env` file. Leave it
  unset to disable elevation entirely.

Response payload (`ElevationResult`):

```json
{"success": false, "method": "sudo", "needs_human": true, "detail": "sudo is not available on this host", "extra": {}}
```

- `success`: whether elevation was driven/granted.
- `method`: which backend ran (`polkit` / `sudo` / `uac` / `none`).
- `needs_human`: true when no automated path can proceed and a person must
  complete the prompt.
- `detail`: a short, **redacted** status string. The password never appears in
  `detail` or anywhere else in the response.
- `extra`: non-sensitive structured context (return code, snapshot id, …).

### Windows UAC limit (honest)

The UAC consent dialog runs on the **secure desktop**, an isolated desktop that
a non-elevated process without `uiAccess` cannot read or click. This tool
therefore cannot drive the UAC prompt: `method: "uac"` returns
`success: false, needs_human: true`. The only honest ways past UAC are for the
tool's own process to already be elevated / hold `uiAccess`, or for a human to
accept the prompt. Relaunching via `runas` still surfaces a UAC dialog a person
must approve.
