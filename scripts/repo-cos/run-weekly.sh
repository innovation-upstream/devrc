#!/usr/bin/env bash
# repo-cos weekly runner — deterministic repo scan → LLM proposal synthesis → email
# digest. Fired by the workbench-only systemd user timer (serverMode; see nix/home.nix).
#
# SELF-HOSTED mail (default paths):
#   SEND  — the digest goes out via Zach's OWN postfix relay (From: repo-cos@mail.zacx.dev,
#           DKIM-signed; Reply-To: repo-cos@inbox.zacx.dev). The relay lives in the
#           PRODUCTION cluster and is only reachable via a `kubectl port-forward`, so this
#           needs PROD_KUBECONFIG + kubectl. No SMTP auth / app-password (relay trusts
#           MYNETWORKS over the localhost hop). See email_send.py.
#   READ  — his reply to repo-cos@inbox.zacx.dev routes Gmail→his MX→mail-receiver→the
#           homelab Postgres `mail` table. feedback.py reads it via a `kubectl port-forward`
#           to the HOMELAB cluster (HOMELAB_KUBECONFIG) + psycopg2. See feedback.py.
#
# So the weekly send now depends on BOTH clusters + TWO port-forwards. Both are BEST-EFFORT:
# a relay/postgres hiccup logs + skips (send fails loudly, feedback returns None) rather
# than wedging the run. The Python resolves each kubeconfig per operation (relay→production,
# reply-read→homelab).
#
# Credentials / config (all kept OUT of the nix store):
#   - OPENROUTER_API_KEY   ← ~/.config/repo-cos/env (chmod 600, local secret file)
#   - PROD_KUBECONFIG      ← production cluster (postfix relay send)
#   - HOMELAB_KUBECONFIG   ← homelab cluster (Postgres reply-read; also the mail-actions DB)
#   - SOPS/Gmail app-pw    — ONLY needed for the fallback paths (REPO_COS_SEND=gmail or
#     REPO_COS_REPLY_SRC=imap); the default relay+postgres paths need no app-password.
#
# Runs the scanner under nix-shell (python3 + requests + psycopg2), with kubectl + sops on
# PATH (kubectl for both port-forwards; sops only for the Gmail fallback decrypt).
set -euo pipefail

ENV_FILE="${HOME}/.config/repo-cos/env"
if [ ! -r "$ENV_FILE" ]; then
  echo "repo-cos: missing $ENV_FILE (needs OPENROUTER_API_KEY) — skipping run" >&2
  exit 1
fi
set -a; . "$ENV_FILE"; set +a

# Two kubeconfigs — the Python picks each per operation:
#   relay send  → REPO_COS_PROD_KUBECONFIG (email_send.py, production cluster)
#   reply read  → KUBECONFIG (feedback.py → mail-actions/_db.py, homelab cluster)
# feedback's _db.py reads KUBECONFIG, so KUBECONFIG must be the HOMELAB one; the relay
# path reads its own REPO_COS_PROD_KUBECONFIG env and does not touch KUBECONFIG.
export REPO_COS_PROD_KUBECONFIG="${REPO_COS_PROD_KUBECONFIG:-${HOME}/workspace/homelab-talos/production-kubeconfig}"
export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"

# SOPS age key — only used by the Gmail fallback (REPO_COS_SEND=gmail / REPO_COS_REPLY_SRC=imap).
export SOPS_AGE_KEY_FILE="${HOME}/workspace/homelab-talos/.secrets/age.key"

exec nix-shell -p 'python3.withPackages(p:[p.requests p.psycopg2])' kubectl sops --run \
  "python ${HOME}/workspace/devrc/scripts/repo-cos/scan.py --email"
