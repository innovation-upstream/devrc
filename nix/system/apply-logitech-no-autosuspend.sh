#!/usr/bin/env bash
# =============================================================================
# Disable USB autosuspend for Logitech wireless receivers
# =============================================================================
# Prevents the mouse from going to sleep after short idle periods.
# Applies the NixOS module logitech-no-autosuspend.nix, which adds a udev rule
# via services.udev.extraRules. Idempotent. Run from the repo root:
#
#     sudo bash nix/system/apply-logitech-no-autosuspend.sh
# =============================================================================
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "This must run as root:  sudo bash nix/system/apply-logitech-no-autosuspend.sh" >&2
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"
CONFIG="${NIXOS_DIR}/configuration.nix"
MODULE="${NIXOS_DIR}/logitech-no-autosuspend.nix"

echo "=== Disable Logitech USB autosuspend ==="

# ---------------------------------------------------------------------------- #
# 1. Install the module.
# ---------------------------------------------------------------------------- #
echo "[1/3] Installing ${MODULE}..."
install -m 0644 -o root -g root "${REPO}/nix/system/logitech-no-autosuspend.nix" "${MODULE}"

# ---------------------------------------------------------------------------- #
# 2. Ensure configuration.nix imports it.
# ---------------------------------------------------------------------------- #
if grep -q 'logitech-no-autosuspend.nix' "${CONFIG}"; then
  echo "[2/3] Import already present — skipping."
else
  echo "[2/3] Wiring import into ${CONFIG}..."
  cp "${CONFIG}" "${CONFIG}.bak-logitech"
  n_imports="$(grep -cE '^[[:space:]]*imports[[:space:]]*=' "${CONFIG}" || true)"
  if [[ "${n_imports}" != "1" ]]; then
    echo "  -> ERROR: found ${n_imports} 'imports =' assignment(s) in ${CONFIG}." >&2
    echo "     Add ./logitech-no-autosuspend.nix to imports manually, then re-run." >&2
    exit 1
  fi
  awk '
    !ins && /^[[:space:]]*imports[[:space:]]*=/ { arm = 1 }
    arm && !ins && index($0, "[") > 0 {
      p = index($0, "[")
      print substr($0, 1, p) "\n      ./logitech-no-autosuspend.nix" substr($0, p + 1)
      ins = 1; arm = 0; next
    }
    { print }
  ' "${CONFIG}" > "${CONFIG}.tmp.logitech"
  cat "${CONFIG}.tmp.logitech" > "${CONFIG}"
  rm -f "${CONFIG}.tmp.logitech"
  if ! grep -q 'logitech-no-autosuspend.nix' "${CONFIG}"; then
    echo "  -> ERROR: could not wire import automatically." >&2
    cp "${CONFIG}.bak-logitech" "${CONFIG}"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------- #
# 3. Rebuild.
# ---------------------------------------------------------------------------- #
echo "[3/3] nixos-rebuild switch..."
nixos-rebuild switch

# Immediately apply to the current receiver (no replug needed)
if [[ -d /sys/bus/usb/devices/3-1 ]]; then
  echo "on" > /sys/bus/usb/devices/3-1/power/control 2>/dev/null || true
fi

echo "Done. Logitech receivers will no longer autosuspend."
