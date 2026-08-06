#!/usr/bin/env bash
# Scheduled ClickHouse regrowth check for the activity store — systemd wrapper.
#
# Provisions the admin credential at RUN TIME (sops decrypt, NO plaintext secret
# at rest) and runs scripts/collector/ch_regrowth.py. See that file's docstring
# for what is asserted and why; this wrapper is only credential + endpoint
# plumbing.
#
# 🔴 EVERY GUARD HERE FAILS CLOSED. This is the deliberate opposite of
# scripts/initiatives/run-sync.sh, whose sops block is best-effort because a
# telemetry-off scan is still useful. Here a missing age key / absent repo /
# missing sops / empty decrypt means THE STORE WAS NOT MEASURED, and a check
# that cannot measure must never look like a check that measured a clean store.
# So each failure exits 2 (CANNOT TELL), which systemd records as a unit failure
# and OnFailure=notify-failure@%n.service turns into a sticky critical toast.
#
# Exit codes are ch_regrowth.py's, passed through unchanged:
#   0 ok · 1 ALARM · 2 CANNOT TELL · 3 WARN
#
# Run by hand or via systemd: systemctl --user start ch-regrowth-check.service
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXIT_UNKNOWN=2

die_unknown() {
  echo "ch-regrowth: $* — CANNOT TELL (the store was NOT measured; this is not a clean result)" >&2
  exit "$EXIT_UNKNOWN"
}

# KUBECONFIG for the `kubectl exec ... du` reading. The unit sets it too; keep a
# default so the wrapper works from an interactive shell. The workbench reaches
# the homelab API directly over the LAN; a laptop/off-LAN run needs
# KUBECONFIG=~/.kube/homelab-nebula.yaml and CH_REGROWTH_URL on the nebula IP.
export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"

# ClickHouse HTTP endpoint. Workbench LAN NodePort by default; overridable.
export CH_REGROWTH_URL="${CH_REGROWTH_URL:-http://192.168.50.94:30123}"
# `default` is the admin user; system-table reads need it (the activity_reader
# grant is scoped to activity.*).
export CH_REGROWTH_USER="${CH_REGROWTH_USER:-default}"

HOMELAB_REPO="${HOMELAB:-${HOME}/workspace/homelab-talos}"
AGE_KEY="${SOPS_AGE_KEY_FILE:-${HOMELAB_REPO}/.secrets/age.key}"
SECRETS_REL="clusters/homelab/apps/activity/secrets.enc.yaml"

if [ -z "${CH_REGROWTH_PASSWORD:-}" ]; then
  [ -r "$AGE_KEY" ] || die_unknown "age key not readable ($AGE_KEY)"
  [ -d "${HOMELAB_REPO}/.git" ] || die_unknown "homelab-talos repo absent ($HOMELAB_REPO)"
  command -v sops >/dev/null 2>&1 || die_unknown "sops not on PATH"

  SECRETS_PATH="${HOMELAB_REPO}/${SECRETS_REL}"
  [ -r "$SECRETS_PATH" ] || die_unknown "encrypted secrets not readable ($SECRETS_PATH)"

  # --extract keeps the secret off argv; it is passed to python via the
  # environment, which is readable only by this uid.
  PW="$(SOPS_AGE_KEY_FILE="$AGE_KEY" sops -d \
        --extract '["stringData"]["admin-password"]' "$SECRETS_PATH" 2>/dev/null)" || PW=""
  [ -n "$PW" ] || die_unknown "admin-password decrypt yielded empty"
  export CH_REGROWTH_PASSWORD="$PW"
  unset PW
fi

command -v kubectl >/dev/null 2>&1 || die_unknown "kubectl not on PATH"

exec python3 "${SCRIPT_DIR}/ch_regrowth.py" "$@"
