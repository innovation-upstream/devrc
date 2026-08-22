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
