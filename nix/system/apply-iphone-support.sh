#!/usr/bin/env bash
# =============================================================================
# Enable iPhone connectivity: usbmuxd daemon + libimobiledevice tools
# =============================================================================
# Applies the NixOS module iphone-support.nix (services.usbmuxd.enable +
# libimobiledevice/ifuse in systemPackages), wires its import into
# configuration.nix, prefights with parse + dry-build, then rebuilds.
# Idempotent. Run from the repo root:
#
#     sudo bash nix/system/apply-iphone-support.sh
#
# Rollback:
#     sudo cp /etc/nixos/configuration.nix.bak-iphone-support /etc/nixos/configuration.nix
#     sudo rm /etc/nixos/iphone-support.nix
#     sudo nixos-rebuild switch
# =============================================================================
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "This must run as root:  sudo bash nix/system/apply-iphone-support.sh" >&2
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NIXOS_DIR="/etc/nixos"
CONFIG="${NIXOS_DIR}/configuration.nix"
MODULE="${NIXOS_DIR}/iphone-support.nix"
BAK="${CONFIG}.bak-iphone-support"

echo "=== iPhone support (usbmuxd + libimobiledevice) ==="

# ---------------------------------------------------------------------------- #
# 1. Install the module.
# ---------------------------------------------------------------------------- #
echo "[1/4] Installing ${MODULE}..."
install -m 0644 -o root -g root "${REPO}/nix/system/iphone-support.nix" "${MODULE}"

# ---------------------------------------------------------------------------- #
# 2. Ensure configuration.nix imports it.
# ---------------------------------------------------------------------------- #
if grep -q 'iphone-support.nix' "${CONFIG}"; then
  echo "[2/4] Import already present — skipping."
else
  echo "[2/4] Wiring import into ${CONFIG}..."
  cp "${CONFIG}" "${BAK}"
  n_imports="$(grep -cE '^[[:space:]]*imports[[:space:]]*=' "${CONFIG}" || true)"
  if [[ "${n_imports}" != "1" ]]; then
    echo "  -> ERROR: found ${n_imports} 'imports =' assignment(s) in ${CONFIG}." >&2
    echo "     Add ./iphone-support.nix to imports manually, then re-run." >&2
    exit 1
  fi
  awk '
    !ins && /^[[:space:]]*imports[[:space:]]*=/ { arm = 1 }
    arm && !ins && index($0, "[") > 0 {
      p = index($0, "[")
      print substr($0, 1, p) "\n      ./iphone-support.nix" substr($0, p + 1)
      ins = 1; arm = 0; next
    }
    { print }
  ' "${CONFIG}" > "${CONFIG}.tmp.iphone"
  cat "${CONFIG}.tmp.iphone" > "${CONFIG}"
  rm -f "${CONFIG}.tmp.iphone"
  if ! grep -q 'iphone-support.nix' "${CONFIG}"; then
    echo "  -> ERROR: could not wire import automatically." >&2
    cp "${BAK}" "${CONFIG}"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------- #
# 3. Preflight: the patched config must parse and dry-build before switching.
#    On failure the import is unwired again (nothing was activated either way).
# ---------------------------------------------------------------------------- #
echo "[3/4] Preflight (parse + dry-build)..."
if ! nix-instantiate --parse "${CONFIG}" >/dev/null; then
  echo "  -> ERROR: patched configuration.nix does not parse — unwiring." >&2
  [[ -f "${BAK}" ]] && cp "${BAK}" "${CONFIG}"
  exit 1
fi
echo "  parse: OK"
if ! nixos-rebuild dry-build; then
  echo "  -> ERROR: dry-build failed — unwiring, nothing was activated." >&2
  [[ -f "${BAK}" ]] && cp "${BAK}" "${CONFIG}"
  exit 1
fi
echo "  dry-build: OK"

# ---------------------------------------------------------------------------- #
# 4. Rebuild.
# ---------------------------------------------------------------------------- #
echo "[4/4] nixos-rebuild switch..."
nixos-rebuild switch

# ---------------------------------------------------------------------------- #
# Verify — the daemon active, not merely "the switch succeeded".
# ---------------------------------------------------------------------------- #
echo
active=$(systemctl is-active usbmuxd.service 2>/dev/null || true)
if [[ "${active}" == "active" ]]; then
  echo "verified: usbmuxd.service is active"
else
  echo "WARNING: usbmuxd.service is '${active}', not 'active' — check:" >&2
  echo "    systemctl status usbmuxd.service" >&2
  exit 1
fi

echo
echo "Done. Now plug in the iPhone (USB) and:"
echo "  1. Tap 'Trust' on the phone when prompted (unlock it first)."
echo "  2. From a NEW shell (PATH refresh):"
echo "       idevice_id -l      # UDID once the phone is seen"
echo "       ideviceinfo        # full device info"
echo "       idevicepair pair   # completes the trust handshake"
echo "       ifuse ~/mnt/iphone # FUSE mount (mkdir ~/mnt/iphone first)"
echo "Rollback: see the header of this script."
