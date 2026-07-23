#!/usr/bin/env bash
# Long-running launcher for the initiatives live web viewer (Phase 3).
#
# Serves scripts/initiatives/viewer.py: an auto-refreshing HTML page over the
# homelab `mailbox` Postgres `initiatives.latest` view (read via a kubectl
# port-forward), plus a LIVE tmux overlay read from THIS host at render time.
# Binds a workbench-LAN address by default; NOT wired into the public gateway.
#
# Unlike run-sync.sh this needs NO ClickHouse/sops creds — the viewer only READS
# the already-synced store; it does not re-run the scan's telemetry query. The scan
# module is imported ONLY for its tmux machinery (best-effort; absent if no server).
#
# Requirements on PATH/env (provided by the systemd unit or the login shell):
#   KUBECONFIG  — homelab kubeconfig (the DB is only reachable via port-forward)
#   kubectl     — the port-forward
#   git         — the scan's repo/worktree discovery for the tmux overlay
#   tmux        — the live pane read (overlay degrades to absent if missing)
# The nix-shell below adds psycopg2 (the DB read) + requests (the scan import).
#
# Run by hand or via systemd:  systemctl --user start initiatives-viewer.service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# KUBECONFIG defaults to the homelab admin config; the unit also sets it, but keep a
# default so the wrapper works when invoked from an interactive shell.
export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"

# Bind address/port — the workbench's OWN LAN IP (eth1) by default; override to 127.0.0.1
# for local-only. NOT 192.168.50.94 — that is a homelab node (kube-apiserver/NodePorts),
# not assignable here; the systemd unit sets INITIATIVES_VIEWER_HOST=192.168.50.250 too.
HOST="${INITIATIVES_VIEWER_HOST:-192.168.50.250}"
PORT="${INITIATIVES_VIEWER_PORT:-8899}"

exec nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' \
  --run "python ${SCRIPT_DIR}/viewer.py --host ${HOST} --port ${PORT}"
