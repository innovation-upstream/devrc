#!/usr/bin/env bash
# Deterministic initiative-scan → Postgres sync pass for the systemd user timer.
#
# Runs scripts/initiatives/sync.py, which shells out to initiative-scan.py --json
# and writes one append-only snapshot into the homelab `mailbox` Postgres
# (initiatives schema) via a kubectl port-forward. KUBECONFIG + kubectl + network
# are the cluster requirements; git/gh are on PATH for the scan's git/PR reads.
#
# CLICKHOUSE_* ARE provisioned here now (runtime sops decrypt, NO plaintext secret at
# rest) so the scan runs TELEMETRY-ON, exactly like the /initiatives slash command:
# the activity reader password is decrypted from the homelab-talos secrets at run time
# and exported. Every decrypt step is GUARDED — a missing age key / absent repo /
# missing sops / empty decrypt leaves CLICKHOUSE_* unset and the scan degrades to
# telemetry-off (still writes a useful handoff+git+session snapshot; momentum/ev
# degrade). The decrypt deliberately never aborts the run and never persists the
# secrets yaml, so the unit is safe even if copied to the laptop (no age key there).
#
# Run by hand or via systemd:  systemctl --user start initiatives-sync.service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# KUBECONFIG defaults to the homelab admin config; the unit also sets it, but keep
# a default so the wrapper works when invoked from an interactive shell.
export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"

# Trailing window (default 4, matching the ledger); overridable via env for tuning.
DAYS="${INITIATIVES_SYNC_DAYS:-4}"

# --- ClickHouse reader creds — runtime sops decrypt (NO plaintext secret at rest) ---
# Mirrors the /initiatives slash command: decrypt the activity reader password from the
# homelab-talos secrets AT RUN TIME and export CLICKHOUSE_* so the scan runs telemetry-ON.
# Fully best-effort: any of {age key missing, repo absent, sops absent, decrypt empty}
# leaves CLICKHOUSE_* unset → sync.py runs telemetry-off (the scan already handles that).
# Each step is guarded so a decrypt miss can NEVER abort the sync (even under `set -e`),
# and the decrypted yaml lives only in a mktemp file that is trap-removed on return.
provision_clickhouse_creds() {
  local homelab_repo="${HOMELAB:-${HOME}/workspace/homelab-talos}"
  local age_key="${SOPS_AGE_KEY_FILE:-${homelab_repo}/.secrets/age.key}"

  if [ ! -r "$age_key" ]; then
    echo "run-sync: age key not readable ($age_key) — telemetry-off" >&2
    return 0
  fi
  if [ ! -d "${homelab_repo}/.git" ]; then
    echo "run-sync: homelab-talos repo absent ($homelab_repo) — telemetry-off" >&2
    return 0
  fi
  if ! command -v sops >/dev/null 2>&1; then
    echo "run-sync: sops not on PATH — telemetry-off" >&2
    return 0
  fi

  # Decrypt into a private temp file, trap-cleaned; never persist the secrets yaml.
  local enc
  enc="$(mktemp)" || { echo "run-sync: mktemp failed — telemetry-off" >&2; return 0; }
  # shellcheck disable=SC2064  # expand $enc now (at trap-set time), not at return
  trap "rm -f '$enc'" RETURN

  if ! git -C "$homelab_repo" show \
        origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml >"$enc" 2>/dev/null; then
    echo "run-sync: could not read encrypted secrets from homelab-talos — telemetry-off" >&2
    return 0
  fi

  # --input-type yaml is REQUIRED: the mktemp file has no .yaml extension, so sops
  # cannot auto-detect the format and would otherwise fail to unmarshal (the
  # /initiatives slash command sidesteps this by writing to a *.yaml path).
  local pw
  pw="$(SOPS_AGE_KEY_FILE="$age_key" sops -d --input-type yaml \
        --extract '["stringData"]["reader-password"]' "$enc" 2>/dev/null)" || pw=""
  if [ -z "$pw" ]; then
    echo "run-sync: reader-password decrypt yielded empty — telemetry-off" >&2
    return 0
  fi

  # Workbench LAN reader endpoint (NodePort). Overridable so a laptop copy could point
  # CLICKHOUSE_URL at its nebula CH endpoint; the password never touches the logs.
  export CLICKHOUSE_URL="${CLICKHOUSE_URL:-http://192.168.50.94:30123}"
  export CLICKHOUSE_USER="${CLICKHOUSE_USER:-activity_reader}"
  export CLICKHOUSE_PASSWORD="$pw"
  echo "run-sync: ClickHouse reader creds provisioned — telemetry-on" >&2
}

provision_clickhouse_creds

# nix-shell pulls the sync path's deps: psycopg2 (_db.py write) + requests (the
# scan's ClickHouse read). kubectl/git/gh/sops are provided on PATH by the unit
# (systemd) or the login shell (interactive) and inherited into the nix-shell; the
# CLICKHOUSE_* exports above are inherited too (nix-shell is not --pure).
exec nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' \
  --run "python ${SCRIPT_DIR}/sync.py --days ${DAYS}"
