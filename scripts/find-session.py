#!/usr/bin/env python3
"""Find past Claude Code sessions by keyword.

Searches ~/.claude/projects/**/*.jsonl (one file per session, named <sessionId>.jsonl)
for query terms across user-typed and assistant text, ranks matches by relevance +
recency, and prints each hit with its project, date, branch, genesis message, and the
best matching snippets — plus how to resume it.

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
import json, os, sys, glob, re, argparse
from datetime import datetime

ROOT = os.path.expanduser("~/.claude/projects")


def parse_args():
    p = argparse.ArgumentParser(add_help=True, description="Find past Claude Code sessions by keyword.")
    p.add_argument("terms", nargs="+", help="search terms (ANDed unless --any)")
    p.add_argument("--project", default="", help="only sessions whose cwd/project contains this substring")
    p.add_argument("--since", default="", help="only sessions on/after this date (YYYY-MM-DD)")
    p.add_argument("--limit", type=int, default=10, help="max sessions to show (default 10)")
    p.add_argument("--any", action="store_true", help="match ANY term instead of all")
    p.add_argument("--all", action="store_true", help="search all roles incl. tool output (noisier)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of human text")
    return p.parse_args()


def text_of(msg):
    """Flatten a message's content blocks to searchable text."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
    return "\n".join(out)


def first_user_text(msg):
    """Genesis: the user's typed text, stripped of command wrappers/reminders."""
    t = text_of(msg)
    t = re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S)
    t = re.sub(r"<local-command-stdout>.*?</local-command-stdout>", "", t, flags=re.S)
    cmd = re.search(r"<command-name>(.*?)</command-name>", t, re.S)
    if cmd:
        args = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
        return (cmd.group(1).strip() + " " + (args.group(1).strip() if args else "")).strip()
    return t.strip()


def main():
    a = parse_args()
    pats = [re.compile(re.escape(t), re.I) for t in a.terms]
    since = None
    if a.since:
        try:
            since = datetime.fromisoformat(a.since)
        except ValueError:
            print(f"bad --since date: {a.since!r} (want YYYY-MM-DD)", file=sys.stderr)
            sys.exit(2)

    results = []
    for path in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
        project_dir = os.path.basename(os.path.dirname(path))
        if project_dir == "subagents" or project_dir.startswith("wf_"):
            continue
        session_id = os.path.splitext(os.path.basename(path))[0]
        cwd = ""
        branch = ""
        ts_first = ts_last = None
        genesis = ""
        # per-term: count + best snippet
        term_hits = {t: 0 for t in a.terms}
        snippets = {}  # term -> (role, snippet)
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    typ = o.get("type")
                    if typ not in ("user", "assistant"):
                        continue
                    if o.get("isSidechain"):
                        continue
                    if not cwd:
                        cwd = o.get("cwd", "")
                    if not branch:
                        branch = o.get("gitBranch", "")
                    ts = o.get("timestamp")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if ts_first is None or dt < ts_first:
                                ts_first = dt
                            if ts_last is None or dt > ts_last:
                                ts_last = dt
                        except Exception:
                            pass
                    msg = o.get("message") or {}
                    is_user = typ == "user" and not o.get("isMeta")
                    if is_user and not genesis:
                        g = first_user_text(msg)
                        if g and not g.startswith("<") and not g.startswith("Caveat:"):
                            genesis = g[:200]
                    # restrict search surface unless --all
                    if not a.all and typ not in ("user", "assistant"):
                        continue
                    body = text_of(msg)
                    if not body:
                        continue
                    for term, pat in zip(a.terms, pats):
                        m = pat.search(body)
                        if m:
                            term_hits[term] += 1
                            if term not in snippets:
                                s, e = max(0, m.start() - 50), m.end() + 50
                                snip = body[s:e].replace("\n", " ").strip()
                                snippets[term] = ("you" if is_user else "claude", snip)
        except Exception as e:
            print(f"ERR {path}: {e}", file=sys.stderr)
            continue

        matched_terms = [t for t in a.terms if term_hits[t] > 0]
        ok = bool(matched_terms) if a.any else (len(matched_terms) == len(a.terms))
        if not ok:
            continue
        if a.project and a.project.lower() not in (cwd.lower() + " " + project_dir.lower()):
            continue
        if since and ts_last and ts_last.replace(tzinfo=None) < since:
            continue

        total = sum(term_hits.values())
        results.append({
            "session_id": session_id,
            "project": os.path.basename(cwd) or project_dir,
            "cwd": cwd,
            "branch": branch,
            "first": ts_first.isoformat() if ts_first else "",
            "last": ts_last.isoformat() if ts_last else "",
            "genesis": genesis,
            "matched_terms": matched_terms,
            "total_hits": total,
            "snippets": snippets,
            "path": path,
        })

    # rank: more distinct terms matched, then more hits, then more recent
    results.sort(key=lambda r: (len(r["matched_terms"]), r["total_hits"], r["last"]), reverse=True)
    shown = results[: a.limit]

    if a.json:
        print(json.dumps(shown, indent=2))
        return

    if not results:
        print(f"No sessions matched: {' '.join(a.terms)}")
        return

    print(f"{len(results)} session(s) matched {' '.join(a.terms)!r}"
          + (f" (showing {len(shown)})" if len(shown) < len(results) else "") + "\n")
    for i, r in enumerate(shown, 1):
        date = (r["last"] or r["first"])[:16].replace("T", " ")
        print(f"{i}. [{date}] {r['project']}  ({r['branch'] or 'no-branch'})  ·  {r['total_hits']} hits")
        if r["genesis"]:
            print(f"   opened: {r['genesis'][:120]!r}")
        for term, (role, snip) in r["snippets"].items():
            print(f"   {term} → ({role}) …{snip[:120]}…")
        print(f"   resume: claude --resume {r['session_id']}")
        print(f"   file:   {r['path']}")
        print()


if __name__ == "__main__":
    main()
