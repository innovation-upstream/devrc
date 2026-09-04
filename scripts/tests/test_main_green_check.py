"""`scripts/main-green-check.sh` — the deadman that reports a red `main`.

WHY THIS FILE EXISTS. Branch protection on this repo is OFF by a live operator
decision, and on 2026-09-03 two direct-to-main commits (`a720d30d`, `a451abc0`)
each red-ed `main` for hours. BOTH times the only detector was a human running
the gate by hand, incidentally. The script under test is that missing detector.

🔴 WHAT THESE TESTS ARE, HONESTLY LABELLED. None of them is regression coverage
for a bug in the script — the script is new, so nothing here was ever RED at a
base that contained it. They are:

  * BEHAVIOURAL CONTRACTS on each arm (green / red / flake / unmeasured /
    memoized), each of which would be green-and-meaningless without...
  * ...the two CONTROLS below, which is why they come first in the file. A
    deadman's whole value is that a zero means something, and a harness that
    cannot go red produces a reassuring zero for a broken world.
  * one STATIC guard (`test_the_script_never_touches_the_operators_checkout`)
    pinning a RELATIONSHIP the behavioural tests structurally cannot see: they
    run against a throwaway remote, so none of them would notice this script
    growing a `git -C $DEVRC checkout`.

🔴 THE SEAM THESE TESTS DRIVE IS NOT THE PRODUCTION PATH, and that is the danger.
Every test below sets MAIN_GREEN_GATE_CMD, so none of them ever invokes `nix
build`. `test_the_production_path_builds_the_two_sandbox_derivations` pins the
real invocation textually so the seam cannot drift away from what production
does while every behavioural test stays green.
"""
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "main-green-check.sh"

RC_GREEN, RC_RED, RC_UNMEASURED, RC_BLIND, RC_USAGE = 0, 10, 11, 12, 2


def _code_only(src):
    """Executable lines only — comments stripped.

    A static guard that reads prose is walkable by rewording AND breakable by
    documenting the very hazard it forbids. Both happened here on the first
    draft, which is why this helper exists rather than a whole-file grep.
    """
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True, timeout=60)


@pytest.fixture
def world(tmp_path):
    """A throwaway origin + a place for the script's private cache.

    Deliberately NOT this repo: these tests must never be able to reach the
    operator's checkout, and building the fixture out of a real remote would
    make that reach possible by accident.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "--quiet", "--initial-branch=main", ".", cwd=origin)
    _git("config", "user.email", "t@example.invalid", cwd=origin)
    _git("config", "user.name", "T", cwd=origin)
    (origin / "file.txt").write_text("one\n")
    _git("add", "file.txt", cwd=origin)
    _git("commit", "--quiet", "-m", "first commit", cwd=origin)

    cache = tmp_path / "cache"
    calls = tmp_path / "calls"          # the gate stub appends one line per call
    return {"origin": origin, "cache": cache, "calls": calls, "tmp": tmp_path}


def _stub(world, script_body):
    """Write a gate stub. It is handed (checkout, tier) exactly as production is."""
    p = world["tmp"] / "gate-stub.sh"
    p.write_text("#!/usr/bin/env bash\n"
                 'echo "$2" >> "$CALLS"\n' + script_body)
    p.chmod(0o755)
    return p


def _run(world, stub, *args, extra_env=None):
    env = dict(os.environ)
    env.update({
        "MAIN_GREEN_CACHE": str(world["cache"]),
        "MAIN_GREEN_REMOTE": str(world["origin"]),
        "MAIN_GREEN_GATE_CMD": str(stub),
        "CALLS": str(world["calls"]),
    })
    env.update(extra_env or {})
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=300)


def _calls(world):
    if not world["calls"].exists():
        return []
    return world["calls"].read_text().split()


def _advance_main(world, msg="second commit"):
    (world["origin"] / "file.txt").write_text(msg + "\n")
    _git("add", "file.txt", cwd=world["origin"])
    _git("commit", "--quiet", "-m", msg, cwd=world["origin"])


# ── the two controls ─────────────────────────────────────────────────────────

def test_NEGATIVE_CONTROL_the_harness_can_go_red(world):
    """🔴 Feed it a gate that MUST fail. If this reports success, every other
    test in this file is measuring nothing and the deadman is decorative."""
    stub = _stub(world, 'echo "RESULT: FAIL (exit=1)"; exit 1\n')
    r = _run(world, stub)
    assert r.returncode == RC_RED, r.stdout + r.stderr
    assert "RED, REPRODUCED" in r.stdout


def test_POSITIVE_CONTROL_the_harness_can_observe_a_pass(world):
    """The mirror: a gate that MUST pass has to reach a green verdict, or the
    red above would be indistinguishable from a script that always fails."""
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    r = _run(world, stub)
    assert r.returncode == RC_GREEN, r.stdout + r.stderr
    assert "GREEN" in r.stdout
    assert _calls(world) == ["pytests", "nodetests"], "both tiers must run"


# ── the arms ─────────────────────────────────────────────────────────────────

def test_a_red_is_retried_EXACTLY_once_and_a_clearing_red_is_a_FLAKE_not_a_pass(world):
    """A red that clears on retry is recorded as `flake`, never as `green`.

    The distinction is the point: a flake silently relabelled a pass is how a
    real break gets absorbed into the noise. It must also still exit 0, because
    toasting on it would make this the permanently-red gate RULES.md forbids.
    """
    stub = _stub(world, textwrap.dedent('''
        n=$(cat "$CALLS" | wc -l)
        if [ "$n" -le 2 ]; then echo "RESULT: FAIL (exit=1)"; exit 1; fi
        echo "RESULT: PASS (exit=0)"; exit 0
    '''))
    r = _run(world, stub)
    assert r.returncode == RC_GREEN, r.stdout + r.stderr
    assert "FLAKE ABSORBED" in r.stdout
    assert "not hidden" in r.stdout.lower()
    state = (world["cache"] / "state").read_text()
    assert "verdict flake" in state, state


def test_the_retry_does_NOT_loop_until_green(world):
    """🔴 The retry runs ONCE. A deadman that retried until green would report a
    permanently-broken main as clean, which inverts its entire purpose.

    Measured mechanically: with a gate that fails forever, the stub must be
    invoked exactly 4 times (2 tiers x 2 attempts) and no more.
    """
    stub = _stub(world, 'echo "RESULT: FAIL (exit=1)"; exit 1\n')
    r = _run(world, stub)
    assert r.returncode == RC_RED
    assert len(_calls(world)) == 4, _calls(world)


def test_a_green_verdict_is_MEMOIZED_on_the_sha_and_reruns_nothing(world):
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    assert _run(world, stub).returncode == RC_GREEN
    first = len(_calls(world))
    r = _run(world, stub)
    assert r.returncode == RC_GREEN
    assert "already verified" in r.stdout
    assert len(_calls(world)) == first, "a memoized run must invoke the gate ZERO more times"


def test_the_memo_is_INVALIDATED_when_main_moves(world):
    """The memo is keyed on the sha, so a new commit must be re-checked — this is
    the whole mechanism that makes a direct-to-main push get looked at."""
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    _run(world, stub)
    before = len(_calls(world))
    _advance_main(world)
    r = _run(world, stub)
    assert r.returncode == RC_GREEN
    assert len(_calls(world)) == before + 2, "a moved main must re-run both tiers"


def test_force_overrides_the_memo(world):
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    _run(world, stub)
    before = len(_calls(world))
    r = _run(world, stub, "--force")
    assert r.returncode == RC_GREEN
    assert len(_calls(world)) == before + 2


def test_a_RED_main_that_has_not_moved_still_reports_RED(world):
    """🔴 The memo must not silence an unfixed break. A red that is remembered is
    still red — reporting `already verified, nothing to do` here would mean the
    deadman goes quiet precisely while main stays broken."""
    stub = _stub(world, 'echo "RESULT: FAIL (exit=1)"; exit 1\n')
    assert _run(world, stub).returncode == RC_RED
    before = len(_calls(world))
    r = _run(world, stub)
    assert r.returncode == RC_RED, r.stdout
    assert "has not moved" in r.stdout
    assert len(_calls(world)) == before, "no need to re-run to know it is still red"


# ── could-not-measure is its own answer ──────────────────────────────────────

def test_a_gate_that_exits_0_with_NO_verdict_is_UNMEASURED_not_a_pass(world):
    """A truncated or silently-killed run prints no `RESULT:` line. Reading that
    as green is the exact failure this repo has hit before (four agents reported
    `exit 0` over content saying `RESULT: FAIL`)."""
    stub = _stub(world, 'echo "some output but no verdict line"; exit 0\n')
    r = _run(world, stub)
    assert r.returncode == RC_UNMEASURED, r.stdout + r.stderr
    assert "COULD NOT MEASURE" in r.stdout
    assert "not" in r.stdout.lower() and "green" in r.stdout.lower()


def test_status_and_content_DISAGREEING_is_UNMEASURED_not_resolved_in_favour_of_the_pass(world):
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 1\n')
    r = _run(world, stub)
    assert r.returncode == RC_UNMEASURED, r.stdout + r.stderr
    assert "DISAGREE" in r.stdout


def test_an_unreachable_remote_is_UNMEASURED_and_says_it_is_not_a_pass(world):
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    r = _run(world, stub, extra_env={"MAIN_GREEN_REMOTE": str(world["tmp"] / "nope")})
    assert r.returncode == RC_UNMEASURED, r.stdout + r.stderr
    assert _calls(world) == [], "nothing may be gated when the tree could not be fetched"


def test_repeated_could_not_measure_ESCALATES_to_BLIND(world):
    """🔴 The hole this closes: making rc 11 a systemd success stops a network
    blip from toasting, and on its own lets the deadman go blind FOREVER in
    silence — the same shape as the bug it exists to catch.

    Threshold lowered to 2 so the test is fast; the DEFAULT is pinned separately
    below, because a test that only ever exercises an overridden value would not
    notice the shipped default becoming 0 or 9999.
    """
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    env = {"MAIN_GREEN_REMOTE": str(world["tmp"] / "gone"),
           "MAIN_GREEN_BLIND_ESCALATE": "2"}
    first = _run(world, stub, extra_env=env)
    assert first.returncode == RC_UNMEASURED, first.stdout
    second = _run(world, stub, extra_env=env)
    assert second.returncode == RC_BLIND, second.stdout
    assert "BLIND" in second.stdout
    assert "meant NOTHING" in second.stdout


def test_a_measured_run_RESETS_the_blind_streak(world):
    """One good look clears the ladder — otherwise a single bad week would leave
    it permanently escalated, which is the red-gate-nobody-reads failure."""
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    env = {"MAIN_GREEN_REMOTE": str(world["tmp"] / "gone"),
           "MAIN_GREEN_BLIND_ESCALATE": "2"}
    assert _run(world, stub, extra_env=env).returncode == RC_UNMEASURED
    assert (world["cache"] / "blind-streak").read_text().strip() == "1"
    assert _run(world, stub).returncode == RC_GREEN          # a real measurement
    assert (world["cache"] / "blind-streak").read_text().strip() == "0"


def test_an_unmeasured_run_does_NOT_clobber_the_last_real_verdict(world):
    """'I could not look today' and 'main was red when I last looked' are
    different facts, and the second must survive the first — which is why the
    streak lives in its own file rather than in the state record."""
    red = _stub(world, 'echo "RESULT: FAIL (exit=1)"; exit 1\n')
    assert _run(world, red).returncode == RC_RED
    _run(world, red, extra_env={"MAIN_GREEN_REMOTE": str(world["tmp"] / "gone")})
    assert "verdict red" in (world["cache"] / "state").read_text()


def test_the_shipped_blind_threshold_is_a_sane_default():
    """Pinned because every behavioural test above OVERRIDES it. A default of 0
    would escalate on the first blip; a huge one would never escalate at all,
    which is the exact 'never fires, reads as clean forever' failure."""
    src = _code_only(SCRIPT.read_text())
    m = re.search(r'BLIND_ESCALATE="\$\{MAIN_GREEN_BLIND_ESCALATE:-(\d+)\}"', src)
    assert m, "the blind-ladder default moved or lost its env override"
    assert 2 <= int(m.group(1)) <= 24, m.group(1)


def test_the_three_outcomes_have_DISTINCT_exit_codes():
    """green / red / unmeasured must never collapse — a deadman whose 'could not
    look' is spelled the same as 'clean' is worse than no deadman."""
    assert len({RC_GREEN, RC_RED, RC_UNMEASURED, RC_BLIND, RC_USAGE}) == 5


# ── the seam must not drift away from production ─────────────────────────────

def test_the_production_path_builds_the_two_sandbox_derivations():
    """🔴 Every behavioural test above sets MAIN_GREEN_GATE_CMD, so NONE of them
    executes the real command. Without this, the seam could be rewired to
    something that does not exist and the suite would stay fully green.

    Pinned textually, and deliberately including `-L` and the ONE-AT-A-TIME
    shape: devrc/CLAUDE.md records that a COMBINED `nix build` of both
    derivations produced false failures a sequential pair did not.
    """
    src = _code_only(SCRIPT.read_text())
    assert 'nix build "$CLONE#checks.x86_64-linux.$tier"' in src, (
        "the production gate invocation moved; this seam no longer mirrors it")
    tiers = re.search(r'for tier in ([a-z ]+); do', src)
    assert tiers and tiers.group(1).split() == ["pytests", "nodetests"], (
        "the tier list moved: %r" % (tiers.group(1) if tiers else None))
    assert src.count('nix build "$CLONE') == 1, (
        "more than one nix build invocation — the one-at-a-time property is "
        "what stops the documented store-contention false failures")


def test_the_script_never_touches_the_operators_checkout():
    """🔴 A STATIC guard pinning a RELATIONSHIP no behavioural test can see.

    Every test in this file points the script at a throwaway remote, so a
    `git -C "$SRC_REPO" checkout` added tomorrow would leave all of them green
    while the deadman started mutating the very checkout it is supposed to only
    observe. `git worktree add` counts too: it writes the COMMON git dir, so it
    is repo-global even when it looks local.

    The one allowed read against SRC_REPO is `remote get-url`, enumerated here
    rather than pattern-matched, so a new verb is a failure by default.

    🔴 SCANS CODE, NOT PROSE. The first version of this guard grepped the whole
    file for `worktree add` and went red on the COMMENT explaining why
    `worktree add` is forbidden — a guard walkable by wording rather than one
    pinning a state. Comment lines are stripped first, so the guard is about
    what the script DOES.
    """
    src = _code_only(SCRIPT.read_text())
    src_repo_calls = re.findall(r'git -C "\$SRC_REPO" ([a-z-]+(?: [a-z-]+)?)', src)
    assert src_repo_calls, "expected at least the one read; did the variable get renamed?"
    for verb in src_repo_calls:
        assert verb == "remote get-url", (
            "scripts/main-green-check.sh runs `git -C $SRC_REPO %s` — the "
            "operator's checkout is READ-ONLY to this script, and the only "
            "permitted read is `remote get-url`." % verb)
    assert "worktree add" not in src, (
        "`git worktree add` writes the COMMON git dir (refs, config, registry), "
        "so it is a repo-GLOBAL mutation of a checkout other sessions share. "
        "This script has its own clone precisely to avoid that.")


def test_it_derives_the_mainline_instead_of_hardcoding_main():
    """A hardcoded `main` would silently check a branch that does not exist in a
    `trunk` repo and report a reassuring nothing."""
    src = SCRIPT.read_text()
    assert "symbolic-ref" in src and "refs/remotes/origin/HEAD" in src


def test_it_single_flights():
    """A run can outlast the timer interval; two concurrent runs would fight over
    the clone AND contend in the nix store, which is a documented source of false
    failures in this repo."""
    src = SCRIPT.read_text()
    assert "flock" in src


def test_the_script_is_executable_and_parses():
    assert os.access(SCRIPT, os.X_OK), "must be chmod +x or the systemd unit cannot run it"
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_an_unknown_argument_is_a_usage_error_not_a_silent_pass(world):
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    r = _run(world, stub, "--wat")
    assert r.returncode == RC_USAGE, r.stdout + r.stderr
    assert _calls(world) == []
