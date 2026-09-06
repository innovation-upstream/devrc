"""Behavioural tests for scripts/tmux-restore-observe.sh — the reader that turns
ONE reboot into an answer about the post-boot workspace restore race.

Everything here drives the script's `verdict` and `extract` subcommands against
FIXTURE files. Nothing reboots, nothing touches the real tmux server, the real
restore plan, the real resurrect directory or systemd.

WHAT THIS SUITE IS FOR
----------------------
A reboot is one shot, so a verdict that is wrong — or that reads as clean when
it measured nothing — spends that shot for nothing. Round 1 of the audit found
the instrument wrong in BOTH directions on real data, and those two cases are
the load-bearing tests here:

  1. 🔴 A HEALTHY WORKSPACE MUST READ CLEAN. The first design keyed on duplicate
     window NAMES. `automatic-rename-format` is the basename of the pane's cwd,
     so windows of one session sitting in one repo share a name BY CONFIGURATION
     — measured: 9 duplicate (session, name) groups in a healthy live workspace,
     and the verdict returned rc 1 "RACE EVIDENCE" on a perfect restore.
     `test_duplicate_window_NAMES_are_not_race_evidence` is that regression.

  2. 🔴 MISPLACEMENT MUST BE VISIBLE. The PR's own premise names "duplicated or
     MISPLACED windows" and the first design could only see count changes. A
     window that came back at the right id holding the wrong cwd is the
     consequential case: `tmux-session-restore.py` targets `<session>:<index>`,
     so a resume lands in the wrong window.

  3. 🔴 THE cwd IS LOCATED BY CONTENT, NOT BY FIELD INDEX. Measured on one real
     save: 49 pane lines carry it at field 8 and 5 carry it at field 7, all with
     11 fields. Reading `$8` returned a neighbouring field for those five and
     reported a healthy workspace as MISPLACED.
     `test_the_cwd_is_found_at_EITHER_real_field_position` pins both variants.

And, as before, what the verdict REFUSES to say:

  4. NO REBOOT, NO VERDICT. Identical `boot_time` means the two captures describe
     the same boot — INCONCLUSIVE, never "no race observed".
  5. A TOTAL RESTORE FAILURE IS NOT "COULD NOT DECIDE". No tmux server at all is
     the most damning outcome and gets its own rc, not the shrug.
  6. AN ARM THAT COULD NOT RUN IS NOT A CLEAN ARM. An empty result from a failed
     comparison must not be reported as agreement.

Exit codes are asserted per condition: 0 clean, 1 race/misplacement, 2 usage,
3 could-not-decide, 4 windows missing, 5 no workspace at all.
"""

import os
import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tmux-restore-observe.sh"

RC_CLEAN = 0
RC_RACE = 1
RC_USAGE = 2
RC_INCONCLUSIVE = 3
RC_MISSING = 4
RC_NO_WORKSPACE = 5

HOST_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HOST_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# A healthy workspace as this host actually produces one: window NAMES repeat
# across sessions because automatic-rename derives them from the cwd basename.
HEALTHY_IDS = [("alpha", "1"), ("alpha", "2"), ("beta", "1")]
HEALTHY_NAMES = [
    ("alpha", "1", "devrc"),
    ("alpha", "2", "devrc"),   # same name as alpha:1 — by configuration
    ("beta", "1", "devrc"),    # and again in another session
]
HEALTHY_CWD = [
    ("alpha", "1", "/home/u/workspace/devrc"),
    ("alpha", "2", "/home/u/workspace/devrc"),
    ("beta", "1", "/home/u/workspace/devrc"),
]


def _rows(triples):
    return "\n".join("\t".join(t) for t in triples)


def _pre(tmp_path, name="pre.txt", boot="BOOT-A", host=HOST_A):
    body = [
        f"boot_time={boot}",
        "captured_at=T0",
        f"host_machine_id={host}",
        "host_name=hostA",
        "plan_entries=46",
    ]
    p = tmp_path / name
    p.write_text("\n".join(body) + "\n")
    return p


def _post(
    tmp_path,
    name="post.txt",
    boot="BOOT-B",
    host=HOST_A,
    windows="3",
    ids=None,
    names=None,
    expected_ids=None,
    obs_cwd=None,
    exp_cwd=None,
    replayed="/h/.tmux/resurrect/tmux_resurrect_PRE.txt",
    unit_started="Sat 2026-09-06 14:00:45 CDT",
    unparsed="0",
    plan_ids=None,
    plan_cwd=None,
    unit_status="0",
    journal="(fixture)",
):
    ids = HEALTHY_IDS if ids is None else ids
    names = HEALTHY_NAMES if names is None else names
    expected_ids = HEALTHY_IDS if expected_ids is None else expected_ids
    obs_cwd = HEALTHY_CWD if obs_cwd is None else obs_cwd
    exp_cwd = HEALTHY_CWD if exp_cwd is None else exp_cwd

    lines = [
        f"boot_time={boot}",
        "captured_at=T1",
        f"host_machine_id={host}",
        "host_name=hostA",
        "unit_Result=success",
        f"unit_ExecMainStatus={unit_status}",
        f"unit_InactiveExitTimestamp={unit_started}",
        "tmux_sessions=2",
    ]
    if windows is not None:
        lines.append(f"tmux_windows={windows}")
    if replayed is not None:
        lines += [
            f"replayed_layout_file={replayed}",
            "replayed_layout_mtime=2026-09-06 13:31:56 -0500",
            "replayed_layout_windows=3",
            f"replayed_layout_cwd_unparsed={unparsed}",
            "resurrect_last_link=" + replayed,
        ]
    else:
        lines.append("replayed_layout=UNMEASURED reason=no-resurrect-save-older-than-this-boot")

    lines.append("--- INVENTORY (session<TAB>index<TAB>window_name) ---")
    lines.append(_rows(names))
    lines.append("--- OBSERVED-IDS (session<TAB>index) ---")
    lines.append(_rows(ids))
    lines.append("--- OBSERVED-CWD (session<TAB>index<TAB>cwd) ---")
    lines.append(_rows(obs_cwd))
    lines.append("--- EXPECTED-IDS (session<TAB>index) ---")
    lines.append(_rows(expected_ids))
    lines.append("--- EXPECTED-CWD (session<TAB>index<TAB>cwd) ---")
    lines.append(_rows(exp_cwd))
    if plan_ids is not None:
        lines.append("--- PLAN-IDS (session<TAB>index) ---")
        lines.append(_rows(plan_ids))
    if plan_cwd is not None:
        lines.append("--- PLAN-CWD (session<TAB>index<TAB>cwd) ---")
        lines.append(_rows(plan_cwd))
    lines.append("--- JOURNAL ---")
    lines.append(journal)

    p = tmp_path / name
    p.write_text("\n".join(x for x in lines if x != "") + "\n")
    return p


def _run(args, tmp_path, timeout=60):
    env = dict(os.environ, TMUX_RESTORE_OBSERVE_DIR=str(tmp_path / "obs"))
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def _verdict(pre, post, tmp_path):
    return _run(["verdict", str(pre), str(post)], tmp_path)


# --------------------------------------------------------------------------- #
# The two directions round 1 measured wrong on real data
# --------------------------------------------------------------------------- #

def test_duplicate_window_NAMES_are_not_race_evidence(tmp_path):
    """🔴 THE REGRESSION. `automatic-rename-format` is the cwd basename, so
    repeated (session, name) pairs are the designed steady state. Measured: 9
    such groups in a healthy live workspace, and a name-keyed verdict returned
    rc 1 on a perfect restore — spending the one reboot on a false positive."""
    pre = _pre(tmp_path)
    post = _post(tmp_path)  # HEALTHY_NAMES repeats "devrc" three times
    r = _verdict(pre, post, tmp_path)
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "NO RACE OBSERVED" in r.stdout
    assert "RACE EVIDENCE" not in r.stdout
    assert "MISPLACEMENT" not in r.stdout


def test_a_window_that_came_back_in_the_wrong_place_is_race_evidence(tmp_path):
    """Same ids, same count, no duplicates — only the cwd moved. A count-keyed
    check is blind to this, and it is the case that actually causes damage:
    the restore targets `<session>:<index>`, so a resume lands in the wrong
    window."""
    moved = [
        ("alpha", "1", "/home/u/workspace/SOMEWHERE-ELSE"),
        ("alpha", "2", "/home/u/workspace/devrc"),
        ("beta", "1", "/home/u/workspace/devrc"),
    ]
    r = _verdict(_pre(tmp_path), _post(tmp_path, obs_cwd=moved), tmp_path)
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "MISPLACEMENT" in r.stdout
    assert "SOMEWHERE-ELSE" in r.stdout


# --------------------------------------------------------------------------- #
# The layout reader
# --------------------------------------------------------------------------- #

def _layout(tmp_path, pane_lines, window_lines=("window\talpha\t1\t:devrc\t1\t:*\tL\toff",)):
    p = tmp_path / "layout.txt"
    p.write_text("\n".join([*window_lines, *pane_lines]) + "\n")
    return p


def test_the_cwd_is_found_at_EITHER_real_field_position(tmp_path):
    """🔴 Measured on one real save: 49 pane lines carry the cwd at field 8 and
    5 carry it at field 7, all with 11 fields. A positional read returned the
    neighbouring field for those five and reported a healthy workspace as
    MISPLACED. Both real shapes are pinned here, verbatim in structure."""
    at8 = "pane\talpha\t1\t1\t:*\t1\t✳ a title\t:/home/u/eight\t1\tclaude\t:claude"
    at7 = "pane\tbeta\t1\t0\t:\t1\t:/home/u/seven\t1\tzsh\t4087506\t:"
    r = _run(["extract", str(_layout(tmp_path, [at8, at7]))], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "alpha\t1\t/home/u/eight" in r.stdout, r.stdout
    assert "beta\t1\t/home/u/seven" in r.stdout, r.stdout
    assert "cwd_unparsed=0" in r.stdout


def test_a_pane_title_that_looks_like_a_path_is_not_taken_as_the_cwd(tmp_path):
    """The successor test (`0|1` pane_active) is what separates them. Without
    it the title wins, because it comes first."""
    line = "pane\talpha\t1\t1\t:*\t1\t:/home/u/A-TITLE\t:/home/u/real\t1\tzsh\t:"
    r = _run(["extract", str(_layout(tmp_path, [line]))], tmp_path)
    assert "alpha\t1\t/home/u/real" in r.stdout, r.stdout
    assert "A-TITLE" not in r.stdout


def test_a_pane_line_with_no_readable_cwd_is_COUNTED_not_silently_dropped(tmp_path):
    """A dropped line leaves that window outside the misplacement check, and a
    silent drop is how a real misplacement goes unseen."""
    good = "pane\talpha\t1\t1\t:*\t1\tt\t:/home/u/ok\t1\tzsh\t:"
    bad = "pane\tbeta\t1\t1\t:*\t1\tt\tNOT-A-PATH\t1\tzsh\t:"
    r = _run(["extract", str(_layout(tmp_path, [good, bad]))], tmp_path)
    assert "cwd_unparsed=1" in r.stdout, r.stdout


def test_unreadable_pane_lines_are_disclosed_in_the_verdict(tmp_path):
    r = _verdict(_pre(tmp_path), _post(tmp_path, unparsed="2"), tmp_path)
    assert "2 pane line(s)" in r.stdout
    assert "OUTSIDE the misplacement check" in r.stdout


# --------------------------------------------------------------------------- #
# Count arms
# --------------------------------------------------------------------------- #

def test_a_window_the_replayed_layout_does_not_contain_is_race_evidence(tmp_path):
    ids = HEALTHY_IDS + [("ghost", "9")]
    r = _verdict(_pre(tmp_path), _post(tmp_path, ids=ids, windows="4"), tmp_path)
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "RACE EVIDENCE" in r.stdout
    assert "ghost" in r.stdout


def test_missing_windows_are_their_own_finding_not_a_clean_pass(tmp_path):
    r = _verdict(
        _pre(tmp_path),
        _post(tmp_path, ids=HEALTHY_IDS[:2], obs_cwd=HEALTHY_CWD[:2], windows="2"),
        tmp_path,
    )
    assert r.returncode == RC_MISSING, r.stdout + r.stderr
    assert "WINDOWS MISSING" in r.stdout
    assert "NO RACE OBSERVED" not in r.stdout


def test_missing_windows_are_reported_even_when_a_race_also_fired(tmp_path):
    """🔴 REACHABILITY, not just breakability. An earlier revision nested the
    missing arm under `if rc == 0`, so a single race line made every missing
    window unreportable — the guard existed and could never execute."""
    ids = [("alpha", "1"), ("ghost", "9")]  # beta:1 gone AND a ghost present
    cwd = [("alpha", "1", "/home/u/workspace/devrc")]
    r = _verdict(_pre(tmp_path), _post(tmp_path, ids=ids, obs_cwd=cwd), tmp_path)
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "RACE EVIDENCE" in r.stdout
    assert "WINDOWS MISSING" in r.stdout, "the missing arm is unreachable behind the race arm"
    assert "beta" in r.stdout


# --------------------------------------------------------------------------- #
# What it refuses to say
# --------------------------------------------------------------------------- #

def test_an_identical_boot_time_refuses_to_render_a_verdict(tmp_path):
    post = _post(tmp_path, boot="SAME", ids=HEALTHY_IDS + [("ghost", "9")])
    r = _verdict(_pre(tmp_path, boot="SAME"), post, tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "NO REBOOT" in r.stdout
    # Must not reach the comparison, even though a ghost window is present.
    assert "RACE EVIDENCE" not in r.stdout


def test_a_baseline_from_the_OTHER_HOST_refuses(tmp_path):
    """devrc manages two hosts and their workspaces are unrelated. Comparing
    one machine's layout to the other's windows renders a confident verdict
    about nothing."""
    r = _verdict(_pre(tmp_path, host=HOST_A), _post(tmp_path, host=HOST_B), tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "DIFFERENT HOST" in r.stdout


def test_no_tmux_server_is_a_TOTAL_RESTORE_FAILURE_not_a_shrug(tmp_path):
    """The most damning outcome must not read as 'could not decide' — the
    operator's response to a shrug is to re-run, which cannot help."""
    r = _verdict(_pre(tmp_path), _post(tmp_path, windows=None), tmp_path)
    assert r.returncode == RC_NO_WORKSPACE, r.stdout + r.stderr
    assert "TOTAL RESTORE FAILURE" in r.stdout
    assert "INCONCLUSIVE" not in r.stdout


def test_no_pre_boot_layout_means_there_is_nothing_to_compare_against(tmp_path):
    r = _verdict(_pre(tmp_path), _post(tmp_path, replayed=None), tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "no resurrect save older than this boot" in r.stdout


def test_an_empty_id_section_is_inconclusive_never_a_comparison(tmp_path):
    r = _verdict(_pre(tmp_path), _post(tmp_path, expected_ids=[]), tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "vacuous" in r.stdout


def test_a_unit_that_never_ran_says_so_instead_of_reading_as_success(tmp_path):
    """🔴 `Result=success ExecMainStatus=0` is byte-identical for a unit that
    never started — measured on a never-run user unit. The timer is
    OnActiveSec=45s, so an operator running `post` immediately after login hits
    exactly this and would otherwise read 'the unit succeeded'."""
    r = _verdict(_pre(tmp_path), _post(tmp_path, unit_started=""), tmp_path)
    assert "NOT RUN" in r.stdout
    assert "OnActiveSec=45s" in r.stdout
    assert "reads the same" in r.stdout
    # It is a caveat on the reading, not a verdict of its own.
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr


def test_a_clean_boot_says_so_AND_says_it_is_only_one_sample(tmp_path):
    r = _verdict(_pre(tmp_path), _post(tmp_path), tmp_path)
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "NO RACE OBSERVED" in r.stdout
    assert "ONE boot" in r.stdout
    assert "does not close" in r.stdout


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

def test_the_script_exists_and_is_executable():
    assert SCRIPT.is_file(), SCRIPT
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_post_without_a_baseline_refuses_rather_than_capturing_half_the_evidence(tmp_path):
    r = _run(["post"], tmp_path, timeout=120)
    assert r.returncode == RC_USAGE, r.stdout + r.stderr
    assert "no baseline" in r.stderr


def test_an_unknown_subcommand_is_an_error_not_a_silent_success(tmp_path):
    r = _run(["observe-everything"], tmp_path)
    assert r.returncode == RC_USAGE, r.stdout + r.stderr
    assert "usage" in r.stderr


def test_the_usage_line_documents_every_rc_the_script_can_return(tmp_path):
    """The handoff hands an operator an rc. A code the usage does not name is a
    code nobody can act on — rc 2 and rc 5 were both missing from an earlier
    legend."""
    r = _run(["observe-everything"], tmp_path)
    for code in ("0", "1", "2", "3", "4", "5"):
        assert re.search(rf"\brc .*\b{code}\b|·\s*{code}\b", r.stderr), (
            f"rc {code} is not named in the usage legend: {r.stderr}")


def _stub_bin(tmp_path):
    """A PATH prefix where the host-touching readers all fail, so `capture` runs
    its real code path without reaching this machine's tmux, systemd or journal.
    Everything else (date, stat, grep, awk, python3) still resolves behind it."""
    from testlib import mockbin  # noqa: PLC0415 — keep module import cheap

    b = tmp_path / "stubbin"
    b.mkdir(exist_ok=True)
    for name, body in (
        ("tmux", "exit 1\n"),
        ("systemctl", "exit 1\n"),
        ("journalctl", "exit 0\n"),
    ):
        mockbin.write_exec(b / name, body)
    return b


def _live_stub_bin(tmp_path):
    """A PATH prefix where tmux and systemctl SUCCEED, so `capture` reaches the
    emits that a failing stub leaves legitimately UNMEASURED.

    🔴 This exists because the failing-stub seam test could NOT pin them:
    mutants deleting the `--- OBSERVED-CWD` section, `tmux_windows` and the
    `unit_*` block all SURVIVED against it. `tmux_windows` alone decides rc 5,
    so its loss would turn every verdict into TOTAL RESTORE FAILURE."""
    from testlib import mockbin  # noqa: PLC0415

    b = tmp_path / "livestub"
    b.mkdir(exist_ok=True)
    mockbin.write_exec(b / "tmux", r'''
case "$1" in
  has-session) exit 0 ;;
  list-sessions) printf 'alpha\n' ;;
  list-windows)
      case "$*" in
        *window_name*) printf 'alpha\t1\tdevrc\n' ;;
        *window_index*) printf 'alpha\t1\n' ;;
        *) printf 'alpha\n' ;;
      esac ;;
  list-panes)
      case "$*" in
        *pane_current_path*) printf 'alpha\t1\t/home/u/devrc\n' ;;
        *) printf '%%0\n' ;;
      esac ;;
  display-message) echo 1 ;;
  show-options) echo "" ;;
  *) exit 0 ;;
esac
''')
    mockbin.write_exec(b / "systemctl", r'''
printf 'Result=success\nExecMainStatus=0\nNRestarts=0\n'
printf 'ActiveEnterTimestamp=Sat 2026-09-06 14:00:46 CDT\n'
printf 'InactiveExitTimestamp=Sat 2026-09-06 14:00:45 CDT\nAfter=x\n'
''')
    mockbin.write_exec(b / "journalctl", "exit 0\n")
    return b


def test_capture_post_EMITS_the_verdict_DECIDING_fields_when_the_readers_SUCCEED(tmp_path):
    """The other half of the seam. The failing-stub test below pins the fields
    reachable when tmux/systemctl are down; this one pins the ones that only
    exist when they answer — the fields that actually decide the verdict."""
    obs = tmp_path / "obs"
    obs.mkdir()
    (obs / "pre-latest.txt").write_text("boot_time=OLD\ncaptured_at=T0\n")
    res = tmp_path / "resurrect"
    res.mkdir()
    layout = res / "tmux_resurrect_20200101T000000.txt"
    layout.write_text(
        "window\talpha\t1\t:devrc\t1\t:*\tL\toff\n"
        "pane\talpha\t1\t1\t:*\t1\tt\t:/home/u/devrc\t1\tzsh\t:\n"
    )
    os.utime(layout, (1, 1))
    plan = tmp_path / "plan.json"
    plan.write_text(
        '[{"session":"alpha","window":"1","cwd":"/home/u/devrc","session_id":"x",'
        '"bind_source":"ledger"}]'
    )

    env = dict(
        os.environ,
        PATH=f"{_live_stub_bin(tmp_path)}:{os.environ['PATH']}",
        TMUX_RESTORE_OBSERVE_DIR=str(obs),
        TMUX_RESURRECT_DIR=str(res),
        TMUX_RESTORE_PLAN=str(plan),
        TMUX_RESTORE_LOG=str(tmp_path / "none.log"),
    )
    subprocess.run(["bash", str(SCRIPT), "post"],
                   capture_output=True, text=True, env=env, timeout=180)
    text = sorted(obs.glob("post-*.txt"))[-1].read_text()

    # Each of these was a SURVIVING mutant against the failing-stub fixture.
    assert "tmux_windows=" in text, f"the field that decides rc 5 is absent:\n{text}"
    assert "unit_Result=" in text and "unit_ExecMainStatus=" in text, text
    for header in ("--- OBSERVED-CWD", "--- OBSERVED-IDS", "--- PLAN-IDS", "--- PLAN-CWD"):
        assert header in text, f"{header} missing:\n{text}"
    assert "alpha\t1\t/home/u/devrc" in text, text
    assert "plan_layout_skew_seconds=" in text, text


def test_capture_post_EMITS_the_fields_the_verdict_READS(tmp_path):
    """🔴 THE SEAM. `extract` computes the unparsed count and `verdict` prints
    it, and both were pinned — while nothing checked that `capture post` writes
    it at all. Measured: deleting the emit line from `capture` left the whole
    suite green, so the two hermetically-tested halves were joined by an
    unguarded wire.

    ⚠ SCOPE, stated because the earlier docstring overclaimed it: this fixture
    stubs tmux and systemctl to FAIL, so it pins only the fields reachable with
    those readers down — the layout-derived keys and sections. The fields that
    exist only when they answer (`tmux_windows`, the `unit_*` block,
    `--- OBSERVED-CWD`) are pinned by
    `test_capture_post_EMITS_the_verdict_DECIDING_fields_when_the_readers_SUCCEED`
    above; against THIS fixture alone, mutants deleting them SURVIVE.
    """
    obs = tmp_path / "obs"
    obs.mkdir()
    (obs / "pre-latest.txt").write_text("boot_time=OLD\ncaptured_at=T0\n")

    # A save older than this boot is what `replayed_layout` looks for.
    res = tmp_path / "resurrect"
    res.mkdir()
    layout = res / "tmux_resurrect_20200101T000000.txt"
    layout.write_text(
        "window\talpha\t1\t:devrc\t1\t:*\tL\toff\n"
        "pane\talpha\t1\t1\t:*\t1\tt\t:/home/u/ok\t1\tzsh\t:\n"
        "pane\tbeta\t1\t1\t:*\t1\tt\tNOT-A-PATH\t1\tzsh\t:\n"
    )
    os.utime(layout, (1, 1))  # older than any real boot on this machine

    env = dict(
        os.environ,
        PATH=f"{_stub_bin(tmp_path)}:{os.environ['PATH']}",
        TMUX_RESTORE_OBSERVE_DIR=str(obs),
        TMUX_RESURRECT_DIR=str(res),
        TMUX_RESTORE_PLAN=str(tmp_path / "no-such-plan.json"),
        TMUX_RESTORE_LOG=str(tmp_path / "no-such-restore.log"),
    )
    subprocess.run(
        ["bash", str(SCRIPT), "post"],
        capture_output=True, text=True, env=env, timeout=180,
    )

    posts = sorted(obs.glob("post-*.txt"))
    assert posts, f"`post` wrote no capture file into {obs}"
    text = posts[-1].read_text()

    for key in (
        "boot_time=", "host_machine_id=", "replayed_layout_file=",
        "replayed_layout_cwd_unparsed=",
    ):
        assert key in text, f"capture did not emit {key!r}, which verdict reads:\n{text}"
    for header in ("--- OBSERVED-IDS", "--- EXPECTED-IDS", "--- EXPECTED-CWD"):
        assert header in text, f"capture did not emit the {header} section:\n{text}"

    # The value must be the REAL computation, not a placeholder: the fixture has
    # exactly one unreadable pane line.
    assert "replayed_layout_cwd_unparsed=1" in text, text
    assert "alpha\t1\t/home/u/ok" in text, text
    # And the reader that could not run says UNMEASURED rather than reporting 0.
    assert "tmux=UNMEASURED" in text, text


# --------------------------------------------------------------------------- #
# Round 2: the post-boot workspace has TWO writers, not one
# --------------------------------------------------------------------------- #

def test_a_window_the_RESTORE_created_is_not_race_evidence(tmp_path):
    """🔴 THE REGRESSION. `tmux-session-restore.py` runs `new-window -t
    <sess>:<idx>` (:743) for any plan entry missing from the workspace, so a
    plan id absent from the replayed layout is a window the restore created ON
    PURPOSE. Measured against a layout-only expectation at a plan/layout skew of
    46 min: 1 false RACE id and 4 false MISPLACEMENT rows; at 91 min, 1 and 5.
    Skew is normally ~40 s, but the restore tolerates 2 h BY DESIGN
    (`--staleness-check 2`), and a dead save chain is this instrument's own
    premise — so the false-positive regime is the degraded state it exists to
    observe."""
    ids = HEALTHY_IDS + [("alpha", "5")]
    cwd = HEALTHY_CWD + [("alpha", "5", "/home/u/workspace/talos")]
    r = _verdict(
        _pre(tmp_path),
        _post(tmp_path, ids=ids, obs_cwd=cwd, windows="4",
              plan_ids=[("alpha", "5")],
              plan_cwd=[("alpha", "5", "/home/u/workspace/talos")]),
        tmp_path,
    )
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "the restore itself created 1 window(s)" in r.stdout
    assert "RACE EVIDENCE" not in r.stdout


def test_a_window_in_NEITHER_the_layout_nor_the_plan_is_still_race_evidence(tmp_path):
    """The control for the exclusion above: subtracting the plan must not
    subtract everything."""
    ids = HEALTHY_IDS + [("ghost", "9")]
    cwd = HEALTHY_CWD + [("ghost", "9", "/home/u/elsewhere")]
    r = _verdict(
        _pre(tmp_path),
        _post(tmp_path, ids=ids, obs_cwd=cwd, windows="4",
              plan_ids=[("alpha", "1")], plan_cwd=[("alpha", "1", "/home/u/workspace/devrc")]),
        tmp_path,
    )
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "RACE EVIDENCE" in r.stdout
    assert "ghost" in r.stdout


def test_a_cwd_matching_the_PLAN_is_not_misplacement(tmp_path):
    """The restore `send-keys`es `cd <plan cwd> && claude --resume` (:755), so a
    window sitting in the plan's cwd rather than the layout's is the restore
    working correctly. Only a cwd matching NEITHER is a misplacement."""
    moved_to_plan = [
        ("alpha", "1", "/home/u/workspace/PLANNED"),
        ("alpha", "2", "/home/u/workspace/devrc"),
        ("beta", "1", "/home/u/workspace/devrc"),
    ]
    r = _verdict(
        _pre(tmp_path),
        _post(tmp_path, obs_cwd=moved_to_plan,
              plan_ids=[("alpha", "1")],
              plan_cwd=[("alpha", "1", "/home/u/workspace/PLANNED")]),
        tmp_path,
    )
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "MISPLACEMENT" not in r.stdout


def test_a_cwd_matching_NEITHER_the_layout_nor_the_plan_is_misplacement(tmp_path):
    """Control for the exclusion above."""
    r = _verdict(
        _pre(tmp_path),
        _post(tmp_path,
              obs_cwd=[("alpha", "1", "/home/u/NOWHERE")] + HEALTHY_CWD[1:],
              plan_ids=[("alpha", "1")],
              plan_cwd=[("alpha", "1", "/home/u/workspace/PLANNED")]),
        tmp_path,
    )
    assert r.returncode == RC_RACE, r.stdout + r.stderr
    assert "MISPLACEMENT" in r.stdout
    assert "NOWHERE" in r.stdout


# --------------------------------------------------------------------------- #
# Round 2: an unmeasured arm must not render as agreement
# --------------------------------------------------------------------------- #

def test_an_empty_cwd_section_is_NOT_reported_as_in_the_right_place(tmp_path):
    """🔴 THE REGRESSION. The id sections had a vacuity guard and the cwd
    sections did not, so an empty OBSERVED-CWD produced an empty `moved` and
    rendered as `in the right place`, rc 0 — demonstrated on the shipped
    script. Reachable because list-windows and list-panes are two separate tmux
    calls: the server dying between them leaves ids populated, cwds empty."""
    r = _verdict(_pre(tmp_path), _post(tmp_path, obs_cwd=[]), tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "MISPLACEMENT NOT MEASURED" in r.stdout
    assert "in the right place" not in r.stdout
    assert "NO RACE OBSERVED" not in r.stdout


def test_the_emitters_OWN_unmeasured_marker_does_not_render_as_clean(tmp_path):
    """The sharpest form: `section()` strips `#` rows, so the emitter's explicit
    `# UNMEASURED — no tmux server responding` became an EMPTY section and then
    a clean verdict — the instrument converting its own admission of ignorance
    into a reassuring answer."""
    post = _post(tmp_path)
    text = post.read_text().replace(
        "\n".join("\t".join(t) for t in HEALTHY_CWD),
        "# UNMEASURED — no tmux server responding",
    )
    post.write_text(text)
    r = _verdict(_pre(tmp_path), post, tmp_path)
    assert r.returncode == RC_INCONCLUSIVE, r.stdout + r.stderr
    assert "MISPLACEMENT NOT MEASURED" in r.stdout


# --------------------------------------------------------------------------- #
# Round 2: the unit's process status
# --------------------------------------------------------------------------- #

def test_a_unit_that_ran_and_FAILED_says_so(tmp_path):
    """🔴 `Result` is systemd's verdict on the UNIT; `ExecMainStatus` is the
    PROCESS's exit code, and for a Type=oneshot they disagree routinely.
    Measured on this host: `Result=success` with `ExecMainStatus=1`. The handoff
    records this unit exiting 1 on EVERY boot from 2026-08-04 until
    #1297+#1309 — the historically most common failure state, and it used to be
    a parenthetical beside the word 'success'."""
    r = _verdict(_pre(tmp_path), _post(tmp_path, unit_status="1"), tmp_path)
    assert "RAN AND FAILED" in r.stdout
    assert "ExecMainStatus=1" in r.stdout
    assert "did not complete its own work" in r.stdout


def test_a_unit_that_ran_and_succeeded_does_not_cry_wolf(tmp_path):
    r = _verdict(_pre(tmp_path), _post(tmp_path, unit_status="0"), tmp_path)
    assert "RAN AND FAILED" not in r.stdout
    assert "exited 0" in r.stdout


# --------------------------------------------------------------------------- #
# Round 2: the section parser
# --------------------------------------------------------------------------- #

def test_a_later_line_reproducing_a_section_HEADER_cannot_reopen_it(tmp_path):
    """🔴 `section()` re-evaluated `inside` at every `--- ` line, so a JOURNAL
    line spelling a section header re-opened the block. Demonstrated on the
    shipped script: an injected `GHOST 99` produced a false RACE EVIDENCE, rc 1.
    The header comment claimed ordering made this impossible; ordering does not
    protect a parser."""
    r = _verdict(
        _pre(tmp_path),
        _post(tmp_path, journal=(
            "restore log follows\n"
            "--- OBSERVED-IDS (session<TAB>index) ---\n"
            "GHOST\t99")),
        tmp_path,
    )
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    assert "GHOST" not in r.stdout
    assert "RACE EVIDENCE" not in r.stdout


def test_a_save_whose_mtime_is_the_epoch_is_still_a_candidate(tmp_path):
    """An edge-case pin, labelled as one. `replayed_layout` seeds its
    best-so-far with a sentinel BELOW every possible mtime; seeded at 0 it
    compares `t > 0` and silently skips a file stamped exactly at the epoch.
    No real save carries that mtime — this exists so the sentinel cannot be
    "simplified" back to 0 without a red."""
    obs = tmp_path / "obs"
    obs.mkdir()
    (obs / "pre-latest.txt").write_text("boot_time=OLD\ncaptured_at=T0\n")
    res = tmp_path / "resurrect"
    res.mkdir()
    layout = res / "tmux_resurrect_19700101T000000.txt"
    layout.write_text("window\talpha\t1\t:devrc\t1\t:*\tL\toff\n")
    os.utime(layout, (0, 0))

    env = dict(
        os.environ,
        PATH=f"{_stub_bin(tmp_path)}:{os.environ['PATH']}",
        TMUX_RESTORE_OBSERVE_DIR=str(obs),
        TMUX_RESURRECT_DIR=str(res),
        TMUX_RESTORE_PLAN=str(tmp_path / "none.json"),
        TMUX_RESTORE_LOG=str(tmp_path / "none.log"),
    )
    subprocess.run(["bash", str(SCRIPT), "post"],
                   capture_output=True, text=True, env=env, timeout=180)
    text = sorted(obs.glob("post-*.txt"))[-1].read_text()
    assert f"replayed_layout_file={layout}" in text, text


def test_a_broken_awk_degrades_to_INCONCLUSIVE_not_to_clean(tmp_path):
    """🔴 Every section reader is awk. If awk cannot run, the comparison has no
    data — and the one thing that must not happen is for an empty comparison to
    render as agreement. Pinned as the REAL degradation path, which is why the
    `moved_rc` branch in the script is labelled unreachable rather than claimed
    as coverage."""
    from testlib import mockbin  # noqa: PLC0415

    b = tmp_path / "brokenbin"
    b.mkdir()
    mockbin.write_exec(b / "awk", "exit 2\n")

    env = dict(
        os.environ,
        PATH=f"{b}:{os.environ['PATH']}",
        TMUX_RESTORE_OBSERVE_DIR=str(tmp_path / "obs"),
    )
    r = subprocess.run(
        ["bash", str(SCRIPT), "verdict", str(_pre(tmp_path)), str(_post(tmp_path))],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode != RC_CLEAN, r.stdout + r.stderr
    assert "NO RACE OBSERVED" not in r.stdout, (
        "a verdict computed with no readable sections reported agreement")
    assert "INCONCLUSIVE" in r.stdout


def test_pre_installs_the_copy_of_itself_that_the_handoff_points_at(tmp_path):
    """🔴 The handoff advertises `<obs-dir>/tmux-restore-observe.sh` as the
    branch-switch-proof path. Without a producer it is a path nothing creates
    and nothing refreshes, so a later revision silently leaves the operator
    running the old script."""
    obs = tmp_path / "obs"
    r = _run(["pre"], tmp_path, timeout=180)
    assert r.returncode == RC_CLEAN, r.stdout + r.stderr
    copy = obs / "tmux-restore-observe.sh"
    assert copy.is_file(), f"{copy} was not written by `pre`"
    assert copy.read_bytes() == SCRIPT.read_bytes(), "the installed copy is not this script"
    assert os.access(copy, os.X_OK)
    assert (obs / "pre-latest.txt").exists()


# --------------------------------------------------------------------------- #
# The systemctl acknowledgement in test_no_real_launchers.py rests on these
# --------------------------------------------------------------------------- #

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
            verbs.append(next((t for t in rest if not t.startswith("-")), None))
    return verbs


def test_every_systemctl_call_site_in_the_script_uses_a_READ_verb():
    """The acknowledgement in test_no_real_launchers.py rests on this: `show` is
    on nolaunch.SYSTEMCTL_READ_VERBS, so the verb-splitting stub passes it
    through as a read and it cannot start, stop or restart anything. That
    justification becomes FALSE the moment the script grows a mutating verb.

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
