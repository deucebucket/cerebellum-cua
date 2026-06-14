#!/bin/bash
# One-time privileged Linux enablement for cerebellum-cua.
#
# Run ONCE, by a human, with sudo. It makes the AT-SPI accessibility bus work
# under SELinux Enforcing and installs a tightly-scoped sudoers allow-list so an
# automated agent never stalls on an interactive sudo prompt afterward.
#
# It does three guarded, idempotent things:
#   1. SELinux a11y fix — build a policy module from recent atspi denials with
#      audit2allow; if none are found, fall back to making the gnome_atspi_t
#      domain permissive. Global Enforcing is preserved either way.
#   2. Accessibility enablement — set the GNOME toolkit-accessibility key if the
#      schema exists, and always print the KDE/Qt guidance (this host is KDE).
#   3. Scoped sudoers — install packaging/sudoers/cerebellum-cua.template to
#      /etc/sudoers.d/cerebellum-cua (0440), validated with `visudo -cf`.
#
# Usage:
#   sudo bash scripts/setup-linux.sh            apply everything (idempotent)
#   sudo bash scripts/setup-linux.sh --check    report status, change nothing
#   sudo bash scripts/setup-linux.sh --uninstall remove sudoers + SELinux fix
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SUDOERS_DST="/etc/sudoers.d/cerebellum-cua"
SUDOERS_TPL="$REPO_ROOT/packaging/sudoers/cerebellum-cua.template"
POLICY_DIR="/etc/cerebellum"
POLICY_TE="$REPO_ROOT/packaging/selinux/cerebellum_atspi.te"
MODULE_NAME="cerebellum_atspi"
A11Y_DOMAIN="gnome_atspi_t"

MODE="apply"  # apply | check | uninstall
case "${1:-}" in
  --check) MODE="check" ;;
  --uninstall) MODE="uninstall" ;;
  "") MODE="apply" ;;
  *) echo "usage: $0 [--check|--uninstall]" >&2; exit 2 ;;
esac

log() { printf '[setup-linux] %s\n' "$*"; }

# The invoking user, even under sudo. This is who gets the NOPASSWD allow-list.
TARGET_USER="${SUDO_USER:-$USER}"

require_root() {
  if [ "$(id -u)" -ne 0 ] && [ "$MODE" != "check" ]; then
    echo "error: $MODE must run as root (use sudo)." >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# 1. SELinux a11y fix
# ---------------------------------------------------------------------------
selinux_status() {
  if ! command -v getenforce >/dev/null 2>&1; then
    log "SELinux: getenforce not found — SELinux not present, skipping a11y fix."
    return 1
  fi
  log "SELinux mode: $(getenforce)"
  return 0
}

selinux_apply() {
  selinux_status || return 0
  if semodule -l 2>/dev/null | grep -qx "$MODULE_NAME"; then
    log "SELinux: module '$MODULE_NAME' already installed — nothing to do."
    return 0
  fi
  if semanage permissive -l 2>/dev/null | grep -qx "$A11Y_DOMAIN"; then
    log "SELinux: '$A11Y_DOMAIN' already permissive — nothing to do."
    return 0
  fi

  # Preferred: build a minimal module from the host's own recent denials.
  local workdir built=0
  workdir="$(mktemp -d)"
  if command -v ausearch >/dev/null 2>&1 && command -v audit2allow >/dev/null 2>&1; then
    log "SELinux: scanning recent atspi denials with ausearch/audit2allow..."
    if ausearch -m avc -ts recent 2>/dev/null | grep -i atspi \
        | audit2allow -M "$workdir/$MODULE_NAME" >/dev/null 2>&1 \
        && [ -f "$workdir/$MODULE_NAME.pp" ]; then
      mkdir -p "$POLICY_DIR"
      install -m 0644 "$workdir/$MODULE_NAME.pp" "$POLICY_DIR/$MODULE_NAME.pp"
      semodule -i "$POLICY_DIR/$MODULE_NAME.pp"
      log "SELinux: installed generated module '$MODULE_NAME' (global Enforcing kept)."
      built=1
    else
      log "SELinux: no recent atspi denials to build from."
    fi
  else
    log "SELinux: ausearch/audit2allow unavailable; skipping module build."
  fi
  rm -rf "$workdir"

  # Fallback: make only the a11y domain permissive (targeted, persistent).
  if [ "$built" -eq 0 ]; then
    if command -v semanage >/dev/null 2>&1; then
      log "SELinux: falling back to 'semanage permissive -a $A11Y_DOMAIN'."
      semanage permissive -a "$A11Y_DOMAIN"
      log "SELinux: '$A11Y_DOMAIN' is now permissive (global Enforcing kept)."
    else
      log "SELinux: semanage unavailable — cannot apply a11y fix. Install policycoreutils."
    fi
  fi
}

selinux_uninstall() {
  command -v getenforce >/dev/null 2>&1 || return 0
  if semodule -l 2>/dev/null | grep -qx "$MODULE_NAME"; then
    log "SELinux: removing module '$MODULE_NAME'."
    semodule -r "$MODULE_NAME" || true
  fi
  rm -f "$POLICY_DIR/$MODULE_NAME.pp"
  rmdir "$POLICY_DIR" 2>/dev/null || true
  if command -v semanage >/dev/null 2>&1 \
      && semanage permissive -l 2>/dev/null | grep -qx "$A11Y_DOMAIN"; then
    log "SELinux: removing permissive entry for '$A11Y_DOMAIN'."
    semanage permissive -d "$A11Y_DOMAIN" || true
  fi
}

selinux_check() {
  selinux_status || return 0
  if semodule -l 2>/dev/null | grep -qx "$MODULE_NAME"; then
    log "SELinux: module '$MODULE_NAME' INSTALLED."
  elif command -v semanage >/dev/null 2>&1 \
      && semanage permissive -l 2>/dev/null | grep -qx "$A11Y_DOMAIN"; then
    log "SELinux: '$A11Y_DOMAIN' is PERMISSIVE."
  else
    log "SELinux: no cerebellum a11y fix applied yet."
  fi
}

# ---------------------------------------------------------------------------
# 2. Accessibility enablement (GNOME key best-effort; KDE/Qt guidance always)
# ---------------------------------------------------------------------------
a11y_guidance() {
  cat <<'EOF'
[setup-linux] Accessibility (desktop-agnostic) guidance:
  * Ensure at-spi2-core is installed (provides the org.a11y.Bus and registry).
  * This host runs KDE on Wayland: the GNOME 'toolkit-accessibility' gsettings
    key may NOT apply to Qt/KDE apps. For Qt apps export QT_ACCESSIBILITY=1
    before launching them so they publish their AT-SPI trees.
  * The a11y bus is D-Bus-activated on demand; confirm it is reachable with:
      gdbus call --session --dest org.a11y.Bus --object-path /org/a11y/bus \
        --method org.a11y.Bus.GetAddress
  * Apps only expose trees if they were STARTED AFTER the bus came up.
EOF
}

a11y_apply() {
  if command -v gsettings >/dev/null 2>&1 \
      && gsettings list-schemas 2>/dev/null | grep -qx "org.gnome.desktop.interface"; then
    log "GNOME schema present: setting toolkit-accessibility true (GNOME/GTK apps)."
    # Run as the target user so it lands in their dconf, not root's.
    if [ -n "${SUDO_USER:-}" ] && command -v runuser >/dev/null 2>&1; then
      runuser -u "$SUDO_USER" -- gsettings set org.gnome.desktop.interface \
        toolkit-accessibility true 2>/dev/null || \
        log "GNOME: could not set key for $SUDO_USER (no session bus?) — skipping."
    else
      gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null || \
        log "GNOME: could not set key (no session bus?) — skipping."
    fi
  else
    log "GNOME desktop.interface schema not found — skipping toolkit-accessibility key."
  fi
  a11y_guidance
}

# ---------------------------------------------------------------------------
# 3. Scoped sudoers drop-in
# ---------------------------------------------------------------------------
sudoers_apply() {
  if [ ! -f "$SUDOERS_TPL" ]; then
    echo "error: sudoers template missing: $SUDOERS_TPL" >&2
    exit 1
  fi
  log "sudoers: rendering template for user '$TARGET_USER' -> $SUDOERS_DST"
  local tmp
  tmp="$(mktemp)"
  sed "s/__USER__/$TARGET_USER/g" "$SUDOERS_TPL" > "$tmp"
  chmod 0440 "$tmp"
  if visudo -cf "$tmp" >/dev/null 2>&1; then
    install -m 0440 -o root -g root "$tmp" "$SUDOERS_DST"
    log "sudoers: installed and validated $SUDOERS_DST (0440)."
  else
    rm -f "$tmp"
    echo "error: rendered sudoers failed 'visudo -cf' validation; NOT installing." >&2
    exit 1
  fi
  rm -f "$tmp"
}

sudoers_uninstall() {
  if [ -f "$SUDOERS_DST" ]; then
    log "sudoers: removing $SUDOERS_DST"
    rm -f "$SUDOERS_DST"
  else
    log "sudoers: $SUDOERS_DST not present — nothing to remove."
  fi
}

sudoers_check() {
  log "sudoers: resolved tool paths:"
  for tool in setenforce semanage semodule visudo; do
    printf '[setup-linux]   %-10s %s\n' "$tool" "$(command -v "$tool" 2>/dev/null || echo '<not found>')"
  done
  if [ -f "$SUDOERS_DST" ]; then
    log "sudoers: $SUDOERS_DST INSTALLED."
  else
    log "sudoers: $SUDOERS_DST not installed."
  fi
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
require_root
case "$MODE" in
  check)
    log "running --check (no changes will be made)."
    selinux_check
    a11y_guidance
    sudoers_check
    ;;
  uninstall)
    log "running --uninstall (target user: $TARGET_USER)."
    sudoers_uninstall
    selinux_uninstall
    log "uninstall complete."
    ;;
  apply)
    log "running apply (target user: $TARGET_USER)."
    selinux_apply
    a11y_apply
    sudoers_apply
    log "setup complete. The agent should no longer stall on sudo for the a11y fix."
    ;;
esac
