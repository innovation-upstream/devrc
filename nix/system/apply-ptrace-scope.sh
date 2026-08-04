#!/usr/bin/env bash
# Persist kernel.yama.ptrace_scope=0 in /etc/nixos/configuration.nix.
#
# Claude cannot run `sudo nixos-rebuild`, so this is staged for you:
#
#   sudo bash nix/system/apply-ptrace-scope.sh
#
# Idempotent, backs up first, parse-validates, restores the backup on any
# failure before validation, and is safe on a non-TTY (the prompt is skipped
# rather than aborting mid-edit).
#
# ── WHY ──────────────────────────────────────────────────────────────────────
# keylog-spin-capture.service uses py-spy to dump the stack of keylog.service.
# That is a SIBLING process (both are `systemd --user` units), never a
# descendant of the capture, so Yama's default scope=1 ("descendants only")
# blocks both PTRACE_ATTACH and process_vm_readv. The capture then hits its
# sysctl gate and exits silently every 5 minutes — the watcher is enabled but
# structurally incapable of ever succeeding.
#
# The workbench read 0 for most of 2026-07-30 only because of a MANUAL
# `echo 0 | sudo tee` during the investigation. It was never persisted, so the
# 2026-08-04 reboot reset it to 1 and the watcher went inert. This closes that.
#
# ── THE TRADE, STATED PLAINLY ────────────────────────────────────────────────
# scope=0 is the traditional Linux default: any process may ptrace another
# running as the SAME UID. It does NOT grant cross-user or root access. But on
# a box that routinely runs agent-spawned code as this user, it widens what a
# compromised same-user process can read out of your other processes —
# including secrets held in their memory (browser sessions, kubeconfigs loaded
# into a running tool, ssh-agent). That is a real, if modest, exposure
# increase, and it is the reason this is a deliberate opt-in rather than
# something that shipped silently with the watcher.
#
# Narrower alternatives, rejected for this use:
#   - CAP_SYS_PTRACE on the capture unit: `systemd --user` services cannot
#     gain capabilities; it would have to become a system-level unit running
#     as root, which is a larger privilege grant than scope=0, not a smaller
#     one.
#   - Run py-spy as root ad hoc: works, but defeats the point — the whole
#     mechanism exists to catch a spin that appears unpredictably over
#     24h+, precisely when you are NOT sitting there to run something.
#
# ── REVERTING ────────────────────────────────────────────────────────────────
# Delete the line this adds and `nixos-rebuild switch`, then either reboot or
# `sudo sysctl -w kernel.yama.ptrace_scope=1` (a switch alone will NOT re-apply
# it — see the systemd-sysctl note below). The keylog-spin-capture unit
# degrades safely: it detects scope!=0 and exits quietly without dumping,
# toasting, or retrying.
set -euo pipefail

CFG=/etc/nixos/configuration.nix
[[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)"; exit 1; }
[[ -f $CFG ]] || { echo "missing $CFG"; exit 1; }

BAK="$CFG.bak-ptrace-$(date +%Y%m%d-%H%M%S)"
cp -a "$CFG" "$BAK"
echo "[0/3] backed up $CFG -> $BAK"

restore_on_err() {
  local rc=$?
  echo "ERROR (rc=$rc) — restoring $CFG from $BAK" >&2
  cp -a "$BAK" "$CFG"
  exit "$rc"
}
trap restore_on_err ERR

# ---- 1. Declare the sysctl ----------------------------------------------
if grep -qE '^\s*"kernel\.yama\.ptrace_scope"' "$CFG"; then
  echo "[1/3] ptrace_scope already declared — skipping"
else
  sed -i '/^\s*"vm\.page-cluster" = 0;/a\
    # Allow same-UID ptrace so py-spy can attach to a SIBLING systemd --user\
    # service (keylog-spin-capture -> keylog). Yama default 1 = descendants\
    # only, which blocks it outright. Trade-off documented in\
    # nix/system/apply-ptrace-scope.sh.\
    "kernel.yama.ptrace_scope" = 0;' "$CFG"
  grep -qE '^\s*"kernel\.yama\.ptrace_scope" = 0;' "$CFG" \
    || { echo "ERROR: ptrace_scope edit did not apply"; false; }
  echo "[1/3] added kernel.yama.ptrace_scope = 0"
fi

# ---- 2. Validate ---------------------------------------------------------
echo "[2/3] parsing $CFG ..."
nix-instantiate --parse "$CFG" >/dev/null
echo "[2/3] parse OK"
trap - ERR

echo
echo "Review before applying:  diff -u $BAK $CFG"
echo

if [[ -t 0 ]]; then
  read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans || ans=n
else
  ans=n
  echo "(non-interactive stdin — skipping the rebuild prompt)"
fi

if [[ ${ans:-n} =~ ^[Yy]$ ]]; then
  if ! nixos-rebuild switch; then
    echo
    echo "nixos-rebuild FAILED. The config edit is still in place." >&2
    echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch" >&2
    exit 1
  fi
else
  echo "Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch"
fi

# ---- 3. Apply to the LIVE kernel ----------------------------------------
# REQUIRED, not optional: `nixos-rebuild switch` does NOT restart
# systemd-sysctl (NixOS/nixpkgs#289174), so a boot.kernel.sysctl edit stays
# inert on the running kernel until this unit restarts or you reboot. This is
# the case where the restart is genuinely warranted — there IS a pending edit.
echo "[3/3] restarting systemd-sysctl (switch does NOT do this — nixpkgs#289174)"
systemctl restart systemd-sysctl.service

echo
echo "Verify (as your normal user, not root):"
echo "  sysctl -n kernel.yama.ptrace_scope     # expect 0"
echo
echo "  # Prove py-spy can actually attach to the sibling unit — this is the"
echo "  # thing scope=1 was blocking, and the only check that matters:"
echo "  nix-shell -p 'py-spy.overrideAttrs (_: { doCheck = false; })' --run \\"
echo "    \"py-spy dump --pid \\\$(systemctl --user show keylog.service -p MainPID --value)\""
echo "  # expect thread stacks; 'Permission Denied' means it did NOT take effect"
echo
echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch && sysctl -w kernel.yama.ptrace_scope=1"
