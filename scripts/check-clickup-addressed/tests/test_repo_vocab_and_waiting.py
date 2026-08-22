#!/usr/bin/env python3
"""Round 5 (2026-08-21). Three defects found by RUNNING the tool, not by reading it.

  D9   `_repo_for_ref` returns None in two different situations and `annotate_pr_refs`
       renders both as "unresolved (repo not named)". When a repo IS named but is absent
       from KNOWN_REPOS, that message is FALSE. Measured live on the snippet
       `| **#4181**, **devrc #591** | merged, verified by content |`: the regex captured
       word='devrc' — the repo was named — yet the report said it was not. Both PRs were
       resolvable by hand (civitai/civitai#4181 MERGED, innovation-upstream/devrc#591
       MERGED), so round 2's "is this cited PR actually still open?" cross-check
       contributed NOTHING on the one task in the run that had completion signals, while
       reporting a cause that was wrong. Same closed-vocabulary silent-miss class as D6,
       but worse: a wrong explanation stops the reader looking, where an honest "I don't
       know this repo" sends them to add it.

  D10  round 4's `confidence_marker()` is used by check-completion.py's own printer and
       NOT by check-addressed.py, the entry point everyone runs. That printer's `✓`/`○`
       mark completion-vs-open, not confidence, so the adjacency signal never reached the
       report. Measured at base: a signal at proximity 1.5 (task ID inside its own
       snippet) and one at 1.0 (merely in the same ±2000-char window) rendered
       identically. This is the "a guard's DESCRIPTION claims coverage wider than its
       implementation" defect, walked into while fixing that defect class.

  D11  `disagreements()` emitted nothing for a task that is OPEN, has ZERO transcript
       evidence, and carries a recent comment from someone else. Measured live on
       868kuam02: `to do`/high, @Ellie King 2026-08-20, 0 mentions — and the "Needs a
       decision" block said nothing. Nothing "disagrees", so every existing rule stayed
       quiet, but "a colleague asked you something and no work exists anywhere" is the
       most actionable thing this tool can detect.

Tests marked INVARIANT GUARD pass at base too. They are false-positive controls, not
regression coverage; several of them exist only to kill a WIDENING mutant (a flag that
fires on everything is as useless as one that fires on nothing).
"""
import importlib.util, io, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

spec = importlib.util.spec_from_file_location("check_completion", SCRIPT_DIR / "check-completion.py")
check_completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_completion)

spec2 = importlib.util.spec_from_file_location("check_addressed", SCRIPT_DIR / "check-addressed.py")
check_addressed = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(check_addressed)

# The exact snippet the live run produced, verbatim from `check-addressed.py --limit 5`
# on 2026-08-21 (task 868ktvqf9).
LIVE_SNIPPET = "| **#4181**, **devrc #591** | merged, verified by content |"

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _no_network():
    """Swap the GitHub call for a recording stub. Returns (restore, calls)."""
    calls = []
    orig = check_completion.resolve_pr

    def stub(repo, num):
        calls.append((repo, num))
        return "merged 2026-08-20"

    check_completion.resolve_pr = stub

    def restore():
        check_completion.resolve_pr = orig

    return restore, calls


def _state(snippet, ref_num):
    restore, _ = _no_network()
    try:
        sig = [{"signal": "PR merged", "snippet": snippet}]
        check_completion.annotate_pr_refs(sig, default_repo=None)
    finally:
        restore()
    for r in sig[0].get("pr_refs", []):
        if r["ref"].endswith(f"#{ref_num}"):
            return r["ref"], r["state"]
    raise AssertionError(f"#{ref_num} produced no pr_ref at all: {sig[0].get('pr_refs')}")


# --------------------------------------------------------------------------- D9

def test_a_named_but_unknown_repo_is_not_reported_as_unnamed():
    """The false message. `devrc` IS named; it is merely absent from KNOWN_REPOS.

    At base both refs in this live snippet came back "unresolved (repo not named)".
    """
    ref, state = _state("| **#4181**, **notarepo-xyz #591** | merged |", "591")
    assert "not named" not in state, \
        f"a NAMED repo was reported as unnamed — the message is false: {state!r}"
    assert "notarepo-xyz" in state, \
        f"the unknown repo must be named so the reader can add it: {state!r}"


def test_the_unknown_repo_message_says_what_to_do():
    """An honest 'I don't know this repo' has to send the reader somewhere.

    D6's lesson: widening the vocabulary is not the fix, the announcement is. A message
    that names the gap without naming the table it is missing from is half a message.
    """
    _, state = _state("fix landed in notarepo-xyz #4242", "4242")
    assert "KNOWN_REPOS" in state, \
        f"the message does not name the table to add the repo to: {state!r}"


def test_devrc_now_resolves_to_innovation_upstream():
    """The live case. Verified with `gh repo view`: devrc is innovation-upstream/devrc,
    NOT a civitai repo — the owner is the part a guess gets wrong."""
    restore, calls = _no_network()
    try:
        sig = [{"signal": "PR merged", "snippet": LIVE_SNIPPET}]
        check_completion.annotate_pr_refs(sig, default_repo=None)
    finally:
        restore()
    assert ("innovation-upstream/devrc", "591") in calls, \
        f"devrc #591 did not resolve to innovation-upstream/devrc: {calls}"


def test_the_hub_repos_are_all_present_and_correctly_owned():
    """A ledger, not a spot-check. Every owner verified with `gh repo view` 2026-08-21.

    Pinned as an exact mapping so the table cannot silently gain a guessed owner — the
    devrc case proves the owner is exactly what a guess gets wrong.
    """
    expected = {
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
    assert check_completion.KNOWN_REPOS == expected, \
        ("KNOWN_REPOS drifted. Verify any new entry with `gh repo view <owner>/<name>` "
         f"before adding it here.\n  got:      {check_completion.KNOWN_REPOS}\n"
         f"  expected: {expected}")


def test_a_genuinely_unnamed_ref_still_says_not_named():
    """INVARIANT GUARD — passes at base. The refusal to guess is deliberate and stays.

    `#4181` in the live snippet has no repo word before it and no known repo within
    REPO_LOOKBEHIND. "repo not named" is the CORRECT message there, and the D9 fix must
    not relabel it as an unknown-repo problem.
    """
    _, state = _state(LIVE_SNIPPET, "4181")
    assert state == "unresolved (repo not named)", \
        f"the honest not-named case was relabelled: {state!r}"


def test_an_unknown_repo_is_still_never_guessed():
    """INVARIANT GUARD / widening control — passes at base.

    Naming the unknown repo must not become resolving it. Guessing an owner is the
    round-2 failure (`civitai/civitai#1080: merged 2024-03-18`), and it is worse than
    admitting ignorance.
    """
    restore, calls = _no_network()
    try:
        sig = [{"signal": "PR merged", "snippet": "notarepo-xyz #591 merged"}]
        check_completion.annotate_pr_refs(sig, default_repo=None)
    finally:
        restore()
    assert calls == [], f"an unknown repo name was guessed at and resolved: {calls}"


def test_ambiguous_repos_are_named_rather_than_called_unnamed():
    """Third arm of the same wrong message: two known repos in range, neither adjacent.

    Repos WERE named there; the problem is that more than one was. Reporting that as
    "repo not named" is the same false explanation in a different shape.
    """
    _, state = _state("civitai talos-infra saw #1065 merged", "1065")
    assert "not named" not in state, f"ambiguity reported as absence: {state!r}"
    assert "civitai/civitai" in state and "civitai/talos-infra" in state, \
        f"the ambiguous candidates are not named: {state!r}"


# --------------------------------------------------------------------------- D10

def test_report_lines_carry_the_adjacency_marker():
    """The entry point's report must show adjacency, not just completion-vs-open.

    At base a proximity-1.5 signal and a proximity-1.0 signal rendered IDENTICALLY:
        ✓ PR merged: ADJACENT ...
        ✓ PR merged: FAR AWAY ...
    """
    adjacent = {"signal": "PR merged", "snippet": "ADJACENT", "proximity": 1.5}
    far = {"signal": "PR merged", "snippet": "FAR AWAY", "proximity": 1.0}
    line_a = check_addressed.signal_line("✓", adjacent, verbose=False)
    line_f = check_addressed.signal_line("✓", far, verbose=False)
    assert line_a != line_f, \
        f"adjacent and same-window-only signals render identically: {line_a!r}"
    assert "●" in line_a, f"adjacent signal is not marked ●: {line_a!r}"
    assert "●" not in line_f, f"a same-window-only signal was marked ●: {line_f!r}"


def test_the_adjacency_marker_does_not_collide_with_the_open_glyph():
    """`○` already means 'open item' in the first column of this report.

    Reusing one glyph for two orthogonal facts is how a marker becomes unreadable, so the
    adjacency field must be visually separated from the completion/open glyph rather than
    merged into it.
    """
    far_open = {"signal": "still open", "snippet": "FAR", "proximity": 1.0}
    line = check_addressed.signal_line("○", far_open, verbose=False)
    assert line.count("○") == 2, \
        f"the two ○ meanings are not separable on this line: {line!r}"
    assert "[○]" in line, \
        f"the adjacency field is not delimited from the kind glyph: {line!r}"


def test_the_entry_point_agrees_with_check_completion_at_every_boundary():
    """One vocabulary, one threshold — asserted BEHAVIOURALLY, across the boundary.

    Object identity cannot be asserted here: this test file loads check-completion.py with
    its own `exec_module`, and check-addressed.py loads a second, independent instance, so
    `is` compares two different function objects even when the import is correct. That
    version of this test failed for a reason that had nothing to do with the code.

    Behavioural equivalence is also the stronger claim: it catches the hazard that actually
    matters — a re-implementation with a DIFFERENT threshold, which object identity would
    catch only by accident. The sweep straddles ADJACENCY_MARK on both sides and lands
    exactly ON it, since a boundary is where two copies of a rule diverge first.
    """
    mark = check_completion.ADJACENCY_MARK
    for prox in (0.0, 0.5, 1.0, mark - 0.01, mark, mark + 0.01, 1.5, 99.0):
        mine = check_addressed.confidence_marker({"proximity": prox})
        theirs = check_completion.confidence_marker({"proximity": prox})
        assert mine == theirs, \
            f"the two markers disagree at proximity {prox}: {mine!r} vs {theirs!r}"
    # Positive control: the sweep must actually see BOTH values, or agreement is vacuous.
    seen = {check_addressed.confidence_marker({"proximity": p}) for p in (1.0, 1.5)}
    assert seen == {"●", "○"}, f"the sweep never exercised both markers: {seen}"


def test_the_marker_legend_is_printed_in_the_report():
    """An unexplained glyph is a glyph nobody reads. The report must say what ● / ○ mean.

    Driven through main() with stubbed sub-processes so this asserts what an operator
    actually sees, not what a helper returns.
    """
    out = _run_main(["--no-resolve-prs"], completion=[
        {"signal": "PR merged", "snippet": "x", "proximity": 1.5}])
    assert "●" in out and "○" in out, f"no legend glyphs in the report:\n{out}"
    assert "same" in out.lower() and "window" in out.lower(), \
        f"the legend does not explain what ○ means:\n{out}"


def _run_main(argv, completion=None, open_items=None, status="partially_addressed",
              mentions=3, clickup_status="to do", comment="hi", comment_date=None,
              task="868ktvqf9"):
    """Drive check-addressed.main() with every sub-process stubbed. Returns stdout."""
    def fake(name, *args):
        if name == "recent-comments.py":
            return json.dumps([{
                "task_id": task, "task_name": "t", "task_status": clickup_status,
                "task_priority": "high",
                "date": comment_date or NOW.strftime("%Y-%m-%d %H:%M"),
                "author": "colleague", "snippet": comment}]), 0
        if name == "search-sessions.py":
            return "[]", 0
        return json.dumps([{
            "task_id": task, "status": status, "sessions_searched": 1,
            "mentions_found": mentions, "completion": completion or [],
            "open": open_items or []}]), 0

    orig_run, orig_argv, orig_stdout = check_addressed.run_script, sys.argv, sys.stdout
    check_addressed.run_script = fake
    sys.argv = ["check-addressed.py", *argv]
    sys.stdout = io.StringIO()
    try:
        check_addressed.main()
        return sys.stdout.getvalue()
    finally:
        check_addressed.run_script, sys.argv, sys.stdout = orig_run, orig_argv, orig_stdout


def test_both_marker_values_reach_a_real_report():
    """Positive control on BOTH values — a marker that can only print one symbol is the
    exact defect round 4 fixed, reintroduced one layer up.

    Mirrors the live distribution measured 2026-08-21: 868ktvqf9 carries proximity 1.5
    AND 1.0 signals; 868kt8pfu carries only 1.0.
    """
    out = _run_main(["--no-resolve-prs"], completion=[
        {"signal": "PR merged", "snippet": "ADJACENT", "proximity": 1.5},
        {"signal": "PR merged", "snippet": "FAR AWAY", "proximity": 1.0}])
    body = [l for l in out.splitlines() if "ADJACENT" in l or "FAR AWAY" in l]
    assert len(body) == 2, f"both signals must be printed: {body}"
    adj = next(l for l in body if "ADJACENT" in l)
    far = next(l for l in body if "FAR AWAY" in l)
    assert "[●]" in adj, f"live-shaped adjacent signal not marked ●: {adj!r}"
    assert "[○]" in far, f"live-shaped distant signal not marked ○: {far!r}"


# --------------------------------------------------------------------------- D11

def _waiting(days_ago=1, status="to do", mentions=0, transcript="no_mentions_found"):
    d = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")
    return {
        "task_id": "868kuam02", "status": transcript, "sessions_searched": 1,
        "mentions_found": mentions, "clickup_status": status, "clickup_priority": "high",
        "newest_comment": {"date": d, "author": "Ellie King",
                           "snippet": "Follow-up from the reporter, on why this looks pixelated"},
        "completion": [], "open": [],
    }


def _flags(r):
    return check_addressed.disagreements([r], now=NOW)


def test_an_unanswered_comment_with_zero_evidence_is_flagged():
    """The live 868kuam02 shape. At base: [].

    An open ticket + zero transcript evidence anywhere + a recent comment from someone
    else means a human asked and nobody has started. Nothing "disagrees", so every
    existing rule stayed silent about the most actionable state in the report.
    """
    flags = _flags(_waiting())
    waiting = [f for f in flags if "WAITING" in f]
    assert waiting, f"an unanswered colleague comment over zero evidence produced no flag: {flags}"
    joined = " ".join(waiting).lower()
    assert "868kuam02" in joined, f"the flag does not name the task: {waiting}"
    assert "ellie king" in joined, f"the flag does not name who is waiting: {waiting}"


def test_no_sessions_found_is_the_same_zero_evidence_state():
    """Gate on the STATE (mentions_found == 0), not on one status word.

    `no_sessions_found` and `no_mentions_found` are both zero evidence. A guard spelled
    against one word passes while the hazard exists in the other's shape.
    """
    assert any("WAITING" in f for f in _flags(_waiting(transcript="no_sessions_found"))), \
        "no_sessions_found is zero evidence too, and was not flagged"


def test_an_unknown_clickup_status_does_not_disable_the_waiting_flag():
    """D6's lesson applied forward: this flag must not join the closed-vocabulary gate.

    'a human is waiting and no work exists' is safe to surface whatever the status word
    reads, exactly like the keep-open veto.

    🔴 This assertion looks for the WAITING flag specifically, not merely for a non-empty
    list. `in review` also trips round 4's unknown-status announcement, so the weaker
    `assert flags` was GREEN FOR THE OTHER GUARD'S REASON: the M42 mutant (which rebuilds
    exactly this blind spot by gating on OPEN_STATUSES) SURVIVED a fully green suite, and
    only tightening the assertion killed it.
    """
    flags = _flags(_waiting(status="in review"))
    assert any("WAITING" in f for f in flags), \
        f"an unrecognised status silently disabled the waiting flag — D6 all over again: {flags}"
    # Positive control on the OTHER guard: both flags must fire here, so this test cannot
    # pass by having quietly suppressed round 4's announcement.
    assert any("DID NOT RUN" in f for f in flags), \
        f"the round-4 unknown-status announcement stopped firing: {flags}"


# --- the widening direction: a flag that fires on everything is as useless as one that
# --- fires on nothing. Each of these is green at base by construction (base flags
# --- nothing), so they are controls; they earn their place under mutation.

def test_a_stale_backlog_comment_does_not_fire():
    """INVARIANT GUARD / widening control.

    Bounded by RECENCY rather than priority. Priority is a property of the TICKET, set
    once and often stale; recency is a property of the INTERACTION and says a human is
    waiting NOW. A recency bound also self-clears, so the flag's false-positive volume is
    bounded by construction — and a permanently-noisy block trains the reader to skip it.
    """
    assert _flags(_waiting(days_ago=90)) == [], \
        "every unstarted backlog item with an old comment would be flagged forever"


def test_evidence_in_the_transcripts_suppresses_the_flag():
    """INVARIANT GUARD / widening control. The claim is 'no work exists ANYWHERE'.

    Once a transcript mentions the task there IS evidence, and the existing verdict
    branches own it.
    """
    assert _flags(_waiting(mentions=7, transcript="unclear")) == [], \
        "the flag fired over a task the transcripts actually discuss"


def test_a_closed_ticket_does_not_fire():
    """INVARIANT GUARD / widening control. A done ticket with no transcript evidence is
    already covered by the 'verify before trusting the close' flag; claiming someone is
    waiting on it would be false."""
    for f in _flags(_waiting(status="complete")):
        assert "waiting" not in f.lower(), f"a closed ticket was reported as waiting: {f}"


def test_a_record_with_no_mention_count_does_not_fire():
    """INVARIANT GUARD / widening control — and the one a real fixture caught.

    "no work exists ANYWHERE" is the strongest claim this flag makes. Deriving it from a
    key that is simply ABSENT is the field-that-is-not-a-guard mistake inverted: the count
    must be present and zero, never defaulted to zero. Found by round 2's
    `test_no_flag_when_ticket_and_comment_agree`, whose fixture omits the key — the first
    draft of this flag fired on it.
    """
    r = _waiting()
    del r["mentions_found"]
    assert _flags(r) == [], "a record that never reported a mention count was read as zero evidence"


def test_a_record_with_no_comment_date_does_not_fire():
    """INVARIANT GUARD / widening control. ABSENT and MALFORMED are different facts.

    Absent means there is no interaction to be recent about. Only a date that is present
    and unreadable indicates the formatter drifted, and only that one announces itself.
    """
    r = _waiting()
    r["newest_comment"].pop("date")
    assert _flags(r) == [], "a record with no comment date was treated as a fresh comment"


def test_an_unparseable_comment_date_is_surfaced_not_swallowed():
    """A date this checker cannot read must not silently become 'not recent'.

    That is the reassuring-nothing this whole skill exists to police: an unparseable date
    is unknown age, and a missed waiting human costs more than one noisy line.
    """
    r = _waiting()
    r["newest_comment"]["date"] = "yesterday-ish"
    flags = _flags(r)
    assert flags, "an unparseable comment date silently disabled the flag"
    assert any("date" in f.lower() for f in flags), \
        f"the flag does not admit the date was unreadable: {flags}"


def test_round_four_flags_all_still_fire():
    """INVARIANT GUARD — the round-4 and round-3 behaviour must survive round 5."""
    keep_open = {"task_id": "868kr0799", "status": "unclear", "mentions_found": 2,
                 "clickup_status": "to do",
                 "newest_comment": {"date": NOW.strftime("%Y-%m-%d %H:%M"),
                                    "author": "Justin Maier",
                                    "snippet": "Still live, do not close. P95 > 5s fired and resolved repeatedly."},
                 "completion": [], "open": []}
    joined = " ".join(check_addressed.disagreements([keep_open], now=NOW))
    assert "do NOT close" in joined, f"the round-3 keep-open veto stopped firing: {joined}"
    assert "close it, or say why" not in joined, f"veto lost its suppression: {joined}"

    unknown = {"task_id": "868kzzzzz", "status": "likely_addressed", "mentions_found": 4,
               "clickup_status": "in review",
               "newest_comment": {"date": NOW.strftime("%Y-%m-%d %H:%M"),
                                  "author": "x", "snippet": "Resolved. Recommend closing."},
               "completion": [], "open": []}
    joined = " ".join(check_addressed.disagreements([unknown], now=NOW)).lower()
    assert "did not run" in joined, f"the round-4 unknown-status announcement was lost: {joined}"


def test_disagreements_still_works_without_an_explicit_now():
    """INVARIANT GUARD — `now` is a test seam, not a required argument. main() calls
    disagreements(results) with one argument and must keep working."""
    r = _waiting(days_ago=0)
    r["newest_comment"]["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    assert check_addressed.disagreements([r]), \
        "disagreements() lost its default clock and stopped flagging a fresh comment"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except SystemExit as e:
            print(f"  ✗ {t.__name__}: SystemExit({e.code}) escaped the test")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
