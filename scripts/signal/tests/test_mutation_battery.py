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
def test_every_named_killer_test_EXISTS(mutant):
    """A killer that does not exist can never fire — so the mutant can never die.

    The battery would report SURVIVED (the suite stays green because the named
    test is absent, not because the code is fine), which inverts the meaning of
    its own output.
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


def test_the_runner_refuses_a_dirty_tree():
    """It edits files in place; a crash mid-run would destroy uncommitted work.

    Pinned structurally rather than by running it, because running it would mean
    mutating this very checkout. This repo is a SHARED clone whose dirty files
    usually belong to another session, which is what makes the refusal
    load-bearing rather than tidy.
    """
    src = inspect.getsource(battery.main)
    assert "--porcelain" in src and "REFUSING" in src, (
        "the dirty-tree refusal is gone from the runner")


def test_the_runner_disables_the_BYTECODE_CACHE():
    """🔴 Without this a mutant can be scored SURVIVED without ever executing.

    CPython validates a cached module on source mtime-in-whole-SECONDS + size, so
    a same-LENGTH edit landing in the same second as the last import imports the
    ORIGINAL bytecode. Several mutants here are exactly same-length (a digit
    swap, an operand reorder), so this is not a theoretical concern for this
    battery specifically.
    """
    assert "PYTHONDONTWRITEBYTECODE" in inspect.getsource(battery._run)


def test_the_runner_requires_the_NAMED_test_to_be_the_killer():
    """"Some test failed" is not a kill — it can be green for the wrong reason."""
    src = inspect.getsource(battery.main)
    assert "KILLED-WRONG-REASON" in src and "m.killer in failures" in src


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
