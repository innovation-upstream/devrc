#!/usr/bin/env python3
"""The agent activity ledger — one record per agent run, per host, on disk.

WHY THIS EXISTS
---------------
`session-manager`'s default view lost three things when fuzzyclaw was switched off
in #419: every row's AGE, the `stale` bucket that age feeds, and the
`claude_session_id` that the ClickHouse join needs. Measured on the workbench
2026-08-12: default view `rows with an age: 0`, `statuses: idle 40, busy 11` (no
`stale`), `claude_session_id` on 0 rows; with `--fuzzyclaw`, 30/30 ages, `stale 16`,
30/30 session ids. The dogfood finding that motivated #419 — "fuzzyclaw contributes
nothing" — was true of its `status` field (every live row read `paused`) and false of
the source. This module is the replacement: the same three facts, written by the
runtimes themselves, owned here.

Spec: `claudedocs/spec-agent-activity-ledger.md` (#428).

THE RECORD
----------
    {schema, runtime, session_id, last_activity_ts, window_id?, pane_id?,
     tmux_pid?, host?, transcript_path?}

🔴 `None` means "does not apply", NEVER zero — the convention throughout
`session-manager`. A clawgate agent has no pane, so its `window_id`/`tmux_pid` are
null; that is a fact about the runtime, not a missing measurement.

ONE FILE PER PANE, ONE LINE PER FILE
------------------------------------
A tmux record is keyed on its `pane_id` — NOT on the window it is joined by, and not
on the session. Sessions come and go inside one pane, so keying on the session would
accumulate a file per session ever run (fuzzyclaw's rot: 401 files, ~90% stale).

🔴 KEYING ON THE WINDOW LOOKS RIGHT AND IS WRONG, because a window can hold TWO
CLAUDE PANES. One file per window then means two live agents overwrite each other:
measured on the first draft, 10 alternating writes from two sessions inside a 10s
throttle window all landed (the throttle is session-scoped, so alternating writers
never throttle), the file ended up naming whichever wrote last, and
`index_by_window` could not see any of it — there was only ever one file to compare.
The row's `claude_session_id` is the sole carrier into the ClickHouse join, so a
shared window silently resolved to an arbitrary one of its two agents.

Per pane, the same situation produces TWO files carrying ONE `window_id`, which is
exactly the shape `index_by_window` already reports as a conflict. The window still
gets one row and one age — the newest — but the contention is now visible instead
of being decided by write order.

The body is EXACTLY ONE LINE of JSON terminated by a newline, so the whole ledger
reads as one command over a pipe — local or over SSH, no per-file round trip.

🔴 The read uses `awk 1` rather than `cat`, and that is a correctness fix, not a
style one. `cat` concatenates bytes, so a record file with NO trailing newline welds
onto its glob-neighbour and BOTH are lost: measured on the first draft, 3 records
written, 1 parsed, 2 counted `unparseable`. `write_record` always terminates its
line — but it is not the only writer for long (spec §4 adds opencode and clawgate),
and "every writer remembers the newline" is a convention across processes, which is
not a guarantee. `awk 1` emits every input line with a terminator regardless, so the
class is closed at the reader for the price of one fork.

🔴 THE GENERATION GUARD — WHY `tmux_pid` IS ON THE RECORD
---------------------------------------------------------
tmux window ids (`@41`) are unique within a server's lifetime and RESTART AT `@0`
when the server does. So after a reboot — precisely when `tmux-task-resume.sh`
rebuilds the workspace — yesterday's `@5` record and today's `@5` window collide, and
a join on the id alone hands a fresh window a dead session's id and a multi-day age:
a confident wrong value, which is the one thing this tool must not emit. `#{pid}` is
the tmux SERVER pid, constant across every window of a server and different after a
restart, so `record.tmux_pid == live tmux_pid` is an exact generation check.
Measured 2026-08-13 on the workbench: `list-windows -a -F '…|#{pid}'` returns the
same pid (4025325) on every window. Records that cannot be checked (writer predating
the field, or a host whose pid was not measured) are KEPT and COUNTED separately —
`generation_unchecked` — never silently treated as verified.

WHAT THIS MODULE DOES AND DOES NOT DO
-------------------------------------
It does not read the network. `write_record`/`prune` touch the filesystem and
`tmux_context` shells out to tmux; everything else is pure, so the reader's join
is exercised end-to-end from fixtures with no host state at all.

🔴 `tmux_context` LIVES HERE rather than in the Claude hook, because it now has
TWO callers — that hook and the `--write` CLI the opencode plugin spawns. A
window/pid resolver copied into each writer is the duplicated predicate that ends
up wrong at one of its sites; there is one, and both writers reach it.

THE `--write` CLI, and why the opencode writer is a SPAWN
---------------------------------------------------------
`scripts/opencode/plugin/ledger.js` shells out to `python3 agent_ledger.py
--write …` rather than re-implementing the record in JavaScript. That is the
same shape `guard.js` uses for `guard_core.py` and `activity-plugin.js` uses for
its emit script: the JS holds no schema, so writer 2 cannot drift from writer 1
or from the reader. It costs one interpreter start per opencode tool call
(~9 ms measured), which is the price of there being one definition.

`parse_iso_epoch` lives HERE and `session-manager` aliases it, so the timestamp both
sides of the ledger agree on has ONE definition. The hook cannot import
`session-manager` (extensionless, and it opens a ClickHouse client on import), and a
second six-line ISO parser is exactly the duplicated predicate that ends up wrong at
one of its two sites.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCHEMA = 1

# Bumping SCHEMA is a WIRE break: the reader rejects a record whose schema it does
# not know rather than guessing at the shape — a record silently reinterpreted
# under the wrong shape is how a wrong session id gets published.
#
# 🔴 THERE **IS** A MIXED-VERSION WINDOW, and an earlier version of this comment
# denied it. The hook runs the nix-store COPY (`~/.claude/hooks/agent_ledger.py`,
# a `home.file`) while `session-manager` loads the repo WORKING COPY by path — so
# between a `git pull` and a `home-manager switch`, the very gap this repo's
# CLAUDE.md warns about, writer and reader are running different code. Writer and
# reader agree by construction only at the instant of a switch. What SCHEMA buys
# is that the disagreement surfaces as a rising `unparseable` count in the
# rendered ledger section rather than as a wrong value on a row.
KNOWN_SCHEMAS = frozenset({SCHEMA})

HOME = os.path.expanduser("~")

# Relative to $HOME on purpose: the SAME string is used locally (joined to this
# host's HOME) and inside a shell command that runs on the REMOTE host, where
# `$HOME` must expand there, not here.
LEDGER_SUBPATH = os.path.join(".cache", "agent-ledger")
LEDGER_DIR = os.path.join(HOME, LEDGER_SUBPATH)

# 🔴 The read protocol's positive control, and it must contain no shell
# metacharacter: an earlier draft used a leading `#`, which `sh` reads as the start
# of a comment, so `echo` printed an empty line and the sentinel never arrived —
# indistinguishable from a host that did not answer.
SENTINEL = "AGENT_LEDGER_V1"

# fuzzyclaw reached 401 files, ~90% stale, because nothing ever pruned. `write_record`
# does NOT prune; both writers call `prune` on their SESSION BOUNDARIES only, and a
# prune sweeps the whole directory — so the ceiling is "records written in the last
# week" rather than "records ever written", enforced by other sessions' boundaries
# as much as by a record's own writer.
DEFAULT_MAX_AGE = 7 * 86400

# A write costs a stat + a small atomic replace. `PostToolUse` fires on every tool
# call, so a throttle keeps a grinding agent from rewriting the same record hundreds
# of times a minute — while still keeping the age fresh far inside the 1h stale
# threshold `classify_status` uses.
DEFAULT_THROTTLE = 30

# How long a writer may wait for tmux to answer `display-message`. Deliberately
# SHORT: both writers sit on an agent's hot path (`PostToolUse` for the Claude
# hook, every opencode tool call for the CLI), so a wedged or restarting tmux
# server must cost the agent ~nothing. Missing it is not an error — see
# `filename_for`, which keys the record on the SESSION when the window id is
# unknown precisely so this degradation stays lossless.
#
# 🔴 IT IS A CONSTANT HERE AND A PARAMETER EVERYWHERE ELSE, and that is the
# whole point. A caller that needs the tmux answer to be RELIABLE rather than
# CHEAP — a test asserting the pane-keyed filename, say — must be able to buy
# more time, because otherwise its assertion silently depends on how loaded the
# machine is. MEASURED 2026-08-24: this stub-tmux call takes 0.003s (p50) idle
# and 0.197s worst at a 20x CPU stall (`systemd-run -p CPUQuota=5%`) on a
# 24-core workbench — but the `devrc-ci` pytest leg runs in a container capped
# at 4 CPUs / 8Gi beside ~15.8k other tests, several such pods to a 16-core
# node, and `tekton/devrc-pytests` went red on exactly this: the CLI wrote
# `opencode-s-oc-4.json` where the test expected `opencode-p77.json`
# (devrc-ci-wwj4d). Reproduced end-to-end by making the stub sleep 2.5s.
DEFAULT_TMUX_TIMEOUT_S = 2.0

# 🔴 THE ENV VAR EXISTS BECAUSE A PARAMETER CANNOT REACH FOUR OF THE SIX
# EXPOSURES. #810 gave the CLI a `--tmux-timeout` flag, and an audit found it
# covers 2 of 6: `scripts/opencode/plugin/ledger.js` HARDCODES its argv
# (:114-129) so no flag can be injected, and `scripts/claude-hooks/
# agent-ledger-hook.py` calls `tmux_context(pane=...)` with no knob at all. Both
# read the process environment, so this is the one mechanism that reaches every
# caller — including the four sibling tests whose pane-keyed assertions
# otherwise depend on how loaded the machine is, which is the #810 flake.
#
# Precedence: an explicit `timeout=` argument WINS (a caller that named a number
# meant it), then this, then the constant.
TMUX_TIMEOUT_ENV = "AGENT_LEDGER_TMUX_TIMEOUT_S"


def resolve_tmux_budget(timeout=None, environ=None, warn=None):
    """Seconds to allow tmux: explicit arg > `$AGENT_LEDGER_TMUX_TIMEOUT_S` >
    `DEFAULT_TMUX_TIMEOUT_S`.

    🔴 NEVER RAISES, and that is load-bearing rather than defensive: this sits on
    `PostToolUse` and on every opencode tool call, and `tmux_context`'s docstring
    promises every failure is `(None, None)`. An audit of #810 found `float()`
    outside the `try`, so `timeout="abc"` raised `ValueError` straight through a
    contract that said it could not — contained only because the one caller that
    takes user input validated first.

    🔴 AN INVALID BUDGET IS NOT SILENTLY ACCEPTED. `timeout=0` and `timeout=-5`
    previously reached `subprocess.run` and forced an instant timeout — i.e. the
    degraded session-keyed path, silently. That is precisely the failure the
    CLI's own guard exists to prevent, reachable from Python one layer down. Junk
    now falls back to the default AND says so on stderr: a typo'd env var in a
    test must not read as "the budget I asked for", because that puts the
    assertion right back on the machine's load.
    """
    warn = warn if warn is not None else (
        lambda m: print(m, file=sys.stderr, flush=True))
    if timeout is not None:
        src, raw = "timeout=", timeout
    else:
        env = os.environ if environ is None else environ
        raw = env.get(TMUX_TIMEOUT_ENV)
        if raw is None or raw == "":
            return DEFAULT_TMUX_TIMEOUT_S
        src = TMUX_TIMEOUT_ENV + "="
    try:
        val = float(raw)
    except (TypeError, ValueError):
        warn(f"agent_ledger: ignoring {src}{raw!r} (not a number); "
             f"using {DEFAULT_TMUX_TIMEOUT_S}s")
        return DEFAULT_TMUX_TIMEOUT_S
    if not val > 0 or val != val or val == float("inf"):
        warn(f"agent_ledger: ignoring {src}{raw!r} (must be finite and > 0); "
             f"using {DEFAULT_TMUX_TIMEOUT_S}s")
        return DEFAULT_TMUX_TIMEOUT_S
    return val

RUNTIMES = ("claude", "opencode", "clawgate")

# `@41` -> `41`, matching the naming `tmux-task-hook.sh` used (`${WIN_ID//[@%]/}`).
# 🔴 The id is reconstructed from the record BODY, never from the filename, so
# this is naming only and the substitution need not be reversible. Everything
# outside the safe set collapses to `_`, so no id — however hostile, and it
# arrives as JSON written by another host — can put a path separator in a name.
_SIGILS = re.compile(r"^[@%]+")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _clean(value) -> str:
    return _UNSAFE.sub("_", _SIGILS.sub("", str(value)))


def parse_iso_epoch(value):
    """ISO-8601 (with or without offset, `Z` accepted) -> epoch secs, else None.

    🔴 THE canonical definition — `session-manager` aliases this name rather than
    keeping its own copy. A naive timestamp is read as UTC, matching `now_iso`,
    which is the only shape this module ever writes.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def now_iso(now=None) -> str:
    """UTC ISO-8601 with a `Z`, the format `parse_iso_epoch` already accepts."""
    import time as _time
    ts = _time.time() if now is None else float(now)
    return (datetime.fromtimestamp(ts, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def build_record(runtime, session_id, last_activity_ts, window_id=None,
                 pane_id=None, tmux_pid=None, host=None,
                 transcript_path=None) -> dict:
    """The record, validated. Raises ValueError on a shape that cannot be joined.

    🔴 It raises rather than returning a partial record. A record with no
    `session_id` is exactly the row the ClickHouse join cannot resolve, and writing
    it would restore the #419 symptom (`claude_session_id` on 0 rows) while the
    ledger reported records live. The hook's caller catches and exits 0 — a hook
    must never break the turn — but it does not write a hollow record.
    """
    if not str(runtime or "").strip():
        raise ValueError("runtime is required")
    if not str(session_id or "").strip():
        raise ValueError("session_id is required")
    if not str(last_activity_ts or "").strip():
        raise ValueError("last_activity_ts is required")
    return {
        "schema": SCHEMA,
        "runtime": str(runtime).strip(),
        "session_id": str(session_id).strip(),
        "last_activity_ts": str(last_activity_ts).strip(),
        "window_id": (str(window_id).strip() or None) if window_id else None,
        # The FILE KEY. `window_id` stays the JOIN key — the two are different
        # jobs and collapsing them is what let two agents in one window
        # overwrite each other (see the module docstring).
        "pane_id": (str(pane_id).strip() or None) if pane_id else None,
        "tmux_pid": (str(tmux_pid).strip() or None) if tmux_pid else None,
        "host": (str(host).strip() or None) if host else None,
        "transcript_path": (str(transcript_path).strip() or None
                            if transcript_path else None),
    }


def filename_for(runtime, pane_id=None, window_id=None, session_id=None) -> str:
    """The file key, in ONE place — the writer needs it before it has a record.

    `claude-p11.json` for a pane, `claude-41.json` for a tmux record with no pane
    id, `claude-s-<session>.json` for a runtime with no tmux presence at all. The
    `p` prefix keeps the two namespaces apart: pane `%11` and window `@41` both
    clean to digits, and without it a pane could collide with a window.

    🔴 The hook resolves this from `$TMUX_PANE` alone — free, no subprocess —
    so it can consult the throttle BEFORE deciding whether to shell out to tmux
    for the window id and the server pid. That ordering is why this is a
    standalone function rather than a body inside `record_filename`.

    🔴 THE PANE KEY REQUIRES A WINDOW, and that conjunction is a regression fix,
    not a tidiness rule. `$TMUX_PANE` is set but `tmux display-message` can still
    fail — a 2s timeout under load, a dead or restarted server — and the record
    then has a pane and NO `window_id`. Keyed on the pane alone it would overwrite
    the good record IN PLACE, and the window would lose `age_secs`,
    `claude_session_id` and its `stale` bucket outright: the #419 symptom,
    reproduced silently, and rendered as a *measured* `no_window` rejection.
    Measured on the broken version — one file, `seen=1 live=0 no_window=1`;
    with this conjunction, two files and `live=1`. The degraded record still
    lands (it is real activity) but in the session file, where it cannot
    displace a joinable one.
    """
    if pane_id and window_id:
        return pane_filename(runtime, pane_id)
    runtime = _clean(runtime or "unknown")
    if window_id:
        return "%s-%s.json" % (runtime, _clean(window_id))
    return "%s-s-%s.json" % (runtime, _clean(session_id or "unknown"))


def pane_filename(runtime, pane_id) -> str:
    """The pane file's name for a caller that knows the pane but has NOT yet
    resolved the window — the hook's pre-tmux throttle check, and the only such
    caller.

    🔴 It is a PREDICTION, and it is allowed to be wrong in exactly one way: if
    the tmux lookup then fails, the record lands in the session file instead
    (see `filename_for`). Mispredicting there costs at most one suppressed write
    for a session that is already inside its throttle interval — never a wrong
    value on a row. Sharing `filename_for`'s spelling rather than restating it,
    because two format strings for one filename is how the check and the write
    end up looking at different files.
    """
    return "%s-p%s.json" % (_clean(runtime or "unknown"), _clean(pane_id))


def record_filename(rec) -> str:
    return filename_for(rec.get("runtime"), rec.get("pane_id"),
                        rec.get("window_id"), rec.get("session_id"))


def valid_record(obj) -> bool:
    """Is this a record this reader understands? Shape only — no liveness."""
    if not isinstance(obj, dict):
        return False
    if obj.get("schema") not in KNOWN_SCHEMAS:
        return False
    for key in ("runtime", "session_id", "last_activity_ts"):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            return False
    return True


def encode_record(rec) -> str:
    """One line, newline-terminated. `sort_keys` so a byte comparison of two
    writes is meaningful, and the separators drop the padding a `cat` pipe pays
    for on every record."""
    return json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"


# --------------------------------------------------------------------------- #
# WRITE side — the only functions here that touch the filesystem
# --------------------------------------------------------------------------- #
def _read_existing(path):
    try:
        with open(path) as fh:
            obj = json.loads(fh.readline())
    except Exception:  # noqa: BLE001 — absent, truncated, or junk: all "no record"
        return None
    return obj if valid_record(obj) else None


def is_throttled(path, session_id, throttle_secs, now=None) -> bool:
    """Would a write to `path` by `session_id` be suppressed right now?

    🔴 THE THROTTLE IS SESSION-SCOPED, and that is not a detail. Skipping a write
    because "one landed 4 seconds ago" is right for the same session's next tool
    call and WRONG the moment a different session takes the pane over: the stale
    record's session id would keep winning the join for a full throttle interval,
    publishing one session's ClickHouse history under another's window. So a
    differing `session_id` is never throttled.

    ONE definition, called by `write_record` AND by the hook — which consults it
    before spawning `tmux`, on the hot path where most calls are throttled. Two
    copies of a predicate is how the cheap path and the correct path drift apart.
    """
    if not throttle_secs:
        return False
    import time as _time
    ts = _time.time() if now is None else float(now)
    prev = _read_existing(path)
    if prev is None or prev.get("session_id") != session_id:
        return False
    prev_ts = parse_iso_epoch(prev.get("last_activity_ts"))
    return prev_ts is not None and (ts - prev_ts) < float(throttle_secs)


def write_record(rec, directory=LEDGER_DIR, throttle_secs=None, now=None) -> dict:
    """Write `rec` atomically. Returns {"written", "path", "reason"}.

    The throttle rule lives in `is_throttled` — see it for why it is scoped to
    the session rather than to the file.
    """
    import time as _time
    ts = _time.time() if now is None else float(now)
    reason = "ok"
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, record_filename(rec))
        if is_throttled(path, rec.get("session_id"), throttle_secs, now=ts):
            return {"written": False, "path": path,
                    "reason": "throttled", "error": None}
        # Atomic: a reader `cat`ing the directory mid-write must see either the old
        # record or the new one, never half a line — a truncated line would be
        # counted `unparseable` and the window would silently lose its age.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ledger.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(encode_record(rec))
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception as e:  # noqa: BLE001
        return {"written": False, "path": None, "reason": "error",
                "error": "%s: %s" % (type(e).__name__, e)}
    return {"written": True, "path": path, "reason": reason, "error": None}


def prune(directory=LEDGER_DIR, max_age_secs=DEFAULT_MAX_AGE, now=None) -> dict:
    """Delete records older than `max_age_secs`. Returns counts.

    🔴 `examined` travels beside `removed`, always. "0 removed" from a sweep that
    walked an empty or unreadable directory is a different fact from "0 removed,
    31 examined, none old enough", and only the second is a clean bill of health.

    A record whose timestamp cannot be parsed is examined, counted `unparseable`
    and KEPT: pruning is the destructive path, so an unreadable age must never be
    read as an old one.
    """
    out = {"examined": 0, "removed": 0, "kept": 0, "unparseable": 0,
           "temps_removed": 0, "error": None}
    import time as _time
    ts = _time.time() if now is None else float(now)
    try:
        entries = sorted(os.listdir(directory))
    except OSError as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    # 🔴 `write_record` cleans its own temp file on a Python exception, but a
    # SIGKILL between `mkstemp` and `os.replace` leaks one — and since prune
    # otherwise only ever looks at `*.json`, that leak would be PERMANENT. Same
    # age rule, by mtime, because a temp file carries no parseable record.
    for name in entries:
        if not (name.startswith(".ledger.") and name.endswith(".tmp")):
            continue
        path = os.path.join(directory, name)
        try:
            if (ts - os.path.getmtime(path)) >= max_age_secs:
                os.remove(path)
                out["temps_removed"] += 1
        except OSError:
            pass
    names = [n for n in entries if n.endswith(".json")]
    for name in names:
        path = os.path.join(directory, name)
        out["examined"] += 1
        rec = _read_existing(path)
        age = None
        if rec is not None:
            last = parse_iso_epoch(rec.get("last_activity_ts"))
            if last is not None:
                age = ts - last
        if age is None:
            out["unparseable"] += 1
            out["kept"] += 1
            continue
        if age >= max_age_secs:
            try:
                os.remove(path)
                out["removed"] += 1
            except OSError:
                out["kept"] += 1
        else:
            out["kept"] += 1
    return out


# --------------------------------------------------------------------------- #
# READ side — pure
# --------------------------------------------------------------------------- #
def read_command(subpath=LEDGER_SUBPATH, abs_dir=None) -> str:
    """The one shell command that reads a whole host's ledger.

    Identical locally and over SSH, which is the point: `$HOME` expands on the
    machine that runs it, so nothing here assumes the two hosts agree on a path.
    `exit 0` is deliberate — a missing directory and an empty one are the same
    answer (`no records`), and neither is an error. A host that genuinely did not
    answer fails at the SSH/spawn layer instead, and prints no sentinel.

    🔴 THE SENTINEL LINE CARRIES THE HOST'S LIVE tmux SERVER PID, and that
    coupling is deliberate rather than lazy. The pid is the generation guard (see
    the module docstring) and it is worthless without the records it validates, so
    fetching it separately would buy a second SSH round trip per host and a window
    in which the two disagree — the server could restart between the two calls and
    every record would be judged against a generation that was not the one they
    were read beside. One call, one instant, one verdict. A host with no tmux
    server prints the sentinel with no pid, which reads as `generation_unchecked`,
    never as a mismatch.

    Measured 2026-08-13 through the real `ssh_wrap`/`shlex.join` path: workbench
    `AGENT_LEDGER_V1 4025325`, laptop `AGENT_LEDGER_V1 3737` — two hosts, two
    generations, one command.
    """
    return ('echo "%s $(tmux display-message -p \'#{pid}\' 2>/dev/null)"; '
            'awk 1 %s/*.json 2>/dev/null; exit 0'
            % (SENTINEL, _dir_expr(subpath, abs_dir)))


def _dir_expr(subpath=LEDGER_SUBPATH, abs_dir=None) -> str:
    """The directory as the SHELL should see it.

    `abs_dir` exists so the tests and `--selftest` drive the REAL `read_command`
    against a throwaway directory instead of a hand-copied lookalike. A copy of
    the command in a test validates the copy — the instrument that ships stays
    unexercised, which is how a read path gets certified while wired to nothing.
    """
    if abs_dir:
        import shlex
        return shlex.quote(str(abs_dir))
    return '"$HOME"/%s' % subpath


def read_argv(subpath=LEDGER_SUBPATH, abs_dir=None) -> tuple:
    return ("sh", "-c", read_command(subpath, abs_dir))


def parse_ledger(raw) -> dict:
    """Ledger stdout -> {"measured", "tmux_pid", "records", "seen", "unparseable"}.

    🔴 `measured` IS THE POSITIVE CONTROL, carried on the wire. Without the
    sentinel, "the command ran and this host has no agent records" and "something
    swallowed the output" both arrive as an empty string, and publishing the first
    interpretation is exactly the fabricated zero the #419 regression consisted of.
    When `measured` is False every count is None, not 0.

    `tmux_pid` is None whenever the sentinel carried no pid — a host with no tmux
    server. That is a THIRD state, distinct from both "measured" and "not
    measured": the ledger was read, and the generation of its records cannot be
    checked. `filter_live` counts those records `generation_unchecked` rather than
    trusting or discarding them.
    """
    lines = (raw or "").splitlines()
    head = next((ln for ln in lines if ln.strip().split()[:1] == [SENTINEL]),
                None)
    if head is None:
        return {"measured": False, "tmux_pid": None, "records": [],
                "seen": None, "unparseable": None}
    parts = head.strip().split()
    tmux_pid = parts[1] if len(parts) > 1 and parts[1].isdigit() else None
    seen = unparseable = 0
    records = []
    started = False
    for line in lines:
        if not started:
            started = line is head
            continue
        if not line.strip():
            continue
        seen += 1
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            unparseable += 1
            continue
        if not valid_record(obj):
            unparseable += 1
            continue
        records.append(obj)
    return {"measured": True, "tmux_pid": tmux_pid, "records": records,
            "seen": seen, "unparseable": unparseable}


def filter_live(records, live_windows, tmux_pid=None, unmeasured_reason=None):
    """Keep only records a row can honestly be joined to.

    `live_windows` is `{window_id: (session, index)}` from `parse_windows`, or
    `None` when that host's window list was never measured. `tmux_pid` is that same
    host's live tmux SERVER pid, or None when unmeasured.

    Four dispositions, each counted:
      * `live`                  — window is live and the generation matches (or
                                  could not be checked; see `generation_unchecked`)
      * `not_live`              — the window is gone
      * `generation_mismatch`   — the window id exists but belongs to a DIFFERENT
                                  tmux server than the one that wrote the record
      * `no_window`             — the record has no `window_id` at all, so it is
                                  not a tmux row; COUNTED and dropped, never
                                  joined (a Claude run outside tmux). It is a
                                  count and not a list on purpose: an earlier
                                  revision returned the records too and every
                                  caller discarded them, which is a field that
                                  looks like a feature and is dead weight.

    🔴 When `live_windows is None` NOTHING is filtered and NOTHING is kept: the
    status is `unmeasured`, every count is None and `records` is empty. Returning
    the unfiltered set would publish records for windows that may not exist, and
    returning an empty set with `status: ok` would publish a measured zero for a
    measurement that never happened. Both mistakes have shipped in this tool
    before, on this exact join.
    """
    if live_windows is None:
        return {"status": "unmeasured", "records": [],
                "seen": None, "live": None, "not_live": None,
                "generation_mismatch": None, "generation_unchecked": None,
                "no_window": None, "error": unmeasured_reason}
    kept = []
    counts = {"not_live": 0, "generation_mismatch": 0,
              "generation_unchecked": 0, "no_window": 0}
    for rec in (records or ()):
        wid = rec.get("window_id")
        if not wid:
            counts["no_window"] += 1
            continue
        rec_pid, live_pid = rec.get("tmux_pid"), tmux_pid
        if rec_pid and live_pid and str(rec_pid) != str(live_pid):
            counts["generation_mismatch"] += 1
            continue
        if wid not in live_windows:
            counts["not_live"] += 1
            continue
        if not (rec_pid and live_pid):
            counts["generation_unchecked"] += 1
        kept.append(rec)
    return {"status": "ok", "records": kept,
            "seen": len(records or ()), "live": len(kept), "error": None,
            **counts}


def index_by_window(records) -> dict:
    """`{window_id: record}` + the records that lost a contested window.

    🔴 Two records claiming one window is the shape that cost fuzzyclaw its
    window entirely, and the resolution here is deliberately different: keying on
    `window_id` means a duplicate can only arise from a filename collision or a
    hand-edited ledger, so the NEWEST wins by `last_activity_ts` rather than the
    window being dropped. Contested windows are reported either way — a silent
    tie-break is how the wrong session id gets published without a trace.

    Ties (identical timestamps) resolve on the record read FIRST. That is the
    shell's glob order in `read_command` — locale collation, not Python
    `sorted()`, which an earlier version of this sentence claimed. Stable for a
    given host and locale; not something to depend on across hosts.
    """
    index, conflicts = {}, {}
    for rec in (records or ()):
        wid = rec.get("window_id")
        if not wid:
            continue
        prev = index.get(wid)
        if prev is None:
            index[wid] = rec
            continue
        conflicts.setdefault(wid, [prev])
        conflicts[wid].append(rec)
        if str(rec.get("last_activity_ts") or "") > str(
                prev.get("last_activity_ts") or ""):
            index[wid] = rec
    return {
        "index": index,
        "conflicts": [{"window_id": w,
                       "claimants": len(v),
                       "session_ids": sorted({str(r.get("session_id"))
                                              for r in v}),
                       # 🔴 The RUNTIMES too, because the commonest real
                       # conflict is cross-runtime and two opaque session ids
                       # do not say so. A pane that ran Claude and later
                       # opencode holds one record per runtime — both live,
                       # both naming the same window — and `claude, opencode`
                       # is the difference between "which agent owns this
                       # window" and two UUIDs a reader cannot act on.
                       "runtimes": sorted({str(r.get("runtime")) for r in v})}
                      for w, v in sorted(conflicts.items())],
    }


# =========================================================================== #
# TMUX + the CLI — the two IMPURE edges, both shared by every writer
# =========================================================================== #
def tmux_context(runner=None, pane=None, timeout=None):
    """`(window_id, tmux_pid)` for this pane, or `(None, None)`.

    ONE tmux call for both fields: they come from the same server and asking twice
    invites a skew between them for no benefit. Outside tmux — a Claude run in a
    bare terminal, an opencode run outside tmux, or a subagent — there is no
    pane and both are None, which the record carries as "does not apply".

    TWO callers: the Claude hook and the `--write` CLI. It is here so there is
    one resolver rather than one per writer.

    `timeout` is the seconds to allow tmux. Resolution — explicit argument,
    then `$AGENT_LEDGER_TMUX_TIMEOUT_S`, then `DEFAULT_TMUX_TIMEOUT_S` — lives in
    `resolve_tmux_budget`; read that for why the env var exists (a parameter
    cannot reach four of this function's six exposures) and why junk falls back
    loudly rather than silently forcing the degraded path.

    ⚠ `timeout`/the env var apply to the DEFAULT runner only. A caller supplying
    `runner=` owns its own deadline — the budget is not threaded into an injected
    runner, so a test passing both would get a false green about the budget.
    """
    pane = os.environ.get("TMUX_PANE") if pane is None else pane
    if not pane:
        return None, None
    # Never raises, so it is safe outside the `try` — see its docstring. The
    # previous `float(timeout)` here was NOT, which broke this function's own
    # "every failure is (None, None)" promise.
    budget = resolve_tmux_budget(timeout)
    argv = ["tmux", "display-message", "-t", pane, "-p", "#{window_id}|#{pid}"]
    try:
        run = runner or (lambda a: subprocess.run(
            a, capture_output=True, text=True, timeout=budget))
        proc = run(argv)
        if proc.returncode != 0:
            return None, None
        parts = (proc.stdout or "").strip().split("|")
    except Exception:  # noqa: BLE001 — no tmux, dead server, timeout: all "no pane"
        return None, None
    if len(parts) < 2:
        return None, None
    wid, pid = parts[0].strip(), parts[1].strip()
    if not wid.startswith("@") or not pid.isdigit():
        return None, None
    return wid, pid


def main(argv=None) -> int:
    """`--write` a record for a runtime that cannot import this module.

    Used by `scripts/opencode/plugin/ledger.js`. The pane comes from
    `$TMUX_PANE` (free); the window id and server pid cost one tmux call, and
    only when a write is actually going to happen — the same ordering the Claude
    hook uses, for the same reason: most calls are inside the throttle.

    🔴 Exit codes are HONEST (0 wrote-or-throttled, 1 could not) even though the
    only caller ignores them. A CLI that always exits 0 is untestable, and its
    tests would then be asserting nothing.
    """
    import argparse
    p = argparse.ArgumentParser(prog="agent_ledger", description=__doc__)
    p.add_argument("--write", action="store_true", required=True)
    p.add_argument("--runtime", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--transcript-path", default=None)
    p.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE)
    # 🔴 default=None, NOT the constant — and the help text spells the constant
    # itself rather than `%(default)s`. An argparse default is an EXPLICIT
    # argument by the time `main` sees it, and an explicit argument beats the
    # env var by design, so defaulting to the constant here made
    # `$AGENT_LEDGER_TMUX_TIMEOUT_S` structurally unreachable on the CLI path —
    # i.e. on the opencode plugin, which is one of the two exposures the env var
    # exists to serve. Measured: with default=<constant>, the plugin's
    # pane-keyed tests still went red under a mutated constant despite the
    # variable being set. None means "nobody said", which is what lets
    # `resolve_tmux_budget` consult the environment.
    p.add_argument("--tmux-timeout", type=float, default=None,
                   help="seconds to allow tmux to answer (default %s, or "
                        "$%s). Raise it when the pane-keyed filename must be "
                        "RELIABLE rather than cheap — see DEFAULT_TMUX_TIMEOUT_S."
                        % (DEFAULT_TMUX_TIMEOUT_S, TMUX_TIMEOUT_ENV))
    p.add_argument("--prune", action="store_true",
                   help="also reap records past the retention window; the "
                        "caller does this on a session boundary, not per call")
    p.add_argument("--directory", default=LEDGER_DIR)
    args = p.parse_args(sys.argv[1:] if argv is None else list(argv))
    # 🔴 FAIL CLOSED on a junk budget rather than falling back to the default.
    # `--tmux-timeout 0` is not "no limit" — `subprocess.run(timeout=0)` expires
    # immediately, so a typo would silently force the DEGRADED session-keyed
    # path on every write while the CLI still exited 0. argparse's own exit
    # code (2) is the right one: this is a bad invocation, not a failed write.
    # Only when the flag was actually GIVEN: `None` means nobody said, which is
    # not a bad invocation — it hands the decision to `resolve_tmux_budget`,
    # which consults $AGENT_LEDGER_TMUX_TIMEOUT_S and then the constant, and
    # warns rather than raising if THAT is junk. Guarding on `is not None` also
    # stops the comparison below raising TypeError on the default path.
    if args.tmux_timeout is not None and (
            not args.tmux_timeout > 0 or args.tmux_timeout == float("inf")):
        p.error("--tmux-timeout must be a positive, finite number of seconds "
                "(got %r); it bounds the tmux lookup, and a non-positive value "
                "silently forces the session-keyed fallback" % args.tmux_timeout)

    try:
        pane = os.environ.get("TMUX_PANE") or None
        # Same early-out as the hook: the pane names the file, so the throttle
        # is answerable without asking tmux anything.
        if args.throttle and pane:
            path = os.path.join(args.directory,
                                pane_filename(args.runtime, pane))
            if is_throttled(path, args.session, args.throttle):
                return 0
        wid, pid = tmux_context(pane=pane or "", timeout=args.tmux_timeout)
        rec = build_record(
            runtime=args.runtime, session_id=args.session,
            last_activity_ts=now_iso(), window_id=wid, pane_id=pane,
            tmux_pid=pid, transcript_path=args.transcript_path,
            # Same source the Claude hook uses. Without it writer 2's records
            # carried `host: null` while writer 1's did not — an asymmetry no
            # consumer reads today (the READER attributes a host by which
            # machine it read from, which is strictly more reliable), but one
            # that made the record shape differ by writer for no reason.
            host=(os.environ.get("ACTIVITY_HOST") or "").strip() or None)
        out = write_record(rec, directory=args.directory,
                           throttle_secs=args.throttle)
        if args.prune:
            prune(directory=args.directory)
        return 0 if out["written"] or out["reason"] == "throttled" else 1
    except Exception as e:  # noqa: BLE001
        print("agent_ledger --write: %s: %s" % (type(e).__name__, e),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
