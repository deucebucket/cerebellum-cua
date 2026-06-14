#!/bin/bash
# Live-stream the rig's virtual X display so it can be WATCHED in a browser or a
# VNC client while the agent drives it. This is the live counterpart to the
# record-and-exit path in session.sh.
#
# session.sh sources/calls this only when STREAM=1. It assumes Xvfb, the session
# D-Bus, openbox, the AT-SPI bus, and the target app are already up on $DISPLAY.
#
# It serves the display two ways:
#   - x11vnc      -> raw VNC on ${VNC_PORT} (bound to localhost inside the container)
#   - websockify  -> noVNC web client on ${NOVNC_PORT}, proxying to the VNC port
#
# Both processes run in the foreground-friendly background; this function blocks
# at the end so the container stays alive (the agent can be driven while watched)
# until the stream is stopped.
#
#   DISPLAY      X display to serve                        (default: :99)
#   OUT          directory for logs                        (default: /rig/out)
#   VNC_PORT     x11vnc RFB port                           (default: 5900)
#   NOVNC_PORT   noVNC / websockify web port              (default: 6080)
#
# Logs: $OUT/x11vnc.log, $OUT/novnc.log

# start_stream: bring up x11vnc + noVNC against an already-running display and
# block to keep the session alive for live viewing.
start_stream() {
  local display="${DISPLAY:-:99}"
  local out="${OUT:-/rig/out}"
  local vnc_port="${VNC_PORT:-5900}"
  local novnc_port="${NOVNC_PORT:-6080}"

  mkdir -p "$out"

  # Serve the X display over VNC. -localhost keeps x11vnc bound to the loopback
  # interface inside the container; the host only ever reaches it through the
  # localhost-published podman ports. -nopw is acceptable because exposure is
  # localhost-only and the session is ephemeral.
  x11vnc -display "$display" -forever -shared -nopw -localhost \
    -rfbport "$vnc_port" >"$out/x11vnc.log" 2>&1 &
  local vnc_pid=$!

  # Wait for x11vnc to start listening before launching the web proxy.
  for _ in $(seq 1 50); do
    if grep -q "PORT=$vnc_port" "$out/x11vnc.log" 2>/dev/null; then
      break
    fi
    sleep 0.2
  done
  echo "x11vnc serving $display on VNC port $vnc_port (localhost)"

  # noVNC web client. websockify ships the bundled noVNC web root via --web so a
  # browser can open http://<host>:<novnc_port>/vnc.html. Bind to all interfaces
  # inside the container; the host side is constrained to localhost by the
  # published port mapping in scripts/stream-vm.sh.
  local web_root=""
  for cand in /usr/share/novnc /usr/share/webapps/novnc; do
    if [ -d "$cand" ]; then
      web_root="$cand"
      break
    fi
  done

  if [ -n "$web_root" ]; then
    websockify --web "$web_root" "$novnc_port" "localhost:$vnc_port" \
      >"$out/novnc.log" 2>&1 &
  else
    # Fall back to a bare websockify proxy (no web root); a VNC client can still
    # connect to the VNC port directly.
    echo "noVNC web root not found; serving websockify proxy only" >&2
    websockify "$novnc_port" "localhost:$vnc_port" >"$out/novnc.log" 2>&1 &
  fi
  local novnc_pid=$!

  echo "=== live stream up ==="
  echo "  noVNC (browser): http://127.0.0.1:${novnc_port}/vnc.html"
  echo "  VNC (client):    127.0.0.1:${vnc_port}"
  echo "  (localhost-bound; ephemeral — stops when the container stops)"
  echo "watching display $display; Ctrl-C / podman stop to end the session"

  # Block on the streamers so the container keeps running and the agent can be
  # driven live. If either dies, fall through so the container exits.
  wait "$vnc_pid" "$novnc_pid"
}
