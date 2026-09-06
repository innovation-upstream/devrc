"""Behavioural tests for scripts/tmux-restore-observe.sh — the reader that turns
ONE reboot into an answer about the post-boot workspace restore race.

Everything here drives the script's `verdict` subcommand against FIXTURE capture
files. Nothing reboots, nothing touches the real tmux server, the real restore
plan, the real resurrect directory or systemd.

WHAT THIS SUITE IS FOR
----------------------
The script exists because a reboot is expensive and unrepeatable-on-demand: the
operator gets one shot, and a verdict that is wrong — or that reads as clean
when it measured nothing — spends that shot for nothing. So the load-bearing
tests are the ones about what the verdict REFUSES to say:

  1. NO REBOOT, NO VERDICT. `boot_time` identical across the pair means the two
     captures describe the same boot. That must be INCONCLUSIVE, never "no race
     observed" — the exact shape of a green that observed nothing.

  2. UNMEASURED IS NOT ZERO. If either side of the comparison could not be read,
     the run must say INCONCLUSIVE rather than compare a missing number.

  3. THE DUPLICATE ARM IS INDEPENDENT OF THE COUNT ARM. The race's signature is
     duplicated (session, window_name) pairs, and continuum replacing one window
     while creating another keeps the TOTAL unchanged. A count-only check is
     blind to exactly that, so the duplicate case is asserted with the counts
     deliberately EQUAL.

  4. FEWER WINDOWS IS A FINDING TOO. Below-layout is not the duplication race,
     but it is not clean either, and it gets its own rc so an operator is not
     told "no race" about a restore that dropped windows.

Exit codes are asserted per condition, because each one leads somewhere
different: 0 clean, 1 race evidence, 3 could-not-decide, 4 windows missing.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tmux-restore-observe.sh"

RC_CLEAN = 0
RC_RACE = 1
RC_INCONCLUSIVE = 3
RC_MISSING = 4


def _pre(tmp_path, name="pre.txt", boot="BOOT-A", layout_windows="56", extra=""):
    body = f"boot_time={boot}\ncaptured_at=T0\nplan_entries=46\n"
    if layout_windows is not None:
        body += f"layout_windows={layout_windows}\n"
    body += extra
    p = tmp_path / name
    p.write_text(body)
    return p


def _post(tmp_path, name="post.txt", boot="BOOT-B", windows="56", inventory=None):
    if inventory is None:
        inventory = [("alpha", "1", "claude"), ("alpha", "2", "shell")]
    lines = [
        f"boot_time={boot}",
        "captured_at=T1",
        "plan_entries=46",
        "unit_Result=success",
        "unit_ExecMainStatus=0",
        "tmux_server_started=fixture",
    ]
    if windows is not None:
        lines.append(f"tmux_windows={windows}")
    lines.append("--- INVENTORY (session;index;window_name) ---")
    lines += ["\t".join(row) for row in inventory]
    lines += ["--- JOURNAL ---", "(fixture)"]
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return p


def _verdict(pre, post, tmp_path):
    env = dict(os.environ, TMUX_RESTORE_OBSERVE_DIR=str(tmp_path / "obs"))
    return subprocess.run(
        ["bash", str(SCRIPT), "verdict", str(pre), str(post)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_the_script_exists_and_is_executable():
    assert SCRIPT.is_file(), SCRIPT
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_an_identical_boot_time_refuses_to_render_a_verdict(tmp_path):
    """The green that observed nothing. Both captures describe the same boot, so
    the comparison is not about a reboot at all and must not be reported as one."""
    pre = _pre(tmp_path, boot="SAME-BOOT")
    post = _post(tmp_path, boot="SAME-BOOT", windows="999")
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "INCONCLUSIVE" in r.stdout
    assert "NO REBOOT" in r.stdout
    # It must NOT reach the count comparison, even though 999 > 56 would be a race.
    assert "RACE EVIDENCE" not in r.stdout


def test_more_windows_than_the_layout_is_reported_as_race_evidence(tmp_path):
    pre = _pre(tmp_path, layout_windows="50")
    post = _post(tmp_path, windows="60")
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "RACE EVIDENCE" in r.stdout
    assert "EXCEEDS" in r.stdout


def test_a_duplicate_pair_is_race_evidence_even_when_the_counts_MATCH(tmp_path):
    """The arm a count-only check cannot see. Totals are equal on purpose."""
    inventory = [
        ("alpha", "1", "claude"),
        ("alpha", "2", "shell"),
        ("alpha", "3", "claude"),  # duplicate (session, window_name)
    ]
    pre = _pre(tmp_path, layout_windows="3")
    post = _post(tmp_path, windows="3", inventory=inventory)
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "duplicated" in r.stdout
    assert "alpha" in r.stdout and "claude" in r.stdout
    assert "NO RACE OBSERVED" not in r.stdout


def test_a_window_name_containing_a_semicolon_does_not_fake_a_duplicate(tmp_path):
    """The inventory is TAB-separated precisely so a ';' in an agent-generated
    window name cannot split a row into a false duplicate."""
    inventory = [
        ("alpha", "1", "run; then check"),
        ("alpha", "2", "run; then ship"),
    ]
    pre = _pre(tmp_path, layout_windows="2")
    post = _post(tmp_path, windows="2", inventory=inventory)
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "duplicated" not in r.stdout


def test_fewer_windows_than_the_layout_is_its_own_finding_not_a_clean_pass(tmp_path):
    pre = _pre(tmp_path, layout_windows="56")
    post = _post(tmp_path, windows="40")
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_MISSING, r.stdout + r.stderr
    assert "BELOW" in r.stdout
    assert "MISSING" in r.stdout
    assert "NO RACE OBSERVED" not in r.stdout


@pytest.mark.parametrize(
    "pre_layout,post_windows",
    [(None, "56"), ("56", None)],
    ids=["layout-unmeasured", "windows-unmeasured"],
)
def test_an_unmeasured_side_is_inconclusive_never_a_comparison(
    tmp_path, pre_layout, post_windows
):
    pre = _pre(tmp_path, layout_windows=pre_layout, extra="layout=UNMEASURED reason=x\n")
    post = _post(tmp_path, windows=post_windows)
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "INCONCLUSIVE" in r.stdout
    assert "UNMEASURED" in r.stdout


def test_a_clean_boot_says_so_AND_says_it_is_only_one_sample(tmp_path):
    """One negative sample of an unguarded timing assumption is not a closure,
    and the output has to say that or it will be read as one."""
    pre = _pre(tmp_path, layout_windows="56")
    post = _post(tmp_path, windows="56")
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "NO RACE OBSERVED" in r.stdout
    assert "ONE boot" in r.stdout
    assert "does not close the" in r.stdout


def test_post_without_a_baseline_refuses_rather_than_capturing_half_the_evidence(
    tmp_path,
):
    """A post-only capture cannot answer the question, so it must not pretend to."""
    env = dict(os.environ, TMUX_RESTORE_OBSERVE_DIR=str(tmp_path / "empty-obs"))
    r = subprocess.run(
        ["bash", str(SCRIPT), "post"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no baseline" in r.stderr


def _systemctl_verbs(text):
    """Every `systemctl` occurrence's positional verb, in source order.

    A verb is positional: the first token after the binary that is not a flag.
    Line-based on purpose — the real call site wraps across three lines with
    `\\`, and the verb sits on the first of them.

    Only INVOCATION-shaped occurrences count: the name must be followed by
    whitespace. The script's `reason=systemctl-show-returned-nothing` diagnostic
    is not a call site, and the fix for it is a precise scanner rather than a
    reworded message — rewording to dodge a scanner is how a justification stops
    describing the tree it claims to.
    """
    verbs = []
    for line in text.split("\n"):
        for m in re.finditer(r"\bsystemctl(?=\s)", line):
            rest = line[m.end():].split()
            verb = next((t for t in rest if not t.startswith("-")), None)
            verbs.append(verb)
    return verbs


def test_every_systemctl_call_site_in_the_script_uses_a_READ_verb():
    """The acknowledgement in test_no_real_launchers.py rests on this.

    `scripts/tmux-restore-observe.sh` is listed in ACKNOWLEDGED_UNSTUBBED for
    `systemctl` on the VERB ground — `show` is on nolaunch.SYSTEMCTL_READ_VERBS,
    so the verb-splitting stub passes it through as a read and it cannot start,
    stop or restart anything. That justification becomes FALSE the moment the
    script grows a mutating verb, and this is the test named there.

    Pinned against SYSTEMCTL_READ_VERBS itself, never a copied literal.
    """
    from testlib import nolaunch  # noqa: PLC0415 — keep module import cheap

    verbs = _systemctl_verbs(SCRIPT.read_text())
    assert verbs, (
        "positive control: the extractor found NO systemctl occurrence at all. "
        "A zero here is indistinguishable from a scan wired to nothing — if the "
        "call site was genuinely removed, drop the acknowledgement instead.")
    # Reachability, not coverage: proves the REAL invocation is inside the
    # scanned set, so the loop below is not passing over some other occurrence.
    assert "show" in verbs, verbs
    for verb in verbs:
        assert verb in nolaunch.SYSTEMCTL_READ_VERBS, (
            f"{verb!r} is not a read verb; the nolaunch stub blocks it and the "
            "acknowledgement in test_no_real_launchers.py is no longer true")


def test_the_systemctl_verb_extractor_can_SEE_a_mutating_verb():
    """The control for the test above. Without it, a `verbs` list that silently
    stopped resolving anything would pass the read-verb loop vacuously."""
    from testlib import nolaunch  # noqa: PLC0415

    bad = _systemctl_verbs('  raw=$(systemctl --user restart "$UNIT" 2>&1)\n')
    assert bad == ["restart"], bad
    assert bad[0] not in nolaunch.SYSTEMCTL_READ_VERBS


def test_an_unknown_subcommand_is_an_error_not_a_silent_success(tmp_path):
    env = dict(os.environ, TMUX_RESTORE_OBSERVE_DIR=str(tmp_path / "obs"))
    r = subprocess.run(
        ["bash", str(SCRIPT), "observe-everything"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "usage" in r.stderr
