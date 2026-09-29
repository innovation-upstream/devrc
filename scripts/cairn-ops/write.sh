#!/usr/bin/env bash
# write.sh — the WRITE cluster of the cairn surface: create, put, append.
#
# 🔴 IT ENFORCES THE APPEND PROTOCOL BEFORE THE NETWORK, AND IT RUNS THE MANDATED
# POST-WRITE CHECK AFTER IT. Those are the two halves the skills stated in prose
# and nothing executed:
#
#   * `claude/skills/subsystem-index/SKILL.md` documents `--text` as ONE line with
#     NO leading `- ` and NO leading date, because the SERVER adds both. A text
#     that carries either is accepted by the pod and renders double-prefixed — a
#     malformed bullet that no exit code reports.
#   * the same file mandates `cairn sync && cairn-validate --scope <scope>` after
#     ANY write, and `claude/skills/cairn/SKILL.md` records that the two skills
#     stating it had already DRIFTED once. A mandate nothing executes is a mandate
#     that is sometimes skipped.
#
# 🔴 AND THE `cairn sync` IN THAT CHECK IS LOAD-BEARING RATHER THAN DECORATION —
# `cairn append` writes to the POD and does not touch the local cache, so an
# unsynced check cleanly parses the PRE-WRITE bytes and passes on exactly the
# defect it exists to catch. ⚠ THE SYNC LIVES IN `hygiene.sh validate`, NOT HERE,
# and that is a correction rather than a layout choice: chaining it here made the
# property true for writes through this door and FALSE for a human validating after
# a manual `cairn append`. A failed sync there exits non-zero and arrives here as an
# unverified write (24).
#
# ⚠ WHAT IT DOES NOT DO: it does not compose the bullet, choose the ref, or decide
# what is worth recording. `claude/skills/subsystem-index/SKILL.md` owns all of
# that and remains the one protocol; this is the door that protocol goes through.
set -uo pipefail

CAIRN_OPS_NAME="write.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

# write.sh's OWN refusals.
#
# 21 fires BEFORE anything is sent, so a 21 is a guarantee that nothing was
# written. 24 fires AFTER a write the pod accepted, so a 24 is the opposite
# guarantee — something landed and could not be confirmed. Two codes because those
# two states need opposite remedies, and a caller that could not tell them apart
# would retry a write that had already succeeded.
readonly EXIT_PROTOCOL=21
readonly EXIT_UNVERIFIED_WRITE=24

# The bullet ceiling the client's own `--help` states. Named here so a text that
# would be truncated is refused locally rather than silently shortened upstream.
readonly MAX_TEXT_CHARS=2000

usage() {
  cat <<USAGE
write.sh — the WRITE cluster of the cairn surface. Protocol enforced, write verified.

usage:
  write.sh append --scope S --ref R --session <uuid> --text '<one line>'
  write.sh put    --scope S --ref R --file <path> [--if-match <rev>]
  write.sh create --scope S --ref R --file <path>

  --no-verify      skip the mandated post-write parse check (which syncs first).
                   For a caller that runs its own; it is NOT the default, because
                   an unrun check is indistinguishable from a passed one.
  --if-available   the client's absence is a SKIP at exit 0, not a refusal.

Every write requires --scope: the post-write check is scope-scoped, so a write
whose scope this wrapper cannot name is a write it cannot verify.

exit codes:
$(cairn_shared_code_legend)
  21  the APPEND PROTOCOL was violated and NOTHING WAS SENT — --text carried a
      leading bullet marker, a leading date, a newline, or more than $MAX_TEXT_CHARS
      characters. The server adds the marker and the date; a text carrying its own
      renders double-prefixed, which nothing upstream reports as an error.
  24  the write LANDED and the mandated post-write check did not confirm it. This is
      NOT a failed write — re-read the scope; never retry.
$(cairn_passthrough_legend)
USAGE
}

# --------------------------------------------------------------------------- #
# THE APPEND PROTOCOL, AS A PREDICATE
#
# 🔴 EACH ARM IS A REALISTIC MISTAKE, NOT A TEXTBOOK ONE. The leading `- ` is what
# every hand-written bullet starts with; the leading date is what a writer copies
# off the entry it is appending to; the newline is what a shell heredoc produces;
# the length is the ceiling the client's own help names. All four are accepted by
# the pod and all four render wrong.
# --------------------------------------------------------------------------- #
refuse_protocol() {
  printf '%s: the append protocol was violated — %s\n' "$CAIRN_OPS_NAME" "$1" >&2
  printf '%s: NOTHING WAS SENT. The server adds the bullet marker and the date; pass the bullet text alone.\n' \
    "$CAIRN_OPS_NAME" >&2
  exit "$EXIT_PROTOCOL"
}

check_append_text() {
  local text="$1"
  [ -n "$text" ] || refuse_protocol "--text is empty"
  case "$text" in
    "- "*) refuse_protocol "--text starts with \`- \`, a bullet marker the server adds" ;;
    "* "*) refuse_protocol "--text starts with \`* \`, a bullet marker the server adds" ;;
    "+ "*) refuse_protocol "--text starts with \`+ \`, a bullet marker the server adds" ;;
  esac
  # A leading ISO date, in the shape the entries themselves carry.
  if printf '%s' "$text" | grep -Eq '^[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}'; then
    refuse_protocol "--text starts with a date; the server adds the date"
  fi
  # `wc -l` counts NEWLINES, so a one-line text with no trailing newline counts 0.
  if [ "$(printf '%s' "$text" | wc -l)" -gt 0 ]; then
    refuse_protocol "--text spans more than one line; a bullet is ONE line"
  fi
  if [ "${#text}" -gt "$MAX_TEXT_CHARS" ]; then
    refuse_protocol "--text is ${#text} characters, over the $MAX_TEXT_CHARS-character ceiling the client documents"
  fi
}

# --------------------------------------------------------------------------- #
# THE MANDATED POST-WRITE CHECK
#
# Delegated to `hygiene.sh validate` rather than re-spelled: that is where the
# per-instance store resolution and the NOTHING-CHECKED refusal live, and a second
# copy here is precisely the drift this directory exists to remove.
# --------------------------------------------------------------------------- #
unverified() {
  printf '%s: the write LANDED but %s, so it is UNCONFIRMED.\n' "$CAIRN_OPS_NAME" "$1" >&2
  printf '%s: do NOT retry the write — re-read the scope and read the output above.\n' \
    "$CAIRN_OPS_NAME" >&2
  exit "$EXIT_UNVERIFIED_WRITE"
}

verify_write() {
  local scope="$1" rc
  printf '%s: verifying — the write-protocol parse check (which syncs first) on scope `%s`.\n' \
    "$CAIRN_OPS_NAME" "$scope" >&2
  # 🔴 THE SYNC IS INSIDE `hygiene.sh validate`, NOT CHAINED HERE, AND THAT IS THE
  # POINT RATHER THAN A SHORTCUT. Chaining it here would leave the property true for
  # writes through this door and FALSE for a human validating after a manual
  # `cairn append` — one rule, one place. A failed sync there exits non-zero, which
  # arrives below as an unverified write.
  "$HERE/hygiene.sh" validate --scope "$scope" >&2
  rc=$?
  [ "$rc" -eq 0 ] || unverified "the post-write check exited $rc"
}

main() {
  local verb
  [ "$#" -gt 0 ] || { usage >&2; exit "$EXIT_USAGE"; }
  verb="$1"; shift

  case "$verb" in
    -h|--help|help) usage; exit "$EXIT_OK" ;;
    append|put|create) ;;
    *) die_usage "unknown subcommand \`$verb\` — write.sh owns append, put and create" ;;
  esac

  local scope="" text="" verify=1 have_text=0 have_session=0
  local -a passthrough=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --no-verify) verify=0 ;;
      --if-available) CAIRN_OPS_IF_AVAILABLE=1; export CAIRN_OPS_IF_AVAILABLE ;;
      --scope) shift; [ "$#" -gt 0 ] || die_usage "--scope needs a value"
               scope="$1"; passthrough+=(--scope "$1") ;;
      --scope=*) scope="${1#--scope=}"; passthrough+=("$1") ;;
      --text) shift; [ "$#" -gt 0 ] || die_usage "--text needs a value"
              text="$1"; have_text=1; passthrough+=(--text "$1") ;;
      --text=*) text="${1#--text=}"; have_text=1; passthrough+=("$1") ;;
      --session|--session=*) have_session=1; passthrough+=("$1")
              if [ "$1" = "--session" ]; then
                shift; [ "$#" -gt 0 ] || die_usage "--session needs a value"
                passthrough+=("$1")
              fi ;;
      *) passthrough+=("$1") ;;
    esac
    shift
  done

  # 🔴 THE PROTOCOL CHECK RUNS FIRST, BEFORE ANY OTHER REFUSAL AND BEFORE THE
  # CLIENT IS EVEN LOOKED FOR, so a 21 is unambiguous about what did not happen.
  if [ "$verb" = "append" ]; then
    [ "$have_text" -eq 1 ] || die_usage "append needs --text '<one line>'"
    check_append_text "$text"
    [ "$have_session" -eq 1 ] \
      || die_usage "append needs --session <uuid>: an unattributed bullet cannot be traced to the session that wrote it"
  fi

  require_client
  [ -n "$scope" ] \
    || die_usage "every write needs --scope: the post-write check is scope-scoped, and an unnamed scope is an unverifiable write"

  cairn "$verb" "${passthrough[@]}"
  local rc=$?
  [ "$rc" -eq 0 ] || exit "$rc"

  if [ "$verify" -eq 1 ]; then
    verify_write "$scope"
  else
    printf '%s: --no-verify given, so the mandated post-write check DID NOT RUN. This exit 0 is about the write only.\n' \
      "$CAIRN_OPS_NAME" >&2
  fi
  exit "$EXIT_OK"
}

main "$@"
