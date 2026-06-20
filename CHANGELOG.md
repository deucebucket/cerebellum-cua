# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Focused (region/element) screenshots**: the `screenshot` op + MCP tool accept
  `region=[x,y,w,h]` or `row_id` (crop to an element's bbox) for far cheaper
  "look at just this widget" captures; full-screen behaviour is unchanged.
- **Self-recorded captioned demo** (`docs/assets/cua-drive.mp4`/`.gif` + editable
  `clips/`): cerebellum-cua drives gedit (type → focused shot → menu → click →
  read) via the a11y tree, with per-step three-way token captions (a11y matrix vs
  focused shot vs full screenshot, ~12× cheaper) and a closing total — recorded by
  the rig running CUA. Tutorial runner/captions gained per-step token + perceived
  + bbox instrumentation, paced holds, and a clip planner (`tutorial.clips`).
- Token-budget accounting in the gateway (`estimated_tokens`, optional ceiling,
  `TokenBudgetExceededError` 1009). (#1)
- MCP server (`cerebellum_cua.mcp`, `cerebellum-cua-mcp`, `[mcp]` extra). (#2)
- Action execution: AT-SPI/UIA invoke/set_text/toggle/select/set_value/expand,
  coordinate click/type/key, and element re-acquisition after a DB round-trip. (#3)
- Human-visible cursor motion (glide, paced typing) and a user-takeover
  kill-switch (evdev) that aborts in-progress input. (#11, #12)
- Optional on-demand `screenshot` operation (hybrid perception). (#19)
- `vision` capture backend: screenshot → structured elements (OCR + OpenCV),
  behind the same seam as `uia`/`atspi`. (#21)
- `read_text` + AT-SPI Text-buffer capture (on-screen text into coordinates). (#26)
- Drag/scroll input and an opt-in action-verification loop (act → re-capture →
  diff → `verified`/`effect`). (#25)
- Skills layer (`run_skill`: resolve → act → verify) — click/type_into/open/etc. (#27)
- `--mode {desktop,vm,background}` and an in-repo VM rig (`rig/`, `scripts/`). (#13)
- Authoritative window-state source (`list_windows`; X11 backends). (#23)
- Compact cipher legend (`read_legend`) + composite/annotated views
  (`annotate`, `wireframe`). (#22, #28)
- Token benchmarks (`scripts/benchmark_tokens.py`, `docs/BENCHMARKS.md`). (#8)
- Adjacent media pipeline (motion/scene → cut-list → xfade). (#29)
- One-time Linux setup (SELinux fix + scoped sudoers) + `.env` config. (#4)
- Cross-platform elevation (polkit/sudo/UAC; password from `.env`). (#39)
- PyPI packaging (schema shipped in the wheel) + release workflow. (#7)
- Live VNC/noVNC streaming of the VM session. (#34)
- Tutorial generation (scripted steps + on-screen captions). (#35)

### Changed
- **MCP server now exposes every engine operation at parity with JSONL** (13
  tools, up from 5): adds `list_windows`, `screenshot`, `read_text`, `run_skill`,
  `read_legend`, `wireframe`, `annotate`, and `elevate`, and expands
  `invoke_action` to the full control surface (coordinate `click_point`/`drag`/
  `scroll`/`type`/`key` and `verify`, not just `row_id`). Every tool now carries a
  workflow-oriented description (previously the registered descriptions were
  empty), so an agent can use the surface without reading external docs.
- **UIA backend ported to the real `uiautomation` library API** (was written
  against raw IUIAutomation COM that does not exist in the library). **Validated
  on real Windows 11**: captures the live interactive desktop (184 elements,
  correct control-type mapping). (#5)
- `build_matrix` in `auto` mode now degrades to the `vision` backend when the
  OS-default a11y backend is unavailable and vision is usable, instead of
  hard-failing; the response reports the `capture_backend` actually used and a
  `degraded` flag. A pinned `capture_backend` never silently degrades. The
  `capture_unavailable` (1006) message now states exact per-backend
  remediation. (#50)
- `build_matrix` now attaches a `diagnostics` object whenever a capture yields
  `total_elements: 0`, explaining the cause (`atspi_registry_empty`,
  `no_root_matched_target`, `all_elements_filtered`, or `no_elements`) with
  remediation, so an empty a11y tree is no longer mistaken for a blank screen.
  The AT-SPI backend records how many apps the registry exposed vs. matched the
  target to make the reason accurate.

### Fixed
- **`screenshot` no longer returns a silent all-black "success"** (issue #55): a
  full-screen grab that decodes to pure black is rejected with a typed `1006`
  (under a Wayland compositor an X11 root grab is black). Wayland is now detected
  via `WAYLAND_DISPLAY` even when `XDG_SESSION_TYPE` is unset, and a new
  `window_id` scope captures one X11/Xwayland window's real pixels
  (`import -window <id>`) — the reliable path under KWin/Wayland. This also stops
  the vision `build_matrix` from OCRing a black frame into an empty matrix.
- **Coordinate/raw synthetic input no longer core-dumps the engine** when the
  AT-SPI registry is unreachable/broken (`dbind` C-level abort, uncatchable —
  issue #54). `Atspi.generate_*_event` is now opt-in (`use_atspi_input` /
  `CEREBELLUM_ATSPI_INPUT=1`); coordinate/key actions default to a CLI tool, with
  a new **`xdotool` (X11)** path alongside `ydotool` (Wayland), chosen by display
  server.
- Skill `click`/`open`/`focus` hard-failed (`reacquire_failed`, 1006) on ephemeral
  popover/menu items even when the element's box was freshly captured; they now
  fall back to a coordinate click at the element's bbox centre, and `type_into`
  recovers the same way — making menu/popover/dynamic navigation reliable. Skill
  results now also carry `resolved_role`/`resolved_name`/`resolved_bbox`.
- SQLite `sqlite://` DSN parsing stripped *every* leading slash, making absolute
  paths impossible (`sqlite:////abs.db` crashed; `sqlite:///abs.db` silently
  went relative). Now follows the SQLAlchemy convention:
  `sqlite:////abs.db` → `/abs.db`, `sqlite:///rel.db` → `rel.db`. (#49)
- The `vision` backend returned `status: success` with `total_elements: 0` when
  OpenCV/Tesseract were missing, masking the failure. It now raises an explicit
  `1006` with the exact missing dependencies. (#51)
- `rig/session.sh` now preflights its runtime binaries (Xvfb, openbox,
  at-spi-bus-launcher, ffmpeg, and x11vnc/websockify under `STREAM=1`) and fails
  fast with per-distro install commands; `scripts/run-vm.sh` checks for
  `podman`. Required system packages are documented in the README. (#52)

## [0.1.0] - 2026-06-13

Initial release. Implements the capture engine, storage, matrix model, gateway,
and JSONL protocol (wire version 4.2). Live capture has been exercised
end-to-end on Linux; it is not yet validated against a broad set of real
applications, and the Windows/UIA path is untested on real Windows.

### Added
- **Capture seam** (`cerebellum_cua.capture`): OS-neutral `CaptureBackend`
  interface with a driver that assigns dense matrix row ids; `get_capture_backend`
  auto-selects by OS; `available_backends()` probe.
  - `uia` backend (Windows UI Automation) wrapping the UIA layer.
  - `atspi` backend (Linux AT-SPI) with role/state mapping; degrades gracefully
    when the a11y bus is unavailable (no process abort).
- **UIA layer** (`cerebellum_cua.uia`): the `should_include` predicate, FindAll
  traversal, pattern extraction, and workarounds for the documented UI Automation
  tree failure modes (stale references, virtualized containers, non-unique ids,
  recursion limits, missing patterns, browser content, proxy reparenting, access
  denial, cached-value staleness, event-handler leaks).
- **Matrix model** (`cerebellum_cua.matrix`): snapshot builder, stable
  content-addressable element identity, and epoch diffing.
- **Storage** (`cerebellum_cua.storage`): `StorageBackend` interface with SQLite
  (default) and PostgreSQL implementations, plus the canonical SQL schema.
- **Semantics** (`cerebellum_cua.semantics`): heuristic control-type → domain
  concept mappings and a rule evaluator.
- **Gateway** (`cerebellum_cua.gateway`): accordion lazy-loading, JWT lazy
  tokens, and the JSONL protocol/dispatch layer.
- **Events** (`cerebellum_cua.events`): event-handler manager (remove-before-add)
  and a debounce coalescer for structure-change bursts.
- **CLI** (`cerebellum_cua.cli`): the `CuaEngine` composition root, the JSONL
  stdio REPL, and the `cerebellum-cua` console entry point. Operations:
  `build_matrix`, `get_element`, `load_children`, `invoke_action`,
  `get_snapshot_diff`.
- Unit test suite, `ruff` configuration, and CI.

### Known gaps (tracked as issues)
- Live capture is unverified against a wide range of real applications.
- `invoke_action` re-acquisition of a stored element is backend-incomplete.
- No macOS AX backend yet; not yet published to PyPI.

[Unreleased]: https://github.com/deucebucket/cerebellum-cua/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deucebucket/cerebellum-cua/releases/tag/v0.1.0
