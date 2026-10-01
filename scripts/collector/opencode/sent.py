#!/usr/bin/env python3
"""oc-sent — show the messages the user SENT in an opencode session.

Usage:
    oc-sent show <session-id> [--all-agents] [--db PATH]
    oc-sent here [CWD] [--all-agents] [--db PATH]

Session DISCOVERY is a solved problem — `opencode session list [-n N]
[--format json]` prints full untruncated ids, titles and times newest-first —
so this tool does not duplicate it: `show` takes the id straight from there.
(round-0 audit candidate D1: an agent-invented `list` subcommand deleted as a
built-in duplicate; disposition recorded on the PR.)

`here` is the tmux-keybind entry point (prefix+S → display-popup): it resolves
WHICH session is "current" from a working directory — exact `session.directory`
match first, then the LONGEST ancestor directory of CWD, then the newest
session overall with a note on stderr. It prints a `# <title>  <id>` header
line first, so a reader of the popup knows which session the messages belong
to without cross-referencing. CWD defaults to the process's cwd; the keybind
passes `#{pane_current_path}`.
`show` and `here` print user messages one line each, in send order: local
timestamp, then the text (internal newlines rendered as " ⏎ ", so a message
stays one greppable line). Local time, not UTC — the reader is a human
scanning for "what did I ask around 3pm".

This is the human-display sibling of `export.py` (machine artifact) and the
telemetry `tailer.py` (events). It reads the store ONLY through `_shared` —
read-only, schema-drift tolerant — and takes its connection from `_shared.get_db`,
never its own (test-enforced).

🔴 WHAT A DISPLAYED SESSION DOES *NOT* CONTAIN — name the omission:

  * **a user message with no text part is omitted, and the omission is
    announced on stderr** (count, not identity). `export.py` documents the
    same population hole for the same reason: the unit is the part, and a
    message the store stored with only non-text parts (attachments) is not
    text to show. It is *counted* here because a human diffing "I sent 5
    things" against 4 lines needs the discrepancy named, not discovered.
  * only `session`, `message` and `part` are read (17 of the store's 20
    tables are invisible — the same list `export.py`'s header enumerates).
  * only the FIRST `session_id` match is shown; session ids are unique, so
    this is belt-and-braces, not a known hole.

🔴 HOW A "REAL" TYPED MESSAGE IS RECOGNISED — measured 2026-09-30, store-wide,
446 user-role messages: there is NO is_human flag. The only main-agent name
observed is `build` (388 of 446); every other `agent` value on a user-role
message is a subagent or automation injecting itself as a pseudo-user:
explore 18, probe 16, general 12, review 5, notools 3, skillonly 1,
readonly 1, nav 1, k8s 1. So the default filter is `agent == "build"`, and
`--all-agents` shows the rest, each tagged with its agent value.

  ⚠ No `plan`-agent user message exists in this store (0 of 446), so the
  default filter's correctness on plan-mode prompts is UNMEASURED, not
  verified. If a plan-mode prompt ever disappears from `show`, that is the
  first suspect — `--all-agents` is the escape hatch, and the fix is to
  widen the default set against a fresh measurement, never a guess.

🔴 THE STORE SCHEMA IS NOT A STABILITY GUARANTEE. `_shared` deliberately
swallows schema drift into empty iterators, which is right for a tailer and
poisonous here, where emptiness would be read as "you sent nothing" — the
exact misdirection `export.py`'s EXIT_STORE_UNREADABLE exists to prevent.
`show` reuses `export._store_is_readable` and compensates for what that
predicate does NOT cover — round-1 finding 1, measured:

  * the predicate probes only (id, time_created) on `session`, while the
    `known` scan reads 16 columns through `_shared.iter_sessions`. A store
    whose session table carries exactly the probed columns PASSES the probe
    and then raises IndexError on the scan. The wrap below
    (`except (sqlite3.DatabaseError, IndexError)` → rc 5, mirroring
    export.py's own) is the compensation — the docstring previously claimed
    the predicate "asks for the same columns the reader reads", which was
    false for the session reader; this paragraph is its replacement, and the
    probe gap is INHERITED from export.py, not fixed here (widening it would
    change export.py's behaviour too).
  * `S.get_db()` itself can raise (`--db <directory>` → OperationalError on
    the URI open); it is wrapped → "cannot open store" + rc 5, exactly
    export.py:244-248.

Exit family (as export.py): 0 ok, 2 no store, 3 no such session, 4 no
DISPLAYABLE user messages under the current filter, 5 store unreadable.
Message/part ordering re-uses `export._order_key`, because `_shared`'s
`ORDER BY time_created` is not a total order and two runs must agree.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys
from pathlib import Path

# ⚠ Deliberately NOT `.resolve()` — same reasoning as `export.py`, whose header
# records why: this tree also ships as store symlinks, and the UNRESOLVED
# parent is where the sibling modules live under both deployments. The
# `~/.local/bin/oc-sent` PATH shim resolves the symlink itself (readlink -f)
# and execs this file by its real path, so `__file__` is always a directory
# that contains the siblings.
_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _shared as S  # noqa: E402
import export as X  # noqa: E402

#: Reused from export.py rather than re-declared: one predicate, one place —
#: a second copy of "is the store readable" would drift from the reader's
#: actual column set exactly the way the export.py docstring warns.
_store_is_readable = X._store_is_readable
_order_key = X._order_key

EXIT_OK = X.EXIT_OK
EXIT_NO_DB = X.EXIT_NO_DB
EXIT_NO_SUCH_SESSION = X.EXIT_NO_SUCH_SESSION
EXIT_SESSION_EMPTY = X.EXIT_SESSION_EMPTY
EXIT_STORE_UNREADABLE = X.EXIT_STORE_UNREADABLE

#: The measured main-agent name (see module docstring for the measurement).
HUMAN_AGENT = "build"


def _fmt_ts(epoch_ms) -> str:
    """Epoch milliseconds → local wall-clock string, one line per message.

    Local, not UTC: the consumer is a human correlating against their own
    afternoon. Values that are not numeric render as "?" rather than raising —
    a display tool must not die on schema drift `_shared` already tolerated.
    """
    try:
        return datetime.datetime.fromtimestamp(
            int(epoch_ms) / 1000, tz=datetime.timezone.utc
        ).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "?"


def _one_line(text: str) -> str:
    """Fold internal newlines so one message is one greppable output line."""
    return " ⏎ ".join(seg.strip() for seg in text.splitlines() if seg.strip())


def user_messages(
    db, session_id: str, all_agents: bool = False
) -> tuple[list[tuple[str, str, str]], int, int]:
    """Return ([(ts_str, agent, text)], textless_hidden, other_agent_hidden).

    Ordered by `export._order_key` (total order), text joined from ALL text
    parts of the message — `tailer.extract_text` takes only the first part,
    which is the telemetry view; a dispatch brief attached as a second part is
    part of what the user sent. Empty-text parts contribute nothing, and a
    message whose text parts are all empty is dropped from the list and
    counted in textless_hidden instead. Non-build user messages are likewise
    counted in other_agent_hidden — round-1 finding 2: 60 of 223
    sessions-with-user-messages measured 2026-10-01 are ALL non-build (every
    dispatched-subagent session, 0 mixed), so "no user messages" would be a
    false sentence for 27% of the population unless the count names what the
    filter did.
    """
    out: list[tuple[str, str, str]] = []
    textless = 0
    other_agent = 0
    for message in sorted(S.iter_messages(db, session_id), key=_order_key):
        if message.get("role") != "user":
            continue
        agent = message.get("agent") or "?"
        if not all_agents and agent != HUMAN_AGENT:
            other_agent += 1
            continue
        texts = [
            part.get("text") or ""
            for part in sorted(S.iter_parts(db, message["id"]), key=_order_key)
            if part.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t.strip())
        if not joined.strip():
            textless += 1
            continue
        out.append((
            _fmt_ts(message.get("time_created")),
            agent,
            joined,
        ))
    return out, textless, other_agent


def _print_messages(db, session_id: str, all_agents: bool) -> int:
    """Render one session's user messages. Empty → EXIT_SESSION_EMPTY with
    the filter wording; the caller owns the "is this session known" check."""
    try:
        messages, textless, other_agent = user_messages(
            db, session_id, all_agents=all_agents
        )
    except (sqlite3.DatabaseError, IndexError) as exc:
        print(f"store read failed: {exc}", file=sys.stderr)
        return EXIT_STORE_UNREADABLE

    if not messages:
        if other_agent:
            print(
                f"no {HUMAN_AGENT}-agent user messages "
                f"({other_agent} non-build hidden — retry with --all-agents)",
                file=sys.stderr,
            )
        else:
            print("no displayable user messages", file=sys.stderr)
        if textless:
            print(
                f"({textless} user message(s) had no text part and are not shown)",
                file=sys.stderr,
            )
        return EXIT_SESSION_EMPTY

    for ts, agent, text in messages:
        tag = "" if agent == HUMAN_AGENT else f"[{agent}] "
        print(f"{ts}  {tag}{_one_line(text)}".rstrip())
    if textless:
        print(
            f"({textless} user message(s) had no text part and are not shown)",
            file=sys.stderr,
        )
    return EXIT_OK


def cmd_show(db, session_id: str, all_agents: bool) -> int:
    if not _store_is_readable(db):
        print(
            "store is unreadable (missing tables or schema drift) — "
            "this is NOT the same as an unknown session",
            file=sys.stderr,
        )
        return EXIT_STORE_UNREADABLE

    try:
        known = any(s.get("id") == session_id for s in S.iter_sessions(db))
    except (sqlite3.DatabaseError, IndexError) as exc:
        print(f"store read failed: {exc}", file=sys.stderr)
        return EXIT_STORE_UNREADABLE
    if not known:
        print(f"no such session: {session_id}", file=sys.stderr)
        return EXIT_NO_SUCH_SESSION
    return _print_messages(db, session_id, all_agents)


def resolve_session_for_cwd(
    db, cwd: str
) -> tuple[dict | None, str]:
    """Return (session, note) for "the current opencode session" at a cwd.

    Exact `session.directory` match first; then the LONGEST ancestor
    directory of cwd (an opencode TUI launched at the project root records
    the root, while the pane may sit in a subdir); then the newest session
    overall WITH a note — a wrong answer labelled beats a silent wrong
    answer, and the newest session is the best guess available. Newest
    means `time_updated` (fallback `time_created`), matching `opencode
    session list`'s own ordering.
    """
    sessions = sorted(
        S.iter_sessions(db),
        key=lambda s: s.get("time_updated") or s.get("time_created") or 0,
        reverse=True,
    )
    if not sessions:
        return None, ""

    def _norm(p: str | None) -> str:
        return (p or "").rstrip("/")

    cwd_n = _norm(cwd)
    exact = [s for s in sessions if _norm(s.get("directory")) == cwd_n]
    if exact:
        return exact[0], ""
    ancestors = [
        s for s in sessions
        if _norm(s.get("directory")) and (cwd_n + "/").startswith(_norm(s.get("directory")) + "/")
    ]
    if ancestors:
        return max(ancestors, key=lambda s: len(_norm(s["directory"]))), ""
    return sessions[0], (
        f"(no session directory matches {cwd} — showing the most recently "
        "active session)"
    )


def cmd_here(db, cwd: str, all_agents: bool) -> int:
    if not _store_is_readable(db):
        print(
            "store is unreadable (missing tables or schema drift) — "
            "this is NOT the same as an unknown session",
            file=sys.stderr,
        )
        return EXIT_STORE_UNREADABLE

    try:
        session, note = resolve_session_for_cwd(db, cwd)
    except (sqlite3.DatabaseError, IndexError) as exc:
        print(f"store read failed: {exc}", file=sys.stderr)
        return EXIT_STORE_UNREADABLE
    if session is None:
        print("no opencode sessions found", file=sys.stderr)
        return EXIT_NO_SUCH_SESSION
    if note:
        print(note, file=sys.stderr)
    title = (session.get("title") or "").strip() or "(untitled)"
    print(f"# {title}  {session['id']}")
    return _print_messages(db, session["id"], all_agents)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="oc-sent",
        description="Show the messages the user sent in opencode sessions.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_show = sub.add_parser("show", help="one session's user messages")
    p_show.add_argument("session_id")
    p_show.add_argument(
        "--all-agents", action="store_true",
        help="include subagent-injected pseudo-user messages, tagged",
    )
    p_show.add_argument("--db", help="explicit store path (default: discovered)")
    p_here = sub.add_parser(
        "here", help="messages for the session matching a cwd (tmux keybind entry)"
    )
    p_here.add_argument(
        "cwd", nargs="?", default=None,
        help="directory to resolve against (default: the process's cwd)",
    )
    p_here.add_argument(
        "--all-agents", action="store_true",
        help="include subagent-injected pseudo-user messages, tagged",
    )
    p_here.add_argument("--db", help="explicit store path (default: discovered)")
    args = ap.parse_args(argv)

    try:
        db = S.get_db(Path(args.db) if args.db else None)
    except sqlite3.DatabaseError as exc:
        print(f"cannot open store: {exc}", file=sys.stderr)
        return EXIT_STORE_UNREADABLE
    if db is None:
        print("no opencode database found", file=sys.stderr)
        return EXIT_NO_DB
    try:
        if args.cmd == "show":
            return cmd_show(db, args.session_id, args.all_agents)
        return cmd_here(db, args.cwd or os.getcwd(), args.all_agents)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
