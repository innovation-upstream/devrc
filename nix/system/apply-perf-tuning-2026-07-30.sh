#!/usr/bin/env bash
# Staged system-level perf tuning — workbench, 2026-07-30.
#
# Claude cannot run `sudo nixos-rebuild`, so the /etc/nixos edits from the perf
# investigation are staged here. Idempotent: safe to re-run.
#
#   sudo bash nix/system/apply-perf-tuning-2026-07-30.sh
#
# Safe on a non-TTY (pipe, ssh without -t, agent shell): the prompt is skipped
# rather than aborting mid-edit, and any error restores the backup via an ERR
# trap. An earlier revision did NOT do this and could leave the config
# half-written and unvalidated — see the audit on PR #214.
#
# ── What this changes, and the evidence ──────────────────────────────────────
#
#  1. nix.settings max-jobs/cores — currently UNSET, so Nix uses stock defaults
#     max-jobs=auto(24) x cores=0(=24 threads per job). Per the Nix manual,
#     "the maximum number of consumed cores is a simple multiplication,
#     max-jobs * NIX_BUILD_CORES" — 24 jobs each requesting 24 threads on a
#     12C/24T part. Measured: load 35-69 on 24 threads, 195-275k context
#     switches/s, PSI cpu some=63% while full=0% (contention, not saturation).
#     6x2 budgets ~12 of 24 threads to builds. Heuristic, not a benchmarked
#     optimum — 4x4 and 8x2 are equally defensible; bounding the PRODUCT is
#     the point.
#
#  2. zramSwap memoryPercent 50 -> 70 — PREVENTIVE headroom, not a fix for an
#     observed failure. Be honest about both sides:
#       FOR: zram sits at ~96% of its 61.7 GiB LOGICAL disksize, with swap-free
#            logged at 0.03-2.72% across six consecutive hours (once 2.8 MiB).
#            There is no second swap device, so exhausting disksize means the
#            driver returns ENOSPC and reclaim/OOM pressure follows.
#       AGAINST: /sys/block/zram0/io_stat shows failed_writes=0 over 25 days —
#            that cliff has never actually been hit. And raising the logical
#            size converts free RAM into potential swap backing (mem_used is
#            ~16 GB today, mem_used_max 21.5 GB) on a box with limited free RAM.
#     Net: defensible as margin, but do not deploy it believing it fixes a
#     failure that has occurred. Revert if RSS pressure worsens.
#
# ── DELIBERATELY REMOVED after the PR #214 audit ─────────────────────────────
#
#   - systemd-sysctl restart. The earlier revision restarted it, claiming every
#     boot.kernel.sysctl edit since the last reboot had been inert. That
#     premise was WRONG on this host: /run/booted-system and /run/current-system
#     render IDENTICAL sysctl files (both declare vm.watermark_scale_factor=125),
#     and every configuration.nix backup back to 2026-07-05 also says 125 — so
#     there were no pending edits to apply. Something raised the live value to
#     131 at runtime and the cause is still unidentified; restarting the unit
#     would stomp an unexplained live memory-pressure knob for no benefit.
#     NOTE the underlying trap is still REAL and still worth knowing: a
#     `nixos-rebuild switch` does NOT restart systemd-sysctl
#     (NixOS/nixpkgs#289174). If you ever DO edit boot.kernel.sysctl, run
#     `systemctl restart systemd-sysctl` yourself or reboot — the switch alone
#     will not apply it.
#
#   - kernel.yama.ptrace_scope=0. The earlier revision prompted to set this so
#     py-spy could attach to keylog.service. Unnecessary: this host already
#     reads 0 (nothing in /etc/sysctl.d sets it, and a py-spy attach succeeded
#     — see ~/.cache/keylog-spin/baseline-healthy-*.txt). It would have asked
#     you to accept a real, if mild, security relaxation for zero gain.
#
#   - zramSwap.memoryMax. Removed as an inert no-op that was documented as the
#     opposite of what it does. nixpkgs computes
#     `zram-size = min(memoryPercent%, memoryMax)`, i.e. it caps the LOGICAL
#     disksize, not RAM cost — both option descriptions say verbatim "This
#     doesn't define how much memory will be used by the zram swap devices."
#     And numerically it never bound: 70% of 123.4 GiB = 86.4, min(86.4,100) =
#     86.4.
#
# ── NOT INCLUDED (need a decision or measurement first) ──────────────────────
#   - /tmp aging via systemd.tmpfiles: /tmp holds 99 GiB of Claude scratchpads,
#     90 GiB of which is ML model weights (LTX-2.3 etc) from one ComfyUI
#     session, and those files are NOT duplicated in comfyui/models — they are
#     the only copies. An aging rule WOULD EVENTUALLY DELETE THEM. Relocate
#     them to permanent storage first, then add the rule.
#   - services.scx (sched_ext): real opportunity, but a behavioural change
#     that deserves its own A/B against PSI/wakeup latency, not a blind ship.
#   - k3s --system-reserved/--kube-reserved: requires a k3s restart on a
#     196-day single-node control plane. Schedule it deliberately.
set -euo pipefail

CFG=/etc/nixos/configuration.nix
[[ $EUID -eq 0 ]] || { echo "must run as root (sudo bash $0)"; exit 1; }
[[ -f $CFG ]] || { echo "missing $CFG"; exit 1; }

BAK="$CFG.bak-perf-$(date +%Y%m%d-%H%M%S)"
cp -a "$CFG" "$BAK"
echo "[0/3] backed up $CFG -> $BAK"

# Any unexpected failure from here on restores the backup. Without this, an
# abort between the first edit and the parse check leaves a mangled config.
restore_on_err() {
  local rc=$?
  echo "ERROR (rc=$rc) — restoring $CFG from $BAK" >&2
  cp -a "$BAK" "$CFG"
  exit "$rc"
}
trap restore_on_err ERR

# ---- 1. Nix build parallelism -------------------------------------------
# Guard matches both dotted and block form so a later refactor to
# `nix.settings = { max-jobs = ...; }` cannot produce a duplicate attribute.
if grep -qE '^\s*(nix\.settings\.max-jobs|max-jobs)\s*=' "$CFG"; then
  echo "[1/3] max-jobs already present — skipping"
else
  sed -i '/^\s*nix\.settings\.experimental-features/a\
\
  # Bound total build parallelism. Stock defaults (max-jobs=auto x cores=0)\
  # let 24 concurrent jobs each request all 24 threads; the Nix manual warns\
  # this degrades throughput "due to extensive context switching". 6x2 keeps\
  # ~half the box free for interactive agent work during a build.\
  nix.settings.max-jobs = 6;\
  nix.settings.cores = 2;' "$CFG"
  grep -qE '^\s*nix\.settings\.max-jobs' "$CFG" || { echo "ERROR: max-jobs edit did not apply"; false; }
  echo "[1/3] added nix.settings.max-jobs=6 / cores=2"
fi

# ---- 2. zram headroom ----------------------------------------------------
if grep -qE '^\s*memoryPercent\s*=\s*70;' "$CFG"; then
  echo "[2/3] zramSwap.memoryPercent already 70 — skipping"
else
  sed -i 's/^\(\s*\)memoryPercent = 50;/\1memoryPercent = 70;/' "$CFG"
  grep -qE '^\s*memoryPercent = 70;' "$CFG" || { echo "ERROR: zram edit did not apply"; false; }
  echo "[2/3] zramSwap: memoryPercent 50->70"
fi

# ---- 3. Validate ---------------------------------------------------------
echo "[3/3] parsing $CFG ..."
nix-instantiate --parse "$CFG" >/dev/null
echo "[3/3] parse OK"

# Past this point the file is valid; a failed rebuild is the operator's to
# judge, not something to auto-revert.
trap - ERR

echo
echo "Review the diff before applying:"
echo "    diff -u $BAK $CFG"
echo

if [[ -t 0 ]]; then
  read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans || ans=n
else
  ans=n
  echo "(non-interactive stdin — skipping the rebuild prompt)"
fi

if [[ ${ans:-n} =~ ^[Yy]$ ]]; then
  nixos-rebuild switch
else
  echo "Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch"
fi

echo
echo "Verify:"
echo "  nix config show | grep -E '^(cores|max-jobs)'   # expect cores=2, max-jobs=6"
echo "  cat /sys/block/zram0/disksize                   # expect ~86 GiB (was 61.7)"
echo "  cat /proc/pressure/cpu                          # compare 'some' against the 63% baseline"
echo
echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch"
