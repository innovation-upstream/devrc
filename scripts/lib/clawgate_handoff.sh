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
#    it into a 0600 curl config file and unlinks it under a trap. THIS CODE
#    never echoes it, never interpolates it into a URL and never names it in an
#    error message — a claim about this file, NOT about the process: `bash -x`,
#    a `set -x` inherited from a caller, or a core dump would still show it.
#
# Usage (as a CLI):
#   clawgate_handoff.sh resolve            — which task(s) does THIS session own?
#   clawgate_handoff.sh field <doc>        — print the doc's recorded task id
#
# Exit codes for `resolve`:
#   0  exactly one WORKED task resolved (the id is on stdout, ready to record).
#      With no `role` on any row it falls back to "exactly one task", and says so.
#   3  no session id — nothing was asked
#   4  clawgate did NOT answer (no token, a missing tool, unreachable, non-200,
#      no `tasks` array, a row carrying no usable id, or a counting pass that
#      produced no usable tally — a broken READ is never reported as an empty
#      board)
#   5  the board answered and resolved NOTHING — see the caveat printed with it
#   6  ASK; this command will not pick one. FOUR distinguishable causes, each
#      named in the output: several WORKED tasks, NO worked task beside one or
#      more created/read links, a role this tool does not recognise, or (roles
#      unavailable) several tasks by the old count-based rule.
# For `field`:
#   0  a readable id is on stdout
#   1  no clawgate-task: field at all
#   2  the field is there and UNREADABLE — a value that is not a task id, or a
#      front-matter block that is never closed
#  64  usage error (no verb, unknown verb, no path) — about the COMMAND
#  66  the doc could not be read — also about the command, NOT about any field
# Any other code means this tool did not run.
#
# ⚠ SOURCING THIS FILE SETS `-u` AND `-o pipefail` IN THE CALLER. Both consumers
# already set exactly that (`resume-state.sh` line 1 of its body, and this file's
# own CLI), so nothing changes for them — but a future sourcer inherits it
# silently, which is worth knowing before adding a third.
set -uo pipefail

#: Fallback base URL, matching scripts/lib/clawgate_tasks.py's DEFAULT_API_URL.
CLAWGATE_DEFAULT_API_URL="http://192.168.50.250:30302"

#: Where the hook token and base URL live, RELATIVE to $HOME. Expanded at call
#: time, never at source time — a caller may set HOME after sourcing.
CLAWGATE_ENV_REL=".claude/clawgate.env"

#: The front-matter key. One spelling, used by the reader and the writer.
CLAWGATE_FIELD_KEY="clawgate-task"

#: 🔴 A CROSS-REPO SEAM WITH NOTHING HOLDING IT TOGETHER, so it is named here.
#: The authority for these four is clawgate's own
#: `<homelab-talos>/containers/clawgate/internal/taskstatus/taskstatus.go`
#: (`Open` / `InProgress` / `ReadyForReview` / `Complete`), which lives in a
#: DIFFERENT REPOSITORY — no test in devrc can see it, and no test there knows
#: this file exists. Verified equal by hand 2026-08-21; the devrc-side pin
#: (`test_the_shell_vocabulary_covers_everything_PYTHON_knows_about`) can only
#: keep this list and `scripts/lib/clawgate_tasks.py` agreeing with EACH OTHER.
#: A fifth constant added upstream is therefore caught at RUNTIME, by the gap
#: below, not by any gate — which is why the gap has to exist.
#:
#: ⚠ It is NOT a permanently-red gate: it fires per task, only for a task
#: actually sitting in a state this list does not carry, so a healthy board
#: produces none of it. (An earlier review worried it would fire constantly;
#: measured, it does not.)
#:
#: 🔴 THE CLOSED STATUS VOCABULARY, and it is closed on purpose. `clawgate_drift_lines`
#: below decides on `complete` / `ready_for_review` and stays silent otherwise —
#: which means a status NOBODY HERE HAS HEARD OF renders exactly like a healthy
#: one. clawgate's own `taskstatus.go` header records an incident where adding a
#: constant left a whole suite green, so the caller checks membership HERE and
#: emits a `!` gap for anything outside it rather than letting an unknown state
#: read as "no drift". Space-separated for `case` matching in `clawgate_known_status`.
CLAWGATE_TASK_STATUSES="open in_progress ready_for_review complete"

#: 🔴 THE SESSION↔TASK LINK ROLES, and the SECOND cross-repo seam in this file —
#: same shape as the status vocabulary above, same lack of a gate. The authority
#: is `<homelab-talos>/containers/clawgate/internal/notes/threads.go`
#: (`RoleRead` / `RoleWorked` / `RoleCreated`), verified by reading that file
#: 2026-08-22. WHAT SETS EACH, from `internal/api/notes.go`:
#:
#:   read     GET /api/tasks/{id}                     — the session merely looked
#:   worked   PATCH /api/tasks/{id}                   — edited the task
#:            PATCH /api/tasks/{id}/status            — flipped its status
#:            POST  /api/tasks/{id}/comments          — commented on it
#:   created  POST /api/tasks (+ the 0023 backfill)   — the session FILED it
#:
#: 🔴 SPELLED IN THE SERVER'S OWN MONOTONIC ORDER (read < worked < created): a
#: link's role only ever moves UP that list. TWO consequences this file depends
#: on, neither of them obvious:
#:   * `worked` is NOT a weak signal. `flows/task-pickup.md` mandates the
#:     comment/status write-back on every pickup and
#:     `scripts/claude-hooks/clawgate-writeback-guard.py` BLOCKS the turn from
#:     ending without it — so a session that actually worked a task carries it.
#:   * `created` is TERMINAL and outranks `worked`, so a session that FILES a
#:     task and then works it stays `created`. That case therefore lands in the
#:     "none worked" branch below and is ASKED about, not auto-recorded. It is
#:     the honest answer: this tool cannot tell it apart from a session that only
#:     filed the ticket, and those want opposite verdicts.
#:
#: Space-separated to match CLAWGATE_TASK_STATUSES; split by jq, not by the shell.
CLAWGATE_TASK_ROLES="read worked created"

# `clawgate_known_status <status>` — is this one of the four we can reason about?
clawgate_known_status(){
  local s
  for s in $CLAWGATE_TASK_STATUSES; do
    [ "$s" = "${1:-}" ] && return 0
  done
  return 1
}


# --------------------------------------------------------------------------- #
# PURE: the front-matter field
# --------------------------------------------------------------------------- #

# `clawgate_task_field_raw <text>` — print the RAW value recorded under the
# front-matter key. THREE outcomes, not two:
#
#   0  the key is present in a properly CLOSED front-matter block (value on stdout)
#   1  no front-matter key at all
#   2  the key is there but the BLOCK IS MALFORMED — never closed
#
# 🔴 The key/value distinction is the whole point of this function existing.
# "is a field already there?" (the writer's question, so it does not double-add)
# and "what task does it name?" (the reader's) have DIFFERENT answers for
# `clawgate-task: TBD`, and collapsing them means /handoff appends a second
# field beside an unreadable one.
#
# 🔴 EXIT 2 EXISTS BECAUSE THIS READER AND `handoff_doc.py`'s MERGER MUST AGREE
# ON WHAT A BLOCK *IS*, and an earlier revision did not. The python side's
# `_FRONT_MATTER` requires a closing `---`; this side used to return the value
# the moment it saw the key. So an unterminated block — an ordinary LLM writing
# slip — made the shell print `193` while `merge` treated those lines as body
# and DROPPED them: the exact silent data loss this whole change exists to fix,
# reintroduced at the seam. Termination is now required on BOTH sides (relaxing
# python instead would let a stray `---` on line 1 swallow an entire document as
# front matter). Exit 2 keeps the failure LOUD: the caller reports an unreadable
# field, i.e. a `!` gap, rather than "this doc names no task".
#
# STRICT in three more ways, each for a reason:
#   * The front matter must START THE FILE — line 1 is exactly `---`. A `---`
#     later in a markdown doc is a horizontal rule or a setext underline, and
#     letting one open a front-matter block means arbitrary body prose can mint
#     a task id.
#   * The block ends at the first closing `---`; nothing after it is read.
#   * The FIRST occurrence of the key wins. A duplicated key is malformed YAML,
#     and picking the last one silently would make which task you reconcile
#     depend on append order.
clawgate_task_field_raw(){
  local line n=0 v found=0
  while IFS= read -r line; do
    n=$((n+1))
    line=${line%$'\r'}                       # tolerate CRLF
    if [ "$n" -eq 1 ]; then
      [ "$line" = "---" ] || return 1
      continue
    fi
    if [ "$line" = "---" ]; then             # the block CLOSES here
      [ "$found" -eq 1 ] || return 1         # …and carried no key
      printf '%s\n' "$v"
      return 0
    fi
    [ "$found" -eq 1 ] && continue           # first key wins; keep scanning for `---`
    case "$line" in
      "$CLAWGATE_FIELD_KEY":*) ;;
      *) continue ;;
    esac
    v=${line#*:}
    v="${v#"${v%%[![:space:]]*}"}"           # ltrim
    v="${v%"${v##*[![:space:]]}"}"           # rtrim
    v=${v#\"}; v=${v%\"}                     # one layer of quotes, either kind
    v=${v#\'}; v=${v%\'}
    found=1
  done <<<"$1"
  # EOF with no closing delimiter.
  [ "$found" -eq 1 ] && return 2
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
  v=$(clawgate_task_field_raw "$1") || return 1   # 1 = absent, 2 = unterminated
  case "$v" in
    ''|*[!0-9]*) return 1 ;;
    *) printf '%s\n' "$v"; return 0 ;;
  esac
}

# `clawgate_field_present <text>` — is the KEY there at all, readable or not?
# TRUE for an unterminated block too (exit 2 above): the doc tried to name a
# task, so the honest report is "unreadable field", never "no field".
clawgate_field_present(){
  local rc
  clawgate_task_field_raw "$1" >/dev/null; rc=$?
  [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]
}


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
# 🔴 `total` is -1 when `comments` is present and is NOT an array — a schema
# break, or an error object that happens to parse as JSON, which must not
# render as "0 new comments": that is the reassuring shape of a source that
# never answered.
#
# ⚠ AN ABSENT/NULL `comments` KEY IS A REAL ZERO, and this was measured rather
# than assumed. The first cut treated absent as -1 too, and the live board then
# emitted the gap on a perfectly healthy task: the field is `omitempty`, so it
# is simply MISSING when a task has no comments. Measured against live 0.7.98
# on 2026-08-21 — task #306 `has("comments") == false` with zero comments, task
# #299 an array of 5 — i.e. alarming on absence would fire on the majority of
# tasks, which is the permanently-red gate `claude/RULES.md` calls worse than
# no gate. The distinguishable case (a non-array value) keeps its alarm.
#
# Timestamps: Go emits RFC3339 with a fractional part (`…:18.640843Z`) that
# jq's fromdateiso8601 rejects, so the fraction is stripped first. A non-`Z`
# offset still fails to parse and lands in `unreadable`, by design — inventing
# a timezone would move every verdict by hours.
clawgate_new_comments(){
  printf '%s' "$1" | jq -r --argjson cut "${2:-0}" '
    if (.comments | type) == "null" and (type == "object") then "0 0 0"
    elif (.comments | type) != "array" then "0 0 -1"
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
#
# 🔴 AN UNKNOWN STATUS ALSO PRODUCES NOTHING HERE, AND THAT SILENCE IS ONLY SAFE
# BECAUSE THE CALLER GAPS ON IT FIRST. Inventing a finding from a state we
# cannot reason about would be fabrication; reporting nothing at all would let a
# fifth status render as a clean bill of health. So membership is checked with
# `clawgate_known_status` BEFORE this is called (see resume-state.sh's
# clawgate_block), and this function is never the thing standing between an
# unknown state and a reassuring digest. An earlier revision claimed the caller
# did that while it only gapped on an EMPTY status — the comment was right and
# the code was not.
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
# PURE: ranking the rows the board returned
# --------------------------------------------------------------------------- #

# `clawgate_rank_rows <response-body-text>` — the whole verdict, from text alone.
# Prints the candidate rows (role-ANNOTATED and role-ORDERED, worked first) and
# then one verdict line, and returns `resolve`'s 0/4/5/6 (see the header).
#
# 🔴 IT TAKES TEXT, NOT A PATH, and it lives above the I/O line on purpose. The
# caller does the network; this does the deciding, so the suite can drive every
# verdict off a fixture string with no server and no temp file.
#
# 🔴 WHY `role` DECIDES AND A COUNT NO LONGER DOES. The endpoint has always
# returned `role` beside `id`/`status`/`title`; this function used to discard it
# and count links instead, so every link weighed the same. Measured against the
# live board 2026-08-22 (138 tasks, 43 links, 12 linked sessions): 6 of the 12
# sessions were multi-task and therefore got "ASK which one", while only 1 had
# more than one WORKED task — i.e. 5 of the 6 questions had an answer the data
# already knew. The worst was a backlog-filling session linked to 19 tasks, 18
# `created` + 1 `read`: asking there is not merely noisy, EVERY answer is wrong,
# because the front-matter field means "the task whose work this doc describes"
# and that session did nobody's work.
#
# 🔴 A LONE `created` ROW IS NOT A RESOLUTION — a deliberate behaviour change
# from the old count rule, which returned 0 for it. "I filed this ticket" and
# "this document describes that work" are different claims, and recording the
# first as the second is how a doc acquires a task it will be reconciled against
# for its whole life.
#
# 🔴 A MISSING FIELD MUST NOT BECOME "ZERO WORKED". If NOT ONE row carries a
# role — an older server, or the field being dropped — the counts are all zero
# and the role rule would silently answer "none of them worked" about a board
# that was never asked. So that case falls back to the old count rule and SAYS
# it did. Partial absence is different and is treated as data: the rows that do
# carry a role are ranked, the ones that do not are reported and count as not
# worked.
#
# 🔴 AN UNRECOGNISED ROLE IS REPORTED, NEVER GUESSED AT. The server's CHECK
# constraint means today's board cannot emit one, so this is not a
# permanently-red gate — it fires only on a vocabulary change, which is exactly
# when a silent "that is not `worked`" would be wrong. Same reasoning as
# `clawgate_known_status` above: a state nobody here has heard of must not
# render like a healthy one.
# `clawgate_usable_id <text>` — is this ONE bare task id, fit to be written into
# a doc? Prints the refusal and returns 1 when it is not.
#
# 🔴 THIS GUARD WAS ARGUED AWAY ONCE, ON A REACHABILITY CLAIM THAT WAS FALSE.
# The reasoning was: the `n_id -ne n` branch above already proved every row
# carries a usable id, and the tally says exactly one row matches, so the re-read
# below cannot come back empty. That is true of the PAYLOAD and says nothing
# about the PASS — kill the jq invocation that performs the re-read and the
# substitution yields "" while every count stays valid. An audit measured it:
# `clawgate: 1 WORKED task of 2 link(s) … record it as front matter:
# clawgate-task:` — rc 0, blank value.
#
# 🔴 PRICED FROM THE CONSUMER, WHICH IS WHY IT IS rc 4 AND NOT A COSMETIC NOTE.
# `claude/skills/handoff/SKILL.md` tells the executor to act on the exit code and
# nothing else, so rc 0 means it writes `clawgate-task:` with no value. That is
# not a blank field: `clawgate_task_field_raw` classifies it present-and-
# UNREADABLE, so `resume-state.sh` prints an UNRECONCILED gap on every future
# /resume of that document, forever. A one-line refusal here is cheaper than a
# permanent gap in a file nobody will connect back to this function.
#
# Both callers ask the identical question, so it is asked in one place —
# `claude/RULES.md` -> "One rule, one place": a predicate open-coded at two sites
# is wrong at one of them in the same direction.
clawgate_usable_id(){
  case "${1:-}" in
    # Empty, or anything that is not a pure run of digits — which also rejects a
    # MULTI-LINE value (a newline is not a digit), i.e. a re-read that matched
    # more rows than the tally said it would.
    ''|*[!0-9]*)
      echo "clawgate: DID NOT ANSWER USABLY — the pass that RE-READS the resolved task's id produced '${1:-}', which is not one bare id. Recording that would write an UNREADABLE \`$CLAWGATE_FIELD_KEY:\` field and gap every future /resume of the doc. UNKNOWN, not empty; record nothing."
      return 1
      ;;
  esac
  return 0
}

clawgate_rank_rows(){
  local body="${1:-}"

  # Shared jq prelude. `norm` is the reason every field access below is safe: a
  # row that is not an object becomes `{}`, so it renders as a visibly broken
  # `#? role=? status=?` line and is caught by the usable-id check, instead of
  # aborting jq and turning a schema break into a silent zero.
  #
  # 🔴 TWO jq TRAPS, BOTH MEASURED HERE, BOTH SILENT.
  #   * A FILTER ARGUMENT IS EVALUATED AGAINST THE FILTER'S INPUT, not against
  #     the surrounding `.`. `$known | index(.role)` reads `.role` off the
  #     ARRAY `$known`, which aborts jq — so the counts came back empty, all
  #     seven fell back to 0, and every payload carrying a REAL role rendered
  #     as "NOTHING RESOLVED — 0 tasks", the reassuring shape of a source that
  #     never answered. Hence `.role as $r` first, everywhere.
  #   * `index` RETURNS 0 FOR THE FIRST ELEMENT, and 0 is TRUTHY in jq (only
  #     `null` and `false` are falsy), so a bare `if ($known|index($r))` happens
  #     to be right — and would be wrong the moment anyone "fixed" it with a C
  #     intuition. Compared against `null` explicitly so the reading is the
  #     same in both languages.
  local jqdefs='
    def norm: if type == "object" then . else {} end;
    def rolestr: norm
      | if .role == null then "?"
        elif (.role | type) == "string" then .role
        else (.role | tojson) end;
    def bucket($known): norm | .role as $r
      | if $r == null then "absent"
        elif ($r | type) != "string" then "unknown"
        elif ($known | index($r)) != null then $r
        else "unknown" end;
    def rank: rolestr
      | if . == "worked" then 0 elif . == "created" then 1
        elif . == "read" then 2 else 3 end;
    def usable: select((type == "number" and . == floor and . >= 0)
                       or (type == "string" and test("^[0-9]+$")));
  '

  # 🔴 THIS CHECK DELIBERATELY DOES NOT USE `$jqdefs`, and the asymmetry is
  # load-bearing rather than an oversight: it is the one pass that must still
  # answer when the prelude itself is broken. A syntax error inside `$jqdefs`
  # therefore leaves the shape check green and lands on the counting pass below,
  # which is where it is caught — see the tally guard there.
  local shape
  shape=$(printf '%s' "$body" | jq -r '(.tasks | type)' 2>/dev/null)
  if [ "$shape" != "array" ]; then
    echo "clawgate: DID NOT ANSWER USABLY — the 200 carried no \`tasks\` array. UNKNOWN, not empty."
    return 4
  fi

  # 🔴 COUNTED BY jq, NOT BY LINES. A task TITLE may contain a newline — the
  # server trims and rune-caps titles but does not strip interior newlines — so
  # counting rendered lines reported "2 tasks resolved — ASK which one" for a
  # single task. Fails safe, but the verdict was wrong and the writer would have
  # gone and asked about a task that does not exist. Every number below comes
  # out of ONE jq pass so they cannot disagree with each other.
  #
  # `n_id` counts rows carrying a USABLE id. A row without one used to reach a
  # `sed` that matched zero digits and told the writer to record
  # `clawgate-task:` with NO VALUE — which the reader then classifies as
  # present-and-unreadable, i.e. a permanent `!` gap on every future /resume of
  # that doc. Unreachable from today's server; free to refuse.
  local counts n n_id n_worked n_read n_created n_unknown n_absent
  counts=$(printf '%s' "$body" | jq -r --arg roles "$CLAWGATE_TASK_ROLES" "$jqdefs"'
    ($roles | split(" ")) as $known
    | [ .tasks[]? | bucket($known) ] as $b
    | [ (.tasks | length),
        ([ .tasks[]? | norm | .id | usable ] | length),
        ($b | map(select(. == "worked"))  | length),
        ($b | map(select(. == "read"))    | length),
        ($b | map(select(. == "created")) | length),
        ($b | map(select(. == "unknown")) | length),
        ($b | map(select(. == "absent"))  | length) ]
    | map(tostring) | join(" ")' 2>/dev/null)
  # 🔴 A DEAD COUNTING PASS IS rc 4, NOT rc 5, AND NOTHING BUT THIS SAYS SO.
  # Defaulting each field to 0 on garbage — the obvious defence, and the one this
  # function shipped with — is what makes the failure INVISIBLE: seven zeros walk
  # straight into the `n -eq 0` branch and print "NOTHING RESOLVED — 0 tasks for
  # this session", which is a claim ABOUT THE BOARD. rc 5 means the board
  # answered and resolved nothing; a jq that died answered nothing at all, which
  # is rc 4's entire job.
  #
  # 🔴 IT IS NOT PAYLOAD-REACHABLE TODAY — `norm` guards every field access — so
  # this guards the NEXT EDIT to `$jqdefs`, which is not hypothetical: writing
  # this function, `$known | index(.role)` aborted jq and turned every
  # role-carrying payload into exactly that confident zero. That instance is
  # fixed; without this, the next one reads the same way.
  #
  # So: the tally must be SEVEN NUMERIC FIELDS ON EXACTLY ONE LINE, or the pass
  # did not answer. THREE halves, and each catches a different broken filter:
  #
  #   * the LINE COUNT — 🔴 `read -r -a` CONSUMES ONE LINE AND DISCARDS THE REST,
  #     so a filter emitting MULTIPLE outputs (an edit dropping the `[ … ]`
  #     wrapper, or adding a `.[]`) satisfies the two checks below on line 1 while
  #     every later line vanishes. Measured against two `created` rows: it printed
  #     "1 WORKED task of 1 link(s) (0 created, 0 read)" and returned rc 0 with a
  #     BLANK id — fabricated counts, from a tally that was never read.
  #   * the FIELD COUNT — a filter that emitted the wrong shape. Both directions:
  #     six fields leave `n_absent` unset, eight mean the array grew and every
  #     index below now names the wrong quantity.
  #   * the per-field DIGIT test — a filter that emitted text.
  case "$counts" in
    *$'\n'*)
      echo "clawgate: DID NOT ANSWER USABLY — the pass that COUNTS the \`tasks\` array emitted MORE THAN ONE LINE, so any tally read from it is a fragment. That is a broken read, NOT an empty board. UNKNOWN, not empty; record nothing."
      return 4
      ;;
  esac
  local -a fields=()
  IFS=' ' read -r -a fields <<<"$counts"
  local tally_ok=1 f
  if [ "${#fields[@]}" -ne 7 ]; then
    tally_ok=0
  else
    for f in "${fields[@]}"; do
      case "$f" in ''|*[!0-9]*) tally_ok=0 ;; esac
    done
  fi
  if [ "$tally_ok" -ne 1 ]; then
    echo "clawgate: DID NOT ANSWER USABLY — the pass that COUNTS the \`tasks\` array produced no usable tally (wanted 7 integers, got: '${counts}'). That is a broken read, NOT an empty board. UNKNOWN, not empty; record nothing."
    return 4
  fi
  # Every field is now known to be a non-empty run of digits, so the assignments
  # below cannot produce a value an arithmetic comparison would abort on. There
  # is deliberately NO second per-field defaulting here: after the guard above it
  # could never fire, and an unreachable fallback reads as coverage while
  # providing none (`claude/RULES.md` -> "prove it REACHABLE").
  n="${fields[0]}"; n_id="${fields[1]}"; n_worked="${fields[2]}"
  n_read="${fields[3]}"; n_created="${fields[4]}"
  n_unknown="${fields[5]}"; n_absent="${fields[6]}"

  if [ "$n" -eq 0 ]; then
    echo "clawgate: NOTHING RESOLVED — 0 tasks for this session."
    echo "  An unknown session id answers 200 with an EMPTY ARRAY, so this cannot"
    echo "  distinguish 'this session touched no task' from 'the id is wrong'."
    echo "  Write NO clawgate-task: field, say so in the report, and never create a task."
    return 5
  fi

  # 🔴 THE ORDER IS THE ANSWER TO THE QUESTION rc 6 ASKS. Printing the rows
  # worked-first, each with its role, is what lets the operator answer without
  # running a second command. The two-space indent is jq's — a `sed` here would
  # be a fourth external command inside a function whose preflight names three.
  local rows
  rows=$(printf '%s' "$body" | jq -r "$jqdefs"'
    [ .tasks[]? | norm ] | to_entries
    | map({k: (.value | rank), i: .key,
           s: ("  #\(.value.id // "?") role=\(.value | rolestr) status=\(.value.status // "?") "
               + ((.value.title // "") | tostring | gsub("\\s+"; " ")))})
    | sort_by([.k, .i]) | .[] | .s' 2>/dev/null)
  # 🔴 AN EMPTY RENDER IS A FAILED RENDER HERE, NOT "nothing to show". `n` is
  # already known to be >= 1, so this pass owes exactly `n` lines; a `[ -n
  # "$rows" ] &&` that silently swallows the empty case made the function
  # CONTRADICT ITSELF — "ROLES UNAVAILABLE … Read the rows yourself" printed with
  # no rows beneath it, and rc 6's "ASK which one" printed with no candidates,
  # which is precisely the question this output exists to make answerable. The
  # verdict below is still computed from the tally and is still trustworthy, so
  # this REPORTS and continues rather than returning: losing the display is not
  # losing the answer, and turning a cosmetic failure into rc 4 would throw away
  # a resolution the counts fully support.
  if [ -n "$rows" ]; then
    printf '%s\n' "$rows"
  else
    echo "clawgate: (the pass that RENDERS the candidate rows produced nothing for $n row(s) — the verdict below still comes from the counting pass, but the candidates could not be shown. Read the board directly before answering any question it asks.)"
  fi

  if [ "$n_id" -ne "$n" ]; then
    echo "clawgate: DID NOT ANSWER USABLY — $n task(s) came back but only $n_id carry a usable id. UNKNOWN, not empty; record nothing."
    return 4
  fi

  if [ "$n_unknown" -gt 0 ]; then
    local unknown_vals
    unknown_vals=$(printf '%s' "$body" | jq -r --arg roles "$CLAWGATE_TASK_ROLES" "$jqdefs"'
      ($roles | split(" ")) as $known
      | [ .tasks[]? | norm | .role | select(. != null) | . as $r
          | select(((($r | type) == "string")
                    and (($known | index($r)) != null)) | not)
          | (if ($r | type) == "string" then $r else ($r | tojson) end) ]
      | unique | join(", ")' 2>/dev/null)
    echo "clawgate: UNRECOGNISED ROLE — $n_unknown of $n link(s) carry a role this tool has never heard of: ${unknown_vals:-<unprintable>}."
    echo "  The vocabulary it knows is \`$CLAWGATE_TASK_ROLES\`. An unrecognised role is NOT 'worked' and is NOT guessed at."
    echo "  ASK which one this handoff belongs to. Do not guess, and never create a task."
    return 6
  fi

  if [ "$n_absent" -eq "$n" ]; then
    echo "clawgate: ROLES UNAVAILABLE — not one of the $n row(s) carried a \`role\`, so this COUNTED the links instead of ranking them. Read the rows yourself."
    if [ "$n" -gt 1 ]; then
      echo "clawgate: $n tasks resolved — ASK which one this handoff belongs to. Do not guess, and never create a task."
      return 6
    fi
    local ids
    ids=$(printf '%s' "$body" | jq -r "$jqdefs"'
      [ .tasks[]? | norm | .id | usable ] | map(tostring) | .[]' 2>/dev/null)
    clawgate_usable_id "$ids" || return 4
    echo "clawgate: 1 task resolved — record it as front matter: $CLAWGATE_FIELD_KEY: $ids"
    return 0
  fi

  if [ "$n_absent" -gt 0 ]; then
    echo "clawgate: $n_absent of $n row(s) carried no \`role\` — ranked as NOT worked, not as unknown."
  fi

  if [ "$n_worked" -eq 1 ]; then
    local worked_ids
    worked_ids=$(printf '%s' "$body" | jq -r "$jqdefs"'
      [ .tasks[]? | norm | select(.role == "worked") | .id | usable ]
      | map(tostring) | .[]' 2>/dev/null)
    clawgate_usable_id "$worked_ids" || return 4
    echo "clawgate: 1 WORKED task of $n link(s) ($n_created created, $n_read read) — record it as front matter: $CLAWGATE_FIELD_KEY: $worked_ids"
    return 0
  fi
  if [ "$n_worked" -gt 1 ]; then
    echo "clawgate: $n_worked WORKED tasks resolved — ASK which one this handoff belongs to. Do not guess, and never create a task."
    return 6
  fi
  echo "clawgate: $n task(s) linked to this session, NONE of them WORKED ($n_created created, $n_read read) — this handoff likely belongs to NONE of them; record nothing unless you recognise one."
  echo "  Filing a task and reading a task are not doing its work. ASK which one rather than guessing, and never create a task."
  return 6
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

  # EVERY external command this function runs, checked up front. `grep` used to
  # be missing from this list while the count below used it, so a host without
  # grep turned a real resolution into "NOTHING RESOLVED" (exit 5) — a tool
  # absence rendering as a fact about the board. The count is jq's now, and the
  # list is the whole set rather than the two anybody remembered.
  #
  # 🔴 THE LIST IS PINNED AND EACH ENTRY IS DRIVEN. Removing `mktemp` from it
  # once survived the whole suite, because only `jq`'s absence was ever
  # exercised — a preflight is exactly the kind of code where one tested member
  # is read as a tested list. `test_every_preflighted_tool_is_individually_driven`
  # runs `resolve` with each of these missing in turn, and a ledger assertion
  # fails when the list grows or shrinks without that test moving.
  local need
  for need in curl jq mktemp; do
    if ! command -v "$need" >/dev/null 2>&1; then
      echo "clawgate: DID NOT ANSWER — \`$need\` is not on PATH, so the board was never asked. UNKNOWN, not empty."
      return 4
    fi
  done

  local cfg body code rc
  cfg=$(mktemp) || { echo "clawgate: DID NOT ANSWER — could not create a temp file."; return 4; }
  body=$(mktemp) || { rm -f "$cfg"; echo "clawgate: DID NOT ANSWER — could not create a temp file."; return 4; }
  # 🔴 A TRAP, not just straight-line cleanup: the file about to be written holds
  # a live bearer token, and `^C` between `mktemp` and the `rm` would leave it in
  # TMPDIR indefinitely. Every `return` below still unlinks explicitly and then
  # CLEARS the trap — this function is sourced as well as run as a CLI, and a
  # sourced caller must not inherit an EXIT trap naming two locals.
  #
  # ⚠ TWO LATENT COSTS, recorded rather than fixed because no caller reaches
  # them today (`resume-state.sh` sources this file but never calls `resolve`):
  #   * the INT/TERM handler does not re-raise, so a `^C` here exits 0-ish
  #     rather than 130 — invisible to the CLI, wrong for a future caller that
  #     branches on the signal;
  #   * `trap - EXIT` below clears the trap unconditionally, which would also
  #     discard an EXIT trap a SOURCING caller had installed for itself.
  # Both become real the moment something both sources this file and calls this
  # function; fix them then, with a test that has a caller to write.
  trap 'rm -f "$cfg" "$body"' EXIT INT TERM
  chmod 600 "$cfg" 2>/dev/null
  # 🔴 The ONLY place the token is written. Not `-H "Authorization: …"`: that
  # puts it in argv, which /proc makes world-readable.
  #
  # 🔴 ESCAPED, because curl's config parser processes backslash escapes inside a
  # DOUBLE-QUOTED value: a token containing `"` or `\` would otherwise be sent
  # mangled (or truncate the header), and the failure would arrive as a 401 that
  # reads like a wrong token rather than a quoting bug.
  local esc="${tok//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  printf 'header = "Authorization: Bearer %s"\n' "$esc" > "$cfg"
  code=$(curl -sS --max-time 10 --config "$cfg" -o "$body" -w '%{http_code}' \
         "$base/api/sessions/$sid/tasks" 2>/dev/null)
  rc=$?
  rm -f "$cfg"

  if [ "$rc" -ne 0 ]; then
    rm -f "$body"; trap - EXIT INT TERM
    echo "clawgate: DID NOT ANSWER (curl exit $rc — unreachable, TLS, or timed out). UNKNOWN, not empty."
    return 4
  fi
  if [ "$code" != "200" ]; then
    rm -f "$body"; trap - EXIT INT TERM
    echo "clawgate: DID NOT ANSWER (HTTP $code). UNKNOWN, not empty."
    return 4
  fi

  # 🔴 THE DECIDING HAPPENS ABOVE THE I/O LINE. Everything from here is handing
  # the response TEXT to `clawgate_rank_rows` — `$(< "$body")` is a bash
  # redirection, not a `cat`, so this does not grow the preflight ledger. The
  # body is read and the temp file unlinked BEFORE any verdict runs, so no exit
  # path can leave it behind.
  local body_text
  body_text=$(< "$body")
  rm -f "$body"; trap - EXIT INT TERM

  clawgate_rank_rows "$body_text"
}

clawgate_handoff_usage(){
  echo "usage: clawgate_handoff.sh resolve | field <handoff-doc>" >&2
}

# 🔴 A BAD INVOCATION AND A BAD DOCUMENT GET DIFFERENT CODES. `field` used to
# answer 2 for a missing verb, a mistyped path AND a present-but-unreadable
# field, while the skill documented only the last — so a typo in the path read
# to the executor as "there is a broken clawgate-task: field in this doc", and
# the repair it would then attempt is to a file that does not exist. 64/66 are
# the sysexits spellings (EX_USAGE / EX_NOINPUT); anything this file does not
# define (127, say) means the TOOL did not run at all.
CLAWGATE_EX_USAGE=64
CLAWGATE_EX_NOINPUT=66

clawgate_handoff_main(){
  local doc text rc
  case "${1:-}" in
    resolve) clawgate_resolve ;;
    field)
      doc="${2:-}"
      if [ -z "$doc" ]; then clawgate_handoff_usage; return "$CLAWGATE_EX_USAGE"; fi
      if [ ! -f "$doc" ] || [ ! -r "$doc" ]; then
        echo "clawgate_handoff.sh: cannot read '$doc' — this says NOTHING about any field" >&2
        return "$CLAWGATE_EX_NOINPUT"
      fi
      text=$(cat "$doc")
      if clawgate_task_field "$text"; then return 0; fi
      clawgate_task_field_raw "$text" >/dev/null; rc=$?
      if [ "$rc" -eq 2 ]; then
        echo "clawgate-task: field present but the front-matter block is NEVER CLOSED — fix that block, do not append a second field" >&2
        return 2
      fi
      if [ "$rc" -eq 0 ]; then
        echo "clawgate-task: field present but UNREADABLE (not a task id) — fix that one, do not append a second" >&2
        return 2
      fi
      return 1
      ;;
    *) clawgate_handoff_usage; return "$CLAWGATE_EX_USAGE" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then clawgate_handoff_main "$@"; fi
