"""Guards on the mutation battery itself. Mutates NOTHING — safe in the gate.

The battery in `mutation_battery.py` is a manual tool: it edits files in place,
so it must never run inside the suite. But a battery nobody runs for six weeks
rots invisibly, and it rots in the one direction that is indistinguishable from
success — an anchor stops matching, the mutant never lands, and the run reports
`ANCHOR-MISS`, which reads like a hiccup rather than "this mutant tested
nothing". That already happened once here: a mutant's anchor also matched an
unrelated branch, so it reported ANCHOR-MISS and the site it named went
unmutated while the summary looked busy.

So these tests pin the FRAGILE half — the anchors and the killer-test names —
without executing a single mutation. A refactor that moves the guarded code goes
red HERE, in the ordinary gate, instead of silently disarming the battery for
whoever runs it next.

🔴 What these tests do NOT claim: nothing here says the mutants are killed. That
requires actually running the battery, which requires mutating files. Read the
distinction before treating a green gate as "mutation-tested".
"""
from __future__ import annotations

import inspect
import re
import signal as _signal
import subprocess

import pytest

import mutation_battery as battery


def test_the_ledger_is_not_empty():
    """HARNESS CHECK: an empty ledger would make every assertion below vacuous."""
    assert len(battery.MUTANTS) >= 15, (
        f"only {len(battery.MUTANTS)} mutants — the ledger has shrunk; mutants are "
        "removed only when the code they break is gone, and that should be argued "
        "in the commit rather than done quietly")


def test_every_mutant_id_is_unique():
    """`--only A1` must select exactly one mutant."""
    ids = [m.id for m in battery.MUTANTS]
    assert len(ids) == len(set(ids)), f"duplicate mutant ids: {sorted(ids)}"


@pytest.mark.parametrize("mutant", battery.MUTANTS, ids=lambda m: m.id)
def test_every_anchor_matches_EXACTLY_ONCE_in_its_file(mutant):
    """🔴 The failure this exists for is silent, not loud.

    0 matches -> the mutant never lands and the run says ANCHOR-MISS, which is
    neither a kill nor a survival: it is the battery testing nothing while
    printing a line that looks like work.
    2+ matches -> `str.replace(..., 1)` hits whichever occurrence comes first,
    which is not the site the mutant's description names. The battery then
    reports a kill for a mutation it did not make.
    """
    text = (battery.REPO / mutant.path).read_text(encoding="utf-8")
    hits = text.count(mutant.old)
    assert hits == 1, (
        f"mutant {mutant.id}'s anchor matches {hits}x in {mutant.path} (need exactly 1).\n"
        f"  the code it targets has moved or been duplicated; re-anchor the mutant.\n"
        f"  anchor: {mutant.old!r}")


@pytest.mark.parametrize("mutant", battery.MUTANTS, ids=lambda m: m.id)
def test_every_mutant_actually_CHANGES_the_source(mutant):
    """An `old == new` mutant is an equivalent mutant that can never be killed."""
    assert mutant.old != mutant.new, f"mutant {mutant.id} is a no-op"


@pytest.mark.parametrize("mutant", battery.MUTANTS, ids=lambda m: m.id)
def test_an_EQUIVALENT_mutant_ARGUES_for_itself(mutant):
    """🔴 `equivalent=True` flips a SURVIVED from a finding into a pass.

    That is exactly the switch somebody reaches for when a mutant will not die
    and the deadline is close — "call it equivalent and move on" — and it is
    indistinguishable, in the summary line, from a genuinely unkillable
    mutation. So the flag has to cost something: the row must say the word in
    its own `why`, where a reader and a reviewer will see the ARGUMENT next to
    the claim rather than a bare boolean buried in the call.

    Not a proof of equivalence — nothing static can be — but it makes an
    unargued one impossible to add quietly.
    """
    if not mutant.equivalent:
        assert mutant.expected == "KILLED"
        return
    assert "EQUIVALENT" in mutant.why.upper(), (
        f"mutant {mutant.id} is flagged equivalent but its description does not "
        f"say so, let alone argue it: {mutant.why!r}")
    assert mutant.expected == "SURVIVED"


def test_an_equivalent_mutant_that_gets_KILLED_is_a_FAILURE_not_a_pass():
    """Behavioural, on the pure comparison the runner uses.

    A killed "equivalent" mutant means the equivalence argument was WRONG — the
    two forms are distinguishable after all — which is a finding about the
    ledger, not a success. Pinned here because the runner's own summary line
    would otherwise read `0/0 killed` and look like a clean run.
    """
    equiv = battery.Mutant("X", "EQUIVALENT: argued", "p", "a", "b", "k", "s",
                           equivalent=True)
    plain = battery.Mutant("Y", "a real mutant", "p", "a", "b", "k", "s")
    assert equiv.expected == "SURVIVED" and plain.expected == "KILLED"
    # The runner's failure set is `verdict != m.expected`, so the four cells:
    assert ("SURVIVED" != equiv.expected) is False   # equivalent survives -> pass
    assert ("KILLED" != equiv.expected) is True      # equivalent killed   -> FAIL
    assert ("KILLED" != plain.expected) is False     # real killed         -> pass
    assert ("SURVIVED" != plain.expected) is True    # real survived       -> FAIL


@pytest.mark.parametrize("mutant", battery.MUTANTS, ids=lambda m: m.id)
def test_every_named_killer_test_EXISTS(mutant):
    """A killer that does not exist can never fire — so the mutant can never die.

    The battery would report SURVIVED (the suite stays green because the named
    test is absent, not because the code is fine), which inverts the meaning of
    its own output.

    🔴 SCOPE: this checks the killer EXISTS, not that it RUNS. An audit walked it
    by adding `@pytest.mark.skip` to a killer — still collected, still greps as
    `def <name>`, 66 tests green, and its mutant would have been scored SURVIVED.
    Existence is all a static check can see. "It actually ran and passed" is
    enforced at RUNTIME instead, in the runner's baseline phase, which refuses to
    start when any named killer did not PASS. Two checks, different questions;
    neither substitutes for the other.
    """
    suite = (battery.REPO / mutant.suite)
    if suite.is_dir():
        sources = "\n".join(p.read_text(encoding="utf-8")
                            for p in sorted(suite.glob("test_*.py")))
    else:
        sources = suite.read_text(encoding="utf-8")
    assert re.search(rf"^def {re.escape(mutant.killer)}\b", sources, re.MULTILINE), (
        f"mutant {mutant.id} names killer {mutant.killer!r}, which does not exist in "
        f"{mutant.suite}. A missing killer makes the mutant unkillable and the "
        f"battery would score it SURVIVED for the wrong reason.")


# --------------------------------------------------------------------------- #
# 🔴 The three guards below were SPELLED before an audit walked all three.
#
# Each was a substring check over `inspect.getsource(...)`: `"--porcelain" in
# src`, `"PYTHONDONTWRITEBYTECODE" in src`, `"m.killer in failures" in src`. All
# three passed a fully green 66-test suite against a runner whose BEHAVIOUR had
# been removed while the WORDS stayed — `if dirty:` → `if False:`,
# `="1"` → `="0"`, `elif m.killer in failures or True:`. That is the exact
# shape the rules name: a guard on words is walkable by rewording.
#
# These replacements exercise the behaviour and still mutate nothing: a
# throwaway git repo for the refusal, an injected `subprocess.run` for the env,
# and a pure function for the verdict.
# --------------------------------------------------------------------------- #
def test_the_runner_REFUSES_a_dirty_tree(tmp_path, monkeypatch):
    """Behavioural: point it at a dirty throwaway repo and require exit 2."""
    repo = tmp_path / "repo"
    (repo / "scripts" / "signal" / "tests").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "UNCOMMITTED.txt").write_text("someone else's work\n", encoding="utf-8")

    monkeypatch.setattr(battery, "REPO", repo)
    monkeypatch.setattr(battery, "_run", lambda *a, **k: pytest.fail(
        "the runner reached the test phase on a DIRTY tree"))
    assert battery.main([]) == 2


def test_the_runner_refuses_when_git_status_CANNOT_BE_READ(tmp_path, monkeypatch):
    """🔴 Fails CLOSED. An unreadable answer is not a clean one.

    Both git checks used to read `.stdout` and ignore the return code, so any
    git failure produced `""` — indistinguishable from a clean tree. Measured
    with git broken: the runner did not refuse, mutated a module in a tree
    holding uncommitted work, and printed `tree restored: clean`.
    """
    monkeypatch.setattr(battery, "REPO", tmp_path)          # not a git repo
    monkeypatch.setattr(battery, "_run", lambda *a, **k: pytest.fail(
        "the runner proceeded despite an unreadable git status"))
    assert battery.main([]) == 2


def test_the_runner_DISABLES_the_bytecode_cache(monkeypatch):
    """Behavioural: capture the env actually handed to the subprocess.

    Without this a mutant can be scored SURVIVED without ever executing —
    CPython validates cached bytecode on mtime-in-whole-SECONDS + size, so a
    same-LENGTH edit in the same second imports the ORIGINAL module. Several
    mutants here are exactly same-length, so it is not theoretical.
    """
    seen = {}

    class _Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return _Done()

    monkeypatch.setattr(battery.subprocess, "run", fake_run)
    battery._run("scripts/signal/tests/")
    assert seen.get("PYTHONDONTWRITEBYTECODE") == "1", (
        f"the subprocess env carried PYTHONDONTWRITEBYTECODE="
        f"{seen.get('PYTHONDONTWRITEBYTECODE')!r}")


@pytest.mark.parametrize("rc, failures, expected", [
    (0, set(), "SURVIVED"),                       # green suite = not killed
    (1, {"the_killer"}, "KILLED"),                # the named test fired
    (1, {"some_other_test"}, "KILLED-WRONG-REASON"),   # green for the wrong reason
    (1, set(), "KILLED-WRONG-REASON"),            # red but no parseable failure
    (1, {"the_killer", "other"}, "KILLED"),       # killer among several
])
def test_the_verdict_requires_the_NAMED_test(rc, failures, expected):
    """"Some test failed" is not a kill.

    A different guard's error is green for the wrong reason and stays green with
    the guard under test deleted. Table-tested against the pure `_verdict`,
    because the substring check this replaced was satisfied by
    `elif m.killer in failures or True:` — which scores everything KILLED.
    """
    verdict, _ = battery._verdict(rc, failures, "the_killer")
    assert verdict == expected


def test_the_runner_INSTALLS_a_SIGTERM_handler_so_the_restore_still_runs(
        tmp_path, monkeypatch):
    """`finally` covers exceptions and Ctrl-C, but NOT a default-handled SIGTERM.

    Measured before the fix: `timeout -s TERM` left `_signal_db.py` modified in
    the tree, while `timeout -s INT` restored cleanly. In a shared checkout that
    silently hands the next session a mutated production module.

    🔴 This assertion reads the PROCESS's signal disposition, not the source.
    The first version of this test asserted `"SIGTERM" in getsource(main)` and
    was walked immediately: deleting the handler leaves the word behind in the
    COMMENT that explains it, so the guard passed against a runner with no
    handler at all. That is the third time in this file's short history that a
    guard on words survived removal of the behaviour — the reason every guard
    here is now behavioural.
    """
    previous = _signal.getsignal(_signal.SIGTERM)
    try:
        monkeypatch.setattr(battery, "REPO", tmp_path)     # refuses immediately
        battery.main([])
        installed = _signal.getsignal(_signal.SIGTERM)
        assert callable(installed) and installed is not previous, (
            f"SIGTERM disposition is still {installed!r} — no handler was "
            "installed, so a kill mid-run leaves a mutant in the tree")
    finally:
        _signal.signal(_signal.SIGTERM, previous)


def test_the_audit_found_mutants_are_still_present():
    """🔴 The four highest-value rows, kept by id.

    Each SURVIVED a battery its author had just certified as complete, and was
    found only because an independent audit built a DIFFERENT battery. They are
    the evidence that a green battery is a claim about its author's imagination.
    Deleting one because it "looks redundant" throws away the only record of a
    blind spot that has actually occurred.
    """
    ids = {m.id for m in battery.MUTANTS}
    assert {"A1", "A2", "A3", "A4"} <= ids, (
        f"an audit-found mutant was removed; present: {sorted(ids)}")
    for m in battery.MUTANTS:
        if m.id.startswith("A"):
            assert "[audit]" in m.why, f"{m.id} lost its provenance note"


# --------------------------------------------------------------------------- #
# ROUND-4 delta audit
# --------------------------------------------------------------------------- #
# 🔴 The BUDGET, not just the word. `test_an_EQUIVALENT_mutant_ARGUES_for_itself`
# above covers only the KILLED direction; the LAUNDERING direction was measured
# end to end by the round-4 audit. `NAME_HINT_MAX = 5 -> 4` was a GENUINE
# survivor of all 908 tests, and adding it to the ledger with `equivalent=True`
# and the word "EQUIVALENT" in its `why` produced BATTERY_RC=0, a green suite and
# 213 passing battery-meta tests. Cost of laundering a real coverage gap into a
# green gate: one word in a string — the same spelled-guard class this branch
# removed from `test_skill_doc.py`.
#
# A cap cannot prove an equivalence argument (nothing static can). What it does
# is take the move out of the quiet path: a new `equivalent=True` row now
# REQUIRES editing the number below, in a diff a reviewer reads, next to the
# argument they must accept. That mutant is itself now KILLED rather than
# reclassified — see MEN12 and its killer.
EQUIVALENT_BUDGET = 1


def test_the_number_of_EQUIVALENT_rows_is_CAPPED():
    """The ledger may hold at most `EQUIVALENT_BUDGET` argued-equivalent rows."""
    equiv = [m for m in battery.MUTANTS if m.equivalent]
    assert sum(m.equivalent for m in battery.MUTANTS) <= EQUIVALENT_BUDGET, (
        f"{len(equiv)} rows are flagged equivalent ({[m.id for m in equiv]}) but "
        f"the budget is {EQUIVALENT_BUDGET}. `equivalent=True` turns a SURVIVED "
        f"from a finding into a pass, so raising the cap is a deliberate, "
        f"reviewable edit — make it here, in the same diff as the argument.")


def test_the_budget_is_TIGHT_so_a_new_equivalent_row_cannot_slip_in():
    """🔴 The cap must BIND. A slack budget is a cap that permits the move.

    Measured: at fbeca469 the ledger held exactly one equivalent row (MEN3) and
    no cap at all, so a second could be added with `equivalent=True` and one word
    in a string. This asserts the budget equals what is actually spent — so the
    next row is refused by the test above rather than absorbed by headroom.
    """
    assert sum(m.equivalent for m in battery.MUTANTS) == EQUIVALENT_BUDGET, (
        "the budget has slack: it must equal the number of equivalent rows, or "
        "a new one lands green without anyone editing this file")


def test_the_ONE_equivalent_row_is_MEN3_and_it_still_argues_the_same_premise():
    """The budgeted row is named, so spending it elsewhere is loud.

    MEN3's equivalence rests on a PREMISE about the code around it — `re` folds
    one code point to one, so a match consumes exactly `len(pattern)`. Keeping
    the row keyed by id means a future round cannot re-spend the budget on a
    different mutation while the count stays at one.
    """
    equiv = [m for m in battery.MUTANTS if m.equivalent]
    assert [m.id for m in equiv] == ["MEN3"], [m.id for m in equiv]
    assert "folds one code point to one" in equiv[0].why


@pytest.mark.parametrize("rows, expected", [
    # (equivalent?, verdict) pairs -> the headline text
    ([(False, "KILLED")], "1/1 killed by their NAMED test"),
    ([(False, "SURVIVED")], "0/1 killed by their NAMED test"),
    ([(False, "KILLED-WRONG-REASON")], "0/1 killed by their NAMED test"),
    ([(True, "SURVIVED")],
     "0/0 killed by their NAMED test  (1 EQUIVALENT, expected to survive)"),
    # 🔴 THE F-D CELL: a row that is BOTH equivalent AND wrong. The old
    # `len(results) - len(bad) - len(equiv)` charged it twice and printed -1/0.
    ([(True, "KILLED")],
     "0/0 killed by their NAMED test  (1 EQUIVALENT, expected to survive)"),
    ([(True, "KILLED"), (False, "KILLED")],
     "1/1 killed by their NAMED test  (1 EQUIVALENT, expected to survive)"),
])
def test_the_headline_count_never_double_subtracts(rows, expected):
    """Round-4 F-D, table-tested against the pure `headline()`.

    The exit code and the `!!` detail lines were correct all along; this is the
    number a reader scans first, and `-1/0` reads as a glitch in the tool rather
    than as the finding it actually was.

    🔴 SCOPE OF THE RED. At fbeca469 this logic was INLINE in `main()`, so these
    cases fail there with an `AttributeError` — the extraction is what makes the
    defect reachable at all, exactly as `_verdict` was extracted for the same
    reason. The BEHAVIOURAL proof that the old expression was wrong is the test
    below, which runs the old arithmetic and watches it go negative.
    """
    results = [(battery.Mutant(f"X{i}", "EQUIVALENT: argued" if eq else "real",
                               "p", "a", "b", "k", "s", equivalent=eq),
                verdict, "detail")
               for i, (eq, verdict) in enumerate(rows)]
    assert battery.headline(results) == expected


def test_the_OLD_headline_arithmetic_really_did_go_NEGATIVE():
    """The control that makes the test above regression coverage, not a rewrite.

    The shipped expression was `len(results) - len(bad) - len(equiv)` over
    `bad = [verdict != expected]` and `equiv = [m.equivalent]`. On the one cell
    where those two sets INTERSECT — an equivalent mutant that got KILLED, the
    single finding the flag exists to surface — the row is subtracted twice.
    Run here rather than described, so the claim is checked by the machine: the
    old form yields -1 where `headline()` yields 0, and `-1/0 killed` is what a
    live run printed.
    """
    equiv_row = (battery.Mutant("X", "EQUIVALENT: argued", "p", "a", "b", "k",
                                "s", equivalent=True), "KILLED", "detail")
    results = [equiv_row]
    bad = [r for r in results if r[1] != r[0].expected]
    equiv = [r for r in results if r[0].equivalent]
    old = len(results) - len(bad) - len(equiv)
    assert old == -1, "the old arithmetic is not being reproduced faithfully"
    assert battery.headline(results).startswith("0/0 "), battery.headline(results)
