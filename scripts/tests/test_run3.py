"""Tests for `scripts/run3` — run a command, capture its two streams SEPARATELY.

WHAT THIS SUITE IS, HONESTLY
----------------------------
`scripts/run3` does not exist on `origin/main`, so every test here is RED there
only because the file is absent (collection fails at import time on the
`RUN3.is_file()` assertion, not because a defect is reproduced). That makes this
a NEW GUARD, not regression coverage: no test below has ever been watched to
fail against a version of run3 that had the bug.

Two tests carry their own evidence instead, and they are the reason the guard is
not vacuous:

  * `test_zsh_multios_control_proves_the_trap_is_live` asserts the DEFECT
    MECHANISM directly in this environment's zsh — `cmd 2>&1 >/dev/null | c`
    hands the consumer STDOUT. It passes on `origin/main` too (it never touches
    run3). It is the positive control: without it, a green separation test could
    mean "the streams were never merged in the first place".

  * `test_a_merging_variant_fails_the_separation_assertion` mutates run3's own
    redirection into a merging one and asserts the separation check goes RED
    against it. That is the mutation this suite exists to catch, killed on
    purpose, in-band.

THE FAILURE THIS PREVENTS
-------------------------
Attributing output to the wrong stream. Measured three times in one session
(2026-08-20): a `--json` payload correctly on stdout reported as being on
stderr, with a subagent dispatched to fix a bug that did not exist; and twice,
stderr advisory text reported as "polluting stdout" after a plain `2>&1` had
already destroyed the distinction.

    run:  pytest scripts/tests/test_run3.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
RUN3 = SCRIPTS / "run3"

# 🔴 Resolve interpreters ONCE to an absolute path, from the ambient
# environment. `/usr/bin/env` does not exist in the nix build sandbox — the
# authoritative tier — so an argv whose first element is that literal path
# raises FileNotFoundError there while passing on the dev host. Same reasoning
# as test_monitor_blackout.py; see scripts/testlib/mockbin.py.
_BASH = shutil.which("bash")
if _BASH is None:  # pragma: no cover — both tiers ship bash
    raise RuntimeError("bash not found on PATH; this suite cannot run hermetically")

_ZSH = shutil.which("zsh")
_needs_zsh = pytest.mark.skipif(_ZSH is None, reason="zsh not on PATH")

OUT_MARK = "STDOUT-MARK-9f21"
ERR_MARK = "STDERR-MARK-4c07"


def test_run3_exists_and_is_executable():
    """Guard the guard: a moved or non-executable run3 must not pass silently."""
    assert RUN3.is_file(), f"{RUN3} not found — every test below would be vacuous."
    assert os.access(RUN3, os.X_OK), f"{RUN3} is not executable."


def _run(*args, script: Path | None = None, env=None, cwd=None):
    """Invoke run3 under an absolute bash, capturing OUR two streams separately.

    Note the shape: `capture_output=True` gives `.stdout` and `.stderr` as
    distinct attributes. This helper never merges them either — a test harness
    that merged would be unable to see the bug it is testing for.
    """
    return subprocess.run(
        [_BASH, str(script or RUN3), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def _summary_paths(stdout: str) -> dict[str, Path]:
    """Parse the `run3: stdout <n> B  <path>` summary lines into {stream: path}."""
    paths: dict[str, Path] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "run3:" and parts[1] in ("stdout", "stderr"):
            paths[parts[1]] = Path(parts[-1])
    return paths


def _summary_bytes(stdout: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "run3:" and parts[1] in ("stdout", "stderr"):
            counts[parts[1]] = int(parts[2])
    return counts


# --- the core guard ----------------------------------------------------------


def test_stdout_and_stderr_land_in_separate_files():
    """🔴 THE TEST THAT WOULD FAIL IF run3 MERGED THE STREAMS.

    Distinguishable text on each stream; each file must hold ITS OWN text and
    must NOT hold the other's. A merged capture fails on the cross-contamination
    assertions even if the "own text" assertions still pass.
    """
    r = _run("sh", "-c", f'printf "{OUT_MARK}\\n"; printf "{ERR_MARK}\\n" >&2')
    assert r.returncode == 0, r.stderr

    paths = _summary_paths(r.stdout)
    assert set(paths) == {"stdout", "stderr"}, f"summary not parseable: {r.stdout!r}"

    out_text = paths["stdout"].read_text()
    err_text = paths["stderr"].read_text()

    assert out_text == f"{OUT_MARK}\n", f"stdout capture wrong: {out_text!r}"
    assert err_text == f"{ERR_MARK}\n", f"stderr capture wrong: {err_text!r}"
    assert ERR_MARK not in out_text, "STREAMS MERGED: stderr text reached the stdout file"
    assert OUT_MARK not in err_text, "STREAMS MERGED: stdout text reached the stderr file"


def test_a_merging_variant_fails_the_separation_assertion(tmp_path):
    """Mutation control for the test above — break run3 and watch it go red.

    A guard nobody has watched fail proves nothing. Here the mutation is the
    exact defect: one redirection into the stdout file plus `2>&1`. The
    separation assertions must then be violated, and specifically by
    CONTAMINATION (both marks in the stdout file, stderr file empty) — not by
    some unrelated crash, which is why the exit code is asserted too.
    """
    src = RUN3.read_text()
    needle = '"$@" >"$outf" 2>"$errf"'
    assert src.count(needle) == 1, (
        "the redirection this mutation targets is not where it was; "
        "re-point the mutation before trusting its result"
    )
    # The mutant still CREATES both files (`: >"$errf"`), so the only thing that
    # differs from the real script is where stderr goes. A mutation that also
    # removed the stderr file would go red for the wrong reason — a missing file
    # rather than a contaminated one.
    mutant = tmp_path / "run3-merged"
    mutant.write_text(src.replace(needle, ': >"$errf"\n"$@" >"$outf" 2>&1'))
    mutant.chmod(0o755)

    r = _run(
        "sh", "-c", f'printf "{OUT_MARK}\\n"; printf "{ERR_MARK}\\n" >&2',
        script=mutant,
    )
    assert r.returncode == 0, r.stderr
    paths = _summary_paths(r.stdout)
    out_text = paths["stdout"].read_text()
    err_text = paths["stderr"].read_text()

    # This is what the real test's assertions catch.
    assert ERR_MARK in out_text, (
        "the mutant did NOT merge, so the separation test above is not known to "
        "discriminate — the mutation is wrong, not the code"
    )
    assert err_text == "", f"mutant should leave the stderr file empty, got {err_text!r}"


@_needs_zsh
def test_zsh_multios_control_proves_the_trap_is_live():
    """Positive control on the DEFECT MECHANISM, independent of run3.

    In zsh, MULTIOS duplicates stdout onto the pipe, so the canonical
    "stderr only" idiom hands the consumer STDOUT. bash yields nothing. If this
    ever stops holding, the separation tests are still correct but the RULE they
    enforce would need re-measuring — so assert it rather than assume it.
    """
    z = subprocess.run(
        [_ZSH, "-c", 'printf "PAYLOAD\\n" 2>&1 >/dev/null | cat'],
        capture_output=True, text=True,
    )
    b = subprocess.run(
        [_BASH, "-c", 'printf "PAYLOAD\\n" 2>&1 >/dev/null | cat'],
        capture_output=True, text=True,
    )
    assert z.stdout == "PAYLOAD\n", (
        "zsh MULTIOS no longer duplicates stdout onto the pipe; the rule in "
        f"claude/RULES.md needs re-measuring. got {z.stdout!r}"
    )
    assert b.stdout == "", f"bash should yield nothing here, got {b.stdout!r}"


@_needs_zsh
def test_invoked_from_zsh_streams_stay_separate():
    """run3 has a bash shebang but is CALLED from zsh — the real failure site."""
    cmd = f'{RUN3} sh -c \'printf "{OUT_MARK}\\n"; printf "{ERR_MARK}\\n" >&2\''
    r = subprocess.run([_ZSH, "-c", cmd], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    paths = _summary_paths(r.stdout)
    assert paths["stdout"].read_text() == f"{OUT_MARK}\n"
    assert paths["stderr"].read_text() == f"{ERR_MARK}\n"


# --- exit status -------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, 7, 42])
def test_exit_code_is_preserved(code):
    """A wrapper that swallows a non-zero status turns a failure into a pass."""
    r = _run("sh", "-c", f"exit {code}")
    assert r.returncode == code, f"run3 exited {r.returncode}, expected {code}"
    assert f"run3: rc={code}" in r.stdout, f"summary did not report rc: {r.stdout!r}"


def test_a_command_that_does_not_exist_reports_127():
    r = _run("definitely-not-a-real-binary-6b3f")
    assert r.returncode == 127, f"expected 127, got {r.returncode}"
    assert "run3: rc=127" in r.stdout
    paths = _summary_paths(r.stdout)
    # The shell's "not found" diagnostic is stderr — and must be in the STDERR
    # file, which is the whole attribution claim.
    assert paths["stdout"].read_text() == ""
    assert paths["stderr"].read_text() != ""


# --- empty / one-sided / large output ----------------------------------------


def test_no_output_on_either_stream():
    r = _run("sh", "-c", "true")
    assert r.returncode == 0
    counts = _summary_bytes(r.stdout)
    assert counts == {"stdout": 0, "stderr": 0}, counts
    paths = _summary_paths(r.stdout)
    assert paths["stdout"].is_file() and paths["stderr"].is_file(), (
        "both files must exist even when empty — a missing file is not the same "
        "claim as an empty one"
    )


@pytest.mark.parametrize(
    "stream,shell",
    [
        ("stdout", f'printf "{OUT_MARK}\\n"'),
        ("stderr", f'printf "{ERR_MARK}\\n" >&2'),
    ],
)
def test_output_on_only_one_stream(stream, shell):
    other = "stderr" if stream == "stdout" else "stdout"
    r = _run("sh", "-c", shell)
    counts = _summary_bytes(r.stdout)
    assert counts[stream] > 0, f"{stream} should have bytes: {counts}"
    assert counts[other] == 0, f"{other} should be empty: {counts}"


def test_large_output_byte_counts_are_exact():
    """1 MiB on each stream — captures stream to a file, nothing buffers in a var."""
    n = 1024 * 1024
    r = _run(
        "sh", "-c",
        f'yes x | head -c {n}; yes y | head -c {n} >&2',
    )
    assert r.returncode == 0, r.stderr
    counts = _summary_bytes(r.stdout)
    assert counts == {"stdout": n, "stderr": n}, counts
    paths = _summary_paths(r.stdout)
    assert paths["stdout"].stat().st_size == n
    assert paths["stderr"].stat().st_size == n
    # And still not merged at size.
    assert paths["stdout"].read_bytes()[:16] == b"x\n" * 8
    assert paths["stderr"].read_bytes()[:16] == b"y\n" * 8


# --- capture location --------------------------------------------------------


def test_capture_dir_is_unique_per_invocation():
    """🔴 No fixed shared path. A concurrent agent overwrote exactly such a path."""
    a = _summary_paths(_run("sh", "-c", "true").stdout)
    b = _summary_paths(_run("sh", "-c", "true").stdout)
    assert a["stdout"].parent != b["stdout"].parent, (
        f"two invocations shared a capture dir: {a['stdout'].parent}"
    )


def test_dir_option_places_the_capture_under_it(tmp_path):
    target = tmp_path / "captures"
    r = _run("--dir", str(target), "sh", "-c", "true")
    assert r.returncode == 0, r.stderr
    paths = _summary_paths(r.stdout)
    assert target in paths["stdout"].parents, f"{paths['stdout']} not under {target}"


def test_source_never_uses_the_merging_idiom():
    """Structural: run3 must not merge its own streams anywhere.

    `2>&1` appears in the header comment as the DOCUMENTED trap, so the scan is
    over code lines only — comment lines are stripped first, and the test proves
    the strip did not eat everything (a positive control on the filter itself).
    """
    lines = RUN3.read_text().splitlines()
    code = [ln for ln in lines if not ln.lstrip().startswith("#")]
    assert len(code) > 40, "the comment strip ate the script; the scan below is vacuous"
    offenders = [ln for ln in code if "2>&1" in ln]
    assert not offenders, f"run3 merges its own streams: {offenders}"


def test_source_uses_mktemp():
    assert "mktemp -d" in RUN3.read_text(), (
        "run3 must create its capture dir with mktemp, not a fixed path"
    )


# --- CLI surface -------------------------------------------------------------


def test_no_command_is_a_usage_error():
    r = _run()
    assert r.returncode == 2, f"expected usage rc 2, got {r.returncode}"
    assert "no command given" in r.stderr


def test_unknown_option_is_a_usage_error():
    r = _run("--not-an-option", "true")
    assert r.returncode == 2
    assert "unknown option" in r.stderr


def test_help_exits_zero_and_prints_usage():
    r = _run("--help")
    assert r.returncode == 0
    assert "usage: run3" in r.stdout


def test_echo_flags_show_each_stream():
    r = _run("-b", "sh", "-c", f'printf "{OUT_MARK}"; printf "{ERR_MARK}" >&2')
    assert OUT_MARK in r.stdout and ERR_MARK in r.stdout
    assert "--- stdout" in r.stdout and "--- stderr" in r.stdout


@pytest.mark.parametrize(
    "flag,want,unwanted",
    [("-o", OUT_MARK, ERR_MARK), ("-e", ERR_MARK, OUT_MARK)],
)
def test_raw_prints_only_the_selected_stream(flag, want, unwanted):
    r = _run(
        flag, "--raw", "sh", "-c",
        f'printf "{OUT_MARK}"; printf "{ERR_MARK}" >&2',
    )
    assert r.stdout == want, f"--raw should print the stream verbatim: {r.stdout!r}"
    assert unwanted not in r.stdout
    assert "run3:" not in r.stdout, "--raw must suppress the summary"


def test_raw_without_exactly_one_stream_is_a_usage_error():
    assert _run("--raw", "true").returncode == 2
    assert _run("-b", "--raw", "true").returncode == 2


def test_double_dash_lets_a_command_start_with_a_dash():
    r = _run("--", "sh", "-c", "true")
    assert r.returncode == 0, r.stderr


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
