"""`scripts/main-status-watch.py` — the early trigger for the main-green deadman.

WHY THIS FILE EXISTS. `main-green-check.service` runs on `OnUnitActiveSec=4h`
with `TimeoutStartSec=5400`, so `main` can be red for up to ~5.5h before anything
looks. MEASURED over the 100 newest commits to `main` (2026-09-08..09-10), the
`tekton/devrc-main-*` leg posts on 100/100 pushes and an authoritative verdict
lands p50 ~19.6 min after the commit — so the signal to look sooner already
exists. The script under test converts that signal into an early start of the
deadman, and claims nothing about main itself.

🔴 WHAT THESE TESTS ARE, HONESTLY LABELLED. They are:

  * two CONTROLS, first in the file, because every behavioural test below is a
    reassuring zero if the harness cannot go red or cannot see a trigger;
  * BEHAVIOURAL CONTRACTS on each arm (green / red / no-verdict / flake-screen /
    debounce / unmeasured / blind);
  * REAL-FIXTURE guards built from status descriptions this repo actually
    posted, which is the only reason the truncation hazard below is provable
    rather than imagined;
  * STATIC guards pinning relationships the behavioural tests structurally
    cannot see — the roll-up endpoint, both production argvs, the DND wiring,
    the env-knob ledger, the two consolidated one-place rules, and that every
    test the script CITES exists;
  * MUTATION-DERIVED guards (the round-3 block at the end), each carrying the
    mutant id it kills instead of a base ref.

⚠ TWO CLAIMS THAT USED TO SIT HERE WERE FALSIFIED BY THE FILE'S OWN GROWTH, and
both are the "a fix round's own prose is the likeliest next finding" shape:
"NOTHING here is regression coverage for a defect observed at a base containing
it" stopped being true the moment round 2 landed — `test_an_ENOSPC_SHAPED_state_
dir_ESCALATES_rather_than_reporting_success`, `test_a_REPEATEDLY_failing_trigger_
ESCALATES_to_blind` and `test_a_failed_episode_write_does_NOT_trigger` each
carry a measured pre-fix sequence in their own docstring. And "two STATIC
guards" counted that section when it held two; it has grown several times
since. Neither sentence was re-read when the thing it counted changed, which
is the argument for describing a section rather than counting it.

🔴 THE SEAM THESE TESTS DRIVE IS NOT THE PRODUCTION TRIGGER. Every behavioural
test sets MAIN_STATUS_WATCH_TRIGGER, so none of them ever starts a real systemd
unit. `test_the_production_trigger_is_systemctl_start_main_green_check` pins the
real command textually so the seam cannot drift away from what production does
while every behavioural test stays green.
"""
import ast
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

SCRIPT = ROOT / "scripts" / "main-status-watch.py"

RC_OK, RC_TRIGGERED, RC_UNMEASURED, RC_BLIND, RC_USAGE = 0, 10, 11, 12, 2

# ── REAL descriptions, copied from statuses this repo posted ──────────────────
# These are this repo's OWN CI output about its OWN tests: no third-party text,
# no hostnames, no captured content. They are here because the truncation
# hazard the flake screen turns on is only demonstrable with real ones — every
# synthetic description anybody would write by hand is conveniently complete.
#
# The known flake, cut MID-WORD by GitHub's 140-char description cap:
REAL_FLAKE_DESC = (
    "FAILED: pytests — FAILING: TestARefusedWriteIsIndistinguishableFromAnAbsentOne"
    ".test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_dif"
)
# Seven failures, ONE of them named:
REAL_SEVEN_FAILED_ONE_NAMED = (
    "FAILED: pytests — FAILING: test_every_decrypt_family_VERDICT_is_pinned_WHOLE "
    "| TOTAL collected=20908  passed=20899  skipped=2  failed=7  ("
)
# A real red that named NO test at all:
REAL_NO_NAME = (
    "FAILED: pytests — TOTAL collected=20900  passed=20897  skipped=2  failed=1  "
    "(floor: 20208 = sum of 28 per-target floors)"
)
REAL_SUPERSEDED = "superseded by a newer run — this commit was not validated"
REAL_KILLED = (
    "KILLED: pytests — the gate pod died at or after step pytests "
    "(preempted/evicted/OOM/timeout). Not a code failure."
)
REAL_NO_GATE_POD = (
    "NO GATE POD: pytests — the gate never started (InitContainerFailed). "
    "Not a code failure."
)
REAL_SUCCESS = (
    "TOTAL collected=21972  passed=21970  skipped=2  failed=0  "
    "(floor: 20441 = sum of 28 per-target floors)"
)

CTX_PY = "tekton/devrc-main-pytests"
CTX_NODE = "tekton/devrc-main-nodetests"


def _status(context, state, description):
    return {"context": context, "state": state, "description": description}


class Harness:
    """A fake `gh` serving canned JSON, plus a trigger that leaves a receipt."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.cache = tmp_path / "cache"
        self.responses = tmp_path / "responses"
        self.responses.mkdir(parents=True, exist_ok=True)
        self.receipt = tmp_path / "triggered"
        self.calls = tmp_path / "gh-calls"
        self.gh = self.bin / "fake-gh"
        # `gh api <path>` -> the file named after a sanitised <path>.
        write_exec(
            self.gh,
            f'printf "%s\\n" "$2" >> "{self.calls}"\n'
            f'f=$(printf "%s" "$2" | tr "/?&=" "____")\n'
            f'if [ -f "{self.responses}/$f" ]; then cat "{self.responses}/$f"; exit 0; fi\n'
            f'echo "no canned response for $2" >&2; exit 22\n',
        )
        self.trigger = self.bin / "fake-trigger"
        write_exec(self.trigger, f'echo "$@" >> "{self.receipt}"\nexit 0\n')

    def serve(self, path, payload):
        name = "".join("_" if c in "/?&=" else c for c in path)
        (self.responses / name).write_text(json.dumps(payload), encoding="utf-8")

    def serve_commits(self, shas, repo="o/r", depth=20):
        self.serve(
            f"/repos/{repo}/commits?sha=main&per_page={depth}",
            [{"sha": s} for s in shas],
        )

    def serve_statuses(self, sha, rows, repo="o/r"):
        self.serve(f"/repos/{repo}/commits/{sha}/statuses", rows)

    def env(self, **extra):
        env = dict(os.environ)
        env.update(
            {
                "MAIN_STATUS_WATCH_GH": str(self.gh),
                "MAIN_STATUS_WATCH_TRIGGER": str(self.trigger),
                "MAIN_STATUS_WATCH_CACHE": str(self.cache),
                "MAIN_STATUS_WATCH_REPO": "o/r",
                "MAIN_STATUS_WATCH_DEPTH": "20",
            }
        )
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def run(self, **extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, timeout=120, env=self.env(**extra),
        )

    def triggered(self):
        return self.receipt.exists()

    def responses_off(self):
        """Make every API read fail, so the next run is UNMEASURED."""
        for f in self.responses.iterdir():
            f.unlink()


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def _red_commit(h, sha, desc=REAL_SEVEN_FAILED_ONE_NAMED):
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "failure", desc),
                           _status(CTX_NODE, "success", REAL_SUCCESS)])


# ══ CONTROLS ══════════════════════════════════════════════════════════════════
# 🔴 THESE COME FIRST BECAUSE EVERY TEST BELOW IS MEANINGLESS WITHOUT THEM.
# "the script did not trigger" is the assertion most tests here make, and it is
# indistinguishable from "the harness cannot observe a trigger at all".

def test_NEGATIVE_CONTROL_the_harness_can_go_red(h):
    """Feed it a case it MUST report as red, and watch a wrong claim fail.

    If this passes while asserting the OPPOSITE, the harness is wired to nothing.
    """
    _red_commit(h, "a" * 40)
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout + proc.stderr
    with pytest.raises(AssertionError):
        assert proc.returncode == RC_OK, "deliberately wrong: a red must not read as OK"
    # 🔴 A FIRST RED MUST NOT REPORT A PRE-EXISTING EPISODE. `State._read`
    # returns "" when the file is absent, and every mutant returning a WORD
    # there instead survived the sweep: the run then announces an ancient
    # episode "since <that word>" on a box that has never seen a red. Same rc,
    # same trigger, a log that describes a state the machine was never in.
    assert "red episode" not in proc.stdout, proc.stdout


def test_POSITIVE_CONTROL_the_trigger_receipt_can_move_off_zero(h):
    """A zero receipt count must be shown to be capable of being non-zero.

    Reported as a PAIR: 1 on the positive control, 0 on the green case below.
    """
    assert not h.triggered(), "receipt must start absent"
    _red_commit(h, "b" * 40)
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED
    assert h.triggered(), "the trigger seam never fired — every 'did not trigger' below would be vacuous"
    # NOT `"main-green-check" in stdout`: under the seam the command printed is
    # the stub's path, and asserting the production name here would pass only by
    # accident of the seam's naming. The production command is pinned
    # textually by `test_the_production_trigger_is_systemctl_start_main_green_check`.
    assert "TRIGGERED the authoritative check early" in proc.stdout


# ══ THE ROLL-UP TRAP ══════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "desc", [REAL_SUPERSEDED, REAL_KILLED, REAL_NO_GATE_POD],
    ids=["superseded", "killed", "no-gate-pod"],
)
def test_an_error_state_is_never_a_red(h, desc):
    """`error` rows are 79% of this leg. On the roll-up endpoint they read as
    `failure`, which would have fired on ~80% of main pushes on day one."""
    sha = "c" * 40
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "error", desc),
                           _status(CTX_NODE, "error", desc)])
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert not h.triggered(), "an infra error must never trigger a confirmation run"


@pytest.mark.parametrize(
    "desc", [REAL_SUPERSEDED, REAL_KILLED, REAL_NO_GATE_POD],
    ids=["superseded", "killed", "no-gate-pod"],
)
def test_an_error_state_is_never_a_green_either(h, desc):
    """The mirror image, and the one this repo keeps getting bitten by: a
    commit nothing ran against must report NO VERDICT, not a reassuring pass."""
    sha = "d" * 40
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "error", desc)])
    proc = h.run()
    assert "no authoritative" in proc.stdout, proc.stdout
    assert "GREEN" not in proc.stdout


def test_a_stale_pending_row_does_not_outvote_the_verdict_that_replaced_it(h):
    """GitHub returns statuses newest-first and a commit accumulates
    `pending` then its verdict under the SAME context."""
    sha = "e" * 40
    h.serve_commits([sha])
    h.serve_statuses(sha, [
        _status(CTX_PY, "failure", REAL_SEVEN_FAILED_ONE_NAMED),  # newest
        _status(CTX_PY, "pending", "devrc gate running"),         # older
    ])
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert h.triggered()


def test_a_pending_only_commit_is_not_a_verdict(h):
    sha = "f" * 40
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "pending", "devrc gate running")])
    proc = h.run()
    assert proc.returncode == RC_OK
    assert not h.triggered()
    assert "no authoritative" in proc.stdout


# ══ THE WALK ══════════════════════════════════════════════════════════════════

def test_the_walk_skips_superseded_commits_to_reach_the_newest_real_verdict(h):
    """MEASURED: only 21 of 100 main commits carry an authoritative verdict.
    Reading the tip alone would find `superseded` and report nothing."""
    tip, mid, old = "1" * 40, "2" * 40, "3" * 40
    h.serve_commits([tip, mid, old])
    h.serve_statuses(tip, [_status(CTX_PY, "error", REAL_SUPERSEDED)])
    h.serve_statuses(mid, [_status(CTX_PY, "error", REAL_KILLED)])
    h.serve_statuses(old, [_status(CTX_PY, "failure", REAL_SEVEN_FAILED_ONE_NAMED)])
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert old[:8] in proc.stdout


def test_a_newer_green_wins_over_an_older_red(h):
    """A red that a later commit already fixed must not trigger: the deadman
    checks the TIP, so re-running it would only confirm the fix."""
    tip, old = "4" * 40, "5" * 40
    h.serve_commits([tip, old])
    h.serve_statuses(tip, [_status(CTX_PY, "success", REAL_SUCCESS)])
    h.serve_statuses(old, [_status(CTX_PY, "failure", REAL_SEVEN_FAILED_ONE_NAMED)])
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert not h.triggered(), "0 on the green case; the positive control shows the receipt CAN move"


def test_a_red_on_either_leg_triggers(h):
    """pytests green, nodetests red — a leg-specific red must not be masked."""
    sha = "6" * 40
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "success", REAL_SUCCESS),
                           _status(CTX_NODE, "failure", REAL_NO_NAME)])
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout


def test_a_foreign_context_is_ignored(h):
    """`tekton/devrc-cairn-client-runs` posts on main too, and it is a different
    pipeline's verdict. Only the main-gate legs may speak here."""
    sha = "7" * 40
    h.serve_commits([sha])
    h.serve_statuses(sha, [
        _status("tekton/devrc-cairn-client-runs", "failure", "FAILED: something else"),
        _status(CTX_PY, "success", REAL_SUCCESS),
    ])
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert not h.triggered()


# ══ THE FLAKE SCREEN ══════════════════════════════════════════════════════════
# 🔴 THE COUNTERINTUITIVE HALF FIRST: the REAL known-flake row must TRIGGER.

def test_the_REAL_known_flake_row_still_triggers_because_it_is_truncated(h):
    """The row that named the known flake was cut MID-WORD at GitHub's 140-char
    cap, so it proves neither the full name nor that it was the only failure.
    Skipping on it would skip real reds hiding behind the truncation.

    This is the guard that makes the ledger safe to have at all.
    """
    _red_commit(h, "8" * 40, desc=REAL_FLAKE_DESC)
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert h.triggered()


def test_seven_failures_with_one_named_triggers(h):
    """A real row: `failed=7`, one test named. The count disagreeing with the
    names is exactly the evidence that the list is incomplete."""
    _red_commit(h, "9" * 40, desc=REAL_SEVEN_FAILED_ONE_NAMED)
    assert h.run().returncode == RC_TRIGGERED


def test_a_red_naming_no_test_triggers(h):
    """A real row that named nothing. No names means nothing to screen on."""
    _red_commit(h, "a1" + "0" * 38, desc=REAL_NO_NAME)
    assert h.run().returncode == RC_TRIGGERED


# 🔴 SPELLED OUT HERE, NOT READ OUT OF THE LEDGER. A mutation sweep caught the
# earlier version of these tests deriving the flake name from
# `main-status-watch.py` itself: emptying the ledger and replacing it with a
# nonsense string left all 30 tests GREEN, because the test rebuilt its fixture
# out of whatever the implementation happened to contain. That is
# `claude/RULES.md`'s "never derive a test's expectation from the implementation
# it tests" in its purest form. This literal is an independent restatement of
# the contract, and `test_the_ledger_contains_exactly_the_documented_flake`
# pins it two-way.
KNOWN_FLAKE_NAME = (
    "TestARefusedWriteIsIndistinguishableFromAnAbsentOne"
    ".test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference"
)


def test_the_ledger_contains_exactly_the_documented_flake():
    """Two-way: a name added to the ledger without being documented here fails,
    and removing the documented one fails."""
    assert _ledger() == frozenset({KNOWN_FLAKE_NAME}), (
        "the known-flake ledger moved. A name here can only ever suppress a "
        "confirmation run, so adding one is a decision, not a detail."
    )


def test_a_PROVABLY_COMPLETE_known_flake_row_does_not_trigger(h):
    """The only shape the screen may fire on: every failure named, the count
    agreeing, and every name on the ledger.

    ⚠ LABELLED HONESTLY: this shape was NOT observed in the 100 commits
    measured — the cap truncates real rows before the count. It is a synthetic
    construction, so this is an invariant guard on the screen's soundness, not
    evidence the screen earns its keep in production.
    """
    sha = "b1" + "0" * 38
    _red_commit(
        h, sha,
        desc=f"FAILED: pytests — FAILING: {KNOWN_FLAKE_NAME} | TOTAL failed=1  (floor: 1)",
    )
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert not h.triggered()
    assert "known flake" in proc.stdout


def test_a_complete_row_mixing_a_flake_with_a_real_failure_triggers(h):
    sha = "c1" + "0" * 38
    _red_commit(
        h, sha,
        desc=f"FAILED: pytests — FAILING: {KNOWN_FLAKE_NAME} | test_a_real_one "
             "| TOTAL failed=2  (f)",
    )
    assert h.run().returncode == RC_TRIGGERED


# 🔴 THE NEXT TWO EXIST BECAUSE THE MUTATION SWEEP PROVED THE GUARDS THEY COVER
# WERE UNREACHABLE. `test_the_REAL_known_flake_row_still_triggers` and
# `test_seven_failures_with_one_named_triggers` both pass on the REAL rows — but
# for the wrong reason: those rows' names do not match the ledger anyway (one is
# truncated mid-word, the other is a different test), so the name check alone
# decides them and the count checks never execute. Deleting `count is None` or
# `count != len(names)` left all 30 tests green. These reach past the name check
# with a fixture whose name DOES match, so the count guard is the only thing
# that can produce the required answer.

def test_a_LEDGER_MATCHING_name_with_no_count_still_triggers(h):
    """Truncated before `failed=`: the name matches, but nothing proves it was
    the ONLY failure. Completeness unprovable must mean confirm, not skip."""
    sha = "d2" + "0" * 38
    _red_commit(h, sha, desc=f"FAILED: pytests — FAILING: {KNOWN_FLAKE_NAME}")
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert h.triggered()


def test_a_LEDGER_MATCHING_name_with_MORE_failures_than_named_triggers(h):
    """`failed=3` while naming one flake: two failures are unaccounted for and
    either could be a real break hiding behind the flake's name.

    The count (3) is deliberately not 1, not 2, and not a multiple of the named
    set's size, so the assertion cannot be satisfied by an off-by-one mutant.
    """
    sha = "e2" + "0" * 38
    _red_commit(
        h, sha,
        desc=f"FAILED: pytests — FAILING: {KNOWN_FLAKE_NAME} | TOTAL failed=3  (f)",
    )
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert h.triggered()


def _load():
    """Import the script as a module so pure functions can be driven directly."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("msw", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger():
    return _load().KNOWN_FLAKES


# ══ DEBOUNCE ══════════════════════════════════════════════════════════════════

def test_the_same_red_sha_is_handed_over_exactly_once(h):
    """The timer fires far more often than the deadman's ~20-minute run. Without
    this the watcher would restart it every interval for the same evidence."""
    _red_commit(h, "d1" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    first = h.receipt.read_text()
    second = h.run()
    assert second.returncode == RC_OK, second.stdout
    assert "red episode already open" in second.stdout
    assert h.receipt.read_text() == first, "it re-triggered on unchanged evidence"


def test_a_NEW_red_sha_in_the_SAME_episode_does_NOT_trigger_again(h):
    """🔴 THIS EXPECTATION IS THE INVERSE OF THE FIRST DRAFT'S, DELIBERATELY.

    The debounce used to key on the newest VERDICTED sha, and that is the wrong
    unit: only ~22% of main commits ever get a verdict, so the verdicted sha
    changes repeatedly inside ONE sustained red window. Every change was a new
    trigger — at the measured 1.43h median verdict gap against ~40 min per
    deadman run, roughly a 47% duty cycle of the most expensive job on the box,
    during exactly the scenario this unit exists for.

    And it cost ATTENTION, not just compute: main-green-check carries
    `OnFailure = notify-failure@`, and its memo's red branch exits RC_RED
    WITHOUT re-running anything — so the second trigger fires the DND-defeating
    toast having measured nothing new.
    """
    _red_commit(h, "e1" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    _red_commit(h, "f1" + "0" * 38)      # a DIFFERENT red commit, same episode
    second = h.run()
    assert second.returncode == RC_OK, second.stdout
    assert "red episode already open" in second.stdout
    assert len(h.receipt.read_text().splitlines()) == 1, "it re-triggered mid-episode"


def test_a_GREEN_verdict_closes_the_episode_and_a_LATER_red_triggers_again(h):
    """The episode must actually END, or the unit alerts once and never again.
    A green verdict is the only thing that closes it — a red window ends when
    main is observed good, not when it stops being re-verdicted."""
    _red_commit(h, "a4" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED

    green = "b4" + "0" * 38
    h.serve_commits([green])
    h.serve_statuses(green, [_status(CTX_PY, "success", REAL_SUCCESS)])
    recovered = h.run()
    assert recovered.returncode == RC_OK
    assert "closed the red episode" in recovered.stdout

    _red_commit(h, "c4" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED, "a NEW episode must be able to trigger"
    assert len(h.receipt.read_text().splitlines()) == 2


def test_a_NON_VERDICT_does_not_close_an_open_episode(h):
    """Absence is not a fix. If it closed the episode, a red window with a burst
    of superseded commits would re-trigger the moment one got verdicted."""
    _red_commit(h, "d4" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    quiet = "e4" + "0" * 38
    h.serve_commits([quiet])
    h.serve_statuses(quiet, [_status(CTX_PY, "error", REAL_SUPERSEDED)])
    assert h.run().returncode == RC_OK
    _red_commit(h, "f4" + "0" * 38)
    again = h.run()
    assert again.returncode == RC_OK, again.stdout
    assert "red episode already open" in again.stdout
    assert len(h.receipt.read_text().splitlines()) == 1


# ══ UNMEASURED AND THE BLIND LADDER ═══════════════════════════════════════════

def test_an_unreadable_api_is_UNMEASURED_not_green(h):
    """No canned responses at all -> the fake gh exits 22."""
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "COULD NOT MEASURE" in proc.stdout
    assert "NOT 'main is green'" in proc.stdout
    assert not h.triggered()


def test_unparseable_json_is_UNMEASURED(h, tmp_path):
    (h.responses / "_repos_o_r_commits?sha_main&per_page_20".replace("?", "_")
     .replace("&", "_").replace("=", "_")).write_text("{not json", encoding="utf-8")
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout


def test_the_blind_ladder_escalates_after_N_consecutive_unmeasured_runs(h):
    """Setting no code was right per run and wrong forever — drift-check rc 18
    is the precedent. rc 12 fails the unit; it does NOT toast."""
    for i in range(2):
        assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3).returncode == RC_UNMEASURED, i
    proc = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3)
    assert proc.returncode == RC_BLIND, proc.stdout
    assert "BLIND" in proc.stdout


def test_one_good_run_resets_the_blind_ladder(h):
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3).returncode == RC_UNMEASURED
    sha = "a2" + "0" * 38
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "success", REAL_SUCCESS)])
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3).returncode == RC_OK
    # the streak restarts from zero, so two more unmeasured runs must NOT escalate
    h.responses.rename(h.tmp / "responses-off")
    (h.tmp / "responses").mkdir()
    for i in range(2):
        assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3).returncode == RC_UNMEASURED, i


def test_running_out_of_the_total_budget_is_UNMEASURED_not_a_kill(h):
    """🔴 The walk makes up to DEPTH+1 reads. Without its own deadline a slow
    GitHub would let systemd SIGTERM the unit at TimeoutStartSec, which fails it
    with no message and no streak entry — the script never gets to say COULD NOT
    MEASURE. A zero budget forces that path deterministically."""
    _red_commit(h, "f2" + "0" * 38)
    proc = h.run(MAIN_STATUS_WATCH_BUDGET="0")
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "budget" in proc.stdout
    assert not h.triggered()


def test_the_total_budget_PLUS_the_trigger_timeout_is_under_the_units_timeout():
    """🔴 THREE numbers, and the first draft of this pin named only TWO.

    `trigger_deadman`'s subprocess timeout is NOT charged against `_DEADLINE` —
    the budget covers the API walk only — so the real worst case is
    BUDGET + TRIGGER. Measured at a 10s budget: a fast API plus a HANGING
    trigger ran 30s, three times the budget. 120+30=150 < 180 is safe today,
    but any edit raising the budget into [150, 179] would keep a
    `budget < TimeoutStartSec` assertion GREEN while making SIGTERM reachable —
    the precise outcome the constant exists to prevent, which is what makes the
    two-number version worse than no pin.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    budget = int(re.search(r"^TOTAL_BUDGET_S = (\d+)", src, re.M).group(1))
    trigger = int(re.search(r"^TRIGGER_TIMEOUT_S = (\d+)", src, re.M).group(1))
    declared = int(re.search(r"^UNIT_TIMEOUT_START_SEC = (\d+)", src, re.M).group(1))
    home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
    start = home_nix.index("systemd.user.services.main-status-watch")
    end = home_nix.index("systemd.user.timers.main-status-watch")
    actual = int(re.search(r"TimeoutStartSec = (\d+)", home_nix[start:end]).group(1))
    assert declared == actual, (
        f"the script believes the unit's TimeoutStartSec is {declared}, "
        f"home.nix says {actual} — the guard is reasoning about the wrong number"
    )
    assert budget + trigger < actual, (
        f"worst case is budget {budget}s + trigger {trigger}s = {budget + trigger}s, "
        f"which must stay under TimeoutStartSec {actual}s or systemd SIGTERMs the "
        "run before it can report COULD NOT MEASURE"
    )


# ══ MALFORMED API SHAPES ══════════════════════════════════════════════════════
# 🔴 THIS RUNS UNATTENDED ON A TIMER. An AttributeError inside the walk exits 1
# with a bare traceback: the unit fails carrying no explanation, and — because
# `main` never reached its own handler — the blind streak is never touched, so a
# permanently broken watcher never escalates. Every unreadable shape must land on
# the SAME unmeasured path as a network failure.

def test_a_commit_list_of_non_objects_is_UNMEASURED_not_a_traceback(h):
    h.serve("/repos/o/r/commits?sha=main&per_page=20", ["not-an-object"])
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "COULD NOT MEASURE" in proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not h.triggered()


def test_a_status_row_that_is_not_an_object_is_UNMEASURED_not_a_traceback(h):
    sha = "a3" + "0" * 38
    h.serve_commits([sha])
    h.serve_statuses(sha, ["not-an-object"])
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr


def test_an_unexpected_exception_is_REPORTED_and_LADDERED_not_swallowed(h):
    """The net exists so a bug degrades to 'could not look', never to a silent
    unit failure — and never to a claim about main.

    🔴 THE FIXTURE HAD TO BE REBUILT: the first version fed a commit list that
    was a dict, which `find_newest_verdict` ALREADY rejects with a clean
    `Unmeasured` — so the test passed with the net deleted, green for the wrong
    reason, and a mutation sweep caught it. This shape passes every isinstance
    check and then explodes deep inside `classify`, where `.strip()` meets a
    dict — a genuinely UNANTICIPATED failure, which is the only kind the net is
    for.

    🔴 AND IT HAD TO BE REBUILT A SECOND TIME, FOR A FAULT THIS FILE ALREADY
    NAMES ONE SCREEN AWAY. It asserted `returncode in (RC_UNMEASURED, RC_BLIND)`
    — the same disjunction `test_a_failed_episode_write_does_NOT_trigger`'s
    docstring condemns as unable to tell "ladders" from "never escalates".
    MEASURED: it let SIXTEEN mutants of the general net survive a fully green
    suite, including both of its `RC_BLIND`s swapped for `RC_OK` and
    `n >= escalate` forced False — i.e. a net that can never escalate reads
    exactly like one that can. (⚠ The first version of this sentence said
    TWENTY-ONE. That was the whole function's survivor count, 23, minus a
    rounding-down nobody did: 3 were log-line deletions and 4 belonged to a
    different arm, the `except Unmeasured` this PR deletes. A measured number
    stated wider than what was measured is the same fault as a guard stated
    wider than its implementation, in the file auditing exactly that.)
    One rule, two places; this was the copy left unfixed. Assert the
    EXACT code on each run, and RUN THE LADDER so the escalation is observed
    rather than allowed.
    """
    sha = "b3" + "0" * 38
    h.serve_commits([sha])
    h.serve_statuses(sha, [
        {"context": CTX_PY, "state": "failure", "description": {"nested": "object"}},
    ])
    proc = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2)
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "UNEXPECTED AttributeError" in proc.stdout, proc.stdout
    assert "BUG in main-status-watch" in proc.stdout
    assert "blind ladder (streak 1/2" in proc.stdout, "an unexpected failure must be counted"
    assert "GREEN" not in proc.stdout
    assert not h.triggered()
    # the ladder must actually ADVANCE — and the traceback must survive to the
    # journal, which is stderr, not stdout.
    assert "Traceback" in proc.stderr, "the net swallowed the traceback"
    second = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2)
    assert second.returncode == RC_BLIND, (
        f"got {second.returncode} — a recurring bug must escalate exactly like a "
        "persistent outage, or rc 11 reports success forever"
    )


def test_a_NON_VERDICT_is_never_treated_as_green(h):
    """🔴 THE HEADLINE FACT ABOUT THIS SIGNAL: only ~21% of main commits carry an
    authoritative verdict, so 'main's status' is USUALLY NOT A VERDICT. Absence
    must be reported as absence — the design's answer is 'claim nothing, take no
    action, the 4-hourly deadman still covers it'."""
    shas = [f"{i:040x}" for i in range(5)]
    h.serve_commits(shas)
    for s in shas:
        h.serve_statuses(s, [_status(CTX_PY, "error", REAL_SUPERSEDED)])
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert "no authoritative" in proc.stdout
    assert "deadman still covers it" in proc.stdout
    assert "GREEN" not in proc.stdout
    assert not h.triggered()


def test_the_no_verdict_return_carries_a_SLICEABLE_sha(monkeypatch):
    """🔴 A PAIRING, NOT A VALUE. Every caller formats `sha[:8]`, and they are
    correct today only because verdict=="none" happens to be returned alongside
    an unusable sha — an invariant nothing enforced. Returning None there makes
    the slice a latent TypeError that only a future return-path pairing would
    reach, on a timer, silently. Pinning the TYPE is what makes the slice total.
    """
    mod = _load()
    monkeypatch.setattr(mod, "gh_json", lambda path, timeout=45: (
        [{"sha": "z" * 40}] if "commits?sha=main" in path
        else [{"context": CTX_PY, "state": "error", "description": REAL_SUPERSEDED}]
    ))
    sha, verdict, reds, walked = mod.find_newest_verdict("o/r", 5)
    assert verdict == "none"
    assert isinstance(sha, str), f"no-verdict sha must be str, got {type(sha).__name__}"
    assert sha[:8] == ""      # the slice every caller performs must not raise
    assert walked == 1


def test_a_REPEATEDLY_failing_trigger_ESCALATES_to_blind(h):
    """🔴 THE LADDER WAS STRUCTURALLY UNREACHABLE FOR THIS ENTIRE CLASS.

    `reset_streak()` used to run as soon as the WALK succeeded — which it does
    on every one of these runs — so the bump in the trigger-failure handler
    could only ever return 1 and `n >= escalate` was never true. Measured with
    escalate=2: six consecutive trigger failures all printed `streak 1/2` and
    returned rc 11, which `SuccessExitStatus = "10 11"` makes a systemd SUCCESS.

    Scenario: main-green-check.service not loaded (renamed unit, a switch
    without daemon-reload, a DBus hiccup) — `systemctl --user start` exits 5,
    the accelerator is inert, and the unit reports healthy forever. That is the
    shape drift-check's rc 18 exists to prevent, in the one arm where the
    trigger IS the whole job.

    The single-run test below is consistent with the bug; only the LADDER
    detects it, which is why this exists separately.
    """
    write_exec(h.trigger, 'echo "Failed to start: Unit not found." >&2\nexit 5\n')
    _red_commit(h, "a5" + "0" * 38)
    first = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3)
    assert first.returncode == RC_UNMEASURED, first.stdout
    assert "streak 1/3" in first.stdout, first.stdout

    _red_commit(h, "b5" + "0" * 38)
    second = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3)
    assert second.returncode == RC_UNMEASURED, second.stdout
    assert "streak 2/3" in second.stdout, "the walk succeeding reset the streak again"

    _red_commit(h, "c5" + "0" * 38)
    third = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3)
    assert third.returncode == RC_BLIND, third.stdout


def test_a_failed_episode_write_does_NOT_trigger(h, tmp_path):
    """🔴 THE ONLY FINDING THAT WOULD HAVE BEEN WORSE THAN NO PR AT ALL.

    The receipt used to be written AFTER a successful trigger, with no error
    handling. A failed write left the deadman started and no record, so the next
    run saw the same red as new and started it AGAIN — measured: runs 1/2/3 gave
    starts=1/2/3, each a real `systemctl --user start main-green-check.service`,
    i.e. two ~20-minute sandbox tiers every 10 minutes.

    The realistic cause is ENOSPC on ~/.cache, which shares the root filesystem
    with the nix store — CORRELATED with the failure, since the disk pressure
    that breaks the write is produced by the very builds least worth re-kicking.
    So it now records FIRST and fails CLOSED.

    🔴 THE FIRST VERSION OF THIS FIXTURE IS WHY THE REAL BUG SHIPPED GREEN. It
    made `red-episode` a DIRECTORY, which leaves the state dir itself writable —
    so `bump_streak()` succeeded and the ladder advanced (`11,12,12,12`). The
    docstring described ENOSPC; the fixture exercised something else, and could
    not reach the defect it claimed to cover. `test_an_ENOSPC_SHAPED_state_dir_
    ESCALATES` below now exercises the shape this docstring names.

    It also asserted `returncode in (RC_UNMEASURED, RC_BLIND)` — a disjunction
    that passes whichever the arm returns, so it could not tell "ladders" from
    "never escalates". Assert the exact code.
    """
    _red_commit(h, "d5" + "0" * 38)
    h.cache.mkdir(parents=True, exist_ok=True)
    # make the episode path unwritable by making it a directory
    (h.cache / "red-episode").mkdir()
    proc = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=3)
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "cannot record the red episode" in proc.stdout
    assert "streak 1/3" in proc.stdout
    assert "NOT triggering" in proc.stdout
    assert not h.triggered(), "it triggered without being able to record it"


def _enospc_shaped(cache):
    """The state dir exists but NOTHING in it can be written — the shape ENOSPC
    actually produces, and the one that breaks the ladder along with everything
    else. `exist_ok=True` means `mkdir()` succeeds, so this reaches the arms that
    a missing directory never does."""
    cache.mkdir(parents=True, exist_ok=True)
    os.chmod(cache, 0o500)


def test_an_ENOSPC_SHAPED_state_dir_ESCALATES_rather_than_reporting_success(h):
    """🔴 THE ROUND-2 🔴, AND IT IS F1's SHAPE REBUILT INSIDE F2's FIX.

    `open_episode`'s docstring names ENOSPC on ~/.cache as the realistic cause.
    ENOSPC breaks EVERY write in that directory — including `bump_streak()`'s,
    which swallowed its own OSError and returned a recomputed count nobody
    saved. So `n` was 1 on every run and `n >= escalate` was never true.
    Measured, escalate=2, six polls: rc `[11,11,11,11,11,11]`, `streak 1/2`
    every time — byte-for-byte the sequence the F1 commit cites as the bug it
    removed, and rc 11 is a systemd SUCCESS, so the unit stayed green forever.

    🔴 The inversion is what made it bite: `state.mkdir()` uses `exist_ok=True`,
    so the SAME root cause gave the loud rc 12 when the directory was missing
    and the silent rc 11 when it existed — and in production it is created on
    the first run and always exists afterwards, so the quiet branch is the one
    that actually happens.
    """
    _red_commit(h, "e5" + "0" * 38)
    _enospc_shaped(h.cache)
    try:
        rcs = [h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2).returncode for _ in range(3)]
    finally:
        os.chmod(h.cache, 0o700)
    assert rcs == [RC_BLIND, RC_BLIND, RC_BLIND], (
        f"got {rcs}; an arm whose ladder cannot be written must fail the unit "
        "immediately, not report success forever"
    )
    assert not h.triggered()


def test_an_ENOSPC_SHAPED_dir_does_not_falsely_claim_it_closed_the_episode(h):
    """The green branch printed "closing the red episode" while the unlink had
    failed and the file was still there — a log line asserting an action that
    did not happen, leaving the accelerator disarmed for every later red."""
    _red_commit(h, "f5" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    green = "a6" + "0" * 38
    h.serve_commits([green])
    h.serve_statuses(green, [_status(CTX_PY, "success", REAL_SUCCESS)])
    # ⚠ 0o500 blocks CREATE and UNLINK, not modification of an existing file —
    # so `close_episode`'s unlink fails while `bump_streak` (rewriting a file
    # that already exists) still succeeds. That is the honest shape here: the
    # episode cannot be cleared but the ladder DOES advance, so this arm
    # escalates on the second run rather than the first.
    os.chmod(h.cache, 0o500)
    try:
        first = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2)
        second = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2)
    finally:
        os.chmod(h.cache, 0o700)
    assert "closed the red episode" not in first.stdout, (
        "it claimed a state change it did not achieve"
    )
    assert "could NOT" in first.stdout
    assert "cannot clear the red episode" in first.stdout
    assert (first.returncode, second.returncode) == (RC_UNMEASURED, RC_BLIND), (
        f"got {first.returncode},{second.returncode} — an episode that cannot be "
        "cleared must ladder to BLIND, not report success forever"
    )


def test_an_uncreatable_state_dir_is_BLIND_not_a_quiet_success(h, tmp_path):
    """🔴 THE ARM THE MUTATION SWEEP PROVED UNCOVERED. Mutating this return to
    RC_OK SURVIVED a fully green 44-test suite — "I could not create my state
    dir" reading as "looked, nothing to do", the exact reassuring zero this
    design orbits.

    It is rc 12 rather than rc 11 on purpose: the streak file lives INSIDE the
    directory that could not be created, so this arm cannot ladder at all. An
    arm that never advances a ladder and never fails the unit is permanently
    silent.
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    proc = h.run(MAIN_STATUS_WATCH_CACHE=str(blocker / "cache"))
    assert proc.returncode == RC_BLIND, proc.stdout
    assert "cannot create the state dir" in proc.stdout
    assert "NOT 'main is green'" in proc.stdout
    assert not h.triggered()


# 🔴 EACH TERMINAL SUCCESS PATH RESETS THE STREAK — PINNED INDIVIDUALLY.
# The commit message claimed "the reset now happens on each TERMINAL SUCCESS
# path only", and an independent sweep found that asserted for four paths and
# ENFORCED for one: deleting the reset at the `none`, `episode-open` or
# `triggered` site left the whole suite green. Each now has its own case, built
# the same way — strand the streak at 1, take the path, and require that a
# LATER unmeasured run reports 1 again rather than 2.

def _strand_streak_at_one(h):
    """One unmeasured run, so the streak is 1 and escalate=2 is one step away.

    Turns the canned responses off itself — a caller that had just served a red
    would otherwise take a SUCCESS path here and strand nothing, which is how the
    first draft of these tests passed while measuring the wrong thing.
    """
    h.responses_off()
    proc = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2)
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "streak 1/2" in proc.stdout


def _assert_streak_was_reset(h, why):
    """A following unmeasured run must say 1/2 again, not 2/2."""
    h.responses_off()
    proc = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2)
    assert "streak 1/2" in proc.stdout, f"{why}: {proc.stdout}"


def test_the_NO_VERDICT_path_resets_the_streak(h):
    _strand_streak_at_one(h)
    sha = "b6" + "0" * 38
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "error", REAL_SUPERSEDED)])
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2).returncode == RC_OK
    _assert_streak_was_reset(h, "the no-verdict path did not reset the streak")


def test_the_EPISODE_ALREADY_OPEN_path_resets_the_streak(h):
    _red_commit(h, "c6" + "0" * 38)
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2).returncode == RC_TRIGGERED
    _strand_streak_at_one(h)
    _red_commit(h, "d6" + "0" * 38)
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2).returncode == RC_OK
    _assert_streak_was_reset(h, "the episode-open path did not reset the streak")


def test_the_TRIGGERED_path_resets_the_streak(h):
    _strand_streak_at_one(h)
    _red_commit(h, "e6" + "0" * 38)
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2).returncode == RC_TRIGGERED
    _assert_streak_was_reset(h, "the triggered path did not reset the streak")


def test_the_FLAKE_SCREENED_path_resets_the_streak(h):
    _strand_streak_at_one(h)
    sha = "f6" + "0" * 38
    _red_commit(
        h, sha,
        desc=f"FAILED: pytests — FAILING: {KNOWN_FLAKE_NAME} | TOTAL failed=1  (f)",
    )
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=2).returncode == RC_OK
    _assert_streak_was_reset(h, "the flake-screened path did not reset the streak")


# ══ THE EPISODE AGE BOUND ═════════════════════════════════════════════════════

def test_a_STUCK_episode_expires_instead_of_disarming_the_unit_forever(h):
    """🔴 `close_episode` is reachable from exactly two places and nothing bounds
    an episode's age. Measured paths where it stayed open indefinitely: main
    never gets a GREEN verdict inside the walk (renamed context, disabled
    pipeline, a red window longer than the walk); a crash between the record and
    the trigger; a second distinct breakage with no green between. In each, later
    reds returned rc 0 with no start and NO SIGNAL — and the `none` arm resets
    the streak, so nothing accumulated either.

    That is this file's own F1 argument applied to the state machine F5 added:
    an arm that can never advance a ladder and never fails the unit is
    permanently silent.
    """
    _red_commit(h, "a7" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    # a fresh episode suppresses
    _red_commit(h, "b7" + "0" * 38)
    assert h.run().returncode == RC_OK

    # age it past the bound
    mod = _load()
    ep = h.cache / "red-episode"
    sha_txt = ep.read_text().split()[0]
    ep.write_text(f"{sha_txt} {int(time.time()) - mod.EPISODE_MAX_AGE_S - 60}\n")

    _red_commit(h, "c7" + "0" * 38)
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert "treating this as a new one" in proc.stdout


def test_an_episode_file_with_no_timestamp_is_treated_as_ANCIENT(h):
    """An episode written by an older version, or truncated, must not grant an
    unbounded pass — an unreadable age is the one that most needs the bound."""
    _red_commit(h, "d7" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    (h.cache / "red-episode").write_text("deadbeef\n", encoding="utf-8")
    _red_commit(h, "e7" + "0" * 38)
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout


def test_a_FRESH_episode_is_still_suppressed(h):
    """The control for the two above: the bound must not defeat the debounce."""
    _red_commit(h, "f7" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    _red_commit(h, "a8" + "0" * 38)
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert "not re-triggering" in proc.stdout


def test_a_trigger_TIMEOUT_leaves_the_episode_open(h):
    """`systemctl start --no-block` can time out AFTER queueing the job, so
    closing the episode there would let the next poll start a run already under
    way — the one path that broke the 'one extra run per window' bound."""
    write_exec(h.trigger, "sleep 30\n")
    _red_commit(h, "b8" + "0" * 38)
    proc = h.run(MAIN_STATUS_WATCH_BUDGET="60", MAIN_STATUS_WATCH_BLIND_ESCALATE=9)
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "LEFT OPEN" in proc.stdout, proc.stdout
    assert (h.cache / "red-episode").exists()


def test_a_NON_timeout_trigger_failure_DROPS_the_episode_so_a_retry_is_possible(h):
    write_exec(h.trigger, 'echo "Unit not found." >&2\nexit 5\n')
    _red_commit(h, "c8" + "0" * 38)
    proc = h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=9)
    assert proc.returncode == RC_UNMEASURED
    assert not (h.cache / "red-episode").exists()
    # 🔴 THE MIRROR IMAGE OF "never claim a state change you did not achieve":
    # never report a FAILURE that did not happen either. Dropping the `not` from
    # `if not state.close_episode():` survived the sweep — the episode really was
    # dropped while the run warned that it had not been, which would send an
    # operator looking for a stuck file that is not there.
    assert "could not drop the episode" not in proc.stdout, proc.stdout
    # and once the trigger works again, it really does retry
    write_exec(h.trigger, f'echo "$@" >> "{h.receipt}"\nexit 0\n')
    assert h.run(MAIN_STATUS_WATCH_BLIND_ESCALATE=9).returncode == RC_TRIGGERED


def test_a_failing_trigger_is_UNMEASURED_not_a_silent_success(h):
    """If systemctl cannot start the unit, saying nothing would leave the
    operator believing the early path is armed when it is not."""
    write_exec(h.trigger, 'echo "boom" >&2\nexit 5\n')
    _red_commit(h, "b2" + "0" * 38)
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "COULD NOT MEASURE" in proc.stdout


# ══ STATIC GUARDS ═════════════════════════════════════════════════════════════

def _code_only():
    """Executable lines only. A static guard that reads prose is walkable by
    rewording AND breakable by documenting the very hazard it forbids.

    ⚠ THE NAME USED TO OVER-CLAIM: it stripped `#` comments and kept every
    DOCSTRING, so each guard below still read a large amount of prose — and this
    file's own docstrings discuss `/status`, `--force` and `systemctl` at
    length. Nothing tripped yet, which is the point: the guard was one
    explanatory sentence away from a false red, in a file whose habit is to
    explain its hazards next to them. Docstrings are stripped by AST now, so
    the sentence is true rather than aspirational.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    drop = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node) is not None:
            first = node.body[0]
            drop.update(range(first.lineno, first.end_lineno + 1))
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in drop or line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_code_only_really_strips_the_prose_it_claims_to():
    """The instrument behind four static guards, with both controls.

    NEGATIVE: a phrase that exists ONLY in a docstring must be gone.
    POSITIVE: a phrase that exists only in executable code must survive — or
    `_code_only` could satisfy the first assertion by returning nothing at all,
    and every guard reading it would pass vacuously.
    """
    code = _code_only()
    raw = SCRIPT.read_text(encoding="utf-8")
    only_in_a_docstring = "Raised for every reason the world could not be read"
    assert only_in_a_docstring in raw, "fixture phrase moved; this guard is blind"
    assert only_in_a_docstring not in code, "docstring prose still reaches the guards"
    assert "def classify(state, description):" in code, (
        "_code_only stripped executable lines too — every guard reading it is vacuous"
    )


def test_the_script_never_reads_the_rollup_status_endpoint():
    """🔴 THE DEFECT THIS WHOLE FILE ORBITS. `/commits/{sha}/status` (singular)
    collapses `error` into `failure`; `/statuses` (plural) has no roll-up field.
    Structural, not stylistic: on the roll-up, 48 of 60 recent main pushes read
    as red when 9 rows in 200 actually were."""
    code = _code_only()
    assert "/statuses" in code, "the list endpoint is the whole point"
    import re as _re
    bad = _re.findall(r"/status(?![e/])", code)
    assert not bad, f"the roll-up endpoint is reachable from the code: {bad}"


def test_the_production_trigger_is_systemctl_start_main_green_check():
    """Every behavioural test drives the seam, so only this sees production."""
    code = _code_only()
    assert '"systemctl", "--user", "start", "--no-block", "main-green-check.service"' in code
    assert "--force" not in code, (
        "--force would let a status row overrule the deadman's own reproduction"
    )


def test_the_trigger_verb_is_MUTATING_so_the_stub_fails_it_closed():
    """🔴 THE PIN `test_no_real_launchers.py` FILES ITS ACKNOWLEDGEMENT AGAINST.

    `systemctl` is not in `nolaunch.HOST_LAUNCHERS`; it is VERB-SPLIT, and the
    stub passes a read verb through while blocking a mutating one. syshealth and
    tmux-restore-observe.sh are acknowledged because their verbs ARE reads. This
    file is the opposite case — `start` is mutating on purpose, since starting
    the deadman is the entire job — so its safety rests on:

      1. the SEAM: every behavioural test above sets MAIN_STATUS_WATCH_TRIGGER,
         so none executes the production argv;
      2. FAIL-CLOSED: `start` not being a read verb means the stub SWALLOWS the
         call rather than passing it to the host, so a future test that forgets
         the seam cannot kick off a 20-minute gate run on the operator's box.

    ⚠ BE PRECISE ABOUT LEG 2, because the obvious wording ("it would get an
    error") is HALF RIGHT and is the same half-right sentence the ledger entry in
    `test_no_real_launchers.py` was corrected for. The blocked stub does NOT
    fail: it EXITS 0. So a seam-less test would see `trigger_deadman()` succeed,
    print TRIGGERED, write an episode file and return rc 10 — passing VACUOUSLY
    rather than failing loudly. The SAFETY claim is complete (nothing on the host
    starts); what leg 2 does not give you is a signal that your test is inert.
    That is leg 1's job, which is why it is stated first and not treated as
    redundant. One rule, two places — and this was the copy left unfixed.

    Leg 2 is what protects against a test nobody has written yet, and it is only
    true while the verb stays mutating and stays the ONLY call site. Both are
    asserted here, against `SYSTEMCTL_READ_VERBS` ITSELF rather than a copied
    literal — the acknowledgements above were each shown to blind the guard they
    were filed under, and this one is not allowed to.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from testlib import nolaunch  # noqa: E402

    code = _code_only()
    # exactly one systemctl call site, so the acknowledgement cannot absorb a second
    assert code.count('"systemctl"') == 1, (
        "main-status-watch.py has grown a second systemctl call site — "
        "test_no_real_launchers.py's acknowledgement covers ONE, and a new one "
        "must be justified there, not absorbed here"
    )
    m = re.search(r'"systemctl",\s*"--user",\s*"([a-z-]+)"', code)
    assert m, "could not find the systemctl argv — the pin cannot read the verb"
    verb = m.group(1)
    assert verb == "start", f"the trigger verb moved to {verb!r}"
    assert verb not in nolaunch.SYSTEMCTL_READ_VERBS, (
        f"{verb!r} is now a READ verb, so the verb-splitting stub would PASS IT "
        "THROUGH instead of failing closed — leg 2 of the acknowledgement in "
        "test_no_real_launchers.py is void and must be re-argued"
    )
    # POSITIVE CONTROL: the list is real and this comparison can actually match.
    assert "status" in nolaunch.SYSTEMCTL_READ_VERBS, (
        "SYSTEMCTL_READ_VERBS looks empty or renamed — the assertion above "
        "would then pass for every possible verb"
    )


def test_the_watcher_is_not_wired_to_the_do_not_disturb_toast():
    """🔴 A RELATIONSHIP, not a word: the accelerator must never take the
    operator's attention, because its failure mode is 'coverage is what it was
    yesterday'. `notify-failure@` is the DND-defeating class."""
    home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
    start = home_nix.index("systemd.user.services.main-status-watch")
    end = home_nix.index("systemd.user.timers.main-status-watch")
    block = home_nix[start:end]
    assert "notify-failure" not in block, (
        "the early-trigger unit must not be a source of the DND-bypassing toast"
    )


def test_every_env_var_the_code_reads_is_documented_in_the_header():
    """🔴 TWO-WAY. An undocumented knob is a knob nobody can find; one named in
    the header but no longer read is a lie in the `--help` output.

    This exists because `MAIN_STATUS_WATCH_BUDGET` was read for a full commit
    before anything mentioned it — the same shape as an undocumented `MIN_TESTS`.

    ⚠ The rationale first written here — "the header IS the `--help` text" — was
    FALSE: `--help` printed `__doc__`, one line, naming ZERO knobs. Rather than
    delete the sentence, `--help` now prints the header, which
    `test_help_actually_prints_the_env_knobs` verifies. So the claim is true by
    construction instead of by assertion.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    read = set(re.findall(r'os\.environ\.get\(\s*"(MAIN_STATUS_WATCH_[A-Z_]+)"', src))
    documented = set(re.findall(r"^#\s+(MAIN_STATUS_WATCH_[A-Z_]+)\s", src, re.M))
    assert read, "found no env reads at all — this guard would pass vacuously"
    assert read == documented, (
        f"read but undocumented: {sorted(read - documented)}; "
        f"documented but never read: {sorted(documented - read)}"
    )


def test_help_actually_prints_the_env_knobs(h):
    """Makes the guard above's rationale true rather than merely asserted.

    A `--help` that prints nothing and exits 0 is the reassuring zero this file
    argues against, so the positive control is the COUNT, not the exit code.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=60, env=h.env(),
    )
    assert proc.returncode == RC_OK, proc.stderr
    src = SCRIPT.read_text(encoding="utf-8")
    knobs = set(re.findall(r'os\.environ\.get\(\s*"(MAIN_STATUS_WATCH_[A-Z_]+)"', src))
    missing = {k for k in knobs if k not in proc.stdout}
    assert not missing, f"--help does not mention {sorted(missing)}"
    assert len(proc.stdout.splitlines()) > 20, "--help printed a stub, not the header"


def test_the_blind_ladder_default_lives_in_exactly_one_place():
    """🔴 A CONSOLIDATION GUARD, not a style rule. The escalation default was
    open-coded at BOTH ladder sites — literal and parsing guard duplicated. The
    ladder's failure mode is SILENCE, so two copies drifting apart would not
    announce themselves. Pinning the count is what stops the third copy."""
    code = _code_only()
    assert code.count("MAIN_STATUS_WATCH_BLIND_ESCALATE") == 1, (
        "the escalation env var is read in more than one place — call "
        "blind_escalate() instead of re-deriving it"
    )
    assert code.count("BLIND_ESCALATE_DEFAULT = 12") == 1
    assert code.count("blind_escalate()") >= 2, "the helper must actually be used"


def test_blind_escalate_parses_and_defaults(monkeypatch):
    """The extraction is behaviour-preserving, so this pins the behaviour it
    preserved rather than claiming new coverage."""
    mod = _load()
    monkeypatch.delenv("MAIN_STATUS_WATCH_BLIND_ESCALATE", raising=False)
    assert mod.blind_escalate() == 12
    monkeypatch.setenv("MAIN_STATUS_WATCH_BLIND_ESCALATE", "5")
    assert mod.blind_escalate() == 5
    # a bad value must fall back, never raise: this is called on the FAILURE path
    monkeypatch.setenv("MAIN_STATUS_WATCH_BLIND_ESCALATE", "not-a-number")
    assert mod.blind_escalate() == 12


def test_a_context_typo_is_not_silent():
    """The context prefix is a literal contract with `devrc-ci-push-main`. A typo
    makes every walk find nothing — so an empty walk must SAY so, never pass as
    green. This pins the message that makes that visible."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'CONTEXT_PREFIX = "tekton/devrc-main-"' in src
    assert "no authoritative" in src


# ══ ROUND 3: THE ARMS AN ENUMERATED SWEEP PROVED UNCOVERED ════════════════════
# 🔴 HONESTLY LABELLED. Every case in this block was found by MUTATION, not by a
# defect anyone observed running: the 440-mutant enumerated sweep that produced
# them is described in the PR. So each is a MUTATION-DERIVED guard — the matrix
# it carries is "red on mutant M<nnn>, green at HEAD", not "red at a base".
# `test_an_Unmeasured_that_ESCAPES_mains_handler_still_LADDERS` is the one
# exception: it is red at the pre-change source, because that one was a defect.


def test_a_MOVED_docstring_sentinel_REFUSES_rather_than_dumping_the_file(tmp_path):
    """🔴 THE SURVIVOR THE ROUND-2 SWEEP FOUND AND NOBODY CLOSED.

    `print_header` bounds the header by the module docstring's opening line, and
    the whole arm that fires when that sentinel MOVES had no coverage at all:
    three separate mutants of it survived a fully green 61-test suite —

      * `return False` -> `return True`  (the refusal becomes a silent rc 0)
      * `if end is None:` -> `if False:`  (`src[1:None]` DUMPS THE WHOLE FILE)
      * deleting the diagnostic `say(...)` (the operator's only clue)

    Every behavioural test reaches this function on the HAPPY path only, where
    the sentinel is always found, so none of them can see any of the three. The
    file argues at length that a `--help` printing nothing and exiting 0 is the
    reassuring zero it exists to prevent — this is the test that makes the
    argument enforceable.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    moved = src.replace('"""Start the main-green deadman early',
                        '"""Begin the main-green deadman early', 1)
    assert moved != src, "the docstring sentinel is not where this test thinks"
    copy = tmp_path / "moved-sentinel.py"
    copy.write_text(moved, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(copy), "--help"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == RC_USAGE, proc.stdout + proc.stderr
    assert "cannot locate the end of the header" in proc.stdout
    # and it must NOT fall back to dumping the source: a header knob name would
    # appear in the output if `src[1:None]` had printed the whole file.
    assert "MAIN_STATUS_WATCH_BUDGET" not in proc.stdout, (
        "a moved sentinel dumped the file instead of refusing"
    )
    # POSITIVE CONTROL for the line above: the UNmoved script really does print
    # that knob, so its absence is evidence rather than a pattern that never
    # matches anything.
    ok = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                        capture_output=True, text=True, timeout=60)
    assert "MAIN_STATUS_WATCH_BUDGET" in ok.stdout


def test_an_unknown_argument_is_a_USAGE_error(h):
    """The other half of the argv arm, and it was uncovered too: mutating
    `RC_USAGE = 2` to 3, and forcing `if len(argv) > 1` to False, both survived.
    """
    proc = subprocess.run([sys.executable, str(SCRIPT), "--not-a-flag"],
                          capture_output=True, text=True, timeout=60, env=h.env())
    assert proc.returncode == RC_USAGE, proc.stdout + proc.stderr
    assert "unknown argument: --not-a-flag" in proc.stdout
    assert not h.triggered()


# ── classify's vocabulary ─────────────────────────────────────────────────────
# 🔴 THE SWEEP'S WORST FINDING, AND IT IS AN "ABSENCE READS AS GREEN" ONE.
# `classify`'s docstring said the tests drive it directly. No test called it at
# all — and an end-to-end test STRUCTURALLY CANNOT see most of what it decides,
# because `commit_verdict` branches on green/red only and folds every other
# class into "not a verdict". MEASURED: 35 of its 60 enumerated mutants survived
# a fully green suite, and the 25 that died were exactly those that moved an
# answer INTO green-or-red. ⚠ Be precise about that — an earlier wording here
# said "every mutant of its pending and error arms survived", and a quarter of
# them did not. Overstating a measurement is the same fault as overstating a
# guard, in the file that exists to catch it.
#
# The part that is not merely unobservable is the pair of fall-through
# `return "error-other"`s: NO fixture reaches either, so turning one into
# `return "green"` survived. The catch-all is what an UNRECOGNISED state lands
# on, so a state GitHub adds tomorrow would be folded into "main is green" —
# closing an open red episode and disarming the accelerator on the strength of a
# word nobody has seen yet.

@pytest.mark.parametrize(
    "state,description,expected",
    [
        ("success", REAL_SUCCESS, "green"),
        ("failure", REAL_NO_NAME, "red"),
        ("pending", "devrc gate running", "pending"),
        ("error", REAL_SUPERSEDED, "superseded"),
        ("error", REAL_KILLED, "killed"),
        ("error", REAL_NO_GATE_POD, "no-gate-pod"),
        ("error", "something this pipeline has never posted", "error-other"),
        # the arms nothing drove: an unrecognised state, and a missing one.
        ("queued", "a state this leg does not post today", "error-other"),
        ("", "", "error-other"),
        (None, None, "error-other"),
    ],
)
def test_classify_maps_a_row_to_its_documented_class(state, description, expected):
    assert _load().classify(state, description) == expected


def test_an_UNRECOGNISED_state_is_never_green_end_to_end(h):
    """The behavioural half of the case above, because a unit test on `classify`
    alone cannot see what the walk does with its answer. A state neither this
    leg nor this script knows must leave `main` UNCLAIMED — not green, not red.
    """
    sha = "a9" + "0" * 38
    h.serve_commits([sha])
    h.serve_statuses(sha, [_status(CTX_PY, "queued", "a state nobody predicted")])
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert "no authoritative" in proc.stdout
    assert "GREEN" not in proc.stdout
    assert not h.triggered()


def test_the_flake_screen_refuses_an_EMPTY_description_set():
    """`screen_all_known_flakes([])` returns True if its first guard is removed —
    i.e. it would SUPPRESS a confirmation run having been told nothing at all.
    The caller cannot reach that today, which is exactly why the guard needs a
    test rather than a caller: an unreachable guard nobody drives is one refactor
    away from being deleted as dead.
    """
    mod = _load()
    assert mod.screen_all_known_flakes([]) is False
    # POSITIVE CONTROL: the function CAN return True, so the assertion above is
    # not passing because it always returns False.
    assert mod.screen_all_known_flakes(
        [f"FAILED: pytests — FAILING: {KNOWN_FLAKE_NAME} | TOTAL failed=1"]
    ) is True


# ── the production repo path ──────────────────────────────────────────────────
# 🔴 THE HEADER CLAIMED THIS WAS PINNED AND IT WAS NOT. Every behavioural test
# sets the repo override, so nothing exercised `resolve_repo` past its first
# line: forcing `if not m` to True or False, and `proc.returncode != 0` to
# either, all survived — TEN of its ELEVEN enumerated mutants. ⚠ Not "zero
# coverage", which is what this comment said first: the ONE death is the
# override branch itself, and it dies only because every other test in the file
# depends on it. Incidental coverage of the line that bypasses the function is
# not coverage of the function. That is the same seam-drift hazard
# `test_the_production_trigger_...` exists for, on the other production argv.

def _fake_git(monkeypatch, mod, *, rc=0, stdout="", calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(
        run=run,
        TimeoutExpired=subprocess.TimeoutExpired,
        CompletedProcess=subprocess.CompletedProcess,
    ))


@pytest.mark.parametrize("url", [
    "git@github.com:owner/name.git",
    "https://github.com/owner/name.git",
    "https://github.com/owner/name",
    "ssh://git@github.com/owner/name.git",
])
def test_resolve_repo_parses_every_origin_url_shape(monkeypatch, url):
    mod = _load()
    monkeypatch.delenv("MAIN_STATUS_WATCH_REPO", raising=False)
    calls = []
    _fake_git(monkeypatch, mod, stdout=url + "\n", calls=calls)
    assert mod.resolve_repo() == "owner/name"
    # and it asked the question production asks — the seam cannot drift away
    # from `git remote get-url origin` while the behavioural tests stay green.
    assert calls[0][:2] == ["git", "-C"]
    assert calls[0][-3:] == ["remote", "get-url", "origin"]


def test_the_repo_override_wins_and_does_not_shell_out(monkeypatch):
    mod = _load()
    monkeypatch.setenv("MAIN_STATUS_WATCH_REPO", "o/r")
    calls = []
    _fake_git(monkeypatch, mod, stdout="git@github.com:other/thing.git\n", calls=calls)
    assert mod.resolve_repo() == "o/r"
    assert calls == [], "the override must short-circuit before running git"


def test_an_unparseable_origin_url_is_UNMEASURED_not_a_guess(monkeypatch):
    mod = _load()
    monkeypatch.delenv("MAIN_STATUS_WATCH_REPO", raising=False)
    _fake_git(monkeypatch, mod, stdout="not-a-url\n")
    with pytest.raises(mod.Unmeasured) as exc:
        mod.resolve_repo()
    assert "cannot parse owner/name" in str(exc.value)


def test_a_FAILING_git_is_UNMEASURED_not_an_empty_repo_name(monkeypatch):
    mod = _load()
    monkeypatch.delenv("MAIN_STATUS_WATCH_REPO", raising=False)
    _fake_git(monkeypatch, mod, rc=128, stdout="")
    with pytest.raises(mod.Unmeasured) as exc:
        mod.resolve_repo()
    assert "cannot read origin remote" in str(exc.value)


# ── the API reader's own failure reporting ────────────────────────────────────

def test_a_NON_ZERO_gh_exit_is_reported_with_its_code_and_stderr(h):
    """Forcing `if proc.returncode != 0:` to False survived, because the fake gh
    printed NOTHING on failure — so `json.loads("")` raised and the run was
    unmeasured either way, for the wrong reason. `gh api` really does print a
    JSON error body on stdout, and with it the returncode check is the ONLY
    thing standing between an HTTP 403 and a confident 'no commits returned'.
    """
    write_exec(h.gh,
               'echo "{\\"message\\": \\"API rate limit exceeded\\"}"\n'
               'echo "gh: rate limited" >&2\nexit 1\n')
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "exited 1" in proc.stdout, proc.stdout
    assert "rate limited" in proc.stdout, "the operator's only clue is dropped"
    assert not h.triggered()


def test_an_EMPTY_commit_list_is_UNMEASURED_not_a_quiet_nothing_to_do(h):
    """`if not isinstance(commits, list) or not commits:` forced to False
    survived: with the guard gone an empty list walks zero commits and reports
    rc 0, 'no authoritative verdict in the newest 0 commits'. An API that
    returned no commits at all has told us nothing, and 'nothing to do' is the
    reassuring zero — it must ladder like every other unreadable answer.
    """
    h.serve("/repos/o/r/commits?sha=main&per_page=20", [])
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "no commits returned" in proc.stdout
    assert not h.triggered()


# ── the outermost net ─────────────────────────────────────────────────────────

def test_an_Unmeasured_that_ESCAPES_mains_handler_still_LADDERS(tmp_path, monkeypatch):
    """🔴 THE ONE ROUND-3 FINDING THAT IS A DEFECT, NOT A COVERAGE GAP, AND IT IS
    F1's SHAPE FOR THE THIRD TIME.

    `_guarded_main` had a dedicated `except Unmeasured` arm that printed COULD
    NOT MEASURE and returned rc 11 — WITHOUT bumping the blind streak. rc 11 is
    a systemd SUCCESS, so an `Unmeasured` raised anywhere outside `main`'s own
    handler was an arm that can never advance a ladder and never fails the unit:
    permanently silent, which is precisely what this file's F1/F3 comments
    condemn and what `ladder_exit`'s docstring says it applies "wherever that
    condition arises".

    Its own docstring asserted the opposite — "the blind ladder counts it — so a
    bug that recurs escalates to rc 12 exactly like a persistent outage" — a
    coverage claim wider than the implementation, for the arm the sentence was
    describing. The fix deletes the special case: `Unmeasured` is an `Exception`,
    so the general arm already reports it AND ladders it.

    RED at the pre-change source (11, 11); GREEN here (11, 12).
    """
    mod = _load()
    monkeypatch.setenv("MAIN_STATUS_WATCH_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("MAIN_STATUS_WATCH_BLIND_ESCALATE", "2")

    def boom(argv):
        raise mod.Unmeasured("a path that raised after the handler")

    monkeypatch.setattr(mod, "main", boom)
    first = mod._guarded_main(["main-status-watch.py"])
    second = mod._guarded_main(["main-status-watch.py"])
    assert (first, second) == (RC_UNMEASURED, RC_BLIND), (
        f"got {first},{second} — an arm whose failures are never counted can "
        "never escalate, and rc 11 is a systemd SUCCESS"
    )
    assert (tmp_path / "cache" / "blind-streak").exists(), (
        "nothing was recorded, so this arm is permanently silent"
    )


def test_the_state_directory_default_lives_in_exactly_one_place():
    """🔴 A CONSOLIDATION GUARD, the same one the escalation default already has.

    The cache root was open-coded at TWO sites — `main` and the outermost net —
    and the second is the one that runs when the first has already failed. Two
    copies drifting apart would send the blind streak to a different directory
    than the one the next run reads, which silently un-ladders the net: the
    failure mode is SILENCE, so it would not announce itself.
    """
    code = _code_only()
    assert code.count("MAIN_STATUS_WATCH_CACHE") == 1, (
        "the cache env var is read in more than one place — call cache_root()"
    )
    assert code.count('".cache"') == 1, "the default path is derived twice"
    assert code.count("cache_root()") >= 2, "the helper must actually be used"


def test_every_test_this_script_names_actually_exists():
    """🔴 THE PROSE FINDING THAT REPEATED AT EVERY ROUND OF THIS ARC: a comment
    pointing at a test by name, where the test was renamed and only one copy of
    the rule moved.

    Measured: `main-status-watch.py:463` named
    `test_the_total_budget_is_under_the_units_timeout`, which the round-2 fix had
    renamed to `..._PLUS_the_trigger_timeout_...` when it added the third number.
    The pointer read as a citation and resolved to nothing.

    The name is joined across comment line-breaks first, because that wrap is
    exactly what hid it from every grep anyone ran.

    ⚠ THE JOIN MUST ALLOW AN INDENTED `#`, and the first version of this guard
    did not — it anchored the continuation at column 0, so a wrapped name inside
    a FUNCTION body stayed split and read as a name nobody defined. It failed on
    its own first run against a citation this very PR added, which is the
    cheapest possible way to learn that a pattern is narrower than its claim.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    joined = re.sub(r"_\n[ \t]*#[ \t]*", "_", src)
    referenced = set(re.findall(r"\btest_[a-z][A-Za-z0-9_]+", joined))
    defined, stems = set(), set()
    for p in sorted((ROOT / "scripts" / "tests").glob("test_*.py")):
        stems.add(p.stem)
        defined |= set(re.findall(r"^def (test_[A-Za-z0-9_]+)", p.read_text(encoding="utf-8"), re.M))
    assert referenced, "the script names no tests at all — this guard is vacuous"
    # POSITIVE CONTROL: the comparison set is real and CAN match.
    assert "test_a_context_typo_is_not_silent" in defined
    missing = sorted(referenced - defined - stems)
    assert not missing, f"the script cites tests that do not exist: {missing}"


# ══ ROUND 4: WHAT THE ROUND-3 DELTA'S OWN SWEEP FOUND ═════════════════════════
# 🔴 AN AUDIT FIX RESETS THE VERIFICATION GATE, and re-running the sweep against
# the FIXED tree is what makes that concrete. It cut survivors 155 -> 73 and then
# found three arms round 3 had not reached — one of them an arm round 3's own PR
# body claimed it had covered. That claim was written from the finding list, not
# from the diff.

def test_a_commit_with_ONLY_FOREIGN_contexts_is_no_verdict_not_green(h):
    """🔴 THE ONE ROUND 3 SAID IT HAD CLOSED AND HAD NOT.

    `commit_verdict` returns `"none"` when NO row carries the main-gate prefix,
    and mutating that return to `"green"` survived both sweeps: the existing
    foreign-context test always serves a real leg alongside the foreign one, so
    the empty-`per_ctx` arm is never reached. A commit carrying only
    `tekton/devrc-cairn-client-runs` rows would then report main GREEN off
    another pipeline's verdict — and a green CLOSES an open red episode, so the
    accelerator would be disarmed by a status it must not read at all.
    """
    sha = "b9" + "0" * 38
    h.serve_commits([sha])
    h.serve_statuses(sha, [
        _status("tekton/devrc-cairn-client-runs", "success", "all good elsewhere"),
        _status("tekton/devrc-something-else", "failure", "FAILED: a different pipeline"),
    ])
    proc = h.run()
    assert proc.returncode == RC_OK, proc.stdout
    assert "no authoritative" in proc.stdout, proc.stdout
    assert "GREEN" not in proc.stdout, "another pipeline's verdict was read as main's"
    assert not h.triggered()


def test_an_OPEN_episode_SURVIVES_a_foreign_only_commit(h):
    """The consequence that makes the case above matter rather than merely be
    wrong: if a foreign-only commit read as green it would close the episode,
    and the next red in the same window would re-trigger the deadman."""
    _red_commit(h, "c9" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    foreign = "d9" + "0" * 38
    h.serve_commits([foreign])
    h.serve_statuses(foreign, [_status("tekton/devrc-cairn-client-runs", "success", "ok")])
    assert h.run().returncode == RC_OK
    assert (h.cache / "red-episode").exists(), "a foreign verdict closed the episode"


def test_the_NETs_own_ladder_FAILS_THE_UNIT_when_it_cannot_PERSIST(h, monkeypatch):
    """🔴 F2's SHAPE INSIDE THE NET — THE ONE COPY NOTHING DROVE.

    `ladder_exit` handles an unwritable streak by failing the unit NOW, and
    `test_an_ENOSPC_SHAPED_state_dir_ESCALATES_rather_than_reporting_success`
    pins it. The outermost net re-implements the same rule, and its copy had no
    test: mutating its `RC_BLIND` to `RC_OK`, or forcing `not persisted` to
    False, all survived. Under ENOSPC — the cause `open_episode`'s docstring
    names — the net would then report SUCCESS while permanently blind, which is
    the exact pair of bugs (F1's silence, F2's uncountable alarm) this file was
    built to remove.

    The directory is made 0o500 AFTER it exists and BEFORE any streak file is
    written, so `mkdir(exist_ok=True)` succeeds and the write is what fails —
    the shape ENOSPC actually produces.
    """
    mod = _load()
    cache = h.tmp / "net-cache"
    cache.mkdir()
    monkeypatch.setenv("MAIN_STATUS_WATCH_CACHE", str(cache))

    def boom(argv):
        raise ValueError("a shape nobody predicted")

    monkeypatch.setattr(mod, "main", boom)
    os.chmod(cache, 0o500)
    try:
        rc = mod._guarded_main(["main-status-watch.py"])
    finally:
        os.chmod(cache, 0o700)
    assert rc == RC_BLIND, (
        f"got {rc} — a net whose ladder cannot be written must fail the unit now, "
        "not report success forever"
    )
    assert not (cache / "blind-streak").exists(), "fixture is wrong: the write succeeded"


def test_the_episode_bound_matches_the_deadmans_OWN_cadence():
    """🔴 A CROSS-FILE RELATIONSHIP ASSERTED IN PROSE AND PINNED BY NOTHING.

    `EPISODE_MAX_AGE_S`'s comment argues the number is not a taste: 4h is chosen
    to equal `main-green-check`'s own `OnUnitActiveSec`, because past that point
    the deadman re-runs against the tip ANYWAY, so re-triggering adds no work
    that was not already going to happen. Change the deadman's cadence and that
    argument is silently false — the same failure
    `test_the_total_budget_PLUS_the_trigger_timeout_is_under_the_units_timeout`
    exists to stop for TimeoutStartSec, one file over. Mutating the `4` survived
    both sweeps, because the behavioural test reads the constant out of the
    implementation it is testing.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^EPISODE_MAX_AGE_S = (\d+) \* 60 \* 60", src, re.M)
    assert m, "EPISODE_MAX_AGE_S is no longer written in hours; re-read this guard"
    episode_hours = int(m.group(1))
    home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
    start = home_nix.index("systemd.user.timers.main-green-check")
    end = home_nix.index("systemd.user.services.main-status-watch")
    block = home_nix[start:end]
    cadence = re.search(r'OnUnitActiveSec = "(\d+)h"', block)
    assert cadence, "the deadman timer's cadence is no longer stated in whole hours"
    assert episode_hours == int(cadence.group(1)), (
        f"the episode bound is {episode_hours}h but main-green-check re-runs every "
        f"{cadence.group(1)}h — the bound's whole argument is that they are equal"
    )


def test_a_commit_entry_with_NO_sha_is_skipped_not_walked(h):
    """A malformed entry must be stepped over, not turned into a request for
    `/commits//statuses`. Forcing `if not sha: continue` off survived: the walk
    then asks for an empty sha, gets nothing, and the whole run is UNMEASURED —
    one bad element in the list costing the verdict that was two entries down.
    """
    good = "e9" + "0" * 38
    h.serve("/repos/o/r/commits?sha=main&per_page=20", [{"sha": ""}, {"sha": good}])
    h.serve_statuses(good, [_status(CTX_PY, "failure", REAL_SEVEN_FAILED_ONE_NAMED),
                            _status(CTX_NODE, "success", REAL_SUCCESS)])
    proc = h.run()
    assert proc.returncode == RC_TRIGGERED, proc.stdout
    assert "(walked 1)" in proc.stdout, "the sha-less entry was counted as walked"


def test_an_EMPTY_stderr_still_produces_a_readable_diagnostic(h):
    """The `(no stderr)` fallback: forcing the conditional the other way makes
    `err[0]` raise IndexError on an empty list, turning a clean COULD NOT
    MEASURE into an UNEXPECTED IndexError — a bug report about the watcher
    instead of a report about `gh`."""
    write_exec(h.gh, "exit 7\n")
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "exited 7" in proc.stdout
    assert "(no stderr)" in proc.stdout, proc.stdout
    assert "UNEXPECTED" not in proc.stdout, "the fallback raised instead of reporting"


def test_a_failing_triggers_stderr_reaches_the_operator(h):
    """`systemctl`'s own words are the only clue why the start failed — a unit
    that is not loaded says so. Dropping them survived the sweep."""
    write_exec(h.trigger, 'echo "Failed to start: Unit not found." >&2\nexit 5\n')
    _red_commit(h, "f9" + "0" * 38)
    proc = h.run()
    assert proc.returncode == RC_UNMEASURED, proc.stdout
    assert "Unit not found" in proc.stdout, proc.stdout


def test_the_flake_screen_refuses_a_row_that_NAMES_NOTHING_with_a_zero_count():
    """The `not names` guard, made reachable. Every other fixture reaches it with
    a count that DISAGREES anyway, so the next guard decides and this one never
    runs — the "isolate the mutation" trap in its natural habitat. Here the
    count agrees with the empty name set, which is the one shape where removing
    this guard flips the answer to SUPPRESS.
    """
    mod = _load()
    assert mod.parse_failing_names("FAILED: pytests — failed=0 FAILING: |") == []
    assert mod.screen_all_known_flakes(
        ["FAILED: pytests — failed=0 FAILING: |"]) is False
