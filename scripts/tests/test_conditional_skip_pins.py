"""🔒 Conditional `EXPECTED_SKIPS` entries (`dir|reason|unset:VAR`).

WHY THIS FILE EXISTS. The `EXPECTED_SKIPS` header has always promised that "an
entry may be conditional", while the accounting compared the skipped-TEST total
against a flat entry COUNT. So on a host where a pinned skip legitimately RUNS,
the gate went RED — and the advice it printed ("delete its EXPECTED_SKIPS entry")
is WRONG for a host-conditional pin, because deleting it reds every other host.
Measured: any developer exporting `SIGNAL_PG_DSN` had a permanently-red gate with
no correct way to unbreak it. A permanently-red gate is worse than no gate — it
is what teaches people to pass `DEVRC_SKIP_TESTS=1`.

🔴 THESE TESTS EXECUTE THE SHIPPED CODE. Each one extracts the real block out of
`scripts/run-tests.sh` and runs it under bash with synthetic inputs. That is
deliberate and it is the point: a previous attempt at this area asserted only
that certain STRINGS appeared in the script, and every one of those assertions
was satisfied by the identical string sitting in a COMMENT two lines above the
code — a guard that passed with the code under it mutated. Substring checks
against a shell script are not tests of that script.

🔴 NO `git ls-files` HERE. `nix flake check` builds `checks.pytests` from a
tracked-file copy with NO `.git`; an unguarded `git` call exits 128 and reds the
hermetic tier while the pre-push tier (which has `.git`) stays green. That
two-tier blind spot is documented in `test_doc_path_rot.py` and has bitten this
repo before. This file only reads `run-tests.sh`.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run-tests.sh"
# The one test in this repo that can only run inside a real xdist worker. Its
# pin is a SEAM between two files, so the cases below read both — still no
# `git`, so the hermetic tier is unaffected.
XDIST_ONLY_TEST = REPO / "scripts" / "tests" / "test_gitenv_sibling_exclusion.py"


def _runner_text() -> str:
    return RUNNER.read_text(encoding="utf8")


def _extract(start: str, end: str) -> str:
    """Lift a verbatim block out of run-tests.sh, so the code under test is the
    code that ships. Fails loudly rather than returning an empty string — an
    empty harness would make every assertion below pass vacuously."""
    txt = _runner_text()
    # 🔴 The anchor must be UNIQUE. `for line in "${SKIP_LINES[@]}"; do` occurs
    # twice — the print loop and the matching loop — and an ambiguous anchor
    # silently extracted the wrong block, which is the "executes a stale copy"
    # failure an extraction-based test is most exposed to.
    assert txt.count(start) == 1, (
        f"anchor {start!r} occurs {txt.count(start)} times — not unique")
    i = txt.index(start)
    j = txt.index(end, i)
    block = txt[i:j]
    assert block.strip(), f"extracted an EMPTY block for {start!r}"
    return block


def _ledger_condition_vars() -> set[str]:
    """Every variable named by an `unset:VAR` condition in the real ledger.
    Derived, so a new conditional entry cannot leave a stale hardcoded pop-set
    behind — the failure mode that shipped in the first version of this file."""
    block = _extract("EXPECTED_SKIPS=(", "\n)")
    return set(re.findall(r"\|unset:([A-Za-z_][A-Za-z0-9_]*)", block))


def _bash(script: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    # 🔴 Start from a known state: EVERY variable any case or the real ledger
    # depends on must be genuinely absent unless a case sets it, or an "unset"
    # arm silently tests "set". Derived from the ledger rather than hardcoded —
    # the first version of this popped only the synthetic probe var and left
    # SIGNAL_PG_DSN alone, so `test_the_real_ledger_behaves_the_same_way` FAILED
    # for exactly the developer this whole change exists to unbreak.
    for _v in _ledger_condition_vars() | {"ZZ_PROBE_DSN"}:
        env.pop(_v, None)
    env.update(env_extra or {})
    return subprocess.run(["bash", "-uo", "pipefail", "-c", script],
                          capture_output=True, text=True, env=env)


SPLIT_FN = None


def _split_fn() -> str:
    global SPLIT_FN
    if SPLIT_FN is None:
        SPLIT_FN = _extract("_split_skip_entry() {", "\n}\n") + "\n}\n"
    return SPLIT_FN


APPLIES_FN = None


def _applies_fn() -> str:
    """The shipped `_skip_entry_applies`, verbatim."""
    global APPLIES_FN
    if APPLIES_FN is None:
        APPLIES_FN = _extract("_skip_entry_applies() {", "\n}\n") + "\n}\n"
    return APPLIES_FN


# --------------------------------------------------------------------------
# The parser — including the alternation trap that motivated it.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("entry,want_dir,want_re,want_cond", [
    ("scripts/x/tests|some reason", "scripts/x/tests", "some reason", ""),
    ("scripts/x/tests|some reason|unset:FOO", "scripts/x/tests", "some reason", "unset:FOO"),
    # A reason containing a regex alternation must survive intact.
    ("scripts/x/tests|a or b|unset:FOO", "scripts/x/tests", "a or b", "unset:FOO"),
])
def test_split_parses_two_and_three_field_entries(entry, want_dir, want_re, want_cond):
    r = _bash(_split_fn() + f'''
      _split_skip_entry {entry!r}
      printf '%s\\n%s\\n%s\\n' "$edir" "$ere" "$econd"
    ''')
    assert r.returncode == 0, r.stderr
    got_dir, got_re, got_cond = r.stdout.split("\n")[:3]
    assert (got_dir, got_re, got_cond) == (want_dir, want_re, want_cond)


def test_three_field_entry_does_not_leak_the_condition_into_the_regex():
    """🔴 The trap this parser exists for. `${entry#*|}` takes everything after
    the FIRST `|`, so a 3-field entry would yield `reason|unset:VAR` — and that
    string goes to `grep -qE`, where `|` is ALTERNATION. The matcher would then
    accept any skip whose reason contained EITHER side, silently WIDENING the
    pin. Asserts the reason is exactly the middle field, with no `|` in it."""
    r = _bash(_split_fn() + '''
      _split_skip_entry "scripts/x/tests|needs a real Postgres|unset:SIGNAL_PG_DSN"
      printf '%s' "$ere"
    ''')
    assert r.returncode == 0, r.stderr
    assert r.stdout == "needs a real Postgres"
    assert "|" not in r.stdout, "the condition leaked into the grep -E pattern"


# --------------------------------------------------------------------------
# The counting loop — driven, not grepped. Both arms.
# --------------------------------------------------------------------------

def _count_harness(entries: list[str]) -> str:
    loop = _extract("pin_expected=0", "\nfor line in ")
    decl = "\n".join(f'  {e!r}' for e in entries)
    return textwrap.dedent(f"""
        fail=0
        EXPECTED_SKIPS=(
        {decl}
        )
    """) + _split_fn() + _applies_fn() + loop + '\nprintf "%s %s\\n" "$pin_expected" "$fail"\n'


def test_unconditional_entry_always_counts():
    r = _bash(_count_harness(["scripts/a/tests|plain"]))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["1", "0"]


def test_conditional_entry_counts_only_when_the_var_is_UNSET():
    """Both arms. The forgiving arm alone proves nothing — the whole defect was a
    count that was right in one environment and wrong in the other."""
    harness = _count_harness(["scripts/a/tests|needs pg|unset:ZZ_PROBE_DSN"])

    unset_arm = _bash(harness)
    assert unset_arm.returncode == 0, unset_arm.stderr
    assert unset_arm.stdout.split() == ["1", "0"], "absent VAR must COUNT the pin"

    set_arm = _bash(harness, {"ZZ_PROBE_DSN": "postgres://x"})
    assert set_arm.returncode == 0, set_arm.stderr
    assert set_arm.stdout.split() == ["0", "0"], \
        "VAR set means the test RUNS, so the pin must NOT be counted"


def test_the_real_ledger_behaves_the_same_way():
    """The shipped entries, not a synthetic pair — so a future edit that drops the
    condition from the Postgres pin is caught here and not only in production."""
    block = _extract("EXPECTED_SKIPS=(", "\n)")
    entries = re.findall(r'^\s*"([^"]+)"', block, re.M)
    assert len(entries) >= 2, f"parsed {len(entries)} ledger entries — extraction is broken"
    assert any(e.startswith("scripts/signal/tests|") and e.endswith("|unset:SIGNAL_PG_DSN")
               for e in entries), "the Postgres pin lost its condition"

    harness = _count_harness(entries)
    with_dsn = _bash(harness, {"SIGNAL_PG_DSN": "postgres://x"})
    without = _bash(harness)
    assert without.returncode == 0 and with_dsn.returncode == 0
    n_without = int(without.stdout.split()[0])
    n_with = int(with_dsn.stdout.split()[0])
    assert n_with == n_without - 1, (
        f"exporting SIGNAL_PG_DSN must drop exactly one expected pin "
        f"(got {n_without} -> {n_with}); this is the permanently-red-gate bug")


def test_an_unknown_condition_is_a_HARD_ERROR_not_a_silent_pass():
    """Fail closed. A typo'd condition must not quietly degrade a conditional pin
    into an unconditional one — that would restore the original bug invisibly."""
    r = _bash(_count_harness(["scripts/a/tests|reason|whenever:FOO"]))
    assert r.returncode == 0, r.stderr
    _, fail = r.stdout.split()
    assert fail == "1", "an unrecognised condition must set fail=1"
    assert "unknown condition" in r.stderr.lower()


# --------------------------------------------------------------------------
# THE COMPARISON ITSELF — the line the whole change turns on.
# --------------------------------------------------------------------------

def _verdict_harness(entries: list[str], tot_skipped: int,
                     skip_lines: list[str] | None = None) -> str:
    """ONE CONTIGUOUS slice of the shipped gate: the counting loop, the matching
    loop, the unpinned-skip report, and the comparison — driven with synthetic
    `SKIP_LINES` and `TOT_SKIPPED`.

    🔴 WHY CONTIGUOUS, and why that word is load-bearing. Two earlier versions of
    this harness were BOTH defeated by the same shape:

      * v1 extracted only up to `for line in `, stopping BEFORE the comparison —
        so it pinned how `pin_expected` is COMPUTED and never that the gate
        BRANCHES on it. Reverting the comparison (deleting the whole fix) left
        the suite green.
      * v2 stitched the count loop to the comparison across a GAP, skipping the
        matching loop and the report block. Injecting `pin_expected=${#EXPECTED_SKIPS[@]}`
        INTO that gap — the same revert, different placement — survived green.

    A harness assembled from disjoint slices cannot see anything in the seams,
    and the seams are where a revert hides. Taking one slice removes the class
    rather than the instance.
    """
    block = _extract("pin_expected=0\nfor entry in", "\n# --- GUARD 7")
    decl = "\n".join(f'  {e!r}' for e in entries)
    lines = "\n".join(f'  {l!r}' for l in (skip_lines or []))
    return (
        f"fail=0\nTOT_SKIPPED={tot_skipped}\nunexpected=()\n"
        f"SKIP_LINES=(\n{lines}\n)\n"
        f"EXPECTED_SKIPS=(\n{decl}\n)\n"
        + _split_fn() + _applies_fn() + block
        + '\nprintf "fail=%s pin_expected=%s unexpected=%s\\n" "$fail" "$pin_expected" "${#unexpected[@]}"\n'
    )


def test_comparison_is_GREEN_when_the_total_matches_applicable_pins():
    """DSN set: the pinned test RUNS, so one skip and one applicable pin."""
    h = _verdict_harness(["scripts/signal/tests|needs a real Postgres|unset:ZZ_PROBE_DSN"], 0)
    r = _bash(h, {"ZZ_PROBE_DSN": "postgres://x"})
    assert r.returncode == 0, r.stderr
    assert "fail=0" in r.stdout, f"expected green, got {r.stdout!r} {r.stderr!r}"


def test_comparison_is_RED_when_the_total_exceeds_applicable_pins():
    """The variable is set (pin does not apply) but a skip happened anyway —
    the discrepancy the gate exists to surface."""
    h = _verdict_harness(["scripts/signal/tests|needs a real Postgres|unset:ZZ_PROBE_DSN"], 1)
    r = _bash(h, {"ZZ_PROBE_DSN": "postgres://x"})
    assert "fail=1" in r.stdout, f"expected red, got {r.stdout!r}"
    assert "MORE than" in r.stderr


def test_comparison_USES_pin_expected_not_the_raw_entry_count():
    """🔴 THE ANTI-REVERT TEST. Reverting the comparison to
    `${#EXPECTED_SKIPS[@]}` deletes the fix; this case distinguishes the two,
    because the raw count (1) and the applicable count (0) differ.

    With the var SET the pin does not apply, so `pin_expected` is 0 and a
    TOT_SKIPPED of 0 must be GREEN. Under the reverted comparison the raw entry
    count is 1, so 0 != 1 and it goes RED. Any harness where the two counts are
    equal cannot tell the versions apart — which is exactly how the reverted
    mutant survived before.
    """
    h = _verdict_harness(["scripts/a/tests|reason|unset:ZZ_PROBE_DSN"], 0)
    r = _bash(h, {"ZZ_PROBE_DSN": "set"})
    assert "pin_expected=0" in r.stdout, r.stdout
    assert "fail=0" in r.stdout, (
        "the comparison is reading the raw entry count, not pin_expected — "
        f"the fix is reverted or bypassed. stdout={r.stdout!r} stderr={r.stderr!r}")


def test_a_non_applicable_pin_does_not_FORGIVE_a_skip():
    """🔴 The two halves must agree. A pin whose condition does not hold here
    must not absorb a skip in the matching loop while being excluded from the
    count — that combination balanced the totals and hid a real discrepancy."""
    entries = ["scripts/a/tests|reason here|unset:ZZ_PROBE_DSN"]
    lines = ["SKIPPED [1] scripts/a/tests/t.py:9: reason here"]

    applies = _bash(_verdict_harness(entries, 1, lines))
    assert "unexpected=0" in applies.stdout, f"applicable pin must forgive: {applies.stdout!r}"

    not_applies = _bash(_verdict_harness(entries, 1, lines), {"ZZ_PROBE_DSN": "set"})
    assert "unexpected=1" in not_applies.stdout, (
        "a pin whose condition does NOT hold here still forgave the skip; the "
        f"matching loop is condition-blind. stdout={not_applies.stdout!r}")


def test_a_malformed_unset_tail_is_a_hard_error_and_does_NOT_abort_the_loop():
    """🔴 The `unset:*` arm is reached by a MALFORMED tail too — `unset:FOO|typo`
    from a 4-field entry, or a bare `unset:`. Before validation, `${!_v-}` raised
    bash's `invalid variable name`, and that error ABORTED the enclosing loop:
    every later entry went unevaluated, `pin_expected` was truncated, and `fail`
    stayed 0 — a silent widening.

    Asserts BOTH halves, because either alone is satisfiable by the broken code:
    `fail=1` (it was 0), AND that the entry AFTER the bad one was still counted
    (it was not). The previously-covered case (`whenever:FOO`) takes the `*)` arm
    and never exercised this path — the likelier typo shape was the untested one.
    """
    for bad in ("unset:FOO|typo", "unset:", "unset:1BAD"):
        h = _verdict_harness([f"scripts/a/tests|r1|{bad}", "scripts/b/tests|r2"], 1)
        r = _bash(h)
        assert "fail=1" in r.stdout, f"{bad!r} must hard-error, got {r.stdout!r}"
        assert "pin_expected=1" in r.stdout, (
            f"{bad!r} aborted the loop — the entry after it was never counted. "
            f"stdout={r.stdout!r} stderr={r.stderr!r}")
        assert "invalid variable" in r.stderr.lower(), r.stderr


# --------------------------------------------------------------------------
# THE XDIST-ONLY POSITIVE CONTROL, and why its pin needs the third field.
#
# 🔴 WHAT WENT WRONG. #841 introduced BOTH the `-n` parallelism and
# `test_gitenv_sibling_exclusion.py::test_a_real_worker_reports_a_run_id`, which
# is `skipif`-ed outside a real xdist worker. The ledger got no entry, so
# `DEVRC_TEST_JOBS=1` — the serial mode this runner itself recommends for a
# bisect or a flake hunt, and the documented workaround for #841's own race —
# exited 1 on GUARD 2 alone: one UNPINNED skip group plus `3 skipped vs 2
# pinned`. A permanently-red gate is worse than no gate.
#
# 🔴 WHY A FLAT PIN IS NOT THE FIX, and why the condition variable is NOT
# `PYTEST_XDIST_WORKER`. The ledger is evaluated in the RUNNER's shell. xdist
# sets PYTEST_XDIST_WORKER inside the worker PROCESSES it spawns, never in the
# process that spawned pytest — so that variable is unset there in BOTH modes,
# and a pin keyed to it applies always, i.e. a flat pin in disguise. Either
# shape reds the PARALLEL mode instead, where the test legitimately RUNS. The
# runner therefore publishes `DEVRC_XDIST_ACTIVE` beside `PYTEST_PARALLEL_ARGS`,
# and the last case below pins that flag to the `-n` it is supposed to track —
# without it, every case here would be satisfied by a condition naming a
# variable nothing ever sets.
# --------------------------------------------------------------------------

def _ledger_entries() -> list[str]:
    block = _extract("EXPECTED_SKIPS=(", "\n)")
    entries = re.findall(r'^\s*"([^"]+)"', block, re.M)
    assert len(entries) >= 2, f"parsed {len(entries)} ledger entries — extraction is broken"
    return entries


def _xdist_only_skip_reason() -> str:
    """The literal `reason=` of the xdist-only skip, read from the TEST FILE.

    Read rather than restated, so a reword there cannot silently un-pin the
    skip: the ledger's regex is exercised against the real string below.
    """
    src = XDIST_ONLY_TEST.read_text(encoding="utf8")
    reasons = re.findall(r'reason="([^"]+)"', src)
    assert len(reasons) == 1, (
        f"expected exactly one skipif reason in {XDIST_ONLY_TEST.name}, "
        f"found {len(reasons)}: {reasons!r} — this fixture no longer knows "
        f"which skip it is pinning")
    return reasons[0]


def _xdist_only_skip_line() -> str:
    """A `-rs` line in pytest's exact shape for that skip."""
    rel = XDIST_ONLY_TEST.relative_to(REPO).as_posix()
    return f"SKIPPED [1] {rel}:1: {_xdist_only_skip_reason()}"


def _xdist_pin() -> tuple[str, str]:
    """`(entry, condition-variable)` for the ledger pin that covers that skip.

    Everything is DERIVED — the owning directory from the test's own path, the
    entry by actually running the ledger's regex against the test's own reason,
    and the variable out of the entry. Renaming the flag or moving the file
    keeps these cases honest; losing the pin, or its third field, fails here
    with a named reason instead of somewhere downstream.
    """
    rel = XDIST_ONLY_TEST.relative_to(REPO).as_posix()
    owning_dir = rel.rsplit("/", 1)[0]
    reason = _xdist_only_skip_reason()

    hits = []
    for entry in _ledger_entries():
        fields = entry.split("|")
        if fields[0] != owning_dir:
            continue
        if re.search(fields[1], reason):
            hits.append(entry)
    assert len(hits) == 1, (
        f"expected exactly ONE EXPECTED_SKIPS entry matching {owning_dir!r} + "
        f"{reason!r}, found {len(hits)}: {hits!r}. With none, "
        f"`DEVRC_TEST_JOBS=1 scripts/run-tests.sh` exits 1 on GUARD 2's "
        f"unpinned-skip guard — the permanently-red serial gate.")

    fields = hits[0].split("|")
    assert len(fields) == 3, (
        f"the xdist-only pin {hits[0]!r} is FLAT. It must be conditional "
        f"(`|unset:VAR`): under `-n` the test RUNS, the skip total drops, and a "
        f"flat entry count reds the default parallel mode instead — the same "
        f"defect the SIGNAL_PG_DSN pin was given a third field to fix.")
    assert fields[2].startswith("unset:"), f"unsupported condition {fields[2]!r}"
    var = fields[2][len("unset:"):]
    assert var != "PYTEST_XDIST_WORKER", (
        "the pin is conditional on xdist's OWN variable, which is set only "
        "inside worker processes and never in the runner's shell — so it "
        "applies in both modes and is a flat pin in disguise")
    return hits[0], var


def test_the_xdist_only_control_is_forgiven_SERIAL_and_not_forgiven_PARALLEL():
    """🔴 The behaviour, both arms, through the REAL ledger.

    The forgiving arm alone would be satisfied by a flat pin, and the flat pin
    is the version that reds the mode the merge gate actually runs.
    """
    entries = _ledger_entries()
    _, var = _xdist_pin()
    lines = [_xdist_only_skip_line()]

    serial = _bash(_verdict_harness(entries, 1, lines))
    assert serial.returncode == 0, serial.stderr
    assert "unexpected=0" in serial.stdout, (
        "the serial-mode skip is UNPINNED — this is the red gate. "
        f"stdout={serial.stdout!r} stderr={serial.stderr!r}")

    parallel = _bash(_verdict_harness(entries, 1, lines), {var: "1"})
    assert "unexpected=1" in parallel.stdout, (
        f"with {var} set the test RUNS, so nothing may forgive that skip line; "
        f"the pin is behaving as a flat pin. stdout={parallel.stdout!r}")


def test_going_parallel_drops_exactly_one_expected_pin():
    """The counting half of the same claim, on the shipped entries.

    Mirrors `test_the_real_ledger_behaves_the_same_way` for the other
    conditional pin: the number of applicable pins must FALL by one when the
    runner goes parallel, because the xdist-only control then runs.
    """
    entries = _ledger_entries()
    _, var = _xdist_pin()
    harness = _count_harness(entries)

    serial = _bash(harness)
    parallel = _bash(harness, {var: "1"})
    assert serial.returncode == 0 and parallel.returncode == 0, \
        (serial.stderr, parallel.stderr)
    n_serial = int(serial.stdout.split()[0])
    n_parallel = int(parallel.stdout.split()[0])
    assert n_parallel == n_serial - 1, (
        f"setting {var} must drop exactly one expected pin (got {n_serial} -> "
        f"{n_parallel}); equal counts mean the pin is flat and the PARALLEL "
        f"gate goes red")


def test_the_runner_SETS_that_flag_exactly_when_it_passes_dash_n():
    """🔴 REACHABILITY. Every case above drives the condition variable by hand,
    so all of them stay green if the runner never sets it — the pin would then
    apply in both modes and the parallel gate would be red, unobserved.

    So this drives the shipped parallelism block itself and pins the
    RELATIONSHIP: the flag is non-empty exactly when `-n` is in the pytest
    argv. Both arms, because either alone is satisfiable by a constant.
    """
    _, var = _xdist_pin()
    block = _extract("PYTEST_PARALLEL_ARGS=()", "\n# Every other lever")
    probe = (f'\nprintf "flag=[%s] args=[%s]\\n" "${{{var}-}}" '
             f'"${{PYTEST_PARALLEL_ARGS[*]-}}"\n')

    serial = _bash(f"PYTEST_JOBS=1\n{block}{probe}")
    assert serial.returncode == 0, serial.stderr
    assert serial.stdout.strip() == "flag=[] args=[]", (
        f"serial: {var} must be empty and no -n passed, got {serial.stdout!r}")

    parallel = _bash(f"PYTEST_JOBS=4\n{block}{probe}")
    assert parallel.returncode == 0, parallel.stderr
    assert parallel.stdout.strip() == "flag=[1] args=[-n 4 --dist loadfile]", (
        f"parallel: {var} must be set alongside the -n argv, got "
        f"{parallel.stdout!r}. If the argv moved but the flag did not, the "
        f"pin's condition no longer tracks the mode it names.")


def test_an_ambient_value_cannot_forge_the_flag():
    """🔴 THE `=""` RESET, WHICH NOTHING ELSE HERE CAN SEE.

    Every other case drives the condition variable through `_bash`, and `_bash`
    POPS every ledger condition var from the environment before each run — so
    no other case can ever observe an ambient value, and deleting the reset
    line survived the entire suite. Measured consequence of that deletion: a
    SERIAL run with the flag exported gave `fail=1 pin_expected=2
    unexpected=1`, i.e. precisely the permanently-red gate this ledger entry
    exists to remove, reached from a caller's environment rather than a code
    change.

    So this case deliberately puts the value BACK, via `env_extra` (applied
    after the pop), and requires the block to clear it.

    It also pins the second half of that line's promise — the flag must not be
    EXPORTED. Bash keeps the export attribute across re-assignment, so a name
    the ambient environment already exported stays exported through a plain
    `VAR=""`; without `export -n` the flag would reach pytest and its workers
    on exactly the invocations where a caller happens to export it, and not
    otherwise. Nothing branches on it today, which is the whole reason to fix
    it now rather than after something does.
    """
    _, var = _xdist_pin()
    block = _extract("PYTEST_PARALLEL_ARGS=()", "\n# Every other lever")
    probe = (f'\nprintf "flag=[%s] exported=%s\\n" "${{{var}-}}" '
             f'"$(env | grep -c \'^{var}=\' || true)"\n')

    forged = _bash(f"PYTEST_JOBS=1\n{block}{probe}", {var: "1"})
    assert forged.returncode == 0, forged.stderr
    flag, exported = forged.stdout.strip().split()
    assert flag == "flag=[]", (
        f"an ambient {var}=1 survived into a SERIAL run ({flag}): the pin then "
        f"does not apply, the skip is unforgiven, and the gate is red again — "
        f"the `{var}=\"\"` reset is gone or no longer unconditional")
    assert exported == "exported=0", (
        f"{var} is EXPORTED ({exported}) — bash keeps the export attribute "
        f"across re-assignment, so `export -n` is what makes the "
        f"'plain shell variable, NOT exported' comment true when a caller "
        f"already exported the name")
