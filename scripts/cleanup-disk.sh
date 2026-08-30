#!/usr/bin/env bash
set -euo pipefail

# 🔴 DRY-RUN BY DEFAULT — destructive steps need an explicit `--apply`.
#
# Round 2 of the audit on devrc#1057 found that removing the sudo call (see the
# journal block below) closed ONE instance of the laundering hazard and left two
# WORSE-rated ones. MEASURED through oc_permissions.py over opencode.jsonc:
#
#   rm -rf /home/zach/.local/share/NuGet                     -> deny  [*rm -rf /*]
#   rm -rf /home/zach/.local/share/dp-prod-ssr-regression-*  -> deny  [*rm -rf /*]
#   bash <anywhere>/cleanup-disk.sh                          -> allow [*]
#
# `deny` outranks the `ask` that was removed, so a tracked, allow-rated script
# was laundering two deny-rated recursive deletes past a ledger that matches on
# the command STRING and cannot see inside an invoked script file. That gating
# was deliberately hardened in #744 and verified live; defeating it by accident
# is exactly what this file's own comment says not to do.
#
# ⚠ Rewriting `rm -rf` as `find -delete` would dodge the rule WITHOUT closing
# the hazard — MEASURED: `find / -delete` resolves `allow`, so the ledger would
# never see it. That is gaming the ledger, not respecting it.
#
# 🔴 WHAT THE GATE BELOW DOES AND DOES NOT DO — read this before trusting it.
# It NARROWS the hazard; it does NOT close it. An earlier version of this
# comment claimed the gate meant "the allow-rating grants nothing". That was
# FALSE, and audit round 3 measured it:
#
#   bash <anywhere>/cleanup-disk.sh            -> allow [*]
#   bash <anywhere>/cleanup-disk.sh --apply    -> allow [*]   <-- equally allowed
#
# `--apply` is rated exactly as permissively as the bare form. The ledger is
# blind to both, so an agent that decides to pass `--apply` still executes two
# `deny`-rated recursive deletes unattended. What the gate actually buys is
# narrower and still worth having: an ACCIDENTAL or incurious invocation is
# inert, and deleting anything now takes a deliberate, auditable token rather
# than happening as a side effect of "free up some disk space".
#
# The residual hole is real and is NOT closed here: closing it would mean the
# script printing the `rm -rf` commands for the caller to run — so the caller
# hits the `deny` — which turns this into a printer and defeats its purpose.
# That trade is the operator's to make, not this file's.
#
# Same shape as the repo's other operator-acts (`sync-skill-tiers.py`,
# `sync-claude-permissions.py`): dry-run unless `--apply`.
APPLY=0
for _a in "$@"; do
  [ "$_a" = "--apply" ] && APPLY=1
done

if [ "$APPLY" -ne 1 ]; then
  echo "=== Disk cleanup — DRY RUN (nothing will be deleted) ==="
  echo "Would clean:"
  echo "  - Go build cache            (go clean -cache)"
  echo "  - Bazel cache              /home/zach/.cache/bazel/*"
  echo "  - pnpm store               (pnpm store prune)"
  echo "  - NuGet cache              /home/zach/.local/share/NuGet"
  echo "  - dp-prod test artifacts   /home/zach/.local/share/dp-prod-ssr-regression-*"
  echo
  # 🔴 The escalation hint is printed ONLY to an interactive terminal.
  # Audit round 3: "the dry-run text IS the escalation instruction — the gate
  # prints its own bypass to whoever is reading, and an agent is a reader."
  # A headless agent capturing stdout is not handed the next step. This is a
  # speed bump, not a boundary — the flag is still discoverable by reading the
  # script — but it stops the inert path from advertising the live one.
  if [ -t 1 ]; then
    echo "Re-run with --apply to actually delete."
  else
    echo "Nothing was deleted. Re-run interactively to see how to proceed."
  fi
  echo "Not included (needs root, run it yourself):"
  echo "  sudo journalctl --vacuum-size=500M"
  exit 0
fi

echo "=== Disk cleanup ==="
echo "Before: $(df -h / | tail -1 | awk '{print $4}') free"

# Go build cache (no sudo needed)
echo "Cleaning Go build cache..."
go clean -cache 2>/dev/null && echo "  ✓ go-build cache" || echo "  ✗ go-build failed"

# Bazel cache (owned by user but some files are immutable)
echo "Cleaning Bazel cache..."
find /home/zach/.cache/bazel -mindepth 1 -delete 2>/dev/null || true
echo "  ✓ bazel cache"

# pnpm store prune
echo "Pruning pnpm store..."
pnpm store prune 2>/dev/null && echo "  ✓ pnpm store" || echo "  ✗ pnpm failed"

# NuGet cache
echo "Cleaning NuGet cache..."
rm -rf /home/zach/.local/share/NuGet && echo "  ✓ NuGet" || echo "  ✗ NuGet failed"

# Test artifacts
echo "Cleaning test artifacts..."
rm -rf /home/zach/.local/share/dp-prod-ssr-regression-* && echo "  ✓ dp-prod artifacts" || echo "  ✗ dp-prod failed"

# System journal — DELIBERATELY NOT DONE HERE.
#
# 🔴 The original untracked copy of this script ran:
#
#     sudo journalctl --vacuum-size=500M
#
# It is removed rather than preserved, and the reason is a privilege gate, not
# style. MEASURED through the repo's own resolver
# (scripts/opencode/lib/oc_permissions.py over scripts/opencode/opencode.jsonc):
#
#     sudo journalctl --vacuum-size=500M          -> ask
#     bash <anywhere>/cleanup-disk.sh             -> allow
#
# The sudo rules in opencode.jsonc match on the COMMAND STRING, and guard_core.py
# recurses into `bash -c '…'` but cannot read a script FILE. So a tracked script
# that calls sudo internally is an `allow`-rated route to an `ask`-rated action —
# it hands the headless opencode agent the privileged call the ledger says to
# stop and ask about.
#
# ⚠ Moving this file does NOT fix that: nix/system/apply-disk-cleanup.sh measures
# `allow` too (checked). Any script path is allow-rated, so the only effective
# fix is for the script to contain no sudo call.
#
# Run it yourself when you want it:
#     sudo journalctl --vacuum-size=500M
echo "Skipping systemd journal (needs sudo — run it yourself):"
echo "  sudo journalctl --vacuum-size=500M"

echo ""
echo "After: $(df -h / | tail -1 | awk '{print $4}') free"
