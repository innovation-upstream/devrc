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
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

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
    """Write a gate stub. It is handed (checkout, tier) exactly as production is.

    🔴 THE SHEBANG IS NOT OURS TO WRITE — `testlib.mockbin.write_exec` owns it.
    The first draft wrote `#!/usr/bin/env bash` by hand, which works on the dev
    host and does NOT exist inside the nix build sandbox: every stub exec came
    back `rc=126` (found, not executable), the script correctly reported RED, and
    9 tests in this file failed in the sandbox tier while all 22 passed on the
    dev host. `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` is the
    repo's existing guard for exactly that and caught it as the 10th failure.
    Bodies must be POSIX sh — write_exec's shebang is /bin/sh, not bash.
    """
    return write_exec(world["tmp"] / "gate-stub.sh",
                      'echo "$2" >> "$CALLS"\n' + script_body)


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


def test_a_gate_that_FAILS_with_no_verdict_is_UNMEASURED_not_a_RED_accusation(world):
    """🔴 RED at 3033a22f. A gate that exits NON-ZERO having printed no `RESULT:`
    line is a BROKEN GATE, not a broken `main` — a `nix` that cannot start, an
    OOM, a missing binary. The header and `tier_verdict`'s own comment both said
    so; the code classified it `red`.

    Why it matters more here than anywhere else: the red arm names the commit's
    AUTHOR and fires a do-not-disturb-defeating toast saying "main is broken
    RIGHT NOW", with an empty "Failing tests:" list. Measured at 3033a22f with a
    stub that exits 1 silently: rc 10, `author: Innocent Author`.

    A genuinely failing suite cannot reach here — it exits non-zero AND prints
    `RESULT: FAIL`, which the arm above catches.
    """
    stub = _stub(world, 'exit 1\n')
    r = _run(world, stub)
    assert r.returncode == RC_UNMEASURED, r.stdout + r.stderr
    assert "RED, REPRODUCED" not in r.stdout, (
        "a broken gate must not be reported as a broken main:\n" + r.stdout)


def test_a_DISAGREEING_tier_cannot_ERASE_a_RED_from_another_tier(world):
    """🔴 RED at 3033a22f, and it made the verdict ORDER-DEPENDENT.

    `noverdict` was guarded so it could only downgrade a green; `disagree` was
    not, so it overwrote `red` with `unmeasured`. Measured at 3033a22f: pytests
    RESULT: FAIL + nodetests status/content disagreement -> rc 11, a systemd
    SUCCESS, no toast — a genuinely red `main` reported as nothing at all, which
    is the exact outcome this whole script exists to prevent.
    """
    stub = _stub(world, textwrap.dedent('''
        if [ "$2" = pytests ]; then echo "RESULT: FAIL (exit=1)"; exit 1; fi
        echo "RESULT: PASS (exit=0)"; exit 1
    '''))
    r = _run(world, stub)
    assert r.returncode == RC_RED, (
        "a red tier was erased by a disagreeing one:\n" + r.stdout)


def test_a_CACHED_gate_run_is_GREEN_not_unmeasured(world):
    """🔴 RED at 3033a22f — and it is the MODAL case, not an edge one.

    `nix build -L` streams a log only while a build RUNS. An already-realised
    derivation is not rebuilt, so nix prints NOTHING and exits 0. Since
    `CLAUDE.md` tells every merger to build these same two derivations on the
    merged tree before merging, the tip this deadman checks is USUALLY already
    realised — so the normal path returned `noverdict` -> rc 11, and six of them
    ladder to a BLIND toast about a perfectly green `main`. `--force` was broken
    by construction for the same reason.

    A realised check derivation IS the verdict: these derivations RUN the suite,
    so nix exiting 0 means it built, which means the tests passed. Empty output
    with rc 0 is therefore GREEN. (Non-empty output with rc 0 and no `RESULT:`
    line stays UNMEASURED — that is a truncated run, a different fact, pinned by
    the test above this one.)
    """
    stub = _stub(world, 'exit 0\n')          # silent + rc 0 == the cached shape
    r = _run(world, stub)
    assert r.returncode == RC_GREEN, r.stdout + r.stderr
    assert "GREEN" in r.stdout


def test_a_CORRUPT_streak_file_cannot_make_the_guards_FALL_THROUGH(world):
    """🔴 RED at 3033a22f, and the blast radius was much wider than the ladder.

    `read_streak` cat'd an unvalidated file into `$(( ... + 1 ))`. A non-integer
    makes that a bash ARITHMETIC SYNTAX ERROR, which aborts `unmeasured_exit`
    WITHOUT exiting — and every one of its call sites is inside an `if ... fi`,
    so the script CONTINUES past a guard that just fired. Measured at 3033a22f
    with a streak file of `1 2`: a failed clone was announced and then ignored,
    the run gated an EMPTY sha, and it exited 10 reporting
    `RED, REPRODUCED — origin/main  failed BOTH attempts` with a blank author.

    So this is not a ladder nit: a corrupt counter turned a total failure to
    even fetch into a do-not-disturb-defeating accusation about `main`.
    """
    world["cache"].mkdir(parents=True, exist_ok=True)
    (world["cache"] / "blind-streak").write_text("1 2")
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    r = _run(world, stub, extra_env={"MAIN_GREEN_REMOTE": str(world["tmp"] / "gone")})
    assert r.returncode == RC_UNMEASURED, (
        "a corrupt streak file let the run continue past a failed clone:\n"
        + r.stdout)
    assert "RED, REPRODUCED" not in r.stdout


# (raw streak file content, the integer the sanitiser MUST read out of it)
# 🔴 THE COUNTER ALONE IS NOT A SUFFICIENT DISCRIMINATOR, AND AN EARLIER COMMENT
# HERE CLAIMED IT WAS. It asserted "every expected value is distinct" — false:
# `1` appears three times, and `1` is EXACTLY what the broken path produces,
# because an arithmetic abort inside `$(read_streak)` yields an empty string and
# bash evaluates `$(( + 1 ))` as unary plus == 1 (measured). So deleting the
# `case` sanitiser SURVIVED a fully green suite even with the counter asserted.
# A corrupt value cannot expect anything BUT 1 (corrupt -> 0 -> 0+1), so the
# second discriminator is STDERR: the broken path emits bash's own
# `arithmetic syntax error` / `value too great for base`, the fixed path is
# silent. That is the mechanism itself, not a proxy for it.
CORRUPT_STREAKS = [
    ("1 2", 1),                      # internal space -> corrupt -> 0, so 0+1
    ("08", 9),                       # ALL DIGITS, but octal to bash -> must be 8
    ("09", 10),                      # the other octal-invalid digit
    ("007", 8),                      # leading zeros, still base ten -> 7
    ("  5", 1),                      # leading space -> corrupt -> 0
    ("5\n\n", 6),                   # trailing newlines are not corruption
    ("9" * 22, 1),                   # overflows the arithmetic -> corrupt -> 0
]


@pytest.mark.parametrize("corrupt,expected", CORRUPT_STREAKS)
def test_a_CORRUPT_streak_cannot_make_the_guards_FALL_THROUGH(world, corrupt, expected):
    """🔴 The round-1 sanitiser was NARROWER THAN THE HAZARD it described.

    `case $s in ''|*[!0-9]*) s=0` rejects non-digits — but `08` and `09` ARE all
    digits, and bash reads a leading zero as OCTAL: `08: value too great for
    base`. That is the same arithmetic abort, so `unmeasured_exit` again died
    without exiting and the run continued past a guard that had just fired.

    🔴 REPRODUCED at 31864127, and it is the worst outcome this file can produce:
    with the clone AND the fetch both failed and the sha EMPTY, it printed
    `✅ GREEN — origin/main  passed both sandbox tiers` and exited 0. A deadman
    reporting a green it never measured is strictly worse than no deadman.

    The escalation threshold is pinned high so the RUN's exit code stays 11 for
    every row; what is asserted is the COUNTER, which is the only thing that
    separates "sanitised correctly" from "the arithmetic blew up and the guard
    fell through".
    """
    world["cache"].mkdir(parents=True, exist_ok=True)
    (world["cache"] / "blind-streak").write_text(corrupt)
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    r = _run(world, stub, extra_env={"MAIN_GREEN_REMOTE": str(world["tmp"] / "gone"),
                                     "MAIN_GREEN_BLIND_ESCALATE": "99"})
    assert r.returncode == RC_UNMEASURED, (
        "streak %r let the run continue past a failed clone:\n%s"
        % (corrupt, r.stdout))
    assert "GREEN" not in r.stdout, (
        "streak %r produced a GREEN for a tree that was never fetched:\n%s"
        % (corrupt, r.stdout))
    assert not re.search(r"arithmetic syntax error|value too great for base",
                         r.stderr), (
        "streak %r reached bash's arithmetic evaluator uncoerced — the sanitiser "
        "did not run or did not cover this shape:\n%s" % (corrupt, r.stderr))
    got = (world["cache"] / "blind-streak").read_text().strip()
    assert got == str(expected), (
        "streak %r was read as %r, expected %r — the sanitiser did not coerce "
        "it to base ten, so the arithmetic aborted and the guard fell through"
        % (corrupt, got, str(expected)))



def test_repeated_CONTENTION_does_not_report_GREEN_forever(world):
    """🔴 Round 1 closed the BROKEN-flock door and left the CONTENTION one open,
    while its own comment condemned the whole class.

    A held lock — you started a run by hand in a tmux pane and it is still going,
    or wedged — makes every 4h fire print "another run holds the lock" and exit
    0: silent, a systemd success, and touching no ladder. The deadman is blind
    indefinitely and nothing says so. Contention must therefore ladder too.
    """
    import fcntl
    world["cache"].mkdir(parents=True, exist_ok=True)
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    env = {"MAIN_GREEN_CONTENTION_ESCALATE": "2"}
    with open(world["cache"] / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        first = _run(world, stub, extra_env=env)
        second = _run(world, stub, extra_env=env)
    assert first.returncode == RC_GREEN, first.stdout
    # 🔴 EXACT, NOT `in (...)`. Claim 9 removed a loose `rc in (11, 12)` and this
    # test reintroduced the same shape in the same commit: with it, a change
    # making the SECOND contention fire the loud BLIND toast immediately —
    # instead of laddering quietly through 11, which is the whole point of
    # `SuccessExitStatus = 11` — was invisible.
    assert second.returncode == RC_UNMEASURED, (
        "expected the quiet ladder rung (11), got %d — contention must not jump "
        "straight to the DND-defeating toast:\n%s"
        % (second.returncode, second.stdout))
    assert (world["cache"] / "contention-streak").read_text().strip() == "2"
    assert _calls(world) == [], "sanity: the gate never ran"


def test_acquiring_the_lock_CLEARS_the_contention_counter(world):
    """🔴 UNCOVERED until round 3: two mutants (delete the reset, or set it to 1)
    both survived a fully green suite.

    Without the reset the counter is cumulative over the process's whole life:
    three overlaps MONTHS apart, each separated by dozens of clean measured runs,
    reach the threshold and fire a BLIND toast about a deadman that has been
    measuring `main` correctly the entire time. That is the permanently-red gate
    this file's header argues against, arrived at by bookkeeping.
    """
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    world["cache"].mkdir(parents=True, exist_ok=True)
    (world["cache"] / "contention-streak").write_text("2")
    r = _run(world, stub)
    assert r.returncode == RC_GREEN, r.stdout + r.stderr
    leftover = world["cache"] / "contention-streak"
    assert not leftover.exists() or leftover.read_text().strip() in ("", "0"), (
        "a run that ACQUIRED the lock left the contention counter standing at "
        "%r — it accumulates across unrelated outages and eventually toasts"
        % leftover.read_text())


def test_help_prints_the_header_and_REFUSES_if_it_cannot_find_the_end(world):
    """🔴 The computed range fixed truncation and introduced SILENCE-WITH-RC-0.

    Reword the `set -uo pipefail` sentinel and `grep` matches nothing, `_hdr` is
    empty, `$(( _hdr - 1 ))` is -1, and `sed -n "2,-1p"` errors to stderr while
    --help prints ZERO lines and exits 0 — measured. The literal it replaced
    merely truncated; this deleted the help and reported success, which is the
    "a zero that means nothing" shape the file's own header condemns.

    Also pins that --help exists at all: a mutant hardcoding `2,60p` — the exact
    regression the computed range exists to prevent — survived a green suite.
    """
    stub = _stub(world, 'exit 0\n')
    r = _run(world, stub, "--help")
    assert r.returncode == RC_GREEN, r.stderr
    assert len(r.stdout.splitlines()) > 80, (
        "--help rendered %d lines; the header is longer than that, so the range "
        "is truncating" % len(r.stdout.splitlines()))
    for needed in ("OPTIONS", "--force", "--status", "BLIND", "COULD NOT MEASURE"):
        assert needed in r.stdout, "--help no longer reaches %r" % needed
    assert "set -uo pipefail" not in r.stdout, "the range leaked past the header"

    broken = world["tmp"] / "broken.sh"
    broken.write_text(SCRIPT.read_text().replace("set -uo pipefail",
                                                 "set -o pipefail; set -u", 1))
    b = subprocess.run(["bash", str(broken), "--help"], capture_output=True,
                       text=True, timeout=60, env={**os.environ,
                       "MAIN_GREEN_CACHE": str(world["tmp"] / "hc")})
    assert b.returncode != RC_GREEN, (
        "with the sentinel moved, --help printed %d lines and still exited 0"
        % len(b.stdout.splitlines()))


def test_status_works_while_a_run_holds_the_lock(world):
    """The OPTIONS block claims `--status` does not block on a run in flight, and
    a claim in the header is a claim like any other. At round 2 it was FALSE —
    the lock was taken before the STATUS_ONLY branch — so the moment you most
    want the last verdict (during a 20-minute run) was the one moment you could
    not read it. Fixed by ordering, pinned here."""
    import fcntl
    world["cache"].mkdir(parents=True, exist_ok=True)
    (world["cache"] / "state").write_text("sha deadbeef\nverdict green\n")
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    with open(world["cache"] / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _run(world, stub, "--status")
    assert r.returncode == RC_GREEN, r.stdout + r.stderr
    assert "deadbeef" in r.stdout, r.stdout
    assert _calls(world) == []


def test_the_unit_does_NOT_carry_NIX_CONFIG_in_its_Environment(world):
    """🔴 The round-1 guard for this read the WRONG FILE.

    Its message said "it must not move back into the unit's `Environment=`" and
    its body asserted a string in `main-green-check.sh` — it never opened
    `nix/home.nix`. Both facts could hold at once: the flag on the command line
    AND `NIX_CONFIG=experimental-features = nix-command flakes` re-added to the
    unit. The suite would stay fully green while systemd whitespace-split it,
    nix hard-errored on every invocation, and the deadman laddered to BLIND.

    A docstring naming a RELATIONSHIP whose body inspects ONE SIDE reads as
    coverage while providing none, which stops anyone looking.
    """
    home_nix = (ROOT / "nix" / "home.nix").read_text()
    block = re.search(r"systemd\.user\.services\.main-green-check = \{.*?\n  \};",
                      home_nix, re.S)
    assert block, "the main-green-check unit is gone from nix/home.nix"
    code = _code_only(block.group(0))
    assert "NIX_CONFIG" not in code, (
        "NIX_CONFIG is back in the unit. systemd WHITESPACE-SPLITS Environment=, "
        "so `experimental-features = nix-command flakes` becomes the bare word "
        "`experimental-features` and nix hard-errors on every invocation — "
        "measured with systemd-analyze verify. The flag belongs on the command "
        "line in main-green-check.sh.")


def test_a_BROKEN_flock_is_not_reported_as_GREEN(world):
    """🔴 RED at 3033a22f. `flock -n` returning non-zero for ANY reason — ENOLCK
    on a filesystem without working locks, a missing binary, EBADF — was
    indistinguishable from contention, and the answer was `exit 0` GREEN.

    That is silent, permanent, a systemd success, and it never touches the blind
    ladder — precisely the "goes blind in silence" shape the ladder exists to
    close, reached by a different door. The fix is a POSITIVE CONTROL: if we
    cannot take a lock NOBODY holds, we have measured nothing.
    """
    fake_bin = world["tmp"] / "fakebin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    write_exec(fake_bin / "flock", "exit 1\n")      # always fails, never contention
    stub = _stub(world, 'echo "RESULT: FAIL (exit=1)"; exit 1\n')
    r = _run(world, stub, extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert r.returncode != RC_GREEN, (
        "a broken flock reported GREEN and never ran the gate:\n" + r.stdout)
    assert _calls(world) == [], "sanity: the gate must not have run"


def test_single_flight_is_BEHAVIOURAL_not_just_the_word_flock(world):
    """🔴 The guard this REPLACED asserted only `"flock" in src`, and a mutant
    that kept the word while deleting the branch (`if false`) SURVIVED a fully
    green suite. A guard satisfiable by spelling is not a guard.

    This holds the real lock and asserts the observable consequence: the gate is
    not invoked.
    """
    import fcntl
    world["cache"].mkdir(parents=True, exist_ok=True)
    lock = world["cache"] / "lock"
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _run(world, stub)
    assert r.returncode == RC_GREEN, r.stdout + r.stderr
    assert _calls(world) == [], (
        "the gate ran while another process held the lock — single-flight is "
        "not in force:\n" + r.stdout)


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


def test_the_shipped_contention_threshold_is_a_sane_default():
    """The twin of the blind-threshold pin, and round 2 added the second ladder
    without extending it: mutating the default to 99999 SURVIVED a green suite
    while, in production, the contention ladder would never fire — the exact
    "never fires, reads as clean forever" failure the other pin exists for."""
    src = _code_only(SCRIPT.read_text())
    m = re.search(r'CONTENTION_ESCALATE="\$\{MAIN_GREEN_CONTENTION_ESCALATE:-(\d+)\}"', src)
    assert m, "the contention ladder default moved or lost its env override"
    n = int(m.group(1))
    assert 2 <= n <= 12, n
    b = re.search(r'BLIND_ESCALATE="\$\{MAIN_GREEN_BLIND_ESCALATE:-(\d+)\}"', src)
    assert b and n <= int(b.group(1)), (
        "contention should escalate no later than blindness: %s vs %s"
        % (n, b and b.group(1)))


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
    assert 'build "$CLONE#checks.x86_64-linux.$tier"' in src, (
        "the production gate invocation moved; this seam no longer mirrors it")
    for flag in ("-L", "--no-link", "--no-warn-dirty"):
        assert flag in src, (
            "%s left the production nix invocation. `--no-link` keeps $CLONE "
            "clean (a `result` symlink would dirty it) and `--no-warn-dirty` "
            "keeps a cached build's output EMPTY — a dirty tree emits a warning, "
            "which makes the cached case non-empty and therefore UNMEASURED, "
            "laddering to BLIND about a green main." % flag)
    assert '--extra-experimental-features "nix-command flakes"' in src, (
        "the flake features flag left the COMMAND LINE. It must not move back "
        "into the unit's Environment=, which systemd whitespace-splits — "
        "measured to set NIX_CONFIG to the bare word `experimental-features` "
        "and make every nix invocation hard-error.")
    tiers = re.search(r'for tier in ([a-z ]+); do', src)
    assert tiers and tiers.group(1).split() == ["pytests", "nodetests"], (
        "the tier list moved: %r" % (tiers.group(1) if tiers else None))
    assert src.count('build "$CLONE') == 1, (
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
    # 🔴 AN ENUMERATED ALLOWLIST, NOT A PATTERN — a NEW use is a failure by
    # DEFAULT. Round 1 answered "the guard is walkable" by adding the two
    # spellings it had been walked with (`cd "$SRC_REPO"`, `git -C
    # "$SCRIPT_DIR"`), which left `cd "${SRC_REPO}"`, `cd -- "$SRC_REPO"`,
    # `pushd "$SRC_REPO"` and unquoted `git -C $SCRIPT_DIR` all still open.
    # Chasing spellings cannot terminate; enumerating the legitimate uses can.
    ALLOWED_USES = {
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'SRC_REPO="$(dirname "$SCRIPT_DIR")"',
        'REMOTE_URL="${MAIN_GREEN_REMOTE:-$(git -C "$SRC_REPO" remote get-url origin 2>/dev/null || true)}"',
        'say "COULD NOT MEASURE — no \'origin\' remote on $SRC_REPO, so there is no"',
    }
    uses = [ln.strip() for ln in src.splitlines()
            if "SRC_REPO" in ln or "SCRIPT_DIR" in ln]
    unlisted = [u for u in uses if u not in ALLOWED_USES]
    assert not unlisted, (
        "new use(s) of the operator's checkout path — every one is a chance to "
        "write to a tree this script must only observe. Add it here ONLY after "
        "confirming it is read-only:\n  " + "\n  ".join(unlisted))
    assert "worktree add" not in src, (
        "`git worktree add` writes the COMMON git dir (refs, config, registry), "
        "so it is a repo-GLOBAL mutation of a checkout other sessions share. "
        "This script has its own clone precisely to avoid that.")


def test_it_derives_the_mainline_instead_of_hardcoding_main():
    """A hardcoded `main` would silently check a branch that does not exist in a
    `trunk` repo and report a reassuring nothing."""
    src = SCRIPT.read_text()
    assert "symbolic-ref" in src and "refs/remotes/origin/HEAD" in src



def test_the_script_is_executable_and_parses():
    # NOT for systemd's sake — ExecStart is `${pkgs.bash}/bin/bash %h/...`, so
    # the exec bit is irrelevant there. It is for a human running it directly.
    assert os.access(SCRIPT, os.X_OK), "chmod +x so it can be run by hand"
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_an_unknown_argument_is_a_usage_error_not_a_silent_pass(world):
    stub = _stub(world, 'echo "RESULT: PASS (exit=0)"; exit 0\n')
    r = _run(world, stub, "--wat")
    assert r.returncode == RC_USAGE, r.stdout + r.stderr
    assert _calls(world) == []
