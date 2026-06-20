#!/bin/bash
# Bring up an isolated X11 desktop inside the container, launch a target app,
# record video + screenshots, and (optionally) run a demo script against it.
#
# Everything is parameterized via environment variables so the same harness
# serves the `vm` and `background` execution modes and the record-demo flow:
#
#   APP             app to launch in the virtual session   (default: gedit)
#   RECORD_SECONDS  ffmpeg capture duration in seconds      (default: 16)
#   DEMO            path to a python demo to run, or empty  (default: empty)
#   OUT             directory for artifacts                  (default: /rig/out)
#   SCREEN_SIZE     Xvfb geometry WxHxDepth                  (default: 1280x800x24)
#   APP_WARMUP      seconds to wait after launching APP      (default: 4)
#   STREAM          set to 1 to live-stream instead of record (default: unset)
#   VNC_PORT        x11vnc RFB port (STREAM only)            (default: 5900)
#   NOVNC_PORT      noVNC web port  (STREAM only)            (default: 6080)
#
# Artifacts written to $OUT: xvfb.log, openbox.log, atspi.log, the app log,
# ffmpeg.log, before.png, after.png, demo.mp4, and (if a demo ran) demo.log.
#
# When STREAM=1 the record-and-exit path is replaced by a live stream: after the
# session and app are up, x11vnc + noVNC serve the display and the container
# stays running so the agent can be driven while watched. See rig/stream.sh.
set -u

APP="${APP:-gedit}"
RECORD_SECONDS="${RECORD_SECONDS:-16}"
DEMO="${DEMO:-}"
OUT="${OUT:-/rig/out}"
SCREEN_SIZE="${SCREEN_SIZE:-1280x800x24}"
APP_WARMUP="${APP_WARMUP:-4}"

# ffmpeg's x11grab wants the WxH part of the geometry, without the depth.
VIDEO_SIZE="${SCREEN_SIZE%x*}"
DISPLAY_NUM="${DISPLAY:-:99}"

# ---------------------------------------------------------------------------
# Dependency preflight: fail fast with explicit install guidance when a binary
# the rig needs is missing. The rig image (rig/Containerfile) bundles all of
# these; this check matters when session.sh is run directly on a host. Without
# it the session half-starts and dies deep in Xvfb/ffmpeg with no clear cause.
# ---------------------------------------------------------------------------
preflight() {
  # binary -> "what it provides | debian pkg | fedora pkg"
  local -a needed=(
    "Xvfb|virtual X display|xvfb|xorg-x11-server-Xvfb"
    "xdpyinfo|X readiness probe|x11-utils|xorg-x11-utils"
    "dbus-launch|session bus|dbus-x11|dbus-x11"
    "openbox|window manager|openbox|openbox"
    "ffmpeg|screen record/screenshot|ffmpeg|ffmpeg"
    "python3|demo runner|python3|python3"
  )
  if [ "${STREAM:-0}" = "1" ]; then
    needed+=(
      "x11vnc|VNC server (STREAM)|x11vnc|x11vnc"
      "websockify|noVNC web proxy (STREAM)|websockify|python3-websockify"
    )
  fi

  local missing=0 deb="" fed=""
  for spec in "${needed[@]}"; do
    IFS='|' read -r bin what dpkg fpkg <<<"$spec"
    if ! command -v "$bin" >/dev/null 2>&1; then
      echo "  missing: $bin ($what)" >&2
      deb="$deb $dpkg"; fed="$fed $fpkg"; missing=1
    fi
  done
  # at-spi-bus-launcher ships under libexec, not on PATH — check both.
  if [ ! -x /usr/libexec/at-spi-bus-launcher ] \
      && ! command -v at-spi-bus-launcher >/dev/null 2>&1; then
    echo "  missing: at-spi-bus-launcher (a11y bus)" >&2
    deb="$deb at-spi2-core"; fed="$fed at-spi2-core"; missing=1
  fi

  if [ "$missing" -eq 1 ]; then
    {
      echo "error: rig dependencies are missing on this host."
      echo "install (Debian/Ubuntu): sudo apt-get install -y$deb"
      echo "install (Fedora):        sudo dnf install -y$fed"
      echo "or run the rig in its container instead: scripts/run-vm.sh"
    } >&2
    exit 3
  fi
}
preflight

mkdir -p "$OUT"

# 1. virtual X display
Xvfb "$DISPLAY_NUM" -screen 0 "$SCREEN_SIZE" >"$OUT/xvfb.log" 2>&1 &
for _ in $(seq 1 50); do
  xdpyinfo -display "$DISPLAY_NUM" >/dev/null 2>&1 && break
  sleep 0.2
done
echo "Xvfb up on $DISPLAY_NUM ($SCREEN_SIZE)"

# 2. session bus + window manager + a11y bus
eval "$(dbus-launch)"
export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID
openbox >"$OUT/openbox.log" 2>&1 &
/usr/libexec/at-spi-bus-launcher --launch-immediately >"$OUT/atspi.log" 2>&1 &
sleep 1
echo "session bus + openbox + a11y bus up"

# 3. launch the target app (started after the a11y bus so it exposes a tree)
"$APP" >"$OUT/app.log" 2>&1 &
sleep "$APP_WARMUP"
echo "launched app: $APP"

# 3a. STREAM path: serve the live display for watching and keep running.
# When STREAM is unset, fall through to the record-and-exit path below.
if [ "${STREAM:-0}" = "1" ]; then
  STREAM_SH="$(dirname "${BASH_SOURCE[0]}")/stream.sh"
  [ -f "$STREAM_SH" ] || STREAM_SH=/rig/stream.sh
  # shellcheck source=/dev/null
  . "$STREAM_SH"
  start_stream
  exit $?
fi

# 4. record the desktop while the demo runs
ffmpeg -y -f x11grab -video_size "$VIDEO_SIZE" -framerate 12 -i "$DISPLAY_NUM" \
  -t "$RECORD_SECONDS" "$OUT/demo.mp4" >"$OUT/ffmpeg.log" 2>&1 &
FFPID=$!

# screenshot helper: single-frame x11grab to a PNG under $OUT
shot() {
  ffmpeg -y -f x11grab -video_size "$VIDEO_SIZE" -i "$DISPLAY_NUM" \
    -frames:v 1 "$OUT/$1" >/dev/null 2>&1
}
shot before.png
sleep 1

# 5. run the demo (if one was supplied), else just hold for the recording
if [ -n "$DEMO" ] && [ -f "$DEMO" ]; then
  echo "running demo: $DEMO"
  python3 "$DEMO" 2>&1 | tee "$OUT/demo.log"
else
  echo "no demo supplied; idling for the recording window"
  sleep "$RECORD_SECONDS"
fi
shot after.png

wait "$FFPID" 2>/dev/null
echo "=== artifacts ==="
ls -la "$OUT"
