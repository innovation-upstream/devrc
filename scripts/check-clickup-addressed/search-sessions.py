#!/usr/bin/env python3
"""Search Claude Code session transcripts for keywords, return ranked matches.

Usage:
    python3 search-sessions.py [--since YYYY-MM-DD] [--limit N] [--project SUBSTR] term1 [term2 ...]

Output (default): one line per match, tab-separated:
    session_id\tdate\tproject\thits\topening_snippet

With --json: an OBJECT — {"sessions": [...], "self_runs_skipped": N,
"self_runs_skipped_ids": [...]}. It was a bare array until 2026-08-21; the
count has to travel with the results or the self-run drop is invisible to the
caller, which sees only a shorter list.

Terms are ANDed by default (session must match all). Add --any to OR them.
"""
import json, os, re, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _selfrun import is_self_run  # noqa: E402

CLAUDE_DIR = Path.home() / ".claude" / "projects"


def load_session(path):
    """Load a session JSONL, return list of (type, content) tuples."""
    entries = []
    with open(path, errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            etype = obj.get("type", "")
            if etype in ("user", "assistant"):
                msg = obj.get("message", {})
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                else:
                    content = str(msg)
                if isinstance(content, list):
                    # Flatten assistant content blocks
                    texts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                texts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                inp = block.get("input", {})
                                if isinstance(inp, dict):
                                    texts.append(json.dumps(inp))
                    content = " ".join(texts)
                if content:
                    entries.append((etype, str(content)))
            elif etype == "ai-title":
                entries.append(("title", obj.get("aiTitle", "")))
    return entries


def search_sessions(terms, since=None, limit=10, project_substr=None, match_any=False,
                    exclude_sessions=None, include_self_runs=False, stats=None):
    """Rank sessions matching `terms`.

    Runs of this checker are dropped by default: a prior run mentions every task ID it was
    asked about, so it always ranks top (14 hits, on 2026-08-20) and reads as the session
    that did the work. See `_selfrun.py`.

    Pass `stats` (a dict) to learn how many were dropped — it gets `self_runs_skipped` and
    `self_runs_skipped_ids`. The count is not returned in the result list because the drop
    is otherwise INVISIBLE: the caller just sees a shorter list and a smaller "N found"
    header, which is indistinguishable from the sessions not existing.
    """
    exclude = {s for s in (exclude_sessions or []) if s}
    results = []
    skipped_ids = []
    for project_dir in CLAUDE_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        if project_substr and project_substr not in project_dir.name:
            continue
        for session_file in project_dir.glob("*.jsonl"):
            if session_file.stem in exclude:
                continue
            mtime = datetime.fromtimestamp(session_file.stat().st_mtime)
            if since and mtime < since:
                continue
            try:
                entries = load_session(session_file)
            except (json.JSONDecodeError, OSError):
                continue

            # Combine all text for matching
            all_text = " ".join(content for _, content in entries)

            # Count matches per term
            term_hits = {}
            for term in terms:
                count = all_text.lower().count(term.lower())
                if count > 0:
                    term_hits[term] = count

            if match_any:
                matched = len(term_hits) > 0
            else:
                matched = len(term_hits) == len(terms)

            if not matched:
                continue

            # Self-run check runs AFTER term matching, deliberately. It reads the file to
            # EOF, and a non-matching file is discarded anyway, so testing it first only
            # bought a full extra read of every transcript in the corpus: measured
            # 5.6s -> 16.1s over 746 files, once PER TASK. Same result set, 2.8x the cost.
            if not include_self_runs and is_self_run(session_file):
                skipped_ids.append(session_file.stem)
                continue

            total_hits = sum(term_hits.values())

            # Get opening message
            opening = ""
            for etype, content in entries:
                if etype == "user" and content:
                    opening = content[:150]
                    break
                elif etype == "title" and content:
                    opening = f"[title] {content}"

            # Get project name from dir
            project = project_dir.name.replace("-home-zach-workspace-", "").replace("-", "/")

            results.append({
                "session_id": session_file.stem,
                "date": mtime.strftime("%Y-%m-%d %H:%M"),
                "project": project,
                "hits": total_hits,
                "term_hits": term_hits,
                "opening": opening[:120],
                "file": str(session_file),
            })

    if stats is not None:
        stats["self_runs_skipped"] = len(skipped_ids)
        stats["self_runs_skipped_ids"] = skipped_ids

    results.sort(key=lambda x: (len(x["term_hits"]), x["hits"], x["date"]), reverse=True)
    return results[:limit]


def render_payload(results, stats):
    """The `--json` document.

    An OBJECT, not a bare list: the skip count has to travel with the results or the drop is
    invisible to check-addressed.py, which then sees only a shorter list — indistinguishable
    from the sessions not existing. A function so the emitted SHAPE is pinned by a test; the
    consumer half (`parse_search_payload`) was tested while the producer half was not, so
    reverting this to a bare list passed the whole suite.
    """
    return {"sessions": results, **stats}


def main():
    since_str = None
    limit = 10
    project = None
    match_any = False
    terms = []
    exclude_sessions = []
    include_self_runs = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--since" and i + 1 < len(args):
            since_str = args[i + 1]; i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]; i += 2
        elif args[i] == "--exclude-session" and i + 1 < len(args):
            exclude_sessions.append(args[i + 1]); i += 2
        elif args[i] == "--include-self-runs":
            include_self_runs = True; i += 1
        elif args[i] == "--any":
            match_any = True; i += 1
        elif args[i] == "--json":
            i += 1  # handled below
        else:
            terms.append(args[i]); i += 1

    if not terms:
        print("Usage: search-sessions.py [--since YYYY-MM-DD] [--limit N] term1 [term2 ...]", file=sys.stderr)
        sys.exit(1)

    since = None
    if since_str:
        since = datetime.strptime(since_str, "%Y-%m-%d")

    as_json = "--json" in sys.argv

    stats = {}
    results = search_sessions(terms, since=since, limit=limit, project_substr=project,
                              match_any=match_any, exclude_sessions=exclude_sessions,
                              include_self_runs=include_self_runs, stats=stats)

    if as_json:
        print(json.dumps(render_payload(results, stats), indent=2))
    else:
        if stats.get("self_runs_skipped"):
            print(f"(skipped {stats['self_runs_skipped']} transcript(s) that are runs of "
                  f"this checker: {', '.join(stats['self_runs_skipped_ids'])})")
            print()
        for r in results:
            terms_str = ", ".join(f"{t}({c})" for t, c in r["term_hits"].items())
            print(f"{r['session_id']}\t{r['date']}\t{r['project']}\t{r['hits']} hits\t{terms_str}")
            print(f"  opening: {r['opening']}")
            print(f"  file: {r['file']}")
            print()


if __name__ == "__main__":
    main()
