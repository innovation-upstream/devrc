#!/usr/bin/env python3
"""Find past Claude Code sessions by keyword.

Searches ~/.claude/projects/**/*.jsonl (one file per session, named <sessionId>.jsonl)
for query terms across user-typed and assistant text, ranks matches by relevance +
recency, and prints each hit with its project, date, branch, genesis message, and the
best matching snippets — plus how to resume it.

The walk, the ranking and the snippet extraction live in `scripts/lib/transcript_search.py`
and are shared with `scripts/check-clickup-addressed/`. This file is the CLI only.

Usage:
  find-session.py <term> [<term> ...] [--project SUBSTR] [--since YYYY-MM-DD]
                  [--limit N] [--all] [--json]

  Terms are ANDed by default (a session must match all). Pass --any to OR them.
  Quote a multi-word term to match it as a phrase: find-session.py "pr 235"

Examples:
  find-session.py redis vpn            # sessions mentioning both redis AND vpn
  find-session.py "pr 235"             # the session where PR 235 was worked
  find-session.py minio --project talos --since 2026-05-01
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from transcript_search import (  # noqa: E402
    DEFAULT_ROOT, SURFACE_ALL, SURFACE_TEXT, search,
)
from opencode_search import search_opencode  # noqa: E402

# Reassigned by tests to point at a tmp corpus. Read at CALL time, never captured.
ROOT = DEFAULT_ROOT


def parse_args(argv=None):
    p = argparse.ArgumentParser(add_help=True, description="Find past Claude Code and opencode sessions by keyword.")
    p.add_argument("terms", nargs="*", help="search terms (ANDed unless --any)")
    p.add_argument("--skill", default="",
                   help="only sessions that USED this skill (exact name). Reads the "
                        "skill attribution on each record, so it sees a skill that "
                        "auto-fired as well as one typed as /name — neither of which "
                        "a keyword search can distinguish from prose. May be used "
                        "alone, with no search terms. COUNTS SESSIONS: a skill used "
                        "only inside a dispatched SUBAGENT is not counted, because "
                        "subagent transcripts are not part of this corpus (measured "
                        "2026-08-29: `activity` had 15 such transcripts against 8 "
                        "sessions, `browser` 8 against 50).")
    p.add_argument("--project", default="", help="only sessions whose cwd/project contains this substring")
    p.add_argument("--since", default="", help="only sessions on/after this date (YYYY-MM-DD)")
    p.add_argument("--limit", type=int, default=10, help="max sessions to show (default 10)")
    p.add_argument("--any", action="store_true", help="match ANY term instead of all")
    p.add_argument("--all", action="store_true",
                   help="widen the search surface to tool inputs AND tool output (noisier)")
    p.add_argument("--claude-only", action="store_true",
                   help="search only Claude Code transcripts (skip opencode)")
    p.add_argument("--opencode-only", action="store_true",
                   help="search only opencode sessions (skip Claude Code)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of human text")
    return p.parse_args(argv)


def render(r):
    """The JSON document. Datetimes are dropped; every field here is a string or a number."""
    d = {
        "session_id": r["session_id"],
        "project": os.path.basename(r["cwd"]) or r["project_dir"],
        "cwd": r["cwd"],
        "branch": r["branch"],
        "first": r["first"],
        "last": r["last"],
        "genesis": r["genesis"],
        "matched_terms": r["matched_terms"],
        "total_hits": r["total_hits"],
        "snippets": r["snippets"],
        "path": r["path"],
    }
    if r.get("source"):
        d["source"] = r["source"]
    return d


def main(argv=None):
    a = parse_args(argv)
    since = None
    if a.since:
        try:
            since = datetime.fromisoformat(a.since)
        except ValueError:
            print(f"bad --since date: {a.since!r} (want YYYY-MM-DD)", file=sys.stderr)
            sys.exit(2)

    # 🔴 `--all` used to be INERT. Its handler sat behind `if not a.all and typ not in
    # ("user", "assistant")`, twenty lines after an unconditional `if typ not in
    # ("user", "assistant"): continue` had already skipped everything it could have
    # admitted — so the flag the SKILL.md advertises for "tool output" widened nothing.
    # It now selects the search surface, which is the only thing it ever meant.
    surface = SURFACE_ALL if a.all else SURFACE_TEXT

    if not a.terms and not a.skill:
        print("nothing to search for: give at least one term, or --skill NAME",
              file=sys.stderr)
        sys.exit(2)

    # 🔴 `--skill` reads Claude Code's per-record skill attribution, which the
    # opencode corpus does not have — there, a skill invocation is a tool CALL
    # named `skill`, a different shape this search does not yet read. So the
    # opencode leg is SKIPPED under --skill and the omission is PRINTED. Running
    # it unfiltered would fold in sessions selected on terms alone, quietly
    # answering a different question; skipping it silently would hand back a
    # partial count that reads as the whole fleet. Both are the failure this
    # flag exists to end.
    skill_skips_opencode = bool(a.skill) and not a.claude_only
    if a.skill and a.opencode_only:
        print("--skill cannot be answered from the opencode corpus (no per-record "
              "skill attribution there); drop --opencode-only", file=sys.stderr)
        sys.exit(2)

    results = []

    # Search Claude Code transcripts (default)
    if not a.opencode_only:
        cc_results = search(a.terms, root=ROOT, match_any=a.any, since=since,
                            project=a.project, surface=surface, limit=None,
                            skill=a.skill)
        results.extend(cc_results)

    # Search opencode sessions (default)
    if not a.claude_only and not a.skill:
        try:
            oc_results = search_opencode(a.terms, match_any=a.any, since=since,
                                         project=a.project, limit=None)
            results.extend(oc_results)
        except Exception as e:
            print(f"WARN: opencode search failed: {e}", file=sys.stderr)

    # Re-rank the merged set by the same criteria
    results.sort(key=lambda r: (len(r["matched_terms"]), r["total_hits"], r["last_local"]),
                 reverse=True)

    shown = results[: a.limit]

    if a.json:
        print(json.dumps([render(r) for r in shown], indent=2))
        return 0

    label = " ".join(a.terms)
    if a.skill:
        label = (f"skill={a.skill}" + (f" + {label}" if label else "")).strip()

    # Printed BEFORE the count, and on the empty path too: a zero has to arrive
    # carrying the scope it was measured over.
    if skill_skips_opencode:
        print("NOT searched: the opencode corpus (--skill has no attribution there); "
              "these results are Claude Code sessions on THIS host only.\n")

    if not results:
        print(f"No sessions matched: {label}")
        return 0

    print(f"{len(results)} session(s) matched {label!r}"
          + (f" (showing {len(shown)})" if len(shown) < len(results) else "") + "\n")
    for i, r in enumerate(shown, 1):
        date = (r["last"] or r["first"])[:16].replace("T", " ")
        project = os.path.basename(r["cwd"]) or r["project_dir"]
        source_tag = f"  [{r['source']}]" if r.get("source") else ""
        print(f"{i}. [{date}] {project}  ({r['branch'] or 'no-branch'}){source_tag}  ·  {r['total_hits']} hits")
        if r["genesis"]:
            print(f"   opened: {r['genesis'][:120]!r}")
        for term, (role, snip) in r["snippets"].items():
            print(f"   {term} → ({role}) …{snip[:120]}…")
        if r.get("source") == "opencode":
            print(f"   resume: opencode --session {r['session_id']}")
        else:
            print(f"   resume: claude --resume {r['session_id']}")
        print(f"   file:   {r['path']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
