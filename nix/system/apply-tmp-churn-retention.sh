#!/usr/bin/env bash
# /tmp churn retention — workbench, 2026-08-15.
#
# Claude cannot run `sudo nixos-rebuild`, so the /etc/nixos edit is staged here.
# Idempotent: safe to re-run.
#
#   sudo bash nix/system/apply-tmp-churn-retention.sh
#
# Safe on a non-TTY (pipe, ssh without -t, agent shell): the prompt is skipped
# rather than aborting mid-edit, and any error restores the backup via an ERR
# trap — same shape as apply-perf-tuning-2026-07-30.sh next door.
#
# ── THE PROBLEM, AND WHY THE OBVIOUS FIX IS NOT IT ───────────────────────────
#
# workbench went DiskPressure=True on 2026-08-13 and evicted node-wide (clawgate,
# supabase, remix-worker, auditloop, orchestrator-devpod). The disk genuinely
# filled: 1.7 TB of 1.8 TB, down to 101 GiB free against a ~92 GiB eviction line.
#
# 🔴 Do NOT reach for the two fixes the incident handoff proposed — both are
# no-ops, measured 2026-08-15:
#   - "relax eviction-hard to 5%": it is ALREADY 5%. That is a k3s BUILT-IN
#     default, present in no config file, surviving every rebuild. (And writing
#     the flag by hand is hazardous: eviction-hard REPLACES the whole default
#     set, so spelling only the two disk signals silently drops memory.available
#     and both inodesFree guards.)
#   - "add systemd-tmpfiles ageing on /tmp": it ALREADY exists. systemd ships
#     `q /tmp 1777 root root 10d` and systemd-tmpfiles-clean.timer runs daily and
#     succeeds. `systemd-tmpfiles --clean --dry-run` reclaims 0.55 GiB.
#
# ── ROOT CAUSE: atime keeps stale dirs immortal ──────────────────────────────
#
# systemd-tmpfiles ages on the MOST RECENT of atime/mtime/ctime. Measured on
# real /tmp entries:
#
#   /tmp/nix-shell-3304470-3498967483   m=2026-08-02  a=2026-08-14  c=2026-08-02
#   /tmp/nix-shell-2760455-2726689738   m=2026-07-30  a=2026-08-14  c=2026-07-30
#
# mtime/ctime are weeks old; atime is yesterday. So the 10d rule sees "1 day old"
# and never expires them.
#
# 🔴 And what refreshes atime is SCANNING /tmp with du or find — precisely what
# investigating a full disk requires. Every investigation resets the clock on
# everything it looks at. That is why /tmp sat at 362 GB with a working cleaner.
#
# ── THE FIX: mtime-only ageing, SCOPED to machine-generated churn ────────────
#
# systemd >= 253 accepts an age-by prefix (`m:7d` = consider mtime only). Verified
# on this host's systemd 258 by control pair: `z:7d` -> "Invalid age-by 'z'",
# `m:7d` -> parses and matches.
#
# 🔴 It is scoped by GLOB, not applied to /tmp as a whole, because /tmp holds
# real work that a blanket rule would destroy. Measured: a blanket mtime-only
# 7d rule would have deleted TWO LIVE GIT WORKTREES —
#   /tmp/wt-apps-ui-3497  (14d)  gitdir: ~/workspace/civit/civitai/.git/worktrees/...
#   /tmp/wt-waitunit      (10d)  gitdir: ~/workspace/civit/civitai/.git/worktrees/...
# possibly carrying uncommitted work. Hence: machine-generated prefixes only.
#
# Coverage, measured 2026-08-15 (dirs / of those with mtime >7d):
#   nix-shell-*           1003 /  988      go-build*              114 /  98
#   nix-develop-*         1196 /  392      chromedp-runner*       158 / 158
#   nix-[0-9]*             778 /  612      homelab-talos-prs-*   2554 / 2537
# Every glob matches real entries — none is a dead rule.
#
# Dry-run of the exact rule set below: 4,255,261 entries would be removed, and
# ZERO matches against /tmp/wt-*, /tmp/claude-1000 or the named project dirs
# (civitai-integration, peakmaps, chunkcache, pnpmvar, audit187). Estimated
# reclaim ~47 GB, a LOWER BOUND — the dry-run ran unprivileged and cannot see
# root-owned paths.
#
# NOT covered on purpose:
#   - /tmp/claude-1000 (52 GB): Claude session scratchpads. The stock 10d /tmp
#     rule already covers them and they are the user's own working state; a
#     mid-session sweep is not worth 52 GB.
#   - /tmp/wt-*: real git worktrees. Never age these automatically.
#
# Rollback: cp -a the backup this script prints, then nixos-rebuild switch. The
# rules are additive and delete nothing at rebuild time — cleanup happens on the
# next systemd-tmpfiles-clean.timer firing.

set -euo pipefail

CFG=/etc/nixos/configuration.nix
BAK="${CFG}.bak-tmp-churn-$(date +%Y%m%d-%H%M%S)"
MARKER='/tmp churn retention'

if [[ $EUID -ne 0 ]]; then
  echo "This script edits ${CFG} and must run as root:" >&2
  echo "    sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "$CFG" ]]; then
  echo "ERROR: ${CFG} not found — is this workbench?" >&2
  exit 1
fi

cp -a "$CFG" "$BAK"
echo "Backup: ${BAK}"

restore_on_err() {
  echo "ERROR — restoring ${CFG} from ${BAK}" >&2
  cp -a "$BAK" "$CFG"
}
trap restore_on_err ERR

if grep -qF "$MARKER" "$CFG"; then
  echo "[1/1] tmp churn retention rules already present — skipping edit"
else
  echo "[1/1] inserting tmpfiles rules into systemd.tmpfiles.rules"
  python3 - "$CFG" <<'PY'
import sys
path = sys.argv[1]
src = open(path).read()
anchor = "  systemd.tmpfiles.rules = [\n"
if anchor not in src:
    raise SystemExit("ERROR: could not find 'systemd.tmpfiles.rules = [' in configuration.nix")
rules = '''    # /tmp churn retention (2026-08-15). mtime-ONLY ageing (`m:`), because
    # systemd-tmpfiles ages on the newest of atime/mtime/ctime and any `du`/`find`
    # over /tmp refreshes atime — which made the stock 10d rule never expire
    # anything. Scoped to machine-generated prefixes ONLY: a blanket rule would
    # delete live git worktrees parked in /tmp. See nix/system/apply-tmp-churn-retention.sh.
    "e /tmp/nix-shell-* - - - m:7d"
    "e /tmp/nix-develop-* - - - m:7d"
    "e /tmp/nix-[0-9]* - - - m:7d"
    "e /tmp/go-build* - - - m:7d"
    "e /tmp/chromedp-runner* - - - m:7d"
    "e /tmp/homelab-talos-prs-* - - - m:7d"
'''
open(path, "w").write(src.replace(anchor, anchor + rules, 1))
PY
  grep -qF "$MARKER" "$CFG" || { echo "ERROR: tmpfiles edit did not apply"; false; }
  grep -qF 'e /tmp/nix-shell-* - - - m:7d' "$CFG" || { echo "ERROR: rule text missing after edit"; false; }
  echo "      rules inserted and verified present"
fi

trap - ERR

echo
echo "Config edited. Validate the rule syntax BEFORE rebuilding:"
echo "    systemd-tmpfiles --clean --dry-run 2>&1 | head"
echo "  (a bad age-by prefix reports 'Invalid age-by' and names the file:line)"
echo

if [[ -t 0 ]]; then
  read -r -p "Run 'nixos-rebuild switch' now? [y/N] " ans || ans=n
else
  echo "Non-interactive shell — skipping the rebuild prompt."
  ans=n
fi

if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
  if ! nixos-rebuild switch; then
    echo >&2
    echo "nixos-rebuild FAILED. The config edits are still in place." >&2
    echo "Rollback: cp -a ${BAK} ${CFG} && nixos-rebuild switch" >&2
    exit 1
  fi
  echo
  echo "Switched. Cleanup runs on the next systemd-tmpfiles-clean.timer firing;"
  echo "to trigger it now:  systemctl start systemd-tmpfiles-clean.service"
else
  echo "Config edited and validated; nixos-rebuild NOT run. Run it when ready:"
  echo "    sudo nixos-rebuild switch"
fi

echo
echo "Rollback: cp -a ${BAK} ${CFG} && nixos-rebuild switch"
