#!/usr/bin/env bash
# read.sh — the READ cluster of the cairn surface: recall, search, ls-entries.
#
# 🔴 WHAT IT ADDS OVER TYPING `cairn recall` IS A PRE-FLIGHT REFUSAL, and that is
# the whole point of the wrapper. A read with no resolvable scope is the shape
# that returns an EMPTY REPORT rather than an error — "nothing recorded" is
# indistinguishable from "I could not work out what to look at", and
# `claude/RULES.md` names that class: "An EMPTY RESULT cannot distinguish two
# mechanisms." So this refuses with its OWN code (20) before the client runs,
# leaving the client's own codes to mean only what they meant before.
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
  20  no scope could be resolved — neither --scope nor --repo was given and the
      working directory is not inside a git repository, so the client would have
      had nothing to read and would have said so as an EMPTY report
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
    recall|ls-entries)
      require_client
      resolvable_scope "${passthrough[@]+"${passthrough[@]}"}" || refuse_no_scope
      exec cairn "$verb" "${passthrough[@]+"${passthrough[@]}"}"
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
