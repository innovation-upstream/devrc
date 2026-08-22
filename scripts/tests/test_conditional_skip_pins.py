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
    i = txt.index(start)
    j = txt.index(end, i)
    block = txt[i:j]
    assert block.strip(), f"extracted an EMPTY block for {start!r}"
    return block


def _bash(script: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    # Start from a known state: the variable under test must be genuinely absent
    # unless a case sets it, or "unset" cases would silently test "set".
    env.pop("ZZ_PROBE_DSN", None)
    env.update(env_extra or {})
    return subprocess.run(["bash", "-uo", "pipefail", "-c", script],
                          capture_output=True, text=True, env=env)


SPLIT_FN = None


def _split_fn() -> str:
    global SPLIT_FN
    if SPLIT_FN is None:
        SPLIT_FN = _extract("_split_skip_entry() {", "\n}\n") + "\n}\n"
    return SPLIT_FN


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
    """) + _split_fn() + loop + '\nprintf "%s %s\\n" "$pin_expected" "$fail"\n'


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
