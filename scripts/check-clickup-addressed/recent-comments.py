#!/usr/bin/env python3
"""Fetch recent comments on assigned ClickUp tasks, return task IDs + comment metadata.

Usage:
    python3 recent-comments.py [--limit N] [--json] [--fast]

Output (default): one line per comment, tab-separated:
    task_id\ttask_name\tcomment_date\tcomment_author\tcomment_snippet

With --json: JSON array of objects.

--fast: Only check the 10 most recently updated tasks (much faster for large backlogs).
"""
import subprocess, json, sys, os
from datetime import datetime, timezone

CLICKUP_DIR = os.environ.get(
    "CLICKUP_DIR", os.path.expanduser("~/.config/opencode/skills/clickup")
)


def run_clickup(*args):
    result = subprocess.run(
        ["node", "query.mjs", *args],
        capture_output=True, text=True, cwd=CLICKUP_DIR, timeout=120
    )
    return result.stdout


def get_my_tasks():
    raw = run_clickup("my-tasks", "--json")
    lines = raw.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("["))
        return json.loads("\n".join(lines[start:]))
    except (StopIteration, json.JSONDecodeError):
        return []


def get_comments(task_id):
    raw = run_clickup("comments", task_id, "--json")
    lines = raw.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("["))
        return json.loads("\n".join(lines[start:]))
    except (StopIteration, json.JSONDecodeError):
        return []


def get_my_user_id():
    raw = run_clickup("me")
    for line in raw.split("\n"):
        if line.startswith("ID:"):
            return line.split(":", 1)[1].strip()
    return None


def extract_text(comment_obj):
    parts = []
    for p in comment_obj.get("comment", []):
        if isinstance(p, dict):
            parts.append(p.get("text", ""))
    return " ".join(parts).strip()


def task_status(task):
    """ClickUp returns status as {"status": "to do", ...} or occasionally a bare string."""
    s = task.get("status")
    if isinstance(s, dict):
        return s.get("status")
    return s


def task_priority(task):
    p = task.get("priority")
    if isinstance(p, dict):
        return p.get("priority")
    return p


def format_date(ts_ms):
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(ts_ms)


def main():
    limit = 3
    as_json = False
    fast = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--json":
            as_json = True; i += 1
        elif args[i] == "--fast":
            fast = True; i += 1
        else:
            i += 1

    return _collect(limit, fast, as_json)


# Display truncation for the report; `text` below is what DECISIONS read.
SNIPPET_CHARS = 200
# Generous cap on the analysed text. Not uncapped, because this rides in every report's
# JSON — but 20x the display cap, because the keep-open veto is absolute and a refusal to
# close that falls past a truncation boundary silently restores the "close it" instruction.
# The comment that motivated the veto is 196 characters; it cleared the old 200-char cap by
# four. See `claude/skills/check-clickup-addressed/reference/validation-history.md` round 4.
TEXT_CHARS = 4000


# Sentinel for "we could not identify the user at all", which is NOT the same fact as "the
# user has not replied". `get_my_user_id()` returns None whenever the ClickUp CLI prints no
# `ID:` line — auth expiry, a network error, an output-format change — and `run_clickup` does
# not check the subprocess return code, so this is silent. Without the sentinel the id
# comparison below never matches, `latest_reply_by` returns None, and the record carries
# `my_latest_reply: null`, which the consumer reads as the POSITIVE claim "I looked; you never
# replied". That is the one design property this whole seam exists to protect, defeated on the
# exact failure mode that produces it. `build_record` omits the key for this value, so the
# consumer takes its announce-rather-than-decide branch.
UNIDENTIFIED = object()


def _epoch_ms(value):
    """ClickUp's raw millisecond timestamp as an int, or None if it cannot be read.

    Tolerant of the string form because that is what the API returns (`"1755800000000"`).
    The CONSUMER's parse is deliberately stricter — see `_ms` in check-addressed.py — because
    it reads a field this file wrote, where anything but an int means a drifted producer.
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def latest_reply_ts_by(comments, my_id):
    """Raw epoch-ms of the newest comment authored by `my_id`.

    Three return values, and the third is the point:
      * an int — my newest reply,
      * `None` — I looked at every comment and none is mine ("you never replied"),
      * `UNIDENTIFIED` — one of MY comments carries a date this function cannot read, so
        which of my comments is newest is UNKNOWABLE.

    🔴 That third case used to `continue`, and it restored the exact false positive round 6
    fixed. An unreadable date on my NEWEST comment made the loop skip it and return either
    an OLDER reply of mine or `None` — and `None` is the POSITIVE claim "I looked; you
    never replied", so the waiting flag says "nobody has answered" about a ticket that was
    answered. `UNIDENTIFIED`'s own comment above states that property; this is that
    property defeated on a different input, which is why the sentinel is reused here rather
    than a second, weaker signal being invented.

    It is deliberately ANY of my comments, not "the newest one": an unreadable date has no
    position in the ranking, so there is no way to establish that it is not the newest. The
    conservative reading — we cannot rank my comments — is the only one that is true.

    Ranked on the raw epoch-ms, never on the formatted string: `format_date` falls back to
    `str(ts_ms)` for an unparseable value, so a max() over formatted dates can rank garbage
    above a real timestamp. Format the winner, not the candidates.
    """
    best = None
    for c in comments:
        if str(c.get("user", {}).get("id", "")) != my_id:
            continue
        ts = _epoch_ms(c.get("date", ""))
        if ts is None:
            return UNIDENTIFIED
        if best is None or ts > best:
            best = ts
    return best


def _formatted_reply(ts):
    """Display form of `latest_reply_ts_by`'s answer, passing both non-int states through.

    One function rather than two parallel expressions: `_collect` needs the raw ms AND the
    formatted string for the same reply, and deriving them at two sites is how they drift
    apart. `format_date(UNIDENTIFIED)` would return the repr of an `object()` — a string,
    truthy, and indistinguishable downstream from a real date.
    """
    if ts is None or ts is UNIDENTIFIED:
        return ts
    return format_date(ts)


def latest_reply_by(comments, my_id):
    """Formatted date of my newest comment, `None` if I never replied, `UNIDENTIFIED` if
    my comments cannot be ranked. See `latest_reply_ts_by` for why the third state exists.

    The loop there drops every comment that is not mine — the mirror of the filter in
    `_collect`, which is right for a report about what OTHER people said and is exactly why
    the downstream "nobody has answered" flag was blind: the evidence that would refute it
    is removed before the flag ever runs. This carries the one fact across that seam.
    """
    return _formatted_reply(latest_reply_ts_by(comments, my_id))


def build_record(tid, tname, task, comment, text, my_latest_reply, my_latest_reply_ms):
    """One comment row.

    A function, not an inline dict, so the `snippet`/`text` split is pinned by a test. Both
    ends of that split were tested independently while the JOIN was not — and dropping
    `text` here silently reverts the whole fix, since the consumer falls back to `snippet`.
    The same is true of `my_latest_reply`: drop it and the waiting flag goes back to
    reporting answered tickets as unanswered, with nothing failing.

    `my_latest_reply` is a REQUIRED positional argument rather than a defaulted one. A
    default would let a caller omit it and get the pre-fix behaviour silently; this way the
    omission is a TypeError at the only call site. `my_latest_reply_ms` is required for the
    same reason and it is the stronger case: omitting it degrades silently to the
    minute-resolution comparison, which is the pre-change behaviour and looks like a
    working tool.
    """
    rec = {
        "task_id": tid,
        "task_name": tname,
        # The ticket's OWN state is the authority on whether work is outstanding; the
        # transcript scan downstream cannot see it. A ticket left at `to do` under a
        # comment reading "Resolved, recommend closing" is invisible without these two.
        "task_status": task_status(task),
        "task_priority": task_priority(task),
        "date": format_date(comment.get("date", "")),
        "author": comment.get("user", {}).get("username", "?"),
        "snippet": text[:SNIPPET_CHARS],
        "text": text[:TEXT_CHARS],
    }
    # 🔴 The RAW millisecond, beside the display string it was formatted from — because
    # `format_date` throws the seconds away and the consumer's "have I already answered
    # this?" comparison then runs at MINUTE resolution, where a reply written 20 seconds
    # BEFORE the question compares EQUAL to it. At minute resolution "equal counts as
    # answered" is a judgement call that has been argued both ways; carrying the ms makes it
    # a question of fact instead, and a tie a real tie.
    #
    # Emitted only when it PARSES. An unreadable date must not become a `0` (1970, so every
    # reply looks newer) nor a `None` (a JSON null the consumer would have to special-case
    # anyway) — it is ABSENT, and the consumer falls back to the age comparison it already
    # had. Absent-vs-present is the same discipline `my_latest_reply` gets below.
    date_ms = _epoch_ms(comment.get("date", ""))
    if date_ms is not None:
        rec["date_ms"] = date_ms
    # Present-and-null ("I looked; you never replied") is a different fact from absent
    # ("nobody could look"). The consumer branches on which, so the key is emitted for every
    # value EXCEPT the UNIDENTIFIED sentinel, whose whole purpose is to reach the absent
    # branch. Emitting it unconditionally is what turned a failed user lookup into a
    # confident false negative.
    if my_latest_reply is not UNIDENTIFIED:
        rec["my_latest_reply"] = my_latest_reply
        # Nested, so the ms can never appear without the formatted date it refines: one is
        # EVIDENCE (did the producer look?), the other is only PRECISION. The evidence
        # distinction lives in `my_latest_reply` alone and is deliberately NOT duplicated
        # here — two fields carrying the same fact is two chances to disagree — so this key
        # is simply absent for "no reply" and for "unreadable", and its absence is never
        # read as a claim about anything.
        if isinstance(my_latest_reply_ms, int) and not isinstance(my_latest_reply_ms, bool):
            rec["my_latest_reply_ms"] = my_latest_reply_ms
    return rec


def _collect(limit, fast, as_json):
    my_id = get_my_user_id()
    if not my_id:
        print("recent-comments: WARNING — could not resolve your ClickUp user id, so your "
              "own comments cannot be distinguished from anyone else's. Every downstream "
              "'have I already replied?' check is disabled for this run.", file=sys.stderr)
    tasks = get_my_tasks()

    # In fast mode, only check the 10 most recently updated tasks
    if fast and len(tasks) > 10:
        tasks = tasks[:10]

    results = []
    for task in tasks:
        tid = task["id"]
        tname = task.get("name", tid)[:80]
        comments = get_comments(tid)
        # Computed over the FULL comment list, before the loop below discards mine. When the
        # user could not be identified at all, the answer is UNKNOWN rather than "no reply" —
        # see UNIDENTIFIED. Loud on stderr too: a report whose author id is missing cannot
        # tell your comments from anyone else's, and silence there reads as a normal run.
        if my_id:
            my_reply_ts = latest_reply_ts_by(comments, my_id)
        else:
            my_reply_ts = UNIDENTIFIED
        # The raw ms and its display string come from ONE lookup, so they cannot describe
        # two different comments.
        my_reply = _formatted_reply(my_reply_ts)
        for c in comments:
            user_id = str(c.get("user", {}).get("id", ""))
            if user_id == my_id:
                continue
            text = extract_text(c)
            if not text:
                continue
            results.append(
                build_record(tid, tname, task, c, text, my_reply, my_reply_ts))

    # Sort newest first, take top N
    results.sort(key=lambda x: x["date"], reverse=True)
    results = results[:limit]

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{r['task_id']}\t{r['task_name']}\t{r['date']}\t@{r['author']}\t{r['snippet'][:120]}")


if __name__ == "__main__":
    main()
