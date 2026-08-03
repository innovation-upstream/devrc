#!/usr/bin/env bash
# Size-cap the unrotated logs in ~/.claude.
#
# WHY: nothing rotates them. Measured 2026-08-02 on the workbench:
#   notify.log         39,822,110 B   (last written 2026-07-06 — a DEAD writer)
#   clawgate-hook.log  12,033,179 B   (live)
#   claude-notify.log      60,684 B
#   daemon.log              6,091 B
# 51.6 MB total, growing without bound. Only `claude-notify.log` has a writer in
# this repo (scripts/claude-hooks/claude-notify.py); `notify.log` and
# `clawgate-hook.log` are written by things OUTSIDE it, which is exactly why the
# cap has to live at the DIRECTORY, not in each writer.
#
# `copytruncate` is load-bearing and not an optimisation: the writers hold open
# file descriptors, they are not ours to signal, and one of them is a hook that
# runs on every Claude Code tool call. Renaming the file out from under an open
# fd would leave it appending to an unlinked inode — the log would appear frozen
# while silently consuming the same disk. copytruncate copies then truncates in
# place, so the open fd keeps writing to the same, now-empty, file.
#
# 🔴 SCOPE: `*.log` ONLY. It deliberately does NOT touch the 9 stale `.bak`
# files in that directory (settings.json.bak.*, RULES.md.bak*, PRINCIPLES.md.bak*,
# CLAUDE.md.bak-*). Those are hand-made safety copies of config; deleting them
# from an automated job is not this script's call to make. They are listed in
# the PR for manual removal.
#
# Run by hand or via systemd:
#   systemctl --user start claude-log-rotate.service
#   scripts/claude-log-rotate/rotate.sh [DIR]
#   scripts/claude-log-rotate/rotate.sh --print-config [DIR]   # config only, no run
set -euo pipefail

PRINT_ONLY=0
if [ "${1:-}" = "--print-config" ]; then
  PRINT_ONLY=1
  shift
fi

DIR="${1:-${HOME}/.claude}"

# Tunables. Defaults chosen against the measured sizes above: a 10M cap turns the
# 39.8 MB notify.log into one ≤10M live file plus at most 3 compressed
# generations, and clawgate-hook.log stops being a 12 MB single file.
SIZE="${CLAUDE_LOG_ROTATE_SIZE:-10M}"
KEEP="${CLAUDE_LOG_ROTATE_KEEP:-3}"
STATE="${CLAUDE_LOG_ROTATE_STATE:-${XDG_STATE_HOME:-${HOME}/.local/state}/claude-log-rotate.status}"

emit_config() {
  # NOTE the quoted glob: logrotate does its own globbing, and an unquoted
  # pattern here would be expanded by the shell into a fixed file list captured
  # at config-generation time — so a log created later would never be rotated.
  cat <<CONF
"${DIR}/*.log" {
    size ${SIZE}
    rotate ${KEEP}
    copytruncate
    compress
    delaycompress
    missingok
    notifempty
    nomail
}
CONF
}

if [ "$PRINT_ONLY" = "1" ]; then
  emit_config
  exit 0
fi

if ! command -v logrotate >/dev/null 2>&1; then
  echo "claude-log-rotate: logrotate not on PATH — refusing to report success" >&2
  exit 1
fi

mkdir -p "$(dirname "$STATE")"

conf="$(mktemp -t claude-log-rotate.conf.XXXXXX)"
trap 'rm -f "$conf"' EXIT
emit_config >"$conf"

logrotate --state "$STATE" "$conf"
echo "claude-log-rotate: applied size=${SIZE} rotate=${KEEP} to ${DIR}/*.log (state=${STATE})"
