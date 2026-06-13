# Architecture

`cerebellum-cua` is the perception/capture layer of the broader Cerebellum
project: it reads an operating system's accessibility tree and serves it to an
agent as a queryable, versioned data structure rather than as a screenshot.

This document describes how the package is organized, how data flows through it,
and the trade-offs of the accessibility-tree approach. It is descriptive of the
current code, not aspirational.

## What the package does

1. Captures the OS accessibility tree (Windows UI Automation, or Linux AT-SPI)
   through a pluggable capture backend.
2. Filters out elements that carry no value to an agent (scrollbars, 1x1
   hit-test rects, offscreen/zero-sized/decorative nodes) via an inclusion
   predicate.
3. Builds a versioned relational matrix: a dense list of elements plus a sparse
   set of directed relationship edges (parent/child, sibling, etc.), tagged with
   an epoch number.
4. Persists each matrix snapshot to SQLite or PostgreSQL.
5. Serves the matrix to an agent over a line-delimited JSON (JSONL) protocol on
   stdio, using accordion lazy-loading and short-lived signed tokens so an agent
   pulls the tree one node at a time instead of receiving the whole thing.

It does **not** take screenshots and does **not** use a vision model.

## Why accessibility trees instead of screenshots

Driving a GUI from an accessibility tree (rather than from pixels + a vision
model) is an established, benchmarked technique. Prior art includes Microsoft's
UFO / UFO2 for Windows UI Automation and agent-sh/computer-use-linux for AT-SPI.
The reported benefit is token efficiency: a structured tree of named, typed
elements is cheaper to put in an LLM context than a rendered image, and several
published comparisons report roughly 6x fewer tokens for tree-based input versus
screenshot input. That number is from the prior literature, not a measurement of
this project.

The trade-offs are real and are not solved here:

- Accessibility trees can be slow to generate on large or deeply nested UIs.
- They can be very large, which is the reason for the lazy-loading gateway.
- They can be near-empty on legacy applications or custom-drawn UIs that do not
  expose an accessibility tree. When an app exposes nothing, the capture is
  empty. There is no pixel fallback.
- Live capture in this project is unverified against many real applications.
- The Windows / UIA path has not been run on real Windows hardware in this
  project's own testing.

## Layering

Dependencies flow in one direction only. A module may import from layers below
it, never from a layer above it or sideways into a peer's internals. This keeps
the engine testable on a non-Windows host: everything except the capture
backends is platform-neutral, and the capture backends import their OS-specific
libraries lazily so that importing the package never fails on the wrong OS.

```
+---------------------------------------------------------------+
|  cli                                                          |
|  CuaEngine composition root, argparse entry, JSONL stdio REPL |
+---------------------------------------------------------------+
        |                |                |
        v                v                v
+--------------+  +--------------+  +----------------------------+
|  gateway     |  |  events      |  |  capture                   |
|  accordion,  |  |  UIA event   |  |  CaptureBackend seam:      |
|  JWT tokens, |  |  manager +   |  |  uia_backend / atspi /     |
|  protocol    |  |  coalescer   |  |  (future macOS AX)         |
+--------------+  +--------------+  +----------------------------+
        |                                  |
        v                                  v
+--------------+  +--------------+  +----------------------------+
|  matrix      |  |  semantics   |  |  uia                       |
|  builder,    |  |  seed maps + |  |  COM/UIAutomation layer:   |
|  identity,   |  |  matcher     |  |  the only place            |
|  epoch diff  |  |              |  |  'import uiautomation' is   |
|  (pure)      |  |              |  |  allowed (lazy/guarded)    |
+--------------+  +--------------+  +----------------------------+
        |                |                |
        v                v                v
+---------------------------------------------------------------+
|  storage                                                      |
|  StorageBackend ABC + sqlite / postgres implementations      |
+---------------------------------------------------------------+
        |
        v
+---------------------------------------------------------------+
|  model · config · errors                                     |
|  dependency-free shared contracts (importable from anywhere) |
+---------------------------------------------------------------+
```

Layer responsibilities:

- **model / config / errors** — shared dataclasses (`Element`, `Relationship`,
  `Snapshot`, `BoundingRect`, `ChildStub`, `SemanticConcept`, the `ControlType`
  and `RelationshipCode` enums), the `MatrixConfig` knobs, and the numeric error
  taxonomy. These import nothing from other package modules.
- **capture** — the `CaptureBackend` seam and its OS backends. A backend reads
  the live tree and yields a normalized pre-order stream of `CapturedElement`
  records; it does not assign row ids, touch the DB, or make protocol decisions.
- **uia** — the Windows COM layer. The 10 UIA failure-mode workarounds live
  here (stale-handle resolver, stabilization, predicate, traversal, patterns).
  Windows-only imports are confined to this package and the UIA capture backend,
  and they are lazy so the package imports cleanly on Linux.
- **storage** — `StorageBackend` ABC plus SQLite and PostgreSQL backends.
  Storage persists and reads; it makes no policy decisions and never calls COM.
- **matrix** — pure logic: `build_snapshot` turns a walked node stream into a
  `Snapshot`; `identity` computes the stable composite key; `diff` computes the
  minimal delta between two epochs. No COM, no DB.
- **semantics** — heuristic mappings from raw control types to domain concepts
  (`action_button`, `text_input`, `menu_item`, ...) and the matcher that applies
  them. Imports only the model.
- **events** — the UIA `EventManager` (Remove-before-Add registration, bulk
  shutdown) and the `EventCoalescer` (debounces bursts of StructureChanged
  events). These are the workaround for UIA event-callback storms. Pure stdlib;
  the automation object is injected, so they unit-test on Linux.
- **gateway** — the accordion (lazy expansion over stored snapshots), the JWT
  lazy-token codec, and the JSONL protocol framing/dispatch. The gateway reads
  only from storage; it never walks the live tree.
- **cli** — `CuaEngine`, the composition root that wires the layers together,
  plus the argparse entry point and the stdio REPL.

A soft cap of roughly 300 lines per source file is applied; modules that grow
past it are split by responsibility into subpackages (this is why `cli/`,
`capture/atspi/`, `uia/`, and `gateway/` are subpackages rather than single
files).

## The capture seam

`cerebellum_cua.capture` defines the `CaptureBackend` abstract base class. A
backend implements two required methods:

- `is_available()` — whether the backend can actually run on the current host
  (OS present, libraries importable, live a11y bus reachable).
- `iter_tree(target, config)` — a generator yielding `(CapturedElement, depth,
  parent_key)` tuples in pre-order (parents before children).

and optionally overrides `invoke(element, action, **params)` to execute an
action on a live element.

`get_capture_backend(kind)` returns a backend by name; `kind="auto"` selects UIA
on Windows and AT-SPI on Linux. `available_backends()` returns the names of
backends that report themselves runnable on the current host. Backends are
imported lazily inside these functions so that importing `cerebellum_cua.capture`
never pulls in a Windows-only or Linux-only dependency.

Current backends:

- **uia_backend** (Windows) — wraps the `cerebellum_cua.uia` COM layer.
- **atspi** (Linux) — reads the AT-SPI2 tree through the GObject-Introspection
  `Atspi` bindings. Its `is_available()` probes the `org.a11y.Bus` address over
  D-Bus *without* calling `Atspi.init()`, because `Atspi.init()` against a dead
  bus can hard-abort the process.
- **macOS AX** — named as a future slot in the seam's contract; not implemented.

The `CapturedElement` dataclass carries an opaque `native_ref` (a COM element or
an `Atspi.Accessible`) used only by the backend for later action execution. It is
never persisted. `CapturedElement.control_type` is the canonical cross-platform
taxonomy: the integer values of `cerebellum_cua.model.ControlType` (UIA-derived).
Each backend maps its native roles into that taxonomy so the predicate and
semantics layers are uniform across platforms.

## Data flow

```
live a11y tree
   |
   |  CaptureBackend.iter_tree()  -> (CapturedElement, depth, parent_key), pre-order
   v
inclusion predicate            (atspi_should_include / uia should_include)
   |  drops scrollbars, 1x1 hit-test rects, offscreen / zero-sized / decorative nodes
   v
capture driver (walk_to_rows)  -> assigns dense 0-based row ids, resolves parent_key -> parent row_id
   v
matrix builder (build_snapshot)
   |  -> Snapshot: dense Element list
   |               + sparse Relationship edges (PARENT_OF always; FIRST_CHILD_OF /
   |                 NEXT_SIBLING_OF when cheaply derivable)
   |               + per-element children_stub counts
   |               + epoch number
   v
storage (persist_snapshot)     -> SQLite or PostgreSQL
   |
   |  semantic enrichment: match each element to domain concepts, write link rows
   v
accordion gateway              -> reads from storage only
   |  get_initial_context / get_element / load_children
   |  issues short-lived signed lazy tokens for expandable nodes
   v
JSONL protocol (stdio)         -> one response line per request line
   v
agent
```

The agent never receives the whole tree in one response. `build_matrix` returns
a small summary (snapshot id, epoch, element count, root row ids). The agent then
pulls nodes on demand with `get_element` and `load_children`, each of which
returns one node (or one node's direct children) plus a `children_stub` that
indicates whether further expansion is possible and carries the token to do it.

## The relational matrix and epoch-diff model

A `Snapshot` is an immutable, epoch-versioned capture of the matrix:

- **Elements** — a dense, 0-based list. Each `Element` has a `row_id` (its index,
  stable within the epoch), a `control_type`, name/class/automation id, a
  `bounding_rect`, raw `properties` and `patterns` maps, `is_interactive` /
  `is_content` flags, inferred `semantics`, a `children_stub`, and `metadata`
  (including its `depth`, `parent_row_id`, and a stable `composite_key`).
- **Relationships** — a sparse list of directed edges. Each `Relationship` has a
  `from_row_id`, `to_row_id`, a `relationship_code` (the `RelationshipCode` enum:
  1 = PARENT_OF, 2 = FIRST_CHILD_OF, 3 = NEXT_SIBLING_OF, 4 = PREVIOUS_SIBLING_OF,
  5 = LABELED_BY, 6 = LABEL_FOR, 7 = MEMBER_OF, 8 = CONTAINS_VIA_GEOMETRY,
  9 = SCROLLS, 10 = INVOKES), a `weight`, and `metadata`. The builder always
  emits PARENT_OF for each parent/child pair and adds FIRST_CHILD_OF or
  NEXT_SIBLING_OF when derivable from sibling order.

When the UI changes, a new snapshot is built with a fresh epoch. To avoid
re-sending the whole tree, `cerebellum_cua.matrix.diff_snapshots(old, new)`
computes the minimal delta. It matches elements across epochs by stable identity:

1. `uia_runtime_id_hash` when present (provider-stable while the session lives),
2. otherwise the composite key (name + class + control type + rounded rect +
   parent), which the builder stamps into each element's metadata.

The diff returns `added_row_ids`, `removed_row_ids`, `modified_row_ids`, and a
list of `patches`, where each patch carries the new `row_id` and a per-field
`{old, new}` change set over tracked scalar fields plus the bounding rect,
properties, and patterns. Added/modified ids are from the new snapshot; removed
ids are from the old one.

Note on the current diff path: `get_snapshot_diff` compares two snapshots held in
the engine's in-memory epoch history. It diffs full snapshots that were registered
in the running process; it does not re-read arbitrary historical snapshots from
storage.

## Stable element identity

Raw UIA COM pointers go stale after any UI change and must not be retained across
event boundaries. Identity is content-addressable: the composite key is a hash of
(name, class name, control type, rounded bounding rect, parent), computed in
`cerebellum_cua.matrix.identity`. The UIA layer additionally carries a
stale-element resolver that re-finds an element from its nearest live ancestor.
The diff layer relies on these keys to match rows across epochs.

## Token-bounded lazy loading

The gateway's accordion exposes the stored matrix as an expandable tree:

- `get_initial_context(snapshot_id)` hydrates the root subtree (depth <= 1).
- `get_element(snapshot_id, row_id)` returns one hydrated element, optionally
  with its relationships and semantics.
- `load_children(snapshot_id, parent_row_id, lazy_token, max_depth)` returns one
  node's direct children.

Each hydrated node that has children and has remaining depth budget carries a
`children_stub` with a freshly minted lazy token. A lazy token is an HS256-signed
JWT bound to a specific `(snapshot_id, parent_row_id)` pair with a 300-second TTL
(`{sid, pid, max_d, iat, exp}`). `load_children` validates both the JWT
signature/expiry/binding and the server-side token record before returning any
children. This bounds how much of the tree an agent can pull per call and ties
each expansion to the node it was issued for.
