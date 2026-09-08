#!/usr/bin/env bash
# Staged system-level OOM protection for the operator's tmux server — 2026-09-07.
#
# Claude cannot run `sudo nixos-rebuild`, so this is STAGED, NOT APPLIED:
#
#   sudo bash nix/system/apply-tmux-oom-protection.sh
#
# Idempotent: safe to re-run. Restores the backup via an ERR trap.
#
# ─────────────────────────────────────────────────────────────────────────────
# 🔴 READ THIS BEFORE APPLYING: THIS DOES NOT ADDRESS THE 2026-09-07 INCIDENT
# ─────────────────────────────────────────────────────────────────────────────
#
# This script was written in response to the loss of the operator's tmux server
# and 47 live Claude conversations at 21:54:21 CDT on 2026-09-07, under the
# working hypothesis that memory pressure had killed it.
#
# THAT HYPOTHESIS IS FALSE, and it was falsified before this file was written.
# The measured cause was an agent running
#
#     TMUX_TMPDIR=$SCRATCH/run tmux kill-server
#
# at 21:54:15.802 CDT, believing `TMUX_TMPDIR` isolated it from the operator's
# server. It does not — a client inside a pane reads `$TMUX`, whose socket path
# wins. The 42 `tmux-spawn-*.scope` teardowns follow 1.2s later. The real fix is
# `check_tmux_kill_shared_server` in scripts/claude-hooks/guard_core.py, which
# blocks that command shape; it shipped in the same PR as this file.
#
# The evidence that NO out-of-memory mechanism fired, stated at the confidence
# it was measured:
#
#   - THE KERNEL OOM KILLER DID NOT RUN. `journalctl -k` for the incident boot
#     contains zero `oom-kill:` / `Killed process` / `Out of memory: Kill` lines.
#     🔴 That zero is instrument-validated, not a bare absence: the same grep
#     over the same journal DOES match real kernel OOM kills from 2026-08-28
#     (`oom-kill:constraint=CONSTRAINT_MEMCG … task=uvicorn`), so the pattern can
#     fire and simply did not. The kernel emits that line unconditionally before
#     it kills, so this is a hard negative rather than an ambiguous silence.
#
#   - systemd-oomd IS NOT INSTALLED on this host. `systemctl is-active
#     systemd-oomd` -> inactive, `systemctl cat systemd-oomd` -> "No files found".
#     `ManagedOOMPreference=avoid` would therefore be pure decoration here.
#
#   - THE TMUX SERVER DID NOT CRASH. Its `Max core file size` is `unlimited` and
#     `kernel.core_pattern` pipes to systemd-coredump, so a SIGSEGV/SIGABRT would
#     have been captured. `coredumpctl` for that window holds exactly one dump,
#     and it is `tsserver.js` (a TypeScript language server) hitting its own V8
#     per-process heap cap in a DIFFERENT scope — 24 ms after the kill-server,
#     and unrelated to it. There is no tmux core.
#
#   - THE "57.7G / 38.2G / 29.8G …" FIGURES DO NOT MEAN WHAT THEY LOOK LIKE.
#     They are systemd's per-scope `memory peak` accounting printed AT TEARDOWN,
#     each over that scope's whole lifetime (23h22m for most of them, 7h28m for
#     the 57.7G one). They are lifetime high-water marks for scopes that mostly
#     no longer overlapped, NOT concurrent usage at 21:54, and summing them is
#     not a measurement of anything.
#
# ─────────────────────────────────────────────────────────────────────────────
# SO WHY DOES THIS FILE EXIST, AND SHOULD YOU APPLY IT?
# ─────────────────────────────────────────────────────────────────────────────
#
# The operator asked for OOM protection specifically. It is defensible as
# PROPHYLAXIS — the box does run 40+ multi-GB agents on 123 GB — but it is
# prophylaxis against a mechanism that has never fired here, so it is staged for
# an explicit decision rather than applied. Both sides, measured:
#
#   FOR: the tmux server is a SINGLE POINT OF FAILURE for every conversation on
#        the box. Losing it costs all ~47 panes at once; losing any one pane
#        costs one. It is also a legitimately fat process — it holds every pane's
#        scrollback in its own address space (554 MB RSS eleven minutes after a
#        cold start, and it grows all day), so it is not a trivial target.
#
#   AGAINST: on the live ranking it is nowhere near the front of the queue.
#        Measured 2026-09-07: `tmux: server` oom_score 668 with oom_score_adj 0,
#        while the top of the box sits at 1332-1333 — every one of them a
#        Kubernetes container carrying an explicit oom_score_adj of 998-1000.
#        A global OOM would work through dozens of those before reaching tmux.
#        And no global OOM has ever occurred on this host; the only kernel OOM
#        kills in the journal are cgroup-limited kubepods kills.
#
# ─────────────────────────────────────────────────────────────────────────────
# 🔴 WHY THIS IS SYSTEM-LEVEL AND CANNOT BE DONE IN HOME-MANAGER
# ─────────────────────────────────────────────────────────────────────────────
#
# Two independent blockers, both measured — do not "simplify" this into
# nix/home.nix, it will silently do nothing:
#
#   1. AN UNPRIVILEGED PROCESS CANNOT LOWER `oom_score_adj`. Measured:
#        $ sh -c 'echo -500 > /proc/self/oom_score_adj'
#        sh: echo: write error: Permission denied      # rc=1
#        $ sh -c 'echo 500 > /proc/self/oom_score_adj' # rc=0 — RAISING is fine
#      Lowering requires CAP_SYS_RESOURCE. Anything the operator's own session
#      can do can only make things WORSE.
#
#   2. THE TMUX SERVER IS NOT IN A HOME-MANAGER UNIT. It is started
#      interactively, and lives in the logind session scope:
#        /proc/<tmux>/cgroup -> 0::/user.slice/user-1000.slice/session-3.scope
#      Its PANES are in `tmux-spawn-*.scope` under `user@1000.service`, but the
#      server itself is not, so `systemd.user.services.*.OOMScoreAdjust` and any
#      setting on `user@1000.service` both MISS IT. The scope name is also
#      per-login (`session-3` today), so it cannot be named declaratively.
#
# Hence: a tiny root-owned timer that finds the server by identity and lowers it.
#
# ─────────────────────────────────────────────────────────────────────────────
# 🔴 WHAT THIS DISPLACES — the question that nearly changed the design
# ─────────────────────────────────────────────────────────────────────────────
#
# `oom_score_adj` is a RANKING, so protecting tmux necessarily makes everything
# else relatively more attractive — including the Claude panes whose
# conversations are the thing actually worth saving. Protecting an empty
# workspace would be a bad trade. Three reasons it is nevertheless the right one:
#
#   a. The trade is strictly favourable GIVEN A KILL MUST HAPPEN. Sacrificing one
#      pane loses one conversation; sacrificing the server loses all of them.
#      There is no ordering in which killing the server first is preferable.
#
#   b. It mostly formalises the existing order rather than changing it. Badness
#      scales with RSS, and the heavy Claude panes ran 4-15 GB against the
#      server's 0.5 GB, so they already outrank it. This makes an accident
#      deliberate.
#
#   c. It deliberately does NOT raise anyone else's score. The alternative design
#      — pushing `OOMScoreAdjust=+N` onto the `tmux-spawn-*.scope` units — would
#      have reached the same relative ordering by making the CONVERSATIONS more
#      killable, and would also have applied to panes doing work unrelated to
#      Claude. Rejected for that reason. This script touches exactly one process.
#
# The residual risk is honest: if the true memory hog is ever the tmux server
# itself (runaway scrollback), this makes the kernel less willing to kill the
# one process that would free the memory. `history-limit` bounds that, and the
# timer's floor (-500, not -1000) leaves it killable rather than immune.
set -euo pipefail

CFG=/etc/nixos/configuration.nix
MODULE=/etc/nixos/tmux-oom-protection.nix
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: must run as root:  sudo bash $0" >&2
  exit 1
fi

BACKUP="${CFG}.bak-tmux-oom-${STAMP}"
cp -a "$CFG" "$BACKUP"
restore() { echo "FAILED — restoring $CFG from $BACKUP" >&2; cp -a "$BACKUP" "$CFG"; }
trap restore ERR

cat > "$MODULE" <<'NIXEOF'
# Lower the interactive tmux server's OOM badness. Generated by
# devrc nix/system/apply-tmux-oom-protection.sh — read that script's header
# first: it documents that the 2026-09-07 incident was NOT an OOM kill, and
# that this is prophylaxis against a mechanism which has never fired on this
# host.
#
# A timer rather than a unit property because the tmux server is started
# interactively and lives in a per-login `session-N.scope`, which cannot be
# named declaratively. Re-running is how it survives a tmux restart.
{ config, lib, pkgs, ... }:
{
  systemd.services.tmux-oom-protect = {
    description = "Lower the interactive tmux server's OOM badness";
    serviceConfig = {
      Type = "oneshot";
      # -500 not -1000: the server should be DISFAVOURED, never immune. If it is
      # ever itself the runaway (unbounded scrollback), the kernel must still be
      # able to reclaim it.
      ExecStart = pkgs.writeShellScript "tmux-oom-protect" ''
        set -u
        adjusted=0
        # Identity, not a pattern: `pgrep -x` matches the process NAME exactly,
        # so this can never reach a `tmux attach` client, an editor with "tmux"
        # in its command line, or — the hazard RULES.md names — the caller's own
        # shell. Nothing here kills anything; the worst case is a no-op.
        for pid in $(${pkgs.procps}/bin/pgrep -u 1000 -x tmux 2>/dev/null || true); do
          # Re-read identity at the moment of the write: the pid may have been
          # recycled between pgrep and here.
          comm=$(cat /proc/"$pid"/comm 2>/dev/null || true)
          case "$comm" in
            tmux*) ;;
            *) continue ;;
          esac
          if echo -500 > /proc/"$pid"/oom_score_adj 2>/dev/null; then
            adjusted=$((adjusted + 1))
          fi
        done
        echo "tmux-oom-protect: adjusted $adjusted tmux server process(es) to oom_score_adj=-500"
      '';
    };
  };

  systemd.timers.tmux-oom-protect = {
    description = "Re-apply tmux server OOM protection (survives a tmux restart)";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "2min";
      AccuracySec = "30s";
    };
  };
}
NIXEOF

# Idempotent import: add the module to configuration.nix's imports exactly once.
if ! grep -q "tmux-oom-protection.nix" "$CFG"; then
  # Insert immediately after the first `imports = [` line.
  awk '
    !done && /imports[[:space:]]*=[[:space:]]*\[/ {
      print; print "      ./tmux-oom-protection.nix"; done=1; next
    }
    { print }
  ' "$CFG" > "$CFG.new"
  if ! grep -q "tmux-oom-protection.nix" "$CFG.new"; then
    rm -f "$CFG.new"
    echo "ERROR: could not find an 'imports = [' line in $CFG — add" >&2
    echo "       ./tmux-oom-protection.nix to its imports by hand, then rebuild." >&2
    exit 1
  fi
  mv "$CFG.new" "$CFG"
  echo "added ./tmux-oom-protection.nix to $CFG imports"
else
  echo "$CFG already imports tmux-oom-protection.nix — leaving it alone"
fi

echo "rebuilding…"
nixos-rebuild switch

trap - ERR
echo
echo "APPLIED. Verify — and read the CONTENT, not just the exit code:"
echo "  systemctl start tmux-oom-protect && journalctl -u tmux-oom-protect -n 5"
echo "  for p in \$(pgrep -x tmux); do echo \"\$p adj=\$(cat /proc/\$p/oom_score_adj) score=\$(cat /proc/\$p/oom_score)\"; done"
echo
echo "Expect adj=-500 and a score ~500 lower than the 668 measured on 2026-09-07."
echo "An 'adjusted 0' line means it matched nothing — that is a failure, not a pass."
echo "Backup of the previous configuration.nix: $BACKUP"
