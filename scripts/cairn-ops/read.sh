#!/usr/bin/env bash
# read.sh — the READ cluster of the cairn surface: recall, search, ls-entries.
#
# 🔴 WHAT IT ADDS OVER TYPING `cairn recall` IS A WRAPPER-OWNED REFUSAL CODE, AND
# THAT CLAIM IS NARROWER THAN THE ONE THIS COMMENT USED TO MAKE. It said a
# scope-less read "returns an EMPTY REPORT rather than an error". MEASURED against
# the deployed client, that is false: `cairn recall` and `cairn search` with no
# resolvable scope refuse at **rc 2** naming the remedy —
# `could not derive a scope from '.': … pass --scope explicitly` — and the client's
# own comment records the older shape as rc 1 plus a traceback, so there is no
# version of it that answered with an empty report. Nothing here is closing a
# silent-zero hole, because the client does not leave one.
#
# What the pre-flight is actually for: the refusal arrives with a code in the
# >=19 band this directory owns (see `common.sh`), so a caller branching on the
# exit status can tell "the wrapper refused before doing anything" from "the store
# refused" — which the client's own 2 (shared with every usage error) cannot. It is
# co-extensive with the client's refusal, deliberately: the client's only scope
# sources are `--scope` and `scope_for_repo(--repo)` with `--repo` defaulting to
# `.`, so there is no case where this refuses a read the client would have served.
#
# 🔴 AND IT COVERS `recall` AND `search` ONLY — `ls-entries` TAKES NO SCOPE.
# `ls-entries` was in `recall`'s arm once, which refused a listing from a non-repo
# cwd at rc 20 that the bare client answers at rc 0 with every entry on every
# instance. The client IGNORES `--scope` for it (measured; the note is on
# `_CAIRN_STUB` in `test_cairn_ops.py`), so the guard was not merely wrong but
# walkable — a meaningless `--scope` value satisfied it and changed the output not
# at all. Both halves are pinned by
# `test_cairn_ops.py::TestTheScopePreflightCoversOnlyTheVerbsThatNeedAScope`.
# 🔴 SO: DO NOT ADD A VERB TO THE PRE-FLIGHT ARM WITHOUT MEASURING WHETHER THE
# CLIENT DERIVES A SCOPE FOR IT. Guarding a verb that needs no scope turns a
# working read into a refusal, and that is the direction nobody notices.
#
# 🔴 STDOUT IS THE CLIENT'S, BYTE FOR BYTE. Every line this script emits goes to
# stderr. `/resume`, `/handoff` and `/analyze-service` compare recall output
# against prior captures, and a wrapper that prefixed a summary line would make
# every one of those comparisons red for a reason that is not a defect.
set -uo pipefail

CAIRN_OPS_NAME="read.sh"
# shellcheck source=common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# read.sh's OWN refusal. See the header: a read with no resolvable scope.
readonly EXIT_NO_SCOPE=20

usage() {
  cat <<USAGE
read.sh — the READ cluster of the cairn surface. One call, one answer.

usage:
  read.sh recall     [--scope S | --repo P] [client flags…]
  read.sh search     <query> [--scope S | --repo P | --all-scopes] [client flags…]
  read.sh ls-entries [--scope S | --repo P] [client flags…]

  --if-available   the client's absence is a SKIP at exit 0, not a refusal.
                   For a best-effort preflight only — never for a mandated read.

Client flags are passed through untouched (--no-sync, --list, --limit, --page,
--ref, --mode, --all-scopes). stdout is the client's bytes verbatim; everything
this wrapper says goes to stderr.

exit codes:
$(cairn_shared_code_legend)
  20  \`recall\` and \`search\` ONLY — no scope could be resolved: neither --scope nor
      --repo was given and the working directory is not inside a git repository, so
      the client would have refused too (rc 2, \`could not derive a scope\`). This is
      the same refusal in this directory's own >=19 band, so a caller can tell it
      from the client's 2. \`ls-entries\` cannot return it and is never gated on a
      scope: it takes none, and the client ignores --scope for it.
$(cairn_passthrough_legend)
USAGE
}

# 🔴 THE SCOPE PRE-FLIGHT, AND WHY IT ASKS GIT RATHER THAN THE CLIENT. With no
# --scope and no --repo the client derives a scope from the working directory's
# repository. Outside a repository there is nothing to derive, and the client's
# honest answer is a report with no entries in it. This is the discriminating
# check: `git rev-parse --show-toplevel` either names a repo or it does not.
resolvable_scope() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --scope|--repo) return 0 ;;
      --scope=*|--repo=*) return 0 ;;
    esac
  done
  git rev-parse --show-toplevel >/dev/null 2>&1
}

refuse_no_scope() {
  printf '%s: no scope could be resolved — no --scope, no --repo, and `%s` is not inside a git repository.\n' \
    "$CAIRN_OPS_NAME" "$PWD" >&2
  printf '%s: the client would have answered with an EMPTY report, which reads as "nothing recorded" rather than "nothing was asked". Pass --scope <scope> or --repo <path>.\n' \
    "$CAIRN_OPS_NAME" >&2
  exit "$EXIT_NO_SCOPE"
}

main() {
  local verb
  [ "$#" -gt 0 ] || { usage >&2; exit "$EXIT_USAGE"; }
  verb="$1"; shift

  case "$verb" in
    -h|--help|help) usage; exit "$EXIT_OK" ;;
  esac

  local -a passthrough=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      --if-available) CAIRN_OPS_IF_AVAILABLE=1; export CAIRN_OPS_IF_AVAILABLE ;;
      *) passthrough+=("$arg") ;;
    esac
  done

  case "$verb" in
    recall)
      require_client
      resolvable_scope "${passthrough[@]+"${passthrough[@]}"}" || refuse_no_scope
      exec cairn recall "${passthrough[@]+"${passthrough[@]}"}"
      ;;
    ls-entries)
      # 🔴 NO SCOPE PRE-FLIGHT, AND ITS ABSENCE IS THE POINT RATHER THAN AN
      # OVERSIGHT. `ls-entries` asks what the CACHES hold, not what one scope
      # holds: the client ignores `--scope` for it and lists every entry on every
      # instance. See the header — guarding this refused a working listing at rc 20.
      require_client
      exec cairn ls-entries "${passthrough[@]+"${passthrough[@]}"}"
      ;;
    search)
      require_client
      # A search needs a query, and an empty one is the other silent-zero shape:
      # the client would match every hunk or none depending on its own rules, and
      # neither is what the caller meant.
      [ "${#passthrough[@]}" -gt 0 ] || die_usage "search needs a query — read.sh search '<text>' [--scope S]"
      case "${passthrough[0]}" in
        -*) die_usage "search's first argument is the QUERY, not a flag (got \`${passthrough[0]}\`)" ;;
      esac
      # --all-scopes resolves no scope of its own and needs none.
      if ! printf '%s\n' "${passthrough[@]}" | grep -qx -- '--all-scopes'; then
        resolvable_scope "${passthrough[@]}" || refuse_no_scope
      fi
      exec cairn search "${passthrough[@]}"
      ;;
    *)
      die_usage "unknown subcommand \`$verb\` — read.sh owns recall, search and ls-entries"
      ;;
  esac
}

main "$@"
