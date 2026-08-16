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
     Bash `git commit` / `git push` / `gh pr create`, and
  3. a LIVE re-read of the board at Stop shows NO comment authored by `claude-code`
     with `createdAt` at or after the first read.

Condition 2 is the false-positive killer and it is not optional. The SKILL's own
step 2 is "EVALUATE and report to Zach. Do NOT flip status yet" — a session that
reads a card, forms an opinion and reports back owes the board NOTHING, and a hook
that blocks that turn is worse than no hook. Nothing here fires on a read alone.

Condition 3 is a LIVE MEASUREMENT, not an inference from what this process saw. It
is what makes the hook self-suppressing: the moment the ritual is followed the
board says so and the guard goes quiet, including for a comment written by a
different process, a subagent, or a devpod agent. A hook that tracked only its own
observations would keep firing after the work was correctly written back.

🔴 A LIVE READ THAT FAILS IS NOT A CLEAN BILL OF HEALTH. Unreachable board, no
client, unparseable JSON — all of those mean "could not measure", and this hook says
so out loud with a NON-BLOCKING notice rather than going silent. RULES.md: an empty
result cannot distinguish two mechanisms, and reporting silence for "the board is
down" is reporting the same observable as "the ritual was followed".

ESCALATION LADDER — per session, per task id
---------------------------------------------
    fire 1  ->  decision: block      (the turn does not end; `reason` reaches the model)
    fire 2  ->  decision: block
    fire 3  ->  additionalContext    (non-blocking; the turn ends)
    fire 4+ ->  silent

Derived from the INSTALLED CLI bundle, not from documentation (claude-code 2.1.220,
`bin/.claude-wrapped` — NOT the 20 KB `bin/claude` wrapper, a grep against which
returns a meaningless zero). Both controls were run against the 275 MB bundle before
any of this was believed: positive `hookEventName` -> 68 matches, negative
`zzzNOTPRESENTzzz_devrc_control` -> 0.

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
    be overridden by the harness has already lost the operator.

🔴 THERE IS DELIBERATELY NO `stop_hook_active` GATE, and that is the one place this
hook diverges from the CLI's own advice ("check stop_hook_active and return success
while it's true"). That advice exists to stop an unbounded block loop; the ladder
above bounds it at 2 per task instead, and the SECOND block is the whole point — it
is what catches a turn that acknowledged the first block and still stopped without
writing. Skipping the second Stop would make fire 2 unreachable in the only shape it
matters. The interaction with MAX_TASKS is named rather than hidden: several tasks
each blocking twice can in principle stack toward the CLI's 8, at which point the
CLI ends the turn with a warning — a graceful ceiling, not a wedge.

🔴 SubagentStop IS REFUSED. A subagent's turn never reaches the operator, so it owes
them nothing (next-step-nudge.py refuses it for the same reason). PostToolUse is NOT
refused for subagents: in both measured failures the work ran in a dispatched
subagent, and its edits and commits are exactly the evidence condition 2 needs.

🔴 HOT PATH. PostToolUse fires after EVERY tool call of every session, and
agent-ledger-hook.py already costs ~21 ms there. The fast path is: resolve the state
dir from `session_id` (string work, no IO), ONE `os.path.exists`, and the trigger
regex. A session that has never read a clawgate task and is not reading one now does
nothing else — no directory creation, no state read, no subprocess, and it does not
even IMPORT `subprocess` or `shutil` (see the deferred-import note below). That
ordering is pinned by tests that COUNT the calls and read the module list out of
`-X importtime`, because an earlier hook in this repo shipped with its throttle
consulted AFTER the subprocess spawn while its comment claimed otherwise. Measured:
13.9 ms/call against 8.6 ms for a bare interpreter start.

🔴 FAIL-OPEN, ALWAYS. Every internal exception exits 0 with an empty stdout and
blocks nothing. main() has exactly ONE exit and it is always 0. A hook that wedges a
turn to enforce a bookkeeping ritual has inverted its own cost model.

WHAT THIS STRUCTURALLY CANNOT SEE (say it here, not in a report nobody re-reads):
  * work done anywhere this hook is not running — a devpod agent, the clawgate web
    UI, a human, opencode, or a host that has not had `home-manager switch` run;
  * a session that read the task via a route neither trigger matches (the board UI,
    an `/api/tasks?summary=1` list, someone pasting the body in);
  * whether the comment that DOES exist is any good — it checks that a `claude-code`
    comment exists since the read, never what it says;
  * the `in_progress` flip and the pre-start comment, neither of which it requires:
    it gates the WRITE-BACK, which is the thing that was measured missing.

Deployed by `nix/home.nix`; registered on PostToolUse (no matcher) + Stop by
`register-nudge-hook.py`.
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
STOP_BUDGET_SECS = 8.0
PER_TASK_TIMEOUT_SECS = 5.0

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
# trailing segment shapes that exist (`/api/tasks/193/comments`, `/193`).
TASK_API_RX = re.compile(r"/api/tasks/(\d+)\b")

# Tool calls that ARE work, by name.
WORK_TOOLS = ("Edit", "Write", "NotebookEdit")

# ...and by Bash command. Anchored on the SUBCOMMAND immediately after `git` (with
# an allowance for `-C <path>` / `--git-dir=…` style global flags), so
# `git log --oneline | grep commit` is not work and `git -C $DEVRC commit -m …` is.
WORK_BASH_RX = re.compile(
    r"\bgit\s+(?:-[A-Za-z]\s+\S+\s+|--[A-Za-z][-\w]*(?:=\S+)?\s+)*(?:commit|push)\b"
    r"|\bgh\s+pr\s+create\b")

STATE_WORK = "work"


# --------------------------------------------------------------------------- #
# Session-scoped state
# --------------------------------------------------------------------------- #
def _state_root():
    """HOME read at CALL time, not import time, so a test can point it somewhere safe."""
    return os.path.join(os.path.expanduser("~"), ".cache",
                        "claude-clawgate-writeback", "s")


def _sanitize(part):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:120]


def _state_dir(data):
    session = (data or {}).get("session_id")
    if not isinstance(session, str) or not session:
        return None
    return os.path.join(_state_root(), _sanitize(session))


def _read_path(state_dir, task_id):
    return os.path.join(state_dir, "read-%d" % int(task_id))


def _fires_path(state_dir, task_id):
    return os.path.join(state_dir, "fires-%d" % int(task_id))


def now_iso(now=None):
    ts = datetime.datetime.fromtimestamp(
        time.time() if now is None else now, datetime.timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def parse_ts(s):
    """RFC3339 -> epoch seconds, or None.

    The board emits Go's `time.RFC3339Nano` (`2026-08-15T04:27:49.005565Z`), which
    can carry up to nine fractional digits; `datetime.fromisoformat` accepts at most
    six, so the tail is trimmed rather than allowed to fail the whole comparison.
    """
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    m = re.match(r"^(.*\.\d{1,6})\d*([+-]\d{2}:?\d{2})?$", t)
    if m:
        t = m.group(1) + (m.group(2) or "")
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
    if os.path.exists(path):
        return False
    os.makedirs(state_dir, exist_ok=True)
    if os.path.exists(path):
        return False
    if len([n for n in os.listdir(state_dir) if n.startswith("read-")]) >= MAX_TASKS:
        return False
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"task_id": int(task_id), "first_read_ts": now_iso(now)}, fh)
    os.replace(tmp, path)
    return True


def record_work(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, STATE_WORK), "w") as fh:
        fh.write("1")


def work_after_read(state_dir):
    return os.path.exists(os.path.join(state_dir, STATE_WORK))


def bump_fires(state_dir, task_id):
    """Read-increment-write the per-task fire counter; returns the NEW 1-based count."""
    path = _fires_path(state_dir, task_id)
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
    """1-based fire number -> "block" | "context" | "silent"."""
    if fire_number <= MAX_BLOCKS:
        return "block"
    if fire_number <= MAX_FIRES:
        return "context"
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


def is_work(data):
    """True when this PostToolUse payload is REAL WORK, not a read or a look-around."""
    d = data or {}
    if d.get("tool_name") in WORK_TOOLS:
        return True
    if d.get("tool_name") != "Bash":
        return False
    cmd = (d.get("tool_input") or {}).get("command")
    return isinstance(cmd, str) and bool(WORK_BASH_RX.search(cmd))


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


def _via_clawgatectl(task_id, timeout):
    proc = _sp().run(["clawgatectl", "task", "get", str(int(task_id))],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise LiveReadError("clawgatectl rc=%d %s"
                            % (proc.returncode, (proc.stderr or "").strip()[:120]))
    return json.loads(proc.stdout)


def _via_curl(task_id, timeout, env_path=CLAWGATE_ENV, why=None):
    """The fallback for a host with no `clawgatectl` (the laptop today — its
    homelab-talos checkout predates the command, so nix does not build it).

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
    proc = _sp().run(["curl", "-K", "-"], input=cfg,
                          capture_output=True, text=True, timeout=timeout + 2)
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


def writeback_state(task, first_read_ts, skew=CLOCK_SKEW_ALLOWANCE_SECS):
    """"closed" | "written" | "missing" | "unknown" for one live task payload.

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
    cutoff = read_at - skew
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
def missing_text(task_id, first_read_ts):
    return (
        "clawgate write-back MISSING for task %(id)d.\n"
        "This session read task %(id)d at %(ts)s and then did real work (an edit, a "
        "commit, a push or a PR), but a LIVE read of the board just now shows no "
        "comment authored by `claude-code` since that read. Two tasks (#193, #194) "
        "already shipped this way: the card stayed `open` with zero comments and was "
        "re-dispatched and paid for twice.\n"
        "Write it back before this turn ends:\n"
        "  clawgatectl task comment %(id)d --body \"<what shipped, evidence per "
        "acceptance criterion, and an explicit NOT-verified list>\"\n"
        "  clawgatectl task status %(id)d ready_for_review\n"
        "Use `complete` instead of `ready_for_review` ONLY when the task body carried "
        "a `## Acceptance criteria` heading AND every criterion is validated — see the "
        "clawgate skill's status gate. If you did NOT do work on this task, say so in "
        "one line and stop; nothing else is being asked for."
        % {"id": int(task_id), "ts": first_read_ts}
    )


def unknown_text(task_id, first_read_ts, error):
    return (
        "clawgate write-back UNVERIFIED for task %(id)d: the board could not be "
        "reached to check whether a `claude-code` comment was written since %(ts)s "
        "(%(err)s). This is a NOTICE, not a block — nothing is being asserted about "
        "the card, because nothing could be measured.\n"
        "If this session did work on task %(id)d, write it back:\n"
        "  clawgatectl task comment %(id)d --body \"…\"\n"
        "  clawgatectl task status %(id)d ready_for_review"
        % {"id": int(task_id), "ts": first_read_ts, "err": str(error)[:160]}
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
    state_dir = _state_dir(data)
    if state_dir is None:
        return {"fast_path": True, "recorded": [], "work": False}
    tracked = os.path.exists(state_dir)
    ids = task_read_ids(data)
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
    # Work only counts AFTER a read: `tracked` (or a read recorded on this very call)
    # is what says a card is in play. A session that has never touched the board
    # cannot accumulate a work flag it would later be judged on.
    marked = False
    if work and (tracked or recorded):
        try:
            record_work(state_dir)
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
    {"silent", "context", "block"}. Side effect: it bumps the per-task fire counters,
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

    deadline = clock() + budget
    blocks, notices = [], []
    for tid in sorted(ids)[:MAX_TASKS]:
        remaining = deadline - clock()
        if remaining <= 0:
            break
        first_read_ts = ids[tid]
        err = "the board returned a task payload this hook could not read"
        try:
            task = reader(tid, timeout=min(PER_TASK_TIMEOUT_SECS, remaining))
            state = writeback_state(task, first_read_ts)
        except LiveReadError as e:
            state, err = "unknown", e
        except Exception as e:            # noqa: BLE001 — a reader that raises anything
            state, err = "unknown", e
        if state in ("closed", "written"):
            continue
        fire = bump_fires(state_dir, tid)
        rung = escalate(fire)
        if rung == "silent":
            continue
        if state == "unknown":
            # 🔴 NEVER blocks. Cannot-measure is reported, never enforced.
            notices.append(unknown_text(tid, first_read_ts, err))
            continue
        (blocks if rung == "block" else notices).append(
            missing_text(tid, first_read_ts))

    # 🔴 A "could not measure" notice NEVER CAUSES a block — only `blocks` does, and
    # only a MEASURED missing write-back can put an entry there. When some other task
    # is blocking anyway the notice rides along in the same reason rather than being
    # dropped, but a Stop whose every task is unmeasurable can only ever reach
    # `context`. Pinned by a test that drives an all-unknown session up the ladder.
    if blocks:
        return ("block", "\n\n".join(blocks + notices))
    if notices:
        return ("context", "\n\n".join(notices))
    return ("silent", "")


def emit(kind, text):
    """The ONE writer. `silent` is handled HERE rather than at the call site, so there
    is exactly one place that decides what reaches stdout — a caller-side `if kind !=
    "silent"` in front of this was redundant with the fall-through below, and a branch
    no mutation can kill reads as a coverage gap when it is really just a duplicate.
    """
    if kind == "block":
        json.dump({"decision": "block", "reason": text}, sys.stdout)
        sys.stdout.write("\n")
    elif kind == "context":
        json.dump({"hookSpecificOutput": {"hookEventName": "Stop",
                                          "additionalContext": text}}, sys.stdout)
        sys.stdout.write("\n")


# --------------------------------------------------------------------------- #
def main():
    # 🔴 ONE exit, and it is always 0. Nothing inside the try may call sys.exit():
    # SystemExit is a BaseException and would sail past `except Exception`.
    try:
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
