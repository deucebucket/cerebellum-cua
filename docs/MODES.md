# Execution modes and the virtual-desktop rig

`cerebellum-cua` drives a GUI session. The `--mode` flag on the CLI selects
*where* that session is and *how* actions appear in it, by choosing defaults for
two `CuaEngine` options: the capture backend (`capture_backend_kind`) and whether
actions glide a visible cursor first (`visible_cursor`).

## The three modes

| Mode         | Capture backend | Visible cursor | Session                                   |
|--------------|-----------------|----------------|-------------------------------------------|
| `desktop`    | `auto`          | on             | the real, logged-in desktop               |
| `vm`         | `atspi`         | on             | the isolated virtual session (with viewer)|
| `background` | `atspi`         | off            | the isolated virtual session (headless)   |

`desktop` is the default.

- **`desktop`** attaches to the real session. The backend is auto-selected (UIA
  on Windows, AT-SPI on Linux) and a visible cursor is moved so the automation
  looks user-operated. This needs the host accessibility enablement in
  [INSTALL.md](INSTALL.md) (the AT-SPI bus running and apps started after it on
  Linux; the `uia` extra on Windows).
- **`vm`** runs inside / against the isolated virtual session that
  `scripts/run-vm.sh` brings up (Xvfb + openbox + a session D-Bus + the AT-SPI
  bus). The AT-SPI backend is forced and the visible cursor stays on so a viewer
  attached to the virtual display (for example a VNC server pointed at the Xvfb
  display) shows realistic motion.
- **`background`** is the same isolated session run unattended: no viewer is
  attached and the visible cursor is off (no glide), since nothing is watching.

```bash
# real desktop (default)
python -m cerebellum_cua.cli --db-dsn ./state.db --secret "$SECRET"

# isolated session, viewer-friendly
python -m cerebellum_cua.cli --db-dsn ./state.db --secret "$SECRET" --mode vm

# isolated session, headless / unattended
python -m cerebellum_cua.cli --db-dsn ./state.db --secret "$SECRET" --mode background
```

The mapping lives in `cerebellum_cua/cli/modes.py` and is unit-tested in
`tests/unit/test_modes.py`.

## The virtual-desktop rig

The rig is a reproducible isolated desktop used by the `vm` and `background`
modes and by the recording harness. It is shell/Containerfile assets, not part of
the Python package:

```
rig/
  Containerfile     Ubuntu image: Xvfb + openbox + session D-Bus + AT-SPI bus,
                    plus ffmpeg, xdotool, tesseract/opencv, and gedit.
  session.sh        Brings up the virtual desktop, launches an app, records
                    video + screenshots, and optionally runs a demo script.
scripts/
  run-vm.sh         Build the image if needed and `podman run` the rig with the
                    repo mounted read-only at /work and an output dir at /rig/out.
  record-demo.sh    Run a given demo python in the rig and collect out/*.png and
                    out/*.mp4.
```

### Build and run

`scripts/run-vm.sh` builds the image (if it does not already exist) and runs the
session:

```bash
# build (first run) + run with defaults (launches gedit, records 16s)
scripts/run-vm.sh

# override the app, recording length, or display geometry
APP=gedit RECORD_SECONDS=20 SCREEN_SIZE=1920x1080x24 scripts/run-vm.sh

# force a rebuild of the image
REBUILD=1 scripts/run-vm.sh
```

The container is started with rootless-podman-friendly flags
(`--cgroup-manager=cgroupfs --events-backend=file --shm-size=256m
--security-opt label=disable`), the repo mounted read-only at `/work` (so
`PYTHONPATH=/work/src` finds the package without installing it), and a host
output directory mounted read-write at `/rig/out` for artifacts.

`session.sh` is parameterized via environment variables:

| Variable         | Default        | Meaning                                  |
|------------------|----------------|------------------------------------------|
| `APP`            | `gedit`        | app to launch in the virtual session     |
| `RECORD_SECONDS` | `16`           | ffmpeg capture duration (seconds)        |
| `DEMO`           | *(empty)*      | in-container path to a demo python to run |
| `OUT`            | `/rig/out`     | artifacts directory                      |
| `SCREEN_SIZE`    | `1280x800x24`  | Xvfb geometry (`WxHxDepth`)              |
| `APP_WARMUP`     | `4`            | seconds to wait after launching `APP`    |

### Record a demo

`scripts/record-demo.sh` runs a demo python (which must live inside the repo so
the read-only `/work` mount can see it) and then lists the produced screenshots
and video:

```bash
scripts/record-demo.sh path/to/demo.py            # artifacts -> rig/out/
scripts/record-demo.sh path/to/demo.py /tmp/out   # artifacts -> /tmp/out/
```

A demo is an ordinary script that constructs a `CuaEngine` (with
`capture_backend_kind="atspi"`) and drives it; running it under the rig records
the on-screen result to `out/demo.mp4` plus `out/before.png` / `out/after.png`.

### Attaching a viewer

To watch a `vm`-mode session, attach a VNC server to the container's virtual
display (the Xvfb display, `:99` by default) and connect a VNC client to it. The
`background` mode is intended to run without any viewer.
