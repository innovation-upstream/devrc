#!/usr/bin/env bash
# hygiene.sh — the HYGIENE cluster of the cairn surface: audit, prune, validate.
#
# 🔴 IT EXISTS TO TURN ONE MEASURED WRONG READING INTO A REFUSAL. Measured on this
# host 2026-09-27, two instances configured:
#
#     cairn-validate --scope civitai-developer-docs
#       -> "NOTHING WAS CHECKED — no entry files were found", exit 0
#     cairn-validate --scope civitai-developer-docs --store <the civitai cache>
#       -> "checked: 4 entry file(s)", "OK — 4 of 4 entry file(s) parse", exit 0
#
# Same scope, same host, same second. The first walked the DEFAULT instance's
# cache, which holds nothing for that scope. Both exit 0, so a flow that branches
# on the exit code records a pass over an empty directory — and that is exactly
# what happened in a real `/handoff` run (devrc PR #1872, consequence 1).
#
# 🔴 SO THIS SCRIPT DOES TWO THINGS NOTHING ELSE DOES. It resolves the store for
# the SCOPE rather than for the host, and it refuses (22) when a check would
# report nothing checked. The honest banner that already says "a zero here is NOT
# a clean bill of health" stays exactly as it is — it was never the problem. The
# problem was the exit code beside it.
#
# ⚠ WHAT IS DELIBERATELY NOT HERE: `subsystem_touch.py`'s own `--store` default.
# clawgate cg#563 owns that code half, and the frozen pre-cutover mirror at
# `~/.claude/analyze-service-index` is its rollback artifact. This script passes an
# explicit `--store`, which argparse resolves last-occurrence-wins — the same
# mechanism `scripts/cairn-validate`'s own docstring describes and relies on.
set -uo pipefail

CAIRN_OPS_NAME="hygiene.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$HERE/.." && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

# hygiene.sh's OWN refusal — the measured wrong reading, converted.
readonly EXIT_NOTHING_CHECKED=22

# 🔴 THE SYNC IS PART OF THE CHECK, NOT SOMETHING THE CALLER CHAINS, AND THAT WAS A
# DEFECT IN THE FIRST DRAFT OF THIS FILE. The mandated post-write check used to be
# spelled `cairn sync && cairn-validate --scope <scope>`, and
# `scripts/tests/test_cairn_skill_verb_ledger.py` pins that the `cairn sync` half is
# load-bearing at EVERY occurrence: `append` writes to the POD and does not touch the
# local cache, and the checker reads that cache permissively, so an unsynced run
# cleanly parses the PRE-WRITE bytes and passes on exactly the defect it exists to
# catch. The first draft replaced the two-command form with a wrapper that did NOT
# sync — it relied on `write.sh` having synced a moment earlier, which is true for a
# write through that door and FALSE for the every-other case: a human validating after
# a manual `cairn append`. That ledger test is what found it.
#
# So the sync happens HERE, the property is structural rather than a rule a caller has
# to remember, and a FAILED sync is a non-zero exit rather than a check over stale
# bytes. `--no-sync` exists for a caller that has just synced and says so.
readonly SYNC_BEFORE_DEFAULT=1

usage() {
  cat <<USAGE
hygiene.sh — the HYGIENE cluster of the cairn surface. Per-INSTANCE, and it refuses a zero.

usage:
  hygiene.sh validate --scope S                       the write-protocol parse check
  hygiene.sh audit    --scope S [audit flags…]        the budget/pointer audit (READ-ONLY)
  hygiene.sh prune    --scope S --ref R --file F --confirm

  --no-sync        do NOT refresh the cache first. The sync is part of the check by
                   default, because a write lands on the POD and leaves the local
                   cache untouched — an unsynced check parses the PRE-WRITE bytes and
                   passes. Pass this only when you have just synced.
  --if-available   the client's absence is a SKIP at exit 0, not a refusal.

Every subcommand resolves the store for the SCOPE, not for the host: there is one
read-through cache per configured INSTANCE, so a scope on a non-default instance
is invisible to any tool that defaults to a path.

exit codes:
$(cairn_shared_code_legend)
  22  NOTHING WAS CHECKED — the resolved store holds no entry file for that scope,
      or the checker reported \`checked: 0\`. The underlying tools exit 0 here and say
      so in prose; this refuses, because a flow that branches on an exit code
      otherwise records a pass over an empty directory.
$(cairn_passthrough_legend)
  ⚠ \`validate\` passes through the WRITER's codes, not the client's, and the two
    tables must not be read for each other: the writer exits 3 on a MALFORMED
    ENTRY where the client's 3 means unreachable-and-no-cache.
USAGE
}

# `validator` — where `cairn-validate` comes from.
#
# 🔴 PATH FIRST, CHECKOUT SECOND, AND THE ORDER IS THE DEPLOYED SPELLING RATHER
# THAN A TEST HOOK. `scripts/cairn-validate`'s own docstring states the rule this
# follows: "an absolute checkout path baked into a protocol whose whole point is
# that agents work in other repos… a bare command on `home.sessionPath` resolves
# from any cwd, in either runtime." `nix/home.nix` puts it on PATH as an
# out-of-store symlink into this very checkout, so on a switched host the two
# resolutions are the SAME FILE — the fallback exists for a tree that has not
# switched yet, and for the hermetic tier, which has neither.
validator() {
  if command -v cairn-validate >/dev/null 2>&1; then
    command -v cairn-validate
    return 0
  fi
  printf '%s\n' "$SCRIPTS/cairn-validate"
}

refuse_nothing_checked() {
  printf '%s: NOTHING WAS CHECKED for scope `%s` — %s\n' "$CAIRN_OPS_NAME" "$1" "$2" >&2
  printf '%s: this is NOT a clean bill of health, which is why it is not an exit 0.\n' \
    "$CAIRN_OPS_NAME" >&2
  printf '%s: `cairn routes` names the instance a scope lives on; `cairn doctor` names each instance'"'"'s cache.\n' \
    "$CAIRN_OPS_NAME" >&2
  exit "$EXIT_NOTHING_CHECKED"
}

# `entries_in_scope <scope>` — how many entry files the READER can see for a scope,
# asked as the reader rather than derived from a store layout.
#
# 🔴 THE FILTER IS THIS FUNCTION'S OWN, BECAUSE `--scope` DOES NOT FILTER
# `ls-entries` — MEASURED, NOT ASSUMED. `cairn ls-entries --scope
# civitai-developer-docs --no-sync` printed **437** lines on this host on
# 2026-09-27: every entry on every instance, prefixed `[<instance>] `. An earlier
# draft of this function counted those lines, so its "is the scope empty?" test was
# satisfied 437 times over and could never fire — a guard that reads as coverage
# while providing none. The line shape is `[<instance>] <scope>/<entry>.md`, and
# the prefix is ABSENT on a single-instance host (the client only labels when more
# than one place could have answered), so both shapes are matched.
entries_in_scope() {
  local scope="$1"
  cairn ls-entries --no-sync 2>/dev/null \
    | grep -cE "(^|\] )${scope}/[^/]+\.md$"
}

# `sync_first <scope>` — refresh before reading. A failed refresh exits with the
# client's own code rather than continuing, because continuing is the silent pass.
sync_first() {
  local scope="$1" rc
  [ "${DO_SYNC:-$SYNC_BEFORE_DEFAULT}" = "1" ] || return 0
  cairn sync --scope "$scope" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '%s: the pre-check `cairn sync` exited %d, so the cache was NOT refreshed.\n' \
      "$CAIRN_OPS_NAME" "$rc" >&2
    printf '%s: refusing to check the pre-write bytes. Read the client banner, then re-run.\n' \
      "$CAIRN_OPS_NAME" >&2
    exit "$rc"
  fi
}

# 🔴 NO `$(resolve … || exit)` HELPER ANYWHERE BELOW, DELIBERATELY. A command
# substitution runs in a SUBSHELL, so an `exit` inside one exits the subshell and the
# caller carries on with an empty value — a refusal that silently becomes a store root
# of `""`. Every call site therefore branches on the assignment's own status instead.
cmd_validate() {
  local scope="$1"; shift
  local store checked out rc
  sync_first "$scope"
  if ! store=$(store_for_scope "$scope"); then
    refuse_nothing_checked "$scope" "the reader would not name a store for it"
  fi

  # THE PRE-FLIGHT. Refuses before running the checker at all, so the refusal does
  # not depend on the checker's output format.
  if [ "$(entries_in_scope "$scope")" -eq 0 ]; then
    refuse_nothing_checked "$scope" "the reader sees no entry file for it in $store"
  fi

  # 🔴 EACH STREAM GETS ITS OWN FILE, AND THAT IS NOT FASTIDIOUSNESS.
  # `claude/RULES.md`: "Never infer a stream from redirection order or a merged
  # capture; give each its own file and read both." A `2>&1` capture here would
  # (a) republish the checker's state banner on STDOUT, changing the bytes every
  # caller sees, and (b) make the `checked:` parse depend on which stream a future
  # version of the checker writes that line to. Both streams are replayed on the
  # stream they arrived on.
  local tmp
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/cairn-ops-validate.XXXXXX") || die_usage "could not create a temporary directory"
  "$(validator)" --scope "$scope" --store "$store" "$@" >"$tmp/out" 2>"$tmp/err"
  rc=$?
  cat "$tmp/err" >&2
  cat "$tmp/out"
  out=$(cat "$tmp/out")
  rm -rf "$tmp"

  # THE POST-PARSE. Defence in depth on the same code: the pre-flight asks the
  # reader, this asks the checker, and a disagreement between them is itself a
  # finding rather than something to average.
  checked=$(printf '%s\n' "$out" \
    | sed -n 's/^[[:space:]]*checked:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
  if [ -z "$checked" ]; then
    refuse_nothing_checked "$scope" "the checker printed no \`checked: N\` line, so nothing said how many files it read"
  fi
  if [ "$checked" -eq 0 ]; then
    refuse_nothing_checked "$scope" "the checker reported \`checked: 0\` against $store"
  fi
  exit "$rc"
}

cmd_audit() {
  local scope="$1"; shift
  local store
  sync_first "$scope"
  if ! store=$(store_for_scope "$scope"); then
    refuse_nothing_checked "$scope" "the reader would not name a store for it"
  fi
  if [ "$(entries_in_scope "$scope")" -eq 0 ]; then
    refuse_nothing_checked "$scope" "the reader sees no entry file for it in $store"
  fi
  exec python3 "$SCRIPTS/subsystem-audit.py" --store "$store" --scope "$scope" "$@"
}

# 🔴 PRUNE BACKS UP FIRST, AND THE BACKUP IS OF THE RESOLVED INSTANCE CACHE RATHER
# THAN OF "the store". That is the same per-instance defect one step further on: a
# `cp -a` of the default cache before pruning a scope on another instance backs up
# bytes that were never at risk, and the reassuring copy is what makes the loss
# unrecoverable.
cmd_prune() {
  local scope="$1" ref="$2" file="$3" confirmed="$4"
  local store backup
  [ "$confirmed" = "1" ] \
    || die_usage "prune needs --confirm: it replaces an entry's whole body, and a whole-file retype is measured to lose a concurrent append silently"
  [ -f "$file" ] || die_usage "--file \`$file\` is not a readable file"
  sync_first "$scope"
  if ! store=$(store_for_scope "$scope"); then
    refuse_nothing_checked "$scope" "the reader would not name a store for it"
  fi
  backup="${TMPDIR:-/tmp}/cairn-prune-$(date +%s)-$scope"
  mkdir -p "$backup" || die_usage "could not create the backup directory $backup"
  cp -a "$store/." "$backup/" 2>/dev/null \
    || refuse_nothing_checked "$scope" "the resolved store $store could not be copied, so there is no rollback artifact"
  printf '%s: backed up %s -> %s before writing.\n' "$CAIRN_OPS_NAME" "$store" "$backup" >&2
  # The write goes through write.sh, so the append protocol and the mandated
  # post-write check apply to a prune exactly as they do to any other write.
  exec "$HERE/write.sh" put --scope "$scope" --ref "$ref" --file "$file"
}

main() {
  local verb
  [ "$#" -gt 0 ] || { usage >&2; exit "$EXIT_USAGE"; }
  verb="$1"; shift

  case "$verb" in
    -h|--help|help) usage; exit "$EXIT_OK" ;;
    validate|audit|prune) ;;
    *) die_usage "unknown subcommand \`$verb\` — hygiene.sh owns validate, audit and prune" ;;
  esac

  local scope="" ref="" file="" confirmed=0
  local DO_SYNC="$SYNC_BEFORE_DEFAULT"
  local -a passthrough=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --if-available) CAIRN_OPS_IF_AVAILABLE=1; export CAIRN_OPS_IF_AVAILABLE ;;
      --no-sync) DO_SYNC=0 ;;
      --confirm) confirmed=1 ;;
      --scope) shift; [ "$#" -gt 0 ] || die_usage "--scope needs a value"; scope="$1" ;;
      --scope=*) scope="${1#--scope=}" ;;
      --ref) shift; [ "$#" -gt 0 ] || die_usage "--ref needs a value"; ref="$1" ;;
      --ref=*) ref="${1#--ref=}" ;;
      --file) shift; [ "$#" -gt 0 ] || die_usage "--file needs a value"; file="$1" ;;
      --file=*) file="${1#--file=}" ;;
      *) passthrough+=("$1") ;;
    esac
    shift
  done

  require_client
  [ -n "$scope" ] \
    || die_usage "$verb needs --scope: the store is resolved PER INSTANCE, and an unnamed scope cannot be routed to one"

  case "$verb" in
    validate) cmd_validate "$scope" "${passthrough[@]+"${passthrough[@]}"}" ;;
    audit)    cmd_audit "$scope" "${passthrough[@]+"${passthrough[@]}"}" ;;
    prune)
      [ -n "$ref" ] || die_usage "prune needs --ref <entry>"
      [ -n "$file" ] || die_usage "prune needs --file <the pruned body>"
      cmd_prune "$scope" "$ref" "$file" "$confirmed"
      ;;
  esac
}

main "$@"
