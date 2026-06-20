#!/bin/bash
# Build (if needed) and run the isolated virtual-desktop rig with podman.
#
# Mounts the repo read-only at /work (so PYTHONPATH=/work/src finds the package
# without installing it) and a host output directory read-write at /rig/out for
# the recording artifacts, then runs rig/session.sh inside the container.
#
# Environment overrides (passed through to rig/session.sh):
#   APP             app to launch         (default: gedit)
#   RECORD_SECONDS  capture duration      (default: 16)
#   DEMO            in-container demo path (default: empty)
#   SCREEN_SIZE     Xvfb geometry         (default: 1280x800x24)
# Rig-local overrides:
#   IMAGE           image tag             (default: cerebellum-cua-rig)
#   OUT_DIR         host artifacts dir    (default: <repo>/rig/out)
#   REBUILD         set to 1 to force a rebuild
#
# A viewer (e.g. VNC) can be attached to the container's virtual display to
# watch the session; this script itself runs headless.
set -euo pipefail

# Resolve repo root from this script's location (scripts/ is one level down).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# The host only needs podman; the image bundles the rest (rig/session.sh
# preflights its own runtime deps inside the container).
if ! command -v podman >/dev/null 2>&1; then
  echo "error: 'podman' is not installed. Install it (Debian/Ubuntu: " \
       "'sudo apt-get install -y podman'; Fedora: 'sudo dnf install -y podman')," \
       "or run rig/session.sh directly on a host with the rig deps installed." >&2
  exit 3
fi

IMAGE="${IMAGE:-cerebellum-cua-rig}"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/rig/out}"
APP="${APP:-gedit}"
RECORD_SECONDS="${RECORD_SECONDS:-16}"
DEMO="${DEMO:-}"
SCREEN_SIZE="${SCREEN_SIZE:-1280x800x24}"

mkdir -p "$OUT_DIR"

# Build the image unless it already exists (or a rebuild is forced).
if [ "${REBUILD:-0}" = "1" ] || ! podman image exists "$IMAGE"; then
  echo "building image: $IMAGE"
  podman build -t "$IMAGE" -f "$REPO_ROOT/rig/Containerfile" "$REPO_ROOT/rig"
fi

echo "running rig: APP=$APP RECORD_SECONDS=$RECORD_SECONDS DEMO=${DEMO:-<none>}"
# Rootless-podman-friendly flags: cgroupfs manager, file events backend, a
# shared-memory bump for X/Chromium, and label=disable so the read-only repo
# mount is readable under SELinux without relabeling the source tree.
podman run --rm \
  --cgroup-manager=cgroupfs \
  --events-backend=file \
  --shm-size=256m \
  --security-opt label=disable \
  -v "$REPO_ROOT:/work:ro" \
  -v "$OUT_DIR:/rig/out" \
  -e APP="$APP" \
  -e RECORD_SECONDS="$RECORD_SECONDS" \
  -e DEMO="$DEMO" \
  -e SCREEN_SIZE="$SCREEN_SIZE" \
  -e OUT=/rig/out \
  "$IMAGE" /work/rig/session.sh

echo "artifacts in: $OUT_DIR"
