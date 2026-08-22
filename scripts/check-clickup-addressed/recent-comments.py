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


def build_record(tid, tname, task, comment, text):
    """One comment row.

    A function, not an inline dict, so the `snippet`/`text` split is pinned by a test. Both
    ends of that split were tested independently while the JOIN was not — and dropping
    `text` here silently reverts the whole fix, since the consumer falls back to `snippet`.
    """
    return {
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


def _collect(limit, fast, as_json):
    my_id = get_my_user_id()
    tasks = get_my_tasks()

    # In fast mode, only check the 10 most recently updated tasks
    if fast and len(tasks) > 10:
        tasks = tasks[:10]

    results = []
    for task in tasks:
        tid = task["id"]
        tname = task.get("name", tid)[:80]
        comments = get_comments(tid)
        for c in comments:
            user_id = str(c.get("user", {}).get("id", ""))
            if user_id == my_id:
                continue
            text = extract_text(c)
            if not text:
                continue
            results.append(build_record(tid, tname, task, c, text))

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
