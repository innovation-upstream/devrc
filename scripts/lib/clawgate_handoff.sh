#!/usr/bin/env bash
# clawgate_handoff.sh — the seam between a handoff doc and the clawgate board.
#
# TWO CONSUMERS, ONE COPY OF EACH RULE:
#
#   /handoff  resolves which clawgate task THIS SESSION touched and records it
#             as YAML front matter at the very top of the handoff doc:
#
#                 ---
#                 clawgate-task: 193
#                 ---
#
#             -> the `resolve` and `field` verbs below.
#
#   /resume   parses that field back out and reconciles it against the LIVE
#             board -> scripts/resume-state.sh SOURCES this file for the pure
#             functions and adds a CLAWGATE block to its digest.
#
# The parser lives here exactly once because it has two callers, and a
# predicate open-coded at two sites is wrong at one of them in the same
# direction (claude/RULES.md, "One rule, one place"). The writer deciding
# whether a field is ALREADY present and the reader deciding what it SAYS must
# agree by construction, or /handoff double-adds a field /resume then reads
# twice.
#
# 🔴 EVERYTHING ABOVE `resolve` IS PURE — no network, no clock, no filesystem.
#    That is what lets scripts/tests/test_resume_state_clawgate.py source this
#    file and assert on fixture text, the same contract resume-state.sh's own
#    extraction heuristics carry. Keep new logic on that side of the line.
#
# 🔴 THE TOKEN IS NEVER PUT IN ARGV. `/proc/<pid>/cmdline` is world-readable,
#    which is why clawgatectl refuses a token positional too; `resolve` writes
#    it into a 0600 curl config file and deletes it. It is never echoed, never
#    interpolated into a URL, and never named in an error message.
#
# Usage (as a CLI):
#   clawgate_handoff.sh resolve            — which task(s) does THIS session own?
#   clawgate_handoff.sh field <doc>        — print the doc's recorded task id
#
# Exit codes for `resolve`:
#   0  exactly one task resolved (the id is on stdout, ready to record)
#   3  no session id — nothing was asked
#   4  clawgate did NOT answer (no token, unreachable, non-200, unparseable)
#   5  the board answered and resolved NOTHING — see the caveat printed with it
#   6  several tasks resolved — ASK; this command will not pick one
# For `field`:
#   0  a readable id is on stdout
#   1  no clawgate-task: field at all
#   2  the field is there and UNREADABLE (a value that is not a task id)
set -uo pipefail

#: Fallback base URL, matching scripts/lib/clawgate_tasks.py's DEFAULT_API_URL.
CLAWGATE_DEFAULT_API_URL="http://192.168.50.250:30302"

#: Where the hook token and base URL live, RELATIVE to $HOME. Expanded at call
#: time, never at source time — a caller may set HOME after sourcing.
CLAWGATE_ENV_REL=".claude/clawgate.env"

#: The front-matter key. One spelling, used by the reader and the writer.
CLAWGATE_FIELD_KEY="clawgate-task"


# --------------------------------------------------------------------------- #
# PURE: the front-matter field
# --------------------------------------------------------------------------- #

# `clawgate_task_field_raw <text>` — print the RAW value recorded under the
# front-matter key; exit 0 if the KEY is present, 1 if it is not.
#
# 🔴 The key/value distinction is the whole point of this function existing.
# "is a field already there?" (the writer's question, so it does not double-add)
# and "what task does it name?" (the reader's) have DIFFERENT answers for
# `clawgate-task: TBD`, and collapsing them means /handoff appends a second
# field beside an unreadable one.
#
# STRICT in three ways, each for a reason:
#   * The front matter must START THE FILE — line 1 is exactly `---`. A `---`
#     later in a markdown doc is a horizontal rule or a setext underline, and
#     letting one open a front-matter block means arbitrary body prose can mint
#     a task id.
#   * The block ends at the first closing `---`; nothing after it is read.
#   * The FIRST occurrence of the key wins and the scan stops. A duplicated key
#     is malformed YAML, and picking the last one silently would make which
#     task you reconcile depend on append order.
clawgate_task_field_raw(){
  local line n=0 v
  while IFS= read -r line; do
    n=$((n+1))
    line=${line%$'\r'}                       # tolerate CRLF
    if [ "$n" -eq 1 ]; then
      [ "$line" = "---" ] || return 1
      continue
    fi
    [ "$line" = "---" ] && return 1          # closing delimiter: key absent
    case "$line" in
      "$CLAWGATE_FIELD_KEY":*) ;;
      *) continue ;;
    esac
    v=${line#*:}
    v="${v#"${v%%[![:space:]]*}"}"           # ltrim
    v="${v%"${v##*[![:space:]]}"}"           # rtrim
    v=${v#\"}; v=${v%\"}                     # one layer of quotes, either kind
    v=${v#\'}; v=${v%\'}
    printf '%s\n' "$v"
    return 0
  done <<<"$1"
  return 1
}

# `clawgate_task_field <text>` — print the task id, or nothing.
#
# A value that is not all digits prints NOTHING and is not an id: sending
# `clawgate-task: TBD` to `clawgatectl task get TBD` would produce a confident
# "did not answer" about a task that was never named. The caller distinguishes
# "no field" from "unreadable field" with `clawgate_field_present`.
clawgate_task_field(){
  local v
  v=$(clawgate_task_field_raw "$1") || return 1
  case "$v" in
    ''|*[!0-9]*) return 1 ;;
    *) printf '%s\n' "$v"; return 0 ;;
  esac
}

# `clawgate_field_present <text>` — is the KEY there at all, readable or not?
clawgate_field_present(){ clawgate_task_field_raw "$1" >/dev/null; }


# --------------------------------------------------------------------------- #
# PURE: what the board says vs what the doc assumed
# --------------------------------------------------------------------------- #

# `clawgate_new_comments <task-json> <cutoff-epoch>` — print three numbers:
#
#     <newer> <unreadable> <total>
#
# `newer` counts comments created strictly after the cutoff (the handoff doc's
# mtime). `unreadable` counts comments whose timestamp this could not parse, so
# `newer` is honestly a FLOOR rather than a measurement — the caller reports
# that as a gap instead of quietly rounding it into the count.
#
# 🔴 `total` is -1 when the answer carries no `comments` ARRAY at all. That is
# not zero comments: a server that stopped embedding them, or an error object
# that happens to parse as JSON, would otherwise render as "0 new comments",
# which is the reassuring shape of a source that never answered.
#
# Timestamps: Go emits RFC3339 with a fractional part (`…:18.640843Z`) that
# jq's fromdateiso8601 rejects, so the fraction is stripped first. A non-`Z`
# offset still fails to parse and lands in `unreadable`, by design — inventing
# a timezone would move every verdict by hours.
clawgate_new_comments(){
  printf '%s' "$1" | jq -r --argjson cut "${2:-0}" '
    if (.comments | type) != "array" then "0 0 -1"
    else
      [ .comments[]? | (.createdAt // .created_at // null) ] as $raw
      | [ $raw[] | select(type == "string")
                 | (sub("\\.[0-9]+"; "") | fromdateiso8601)? ] as $ok
      | "\($ok | map(select(. > $cut)) | length) "
        + "\(($raw | length) - ($ok | length)) "
        + "\($raw | length)"
    end' 2>/dev/null || printf '0 0 -1\n'
}

# `clawgate_drift_lines <id> <status> <newer-comments>` — one DRIFT line per
# way the live board contradicts what a doc being RESUMED implies.
#
# The premise: you only run /resume on a handoff you intend to continue, and
# the front-matter field says that work belongs to this task. So a board status
# that means the work is FINISHED is drift, by itself, with no prose sniffing —
# unlike a referenced PR, where handoff docs routinely list already-merged ones
# and a blanket "merged => drift" is pure noise (see `handoff_says_inflight` in
# resume-state.sh). A handoff does not routinely name a completed task.
#
# `open` and `in_progress` produce nothing: they agree with the doc's premise.
# An UNKNOWN status produces nothing either — it is the caller's gap to report,
# never a finding.
clawgate_drift_lines(){
  local id="$1" status="${2:-}" newer="${3:-0}"
  case "$status" in
    complete)
      printf 'clawgate task #%s is COMPLETE on the board, but the handoff is being resumed as open work (is anything actually left to do?)\n' "$id"
      ;;
    ready_for_review)
      printf 'clawgate task #%s is READY_FOR_REVIEW on the board — the work this handoff describes is finished and waiting on a review\n' "$id"
      ;;
  esac
  case "$newer" in
    ''|*[!0-9]*) return 0 ;;
  esac
  if [ "$newer" -gt 0 ]; then
    printf 'clawgate task #%s has %s comment(s) POSTDATING the handoff doc — read them before acting on the doc\n' "$id" "$newer"
  fi
}


# --------------------------------------------------------------------------- #
# I/O — everything below this line touches the filesystem or the network
# --------------------------------------------------------------------------- #

# `clawgate_env_get <KEY> [path]` — one value out of ~/.claude/clawgate.env.
# Same parse as scripts/lib/clawgate_tasks.py's read_clawgate_env: skip blanks
# and comments, split on the FIRST `=`, trim. Never logs what it read.
clawgate_env_get(){
  local key="$1" path="${2:-$HOME/$CLAWGATE_ENV_REL}" line v
  [ -f "$path" ] || return 1
  while IFS= read -r line; do
    line=${line%$'\r'}
    case "$line" in
      "#"*|"") continue ;;
      "$key"=*) ;;
      *) continue ;;
    esac
    v=${line#*=}
    v="${v#"${v%%[![:space:]]*}"}"
    v="${v%"${v##*[![:space:]]}"}"
    [ -n "$v" ] || return 1
    printf '%s\n' "$v"
    return 0
  done < "$path"
  return 1
}

# `clawgate_resolve` — which clawgate task(s) does THIS session own?
#
# 🔴 THE SESSION ID COMES FROM `CLAUDE_CODE_SESSION_ID` — there is no `CLAUDE_SESSION_ID`.
# Reading a name that does not exist is how a feature ships INERT, because an
# unset variable and a session that touched nothing produce the same empty
# result and neither raises anything. Hence the explicit exit 3 below: "the
# variable was not set" is reported as its own outcome and never folded into
# "no tasks".
#
# 🔴 AN UNKNOWN SESSION ANSWERS `200 {"tasks":[]}`, NOT 404. So an empty array
# cannot distinguish "this session touched no task" from "the id is wrong" —
# exit 5 says exactly that rather than reporting a clean resolution of nothing.
clawgate_resolve(){
  local sid="${CLAUDE_CODE_SESSION_ID:-}"
  if [ -z "$sid" ]; then
    echo "clawgate: NO SESSION ID — \$CLAUDE_CODE_SESSION_ID is unset or empty, so the board was never asked. This is NOT 'no task'."
    return 3
  fi
  # The id is interpolated into a URL PATH. Refuse anything that could steer it
  # (a slash, a `..`, a query string) rather than sending it and reading
  # whatever comes back — the same class clawgatectl refuses empty path
  # parameters for.
  case "$sid" in
    *[!A-Za-z0-9._-]*)
      echo "clawgate: REFUSED — \$CLAUDE_CODE_SESSION_ID is not a plain session id, so nothing was asked."
      return 3
      ;;
  esac

  local env_path base tok
  env_path="$HOME/$CLAWGATE_ENV_REL"
  base=$(clawgate_env_get CLAWGATE_API_URL "$env_path") || base=""
  [ -n "$base" ] || base="$CLAWGATE_DEFAULT_API_URL"
  base=${base%/}
  if ! tok=$(clawgate_env_get CLAWGATE_HOOK_TOKEN "$env_path"); then
    echo "clawgate: DID NOT ANSWER — no CLAWGATE_HOOK_TOKEN in $env_path, so the board was never asked. UNKNOWN, not empty."
    return 4
  fi

  if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    echo "clawgate: DID NOT ANSWER — curl or jq is not on PATH, so the board was never asked. UNKNOWN, not empty."
    return 4
  fi

  local cfg body code rc
  cfg=$(mktemp) || { echo "clawgate: DID NOT ANSWER — could not create a temp file."; return 4; }
  body=$(mktemp) || { rm -f "$cfg"; echo "clawgate: DID NOT ANSWER — could not create a temp file."; return 4; }
  chmod 600 "$cfg" 2>/dev/null
  # 🔴 The ONLY place the token is written, and it is unlinked below on every
  # path. Not `-H "Authorization: …"`: that puts it in argv.
  printf 'header = "Authorization: Bearer %s"\n' "$tok" > "$cfg"
  code=$(curl -sS --max-time 10 --config "$cfg" -o "$body" -w '%{http_code}' \
         "$base/api/sessions/$sid/tasks" 2>/dev/null)
  rc=$?
  rm -f "$cfg"

  if [ "$rc" -ne 0 ]; then
    rm -f "$body"
    echo "clawgate: DID NOT ANSWER (curl exit $rc — unreachable, TLS, or timed out). UNKNOWN, not empty."
    return 4
  fi
  if [ "$code" != "200" ]; then
    rm -f "$body"
    echo "clawgate: DID NOT ANSWER (HTTP $code). UNKNOWN, not empty."
    return 4
  fi

  local rows
  rows=$(jq -r 'if (.tasks | type) != "array" then empty
                else .tasks[]? | "#\(.id) status=\(.status // "?") \(.title // "")" end' \
         < "$body" 2>/dev/null)
  local shape
  shape=$(jq -r '(.tasks | type)' < "$body" 2>/dev/null)
  rm -f "$body"
  if [ "$shape" != "array" ]; then
    echo "clawgate: DID NOT ANSWER USABLY — the 200 carried no \`tasks\` array. UNKNOWN, not empty."
    return 4
  fi

  local n
  n=$(printf '%s' "$rows" | grep -c . )
  if [ "${n:-0}" -eq 0 ]; then
    echo "clawgate: NOTHING RESOLVED — 0 tasks for this session."
    echo "  An unknown session id answers 200 with an EMPTY ARRAY, so this cannot"
    echo "  distinguish 'this session touched no task' from 'the id is wrong'."
    echo "  Write NO clawgate-task: field, say so in the report, and never create a task."
    return 5
  fi
  printf '%s\n' "$rows" | sed 's/^/  /'
  if [ "$n" -gt 1 ]; then
    echo "clawgate: $n tasks resolved — ASK which one this handoff belongs to. Do not guess, and never create a task."
    return 6
  fi
  echo "clawgate: 1 task resolved — record it as front matter: $CLAWGATE_FIELD_KEY: $(printf '%s' "$rows" | sed 's/^#\([0-9]*\).*/\1/')"
  return 0
}

clawgate_handoff_usage(){
  echo "usage: clawgate_handoff.sh resolve | field <handoff-doc>" >&2
}

clawgate_handoff_main(){
  local doc text
  case "${1:-}" in
    resolve) clawgate_resolve ;;
    field)
      doc="${2:-}"
      if [ -z "$doc" ] || [ ! -f "$doc" ]; then clawgate_handoff_usage; return 2; fi
      text=$(cat "$doc")
      if clawgate_task_field "$text"; then return 0; fi
      if clawgate_field_present "$text"; then
        echo "clawgate-task: field present but UNREADABLE (not a task id)" >&2
        return 2
      fi
      return 1
      ;;
    *) clawgate_handoff_usage; return 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then clawgate_handoff_main "$@"; fi
