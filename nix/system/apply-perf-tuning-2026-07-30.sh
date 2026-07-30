#!/usr/bin/env bash
# Staged system-level perf tuning — workbench, 2026-07-30.
#
# Claude cannot run `sudo nixos-rebuild`, so the /etc/nixos edits from the
# perf investigation are staged here. Idempotent: safe to re-run.
#
#   sudo bash nix/system/apply-perf-tuning-2026-07-30.sh
#
# Each change and the evidence behind it:
#
#  1. nix.settings max-jobs/cores — currently UNSET, so Nix uses stock defaults
#     max-jobs=auto(24) x cores=0(=24 threads per job). Per the Nix manual,
#     "the maximum number of consumed cores is a simple multiplication,
#     max-jobs * NIX_BUILD_CORES" — 24 jobs each requesting 24 threads on a
#     12C/24T part. Measured symptom: load 35-69 on 24 threads, 195-275k
#     context switches/s, PSI cpu some=63% while full=0% (contention, not
#     saturation). 6x2 budgets ~12 of 24 threads to builds, leaving the rest
#     for the agent fleet and desktop. Heuristic, not a benchmarked optimum.
#
#  2. zramSwap memoryPercent 50 -> 70, plus a memoryMax ceiling — zram is
#     chronically at ~96% of its 61.7 GiB LOGICAL disksize (swap-free logged
#     at 0.03-2.72% across six consecutive hours; hit 3.9 MiB free once)
#     while costing only ~14.6 GiB of real RAM at a measured 3.4x compression.
#     Exhausting disksize is a cliff: the driver returns ENOSPC and, with no
#     other swap device, that falls through to reclaim/OOM pressure.
#     memoryMax bounds the worst case if a future workload compresses badly.
#
#  3. systemd-sysctl restart — NOT a config change, a correctness fix.
#     systemd-sysctl.service last activated at BOOT (2026-07-05) while
#     /run/current-system last switched 2026-07-21. `nixos-rebuild switch`
#     does not restart it (NixOS/nixpkgs#289174), so EVERY boot.kernel.sysctl
#     edit made since the last reboot has been inert on the live kernel.
#     This is why vm.watermark_scale_factor read 131 live vs 125 in config.
#
# DELIBERATELY NOT INCLUDED (need a decision or measurement first):
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
echo "[0/4] backed up $CFG -> $BAK"

# ---- 1. Nix build parallelism -------------------------------------------
if grep -qE '^\s*nix\.settings\.max-jobs' "$CFG"; then
  echo "[1/4] nix.settings.max-jobs already present — skipping"
else
  # Anchor on the existing experimental-features line.
  sed -i '/^\s*nix\.settings\.experimental-features/a\
\
  # Bound total build parallelism. Stock defaults (max-jobs=auto x cores=0)\
  # let 24 concurrent jobs each request all 24 threads; the Nix manual warns\
  # this degrades throughput "due to extensive context switching". 6x2 keeps\
  # ~half the box free for interactive agent work during a build.\
  nix.settings.max-jobs = 6;\
  nix.settings.cores = 2;' "$CFG"
  echo "[1/4] added nix.settings.max-jobs=6 / cores=2"
fi

# ---- 2. zram headroom ----------------------------------------------------
if grep -qE '^\s*memoryPercent\s*=\s*70' "$CFG"; then
  echo "[2/4] zramSwap.memoryPercent already 70 — skipping"
else
  sed -i 's/^\(\s*\)memoryPercent = 50;/\1memoryPercent = 70;\n\1# Ceiling on worst-case RAM cost if compression ratio ever degrades\n\1# toward 1:1 (measured 3.4x today with zstd).\n\1memoryMax = 100 * 1024 * 1024 * 1024;/' "$CFG"
  grep -qE '^\s*memoryPercent = 70;' "$CFG" \
    || { echo "ERROR: zram edit did not apply — restoring backup"; cp -a "$BAK" "$CFG"; exit 1; }
  echo "[2/4] zramSwap: memoryPercent 50->70, added memoryMax=100GiB"
fi

# ---- 3. Validate before switching ---------------------------------------
echo "[3/4] parsing $CFG ..."
nix-instantiate --parse "$CFG" >/dev/null \
  || { echo "ERROR: $CFG does not parse — restoring backup"; cp -a "$BAK" "$CFG"; exit 1; }
echo "[3/4] parse OK"

echo
echo "Review the diff before applying:"
echo "    diff -u $BAK $CFG"
echo
read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans
if [[ ${ans:-n} =~ ^[Yy]$ ]]; then
  nixos-rebuild switch
else
  echo "Skipped nixos-rebuild. Run it yourself when ready."
fi

# ---- 4. Apply sysctls to the LIVE kernel --------------------------------
# Must happen regardless: switch alone does not restart this unit.
echo "[4/4] restarting systemd-sysctl (switch does NOT do this — nixpkgs#289174)"
systemctl restart systemd-sysctl.service

echo
echo "Verify:"
echo "  nix show-config | grep -E '^(cores|max-jobs)'   # expect cores=2, max-jobs=6"
echo "  cat /sys/block/zram0/disksize                   # expect ~86 GiB (was 61.7)"
echo "  sysctl -n vm.watermark_scale_factor             # expect 125 (config value, was 131)"
echo "  cat /proc/pressure/cpu                          # expect 'some' below the 63% baseline"
echo
echo "Rollback: cp -a $BAK $CFG && nixos-rebuild switch"
