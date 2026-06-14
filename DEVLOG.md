# Development log

Chronological, factual notes on how the project is being built and the decisions
behind it. Newest entries first.

## 2026-06-14 — capabilities, hybrid perception, and platform validation

**Action + verification.** Added real control on top of capture: AT-SPI/UIA
invoke/set_text/toggle/select/set_value/expand, coordinate click/type/key/drag/
scroll, element re-acquisition after a DB round-trip, human-visible cursor motion,
a user-takeover kill-switch (evdev), and an opt-in act→re-capture→diff verification
loop. A skills layer (`run_skill`) composes resolve→act→verify into named commands.

**Hybrid perception, one seam.** Kept the accessibility tree as the token-cheap
default and added: an opt-in `screenshot` op; a `vision` capture backend
(screenshot → structured elements via OCR + OpenCV) behind the same
`CaptureBackend` interface as `uia`/`atspi`; full on-screen text into coordinates
(`read_text` + AT-SPI Text buffer); an authoritative window-state source
(`list_windows`); a compact cipher legend and annotated/wireframe composites.
Decision: structured representations (a11y/OCR/window-state) are token-cheap and
the workhorse; raw pixels only via the explicit `screenshot` op; an adjacent media
pipeline handles video via metadata/motion/cut-lists, never frame-by-frame.

**Execution modes + observability.** `--mode {desktop,vm,background}`, an in-repo
container VM rig (`rig/`, `scripts/`) for reproducible isolated runs and recording,
live VNC/noVNC streaming, and tutorial generation with on-screen captions.

**Productization.** Token benchmarks with real numbers (initial context stays flat
~91 tokens from 10→5000 elements), one-time Linux setup (SELinux fix + scoped
sudoers so automation never stalls), cross-platform elevation (polkit/sudo/UAC,
secret from `.env`), and PyPI packaging (SQL schema shipped in the wheel) + a
trusted-publishing release workflow.

**Platform validation.** Linux/AT-SPI proven end-to-end (captured a real desktop
and drove gedit via a skill). **Windows/UIA validated on real Windows 11**: this
surfaced that the UIA layer had been written against raw IUIAutomation COM names
absent from the `uiautomation` library — it connected but crashed on the first tree
walk. Ported all six uia modules to the real API and re-validated live: it captured
the interactive desktop (184 elements, correct control-type mapping). The unit-test
mocks had mirrored the wrong API and were rewritten to the real `Control` surface —
a reminder that mocked tests only verify against the shape you give them.

## 2026-06-13 — v0.1.0 scaffold

**Goal.** A capture/serving layer that turns the OS accessibility tree into a
versioned relational matrix and exposes it to agents over JSONL, without
screenshots.

**Architecture decision: unified core, pluggable capture + storage.** Rather than
two parallel implementations, the codebase is one core with two seams:
- **Storage** is an interface with SQLite (dev/default) and PostgreSQL backends.
- **Capture** is an interface with `uia` (Windows) and `atspi` (Linux) backends,
  selected by OS. The matrix/gateway/protocol layers consume normalized
  dataclasses and never touch OS-specific APIs, so the bulk of the system is
  testable on any host.

**Module discipline.** Hard ~300-line cap per source file and one responsibility
per module, enforced as a project rule. The largest source file is ~275 lines.

**Linux capture (AT-SPI) findings.** AT-SPI is the Linux equivalent of Windows UI
Automation. On a KDE/Wayland session the bindings (`gi` / `Atspi 2.0`) are present
and the engine connects and walks the tree end-to-end. Two real constraints were
observed and are documented in `docs/INSTALL.md`:
1. On SELinux-enforcing immutable distributions, D-Bus activation of
   `at-spi2-registryd` can be denied; the daemons may need a policy allowance or a
   user service.
2. Applications only expose their tree if accessibility was enabled when they
   started, so captures of apps started without it come back empty.
The `atspi` backend is written to degrade gracefully in both cases (it returns an
empty/`unavailable` result rather than aborting the process).

**Control-type taxonomy.** The canonical taxonomy uses the real Microsoft UI
Automation `ControlType` integer constants. The `atspi` backend maps AT-SPI roles
into this taxonomy so semantics and predicates are uniform across backends.

**Spec fidelity.** The wire protocol is labeled v4.2. Token-budget accounting on
responses (so the gateway can enforce an explicit per-response ceiling) is the
first tracked follow-up; the accordion already bounds response size by capping
children per expansion and lazy-loading deeper levels.

**Testing.** Unit tests cover storage round-trips, the predicate, matrix
build/identity/diff, semantics, gateway/accordion/protocol, events, the capture
backends (with mock trees), and the engine end-to-end over the JSONL protocol.
Windows- and Postgres-dependent tests are marked and auto-skip where the
dependency is absent.
