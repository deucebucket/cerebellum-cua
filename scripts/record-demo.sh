#!/bin/bash
# Run a demo python script inside the isolated rig and collect the recording.
#
# Usage:
#   scripts/record-demo.sh <demo.py> [output-dir]
#
# <demo.py> is a path on the host, inside the repo; it is made visible in the
# container via the read-only /work mount and run by rig/session.sh while the
# session is recorded. After the run, the produced screenshots (out/*.png) and
# video (out/*.mp4) are listed from the output directory.
#
# Environment overrides are forwarded to run-vm.sh (APP, RECORD_SECONDS,
# SCREEN_SIZE, IMAGE, REBUILD).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <demo.py> [output-dir]" >&2
  exit 2
fi

DEMO_HOST="$1"
OUT_DIR="${2:-$REPO_ROOT/rig/out}"

if [ ! -f "$DEMO_HOST" ]; then
  echo "demo script not found: $DEMO_HOST" >&2
  exit 2
fi

# Translate the host path into its in-container path under /work. The demo must
# live inside the repo so the read-only mount exposes it.
DEMO_ABS="$(cd "$(dirname "$DEMO_HOST")" && pwd)/$(basename "$DEMO_HOST")"
case "$DEMO_ABS" in
  "$REPO_ROOT"/*)
    DEMO_IN_CONTAINER="/work/${DEMO_ABS#"$REPO_ROOT"/}"
    ;;
  *)
    echo "demo must be inside the repo ($REPO_ROOT) to be visible at /work" >&2
    exit 2
    ;;
esac

mkdir -p "$OUT_DIR"

echo "recording demo: $DEMO_IN_CONTAINER -> $OUT_DIR"
DEMO="$DEMO_IN_CONTAINER" OUT_DIR="$OUT_DIR" "$SCRIPT_DIR/run-vm.sh"

echo "=== collected artifacts ==="
ls -la "$OUT_DIR"/*.png "$OUT_DIR"/*.mp4 2>/dev/null || echo "(no png/mp4 produced)"
