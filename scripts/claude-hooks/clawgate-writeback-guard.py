#!/usr/bin/env python3
"""Make the clawgate task write-back NON-OPTIONAL: PostToolUse watches, Stop gates.

WHY THIS EXISTS — MEASURED, TWICE, AND PROSE ALREADY FAILED
-----------------------------------------------------------
Clawgate tasks #193 and #194 were both picked up, the work was done and shipped as
PRs — and both cards stayed `open` with **ZERO comments**. Both were then
re-dispatched and paid for a second time. `claude/skills/clawgate/SKILL.md`
§"task pickup" already says, in 🔴, that the comment/status ritual is NOT optional
and NOT a thing to be asked for. Prose lost 2/2. PRINCIPLES.md prefers a
deterministic/structural fix over prompt-tuning; this is that fix.

🔴 THE GATE IS KEYED ON THE **READ**, NOT ON THE STATUS FLIP
------------------------------------------------------------
The obvious design — a PreToolUse deny on `clawgatectl task status <id> in_progress`
until a comment exists — is STRUCTURALLY UNREACHABLE for the exact failure it would
be built to catch. In both measured cases no `task status … in_progress` command was
ever issued: the cards never left `open`. A guard on a command that was never run
observes nothing. The one act that provably DID happen in both failures is the read,
so the read is what arms this hook:

    clawgatectl task get <N>          # the SKILL's own step 1
    curl … /api/tasks/<N>[/…]         # the same read, before clawgatectl existed

WHAT FIRES IT — THREE CONDITIONS, ALL REQUIRED
-----------------------------------------------
  1. the session READ a specific clawgate task id (above), and
  2. REAL WORK happened after that read — an Edit/Write/NotebookEdit tool call, or a
     Bash `git commit` / `git push` / `gh pr create|merge` / `gh release create`
     **in command position, outside quotes and comments** (see WORK_BASH_PAT), and
  3. a LIVE re-read of the board at Stop shows NO comment authored by `claude-code`
     with `createdAt` at or after the MOST RECENT WORK EVENT (not merely at or after
     the read — see the anchor note below).

Condition 2 is the false-positive killer and it is not optional. The SKILL's own
step 2 is "EVALUATE and report to Zach. Do NOT flip status yet" — a session that
reads a card, forms an opinion and reports back owes the board NOTHING, and a hook
that blocks that turn is worse than no hook. Nothing here fires on a read alone.

Condition 3 is a LIVE MEASUREMENT, not an inference from what this process saw. It
is what makes the hook self-suppressing: the moment the ritual is followed the
board says so and the guard goes quiet, including for a comment written by a
different process, a subagent, or a devpod agent. A hook that tracked only its own
observations would keep firing after the work was correctly written back.

🔴 THE COMPARISON ANCHOR IS THE LAST WORK EVENT, NOT THE READ — and that is a fix,
not a detail. The clawgate skill's own pickup ritual posts a **"Starting" comment
immediately after the read and BEFORE the work**. Anchored on the read, that comment
satisfies the guard at pickup and the hook can then never observe a missing
COMPLETION write-back: its forward yield on every ritual-following session is zero,
which is the opposite of what it was built for. Anchored on the last work event,
`Starting -> work -> Stop` correctly owes a comment and `Starting -> work -> Done ->
Stop` is satisfied. The CLOCK_SKEW_ALLOWANCE_SECS below is doing real work on this
path: a `Done` comment followed within the allowance by a `git push` is still
satisfied, so only work that lands well AFTER the last comment re-arms the guard.
Bounded, not free — see MULTI-TURN COST below.

🔴 A LIVE READ THAT FAILS IS NOT A CLEAN BILL OF HEALTH. Unreachable board, no
client, unparseable JSON — all of those mean "could not measure", and this hook says
so out loud with a NON-BLOCKING notice rather than going silent. RULES.md: an empty
result cannot distinguish two mechanisms, and reporting silence for "the board is
down" is reporting the same observable as "the ritual was followed".

ESCALATION LADDER — per session, per task id
---------------------------------------------
    fire 1  ->  decision: block      (FORCED CONTINUATION; `reason` reaches the model)
    fire 2  ->  decision: block      (FORCED CONTINUATION)
    fire 3  ->  systemMessage        (the turn ENDS; operator sees it, model does not)
    fire 4+ ->  silent

    TRUE COST of a measured missing write-back: exactly TWO forced continuations.
    TRUE COST of a "could not measure" notice:  ZERO forced continuations, and it
    runs on its OWN counter (`unknown-<id>`), so a board that is down for the first
    Stops cannot spend the block budget a genuinely missing write-back needs later.

🔴 `additionalContext` IS NOT A NON-BLOCKING CHANNEL ON Stop, AND THE FIRST VERSION
OF THIS FILE WAS WRONG ABOUT THAT. Re-derived from the installed bundle (see the
controls below), `Ycd` — the Stop-hook driver — pushes an `additionalContexts` entry
into the SAME array as a `blockingError`:

    if (F.blockingError)      { …; E.push(G); … }
    if (F.additionalContexts) { …; E.push(j); … }
    …
    if (E.length > 0) return { blockingErrors: E, preventContinuation: !1 };

and its caller treats ANY non-empty `blockingErrors` as a forced continuation —
re-querying the model with `stopHookActive:!0` and incrementing the very counter
that `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default 8) bounds. So the old "relent" rung
did not relent: an unreachable board cost THREE forced continuations, not zero.

`systemMessage` is the channel that genuinely does not. In the same bundle it is
yielded on the MESSAGE channel, never into `E`:

    if (j.systemMessage) yield { message: Va({ type:"hook_system_message", … }) };

`Ycd` yields `F.message` straight through and pushes nothing, so `E` stays empty and
the caller returns `{reason:"completed"}` — the turn ends. It is rendered to the
operator as `"<hookName> says: <content>"`, and its attachment-to-messages entry is
`hook_system_message: () => []`, i.e. it is NOT fed back to the model. That is the
right shape for a rung whose whole job is to stop nagging the model and tell the
human instead.

Derived from the INSTALLED CLI bundle, not from documentation (claude-code 2.1.220,
`bin/.claude-wrapped` — NOT the 20 KB `bin/claude` wrapper, a grep against which
returns a meaningless zero). Both controls were re-run against the 275 MB bundle
before ANY of the above was believed a second time: positive
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` -> 5 matches, negative
`zzQuuxNotPresentNonce9137` -> 0. (`ugrep` chokes on `.{N}` against a binary this
size; the reads were done with plain `bytes.find` in Python.)

  * The hook output schema is a top-level object, NOT a hookSpecificOutput arm:
        v.object({continue: …, suppressOutput: …, stopReason: …,
                  decision: v.enum(["approve","block"]).optional(),
                  reason: v.string().describe("Explanation for the decision").optional(),
                  systemMessage: …, terminalSequence: …})
  * and the command-hook consumer reads exactly that, with exit 0:
        M = L && HB(L) && L.decision === "block",
        $ = H.status === 2 || !!M,
        D = M ? L.reason || H.stderr || "" : …            -> {blocked: $, output: D}
    i.e. `{"decision":"block","reason":"…"}` on stdout at exit 0 is the JSON
    equivalent of exit 2, WITHOUT the "Stop hook error occurred" notification that
    exit 2 raises (see next-step-nudge.py's header for that measurement).
  * consecutive Stop blocks are capped BY THE CLI at
        let Kt = wue(process.env.CLAUDE_CODE_STOP_HOOK_BLOCK_CAP, 8);
        if (Kt > 0 && yo > Kt) … "A hook blocked the turn from ending N consecutive
        times — overriding and ending turn."
    Our MAX_BLOCKS = 2 is deliberately far stricter than that 8. A guard that has to
    be overridden by the harness has already lost the operator. 🔴 That cap counts
    `additionalContext` rungs TOO — which is why fire 3 is a `systemMessage`.

🔴 THERE IS DELIBERATELY NO `stop_hook_active` GATE, and that is the one place this
hook diverges from the CLI's own advice ("check stop_hook_active and return success
while it's true"). That advice exists to stop an unbounded block loop; the ladder
above bounds it at 2 per task instead, and the SECOND block is the whole point — it
is what catches a turn that acknowledged the first block and still stopped without
writing. Skipping the second Stop would make fire 2 unreachable in the only shape it
matters. The interaction with MAX_TASKS is named rather than hidden: several tasks
each blocking twice can in principle stack toward the CLI's 8, at which point the
CLI ends the turn with a warning — a graceful ceiling, not a wedge.

🔴 THE SUBAGENT RULE IS ASYMMETRIC: A SUBAGENT'S **READ** DOES NOT ARM THE PARENT,
A SUBAGENT'S **WORK** DOES COUNT AS THE SESSION'S WORK. Two rounds of this file got
it wrong in OPPOSITE directions, so state the rule and both failures, not one.

The mechanism first. The bundle builds every hook payload through

    function Kf(e, t, r) { … return { session_id: t ?? kt(), …,
                                      agent_id: r?.agentId, agent_type: … } }

and PostToolUse calls it as `{...Kf(i, void 0, o), hook_event_name:"PostToolUse", …}`
— second argument `void 0`. So a tool call made INSIDE a dispatched subagent arrives
carrying the PARENT's `session_id`, with only `agent_id` to tell them apart; the
bundle's own schema says as much ("Use this field (not agent_type) to distinguish
subagent calls from main-thread calls").

  * ACCEPTING A SUBAGENT'S READ was wrong: it armed the PARENT off a subagent that
    merely happened to touch a card, and the parent's Stop then blocked on a task the
    parent never touched. MEASURED as a false positive. Still refused.
  * REFUSING A SUBAGENT'S WORK was wrong, and it deleted the yield on the exact
    incident this hook exists for. In BOTH measured failures the work ran in
    dispatched LOCAL SUBAGENTS — `claudedocs/handoff-agent-attention-tooling.md`
    records for #193/#194 that "'dispatch both' meant local subagents, not a devpod".
    MEASURED with a positive control: `parent reads 193 -> a subagent does ALL the work
    (Edit + git commit + git push + gh pr create, every payload carrying agent_id) ->
    parent Stop` returned SILENT against a card with zero comments, while the SAME Edit
    without `agent_id` returned `block`. The parent dispatched the subagent; that work
    is the session's, and the parent owns the write-back.

The work half is still gated on the session being ALREADY TRACKED — i.e. the parent
read a card on its own thread — so a subagent cannot both arm and satisfy the trigger
by itself. SubagentStop (and any Stop carrying an `agent_id`) remains refused
outright: a subagent's turn never reaches the operator, so it owes them nothing
(next-step-nudge.py refuses SubagentStop for the same reason).

🔴 SCOPE OF THE WORK FLAG — SESSION-WIDE, NOT PER-TASK, AND THAT IS A KNOWN COST.
Nothing in a PostToolUse payload says WHICH task an edit belongs to, so "work
happened" is necessarily a session-level fact. Two shapes therefore still fire
without the session owing anything, and both are TESTED rather than hoped away:
  * read task N, then do unrelated work in a different repo -> blocks on N;
  * survey N and M, work on and write back only M -> blocks naming N.
🔴 The THIRD shape that used to be here is now CLOSED, and by data this hook already
stored rather than by a heuristic: `read N -> work -> write N back -> Stop (silent,
correct) -> later merely READ M, no work at all -> Stop blocked naming M`. Work is a
session-level fact, but "was there work AFTER **this task's own** read" is not — the
state dir holds a per-task `first_read_ts` beside the session's `last_work_ts`, so a
task whose read is NEWER than the last work event is skipped outright, before the
live read is even attempted. Deterministic, and it also spends one fewer subprocess.
It does NOT use `cwd` or `agent_type`, both of which are partial and heuristic.
The escape is not prose. `--dismiss` (below) is a real, deterministic mechanism, it
is named in the block text with the session id already filled in, and it clears that
task from this session's ledger for good. The previous escape — "if you did NOT do
work on this task, say so in one line and stop" — was measured NOT to work: the next
Stop re-blocked with identical text, because saying something changes no state.

🔴 AND `--dismiss` ITSELF WAS MEASURED NOT TO WORK, IN PRODUCTION, TWICE — IT NOW
WRITES A TOMBSTONE. Clearing `read-<id>`/`fires-<id>` restored the session to its
pre-read state, so the NEXT read of the card re-armed the guard and it blocked again,
while the message promised "It will not ask about this task again." The footgun is
worse than the bug: the natural way to confirm a dismissal landed is to look at the
card, which IS a read. From the dismissals ledger and the block text's own timestamps:

    22:32:13.912419Z  dismissed 200, removed [fires-200, read-200]
    22:32:14.002017Z  new read of 200 recorded   <- 90 ms later, SAME tool call
    22:46:57.515257Z  dismissed 200 again (identical entry)

— the second dismissal was needed ONLY because verifying the first one re-armed it.
Three audit rounds missed this because every test drove `--dismiss` and then asserted
silence; none of them read the card again afterwards. `dismiss` now also writes
`dismissed-<id>` into the session state dir and `record_read` refuses to re-create
`read-<id>` while it is there. The tombstone is ABSOLUTE for the session and scoped to
it: same directory, so it inherits `prune`'s existing TTL rather than becoming a new
unbounded artifact, and a NEW session starts fresh. Deliberately the opposite placement
from the `dismissals` ledger, which lives OUTSIDE the swept root because it answers a
question asked weeks later.

MULTI-TURN COST OF THE WORK ANCHOR
-----------------------------------
Work that spans several turns fires once per turn until the per-task ladder is spent:
at most TWO forced continuations and one `systemMessage`, then silence forever for
that task in that session. A `Done` comment written within CLOCK_SKEW_ALLOWANCE_SECS
of the last work event already satisfies it, so the shape that actually costs is
"comment, then keep working for more than the allowance, then stop" — which is a turn
whose write-back genuinely is stale. Pinned by tests, so the noise is measured rather
than discovered in production.

🔴 HOT PATH. PostToolUse fires after EVERY tool call of every session, and
agent-ledger-hook.py already costs ~21 ms there. The fast path is: one dict read for
`agent_id`, resolve the state dir from `session_id` (string work, no IO), ONE
`os.path.exists`, and — for a main-thread call only — the trigger regex. A session that has
never read a clawgate task and is not reading one now does nothing else — no
directory creation, no state read, no subprocess, and it does not even IMPORT
`subprocess` or `shutil` (see the deferred-import note below). That ordering is
pinned by tests that COUNT the calls and read the module list out of `-X importtime`,
because an earlier hook in this repo shipped with its throttle consulted AFTER the
subprocess spawn while its comment claimed otherwise.

Re-measured 2026-08-16 after the SECOND audit round, 30 runs per sample, EIGHT
INTERLEAVED samples (1200 processes), every process spawned from an explicit argv list
by a python parent — no shell, so zsh's non-splitting `$var` cannot produce the
impossible 0.37 ms/call an earlier loop reported — and each sample asserting that all
30 exited 0 before its mean is believed:

    main-thread fast path   15.72 ms/call   vs 15.65 for the pre-delta file
    subagent    fast path   15.80 ms/call   vs 15.53
    bare interpreter start   8.90 ms

The main-thread path is PARITY: +0.07 ms against a run-to-run spread of ~0.6 ms, which
is wider. The subagent path is +0.27 ms — also inside that spread, but consistently
above it in 7 of 8 samples, and it is a real added cost with a named cause: a
subagent's payload no longer returns on one dict read, because its WORK has to be able
to count (see the asymmetric rule above). It now resolves the state dir and does ONE
`os.path.exists`; the trigger regex is still skipped for it. Both counts are pinned by
tests that COUNT the calls. A FOUR-sample run of the same benchmark read +0.7 ms on
the main thread and was pure drift — the interleave and the sample count are what
distinguish the two, not one re-run.

🔴 RE-MEASURED AGAIN 2026-08-16 FOR THE DISMISSAL TOMBSTONE, AND THE PROCESS-LEVEL
BENCHMARK COULD NOT RESOLVE IT — SO THE MECHANISM WAS MEASURED DIRECTLY INSTEAD. Two
interleaved 8-sample runs of the benchmark above disagreed with each other (main-thread
NEW-BASE `+0.39 ms`, higher in 7/8 samples; then `+0.06 ms`, higher in 3/8), which is
the signature of an effect below the instrument's floor rather than of a regression.
Calibrating that instrument against the only mechanism available settled it: padding the
file with 130 lines of REAL CODE cost `+0.40 ms` (6/8) and 1300 lines cost `+4.21 ms`
(8/8) — linear, so it resolves ~0.4 ms at best. Comment padding is NOT a valid control
here and the first attempt at one was wrong: 1200 comment lines moved the number
`+0.50 ms` with a stdev of 1.00, because a comment produces no AST node.

The mechanism is compile time and nothing else. The tombstone check lives INSIDE
`record_read`, which the fast path never reaches, so a session that has not read a
clawgate task executes not one new statement — but `python3 <script>` never caches
bytecode for `__main__`, so every invocation re-compiles the whole file and 158 more
source lines are not free. Timed directly (200 `compile()` calls per sample, 10 paired
samples), which has ~1000x the resolution of spawning processes:

    compile BASE  2.140 ms      compile NEW  2.324 ms
    paired delta  +0.184 ms, stdev 0.038, higher in 10/10 samples

i.e. ~1.1% of a ~16 ms call, paid by every tool call in every session, in exchange for
`--dismiss` meaning what it says. Named rather than hidden — and it is the reason not to
answer the next audit round with another page of prose in this docstring.

The earlier round's parity took one deliberate change, still in force: the three
work-detection patterns are pattern STRINGS compiled on first use rather than
module-level `re.compile`, because none of them is reachable from the fast path (see
their note below). A first cut with them compiled at import measured 14.7 against 13.9.

🔴 FAIL-OPEN, ALWAYS. Every internal exception exits 0 with an empty stdout and
blocks nothing. main() has exactly ONE exit and it is always 0. A hook that wedges a
turn to enforce a bookkeeping ritual has inverted its own cost model.

WHAT THIS STRUCTURALLY CANNOT SEE (say it here, not in a report nobody re-reads):
  * work done anywhere this hook is not running — a devpod agent, the clawgate web
    UI, a human, opencode, or a host that has not had `home-manager switch` run.
    🔴 A LOCAL DISPATCHED SUBAGENT IS **NOT** IN THIS LIST: this hook DOES run in one
    (its PostToolUse payloads carry the parent's `session_id` plus an `agent_id`), and
    its work IS counted — see the asymmetric rule above. It is a devpod/remote agent,
    where no hook of ours runs at all, that is invisible;
  * a READ performed only inside a subagent. The read half refuses `agent_id`, so
    `subagent reads 193 -> subagent works -> parent Stop` is SILENT: nothing was ever
    armed. The parent must have read the card on its own thread. Deliberate — the
    alternative was a measured false positive — and the cost is named, not hidden;
  * a session that read the task via a route neither trigger matches (the board UI,
    an `/api/tasks?summary=1` list, someone pasting the body in);
  * whether the comment that DOES exist is any good — it checks that a `claude-code`
    comment exists after the last work event, never what it says;
  * WHICH task a given edit belongs to. The per-task read anchor below narrows this —
    a task read AFTER the last work event is skipped — but among tasks read BEFORE it,
    nothing distinguishes them (see SCOPE OF THE WORK FLAG above);
  * the `in_progress` flip, which it does not require: it gates the WRITE-BACK, which
    is the thing that was measured missing;
  * 🔴 ANY LATER WORK ON A TASK THIS SESSION HAS DISMISSED. The tombstone is ABSOLUTE
    for the session: dismiss task N, then genuinely pick N up and work on it in the
    SAME session, and this guard stays silent for N until the session ends. That is a
    real FALSE NEGATIVE and it is the price of the message being true — the operator
    explicitly asserted the work was not for that card, and the alternative (some
    re-arm signal that decides the assertion has expired) is speculative complexity
    inventing an intention nobody stated. There is deliberately NO `--rearm` flag: the
    escape from a dismissal is a new session, which costs nothing and is unambiguous.
    Everything else about the card is unaffected — a dismissal is not a status change,
    writes nothing to the board, and silences only THIS session's guard.

Deployed by `nix/home.nix`; registered on PostToolUse (no matcher) + Stop by
`register-nudge-hook.py`. It has ONE other mode, and it is not a hook mode:

    clawgate-writeback-guard.py --dismiss <task_id> --session <session_id>

Every invocation of that mode appends one JSON line to
`~/.cache/claude-clawgate-writeback/dismissals` — it is a bypass of a deterministic
guard, so it is MEASURABLE rather than merely discouraged in the block text.
"""
import datetime
import json
import os
import re
import sys
import time

# 🔴 DEFERRED IMPORTS, AND THE REASON IS THE HOT PATH. This hook runs after EVERY
# tool call, so its import cost is paid thousands of times a day. Measured with
# `python -X importtime` on this host: subprocess 3.4 ms, shutil 3.7 ms — 7.1 ms of
# the ~11 ms this hook added over a bare interpreter start, and NEITHER is reachable
# from the PostToolUse path. Both are Stop-only (the live read, and the state prune).
# Measured end to end, 30 fast-path runs per sample, four samples: 19.0 ms/call before
# this against 13.7/13.9/13.8/14.3 after, with a bare interpreter at 8.0-8.8 ms — i.e.
# the hook's own overhead falls from ~11 ms to ~5.4 ms. `re` (2.4 ms) and `json`
# (0.9 ms) stay: the trigger patterns compile at import and the payload is JSON on
# stdin, so both are on the path that cannot avoid them.
# `shutil` is loaded inside the REMOVAL, not at the top of prune(), so a Stop with
# nothing stale to sweep never pays it either — pinned by the importtime test.
subprocess = None
shutil = None


def _sp():
    """`subprocess`, imported on first use. Bound to the MODULE attribute so a test
    can still monkeypatch `guard.subprocess` once the Stop path has touched it.

    No `if subprocess is None` memo guard: `import` IS the memo (every call after the
    first is a `sys.modules` lookup), and a guard whose only effect is skipping that
    lookup is a branch no mutation can kill — which reads as a coverage gap when it is
    really just a duplicate of what the import statement already does.
    """
    global subprocess
    import subprocess as _m
    subprocess = _m
    return subprocess


def _sh():
    """`shutil`, imported on first use — see `_sp`."""
    global shutil
    import shutil as _m
    shutil = _m
    return shutil

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

# Comments authored by anything else (a human on the board, `api`, `drafter`) are
# not this agent writing back. The allowlist that produces this value lives in the
# server (`X-Clawgate-Source` -> {extension, api, drafter, repo-cos, claude-code});
# `clawgatectl task comment` defaults to exactly this one.
AGENT_AUTHOR = "claude-code"

# A card in either of these is already handed over — someone closed it, and nagging
# about a missing comment on a card that is out for review is noise.
CLOSED_STATUSES = ("ready_for_review", "complete")

# Per session, per task id. See the ladder in the module docstring; both are read
# out of the CLI bundle's own cap of 8, which this is deliberately stricter than.
MAX_BLOCKS = 2
MAX_FIRES = 3

# At most this many distinct task ids are tracked per session, so a session that
# sweeps the board cannot turn one Stop into a queue of live reads.
MAX_TASKS = 5

# Wall-clock budget for ALL live reads on one Stop, and the ceiling for any single
# one. A Stop hook that hangs is felt at the exact moment a session is trying to end.
#
# 🔴 NEITHER IS THE REAL WORST CASE, AND SAYING 8.0 OUT LOUD WAS WRONG. The budget is
# only ever consulted BEFORE a read starts, so a read admitted with 0.01 s left can
# still run for its full per-task ceiling; and `_via_curl` gives its `subprocess.run`
# a hard kill at `timeout + 2` so an unkillable curl cannot outlive the wait. The
# bound a Stop can actually hang for is therefore
#     STOP_BUDGET_SECS + PER_TASK_TIMEOUT_SECS + 2  =  15.0 s
# against the CLI's own 600 000 ms hook timeout (`var Hm=600000` in the bundle), which
# is the only other thing bounding it. Both numbers are pinned by tests that exercise
# the DEFAULTS — the ladder tests inject `budget=3.0` and a fake clock, so for a while
# nothing executed these values at all and both survived being multiplied by 75.
STOP_BUDGET_SECS = 8.0
PER_TASK_TIMEOUT_SECS = 5.0
# The margin `_via_curl` adds on top of curl's own `max-time`, so the wait outlives
# the client's self-imposed deadline rather than racing it.
CURL_KILL_MARGIN_SECS = 2

# The board's `createdAt` comes from the SERVER clock; `first_read_ts` is written
# from THIS host's clock. Comparing them across even a small skew would let a
# genuinely-written comment read as older than the read that preceded it. 120 s is
# generous against NTP-synced hosts and far below any real "I read it, then worked
# for a while, then forgot" interval.
CLOCK_SKEW_ALLOWANCE_SECS = 120

# Session state is per-session and never revisited once the session ends, so without
# a sweep `~/.cache/claude-clawgate-writeback/s/` grows one directory per session
# forever. Pruned on Stop only — a handful of times per session, never on the
# per-tool-call path.
STATE_TTL_SECS = 14 * 24 * 3600

# Where the token/URL come from on the curl fallback path. Same file the clawgate
# PermissionRequest hook reads; see the clawgate skill.
CLAWGATE_ENV = "~/.claude/clawgate.env"


# --------------------------------------------------------------------------- #
# Triggers
#
# 🔴 BOTH DIRECTIONS MATTER. These must match a read of a SPECIFIC id and must NOT
# match a listing. `clawgatectl task ls`, `/api/tasks?summary=1` and a bare
# `/api/tasks` are how a session surveys the board without picking anything up, and
# arming the guard on one of those would put a block in front of a turn that never
# claimed a card. `task get` with a non-numeric argument is not a read of task N
# either — there is no such task.
# --------------------------------------------------------------------------- #
TASK_GET_RX = re.compile(r"\bclawgatectl\s+task\s+get\s+(\d+)\b")
# `\b` after the id is what keeps `/api/tasks/193abc` out while admitting the
# trailing segment shapes that exist. `/api/tasks/<id>/comments` is one of them — it
# is 405 on GET (POST-only, per the clawgate skill's task-api reference), so as a
# READ it cannot occur; what it really matches is a curl POSTING a comment, which is
# the write-back itself. Admitting that is harmless in both directions: the live read
# then finds the comment and the guard stays silent.
TASK_API_RX = re.compile(r"/api/tasks/(\d+)\b")

# Tool calls that ARE work, by name.
WORK_TOOLS = ("Edit", "Write", "NotebookEdit")

# 🔴 MATCHING THE LITERAL TEXT ANYWHERE IN A BASH COMMAND IS NOT "IS THIS WORK", AND
# THE FIRST VERSION OF THIS FILE DID EXACTLY THAT. `grep -rn 'git commit' scripts/`,
# `rg 'gh pr create' claude/skills/` and `git log --grep='git push'` all armed the
# work flag — and those exact strings live in this repo's own RULES.md and CLAUDE.md,
# so grepping for them is routine rather than exotic. Seven such over-matches were
# measured. Two deterministic narrowings, in this order:
#
#   1. QUOTED STRINGS AND COMMENTS ARE STRIPPED FIRST (`_strip_literals`). A command
#      that merely mentions `git commit` inside quotes, or after a `#`, is talking
#      about work, not doing it. A quoted run becomes QUOTED_PLACEHOLDER — a TOKEN,
#      not whitespace, because it was a WORD in the shell's own parse and blanking it
#      let the subcommand be eaten as a flag's value. See that constant's note.
#   2. THE VERB MUST BE IN COMMAND POSITION — string start, or after a shell
#      separator (`; & | ( ) { } newline backtick $(`), optionally through one of the
#      keywords that legitimately precede a command (`then`/`else`/`do`/`!`). An
#      unquoted `echo remember to git commit later` is not a commit.
#
# Within `git` itself the match is still anchored on the SUBCOMMAND, allowing
# `-C <path>` / `--git-dir=…` global flags, so `git log --oneline` is not work and
# `git -C $DEVRC commit -m …` is. `gh pr merge` and `gh release create` are here
# because they ship things and their absence was a fail-open under-match.
# 🔴 THESE THREE ARE PATTERN STRINGS, NOT COMPILED OBJECTS, AND THAT IS THE HOT PATH
# AGAIN. Unlike TASK_GET_RX/TASK_API_RX above — which `task_read_ids` consults BEFORE
# the fast-path return and so cannot avoid — every one of these is reachable only from
# `is_work`, i.e. only for a session that has already read a clawgate task. Compiling
# them at import made every tool call in every session pay for three regexes almost
# none of them would use: MEASURED at 14.7 ms/call against 13.9 before, on a path that
# runs thousands of times a day. `re.sub`/`re.search` on a string pattern compile once
# and hit `re`'s own cache thereafter, so a session that DOES work pays exactly what it
# paid when they were module-level `re.compile` calls.
_CMD_START = r"(?:^|[\n;&|(){}`]|\$\()\s*(?:(?:then|else|do|!)\s+)*"
WORK_BASH_PAT = (
    _CMD_START
    + r"git\s+(?:-[A-Za-z]\s+\S+\s+|--[A-Za-z][-\w]*(?:=\S+)?\s+)*(?:commit|push)\b"
    r"|" + _CMD_START
    + r"gh\s+(?:pr\s+(?:create|merge)|release\s+create)\b")

# Single-quoted runs, and double-quoted runs with backslash escapes.
#
# 🔴 REPLACED BY THE PLACEHOLDER TOKEN BELOW, NOT BY A SPACE, AND A SPACE WAS MEASURED
# WRONG IN BOTH DIRECTIONS. A quoted run is a WORD in the shell's own parse — a flag's
# value, a commit message, a path. Blanking it to whitespace destroys that word, and
# WORK_BASH_PAT's global-flag arm (`-[A-Za-z]\s+\S+\s+`) then has no `\S+` to consume,
# so it reads the SUBCOMMAND as the flag's value:
#
#     git -C "$DEVRC" commit -m "msg"   ->  `git -C   commit -m  `  ->  NOT work
#
# `git -C <path>` is the form this repo's own CLAUDE.md MANDATES, so a quoted path is
# routine; the guard silently never armed for it. Measured False for all five shapes
# (`-C "$VAR"`, `-C '/lit'`, `-C "…" push`, `--git-dir="…"`, `-C "$H" -c k=v commit`)
# — the only `git -C` fixtures in the test file were UNQUOTED, which is why nothing
# caught it. A one-character placeholder that is a WORD restores all five.
#
# The token must be non-whitespace, or the weld the space was chosen to prevent comes
# back. Measured over 13 kept positives, 16 negatives and the 5 shapes above:
#     " "     0 lost   0 false-pos   0/5 recovered
#     " '' "  0 lost   0 false-pos   4/5 recovered   + welds `git -m'x'commit` -> True
#     "''"    0 lost   0 false-pos   5/5 recovered   + welds NOTHING
# and `''` additionally CLOSES a false positive the space had: `git 'x'commit` blanked
# to `git  commit` and matched. So the placeholder is strictly better than the space on
# every measured axis, not a trade.
QUOTED_PLACEHOLDER = "''"
QUOTED_PAT = r"'[^']*'|\"(?:[^\"\\]|\\.)*\""
# A `#` at the start of the string or after whitespace, to end of line. Applied AFTER
# the quote strip, so a `#` living inside a quoted string is already gone.
COMMENT_PAT = r"(?:^|(?<=\s))#[^\n]*"

STATE_WORK = "work"

# 🔴 THE DISMISSAL TOMBSTONE. Its own prefix, and that is load-bearing in BOTH
# directions: `tracked_ids` takes only names starting `read-`, and `record_read`'s
# MAX_TASKS census counts only those too — so a tombstone can neither be mistaken for
# a tracked task nor consume a tracking slot. See `dismiss` for the incident.
DISMISSED_PREFIX = "dismissed-"


# --------------------------------------------------------------------------- #
# Session-scoped state
# --------------------------------------------------------------------------- #
def _state_root():
    """HOME read at CALL time, not import time, so a test can point it somewhere safe."""
    return os.path.join(os.path.expanduser("~"), ".cache",
                        "claude-clawgate-writeback", "s")


def _sanitize(part):
    """One path component, made safe to join onto the state root.

    🔴 THE ALLOWED SET INCLUDES `.`, SO IT MUST EXCLUDE THE ALL-DOTS COMPONENTS. `/` is
    already replaced, which leaves exactly `.`, `..`, `...` … as the strings that can
    traverse: `--session ..` resolved to the state ROOT rather than to a session dir.
    Bounded today (only `read-N`/`fires-N`/`unknown-N` are ever unlinked, all
    `%d`-formatted, and `os.remove` refuses a directory) and nothing here is ever
    interpolated into a shell — but "bounded" is a property of today's call sites, not
    of this function. An enumerated fix, not a pattern: a component that is nothing but
    dots is neutered; `.zshrc` or `v1.2.3` are untouched.
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


def _read_path(state_dir, task_id):
    return os.path.join(state_dir, "read-%d" % int(task_id))


def _dismissed_path(state_dir, task_id):
    return os.path.join(state_dir, "%s%d" % (DISMISSED_PREFIX, int(task_id)))


def is_dismissed(state_dir, task_id):
    """Has `--dismiss` been run for THIS task in THIS session?

    EXISTENCE is the entire signal; the file's contents are documentation for a human
    reading the cache by hand. A truncated or empty tombstone therefore still silences
    — the fail-QUIET direction, and the right one here: the operator has already
    asserted out loud that this session's work was not for this card, so a half-written
    file must not resurrect the nagging.

    NO try/except here, deliberately. `os.path.exists` swallows every OSError itself and
    the id is `%d`-formatted, so the only handler that could fire would be one no test
    can reach — the unkillable-branch shape this file has already deleted twice (see
    `record_read` and `post_tool_use`). Fail-open is preserved STRUCTURALLY instead:
    both call sites are already inside one — `post_tool_use` wraps each `record_read` in
    a per-read `except Exception`, and `dismiss_main` runs inside `main()`'s single
    backstop — and a raise there fails toward NOT arming the guard, which is the quiet
    direction.
    """
    return os.path.exists(_dismissed_path(state_dir, task_id))


def write_dismissal_tombstone(state_dir, task_id, now=None):
    """Record that this session dismissed this task. Best-effort; returns whether the
    write itself succeeded — but see `dismiss_main`, which does NOT trust this value and
    re-measures the disk instead.

    `makedirs` matches every other writer here (`record_work`, `bump_fires`): a
    dismissal that arrives before the session dir exists — a pre-emptive one, or one
    that lands after `prune` swept the session — still has to mean something.
    """
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(_dismissed_path(state_dir, task_id), "w") as fh:
            json.dump({"task_id": int(task_id), "dismissed_ts": now_iso(now)}, fh)
        return True
    except Exception:                     # noqa: BLE001
        return False


def _fires_path(state_dir, task_id, kind="fires"):
    """`fires-<id>` for a MEASURED missing write-back, `unknown-<id>` for a
    could-not-measure notice. 🔴 Two counters, deliberately: sharing one let a board
    that was merely unreachable for the first Stops spend the budget a genuinely
    missing write-back needs later, so the case the hook exists for could no longer
    be blocked in that session."""
    return os.path.join(state_dir, "%s-%d" % (kind, int(task_id)))


def now_iso(now=None):
    ts = datetime.datetime.fromtimestamp(
        time.time() if now is None else now, datetime.timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def parse_ts(s):
    """RFC3339 -> epoch seconds, or None.

    The board emits Go's `time.RFC3339Nano` (`2026-08-15T04:27:49.005565Z`), which
    can carry up to nine fractional digits.

    🔴 THE NANOSECOND TRIM THAT USED TO LIVE HERE IS GONE, AND SO IS THE CLAIM THAT
    JUSTIFIED IT. "`datetime.fromisoformat` accepts at most six [fractional digits]"
    was true up to CPython 3.10 and is FALSE on the interpreter this repo pins
    (`python312` in flake.nix; measured on 3.12.13: `.000000000` parses, and
    `.12345678` is truncated to microseconds rather than rejected). The trim was
    therefore dead code whose only visible effect was a mutation-sweep survivor —
    setting its match result to None changed nothing, because the try block below had
    always been doing the whole job. The nine-digit case is still pinned by a test.
    """
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


def tracked_ids(state_dir):
    """Task ids this session has read, with the timestamp of the FIRST read of each."""
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
            tid = int(rec["task_id"])
            ts = rec["first_read_ts"]
        except Exception:
            continue
        if isinstance(ts, str) and ts:
            out[tid] = ts
    return out


def record_read(state_dir, task_id, now=None):
    """Record the FIRST read of `task_id`. Idempotent: a later read never moves the
    timestamp, because the window this hook measures over starts at the first one."""
    path = _read_path(state_dir, task_id)
    # ONE existence check, not two. The second (post-`makedirs`) copy that used to sit
    # below was provably redundant — `makedirs(exist_ok=True)` cannot create or delete
    # THIS file — so neither copy could be killed by a mutation: deleting either left
    # the other answering identically. The cheap one is kept, so a re-read of an
    # already-recorded task still costs no `makedirs`.
    if os.path.exists(path):
        return False
    # 🔴 THE DISMISSAL TOMBSTONE IS CONSULTED HERE, AT THE WRITER, AND NOWHERE ELSE.
    # Measured in production, twice: `--dismiss 200` cleared `read-200`/`fires-200` and
    # wrote nothing, so the very next read of the card re-created `read-200` and the
    # guard blocked again. The dismissals ledger and the block text's own timestamps
    # caught it 90 ms apart, in the SAME tool call — because the natural way to confirm
    # a dismissal worked is to look at the card, which is a read. Three audit rounds
    # missed it: every test drove `--dismiss` and then asserted silence, and none of
    # them read the card again afterwards.
    #
    # One consultation point, deliberately. `tracked_ids` cannot return a dismissed
    # task anyway (its `read-` file is gone and this line stops it coming back), so a
    # second check in `stop_decision` would be a duplicate of a decision already made
    # here — the shape this file has removed twice before, unkillable by any mutation
    # and reading as a coverage gap when it is really a copy. Same reasoning as
    # MAX_TASKS, which is enforced only at this writer.
    #
    # AFTER the `exists` above, so a re-read of an already-tracked task still costs one
    # stat: this branch is reachable only on the FIRST read of a given task in a
    # session, which is the rarest event on a path that is itself off the fast path.
    if is_dismissed(state_dir, task_id):
        return False
    os.makedirs(state_dir, exist_ok=True)
    if len([n for n in os.listdir(state_dir) if n.startswith("read-")]) >= MAX_TASKS:
        return False
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"task_id": int(task_id), "first_read_ts": now_iso(now)}, fh)
    os.replace(tmp, path)
    return True


def record_work(state_dir, now=None):
    """Stamp the LATEST work event. The file used to hold the literal `"1"`; it now
    holds an RFC3339 timestamp, because "was there a comment since the work" needs a
    when and not just a whether — see the anchor note in the module docstring. Every
    work tool call overwrites it, so the value is the MOST RECENT work, which is the
    one a completion write-back has to be newer than."""
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, STATE_WORK), "w") as fh:
        fh.write(now_iso(now))


def work_after_read(state_dir):
    return os.path.exists(os.path.join(state_dir, STATE_WORK))


def last_work_ts(state_dir):
    """The stamp `record_work` wrote, or None. None is the FAIL-QUIET direction: a
    truncated write, or state left by a build that wrote `"1"`, falls back to the read
    anchor rather than inventing a stricter cutoff out of an unreadable file."""
    try:
        with open(os.path.join(state_dir, STATE_WORK)) as fh:
            return fh.read().strip() or None
    except Exception:                     # noqa: BLE001
        return None


def bump_fires(state_dir, task_id, kind="fires"):
    """Read-increment-write the per-task fire counter; returns the NEW 1-based count."""
    path = _fires_path(state_dir, task_id, kind)
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


def dismiss(state_dir, task_id, now=None):
    """Clear ONE task from ONE session's ledger AND tombstone it. Returns the file
    names removed (the tombstone is a write, not a removal, and is not in that list).

    🔴 THIS IS THE ESCAPE HATCH, AND IT HAS TO BE A MECHANISM. The block text used to
    say "if you did NOT do work on this task, say so in one line and stop" — measured
    NOT to work, because saying something changes no state and the next Stop re-blocked
    with identical text. Removing `read-<id>` is what actually ends it: `tracked_ids`
    no longer yields the task, so no live read, no counter, no verdict.

    🔴 REMOVAL ALONE WAS NOT ENOUGH, AND THAT WAS MEASURED IN PRODUCTION TWICE. Clearing
    the entries leaves the session in the state it was in before the card was ever read,
    so the NEXT read re-arms the guard and it blocks again — while the message claimed
    "It will not ask about this task again." The footgun is worse than the bug: the
    natural way to check a dismissal took is to look at the card, which IS a read. The
    ledger and the block text's own timestamps recorded the whole loop:

        22:32:13.912419Z  dismissed 200, removed [fires-200, read-200]
        22:32:14.002017Z  new read of 200 recorded   <- 90 ms later, SAME tool call
        22:46:57.515257Z  dismissed 200 again (identical entry)

    So the removal is now paired with a tombstone that `record_read` consults. The
    tombstone lives INSIDE the session dir on purpose — it is a statement about THIS
    session's work, it inherits `prune`'s existing TTL with no new artifact and no new
    sweep, and a new session correctly starts fresh. That is the opposite placement from
    the `dismissals` ledger, which sits OUTSIDE the swept root because it answers a
    question asked weeks later; the two are not the same kind of record.

    Tombstoning is unconditional, including for a task that was never read: a dismissal
    is the operator asserting this session's work is not for this card, and that is as
    true said before the first read as after it.
    """
    removed = []
    for name in ("read-%d" % int(task_id), "fires-%d" % int(task_id),
                 "unknown-%d" % int(task_id)):
        try:
            os.remove(os.path.join(state_dir, name))
            removed.append(name)
        except Exception:                 # noqa: BLE001 — absent is the common case
            pass
    # Best-effort and last: a tombstone that cannot be written NEVER changes what the
    # removals above already did. What it does change is what the caller is allowed to
    # PROMISE — see `dismiss_report`.
    write_dismissal_tombstone(state_dir, task_id, now=now)
    return removed


def _dismissals_path():
    """The audit log, deliberately OUTSIDE the per-session root that `prune` sweeps.

    `_state_root()` is `…/claude-clawgate-writeback/s`; this is its sibling, so a
    session dir ageing out cannot take the record of its dismissals with it.
    """
    return os.path.join(os.path.dirname(_state_root()), "dismissals")


def record_dismissal(task_id, session_id, removed, now=None):
    """Append ONE line per `--dismiss` invocation. Returns True if it was written.

    🔴 `--dismiss` IS A REAL BYPASS OF A DETERMINISTIC GUARD, and until this existed it
    left no trace anywhere — so "is it being used honestly, or is it the new way to make
    the hook shut up?" was unanswerable. In a hook whose entire premise is that PROSE
    LOST 2/2, gating the bypass on prose alone ("do NOT run this if you did work") and
    then not measuring it is the same mistake one level up. One JSON line: when, which
    task, which session, and what was actually cleared — a no-op dismissal records too,
    so repeat attempts are visible rather than silently identical to a first one.

    Fail-open and best-effort: an unwritable log NEVER changes what `--dismiss` does.
    The dismissal is the user-visible act; the record is bookkeeping.
    """
    try:
        path = _dismissals_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps({"ts": now_iso(now), "task_id": int(task_id),
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

    "notice" is the rung that RELENTS. It used to be named "context" and emitted
    `hookSpecificOutput.additionalContext`, which the CLI feeds into the same
    `blockingErrors` array as `decision:"block"` — so it forced a third continuation
    instead of letting the turn end. It now emits `systemMessage`. See the module
    docstring for the bundle reads, both controls, and why that channel is different.
    """
    if fire_number <= MAX_BLOCKS:
        return "block"
    if fire_number <= MAX_FIRES:
        return "notice"
    return "silent"


# --------------------------------------------------------------------------- #
# Trigger matching
# --------------------------------------------------------------------------- #
def task_read_ids(data):
    """Every clawgate task id this PostToolUse payload is a READ of. Order-preserving,
    de-duplicated, and empty for anything that is not a single-task read."""
    cmd = ((data or {}).get("tool_input") or {}).get("command")
    if not isinstance(cmd, str) or not cmd:
        return []
    out = []
    for rx in (TASK_GET_RX, TASK_API_RX):
        for m in rx.finditer(cmd):
            tid = int(m.group(1))
            if tid not in out:
                out.append(tid)
    return out


def _strip_literals(cmd):
    """Replace quoted runs with QUOTED_PLACEHOLDER and blank trailing `#` comments, so
    a command that MENTIONS a work verb is not mistaken for one that RUNS it.

    🔴 The quoted run becomes a TOKEN, not whitespace — a quoted flag value is a word
    and deleting the word let the subcommand be eaten as the flag's value. See
    QUOTED_PLACEHOLDER's note for the five measured shapes and the three-way sweep.
    This function's exact output is deliberately NOT pinned by any test: the observable
    is `is_work`, and an assertion on the internal string is a spelling of it that
    breaks on any change to the placeholder while proving nothing extra.
    """
    return re.sub(COMMENT_PAT, " ",
                  re.sub(QUOTED_PAT, QUOTED_PLACEHOLDER, cmd))


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


# --------------------------------------------------------------------------- #
# The live read — the measurement, not an inference
# --------------------------------------------------------------------------- #
class LiveReadError(Exception):
    """Could not measure. NEVER silence: the caller emits a non-blocking notice."""


def _env_file(path=CLAWGATE_ENV):
    conf = {}
    try:
        with open(os.path.expanduser(path)) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    except Exception:
        return {}
    return conf


def _scrub(s, limit=120):
    """Third-party stderr, made safe to splice into text an operator will read.

    🔴 `proc.stderr` is an UNFILTERED PIPE OUT OF A BINARY THIS HOOK DOES NOT OWN, and
    it used to go straight into the reason string. Control bytes (a `\\r`, an ANSI
    escape, a stray newline) let that binary's output impersonate structure in the
    transcript. Collapse to single-space-separated printables, then truncate.
    """
    return " ".join((s or "").split())[:limit]


def _via_clawgatectl(task_id, timeout):
    proc = _sp().run(["clawgatectl", "task", "get", str(int(task_id))],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise LiveReadError("clawgatectl rc=%d %s"
                            % (proc.returncode, _scrub(proc.stderr)))
    return json.loads(proc.stdout)


def _via_curl(task_id, timeout, env_path=CLAWGATE_ENV, why=None):
    """The fallback for a host with no `clawgatectl` on PATH.

    That used to say "the laptop today — its homelab-talos checkout predates the
    command, so nix does not build it", and that is now STALE: `clawgatectl` was
    verified present on BOTH hosts. The fallback stays anyway — it costs nothing on a
    host that has the binary (it is only reached once `clawgatectl` has failed or is
    absent), and "the client is missing" is not the only way to arrive here: a
    `clawgatectl` that exists and exits non-zero lands here too.

    🔴 The token goes in on STDIN via `curl -K -`, never in argv: an argv is visible
    to every process on the box through /proc, and this runs after every turn.

    `why` carries the FIRST client's failure into this one's message. Without it a
    `clawgatectl` that exists but exits non-zero reports as "no clawgatectl" — a
    diagnosis pointing at the wrong subsystem, which is the shape that has cost this
    repo whole sessions.
    """
    conf = _env_file(env_path)
    url = conf.get("CLAWGATE_API_URL")
    token = conf.get("CLAWGATE_HOOK_TOKEN")
    if not url or not token:
        raise LiveReadError("%s has no API url/token (first client: %s)"
                            % (os.path.expanduser(env_path),
                               why or "clawgatectl not on PATH"))
    cfg = "".join([
        "silent\n", "fail\n",
        "max-time = %d\n" % max(1, int(timeout)),
        'url = "%s/api/tasks/%d"\n' % (url.rstrip("/"), int(task_id)),
        'header = "Authorization: Bearer %s"\n' % token,
    ])
    # 🔴 `timeout + CURL_KILL_MARGIN_SECS`, not `timeout`: curl's own `max-time` above
    # is the deadline we WANT it to honour, and the wait has to outlive it or the
    # SIGKILL races the clean exit and we lose curl's rc. Deleting the margin survived
    # a mutation sweep until a test pinned the two numbers apart.
    proc = _sp().run(["curl", "-K", "-"], input=cfg, capture_output=True, text=True,
                     timeout=timeout + CURL_KILL_MARGIN_SECS)
    if proc.returncode != 0:
        raise LiveReadError("curl rc=%d" % proc.returncode)
    return json.loads(proc.stdout)


def live_task(task_id, timeout=PER_TASK_TIMEOUT_SECS, env_path=CLAWGATE_ENV):
    """Live re-read of one task. Raises LiveReadError when it cannot be measured."""
    # Bound BEFORE the try: the `except subprocess.TimeoutExpired` clauses below name
    # the module attribute, and an except clause is evaluated even when the exception
    # came from the import itself — at which point a still-None `subprocess` would
    # raise AttributeError out of the handler and defeat the fail-open contract.
    _sp()
    why = None
    try:
        return _via_clawgatectl(task_id, timeout)
    except LiveReadError as e:
        why = str(e)
    except FileNotFoundError:
        why = "clawgatectl not on PATH"   # no clawgatectl on this host
    except subprocess.TimeoutExpired:
        raise LiveReadError("clawgatectl timed out after %ss" % timeout)
    except Exception as e:                # noqa: BLE001 — unparseable stdout, etc.
        raise LiveReadError("%s: %s" % (type(e).__name__, e))
    try:
        return _via_curl(task_id, timeout, env_path=env_path, why=why)
    except LiveReadError:
        raise
    except FileNotFoundError:
        raise LiveReadError("neither clawgatectl nor curl is available")
    except subprocess.TimeoutExpired:
        raise LiveReadError("curl timed out after %ss" % timeout)
    except Exception as e:                # noqa: BLE001
        raise LiveReadError("%s: %s" % (type(e).__name__, e))


def writeback_state(task, first_read_ts, skew=CLOCK_SKEW_ALLOWANCE_SECS,
                    work_ts=None):
    """"closed" | "written" | "missing" | "unknown" for one live task payload.

    🔴 THE CUTOFF IS THE LATER OF THE FIRST READ AND THE LAST WORK EVENT. Anchoring on
    the read alone is what let the skill's own pre-start "Starting" comment — posted
    right after the read and BEFORE the work — satisfy this check at pickup, so the
    guard could never observe a missing COMPLETION write-back on any session that
    followed the ritual. `work_ts` is optional and a None (or unparseable) value falls
    back to the read anchor, which is the quieter of the two.

    🔴 An UNPARSEABLE `createdAt` on a `claude-code` comment resolves to "written",
    not to "missing". The comment demonstrably exists and only its timestamp is
    unreadable; resolving that toward a BLOCK would spend the operator's turn on a
    formatting change at the far end of a wire this hook does not own.
    """
    if not isinstance(task, dict):
        return "unknown"
    if task.get("status") in CLOSED_STATUSES:
        return "closed"
    read_at = parse_ts(first_read_ts)
    if read_at is None:
        return "unknown"
    anchor = read_at
    worked_at = parse_ts(work_ts)
    if worked_at is not None and worked_at > anchor:
        anchor = worked_at
    cutoff = anchor - skew
    comments = task.get("comments")
    if comments is None:
        comments = []
    if not isinstance(comments, list):
        return "unknown"
    for c in comments:
        if not isinstance(c, dict) or c.get("author") != AGENT_AUTHOR:
            continue
        if c.get("retracted"):
            continue                      # withdrawn: it is not a write-back
        ts = parse_ts(c.get("createdAt"))
        if ts is None or ts >= cutoff:
            return "written"
    return "missing"


# --------------------------------------------------------------------------- #
# The text the operator's model actually reads
# --------------------------------------------------------------------------- #
def dismiss_cmd(task_id, session_id):
    """The ONE command that deterministically clears a task from this session.

    🔴 The session id is INTERPOLATED rather than left as a placeholder. A model
    cannot see its own hook payload, so a command it has to fill in a session id for
    is a command it cannot run — which is how the previous escape ("say so and stop")
    ended up being no escape at all.
    """
    return ("python3 ~/.claude/hooks/clawgate-writeback-guard.py --dismiss %d "
            "--session %s" % (int(task_id), _sanitize(session_id)))


def missing_text(task_id, first_read_ts, session_id=""):
    return (
        "clawgate write-back MISSING for task %(id)d.\n"
        "This session read task %(id)d at %(ts)s and has done real work (an edit, a "
        "commit, a push or a PR) since, but a LIVE read of the board just now shows no "
        "comment authored by `claude-code` newer than that work. Two tasks (#193, "
        "#194) already shipped this way: the card stayed `open` with zero comments and "
        "was re-dispatched and paid for twice.\n"
        "Write it back before this turn ends:\n"
        "  clawgatectl task comment %(id)d --body \"<what shipped, evidence per "
        "acceptance criterion, and an explicit NOT-verified list>\"\n"
        "  clawgatectl task status %(id)d ready_for_review\n"
        "Use `complete` instead of `ready_for_review` ONLY when the task body carried "
        "a `## Acceptance criteria` heading AND every criterion is validated — see the "
        "clawgate skill's status gate.\n"
        "🔴 IF THIS SESSION'S WORK WAS NOT FOR TASK %(id)d — you only read the card, or "
        "the work belongs to a different task or repo — do NOT comment and do NOT flip "
        "the status: a junk comment permanently silences this guard for the card, and "
        "`ready_for_review` fires a push notification to Zach. Run this instead — it "
        "will not ask about task %(id)d again in THIS session, even if you read the "
        "card again, and a new session starts fresh:\n"
        "  %(dismiss)s"
        % {"id": int(task_id), "ts": first_read_ts,
           "dismiss": dismiss_cmd(task_id, session_id)}
    )


def unknown_text(task_id, first_read_ts, error, session_id=""):
    # 🔴 THE SENTENCE MUST BE TRUE IN BOTH CONTEXTS IT IS READ IN. This text is emitted
    # on its own as a `systemMessage` (the turn does end) AND spliced into a
    # `decision:"block"` reason whenever some OTHER task is blocking — `stop_decision`
    # joins `blocks + notices` into one string. The previous wording, "and this turn is
    # ending normally", was MEASURED verbatim inside a block reason, i.e. the model was
    # told the turn was ending while it was being forcibly continued. The claim this
    # notice can actually make is about ITSELF, not about the turn.
    return (
        "clawgate write-back UNVERIFIED for task %(id)d: the board could not be "
        "reached to check whether a `claude-code` comment was written since %(ts)s "
        "(%(err)s). This is a NOTICE, not a block — nothing is being asserted about "
        "the card, because nothing could be measured, and this notice on its own "
        "does not hold the turn open.\n"
        "If this session did work on task %(id)d, write it back:\n"
        "  clawgatectl task comment %(id)d --body \"…\"\n"
        "  clawgatectl task status %(id)d ready_for_review\n"
        "If it did not, silence it for this session with:\n"
        "  %(dismiss)s"
        % {"id": int(task_id), "ts": first_read_ts, "err": _scrub(str(error), 160),
           "dismiss": dismiss_cmd(task_id, session_id)}
    )


# --------------------------------------------------------------------------- #
# PostToolUse
# --------------------------------------------------------------------------- #
def post_tool_use(data, now=None):
    """Record reads and work. Returns a small dict describing what it did, for tests.

    🔴 THE ORDERING IS THE POINT ON THIS PATH. `os.path.exists` on the session's state
    dir and the trigger regex come FIRST, and a session that is neither tracked nor
    reading a task returns before touching the filesystem again. Nothing below the
    fast-path return runs for the overwhelming majority of tool calls.
    """
    # 🔴 THE SUBAGENT RULE IS ASYMMETRIC, AND BOTH HALVES ARE THERE BECAUSE THE
    # SYMMETRIC VERSIONS WERE EACH MEASURED WRONG. A subagent's tool call arrives
    # wearing the PARENT's `session_id` (see the `Kf()` quote in the module docstring),
    # so `agent_id` is the only field separating them — but the two directions are not
    # the same question:
    #
    #   * a subagent's READ MUST NOT ARM the parent. Accepting it armed the parent off a
    #     subagent that merely happened to read a card, and the parent's Stop then
    #     blocked on a task the parent never touched. MEASURED as a false positive.
    #   * a subagent's WORK IS THE SESSION'S WORK. The parent dispatched it; the parent
    #     owns the write-back. REFUSING it is what the previous round got wrong, and it
    #     deleted the yield on the exact incident this hook exists for: in BOTH measured
    #     failures (#193/#194) the work ran in dispatched LOCAL SUBAGENTS — the canonical
    #     handoff records "'dispatch both' meant local subagents, not a devpod". With the
    #     refusal in place, `parent reads 193 -> subagent edits/commits/pushes/opens the
    #     PR -> parent Stop` measured SILENT against a card with zero comments, while the
    #     same Edit WITHOUT `agent_id` measured `block`.
    #
    # So: `agent_id` suppresses only the READ half. The work half still requires the
    # session to be ALREADY TRACKED (`tracked` below), i.e. the parent read a card on
    # its own thread — a subagent cannot both arm and satisfy the trigger by itself.
    agent = bool((data or {}).get("agent_id"))
    state_dir = _state_dir(data)
    if state_dir is None:
        return {"fast_path": True, "recorded": [], "work": False}
    tracked = os.path.exists(state_dir)
    # A subagent's payload never contributes ids, so the trigger regex is skipped
    # entirely for it — the fast path stays one `exists` and no `re` work.
    ids = [] if agent else task_read_ids(data)
    if not tracked and not ids:
        # 🔴 THE FAST-PATH RETURN. Exactly ONE filesystem call (the `exists` above)
        # has happened and nothing has been spawned. Everything below this line —
        # the work regex, every write, every stat of a state file — is reachable
        # ONLY for a session that has actually read a clawgate task. Pinned by
        # test_the_fast_path_does_exactly_one_stat_and_nothing_else, which counts
        # the calls rather than trusting this comment.
        return {"fast_path": True, "recorded": [], "work": False}

    work = is_work(data)
    recorded = []
    for tid in ids:
        try:
            if record_read(state_dir, tid, now=now):
                recorded.append(tid)
        except Exception:                 # noqa: BLE001 — fail-open, per read
            pass
    # Work only counts AFTER a read, and the fast-path return above is ALREADY that
    # gate: reaching this line means `tracked or ids` was true, so a session that has
    # never touched the board cannot get here at all. The `and (tracked or recorded)`
    # that used to be spelled out here was a second copy of a decision already made —
    # unkillable by any mutation, and therefore a coverage gap that was really a
    # duplicate. The condition it encoded is pinned by
    # test_work_is_only_recorded_after_a_read, which drives the real writer.
    marked = False
    if work:
        try:
            record_work(state_dir, now=now)
            marked = True
        except Exception:                 # noqa: BLE001
            pass
    return {"fast_path": False, "recorded": recorded, "work": marked}


# --------------------------------------------------------------------------- #
# Stop
# --------------------------------------------------------------------------- #
def stop_decision(data, reader=None, budget=STOP_BUDGET_SECS,
                  clock=time.monotonic):
    """Pure-ish decision for a Stop payload -> (kind, text) with kind in
    {"silent", "notice", "block"}. Side effect: it bumps the per-task fire counters,
    which is what the ladder is made of.

    🔴 `reader` defaults to None and is resolved to `live_task` HERE, not in the
    signature. A default argument binds at DEF time, so `reader=live_task` would make
    the module attribute unrebindable — which is exactly the trap that produced a
    confident "TAIL_CHARS is inert" reading of an eleven-point sweep in
    next-step-nudge.py that had in fact re-measured one value eleven times.
    """
    if reader is None:
        reader = live_task
    d = data if isinstance(data, dict) else {}
    if d.get("hook_event_name") not in (None, "Stop"):
        return ("silent", "")             # 🔴 SubagentStop and friends: refused
    if d.get("agent_id"):
        return ("silent", "")
    state_dir = _state_dir(d)
    if state_dir is None or not os.path.exists(state_dir):
        return ("silent", "")
    if not work_after_read(state_dir):
        # 🔴 THE FALSE-POSITIVE KILLER. Read-and-evaluate-only is the SKILL's own
        # step 2 and owes the board nothing.
        return ("silent", "")

    ids = tracked_ids(state_dir)
    if not ids:
        return ("silent", "")

    session_id = d.get("session_id") or ""
    worked_at = last_work_ts(state_dir)
    # 🔴 Parsed ONCE, outside the loop, and compared against each task's OWN read. Both
    # sides come from THIS host's clock (`record_read` and `record_work` both call
    # `now_iso`), so no skew allowance belongs here — unlike `writeback_state`, which
    # compares a local stamp against the SERVER's `createdAt`.
    worked_at_epoch = parse_ts(worked_at)
    deadline = clock() + budget
    blocks, notices = [], []
    # No `[:MAX_TASKS]` slice here: `record_read` refuses to create a sixth `read-`
    # file, so `tracked_ids` structurally cannot return more than MAX_TASKS and the
    # slice was a second copy of a cap already enforced at the writer — unkillable,
    # and pinned instead by test_at_most_five_task_ids_are_tracked_per_session.
    for tid in sorted(ids):
        first_read_ts = ids[tid]
        # 🔴 THE PER-TASK READ ANCHOR. `work_after_read` above is a SESSION-level fact;
        # this is the per-task one. A task read AFTER the session's last work event is
        # owed nothing yet — the measured false positive was `read N, work, write N
        # back, then merely read M` blocking on M. Skipped BEFORE the budget check and
        # the live read, so it costs no subprocess either.
        #
        # `>=`, not `>`: one Bash call can be both a read and work (`clawgatectl task
        # get 194 && git commit -m x`), and `post_tool_use` stamps both from the SAME
        # `now`, so an equal pair is work ON that task, not work before it.
        #
        # Either side unparseable -> DO NOT skip. A truncated `work` stamp must not
        # silently disable the guard; `work_after_read` already proved work happened.
        read_at_epoch = parse_ts(first_read_ts)
        if (worked_at_epoch is not None and read_at_epoch is not None
                and worked_at_epoch < read_at_epoch):
            continue
        remaining = deadline - clock()
        if remaining <= 0:
            break
        err = "the board returned a task payload this hook could not read"
        try:
            task = reader(tid, timeout=min(PER_TASK_TIMEOUT_SECS, remaining))
            state = writeback_state(task, first_read_ts, work_ts=worked_at)
        except LiveReadError as e:
            state, err = "unknown", e
        except Exception as e:            # noqa: BLE001 — a reader that raises anything
            state, err = "unknown", e
        if state in ("closed", "written"):
            continue
        if state == "unknown":
            # 🔴 NEVER blocks, and spends its OWN counter. Cannot-measure is reported,
            # never enforced — and never at the expense of the block budget that a
            # measured miss will need if the board comes back later in this session.
            if escalate(bump_fires(state_dir, tid, "unknown")) != "silent":
                notices.append(unknown_text(tid, first_read_ts, err, session_id))
            continue
        rung = escalate(bump_fires(state_dir, tid))
        if rung == "silent":
            continue
        (blocks if rung == "block" else notices).append(
            missing_text(tid, first_read_ts, session_id))

    # 🔴 A "could not measure" notice NEVER CAUSES a block — only `blocks` does, and
    # only a MEASURED missing write-back can put an entry there. When some other task
    # is blocking anyway the notice rides along in the same reason rather than being
    # dropped, but a Stop whose every task is unmeasurable can only ever reach
    # `notice` — which does not force a continuation at all. Pinned by a test that
    # drives an all-unknown session up the ladder and reads the EMITTED JSON.
    if blocks:
        return ("block", "\n\n".join(blocks + notices))
    if notices:
        return ("notice", "\n\n".join(notices))
    return ("silent", "")


def emit(kind, text):
    """The ONE writer. `silent` is handled HERE rather than at the call site, so there
    is exactly one place that decides what reaches stdout — a caller-side `if kind !=
    "silent"` in front of this was redundant with the fall-through below, and a branch
    no mutation can kill reads as a coverage gap when it is really just a duplicate.

    🔴 THERE ARE EXACTLY TWO CHANNELS AND ONLY ONE OF THEM ENDS THE TURN. `decision:
    "block"` forces a continuation. `systemMessage` does not — it is yielded on the
    CLI's message channel, never into `blockingErrors`, and its attachment renders to
    the operator without being fed back to the model. `hookSpecificOutput.
    additionalContext` is NOT a third option: on Stop the CLI pushes it into the same
    `blockingErrors` array as a block, so emitting it here would force a continuation
    while claiming not to. It is deliberately absent from this function.
    """
    if kind == "block":
        json.dump({"decision": "block", "reason": text}, sys.stdout)
        sys.stdout.write("\n")
    elif kind == "notice":
        json.dump({"systemMessage": text}, sys.stdout)
        sys.stdout.write("\n")


def dismiss_report(task_id, session_id, removed, tombstoned):
    """The ONE sentence `--dismiss` prints. A pure function of what actually happened,
    so the claim can be pinned as a whole string by a test rather than sampled for
    keywords — this repo has had four prose guards walked by rewording, and a
    two-word check on this text would be satisfied by its own static prefix.

    🔴 THE PROMISE IS SCOPED TO THE SESSION, AND THAT IS THE FIX, NOT A HEDGE. The old
    text said "It will not ask about this task again", which was false in two separate
    ways: the next read re-armed the guard (the bug), and even with the tombstone it
    says nothing about the NEXT session, which correctly starts fresh. Both are stated.

    🔴 The state dir is NOT printed. It is an absolute path containing $HOME, and
    everything this writes goes to stdout, which the model reads and may quote onward.
    The session id is the only part the caller needs to see.
    """
    sess = _sanitize(session_id)
    tid = int(task_id)
    if removed:
        head = ("clawgate write-back guard: dismissed task %d for session %s "
                "(cleared %s)." % (tid, sess, ", ".join(sorted(removed))))
    else:
        head = ("clawgate write-back guard: nothing to dismiss — task %d is not in "
                "session %s's ledger." % (tid, sess))
    if tombstoned:
        tail = ("It will not ask about task %d again in session %s, even if the card "
                "is read again — a NEW session starts fresh." % (tid, sess))
    else:
        tail = ("WARNING: the tombstone could NOT be written, so a later read of task "
                "%d in session %s will arm this guard again." % (tid, sess))
    return head + " " + tail


# --------------------------------------------------------------------------- #
def dismiss_main(argv):
    """`--dismiss <task_id> --session <session_id>` — the escape hatch, as a command.

    Prints one line saying what it did and returns. NEVER reads stdin: it is invoked
    by a model from a Bash tool call, where stdin is not a hook payload.

    🔴 `--session` is REQUIRED and is not defaulted to anything. This process has no
    way to learn the caller's session id — it is not in the environment — and guessing
    would let a dismissal land on some other session's ledger. The block text supplies
    the id already filled in, so there is nothing for the caller to look up.
    """
    tid = sess = None
    i = 0
    while i < len(argv):
        if argv[i] == "--dismiss" and i + 1 < len(argv):
            tid, i = argv[i + 1], i + 2
        elif argv[i] == "--session" and i + 1 < len(argv):
            sess, i = argv[i + 1], i + 2
        else:
            i += 1
    if tid is None or sess is None:
        sys.stdout.write("usage: clawgate-writeback-guard.py --dismiss <task_id> "
                         "--session <session_id>\n")
        return
    try:
        task_id = int(tid)
    except (TypeError, ValueError):
        sys.stdout.write("not a task id: %s\n" % _sanitize(tid))
        return
    state_dir = _state_dir({"session_id": sess})
    if state_dir is None:
        sys.stdout.write("not a session id: %s\n" % _sanitize(sess))
        return
    removed = dismiss(state_dir, task_id)
    record_dismissal(task_id, sess, removed)
    # 🔴 THE PROMISE IS RE-MEASURED OFF DISK, NOT TAKEN FROM THE WRITER'S RETURN VALUE.
    # Two reasons, and the second is the one a `tombstoned = write_...()` would get
    # wrong: a write that failed while an EARLIER dismissal's tombstone is still present
    # leaves the promise TRUE, and only asking the filesystem sees that. The first is
    # plainer — this sentence is a claim about the state of the world, so it should be
    # read out of the world.
    sys.stdout.write(dismiss_report(task_id, sess, removed,
                                    is_dismissed(state_dir, task_id)) + "\n")


def main():
    # 🔴 ONE exit, and it is always 0. Nothing inside the try may call sys.exit():
    # SystemExit is a BaseException and would sail past `except Exception`.
    try:
        # 🔴 THE CLI MODE IS DECIDED BEFORE THE STDIN READ, and it never performs one.
        # A hook invocation carries no argv, so this cannot shadow the hook path — and
        # reading stdin in CLI mode would hang on a terminal forever.
        if "--dismiss" in sys.argv[1:]:
            dismiss_main(sys.argv[1:])
            data, event = None, None
        else:
            data = json.load(sys.stdin)
            event = (data or {}).get("hook_event_name")
        if event == "PostToolUse":
            post_tool_use(data)
        elif event == "Stop":
            emit(*stop_decision(data))
            # AFTER the decision has been emitted: the operator's turn never waits on
            # housekeeping, and a prune that raises cannot suppress a verdict that has
            # already been written.
            prune()
        # every other event, SubagentStop included, is not ours
    except Exception:                     # noqa: BLE001 — see the fail-open note above
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
