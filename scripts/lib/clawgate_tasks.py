"""The ONE definition of "which clawgate tasks need the operator".

🔴 WHY THIS MODULE EXISTS. The predicate was open-coded at two sites —
`scripts/bar-status-poll` (`PENDING_TASK_STATES`) and `scripts/agent-ops`
(`_PENDING_STATES`) — and both spelled it `{"open", "ready_for_review"}`, i.e.
both were wrong in the SAME direction. `scripts/session-manager` inherited the
same blindness for free by reading the poller's cache rather than the API.
Fixing one site would have regenerated the gap at the other two, so all three
now import from here and there is exactly one place to be wrong.

🔴 WHAT THE OLD PREDICATE MISSED. Excluding `in_progress` was justified as "an
agent is working = not on the human." That is exactly the state a DEAD dispatch
is stranded in: the task stays `in_progress` forever while its agent is in
`error`, was never kicked off, or simply stopped emitting. One such task sat
`status=running, kickedOff=true`, pod Running, for four hours after finishing
its work — contributing nothing to the operator-pending count on any surface.
A status check alone therefore cannot decide this; see `stuck_reasons`.

DATA SOURCE. clawgate 0.7.86 added `GET /api/agents` and, more usefully here, an
`agent` key on every task from `GET /api/tasks[/{id}]` — the same agent object,
or `null` when the task has no dispatched agent. 🔑 **The key is always present**
(measured live against 0.7.86: 17 tasks, `all(has("agent"))` true), so an absent
`agent` key means an OLD server, not an unlinked task, and the two must not be
conflated. `?summary=1` swaps the (large) `body` for `commentCount` /
`attachmentCount` and keeps everything else including `agent` — 190,385 bytes
down to 7,058 on the live board, so every consumer here uses it.

This module is PURE: no network, no clock of its own (`now` is injected), no
filesystem beyond `read_clawgate_env`. Everything is unit-tested in
scripts/tests/test_clawgate_tasks.py, and the consolidation itself is pinned by
scripts/tests/test_clawgate_predicate_single_source.py, which fails if a second
copy of the state set reappears anywhere in the repo.
"""
from __future__ import annotations

import datetime
import os

# --------------------------------------------------------------------------- #
# The predicate's constants. 🔴 ONE definition each — the single-source test
# fails the build if either is re-spelled in a consumer.
# --------------------------------------------------------------------------- #

#: Task statuses that mean "the operator still has to act" without any agent
#: reasoning: a freshly posted task awaiting dispatch (`open`) or a finished
#: agent run awaiting review (`ready_for_review`).
PENDING_TASK_STATES = frozenset({"open", "ready_for_review"})

#: The status a dispatched task sits in while its agent is (supposedly) working.
#: A task in this state needs the operator only if its agent looks dead.
IN_PROGRESS = "in_progress"

#: 🔴 How long an `in_progress` task's agent may be silent before we call the
#: dispatch stuck. 15 minutes, chosen from measurement rather than taste: two
#: healthy runs closed in 138s and 149s (an order of magnitude below this), and
#: the observed wedge sat idle for four hours (an order of magnitude above).
#: Anything inside that gap is a judgement call, and this errs toward the alarm.
AGENT_IDLE_THRESHOLD_SECS = 900

#: The path (query included) every consumer fetches the board from. Kept here so
#: `?summary=1` cannot be adopted on one surface and missed on another.
#:
#: ⚠ Deliberately NOT `?status=open&status=in_progress&...`: 0.7.86 does support
#: a repeatable `?status=` (OR within the param) and 400s on an unknown value —
#: which is the RIGHT failure mode for a producer, but the wrong one for a bar
#: poller. A status added server-side would turn every poll into a hard 400 and
#: the pill would go dark. Filtering client-side costs ~7 KB and cannot fail.
TASKS_PATH = "/api/tasks?summary=1"

#: Where the hook token and base URL live. Read at CALL time, never cached in a
#: module global and never formatted into a URL, a log line or an exception.
CLAWGATE_ENV_PATH = os.path.join("~", ".claude", "clawgate.env")

#: Fallback base URL, matching what the consumers used before this module.
DEFAULT_API_URL = "http://192.168.50.250:30302"

#: 🔴 The stuck reasons, ENUMERATED AND CLOSED. A consumer renders these
#: verbatim, and the single-source test pins this tuple against the branches in
#: `stuck_reasons` so a new disjunct cannot exist in the code and be missing
#: from the vocabulary a reader is shown.
STUCK_REASONS = (
    "no_agent",             # in_progress, but `agent` is null — nothing is running
    "agent_error",          # the agent reconciler marked it `error`
    "not_kicked_off",       # provisioned but the session never started
    "agent_idle",           # kicked off and running, but silent past the threshold
    "activity_unknown",     # running, but it carries no usable activity timestamp
)

#: How many ids the human-readable `detail` string names before it says so.
DETAIL_MAX_IDS = 6


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
def parse_api_ts(value):
    """clawgate's RFC3339 timestamp -> epoch seconds, or None if unusable.

    Go emits `2026-07-30T23:34:18.640843Z`, and can emit nanosecond precision.
    `datetime.fromisoformat` rejects more than 6 fractional digits, so the
    fraction is truncated rather than allowed to fail — a timestamp we refuse to
    parse becomes `activity_unknown`, which is a (correct but noisy) alarm, so
    it is worth not manufacturing one over a digit count.

    Returns None for None, a non-string, or anything unparseable. It never
    raises: this feeds a status pill that must not crash on a server change.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    # Truncate an over-long fractional part (…:18.640843123+00:00 -> …:18.640843)
    if "." in s:
        head, _, tail = s.partition(".")
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        rest = tail[len(digits):]
        if len(digits) > 6:
            digits = digits[:6]
        s = head + "." + digits + rest if digits else head + rest
    try:
        dt = datetime.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        # A naive timestamp from this API is UTC; assuming local time would move
        # the reading by hours and silently change every idle verdict.
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def agent_activity_ts(agent):
    """Newest activity timestamp for an agent object, or None.

    `lastActivityAt` is the server's own max(newest persisted transcript row,
    updatedAt), so it is the field to read. `updatedAt` is a fallback for the
    case where the newer field is absent or unparseable — without it an older
    server would make every running agent read `activity_unknown`.
    """
    if not isinstance(agent, dict):
        return None
    for key in ("lastActivityAt", "updatedAt"):
        ts = parse_api_ts(agent.get(key))
        if ts is not None:
            return ts
    return None


def agent_idle_secs(task, now):
    """Seconds since this task's agent last showed activity, or None.

    🔴 THIS NUMBER SHIPS WITH THE BOOLEAN, EVERYWHERE. A bare "stuck" flag is an
    authoritative-looking verdict with no provenance; `idle 16m` and `idle 4h`
    are the same flag and completely different situations, and only the second
    is worth interrupting anyone over.

    🔴 WHAT IT CANNOT SEE: activity is only persisted between turns, so a single
    long in-flight turn (a big build, a slow test suite) looks identical to a
    wedged one. This measure therefore errs toward the FALSE ALARM, and that is
    the intended direction — a false "check on this dispatch" costs a glance, a
    false "everything is fine" costs the four hours that motivated the module.
    Clamped at 0 so clock skew reads as "just now" rather than as a negative age.
    """
    ts = agent_activity_ts((task or {}).get("agent") if isinstance(task, dict) else None)
    if ts is None:
        return None
    return max(0.0, float(now) - ts)


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #
def is_pending(task) -> bool:
    """True for a task awaiting the operator by STATUS alone."""
    return isinstance(task, dict) and task.get("status") in PENDING_TASK_STATES


def stuck_reasons(task, now, threshold: int = AGENT_IDLE_THRESHOLD_SECS) -> list:
    """Why this `in_progress` task looks stranded — `[]` means it does not.

    Gate first: a task that is not `in_progress` is never stuck, whatever its
    agent looks like (a `complete` task's agent is expected to be idle forever).

    Then the disjuncts, each independently sufficient:
      no_agent          the task claims in_progress with no agent object at all
      agent_error       the reconciler already gave up on it
      not_kicked_off    provisioned but the session never started; clawgate's own
                        `provisioningStuckTimeout` marks the AGENT `error` while
                        leaving the TASK `in_progress`, so this catches the
                        window before that fires and the case where it does not
      agent_idle        running, kicked off, silent past `threshold`
      activity_unknown  running, kicked off, and carrying no timestamp we can
                        read — "cannot tell" must never render as "healthy"

    They are collected rather than short-circuited so a reader sees every reason
    that applies; the caller's boolean is `bool(...)` of this list.
    """
    if not isinstance(task, dict) or task.get("status") != IN_PROGRESS:
        return []
    agent = task.get("agent")
    if not isinstance(agent, dict):
        return ["no_agent"]
    reasons = []
    if agent.get("status") == "error":
        reasons.append("agent_error")
    if agent.get("kickedOff") is not True:
        reasons.append("not_kicked_off")
    idle = agent_idle_secs(task, now)
    if idle is None:
        reasons.append("activity_unknown")
    elif idle > threshold:
        reasons.append("agent_idle")
    return reasons


def is_stuck(task, now, threshold: int = AGENT_IDLE_THRESHOLD_SECS) -> bool:
    """`stuck_reasons` as a boolean. Never use it without the reasons or the
    idle seconds beside it — see `agent_idle_secs`."""
    return bool(stuck_reasons(task, now, threshold))


def _row(task, now, threshold):
    """One task -> the small record every surface renders."""
    title = (task.get("title") or "").strip() or "(no title)"
    reasons = stuck_reasons(task, now, threshold)
    return {
        "id": task.get("id"),
        "title": title,
        "status": task.get("status"),
        "reasons": reasons,
        "agent_idle_secs": agent_idle_secs(task, now),
    }


# --------------------------------------------------------------------------- #
# The roll-up every surface publishes
# --------------------------------------------------------------------------- #
#: Bumped whenever the shape below changes incompatibly. A consumer reading a
#: CACHED copy branches on this rather than probing for a key: a cache written
#: by the previous poller has no stuck information at all, and the honest
#: reading of that is "stuck dispatches were NOT measured", never "zero stuck".
SCHEMA = 2


def attention(tasks, now, threshold: int = AGENT_IDLE_THRESHOLD_SECS) -> dict:
    """The whole `/api/tasks` payload -> what needs the operator.

    Raises ValueError if the top level is not a list (the poller turns that into
    a `stale` marker); junk LIST ELEMENTS are tolerated and skipped.

    `count` is the headline: pending + stuck, i.e. everything a human has to
    look at. It never travels alone — `pending_count` and `stuck_count`
    discriminate it, and `open` / `ready_for_review` / `stuck` ENUMERATE it.

    🔴 `ready_for_review` is a LIST, not a number. Three finished tasks once
    appeared in no list on any surface: the count moved and nothing said what
    had finished, which is the same failure as a bare boolean.
    """
    if not isinstance(tasks, list):
        raise ValueError("clawgate /api/tasks payload is not a list")
    open_rows, review_rows, stuck_rows = [], [], []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        st = t.get("status")
        if st == "open":
            open_rows.append(_row(t, now, threshold))
        elif st == "ready_for_review":
            review_rows.append(_row(t, now, threshold))
        elif st == IN_PROGRESS:
            row = _row(t, now, threshold)
            if row["reasons"]:
                stuck_rows.append(row)
    pending_count = len(open_rows) + len(review_rows)
    count = pending_count + len(stuck_rows)
    detail, shown, truncated = _detail(count, open_rows, review_rows, stuck_rows)
    return {
        "schema": SCHEMA,
        "count": count,
        "pending_count": pending_count,
        "stuck_count": len(stuck_rows),
        "open": [{"id": r["id"], "title": r["title"]} for r in open_rows],
        "ready_for_review": [{"id": r["id"], "title": r["title"]}
                             for r in review_rows],
        "stuck": [{"id": r["id"], "title": r["title"], "reasons": r["reasons"],
                   "agent_idle_secs": r["agent_idle_secs"]} for r in stuck_rows],
        "threshold_secs": threshold,
        "state": "Warning" if count else "Idle",
        "detail": detail,
        "detail_shown": shown,
        "detail_total": count,
        "detail_truncated": truncated,
    }


def fmt_idle(secs) -> str:
    """`agent_idle_secs` as something a human reads at a glance. None -> `?`,
    which is NOT `0s` — an unmeasured idle time must not render as a fresh one."""
    if secs is None:
        return "?"
    s = int(secs)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def _detail(count, open_rows, review_rows, stuck_rows):
    """The one-line human summary, and whether it named everything.

    🔴 THE OLD ONE LIED BY OMISSION. It read `11 task(s) awaiting: <six ids>` —
    six of eleven, with no hint that five were missing, and the dropped tail has
    included `ready_for_review` items, i.e. finished work awaiting review. One
    such omission cost three finished tasks any mention at all on any surface:
    the count moved and nothing named what had moved. Two changes: the cap is
    now stated (`+N more`), and the
    ids are ordered stuck -> review -> open so that the rarest and most urgent
    class can never be the one the cap drops. The full sets travel structurally
    beside this string regardless; nothing should ever parse it for a number.
    """
    if not count:
        return "no pending tasks", 0, False
    ordered = ([("!", r) for r in stuck_rows]
               + [("", r) for r in review_rows]
               + [("", r) for r in open_rows])
    shown = ordered[:DETAIL_MAX_IDS]
    ids = ", ".join("%s#%s" % (mark, r["id"]) for mark, r in shown)
    more = count - len(shown)
    head = "%d need you (%d open, %d review, %d stuck)" % (
        count, len(open_rows), len(review_rows), len(stuck_rows))
    tail = ids + (" (+%d more)" % more if more > 0 else "")
    return "%s: %s" % (head, tail), len(shown), more > 0


def unmeasured(reason: str) -> dict:
    """A roll-up that says nothing was measured. `count: None`, never 0 — the
    substitution of an unread queue for an empty one is the failure every
    consumer of this module exists to refuse."""
    return {"schema": SCHEMA, "count": None, "pending_count": None,
            "stuck_count": None, "open": None, "ready_for_review": None,
            "stuck": None, "threshold_secs": AGENT_IDLE_THRESHOLD_SECS,
            "state": "Unknown", "detail": reason, "detail_shown": 0,
            "detail_total": None, "detail_truncated": False}


# --------------------------------------------------------------------------- #
# Credentials (the only I/O in this module)
# --------------------------------------------------------------------------- #
def read_clawgate_env(path=None):
    """`(base_url, token)` from ~/.claude/clawgate.env, read at CALL TIME.

    🔴 The token is returned and nothing else. It is never logged, never put in
    a URL or in argv, and never formatted into an exception here — a KeyError
    names the missing VARIABLE, not its value. Callers keep their own failure
    policy: the poller lets this raise (-> a `stale` marker), agent-ops swallows
    it (-> no enrichment).
    """
    path = os.path.expanduser(path or CLAWGATE_ENV_PATH)
    env = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    base = (env.get("CLAWGATE_API_URL") or DEFAULT_API_URL).rstrip("/")
    if "CLAWGATE_HOOK_TOKEN" not in env:
        raise KeyError("CLAWGATE_HOOK_TOKEN")
    return base, env["CLAWGATE_HOOK_TOKEN"]


def tasks_url(base: str) -> str:
    """The board URL for a base. No credential is ever put in it."""
    return base.rstrip("/") + TASKS_PATH
