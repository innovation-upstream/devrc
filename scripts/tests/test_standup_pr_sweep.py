"""Behavioural tests for `claude/skills/standup/standup.sh`'s PR sweep.

Everything runs against THROWAWAY git repos in tmp_path and a STUB `gh` on
PATH. Nothing here touches ~/workspace, GitHub, or any cluster.

WHY THIS SUITE EXISTS
---------------------
standup's STATUS line printed `PRs 70 open (0 ready, 0 red)` — a fleet verdict
with two reassuring zeroes. Both were WRONG, and neither was wrong because the
repo list was short. Two independent defects manufactured them:

  1. FIELD SHIFT. The flagged-PR rows were tab-separated and read with
     `IFS=$'\\t' read`. Tab is an IFS *whitespace* character, so a run of tabs
     collapses into one delimiter: a PR with an empty `reviewDecision` — the
     normal state of anything nobody has reviewed yet — shifted every later
     field left, leaving `author` empty, so the `author == $ME` filter dropped
     it. Measured 2026-08-12: `civitai/talos-infra` alone emitted 8 flagged
     rows and every one was discarded.
  2. HALF THE CHECK RESULTS WERE UNREADABLE. `statusCheckRollup` mixes two node
     types — a CheckRun carries `conclusion`, a StatusContext carries `state`
     and has `conclusion: null`. The rollup read only `.conclusion`, so a
     failing StatusContext scored as "pending". Measured the same day: 5
     genuinely-red PRs in one repo read as green-ish.

A count that cannot rise is indistinguishable from a scan wired to nothing, so
the load-bearing tests here are the ones that make it RISE:

  * `test_control_pair_red_moves_zero_to_two` is the positive/negative control.
    It runs the SAME code over a clean fixture and a broken one and asserts the
    number moves. A bare "it reported 0" proves nothing about either.
  * Each defect above has its own test, built so the OTHER defect cannot mask
    it: the field-shift case uses an empty reviewDecision with a CheckRun
    FAILURE; the StatusContext case uses a NON-empty reviewDecision so it stays
    red for its own reason even when the separator is wrong.
  * `test_status_states_the_scope_it_measured` pins the fleet claim itself. The
    STATUS line has to name the repo count it actually swept.
  * Degradation must be LOUD: a failed discovery call or an unreadable repo may
    never render as "All clear — nothing needs you."

`STANDUP_SH` (env) overrides the script under test — that is how the red half
of the matrix was taken, by pointing it at the pre-change file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

STANDUP = Path(os.environ.get(
    "STANDUP_SH", REPO_ROOT / "claude" / "skills" / "standup" / "standup.sh"))

pytestmark = pytest.mark.skipif(
    not all(shutil.which(b) for b in ("bash", "git", "jq")),
    reason="needs bash + git + jq on PATH",
)

# The stub `gh`. It answers exactly the three calls standup makes and applies
# the REAL jq expression standup passes, so the script's own jq program — where
# both defects lived — is what runs. A stub that pre-baked the records would
# test the stub.
GH_STUB = r"""
set -u
fixdir="$GH_FIXTURES"
sub1="${1:-}"; sub2="${2:-}"
shift 2 2>/dev/null || true
repo=""; jqexpr=""
while [ $# -gt 0 ]; do
  case "$1" in
    -R|--repo) repo="${2:-}"; shift 2 ;;
    -q|--jq)   jqexpr="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done
case "$sub1 $sub2" in
  "auth status") exit 0 ;;
  "search prs")
    [ -n "${GH_FAIL_SEARCH:-}" ] && exit 1
    jq -r "$jqexpr" "$fixdir/search.json" ;;
  "pr list")
    case " ${GH_FAIL_REPOS:-} " in *" $repo "*) exit 1 ;; esac
    f="$fixdir/$(printf '%s' "$repo" | sed 's#/#__#g').json"
    [ -f "$f" ] || f="$fixdir/__empty.json"
    jq -r "$jqexpr" "$f" ;;
  *) exit 1 ;;
esac
"""


def pr(number, *, author="ZacxDev", review="", mergeable="MERGEABLE",
       draft=False, checks=()):
    """One `gh pr list --json ...` element.

    `checks` items are ("CheckRun", conclusion) or ("StatusContext", state) —
    the two node shapes GitHub actually returns in statusCheckRollup.
    """
    rollup = []
    for kind, verdict in checks:
        if kind == "CheckRun":
            rollup.append({"__typename": "CheckRun", "conclusion": verdict,
                           "status": "COMPLETED"})
        else:
            rollup.append({"__typename": "StatusContext", "conclusion": None,
                           "state": verdict})
    return {"number": number, "mergeable": mergeable,
            "reviewDecision": review, "statusCheckRollup": rollup,
            "isDraft": draft, "author": {"login": author}}


class Harness:
    """A standup PR sweep wired to fixtures instead of GitHub."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.fix = tmp_path / "fix"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.fix.mkdir(parents=True, exist_ok=True)
        (self.fix / "__empty.json").write_text("[]", encoding="utf-8")
        (self.fix / "search.json").write_text("[]", encoding="utf-8")
        write_exec(self.bin / "gh", GH_STUB)
        self.local: list[Path] = []
        self.env_extra: dict[str, str] = {}

    def repo(self, slug, prs, *, local=False):
        """Register a repo's open-PR fixture; `local` also makes a checkout."""
        (self.fix / (slug.replace("/", "__") + ".json")).write_text(
            json.dumps(prs), encoding="utf-8")
        if local:
            d = self.root / "repos" / slug.replace("/", "__")
            d.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            f"https://github.com/{slug}.git"], check=True)
            self.local.append(d)
        return self

    def discovered(self, *slugs):
        """What `gh search prs --author=@me` reports (one row per open PR)."""
        (self.fix / "search.json").write_text(
            json.dumps([{"repository": {"nameWithOwner": s}} for s in slugs]),
            encoding="utf-8")
        return self

    def run(self, **env_extra):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["GH_FIXTURES"] = str(self.fix)
        # ":"-joined, and never empty — an empty STANDUP_REPOS would fall back
        # to the real hard-coded checkouts and leave the sandbox.
        env["STANDUP_REPOS"] = ":".join(str(p) for p in self.local) or str(
            self.root / "no-such-repo")
        env.update(self.env_extra)
        env.update(env_extra)
        r = subprocess.run(["bash", str(STANDUP), "repos"], env=env,
                           capture_output=True, text=True, timeout=180)
        assert "STATUS:" in r.stdout, f"no STATUS line:\n{r.stdout}\n{r.stderr}"
        return r.stdout

    @staticmethod
    def status(out):
        return next(ln for ln in out.splitlines() if ln.startswith("STATUS:"))

    @staticmethod
    def counts(out):
        """(ready, red, conflicting) as the STATUS line reports them.

        Only for tests that are ABOUT the STATUS line. Behavioural tests use
        `acted()` instead: it reads the ACTIONS block, whose wording predates
        this change, so a red result there is evidence about the DEFECT rather
        than about the status format having moved.
        """
        import re
        m = re.search(r"\((\d+) ready, (\d+) red, (\d+) conflicting",
                      Harness.status(out))
        assert m, f"STATUS did not carry the three counts: {Harness.status(out)}"
        return tuple(int(g) for g in m.groups())

    @staticmethod
    def acted(out, kind):
        """How many ACTIONS lines of `kind` ('red CI', 'conflicting',
        'approved+mergeable') the sweep emitted. Format-independent."""
        return sum(1 for ln in out.splitlines()
                   if ln.lstrip().startswith("- ") and kind in ln)


@pytest.fixture()
def h(tmp_path):
    return Harness(tmp_path)


# --------------------------------------------------------------------------
# The control pair. Everything else is only meaningful next to this.
# --------------------------------------------------------------------------

def test_control_pair_red_moves_zero_to_two(tmp_path):
    """NEGATIVE control then POSITIVE control, same code, same shape of input.

    A green repo must report 0 red; swapping in two genuinely-failing PRs must
    make that number MOVE. Reporting only one half of this pair is how a
    counter wired to nothing passes for a measurement.
    """
    clean = Harness(tmp_path / "clean")
    clean.repo("acme/widgets", [
        pr(1, checks=[("CheckRun", "SUCCESS")]),
        pr(2, checks=[("StatusContext", "SUCCESS")]),
    ], local=True).discovered("acme/widgets")
    zero = Harness.acted(clean.run(), "red CI")

    broken = Harness(tmp_path / "broken")
    broken.repo("acme/widgets", [
        pr(1, checks=[("CheckRun", "FAILURE")]),
        pr(2, checks=[("StatusContext", "FAILURE")]),
    ], local=True).discovered("acme/widgets")
    moved = Harness.acted(broken.run(), "red CI")

    assert zero == 0, f"negative control was not zero: {zero} red actions"
    assert moved == 2, f"positive control did not reach 2: {moved} red actions"


# --------------------------------------------------------------------------
# Defect 1 — the tab collapse. Empty reviewDecision must not eat the author.
# --------------------------------------------------------------------------

def test_flagged_pr_with_empty_review_decision_is_counted(h):
    """The exact shape that produced the false zeroes.

    Empty `reviewDecision` + a real CheckRun FAILURE + CONFLICTING. Under the
    tab-separated parse the author field came back empty and the row was
    dropped by the `author == $ME` filter, so this reported 0 red.
    """
    h.repo("acme/widgets", [
        pr(7, review="", mergeable="CONFLICTING",
           checks=[("CheckRun", "FAILURE")]),
    ], local=True).discovered("acme/widgets")
    out = h.run()
    # Assert the ACTIONS first: that wording is unchanged by this PR, so a red
    # here is the DEFECT, not the STATUS format moving under the test.
    assert "acme/widgets PR #7 red CI" in out, out
    assert "acme/widgets PR #7 conflicting" in out, out
    assert Harness.counts(out)[1:] == (1, 1), Harness.status(out)


def test_author_filter_still_excludes_other_peoples_prs(h):
    """The field-shift fix must not be a blanket 'count everything'.

    Someone else's red PR is not yours to act on — if this went green whatever
    the author, the fix would just be the old bug inverted.

    INVARIANT GUARD, not a regression test: the pre-change script also dropped
    this row (for the wrong reason — it dropped everything). It is here so the
    fix cannot be "delete the author filter".
    """
    h.repo("acme/widgets", [
        pr(8, author="someone-else", review="", mergeable="CONFLICTING",
           checks=[("CheckRun", "FAILURE")]),
    ], local=True).discovered("acme/widgets")
    out = h.run()
    assert Harness.acted(out, "red CI") == 0, out
    assert "#8" not in out


# --------------------------------------------------------------------------
# Defect 2 — StatusContext results were invisible.
# --------------------------------------------------------------------------

def test_status_context_failure_counts_as_red(h):
    """A failing StatusContext is red.

    `reviewDecision` is deliberately NON-empty so this case does not depend on
    the separator fix: it must be red for its own reason, or it would go green
    for the wrong one.
    """
    h.repo("acme/widgets", [
        pr(9, review="CHANGES_REQUESTED", mergeable="MERGEABLE",
           checks=[("StatusContext", "FAILURE")]),
    ], local=True).discovered("acme/widgets")
    out = h.run()
    assert "acme/widgets PR #9 red CI" in out, out
    assert Harness.counts(out)[1] == 1, Harness.status(out)


def test_status_context_error_counts_as_red(h):
    """ERROR is the other failing StatusContext state GitHub emits."""
    h.repo("acme/widgets", [
        pr(10, review="CHANGES_REQUESTED",
           checks=[("StatusContext", "ERROR")]),
    ], local=True).discovered("acme/widgets")
    out = h.run()
    assert "acme/widgets PR #10 red CI" in out, out


def test_in_progress_check_run_is_not_red(h):
    """An in-flight CheckRun has an empty conclusion — that is pending, not red.

    The `// .state // ""` fallback must not turn "still running" into a
    failure; over-flagging burns the operator's trust as fast as under-flagging.
    INVARIANT GUARD — the pre-change script also called this pending.
    """
    h.repo("acme/widgets", [
        pr(11, review="CHANGES_REQUESTED", checks=[("CheckRun", "")]),
    ], local=True).discovered("acme/widgets")
    assert Harness.acted(h.run(), "red CI") == 0


def test_approved_and_mergeable_is_ready(h):
    """The `ready` counter must also be able to leave zero.

    Mixed: the ACTIONS assertion is an INVARIANT GUARD (an APPROVED PR has a
    non-empty reviewDecision, so the tab collapse never reached it and the
    pre-change script emitted this line too). Only the STATUS assertion is new.
    Counted as coverage of the ready path, not as regression evidence.
    """
    h.repo("acme/widgets", [
        pr(12, review="APPROVED", mergeable="MERGEABLE",
           checks=[("CheckRun", "SUCCESS")]),
    ], local=True).discovered("acme/widgets")
    out = h.run()
    assert "PR #12 approved+mergeable" in out, out
    assert Harness.counts(out)[0] == 1, Harness.status(out)


# --------------------------------------------------------------------------
# The fleet claim itself.
# --------------------------------------------------------------------------

def test_status_states_the_scope_it_measured(h):
    """STATUS may not assert a fleet fact without naming what it swept."""
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True).discovered("acme/widgets")
    assert "across 1 repos" in Harness.status(h.run())


def test_widened_scan_reaches_a_repo_with_no_local_checkout(h):
    """The whole point of Unit C: a repo you have no clone of is still swept."""
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True)
    h.repo("acme/remote-only", [
        pr(42, review="CHANGES_REQUESTED", checks=[("CheckRun", "FAILURE")]),
    ])  # NOT local — only discoverable via search
    h.discovered("acme/widgets", "acme/remote-only")
    out = h.run()
    assert "acme/remote-only PR #42 red CI" in out, out
    assert "across 2 repos" in Harness.status(out)


def test_release_bot_repo_is_excluded_and_named(h):
    """homebrew-tap swamps the signal; excluding it silently would be worse."""
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True)
    h.repo("ZacxDev/homebrew-tap", [
        pr(n, review="CHANGES_REQUESTED", checks=[("CheckRun", "FAILURE")])
        for n in range(100, 130)
    ])
    h.discovered("acme/widgets", "ZacxDev/homebrew-tap")
    out = h.run()
    assert Harness.acted(out, "red CI") == 0, out
    assert "across 1 repos" in Harness.status(out)
    assert "ZacxDev/homebrew-tap" in next(
        ln for ln in out.splitlines() if ln.startswith("Filtered:"))


def test_drafts_are_not_counted_as_open(h):
    h.repo("acme/widgets", [
        pr(1, checks=[("CheckRun", "SUCCESS")]),
        pr(2, draft=True, checks=[("CheckRun", "FAILURE")]),
    ], local=True).discovered("acme/widgets")
    out = h.run()
    assert "PRs 1 open" in Harness.status(out)
    assert Harness.acted(out, "red CI") == 0


# --------------------------------------------------------------------------
# Degradation must be loud. These are the anti-silent-zero tests.
# --------------------------------------------------------------------------

def test_failed_discovery_never_reads_as_all_clear(h):
    """If the fleet sweep could not run, STATUS may not imply it did."""
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True)
    out = h.run(GH_FAIL_SEARCH="1")
    assert "fleet discovery FAILED" in Harness.status(out)
    assert "LOCAL repos only" in Harness.status(out)
    # "All clear" in ANY spelling, not one exact sentence — a guard that pins a
    # phrase passes the moment someone rewords the reassurance.
    assert "All clear" not in out
    assert "coverage was degraded" in out


def test_unreadable_repo_is_reported_not_silently_zero(h):
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True)
    h.repo("acme/locked", [])
    h.discovered("acme/widgets", "acme/locked")
    out = h.run(GH_FAIL_REPOS="acme/locked")
    assert "acme/locked: skipped (gh err)" in out
    assert "1 unreadable" in Harness.status(out)
    assert "All clear" not in out


def test_truncated_search_reports_a_floor_not_a_total(h):
    """At the search cap the repo set is a floor; STATUS has to say so."""
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True)
    h.repo("acme/second", [])
    h.discovered("acme/widgets", "acme/second")
    st = Harness.status(h.run(STANDUP_PR_SEARCH_LIMIT="2"))
    assert "≥" in st and "floor" in st, st


def test_clean_fleet_says_all_clear_and_names_the_scope(h):
    """Positive control for the all-clear path itself: when coverage really was
    complete and nothing is flagged, the reassuring line is allowed — but it
    still names the scope, because a `repos` run says nothing about alerts."""
    h.repo("acme/widgets", [pr(1, checks=[("CheckRun", "SUCCESS")])],
           local=True).discovered("acme/widgets")
    out = h.run()
    assert "All clear in scope 'repos' — nothing needs you." in out


# --------------------------------------------------------------------------
# Structural: the separator must not regress to a whitespace one.
# --------------------------------------------------------------------------

def test_record_separator_is_not_ifs_whitespace():
    """Pins the mechanism, not the spelling of one fix.

    A future edit that "simplifies" the records back to tab/space reintroduces
    the field shift, and every behavioural test above would have to be read
    carefully to notice. This fails immediately.
    """
    src = STANDUP.read_text(encoding="utf-8")
    assert "US=$'\\x1f'" in src, "the US record separator is gone"
    assert 'IFS="$US" read' in src, "flagged rows are no longer read with $US"
    assert "IFS=$'\\t' read -r num ci m r author" not in src, (
        "the tab-separated flagged-PR parse is back")
