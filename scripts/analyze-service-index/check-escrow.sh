#!/usr/bin/env bash
# Check the escrowed age key — the whole workflow, in one short command.
#
# 🔴 WHY THIS EXISTS. The documented way to run `--decrypt-check` was a ~300
# character one-liner with nested quoting: a `nix-shell -p …` carrying a
# `python3.withPackages(…)` expression, a `--run` string holding a command
# substitution for the master password, and two more substitutions inside that.
# Measured over three attempts by the operator it went wrong three different
# ways — a Python REPL (the script path token lost), `--identity: expected one
# argument` (the path token lost), and a run in a shell without `minio` (the
# package argument omitted). Every one of those is a QUOTING or PASTE failure,
# not a mistake about the task, and each cost a master-password entry.
#
# So the quoting lives here, once, in a file — where it is version-controlled,
# reviewed, and cannot be damaged in transit. The operator types one word.
#
# 🔴 THE ORDER IS THE POINT, and it is the fix from the #851 audit: this runs a
# LOCKED dry pass FIRST. That pass costs a second and proves the shell, the
# interpreter, the argv and the host label — and it cannot spend a password,
# because `escrow-verify.py` never prompts (every `bw` call runs with stdin on
# /dev/null). Only once it has failed with VAULT-LOCKED — the expected outcome
# — do we ask for the password. A wrong shell therefore costs a second, not a
# credential.
#
# Usage:
#   check-escrow.sh              # byte check + decrypt check against the bucket
#   check-escrow.sh --plan       # no bw, no network, no key: what WOULD run
#   check-escrow.sh -- <args>    # anything after `--` goes to escrow-verify.py
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$HERE/escrow-verify.py"
# The identity this subsystem encrypts to. NOT read from the environment on
# purpose: `SOPS_AGE_KEY_FILE` is exported by unrelated work and once pointed
# this comparison at an unrelated third party's key, whose mismatch read as a
# damaged escrow and whose remedy would have overwritten a good one.
IDENTITY="${HOME}/workspace/homelab-talos/.secrets/age.key"
HOST_LABEL="workbench-$(cat /etc/machine-id)"

# The one place the shell expression lives. `bitwarden-cli` for `bw`, `jq`
# because the documented pipeline uses it, and the python env because
# `--decrypt-check` reaches MinIO through a LAZY `minio` import — a shell
# without it resolves `python3` from the ambient profile, which lacks it.
NIX_PKGS=(-p bitwarden-cli jq 'python3.withPackages(p:[p.minio])')

EXTRA=()
PLAN_ONLY=0
case "${1:-}" in
  --plan) PLAN_ONLY=1 ;;
  --)     shift; EXTRA=("$@") ;;
  "")     ;;
  *)      EXTRA=("$@") ;;
esac

if [ "$PLAN_ONLY" = 1 ]; then
  exec nix-shell "${NIX_PKGS[@]}" --run \
    "python3 '$VERIFY' --print-plan --identity '$IDENTITY' --host '$HOST_LABEL'"
fi

# `${EXTRA[@]+"${EXTRA[@]}"}` — an empty array under `set -u` is an unbound
# variable on bash < 4.4; this form expands to nothing rather than erroring.
printf '=== 1/2  locked dry pass — proves the shell and argv, spends nothing\n'
set +e
nix-shell "${NIX_PKGS[@]}" --run \
  "python3 '$VERIFY' --decrypt-check --identity '$IDENTITY' --host '$HOST_LABEL' ${EXTRA[*]-}"
dry=$?
set -e

# 12 == VAULT-LOCKED, which is exactly what an un-unlocked vault must produce.
# Anything else means the run failed for a reason a password will not fix, so
# stop BEFORE asking for one — that is this script's whole job.
if [ "$dry" -ne 12 ]; then
  printf '\n=== dry pass exited %s, not 12 (VAULT-LOCKED).\n' "$dry"
  printf '=== NOT asking for the master password: this run failed for a reason\n'
  printf '===   unlocking will not fix. Read the message above.\n'
  exit "$dry"
fi

printf '\n=== 2/2  vault is locked as expected — unlocking now\n'
# 🔴 `&&` IS NOT ENOUGH: MEASURED, `bw unlock --raw` can exit ZERO having
# printed NOTHING (it does so when it cannot read a password — no TTY, EOF, or
# a cancelled prompt). The session is then the empty string, `export` succeeds,
# and the verifier runs against a vault it cannot open and reports
# VAULT-LOCKED — which reads as "the vault is locked" when what actually
# happened is "your unlock produced no session". Two different problems, one
# message. So the emptiness is checked explicitly and named.
exec nix-shell "${NIX_PKGS[@]}" --run \
  "BW_SESSION=\$(bw unlock --raw) || exit 12
   if [ -z \"\$BW_SESSION\" ]; then
     printf '=== bw unlock produced an EMPTY session (it exited 0 but printed nothing).\n' >&2
     printf '===   Wrong password, a cancelled prompt, or no terminal to read one.\n' >&2
     printf '===   NOT running the check: it would report VAULT-LOCKED and look\n' >&2
     printf '===   like a vault problem rather than an unlock that never happened.\n' >&2
     exit 12
   fi
   export BW_SESSION
   exec python3 '$VERIFY' --decrypt-check --identity '$IDENTITY' --host '$HOST_LABEL' ${EXTRA[*]-}"
