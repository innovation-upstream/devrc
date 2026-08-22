#!/usr/bin/env python3
"""Regression tests for the three defects that made this checker report confident
false positives (2026-08-19).

Each test below was watched failing against the pre-fix scripts; the matrix is in
`claude/skills/check-clickup-addressed/reference/validation-history.md`. Tests marked INVARIANT
GUARD passed at base too and are labelled as such — they are controls, not coverage.

  D1  a task ID that appears in NO session fell through to a full-text scan over the
      whole corpus at a hardcoded proximity of 0.5, which the "close" tier (> 0.5) can
      never accept, so every such task landed on "partially_addressed" citing other
      tasks' signals.
  D2  a +/-2000-char window swallows neighbouring rows of a multi-task triage table, so
      one task's "#N merged" was reported as another task's completion signal.
  D3  the checker read the transcript it was being written into, which mentions every
      task ID under test by construction.
"""
import json, sys, tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import importlib.util
spec = importlib.util.spec_from_file_location("check_completion", SCRIPT_DIR / "check-completion.py")
check_completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_completion)

spec2 = importlib.util.spec_from_file_location("search_sessions", SCRIPT_DIR / "search-sessions.py")
search_sessions = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(search_sessions)

spec3 = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(check_addressed)

import _selfrun as selfrun  # noqa: E402  (SCRIPT_DIR is on sys.path above)

TARGET = "868krn3y1"   # the task under test
RIVAL = "868kr07fu"    # a different task whose verdict sits in the same window


def _session(tmpdir, session_id, *texts):
    """Write a mock session transcript and point CLAUDE_DIR at it."""
    mock_dir = Path(tmpdir) / ".claude" / "projects" / "test-project"
    mock_dir.mkdir(parents=True, exist_ok=True)
    with open(mock_dir / f"{session_id}.jsonl", "w") as f:
        for t in texts:
            f.write(json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}
            ) + "\n")
    return mock_dir.parent


# --------------------------------------------------------------------------- D1

def test_no_mentions_is_not_partially_addressed():
    """D1: a task mentioned nowhere must not borrow another task's signals.

    Pre-fix this returned "partially_addressed" with 5 completion + 5 open signals, none
    of which mentioned the task. The corpus below deliberately contains one completion
    phrase and one open phrase so the old fallback had something to find.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "s1", "PR #348 merged. The mirror agent is still running.")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task("868kt8pfu", session_ids=["s1"])
        finally:
            check_completion.CLAUDE_DIR = orig

    assert r["status"] == "no_mentions_found", \
        f"unmentioned task must report no_mentions_found, got {r['status']!r}"
    assert r["completion"] == [], f"must cite no completion signals, got {r['completion']}"
    assert r["open"] == [], f"must cite no open signals, got {r['open']}"
    assert r["mentions_found"] == 0


def test_no_mentions_positive_control():
    """Control for the test above: the SAME corpus, with the task ID present, must still
    produce signals. Without this, D1's fix could pass by returning nothing for
    everything."""
    with tempfile.TemporaryDirectory() as tmp:
        # NB: the open phrase here must be TICKET state. An earlier version of this
        # fixture said "the mirror agent is still running", which the tightened
        # OPEN_PATTERNS correctly no longer match — the control was asserting on noise.
        root = _session(tmp, "s1", "868kt8pfu: PR #348 merged. The ticket is still open.")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task("868kt8pfu", session_ids=["s1"])
        finally:
            check_completion.CLAUDE_DIR = orig

    assert r["mentions_found"] == 1
    assert r["completion"], "positive control found no completion signal — guard is inert"
    assert r["open"], "positive control found no open signal — guard is inert"
    assert r["status"] == "partially_addressed"


# --------------------------------------------------------------------------- D2

def test_rival_task_signal_is_not_attributed_to_target():
    """D2: a signal sitting next to a DIFFERENT task's ID must not count for this one.

    Shaped like the real triage table that produced the bug: two rows, each a task ID
    followed by its own verdict. Asking about TARGET must not return RIVAL's merge.
    """
    window = (
        f"| {RIVAL} | talos-infra #1065 merged 08-16, root cause fixed |\n"
        f"| {TARGET} | no work recorded yet |\n"
    )
    offset = window.index(TARGET)
    signals = check_completion.extract_signals_from_windows(
        [(window, 0, offset)], check_completion.COMPLETION_PATTERNS, TARGET
    )
    assert signals == [], (
        "a completion signal adjacent to a rival task ID was attributed to the target: "
        f"{[s[1] for s in signals]}"
    )


def test_own_signal_survives_the_attribution_guard():
    """Positive control for D2 — the guard must not simply drop everything. Same table
    shape, but now we ask about the task the merge actually belongs to."""
    window = (
        f"| {RIVAL} | talos-infra #1065 merged 08-16, root cause fixed |\n"
        f"| {TARGET} | no work recorded yet |\n"
    )
    offset = window.index(RIVAL)
    signals = check_completion.extract_signals_from_windows(
        [(window, 0, offset)], check_completion.COMPLETION_PATTERNS, RIVAL
    )
    assert signals, "the guard dropped a signal that genuinely belongs to this task"
    assert any("1065" in s[1] for s in signals)


def test_nearest_task_id_picks_the_closer_of_two():
    """Unit-level check on the discriminator itself, at both ends: the same window, the
    same two IDs, a signal placed near each in turn."""
    w = f"{RIVAL} shipped it. Much later in the row, {TARGET} is untouched."
    near_rival = w.index("shipped")
    assert check_completion._nearest_task_id(w, near_rival, near_rival + 7, TARGET, w.index(TARGET)) == RIVAL
    near_target = w.index("untouched")
    assert check_completion._nearest_task_id(w, near_target, near_target + 9, TARGET, w.index(TARGET)) == TARGET


# --------------------------------------------------------------------------- D3

def test_excluded_session_is_not_read():
    """D3: the check must be able to skip its own transcript."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "self-sess", f"{TARGET} PR #999 merged and verified on main.")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            included = check_completion.check_task(TARGET, session_ids=["self-sess"])
            excluded = check_completion.check_task(
                TARGET, session_ids=["self-sess"], exclude_sessions=["self-sess"]
            )
        finally:
            check_completion.CLAUDE_DIR = orig

    assert included["completion"], "positive control: the session should match when included"
    assert excluded["status"] == "no_sessions_found", \
        f"excluded session was still read, got {excluded['status']!r}"
    assert excluded["completion"] == []


def test_search_sessions_honours_exclude():
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "self-sess", f"working on {TARGET}")
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            found = search_sessions.search_sessions([TARGET])
            hidden = search_sessions.search_sessions([TARGET], exclude_sessions=["self-sess"])
        finally:
            search_sessions.CLAUDE_DIR = orig

    assert len(found) == 1, "positive control: the session must be findable when not excluded"
    assert hidden == [], f"excluded session still returned: {hidden}"


# --------------------------------------------------------------------------- shape

def test_windows_carry_their_mention_offset():
    """The attribution guard needs to know where in the window the task ID sits."""
    text = f"padding {TARGET} trailing"
    windows = check_completion.extract_text_windows(text, TARGET, window_size=50)
    assert len(windows) == 1
    win, dist, offset = windows[0]
    assert win[offset:offset + len(TARGET)] == TARGET, \
        "mention_offset does not point at the task ID inside the window"


def test_prefix_matching_does_not_invent_windows():
    """A different task sharing the first 6 characters must not create a window.

    868kr07fu and 868kr0zzz share "868kr0"; the old prefix pass matched on that.
    """
    text = "868kr0zzz is a completely different task."
    assert check_completion.extract_text_windows(text, RIVAL, window_size=50) == []


# ----------------------------------------------------------------- signal precision

def test_process_state_is_not_a_ticket_open_signal():
    """"still running" about a process is not a statement about the ticket.

    Every real-run match of the old bare `still (open|pending|waiting|running|needed)`
    alternation was process state, not ticket state (measured 2026-08-19).
    """
    noise = [
        "the throwaway Postgres is still running on port 55432",
        "CI is still running rather than failing",
        "The mirror agent is still running; I'll report when it lands",
    ]
    for text in noise:
        window = f"{TARGET} {text}"
        hits = check_completion.extract_signals_from_windows(
            [(window, 0, 0)], check_completion.OPEN_PATTERNS, TARGET
        )
        assert hits == [], f"process noise scored as an open signal: {text!r} -> {hits}"


def test_ticket_scoped_open_signal_still_fires():
    """Positive control for the tightening: real ticket-state phrasing must survive."""
    for text in ["this ticket is still open", "the task is still blocked", "still unresolved"]:
        window = f"{TARGET} {text}"
        hits = check_completion.extract_signals_from_windows(
            [(window, 0, 0)], check_completion.OPEN_PATTERNS, TARGET
        )
        assert hits, f"real open signal was dropped by the tightening: {text!r}"


def test_blocked_on_credential_is_detected():
    """The 868kt8pfu shape: work finished, landing blocked on a permission."""
    window = f"{TARGET} no PR was opened, blocked on the workflow scope for the token"
    hits = check_completion.extract_signals_from_windows(
        [(window, 0, 0)], check_completion.OPEN_PATTERNS, TARGET
    )
    assert any("blocked" in h[0] for h in hits), f"blocked-on-permission not detected: {hits}"


# ----------------------------------------------------------------- PR resolution

def test_pr_ref_without_a_repo_is_not_guessed():
    sig = [{"signal": "PR merged", "snippet": "PR #1100 merged, verified by content"}]
    check_completion.annotate_pr_refs(sig, default_repo=None)
    assert sig[0]["pr_refs"] == [{"ref": "#1100", "state": "unresolved (repo not named)"}], \
        f"a repo-less PR reference was guessed: {sig[0].get('pr_refs')}"


def test_pr_ref_with_named_repo_resolves(monkeypatched=None):
    """Uses a stubbed resolver so the test stays hermetic (no network)."""
    calls = []
    orig = check_completion.resolve_pr
    check_completion.resolve_pr = lambda repo, num: calls.append((repo, num)) or "merged 2026-08-19"
    try:
        sig = [{"signal": "PR merged", "snippet": "talos-infra #1065 merged 08-16"}]
        check_completion.annotate_pr_refs(sig)
    finally:
        check_completion.resolve_pr = orig
    assert calls == [("civitai/talos-infra", "1065")], f"wrong repo resolved: {calls}"
    assert sig[0]["pr_refs"][0]["state"] == "merged 2026-08-19"


# ----------------------------------------------------------------- transcript seam

def test_user_messages_are_read():
    """search-sessions matches on user text; completion must read it too, or a session
    selected on a user-only mention reports mentions_found: 0."""
    with tempfile.TemporaryDirectory() as tmp:
        mock_dir = Path(tmp) / ".claude" / "projects" / "p"
        mock_dir.mkdir(parents=True)
        with open(mock_dir / "u.jsonl", "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content":
                    [{"type": "text", "text": f"{TARGET} is still unresolved"}]}}) + "\n")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, mock_dir.parent
        try:
            r = check_completion.check_task(TARGET, session_ids=["u"])
        finally:
            check_completion.CLAUDE_DIR = orig
    assert r["mentions_found"] == 1, f"user-message mention was invisible: {r}"
    assert r["status"] == "open", f"expected open, got {r['status']}"


# ----------------------------------------------------------------- repo inference

def test_repo_inferred_from_markdown_separated_name():
    """Real snippets read `talos-infra **#1065` — emphasis between the repo and the '#'
    left almost every reference unresolved before this."""
    calls = []
    orig = check_completion.resolve_pr
    check_completion.resolve_pr = lambda repo, num: calls.append((repo, num)) or "merged"
    try:
        sig = [{"signal": "PR merged", "snippet": "while talos-infra **#1065 merged 08-16** (I confirmed)"}]
        check_completion.annotate_pr_refs(sig)
    finally:
        check_completion.resolve_pr = orig
    assert calls == [("civitai/talos-infra", "1065")], f"repo not inferred: {calls}"


def test_adjacency_breaks_a_repo_tie_through_markdown():
    """Two known repos named, but one is adjacent to the '#'. Adjacency must win.

    This is the case that makes the markdown strip load-bearing: without it the emphasis
    blocks the adjacency capture, both repos look equally plausible, and the reference
    degrades to "unresolved". Found by a SURVIVED mutant, not by review.
    """
    calls = []
    orig = check_completion.resolve_pr
    check_completion.resolve_pr = lambda repo, num: calls.append((repo, num)) or "merged"
    try:
        sig = [{"signal": "PR merged", "snippet": "civitai lagged while talos-infra **#1065 merged"}]
        check_completion.annotate_pr_refs(sig)
    finally:
        check_completion.resolve_pr = orig
    assert calls == [("civitai/talos-infra", "1065")], \
        f"adjacency did not break the tie (markdown strip inert?): {calls} {sig[0].get('pr_refs')}"


def test_ambiguous_repo_stays_unresolved():
    """Two known repos in range and NEITHER adjacent -> refuse to pick.

    Note the fixture has a non-repo word ("saw") directly before the '#'. A repo written
    immediately before the number is an explicit citation and deliberately outranks a
    second repo mentioned nearby — this test is about the case where no such citation
    exists, which is the only genuinely ambiguous one.
    """
    called = []
    orig = check_completion.resolve_pr
    check_completion.resolve_pr = lambda repo, num: called.append((repo, num)) or "merged"
    try:
        sig = [{"signal": "PR merged", "snippet": "civitai talos-infra saw #1065 merged"}]
        check_completion.annotate_pr_refs(sig)
    finally:
        check_completion.resolve_pr = orig
    assert called == [], f"ambiguous repo was resolved anyway: {called}"
    # Round 5 (D9) changed the TEXT of this failure, not the behaviour: two repos WERE
    # named here, so "repo not named" was a false explanation. Refusing to pick is what
    # this test has always been about, and that is unchanged.
    assert sig[0]["pr_refs"][0]["state"] == (
        "unresolved (ambiguous — civitai/civitai, civitai/talos-infra both in range, "
        "neither adjacent)"), f"ambiguous repo was guessed: {sig[0]['pr_refs']}"


def test_distant_repo_name_does_not_claim_a_bare_ref():
    """The false positive a snippet-wide scan produced (2026-08-19).

    "civitai" appears in almost every snippet in this corpus, so a whole-snippet scan
    attributed every bare '#N' to civitai/civitai and reported real-but-unrelated PRs —
    one came back "merged 2024-03-18". A repo far from the '#' must not claim it.
    """
    called = []
    orig = check_completion.resolve_pr
    check_completion.resolve_pr = lambda repo, num: called.append((repo, num)) or "merged"
    try:
        sig = [{"signal": "PR merged", "snippet":
                "civitai counted 8 NO EVIDENCE and 4 partial across the sweep; strongest landed #1080"}]
        check_completion.annotate_pr_refs(sig)
    finally:
        check_completion.resolve_pr = orig
    assert called == [], f"a distant repo name claimed a bare ref: {called}"
    # Round 5 (D9): the word sitting on this '#' is "landed", which is reported as-written.
    # No rule short of enumerating the world separates 'landed' from 'devrc', and a reader
    # separates them at a glance — so the message names the word and refuses to classify
    # it. What matters, and is unchanged, is that nothing was resolved.
    assert sig[0]["pr_refs"][0]["state"].startswith("unresolved ("), \
        f"a distant repo name claimed a bare ref: {sig[0]['pr_refs']}"
    assert "'landed'" in sig[0]["pr_refs"][0]["state"], \
        f"the word on the '#' is not reported: {sig[0]['pr_refs']}"


# ----------------------------------------------------------------- disagreements

def test_resolved_comment_on_open_ticket_is_flagged():
    """The 868kr07fu shape: ticket `to do`/urgent, newest comment says "Resolved"."""
    flags = check_addressed.disagreements([{
        "task_id": "868kr07fu", "status": "partially_addressed",
        "clickup_status": "to do",
        "newest_comment": {"snippet": "Resolved. The 8/17 DR-SWEEP reads 0 missing. Recommend closing."},
    }])
    assert any("still" in f and "868kr07fu" in f for f in flags), \
        f"resolved-comment-on-open-ticket not flagged: {flags}"


def test_no_flag_when_ticket_and_comment_agree():
    """Control: an open ticket with an open-sounding comment must NOT be flagged."""
    flags = check_addressed.disagreements([{
        "task_id": "868krn3y1", "status": "unclear", "clickup_status": "to do",
        "newest_comment": {"snippet": "Escalating priority: this fired on three healthy branches."},
    }])
    assert flags == [], f"false disagreement raised: {flags}"


def test_open_pr_cited_as_done_is_flagged():
    """A completion signal quoting a PR that is actually still open."""
    flags = check_addressed.disagreements([{
        "task_id": "868kr07fu", "status": "likely_addressed", "clickup_status": "complete",
        "newest_comment": {"snippet": ""},
        "completion": [{"signal": "PR merged", "snippet": "addressed in #1073",
                        "pr_refs": [{"ref": "civitai/talos-infra#1073", "state": "open"}]}],
    }])
    assert any("#1073" in f and "OPEN" in f for f in flags), f"open PR not flagged: {flags}"


# The real 868kr0799 comment (2026-08-20), abridged. Contains BOTH an explicit refusal to
# close and the word "resolved" in its alert-cycling sense.
KEEP_OPEN_COMMENT = ("Still live, do not close. The 8/17 clean CAPACITY-SWEEP looks like a "
                     "lucky snapshot. Grafana tonight (8/19): MeiliSearch: P95 > 5s "
                     "(Saturation Burst) fired and resolved repeatedly, A=18.97 at 7:55")


def test_keep_open_comment_vetoes_the_close_flag():
    """A comment refusing closure must never produce a 'close it' instruction, however
    much completion vocabulary it also contains."""
    flags = check_addressed.disagreements([{
        "task_id": "868kr0799", "status": "likely_addressed", "clickup_status": "to do",
        "newest_comment": {"snippet": KEEP_OPEN_COMMENT},
    }])
    assert not any("close it" in f.lower() for f in flags), \
        f"told the operator to close a ticket whose comment says not to: {flags}"
    assert any("do NOT close" in f for f in flags), f"keep-open not surfaced: {flags}"


def test_keep_open_veto_does_not_silence_genuine_resolutions():
    """Positive control: the 868kr07fu shape must still be flagged for closing. Without
    this the veto could pass by suppressing every close-it flag."""
    flags = check_addressed.disagreements([{
        "task_id": "868kr07fu", "status": "likely_addressed", "clickup_status": "to do",
        "newest_comment": {"snippet": "Resolved on both counts. Recommend closing."},
    }])
    assert any("close it" in f for f in flags), f"genuine resolution no longer flagged: {flags}"


# --------------------------------------------------------------------------- D4
#
# D4 (2026-08-20) the checker skipped its OWN transcript but not a PRIOR RUN's. A prior
# run's report prints each task ID directly beside "likely_addressed" / "✓ resolved" /
# "merged", which is the exact shape the proximity scorer rewards — so the tool read
# yesterday's verdict back as today's evidence and re-confirmed it. On the day it was
# found, both tasks in the report scored `likely_addressed` on nothing else; one carried a
# comment reading "Still live, do not close".
#
# Every test below was watched RED against a baseline copy with `is_self_run` forced to
# return False (that constant is exactly the pre-change program). Matrix in
# claude/skills/check-clickup-addressed/reference/validation-history.md.

# A prior run's report: the header check-addressed.py prints, plus a verdict line quoting
# the task ID next to completion vocabulary.
PRIOR_RUN_REPORT = (
    "## Task Completion Status\n\n"
    f"✅ **{TARGET}** — transcripts say `likely_addressed`\n"
    f"  ✓ resolved: the fix shipped in #1065, merged and verified on trunk\n"
)


def test_prior_run_is_not_read_as_evidence():
    """D4: a transcript that is a run of this checker must not supply signals."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run", PRIOR_RUN_REPORT)
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task(TARGET, session_ids=["prior-run"])
        finally:
            check_completion.CLAUDE_DIR = orig

    assert r["status"] != "likely_addressed", \
        f"a prior run of this checker was read back as evidence: {r['status']!r}"
    assert r["completion"] == [], f"cited a prior run's own output: {r['completion']}"
    assert r["self_runs_skipped"] == 1, \
        f"guard did not fire (self_runs_skipped={r.get('self_runs_skipped')!r})"


def test_prior_run_positive_control():
    """Control for the test above: the SAME fixture, guard disabled, MUST produce the bad
    verdict. Without this the test could pass because the fixture is inert."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run", PRIOR_RUN_REPORT)
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task(
                TARGET, session_ids=["prior-run"], include_self_runs=True)
        finally:
            check_completion.CLAUDE_DIR = orig

    assert r["status"] == "likely_addressed", \
        f"fixture cannot reproduce the defect, so the guard test proves nothing: {r['status']!r}"
    assert r["completion"], "fixture produced no completion signals"


def test_ordinary_work_session_is_still_read():
    """The markers must be anchored enough that real work survives them. Over-dropping
    turns every verdict into `no_mentions_found`, which is quiet and useless."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(
            tmp, "real-work",
            f"Fixed the saturation bug for {TARGET}; the fix shipped in #1065, merged.")
        orig, check_completion.CLAUDE_DIR = check_completion.CLAUDE_DIR, root
        try:
            r = check_completion.check_task(TARGET, session_ids=["real-work"])
        finally:
            check_completion.CLAUDE_DIR = orig

    assert r["self_runs_skipped"] == 0, "an ordinary work session was mistaken for a self-run"
    assert r["status"] == "likely_addressed", f"real evidence was dropped: {r['status']!r}"


def test_skill_catalog_mention_is_not_a_self_run():
    """The single most important false-positive control.

    The skill catalog is injected into EVERY session, so a bare `check-clickup-addressed`
    matched 213 of ~250 transcripts on this box, and `/check-clickup-addressed` matched 32
    because it is a substring of the skill's own PATH. Either as a marker would blind the
    tool to the whole corpus while still reporting confident verdicts.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(
            tmp, "catalog-only",
            "- check-clickup-addressed: Verify if ClickUp tasks were fully addressed.\n"
            "Base directory: /home/zach/.claude/skills/check-clickup-addressed/SKILL.md\n"
            f"{TARGET} still needs work.")
        path = root / "test-project" / "catalog-only.jsonl"
        assert not selfrun.is_self_run(path), \
            "the skill catalog line / skill path was mistaken for a run of the checker"


def test_the_devrc_invocation_path_is_a_self_run():
    """🔴 THE MARKER IS A PATH, AND THE MIGRATION MOVED THE PATH (2026-08-22).

    Until this repo, the pipeline lived at `.claude/skills/check-clickup-addressed/scripts/`
    inside datapacket-talos, and the anchored marker was `check-clickup-addressed/scripts/`.
    In devrc the identical call reads
    `~/workspace/devrc/scripts/check-clickup-addressed/check-addressed.py` — the two segments
    are REVERSED, so the old marker matches none of it. Nothing errors: the guard simply
    stops recognising its own runs, and the checker goes back to reading yesterday's report
    as today's evidence, which is the exact failure the guard was built for (see
    `scripts/check-clickup-addressed/_selfrun.py`).

    Watched RED before the marker was added: the assertion below failed on the migrated tree
    with the marker tuple untouched.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(
            tmp, "devrc-run",
            "python3 ~/workspace/devrc/scripts/check-clickup-addressed/check-addressed.py --limit 3")
        path = root / "test-project" / "devrc-run.jsonl"
        assert selfrun.is_self_run(path), \
            "the devrc invocation path was not recognised as a run of this checker"


def test_a_mention_of_the_scripts_DIRECTORY_is_not_a_self_run():
    """🔴 THE NEGATIVE CONTROL THAT REJECTED THE OBVIOUS FIX, and the whole reason the devrc
    marker is a regex requiring a `.py` FILE rather than the directory.

    `scripts/check-clickup-addressed/` reads like the exact counterpart of the old marker.
    It is unusable, for the same reason the bare name is: devrc's CLAUDE.md carries a table
    mapping `scripts/<dir>/` to its owning skill, and a project CLAUDE.md is injected into
    every session in that repo. Measured 2026-08-22 over the 761 transcripts these scripts
    walk, the two sibling rows already in that table appear in 83 (10.9%) and 72 (9.5%) of
    them — the same order as the bare name at 96 (12.6%), which this module rejects by name.
    A ~10% over-drop of the corpus, silent, in the direction of trusting stale evidence.

    Both fixtures below are REAL text: the CLAUDE.md row this migration added, and the gate's
    own per-target line. Both contain the directory; neither is a run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate((
            "| `scripts/check-clickup-addressed/` | `check-clickup-addressed` | did the work "
            "on a ClickUp ticket actually happen |",
            "  PASS  scripts/check-clickup-addressed/tests  (collected=157 passed=157 floor=149)",
        )):
            root = _session(tmp, f"dirmention{i}", f"{text}\n{TARGET} is still open.")
            path = root / "test-project" / f"dirmention{i}.jsonl"
            assert not selfrun.is_self_run(path), \
                f"a mention of the scripts DIRECTORY was mistaken for a run: {text!r}"


# Written out as LITERALS, deliberately not read from selfrun.SELF_RUN_MARKERS. Iterating
# the implementation's own tuple would make "delete a marker" pass vacuously — the deleted
# marker simply stops being tested. This is a ledger: it fails if the set shrinks (a marker
# stopped working) AND if it grows (a new marker arrived unreviewed), so widening what counts
# as a self-run has to be a deliberate edit here, where the false-positive cost is argued.
EXPECTED_SELF_RUN_MARKERS = (
    "<command-name>/check-clickup-addressed</command-name>",
    "check-clickup-addressed/scripts/",     # the pre-2026-08-22 datapacket-talos layout
    "## Task Completion Status",
    '"skill": "check-clickup-addressed"',                       # SELF_RUN_RE
    "scripts/check-clickup-addressed/check-addressed.py",       # NEW_LAYOUT_RE
)


def test_each_self_run_marker_fires_on_its_own():
    """Every marker must be independently load-bearing — a dead one is coverage that
    reads as protection and provides none."""
    with tempfile.TemporaryDirectory() as tmp:
        for i, marker in enumerate(EXPECTED_SELF_RUN_MARKERS):
            root = _session(tmp, f"m{i}", f"some text {marker} more text")
            path = root / "test-project" / f"m{i}.jsonl"
            assert selfrun.is_self_run(path), f"marker never fires: {marker!r}"


def test_self_run_marker_set_has_not_drifted():
    """Pin the literal tuple too, so an added marker cannot slip in untested. The tuple
    holds three of the five; the last two are regexes (SELF_RUN_RE, NEW_LAYOUT_RE)."""
    assert tuple(selfrun.SELF_RUN_MARKERS) == EXPECTED_SELF_RUN_MARKERS[:3], (
        f"self-run marker set changed: {selfrun.SELF_RUN_MARKERS!r}. Add the new marker to "
        f"EXPECTED_SELF_RUN_MARKERS and prove it does not fire on the skill catalog."
    )


def test_skill_tool_invocation_is_a_self_run():
    """The realistic shape of the regex marker: a Skill tool_use block, whose quotes are
    raw JSON structure rather than escaped text. The marker test above writes its fixture
    into message TEXT (escaped quotes); both forms must match, so both are exercised."""
    with tempfile.TemporaryDirectory() as tmp:
        mock_dir = Path(tmp) / ".claude" / "projects" / "test-project"
        mock_dir.mkdir(parents=True)
        path = mock_dir / "skilltool.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{
                    "type": "tool_use", "name": "Skill",
                    "input": {"skill": "check-clickup-addressed"},
                }]},
            }) + "\n")
        assert selfrun.is_self_run(path), "a Skill tool_use invocation was not recognised"


def test_search_sessions_drops_prior_runs():
    """The report lists 'Matching Sessions' from here; on 2026-08-20 a prior run topped
    that list with 14 hits and read as the session that did the work."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _session(tmp, "prior-run", PRIOR_RUN_REPORT)
        _session(tmp, "real-work", f"digging into {TARGET} today")
        orig, search_sessions.CLAUDE_DIR = search_sessions.CLAUDE_DIR, root
        try:
            default = search_sessions.search_sessions([TARGET])
            everything = search_sessions.search_sessions([TARGET], include_self_runs=True)
        finally:
            search_sessions.CLAUDE_DIR = orig

    assert {r["session_id"] for r in default} == {"real-work"}, \
        f"prior run leaked into the session list: {[r['session_id'] for r in default]}"
    assert len(everything) == 2, "positive control: both sessions are findable when allowed"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
