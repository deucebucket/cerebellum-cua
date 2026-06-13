# Development log

Chronological, factual notes on how the project is being built and the decisions
behind it. Newest entries first.

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
