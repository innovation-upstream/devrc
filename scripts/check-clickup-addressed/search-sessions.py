#!/usr/bin/env python3
"""Search Claude Code session transcripts for keywords, return ranked matches.

Usage:
    python3 search-sessions.py [--since YYYY-MM-DD] [--limit N] [--project SUBSTR] term1 [term2 ...]

Output (default): one line per match, tab-separated:
    session_id\tdate\tproject\thits\topening_snippet

With --json: an OBJECT — {"sessions": [...], "self_runs_skipped": N,
"self_runs_skipped_ids": [...], "unreadable": N, "unreadable_paths": [...],
"skipped_stale": N, "sessions_examined": N}. It was a bare array until
2026-08-21; every count has to travel with the results or the corresponding
drop is invisible to the caller, which sees only a shorter list. The four
after the self-run pair were added 2026-08-25: they reached a human through
stderr alone, and `check-addressed.py` does not read stderr. Consumers use
`.get()`, so an added key is not a breaking change — `parse_search_payload`
names the three it wants.

Terms are ANDed by default (session must match all). Add --any to OR them.

🔴 THE WALK IS NOT LOCAL TO THIS FILE. Corpus enumeration, JSONL parsing, ranking,
`--since` and `--project` live in `scripts/lib/transcript_search.py`, shared with
`scripts/find-session.py`. Only two things are this tool's own and stay here: the
self-run drop (`_selfrun.py`) and the output shape `check-addressed.py` parses.

Two SEARCH AXES are passed explicitly below rather than inherited, because this tool and
`find-session.py` genuinely want opposite values and the library default is the other
tool's: `surface=SURFACE_TOOL_USE` (a task id in a Bash command is evidence) and
`include_sidechains=True` (base behaviour here; base find-session.py dropped them).
"""
import json, sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "lib"))
from _selfrun import is_self_run  # noqa: E402
from transcript_search import (  # noqa: E402
    DEFAULT_ROOT, SURFACE_TOOL_USE, load_records, search, text_of,
)

# Reassigned by tests to point at a tmp tree. Read at CALL time, never captured at
# import time, so a test that swaps it after import is actually honoured.
CLAUDE_DIR = DEFAULT_ROOT


def load_session(path):
    """Load a session JSONL, return list of (type, content) tuples.

    A thin adapter over `transcript_search.load_records` / `text_of` — the parsing rules
    are shared, this only pins the tuple shape this tool's tests were written against.
    """
    entries = []
    for obj in load_records(path):
        etype = obj.get("type", "")
        if etype in ("user", "assistant"):
            content = text_of(obj.get("message", {}), SURFACE_TOOL_USE)
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

    Pass `stats` (a dict) to learn how many were dropped and why. It gets this tool's own
    `self_runs_skipped` / `self_runs_skipped_ids`, and the shared walk's `unreadable` /
    `unreadable_paths` / `skipped_stale` / `sessions_examined`. None of these are returned
    in the result list because every one of the drops is otherwise INVISIBLE: the caller
    just sees a shorter list and a smaller "N found" header, which is indistinguishable
    from the sessions not existing.
    """
    inner = {}
    hits = search(
        terms,
        root=CLAUDE_DIR,
        match_any=match_any,
        since=since,
        limit=limit,
        project=project_substr or "",
        # A task id typed into a Bash command is evidence the task was worked, so this
        # tool searches tool INPUT as well as prose. find-session.py deliberately does
        # not — see transcript_search.search's docstring for the measured difference.
        surface=SURFACE_TOOL_USE,
        # 🔴 EXPLICIT, and not the library default. The shared `search` defaults to
        # DROPPING `isSidechain` records because that is what base `find-session.py` did;
        # base `search-sessions.py` had no such filter, so taking the default here would
        # silently narrow THIS tool's evidence surface. Measured 2026-08-25: 0 of 424,853
        # user/assistant records in the live corpus are sidechain-true, so the value
        # changes no verdict today — but the key is present in 795 of 797 transcripts, so
        # it is a layout-dependent zero. A completion check that quietly stops reading a
        # class of message is the failure this skill exists to avoid.
        include_sidechains=True,
        exclude_sessions=exclude_sessions or (),
        # Applied AFTER term matching, deliberately. is_self_run reads the file to EOF and
        # a non-matching file is discarded anyway, so testing it first only bought a full
        # extra read of every transcript in the corpus: measured 5.6s -> 16.1s over 746
        # files, once PER TASK. Same result set, 2.8x the cost.
        session_filter=None if include_self_runs else is_self_run,
        stats=inner,
    )

    if stats is not None:
        stats["self_runs_skipped"] = inner.get("filtered_out", 0)
        stats["self_runs_skipped_ids"] = inner.get("filtered_out_ids", [])
        # 🔴 The OTHER two ways a transcript leaves the walk. The self-run drop travelled
        # to the caller and these did not, so `--json` reported the one drop this tool
        # performs and stayed silent about the two the shared walk performs — reaching a
        # human through stderr only, which `check-addressed.py` does not read. All three
        # are the same failure mode: a shorter list, indistinguishable from the sessions
        # not existing. `sessions_examined` travels with them as the denominator; a raw
        # "3 unreadable" out of an unstated total is not a number anyone can act on.
        stats["unreadable"] = inner.get("unreadable", 0)
        stats["unreadable_paths"] = inner.get("unreadable_paths", [])
        stats["skipped_stale"] = inner.get("skipped_stale", 0)
        stats["sessions_examined"] = inner.get("sessions_examined", 0)

    return [{
        "session_id": h["session_id"],
        "date": h["last_local"].strftime("%Y-%m-%d %H:%M"),
        "project": h["project_dir"].replace("-home-zach-workspace-", "").replace("-", "/"),
        "hits": h["total_hits"],
        "term_hits": h["term_hits"],
        "opening": h["opening"][:120],
        "file": h["path"],
    } for h in hits]


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
