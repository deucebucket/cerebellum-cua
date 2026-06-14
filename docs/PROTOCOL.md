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

There are five operations: `build_matrix`, `get_element`, `load_children`,
`invoke_action`, `get_snapshot_diff`. An unknown operation returns code `9999`.

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
- `{"action":"type","value":"<text>"}`
- `{"action":"key","value":"ctrl+s"}`

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
