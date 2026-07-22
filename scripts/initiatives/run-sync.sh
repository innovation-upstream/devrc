#!/usr/bin/env bash
# Deterministic initiative-scan → Postgres sync pass for the systemd user timer.
#
# Runs scripts/initiatives/sync.py, which shells out to initiative-scan.py --json
# and writes one append-only snapshot into the homelab `mailbox` Postgres
# (initiatives schema) via a kubectl port-forward. KUBECONFIG + kubectl + network
# are the cluster requirements; git/gh are on PATH for the scan's git/PR reads.
#
# CLICKHOUSE_* are NOT set here — see the PR: cred provisioning for the timer's
# environment is an OPEN follow-up. Unset -> the scan runs telemetry-off (still
# writes a useful handoff+git+session snapshot; momentum/ev degrade).
#
# Run by hand or via systemd:  systemctl --user start initiatives-sync.service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# KUBECONFIG defaults to the homelab admin config; the unit also sets it, but keep
# a default so the wrapper works when invoked from an interactive shell.
export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"

# Trailing window (default 4, matching the ledger); overridable via env for tuning.
DAYS="${INITIATIVES_SYNC_DAYS:-4}"

# nix-shell pulls the sync path's deps: psycopg2 (_db.py write) + requests (the
# scan's ClickHouse read). kubectl/git/gh are provided on PATH by the unit (systemd)
# or the login shell (interactive) and inherited into the nix-shell.
exec nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' \
  --run "python ${SCRIPT_DIR}/sync.py --days ${DAYS}"
