# Roadmap

Tracked future work that is intentionally **not** implemented yet, kept here so the
issue tracker stays focused on actionable items. These are not defects; they are
extensions that require hardware or environments not available to this project's
automated CI.

## Platform validation

- **Windows / UI Automation** — **validated on real Windows 11** (build 26200): the
  `uia` backend captures the live interactive desktop (184 elements across the
  taskbar, Chrome, and Explorer, with correct control-type mapping and interactivity
  flags). Note: UIA is per-session, so a capture process must run in the user's
  interactive session (not a service/SSH session-0 context). Still open for Windows:
  the UAC elevation path and broader app coverage / Windows-marked integration tests.
- **macOS / Accessibility (AX)** — no macOS capture backend yet. The capture seam
  is OS-neutral (`uia`, `atspi` today), so an `ax` backend can be added against the
  same `CaptureBackend` interface, mapping AX roles into the canonical control-type
  taxonomy. Needs a macOS host to build and verify.

## Enhancements (buildable on Linux; deferred)

- Codec-level motion signal for the media pipeline (read H.264/H.265 motion vectors
  and I-frames via `ffmpeg -flags2 +export_mvs` instead of frame differencing).
- Optional local vision model (e.g. an OmniParser/YOLO-style detector) for richer
  icon/control detection in the `vision` backend, on supported GPUs.
- KWin/Wayland window enumeration for the desktop window-state source (currently a
  documented no-op on Wayland; needs a loaded KWin script walking `windowList()`).
- Landmark/alias pinning for the cipher legend (user-pinned names re-verified live).

## How to contribute one of these

The capture seam (`cerebellum_cua/capture/`) and the operation/handler pattern make
most of these additive. See [CONTRIBUTING.md](CONTRIBUTING.md) for the module rules
and how to add a capture backend.
