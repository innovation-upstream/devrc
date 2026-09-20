#!/usr/bin/env python3
"""Make the handoff write NON-OPTIONAL for a session that resumed one: PostToolUse
watches, Stop gates.

WHY THIS EXISTS — MEASURED OVER 253 SESSIONS, AND THE LOSS BUCKET IS THIS MOMENT
--------------------------------------------------------------------------------
`claudedocs/handoff-skill-chain-usage-audit.md` measured the `/handoff` -> `/resume`
chain over 2026-08-15..08-29, both hosts, both runtimes: of **253** graded sessions
whose genesis was a `/resume` kickoff, **231 (91.3%) recorded** their work and
**22 (8.7%) did not**. Legitimate declines (`no-change`/`no-advance`) were **ZERO**,
so the gap is not the write gate correctly refusing — it is the write never being
attempted: **19 of the 22 never invoked `handoff_doc.py` at all.**

Rank 1 of that arc then split the 16 readable never-run losses by how the session
ENDED, and the split is what selects this remedy over an auto-draft:

    D cleanly-ended        8   50.0%   <- the dominant bucket
    B interrupted-at-end   4   25.0%
    A context-exhausted    2   12.5%
    0 never-started        2   12.5%   (zero assistant turns)

The losses end CLEANLY, 8-vs-2 against exhaustion. A session that runs out of context
needs an auto-draft-before-compaction and would be reached by 2 of 16; a session that
finishes its work and stops needs a NUDGE AT THE MOMENT IT STOPS, and that is a `Stop`
hook. 🔴 The decisive number: `stop_hook_summary` rows are present in **8 of 8**
cleanly-ended losers (13 of 16 overall) — so the hook infrastructure demonstrably
EXECUTES in 100% of the bucket this is built for. It is not merely warranted, it is
mechanically reachable exactly where the handoff is being lost.

Prose already lost here, the same way it did for the clawgate write-back. `/handoff`
exists, `/resume` step 6 points at it, and 19 sessions still never ran it.
PRINCIPLES.md prefers a deterministic/structural fix over prompt-tuning; this is that
fix, and `clawgate-writeback-guard.py` is its measured precedent — same arm-on-read /
gate-on-Stop shape, 86% write-back compliance on the board it guards.

🔴 ARMED ON A `/resume` **READ**, NOT ON SESSION START
------------------------------------------------------
Arming on `SessionStart` would fire on every session that never touched a handoff —
most of them — and a guard that nags the majority to write a document they have no
business writing is worse than no guard. The act that defines the measured population
is the READ of a specific handoff doc, so that is what arms this hook:

    Read(file_path=".../claudedocs/handoff-<topic>.md")     # /resume step 3
    Bash: git show <ref>:claudedocs/handoff-<topic>.md      # the same read, off a ref
    Bash: cat/head/less .../claudedocs/handoff-<topic>.md

Same reasoning as the precedent's: gate on the act that PROVABLY happened in the
losing sessions, not on the act you wish had.

WHAT FIRES IT — THREE CONDITIONS, ALL REQUIRED
-----------------------------------------------
  1. the session READ a specific handoff doc (above), and
  2. REAL WORK happened in the session — an Edit/Write/NotebookEdit tool call, or a
     Bash `git commit` / `git push` / `gh pr create|merge` / `gh release create`
     **in command position, outside quotes and comments** (see WORK_BASH_PAT), and
  3. NO handoff write is observable since that doc's first read — neither an
     invocation of `handoff_doc.py`, nor a Write/Edit of any handoff doc, nor an
     mtime on the resumed doc itself at or after the read.

Condition 2 is the false-positive killer and it is not optional. A session that
resumes a doc, reconciles it, reports "nothing has moved" and stops owes the record
NOTHING — and the measurement agrees: `no-change`/`no-advance` declines were counted
as legitimate and were zero, i.e. the skill's own gate already handles that case.
Nothing here fires on a read alone.

Condition 3 is a MEASUREMENT of the filesystem plus this session's own observations,
never an inference from one alone. It is what makes the hook self-suppressing: the
moment a handoff is written the guard goes quiet, including for a doc written by
`/handoff` in a throwaway worktree, by a different process, or under a different
topic entirely.

🔴 TOPIC DRIFT COUNTS AS RECORDED, DELIBERATELY. 25 of the 253 sessions resumed doc X
and wrote doc Y (`clawgate-usage-audit` -> `clawgatectl-agent-delivery`). The
measurement scored those RECORDED — the work IS on disk and committed, scope
legitimately moves — so this guard must too, or it would block 25 sessions that did
exactly the right thing. That is why the `handoff_doc.py` / handoff-doc-write
observations are SESSION-level and path-agnostic, while only the mtime route is
per-doc. (What drift genuinely costs — doc X left looking maintained while going
stale — is rank 3 of that arc, and is NOT this hook's job.)

🔴 THE COMPARISON ANCHOR IS THE **FIRST READ** OF EACH DOC, NOT THE LAST WORK EVENT —
AND THAT IS THE ONE PLACE THIS DELIBERATELY DIVERGES FROM ITS PRECEDENT. The clawgate
guard must anchor on the last work event because its skill's pickup ritual posts a
"Starting" comment BEFORE the work, which would satisfy a read-anchored check at
pickup and make a missing COMPLETION write-back unobservable. There is no pre-work
handoff ritual: `/handoff` runs at the END of a session, so a read anchor has no such
degenerate case — and a work anchor would import a false-positive class this one does
not have. The shape that class produces is routine here:

    write the doc (mtime T) -> `git -C $WT commit` -> `git push` (work at T+90s) -> Stop

Anchored on the last work event that owes another handoff write; anchored on the read
it is satisfied, correctly. The cost of the read anchor is named rather than hidden:
a session that writes its handoff EARLY and then works for hours without updating it
is scored as recorded. That is the same thing the measurement scored, and it is a
much rarer and milder shape than blocking every session that commits after writing.

ESCALATION LADDER — per session, per doc
-----------------------------------------
    fire 1  ->  decision: block      (FORCED CONTINUATION; `reason` reaches the model)
    fire 2  ->  decision: block      (FORCED CONTINUATION)
    fire 3  ->  systemMessage        (the turn ENDS; operator sees it, model does not)
    fire 4+ ->  silent

Identical rungs to the precedent, which is measured at 86% compliance — deviating
from a measured ladder without a measurement of my own would be speculation. The
per-doc budget is the same, but MAX_DOCS is 3 rather than 5, so the worst case a
session can reach is strictly cheaper than the guard this is modelled on.

MULTI-TURN COST, STATED RATHER THAN DISCOVERED IN PRODUCTION. Work that spans several
turns fires once per turn until the per-doc ladder is spent: at most TWO forced
continuations and one `systemMessage`, then silence forever for that doc in that
session. Because the anchor is the READ and not the last work event, a handoff written
at ANY point after the read ends it permanently — so the ordinary shape is exactly one
block, the model writes the doc, and the guard never speaks again. The shape that
costs the full ladder is a session that is told to write a handoff, declines twice,
and stops; that is the shape this exists for.

🔴 `additionalContext` IS NOT A NON-BLOCKING CHANNEL ON Stop. The CLI pushes it into
the SAME `blockingErrors` array as `decision:"block"`, so emitting it would force a
continuation while claiming not to; `systemMessage` is yielded on the message channel
and is not fed back to the model. That is a re-derivation from the installed bundle,
not from documentation — the full reads, both controls, and the
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` interaction are in `clawgate-writeback-guard.py`'s
module docstring, which is the ONE place they are recorded. `emit()` below has
exactly the two channels that docstring proves are distinct, and no third.

🔴 THERE IS DELIBERATELY NO `stop_hook_active` GATE, for the precedent's reason: the
SECOND block is the whole point — it is what catches a turn that acknowledged the
first block and stopped anyway — and the ladder bounds the loop at 2 per doc, far
inside the CLI's own cap of 8.

🔴 THE SUBAGENT RULE IS ASYMMETRIC, and both halves are inherited from a measurement,
not invented here. A subagent's tool call arrives wearing the PARENT's `session_id`
with only `agent_id` to tell them apart:
  * a subagent's READ must NOT arm the parent — the precedent measured that as a
    false positive, a parent blocked on something only a subagent touched;
  * a subagent's WORK IS the session's work — the parent dispatched it, the parent
    owns the record. Refusing it deleted the precedent's yield on the exact incident
    it existed for.
Stop carrying an `agent_id`, and SubagentStop, are refused outright: a subagent's turn
never reaches the operator, so it owes them nothing.

🔴 FAIL-OPEN, ALWAYS. Every internal exception exits 0 with empty stdout and blocks
nothing. `main()` has exactly ONE exit and it is always 0. A hook that wedges a turn
to enforce a bookkeeping ritual has inverted its own cost model.

🔴 HOT PATH. PostToolUse fires after EVERY tool call of every session. The fast path
is: one dict read for `agent_id`, resolve the state dir from `session_id` (string
work, no IO), ONE `os.path.exists`, and — for a main-thread call only — the arming
regex. A session that has never read a handoff doc and is not reading one now does
nothing else: no directory creation, no state read, no subprocess, and it does not
import `shutil` (see the deferred-import note). Unlike its precedent this hook spawns
NO subprocess on ANY path, Stop included: condition 3 is filesystem-only.

🔴 THE TELEMETRY IS STOP-ONLY AND IS NOT ON THAT PATH. `hook_telemetry` (and through
it `spool_emit`, which drags in `base64`/`datetime`/`socket`) is imported from
`_emit_telemetry`, which `main()` calls on every event but which RETURNS BEFORE THE
IMPORT when there are no rows — and a PostToolUse call produces none. So the hot path
never loads it and pays nothing. Same reasoning as the `shutil` note below, and the
same measurement behind it.

⚠ SAY IT AS A GUARD, NOT AS A CALL-SITE FACT, BECAUSE THE CALL-SITE SPELLING WAS FALSE
FOR A COMMIT. At `4e384b6f` this paragraph read "imported from `main()`'s Stop arm and
from nowhere else" — true at `0a8034ae`, where the call sat inside `elif event ==
"Stop":`, and false the moment the emission moved into its own handler. MEASURED then:
a PostToolUse payload left `'hook_telemetry' in sys.modules` True. The property is now
`_emit_telemetry`'s early return rather than where the call is written, and
`test_hook_telemetry.py::test_a_post_tool_use_call_does_not_IMPORT_the_emitter` pins it
with the Stop path as its positive control.

WHAT THIS STRUCTURALLY CANNOT SEE (say it here, not in a report nobody re-reads):
  * the 2 never-started sessions of the 16. They produced zero assistant turns, so no
    Stop fired and no hook of any kind could have reached them. Named because it
    bounds the ceiling: this remedy addresses at most 14 of 16, not 16;
  * the 4 interrupted-at-end sessions in the general case — 3 of 4 did fire a Stop
    hook, so most are reachable, but an interrupt that kills the process is not;
  * a read performed only inside a subagent (the read half refuses `agent_id`);
  * a read via a route the arming regex does not match — a `Grep`/`Glob` result the
    model read off screen, an editor, someone pasting the body in;
  * WHICH doc a given write belongs to. Two of the three satisfaction routes are
    session-level by construction, because drift must count (see above). The
    consequence is stated rather than hidden: `read X -> read Y -> write only Y`
    is SILENT for X. That is the deliberate side of the trade;
  * whether the handoff that WAS written is any good — it checks that one exists
    since the read, never what it says. `/handoff`'s own write gate owns quality;
  * a handoff doc that is not REALLY THERE on this host. Arming requires the resolved
    `claudedocs/` directory to be real AND the doc itself to be a file, so a path that
    resolves nowhere is not armed at all — the quiet direction. The one exemption is a
    doc read out of a git object (`git show <ref>:claudedocs/<doc>`), which is
    legitimately absent from the working tree; `_read_off_a_ref` says what that
    exemption does and does not cover, and why it is not verified;
  * anything on a host where this hook is not deployed, or a runtime that is not
    Claude Code. The measurement covered opencode too; this covers Claude Code only.

Deployed by `nix/home.nix`; registered on PostToolUse (no matcher) + Stop by
`register-nudge-hook.py`. It has ONE other mode, and it is not a hook mode:

    handoff-write-guard.py --dismiss <doc-key> --session <session_id>

Every invocation of that mode appends one JSON line to
`~/.cache/claude-handoff-write/dismissals` — it is a bypass of a deterministic guard,
so it is MEASURABLE rather than merely discouraged in the block text.
"""
import datetime
import json
import os
import re
import sys
import time

# 🔴 DEFERRED IMPORT, AND THE REASON IS THE HOT PATH — the same reasoning
# `clawgate-writeback-guard.py` records and measured (`shutil` alone was 3.7 ms of
# that hook's per-call cost). `shutil` is reachable ONLY from `prune`'s removal
# branch, i.e. only on a Stop that has something stale to sweep. `re` and `json` stay
# at the top: the arming pattern is consulted before the fast-path return and the
# payload is JSON on stdin, so both are on a path that cannot avoid them.
#
# 🔴 This hook imports NO `subprocess` at all, on any path. Its precedent needs one
# because its condition 3 is a live read of a remote board; condition 3 here is a
# stat and two file reads, so there is nothing to spawn.
shutil = None


def _sh():
    """`shutil`, imported on first use. Bound to the MODULE attribute so a test can
    monkeypatch `guard.shutil` once the Stop path has touched it.

    No `if shutil is None` memo guard: `import` IS the memo (every call after the
    first is a `sys.modules` lookup), and a guard whose only effect is skipping that
    lookup is a branch no mutation can kill.
    """
    global shutil
    import shutil as _m
    shutil = _m
    return shutil


# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

# 🔴 THE LADDER. Two forced continuations per doc, then one operator-facing notice,
# then silence forever for that doc in that session. Both numbers are the precedent's,
# which is measured at 86% compliance; the CLI's own consecutive-block cap is 8, so
# this stays far inside a ceiling that would otherwise end the turn with a warning.
MAX_BLOCKS = 2
MAX_FIRES = 3

# 🔴 Enforced at the WRITER (`record_read`), so `tracked_docs` structurally cannot
# return a fourth. Three rather than the precedent's five because a session normally
# resumes ONE handoff: the extra slots exist for the `/resume` shapes that legitimately
# read two (a doc plus the doc it drifted to), not to track a survey.
MAX_DOCS = 3

# Session state older than this is swept at Stop. Two weeks: long enough that a
# session resumed after a weekend still has its ledger, short enough that the cache
# cannot grow without bound.
STATE_TTL_SECS = 14 * 24 * 3600

# The write path `/handoff` step 5 drives. Named here so the block text and the
# observation agree on one string.
HANDOFF_TOOL = "scripts/lib/handoff_doc.py"

# --------------------------------------------------------------------------- #
# Triggers
#
# 🔴 BOTH DIRECTIONS MATTER. This must match a read of a SPECIFIC handoff doc and
# must NOT match the ways a session merely talks about handoffs. `ls claudedocs/`,
# `grep -rn 'handoff' claudedocs/` and a bare `claudedocs/` all lack the `.md`
# basename, so none of them arms anything — the pattern requires `claudedocs/` and a
# handoff-shaped `.md` basename in ONE contiguous run of path characters.
# --------------------------------------------------------------------------- #

# A run of path characters containing `claudedocs/` and ending in `.md`.
#
# 🔴 `:` IS NOT IN THE CHARACTER CLASS, AND THAT IS LOAD-BEARING RATHER THAN
# INCIDENTAL. `git show origin/zach/topic:claudedocs/handoff-x.md` is the canonical way
# to read a handoff that lives on an unmerged branch — MEASURED at 1,272 distinct such
# commands across the transcript corpus — and excluding `:` makes the match start
# cleanly at `claudedocs/` instead of swallowing the ref.
# The remaining leading run is what carries a `-C`-less absolute or `~`-prefixed path.
#
# ⚠ THIS NOTE CITED `CLAUDE.md` AND THAT WAS FALSE: that file contains no `git show`
# at all. Round 0 of #1799 caught it, and a LINE-BASED grep did NOT — the claim wrapped
# across these comment lines, so only a sweep over normalised text found it.
HANDOFF_PATH_RX = re.compile(r"[A-Za-z0-9_.~@%+/-]*claudedocs/[A-Za-z0-9_.%+-]+\.md")

# 🔴 TWO BASENAME SHAPES, because `/resume` resolves in exactly this order and the
# second one is a real repo's real handoff: `claudedocs/handoff-*.md` first, then
# `claudedocs/*HANDOFF*.md` (civitai-manager names its doc `SESSION-HANDOFF.md`).
# A skill that resolves two shapes and a guard that arms on one would be silent for
# whichever repo uses the other.
HANDOFF_BASENAME_RX = re.compile(r"(?:^handoff-.*\.md$)|(?:^.*HANDOFF.*\.md$)")

# `python3 …/handoff_doc.py` — the write `/handoff` step 5 performs. Anchored on the
# interpreter so a `grep handoff_doc.py` or a doc mentioning the filename is not
# mistaken for a run of it.
HANDOFF_RUN_RX = re.compile(r"\bpython3?(?:\.\d+)?\s+(?:-\S+\s+)*\S*handoff_doc\.py\b")

# `git -C <dir>` / `git -C<dir>`, used ONLY to widen the set of base directories a
# relative match is resolved against. Never used to decide anything.
DASH_C_RX = re.compile(r"(?:^|\s)-C\s*(\S+)")

# The two halves of `_read_off_a_ref` — the ONLY exemption from the existence gate.
#
# 🔴 A `<ref>:` IMMEDIATELY BEFORE THE MATCH. `HANDOFF_PATH_RX` excludes `:` from its
# class precisely so the match starts at `claudedocs/` rather than swallowing the ref
# (see its own note), which is what leaves the ref sitting in the text BEFORE the
# match for this to find. `\Z` anchors it there: a `:` anywhere else in the command
# is not a ref prefix for THIS token.
#
# 🔴 THE REF TOKEN IS `[^\s:]+`, NOT AN ENUMERATED CHARACTER SET, AND THE ENUMERATION
# IS WHY. It was `[A-Za-z0-9_./~^@{}\[\]-]+` with a leading class of `[\s"'=(]`, which
# admits a LITERAL ref and rejects every computed one: `$B:`, `${B}:`, `$(git rev-parse
# HEAD):` and a backtick substitution all failed, because `$`, `)` and `` ` `` are in
# neither class. MEASURED by round 1 of #1799 over the transcript corpus: of 3,888
# ref-prefixed handoff tokens, 166 were denied the exemption and 32 changed outcome —
# 27 of them `$VAR`/`${VAR}`. 🔴 AND `claude/RULES.md` PRESCRIBES THE BRACED SPELLING:
# zsh eats `$B:path` as a history modifier, so the rules mandate `${B}:path` — the guard
# was blind to precisely the spelling this repo requires. An enumerated set encodes the
# examples its author thought of; `[^\s:]+` encodes the actual boundary, which is that
# a ref is one shell word and cannot contain the `:` that terminates it.
REF_PREFIX_RX = re.compile(r"(?:^|[\s\"'=(])([^\s:]+):\Z")

# …and a git object-read verb in the SAME command segment.
#
# 🔴 THE SEGMENT IS SPLIT OUT BEFORE THIS RUNS, NOT EXPRESSED INSIDE IT. The first
# spelling was `\bgit\b[^;&|\n]*?\b(?:show|cat-file)\b` searched over the WHOLE
# command, on the theory that the negated class kept it inside one segment. It does
# not: `git show HEAD; cat host:claudedocs/x.md` matches `git show` in the FIRST
# segment and the exemption is borrowed by a path in the second. Caught by
# `test_the_ref_exemption_does_NOT_widen_into_a_hole`, which is in the diff that
# introduced the bug — the case was written because the hole was imaginable, and it
# turned out to be real.
#
# ⚠ SPLITTING ON `\n` MAKES THE SEGMENT ONE **LINE**, so a `git … show \` whose
# ref-prefixed path sits on the next line would lose its verb. Line continuations are
# therefore JOINED before the split (`_read_off_a_ref`), which is the shape that
# actually occurs; a genuinely separate line is a separate command and SHOULD lose it.
SEGMENT_SPLIT_RX = re.compile(r"[;&|\n]")
LINE_CONTINUATION_RX = re.compile(r"\\\n")

# 🔴 THE NEGATED CLASS IS BACK, AND THE SCAN IS LENGTH-CAPPED — both for COST, not
# correctness. `\bgit\b.*?\b(?:show|cat-file)\b` anchors at every `git` and walks
# forward from each, i.e. O(k·n): round 1 of #1799 measured 924 ms on 32 KB of `"git "`
# with no verb, 13.9 s on 128 KB, against 0.8 ms at base. This hook runs after EVERY
# tool call and can block a Stop, so a pathological command is a turn-level hang.
# ⚠ REPORTED WITH ITS BOUND: the largest segment ever scanned across 20,670 real
# corpus commands was 296 BYTES and the worst real delta +1.41 ms — there is no
# adversary here, the operator writes his own commands. The cap is 4 KiB, an order of
# magnitude above anything observed, so it bounds the hazard without reaching any real
# command.
GIT_OBJECT_READ_RX = re.compile(r"\bgit\b[^;&|\n]*?\b(?:show|cat-file)\b")
GIT_VERB_SCAN_CAP = 4096

# Tool calls that ARE work, by name. Also the tools whose `file_path` can SATISFY,
# when it names a handoff doc.
WORK_TOOLS = ("Edit", "Write", "NotebookEdit")

# The payload keys a file-taking tool uses. `NotebookEdit` uses `notebook_path`.
PATH_KEYS = ("file_path", "notebook_path")

# 🔴 THIS BLOCK IS A VERBATIM COPY OF `clawgate-writeback-guard.py`'s WORK DETECTION,
# AND THE DUPLICATION IS DELIBERATE, DECLARED, AND PINNED BY A TEST RATHER THAN BY
# THIS COMMENT. `claude/RULES.md` says one rule, one place — so the alternative was
# considered and rejected on measured grounds: extracting it into a shared hook
# module would add an import to the OTHER hook's fast path, which fires after every
# tool call of every session and whose owners measured its cost to ~0.1 ms across
# several audit rounds. Paying a regression on a BLOCKING hook that is not in this
# change's scope, to de-duplicate two constants, is the wrong trade.
#
# What replaces the shared module is an ASSERTED LEDGER: the pair of files is pinned
# byte-for-byte on these three patterns by
# `tests/test_handoff_write_guard.py::test_the_work_detection_is_byte_identical_to_the_precedent`,
# which fails when EITHER copy moves. So the copies cannot drift silently, which is
# the failure mode the rule is actually about. If a third copy is ever wanted, extract
# then — three call sites is where the import cost stops being the dominant term.
#
# The reasoning the patterns encode, in one line each (the full measurements are in
# the precedent): quoted runs and trailing comments are stripped FIRST, so a command
# that MENTIONS `git commit` is not one that RUNS it; the verb must be in COMMAND
# POSITION; a quoted run becomes a one-character TOKEN rather than whitespace, because
# blanking it let `git -C "$DEVRC" commit` read the subcommand as the flag's value —
# and `git -C <path>` is the form these repos mandate.
#
# Pattern STRINGS, not compiled objects, for the precedent's measured reason: none of
# them is reachable from the fast path, and compiling them at import made every tool
# call in every session pay for regexes almost none of them use.
_CMD_START = r"(?:^|[\n;&|(){}`]|\$\()\s*(?:(?:then|else|do|!)\s+)*"
WORK_BASH_PAT = (
    _CMD_START
    + r"git\s+(?:-[A-Za-z]\s+\S+\s+|--[A-Za-z][-\w]*(?:=\S+)?\s+)*(?:commit|push)\b"
    r"|" + _CMD_START
    + r"gh\s+(?:pr\s+(?:create|merge)|release\s+create)\b")
QUOTED_PLACEHOLDER = "''"
QUOTED_PAT = r"'[^']*'|\"(?:[^\"\\]|\\.)*\""
COMMENT_PAT = r"(?:^|(?<=\s))#[^\n]*"

# State file names. `work` is a session-level stamp; `wrote` is the session-level
# record of a handoff write. 🔴 Their prefixes are deliberately disjoint from `read-`,
# `fires-`, `unknown-` and `dismissed-`: `tracked_docs` selects on `read-` and
# `record_read`'s MAX_DOCS census counts only those, so no other artifact can be
# mistaken for a tracked doc or consume a tracking slot.
STATE_WORK = "work"
STATE_WROTE = "wrote"
DISMISSED_PREFIX = "dismissed-"


# --------------------------------------------------------------------------- #
# Session-scoped state
# --------------------------------------------------------------------------- #
def _state_root():
    """HOME read at CALL time, not import time, so a test can point it somewhere safe."""
    return os.path.join(os.path.expanduser("~"), ".cache",
                        "claude-handoff-write", "s")


def _sanitize(part):
    """One path component, made safe to join onto the state root.

    🔴 THE ALLOWED SET INCLUDES `.`, SO IT MUST EXCLUDE THE ALL-DOTS COMPONENTS. `/` is
    already replaced, which leaves exactly `.`, `..`, `...` … as the strings that can
    traverse: a `--dismiss ..` would otherwise resolve to the state ROOT rather than to
    a file inside a session dir. An enumerated fix, not a pattern: a component that is
    nothing but dots is neutered; `handoff-x.md` and `v1.2.3` are untouched. Copied
    from the precedent, where the same defect was found and fixed.
    """
    out = re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:120]
    if out and set(out) <= {"."}:
        out = "_" * len(out)
    return out


def _state_dir(data):
    session = (data or {}).get("session_id")
    if not isinstance(session, str) or not session:
        return None
    return os.path.join(_state_root(), _sanitize(session))


def doc_key(doc_path):
    """The per-doc ledger key: the sanitized BASENAME.

    Basename rather than the full path, deliberately. The same handoff doc is reached
    through several spellings in one session — the base clone's copy, the throwaway
    worktree copy that `/handoff` actually writes, a `..`-bearing relative resolution
    — and keying on the spelling would book an entry per spelling, spend every one of
    the MAX_DOCS slots, and emit a block per spelling naming the same file. The
    resolved path is stored INSIDE the record, so nothing is lost.

    🔴 THE COST, NAMED: two different repos each holding a `handoff-<same-topic>.md`
    collide onto one ledger entry, so the second read is a no-op and only the first
    doc's path is reported. It errs toward FEWER blocks, which is the quiet direction,
    and the alternative — keying on the full path — was measured to be worse in the
    common case rather than the rare one.
    """
    return _sanitize(os.path.basename(doc_path))


def _read_path(state_dir, key):
    return os.path.join(state_dir, "read-%s" % key)


def _read_tmp_name(key):
    """`record_read`'s temp file, deliberately OUTSIDE the `read-` namespace.

    🔴 The precedent shipped this as `read-<id>.tmp` and it was wrong: a temp file that
    outlived its `os.replace` — the CLI kills a hook on its timeout, a
    `home-manager switch` swaps the file mid-run — was parsed by the reader as a
    genuine tracked entry, so the Stop gate demanded a write-back for a read that was
    never committed, and the census counted it. The leading `.` is what does the work;
    keep any future scheme out of `read-`/`fires-`/`unknown-`/`dismissed-` too.
    """
    return ".tmp-read-%s" % key


def _dismissed_path(state_dir, key):
    return os.path.join(state_dir, "%s%s" % (DISMISSED_PREFIX, key))


def is_dismissed(state_dir, key):
    """Has `--dismiss` been run for THIS doc in THIS session?

    EXISTENCE is the entire signal; the contents are documentation for a human reading
    the cache by hand. A truncated tombstone still silences — the fail-QUIET direction,
    and the right one: the operator asserted out loud that this session owes no
    handoff, so a half-written file must not resurrect the nagging.
    """
    return os.path.exists(_dismissed_path(state_dir, key))


def write_dismissal_tombstone(state_dir, key, doc="", now=None):
    """Record that this session dismissed this doc. Best-effort; the return value is
    NOT what `dismiss_main` reports on — that re-measures the disk.

    `makedirs` matches every other writer here: a PRE-EMPTIVE dismissal — "this
    session's work is not a handoff", said before any read — still has to mean
    something.
    """
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(_dismissed_path(state_dir, key), "w") as fh:
            json.dump({"doc": str(doc), "dismissed_ts": now_iso(now)}, fh)
        return True
    except Exception:                     # noqa: BLE001
        return False


def _fires_path(state_dir, key, kind="fires"):
    """`fires-<key>` for a MEASURED missing handoff, `unknown-<key>` for a
    could-not-measure notice. 🔴 Two counters, deliberately — the precedent's lesson:
    sharing one let an unmeasurable early Stop spend the block budget that a genuinely
    missing write needs later, so the case the hook exists for could no longer be
    blocked in that session."""
    return os.path.join(state_dir, "%s-%s" % (kind, key))


def now_iso(now=None):
    ts = datetime.datetime.fromtimestamp(
        time.time() if now is None else now, datetime.timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def parse_ts(s):
    """RFC3339 -> epoch seconds, or None."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def tracked_docs(state_dir):
    """{key: {"doc": <abs path>, "first_read_ts": <rfc3339>}} for docs this session
    has read. A record missing either field is skipped rather than defaulted: a
    truncated write must not produce a verdict about a document nobody can name."""
    out = {}
    try:
        names = os.listdir(state_dir)
    except Exception:
        return out
    for name in sorted(names):
        if not name.startswith("read-"):
            continue
        try:
            with open(os.path.join(state_dir, name)) as fh:
                rec = json.load(fh)
            doc = rec["doc"]
            ts = rec["first_read_ts"]
        except Exception:
            continue
        if isinstance(doc, str) and doc and isinstance(ts, str) and ts:
            out[name[len("read-"):]] = {"doc": doc, "first_read_ts": ts}
    return out


def record_read(state_dir, doc_path, now=None):
    """Record the FIRST read of `doc_path`. Idempotent: a later read never moves the
    timestamp, because the window this hook measures over starts at the first one."""
    key = doc_key(doc_path)
    path = _read_path(state_dir, key)
    # ONE existence check. A second copy after `makedirs` would be provably redundant —
    # `makedirs(exist_ok=True)` can neither create nor delete THIS file — so neither
    # copy could be killed by a mutation. The cheap one is kept, so a re-read of an
    # already-recorded doc still costs no `makedirs`.
    if os.path.exists(path):
        return False
    # 🔴 THE DISMISSAL TOMBSTONE IS CONSULTED HERE, AT THE WRITER, AND NOWHERE ELSE.
    # The precedent measured, in production and twice, that clearing the ledger without
    # a tombstone does not dismiss anything: it restores the session to its pre-read
    # state, so the very next read re-arms the guard — and the natural way to confirm a
    # dismissal took is to LOOK AT THE DOC, which is a read. 90 ms apart, in the same
    # tool call. AFTER the `exists` above, so a re-read of an already-tracked doc still
    # costs one stat.
    if is_dismissed(state_dir, key):
        return False
    os.makedirs(state_dir, exist_ok=True)
    if len([n for n in os.listdir(state_dir) if n.startswith("read-")]) >= MAX_DOCS:
        return False
    tmp = os.path.join(state_dir, _read_tmp_name(key))
    with open(tmp, "w") as fh:
        json.dump({"doc": doc_path, "first_read_ts": now_iso(now)}, fh)
    os.replace(tmp, path)
    return True


def record_work(state_dir, now=None):
    """Stamp that REAL WORK happened. Session-level by construction — nothing in a
    PostToolUse payload says which doc an edit belongs to."""
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, STATE_WORK), "w") as fh:
        fh.write(now_iso(now))


def record_wrote(state_dir, now=None):
    """Stamp that a HANDOFF WRITE was observed — a `handoff_doc.py` run, or a
    Write/Edit whose target is a handoff doc.

    🔴 SESSION-LEVEL AND PATH-AGNOSTIC ON PURPOSE, and this is the topic-drift
    accommodation made mechanical: 25 of the 253 measured sessions resumed doc X and
    wrote doc Y, and the measurement scored every one of them RECORDED. Keying this
    stamp to a path would block those 25 for doing exactly the right thing.
    """
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, STATE_WROTE), "w") as fh:
        fh.write(now_iso(now))


def work_happened(state_dir):
    return os.path.exists(os.path.join(state_dir, STATE_WORK))


def _stamp(state_dir, name):
    """The RFC3339 string in a stamp file, or None. None is the FAIL-QUIET direction
    for `work` (no verdict) and the FAIL-LOUD one for `wrote` (nothing satisfies), so
    it is not a shared default — each caller states which it wants."""
    try:
        with open(os.path.join(state_dir, name)) as fh:
            return fh.read().strip() or None
    except Exception:                     # noqa: BLE001
        return None


def bump_fires(state_dir, key, kind="fires"):
    """Read-increment-write the per-doc fire counter; returns the NEW 1-based count."""
    path = _fires_path(state_dir, key, kind)
    n = 0
    try:
        with open(path) as fh:
            n = int(fh.read().strip() or "0")
    except Exception:
        n = 0
    n += 1
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(str(n))
    except Exception:
        pass
    return n


def dismiss(state_dir, key, doc="", now=None):
    """Clear ONE doc from ONE session's ledger AND tombstone it. Returns the file names
    removed (the tombstone is a write, not a removal, and is not in that list).

    🔴 THIS IS THE ESCAPE HATCH AND IT HAS TO BE A MECHANISM. The precedent measured
    that a prose escape ("if you did not do this work, say so and stop") does not work:
    saying something changes no state, so the next Stop re-blocks with identical text.
    """
    removed = []
    for name in ("read-%s" % key, "fires-%s" % key, "unknown-%s" % key):
        try:
            os.remove(os.path.join(state_dir, name))
            removed.append(name)
        except Exception:                 # noqa: BLE001
            pass
    write_dismissal_tombstone(state_dir, key, doc=doc, now=now)
    return removed


def _ledger_residue(state_dir, key):
    """Which of this doc's ledger entries are STILL on disk. Sorted; usually empty.

    🔴 THIS EXISTS BECAUSE `dismiss`'s `removed` LIST CANNOT ANSWER THE QUESTION THE
    REPORT ASKS. `removed` is "which `os.remove` calls did not raise", so an unwritable
    session dir makes it EMPTY — byte-identical to "there was nothing here to remove".
    The precedent printed `nothing to dismiss` over a `read-` file that was sitting in
    the ledger, measured against a `0o500` dir. Only the disk distinguishes the two.
    """
    return sorted(name for name in ("read-%s" % key, "fires-%s" % key,
                                    "unknown-%s" % key)
                  if os.path.exists(os.path.join(state_dir, name)))


def _dismissals_path():
    """The audit log, deliberately OUTSIDE the per-session root that `prune` sweeps, so
    a session dir ageing out cannot take the record of its dismissals with it."""
    return os.path.join(os.path.dirname(_state_root()), "dismissals")


def record_dismissal(key, session_id, removed, now=None):
    """Append ONE line per `--dismiss` invocation. Returns True if it was written.

    🔴 `--dismiss` IS A REAL BYPASS OF A DETERMINISTIC GUARD. In a hook whose entire
    premise is that prose lost 19 sessions out of 22, gating the bypass on prose alone
    and then not measuring it is the same mistake one level up. A no-op dismissal
    records too, so repeat attempts are visible rather than silently identical to a
    first one. Fail-open: an unwritable log never changes what `--dismiss` does.
    """
    try:
        path = _dismissals_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps({"ts": now_iso(now), "doc_key": _sanitize(key),
                                 "session": _sanitize(session_id),
                                 "removed": sorted(removed)},
                                sort_keys=True) + "\n")
        return True
    except Exception:                     # noqa: BLE001
        return False


def prune(ttl=STATE_TTL_SECS, now=None):
    """Drop session state directories older than `ttl`. Returns the names removed.

    🔴 Keyed on the directory's OWN mtime, which the last write to it moved — so a
    long-lived session is not swept out from under itself. Errors are swallowed per
    entry: a state dir that cannot be removed is a few bytes, and a Stop hook that
    raises is felt at the exact moment a session is trying to end.
    """
    root = _state_root()
    cutoff = (time.time() if now is None else now) - ttl
    removed = []
    try:
        names = os.listdir(root)
    except Exception:
        return removed
    for name in names:
        path = os.path.join(root, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            _sh().rmtree(path, ignore_errors=True)
            removed.append(name)
        except Exception:                 # noqa: BLE001
            pass
    return removed


def escalate(fire_number):
    """1-based fire number -> "block" | "notice" | "silent".

    "notice" is the rung that RELENTS, and it emits `systemMessage` — NOT
    `hookSpecificOutput.additionalContext`, which the CLI feeds into the same
    `blockingErrors` array as a block and which therefore does not relent at all. See
    `emit` and the module docstring.
    """
    if fire_number <= MAX_BLOCKS:
        return "block"
    if fire_number <= MAX_FIRES:
        return "notice"
    return "silent"


# --------------------------------------------------------------------------- #
# Trigger matching
# --------------------------------------------------------------------------- #
def _strip_literals(cmd):
    """Replace quoted runs with QUOTED_PLACEHOLDER and blank trailing `#` comments, so
    a command that MENTIONS a work verb is not mistaken for one that RUNS it. Copied
    verbatim from the precedent — see the ledger note above WORK_BASH_PAT.
    """
    return re.sub(COMMENT_PAT, " ",
                  re.sub(QUOTED_PAT, QUOTED_PLACEHOLDER, cmd))


def _is_handoff_basename(path):
    return bool(HANDOFF_BASENAME_RX.match(os.path.basename(path)))


def _bases(cmd, cwd):
    """Directories a RELATIVE handoff path may be resolved against, in priority order.

    🔴 `cwd` ALONE IS WRONG HERE, AND THAT IS NOT HYPOTHETICAL — it is how this very
    arc's own sessions read their handoff. These repos are dispatch hubs: a session
    sits in one clone and reads a doc out of another with
    `git -C ~/workspace/devrc show <ref>:claudedocs/handoff-x.md`. Resolved against
    `cwd` that names a `claudedocs/` in the WRONG repo — which either does not exist
    (silent, fine) or exists and books the wrong document (not fine). Each `-C` value
    in the same command is tried FIRST, and the whole thing is gated on the resolved
    directory actually existing, so a wrong guess resolves nowhere rather than wrongly.
    """
    out = []
    for m in DASH_C_RX.finditer(cmd or ""):
        d = os.path.expanduser(os.path.expandvars(m.group(1)))
        if d not in out:
            out.append(d)
    if isinstance(cwd, str) and cwd:
        out.append(cwd)
    return out


def _read_off_a_ref(cmd, start):
    """Was the path token at `start` read out of a git OBJECT rather than off disk?

    🔴 THIS IS THE ONE EXEMPTION FROM THE EXISTENCE GATE, AND IT IS SYNTACTIC ON
    PURPOSE. The honest check is `git cat-file -e <ref>:<path>` — and this hook
    declares, and `test_the_hook_spawns_no_subprocess_on_any_path` ENFORCES, that it
    spawns no subprocess on any path. So the ref is not verified; the COMMAND SHAPE is.
    Two conditions, both required: the match is immediately preceded by a `<ref>:`
    (which `HANDOFF_PATH_RX` leaves intact by excluding `:` from its character class),
    and the same command segment carries a git object-read verb.

    🔴 WHAT THIS DOES NOT COVER, SAID PLAINLY: a string of the exact shape
    `git show <anything>:claudedocs/handoff-<anything>.md` still arms whether or not
    that ref or that doc exists. That is a far narrower over-match than the one being
    removed — the false positives this fix exists for (`claudedocs/handoff-x-y.md`,
    `claudedocs/handoff-same.md`, both fixture strings) carry no ref prefix at all —
    but it is an over-match, not an absence of one. Verifying it costs a subprocess on
    a hook that fires after EVERY tool call of every session, to reject a shape nobody
    writes by accident.
    """
    # Line continuations joined FIRST: `\<newline>` is one command, not two, and the
    # segment split below would otherwise strand the git verb on the previous line.
    head = LINE_CONTINUATION_RX.sub(" ", cmd[:start])
    if not head.endswith(":") or not REF_PREFIX_RX.search(head):
        return False
    # The command segment this token belongs to — everything since the last separator,
    # and only its last `GIT_VERB_SCAN_CAP` bytes (see the constant for the measured
    # cost that bound exists for, and for why no real command reaches it).
    seg = SEGMENT_SPLIT_RX.split(head)[-1][-GIT_VERB_SCAN_CAP:]
    return bool(GIT_OBJECT_READ_RX.search(seg))


def _resolve(raw, bases, off_a_ref=False):
    """A matched path token -> an absolute doc path that is REALLY THERE, or None.

    🔴 THE FILE, NOT MERELY ITS DIRECTORY — CHANGED, WITH THE REASON. This required
    only `os.path.isdir(dirname)` until 2026-09-19, and the consequence was that any
    `claudedocs/handoff-*.md`-SHAPED token in a command armed the guard as long as the
    session happened to be standing in a repo that has a `claudedocs/`. MEASURED: the
    Stop guard fired twice in one session demanding a handoff for `handoff-x-y.md` and
    `handoff-same.md`, neither of which has ever existed in any repo — both are
    synthetic strings in `scripts/tests/test_find_session_arc.py`. So writing tests
    ABOUT handoff docs armed the guard against its own fixtures, and the arc whose
    subject WAS handoff docs could not stop tripping it.

    🔴 THE CASE THE OLD SPELLING PROTECTED IS STILL PROTECTED, VIA `off_a_ref`. A
    handoff that exists only on an unmerged branch is read as
    `git -C <repo> show <ref>:claudedocs/<doc>`, and such a doc is legitimately absent
    from the working tree. That read still arms. Requiring the file for everything ELSE
    is what stops a doc-shaped string from booking a document nobody can open.

    ⚠ THE AUTHORITY FOR THAT SHAPE IS MEASUREMENT, NOT A DOCUMENT. This docstring said
    "this repo's CLAUDE.md prescribes exactly that", and `CLAUDE.md` contains no `git
    show` at all — the claim is false, it is pre-existing at `HANDOFF_PATH_RX` above,
    and round 0 of #1799 caught it. What DOES support the shape: 1,272 distinct
    `git … (show|cat-file) … <ref>:claudedocs/handoff-*.md` command strings across the
    transcript corpus. Cite that, not a file that will not corroborate it.

    The directory check is kept as well as, not instead of: it is what keeps a
    dispatch-hub session naming a repo this host does not have from resolving at all,
    and it is the only gate left for the `off_a_ref` case.

    🔴 A SECOND, SEPARATE BEHAVIOUR CHANGE LIVES IN THIS LOOP AND IT SHIPPED
    UNDECLARED — named here because round 1 of #1799 found it, not because it was
    intended. The loop used to `return` at the FIRST candidate whose dirname existed;
    it now `continue`s past one whose file is absent and tries the remaining bases. So
    a command carrying several bases resolves to the one that HAS the doc rather than
    to the first that merely has a `claudedocs/`:

        git -C A -C B log -- claudedocs/handoff-z.md   # the doc exists only in B
        before: A/claudedocs/handoff-z.md  (a path that is not there)
        after:  B/claudedocs/handoff-z.md  (the real one)

    MEASURED over the transcript corpus: 24 payloads arm here that were silent before,
    and 47 arm a DIFFERENT doc. That is a widening on a hook that can block a turn, so
    it is stated rather than left to be rediscovered. It is also the right answer —
    it is what fixes the wrong-base resolution behind most of the latched-but-absent
    records on this host — and it is pinned by
    `test_a_LATER_base_wins_when_the_earlier_one_lacks_the_doc`.
    """
    p = os.path.expanduser(raw)
    cands = [p] if os.path.isabs(p) else [os.path.join(b, p) for b in bases]
    for c in cands:
        c = os.path.normpath(c)
        if not os.path.isdir(os.path.dirname(c)):
            continue
        if os.path.isfile(c) or off_a_ref:
            return c
    return None


def handoff_read_docs(data):
    """Every handoff doc this PostToolUse payload is a READ of. Order-preserving,
    de-duplicated, absolute, and empty for anything that is not a read.

    🔴 ONLY READS ARM. A `Write`/`Edit` of a handoff doc is the thing that SATISFIES
    this guard (see `is_handoff_write`), so admitting it here would let one tool call
    arm and satisfy in the same instant — noise with no yield, and a second ledger
    entry for a doc the session is already finished with.

    🔴 THE BASH ARM DOES **NOT** STRIP QUOTES, and that is a deliberate asymmetry with
    `is_work`. Quote-stripping exists to stop a command that MENTIONS a work verb from
    counting as one; here the corresponding over-match is a session that greps for a
    handoff doc by full path and then does real work without writing a handoff — which
    this guard has no business staying silent about anyway. Stripping quotes would
    instead lose the ordinary `cat "$D/claudedocs/handoff-x.md"`, i.e. trade a benign
    over-match for a real blind spot. Comments are still stripped: a `#`-commented path
    is not a read by anybody's reading. The over-match shape is TESTED, not hoped away.
    """
    d = data or {}
    tool = d.get("tool_name")
    ti = d.get("tool_input") or {}
    raws, bases = [], []
    if tool == "Read":
        for k in PATH_KEYS:
            v = ti.get(k)
            # ⚠ THE ARMS ARE ASYMMETRIC ON `claudedocs/` AND THAT IS LEFT ALONE HERE.
            # The Bash arm requires the literal segment (`HANDOFF_PATH_RX` is built
            # around it) and so does ONE of `is_handoff_write`'s two routes — the
            # Write/Edit one; the `handoff_doc.py` route does not. This arm takes
            # `file_path` on basename alone. Narrowing it was tried in this PR and
            # REMOVED: reads of REAL documents outside `claudedocs/` exist and at
            # least one has armed AND FIRED in production
            # (`containers/clawgate/HANDOFF.md`; `~/taxes/2026/HANDOFF.md` is another).
            # Which arm is WRONG is an open question; narrowing the wider one makes
            # both consistently blind to a doc class this guard was demonstrably
            # working on. Decide it on fresh numbers, in its own change.
            #
            # ⚠ NO COUNT IS QUOTED HERE ON PURPOSE. This comment carried "39 reads …
            # across 8 homelab checkouts"; round 1 re-derived over the same corpus and
            # got 82 distinct, 23 still on disk, ONE clawgate `HANDOFF.md`. Both
            # numbers are probably honest — worktrees come and go — but a count with
            # no method and no date cannot be re-checked, and this one is load-bearing
            # for a decision. Re-measure when you take it; do not inherit a number.
            if isinstance(v, str) and v:
                raws.append((v, False))
        # 🔴 `cwd` IS A BASE HERE TOO, EVEN THOUGH THE TOOL ASKS FOR AN ABSOLUTE PATH.
        # `lib/subsystem_touch.py` — which reads these same payloads out of transcripts
        # — records that `file_path` is "ABSOLUTE whenever the caller passed an
        # absolute", i.e. a relative one reaches the payload verbatim when the caller
        # passes one. Without a base, such a read resolves nowhere and arms nothing:
        # a silent blind spot rather than a visible one, which is the shape this file
        # keeps trying to avoid. There is no `-C` on a Read, so `cwd` is the only base.
        bases = _bases("", d.get("cwd"))
    elif tool == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str) or not cmd:
            return []
        stripped = re.sub(COMMENT_PAT, " ", cmd)
        # `finditer`, not `findall`: the exemption is decided from what sits BEFORE
        # each match, so the position is part of the match.
        raws = [(m.group(0), _read_off_a_ref(stripped, m.start()))
                for m in HANDOFF_PATH_RX.finditer(stripped)]
        bases = _bases(stripped, d.get("cwd"))
    else:
        return []
    out = []
    for raw, off_a_ref in raws:
        if not _is_handoff_basename(raw):
            continue
        resolved = _resolve(raw, bases, off_a_ref)
        if resolved and resolved not in out:
            out.append(resolved)
    return out


def is_work(data):
    """True when this PostToolUse payload is REAL WORK, not a read or a look-around."""
    d = data or {}
    if d.get("tool_name") in WORK_TOOLS:
        return True
    if d.get("tool_name") != "Bash":
        return False
    cmd = (d.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        return False
    return bool(re.search(WORK_BASH_PAT, _strip_literals(cmd)))


def is_handoff_write(data):
    """True when this PostToolUse payload IS a handoff being written.

    Two routes, and both are needed. `handoff_doc.py` is the one `/handoff` mandates
    ("Never `Write` the doc yourself") and is the exact instrument the 8.7% figure was
    measured with — 19 of the 22 losers never invoked it. The Write/Edit route covers
    the doc being authored by any other means, including into a throwaway worktree
    whose copy is not the one the read resolved to.
    """
    d = data or {}
    if d.get("tool_name") in WORK_TOOLS:
        ti = d.get("tool_input") or {}
        for k in PATH_KEYS:
            v = ti.get(k)
            if isinstance(v, str) and v and _is_handoff_basename(v) and "claudedocs/" in v:
                return True
        return False
    if d.get("tool_name") != "Bash":
        return False
    cmd = (d.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        return False
    return bool(HANDOFF_RUN_RX.search(_strip_literals(cmd)))


# --------------------------------------------------------------------------- #
# Telemetry — ONE row per decision this guard reaches at Stop.
#
# WHY IT IS HERE AT ALL. This guard's whole output is a decision that is INVISIBLE to
# every downstream measurement: `activity.events` records no hook invocation, so
# "did the guard fire, on which doc, and did the session comply?" could previously
# only be answered by inferring it from transcript prose — and the guard's own block
# text CONTAINS the strings that define compliance, which is how a measurement of a
# sibling guard came back at a bogus 100.0%. A row emitted at the moment of the
# decision, carrying the DOC KEY it was reasoning about, makes that a query. The
# missing per-entity key is not hypothetical either: a lift figure for the sibling
# guard was wrong (+5.3pp against a correct +10.1pp) because the detector matched any
# task id rather than the blocked one.
#
# 🔴 WHAT MAY GO IN A ROW: the doc KEY, enums, booleans and counts. Never the doc's
# PATH (it contains $HOME), never the block text, never anything read out of a
# document.
#
# 🔴 AND THE KEY IS ADMITTED ONLY WHEN SANITIZING IT CHANGED NOTHING — because
# `_sanitize` LAUNDERS. This sentence used to read "already sanitized to
# `[A-Za-z0-9_.-]` by `_sanitize`", offered as the reason the value was safe, and that
# was backwards: sanitizing is exactly what turns arbitrary text INTO the shape
# `hook_telemetry._SAFE_TOKEN` admits. MEASURED on this branch, before the fix — the
# `Read` arm of `handoff_read_docs` takes `tool_input.file_path` VERBATIM (only the
# `Bash` arm goes through `HANDOFF_PATH_RX`), and `_is_handoff_basename` is
# `HANDOFF_BASENAME_RX` (`:277`), BOTH of whose arms use `.*`, which matches spaces:
#
#     Read(file_path=".../claudedocs/handoff- <a sentence someone typed>.md")
#
# arrived in the payload as one underscore-joined token of up to 120 characters. The
# emitter cannot see that: it is handed a compliant string and has no way to ask where
# it came from.
#
# So the call site enforces the other half, and `telemetry_entity` is it: the key rides
# along only when `_sanitize(basename) == basename`, i.e. when the ledger key IS the
# name rather than a rewriting of it. A doc whose name needed laundering is reported
# with NO entity and an `unnameable-entity` extra, so the row is still a denominator
# and the omission is visible rather than silent. The guard's VERDICT is untouched — it
# still arms, blocks and dismisses on exactly the same documents as before; only what
# leaves this process changed.
#
# 🔴 WHAT THAT DOES **NOT** BUY, BECAUSE TWO ROUNDS IN A ROW OVERSTATED IT. Refusing a
# REWRITING is not refusing a NAME. A basename that is already token-shaped needs no
# laundering and is admitted whole, and that is the ordinary case rather than an edge:
# this repo's own handoffs are `handoff-task-spec-drafter-2026-06-24.md`. MEASURED on
# the final tree — a `handoff-<snake_case sentence>.md`, a `handoff-<kebab-case
# sentence>.md` and a `<snake_case sentence>_HANDOFF.md` (the second arm of
# `HANDOFF_BASENAME_RX` needs no prefix) each shipped as `entity`, against
# `handoff- a sentence.md -> None` as the negative control. ⚠ THIS PARAGRAPH USED TO
# CARRY A THIRD CLAIM AND IT IS NOW FALSE — it read "Nor is an admitted key evidence
# that a FILE exists: `_resolve` requires only the DIRECTORY, deliberately, so a `Read`
# of a path that was never on disk arms the guard and its basename ships". Since the
# existence gate (`_resolve`) an admitted key DOES name a file, with one stated
# exemption: a doc read off a git ref, which exists as an OBJECT and not on disk. So
# `entity` names something real in every case but that one — still not a promise a
# consumer should lean on, because the exemption is real.
#
# So what `entity` carries is a NAME someone or something chose, ≤120 chars, not
# guaranteed to name a file — going to the operator's own authenticated ClickHouse, as
# the join key this instrumentation exists to add. That is the honest description; a
# length cap or a hash is an operator decision nobody has made.
#
# 🔴 IT CANNOT COST THE VERDICT, AND IT CANNOT SUPPRESS ONE. Nothing here emits;
# `stop_decision` only APPENDS DICTS to a caller-supplied list. `main()` emits them
# after the verdict has been written to stdout, in its own handler, so a telemetry
# failure cannot swallow a block.
#
# ⚠ IT CAN STILL DELAY THE TURN, AND SAYING OTHERWISE WAS FALSE. This comment used to
# claim "neither suppress a block nor delay one". Ordering after stdout prevents
# SUPPRESSION; it does not prevent delay, because the CLI waits for the hook PROCESS to
# exit, not for its first write. The emission is on the exit path and its cost was
# measured (~9-12 ms, ~1.2-1.7% of the Stop chain's 709 ms p50) rather than argued to
# be zero. `hook_telemetry` bounds the pathological case — a non-regular spool target
# that would block forever — and names what it cannot bound.
# --------------------------------------------------------------------------- #
HOOK_NAME = "handoff-write-guard"

# The act this guard looks for: a handoff write since the doc's first read, by any of
# the three satisfaction routes. Named in the row so a consumer never has to infer
# what `satisfied` meant.
SATISFIER = "handoff-write"

ENTITY_DOC = "handoff-doc"
ENTITY_SESSION = "session"

# 🔴 SPELLED AS LITERALS, NOT IMPORTED, and pinned against `hook_telemetry.DECISIONS`
# by an ASSERTED LEDGER (`test_hook_telemetry.py::
# test_the_decision_vocabulary_is_pinned_two_way`). An import would make this hook's
# decision path depend on a module that is absent on a host without the collector —
# the one host where it must behave exactly as it did before.
DECISION_FIRED = "fired"
DECISION_ARMED = "armed"
DECISION_SUPPRESSED = "suppressed"
DECISION_UNMEASURED = "could-not-measure"

# 🔴 THE TWO REASONS `main()` OWNS, and they exist because the row that used to vanish
# was the one that mattered most.
#
# MEASURED at 0a8034ae: `_emit_telemetry(rows)` sat INSIDE the same `try` as
# `json.load(sys.stdin)` and the whole verdict path, under one `except Exception: pass`.
# So anything that raised before it — a malformed payload, a JSON LIST payload
# (`(data or {}).get` -> AttributeError), any defect inside `stop_decision` — took the
# telemetry with it and the Stop produced NO ROW AT ALL:
#
#     malformed stdin -> handoff-write-guard.py   rc=0  rows=0
#     malformed stdin -> next-step-nudge.py       rc=0  rows=1   (it does this right)
#     well-formed Stop -> handoff-write-guard.py  rc=0  rows=1   (positive control)
#
# 🔴 WHY THAT IS THE WORST POSSIBLE PLACE FOR IT: the population that disappeared is
# the UNMEASURABLE one, so a compliance rate computed from these rows reads clean over
# a guard that was breaking — a numerator with no denominator, which is precisely the
# defect this whole instrumentation exists to remove, reintroduced one level up.
BAD_PAYLOAD = "bad-payload"      # stdin was unreadable, or is not a JSON object
HOOK_RAISED = "hook-raised"      # the payload parsed; the decision path then raised

# Session-level refusals that mean the live read FAILED rather than came back clean.
# 🔴 Their own value, never folded into a clean one. `ledger-unreadable` appears here
# AND as a per-doc reason: the same fact is reachable at two depths — a record
# `tracked_docs` could not parse at all (session level) and one whose read stamp
# `doc_state` could not parse (per doc). One token, because a consumer asking "how
# often could this guard not measure?" wants both.
UNMEASURED_SESSION_REASONS = frozenset({"no-session", "ledger-unreadable",
                                        BAD_PAYLOAD, HOOK_RAISED})

# 🔴 THE CLOSED REASON VOCABULARY, pinned two-way against this file's own emitters by
# `test_hook_telemetry.py::test_the_guards_reason_vocabulary_is_pinned_two_way`.
#
# ⚠ `not-stop` IS IN THE SET AND IS NOT REACHABLE FROM `main()`, and the ledger would
# otherwise read as a claim that every token is live. `main()` dispatches
# `elif event == "Stop"`, so `stop_decision` never sees another event in production and
# no real row can carry this reason. It is kept rather than deleted because
# `stop_decision` is a PUBLIC function the suites drive directly and the next hook
# wired to this pattern will call it the same way — but a consumer must read it as
# "reachable only through a direct call", never as a population to expect rows in.
REASONS = frozenset({
    "not-stop", "subagent", "no-session", "no-state", "no-work", "no-tracked-docs",
    "handoff-written", "handoff-missing", "ladder-spent", "ledger-unreadable",
    BAD_PAYLOAD, HOOK_RAISED,
})


def _stop_token():
    """A per-INVOCATION id, stamped on every row ONE Stop produces. Never raises.

    🔴 WITHOUT IT THIS HOOK HAS NO DENOMINATOR OF ITS OWN. It emits one row per
    DECISION, not per Stop — up to MAX_DOCS of them — so `count(*)` over its rows counts
    documents, and a compliance rate built on that is wrong by however many handoffs
    each session had open. `uniqExact(stop)` is the Stop count. The nudge deliberately
    carries no such field: it emits exactly one row per Stop, so there the row IS the
    Stop (`hook_telemetry`'s ROW SHAPE section states both).

    🔴 GENERATED LAZILY, ON THE STOP PATH ONLY. `main()` also serves PostToolUse, which
    fires after every tool call of every session and whose cost was measured at ~0.1 ms;
    a `getrandom` syscall on that path would be a few percent of it for a field no
    PostToolUse row exists to carry.
    """
    try:
        return os.urandom(8).hex()
    except Exception:                     # noqa: BLE001 — a row without it still counts
        return ""


def telemetry_entity(key, rec):
    """The ledger key, or None when naming it would LAUNDER text into the payload.

    🔴 THIS IS THE CALL SITE'S HALF OF THE PRIVACY BOUNDARY — see the section header
    above for the measurement. `hook_telemetry._SAFE_TOKEN` rejects a string with a
    space in it; it cannot reject one that `_sanitize` already rewrote INTO that shape,
    and `_is_handoff_basename` is `HANDOFF_BASENAME_RX` (`:277`) —

        (?:^handoff-.*\\.md$)|(?:^.*HANDOFF.*\\.md$)

    — TWO arms, the second needing no prefix at all, and `.*` in both matches spaces,
    commas and everything else a sentence is made of.

    So the test is not "does the key look safe" — a laundered key always does — it is
    "was the key a REWRITING of the name". Sanitizing the basename must have been a
    NO-OP: only then is the key the name itself rather than an encoding of arbitrary
    text. `_sanitize` is asked rather than a second character class restated here, so
    the two cannot drift; the `== key` arm additionally refuses a record whose stored
    path does not correspond to the key it is filed under.

    ⚠ WHAT IT RETURNS IS A NAME, NOT A CERTIFICATE. Read the section header's "what
    that does NOT buy": a key that never needed laundering — any `snake_case` or
    `kebab-case` basename, which is how this repo names its own handoffs — is admitted
    whole. Since the existence gate `_resolve` does require the doc itself, so the name
    now belongs to a real file in every case but the stated `_read_off_a_ref` exemption
    — which is exactly why this stays a ⚠ rather than becoming a guarantee. This
    function bounds the REWRITING, and that is all it bounds.

    ⚠ `_sanitize(base) == base` AND `== key` ARE NOT INDEPENDENT, AND THE REDUNDANCY IS
    DELIBERATE — do not "fix the suite" for it. `key` reaching production is always a
    `doc_key(...)` output and `_sanitize` is idempotent, so on every call `main()` makes
    the `== key` arm already implies the first: a mutant deleting `_sanitize(base) ==`
    SURVIVES the suite, and it is an EQUIVALENT MUTANT rather than a test gap. The
    conjunct stays because this is a PUBLIC function the suites drive directly with a
    `key` of their choosing, where the two arms genuinely differ.

    THE COST, NAMED: a handoff doc legitimately named with a `%`, a `~`, a `+` (all
    admitted by `HANDOFF_PATH_RX`) or a basename over 120 characters is reported with no
    entity. The row still exists and still counts, and the omission is marked in `extra`
    rather than being a silent null.
    """
    doc = rec.get("doc") if isinstance(rec, dict) else None
    if not isinstance(doc, str) or not doc:
        return None
    base = os.path.basename(doc)
    return key if _sanitize(base) == base == key else None


def _session_row(rows, session_id, reason, stop=None):
    """Append the session-level record for a Stop that never reached a doc.

    🔴 THESE ARE THE DENOMINATOR. A row set holding only fires cannot answer "out of
    how many?" — it is a numerator wearing a rate, which is exactly the defect this
    instrumentation exists to remove. So a Stop that refuses at the very first gate
    still produces a row.

    🔴 `measured` IS DERIVED FROM THE REASON, not passed in. One rule, one place: a
    caller that could disagree with `UNMEASURED_SESSION_REASONS` is a second copy of
    the same predicate, and this repo re-fixed exactly that shape five times before
    consolidating it.
    """
    if rows is None:
        return None
    measured = reason not in UNMEASURED_SESSION_REASONS
    row = {"hook": HOOK_NAME,
           "decision": (DECISION_SUPPRESSED if measured
                        else DECISION_UNMEASURED),
           "session": session_id or None,
           "entity": session_id or None,
           "entity_kind": ENTITY_SESSION,
           "satisfier": SATISFIER,
           # Never evaluated: none of these gates reaches the satisfaction check.
           "satisfied": None,
           "measured": bool(measured),
           "reason": reason,
           "extra": {"stop": stop} if stop else {}}
    rows.append(row)
    return row


def _doc_row(rows, session_id, key, rec, decision, reason, satisfied=None,
             measured=True, fire=None, rung=None, stop=None):
    """Append the per-doc record.

    🔴 THE ENTITY GOES THROUGH `telemetry_entity`, NOT STRAIGHT FROM `key`. The key is
    a SANITIZED basename, and sanitizing is the laundering step — read that function's
    docstring before relaxing this. The resolved path never rides along at all: it
    carries $HOME.
    """
    if rows is None:
        return None
    extra = {}
    if fire is not None:
        extra["fire"] = fire
    if rung is not None:
        extra["rung"] = rung
    if stop:
        extra["stop"] = stop
    entity = telemetry_entity(key, rec)
    if entity is None:
        # Visible rather than silent: a null `entity` alone is indistinguishable from a
        # hook that has no entity to report, and this one always does.
        extra["unnameable-entity"] = True
    row = {"hook": HOOK_NAME,
           "decision": decision,
           "session": session_id or None,
           "entity": entity,
           "entity_kind": ENTITY_DOC,
           "satisfier": SATISFIER,
           "satisfied": satisfied,
           "measured": bool(measured),
           "reason": reason,
           "extra": extra}
    rows.append(row)
    return row


def _unreadable_read_records(state_dir):
    """True when the session dir holds `read-` entries and `tracked_docs` returned
    NONE of them — i.e. every record it kept was unparseable.

    🔴 AN OBSERVATION, NOT A GATE. It is consulted only to label a telemetry row, it
    is reached only when `tracked_docs` already came back empty, and no verdict
    depends on it. Errors are swallowed to False: a state dir that cannot be listed is
    not evidence of an unreadable record.
    """
    try:
        return any(n.startswith("read-") for n in os.listdir(state_dir))
    except Exception:                     # noqa: BLE001
        return False


def _telemetry():
    """The shared emitter module, or None. Never raises.

    The deployed guard is `~/.claude/hooks/handoff-write-guard.py`, so Python has
    already put `~/.claude/hooks/` on `sys.path` and the plain import resolves. The
    explicit insert is the fallback for every other way this file is loaded — a test
    importing it by path, a run out of the repo — where `sys.path[0]` is something
    else.
    """
    try:
        import hook_telemetry
        return hook_telemetry
    except Exception:                     # noqa: BLE001
        pass
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import hook_telemetry
        return hook_telemetry
    except Exception:                     # noqa: BLE001 — no telemetry is a no-op
        return None


def telemetry_rows(data, rows, completed=True, cli=False, stop=None):
    """The decision records to SHIP for one invocation. Pure, total, never raises.

    🔴 THIS EXISTS BECAUSE THE ROW THAT VANISHED WAS THE ONE THAT MATTERED. See the
    `BAD_PAYLOAD` / `HOOK_RAISED` block above for the measurement: with the emission
    inside `main()`'s single `try`, every invocation that raised before reaching it
    produced NO row, and those are exactly the unmeasurable Stops a rate must not be
    allowed to silently exclude. Split out from `main()` so the mapping — which is the
    whole semantic content of the fallback — is testable without stdin, a spool or a
    subprocess, exactly as `next-step-nudge.decision_for` is.

    The four arms:

      * `cli` — a `--dismiss` run. NOT a hook invocation at all: no stdin was read, no
        event was decided, and a row would invent a Stop that never happened.
      * `data` is not a mapping — unreadable stdin, `null`, or a JSON LIST (which is
        how `(data or {}).get` raised at 0a8034ae). 🔴 The event name is precisely what
        could not be read, so this row is emitted whether the invocation was a Stop or
        a PostToolUse. That is deliberate and it is why the reason token is its own:
        a consumer computing a per-Stop rate excludes `reason='bad-payload'`, and one
        asking how often this hook could not read its input counts exactly it.
      * `rows is None` with a readable payload — a PostToolUse or an event this hook
        does not serve. Silent, unchanged: a row on the PostToolUse path would be four
        orders of magnitude of noise.
      * `completed` is False — the payload parsed and the decision path then raised.
        Whatever records `stop_decision` had already appended are kept (they are real
        decisions it reached) and a `hook-raised` record is added beside them, so a
        partial Stop cannot read as a complete one.
    """
    out = list(rows) if isinstance(rows, list) else []
    if cli:
        return out
    if not isinstance(data, dict):
        _session_row(out, "", BAD_PAYLOAD, stop=stop or _stop_token())
        return out
    if rows is None:
        return out
    if not completed:
        _session_row(out, data.get("session_id") or "", HOOK_RAISED,
                     stop=stop or _stop_token())
    return out


def _emit_telemetry(rows):
    """Ship the collected decision records. Returns how many were ACCEPTED.

    ⚠ "accepted", not "on disk" — `hook_telemetry.emit_rows`' own docstring says why,
    and nothing here should be read as a claim that bytes landed.

    🔴 THE EMPTY-ROWS RETURN IS WHAT KEEPS THE IMPORT OFF THE HOT PATH, and it is not a
    micro-optimisation — it is the difference between two claims this file makes
    elsewhere being true and being false. `main()` calls this UNCONDITIONALLY, after
    `telemetry_rows`, which returns `[]` for every PostToolUse call; without this line
    `_telemetry()` runs there and imports `hook_telemetry` after EVERY tool call of
    EVERY session. MEASURED on this branch before the fix: a PostToolUse payload left
    `'hook_telemetry' in sys.modules` True (`spool_emit` stayed False — it is loaded
    lazily one level further in). The import was measured at +0.3-1.6 ms against a
    ~0.1 ms hot path, while `_stop_token`'s docstring refuses an `os.urandom(8)` on
    that same path at 0.0002 ms — the module docstring's "never loads it and pays
    nothing" and that refusal are BOTH restored by this return, and both are false
    without it.

    ⚠ `HOOK_TELEMETRY_OFF` does NOT substitute for it: the kill switch is read inside
    `hook_telemetry`, so reaching it already means the import was paid.
    """
    if not rows:
        return 0
    ht = _telemetry()
    if ht is None:
        return 0
    return ht.emit_rows(rows)


# --------------------------------------------------------------------------- #
# The verdict for one tracked doc
# --------------------------------------------------------------------------- #
def doc_state(state_dir, rec):
    """-> "written" | "missing" | "unknown" for ONE tracked doc.

    🔴 THREE SATISFACTION ROUTES, UNIONED. Two are session-level (a `handoff_doc.py`
    run, a write to any handoff doc) because topic drift must count as recorded; the
    third is the resumed doc's OWN mtime, which catches a write this process never
    observed — another tool, another process, a `/handoff` run in a session that
    reloaded. A union is what makes the guard self-suppressing rather than a record of
    what one hook happened to see.

    🔴 `>=`, not `>`, against the read: one Bash call can be both a read and a write
    (`git show …:claudedocs/x.md && python3 …/handoff_doc.py …`) and `post_tool_use`
    stamps both from the SAME `now`, so an equal pair is a write ON that read.
    """
    read_at = parse_ts(rec.get("first_read_ts"))
    if read_at is None:
        # 🔴 CANNOT MEASURE, NOT CLEAN. A truncated read stamp leaves no anchor to
        # compare anything against; reporting silence here would report the same
        # observable as a session that wrote its handoff.
        return "unknown"
    wrote_at = parse_ts(_stamp(state_dir, STATE_WROTE))
    if wrote_at is not None and wrote_at >= read_at:
        return "written"
    doc = rec.get("doc")
    try:
        if isinstance(doc, str) and doc and os.path.getmtime(doc) >= read_at:
            return "written"
    except OSError:
        # The doc is not on disk — the `git show` case. NOT unknown: the two
        # session-level routes above are still real measurements of this session, and
        # they both came back negative. Falling through to "missing" is what keeps the
        # unmerged-branch shape inside the guard's reach.
        pass
    return "missing"


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def dismiss_cmd(key, session_id):
    return ("python3 ~/.claude/hooks/handoff-write-guard.py --dismiss %s --session %s"
            % (_sanitize(key), _sanitize(session_id)))


def missing_text(doc, key, first_read_ts, session_id=""):
    """The block/notice text for a MEASURED missing handoff write."""
    return (
        "handoff write-back guard: this session read `%s` at %s and then did real "
        "work (an edit, a commit, a push or a PR), but NO handoff has been written "
        "since that read.\n"
        "\n"
        "Write it before the turn ends — run `/handoff`. It drives `%s`, which is "
        "what this guard measures. A doc under a DIFFERENT topic also satisfies "
        "this: scope legitimately moves, and any handoff write in this session "
        "counts.\n"
        "\n"
        "Why you are being stopped for a bookkeeping step: measured over 253 "
        "`/resume` sessions, 22 (8.7%%) never recorded their work, ZERO of them "
        "because the write gate correctly declined, and the single largest loss "
        "bucket — 8 of 16 — is a session that ENDED CLEANLY. That is this moment.\n"
        "\n"
        "If this session's work genuinely does not belong in a handoff, dismiss it. "
        "Saying so changes no state; this does:\n"
        "  %s"
        % (os.path.basename(doc), first_read_ts, HANDOFF_TOOL,
           dismiss_cmd(key, session_id)))


def unknown_text(doc, key, session_id=""):
    """The notice text for a doc whose state could NOT be measured. NEVER a block."""
    return (
        "handoff write-back guard: could NOT measure whether a handoff was written "
        "for `%s` — its ledger entry is unreadable, so this is a cannot-measure, not "
        "a clean bill of health. If this session did work worth resuming from, run "
        "`/handoff`.\n"
        "  %s"
        % (os.path.basename(doc), dismiss_cmd(key, session_id)))


# --------------------------------------------------------------------------- #
# PostToolUse
# --------------------------------------------------------------------------- #
def post_tool_use(data, now=None):
    """Record reads, work and handoff writes. Returns a small dict, for tests.

    🔴 THE ORDERING IS THE POINT ON THIS PATH. `os.path.exists` on the session's state
    dir and the arming regex come FIRST, and a session that is neither tracked nor
    reading a handoff returns before touching the filesystem again.
    """
    # 🔴 THE SUBAGENT RULE IS ASYMMETRIC — a subagent's tool call arrives wearing the
    # PARENT's `session_id`, so `agent_id` is the only field separating them. Its READ
    # must not arm the parent (the precedent measured that as a false positive: a
    # parent blocked on a doc only a subagent touched); its WORK is the session's work
    # (the parent dispatched it and owns the record). So `agent_id` suppresses only the
    # read half, and the work half still requires the session to be ALREADY TRACKED.
    agent = bool((data or {}).get("agent_id"))
    state_dir = _state_dir(data)
    if state_dir is None:
        return {"fast_path": True, "recorded": [], "work": False, "wrote": False}
    tracked = os.path.exists(state_dir)
    # A subagent's payload never contributes docs, so the arming regex is skipped
    # entirely for it — the fast path stays one `exists` and no `re` work.
    docs = [] if agent else handoff_read_docs(data)
    if not tracked and not docs:
        # 🔴 THE FAST-PATH RETURN. Exactly ONE filesystem call has happened and nothing
        # has been spawned. Everything below — the work regex, the write regex, every
        # write and stat of a state file — is reachable ONLY for a session that has
        # actually read a handoff doc.
        return {"fast_path": True, "recorded": [], "work": False, "wrote": False}

    recorded = []
    for doc in docs:
        try:
            if record_read(state_dir, doc, now=now):
                recorded.append(doc)
        except Exception:                 # noqa: BLE001 — fail-open, per read
            pass
    # Work and writes count only for a session that has read a handoff, and the
    # fast-path return above IS that gate: reaching this line means `tracked or docs`
    # was true. A second `and (tracked or recorded)` here would be a copy of a decision
    # already made — unkillable by any mutation.
    marked = wrote = False
    if is_work(data):
        try:
            record_work(state_dir, now=now)
            marked = True
        except Exception:                 # noqa: BLE001
            pass
    if is_handoff_write(data):
        try:
            record_wrote(state_dir, now=now)
            wrote = True
        except Exception:                 # noqa: BLE001
            pass
    return {"fast_path": False, "recorded": recorded, "work": marked, "wrote": wrote}


# --------------------------------------------------------------------------- #
# Stop
# --------------------------------------------------------------------------- #
def stop_decision(data, rows=None, stop=None):
    """Pure-ish decision for a Stop payload -> (kind, text), kind in
    {"silent", "notice", "block"}. Side effect: it bumps the per-doc fire counters,
    which is what the ladder is made of.

    🔴 `rows` IS AN OUT-PARAMETER AND IS NOT OPTIONAL BEHAVIOUR — it is optional
    OBSERVATION. Pass a list and this appends one telemetry record per decision it
    reaches (one per tracked doc, or one session-level record for a Stop that never
    reaches a doc); pass nothing and not one line of this function behaves
    differently. That shape is what lets every existing caller and test stay exactly
    as it was while the decision becomes measurable. The records are DICTS, not
    emissions: nothing here touches the spool, so a decision cannot be LOST to
    telemetry and no telemetry failure can suppress a verdict. `main()` owns the
    emission, after the verdict has reached stdout. See `_emit_telemetry`.

    `stop` is the per-invocation id stamped on every record this call produces, so a
    consumer can count STOPS rather than decisions — see `_stop_token`. It is generated
    here when observing and not passed in, so a direct caller gets one for free;
    `main()` passes its own so a record appended by the fallback path in
    `telemetry_rows` carries the SAME id as the records this function already made.
    """
    d = data if isinstance(data, dict) else {}
    session_id = d.get("session_id") or ""
    if rows is not None and not stop:
        stop = _stop_token()

    def refuse(reason):
        _session_row(rows, session_id, reason, stop=stop)
        return ("silent", "")

    if d.get("hook_event_name") not in (None, "Stop"):
        return refuse("not-stop")         # 🔴 SubagentStop and friends: refused
    if d.get("agent_id"):
        return refuse("subagent")
    state_dir = _state_dir(d)
    if state_dir is None:
        # No session id at all: there is nothing to resolve a ledger against, so this
        # is a could-not-measure rather than a clean "nothing was owed".
        return refuse("no-session")
    if not os.path.exists(state_dir):
        return refuse("no-state")
    if not work_happened(state_dir):
        # 🔴 THE FALSE-POSITIVE KILLER. A session that resumed a doc, reconciled it,
        # reported that nothing had moved and stopped owes the record NOTHING — and
        # the measurement agrees: legitimate `no-change` declines were counted
        # separately and were ZERO, so this case is already handled by the skill.
        return refuse("no-work")

    docs = tracked_docs(state_dir)
    if not docs:
        # 🔴 AN EMPTY LEDGER AND AN UNREADABLE ONE ARE NOT THE SAME FACT, AND THEY
        # SHARE AN OBSERVABLE. `tracked_docs` skips a record it cannot parse —
        # correctly, since it must not produce a verdict about a document nobody can
        # name — so `{}` covers both "this session read no handoff" and "every record
        # it kept was truncated". Reporting the second as a clean measurement is
        # precisely the defect this instrumentation exists to remove, one level up
        # from `doc_state`'s own "unknown". The extra `listdir` is charged ONLY when
        # somebody is observing: it changes no verdict, and both arms still return
        # ("silent", "").
        if rows is not None and _unreadable_read_records(state_dir):
            return refuse("ledger-unreadable")
        return refuse("no-tracked-docs")

    blocks, notices = [], []
    # No `[:MAX_DOCS]` slice: `record_read` refuses to create a fourth `read-` file, so
    # `tracked_docs` structurally cannot return more than MAX_DOCS and a slice here
    # would be a second copy of a cap already enforced at the writer.
    for key in sorted(docs):
        rec = docs[key]
        state = doc_state(state_dir, rec)
        if state == "written":
            _doc_row(rows, session_id, key, rec, DECISION_SUPPRESSED,
                     "handoff-written", satisfied=True, stop=stop)
            continue
        if state == "unknown":
            # 🔴 NEVER blocks, and spends its OWN counter. Cannot-measure is reported,
            # never enforced — and never at the expense of the block budget a measured
            # miss will need if the state becomes readable later in this session.
            n = bump_fires(state_dir, key, "unknown")
            rung = escalate(n)
            if rung != "silent":
                notices.append(unknown_text(rec.get("doc", key), key, session_id))
            # 🔴 `could-not-measure` WHETHER OR NOT IT SPOKE. The ladder decides who
            # hears about it; it does not change what was measured, and folding a
            # spent-ladder unknown into `armed` would make an unreadable ledger
            # indistinguishable from a measured miss the guard had stopped nagging
            # about. `rung` rides along in `extra` so the two are separable.
            _doc_row(rows, session_id, key, rec, DECISION_UNMEASURED,
                     "ledger-unreadable", satisfied=None, measured=False, fire=n,
                     rung=rung, stop=stop)
            continue
        n = bump_fires(state_dir, key)
        rung = escalate(n)
        if rung == "silent":
            # Eligible and measured, withheld by the ladder's own budget — ARMED,
            # not suppressed. A compliance rate that counted these as "nothing owed"
            # would be wrong by exactly the size of the ladder.
            _doc_row(rows, session_id, key, rec, DECISION_ARMED, "ladder-spent",
                     satisfied=False, fire=n, rung=rung, stop=stop)
            continue
        _doc_row(rows, session_id, key, rec, DECISION_FIRED, "handoff-missing",
                 satisfied=False, fire=n, rung=rung, stop=stop)
        (blocks if rung == "block" else notices).append(
            missing_text(rec.get("doc", key), key, rec.get("first_read_ts", "?"),
                         session_id))

    # 🔴 A "could not measure" notice NEVER CAUSES a block — only `blocks` does. When
    # some other doc is blocking anyway the notice rides along in the same reason
    # rather than being dropped, but a Stop whose every doc is unmeasurable can only
    # ever reach `notice`, which does not force a continuation at all.
    if blocks:
        return ("block", "\n\n".join(blocks + notices))
    if notices:
        return ("notice", "\n\n".join(notices))
    return ("silent", "")


def emit(kind, text):
    """The ONE writer. `silent` is handled HERE rather than at the call site, so there
    is exactly one place that decides what reaches stdout.

    🔴 THERE ARE EXACTLY TWO CHANNELS AND ONLY ONE OF THEM ENDS THE TURN.
    `decision:"block"` forces a continuation. `systemMessage` does not — the CLI
    yields it on the message channel, never into `blockingErrors`, and its attachment
    renders to the operator without being fed back to the model.
    `hookSpecificOutput.additionalContext` is NOT a third option: on Stop the CLI
    pushes it into the same `blockingErrors` array as a block, so emitting it here
    would force a continuation while claiming not to. It is deliberately absent.
    """
    if kind == "block":
        json.dump({"decision": "block", "reason": text}, sys.stdout)
        sys.stdout.write("\n")
    elif kind == "notice":
        json.dump({"systemMessage": text}, sys.stdout)
        sys.stdout.write("\n")


# --------------------------------------------------------------------------- #
# --dismiss
# --------------------------------------------------------------------------- #
def dismiss_report(key, session_id, removed, tombstoned, residue=()):
    """The ONE sentence `--dismiss` prints. A pure function of what actually happened,
    so the claim can be pinned as a whole string by a test rather than sampled for
    keywords — a two-word check on this text would be satisfied by its own prefix.

    🔴 THE PROMISE IS SCOPED TO THE SESSION, AND THAT IS THE FIX, NOT A HEDGE. The
    precedent's text said "It will not ask about this again", which was false twice
    over: the next read re-armed the guard, and even with a tombstone it says nothing
    about the NEXT session, which correctly starts fresh. Both are stated.

    🔴 The state dir is NOT printed: it is an absolute path containing $HOME, and
    everything this writes goes to stdout, which the model reads and may quote onward.

    🔴 BOTH HALVES ARE MEASURED OFF DISK. `removed` alone cannot tell "nothing to
    remove" from "the removal FAILED" — both are an empty list — so the head asks the
    filesystem via `_ledger_residue`, and the promise asks it via `is_dismissed`.
    """
    sess = _sanitize(session_id)
    k = _sanitize(key)
    # 🔴 THE RESIDUE BRANCH OWNS BOTH HALVES OF THE SENTENCE, AND COMES FIRST. A ledger
    # entry that survived falsifies both other clauses at once: the head would report
    # nothing to dismiss about a doc demonstrably still in the ledger, and the promise
    # would claim a silence `tracked_docs` will not honour.
    if residue:
        return ("handoff write-back guard: could NOT clear `%s` from session %s's "
                "ledger — %s still present. WARNING: nothing was dismissed and this "
                "guard will still ask about `%s` in session %s."
                % (k, sess, ", ".join(sorted(residue)), k, sess))
    if removed:
        head = ("handoff write-back guard: dismissed `%s` for session %s (cleared %s)."
                % (k, sess, ", ".join(sorted(removed))))
    else:
        head = ("handoff write-back guard: nothing to dismiss — `%s` is not in "
                "session %s's ledger." % (k, sess))
    if tombstoned:
        tail = ("It will not ask about `%s` again in session %s, even if the doc is "
                "read again — a NEW session starts fresh." % (k, sess))
    else:
        tail = ("WARNING: the tombstone could NOT be written, so a later read of `%s` "
                "in session %s will arm this guard again." % (k, sess))
    return head + " " + tail


def dismiss_main(argv):
    """`--dismiss <doc-key> --session <session_id>` — the escape hatch, as a command.

    Prints one line saying what it did and returns. NEVER reads stdin: it is invoked
    by a model from a Bash tool call, where stdin is not a hook payload.

    🔴 `--session` is REQUIRED and is not defaulted. This process has no way to learn
    the caller's session id, and guessing would land a dismissal on some other
    session's ledger. The block text supplies it already filled in.

    🔴 The key is accepted as a BASENAME too (`handoff-x.md`), because that is what the
    operator sees in the block text's first line; `doc_key` sanitizes either spelling
    to the same ledger key, so both work and neither can traverse.
    """
    key = sess = None
    i = 0
    while i < len(argv):
        if argv[i] == "--dismiss" and i + 1 < len(argv):
            key, i = argv[i + 1], i + 2
        elif argv[i] == "--session" and i + 1 < len(argv):
            sess, i = argv[i + 1], i + 2
        else:
            i += 1
    if key is None or sess is None:
        sys.stdout.write("usage: handoff-write-guard.py --dismiss <doc-key> "
                         "--session <session_id>\n")
        return
    k = doc_key(key)
    if not k:
        sys.stdout.write("not a handoff doc key: %s\n" % _sanitize(key))
        return
    state_dir = _state_dir({"session_id": sess})
    if state_dir is None:
        sys.stdout.write("not a session id: %s\n" % _sanitize(sess))
        return
    removed = dismiss(state_dir, k, doc=key)
    record_dismissal(k, sess, removed)
    # 🔴 BOTH CLAIMS ARE RE-MEASURED OFF DISK, NOT TAKEN FROM A RETURN VALUE. A
    # tombstone write that failed while an EARLIER dismissal's tombstone is still
    # present leaves the promise TRUE, and only asking the filesystem sees that.
    sys.stdout.write(dismiss_report(k, sess, removed,
                                    is_dismissed(state_dir, k),
                                    _ledger_residue(state_dir, k)) + "\n")


def main():
    # 🔴 ONE exit, and it is always 0. Nothing inside the try may call sys.exit():
    # SystemExit is a BaseException and would sail past `except Exception`.
    #
    # 🔴 THREE HANDLERS, NOT ONE, AND THE SPLIT IS THE WHOLE FIX. At 0a8034ae the
    # emission sat inside the first `try`, so `json.load` raising took the telemetry
    # with it and the Stop produced NO ROW — measured, and worst exactly where it
    # hurts: the rows that disappeared were the unmeasurable ones, which is a
    # numerator with no denominator wearing a clean compliance rate. The verdict path,
    # the emission and the prune now each own a handler, so a failure in one cannot
    # cost the others. `next-step-nudge.main()` has had this shape all along.
    data = None
    rows = None
    stop = None
    completed = cli = False
    try:
        # 🔴 THE CLI MODE IS DECIDED BEFORE THE STDIN READ and never performs one. A
        # hook invocation carries no argv, so this cannot shadow the hook path — and
        # reading stdin in CLI mode would hang on a terminal forever.
        if "--dismiss" in sys.argv[1:]:
            cli = True
            dismiss_main(sys.argv[1:])
            data, event = None, None
        else:
            data = json.load(sys.stdin)
            event = (data or {}).get("hook_event_name")
        if event == "PostToolUse":
            post_tool_use(data)
        elif event == "Stop":
            rows = []
            stop = _stop_token()
            emit(*stop_decision(data, rows=rows, stop=stop))
            completed = True
        # every other event, SubagentStop included, is not ours
    except Exception:                     # noqa: BLE001 — see the fail-open note above
        pass
    # AFTER the verdict has reached stdout, in its OWN handler: a telemetry failure
    # must not be able to swallow a block that was already written, and an invocation
    # that blew up above still owes a `could-not-measure` row — see `telemetry_rows`.
    try:
        _emit_telemetry(telemetry_rows(data, rows, completed=completed, cli=cli,
                                       stop=stop))
    except Exception:                     # noqa: BLE001
        pass
    # Housekeeping, last and only on a Stop that reached a verdict — `prune` can walk a
    # large cache, and it is the one of the two that may legitimately take a while.
    if completed:
        try:
            prune()
        except Exception:                 # noqa: BLE001
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
