# Using cerebellum-cua inside an agent

`cerebellum-cua` is a perception tool: it gives an agent a queryable view of the
current GUI's accessibility tree. The model an agent uses it through is similar to
how an agent uses a browser-automation tool such as Playwright — the agent issues
structured requests and receives structured results, rather than reasoning over
pixels.

There are two ways to drive it: as a subprocess over the JSONL stdio protocol, or
as a Python library via the `CuaEngine` class.

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

## (c) MCP server wrapper — planned, not yet available

An MCP (Model Context Protocol) server wrapper is **planned** so that
`cerebellum-cua` can plug into MCP-based agents the way Playwright's MCP server
does. It is tracked as an issue and **does not exist yet**. Until it lands, use
the JSONL stdio interface (a) or the Python library (b) above. This document will
be updated when the MCP wrapper is available.
