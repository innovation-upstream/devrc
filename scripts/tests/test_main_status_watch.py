"""`scripts/main-status-watch.py` — the early trigger for the main-green deadman.

WHY THIS FILE EXISTS. `main-green-check.service` runs on `OnUnitActiveSec=4h`
with `TimeoutStartSec=5400`, so `main` can be red for up to ~5.5h before anything
looks. MEASURED over the 100 newest commits to `main` (2026-09-08..09-10), the
`tekton/devrc-main-*` leg posts on 100/100 pushes and an authoritative verdict
lands p50 ~19.6 min after the commit — so the signal to look sooner already
exists. The script under test converts that signal into an early start of the
deadman, and claims nothing about main itself.

🔴 WHAT THESE TESTS ARE, HONESTLY LABELLED. The script is new, so NOTHING here is
regression coverage for a defect observed at a base containing it; a test that
passes before and after is an invariant guard and is not counted as coverage.
They are:

  * two CONTROLS, first in the file, because every behavioural test below is a
    reassuring zero if the harness cannot go red or cannot see a trigger;
  * BEHAVIOURAL CONTRACTS on each arm (green / red / no-verdict / flake-screen /
    debounce / unmeasured / blind);
  * REAL-FIXTURE guards built from status descriptions this repo actually
    posted, which is the only reason the truncation hazard below is provable
    rather than imagined;
  * two STATIC guards pinning relationships the behavioural tests structurally
    cannot see — that the script never reads the roll-up endpoint, and that the
    production trigger really is `systemctl … main-green-check` with no
    `--force`.

🔴 THE SEAM THESE TESTS DRIVE IS NOT THE PRODUCTION TRIGGER. Every behavioural
test sets MAIN_STATUS_WATCH_TRIGGER, so none of them ever starts a real systemd
unit. `test_the_production_trigger_is_systemctl_start_main_green_check` pins the
real command textually so the seam cannot drift away from what production does
while every behavioural test stays green.
"""
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


def _ledger():
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("msw", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.KNOWN_FLAKES


# ══ DEBOUNCE ══════════════════════════════════════════════════════════════════

def test_the_same_red_sha_is_handed_over_exactly_once(h):
    """The timer fires far more often than the deadman's ~20-minute run. Without
    this the watcher would restart it every interval for the same evidence."""
    _red_commit(h, "d1" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    first = h.receipt.read_text()
    second = h.run()
    assert second.returncode == RC_OK, second.stdout
    assert "already handed" in second.stdout
    assert h.receipt.read_text() == first, "it re-triggered on unchanged evidence"


def test_a_NEW_red_sha_triggers_again(h):
    _red_commit(h, "e1" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    _red_commit(h, "f1" + "0" * 38)
    assert h.run().returncode == RC_TRIGGERED
    assert len(h.receipt.read_text().splitlines()) == 2


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


def test_the_total_budget_is_under_the_units_timeout():
    """Two numbers whose ORDER is the whole guarantee, in two files. If the
    budget ever exceeds TimeoutStartSec the script is back to being killed
    mid-read, and nothing else would notice."""
    src = SCRIPT.read_text(encoding="utf-8")
    budget = int(re.search(r"^TOTAL_BUDGET_S = (\d+)", src, re.M).group(1))
    declared = int(re.search(r"^UNIT_TIMEOUT_START_SEC = (\d+)", src, re.M).group(1))
    home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
    start = home_nix.index("systemd.user.services.main-status-watch")
    end = home_nix.index("systemd.user.timers.main-status-watch")
    actual = int(re.search(r"TimeoutStartSec = (\d+)", home_nix[start:end]).group(1))
    assert declared == actual, (
        f"the script believes the unit's TimeoutStartSec is {declared}, "
        f"home.nix says {actual} — the guard is reasoning about the wrong number"
    )
    assert budget < actual, f"budget {budget}s must be under TimeoutStartSec {actual}s"


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
    rewording AND breakable by documenting the very hazard it forbids."""
    out = []
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


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


def test_a_context_typo_is_not_silent():
    """The context prefix is a literal contract with `devrc-ci-push-main`. A typo
    makes every walk find nothing — so an empty walk must SAY so, never pass as
    green. This pins the message that makes that visible."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'CONTEXT_PREFIX = "tekton/devrc-main-"' in src
    assert "no authoritative" in src
