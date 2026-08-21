#!/usr/bin/env bash
# Staged system-level freeze instrumentation — LAPTOP host, 2026-08-20.
#
# Claude cannot run `sudo nixos-rebuild`, so the /etc/nixos edits are staged
# here. Idempotent: safe to re-run.
#
#   sudo bash nix/system/apply-freeze-instrumentation.sh
#
# ── Why ─────────────────────────────────────────────────────────────────────
#
# The laptop has stopped uncleanly SIX times since 2026-07-29 (Jul 29, Jul 31,
# Aug 06, Aug 10, Aug 17, Aug 20), after a clean June. "Unclean" here means the
# boot never reached systemd's shutdown sequence — classified by tailing each
# boot for `Journal stopped` / `System Power Off`.
#
# Not one of them recorded a cause: no MCE, no oops, no soft-lockup trace, no
# GPU hang, no OOM, no thermal event. That absence is STRUCTURAL, not evidence
# of health — this host is configured so that a hard lockup cannot be observed:
#
#   1. kernel.nmi_watchdog reads 0. The kernel enables the hard-lockup detector
#      at boot ("NMI watchdog: Enabled", measured 2026-08-20 17:20:15), then TLP
#      writes 0 seven seconds later (17:20:22). NMI_WATCHDOG is absent from the
#      generated /etc/tlp.conf, so tlp falls back to its shipped
#      share/tlp/defaults.conf, which sets NMI_WATCHDOG=0.
#   2. kernel.panic = 0 and kernel.hardlockup_panic = 0, so even a DETECTED
#      lockup hangs forever instead of panicking, capturing and rebooting.
#   3. journald runs at the stock SyncIntervalSec=5m, so the last few minutes
#      before any freeze were never written to disk.
#
# Net: a hard lockup on this box is silent by construction. Until that changes,
# the next freeze will be exactly as blank as the last six. This script does NOT
# fix the freezes — it makes the next one leave evidence.
#
# ── Why the fix goes in services.tlp.settings, not boot.kernel.sysctl ────────
#
# set_nmi_watchdog() is called from tlp's main `start` path
# (sbin/tlp:32), which re-runs on EVERY AC<->battery transition via TLP's udev
# rules, not just at boot. A sysctl.d entry or a boot.kernel.sysctl declaration
# would therefore be silently reverted the next time the charger moves. Setting
# NMI_WATCHDOG=1 in TLP's own config is the only placement TLP will not stomp.
#
# ── Deliberately NOT included ───────────────────────────────────────────────
#
#   - kernel.panic_on_oops. An oops is ALREADY logged; the silent case is the
#     hard lockup, which oopses nothing. Turning this on would convert today's
#     survivable driver oopses into reboots — a real behaviour regression bought
#     for no diagnostic gain.
#   - kernel.softlockup_panic. Soft-lockup warnings fire under heavy nix/docker
#     load on a 4C/8T part; panicking on them would reboot this host during
#     ordinary builds. The detector still WARNS with this left at 0.
#   - ramoops. Unnecessary here: efi_pstore is proven working on this machine —
#     it captured the 2026-07-31 14:18 crash to
#     /var/lib/systemd/pstore/17855255*/. ramoops additionally needs a reserved
#     memory region, which is awkward to place safely on x86+EFI.
#   - netconsole. The gold standard for a hard freeze, but it needs a listener
#     host configured and running. Worth doing if the pstore route comes up dry.
#
# ── Cost, stated honestly ───────────────────────────────────────────────────
#
#   - The NMI watchdog "permanently consumes one hw-PMU counter" (the kernel's
#     own words at boot). If you ever profile with `perf` on this host, that is
#     one fewer hardware counter available.
#   - It also costs a little power, which is why TLP disables it by default.
#   - SyncIntervalSec=30s raises journal write frequency ~10x. On NVMe this is
#     negligible; on battery it is a small extra wakeup source.
#
# ── What this does NOT tell you ─────────────────────────────────────────────
#
# Nothing here identifies the cause. Kernel (7.0.0) and BIOS (03.17) were
# CONSTANT across all 13 boots examined, and the only NixOS generations are
# Jun 23 and Aug 13 — the Jul 29 onset sits inside a generation that had already
# run cleanly for five weeks. An onset with no coincident software change also
# fits hardware degradation (RAM, power delivery, NVMe), which this script does
# not test. A memtest86+ pass remains the cheap discriminator.
#
# -E (errtrace) is load-bearing, not stylistic: without it the ERR trap below is
# NOT inherited into shell functions, so a `false` inside require_one_match()
# exits via `set -e` having skipped the restore entirely — leaving the config
# half-edited, which is the exact state the trap exists to prevent. Caught by
# test_refuses_when_an_anchor_is_duplicated.
set -Eeuo pipefail

# CFG is overridable so the test suite can exercise the edit logic against a
# fixture. --edit-only additionally skips the root check and the rebuild.
CFG=${CFG:-/etc/nixos/configuration.nix}
EDIT_ONLY=0
[[ ${1:-} == "--edit-only" ]] && EDIT_ONLY=1

if [[ $EDIT_ONLY -eq 0 ]]; then
  [[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)"; exit 1; }
fi
[[ -f $CFG ]] || { echo "missing $CFG"; exit 1; }

BAK="$CFG.bak-freeze-$(date +%Y%m%d-%H%M%S)"
cp -a "$CFG" "$BAK"
echo "[0/5] backed up $CFG -> $BAK"

restore_on_err() {
  local rc=$?
  echo "ERROR (rc=$rc) — restoring $CFG from $BAK" >&2
  cp -a "$BAK" "$CFG"
  exit "$rc"
}
trap restore_on_err ERR

# An anchor that matches a different number of lines than we assume is the
# `count=1 replace` hazard: the edit lands somewhere we never pictured. Assert
# the count instead of trusting it.
require_one_match() { # <regex> <human name>
  local n
  n=$(grep -cE "$1" "$CFG" || true)
  if [[ $n -ne 1 ]]; then
    echo "ERROR: expected exactly 1 match for $2, found $n — refusing to edit" >&2
    false
  fi
}

indent_of() { # <line-number> -> leading whitespace of that line
  sed -n "${1}p" "$CFG" | sed -E 's/^([[:space:]]*).*/\1/'
}

# ---- 1. NMI watchdog, via TLP so power events cannot revert it -----------
if grep -qE '^[[:space:]]*NMI_WATCHDOG[[:space:]]*=' "$CFG"; then
  echo "[1/5] NMI_WATCHDOG already declared — skipping"
else
  require_one_match '^[[:space:]]*services\.tlp[[:space:]]*=' "services.tlp"
  tlp_line=$(grep -nE '^[[:space:]]*services\.tlp[[:space:]]*=' "$CFG" | cut -d: -f1)
  set_line=$(awk -v s="$tlp_line" \
    'NR>s && /^[[:space:]]*settings[[:space:]]*=[[:space:]]*\{/ {print NR; exit}' "$CFG")
  [[ -n ${set_line:-} ]] || { echo "ERROR: no 'settings = {' after services.tlp" >&2; false; }
  ind="$(indent_of "$set_line")  "
  sed -i "${set_line}a\\
${ind}\\
${ind}# Freeze instrumentation: TLP's shipped default is NMI_WATCHDOG=0, which\\
${ind}# disables the hard-lockup detector the kernel enables at boot — and it is\\
${ind}# re-applied on every AC<->battery transition, so this must live HERE and\\
${ind}# not in boot.kernel.sysctl. Without it a hard lockup logs nothing at all.\\
${ind}NMI_WATCHDOG = 1;" "$CFG"
  grep -qE '^[[:space:]]*NMI_WATCHDOG[[:space:]]*=[[:space:]]*1;' "$CFG" \
    || { echo "ERROR: NMI_WATCHDOG edit did not apply" >&2; false; }
  echo "[1/5] services.tlp.settings.NMI_WATCHDOG = 1"
fi

# ---- 2/3. Panic on hard lockup, and actually reboot ----------------------
require_one_match '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "boot.kernel.sysctl"
sysctl_line=$(grep -nE '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "$CFG" | cut -d: -f1)

if grep -qE '"kernel\.hardlockup_panic"' "$CFG"; then
  echo "[2/5] kernel.hardlockup_panic already declared — skipping"
else
  ind="$(indent_of "$sysctl_line")  "
  sed -i "${sysctl_line}a\\
${ind}# A detected hard lockup must PANIC (so efi_pstore captures a trace) and\\
${ind}# then reboot, rather than hanging forever as it does today.\\
${ind}\"kernel.hardlockup_panic\" = 1;\\
${ind}\"kernel.panic\" = 20;" "$CFG"
  grep -qE '"kernel\.hardlockup_panic"[[:space:]]*=[[:space:]]*1;' "$CFG" \
    || { echo "ERROR: hardlockup_panic edit did not apply" >&2; false; }
  echo "[2/5] boot.kernel.sysctl: hardlockup_panic=1, panic=20"
fi

# ---- 4. Shrink the journald blind window ---------------------------------
if grep -qE '^[[:space:]]*services\.journald\.extraConfig[[:space:]]*=' "$CFG"; then
  echo "[3/5] services.journald.extraConfig already declared — skipping"
else
  # Recompute: edit 2 may have shifted the anchor.
  sysctl_line=$(grep -nE '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "$CFG" | cut -d: -f1)
  ind="$(indent_of "$sysctl_line")"
  sed -i "$((sysctl_line - 1))a\\
${ind}# Stock SyncIntervalSec is 5m, so the final minutes before each freeze were\\
${ind}# never written to disk. 30s narrows that blind window.\\
${ind}services.journald.extraConfig = \"SyncIntervalSec=30s\";\\
${ind}" "$CFG"
  grep -qE '^[[:space:]]*services\.journald\.extraConfig[[:space:]]*=' "$CFG" \
    || { echo "ERROR: journald edit did not apply" >&2; false; }
  echo "[3/5] services.journald.extraConfig: SyncIntervalSec=30s"
fi

# ---- 5. Validate ---------------------------------------------------------
if command -v nix-instantiate >/dev/null 2>&1; then
  echo "[4/5] parsing $CFG ..."
  nix-instantiate --parse "$CFG" >/dev/null
  echo "[4/5] parse OK"
else
  echo "[4/5] nix-instantiate unavailable — SKIPPING parse validation" >&2
fi

trap - ERR

echo
echo "Review the diff before applying:"
echo "    diff -u $BAK $CFG"
echo

if [[ $EDIT_ONLY -eq 1 ]]; then
  echo "[5/5] --edit-only: config edited and validated, nothing applied."
  exit 0
fi

if [[ -t 0 ]]; then
  read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans || ans=n
else
  ans=n
  echo "(non-interactive stdin — skipping the rebuild prompt)"
fi

if [[ ${ans:-n} =~ ^[Yy]$ ]]; then
  if ! nixos-rebuild switch; then
    echo
    echo "nixos-rebuild FAILED. The config edits are still in place." >&2
    echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch" >&2
    exit 1
  fi
  # A `nixos-rebuild switch` does NOT restart systemd-sysctl
  # (NixOS/nixpkgs#289174), so the sysctl edits above are inert until it is
  # restarted or the host reboots. TLP likewise needs a restart to re-read
  # NMI_WATCHDOG. Both are cheap and idempotent.
  systemctl restart systemd-sysctl
  systemctl restart tlp.service
  echo "[5/5] applied; restarted systemd-sysctl and tlp"
else
  echo "[5/5] Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch && sudo systemctl restart systemd-sysctl tlp.service"
fi

echo
echo "Verify (all three must hold — the first is the one that has been failing):"
echo "  sysctl kernel.nmi_watchdog          # expect 1  (was 0, reverted by TLP)"
echo "  sysctl kernel.hardlockup_panic      # expect 1"
echo "  sysctl kernel.panic                 # expect 20"
echo
echo "Then re-check AFTER moving the charger, which is when TLP re-applies:"
echo "  sysctl kernel.nmi_watchdog          # must STILL be 1"
echo
echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch"
