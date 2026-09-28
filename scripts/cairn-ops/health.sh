#!/usr/bin/env bash
# health.sh — the HEALTH cluster of the cairn surface: sync, doctor, instances.
#
# 🔴 `instances` IS THE SUBCOMMAND THAT DID NOT EXIST ANYWHERE, AND IT IS THE ONE
# WITH A MEASURED FAILURE BEHIND IT. Three things about this host were only
# obtainable by reading two different commands' prose and combining them by hand:
# which instances are configured, which cache each one reads, and which instance a
# given scope routes to. devrc PR #1872 records what that cost — in one `/handoff`
# run, `cairn append --scope civitai-developer-docs` answered `instance=civitai`
# while `cairn create --scope civitai-app-starters` answered `instance=personal`,
# and that second scope exists on BOTH, so a future reader's sibling entries sit on
# the other one. Do not infer the instance; ask.
#
# ⚠ THIS SCRIPT PARSES THE CLIENT'S OUTPUT, WHICH MAKES ITS FORMAT A DEPENDENCY.
# Every parse REFUSES (23) when its pattern does not match, so a format change
# surfaces as a named refusal rather than as an empty table that reads as "no
# instances configured". `scripts/tests/test_cairn_ops.py` pins each pattern
# against the LIVE client for the same reason.
set -uo pipefail

CAIRN_OPS_NAME="health.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

# health.sh's OWN refusal.
readonly EXIT_NO_INSTANCE=23

usage() {
  cat <<USAGE
health.sh — the HEALTH cluster of the cairn surface: is the store reachable, fresh, and WHERE.

usage:
  health.sh sync      [--scope S]                    refresh the cache(s) from the pod(s)
  health.sh doctor    [--json] [--no-sync]           the one diagnostic call
  health.sh instances [--instance A | --scope S]     alias -> cache root, one line each

  --if-available   the client's absence is a SKIP at exit 0, not a refusal.

\`instances\` answers the three questions a per-instance deployment creates: what is
configured, which cache each instance reads, and which instance a scope routes to.
Output is TSV on stdout: \`<alias>\t<cache root>\`, plus \`<alias>\t<cache root>\t<scope>\`
when --scope was given.

exit codes:
$(cairn_shared_code_legend)
  23  no instance could be named — this host has none configured, --instance named an
      alias that is not configured, or --scope named a scope the routing table does
      not route. ⚠ An unrouted scope is NOT a scope that does not exist: routing is
      per-INSTANCE as well as per-host, so this justifies trying a create and never a
      claim that the scope is unrecorded.
$(cairn_passthrough_legend)
USAGE
}

refuse_no_instance() {
  printf '%s: %s\n' "$CAIRN_OPS_NAME" "$1" >&2
  printf '%s: configured aliases: %s\n' "$CAIRN_OPS_NAME" \
    "$(configured_instances | paste -sd, - 2>/dev/null)" >&2
  printf '%s: the table is `cairn routes`; each instance'"'"'s cache is in `cairn doctor`.\n' \
    "$CAIRN_OPS_NAME" >&2
  exit "$EXIT_NO_INSTANCE"
}

# `cache_root_of <alias>` — the cache root the READER resolves for one instance,
# taken from `doctor`'s own `<alias>/reader-resolution` row. Echoes the path;
# non-zero when that row is absent or names no absolute path.
cache_root_of() {
  local alias="$1" root
  # 🔴 ONE PARSE, IN ONE PLACE. The `<alias>/reader-resolution` detail reads "the
  # reader resolves <path>, which carries a sync stamp"; the extraction is the
  # first absolute-path token in it. Both halves live in this one program so a
  # format change reds one thing, and a non-match exits non-zero — never an empty
  # string that a caller would print as a blank column.
  root=$(cairn doctor --json --no-sync 2>/dev/null | python3 -c '
import json, re, sys

alias = sys.argv[1]
try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for check in doc.get("checks", []):
    if check.get("name") == f"{alias}/reader-resolution":
        hit = re.search(r"(?:^|\s)(/[^\s,]+)", check.get("detail", ""))
        if hit:
            print(hit.group(1))
            sys.exit(0)
        sys.exit(1)
sys.exit(1)
' "$alias")
  # 🔴 THE STATUS OF THAT PIPELINE IS NOT THE PARSE'S STATUS, AND BRANCHING ON IT
  # WAS A MEASURED BUG. `set -o pipefail` is on and `cairn doctor` exits **10**
  # whenever any check COULD NOT LOOK — which `--no-sync` guarantees, since every
  # pod-dependent check reports UNMEASURED. So the pipeline reported 10 while the
  # parse had already printed the right path, and an `|| return 1` here refused a
  # correct answer on every host with a reachable-or-not pod. The parse's own
  # verdict is whether it printed anything.
  [ -n "$root" ] || return 1
  printf '%s\n' "$root"
}

cmd_instances() {
  local want_alias="$1" want_scope="$2"
  local aliases alias root rows=0

  aliases=$(configured_instances)
  [ -n "$aliases" ] || refuse_no_instance "this host has no cairn instance configured, so there is no store to read"

  if [ -n "$want_scope" ]; then
    if ! alias=$(instance_for_scope "$want_scope"); then
      refuse_no_instance "the routing table does not route scope \`$want_scope\` to any instance"
    fi
    if ! root=$(cache_root_of "$alias"); then
      refuse_no_instance "instance \`$alias\` is named by the table but the reader reported no cache root for it"
    fi
    printf '%s\t%s\t%s\n' "$alias" "$root" "$want_scope"
    exit "$EXIT_OK"
  fi

  while IFS= read -r alias; do
    [ -n "$alias" ] || continue
    if [ -n "$want_alias" ] && [ "$alias" != "$want_alias" ]; then
      continue
    fi
    if ! root=$(cache_root_of "$alias"); then
      refuse_no_instance "instance \`$alias\` is configured but the reader reported no cache root for it"
    fi
    printf '%s\t%s\n' "$alias" "$root"
    rows=$((rows + 1))
  done <<<"$aliases"

  # 🔴 A ZERO-ROW TABLE IS A REFUSAL, NOT AN ANSWER. An empty stdout here is
  # indistinguishable from a harness wired to nothing, which is the shape this
  # whole directory exists to remove.
  if [ "$rows" -eq 0 ]; then
    refuse_no_instance "--instance \`$want_alias\` is not a configured alias on this host"
  fi
  exit "$EXIT_OK"
}

main() {
  local verb
  [ "$#" -gt 0 ] || { usage >&2; exit "$EXIT_USAGE"; }
  verb="$1"; shift

  case "$verb" in
    -h|--help|help) usage; exit "$EXIT_OK" ;;
    sync|doctor|instances) ;;
    *) die_usage "unknown subcommand \`$verb\` — health.sh owns sync, doctor and instances" ;;
  esac

  local want_alias="" want_scope=""
  local -a passthrough=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --if-available) CAIRN_OPS_IF_AVAILABLE=1; export CAIRN_OPS_IF_AVAILABLE ;;
      --instance) shift; [ "$#" -gt 0 ] || die_usage "--instance needs a value"; want_alias="$1" ;;
      --instance=*) want_alias="${1#--instance=}" ;;
      --scope) shift; [ "$#" -gt 0 ] || die_usage "--scope needs a value"
               want_scope="$1"; passthrough+=(--scope "$1") ;;
      --scope=*) want_scope="${1#--scope=}"; passthrough+=("$1") ;;
      *) passthrough+=("$1") ;;
    esac
    shift
  done

  require_client

  case "$verb" in
    instances) cmd_instances "$want_alias" "$want_scope" ;;
    sync|doctor)
      [ -z "$want_alias" ] || die_usage "--instance is only meaningful for \`instances\`; \`$verb\` covers every configured instance"
      exec cairn "$verb" "${passthrough[@]+"${passthrough[@]}"}"
      ;;
  esac
}

main "$@"
