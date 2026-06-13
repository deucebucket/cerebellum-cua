# Contributing

Thanks for your interest in `cerebellum-cua`. This document covers the
development setup, the checks a change must pass, and the project rules that
apply to all code in the repository.

## Development setup

Python 3.10+ is required.

```bash
git clone <repo-url>
cd cerebellum-cua
pip install -e '.[dev]'
```

The `dev` extra installs `pytest`, `pytest-cov`, `ruff`, and `mypy`. Add
`postgres` if you are working on the PostgreSQL backend (`pip install -e
'.[dev,postgres]'`). The `uia` extra is Windows-only and is not needed to work on
the platform-neutral layers.

## Running the checks

A change must pass these before it is ready:

```bash
pytest                                      # unit + integration tests
pytest -m "not windows and not postgres"    # what CI runs on Linux
ruff check src tests                        # lint (line-length 100)
mypy                                        # type-check
```

Two pytest markers gate platform-specific tests:

- `windows` — tests that need a live Windows UIA host.
- `postgres` — tests that need a running PostgreSQL instance.

Both **auto-skip** when their dependency is absent, so a plain `pytest` run on a
Linux box without Postgres skips them rather than failing. CI runs `pytest -m
"not windows and not postgres"` explicitly.

## Project rules

These apply to all code in the repository and are not optional.

1. **Roughly 300 lines per source file, as a hard cap.** When a module
   approaches it, split by responsibility into a subpackage. One file does one
   job, and its name should predict its contents.

2. **One responsibility per module.** Keep orchestration, persistence, COM
   access, and pure logic in separate modules.

3. **One-directional layering.** A module may import from layers below it, never
   from a layer above or sideways into a peer's internals. The order, top to
   bottom, is: `cli` → (`gateway`, `events`) → (`matrix`, `semantics`) →
   (`storage`, `capture`/`uia`) → (`model`, `config`, `errors`). The
   `model`/`config`/`errors` modules import nothing from other package modules.

4. **Capture stays behind the seam.** OS-specific imports (`uiautomation`,
   `comtypes`, `gi`/`Atspi`) are confined to the capture backends and the `uia`
   layer, and they are imported lazily so that importing the package never fails
   on the wrong OS. The matrix layer consumes plain dataclasses, never live COM
   or AT-SPI objects. Storage never calls into capture; the gateway never walks
   the live tree.

5. **The 10 UIA failure-mode workarounds are requirements, not optional
   hardening.** They live isolated in their own `uia/` modules (stale-handle
   resolver, stabilization, predicate, traversal, patterns, and the event
   manager/coalescer for the callback-storm case). Do not remove or weaken them.

6. **New modules ship with type hints, a module docstring, and unit tests.**

## Adding a new capture backend

Capture backends implement the `CaptureBackend` ABC in
`cerebellum_cua.capture.base`:

- `name` — a short stable identifier (e.g. `"uia"`, `"atspi"`).
- `is_available()` — return `True` only when the backend can actually run on the
  current host (OS, libraries, and any live bus all present). It must never
  trigger a process-level abort while probing.
- `iter_tree(target, config)` — yield `(CapturedElement, depth, parent_key)`
  tuples in pre-order (parents before children). Raise `CaptureNotAvailable` when
  the backend cannot run.
- Optionally override `invoke(element, action, **params)` to execute actions on a
  live element via its `native_ref`.

Then register the backend in `get_capture_backend()` (and the `available_backends()`
probe list) in `cerebellum_cua/capture/__init__.py`. Keep all OS-specific imports
lazy and inside methods so that importing the package and the seam stays safe on
every platform. Map the backend's native roles into the canonical
`cerebellum_cua.model.ControlType` taxonomy so the predicate and semantics layers
behave uniformly. Ship unit tests that exercise the mapping with a fake live
object (the existing AT-SPI backend tests are a model for this).

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
`fix:`, `docs:`, `refactor:`, `test:`, `chore:`, etc., with an optional scope,
e.g. `feat(capture): add macOS AX backend`.

## Pull requests

PRs must pass CI (lint and the Linux test matrix) before they can be merged. Keep
changes focused, include tests for new behavior, and update the relevant docs in
`docs/` when you change the protocol, the capture seam, or the install steps.
