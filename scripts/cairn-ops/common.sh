#!/usr/bin/env bash
# common.sh — the predicates ALL FOUR `scripts/cairn-ops/*.sh` entry points share.
#
# ⚠ WHY THIS DIRECTORY IS `cairn-ops/` AND NOT `cairn/`. `scripts/cairn` is a
# TRACKED FILE — the read-through client itself, ledgered under `ROUTED` in
# `scripts/tests/test_store_root_ledger.py` and identified as Python by its
# shebang alone. A directory of that name cannot exist beside it, so the approved
# four-file shape lives one segment over. Nothing else about the shape moved.
#
# 🔴 IT IS A LIBRARY BECAUSE THE ALTERNATIVE REGENERATES ONE BUG FOUR TIMES.
# `claude/RULES.md`: "One rule, one place. A predicate duplicated across call
# sites regenerates the same bug at every site." Two of the three predicates
# below were already open-coded in the skills this directory replaces — the
# `command -v cairn >/dev/null` guard appears VERBATIM in three files
# (`clawgate/SKILL.md`, `clawgate/reference/prior-work-recall.md`,
# `obs-read/SKILL.md`) — and the third, the store-root question, was open-coded
# in NONE of them. That absence is the defect: every one of those recipes read
# whatever store the client happened to default to.
#
# 🔴 NO STORE PATH IS SPELLED IN THIS DIRECTORY, DELIBERATELY, AND THAT IS WHY
# `store_for_scope` SHELLS THE CLIENT INSTEAD OF COMPUTING A PATH.
# `claude/skills/cairn/SKILL.md`: "Call `resolve_read_store()`. Do **not**
# compute a path." The reader's answer for a scope is already PRINTED — it is the
# `  store: ` line of every recall — so asking the reader is both the one
# resolution and the cheapest one. A second spelling here would be the copy that
# drifts, and `scripts/tests/test_store_root_ledger.py` exists because four
# shipped tools drifted exactly that way, one at a time.
#
# ⚠ PARSING THE CLIENT'S OUTPUT MAKES ITS FORMAT A DEPENDENCY, and that is
# handled rather than ignored: every parse below REFUSES when its pattern does
# not match, so a format change surfaces as a named refusal with a non-zero exit
# rather than as an empty string that reads as "no store" or "no instance".
# `scripts/tests/test_cairn_ops.py::TestTheParsedFormatsAreStillThere` pins each
# pattern against the LIVE client, so the drift is caught here and not in a flow.

# --------------------------------------------------------------------------- #
# THE SHARED EXIT-CODE VOCABULARY
#
# 🔴 EVERY CODE THESE SCRIPTS OWN IS >= 19, AND THAT IS A WIRE FACT RATHER THAN A
# STYLE CHOICE. The `cairn` client already occupies 0, 2, 3, 4, 5, 6, 7, 8, 9 and
# 11, and `cairn doctor` additionally answers 9 and 10. A script-owned code
# inside that band would be indistinguishable from the client's own answer at the
# one moment a caller needs to tell them apart — "the wrapper refused before
# doing anything" versus "the store refused".
#
# 🔴 AND THE CLIENT'S CODES ARE PASSED THROUGH UNTRANSLATED — the same rule
# `scripts/cairn-validate` states about the writer's. Nothing here maps, clamps
# or re-numbers, because every exit code documented in `claude/skills/` is the
# client's and a wrapper that renumbered would make all of them wrong.
# --------------------------------------------------------------------------- #
readonly EXIT_OK=0
readonly EXIT_USAGE=2
readonly EXIT_NO_CLIENT=19

# The legend for the SHARED codes, spelled ONCE. `test_cairn_ops.py` derives the
# printed set from each `--help` and the returnable set from the sources; a code
# documented in four places is a code that goes stale in three.
cairn_shared_code_legend() {
  cat <<'LEGEND'
  0   the wrapped command answered, and the answer is fine
  2   usage — an unknown subcommand, a missing required argument, or a refused argument shape
  19  the `cairn` client is not on PATH, so NOTHING was read or written
LEGEND
}

cairn_passthrough_legend() {
  cat <<'LEGEND'
  Any other code is the `cairn` client's OWN, passed through untranslated:
    reads   3 unreachable-and-no-cache · 4 refresh-failed / unstamped-read-store · 5 corrupt
    writes  6 refused · 7 unreachable · 8 precondition-failed · 9 already-exists
    either  11 unrouted scope; and `doctor`'s 9 (a problem was MEASURED) / 10 (a check COULD NOT LOOK)
LEGEND
}

# --------------------------------------------------------------------------- #
# THE PREDICATES
# --------------------------------------------------------------------------- #

# `die_usage <msg…>` — refuse an argument shape. Exit 2, message on stderr.
die_usage() {
  printf '%s: %s\n' "${CAIRN_OPS_NAME:-cairn-ops}" "$*" >&2
  printf '%s: run `%s --help` for the subcommands and the exit codes.\n' \
    "${CAIRN_OPS_NAME:-cairn-ops}" "${CAIRN_OPS_NAME:-cairn-ops}" >&2
  exit "$EXIT_USAGE"
}

# `require_client` — the guard the three duplicated skill one-liners open-coded.
#
# 🔴 IT REFUSES RATHER THAN SKIPPING, WHICH IS THE OPPOSITE OF WHAT THE RECIPES
# IT REPLACES DID. Those printed `skipped: cairn unavailable` and exited 0 —
# defensible inside a best-effort preflight, and exactly wrong for a mandated
# post-write check, because a flow that branches on the exit code then records a
# pass over nothing. A caller that genuinely wants best-effort asks for it with
# `--if-available`, so the decision is made at the CALL site rather than built
# into the tool.
require_client() {
  if command -v cairn >/dev/null 2>&1; then
    return 0
  fi
  if [ "${CAIRN_OPS_IF_AVAILABLE:-0}" = "1" ]; then
    printf '%s: skipped — the `cairn` client is not on PATH, and --if-available was given.\n' \
      "${CAIRN_OPS_NAME:-cairn-ops}" >&2
    exit "$EXIT_OK"
  fi
  printf '%s: the `cairn` client is not on PATH, so nothing was read or written.\n' \
    "${CAIRN_OPS_NAME:-cairn-ops}" >&2
  printf '%s: remedy — `home-manager switch` installs it at ~/.local/bin/cairn; or pass --if-available to make its absence a skip.\n' \
    "${CAIRN_OPS_NAME:-cairn-ops}" >&2
  exit "$EXIT_NO_CLIENT"
}

# `store_for_scope <scope>` — the READER's own resolved store root for the
# instance that scope lives on. Echoes the path; returns non-zero and explains
# when the reader would not name one.
#
# 🔴 THIS IS THE WHOLE REASON THIS DIRECTORY EXISTS. Measured on this host
# 2026-09-27 with two instances configured: `cairn-validate --scope
# civitai-developer-docs` printed `NOTHING WAS CHECKED — no entry files were
# found` and exited **0**, because it prepends the DEFAULT instance's cache; the
# same scope holds 4 entries on the `civitai` instance, and the same command with
# an explicit `--store` reports `4 of 4 entry file(s) parse`. One directory is not
# the store — there is one read-through cache PER CONFIGURED INSTANCE. devrc
# PR #1872 measured the split; cg#563 owns the `subsystem_touch.py` default this
# wrapper deliberately does not touch.
store_for_scope() {
  local scope="$1" out line
  # `--no-sync` on purpose: resolution is a question about THIS host's disk, so a
  # wrapper that fetched here would make every caller's first read a network
  # call. Each subcommand decides its own sync separately and says so.
  out=$(cairn recall --scope "$scope" --no-sync 2>/dev/null)
  line=$(printf '%s\n' "$out" | sed -n 's/^[[:space:]]*store:[[:space:]]*//p' | head -n 1)
  if [ -z "$line" ]; then
    printf '%s: the reader would not name a store for scope `%s`.\n' \
      "${CAIRN_OPS_NAME:-cairn-ops}" "$scope" >&2
    printf '%s: either that scope is unrouted (read `cairn routes`), or the client stopped printing a `store:` line — a format change this wrapper refuses rather than guesses past.\n' \
      "${CAIRN_OPS_NAME:-cairn-ops}" >&2
    return 1
  fi
  printf '%s\n' "$line"
}

# `instance_for_scope <scope>` — the alias the routing TABLE sends a scope to.
# Echoes the alias; returns non-zero when the table names none.
#
# ⚠ A scope absent from the table is NOT a scope that does not exist. PR #1872:
# `scope-absent` is per-INSTANCE as well as per-host, so it justifies *trying* a
# create and never a claim that a scope is unrecorded.
instance_for_scope() {
  local scope="$1" alias
  alias=$(cairn routes --no-sync 2>/dev/null \
    | sed -n "s|^[[:space:]]*${scope}[[:space:]]*->[[:space:]]*||p" | head -n 1)
  [ -n "$alias" ] || return 1
  printf '%s\n' "$alias"
}

# `configured_instances` — the aliases this host has configured, one per line.
configured_instances() {
  cairn routes --no-sync 2>/dev/null \
    | sed -n 's/^instances:[[:space:]]*//p' | head -n 1 \
    | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | sed '/^$/d'
}
