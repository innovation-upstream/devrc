"""`scripts/stale-base-triage.py` — is a PR's red inherited from a stale base?

WHY THIS FILE EXISTS. Half the genuine `tekton/devrc-pytests` failures measured
in the post-#1458 window were one already-fixed test re-reported by PRs 17-40
commits behind `main`. The script under test assembles the deterministic
evidence for that — blob OIDs and commit lists, no test execution — and these
tests are what say the evidence is real.

🔴 WHAT THESE TESTS ARE, HONESTLY LABELLED.

  * CONTROLS first, because every assertion below is a reassuring green if the
    harness cannot reach the script, cannot see a write, or can only ever
    produce one verdict.
  * PURE-FUNCTION CONTRACTS on the parsers and the classifier, driven directly,
    because the end-to-end path folds several of their distinctions away and
    cannot see them (a truncated name and a missing count both arrive at the
    caller as "COULD NOT MEASURE").
  * EVIDENCE CONTRACTS driven against a REAL temporary git repository. Nothing
    about git is mocked here: the blob OIDs, the merge-base and the commit lists
    are git's own answers about a tree this file builds. A mocked git would have
    made the `git diff --quiet` and squash-ancestry traps untestable, which is
    the whole reason they are the traps they are.
  * WRITE-PATH guards, including the pinned `"off"` literal. The script is a
    reporter; arming it must cost a visible line in a diff.
  * STATIC guards pinning relationships no behavioural test can see — that the
    roll-up endpoint is never requested, that the env-knob ledger is two-way,
    and that every test the script cites by name exists.

🔴 NO REAL GITHUB CALL IS MADE ANYWHERE IN THIS FILE. Every test points
`STALE_BASE_TRIAGE_GH` at a stub that serves canned JSON from tmp_path and
records its argv, so a POST in a test is a line in a receipt file and never a
comment on anybody's PR.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

SCRIPT = ROOT / "scripts" / "stale-base-triage.py"
SRC_SCRIPT = SCRIPT.read_text(encoding="utf-8")

RC_OK, RC_INHERITED, RC_UNMEASURED, RC_USAGE = 0, 10, 11, 2


def _load():
    """Import the script as a module so pure functions can be driven directly."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sbt", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


# ══ the fixture repository ════════════════════════════════════════════════════
# 🔴 FIELD VALUES ARE PAIRWISE DISTINCT, AND DISTINCT FROM ANY CONSTANT THE
# ASSERTIONS NAME. A fixture whose two files, two test names and two commit
# subjects collide cannot see a mutant that hardcodes one of them — it survives
# a fully green suite. So: two differently-named files, four differently-named
# tests, and commit subjects that share no word.

class Repo:
    def __init__(self, path):
        self.path = path
        self.hooks = path.parent / "nohooks"
        self.hooks.mkdir(parents=True, exist_ok=True)

    def git(self, *args, check=True):
        proc = subprocess.run(
            ["git", "-C", str(self.path),
             "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={self.hooks}",
             *args],
            capture_output=True, text=True, timeout=60)
        if check and proc.returncode != 0:
            raise AssertionError(f"git {args}: {proc.stderr}")
        return proc

    def write(self, rel, body):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return rel

    def commit(self, rel, subject):
        self.git("add", "--", rel)
        self.git("commit", "-m", subject)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def sha(self, ref):
        return self.git("rev-parse", ref).stdout.strip()


ALPHA = "scripts/tests/test_alpha_module.py"
BRAVO = "scripts/tests/test_bravo_module.py"

ALPHA_V0 = (
    "def test_the_alpha_invariant_holds():\n    assert 0\n\n\n"
    "class TestTheAlphaSuite:\n"
    "    def test_the_shared_method_name(self):\n        assert 0\n"
)
ALPHA_V1 = (
    "def test_the_alpha_invariant_holds():\n    assert 1\n\n\n"
    "class TestTheAlphaSuite:\n"
    "    def test_the_shared_method_name(self):\n        assert 1\n"
)
ALPHA_V2 = (
    "def test_the_alpha_invariant_holds():\n    assert 2\n\n\n"
    "class TestTheAlphaSuite:\n"
    "    def test_the_shared_method_name(self):\n        assert 2\n"
)
BRAVO_V1 = (
    "def test_the_bravo_invariant_holds():\n    assert 1\n\n\n"
    "def test_the_bravo_invariant_holds_under_load():\n    assert 1\n\n\n"
    "class TestTheBravoSuite:\n"
    "    def test_the_shared_method_name(self):\n        assert 1\n"
)


@pytest.fixture
def repo(tmp_path):
    """main and pr diverge at C0; main then FIXES the alpha file.

        S ── C0(main: rewrite ALPHA) ── C1(main: rewrite ALPHA)   <- main
                     └── P1(pr: rewrite BRAVO)                    <- pr

    So `test_the_alpha_invariant_holds` is the INHERITED case (main moved its
    file, the PR did not) and `test_the_bravo_invariant_holds` is the
    NOT-EXPLAINED case (the PR's own change owns that file).

    S exists so that a test can pass an OLDER merge-base on purpose and get a
    candidate that IS already in the head — the case the `not in_head` filter
    is for, and the one a single-merge-base history cannot otherwise produce.
    """
    r = Repo(tmp_path / "fixture")
    r.path.mkdir(parents=True)
    r.git("init", "-b", "main", "--quiet")
    r.write(ALPHA, ALPHA_V0)
    r.write(BRAVO, BRAVO_V1)
    r.git("add", "--", ALPHA, BRAVO)
    r.git("commit", "-m", "seed both modules")
    r.seed = r.sha("HEAD")
    r.write(ALPHA, ALPHA_V1)
    r.pre = r.commit(ALPHA, "settle alpha before the branch point")
    r.merge_base = r.sha("HEAD")
    r.git("checkout", "--quiet", "-b", "pr")
    r.write(BRAVO, BRAVO_V1.replace("assert 1", "assert 3"))
    r.pr_head = r.commit(BRAVO, "the branch rewrites bravo")
    r.git("checkout", "--quiet", "main")
    r.write(ALPHA, ALPHA_V2)
    r.fix = r.commit(ALPHA, "repair the alpha invariant on trunk")
    return r


def triage(r, name, main_ref="main", head_ref="pr"):
    return M.triage_one_test(r.path, main_ref, head_ref, r.merge_base, name)


# ══ CONTROLS ══════════════════════════════════════════════════════════════════

def test_CONTROL_the_evidence_path_can_produce_ALL_THREE_verdicts(repo):
    """🔴 THE FIRST THING TO DOUBT IS AN INSTRUMENT WITH ONE ANSWER.

    Every assertion below reads as meaningful only if the function can actually
    reach each arm. Three fixtures, three distinct verdicts, one assertion.
    """
    got = {
        triage(repo, "test_the_alpha_invariant_holds")["verdict"],
        triage(repo, "test_the_bravo_invariant_holds")["verdict"],
        triage(repo, "test_a_name_no_file_in_this_tree_defines")["verdict"],
    }
    assert got == {M.VERDICT_INHERITED, M.VERDICT_NOT_STALE, M.VERDICT_UNMEASURED}


def test_CONTROL_the_gh_stub_is_reached_and_its_argv_is_recorded(harness, repo):
    """A write-path test that never reaches the stub proves nothing about
    writes. This pins that the receipt file CAN move."""
    harness.serve_default(repo)
    before = harness.calls()
    proc = harness.run(repo, "--pr", "7")
    after = harness.calls()
    assert len(after) > len(before), proc.stdout
    assert any("/repos/o/r/pulls/7" in c for c in after), after


# ══ PURE: status classification ═══════════════════════════════════════════════

def test_classify_maps_a_row_to_its_documented_class():
    """🔴 ONLY `failure` IS RED. Every broken-gate outcome arrives as `error`
    and each keeps its own name, because the caller folds them together and an
    end-to-end test can therefore only ever see "not a verdict"."""
    assert M.classify("success", "TOTAL collected=1") == "green"
    assert M.classify("failure", "FAILED: pytests") == "red"
    assert M.classify("pending", "devrc gate running") == "pending"
    assert M.classify("error", "superseded by a newer run") == "superseded"
    assert M.classify("error", "KILLED: the gate pod died") == "killed"
    assert M.classify("error", "NO GATE POD") == "no-gate-pod"
    assert M.classify("error", "clone rc 128") == "error-other"
    # 🔴 A state GitHub adds tomorrow must not fold into green.
    assert M.classify("queued", "") == "error-other"
    assert M.classify(None, None) == "error-other"


def test_newest_per_context_is_order_independent():
    """🔴 THE TRAP THIS EXISTS FOR: GitHub returns the array NEWEST-FIRST, so a
    last-wins dict yields the OLDEST post per context — an agent shipped that
    and reported a known-green head as `pending`. Folding on max(created_at) is
    right under either order, so both orders are fed here and pinned to agree.
    """
    old = {"context": "tekton/devrc-pytests", "state": "pending",
           "created_at": "2026-09-11T10:00:00Z", "description": "running"}
    new = {"context": "tekton/devrc-pytests", "state": "failure",
           "created_at": "2026-09-11T17:00:00Z", "description": "FAILED"}
    for order in ([new, old], [old, new]):
        got = M.newest_per_context(order)["tekton/devrc-pytests"]
        assert got["state"] == "failure", order
    # A second context is kept independently, not overwritten by the first.
    both = M.newest_per_context([new, old, {"context": "tekton/devrc-nodetests",
                                            "state": "success",
                                            "created_at": "2026-09-11T17:00:00Z"}])
    assert set(both) == {"tekton/devrc-pytests", "tekton/devrc-nodetests"}


# ══ PURE: description parsing ═════════════════════════════════════════════════
# REAL descriptions, copied from statuses THIS repo posted about its OWN tests.
# No third-party text, no hostnames, no captured content — and they are here
# because every synthetic description anybody writes by hand is conveniently
# complete, so the truncation hazard is only demonstrable with real ones.
REAL_ONE_NAMED_COMPLETE = (
    "FAILED: pytests — FAILING: test_no_test_writes_a_usr_bin_env_shebang_at_runtime"
    " | TOTAL collected=22100  passed=22097  skipped=2  failed=1")
REAL_CUT_BEFORE_THE_COUNT = (
    "FAILED: pytests — FAILING: test_each_ledgered_site_still_QUOTES_the_figure_"
    "rather_than_ASSERTING_it | TOTAL collected=22148  passed=22145 ")
REAL_FIVE_FAILED_ONE_NAMED = (
    "FAILED: pytests — FAILING: test_agent_without_any_tab_is_untouched | TOTAL "
    "collected=21036  passed=21028  skipped=3  failed=5  (floor: 184")
REAL_NO_NAMES_AT_ALL = (
    "FAILED: pytests — TOTAL collected=15464  passed=15461  skipped=2  failed=1"
    "  (floor: 13732 = sum of 27 per-target floors)")
REAL_CUT_MID_NAME = (
    "FAILED: pytests — FAILING: TestARefusedWriteIsIndistinguishableFromAnAbsentOne"
    ".test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_dif")


def test_parse_failing_names_reads_the_names_and_stops_at_TOTAL():
    assert M.parse_failing_names(REAL_ONE_NAMED_COMPLETE) == [
        "test_no_test_writes_a_usr_bin_env_shebang_at_runtime"]
    assert M.parse_failing_names(REAL_NO_NAMES_AT_ALL) == []
    assert M.parse_failing_names(
        "FAILED: pytests — FAILING: test_one | test_two | TOTAL collected=3") == [
        "test_one", "test_two"]


def test_a_trailing_TOTAL_fragment_is_not_a_test_name():
    """🔴 A 140-CHAR CUT LANDING INSIDE ` | TOTAL` LEAVES A FRAGMENT WHERE A NAME
    WOULD BE. Left in, it resolves to no file and the PR is reported as a real
    red on the strength of a truncation artefact — the exact false negative this
    tool must not produce. Only the four proper prefixes of TOTAL are stripped,
    and only in final position: every test in this repo begins `test_` or
    `Test`, none of which is a prefix of `TOTAL`, so a real name cannot be eaten.
    """
    for frag in ("T", "TO", "TOT", "TOTA"):
        assert M.parse_failing_names(f"FAILING: test_real_name | {frag}") == [
            "test_real_name"], frag
    # NOT stripped: a real name, and a fragment that is not in final position.
    assert M.parse_failing_names("FAILING: TestTheThing") == ["TestTheThing"]
    assert M.parse_failing_names("FAILING: TOT | test_real_name") == [
        "TOT", "test_real_name"]


def test_names_are_provably_complete_only_when_the_count_agrees():
    """🔴 THE GUARD THAT STOPS A REAL RED BEING DISMISSED. A description can
    prove "at least one test failed and here is its name"; it can never prove
    "these are all of them" unless `failed=N` survived the cap AND equals the
    number of names. All three failure shapes below are REAL rows."""
    ok, why = M.names_provably_complete(REAL_ONE_NAMED_COMPLETE)
    assert ok and "failed=1" in why
    for desc, fragment in ((REAL_CUT_BEFORE_THE_COUNT, "truncated"),
                           (REAL_FIVE_FAILED_ONE_NAMED, "failed=5"),
                           (REAL_NO_NAMES_AT_ALL, "names no failing test")):
        ok, why = M.names_provably_complete(desc)
        assert not ok, desc
        assert fragment in why, (desc, why)


# ══ PURE: the DERIVED completeness proof ══════════════════════════════════════
# 🔴 WHY SYNTHETIC, AND WHY THEY STILL LAND ON THE REAL BOUNDARY. This repo is
# PUBLIC, so the rows below are BUILT, not pasted. What is copied from the
# measurement is only the SHAPE: a banner cut by GitHub's cap at 140 BYTES —
# which is 138 CHARACTERS once the `—` in `FAILED: pytests —` is counted as the
# three UTF-8 bytes it is, and that is exactly why every real row measured on
# this repo reads `len(desc)=138` rather than 140. Cutting on bytes is what puts
# a synthetic fixture on the same boundary a real one lands on; cutting on
# characters would land two bytes late and quietly leave `failed=` readable,
# which is the whole hazard these tests are about.
GITHUB_DESCRIPTION_BYTE_CAP = 140


def cut_like_github(desc):
    """Truncate to the 140-BYTE cap, never splitting a character."""
    raw = desc.encode("utf-8")[:GITHUB_DESCRIPTION_BYTE_CAP]
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def synth_row(name, collected, passed, skipped, failed):
    """A `devrc-pytests` banner in the shape `run-tests.sh` + the pipeline post."""
    return (f"FAILED: pytests — FAILING: {name} | TOTAL collected={collected}"
            f"  passed={passed}  skipped={skipped}  failed={failed}"
            "  (floor: 18000 = sum of 30 per-target floors)")


# The three rows the tool's own requirement was measured on, reproduced at their
# real shape with invented test names of the real lengths. Field values are
# pairwise distinct so a mutant hardcoding one of them cannot survive. Each is
# kept as the (uncut, cut) PAIR so the control below can prove the cap really
# landed inside `failed=` rather than merely that the row is short.
UNCUT_AND_CUT = [
    (lambda d: (d, cut_like_github(d)))(synth_row(name, c, p, s, 1))
    for name, c, p, s in (
        ("test_the_alpha_invariant_holds_when_the_ledger_is_empty", 22021, 22018, 2),
        ("test_the_delta_ledger_is_rebuilt_from_the_spool_on_boot", 21964, 21960, 3),
        ("test_the_gamma_pill_reports_its_own_sample_population", 22081, 22076, 4),
    )
]
CUT_INSIDE_FAILED = [cut for _, cut in UNCUT_AND_CUT]


def test_CONTROL_the_synthetic_rows_land_on_the_real_140_BYTE_boundary():
    """🔴 THE FIXTURE IS THE INSTRUMENT HERE, so it is validated before anything
    is concluded from it. If these rows were not actually cut inside `failed=`,
    every assertion below would be about the DIRECT route and the derived one
    would never run — a green proving nothing.

    "Inside `failed=`" is asserted against the UNCUT row: the cut string is a
    strict prefix of a banner that did print `failed=N`, and what survived stops
    short of a readable count. The real rows end `…skipped=2  faile` / `…  fa`,
    so the tail is a fragment of the word and cannot be matched for directly.
    """
    for full, desc in UNCUT_AND_CUT:
        assert "  failed=1" in full, full
        assert full.startswith(desc) and len(desc) < len(full), (desc, full)
        assert len(desc) == 138, (len(desc), desc)
        assert len(desc.encode("utf-8")) == GITHUB_DESCRIPTION_BYTE_CAP, desc
        # The cut landed AFTER `skipped=` and BEFORE a readable `failed=N`.
        assert M._TOTALS_RE.search(desc) is not None, desc
        assert M.parse_failed_count(desc) is None, desc
        assert M.parse_failed_count(full) == 1, full
        assert len(M.parse_failing_names(desc)) == 1, desc


def test_a_row_CUT_INSIDE_failed_is_PROVEN_complete_by_the_derived_count():
    """🔴 F2. `failed=` is the LAST field in the banner, so whether it survives
    the cap is decided by the failing test's NAME LENGTH — and on the three PRs
    this tool was justified by it did not survive, so the verdict was
    COULD NOT MEASURE with the answer already in the row.

    `collected`, `passed` and `skipped` all PRECEDE it and do survive, and
    `run-tests.sh` defines `collected = passed + skipped + failed + errors +
    xfailed + xpassed`, so `collected − passed − skipped >= failed >= len(names)`.
    Equality squeezes that shut and PROVES completeness.
    """
    for desc in CUT_INSIDE_FAILED:
        assert M.derived_failure_upper_bound(desc) == 1, desc
        ok, why = M.names_provably_complete(desc)
        assert ok, (desc, why)
        assert "collected−passed−skipped=1" in why, why


def test_SOUNDNESS_the_derived_bound_never_UNDER_counts_the_visible_failed():
    """🔴 THE HALF THAT CAN BE WRONG IN THE DANGEROUS DIRECTION.

    An OVER-count only ever makes this tool withhold a verdict. An UNDER-count
    makes it certify a completeness it does not have and dismiss a real red —
    the one error it must not make. So wherever BOTH values are readable they
    are compared, and the bound must never fall below the banner's own count.

    The table is built from full (uncut) banners so `failed=N` is visible, and
    the three multi-failure rows are the ones that matter: an under-counting
    bound would drag them down toward the single name and certify them.
    """
    table = [
        # name, collected, passed, skipped, failed, xfail+xpass slack
        ("test_one", 21076, 21072, 3, 1, 0),
        ("test_two", 21476, 21471, 2, 3, 0),
        ("test_six", 21036, 21028, 3, 5, 0),
        ("test_fortyish", 21500, 21450, 11, 39, 0),
        # …and one with xfailed/xpassed inflating `collected` above p+s+failed,
        # which is exactly the case where the bound is a strict OVER-count.
        ("test_xf", 21000, 20950, 7, 40, 3),
    ]
    seen = set()
    for name, collected, passed, skipped, failed, slack in table:
        desc = synth_row(name, collected + slack, passed, skipped, failed)
        derived = M.derived_failure_upper_bound(desc)
        count = M.parse_failed_count(desc)
        assert count == failed, desc
        assert derived is not None, desc
        assert derived >= count, (desc, derived, count)      # 🔴 NEVER BELOW
        if slack == 0:
            assert derived == count, (desc, derived, count)  # …and EXACT here
        seen.add((derived, count))
    # The table actually exercised both relations, so "never below" is not a
    # claim about five copies of one row.
    assert any(d == c for d, c in seen) and any(d > c for d, c in seen), seen


def test_a_genuine_MULTI_failure_red_is_STILL_could_not_measure():
    """🔴 THE CONTROL ON THE NEW ROUTE. A row that names ONE test while the
    totals prove 39 failed must NOT be certified complete — with or without
    `failed=N` surviving. Both spellings are driven, because the derived route
    is the one that could newly get this wrong."""
    full = synth_row("test_the_alpha_invariant_holds_when_the_ledger_is_empty",
                      21500, 21450, 11, 39)
    cut = cut_like_github(full)
    assert M.parse_failed_count(full) == 39
    assert M.parse_failed_count(cut) is None, cut     # the row the cap produces
    for desc in (full, cut):
        assert M.derived_failure_upper_bound(desc) == 39, desc
        ok, why = M.names_provably_complete(desc)
        assert not ok, (desc, why)
        assert "39" in why, why


def test_the_DIRECT_route_still_wins_when_failed_N_survived():
    """Route 1 is not replaced, and it is the stronger claim. Pinned by driving
    a row where the two routes would disagree if the order were flipped: the
    slack from an xpassed makes the bound 2 while `failed=1` is right there."""
    desc = synth_row("test_only_one_named", 100, 96, 2, 1)   # collected 100 = 96+2+1+1
    assert M.derived_failure_upper_bound(desc) == 2
    assert M.parse_failed_count(desc) == 1
    ok, why = M.names_provably_complete(desc)
    assert ok and why == "failed=1 and 1 named", why


def test_a_TRUNCATED_skipped_can_only_INFLATE_the_derived_bound():
    """🔴 THE ONE FIELD THE CAP CAN CUT SHORT WITHOUT BEING NOTICED, and the
    reason `_TOTALS_RE` demands the three fields ADJACENT AND IN ORDER.

    `collected=` and `passed=` are each followed by more banner text, so a match
    proves they were not truncated. `skipped=` is last of the three and CAN be
    left short — `skipped=23` arriving as `skipped=2`. Subtracting less inflates
    the bound, which errs toward WITHHOLDING; the opposite would certify.
    """
    full = "FAILED: pytests — FAILING: test_x | TOTAL collected=900  passed=850  skipped=23  failed=27"
    short = full.replace("skipped=23", "skipped=2")
    assert M.derived_failure_upper_bound(full) == 27
    assert M.derived_failure_upper_bound(short) == 48        # inflated, never below
    assert M.derived_failure_upper_bound(short) > M.derived_failure_upper_bound(full)


def test_the_derived_bound_refuses_a_row_it_cannot_read():
    """None, never a number and never a raise — an unreadable row must not
    become a bound of 0, which would certify every single-named row as
    complete, and must not crash the reporting path either."""
    for desc in ("", None, "FAILED: pytests — FAILING: test_x | TOTAL collected=9 ",
                 "FAILED: pytests — FAILING: test_x | TOTAL collected=9  passed=8 ",
                 # out of order: the three fields are read as ONE ordered group,
                 # which is what proves the earlier ones were not cut short.
                 "TOTAL passed=8  collected=9  skipped=0",
                 "TOTAL collected=9  skipped=0  passed=8",
                 # 🔴 CUT EXACTLY ON THE `=`. `skipped=` with NO digits is the
                 # shape a cap landing one character early produces; a `\\d*`
                 # spelling of the group matches it and then `int("")` RAISES on
                 # the reporting path. Refusal is the only safe reading.
                 "TOTAL collected=9  passed=8  skipped=",
                 # arithmetically impossible, so not the banner we parse.
                 "TOTAL collected=9  passed=8  skipped=5"):
        assert M.derived_failure_upper_bound(desc) is None, desc
    # POSITIVE CONTROL: the same parser DOES answer on a well-formed row, so the
    # Nones above are about these rows and not about a parser wired to nothing.
    assert M.derived_failure_upper_bound(
        "TOTAL collected=9  passed=8  skipped=0") == 1


def test_an_UNDERIVABLE_row_says_WHICH_route_failed_not_merely_that_one_did():
    """🔴 THE REASON IS THE PRODUCT HERE, so it is pinned as a whole string.

    Two different refusals reach the operator as COULD NOT MEASURE — "the count
    was cut AND the totals are unreadable" and "the totals say there are more
    failures than were named" — and they call for different next steps. A
    mutant replacing the first with a bound of 0 produces the SAME verdict on
    every input (names are non-empty by the time this runs, so 0 can never
    equal them) and is invisible to a verdict-only assertion; only the text
    distinguishes it.
    """
    ok, why = M.names_provably_complete("FAILED: pytests — FAILING: test_x | TOTAL collected=9 ")
    assert not ok
    assert why == ("the description was truncated before `failed=N`, and "
                   "collected/passed/skipped are not all readable either"), why
    ok, why = M.names_provably_complete(
        "FAILED: pytests — FAILING: test_x | TOTAL collected=9  passed=5  skipped=1  fa")
    assert not ok
    assert why == ("the description was truncated before `failed=N`, and "
                   "collected−passed−skipped=3 does not equal the 1 named"), why


def test_a_banner_that_CONTRADICTS_itself_is_refused_rather_than_believed():
    """🔴 THE TRIPWIRE ON THE COUPLING, HONESTLY LABELLED. While `run-tests.sh`'s
    arithmetic holds, `derived >= failed=N` is guaranteed and this arm cannot
    fire on a real row — it is reachable from a test only. It exists so that an
    arithmetic change upstream surfaces as a refusal to answer rather than as a
    silently wrong bound; the test-time half is the source pin below."""
    desc = "FAILED: pytests — FAILING: test_x | TOTAL collected=9  passed=8  skipped=0  failed=4"
    assert M.derived_failure_upper_bound(desc) == 1
    assert M.parse_failed_count(desc) == 4
    ok, why = M.names_provably_complete(desc)
    assert not ok
    assert "contradicts itself" in why, why


def test_the_run_tests_collected_arithmetic_this_derivation_rests_on_is_pinned():
    """🔴 THE DERIVATION IS A COUPLING TO ANOTHER FILE, SO IT IS PINNED THERE.

    `derived_failure_upper_bound` is sound only because `scripts/run-tests.sh`
    defines `collected` to INCLUDE `passed` and `skipped` and to include every
    term the banner's `failed=` is built from. This reads those expressions out
    of the shell source and checks the containment relation itself, rather than
    asserting a literal string — so a rename of the shell locals is fine and a
    change to WHAT IS SUMMED is not.

    ⚠ WHAT IT CANNOT CHECK: that the remaining terms (`xfailed`, `xpassed`) are
    non-negative counts. That is an arithmetic fact about pytest's summary line,
    not a textual one, and it is stated rather than pinned.
    """
    src = (ROOT / "scripts" / "run-tests.sh").read_text(encoding="utf-8")

    def terms(name):
        m = re.search(rf"^\s*{name}=\$\(\(([^)]*)\)\)", src, re.M)
        assert m, f"no `{name}=$((…))` assignment in run-tests.sh — guard is inert"
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", m.group(1))) - {name}

    collected = terms("collected")
    failed = terms("TOT_FAILED")
    assert collected, "the collected scan matched nothing — this guard is inert"
    assert failed, "the TOT_FAILED scan matched nothing — this guard is inert"
    # The two fields the derivation SUBTRACTS must be summands of `collected`.
    passed_term, = terms("TOT_PASSED")
    skipped_term, = terms("TOT_SKIPPED")
    assert passed_term in collected, (passed_term, collected)
    assert skipped_term in collected, (skipped_term, collected)
    # …and everything the banner's `failed=` is built from must survive the
    # subtraction, which is precisely why the remainder is an UPPER bound.
    # ⚠ CONTAINMENT, NOT EQUALITY, AND DELIBERATELY SO: `run-tests.sh` SHRINKING
    # what it sums into `failed=` keeps the bound an upper bound and is
    # therefore safe — a mutation sweep confirmed that variant survives this
    # guard, correctly. Only a term LEAVING `collected` breaks the derivation.
    assert failed <= collected - {passed_term, skipped_term}, (failed, collected)
    # The accumulators the banner prints are the per-target terms summed.
    assert re.search(r"TOT_COLLECTED\s*\+\s*collected", src)
    # 🔴 EVERY banner line, not merely SOME line. `run-tests.sh` prints TWO —
    # a full-run one and a SCOPED one — and a first version of this guard
    # searched the whole file, so reordering the fields in one of them was
    # satisfied by the other and SURVIVED the sweep. The order is the assumption
    # `_TOTALS_RE` encodes, and it has to hold on every row that can be posted.
    banners = [ln for ln in src.splitlines() if "TOTAL collected=$TOT_COLLECTED" in ln]
    assert len(banners) >= 2, f"expected both TOTAL banners, found {len(banners)}"
    for ln in banners:
        ordered = re.search(
            r"collected=\$TOT_COLLECTED\s+passed=\$TOT_PASSED\s+skipped=\$TOT_SKIPPED\s+"
            r"failed=\$TOT_FAILED", ln)
        assert ordered, f"banner does not print the fields in order: {ln.strip()}"
        # …and the regex this file parses with actually matches that shape.
        assert M._TOTALS_RE.search(
            re.sub(r"\$TOT_(\w+)", "7", ordered.group(0))) is not None, ln


def test_the_prior_art_this_gate_rebuilds_is_CITED_and_still_exists():
    """🔴 A 15-LINE VERSION OF THIS SCREEN ALREADY SHIPPED. Citing it is not
    courtesy: its comment records that it would have fired ZERO times on 100
    commits, and this file's derived route is the explanation for that zero. A
    citation that resolves to nothing reads as authority and is not one."""
    assert "main-status-watch.py" in SRC_SCRIPT
    assert "screen_all_known_flakes" in SRC_SCRIPT
    prior = (ROOT / "scripts" / "main-status-watch.py").read_text(encoding="utf-8")
    assert "def screen_all_known_flakes(" in prior


def test_normalise_test_name_handles_all_three_real_shapes():
    assert M.normalise_test_name("test_plain") == (None, "test_plain")
    assert M.normalise_test_name("TestSuite.test_method") == (
        "TestSuite", "test_method")
    # 🔴 The brackets come off BEFORE the class split: a parametrise id can
    # itself contain a dot and would otherwise be read as the class name.
    assert M.normalise_test_name("test_x[record0-a.b]") == (None, "test_x")
    assert M.normalise_test_name("TestS.test_y[1517-1024]") == ("TestS", "test_y")


# ══ EVIDENCE: the mapping from a test name to its file ════════════════════════

def test_a_name_resolves_to_the_one_file_that_defines_it(repo):
    got = M.resolve_test_file(repo.path, "pr", "test_the_alpha_invariant_holds")
    assert got["status"] == "ok"
    assert got["file"] == ALPHA
    assert got["truncated_name"] is False


def test_an_unknown_name_is_NOT_FOUND_and_never_a_silent_guess(repo):
    got = M.resolve_test_file(repo.path, "pr", "test_this_name_exists_nowhere")
    assert got["status"] == "not-found"
    assert "test_this_name_exists_nowhere" in got["reason"]


def test_a_name_defined_in_two_files_is_AMBIGUOUS(repo):
    """Naming a fix commit off a coin-flip mapping is worse than saying nothing:
    both fixtures define `test_the_shared_method_name`, in different files."""
    got = M.resolve_test_file(repo.path, "pr", "test_the_shared_method_name")
    assert got["status"] == "ambiguous"
    assert sorted(got["files"]) == sorted([ALPHA, BRAVO])


def test_a_class_qualified_name_narrows_an_otherwise_ambiguous_method(repo):
    """The class is a second, independent discriminator, and it only ever
    NARROWS — the same method name lives in both fixture files."""
    got = M.resolve_test_file(repo.path, "pr",
                              "TestTheBravoSuite.test_the_shared_method_name")
    assert got["status"] == "ok"
    assert got["file"] == BRAVO


def test_a_name_TRUNCATED_by_the_140_char_cap_still_resolves(repo):
    """🔴 REAL: a status row ended `…_CAN_see_the_dif`, mid-word. Phase 2 drops
    the opening paren so a prefix can match — and runs ONLY when phase 1 found
    nothing, which is why the next test exists."""
    got = M.resolve_test_file(repo.path, "pr", "test_the_alpha_invariant_ho")
    assert got["status"] == "ok"
    assert got["file"] == ALPHA
    assert got["truncated_name"] is True


def test_a_COMPLETE_name_is_not_made_ambiguous_by_a_longer_sibling(repo):
    """🔴 THE ORDER OF THE TWO PHASES IS THE GUARD. `test_the_bravo_invariant_
    holds` is a strict prefix of `test_the_bravo_invariant_holds_under_load`.
    Phase 1 requires the opening paren, so the exact name wins outright; running
    the prefix phase unconditionally would report a false ambiguity for every
    short name in the repo."""
    got = M.resolve_test_file(repo.path, "pr", "test_the_bravo_invariant_holds")
    assert got["status"] == "ok"
    assert got["truncated_name"] is False


# ══ EVIDENCE: the verdict and its two halves ══════════════════════════════════

def test_an_INHERITED_red_names_the_commit_that_already_fixed_it(repo):
    """The whole point of the tool, end to end through the evidence path."""
    got = triage(repo, "test_the_alpha_invariant_holds")
    assert got["verdict"] == M.VERDICT_INHERITED
    assert got["file"] == ALPHA
    shas = [c["sha"] for c in got["candidates"]]
    assert shas == [repo.fix]
    # 🔴 BOTH HALVES OF THE EVIDENCE PAIR, ASSERTED SEPARATELY.
    assert got["candidates"][0]["on_main"] is True
    assert got["candidates"][0]["in_head"] is False
    assert got["candidates"][0]["subject"] == "repair the alpha invariant on trunk"
    # …and the CONTENT half: the head's copy never moved, main's did.
    assert got["blob_head"] == got["blob_merge_base"]
    assert got["blob_main"] != got["blob_merge_base"]


def test_a_test_whose_file_THIS_PR_MODIFIES_is_not_blamed_on_staleness(repo):
    """🔴 THE DISCRIMINATOR THAT KEEPS THE TOOL HONEST. If the PR's own diff
    contains the failing test's file, staleness does not explain the failure
    whatever main has been doing — and this is the case a mapping-only tool
    would happily call INHERITED."""
    got = triage(repo, "test_the_bravo_invariant_holds")
    assert got["verdict"] == M.VERDICT_NOT_STALE
    assert "the PR itself modifies" in got["reason"]
    assert got["blob_head"] != got["blob_merge_base"]


def test_a_file_UNCHANGED_on_main_is_a_real_red_and_says_so(repo):
    """main cannot have fixed a file it has not touched. Driven by moving the
    main ref back to the merge-base, so main and the head agree exactly."""
    got = triage(repo, "test_the_alpha_invariant_holds", main_ref=repo.merge_base)
    assert got["verdict"] == M.VERDICT_NOT_STALE
    assert "unchanged on main" in got["reason"]
    assert "real red" in got["reason"]


def test_a_file_ABSENT_on_one_side_is_UNMEASURED_not_SAME(repo):
    """🔴 `git diff --quiet <ref> -- <path>` EXITS 0 WHEN THE PATH EXISTS ON
    NEITHER SIDE — a comparison against an absent operand reports SAME, not
    MISSING. Here the test is DELETED on main, which is exactly the shape (a
    rename) that would otherwise read as "unchanged, therefore a real red"."""
    repo.git("rm", "--quiet", "--", ALPHA)
    repo.git("commit", "-m", "retire the alpha module entirely")
    got = triage(repo, "test_the_alpha_invariant_holds")
    assert got["verdict"] == M.VERDICT_UNMEASURED
    assert "does not exist at main" in got["reason"]


def test_path_existence_is_proved_directly(repo):
    """The primitive the test above rests on, driven on its own: it must
    distinguish present from absent, and a ref where the file never existed."""
    assert M.path_exists_at(repo.path, "main", ALPHA) is True
    assert M.path_exists_at(repo.path, "main", "scripts/tests/test_nothing.py") is False


def test_a_candidate_ALREADY_IN_THE_HEAD_is_excluded(repo):
    """🔴 THE HALF OF THE EVIDENCE PAIR THAT IS ACTUALLY A FILTER, driven with
    an older merge-base so a candidate in the range IS in the head.

    Measured: with the real merge-base, dropping `not in_head` from the
    condition SURVIVED the whole suite, because `merge-base..main` cannot
    contain a head ancestor when there is exactly ONE merge-base. `git
    merge-base` returns one base and a criss-cross history has several, so the
    case is real — and this is how it is made reachable.
    """
    # The candidate list is git's own answer over a range that STRADDLES the
    # branch point, so it genuinely contains a commit the head already has.
    raw = M.commits_touching(repo.path, repo.seed, "main", ALPHA)
    assert sorted(c["sha"] for c in raw) == sorted([repo.fix, repo.pre]), raw
    proven = M.prove_candidates(repo.path, raw, "main", "pr")
    assert [c["sha"] for c in proven] == [repo.fix]
    # POSITIVE CONTROL on the filter: the excluded commit was excluded for the
    # stated reason, and it did satisfy the OTHER half.
    excluded = [c for c in raw if c["sha"] == repo.pre][0]
    assert excluded["in_head"] is True
    assert excluded["on_main"] is True


def test_the_on_main_half_is_measured_and_reported_even_though_it_cannot_gate(repo):
    """`on_main` is TRUE BY CONSTRUCTION — `commits_touching` walks
    `merge-base..main`. It is printed so a reader can verify the claim rather
    than trust it, and deliberately NOT part of the filter: a guard that can
    never run reads as coverage and stops anyone looking."""
    got = triage(repo, "test_the_alpha_invariant_holds")
    assert all(c["on_main"] is True for c in got["candidates"])
    assert "if on_main and not" not in SRC_SCRIPT
    # 🔴 AND IT IS MEASURED, NOT ASSUMED. Fed a commit that is genuinely NOT on
    # main — the PR's own head — it must say so; without this the field could
    # be hardcoded True and every test above would still pass.
    fabricated = [{"sha": repo.pr_head, "subject": "a commit main never took"}]
    M.prove_candidates(repo.path, fabricated, "main", "pr")
    assert fabricated[0]["on_main"] is False
    assert fabricated[0]["in_head"] is True


def test_is_ancestor_answers_about_the_head_and_nothing_else(repo):
    """🔴 ANCESTRY IS ASKED ABOUT ONE COMMIT AND ONE HEAD, NEVER "DID THIS
    MERGE?" — this repo squashes, and a squash NEVER makes a branch head an
    ancestor of its base, so the merged reading is a permanent confident FALSE.
    What is pinned here is the question the script actually asks."""
    assert M.is_ancestor(repo.path, repo.fix, "main") is True
    assert M.is_ancestor(repo.path, repo.fix, "pr") is False
    assert M.is_ancestor(repo.path, repo.merge_base, "pr") is True


# ══ PR-LEVEL FOLDING ══════════════════════════════════════════════════════════

def pr_row(desc, state="failure"):
    return {"context": M.CONTEXT, "state": state, "description": desc,
            "created_at": "2026-09-11T17:00:00Z"}


def pr_meta(repo):
    return {"number": 7, "title": "a fixture pull request",
            "head_sha": repo.pr_head, "url": "https://example.invalid/7"}


def test_a_PR_whose_only_named_failure_is_inherited_AND_complete_is_INHERITED(repo):
    got = M.triage_pr(repo.path, "main", pr_meta(repo), pr_row(
        "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL "
        "collected=9  passed=8  skipped=0  failed=1"))
    assert got["verdict"] == M.VERDICT_INHERITED
    assert got["behind"] == 1
    assert got["complete"] is True


def test_ONE_unexplained_name_makes_the_WHOLE_PR_a_real_red(repo):
    """🔴 CONSERVATIVE BY CONSTRUCTION, and this is the mutant that matters: a
    fold that reported INHERITED when ANY name was explained would dismiss a PR
    whose own change is broken. Two names, one of each kind."""
    got = M.triage_pr(repo.path, "main", pr_meta(repo), pr_row(
        "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | "
        "test_the_bravo_invariant_holds | TOTAL collected=9  failed=2"))
    assert got["verdict"] == M.VERDICT_NOT_STALE
    assert "test_the_bravo_invariant_holds" in got["reason"]
    assert got["any_explained"] is True          # …and the hint still fires


def test_an_inherited_name_on_a_TRUNCATED_row_is_UNMEASURED_not_INHERITED(repo):
    """🔴 THE COMPLETENESS GUARD AT PR LEVEL. Every NAMED failure is inherited,
    but the count did not survive the 140-char cap, so other failures may remain
    and the PR must not be dismissed. The per-test line still says what it
    found, which is the useful half."""
    got = M.triage_pr(repo.path, "main", pr_meta(repo), pr_row(
        "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL "
        "collected=9  passed=8 "))
    assert got["verdict"] == M.VERDICT_UNMEASURED
    assert "truncated" in got["reason"]
    assert got["any_explained"] is True
    assert got["tests"][0]["verdict"] == M.VERDICT_INHERITED


def test_a_count_that_EXCEEDS_the_names_is_UNMEASURED(repo):
    """The real `failed=5 … one named` shape."""
    got = M.triage_pr(repo.path, "main", pr_meta(repo), pr_row(
        "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL "
        "collected=9  passed=4  skipped=0  failed=5"))
    assert got["verdict"] == M.VERDICT_UNMEASURED
    assert "failed=5" in got["reason"]


ROW_CUT_INSIDE_FAILED = (
    "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL "
    "collected=9  passed=7  skipped=1  faile")
ROW_CUT_INSIDE_FAILED_MULTI = (
    "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL "
    "collected=9  passed=5  skipped=1  faile")


def test_a_row_CUT_INSIDE_failed_now_reaches_INHERITED_at_PR_level(repo):
    """🔴 F2 AT THE VERDICT. Before the derived route this row — the shape of
    every one of the three PRs the tool was justified by — resolved to
    COULD NOT MEASURE with the correct answer already in it. `failed=` is cut,
    but `collected−passed−skipped = 9−7−1 = 1` equals the one name."""
    got = M.triage_pr(repo.path, "main", pr_meta(repo),
                      pr_row(ROW_CUT_INSIDE_FAILED))
    assert M.parse_failed_count(ROW_CUT_INSIDE_FAILED) is None
    assert got["verdict"] == M.VERDICT_INHERITED
    assert got["complete"] is True
    assert "collected−passed−skipped=1" in got["completeness_reason"]


def test_a_row_CUT_INSIDE_failed_with_MORE_failures_than_names_is_withheld(repo):
    """🔴 THE SAME ROW SHAPE, ONE FIELD DIFFERENT, AND THE ANSWER FLIPS. Without
    this the test above is satisfied by a route that certifies every cut row.
    Here `9−5−1 = 3` against ONE name: two failures are unaccounted for, the
    named one IS inherited, and the PR must still not be dismissed."""
    got = M.triage_pr(repo.path, "main", pr_meta(repo),
                      pr_row(ROW_CUT_INSIDE_FAILED_MULTI))
    assert got["verdict"] == M.VERDICT_UNMEASURED
    assert got["complete"] is False
    assert "collected−passed−skipped=3 does not equal the 1 named" in got["reason"]
    assert got["any_explained"] is True          # …so the HINT still fires


def test_end_to_end_a_row_cut_inside_failed_exits_10_and_names_the_fix(harness, repo):
    """The whole F2 path through the real script: a description GitHub cut
    inside `failed=` now produces the INHERITED exit code and the evidence."""
    harness.serve_default(repo, desc=ROW_CUT_INSIDE_FAILED)
    proc = harness.run(repo, "--pr", "7")
    assert proc.returncode == RC_INHERITED, proc.stdout + proc.stderr
    assert M.VERDICT_INHERITED in proc.stdout
    assert repo.fix[:12] in proc.stdout
    # …and the run SAYS which route proved it, so the claim is auditable.
    assert "collected−passed−skipped=1" in proc.stdout


def test_end_to_end_the_SAME_shape_with_extra_failures_does_NOT_exit_10(harness, repo):
    """🔴 THE NEGATIVE CONTROL ON THE END-TO-END TEST ABOVE. Same script, same
    fixture, same cut — one number different — and the exit code must move.

    ⚠ HONESTLY LABELLED: this one is GREEN at the pre-change base too, because
    the old code withheld every cut row. It is an INVARIANT guard on the new
    route (it must not start dismissing multi-failure reds), NOT regression
    coverage, and it is not counted as such in the red/green matrix.
    """
    harness.serve_default(repo, desc=ROW_CUT_INSIDE_FAILED_MULTI)
    proc = harness.run(repo, "--pr", "7")
    assert proc.returncode == RC_OK, proc.stdout + proc.stderr
    assert M.VERDICT_UNMEASURED in proc.stdout
    assert "HINT: at least one named failure IS inherited" in proc.stdout


def test_a_GREEN_or_PENDING_head_is_not_a_verdict_at_all(repo):
    for state, desc in (("success", "TOTAL collected=9  failed=0"),
                        ("pending", "devrc gate running")):
        got = M.triage_pr(repo.path, "main", pr_meta(repo), pr_row(desc, state))
        assert got["verdict"] is None, state
        assert "tests" not in got


def test_an_ERROR_row_is_reported_as_a_broken_gate_and_never_as_red(repo):
    """🔴 `error` IS NOT `failure`. Every non-code outcome arrives as `error`,
    and over 200 measured rows the roll-up endpoint's conflation turned 9 real
    failures into 48 red-looking pushes."""
    for desc, klass in (("superseded by a newer run", "superseded"),
                        ("KILLED: the gate pod died", "killed"),
                        ("NO GATE POD", "no-gate-pod"),
                        ("COULD NOT RUN: pytests", "error-other")):
        got = M.triage_pr(repo.path, "main", pr_meta(repo), pr_row(desc, "error"))
        assert got["class"] == klass, desc
        assert got["verdict"] is None, desc


def test_a_head_that_is_not_in_the_local_clone_is_UNMEASURED(repo):
    meta = dict(pr_meta(repo), head_sha="0" * 40)
    got = M.triage_pr(repo.path, "main", meta, pr_row(
        "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL failed=1"))
    assert got["verdict"] == M.VERDICT_UNMEASURED
    assert "git fetch origin" in got["reason"]


def test_a_head_with_NO_pytests_row_is_UNMEASURED_never_green(repo):
    got = M.triage_pr(repo.path, "main", pr_meta(repo), None)
    assert got["verdict"] == M.VERDICT_UNMEASURED
    assert got["state"] == "none"


# ══ END TO END, and the write path ════════════════════════════════════════════

class Harness:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.responses = tmp_path / "responses"
        self.responses.mkdir(parents=True, exist_ok=True)
        self.receipt = tmp_path / "gh-calls"
        self.gh = self.bin / "fake-gh"
        # Records EVERY argv, then answers on the first argument that looks like
        # an API path. `-X POST` therefore lands in the receipt exactly like a
        # read does — which is what makes "no write happened" observable.
        write_exec(self.gh,
                   f'printf "%s\\n" "$*" >> "{self.receipt}"\n'
                   'p=""\n'
                   'for a in "$@"; do case "$a" in /*) p="$a"; break;; esac; done\n'
                   f'f=$(printf "%s" "$p" | tr "/?&=" "____")\n'
                   f'if [ -f "{self.responses}/$f" ]; then cat "{self.responses}/$f"; exit 0; fi\n'
                   'echo "no canned response for $p" >&2; exit 22\n')

    def serve(self, path, payload):
        name = "".join("_" if c in "/?&=" else c for c in path)
        (self.responses / name).write_text(json.dumps(payload), encoding="utf-8")

    def serve_default(self, repo, desc=None, state="failure"):
        self.serve("/repos/o/r/pulls/7",
                   {"number": 7, "title": "a fixture pull request",
                    "head": {"sha": repo.pr_head},
                    "html_url": "https://example.invalid/7"})
        self.serve(f"/repos/o/r/commits/{repo.pr_head}/statuses?per_page=100",
                   [pr_row(desc or ("FAILED: pytests — FAILING: "
                                    "test_the_alpha_invariant_holds | TOTAL "
                                    "collected=9  passed=8  skipped=0  failed=1"),
                           state)])
        # Two keys, because the READ is paginated and the WRITE is not — and
        # the stub keys on the literal path. Serving only one of them makes the
        # write path's own failure look like a canned-response gap.
        self.serve("/repos/o/r/issues/7/comments?per_page=100", [])
        self.serve("/repos/o/r/issues/7/comments", {"id": 1})

    def calls(self):
        if not self.receipt.exists():
            return []
        return [c for c in self.receipt.read_text(encoding="utf-8").splitlines() if c]

    def run(self, repo, *args, env=None):
        e = dict(os.environ)
        e.pop("STALE_BASE_TRIAGE_COMMENT_MODE", None)
        e.update({"STALE_BASE_TRIAGE_GH": str(self.gh),
                  "STALE_BASE_TRIAGE_REPO": "o/r"})
        e.update(env or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-path", str(repo.path),
             "--main-ref", "main", *args],
            capture_output=True, text=True, timeout=180, env=e)


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "harness")


def test_end_to_end_an_inherited_PR_exits_10_and_names_the_fix(harness, repo):
    harness.serve_default(repo)
    proc = harness.run(repo, "--pr", "7")
    assert proc.returncode == RC_INHERITED, proc.stdout + proc.stderr
    assert M.VERDICT_INHERITED in proc.stdout
    assert repo.fix[:12] in proc.stdout
    assert "repair the alpha invariant on trunk" in proc.stdout
    assert ALPHA in proc.stdout


def test_end_to_end_a_real_red_exits_0_and_is_not_dismissed(harness, repo):
    harness.serve_default(repo, desc=(
        "FAILED: pytests — FAILING: test_the_bravo_invariant_holds | TOTAL "
        "collected=9  passed=8  skipped=0  failed=1"))
    proc = harness.run(repo, "--pr", "7")
    assert proc.returncode == RC_OK, proc.stdout
    assert M.VERDICT_NOT_STALE in proc.stdout
    assert M.VERDICT_INHERITED not in proc.stdout


def test_the_summary_never_prints_a_bare_zero_for_an_empty_population(harness, repo):
    """🔴 A ZERO MUST CARRY ITS POPULATION. `INHERITED: 0` beside `red: 0` is an
    empty sample; beside `red: 7` it is seven examined reds. The summary prints
    both counts and says so outright when there were no reds at all."""
    harness.serve_default(repo, desc="TOTAL collected=9  failed=0", state="success")
    proc = harness.run(repo, "--pr", "7")
    assert proc.returncode == RC_OK, proc.stdout
    assert "INHERITED:              0" in proc.stdout
    assert "empty sample, not an all-clear" in proc.stdout


def test_a_head_with_NO_status_row_never_prints_a_failure_line(harness, repo):
    """🔴 REGRESSION, MEASURED IN THE FIRST LIVE SWEEP. An earlier draft folded
    "no row" into the red arm and printed `failure   posted ?` directly above a
    verdict reading "no status on this head" — a line asserting a red that was
    never posted, on #359, #355, #612 and #646."""
    harness.serve("/repos/o/r/pulls/7",
                  {"number": 7, "title": "a fixture pull request",
                   "head": {"sha": repo.pr_head}, "html_url": "https://x.invalid/7"})
    harness.serve(f"/repos/o/r/commits/{repo.pr_head}/statuses?per_page=100", [])
    proc = harness.run(repo, "--pr", "7")
    assert "no status on this head" in proc.stdout
    assert "failure" not in proc.stdout, proc.stdout
    assert proc.returncode == RC_OK, proc.stdout


def test_a_broken_gate_is_counted_on_its_OWN_summary_line(harness, repo):
    """🔴 `error` IS NEVER FOLDED INTO THE RED TOTAL. Folding it in is the
    roll-up mistake in a second spelling, and it is the difference between 9
    real failures and 48 red-looking heads."""
    harness.serve_default(repo, desc="superseded by a newer run", state="error")
    proc = harness.run(repo, "--pr", "7")
    assert "broken gate (`error`)" in proc.stdout
    assert "NOT a code failure" in proc.stdout
    assert "red (tekton/devrc-pytests):        0" in proc.stdout
    assert proc.returncode == RC_OK, proc.stdout


def test_the_HINT_is_printed_when_only_SOME_of_the_red_is_inherited(harness, repo):
    """The useful half of a COULD NOT MEASURE: the operator still learns that a
    rebase is worth trying before they start debugging."""
    harness.serve_default(repo, desc=(
        "FAILED: pytests — FAILING: test_the_alpha_invariant_holds | TOTAL "
        "collected=9  passed=8 "))
    proc = harness.run(repo, "--pr", "7")
    assert M.VERDICT_UNMEASURED in proc.stdout
    assert "HINT: at least one named failure IS inherited" in proc.stdout
    assert "commits behind main" in proc.stdout


def test_help_prints_the_env_ledger_rather_than_a_truncated_header(harness, repo):
    """The header is bounded by the docstring sentinel, not a line number: a
    literal range silently truncates --help the moment the header grows."""
    proc = harness.run(repo, "--help")
    assert proc.returncode == RC_OK, proc.stdout
    for knob in ("STALE_BASE_TRIAGE_GH", "STALE_BASE_TRIAGE_COMMENT_MODE"):
        assert knob in proc.stdout, knob
    assert "EXIT CODES" in proc.stdout


def test_the_comment_mode_default_is_the_LITERAL_off():
    """🔴 PINNED TO THE LITERAL, because "it reports only" is a SAFETY CLAIM
    this script makes, not a default it inherits.

    Asserting mere membership in ("off", "dry-run", "on") would let the
    one-character edit that arms a bot commenting on every open PR pass
    unnoticed. So when you DO arm it, change this literal in the arming commit —
    that is the point, not an obstacle: the diff should say out loud that a
    writer went live.
    """
    assert M.COMMENT_MODE_DEFAULT == "off"
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'COMMENT_MODE_DEFAULT = "off"' in src
    # …and the resolver returns it with nothing set anywhere.
    assert M.comment_mode(None) == "off"


def test_an_unrecognised_comment_mode_FAILS_CLOSED(monkeypatch):
    """A typo in the env var must disarm the writer, never arm it, and never
    raise — this is read on the reporting path."""
    for spelling in ("ON", "yes", "true", "1", "", "   ", "dry_run"):
        monkeypatch.setenv("STALE_BASE_TRIAGE_COMMENT_MODE", spelling)
        assert M.comment_mode(None) in ("off", "on"), spelling
    monkeypatch.setenv("STALE_BASE_TRIAGE_COMMENT_MODE", "garbage")
    assert M.comment_mode(None) == "off"
    # The three legal spellings survive, so the guard is not simply "always off".
    for legal in M.COMMENT_MODES:
        monkeypatch.setenv("STALE_BASE_TRIAGE_COMMENT_MODE", legal)
        assert M.comment_mode(None) == legal


def test_the_DEFAULT_run_makes_no_write_api_call_at_all(harness, repo):
    """🔴 THE NEGATIVE HALF. Its positive control is the very next test: without
    one, "no POST in the receipt" is indistinguishable from a receipt nobody
    writes to."""
    harness.serve_default(repo)
    proc = harness.run(repo, "--pr", "7")
    assert proc.returncode == RC_INHERITED, proc.stdout
    assert harness.calls(), "the stub was never reached — this test proves nothing"
    assert not [c for c in harness.calls() if "POST" in c], harness.calls()
    assert "mode=off" in proc.stdout


def test_POSITIVE_CONTROL_arming_it_DOES_reach_the_write_call(harness, repo):
    harness.serve_default(repo)
    proc = harness.run(repo, "--pr", "7", "--comment-mode", "on")
    assert proc.returncode == RC_INHERITED, proc.stdout + proc.stderr
    posts = [c for c in harness.calls() if "POST" in c]
    assert len(posts) == 1, harness.calls()
    assert "/repos/o/r/issues/7/comments" in posts[0]
    assert "posted on #7" in proc.stdout


def test_DRY_RUN_says_what_it_would_do_and_writes_nothing(harness, repo):
    harness.serve_default(repo)
    proc = harness.run(repo, "--pr", "7", "--comment-mode", "dry-run")
    assert "DRY-RUN would comment on #7" in proc.stdout
    assert not [c for c in harness.calls() if "POST" in c], harness.calls()


def test_an_already_commented_PR_is_not_commented_on_twice(harness, repo):
    harness.serve_default(repo)
    harness.serve("/repos/o/r/issues/7/comments?per_page=100",
                  [{"body": f"{M.COMMENT_MARKER}\nsaid this yesterday"}])
    proc = harness.run(repo, "--pr", "7", "--comment-mode", "on")
    assert "already carries the marker" in proc.stdout
    assert not [c for c in harness.calls() if "POST" in c], harness.calls()


def test_a_NOT_INHERITED_PR_is_never_commented_on_even_when_armed(harness, repo):
    harness.serve_default(repo, desc=(
        "FAILED: pytests — FAILING: test_the_bravo_invariant_holds | TOTAL "
        "collected=9  passed=8  skipped=0  failed=1"))
    proc = harness.run(repo, "--pr", "7", "--comment-mode", "on")
    assert not [c for c in harness.calls() if "POST" in c], harness.calls()
    assert proc.returncode == RC_OK, proc.stdout


def test_no_arguments_is_a_usage_error_not_an_empty_clean_run(harness, repo):
    proc = harness.run(repo)
    assert proc.returncode == RC_USAGE, proc.stdout
    assert "usage:" in proc.stdout


def test_a_gh_that_cannot_answer_is_UNMEASURED_not_clean(harness, repo):
    proc = harness.run(repo, "--pr", "7")          # nothing served
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert M.VERDICT_UNMEASURED in proc.stdout


def test_an_UNREADABLE_grep_is_an_error_not_a_reassuring_zero(repo):
    """🔴 THE ZERO THAT IS NOT AN ANSWER. `git grep` exits 1 for "no matches"
    and 128 for "that ref does not exist"; folding the second into the first
    would report "no file defines this test" for every PR whose head the clone
    has never seen. Measured: treating rc!=0 as `[]` SURVIVED the suite before
    this test existed."""
    with pytest.raises(M.Unmeasured):
        M._grep_files(repo.path, "refs/heads/a-ref-that-was-never-created",
                      r"^def test_")
    # POSITIVE CONTROL: the same call against a real ref DOES return rows, so
    # the raise above is about the bad ref and not about a broken invocation.
    assert M._grep_files(repo.path, "main", r"^def test_the_alpha") == [ALPHA]


def test_a_directory_that_is_not_a_checkout_is_UNMEASURED(tmp_path, monkeypatch, repo):
    """🔴 THE MESSAGE IS ASSERTED, NOT JUST THE RAISE. `resolve_repo` has TWO
    raise sites — a failed `git remote` and an unparseable URL — and a bare
    `pytest.raises` is satisfied by either, so a mutant deleting the first
    SURVIVED: the empty stdout simply fell through to the second. A test killed
    by a different guard's error is green for the wrong reason.
    """
    monkeypatch.delenv("STALE_BASE_TRIAGE_REPO", raising=False)
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(M.Unmeasured, match="cannot read origin remote"):
        M.resolve_repo(plain)
    # POSITIVE CONTROL: with a real origin it RESOLVES, so the raise above is
    # about the missing remote and not about this function never working.
    repo.git("remote", "add", "origin", "git@example.invalid:owner/name.git")
    assert M.resolve_repo(repo.path) == "owner/name"


def test_the_API_budget_is_enforced_rather_than_merely_documented(harness, repo):
    """A budget nothing consults is a comment. With zero seconds left the first
    read must report COULD NOT MEASURE instead of running unbounded."""
    harness.serve_default(repo)
    proc = harness.run(repo, "--pr", "7", env={"STALE_BASE_TRIAGE_BUDGET": "0"})
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "budget" in proc.stdout
    assert not harness.calls(), harness.calls()


def test_the_context_is_PINNED_to_the_literal_the_pipeline_posts():
    """🔴 A LITERAL CONTRACT WITH `devrc-ci`, AND IT WAS UNPINNED. Every other
    test in this file reads `M.CONTEXT` for both the fixture row and the
    lookup, so they agree with each other whatever the constant says: a mutant
    changing it to `tekton/devrc-NOPE` SURVIVED the entire suite — it was the
    sweep's own POSITIVE CONTROL, and its survival is what found this.

    A wrong context here reads as "no verdict found" on every PR forever, which
    is indistinguishable from a quiet, healthy repo.
    """
    assert M.CONTEXT == "tekton/devrc-pytests"
    assert 'CONTEXT = "tekton/devrc-pytests"' in SRC_SCRIPT


# ══ STATIC guards — relationships no behavioural test can see ═════════════════

SRC = SRC_SCRIPT


def test_the_rollup_endpoint_and_check_runs_are_never_requested():
    """🔴 `/commits/{sha}/status` (SINGULAR) maps `error` onto `failure`, and
    `/check-runs` returns NOTHING for these checks. Both are reassuring,
    plausible and wrong; neither may appear in a request path."""
    paths = re.findall(r'f?"(/repos/[^"]*)"', SRC)
    assert paths, "the request-path scan matched nothing — this guard is inert"
    for p in paths:
        assert not p.endswith("/status"), p
        assert "check-runs" not in p, p
    assert "/statuses" in " ".join(paths)


def _git_subcommands():
    """Every git subcommand the source actually INVOKES, read from the AST.

    🔴 STRUCTURAL, NOT SPELLED. A string scan over the whole file would match
    the comment that EXPLAINS the hazard — which is exactly what a first draft
    of the guard below did, failing on its own documentation. The AST sees the
    argument list and nothing else, so prose cannot satisfy it or break it.
    """
    tree = ast.parse(SRC)
    subs = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "_git"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.List) and arg.elts:
                head = arg.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    subs.append([e.value for e in arg.elts
                                 if isinstance(e, ast.Constant)])
    return subs


def test_path_existence_is_proved_with_cat_file_not_diff_quiet():
    """🔴 `git diff --quiet <ref> -- <path>` EXITS 0 WHEN THE PATH EXISTS ON
    NEITHER SIDE, so as an existence check it reports SAME for MISSING."""
    subs = _git_subcommands()
    assert subs, "the AST scan found no git invocations — this guard is inert"
    assert any(a[:2] == ["cat-file", "-e"] for a in subs), subs
    assert not [a for a in subs if a[0] == "diff"], subs


def test_every_env_var_the_code_reads_is_documented_in_the_header():
    """Two-way: an undocumented knob is a knob nobody can find, and one named
    in the ledger but no longer read is a lie."""
    read = set(re.findall(r'os\.environ\.get\(\s*"(STALE_BASE_TRIAGE_[A-Z_]+)"', SRC))
    ledgered = set(re.findall(r"^#   (STALE_BASE_TRIAGE_[A-Z_]+) ", SRC, re.M))
    assert read, "the reader scan matched nothing — this guard is inert"
    assert read == ledgered, f"read={sorted(read)} ledgered={sorted(ledgered)}"


def test_every_test_this_script_names_actually_exists():
    """A citation that resolves to nothing reads as authority and is not one.
    Names wrapped across a comment line-break are re-joined before looking."""
    joined = re.sub(r"\n#\s*", "", SRC)
    cited = set(re.findall(r"`(test_[A-Za-z0-9_]+)`", joined))
    assert cited, "the citation scan matched nothing — this guard is inert"
    # Repo-wide, because this script legitimately cites tests it does not own
    # (a real CI row names one, and the measurement in the header names
    # another). Scoping to this file alone would have forced those citations
    # out of the prose rather than validated them.
    here = set()
    for path in sorted((ROOT / "scripts").rglob("test_*.py")):
        here |= set(re.findall(r"^ *def (test_[A-Za-z0-9_]+)",
                               path.read_text(encoding="utf-8", errors="replace"),
                               re.M))
    assert cited <= here, f"dangling citations: {sorted(cited - here)}"


def test_no_git_subcommand_that_WRITES_is_ever_invoked():
    """🔴 "IT WRITES NOTHING" IS THIS TOOL'S SAFETY CLAIM, SO IT IS PINNED
    ABSOLUTELY RATHER THAN RELATIVELY.

    This guard replaces one that said "every ref it writes is namespaced under
    `refs/stale-base-triage/*`" — true, and a weaker claim than the one the tool
    makes, because `refs/` lives in the COMMON git dir: a worktree gives zero
    isolation there, so ANY ref write reaches a concurrent session's repo. The
    `--fetch` flag that made those writes was deleted for buying nothing here
    (this repo's PRs are same-repo branches and `origin` fetches
    `+refs/heads/*`, so an ordinary `git fetch origin` already has every head),
    which lets the claim be pinned at zero.

    🔴 AN ALLOWLIST, NOT A DENYLIST — a git subcommand nobody enumerated is a
    write by default. A denylist of known writers passes silently the day
    someone reaches for one it does not name.
    """
    subs = _git_subcommands()
    assert subs, "the AST scan found no git invocations — this guard is inert"
    read_only = {"rev-parse", "cat-file", "grep", "log", "merge-base", "rev-list"}
    for a in subs:
        if a[0] == "remote":
            # `remote` has writing forms (`add`, `set-url`, `remove`); only the
            # reading one is permitted, and it is checked by its ARGUMENT.
            assert a[:2] == ["remote", "get-url"], a
            continue
        assert a[0] in read_only, f"`git {a[0]}` is not an enumerated read: {a}"
    # POSITIVE CONTROL on the scan: it really does see the arguments, so the
    # allowlist above is not passing over an empty or opaque list.
    assert any(a[:2] == ["merge-base", "--is-ancestor"] for a in subs), subs
    assert any(a[0] == "remote" for a in subs), subs


def test_the_deleted_flags_are_GONE_from_the_parser_not_merely_undocumented():
    """A flag left in the parser is a flag someone can still reach. Both
    deletions are pinned at the source so a revert has to be deliberate.

    `--fetch` was the only git WRITE in a tool whose stated safety property is
    that it writes nothing; `--json` had no consumer anywhere in the repo.
    `--sweep` is deliberately NOT in this list — it is the mode the derived
    completeness route made useful.

    🔴 STRUCTURAL, NOT SPELLED. A first version of this guard was a string scan
    over the whole file and it failed on the HEADER PARAGRAPH THAT EXPLAINS THE
    DELETION — the same trap `_git_subcommands` is AST-based to avoid. The AST
    sees declarations and string constants; prose satisfies neither.
    """
    tree = ast.parse(SRC)
    flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            flags |= {a.value for a in node.args
                      if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    assert flags, "the add_argument scan matched nothing — this guard is inert"
    assert "--sweep" in flags, "--sweep was kept on purpose; see the header"
    for gone in ("--fetch", "--json"):
        assert gone not in flags, f"{gone} is still reachable in the parser"
    # …and neither leaves a stub behind: no module-level `_JSON_MODE`, and no
    # STRING CONSTANT naming the ref namespace `--fetch` used to write.
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    assert "_JSON_MODE" not in assigned, assigned
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [c for c in consts if "refs/stale-base-triage" in c], (
        "a ref-namespace literal survived the deletion of --fetch")
