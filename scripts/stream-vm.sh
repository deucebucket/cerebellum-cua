#!/bin/bash
# Build (if needed) and run the isolated rig in live-streaming mode so you can
# WATCH the agent drive the virtual desktop in a browser or VNC client, without
# touching the real desktop. This is the live counterpart to scripts/run-vm.sh
# (which records and exits).
#
# It runs rig/session.sh with STREAM=1, publishes the noVNC + VNC ports bound to
# localhost only, and prints the URLs to open in a side window. The container
# stays running (so the agent can be driven while watched) until you stop it
# (Ctrl-C, or `podman stop`).
#
# Environment overrides (passed through to rig/session.sh):
#   APP             app to launch         (default: gedit)
#   SCREEN_SIZE     Xvfb geometry         (default: 1280x800x24)
#   VNC_PORT        x11vnc RFB port       (default: 5900)
#   NOVNC_PORT      noVNC web port        (default: 6080)
# Rig-local overrides:
#   IMAGE           image tag             (default: cerebellum-cua-rig)
#   REBUILD         set to 1 to force a rebuild
#
# Security: the ports are published on 127.0.0.1 only, so the stream is reachable
# from this host alone. The session is ephemeral — nothing is persisted and the
# stream ends when the container stops.
set -euo pipefail

# Resolve repo root from this script's location (scripts/ is one level down).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE="${IMAGE:-cerebellum-cua-rig}"
APP="${APP:-gedit}"
SCREEN_SIZE="${SCREEN_SIZE:-1280x800x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

# Build the image unless it already exists (or a rebuild is forced).
if [ "${REBUILD:-0}" = "1" ] || ! podman image exists "$IMAGE"; then
  echo "building image: $IMAGE"
  podman build -t "$IMAGE" -f "$REPO_ROOT/rig/Containerfile" "$REPO_ROOT/rig"
fi

echo "streaming rig: APP=$APP (noVNC :$NOVNC_PORT, VNC :$VNC_PORT)"
echo "open in a side window once it reports 'live stream up':"
echo "  noVNC (browser): http://127.0.0.1:${NOVNC_PORT}/vnc.html"
echo "  VNC (client):    127.0.0.1:${VNC_PORT}"

# Rootless-podman-friendly flags mirror run-vm.sh. The noVNC/VNC ports are
# published on 127.0.0.1 only so the stream never leaves this host. No host
# output dir is mounted: streaming produces no recording artifacts (logs stay
# in the container's /rig/out).
podman run --rm \
  --cgroup-manager=cgroupfs \
  --events-backend=file \
  --shm-size=256m \
  --security-opt label=disable \
  -p "127.0.0.1:${NOVNC_PORT}:${NOVNC_PORT}" \
  -p "127.0.0.1:${VNC_PORT}:${VNC_PORT}" \
  -v "$REPO_ROOT:/work:ro" \
  -e STREAM=1 \
  -e APP="$APP" \
  -e SCREEN_SIZE="$SCREEN_SIZE" \
  -e VNC_PORT="$VNC_PORT" \
  -e NOVNC_PORT="$NOVNC_PORT" \
  -e OUT=/rig/out \
  "$IMAGE" /work/rig/session.sh
