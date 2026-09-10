"""gate.sh's re-entry into the repo's own dev shell.

WHY THIS FILE EXISTS. `run-tests.sh` refuses to run without its REQUIRED_TOOLS
and prints the exact `nix develop …` line that fixes it. The message was never
wrong; making a correct instruction be re-typed by hand is the defect. Measured
2026-09-08 across every gate log dir on the box, **101 of 382 runs (26%)** died
on that FATAL with the pytest tier never starting, and 91 of those 101 ran the
node tier anyway — paying that suite twice for nothing.

⚠ AN EARLIER VERSION OF THAT FIGURE SAID "100 of 100", AND IT WAS A POPULATION
DEFINED BY THE FAILURE'S OWN CAUSE. `LOG_DIR` defaults to
`mktemp -d -t devrc-gate-XXXXXX`, so a run INSIDE `nix develop` lands under
nix's per-shell TMPDIR and a run OUTSIDE it lands in bare /tmp. Globbing
`/tmp/devrc-gate-*` therefore selects exactly the runs launched outside the dev
shell — the failing kind, by construction — and 100% of them failing says
nothing beyond that. Counting both locations: outside 101 dirs / 101 FATALs,
inside 281 dirs / 0.

The failure mode of getting the re-exec wrong is a HANG or an unkillable fork
bomb rather than a wrong answer, which is why the three guards below are tested
individually rather than as a set.

🔴 EVERY RE-EXEC TEST POPS PYTEST_CURRENT_TEST. That is not boilerplate: it is
the ONLY remaining short-circuit in front of the three guards, and leaving it
set is how all three of them used to be unreachable. See the GATE_NESTED comment
in gate.sh for the mutation history.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, os.pardir)))
from testlib.mockbin import write_exec  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "gate.sh"

# 🔴 EVERY GATE VARIABLE THE AMBIENT ENVIRONMENT MIGHT CARRY. A test that
# inherits one of these is steered by whoever exported it — an exported
# `DEVRC_GATE_TIMEOUT=5` would kill every tier these tests run and read as a
# gate bug; an exported `DEVRC_GATE_ENV=1` would suppress the very re-exec this
# file is about. A control whose verdict depends on the operator's shell is not
# a control. The list is pinned two-way against gate.sh's own source by
# `test_the_ambient_scrub_list_covers_every_gate_variable`.
_AMBIENT_GATE_VARS = (
    "DEVRC_GATE_TIMEOUT",
    "DEVRC_GATE_NO_REEXEC",
    "DEVRC_GATE_ENV",
    "DEVRC_GATE_REEXEC",
    "DEVRC_GATE_PYTEST_RUNNER",
    "DEVRC_GATE_NODE_RUNNER",
    # Suppresses gate.sh's gate-weakening-variable refusal. Scrubbed like
    # the rest: a test that inherited it from the ambient shell would be
    # driving a gate whose refusal is silently off, which is the one
    # condition none of these tests could then observe.
    "DEVRC_GATE_ALLOW_AMBIENT",
    # 🔴 Read by gate.sh's ambient refusal through INDIRECT expansion
    # (`for _v in … ; [ -n "${!_v+x}" ]`), so the derivation below could not see
    # them and they sat unscrubbed — an exported value rode straight into every
    # re-exec test. The two-way pin reported full coverage the whole time, which
    # is exactly how it survived: the ledger read as coverage while providing
    # none for the two variables the refusal was added to catch.
    "DEVRC_TARGETS",
    "MIN_TESTS",
)


def _fake_runner(path: Path, *, verdict: str = "PASS") -> Path:
    """A stand-in runner that prints a well-formed verdict and exits to match."""
    rc = 0 if verdict == "PASS" else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    # 🔴 write_exec owns the shebang, and that is not style. An env-based
    # interpreter path written at RUNTIME execs on this NixOS dev host and NOT
    # in the nix build sandbox, which is the authoritative tier — so the defect
    # is structurally invisible to the tier most people run. This file arrived
    # carrying it and test_runtime_shebangs.py caught it on the first sandbox
    # run, which is that guard working.
    # 🔴 The `SCOPE:` line is part of the runner contract this fixture models.
    # gate.sh requires to SEE `SCOPE: FULL` before it may print a gate PASS —
    # a POSITIVE control, so a runner saying nothing about its coverage is
    # "cannot vouch" (exit 91) rather than "ran everything". A fixture that
    # omitted it would model a runner that cannot exist, and every re-exec
    # assertion below would be reading a 91 instead of the 0 it expects.
    return write_exec(
        path,
        textwrap.dedent(
            f"""\
            echo "======== FAKE SUMMARY ========"
            echo "SCOPE: FULL (fake runner: models a whole-suite run)"
            echo "RESULT: {verdict} (exit={rc})"
            exit {rc}
            """
        ),
    )


def _fake_nix(path: Path, record: Path) -> Path:
    """A `nix` that records the argv it was handed and does NOT exec it."""
    return write_exec(
        path,
        textwrap.dedent(
            f"""\
            printf '%s\\n' "$@" > {record}
            exit 0
            """
        ),
    )


def _reexec_env(tmp_path: Path, bindir: Path, **over: str) -> dict[str, str]:
    """An env in which the re-exec WOULD fire unless a guard stops it.

    Keeps the runner seams — they are cheap and they no longer suppress the
    re-exec — so a guard that DOES fire leaves the gate running a fake runner in
    milliseconds rather than the real 20,000-test suite.
    """
    env = {k: v for k, v in os.environ.items() if k not in _AMBIENT_GATE_VARS}
    runner = _fake_runner(tmp_path / "fake-runner.sh")
    env["DEVRC_GATE_PYTEST_RUNNER"] = str(runner)
    env["DEVRC_GATE_NODE_RUNNER"] = str(runner)
    # These two ARE gate-weakening variables and gate.sh refuses them; this
    # suite sets them on purpose to drive the re-exec seam. See gate.sh's
    # pre-flight block and its `Env:` entry for this escape.
    env["DEVRC_GATE_ALLOW_AMBIENT"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env.update(over)
    return env


def _run_gate(tmp_path: Path, env: dict[str, str], *args: str, timeout: int = 120):
    return subprocess.run(
        ["bash", str(GATE), "--log-dir", str(tmp_path / "logs"), *args, str(REPO)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def test_it_re_enters_nix_develop_when_not_in_a_gate_environment(tmp_path):
    """The wasted-invocations fix, observed through a recording fake `nix`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)

    env = _reexec_env(tmp_path, bindir)
    proc = subprocess.run(
        ["bash", str(GATE), "--tier", "pytest", "--log-dir", str(tmp_path / "L"), str(REPO)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert record.exists(), (
        "`nix` was never invoked — the gate ran against the ambient PATH "
        f"instead of re-entering the dev shell.\n{proc.stdout}{proc.stderr}"
    )
    argv = record.read_text().split("\n")
    assert argv[0] == "develop", argv
    assert str(REPO) in argv, argv
    # 🔴 The resolved LOG_DIR must be handed through. Replaying the raw argv
    # would let the inner shell re-run mktemp under nix's per-shell TMPDIR and
    # write its logs somewhere the outer run never announced.
    assert "--log-dir" in argv and str(tmp_path / "L") in argv, (
        f"the re-exec did not pass the resolved log dir through: {argv}"
    )


def test_it_does_not_re_exec_when_already_in_a_gate_environment(tmp_path):
    """DEVRC_GATE_ENV=1 is the normal exit condition: the flake's shellHook set it.

    🔴 Reaches its guard: no runner-seam short-circuit stands in front of it any
    more, and PYTEST_CURRENT_TEST is popped. Deleting the DEVRC_GATE_ENV guard
    from gate.sh makes this test fail — before, it did not.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir, DEVRC_GATE_ENV="1")
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "the gate re-entered `nix develop` from inside a gate environment — the "
        f"DEVRC_GATE_ENV guard did not fire.\n{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "re-entering" not in proc.stdout, proc.stdout


def test_the_loop_guard_stops_a_second_re_exec(tmp_path):
    """If the flake's shellHook ever stops setting DEVRC_GATE_ENV, this is what
    keeps the re-exec from being a fork bomb rather than a wrong answer.

    DEVRC_GATE_ENV is deliberately absent here, so the ONLY thing standing
    between this run and a second `nix develop` is DEVRC_GATE_REEXEC — the
    variable gate.sh sets on itself on the way in.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir, DEVRC_GATE_REEXEC="1")
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "re-exec fired despite the loop guard being set — a second pass means "
        f"there is no bound on the recursion at all.\n{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_no_reexec_opt_out_is_honoured(tmp_path):
    """The documented escape hatch: run against the ambient PATH.

    DEVRC_GATE_ENV and DEVRC_GATE_REEXEC are both absent, so this variable is
    the only guard in play.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir, DEVRC_GATE_NO_REEXEC="1")
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "DEVRC_GATE_NO_REEXEC=1 did not stop the re-exec.\n"
        f"{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_limiter_free_gate_does_not_re_exec_inside_pytest(tmp_path):
    """PYTEST_CURRENT_TEST is the blanket suppression, and it must still hold.

    Every other test in this file pops it in order to reach the guards behind
    it; nothing would notice if it stopped working. A nested run that re-entered
    `nix develop` would cost minutes per invocation inside the suite.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    _fake_nix(bindir / "nix", record)
    env = _reexec_env(tmp_path, bindir)
    env["PYTEST_CURRENT_TEST"] = "fake::test_something (call)"
    proc = _run_gate(tmp_path, env, "--tier", "pytest")
    assert not record.exists(), (
        "a run inside a pytest process re-entered `nix develop`.\n"
        f"{proc.stdout}{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_relative_path_invocation_survives_the_re_exec(tmp_path):
    """`cd <repo>/scripts && bash gate.sh` must still produce a verdict.

    `${BASH_SOURCE[0]}` is whatever the caller typed, `cd "$ROOT"` happens
    before the re-exec, and the re-exec used to hand that relative path to the
    inner shell — which resolved it against the NEW cwd and died
    `bash: gate.sh: No such file or directory`, rc=127, with no GATE block, no
    RESULT line and no instruction. Before the re-exec existed the same
    invocation printed a correct, actionable FATAL, so this was a regression
    into a code outside the script's whole documented exit set.

    The fake `nix` here EXECS what it was handed rather than just recording it,
    so the failure reproduces end to end instead of being inferred from an argv.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "argv.txt"
    write_exec(
        bindir / "nix",
        textwrap.dedent(
            f"""\
            printf '%s\\n' "$@" > {record}
            shift 3          # drop: develop <root> --command
            exec "$@"
            """
        ),
    )
    env = _reexec_env(tmp_path, bindir)
    proc = subprocess.run(
        ["bash", "gate.sh", "--tier", "pytest", "--log-dir", str(tmp_path / "L"), str(REPO)],
        cwd=str(REPO / "scripts"),
        capture_output=True, text=True, env=env, timeout=120,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 127, (
        "the re-exec handed a relative path to the inner shell; rc=127 is not "
        f"in this script's documented exit set at all.\n{combined}"
    )
    assert record.exists(), combined
    argv = record.read_text().split("\n")
    handed = argv[4] if len(argv) > 4 else ""
    assert os.path.isabs(handed) and handed.endswith("gate.sh"), (
        f"the re-exec handed the inner shell {handed!r}, not an absolute path: {argv}"
    )
    assert "GATE: RESULT=PASS" in proc.stdout, (
        f"the re-exec'd run produced no verdict.\n{combined}"
    )
    assert proc.returncode == 0, combined


def _env_vars_gate_sh_reads() -> set[str]:
    """Every environment variable gate.sh reads, DERIVED FROM THE SOURCE.

    🔴 A LITERAL LIST HERE IS THE BUG THIS FUNCTION REPLACES. The predecessor of
    the test below was named "...documents every env var the script reads" and
    checked four hardcoded strings, while several MORE that the script reads
    were absent from --help and it was green. A guard whose name says "every"
    must check every, or it reads as coverage while providing none.

    The rule is mechanical: an ALL-CAPS name read through a tolerates-absence
    expansion (`${NAME:-…}`, `${NAME:+…}`, `${NAME-…}`, `${NAME+…}`) BEFORE the
    script ever assigns it. That is what reading an environment variable looks
    like and what reading one of the script's own locals does not —
    `DEVRC_GATE_REEXEC` is read first and exported later, so it stays in, which
    is correct because both halves are real. Comment lines are excluded, so
    documenting a variable cannot satisfy the test that checks it is documented.
    """
    lines = GATE.read_text().splitlines()
    body_start = next(i for i in range(1, len(lines)) if not lines[i].startswith("#"))
    body = "\n".join(l for l in lines[body_start:] if not l.lstrip().startswith("#"))
    first_read: dict[str, int] = {}
    for m in re.finditer(r"\$\{([A-Z][A-Z0-9_]*)(?::-|:\+|:=|:\?|-|\+)", body):
        first_read.setdefault(m.group(1), m.start())
    # 🔴 INDIRECT EXPANSION IS A READ THIS REGEX CANNOT SEE, and the ambient
    # refusal uses exactly that: `for _v in A B C D; do [ -n "${!_v+x}" ]`.
    # The names never appear inside a `${…}`, so the loop above found NONE of
    # them and the two-way pin below reported full coverage while `DEVRC_TARGETS`
    # and `MIN_TESTS` were unscrubbed — the "reads as coverage while providing
    # none" shape this very docstring warns about, reintroduced one indirection
    # deeper. Harvest the loop's word list too.
    for m in re.finditer(r"(?m)^\s*for\s+\w+\s+in\s+([A-Z][A-Z0-9_ \t]*?);?\s*do\b", body):
        for name in m.group(1).split():
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                first_read.setdefault(name, m.start())
    first_assign: dict[str, int] = {}
    for m in re.finditer(r"(?m)^[^\n]*?\b([A-Z][A-Z0-9_]*)=", body):
        first_assign.setdefault(m.group(1), m.start())
    return {n for n, p in first_read.items() if p < first_assign.get(n, 1 << 30)}


def test_the_env_var_derivation_can_actually_see_a_variable():
    """POSITIVE CONTROL for the derivation. A regex that matched nothing would
    make the test below pass over an empty set — the reassuring zero."""
    found = _env_vars_gate_sh_reads()
    assert len(found) >= 5, f"the derivation found only {sorted(found)}"
    for expected in ("DEVRC_GATE_NO_REEXEC", "DEVRC_GATE_TIMEOUT", "PYTEST_CURRENT_TEST"):
        assert expected in found, f"{expected} not derived; found {sorted(found)}"
    # NEGATIVE CONTROL: a name gate.sh assigns before reading is not an env var.
    assert "TIER" not in found, sorted(found)
    assert "LOG_DIR" not in found, sorted(found)


def test_the_help_text_documents_every_env_var_the_script_reads():
    """--help used to be a hardcoded `sed 2,70p` and silently truncated as the
    header grew. A flag documented nowhere the operator looks is a flag that
    does not exist.

    DEVRC_GATE_ENV is the one an operator most needs and the one that was
    missing: the banner says "not in a gate environment (DEVRC_GATE_ENV unset)"
    and --help could not say what that was.
    """
    proc = subprocess.run(
        ["bash", str(GATE), "--help"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0
    undocumented = sorted(v for v in _env_vars_gate_sh_reads() if v not in proc.stdout)
    assert not undocumented, (
        f"gate.sh reads {undocumented} but --help never mentions them. Add each "
        "to the `Env:` block in the header — that block IS the help text."
    )


def test_the_ambient_scrub_list_covers_every_gate_variable():
    """The seam between two files, asserted as a LEDGER rather than a spot check.

    `_reexec_env` scrubs a hardcoded tuple; gate.sh owns the real set. If the
    script grows a variable nobody adds to `_AMBIENT_GATE_VARS`, the next
    exported value silently steers these tests. Fails when the set GROWS or
    SHRINKS, not merely when it disagrees in one direction.

    PYTEST_CURRENT_TEST is deliberately NOT in the tuple: it is pytest's own, it
    is genuinely true while these tests run, and each test decides for itself
    whether to pop it.
    """
    derived = _env_vars_gate_sh_reads() - {"PYTEST_CURRENT_TEST"}
    assert derived == set(_AMBIENT_GATE_VARS), (
        "the scrub list and gate.sh's env vars disagree.\n"
        f"  in gate.sh but not scrubbed: {sorted(derived - set(_AMBIENT_GATE_VARS))}\n"
        f"  scrubbed but not in gate.sh: {sorted(set(_AMBIENT_GATE_VARS) - derived)}"
    )
