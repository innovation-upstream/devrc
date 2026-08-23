#!/usr/bin/env bash
# Staged system-level change — LAPTOP host, 2026-08-21.
#
#   sudo bash nix/system/apply-freeze-followup-2026-08-21.sh
#
# Three changes:
#   1. kernel.panic_on_oops = 1   — reverses an explicit decision in #616
#   2. a memtest86+ boot entry    — makes the leading hypothesis testable
#   3. re-assert kernel.nmi_watchdog=1 after powertop — see below; ADDED after
#      the 2026-08-21 18:45 freeze, which proved #616's headline fix inert
#
# Idempotent: safe to re-run.
#
# ── 🔴 #616'S HARD-LOCKUP PANIC HAS NEVER ONCE BEEN ARMED ───────────────────
#
# #616 set hardlockup_panic=1 and fixed TLP's NMI_WATCHDOG=0 default. That fix
# is correct and it works. It is also not sufficient: `powertop --auto-tune`
# runs AFTER TLP and writes 0 to /proc/sys/kernel/nmi_watchdog itself.
#
#     19:06:31  kernel: "NMI watchdog: Enabled"
#     19:06:38  tlp:    "Applying power save settings...done."   (writes 1)
#     19:06:38  powertop --auto-tune                             (writes 0)
#     steady state: kernel.nmi_watchdog = 0
#
# Measured with a discriminating control on 2026-08-21 (both units restarted in
# isolation, from a known value of 1):
#
#     after `systemctl restart powertop.service` -> 0
#     after `systemctl restart tlp.service`      -> 1
#
# So powertop is the writer and TLP is innocent. hardlockup_panic=1 is DEAD
# CODE while nmi_watchdog=0 — the detector never runs, so it can never fire the
# panic. Every freeze since #616 has been unwatched.
#
# powertop.service is a `RemainAfterExit=yes` oneshot ordered
# After=multi-user.target, so ExecStartPost is enough to re-assert the value
# after it has done its tuning; TLP independently re-writes 1 on every AC<->BAT
# transition, so the two together cover both the boot path and power events.
# The rest of powertop's tuning is left alone — this reverses ONE tunable.
#
# ── WHY THIS REVERSES AN EARLIER DECISION ───────────────────────────────────
#
# apply-freeze-instrumentation.sh (PR #616) DELIBERATELY EXCLUDED
# kernel.panic_on_oops, with this reasoning:
#
#     "An oops is ALREADY logged; the silent case is the hard lockup, which
#      oopses nothing. Turning this on would convert today's survivable driver
#      oopses into reboots — a real behaviour regression bought for no
#      diagnostic gain."
#
# 🔴 THE 2026-07-31 CRASH DUMP REFUTES THAT FOR THIS MACHINE. Reading
# /var/lib/systemd/pstore/1785525502/002/dmesg.txt (the capture from that
# freeze) shows the first fault WAS an oops — and it was not survivable, and it
# was not usefully logged. It cascaded:
#
#   1. Page fault at ct_kernel_enter_state+0x8. The instruction decodes as
#        mov  %gs:0x1a4d130(%rip),%rax      # load per-CPU context-tracking ptr
#        lock xadd %edi,-0x7b4b2138(%rax)   # atomic add into it
#      with RAX = 0x000000040001f30a — NOT a kernel address. The arithmetic
#      closes exactly: 0x40001f30a - 0x7b4b2138 = 0x384b6d1d2 = CR2. So the
#      per-CPU pointer was garbage; the access itself was fine.
#   2. The fault handler re-entered the same path
#        irqentry_enter -> ct_nmi_enter -> ct_kernel_enter_state -> fault
#      recursing ~272 bytes of stack per level across eleven visible frames.
#   3. Stack exhausted -> #DF double fault -> "Thread overran stack, or stack
#      corrupted", "stack recursion on stack type 5". Unrecoverable, no clean
#      panic path, machine simply stopped. It did NOT reboot.
#
# With panic_on_oops=1 the kernel panics at step 1: one clean trace to
# efi_pstore instead of hundreds of recursive frames, and an automatic reboot on
# the kernel.panic=20 that #616 already set. The cost the original reasoning
# feared — "survivable oopses become reboots" — is the correct trade on a host
# whose oopses demonstrably are not survivable.
#
# ── WHAT THIS DOES NOT CLAIM ────────────────────────────────────────────────
#
# It does not fix anything. The leading hypothesis is MEMORY CORRUPTION, on this
# evidence: a garbage per-CPU pointer, hit from the idle task (CPU 4, PID 0,
# Comm: swapper/4), with unclean stops spanning THREE kernel versions —
# 6.18.4 (Mar 22), 6.19.11 (Apr 12, Apr 17), 7.0.0 (May 18 onward). A
# regression in the entry/context-tracking path would not survive
# 6.18 -> 6.19 -> 7.0. Run memtest86+ before pursuing a software cause.
#
# Also unresolved: the dump reads "Tainted: G W", so something had already
# WARNed earlier in that boot. That journal has rotated away.
set -Eeuo pipefail

CFG=${CFG:-/etc/nixos/configuration.nix}
EDIT_ONLY=0
[[ ${1:-} == "--edit-only" ]] && EDIT_ONLY=1

if [[ $EDIT_ONLY -eq 0 ]]; then
  [[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)"; exit 1; }
fi
[[ -f $CFG ]] || { echo "missing $CFG"; exit 1; }

BAK="$CFG.bak-oops-$(date +%Y%m%d-%H%M%S)"
cp -a "$CFG" "$BAK"
echo "[0/5] backed up $CFG -> $BAK"

restore_on_err() {
  local rc=$?
  echo "ERROR (rc=$rc) — restoring $CFG from $BAK" >&2
  cp -a "$BAK" "$CFG"
  exit "$rc"
}
trap restore_on_err ERR

require_one_match() { # <regex> <human name>
  local n
  n=$(grep -cE "$1" "$CFG" || true)
  if [[ $n -ne 1 ]]; then
    echo "ERROR: expected exactly 1 match for $2, found $n — refusing to edit" >&2
    false
  fi
}

# ---- 1. panic_on_oops ----------------------------------------------------
if grep -qE '"kernel\.panic_on_oops"' "$CFG"; then
  echo "[1/5] kernel.panic_on_oops already declared — skipping"
else
  require_one_match '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "boot.kernel.sysctl"
  line=$(grep -nE '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "$CFG" | cut -d: -f1)
  ind="$(sed -n "${line}p" "$CFG" | sed -E 's/^([[:space:]]*).*/\1/')  "
  sed -i "${line}a\\
${ind}# Panic on the FIRST oops rather than letting it cascade. The 2026-07-31\\
${ind}# capture shows an oops in the context-tracking entry path recursing into\\
${ind}# a double fault and hanging the machine — see\\
${ind}# nix/system/apply-freeze-followup-2026-08-21.sh for the decoded trace.\\
${ind}\"kernel.panic_on_oops\" = 1;" "$CFG"
  grep -qE '"kernel\.panic_on_oops"[[:space:]]*=[[:space:]]*1;' "$CFG" \
    || { echo "ERROR: panic_on_oops edit did not apply" >&2; false; }
  echo "[1/5] boot.kernel.sysctl: panic_on_oops=1"
fi

# ---- 2. memtest86+ boot entry -------------------------------------------
# The leading hypothesis is memory corruption (see the header). Testing it
# should not require finding a USB stick: systemd-boot can carry a memtest86+
# entry, so the test is one reboot away.
#
# Budget REAL time: a full pass over 64 GiB takes hours. Run it overnight, and
# let it complete at least one full pass — a short run that finds nothing is not
# evidence of good RAM.
if grep -qE '^[[:space:]]*boot\.loader\.systemd-boot\.memtest86\.enable' "$CFG"; then
  echo "[2/5] memtest86 boot entry already declared — skipping"
else
  require_one_match '^[[:space:]]*boot\.loader\.systemd-boot\.enable' "boot.loader.systemd-boot.enable"
  line=$(grep -nE '^[[:space:]]*boot\.loader\.systemd-boot\.enable' "$CFG" | cut -d: -f1)
  ind="$(sed -n "${line}p" "$CFG" | sed -E 's/^([[:space:]]*).*/\1/')"
  sed -i "${line}a\\
${ind}# Adds a memtest86+ entry to the boot menu. The 2026-07-31 crash shows a\\
${ind}# GARBAGE per-CPU pointer, and unclean stops span three kernel versions\\
${ind}# (6.18.4, 6.19.11, 7.0.0), so memory corruption is the leading\\
${ind}# hypothesis. Budget hours: a full pass over 64 GiB is not quick.\\
${ind}boot.loader.systemd-boot.memtest86.enable = true;" "$CFG"
  grep -qE 'boot\.loader\.systemd-boot\.memtest86\.enable[[:space:]]*=[[:space:]]*true;' "$CFG" \
    || { echo "ERROR: memtest86 edit did not apply" >&2; false; }
  echo "[2/5] boot.loader.systemd-boot.memtest86.enable = true"
fi

# ---- 3. Re-assert the NMI watchdog after powertop -------------------------
# Inserted at TOP LEVEL, immediately before `boot.kernel.sysctl = {`.
#
# NOT anchored on `powerManagement.powertop.enable` even though that is the
# obviously related line: it sits at 4-space indent inside a nested block, so an
# insertion there could land in the wrong attrset and still parse. The
# boot.kernel.sysctl line is confirmed top-level (2-space indent).
#
# /run/current-system/sw/bin/sysctl rather than ${pkgs.procps}: this is inserted
# by sed into a file whose `let` bindings we cannot see, so it must not depend
# on any identifier being in scope.
if grep -qE 'systemd\.services\.powertop\.serviceConfig\.ExecStartPost' "$CFG"; then
  echo "[3/5] powertop ExecStartPost already declared — skipping"
else
  require_one_match '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "boot.kernel.sysctl"
  line=$(grep -nE '^[[:space:]]*boot\.kernel\.sysctl[[:space:]]*=' "$CFG" | cut -d: -f1)
  ind="$(sed -n "${line}p" "$CFG" | sed -E 's/^([[:space:]]*).*/\1/')"
  sed -i "$((line - 1))a\\
${ind}# 🔴 powertop --auto-tune writes 0 to /proc/sys/kernel/nmi_watchdog, AFTER\\
${ind}# TLP has correctly written 1 (#616). That left hardlockup_panic=1 inert —\\
${ind}# the hard-lockup detector was never running, so it could never fire the\\
${ind}# panic. Confirmed 2026-08-21 by restarting each unit in isolation:\\
${ind}# powertop -> 0, tlp -> 1. See nix/system/diagnose-freeze-2026-08-21.sh.\\
${ind}systemd.services.powertop.serviceConfig.ExecStartPost =\\
${ind}  \"/run/current-system/sw/bin/sysctl -w kernel.nmi_watchdog=1\";" "$CFG"
  grep -qE 'systemd\.services\.powertop\.serviceConfig\.ExecStartPost' "$CFG" \
    || { echo "ERROR: powertop ExecStartPost edit did not apply" >&2; false; }
  echo "[3/5] systemd.services.powertop.serviceConfig.ExecStartPost = sysctl -w kernel.nmi_watchdog=1"
fi

# ---- 4. Validate ---------------------------------------------------------
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
  # (NixOS/nixpkgs#289174), so the edit is inert until it is.
  systemctl restart systemd-sysctl
  # Nor does it restart powertop.service: it is a RemainAfterExit oneshot that
  # is already "active (exited)", so the new ExecStartPost would not run until
  # the next boot. Deployed != restarted.
  systemctl restart powertop.service
  echo "[5/5] applied; restarted systemd-sysctl + powertop"
else
  echo "[5/5] Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch \\"
  echo "      && sudo systemctl restart systemd-sysctl powertop.service"
fi

echo
echo "Verify:"
echo "  sysctl kernel.panic_on_oops    # expect 1"
echo "  sysctl kernel.panic            # expect 20  (set by #616)"
echo "  sysctl kernel.nmi_watchdog     # expect 1   <- THE ONE THAT WAS 0."
echo "                                 #    #616 alone did NOT achieve this;"
echo "                                 #    powertop stomped it every boot."
echo
echo "  # and prove it SURVIVES the writer, rather than trusting the read above:"
echo "  sudo systemctl restart powertop.service && sysctl kernel.nmi_watchdog"
echo "                                 # STILL 1 — this is the real check"
echo
echo "Then REBOOT and pick the memtest86+ entry from the boot menu."
echo "Let it finish at least one FULL pass over 64 GiB — hours, not minutes."
echo "A short clean run is NOT evidence of good RAM."
echo
echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch"
