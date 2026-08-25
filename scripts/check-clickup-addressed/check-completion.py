#!/usr/bin/env python3
"""Read session transcripts and extract completion signals for given task IDs.

Usage:
    python3 check-completion.py [--session SESSION_ID] [--task TASK_ID] [--json] [--window N]

Without arguments: reads recent-comments.py output, finds sessions for each task,
and checks completion status.

Output: per-task summary with completion signals.

The --window flag controls how many characters around each task ID mention to search
for signals (default: 2000). Signals closer to the task ID mention are weighted higher.
"""
import json, os, re, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "lib"))
from _selfrun import is_self_run  # noqa: E402
from transcript_search import (  # noqa: E402
    DEFAULT_ROOT, SURFACE_TEXT, find_transcript, iter_transcripts, load_records, text_of,
)

# Reassigned by tests to point at a tmp tree. Every walker below reads it at CALL time.
CLAUDE_DIR = DEFAULT_ROOT

# Shape of a ClickUp task ID in this workspace (e.g. 868gy0ddd). Used to detect when a
# signal inside a task's text window actually belongs to a DIFFERENT task — the window is
# ±window_size characters wide, so a triage table listing several tasks puts each task's
# verdict inside every other task's window.
# NOTE: if a workspace ever issues IDs that don't match this, the attribution guard
# degrades to "no rival IDs visible" and keeps the signal — it fails OPEN, not closed.
TASK_ID_RE = re.compile(r"\b868[a-z0-9]{6}\b", re.IGNORECASE)

# Signals that indicate completion
COMPLETION_PATTERNS = [
    (r"#[0-9]+.*(?:merged|landed|shipped|deployed)", "PR merged"),
    (r"(?:merged|landed|shipped|deployed).*#[0-9]+", "PR merged (alt)"),
    (r"verified.*(?:by content|on (?:main|trunk))", "verified on main"),
    (r"\bfix(?:ed)?\b.*(?:shipped|merged|deployed|landed)", "fix shipped"),
    (r"\bresolv(?:ed|es)\b.*(?:by|via|through|in)", "resolved"),
    (r"✅.*(?:merged|shipped|done|complete|fix)", "completion confirmed"),
]

# Signals that indicate work is still open.
#
# "still running" and "still waiting" used to sit in the first alternation. They are
# overwhelmingly PROCESS state, not ticket state — measured 2026-08-19, every one of them
# in a real run was noise ("the throwaway Postgres is still running on port 55432", "CI is
# still running rather than failing", "the mirror agent is still running"). They are kept
# only in the scoped pattern below, which requires a ticket-ish subject. Narrowing the
# pattern is preferred to filtering the matches with a denylist of process nouns: the
# denylist has to enumerate the world, the narrower pattern does not.
OPEN_PATTERNS = [
    (r"\bstill (?:open|pending|unresolved|outstanding|unfixed)\b", "still open"),
    (r"\b(?:ticket|task|issue|it|this)\s+(?:is\s+)?still\s+(?:open|running|waiting|blocked|in progress)\b",
     "still open (ticket-scoped)"),
    (r"\bblocked (?:on|by)\b[^.]{0,45}?\b(?:decision|permission|scope|token|credential|review|human|answer|approval|access)\b",
     "blocked on external"),
    (r"\bleft open\b", "left open"),
    (r"\bnot (?:yet )?(?:done|fixed|deployed|merged|resolved|shipped|complete)\b", "not yet done"),
    (r"\bpremise (?:refuted|wrong|dead)\b", "premise refuted"),
    (r"\bneeds? (?:a |its |to be |deciding|re-measure|your call)", "needs action"),
    (r"\bopen issue\b", "open issue"),
    (r"deployed ≠ verified", "deployed not verified"),
    (r"\bnot (?:yet )?deployed\b", "not deployed yet"),
]


# Repos whose PRs get cited in these transcripts by bare name. A citation naming no repo
# is reported UNRESOLVED rather than guessed — guessing produces a confident wrong state.
#
# 🔴 This is a CLOSED VOCABULARY, and widening it is NOT the fix on its own — exactly as
# with the ClickUp status sets in check-addressed.py, adding entries only moves the silence
# to the next repo nobody thought of. What makes the next miss visible is the MESSAGE:
# a `#N` whose repo word is absent from this table now says so and names the word, rather
# than claiming no repo was named. Measured 2026-08-21 on the live snippet
# `| **#4181**, **devrc #591** | merged, verified by content |`: both refs came back
# "unresolved (repo not named)" although the second one names its repo, so round 2's
# is-this-PR-actually-open cross-check contributed nothing on the only task in the run with
# completion signals — while reporting a cause that was false.
#
# Owners are verified with `gh repo view <owner>/<name>` before being added, never inferred
# from the org that owns the neighbouring entries: `devrc` is innovation-upstream/devrc, and
# civitai/devrc does not exist. The owner is precisely the part a guess gets wrong.
KNOWN_REPOS = {
    "talos-infra": "civitai/talos-infra",
    "civitai": "civitai/civitai",
    "civitai-orchestration": "civitai/civitai-orchestration",
    "civitai-image-cacher": "civitai/civitai-image-cacher",
    "civitai-spine-controller": "civitai/civitai-spine-controller",
    "cli": "civitai/cli",
    "storage-resolver": "civitai/storage-resolver",
    "flipt-state": "civitai/flipt-state",
    "devrc": "innovation-upstream/devrc",
}
# "talos-infra #1065", "civitai#4102", "#1100"
PR_REF_RE = re.compile(r"(?:\b([a-z][a-z0-9-]{2,})\s*)?#(\d{2,6})\b", re.IGNORECASE)

_pr_cache = {}


def resolve_pr(repo, number):
    """Ask GitHub what a cited PR actually is. Returns a short state string.

    A '#1234 merged' signal is otherwise believed on sight — nothing checks the PR exists,
    is merged, or belongs to this ticket. On 2026-08-19 this turned a cited-as-done item
    into an open, unmerged PR (talos-infra #1073).
    """
    key = (repo, number)
    if key in _pr_cache:
        return _pr_cache[key]

    import subprocess
    try:
        r = subprocess.run(
            ["gh", "pr", "view", str(number), "--repo", repo, "--json", "state,mergedAt,title"],
            capture_output=True, text=True, timeout=25,
        )
        if r.returncode != 0:
            out = "not found"
        else:
            d = json.loads(r.stdout)
            out = d.get("state", "?").lower()
            if d.get("mergedAt"):
                out += f" {d['mergedAt'][:10]}"
    except (OSError, ValueError, subprocess.SubprocessError):
        out = "lookup failed"

    _pr_cache[key] = out
    return out


# How far back from a '#' to look for the repo it belongs to. Deliberately short.
# A snippet-WIDE scan was tried first and was badly wrong: "civitai" appears in almost
# every snippet here (org name, sibling repo names, prose), so every bare '#N' resolved
# to civitai/civitai and reported a real but unrelated PR — one citation came back
# "merged 2024-03-18". Same principle as the task-ID attribution guard: nearest wins,
# and out of range means unknown rather than guessed.
REPO_LOOKBEHIND = 30


def _repo_for_ref(clean_snippet, match_start, word, default_repo):
    """Resolve which repo a '#N' belongs to. Returns (repo_or_None, reason_or_None).

    Returning None (-> "unresolved") is deliberate. Guessing yields a confident state for
    the WRONG PR, which is worse than admitting the citation is ambiguous.

    🔴 But there are THREE different ways to fail here, and until 2026-08-21 all three
    rendered as the single string "unresolved (repo not named)" — which is TRUE of only one
    of them. The other two are false explanations, and a false explanation is worse than a
    vague one because it stops the reader looking:

      "not_named"   nothing repo-ish before the '#' and no known repo within
                    REPO_LOOKBEHIND. The refusal to guess is the round-2 fix and stays.
      "unknown"     a word IS sitting on the '#' but is absent from KNOWN_REPOS. 🔴 Round 5
                    reported that word back ("repo 'X' is not in KNOWN_REPOS") on the
                    reasoning that a repo WAS named; round 7 retracted that on measurement —
                    'devrc' and 'landed' are indistinguishable to any rule that does not
                    enumerate the world, and the captured token was an ENGLISH word in 2 of
                    3 live cases. So this reason and "not_named" now RENDER identically. The
                    distinction is still carried as DATA; the render just no longer claims
                    to know what the token means. See `_unresolved_state`.
      "ambiguous"   two or more KNOWN repos in range and none adjacent. Repos were named;
                    the problem is that more than one was. Naming the candidates is what
                    lets the reader disambiguate by eye.

    The reason is data, not prose: annotate_pr_refs renders it. Resolution order matches
    the pre-2026-08-21 control flow exactly, so no citation changes repo — only the text of
    a failure changes, plus the three repos added to KNOWN_REPOS.
    """
    if word and word.lower() in KNOWN_REPOS:
        return KNOWN_REPOS[word.lower()], None

    back = clean_snippet[max(0, match_start - REPO_LOOKBEHIND):match_start]
    named = {KNOWN_REPOS[k] for k in KNOWN_REPOS if re.search(rf"\b{re.escape(k)}\b", back, re.I)}
    if len(named) == 1:
        return next(iter(named)), None

    if default_repo:
        return default_repo, None
    if len(named) > 1:
        return None, ("ambiguous", sorted(named))
    if word:
        return None, ("unknown", word)
    return None, ("not_named", None)


def _unresolved_state(reason):
    """Render a resolution failure as the operator-facing string.

    TWO strings, not three. `ambiguous` keeps its own message because it is the one failure
    whose diagnosis is TRUE. `unknown` and `not_named` render identically, deliberately: the
    tool cannot tell them apart on real input, so claiming to is the defect. Both still name
    KNOWN_REPOS — D6's lesson is that the announcement, not the widened vocabulary, is what
    makes the next miss visible — but as a CONDITIONAL, never as an assertion that a repo was
    named. See the comment in the body for the measurement that reversed round 5.
    """
    kind, detail = reason
    if kind == "ambiguous":
        # The only failure that carries a TRUE diagnosis: repos really were named, and the
        # problem is that more than one was. Naming them lets the reader disambiguate by eye.
        return f"unresolved (ambiguous — {', '.join(detail)} both in range, neither adjacent)"
    # 🔴 "unknown" and "not_named" now render IDENTICALLY, and neither names the captured
    # word. Measured upstream 2026-08-22 on a live run: 2 of 3 cited PRs produced
    # `repo 'their' is not in KNOWN_REPOS` and `repo 'which' is not in KNOWN_REPOS` — the
    # lookbehind had captured the preceding ENGLISH word while the comment plainly named a
    # repo and a number. That message asserts a premise ("a repo was named, and it is spelled
    # 'their'") that is false, and sends the reader on an errand to add an English word to a
    # repo table. Round 5 introduced the split to replace one wrong message with three
    # precise ones; on real input the precise version was wrong more often, because `word` is
    # whatever token precedes the '#' and nothing short of enumerating the world tells
    # `devrc` from `landed`. The affordance survives as a CONDITIONAL — true whether or not a
    # repo was named — instead of an assertion that one was.
    return ("unresolved (could not determine the repo; if one is named here, add it to "
            "KNOWN_REPOS in check-completion.py)")


def annotate_pr_refs(signals, default_repo=None):
    """Attach real PR state to any signal citing a PR number. Mutates and returns."""
    for s in signals:
        # No markdown stripping: the bounded lookbehind already sees past `**`/`` ` ``
        # between a repo name and its '#'. A strip step was tried and every mutation of
        # it survived the suite — it was doing nothing the lookbehind did not.
        clean = s.get("snippet", "")
        refs, seen = [], set()
        for m in PR_REF_RE.finditer(clean):
            word, num = m.group(1), m.group(2)
            if num in seen:
                continue
            seen.add(num)
            repo, reason = _repo_for_ref(clean, m.start(), word, default_repo)
            if not repo:
                refs.append({"ref": f"#{num}", "state": _unresolved_state(reason)})
            else:
                refs.append({"ref": f"{repo}#{num}", "state": resolve_pr(repo, num)})
        if refs:
            s["pr_refs"] = refs
    return signals


def session_path(session_id):
    """Locate a session transcript by ID, or None.

    Resolved through the shared corpus enumerator, so this agrees with the search stage
    on what counts as a session — in particular it will not resolve an id to a
    `subagents/` transcript, which is not resumable and never came from the search.
    """
    return find_transcript(session_id, CLAUDE_DIR)


def load_session_text(session_id):
    """Load all text from a session by ID."""
    path = session_path(session_id)
    return _read_session(path) if path else ""


def _read_session(path):
    """Read user AND assistant text from a transcript.

    This used to read assistant text only, while search-sessions.py matched on user text
    too — so a session could be selected on a user-only mention of the task and then
    report `mentions_found: 0` when read here. It also meant a human writing "this is
    still broken" was invisible to every verdict. The two stages now agree on what a
    session contains.

    🔴 The `json.loads` here used to be UNGUARDED, so one malformed line — the shape a
    transcript still being written naturally has — raised out of the whole run rather
    than being skipped. It now parses through `transcript_search.load_records`, which
    skips the line; that is also the rule the search stage uses, so the two stages cannot
    disagree about which lines a transcript contains.
    """
    texts = []
    for obj in load_records(path):
        if obj.get("type") in ("assistant", "user"):
            body = text_of(obj.get("message", {}), SURFACE_TEXT)
            if body:
                texts.append(body)
    return "\n".join(texts)


def extract_text_windows(text, task_id, window_size=2000):
    """Extract text windows around each EXACT mention of task_id.

    Returns list of (window_text, distance_from_mention, mention_offset) tuples, where
    mention_offset is the index of the task ID *within that window* — the anchor the
    attribution guard measures against.

    Deliberately exact-match only. A 6-character-prefix match used to be added here at a
    nominal distance of 100; it bought nothing (ClickUp always hands us the full ID) and
    fed the window with neighbouring tasks that share a prefix.
    """
    windows = []
    text_lower = text.lower()
    task_lower = task_id.lower()

    start = 0
    while True:
        pos = text_lower.find(task_lower, start)
        if pos == -1:
            break

        window_start = max(0, pos - window_size)
        window_end = min(len(text), pos + len(task_id) + window_size)
        windows.append((text[window_start:window_end], 0, pos - window_start))

        start = pos + len(task_id)

    return windows


def _nearest_task_id(window_text, match_start, match_end, task_id, mention_offset):
    """Return the task ID lexically nearest to a matched signal, lowercased.

    The window is anchored on `task_id` at `mention_offset`, but may contain other task
    IDs. A signal belongs to whichever ID sits closest to it.
    """
    candidates = [(task_id.lower(), mention_offset)]
    for m in TASK_ID_RE.finditer(window_text):
        candidates.append((m.group(0).lower(), m.start()))

    def gap(pos):
        if pos < match_start:
            return match_start - pos
        if pos > match_end:
            return pos - match_end
        return 0

    return min(candidates, key=lambda c: gap(c[1]))[0]


def extract_signals_from_windows(windows, patterns, task_id=None):
    """Find matching patterns in text windows, weighted by proximity.
    
    Returns list of (pattern_desc, matched_snippet, proximity_score) tuples.
    If task_id is provided, signals that mention the task ID get a proximity boost.
    """
    signals = []
    seen = set()  # Deduplicate by (pattern, snippet)

    for window_text, distance, mention_offset in windows:
        for pattern, desc in patterns:
            for m in re.finditer(pattern, window_text, re.IGNORECASE):
                # Attribution guard: a window is wide enough to swallow neighbouring
                # tasks' verdicts. Keep the signal only if THIS task is the nearest ID.
                if task_id and _nearest_task_id(
                    window_text, m.start(), m.end(), task_id, mention_offset
                ) != task_id.lower():
                    continue

                start = max(0, m.start() - 40)
                end = min(len(window_text), m.end() + 40)
                snippet = window_text[start:end].replace("\n", " ").strip()

                # Deduplicate
                key = (desc, snippet[:80])
                if key in seen:
                    continue
                seen.add(key)

                # Proximity score: closer = higher score
                proximity_score = 1.0 / (1.0 + distance)

                # Boost if snippet mentions the task ID
                if task_id and task_id.lower() in snippet.lower():
                    proximity_score *= 1.5

                signals.append((desc, snippet, proximity_score))

    # Sort by proximity score (highest first)
    signals.sort(key=lambda x: x[2], reverse=True)
    return signals


# The score `extract_signals_from_windows` emits is an ADJACENCY weight, not a distance.
# Production windows all carry distance 0, so the only thing that varies is the x1.5 boost
# applied when the task ID falls inside the signal's own +/-40-char snippet. This threshold
# sits between the two values production can emit, so the marker reports that — and only
# that. It used to be `prox > 0.5`, which every production signal cleared, so the report
# printed ● for a signal 2000 characters from the mention exactly as for an adjacent one.
ADJACENCY_MARK = 1.25


def confidence_marker(signal):
    """● = the task ID is inside this signal's own snippet; ○ = merely in the same window.

    ○ is the one to distrust: a +/-2000-char window is wide enough that "merely in the same
    window" can mean a different paragraph about different work.
    """
    return "●" if signal.get("proximity", 0) >= ADJACENCY_MARK else "○"


def check_task(task_id, session_ids=None, window_size=2000, exclude_sessions=None,
               resolve_prs=False, include_self_runs=False):
    """Check completion status for a task across sessions.

    Uses windowed search: only looks for signals within ±window_size characters
    of each task ID mention, and weights signals by proximity.

    `exclude_sessions` drops named sessions before reading them — used to keep the check
    from reading its OWN transcript, in which every task ID under test necessarily appears.

    That covers the CURRENT run only. A PRIOR run of this checker is the same defect one
    day later, and worse: its report prints each task ID beside "likely_addressed" / "✓
    resolved" / "merged", so it scores as strong, close, on-topic evidence. Unless
    `include_self_runs`, any transcript `_selfrun.is_self_run` recognises is dropped too.
    """
    excluded = {s for s in (exclude_sessions or []) if s}
    self_runs_skipped = 0

    if not session_ids:
        # Search for the task ID in all sessions
        session_ids, self_runs_skipped = _find_sessions_for_task(
            task_id, excluded, include_self_runs)
    else:
        session_ids = [s for s in session_ids if s not in excluded]
        if not include_self_runs:
            kept = []
            for sid in session_ids:
                p = session_path(sid)
                if p is not None and is_self_run(p):
                    self_runs_skipped += 1
                else:
                    kept.append(sid)
            session_ids = kept

    all_text = ""
    for sid in session_ids:
        all_text += load_session_text(sid) + "\n"

    if not all_text.strip():
        return {
            "task_id": task_id, "status": "no_sessions_found",
            "sessions_searched": len(session_ids), "mentions_found": 0,
            "self_runs_skipped": self_runs_skipped,
            "completion": [], "open": [],
        }

    # Extract text windows around task ID mentions
    windows = extract_text_windows(all_text, task_id, window_size)

    if not windows:
        # The task ID appears nowhere in the sessions we read, so nothing in them can be
        # attributed to it. There used to be a full-text fallback here that scored every
        # signal in the corpus at a hardcoded proximity of 0.5; because the "close" tier
        # below requires > 0.5, its results could never reach it and always fell through
        # to `completion and open_items` -> "partially_addressed". Any unmatched task in a
        # corpus containing one "#N merged" and one "still running" was labelled
        # partially-addressed by construction, citing other tasks' work as its evidence.
        return {
            "task_id": task_id, "status": "no_mentions_found",
            "sessions_searched": len(session_ids), "mentions_found": 0,
            "self_runs_skipped": self_runs_skipped,
            "completion": [], "open": [],
        }

    completion = extract_signals_from_windows(windows, COMPLETION_PATTERNS, task_id)
    open_items = extract_signals_from_windows(windows, OPEN_PATTERNS, task_id)

    # Convert to output format, limit to top 5
    completion = [{"signal": s, "snippet": n, "proximity": round(p, 2)} for s, n, p in completion[:5]]
    open_items = [{"signal": s, "snippet": n, "proximity": round(p, 2)} for s, n, p in open_items[:5]]

    if resolve_prs:
        annotate_pr_refs(completion)
        annotate_pr_refs(open_items)

    # Status. A "close" tier keyed on `proximity > 0.5` used to sit in front of these four
    # branches, with three further branches behind it. It was UNREACHABLE (2026-08-21):
    # extract_text_windows is the only producer of windows in production and hardcodes
    # distance 0, so proximity is 1.0 — or 1.5 with the adjacency boost — and nothing could
    # ever land at or below 0.5. `close_completion` was therefore always == `completion`,
    # the second tier was dead, and the ●/○ marker it fed printed ● for every signal ever
    # emitted. It is vestigial from the full-text fallback deleted in round 1, which was
    # the only thing that ever set a non-unit proximity (a hardcoded 0.5).
    #
    # Deleting it changes no verdict. The premise is pinned by
    # test_production_windows_are_all_distance_zero and
    # test_every_production_signal_scores_above_the_deleted_threshold — if a producer ever
    # emits a non-zero distance, those go red and this tier needs rebuilding, because
    # signals could then fall below the threshold it existed to catch.
    if completion and not open_items:
        status = "likely_addressed"
    elif completion and open_items:
        status = "partially_addressed"
    elif open_items and not completion:
        status = "open"
    else:
        status = "unclear"

    return {
        "task_id": task_id,
        "status": status,
        "sessions_searched": len(session_ids),
        "mentions_found": len(windows),
        "self_runs_skipped": self_runs_skipped,
        "completion": completion,
        "open": open_items,
    }


def _find_sessions_for_task(task_id, excluded=None, include_self_runs=False):
    """Find session IDs that mention this task ID.

    Returns (session_ids, self_runs_skipped). The count is reported rather than swallowed:
    a guard nobody can see fire is indistinguishable from one wired to nothing.

    🔴 The PREDICATE here is deliberately NOT `search-sessions.py`'s and is not folded
    into it: this is a RAW substring test over the whole JSONL line, so it sees the task
    id wherever it appears — a tool_result, a file path, a metadata field — with no
    ranking and no limit. It is the fallback that runs when no `--session` was supplied,
    and narrowing it to the parsed text surface would silently shrink what the completion
    scan can even look at. What IS shared is the corpus: `iter_transcripts` is the single
    enumerator THESE TWO STAGES USE — not the only `*.jsonl` walk in the repo, see its
    module docstring — so this stage and the search stage cannot disagree about which
    files exist or about excluding the `subagents/` tier.
    """
    excluded = excluded or set()
    sessions = []
    skipped = 0
    for path in iter_transcripts(CLAUDE_DIR, exclude_sessions=excluded):
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    if task_id in line:
                        if not include_self_runs and is_self_run(path):
                            skipped += 1
                        else:
                            sessions.append(path.stem)
                        break
        except OSError:
            continue
    return sessions, skipped


def main():
    as_json = "--json" in sys.argv
    task_ids = []
    session_ids = []
    window_size = 2000
    # Default: never read the transcript we are currently writing. Every task ID under
    # test appears in it by construction, so it is a guaranteed self-match.
    exclude_sessions = [os.environ.get("CLAUDE_CODE_SESSION_ID", "")]
    resolve_prs = False
    include_self_runs = False

    args = [a for a in sys.argv[1:] if a != "--json"]
    i = 0
    while i < len(args):
        if args[i] == "--task" and i + 1 < len(args):
            task_ids.append(args[i + 1]); i += 2
        elif args[i] == "--session" and i + 1 < len(args):
            session_ids.append(args[i + 1]); i += 2
        elif args[i] == "--exclude-session" and i + 1 < len(args):
            exclude_sessions.append(args[i + 1]); i += 2
        elif args[i] == "--include-self":
            # Debug escape hatch: read everything, this checker's own runs included.
            exclude_sessions = []; include_self_runs = True; i += 1
        elif args[i] == "--include-self-runs":
            include_self_runs = True; i += 1
        elif args[i] == "--resolve-prs":
            resolve_prs = True; i += 1
        elif args[i] == "--window" and i + 1 < len(args):
            window_size = int(args[i + 1]); i += 2
        else:
            # Bare argument = task ID
            task_ids.append(args[i]); i += 1

    if not task_ids:
        # Read from stdin (piped from recent-comments.py)
        if not sys.stdin.isatty():
            for line in sys.stdin:
                parts = line.strip().split("\t")
                if parts:
                    task_ids.append(parts[0])
        else:
            print("Usage: check-completion.py --task TASK_ID [--session SESSION_ID] [--window N]", file=sys.stderr)
            sys.exit(1)

    results = []
    for tid in task_ids:
        result = check_task(
            tid, session_ids if session_ids else None, window_size,
            exclude_sessions=exclude_sessions, resolve_prs=resolve_prs,
            include_self_runs=include_self_runs,
        )
        results.append(result)

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"\n=== {r['task_id']} ===")
            print(f"Status: {r['status']} (searched {r['sessions_searched']} sessions, found {r.get('mentions_found', '?')} mentions)")
            if r.get("self_runs_skipped"):
                print(f"  (skipped {r['self_runs_skipped']} transcript(s) that are runs of this checker)")
            if r["completion"]:
                print("Completion signals:  (● task ID is inside the snippet · ○ same window only)")
                for s in r["completion"]:
                    print(f"  {confidence_marker(s)} {s['signal']}: {s['snippet'][:100]}")
            if r["open"]:
                print("Open items:  (● task ID is inside the snippet · ○ same window only)")
                for s in r["open"]:
                    print(f"  {confidence_marker(s)} {s['signal']}: {s['snippet'][:100]}")


if __name__ == "__main__":
    main()
