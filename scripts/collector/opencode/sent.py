#!/usr/bin/env python3
"""oc-sent — show the messages the user SENT in an opencode session.

Usage:
    oc-sent list [-n N]                    recent sessions, newest first
    oc-sent show <session-id> [--all-agents]

    both subcommands accept --db PATH (default: the discovered store)

`list` prints one line per session: local time of last activity, the FULL
session id (copy-pasteable as the `show` argument), the title.
`show` prints one line per user message, in send order: local timestamp,
then the text (internal newlines rendered as " ⏎ ", so a message stays one
greppable line). Local time, not UTC — the reader is a human scanning for
"what did I ask around 3pm".

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
`show` therefore reuses `export._store_is_readable` (the predicate that asks
for the same columns the reader reads) and refuses with the same exit code
family as `export.py`: 0 ok, 2 no store, 3 no such session, 4 session has no
user messages, 5 store unreadable. Message/part ordering re-uses
`export._order_key`, because `_shared`'s `ORDER BY time_created` is not a
total order and two runs must agree.
"""
from __future__ import annotations

import argparse
import datetime
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
) -> tuple[list[tuple[int, str, str, str]], int]:
    """Return ([(time_created, ts_str, agent, text)], hidden_count).

    Ordered by `export._order_key` (total order), text joined from ALL text
    parts of the message — `tailer.extract_text` takes only the first part,
    which is the telemetry view; a dispatch brief attached as a second part is
    part of what the user sent. Empty-text parts contribute nothing, and a
    message whose text parts are all empty is dropped from the list and
    counted in hidden_count instead.
    """
    out: list[tuple[int, str, str, str]] = []
    hidden = 0
    for message in sorted(S.iter_messages(db, session_id), key=_order_key):
        if message.get("role") != "user":
            continue
        agent = message.get("agent") or "?"
        if not all_agents and agent != HUMAN_AGENT:
            continue
        texts = [
            part.get("text") or ""
            for part in sorted(S.iter_parts(db, message["id"]), key=_order_key)
            if part.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t.strip())
        if not joined.strip():
            hidden += 1
            continue
        out.append((
            message.get("time_created") or 0,
            _fmt_ts(message.get("time_created")),
            agent,
            joined,
        ))
    return out, hidden


def cmd_list(db, limit: int) -> int:
    sessions = sorted(
        S.iter_sessions(db),
        key=lambda s: s.get("time_updated") or s.get("time_created") or 0,
        reverse=True,
    )
    for s in sessions[: max(limit, 0)]:
        ts = _fmt_ts(s.get("time_updated") or s.get("time_created"))
        title = (s.get("title") or "").strip() or "(untitled)"
        print(f"{ts}  {s['id']}  {title}")
    return EXIT_OK


def cmd_show(db, session_id: str, all_agents: bool) -> int:
    if not _store_is_readable(db):
        print(
            "store is unreadable (missing tables or schema drift) — "
            "this is NOT the same as an unknown session",
            file=sys.stderr,
        )
        return EXIT_STORE_UNREADABLE

    known = any(s.get("id") == session_id for s in S.iter_sessions(db))
    messages, hidden = user_messages(db, session_id, all_agents=all_agents)

    if not messages:
        if not known:
            print(f"no such session: {session_id}", file=sys.stderr)
            return EXIT_NO_SUCH_SESSION
        print("session has no user messages", file=sys.stderr)
        return EXIT_SESSION_EMPTY

    for _, ts, agent, text in messages:
        tag = "" if agent == HUMAN_AGENT else f"[{agent}] "
        print(f"{ts}  {tag}{_one_line(text)}".rstrip())
    if hidden:
        print(
            f"({hidden} user message(s) had no text part and are not shown)",
            file=sys.stderr,
        )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="oc-sent",
        description="Show the messages the user sent in opencode sessions.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="recent sessions, newest first")
    p_list.add_argument("-n", "--limit", type=int, default=25)
    p_show = sub.add_parser("show", help="one session's user messages")
    p_show.add_argument("session_id")
    p_show.add_argument(
        "--all-agents", action="store_true",
        help="include subagent-injected pseudo-user messages, tagged",
    )
    for p in (p_list, p_show):
        p.add_argument("--db", help="explicit store path (default: discovered)")
    args = ap.parse_args(argv)

    db = S.get_db(Path(args.db) if args.db else None)
    if db is None:
        print("no opencode database found", file=sys.stderr)
        return EXIT_NO_DB
    try:
        if args.cmd == "list":
            return cmd_list(db, args.limit)
        return cmd_show(db, args.session_id, args.all_agents)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
