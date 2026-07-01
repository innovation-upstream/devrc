#!/usr/bin/env bash
# repo-cos weekly runner — deterministic repo scan → LLM proposal synthesis → email
# digest. Fired by the workbench-only systemd user timer (serverMode; see nix/home.nix).
#
# Credentials (both kept OUT of the nix store):
#   - OPENROUTER_API_KEY  ← ~/.config/repo-cos/env (chmod 600, local secret file)
#   - Gmail app-password  ← decrypted at runtime by scan.py from the homelab SOPS
#     secret `mailbox-gmail-imap`, which needs SOPS_AGE_KEY_FILE (set below).
# Runs the scanner under nix-shell (python3+requests) with sops on PATH for the decrypt.
set -euo pipefail

ENV_FILE="${HOME}/.config/repo-cos/env"
if [ ! -r "$ENV_FILE" ]; then
  echo "repo-cos: missing $ENV_FILE (needs OPENROUTER_API_KEY) — skipping run" >&2
  exit 1
fi
set -a; . "$ENV_FILE"; set +a
export SOPS_AGE_KEY_FILE="${HOME}/workspace/homelab-talos/.secrets/age.key"

exec nix-shell -p 'python3.withPackages(p:[p.requests])' sops --run \
  "python ${HOME}/workspace/devrc/scripts/repo-cos/scan.py --email"
