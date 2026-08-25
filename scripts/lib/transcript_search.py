#!/usr/bin/env python3
"""ONE search over the Claude Code transcript corpus (`~/.claude/projects/**/*.jsonl`).

Two call sites walked this corpus with two hand-written implementations that shared no
code — `scripts/find-session.py` (the `/find-session` skill) and
`scripts/check-clickup-addressed/search-sessions.py` — plus a third, narrower walk in
`check-completion.py::_find_sessions_for_task`. Consolidating them is what made their
disagreements audible. The ones that survive as deliberate per-call-site differences are
named in `search`'s docstring; the ones that were bugs are each pinned by a test that was
watched RED at 324693fd (`scripts/tests/test_transcript_search.py` and
`scripts/check-clickup-addressed/tests/test_shared_walk.py` carry the ledgers).

🔴 THE CORPUS IS NOT FLAT, and the difference is 6x. Measured 2026-08-24 over
`~/.claude/projects`: 792 session transcripts sit one level down
(`<project>/<session-id>.jsonl`) and **4,776 more sit three levels down**
(`<project>/<session-id>/subagents/agent-*.jsonl`). A subagent transcript is not a
session — it cannot be resumed, and attributing a task's work to one is a wrong answer,
not a broader one. So `iter_transcripts` recurses (a flat `glob("*.jsonl")` would miss a
future main transcript stored deeper) and excludes the subagent tier by name. Today the
two policies pick the same 792 files; that agreement is pinned by a test rather than
assumed.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

# Directory names that hold transcripts which are NOT resumable sessions. `subagents/`
# is the measured one (4,776 files). `wf_` is a project-dir prefix carried over from
# find-session.py; zero such dirs exist today, and it is kept because its cost is a
# string compare and its absence would be silent.
EXCLUDED_DIR_NAMES = ("subagents",)
EXCLUDED_DIR_PREFIXES = ("wf_",)

# Search surfaces, narrowest first. Each is a superset of the one before it.
SURFACE_TEXT = "text"                    # assistant/user text blocks only
SURFACE_TOOL_USE = "text+tool_use"       # + the JSON *input* of each tool_use block
SURFACE_ALL = "all"                      # + tool_result content (tool OUTPUT)
SURFACES = (SURFACE_TEXT, SURFACE_TOOL_USE, SURFACE_ALL)

_SURFACE_RANK = {name: i for i, name in enumerate(SURFACES)}

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_LOCAL_STDOUT_RE = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)

SNIPPET_PAD = 50
GENESIS_CHARS = 200


# --------------------------------------------------------------------------- corpus

def is_corpus_member(path, root):
    """Does `path` count as a resumable session transcript? ONE rule, one place.

    Both the full enumeration and the by-id lookup ask this, so they cannot come to
    different answers about the same file — which is the whole failure mode this module
    exists to remove.
    """
    try:
        rel = Path(path).relative_to(root)
    except ValueError:                                      # pragma: no cover - defensive
        return False
    parents = rel.parts[:-1]
    if any(p in EXCLUDED_DIR_NAMES for p in parents):
        return False
    if any(p.startswith(EXCLUDED_DIR_PREFIXES) for p in parents):
        return False
    return True


def iter_transcripts(root=None, exclude_sessions=()):
    """Yield every session transcript under `root`, in sorted (deterministic) order.

    THE ONLY enumerator of the corpus in this repo — `scripts/tests/test_transcript_search.py`
    pins that ledger two-way, so a fourth hand-rolled walk fails the suite. Excluded:
    anything `is_corpus_member` rejects, and any session id in `exclude_sessions`.

    Sorted rather than raw glob order because ranking ties are broken by encounter order,
    and a search that reorders its own output between runs is not reproducible.
    """
    root = Path(root) if root is not None else DEFAULT_ROOT
    exclude = {s for s in (exclude_sessions or ()) if s}
    if not root.exists():
        return
    for path in sorted(root.glob("**/*.jsonl")):
        if not is_corpus_member(path, root):
            continue
        if path.stem in exclude:
            continue
        yield path


def find_transcript(session_id, root=None):
    """The transcript for one session id, or None.

    A TARGETED glob rather than a full enumeration: this is called once per candidate
    session inside a per-task loop, and walking all 5,568 paths each time cost 0.16s a
    call. It applies the same `is_corpus_member` rule, so an id belonging to a
    `subagents/` transcript still resolves to None here exactly as it is absent there.
    """
    root = Path(root) if root is not None else DEFAULT_ROOT
    if not root.exists() or not session_id:
        return None
    for path in sorted(root.glob(f"**/{session_id}.jsonl")):
        if is_corpus_member(path, root):
            return path
    return None


def project_dir_of(path, root=None):
    """The encoded project directory a transcript belongs to (`-home-zach-workspace-devrc`)."""
    root = Path(root) if root is not None else DEFAULT_ROOT
    try:
        rel = Path(path).relative_to(root)
    except ValueError:
        return Path(path).parent.name
    return rel.parts[0] if len(rel.parts) > 1 else ""


def load_records(path):
    """Yield each JSONL record, SKIPPING a malformed LINE rather than the whole file.

    🔴 The two prior implementations disagreed here and one of them was wrong.
    `search-sessions.py` wrapped its whole-file read in
    `except (json.JSONDecodeError, OSError): continue`, so ONE unparseable line
    discarded every message in that transcript — silently, and indistinguishably from
    the session not mentioning the term. `find-session.py` skipped the line. The line is
    right; a truncated tail is the expected shape of a transcript that is still being
    written. Measured 2026-08-24: 0 of 792 files currently carry a malformed line, so
    this fixed a hazard with no live instances — the guard for it is a regression test
    against the code, not a claim that it was firing.
    """
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


# ----------------------------------------------------------------------------- text

def text_of(msg, surface=SURFACE_TEXT):
    """Flatten one message's content blocks into searchable text for `surface`."""
    if not isinstance(msg, dict):
        return str(msg) if msg else ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    rank = _SURFACE_RANK.get(surface, 0)
    out = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            out.append(block.get("text", ""))
        elif btype == "tool_use" and rank >= _SURFACE_RANK[SURFACE_TOOL_USE]:
            inp = block.get("input", {})
            if isinstance(inp, dict):
                out.append(json.dumps(inp))
        elif btype == "tool_result" and rank >= _SURFACE_RANK[SURFACE_ALL]:
            res = block.get("content")
            if isinstance(res, str):
                out.append(res)
            elif isinstance(res, list):
                for sub in res:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        out.append(sub.get("text", ""))
    return "\n".join(out)


def first_user_text(msg):
    """The user's typed text, stripped of command wrappers and injected reminders.

    Kept from find-session.py. `search-sessions.py` used the raw first user message,
    so its `opening` routinely displayed a `<system-reminder>` blob or a `Caveat:`
    preamble instead of what the human typed.
    """
    t = text_of(msg)
    t = _SYSTEM_REMINDER_RE.sub("", t)
    t = _LOCAL_STDOUT_RE.sub("", t)
    cmd = _COMMAND_NAME_RE.search(t)
    if cmd:
        args = _COMMAND_ARGS_RE.search(t)
        return (cmd.group(1).strip() + " " + (args.group(1).strip() if args else "")).strip()
    return t.strip()


# ------------------------------------------------------------------------- searching

def _local_naive(dt):
    """A tz-aware timestamp as a naive LOCAL datetime.

    🔴 find-session.py compared `datetime.fromisoformat(ts).replace(tzinfo=None)` — a
    naive **UTC** value — against `--since` parsed as a naive **LOCAL** midnight. On a
    UTC-0500 host that admitted every session from the previous evening: `--since
    2026-08-24` matched a session whose last message was 2026-08-23 21:00 local, because
    that is 2026-08-24 02:00 UTC. `--since` names a local calendar day, so both sides are
    converted to local before comparing.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def _parse_ts(raw):
    """A transcript timestamp as an AWARE datetime, or None.

    Aware unconditionally — a naive one is read as UTC, which is what the writer means.
    Mixing the two inside one file is what makes `dt < ts_first` raise
    "can't compare offset-naive and offset-aware datetimes", and a transcript needs only
    ONE record with a bare timestamp to hit it. The old code hid that behind a blanket
    `except Exception: pass` that silently dropped every timestamp after the first
    mismatch, leaving the session's date wrong rather than absent.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def scan_transcript(path, terms, patterns, *, surface=SURFACE_TEXT,
                    include_sidechains=False, include_titles=True):
    """Read ONE transcript and return everything both call sites need from it.

    Returns a dict — never None; a session with no matches still carries its metadata,
    and the caller decides. `term_hits` counts OCCURRENCES, not messages (see `search`).
    """
    cwd = ""
    branch = ""
    title = ""
    genesis = ""
    ts_first = ts_last = None
    term_hits = {t: 0 for t in terms}
    snippets = {}

    for rec in load_records(path):
        typ = rec.get("type")
        if typ == "ai-title":
            this_title = rec.get("aiTitle", "") or ""
            if not title:
                title = this_title
            if not include_titles:
                continue
            body = this_title
            role = "title"
        elif typ in ("user", "assistant"):
            if rec.get("isSidechain") and not include_sidechains:
                continue
            if not cwd:
                cwd = rec.get("cwd", "") or ""
            if not branch:
                branch = rec.get("gitBranch", "") or ""
            dt = _parse_ts(rec.get("timestamp"))
            if dt is not None:
                if ts_first is None or dt < ts_first:
                    ts_first = dt
                if ts_last is None or dt > ts_last:
                    ts_last = dt
            msg = rec.get("message") or {}
            is_user = typ == "user" and not rec.get("isMeta")
            if is_user and not genesis:
                candidate = first_user_text(msg)
                if candidate and not candidate.startswith("<") \
                        and not candidate.startswith("Caveat:"):
                    genesis = candidate[:GENESIS_CHARS]
            body = text_of(msg, surface)
            role = "you" if is_user else "claude"
        else:
            continue

        if not body:
            continue
        for term, pat in zip(terms, patterns):
            found = pat.findall(body)
            if not found:
                continue
            term_hits[term] += len(found)
            if term not in snippets:
                m = pat.search(body)
                start, end = max(0, m.start() - SNIPPET_PAD), m.end() + SNIPPET_PAD
                snippets[term] = (role, body[start:end].replace("\n", " ").strip())

    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    last_local = _local_naive(ts_last) if ts_last else mtime
    return {
        "session_id": Path(path).stem,
        "path": str(path),
        "cwd": cwd,
        "branch": branch,
        "title": title,
        "genesis": genesis,
        "opening": genesis or (f"[title] {title}" if title else ""),
        "first": ts_first.isoformat() if ts_first else "",
        "last": ts_last.isoformat() if ts_last else "",
        "last_local": last_local,
        "mtime": mtime,
        "term_hits": term_hits,
        "snippets": snippets,
    }


def compile_terms(terms):
    return [re.compile(re.escape(t), re.I) for t in terms]


def search(terms, *, root=None, match_any=False, since=None, limit=None, project="",
           surface=SURFACE_TEXT, include_sidechains=False, include_titles=True,
           exclude_sessions=(), session_filter=None, stats=None):
    """Rank every transcript matching `terms`.

    Args that encode a DELIBERATE per-call-site difference (both defaults measured):
      surface        find-session defaults to SURFACE_TEXT (a human reading results wants
                     what was said, not what was run); `--all` widens it to SURFACE_ALL.
                     check-clickup-addressed defaults to SURFACE_TOOL_USE — a task id
                     typed into a Bash command IS evidence the task was worked. On the
                     term "drift-check.sh" over the live corpus these differ by 6
                     sessions (45 vs 51), which is why neither default is imposed on the
                     other.
      session_filter callable(path) -> True to DROP. Only ccua passes one (its self-run
                     detector). Applied AFTER term matching, deliberately: it reads the
                     file to EOF and a non-matching file is discarded anyway — testing it
                     first cost 5.6s -> 16.1s over 746 files for the same result set.

    Unified (previously divergent) behaviour:
      - hits count OCCURRENCES of a term, not messages containing it.
      - `--since` compares the session's LAST message timestamp, converted to local,
        falling back to file mtime when a transcript carries no parseable timestamp.
      - `project` is a case-INSENSITIVE substring of `cwd` OR the encoded project dir.

    `stats`, when a dict, receives `sessions_examined` and — if `session_filter` is set —
    `filtered_out` / `filtered_out_ids`. A drop nobody can count is indistinguishable
    from a filter wired to nothing.
    """
    if surface not in _SURFACE_RANK:
        raise ValueError(f"unknown surface {surface!r}; want one of {SURFACES}")
    root = Path(root) if root is not None else DEFAULT_ROOT
    patterns = compile_terms(terms)
    needle = project.lower() if project else ""

    results = []
    filtered_ids = []
    examined = 0

    for path in iter_transcripts(root, exclude_sessions):
        examined += 1
        try:
            rec = scan_transcript(path, terms, patterns, surface=surface,
                                  include_sidechains=include_sidechains,
                                  include_titles=include_titles)
        except OSError:
            continue

        matched_terms = [t for t in terms if rec["term_hits"][t] > 0]
        ok = bool(matched_terms) if match_any else len(matched_terms) == len(terms)
        if not ok:
            continue

        pdir = project_dir_of(path, root)
        if needle and needle not in (rec["cwd"].lower() + " " + pdir.lower()):
            continue
        if since is not None and rec["last_local"] < since:
            continue
        if session_filter is not None and session_filter(path):
            filtered_ids.append(rec["session_id"])
            continue

        rec["project_dir"] = pdir
        rec["matched_terms"] = matched_terms
        rec["term_hits"] = {t: rec["term_hits"][t] for t in matched_terms}
        rec["total_hits"] = sum(rec["term_hits"].values())
        results.append(rec)

    results.sort(key=lambda r: (len(r["matched_terms"]), r["total_hits"], r["last_local"]),
                 reverse=True)

    if stats is not None:
        stats["sessions_examined"] = examined
        if session_filter is not None:
            stats["filtered_out"] = len(filtered_ids)
            stats["filtered_out_ids"] = filtered_ids

    return results if limit is None else results[:limit]
