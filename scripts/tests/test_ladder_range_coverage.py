#!/usr/bin/env python3
"""Guards for `scripts/ladder-range-coverage.py` — the report that finds churn
no `audit-claims` block range covers.

WHY THESE ARE SHAPED THIS WAY
-----------------------------
The thing under test is a CLASSIFIER over real git reachability, so the fixtures
are real repositories with real commits. A fake runner would let the classifier
agree with a model of `merge-base --is-ancestor` that git does not share — and
the whole defect this script addresses (#1233's round 3) is a reachability
fact, not a parsing one.

🔴 EVERY ASSERTION HERE HAS A CONTROL ON THE OTHER SIDE, because the headline
number this script prints is a ZERO for a healthy ladder. "0 uncovered" and "the
instrument is wired to nothing" are the same output, so:

  * `test_a_tight_chain_reports_zero_uncovered` is only meaningful beside
    `test_the_1233_shape_reports_the_skipped_rounds_churn`, which MUST produce a
    non-zero count off a fixture built to contain one;
  * `test_absent_commits_are_REFUSED_not_reported_as_zero` is the negative
    control for the positive control itself.

The `#1233` fixture is named for the real case in
`claudedocs/audit-ladder-review-2026-09-04.md`: blocks for rounds 1, 2 and 4,
round 3's fixes between `to(2)` and `from(4)`, in no block's range.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib.hermetic_git import hermetic_git_env  # noqa: E402

SCRIPT = SCRIPTS / "ladder-range-coverage.py"
DISPATCH = SCRIPTS / "audit-dispatch.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lrc():
    return _load(SCRIPT, "ladder_range_coverage")


@pytest.fixture(scope="module")
def ad():
    return _load(DISPATCH, "audit_dispatch_under_test")


# --------------------------------------------------------------------------- #
# Fixture repositories
# --------------------------------------------------------------------------- #

def _git(repo, *args, check=True):
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, env=hermetic_git_env(), check=False,
    )
    if check:
        assert p.returncode == 0, f"git {args} failed: {p.stderr or p.stdout}"
    return p.stdout.strip()


def _init(repo):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "T")
    return repo


def _commit(repo, path, lines, msg):
    """Write `lines` lines into `path` and commit. Returns the new sha."""
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(f"line {i}" for i in range(lines)) + "\n",
                 encoding="utf-8")
    _git(repo, "add", "--", path)
    _git(repo, "commit", "--quiet", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _block(round_no, frm, to, claim="a claim"):
    return (f"```audit-claims round={round_no} audited={frm}..{to}\n"
            f"1. {claim}\n"
            "```\n")


@pytest.fixture
def base_repo(tmp_path):
    """`main` at a base commit, plus a `feat` branch off it. The base branch is
    `main`, so `--not main` matches the review's `--not origin/main` shape."""
    repo = _init(tmp_path / "repo")
    _commit(repo, "README.md", 3, "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "-b", "feat")
    return repo, base


# --------------------------------------------------------------------------- #
# The positive control — the #1233 shape MUST produce a number
# --------------------------------------------------------------------------- #

def test_the_1233_shape_reports_the_skipped_rounds_churn(lrc, ad, base_repo):
    """Blocks for rounds 1, 2 and 4; round 3's fixes in NO block's range.

    This is the whole point of the script, and it is the positive control for
    every zero the other tests assert. If this ever reports 0, the instrument is
    measuring nothing and the tight-chain tests below are vacuous.
    """
    repo, base = base_repo
    r1_from = base
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    r2_to = _commit(repo, "b.py", 10, "round 2 fix")
    # ── round 3's fixes: committed, and NO block will name them ──
    r3_a = _commit(repo, "skipped_one.py", 7, "round 3 fix, part 1")
    r3_b = _commit(repo, "skipped_two.py", 5, "round 3 fix, part 2")
    r4_to = _commit(repo, "d.py", 4, "round 4 fix")

    comments = [
        _block(1, r1_from, r1_to),
        _block(2, r1_to, r2_to),
        _block(4, r3_b, r4_to),      # round 4 anchors AFTER round 3's commits
    ]
    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 1233, r4_to, "main",
                           comments)

    assert L.reason is None
    assert L.blocks_used == 3
    assert L.control_churn > 0, "positive control: the blocks' own ranges move"

    gaps = [a for a in L.adjacencies if a.label == lrc.GAP]
    assert len(gaps) == 1, [(a.label, a.from_round, a.to_round)
                            for a in L.adjacencies]
    gap = gaps[0]
    assert (gap.from_round, gap.to_round) == (2, 4)
    assert gap.commits == 2, "round 3 landed two commits"
    # 7 + 5 added lines, nothing deleted. Asserted as a LITERAL, derived from
    # the fixture's own arguments and never from the script's output.
    assert (gap.added, gap.deleted) == (12, 0)
    assert L.uncovered_added + L.uncovered_deleted == 12
    assert r3_a  # the first skipped commit is inside the measured gap


def test_a_tight_chain_reports_zero_uncovered(lrc, ad, base_repo):
    """The negative control. Only meaningful beside the test above."""
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    r2_to = _commit(repo, "b.py", 10, "round 2 fix")
    comments = [_block(1, base, r1_to), _block(2, r1_to, r2_to)]

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 1, r2_to, "main",
                           comments)

    assert L.reason is None
    assert L.control_churn > 0
    assert [a.label for a in L.adjacencies] == [lrc.TIGHT, lrc.TIGHT]
    assert L.uncovered_added + L.uncovered_deleted == 0


def test_the_tail_after_the_last_block_is_its_own_gap(lrc, ad, base_repo):
    """A ladder's FINAL round's fixes land after its last block is posted, so
    the tail is exactly where a terminal round's churn hides."""
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    tail = _commit(repo, "after.py", 6, "fixes posted after the last block")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 2, tail, "main",
                           [_block(1, base, r1_to)])

    assert [a.label for a in L.adjacencies] == [lrc.GAP]
    tail_adj = L.adjacencies[0]
    assert tail_adj.to_round is None, "the tail adjacency has no next round"
    assert (tail_adj.added, tail_adj.deleted) == (6, 0)


# --------------------------------------------------------------------------- #
# The three labels that must NOT become a GAP with a number
# --------------------------------------------------------------------------- #

def test_interior_and_tail_gaps_are_reported_as_SEPARATE_totals(lrc, ad,
                                                                base_repo):
    """🔴 The two gap kinds are different claims and must not share a headline.

    An INTERIOR gap is unambiguous — a round's churn nobody audited. A TAIL gap
    conflates post-last-block fixes with development that continued after the
    ladder ended. Measured over the 2026-09-04 review's 20 ladders the tail is
    3,727 of 4,382 lines, so a single total invites being quoted as an
    under-count it does not support. This fixture has one of each, with
    DELIBERATELY DIFFERENT sizes so a mutant that reports one in place of the
    other cannot pass: an interior gap of 7 and a tail of 4 are distinct from
    each other AND from their sum.
    """
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    skipped = _commit(repo, "interior.py", 7, "round 2's unledgered fix")
    r3_to = _commit(repo, "c.py", 10, "round 3 fix")
    tail = _commit(repo, "tail.py", 4, "after the last block")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 9, tail, "main",
                           [_block(1, base, r1_to), _block(3, skipped, r3_to)])

    assert sum(L.interior) == 7, [(a.label, a.to_round, a.added)
                                  for a in L.adjacencies]
    assert sum(L.tail) == 4
    assert L.uncovered_added + L.uncovered_deleted == 11

    rendered = lrc.render([L], [])
    assert "INTERIOR  7 line(s)" in rendered
    assert "TAIL      4 line(s)" in rendered


def test_the_zero_line_gap_caveat_is_DERIVED_from_this_run(lrc, ad, base_repo):
    """🔴 Regression. That caveat shipped as the literal "Three of the 20 ladders
    look like that" — the figure from the devrc run it was written during — and
    then printed verbatim under a 5-ladder run of a different repo. A count in
    prose beside a measurement it is not computed from is the defect class this
    whole report exists to find.

    The fixture makes a gap of real COMMITS with ZERO churn, which is what an
    upstream bring-in looks like: commits that `--not <base>` excludes entirely.
    """
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    # Commits already in `main`, so `--not main` excludes every line of them:
    # reachable from the branch, contributing no churn.
    _git(repo, "checkout", "--quiet", "main")
    upstream = _commit(repo, "upstream.py", 12, "an upstream commit")
    _git(repo, "checkout", "--quiet", "feat")
    _git(repo, "merge", "--quiet", "--no-edit", "main")
    head = _git(repo, "rev-parse", "HEAD")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 10, head, "main",
                           [_block(1, base, r1_to)])

    tail = L.adjacencies[-1]
    assert tail.label == lrc.GAP, (tail.label, tail.reason)
    assert tail.commits and tail.commits > 0, "the gap has real commits"
    assert (tail.added, tail.deleted) == (0, 0), \
        "`--not main` must exclude the bring-in's lines"

    rendered = lrc.render([L], [])
    assert "1 gap(s) in THIS run look like that" in rendered
    assert "the 20 ladders" not in rendered, \
        "a hardcoded corpus figure is being printed for an unrelated run"
    assert upstream


def test_an_overlap_is_labelled_OVERLAP_and_given_no_size(lrc, ad, base_repo):
    """Two ranges covering the same commits double-count, which is the OPPOSITE
    error from a gap. Reporting it as a 0-line gap would hide it."""
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    r2_to = _commit(repo, "b.py", 10, "round 2 fix")
    # Round 2 claims to have audited `base` — BEFORE round 1's range ended.
    comments = [_block(1, base, r1_to), _block(2, base, r2_to)]

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 3, r2_to, "main",
                           comments)

    labels = [a.label for a in L.adjacencies]
    assert labels[0] == lrc.OVERLAP, labels
    overlap = L.adjacencies[0]
    assert overlap.added is None and overlap.commits is None
    assert "ANCESTOR" in overlap.reason
    assert L.uncovered_added + L.uncovered_deleted == 0


def test_unrelated_histories_are_UNMEASURABLE_not_zero(lrc, ad, base_repo):
    """A rebase mid-ladder leaves two shas neither of which reaches the other.
    There is no gap SIZE; saying 0 would be a claim nobody measured."""
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    # An orphan branch: a real commit in this repo, reachable from nothing here.
    _git(repo, "checkout", "--quiet", "--orphan", "other")
    _git(repo, "rm", "-rf", "--quiet", ".")
    orphan = _commit(repo, "z.py", 3, "unrelated history")
    _git(repo, "checkout", "--quiet", "feat")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 4, r1_to, "main",
                           [_block(1, base, orphan), _block(2, r1_to, r1_to)])

    assert any(a.label == lrc.UNRELATED for a in L.adjacencies), \
        [(a.label, a.reason) for a in L.adjacencies]
    for a in L.adjacencies:
        if a.label == lrc.UNRELATED:
            assert a.added is None, "an unmeasurable adjacency carries no number"


def test_a_sha_git_cannot_resolve_is_UNMEASURABLE_not_a_gap(lrc, ad, base_repo):
    """Reaches `_is_ancestor`'s ERROR return, which no other test here does.

    🔴 Written because the other UNRELATED case (two real but unrelated commits)
    exercises the rc-1 path only, so `_is_ancestor`'s rc-128 branch was
    UNREACHABLE from this suite — a guard a mutation sweep would score as
    surviving while production hits it on every merged PR whose head is not
    fetched. The discriminator: `frm` resolves, `to` does not, and they differ,
    so the classifier cannot take the TIGHT short-circuit.
    """
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    unfetched_head = "9" * 40

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 8, unfetched_head,
                           "main", [_block(1, base, r1_to)])

    assert L.control_churn > 0, "the block's own range is real — not a refusal"
    tail = L.adjacencies[-1]
    assert tail.label == lrc.UNRELATED, (tail.label, tail.reason)
    assert "could not answer" in tail.reason
    assert tail.added is None and tail.commits is None
    assert L.uncovered_added + L.uncovered_deleted == 0


def test_absent_commits_are_REFUSED_not_reported_as_zero(lrc, ad, base_repo):
    """The negative control for the positive control.

    A merged PR's commits are routinely absent from a local checkout. Every
    range then measures empty and the uncovered total is 0 — identical to a
    perfect ladder. The script must refuse instead.
    """
    repo, base = base_repo
    _commit(repo, "a.py", 10, "a commit that exists")
    absent_a = "0" * 40
    absent_b = "1" * 40

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 5, absent_b, "main",
                           [_block(1, absent_a, absent_b)])

    assert not L.control_churn, "the control must read zero for absent commits"
    rendered = lrc.render([L], [])
    assert "REFUSED" in rendered
    assert "zero" in rendered.lower()
    assert base  # the repo itself is fine; only the ladder's shas are absent


def test_exit_code_4_when_a_ladder_is_refused(lrc, tmp_path, base_repo):
    """The refusal must reach the EXIT STATUS, not only the text — a caller
    piping this report would otherwise read a clean 0."""
    repo, _base = base_repo
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({
        "pr": 5, "head": "1" * 40, "base": "main",
        "comments": [_block(1, "0" * 40, "1" * 40)],
    }), encoding="utf-8")

    rc = lrc.main(
        ["--facts-file", str(facts), "--repo-dir", str(repo)],
        out_stream=open("/dev/null", "w"),
    )
    assert rc == lrc.EXIT_REFUSED


# --------------------------------------------------------------------------- #
# Holes with no size — the other two routes to #1233's shape
# --------------------------------------------------------------------------- #

def test_a_bare_audited_sha_is_reported_as_a_hole_with_no_size(lrc, ad,
                                                               base_repo):
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    r2_to = _commit(repo, "b.py", 10, "round 2 fix")
    comments = [
        _block(1, base, r1_to),
        f"```audit-claims round=2 audited={r2_to}\n1. a claim\n```\n",
    ]

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 6, r2_to, "main",
                           comments)

    assert L.bare == [2]
    assert L.blocks_used == 1
    rendered = lrc.render([L], [])
    assert "BARE" in rendered
    assert "No SIZE is reportable" in rendered


def test_an_unparsed_block_is_reported_rather_than_dropped(lrc, ad, base_repo):
    """`parse_claims_blocks` reports a malformed block; dropping that report
    would make an unreadable round look like a round that never happened."""
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    comments = [
        _block(1, base, r1_to),
        "```audit-claims round=2 audited=aaaaaaa..bbbbbbb\n1. never closed\n",
    ]

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 7, r1_to, "main",
                           comments)

    assert L.malformed, "the unclosed fence must be reported"
    assert "UNPARSED BLOCK" in lrc.render([L], [])


def test_a_ledger_starting_at_round_2_says_round_1_is_out_of_window(lrc, ad,
                                                                    base_repo):
    """The #1108 / #1219 case. The window starts at the first block's `from`, so
    round 1's churn is outside it — that must be SAID, not silently excluded."""
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix, never ledgered")
    r2_to = _commit(repo, "b.py", 10, "round 2 fix")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 1108, r2_to, "main",
                           [_block(2, r1_to, r2_to)])

    assert L.first_round == 2
    rendered = lrc.render([L], [])
    assert "STARTS at round 2" in rendered
    assert "outside the window" in rendered
    assert base


# --------------------------------------------------------------------------- #
# The shared core — the extraction must not have changed `measure_ledger`
# --------------------------------------------------------------------------- #

def test_the_commit_count_beside_the_lines_is_the_CHURN_population(lrc, ad,
                                                                   base_repo):
    """🔴 Regression, and the wrong number was the FLATTERING one.

    MEASURED on devrc #1046's tail: this report printed `55 commit(s), 1105
    line(s)` when the churn population was TWO commits — a 66-line fix and a
    1,039-line semantic-conflict resolution. The other 53 were an upstream
    bring-in that `--not <base>` excludes from the churn and that the raw
    `rev-list --count` included. Pairing them makes a real finding ("two commits
    nobody audited, one of them a conflict resolution") read as routine drift
    across 55 commits.

    The fixture reproduces that shape: a gap whose commits are MOSTLY already in
    the base. **FOUR** upstream commits, not one, so the two counts differ by a
    margin no off-by-one can explain — 6 in the range, 2 contributing churn.

    ⚠ The churn population is 2, not 1, and that is CORRECT: the merge commit is
    not reachable from `main` either, so it belongs to the population while
    contributing zero lines. My first version of this test asserted 1 and the
    code was right — the same shape as #1046, where that merge contributed 1,039
    lines of conflict resolution rather than none.
    """
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 10, "round 1 fix")
    # Four commits that land on `main` — excluded from churn by `--not main`.
    _git(repo, "checkout", "--quiet", "main")
    for i in range(4):
        _commit(repo, f"up{i}.py", 8, f"upstream {i}")
    _git(repo, "checkout", "--quiet", "feat")
    _git(repo, "merge", "--quiet", "--no-edit", "main")
    # …and ONE that does not.
    head = _commit(repo, "mine.py", 5, "the only churn-contributing commit")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 11, head, "main",
                           [_block(1, base, r1_to)])

    tail = L.adjacencies[-1]
    assert tail.label == lrc.GAP
    assert (tail.added, tail.deleted) == (5, 0), "only `mine.py` is churn"
    assert tail.commits == 2, (
        f"the reported count is {tail.commits}; it must be the churn population "
        "(2 — the merge plus `mine.py`), not the raw range"
    )

    raw = ad.measure_range_churn(ad.real_runner, str(repo), r1_to, head, "main")
    assert raw.commits == 6 and raw.churn_commits == 2, (
        "the fixture must make the two populations differ by more than one, or "
        f"this guard cannot see the bug (commits={raw.commits}, "
        f"churn_commits={raw.churn_commits})"
    )


# --------------------------------------------------------------------------- #
# The gap-commit census — self-declared round references
# --------------------------------------------------------------------------- #

# 🔴 A TWO-WAY LEDGER OF REAL SUBJECTS, not invented ones. Every positive is a
# commit subject that actually appeared in a measured tail gap (devrc,
# homelab-talos, civit-datapacket-talos, 2026-09-11) plus `audit-dispatch.py`'s
# own dispatch wording. Every negative is a real subject from the SAME gaps that
# must NOT match — including two that a looser pattern would catch
# (`re-trigger`, `widen the … guard`) and one that names a rule number.
_ROUND_REF_POSITIVES = [
    "fix(telemetry,find-session): audit round 5 — the retraction never reached the code",
    "fix(audit r3): parse the packet length by index(), not regex — and END the ladder",
    "fix(comic-flex): audit round 3 — retract a second wrong reason",
    "ci(vetr): close the round-2 audit — F1's shape repeated ONE LAYER DOWN",
    "docs(external-ip-source-drift): round-3 audit — three numbers this ladder staled",
    "fix(autoremix): audit round 9 — pin the word 'unconditionally'",
    "docs(alerts): audit round 5 drive-by — rule 1's selector needs ONE conjunct",
    "Delta re-audit round 4 of PR 900",
]
_ROUND_REF_NEGATIVES = [
    "fix(handoff_doc): rule (i) SHADOWED the stale-base refusal in every realistic repo",
    "merge: main into fix/handoff-doc-absent-base-refusal",
    "feat(find-session): search the OTHER hosts' Claude corpora",
    "docs(clawgate): two seam-guard comments claimed more than the code does",
    "chore(ci): re-trigger — devrc-pytests returned a false red under load",
    "test(gate): widen the conditional-pin guard to its docstring — all, not any",
]


def test_the_round_reference_pattern_is_pinned_BOTH_ways(lrc):
    """🔴 The instrument validation, and both halves are mandatory.

    A pattern that matches nothing makes the census report a reassuring ZERO
    round references — indistinguishable from a corpus with none. A pattern that
    matches too much turns every `fix(...)` into self-declared audit surface and
    inflates the one number this tool is willing to assert.

    Measured: 8 of 8 positives, 0 of 6 false positives. These are the subjects
    the hand classification read, so agreement here is agreement with a human
    pass over the same commits.
    """
    missed = [s for s in _ROUND_REF_POSITIVES if not lrc._ROUND_REF_RE.search(s)]
    assert not missed, f"the pattern cannot see a real round reference: {missed}"
    false_pos = [s for s in _ROUND_REF_NEGATIVES if lrc._ROUND_REF_RE.search(s)]
    assert not false_pos, f"the pattern over-matches: {false_pos}"


def test_the_gap_commit_listing_EXCLUDES_the_base(lrc, ad, base_repo):
    """🔴 Omitting `--not <base>` inverts the conclusion, so it is asserted.

    MEASURED: listing devrc #1046's tail without it showed 55 of `main`'s own
    squash commits, which reads exactly like "the PR kept developing" — the
    hypothesis under test, confirmed by commits that contribute no churn at all.
    Here the fixture puts THREE base commits in the range and one branch commit;
    the listing must return the branch commit (plus the merge), never the three.
    """
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 6, "round 1 fix")
    _git(repo, "checkout", "--quiet", "main")
    for i in range(3):
        _commit(repo, f"up{i}.py", 4, f"upstream {i} — MUST NOT be listed")
    _git(repo, "checkout", "--quiet", "feat")
    _git(repo, "merge", "--quiet", "--no-edit", "main")
    head = _commit(repo, "mine.py", 3, "fix(thing): audit round 7 — the real one")

    commits, why = lrc.classify_gap_commits(
        lrc.real_runner, str(repo), r1_to, head, "main")

    assert why is None, why
    subjects = [c.subject for c in commits]
    assert not [s for s in subjects if "MUST NOT be listed" in s], subjects
    assert any(c.round_ref for c in commits), subjects
    assert any(c.is_merge for c in commits), "the merge belongs to the population"
    assert base and ad


def test_a_gap_whose_commits_cannot_be_LISTED_says_so(lrc, ad, base_repo):
    """🔴 A listing failure must not read as "a gap with zero round references".

    Same rule as the churn refusal: an empty result and a broken command are the
    same observable, so the broken one has to name itself.
    """
    repo, _base = base_repo
    commits, why = lrc.classify_gap_commits(
        lrc.real_runner, str(repo), "no-such-ref", "HEAD", "main")
    assert commits == []
    assert why and "exited" in why

    # …and the reason must survive into the rendered report.
    fake = lrc.Adjacency(
        lrc.GAP, "a" * 40, "b" * 40, 1, None, 5, 0, 1, None,
        [lrc.GapCommit("", [], "COULD NOT LIST: boom", False, False)])
    L = lrc.Ladder(1, "b" * 40, "main", 1, 1, 1, [fake], 10, 5, 0,
                   (0, 0), (5, 0), None, [], [])
    rendered = lrc.render([L], [])
    assert "COULD NOT LIST: boom" in rendered
    assert ad


def test_the_census_does_NOT_call_the_remainder_development(lrc, ad, base_repo):
    """🔴 The honesty clause, asserted rather than trusted.

    The whole point of reporting only self-declared and structural buckets is
    that the leftover is UNKNOWN. A census that labelled it "development" would
    be the guess-dressed-as-measurement this tool was written to replace — and it
    would invert the finding, since the hand pass found 20 fixes among 32 while
    only 8 declared a round.
    """
    repo, base = base_repo
    r1_to = _commit(repo, "a.py", 6, "round 1 fix")
    head = _commit(repo, "b.py", 4, "fix(thing): something with no round named")

    L = lrc.measure_ladder(ad, lrc.real_runner, str(repo), 12, head, "main",
                           [_block(1, base, r1_to)])
    rendered = lrc.render([L], [])

    assert "GAP-COMMIT CENSUS" in rendered
    assert "unclassified   1" in rendered
    assert "NOT 'ordinary development'" in rendered
    assert "FLOOR ON UNLEDGERED ROUNDS, NEVER A RATE" in rendered
    # the un-declared commit must NOT be counted as a round reference
    assert "ROUND-REF     0" in rendered
    # 🔴 ROUND-REF must be described as an UNLEDGERED ROUND, never as evidence
    # the code was unaudited. Round 0 of #1576 found the original wording
    # asserted the opposite of what the commit subject says.
    assert "UNLEDGERED ROUND" in rendered
    assert "NOT evidence the code" in rendered
    # 🔴 and the census must SPLIT interior from tail — this file forbids summing
    # them for lines, and the first census summed them for commits anyway.
    assert "🔴 INTERIOR  round-ref" in rendered
    assert "TAIL      round-ref" in rendered
    assert "READ THE SPLIT, NOT THE TOTAL" in rendered


def test_measure_range_churn_does_NOT_refuse_an_empty_range(ad, base_repo):
    """The inverted read rule, asserted directly.

    `measure_ledger` treats an empty range as a defect; the gap scanner needs
    the opposite, and a shared core enforcing the delta round's rule would
    report every TIGHT chain as unmeasurable.
    """
    repo, base = base_repo
    _commit(repo, "a.py", 4, "one commit")
    head = _git(repo, "rev-parse", "HEAD")

    rep = ad.measure_range_churn(
        ad.real_runner, str(repo), head, head, "main")

    assert rep.reason is None, "an empty range is NOT an error for this caller"
    assert (rep.commits, rep.added, rep.deleted) == (0, 0, 0)
    assert base


def test_measure_ledger_still_refuses_an_empty_range(ad, base_repo):
    """The delegation's behaviour control: the fourth read rule still lives in
    the caller, so `measure_ledger` must still fail on a self-range."""
    repo, base = base_repo
    _commit(repo, "a.py", 4, "one commit")
    head = _git(repo, "rev-parse", "HEAD")

    rep = ad.measure_ledger(ad.real_runner, str(repo), head, "main")

    assert rep.reason is not None, "an empty delta range is still a defect"
    assert "EMPTY" in rep.reason
    assert rep.added is None and rep.commits is None
    assert base


def test_measure_ledger_still_measures_a_real_range(ad, base_repo):
    """…and the positive control for that one: it must still return numbers."""
    repo, base = base_repo
    _commit(repo, "a.py", 9, "the fix")
    head = _git(repo, "rev-parse", "HEAD")

    rep = ad.measure_ledger(ad.real_runner, str(repo), base, "main")

    assert rep.reason is None, rep.reason
    assert rep.commits == 1
    assert rep.added == 9 and rep.deleted == 0
    assert head


def test_a_failed_git_call_is_a_reason_not_a_zero(ad, base_repo):
    """rc != 0 must not become churn 0 — the rule the core exists to hold."""
    repo, _base = base_repo

    rep = ad.measure_range_churn(
        ad.real_runner, str(repo), "no-such-ref", "HEAD", "main")

    assert rep.reason is not None
    assert rep.added is None and rep.commits is None


# --------------------------------------------------------------------------- #
# The script is reachable as a script, and reuses rather than re-implements
# --------------------------------------------------------------------------- #

def test_the_script_runs_and_its_usage_does_not_require_a_network():
    p = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr
    assert "--facts-file" in p.stdout


def test_the_batterys_floor_is_re_derived_from_this_modules_size():
    """🔴 `mutants-ladder-range-coverage.sh`'s `MIN_TESTS` must track THIS module.

    That battery reads pytest's own count and calls anything below `MIN_TESTS`
    a broken harness. A floor left behind as the module grows never complains —
    it is invisible precisely because it only fires downward — and
    `mutants-audit-ladder.sh` has recorded that happening TWICE, the second time
    tolerating the silent loss of both guards the growth had added. So the number
    is pinned here from this battery's first commit rather than maintained by
    memory, and this test prints the replacement value on failure.

    The formula is `run-tests.sh`'s own: `m - min(50, max(1, m // 20))`.
    """
    battery = SCRIPTS / "tests" / "mutants-ladder-range-coverage.sh"
    assert battery.exists(), "the battery this floor belongs to is gone"

    declared = None
    for line in battery.read_text(encoding="utf-8").splitlines():
        if line.startswith("MIN_TESTS="):
            declared = int(line.split("=", 1)[1].strip())
            break
    assert declared is not None, "no MIN_TESTS= literal in the battery"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(Path(__file__).resolve()),
         "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
    )
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    collected = len([
        ln for ln in p.stdout.splitlines()
        if "::" in ln and ln.startswith("scripts/tests/")
    ])
    # 🔴 A positive control on the COUNT, not just on the comparison: a parse
    # that silently yields 0 would make any floor look generous.
    assert collected > 5, (
        f"--collect-only parsed {collected} test(s) from this module, which is "
        f"not a credible count — the floor below would be vacuous.\n{p.stdout[-2000:]}"
    )
    expected = collected - min(50, max(1, collected // 20))
    assert declared == expected, (
        f"this module now collects {collected} test(s), so the battery's floor "
        f"should be {expected}, not {declared}. Set `MIN_TESTS={expected}` in "
        f"{battery.name} — do not compute it by hand."
    )


def test_it_imports_the_churn_command_rather_than_carrying_a_copy():
    """🔴 A SECOND COPY OF THE CHURN COMMAND IS THE DEFECT THIS GUARDS.

    The review's hole was found because the per-block measurement was correct
    and incomplete. A re-typed `--remerge-diff` line here could drift from
    `audit-dispatch.py`'s — which is the one the SKILL tells an auditor to run —
    and the two would disagree silently. Asserted structurally: this script must
    not contain the command, and must call the shared function.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    command_lines = [
        ln for ln in src.splitlines()
        if "--remerge-diff" in ln and not ln.lstrip().startswith("#")
        and '"""' not in ln
    ]
    assert not command_lines, (
        "this script re-types the churn command instead of importing it: "
        f"{command_lines}"
    )
    assert "measure_range_churn" in src
    assert "parse_claims_blocks" in src
