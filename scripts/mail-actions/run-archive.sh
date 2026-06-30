#!/usr/bin/env bash
# Deterministic invoice-archive pass for the systemd user timer.
#
# Scans the homelab Postgres `mail` table for invoice PDFs and uploads them
# (+ JSON sidecars) to the minio-archive bucket `taxes-{year}-invoices`. This
# is the NO-LLM path: it reaches the cluster via `kubectl port-forward`, pulling
# MinIO creds + the PG DSN from k8s secrets itself, so KUBECONFIG + kubectl +
# network are the only external requirements. Idempotent — already-archived
# invoices are skipped (a clean run reports 0 new candidates).
#
# Run by hand or via systemd:  systemctl --user start mail-actions-archive.service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# KUBECONFIG defaults to the homelab admin config; the unit also sets it, but
# keep a default so the wrapper works when invoked from an interactive shell.
export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"

# nix-shell pulls the archive path's deps: psycopg2 (_db.py), minio (_minio.py),
# requests (llm.py imports it lazily — included to be safe). kubectl is provided
# on PATH by the unit (systemd) or the login shell (interactive).
exec nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.minio p.requests])' \
  --run "python ${SCRIPT_DIR}/extract.py archive-invoices"
