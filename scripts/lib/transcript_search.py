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

🔴 THE CORPUS IS NOT FLAT, and the difference is 6x. Measured 2026-08-25 over
`~/.claude/projects`: 797 session transcripts sit one level down
(`<project>/<session-id>.jsonl`) and **4,795 more sit three levels down**
(`<project>/<session-id>/subagents/agent-*.jsonl`). A subagent transcript is not a
session — it cannot be resumed, and attributing a task's work to one is a wrong answer,
not a broader one. So `iter_transcripts` recurses (a flat `glob("*.jsonl")` would miss a
future main transcript stored deeper) and excludes the subagent tier by name. Today the
two policies pick the same 797 files; that agreement is pinned by a test rather than
assumed. (These counts DRIFT — 792/4,776 one day, 797/4,795 the next. They are a shape
and a date, not a constant.)

🔴 THIS IS NOT THE ONLY TRANSCRIPT WALK IN THE REPO, and an earlier version of this
docstring said it was. These other production files glob `*.jsonl` under their own roots:

    scripts/collector/claude/_shared.py
    scripts/collector/claude/tailer.py
    scripts/session-analysis/extract_genesis.py
    scripts/session-analysis/extract_user_msgs.py
    scripts/session-analysis/initiative-scan.py
    scripts/session-analysis/recon_cost.py
    scripts/validation/reconcile.py
    scripts/tmux-session-restore.py

There is deliberately NO COUNT in front of that list. The sentence used to open "Six
other subsystems" and then name eight files across four subsystems, and `scripts/README.md`
carried the same "Six" while listing six — omitting `tmux-session-restore.py`, which this
docstring named and the ledger carried. A number in prose is a claim nobody re-derives, so
the LIST is pinned two-way against the ledger instead, in BOTH places, by
`scripts/tests/test_transcript_search.py::test_the_prose_names_every_other_production_walk`.

They were NOT folded in here: each ships on its own deploy path (the collector is copied
to `~/.config/activity-collector/claude/` with no `scripts/lib` beside it) and each wants
a different unit. What IS true is scoped and machine-checked: this module is the only
corpus walk reachable from `scripts/find-session.py` and
`scripts/check-clickup-addressed/`, and every OTHER `*.jsonl` walk site in a git-tracked
Python file — a glob, an `os.walk`, or a bare `iterdir`/`listdir`/`scandir` listing — is
enumerated with its reason AND ITS COUNT by
`scripts/tests/test_transcript_search.py::test_the_jsonl_glob_site_ledger_is_pinned_two_way`,
which fails on a new one. 🔴 Read THAT test's docstring before relying on this: it states
the exact spellings it does and does not see, and "a walk added anywhere fails the suite"
is wider than what it delivers (a non-Python file, a concatenated filename, and an
untracked file are all outside it).
"""
import json
import os
import re
import sys
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

    The ONE enumerator behind `scripts/find-session.py` and
    `scripts/check-clickup-addressed/` — those two tools and their three former private
    walks. It is NOT the only `*.jsonl` glob in the repo (see the module docstring for the
    others, which keep their own deliberately). `scripts/tests/test_transcript_search.py`
    pins BOTH halves two-way: that these callers reach the corpus only through here, and
    that the full set of walk sites repo-wide is the enumerated one — so a hand-rolled walk
    added to any git-tracked Python file fails the suite rather than passing unseen. The
    residual gaps are named in that test's docstring, not glossed over here. Excluded:
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
                    include_sidechains=False):
    """Read ONE transcript and return everything both call sites need from it.

    Returns a dict — never None; a session with no matches still carries its metadata,
    and the caller decides. `term_hits` counts OCCURRENCES, not messages (see `search`).

    `include_sidechains` is a DELIBERATE per-call-site difference, not a preference:
    base `find-session.py` skipped `isSidechain` records and base `search-sessions.py`
    had no such filter, so the two tools pass opposite values to preserve their own
    behaviour. Both branches are exercised by tests — an untested knob whose default
    silently narrows one caller is how this axis was lost once already.

    There is deliberately NO `include_titles` knob. `ai-title` records are searched
    unconditionally, which is the resolution of a disagreement the consolidation found
    (find-session.py never looked at them; search-sessions.py always did, over 493 of the
    797 live transcripts). A knob there would let that decision be reverted silently, and
    it had zero callers and zero coverage on both branches.
    """
    cwd = ""
    branch = ""
    title = ""
    genesis = ""
    ts_first = ts_last = None
    term_hits = {t: 0 for t in terms}
    snippets = {}
    # WHICH SKILLS this session used. Kept OUT of the term surfaces on purpose:
    # skill invocation is not text the session said, and folding it into the
    # keyword surface is what makes `find-session signal` return 666 sessions
    # that merely mention the word. Two signals, neither a superset of the other
    # — see `skills_used` / `commands_typed` in session-tailer.py for the
    # measurement behind that.
    skills_attributed: dict = {}
    commands_typed: dict = {}

    for rec in load_records(path):
        typ = rec.get("type")
        if typ == "ai-title":
            this_title = rec.get("aiTitle", "") or ""
            if not title:
                title = this_title
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
            # `attributionSkill` is a TOP-LEVEL field on the record (a sibling of
            # `message`), and it is the ONLY signal that sees a skill which
            # auto-fired from its description rather than being typed.
            skill = rec.get("attributionSkill")
            if isinstance(skill, str) and skill.strip():
                s = skill.strip()
                skills_attributed[s] = skills_attributed.get(s, 0) + 1
            msg = rec.get("message") or {}
            is_user = typ == "user" and not rec.get("isMeta")
            if is_user and not genesis:
                candidate = first_user_text(msg)
                if candidate and not candidate.startswith("<") \
                        and not candidate.startswith("Caveat:"):
                    genesis = candidate[:GENESIS_CHARS]
            body = text_of(msg, surface)
            role = "you" if is_user else "claude"
            if is_user:
                # Reuses `body` rather than re-flattening the message: this walk
                # runs once PER TASK over the whole corpus inside
                # check-clickup-addressed, so a second text_of() per user record
                # is not free. Under a WIDER surface, re-flatten narrowly
                # instead — `<command-name>` appearing inside quoted TOOL OUTPUT
                # is not this session invoking anything.
                utext = body if surface == SURFACE_TEXT else text_of(msg)
                cmd_m = _COMMAND_NAME_RE.search(utext)
                if cmd_m:
                    cname = cmd_m.group(1).strip().lstrip("/").strip()
                    if cname:
                        commands_typed[cname] = commands_typed.get(cname, 0) + 1
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
        "skills_attributed": skills_attributed,
        "commands_typed": commands_typed,
    }


def session_used_skill(rec, name) -> bool:
    """Did this session use skill `name`, by EITHER route?

    🔴 EXACT match on the skill's identity, never a substring of it. A substring
    predicate is how "which sessions used `signal`?" turns back into the keyword
    search this exists to replace — `sig` would match it, and so would a session
    that merely wrote the word. Compare the ATTRIBUTED NAME, not the prose.
    """
    if not name:
        return False
    want = str(name).strip().lstrip("/").strip().lower()
    if not want:
        return False
    for bag in (rec.get("skills_attributed") or {}, rec.get("commands_typed") or {}):
        for got in bag:
            if str(got).strip().lower() == want:
                return True
    return False


def compile_terms(terms):
    return [re.compile(re.escape(t), re.I) for t in terms]


def search(terms, *, root=None, match_any=False, since=None, limit=None, project="",
           surface=SURFACE_TEXT, include_sidechains=False,
           exclude_sessions=(), session_filter=None, stats=None, skill=""):
    """Rank every transcript matching `terms`.

    Args that encode a DELIBERATE per-call-site difference (both defaults measured):
      surface        find-session defaults to SURFACE_TEXT (a human reading results wants
                     what was said, not what was run); `--all` widens it to SURFACE_ALL.
                     check-clickup-addressed defaults to SURFACE_TOOL_USE — a task id
                     typed into a Bash command IS evidence the task was worked. On the
                     term "drift-check.sh" over the live corpus these differ by 6
                     sessions (45 vs 51), which is why neither default is imposed on the
                     other.
      include_sidechains
                     find-session passes False (base `find-session.py` skipped
                     `isSidechain` records); ccua passes True (base `search-sessions.py`
                     had no such filter). Measured 2026-08-25: **0** of 424,853
                     user/assistant records in the live corpus are `isSidechain`-true,
                     so today the two agree — but the KEY is present in 795 of 797 files,
                     so this is a layout-dependent zero, not an impossible one. The
                     default is False and it is the NARROWER of the two: leaving it
                     unpassed silently narrows a caller, which is exactly how ccua lost
                     this axis for one review round.
      session_filter callable(path) -> True to DROP. Only ccua passes one (its self-run
                     detector). Applied AFTER term matching, deliberately: it reads the
                     file to EOF and a non-matching file is discarded anyway — testing it
                     first cost 5.6s -> 16.1s over 746 files for the same result set.

    Unified (previously divergent) behaviour:
      - hits count OCCURRENCES of a term, not messages containing it.
      - `--since` compares the session's LAST message timestamp, converted to local,
        falling back to file mtime when a transcript carries no parseable timestamp.
      - `project` is a case-INSENSITIVE substring of `cwd` OR the encoded project dir.

    🔴 `since` is applied TWICE, and the cheap half has to come first. The authoritative
    comparison is against the last message timestamp, which costs a full read to EOF; but
    `st_mtime` is an upper bound on that timestamp for a file nobody rewrote, so a file
    whose mtime already precedes `since` cannot pass and is skipped WITHOUT being opened.
    Both base implementations trusted mtime alone for this. Dropping the prefilter during
    the consolidation cost a measured **4.9x** on the live corpus (2026-08-25): 7.67s ->
    1.56s for `--since 2026-08-22`, where 119 of 797 files pass and 678 are skipped
    unopened — once PER TASK inside `check-addressed.py --transcripts`. Measured at a
    second point on the same dimension so the claim carries its own scope: `--since
    2026-08-01` skips only 144 of 797 and the saving falls to ~1.05x. The prefilter is
    worth most exactly where the window is narrow, which is how the tool is used.
    Result sets were diffed with and without it over the live corpus at both dates x four
    terms: 8 of 8 identical, up to n=500.

    `stats`, when a dict, receives `sessions_examined` (files READ TO EOF — a file that
    raised on open counts under `unreadable` and NOT here, so the three are disjoint and
    `sessions_examined + skipped_stale + unreadable` decomposes the whole walk),
    `skipped_stale` (short-circuited by the `since` mtime prefilter), `unreadable` /
    `unreadable_paths` (an OSError mid-walk), and — if `session_filter` is set —
    `filtered_out` / `filtered_out_ids`. A drop nobody can count is indistinguishable
    from a filter wired to nothing. Every one of these is asserted by a test; the
    decomposition itself is asserted too, because a counter nobody sums is a counter
    nobody notices going wrong.
    """
    if surface not in _SURFACE_RANK:
        raise ValueError(f"unknown surface {surface!r}; want one of {SURFACES}")
    # Normalise BEFORE the guard reads it. A whitespace-only `skill` is truthy,
    # so an un-normalised guard let `search([], skill="  ")` through — and every
    # session then failed the predicate, returning an EMPTY result corpus-wide
    # instead of raising. That is the silent zero this whole change exists to
    # remove, reintroduced one line above it.
    skill = str(skill).strip() if skill else ""
    if not terms and not skill:
        # AND over an empty term list is vacuously true, so this would return the ENTIRE
        # corpus ranked by nothing. Neither CLI can reach it (both reject an empty term
        # list first), which is precisely why it needs a guard here rather than there.
        #
        # `skill` is the ONE thing that makes an empty term list meaningful: "which
        # sessions used skill X" is a complete query with no keyword in it, and it is
        # itself a narrowing predicate, so the corpus-wide result the guard exists to
        # prevent cannot arise. A `skill` that matches nothing returns nothing.
        raise ValueError("search() needs at least one term (or a skill); an empty query "
                         "would match every transcript in the corpus")
    root = Path(root) if root is not None else DEFAULT_ROOT
    patterns = compile_terms(terms)
    needle = project.lower() if project else ""

    results = []
    filtered_ids = []
    unreadable_paths = []
    examined = 0
    skipped_stale = 0

    for path in iter_transcripts(root, exclude_sessions):
        try:
            if since is not None and datetime.fromtimestamp(os.path.getmtime(path)) < since:
                skipped_stale += 1
                continue
            rec = scan_transcript(path, terms, patterns, surface=surface,
                                  include_sidechains=include_sidechains)
            # 🔴 AFTER the read, not before. Incremented first, `sessions_examined`
            # counted a file it never opened: 1 good transcript + 1 unreadable one
            # reported `{'sessions_examined': 2, 'unreadable': 1}`, i.e. two files read
            # where one was. The two counters were then not disjoint, so
            # examined + skipped_stale + unreadable no longer decomposed the walk. The
            # docstring said "files actually READ" the whole time; the code did not,
            # and no assertion pinned it. Pinned now by
            # test_an_unreadable_transcript_is_counted_and_named_not_silently_dropped.
            examined += 1
        except OSError as e:
            # NOT silent, and NOT uncounted. Base find-session.py printed this line; the
            # consolidation dropped it, leaving a transcript that vanishes from every
            # answer with nothing to distinguish it from one that simply did not match.
            unreadable_paths.append(str(path))
            print(f"ERR {path}: {e}", file=sys.stderr)
            continue

        # The skill predicate ANDs with the terms — it NARROWS, never widens. So
        # `--skill signal` alone answers "which sessions used it", and adding a
        # term asks "…and mentioned X". Applied before the term test because it
        # is the cheaper and far more selective of the two.
        if skill and not session_used_skill(rec, skill):
            continue

        matched_terms = [t for t in terms if rec["term_hits"][t] > 0]
        if not terms:
            # A skill-only query. `match_any` over an empty term list is False,
            # not vacuously true, so without this the `--skill X --any`
            # combination would return NOTHING — a silent empty result that
            # reads exactly like "the skill was never used".
            ok = True
        else:
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
        stats["skipped_stale"] = skipped_stale
        stats["unreadable"] = len(unreadable_paths)
        stats["unreadable_paths"] = unreadable_paths
        if session_filter is not None:
            stats["filtered_out"] = len(filtered_ids)
            stats["filtered_out_ids"] = filtered_ids

    return results if limit is None else results[:limit]
