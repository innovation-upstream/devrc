"""🔒 The `requires-env[VAR]` self-pinning skip contract.

WHY THIS FILE EXISTS — measured, not hypothetical. `EXPECTED_SKIPS` in
`scripts/run-tests.sh` is a hand-maintained ledger in ONE file pinning skips
declared in ANOTHER. Keeping the two in step is prose discipline, and it failed
twice, leaving `main` RED both times:

  * #332 (2026-08-04 -> 08-06, TWO DAYS) — tests added without the entry;
  * #657 (2026-08-21) — a Postgres test added without the entry; found only
    because an unrelated push was blocked by it.

`run-tests.sh` already stated the correct rule above `EXPECTED_SKIPS` — "its
condition must be the SAME predicate the test itself uses" — and had no way to
enforce it. `requires-env[VAR]` does: the skip carries its own condition, so
there is no second place to keep in step.

🔴 WHAT THIS ASSERTS THAT THE RUNNER CANNOT. The runner reads the VAR out of the
skip REASON. It never sees the `skipif` CONDITION, so a test whose reason says
`requires-env[FOO]` while its condition tests `BAR` is invisible to it — forgiven
on a host lacking FOO, and never checked against BAR. That mismatch is exactly the
class this mechanism exists to remove, so it is pinned HERE, by reading both out
of the source. A guard whose description claims a RELATIONSHIP must inspect BOTH
sides; inspecting one and reading as coverage is worse than nothing.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "run-tests.sh"

# `requires-env[VAR]` — the VAR must be a shell/py identifier, because the runner
# expands it indirectly (`${!evar-}`); anything else would be a shell injection
# surface, not just a typo.
REASON_RE = re.compile(r"requires-env\[([A-Za-z_][A-Za-z0-9_]*)\]")


def _tracked_test_files() -> list[Path]:
    """Tracked `.py` under scripts/. `git ls-files` (not rglob) so an untracked
    scratch file cannot fail the suite, and a DELETED file cannot linger."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", "scripts/*.py", "scripts/**/*.py"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [REPO / p for p in out.split("\0") if p.endswith(".py")]


def _skipif_decorators(tree: ast.AST):
    """Yield every `pytest.mark.skipif(...)` Call node in the module."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "skipif":
            yield node


def _reason_text(call: ast.Call) -> str:
    """The literal `reason=` string, concatenations included. Returns '' when the
    reason is computed (an f-string / name), which this contract does not cover."""
    for kw in call.keywords:
        if kw.arg != "reason":
            continue
        try:
            v = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError):
            return ""
        return v if isinstance(v, str) else ""
    return ""


def _env_names_in_condition(call: ast.Call) -> set[str]:
    """Every literal env-var name the CONDITION mentions:
    `os.environ.get("X")`, `os.environ["X"]`, `os.getenv("X")`."""
    names: set[str] = set()
    if not call.args:
        return names
    for node in ast.walk(call.args[0]):
        if isinstance(node, ast.Call):
            fn = node.func
            attr = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if attr in ("get", "getenv") and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    names.add(node.args[0].value)
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                names.add(sl.value)
    return names


def _declared_skipifs():
    """(path, var_in_reason, vars_in_condition) for every skipif whose reason
    declares `requires-env[VAR]`."""
    out = []
    for f in _tracked_test_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for call in _skipif_decorators(tree):
            m = REASON_RE.search(_reason_text(call))
            if m:
                out.append((f.relative_to(REPO), m.group(1), _env_names_in_condition(call)))
    return out


# --------------------------------------------------------------------------
# POSITIVE CONTROLS — the extractors must FIND things, or every assertion below
# passes vacuously over an empty set. A reassuring zero is indistinguishable
# from a parser wired to nothing.
# --------------------------------------------------------------------------

def test_positive_control_the_scan_sees_real_test_files():
    files = _tracked_test_files()
    assert len(files) > 200, f"only {len(files)} tracked .py under scripts/ — scan is broken"


def test_positive_control_the_condition_extractor_finds_env_names():
    src = 'import os, pytest\n@pytest.mark.skipif(not os.environ.get("ZZ_A"), reason="r")\ndef t(): pass\n'
    call = next(_skipif_decorators(ast.parse(src)))
    assert _env_names_in_condition(call) == {"ZZ_A"}
    src2 = 'import os, pytest\n@pytest.mark.skipif(not os.environ["ZZ_B"], reason="r")\ndef t(): pass\n'
    call2 = next(_skipif_decorators(ast.parse(src2)))
    assert _env_names_in_condition(call2) == {"ZZ_B"}


def test_positive_control_the_reason_regex_matches_and_rejects():
    assert REASON_RE.search("requires-env[SIGNAL_PG_DSN]: why").group(1) == "SIGNAL_PG_DSN"
    assert REASON_RE.search("requires-env[bad-name]: why") is None
    assert REASON_RE.search("needs a real Postgres") is None


# --------------------------------------------------------------------------
# THE RELATIONSHIP — the half the runner is structurally blind to.
# --------------------------------------------------------------------------

def test_every_declared_var_is_the_one_the_condition_tests():
    """🔴 The reason's VAR must be a variable the CONDITION actually reads.

    The runner forgives the skip when that VAR is absent. If the condition keys
    on something else, the runner forgives a skip whose real cause it never
    checked — a pin that reads as coverage while providing none.
    """
    declared = _declared_skipifs()
    assert declared, "no requires-env[VAR] skips found — the migration regressed"
    bad = [
        f"{p}: reason declares {var!r} but the condition reads {sorted(cond) or 'nothing literal'}"
        for p, var, cond in declared
        if var not in cond
    ]
    assert not bad, "reason/condition mismatch:\n  " + "\n  ".join(bad)


def test_the_migrated_postgres_skip_is_declared_and_unpinned():
    """The #657 skip specifically: self-pinned in the test, and NOT also sitting
    in EXPECTED_SKIPS. Both halves — a skip counted twice would make the runner's
    total equality wrong in the quiet direction."""
    declared = {str(p): var for p, var, _ in _declared_skipifs()}
    assert declared.get("scripts/signal/tests/test_pg_type_compat.py") == "SIGNAL_PG_DSN"

    runner = RUNNER.read_text(encoding="utf8")
    block = runner.split("EXPECTED_SKIPS=(", 1)[1].split("\n)", 1)[0]
    entries = [ln.strip() for ln in block.splitlines()
               if ln.strip().startswith('"') and "|" in ln]
    assert entries, "EXPECTED_SKIPS parsed as empty — this test is not reading the ledger"
    assert not [e for e in entries if "signal" in e], \
        f"the Postgres skip is self-pinned AND still in EXPECTED_SKIPS: {entries}"


def test_the_value_predicate_skip_stays_on_the_ledger():
    """repo-cos keys on `== "1"`, not is-set, so it must NOT adopt requires-env —
    exporting the var as `0` would leave it non-empty while the test still skips,
    and the runner would call that a collapse. Pins the boundary, not just today's
    contents."""
    runner = RUNNER.read_text(encoding="utf8")
    block = runner.split("EXPECTED_SKIPS=(", 1)[1].split("\n)", 1)[0]
    assert "repo-cos" in block, "the value-predicate pin vanished from EXPECTED_SKIPS"
    declared = {str(p) for p, _, _ in _declared_skipifs()}
    assert "scripts/repo-cos/tests/test_routing.py" not in declared, \
        "repo-cos adopted requires-env; its `== '1'` predicate does not fit the is-set contract"


# --------------------------------------------------------------------------
# THE RUNNER'S OWN WIRING — that it reads the VAR indirectly, not literally.
# --------------------------------------------------------------------------

def test_runner_expands_the_var_indirectly():
    """`${!evar-}` (the value of the variable NAMED by $evar), never `$evar` —
    which is the NAME, always non-empty, and would score every declared skip as
    a collapse. A one-character slip that fails in the reassuring direction."""
    runner = RUNNER.read_text(encoding="utf8")
    assert '${!evar-}' in runner, "runner lost the indirect expansion of the declared VAR"


@pytest.mark.parametrize("needle", [
    "declared-env skip(s) SKIPPED WITH THE VARIABLE SET",
    "self-pinned via requires-env[VAR]",
])
def test_runner_reports_both_new_outcomes(needle):
    """The collapse error and the widened accounting line both have to exist —
    the mechanism's whole value is the FAILURE it can now express."""
    assert needle in RUNNER.read_text(encoding="utf8")


# --------------------------------------------------------------------------
# GROUPED SKIPS — the defect this suite originally MISSED.
# --------------------------------------------------------------------------

def test_runner_counts_tests_not_skip_groups():
    """🔴 `SKIPPED [N] …` is N TESTS sharing one reason, on ONE line.

    `TOT_SKIPPED` is summed from pytest's SUMMARY and counts TESTS. Counting
    LINES here and comparing the two is only accidentally correct while every N
    is 1 — and both skips in this repo happen to be `[1]`, so the original
    both-arms verification could not see it. It breaks on the first class-level
    or parametrized `requires-env` decorator, which is the normal way to declare
    an env requirement, and it breaks as a FALSE RED that blocks pushes.

    Pins the extractor AND its use, because a helper that is never called is a
    guard that does not exist.
    """
    runner = RUNNER.read_text(encoding="utf8")
    assert "_env_skip_count()" in runner, "the group-size extractor is gone"
    assert "env_pinned + ecount" in runner, \
        "env_pinned increments by a constant again — grouped skips will false-red"
    assert "env_pinned + 1 ))" not in runner, \
        "a per-line increment survives; TOT_SKIPPED counts tests, not lines"


@pytest.mark.parametrize("line,expected", [
    ("SKIPPED [1] scripts/x/tests/t.py:9: requires-env[FOO]: why", "1"),
    ("SKIPPED [5] scripts/x/tests/t.py:9: requires-env[FOO]: why", "5"),
    ("SKIPPED [12] scripts/x/tests/t.py:9: requires-env[FOO]: why", "12"),
])
def test_group_size_regex_reads_n(line, expected):
    """The sed the runner uses, pinned here so a rewrite cannot quietly change
    which number it reads. Anchored at `^SKIPPED [` so a reason that merely
    CONTAINS a bracketed number cannot be mistaken for the group size."""
    m = re.match(r"^SKIPPED \[([0-9]+)\]", line)
    assert m and m.group(1) == expected
