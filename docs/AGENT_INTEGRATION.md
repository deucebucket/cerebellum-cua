# Using cerebellum-cua inside an agent

`cerebellum-cua` is a perception tool: it gives an agent a queryable view of the
current GUI's accessibility tree. The model an agent uses it through is similar to
how an agent uses a browser-automation tool such as Playwright — the agent issues
structured requests and receives structured results, rather than reasoning over
pixels.

There are two ways to drive it: as a subprocess over the JSONL stdio protocol, or
as a Python library via the `CuaEngine` class.

## Hybrid perception: a11y tree by default, screenshot on demand

Perception is hybrid. The accessibility tree is the default path and should be
preferred for almost everything: it is token-efficient, structured, and directly
actionable (`build_matrix` → `get_element` / `load_children` → `invoke_action`).

An **optional `screenshot` operation** is available for the cases the tree cannot
cover well. Reach for it when:

- The target is **custom-drawn / canvas-based** (a game, a chart, an image
  editor) and `build_matrix` returns a sparse or empty tree, so there is nothing
  structured to reason over.
- You need to **visually verify** an action's effect — confirm a dialog actually
  appeared, that text rendered, or that a layout changed — beyond what the a11y
  properties report.

Do **not** use it as the primary loop: a screenshot is a single image with no
structure, far more tokens than the equivalent tree slice, and it performs no
OCR or analysis (it returns an image file for an external model to inspect). It
is never taken automatically and is not part of `build_matrix`.

```jsonl
→ {"msg_id":"10","operation":"screenshot","payload":{"path":"/tmp/desktop.png"}}
← {"msg_id":"10","type":"response","operation":"screenshot","payload":{"path":"/tmp/desktop.png","width":1920,"height":1080},"error":null}
```

Omit `path` to have a temp file created and its path returned. On a host with no
screenshot tool installed the call returns error `1006` with a `reason` of
`screenshot_unavailable` (see [PROTOCOL.md](PROTOCOL.md)).

## Seeing the desktop layout cheaply: list_windows

Before drilling into any one window, you can see the whole desktop in a single
cheap call. `list_windows` reads top-level window state straight from the window
manager — which windows exist, which is active, each window's geometry, state
(maximized / minimized / fullscreen / shaded) and workspace. That is the WM's
authoritative data; it is cheaper and more reliable than inferring the desktop
arrangement from the a11y tree.

The intended pattern is **list, then drill in**: call `list_windows` to pick the
window you care about, then `build_matrix` to capture just that window's a11y
tree. The window-state view is a handful of rows (one per window), not a full
tree, so it costs almost nothing to keep oriented.

```jsonl
→ {"msg_id":"1","operation":"list_windows","payload":{}}
← {"msg_id":"1","type":"response","operation":"list_windows","payload":{"backend":"x11","count":2,"windows":[{"id":"0x3600007","title":"Firefox","app":"","pid":12345,"bounds":{"left":100,"top":200,"width":800,"height":600,"dpi":96},"active":true,"state":["maximized"],"workspace":0},{"id":"0x1000003","title":"konsole","app":"","pid":777,"bounds":{"left":0,"top":640,"width":960,"height":400,"dpi":96},"active":false,"state":[],"workspace":0}]},"error":null}
→ {"msg_id":"2","operation":"build_matrix","payload":{"target":{"pid":12345}}}
```

Pass `{"backend":"x11"|"kwin"|"wlroots"}` to force a backend; the default
`"auto"` picks by display server. `list_windows` never errors when no window
source is available — it returns `{"windows":[],"backend":null,"count":0}`, so
treat an empty list as "no usable window source" and fall back to `build_matrix`
on the active window directly.

**Backend honesty.** X11 is fully supported (via `wmctrl`, falling back to
`xdotool`). KWin (Wayland) window enumeration is **not** implemented: KWin
exposes no stable window-list method over plain D-Bus, so a complete list needs a
loaded KWin script — until that ships the KWin backend returns `[]` rather than
guess. wlroots is note-only (no CLI for its toplevel protocol). On those sessions
`list_windows` returns an empty list; rely on `screenshot` + `build_matrix`
instead. See [PROTOCOL.md](PROTOCOL.md) for the full schema.

## Reading on-screen text with positions

To read all the text currently on screen, with coordinates, in one cheap call:
run `build_matrix` to capture a snapshot, then `read_text` to aggregate every
text run plus its bounding box. `read_text` reads from storage (the snapshot you
just built), not the live tree, so no second capture is needed.

```jsonl
→ {"msg_id":"1","operation":"build_matrix","payload":{"target":{"app_name":"konsole"}}}
← {"msg_id":"1","type":"response","operation":"build_matrix","payload":{"snapshot_id":1,...},"error":null}
→ {"msg_id":"2","operation":"read_text","payload":{"snapshot_id":1}}
← {"msg_id":"2","type":"response","operation":"read_text","payload":{"texts":[{"row_id":1,"text":"$ ls\nfile.txt","bbox":[5,30,600,400]}],"count":1},"error":null}
```

Each entry has `row_id`, `text`, and `bbox` (`[left, top, width, height]` in
screen pixels). Omit `snapshot_id` to read the latest snapshot.

The source of the text depends on the capture backend:

- **AT-SPI (Linux)** gives exact text. Elements that support the `Text` interface
  (terminals, editors, documents) carry their full buffer in
  `properties.text_content` (capped at `text_content_max_chars`, default 4000);
  other elements fall back to their `name`. This is what makes a Konsole's
  contents readable verbatim and cheaply — text and coordinates only, no pixels.
- **Vision (screenshot + OCR)** gives OCR text. Vision elements store their
  recognized text in `name`, so `read_text` returns it the same way.

Because `read_text` returns text plus coordinates only, it stays far below the
token cost of a screenshot while remaining directly actionable: feed a `bbox`
center to `invoke_action`'s `click_point` to act on what you read.

## Cheap labeling with a cipher legend: read_legend

When you do show element labels to the model, repeating full names every turn is
wasteful. `read_legend` assigns one short code per *distinct concept* in a
snapshot (an element's top semantic concept, else its control type) and returns a
one-time `code -> meaning` legend. The agent reads the legend once, then refers to
elements by cheap codes (`b0`, `e1`, …); the leading letter is a family hint
(`b` button, `e` edit, `m` menu item, `w` window, `l` link, `c` other).

```text
→ {"msg_id":"3","operation":"read_legend","payload":{}}
← {"msg_id":"3","type":"response","operation":"read_legend","payload":{"legend":{"w0":"window","b0":"action_button","b1":"cancel_button"},"elements":[{"row_id":0,"code":"w0"},{"row_id":1,"code":"b0"},{"row_id":2,"code":"b1"}],"count":3},"error":null}
```

The legend is regenerated fresh on every call — it is a per-scan shorthand, not a
persistent position cache, and nothing is stored or aliased across calls. Default
target is the latest snapshot; pass `snapshot_id` for a specific one.

## Visual grounding and docs: annotate and wireframe

Two views render the structured matrix back into something visual, without a live
loop:

- `annotate` draws a **set-of-marks** overlay — each element's bounding box plus a
  short label (the `read_legend` code when available, else the `row_id`) — onto a
  screenshot and saves it. This is useful for grounding (a vision-capable model
  sees marked, numbered targets it can refer to) and for documentation/debugging.
  It grabs a fresh screenshot by default, or annotates a `path` you supply; the
  result is `{"path","width","height","count"}`. Drawing needs OpenCV (the
  `[vision]` extra); without it the call returns code `1006` with reason
  `annotate_unavailable` rather than crashing.

- `wireframe` returns a compact ASCII layout map of a snapshot (bordered boxes
  with truncated labels on a character grid) — a glanceable, text-only structure
  view that costs no image bytes.

```text
→ {"msg_id":"4","operation":"annotate","payload":{"out_path":"/tmp/marked.png"}}
← {"msg_id":"4","type":"response","operation":"annotate","payload":{"path":"/tmp/marked.png","width":1920,"height":1080,"count":3},"error":null}
→ {"msg_id":"5","operation":"wireframe","payload":{}}
← {"msg_id":"5","type":"response","operation":"wireframe","payload":{"text":"+----...----+\n|Main Window|\n..."},"error":null}
```

## Full control surface: click, drag, scroll, type, key

`invoke_action` covers the complete set of pointer/keyboard primitives an agent
needs to drive a GUI without a screenshot loop. Element actions (re-acquired by
`row_id`) run a semantic action; coordinate forms synthesize raw input:

- `click_point` — click at `(x, y)` (`button`, `double` optional).
- `drag` — press at `(x, y)`, glide to `(x2, y2)` with the button held, release.
  Use for sliders, selections, drag-and-drop, and canvas gestures.
- `scroll` — wheel at `(x, y)` by `dx`/`dy` (positive `dy` = down). Use to bring
  off-screen content into view, then re-`build_matrix` to capture it.
- `type` — type `value` into whatever has focus.
- `key` — send a combo like `"ctrl+s"`.

See [PROTOCOL.md](PROTOCOL.md) for the exact payloads.

## Act, then verify: confirming an action landed

A driving agent cannot see the screen, so after acting it must decide whether the
action actually did anything. `invoke_action` has an opt-in verification step that
answers this from the matrix — no screenshot required.

Enable it per-call with `"verify": true` in the payload (or globally with
`CuaEngine(..., verify_actions=True)`; a payload value overrides the engine
default). After a **successful** action the engine re-captures the same target it
last built and diffs it against the pre-action tree, then adds to the response:

- `verified` — `true` if the UI observably changed, `false` if nothing changed,
  `null` if re-capture was not possible.
- `effect` — `"changed"` / `"no_change"` / `"unknown"`.
- `observed_change` — compact `added_row_ids` / `removed_row_ids` /
  `modified_row_ids` lists (present when `verified` is a boolean).
- `reason` — why verification could not run (present when `verified` is `null`).

```jsonl
→ {"msg_id":"5","operation":"invoke_action","payload":{"row_id":7,"action":"click","verify":true}}
← {"msg_id":"5","type":"response","operation":"invoke_action","payload":{"success":true,"verified":true,"effect":"changed","observed_change":{"added_row_ids":[12],"removed_row_ids":[],"modified_row_ids":[4]}},"error":null}
```

The act-then-verify pattern in practice:

1. Act with `"verify": true`.
2. If `effect` is `"changed"`, the action landed — proceed (the row-id lists tell
   you what moved; `build_matrix` again or `get_element` to inspect the new rows).
3. If `effect` is `"no_change"`, the action had no observable effect — this is
   **not** an error. Retry, target a different element, or fall back to a
   coordinate form.
4. If `verified` is `null`, verification could not run (headless host, no backend,
   or no prior `build_matrix`); treat the action result on its own and verify
   manually if needed.

Verification reuses the normal capture path and the `get_snapshot_diff` logic, so
it costs roughly one extra `build_matrix`. Leave it off (the default) for actions
whose effect you do not need to confirm, or when minimizing capture overhead.

## Skills: resolve a target by description, then act

The low-level loop is `build_matrix` → inspect → `invoke_action` by `row_id`. The
`run_skill` operation collapses that into one call: a **skill** resolves a target
element from a small query, runs an action on it, and (when verification is on)
reports whether it landed. The agent names *what* it wants — `click` the `Save`
button — instead of bookkeeping row ids.

A skill resolves against the latest snapshot. Pass `"capture": true` to build a
fresh tree first, which is what you want from a cold start (e.g. opening an app):

```jsonl
→ {"msg_id":"6","operation":"run_skill","payload":{"skill":"click","args":{"name":"Save"}}}
← {"msg_id":"6","type":"response","operation":"run_skill","payload":{"skill":"click","resolved_row_id":7,"success":true,"action":"click","affected_rows":[7]},"error":null}

→ {"msg_id":"7","operation":"run_skill","payload":{"skill":"type_into","args":{"value":"hello","role":"EDIT"}}}
← {"msg_id":"7","type":"response","operation":"run_skill","payload":{"skill":"type_into","resolved_row_id":3,"success":true,"action":"set_text"},"error":null}

→ {"msg_id":"8","operation":"run_skill","payload":{"skill":"open","args":{"name":"My Computer"},"capture":true}}
← {"msg_id":"8","type":"response","operation":"run_skill","payload":{"skill":"open","resolved_row_id":2,"success":true,"action":"click"},"error":null}
```

### Built-in skills

| Skill       | Resolves              | Acts                                              |
|-------------|-----------------------|--------------------------------------------------|
| `click`     | one element by query  | the element's primary action ("click"/invoke).   |
| `type_into` | a field by query      | `set_text` to `value` (falls back to click+type).|
| `open`      | a target by name      | invoke it (launchers, menu items, desktop icons).|
| `focus`     | one element by query  | focus it (via a click).                          |
| `read`      | one element by query  | none — returns the element's `text`.             |

`type_into` takes `value` as a top-level arg; the rest of `args` is the query.

### Query fields (all optional, AND-combined)

- `name` — exact element name, case-insensitive.
- `name_contains` — substring of the name, case-insensitive.
- `text_contains` — substring of the name or `properties.text_content`.
- `role` / `control_type` — a raw UIA control-type int (`50000`) or a control-type
  name (`"BUTTON"`, `"EDIT"`, `"MENU_ITEM"`).
- `semantic` — a domain concept on the element (e.g. `action_button`, `text_input`).
- `nth` — pick the nth match (default `0`) in stable top-to-bottom, left-to-right
  order.

When several elements match, the resolver sorts them by position so a given query
resolves to the same element for an unchanged snapshot; use `nth` to pick another.

### Result shape and the not-found path

Every skill returns `{skill, resolved_row_id, success, …}`. A skill that acts
also carries the underlying `invoke_action` keys (`action`, `affected_rows`, and
`verified`/`effect`/`observed_change` when verification is enabled). If the query
matches nothing the skill returns `{skill, success:false, reason:"not_found",
query}` and performs no action — it never raises. An unknown skill name returns
`{skill, success:false, reason:"unknown_skill"}`.

Skills compose the existing layers; they do not reimplement actions or
verification. Enabling verification (engine `verify_actions=True` or threading
`"verify": true` through `args`) annotates the skill result the same way it
annotates `invoke_action`.

## (a) As a subprocess over JSONL stdio

Launch the engine as a child process and exchange JSONL lines with it. This works
from any language that can spawn a process and read/write its stdio.

```bash
python -m cerebellum_cua.cli --db-dsn ./matrix.db --secret "$JWT_SECRET"
```

Arguments:

- `--db-dsn` (required) — a file path or `sqlite:///...` for the SQLite backend,
  or `postgresql://user:pass@host:5432/dbname` for PostgreSQL.
- `--secret` (required) — the HS256 secret used to sign and validate lazy tokens.
  Use the same secret across processes that share a snapshot.
- `--max-depth` (optional) — default traversal depth.
- `--target-exe` / `--target-title` (optional) — default capture target regexes.
- `--mode` (optional) — execution mode: `desktop` (default; real session, auto
  backend, visible cursor), `vm` (isolated virtual session via
  `scripts/run-vm.sh`, AT-SPI backend, visible cursor for a viewer), or
  `background` (same isolated session, headless, no visible cursor). See
  [MODES.md](MODES.md).

The engine writes one `engine_ready` event, then reads one JSON request per line
from stdin and writes one JSON response per line to stdout. See
[PROTOCOL.md](PROTOCOL.md) for envelope and per-operation detail.

### Copy-pasteable example session

The following sends four requests in sequence. Each `→` line is written to the
engine's stdin; the engine replies with one line on stdout.

```jsonl
→ {"msg_id":"1","operation":"build_matrix","payload":{"target":{"exe_regex":"notepad","title_regex":".*Notepad"},"config":{"max_depth":12}}}
← {"msg_id":"1","type":"response","operation":"build_matrix","payload":{"snapshot_id":1,"epoch":1,"total_elements":3,"build_duration_ms":12,"degraded_branches":0,"root_elements":[0,1,2],"status":"success"},"error":null}

→ {"msg_id":"2","operation":"get_element","payload":{"snapshot_id":1,"row_id":0}}
← {"msg_id":"2","type":"response","operation":"get_element","payload":{"element":{"row_id":0,"name":"Untitled - Notepad","control_type":50032,"children_stub":{"has_children":true,"count":2,"lazy_token":"<jwt>"}, "...":"..."}},"error":null}

→ {"msg_id":"3","operation":"load_children","payload":{"snapshot_id":1,"parent_row_id":0,"lazy_token":"<jwt-from-step-2>","max_depth":2}}
← {"msg_id":"3","type":"response","operation":"load_children","payload":{"parent_row_id":0,"children":[{"row_id":1,"name":"Save","control_type":50000,"semantics":[{"domain_concept":"action_button","confidence":0.94}], "...":"..."}],"has_more":false,"token_expires_at":"..."},"error":null}

→ {"msg_id":"4","operation":"invoke_action","payload":{"snapshot_id":1,"row_id":1}}
← {"msg_id":"4","type":"response","operation":"invoke_action","payload":{"success":true,"new_epoch":2,"affected_rows":[1]},"error":null}
```

The flow is: `build_matrix` to capture and get the snapshot id and root row ids,
`get_element` (or `get_initial_context` semantics) to read a node and obtain a
`lazy_token` from its `children_stub`, `load_children` to expand that node using
the token, and `invoke_action` to act on a chosen element. The `invoke_action`
response above is the Windows/live-capable shape; on a host that cannot perform
live invocation the call returns error `1006` (see PROTOCOL.md).

A minimal Python driver of the subprocess:

```python
import json
import subprocess

proc = subprocess.Popen(
    ["python", "-m", "cerebellum_cua.cli", "--db-dsn", "./matrix.db", "--secret", "your-secret"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)

def call(operation, payload, msg_id):
    proc.stdin.write(json.dumps({"msg_id": msg_id, "operation": operation, "payload": payload}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())

ready = json.loads(proc.stdout.readline())   # engine_ready event
built = call("build_matrix", {"target": {"exe_regex": "notepad"}}, "1")
# ... then get_element / load_children / invoke_action
```

Note: the engine emits the `engine_ready` event line first, before any request;
read and discard it before sending requests.

## (b) As a Python library

Use `CuaEngine` directly in-process. The engine is a context manager that opens
the storage backend on entry and closes it on exit.

```python
from cerebellum_cua.cli import CuaEngine

with CuaEngine(db_dsn="./matrix.db", secret="your-secret") as engine:
    # Dispatch raw JSONL lines:
    response_line = engine.handle_line(
        '{"msg_id":"1","operation":"build_matrix","payload":{"target":{"exe_regex":"notepad"}}}'
    )

    # Or read nodes through the accordion directly:
    ctx = engine.accordion.get_initial_context(snapshot_id=1)
    element = engine.accordion.get_element(snapshot_id=1, row_id=1)
```

`engine.handle_line(line)` is the same entry the stdio REPL uses; it takes a JSONL
request string and returns the JSONL response string. The lower-level accordion
methods (`get_initial_context`, `get_element`, `load_children`) return Python
dicts directly if you prefer to skip JSON framing.

On a non-Windows host without a working capture backend, `build_matrix` and
`invoke_action` return a clean typed error (code `1006`) rather than raising or
crashing; the read paths (`get_element`, `load_children`, `get_snapshot_diff`)
work against any persisted or seeded snapshot.

### Human-visible motion and the user-takeover kill-switch

Coordinate / raw-input actions (`click_point`, `drag`, `scroll`, `type`, `key`)
drive the pointer in a human-observable way and can be cancelled the instant a
real person takes over. (`scroll` is a discrete wheel event with no glide.)

**Motion profile.** `SyntheticInput` glides the cursor to the target along an
ease-in-out path and decomposes a click into move → settle → press → hold →
release; typing is paced per character. It is tunable:

```python
from cerebellum_cua.capture.input import SyntheticInput

SyntheticInput(
    speed="human",        # "human" (animated, default) | "instant" (one jump, no sleeps)
    move_duration=0.5,    # seconds a glide spans
    steps=30,             # interpolation increments per glide
    click_pause=0.08,     # settle-before-press / press-hold duration
    key_delay=0.012,      # per-character typing delay (seconds)
)
```

The `"instant"` profile bypasses all interpolation and sleeps — use it for
headless runs and fast tests. Coordinate input still requires XTEST (X11) or
`ydotool` (Wayland).

**Kill-switch.** Pass `user_takeover_guard` to the engine (default `True`):

```python
from cerebellum_cua.cli import CuaEngine

with CuaEngine(db_dsn="./matrix.db", secret="s", user_takeover_guard=True) as engine:
    ...
```

When enabled, each coordinate action arms an `AbortWatcher` that reads Linux
`evdev` devices in a background thread and trips the moment it sees genuine user
input (any keypress/mouse move) or the panic key (`Space`/`Esc`). The in-progress
motion stops and the action returns `{"success": false, "action": ..., "aborted":
true}`. The watcher ignores our own synthetic device (matched by name —
`ydotool`/`uinput`) so automation never cancels itself.

The kill-switch is opt-in via this flag and **safe by default everywhere**: the
`evdev` package is an optional dependency and `/dev/input` access is not required.
When either is missing the watcher degrades to a no-op (`available is False`) that
never blocks and never crashes — existing behavior is unchanged. To enable real
detection on Linux:

```bash
pip install -e '.[input]'      # installs evdev
# and ensure the running user can read /dev/input (e.g. is in the `input` group)
```

## (c) As an MCP server

An MCP (Model Context Protocol) server wrapper exposes the same five operations
as MCP tools, so `cerebellum-cua` can plug into MCP-based agents the way
Playwright's MCP server does. It is a thin adapter over the `CuaEngine`: each
tool calls the matching operation handler and returns its response payload.

The MCP runtime is an optional dependency. Install it with the `mcp` extra:

```bash
pip install -e '.[mcp]'
```

Run the server over the stdio transport with the same `--db-dsn` / `--secret`
arguments as the JSONL REPL:

```bash
python -m cerebellum_cua.mcp --db-dsn ./matrix.db --secret "$JWT_SECRET"
# or, via the installed console script:
cerebellum-cua-mcp --db-dsn ./matrix.db --secret "$JWT_SECRET"
```

Arguments:

- `--db-dsn` (required) — a file path or `sqlite:///...` for the SQLite backend,
  or `postgresql://user:pass@host:5432/dbname` for PostgreSQL.
- `--secret` (required) — the HS256 secret used to sign and validate lazy tokens.
- `--max-response-tokens` (optional) — per-response token ceiling (default: off).

### Registered tools

The server registers five tools, one per operation. Each takes the operation's
payload fields as typed arguments (see [PROTOCOL.md](PROTOCOL.md)) and returns
the operation's response payload as a dict. A domain error is returned as a
structured `{"error": {"code", "message", "details"}}` dict rather than raised.

| Tool                | Arguments                                                                                          |
|---------------------|----------------------------------------------------------------------------------------------------|
| `build_matrix`      | `target`, `config`, `capture_backend`                                                              |
| `get_element`       | `row_id`, `snapshot_id`, `include_relationships`, `include_semantics`, `include_children_stub`     |
| `load_children`     | `parent_row_id`, `snapshot_id`, `lazy_token`, `max_depth`, `include_properties`, `include_semantics` |
| `invoke_action`     | `row_id`, `snapshot_id`                                                                            |
| `get_snapshot_diff` | `from_epoch`, `to_epoch`                                                                           |

`build_matrix` and `invoke_action` require a live capture backend (Windows UIA,
Linux AT-SPI, or the screenshot-based vision backend); on a host without one they
return the structured `1006` error, matching the JSONL behavior. The read paths
(`get_element`, `load_children`, `get_snapshot_diff`) work against any persisted
or seeded snapshot.

### Choosing or forcing a capture backend

`build_matrix` accepts an optional `capture_backend` argument: `"auto"` (default —
UIA on Windows, AT-SPI on Linux), `"uia"`, `"atspi"`, or `"vision"`. Force a
backend by name when the default does not fit, e.g. `capture_backend="vision"`.

Use the **vision** backend when the target exposes no usable accessibility tree:
games, engine/canvas-rendered UIs, custom-drawn widgets, remote-desktop / streamed
windows, or any app where the a11y tree is empty or unreliable. It captures one
screenshot and derives structured elements (bounding boxes + OCR text + a heuristic
type guess), so the agent still sees a token-bounded element list rather than raw
pixels, and acts on elements by their screen coordinates. Prefer an a11y backend
when one is available: it carries real roles, names and states, so it is higher
fidelity and cheaper than running OCR + contour detection. The vision backend
needs OpenCV, `pytesseract`, the system `tesseract-ocr` binary, and a screenshot
grabber on `PATH`; `available_backends()` reports `"vision"` only when all are
present.

### Registering it with an MCP client

An MCP agent client launches the server as a stdio subprocess. The configuration
follows the standard MCP server shape — a command, its arguments, and any
environment:

```json
{
  "mcpServers": {
    "cerebellum-cua": {
      "command": "cerebellum-cua-mcp",
      "args": ["--db-dsn", "./matrix.db", "--secret", "${JWT_SECRET}"]
    }
  }
}
```

Equivalently, use `"command": "python"` with
`"args": ["-m", "cerebellum_cua.mcp", "--db-dsn", "./matrix.db", "--secret", "${JWT_SECRET}"]`.
Once registered, the agent sees the five tools above and calls them by name.
