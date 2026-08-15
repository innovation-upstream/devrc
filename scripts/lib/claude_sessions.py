"""The pure Claude-Code-in-tmux session detector — ONE definition, two consumers.

Given a tmux pane list and a /proc snapshot, answer "which panes are running a
live Claude Code session, what is each one doing, and how long since its window
last saw agent activity". Every function here is pure given its injected
fetchers, so the whole detector is unit-testable against fixtures without
touching tmux or /proc (scripts/tests/test_claude_sessions.py).

🔴 WHY THIS IS A MODULE AND NOT A SCRIPT. It used to live inside
`scripts/agent-ops`, the mission-control TUI, and the live bar count pill (then
`scripts/i3status-agent-ops`, now `scripts/i3status-claude-runs`) loaded that
TUI by explicit path purely to reuse the detector. When the TUI was retired the detector had to survive it, because the
pill is the one consumer with a live user AND because this detector is strictly
more accurate than the alternative in the tree: `scripts/session-manager` keys
off `pane_current_command =~ /claude/`, which cannot see a Claude running under
a wrapper or a shell, and renders those windows `? unk`. This walks the pane's
whole process TREE.

Consumers load it by EXPLICIT PATH (it is a `.py` under scripts/lib/, and the
consumers are extensionless scripts that cannot be a package). Do not re-spell
any predicate here at a call site — `scripts/tests/test_claude_sessions.py`
carries a single-source ledger of the importers, in the shape of
`test_clawgate_predicate_single_source.py`, and fails when the set grows OR
shrinks.

⚠ NOTHING HERE READS THE FILESYSTEM FOR ACTIVITY. `index_window_activity` is a
pure parser over record *bodies* and `window_activity_age` takes the resulting
index from its CALLER. The one function that actually walked
`~/.tmux/tasks/*.json` — `read_fuzzyclaw_task_texts` — was fuzzyclaw-specific
and was deleted with the TUI, which is a reader removal in the sense of
claudedocs/spec-agent-activity-ledger.md §6. The parser and the join survive it
because they are `classify_claude_sessions`'s documented contract and because
the successor producer is the agent ledger (§3), which emits the same
`{window_id, tmux_session, window_index, last_activity}` record shape. The
current sole consumer (the bar pill) passes no index at all, so every row's
`age_secs` is None — which renders as "unproven", never as 0.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import time


CLAUDE_RE = re.compile(r"claude", re.IGNORECASE)


# pane_title is LAST — it carries the agent's actual task (`✳ Investigate 500s`)
# and may itself contain '|', so parse_panes splits with a bounded maxsplit.
# `window_id` comes from THIS call rather than a second `list-windows` sweep:
# two non-atomic tmux calls can disagree about which window an index names
# (`renumber-windows on` moves them), and the age join keys on window_id.
#
# `start_time` is the SERVER's start time (identical on every pane — tmux has no
# per-window equivalent). It rides along here rather than in a second
# `display-message` call for the same non-atomicity reason, and because one call
# means a pane can never be paired with a start time from a different server.
# `window_activity_age` uses it as the age join's ERA BOUND — see there.
PANE_FORMAT = ("#{pane_id}|#{pane_pid}|#{session_name}|#{window_index}|"
               "#{window_id}|#{window_name}|#{pane_current_path}|"
               "#{pane_current_command}|#{start_time}|#{pane_title}")

# What parse_panes calls each of PANE_FORMAT's fields, in the SAME order. The
# pairing is the contract; the test walks it rather than trusting this comment.
# EXTEND this tuple when you extend the format — appending to one only shifts
# every field after the insertion point into the wrong name, silently.
PANE_FIELDS = ("pane_id", "pane_pid", "session", "window_index", "window_id",
               "window_name", "path", "command", "server_start", "title")


def list_tmux_panes_raw() -> str:
    """Raw `tmux list-panes -a` output (pipe-delimited). '' on any failure."""
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", PANE_FORMAT],
            capture_output=True, text=True, timeout=3)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _read_proc(pid: int):
    """Read (comm, ppid, state, age_secs) for a pid from /proc. None if gone."""
    try:
        with open("/proc/%d/stat" % pid, "r") as fh:
            data = fh.read()
        # comm is in parens and may contain spaces/parens: split on the LAST ')'.
        rparen = data.rfind(")")
        lparen = data.find("(")
        comm = data[lparen + 1:rparen]
        rest = data[rparen + 2:].split()
        state = rest[0]                      # field 3
        ppid = int(rest[1])                  # field 4
        starttime = int(rest[19])            # field 22 (clock ticks since boot)
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime", "r") as fh:
            uptime = float(fh.read().split()[0])
        age = uptime - (starttime / hz) if hz else None
        return {"comm": comm, "ppid": ppid, "state": state, "age_secs": age}
    except Exception:
        return None


def build_proc_index(reader=_read_proc) -> dict:
    """Snapshot every visible process into {pid: {comm,ppid,state,age_secs,children:[]}}.

    `reader(pid)` is injectable so classify_claude_sessions can be unit-tested
    against a mock process tree without touching /proc.
    """
    index: dict[int, dict] = {}
    try:
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except Exception:
        return index
    for pid in pids:
        info = reader(pid)
        if info is None:
            continue
        entry = index.setdefault(pid, {})
        entry.update(info)
        entry.setdefault("children", [])
    # link children
    for pid, info in list(index.items()):
        ppid = info.get("ppid")
        if ppid in index and ppid != pid:
            index[ppid]["children"].append(pid)
    return index


def own_pid_chain(reader=_read_proc) -> set:
    """PIDs of this process and its ancestors (so we can exclude our own pane)."""
    chain = set()
    pid = os.getpid()
    for _ in range(64):
        if pid <= 0 or pid in chain:
            break
        chain.add(pid)
        info = reader(pid)
        if not info:
            break
        pid = info.get("ppid", 0)
    return chain


def parse_panes(raw: str) -> list:
    """Split raw `tmux list-panes -a` output into pane dicts. Tolerates junk.

    pane_title is the LAST field (may contain '|'), so split with a maxsplit of
    `len(PANE_FIELDS) - 1` and absorb any trailing pipes into the title. A line
    one field short (no title) degrades to an empty title rather than being
    dropped. The maxsplit is DERIVED from PANE_FIELDS so extending the format
    cannot leave a stale literal behind — the positional indices below are still
    spelled out, and `test_pane_format_and_parser_agree_field_for_field` walks
    them against the format rather than trusting this docstring.
    """
    title_idx = len(PANE_FIELDS) - 1
    panes = []
    for line in raw.splitlines():
        parts = line.split("|", title_idx)
        if len(parts) < title_idx:
            continue
        pid = parts[1]
        if not pid.isdigit():
            continue
        panes.append({
            "pane_id": parts[0],
            "pane_pid": int(pid),
            "session": parts[2],
            "window_index": parts[3],
            "window_id": parts[4],
            "window_name": parts[5].rstrip(" ●").strip(),
            "path": parts[6],
            "command": parts[7],
            "server_start": parts[8],
            "title": (parts[title_idx].strip() if len(parts) > title_idx else ""),
        })
    return panes


def _iso_epoch(value):
    """ISO-8601 string → epoch seconds; None on anything unparseable.

    fuzzyclaw writes local time WITH an offset (`2026-08-11T19:08:55-05:00`), but
    a naive value is accepted too and read as local time — the same host wrote it.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _epoch_num(value):
    """tmux `#{start_time}` (epoch seconds, rendered as a string) → float.

    None for absent / blank / non-numeric / non-positive, all of which mean "this
    server's era is UNPROVEN". `window_activity_age` treats that as fail-closed:
    a tmux too old to know `start_time` renders "—" rather than an unbounded age.
    """
    try:
        num = float(str(value).strip())
    except Exception:
        return None
    return num if num > 0 else None


# How far into the FUTURE a `last_activity` may sit before it is rejected rather
# than clamped. Real clock skew between two writes on ONE host is sub-second;
# this is generous headroom for an NTP step, not a repair budget. Beyond it the
# file is corrupt or the clock is badly wrong, and clamping would render "0s" —
# "active right now" — which is the single most misleading thing this column can
# say about a window that is in fact dead. Fail closed like everything else here.
FUTURE_SKEW_TOLERANCE = 120.0


def index_window_activity(raw_texts) -> dict:
    """Index fuzzyclaw task-file bodies by tmux `window_id`.

    → `{window_id: {"session", "window_index", "last_activity"}}`. Junk (bad
    JSON, non-dict, missing/blank window_id) is skipped, never fatal.

    🔴 A window_id claimed by two DISAGREEING files maps to **None** — ambiguous,
    so no row gets an age from it. fuzzyclaw is an untrusted writer whose files
    outlive their windows (measured 2026-08-11: 357 of 400 name dead windows);
    two live claimants means we cannot tell which is this window's, and the
    wrong age is exactly the defect this join exists to remove. Byte-identical
    duplicates are NOT a conflict — they carry the same answer.
    """
    index: dict = {}
    for text in (raw_texts or ()):
        try:
            obj = json.loads(text)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        wid = obj.get("window_id")
        if not isinstance(wid, str) or not wid:
            continue
        rec = {"session": obj.get("tmux_session"),
               "window_index": obj.get("window_index"),
               "last_activity": obj.get("last_activity")}
        if wid in index and index[wid] != rec:
            index[wid] = None          # two disagreeing claimants → fail closed
            continue
        index[wid] = rec
    return index


def window_activity_age(pane, activity_index, now=None):
    """Seconds since the pane's WINDOW last saw agent activity — or None.

    🔴 This is PER-WINDOW and it is not the Claude process's uptime. The two are
    different numbers and the dashboard used to print the second one: a window
    whose agent replied minutes ago, inside a `claude` that has been up for days,
    rendered as `▶ … 4d` — busy and four days abandoned at the same time. Every
    window of a session launched in one burst then shows one identical age,
    which is what makes the wrong number look like a per-session bug.

    The join keys on tmux's `window_id` (unique, never reused while the server
    lives) and is then bounded and confirmed:

      * ERA BOUND (`#{start_time}`). Window ids restart at @0 when the tmux
        server does, so a file left by a PREVIOUS server can name an id a
        different window now holds. MEASURED on this host 2026-08-11 (server up
        6.48d): 9 of 39 live window ids carried task files written BEFORE it
        started, the oldest from 2026-04-19. A `last_activity` earlier than the
        server's own start time cannot describe any window this server created,
        whatever else agrees, so it is rejected. This closes id reuse BY
        CONSTRUCTION rather than by coincidence.
      * CONFIRMATION against the task file's own (tmux_session, window_index).
        Within one era `window_id` is already unique, so this is a cross-check on
        an untrusted writer, NOT the id-reuse defence — it was doing that job
        only by coincidence, and only until a restart reproduced a matching
        session/index pair. In the same measurement 4 of those 9 differed in
        just ONE component (three only in session name, one only in index), so
        the coincidence was one layout change from failing. It is kept because a
        fuzzyclaw file that disagrees about which window it describes is not one
        to read a timestamp out of.

    Adding the era bound changed NOTHING on that live sample — the same 28 of 39
    rows resolved, to the same ages, before and after — because the confirmation
    happened to catch all 9 that day. The bound's value is structural, not a
    behaviour delta; do not expect to see it in a before/after render.

    Anything unproven (unknown id, ambiguous claim, mismatch, previous-era
    record, unprovable era, unparseable timestamp, or a timestamp further into
    the future than FUTURE_SKEW_TOLERANCE) returns None and the row shows "—":
    a missing age is honest, a wrong one is not.
    """
    if not activity_index:
        return None
    rec = activity_index.get(pane.get("window_id"))
    if not isinstance(rec, dict):
        return None                    # unknown window, or ambiguous claim
    if str(rec.get("session")) != str(pane.get("session")):
        return None
    if str(rec.get("window_index")) != str(pane.get("window_index")):
        return None
    ts = _iso_epoch(rec.get("last_activity"))
    if ts is None:
        return None
    server_start = _epoch_num(pane.get("server_start"))
    if server_start is None or ts < server_start:
        return None                    # unprovable era, or a previous server's
    delta = (time.time() if now is None else now) - ts
    if delta < -FUTURE_SKEW_TOLERANCE:
        return None                    # corrupt / badly skewed — not "0s"
    return max(0.0, delta)


def _descendants(pid: int, proc_index: dict) -> list:
    """BFS every descendant pid of `pid` (inclusive) using the children map."""
    seen, stack, out = set(), [pid], []
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        stack.extend(proc_index.get(p, {}).get("children", []))
    return out


def _basename_repo(path: str, root_resolver) -> str:
    root = root_resolver(path) if path else None
    if root:
        return os.path.basename(root.rstrip("/"))
    return os.path.basename((path or "").rstrip("/")) or "?"


def default_git_root(path: str):
    """Walk up `path` until a .git entry is found. Pure filesystem, no subprocess."""
    try:
        cur = os.path.abspath(path)
    except Exception:
        return None
    for _ in range(40):
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


# Claude Code renders a leading status glyph in the pane_title: an animated
# BRAILLE spinner (U+2800–U+28FF, e.g. ⠐⠂⠄) while actively working, and a
# "sparkle" (✳/✶/✷/…) at rest / awaiting input. That glyph is a more reliable
# busy signal than a /proc R-state snapshot (which flickers), and stripping it
# leaves the human-readable task.  (The window_name's trailing ` ●` is NOT a
# discriminator — Claude sets it on every one of its windows, busy or idle.)
_BRAILLE_LO, _BRAILLE_HI = 0x2800, 0x28FF
_SPARKLE_GLYPHS = frozenset("✳✻✽✶✷✢✺❋✱*")


def _is_braille(ch: str) -> bool:
    return bool(ch) and _BRAILLE_LO <= ord(ch[0]) <= _BRAILLE_HI


def strip_status_glyph(title: str) -> str:
    """Drop a leading Claude status glyph (braille spinner / sparkle) + whitespace.

    `✳ Investigate remaining 500 errors` → `Investigate remaining 500 errors`.
    A title with no leading glyph (a bare shell prompt like `nixos`) is returned
    trimmed but otherwise unchanged. Never raises."""
    t = (title or "").replace("\n", " ").strip()
    if not t:
        return ""
    if _is_braille(t[0]) or t[0] in _SPARKLE_GLYPHS:
        return t[1:].strip()
    return t


def busy_from_title(title: str):
    """Busy tri-state from the pane_title's leading glyph.

    braille spinner → True (working), sparkle → False (idle/awaiting), neither
    (no glyph / empty) → None (unknown → caller falls back to /proc state)."""
    t = (title or "").strip()
    if not t:
        return None
    if _is_braille(t[0]):
        return True
    if t[0] in _SPARKLE_GLYPHS:
        return False
    return None


def classify_claude_sessions(panes, proc_index, own_pids=frozenset(),
                             root_resolver=default_git_root,
                             activity_index=None, now=None) -> list:
    """Return the live Claude-Code sessions among `panes`.

    A pane is a Claude session if its foreground command matches /claude/ OR any
    process in its tree has a comm matching /claude/ (e.g. `.claude-wrapped`).
    The dashboard's OWN pane (any pane whose tree contains one of `own_pids`) is
    excluded. Each row carries the pane's TASK (pane_title, status-glyph stripped)
    and a busy flag derived from the title glyph, falling back to /proc R-state
    when the title has no glyph. Pure given an injected `proc_index` + `root_resolver`.

    Two ages, deliberately named apart because conflating them WAS the bug:
      * `age_secs`      — time since that WINDOW's last agent activity, joined
                          per-window from `activity_index` (see
                          `window_activity_age`). None when unproven. THIS is
                          what the dashboard renders.
      * `proc_age_secs` — the Claude process's uptime from /proc. Kept because
                          it is a real (and free) fact, but it answers "how long
                          has this been running", NOT "how long since anything
                          happened", and it is identical across every window of
                          a session started in one burst.
    """
    sessions = []
    for pane in panes:
        tree = _descendants(pane["pane_pid"], proc_index)
        if own_pids and any(p in own_pids for p in tree):
            continue  # our own dashboard pane
        claude_pid = None
        for p in tree:
            comm = proc_index.get(p, {}).get("comm", "")
            if CLAUDE_RE.search(comm):
                claude_pid = p
                break
        is_claude = claude_pid is not None or CLAUDE_RE.search(pane.get("command", ""))
        if not is_claude:
            continue
        info = proc_index.get(claude_pid, {}) if claude_pid else {}
        state = info.get("state")
        proc_busy = None if state is None else (state == "R")
        title = pane.get("title", "")
        title_busy = busy_from_title(title)
        busy = title_busy if title_busy is not None else proc_busy
        sessions.append({
            "pane_id": pane["pane_id"],
            "repo": _basename_repo(pane["path"], root_resolver),
            "session": pane["session"],
            "window_index": pane["window_index"],
            "window_id": pane.get("window_id", ""),
            "window_name": pane.get("window_name", ""),
            "task": strip_status_glyph(title),
            "busy": busy,
            "age_secs": window_activity_age(pane, activity_index, now),
            "proc_age_secs": info.get("age_secs"),
        })
    # Group ordering: by repo, then session, then window.
    sessions.sort(key=lambda s: (s["repo"], s["session"],
                                 _int_or(s["window_index"])))
    return sessions


def _int_or(v, default=0):
    try:
        return int(v)
    except Exception:
        return default
