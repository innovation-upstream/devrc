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
    {schema, runtime, session_id, last_activity_ts, window_id?, tmux_pid?,
     host?, transcript_path?}

🔴 `None` means "does not apply", NEVER zero — the convention throughout
`session-manager`. A clawgate agent has no pane, so its `window_id`/`tmux_pid` are
null; that is a fact about the runtime, not a missing measurement.

ONE FILE PER JOIN KEY, ONE LINE PER FILE
----------------------------------------
A tmux record is keyed on its `window_id`, because the window is what the reader
joins to and a window must not accumulate one file per session it has ever hosted —
fuzzyclaw's `<index>.json` naming let two files claim one window, which cost that
window its record entirely (`index_tasks_by_window`), and grew to 401 files of which
90% were stale.

The body is EXACTLY ONE LINE of JSON terminated by a newline, so the whole ledger
reads as `cat dir/*.json` over a pipe — one command, local or over SSH, no
per-file round trip. A writer that emits a second line breaks the next record on the
wire; `write_record` is the only writer and it enforces this.

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

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not read tmux and it does not read the network. `write_record` and `prune`
touch the filesystem; everything else is pure, so the reader's join is exercised
end-to-end from fixtures with no host state at all.

`parse_iso_epoch` lives HERE and `session-manager` aliases it, so the timestamp both
sides of the ledger agree on has ONE definition. The hook cannot import
`session-manager` (extensionless, and it opens a ClickHouse client on import), and a
second six-line ISO parser is exactly the duplicated predicate that ends up wrong at
one of its two sites.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone

SCHEMA = 1

# Bumping SCHEMA is a WIRE break: the reader rejects a record whose schema it does
# not know rather than guessing at the shape. Both halves ship together (the hook
# and session-manager are one repo, one switch), so there is no mixed-version
# window to support — and a record silently reinterpreted under the wrong shape is
# how a wrong session id gets published.
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

# fuzzyclaw reached 401 files, ~90% stale, because nothing ever pruned. Every write
# prunes, so the ceiling is "records written in the last week" rather than "records
# ever written".
DEFAULT_MAX_AGE = 7 * 86400

# A write costs a stat + a small atomic replace. `PostToolUse` fires on every tool
# call, so a throttle keeps a grinding agent from rewriting the same record hundreds
# of times a minute — while still keeping the age fresh far inside the 1h stale
# threshold `classify_status` uses.
DEFAULT_THROTTLE = 30

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
                 tmux_pid=None, host=None, transcript_path=None) -> dict:
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
        "tmux_pid": (str(tmux_pid).strip() or None) if tmux_pid else None,
        "host": (str(host).strip() or None) if host else None,
        "transcript_path": (str(transcript_path).strip() or None
                            if transcript_path else None),
    }


def record_filename(rec) -> str:
    """`claude-41.json` for a tmux record, `claude-s-<session>.json` without one.

    Keyed on `window_id` when there is one so a window holds exactly ONE record
    whatever sequence of sessions has run in it. A runtime with no tmux presence
    falls back to the session id, which is a UUID and unique by construction.
    """
    runtime = _clean(rec.get("runtime") or "unknown")
    wid = rec.get("window_id")
    if wid:
        return "%s-%s.json" % (runtime, _clean(wid))
    return "%s-s-%s.json" % (runtime, _clean(rec.get("session_id") or "unknown"))


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


def write_record(rec, directory=LEDGER_DIR, throttle_secs=None, now=None) -> dict:
    """Write `rec` atomically. Returns {"written", "path", "reason"}.

    🔴 THE THROTTLE IS SESSION-SCOPED, and that is not a detail. Skipping a write
    because "one landed 4 seconds ago" is right for the same session's next tool
    call and WRONG the moment a different session takes the window over: the stale
    record's session id would keep winning the join for a full throttle interval,
    publishing one session's history under another's window. So a differing
    `session_id` always writes, immediately.
    """
    import time as _time
    ts = _time.time() if now is None else float(now)
    reason = "ok"
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, record_filename(rec))
        if throttle_secs:
            prev = _read_existing(path)
            if prev is not None and prev.get("session_id") == rec.get("session_id"):
                prev_ts = parse_iso_epoch(prev.get("last_activity_ts"))
                if prev_ts is not None and (ts - prev_ts) < float(throttle_secs):
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
           "error": None}
    import time as _time
    ts = _time.time() if now is None else float(now)
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except OSError as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
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
            'cat %s/*.json 2>/dev/null; exit 0'
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
                                  not a tmux row; kept aside, never joined

    🔴 When `live_windows is None` NOTHING is filtered and NOTHING is kept: the
    status is `unmeasured`, every count is None and `records` is empty. Returning
    the unfiltered set would publish records for windows that may not exist, and
    returning an empty set with `status: ok` would publish a measured zero for a
    measurement that never happened. Both mistakes have shipped in this tool
    before, on this exact join.
    """
    if live_windows is None:
        return {"status": "unmeasured", "records": [], "unjoinable": [],
                "seen": None, "live": None, "not_live": None,
                "generation_mismatch": None, "generation_unchecked": None,
                "no_window": None, "error": unmeasured_reason}
    kept, unjoinable = [], []
    counts = {"not_live": 0, "generation_mismatch": 0,
              "generation_unchecked": 0, "no_window": 0}
    for rec in (records or ()):
        wid = rec.get("window_id")
        if not wid:
            counts["no_window"] += 1
            unjoinable.append(rec)
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
    return {"status": "ok", "records": kept, "unjoinable": unjoinable,
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

    Ties (identical timestamps) resolve on the record read first, which is
    `sorted()` filename order from `read_command` — deterministic, not arbitrary.
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
                                              for r in v})}
                      for w, v in sorted(conflicts.items())],
    }
