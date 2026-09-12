#!/usr/bin/env python3
"""Gate on the MEASUREMENT LAYER of `scripts/present/` — including its ABSENCES.

WHAT THIS MODULE IS FOR
-----------------------
The explainer page's entire claim to being worth reading is that its numbers were
measured rather than typed. That claim rests on three properties, and this module
pins all three:

  1. a measurer that CANNOT answer produces an `UNMEASURED` ROW, never a gap and
     never a blank;
  2. a build in which NOTHING measured FAILS LOUDLY, rather than emitting a page
     that looks careful and is broken;
  3. the registry cannot silently shrink — every section that asks for a
     measurement key gets one, and every key is reachable from a section.

🔴 WHAT IS AND IS NOT REGRESSION COVERAGE HERE. `scripts/present/` is new in the
commit that adds this file, so a test "shown red at the base ref" is not
available for most of what is below: at the base ref the module does not import.
Those tests are INVARIANT GUARDS and are named as such — they pin properties, and
they are not regression coverage.

The exceptions, and they are the ones that matter, are the three NEGATIVE
CONTROLS: `test_a_raising_measurer_becomes_an_unmeasured_row`,
`test_an_all_unmeasured_build_fails_loudly` and
`test_a_measurer_returning_a_blank_value_is_unmeasured_not_clean`. Each drives
the real code against a deliberately broken registry and watches it produce the
loud outcome. They are the reason the `UNMEASURED` machinery can be believed at
all — without them, "the generator reports absences" is a claim about the
generator, not a fact about it.

🔴 PUBLIC-REPO NOTE. Every fixture below is SYNTHETIC and built inside
`tmp_path`. No real scope name, hostname, address or captured text appears here.
The live-tree tests read the repo they are running in and assert only on SHAPE
(a row exists, a status is one of two values), never on a value.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 🔴 A PLAIN IMPORT, NOT `importorskip`. `scripts/run-tests.sh` GUARD 2 pins the
# expected-skip SET, so a new skip is loud — but a skip still says "this suite
# chose not to run", where an import error says "the module under test is
# broken". Those are different findings and the second one is the true one.
from present import content, measure  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures — synthetic, in tmp_path, never the operator's real machine
# --------------------------------------------------------------------------- #


@pytest.fixture()
def synthetic_env(tmp_path: Path) -> measure.Env:
    """An `Env` pointing at a tree that exists but holds none of the real files.

    Almost every measurer must come back UNMEASURED against this, which is
    exactly what makes it a useful control: it is the "we could not look" state
    the whole design is built around.
    """
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "scripts").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    return measure.Env(
        repo=repo, home=home, claude_dir=home / ".claude",
        index_store=home / ".claude" / "analyze-service-index",
        allow_systemd=False, allow_network=False,
    )


def _registry(*entries):
    return tuple(entries)


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROLS — the three that make the UNMEASURED machinery believable
# --------------------------------------------------------------------------- #


def test_a_raising_measurer_becomes_an_unmeasured_row(synthetic_env):
    """NEGATIVE CONTROL. A measurer that raises must produce a ROW, not a gap.

    The failure this prevents is the one `drift-check.sh` rc 18 records: "we
    could not look" rendering identically to "we looked and it was clean".
    """
    def boom(env):
        raise measure.Unmeasurable("the probe could not reach the thing")

    ms = measure.take(synthetic_env, _registry(
        ("k.boom", "sec", "A label", boom, "run the settling command"),
    ))
    assert len(ms) == 1, "the row was DROPPED — an omitted row reads as a clean one"
    row = ms.by_key("k.boom")
    assert row.status == measure.UNMEASURED
    assert row.value is None, "an unmeasured row must not carry a value"
    assert "could not reach the thing" in row.reason
    assert row.settle == "run the settling command", (
        "an unmeasured row with no settling command tells the reader a fact is "
        "missing and gives them nowhere to go — the same dead end as omitting it"
    )


def test_an_unexpected_exception_also_becomes_a_row_not_a_crash(synthetic_env):
    """NEGATIVE CONTROL. A measurer may raise ANYTHING; the build still reports.

    Letting one measurer's ImportError abort the whole build trades a page with
    one honest absence for no page at all.
    """
    def boom(env):
        raise ZeroDivisionError("something entirely unplanned")

    ms = measure.take(synthetic_env, _registry(
        ("k.crash", "sec", "A label", boom, "settle it"),
    ))
    row = ms.by_key("k.crash")
    assert row.status == measure.UNMEASURED
    assert "ZeroDivisionError" in row.reason, (
        "the reason must name what actually went wrong, or the row is an absence "
        "with no diagnosis"
    )


def test_a_measurer_returning_a_blank_value_is_unmeasured_not_clean(synthetic_env):
    """NEGATIVE CONTROL. A measurer that returns nothing useful is UNMEASURED.

    This is the silent-zero shape in miniature: a function that "succeeded" and
    produced an empty string would otherwise render as a measured row with a
    blank value, which reads as a real answer.
    """
    for payload in ({}, {"value": ""}, {"value": None}, None):
        ms = measure.take(synthetic_env, _registry(
            ("k.blank", "sec", "L", lambda env, p=payload: p, "settle it"),
        ))
        row = ms.by_key("k.blank")
        assert row.status == measure.UNMEASURED, f"{payload!r} rendered as measured"
        assert row.reason


def test_an_all_unmeasured_build_fails_loudly(synthetic_env):
    """NEGATIVE CONTROL. Nothing measured must be a FAILED BUILD, not a page.

    A page where every row says UNMEASURED looks like a careful page. It is a
    broken build, and the generator must refuse to emit it.
    """
    def boom(env):
        raise measure.Unmeasurable("nope")

    ms = measure.take(synthetic_env, _registry(
        ("a", "s", "A", boom, "x"), ("b", "s", "B", boom, "y"),
    ))
    assert ms.verdict() == "all-unmeasured"
    assert measure.MeasurementSet().verdict() == "empty", (
        "an EMPTY registry and an ALL-UNMEASURED run are different defects and "
        "must not collapse into one verdict"
    )


def test_one_measured_row_is_enough_to_make_the_build_ok(synthetic_env):
    """POSITIVE CONTROL for the verdict.

    Without this, `verdict()` returning `all-unmeasured` unconditionally would
    pass the negative control above and be completely broken. The pair is the
    point: a check that can only ever say one thing is not a check.
    """
    def boom(env):
        raise measure.Unmeasurable("nope")

    def fine(env):
        return {"value": "42 widgets", "source": "a synthetic source"}

    ms = measure.take(synthetic_env, _registry(
        ("a", "s", "A", boom, "x"), ("b", "s", "B", fine, "y"),
    ))
    assert ms.verdict() == "ok"
    assert len(ms.measured) == 1 and len(ms.unmeasured) == 1


# --------------------------------------------------------------------------- #
# The whole real registry, driven against a tree that holds nothing
# --------------------------------------------------------------------------- #


def test_every_real_measurer_survives_an_empty_tree(synthetic_env):
    """INVARIANT GUARD. No measurer may crash the build on a bare tree.

    Also asserts the shape that matters: every registry entry produces exactly
    one row, and every unmeasured row carries both a reason and a settling
    command. A measurer that returned two rows, or none, would break the
    section-to-row mapping silently.
    """
    ms = measure.take(synthetic_env)
    assert len(ms) == len(measure.REGISTRY), "a measurer produced the wrong row count"
    keys = [m.key for m in ms]
    assert len(keys) == len(set(keys)), "duplicate measurement key"
    for row in ms.unmeasured:
        assert row.reason, f"{row.key} is unmeasured with no reason"
        assert row.settle, f"{row.key} is unmeasured with no settling command"
    assert ms.verdict() in {"ok", "all-unmeasured"}


def test_every_registry_entry_has_a_settling_command():
    """INVARIANT GUARD. The settle command is what an absence hands the reader."""
    for key, section, label, fn, settle in measure.REGISTRY:
        assert key and section and label, f"{key!r}: an incomplete registry entry"
        assert callable(fn), f"{key!r}: not callable"
        assert settle and len(settle) > 8, (
            f"{key!r} has no usable settling command — an UNMEASURED row for it "
            "would report an absence and offer no way to resolve it"
        )


def test_the_registry_and_the_sections_pin_each_other_both_ways():
    """SEAM GUARD. Every key a section renders exists; every key is reachable.

    🔴 This is the property that keeps the page honest as it grows. Without the
    forward direction a section can ask for a fact nobody measures; without the
    reverse a measurer can be written, cost build time on every run, and never
    appear on the page — a measurement nobody reads is indistinguishable from
    one that was never taken.
    """
    registered = {e[0] for e in measure.REGISTRY}
    asked = {
        payload
        for section in content.SECTIONS
        for kind, payload in section.blocks
        if kind == "measure"
    }
    assert asked - registered == set(), (
        f"a section asks for measurement key(s) no measurer produces: "
        f"{sorted(asked - registered)}"
    )
    assert registered - asked == set(), (
        f"measurer(s) run on every build and appear on no section: "
        f"{sorted(registered - asked)}"
    )


def test_measurement_sections_all_name_a_real_section_slug():
    """INVARIANT GUARD. A registry entry's `section` must match a page section."""
    slugs = {s.slug for s in content.SECTIONS}
    for key, section, *_ in measure.REGISTRY:
        assert section in slugs, f"{key!r} claims section {section!r}, which is not a page section"


# --------------------------------------------------------------------------- #
# The live tree — SHAPE only, never a value
# --------------------------------------------------------------------------- #


def test_the_live_repo_measures_something():
    """POSITIVE CONTROL against the real tree.

    🔴 Every test above would pass against a measurement layer that can only
    ever say UNMEASURED. This one feeds it a case that MUST produce measured
    rows — the repo it is running in — and watches the number move. Report the
    pair, never the zero alone.
    """
    env = measure.Env(
        repo=REPO_ROOT,
        home=Path.home(),
        claude_dir=Path.home() / ".claude",
        index_store=Path.home() / ".claude" / "analyze-service-index",
        allow_systemd=False, allow_network=False,
    )
    ms = measure.take(env)
    assert ms.verdict() == "ok"
    # These three read the REPO and nothing else, so they must measure wherever
    # this suite can run at all — including the nix sandbox, which has no home
    # directory worth the name and no systemd.
    for key in ("rules.bytes", "skills.listing", "ship.managed"):
        row = ms.by_key(key)
        assert row is not None and row.measured, (
            f"{key} came back UNMEASURED against the real repo: {row.reason if row else 'absent'}"
        )
        assert row.value and any(ch.isdigit() for ch in row.value)


def test_the_rules_ceiling_is_read_from_the_test_that_owns_it():
    """SEAM GUARD. The ceiling must come from its owner, not from a second copy.

    Mechanically: change `MAX_BYTES` in its owning module and the page's number
    must move. This drives that by loading the owner, reading the constant, and
    asserting the rendered row quotes it — so a hardcoded literal in
    `measure.py` would fail here rather than quietly diverge.
    """
    owner = REPO_ROOT / "scripts" / "tests" / "test_rules_size.py"
    loader = importlib.machinery.SourceFileLoader("_owner_rules_size", str(owner))
    spec = importlib.util.spec_from_loader("_owner_rules_size", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    env = measure.Env(repo=REPO_ROOT, home=Path.home(),
                      claude_dir=Path.home() / ".claude",
                      index_store=Path.home() / ".claude" / "nonexistent",
                      allow_systemd=False, allow_network=False)
    entry = next(e for e in measure.REGISTRY if e[0] == "rules.bytes")
    row = measure.take(env, _registry(entry)).by_key("rules.bytes")
    assert row.measured, row.reason
    assert f"{mod.MAX_BYTES:,}" in row.value, (
        "the rendered ceiling is not the one its owning test defines — a second "
        "hand-maintained copy of that number is exactly how the drift regrows"
    )


def test_the_reader_rows_and_the_detail_prose_do_not_CONTRADICT_each_other():
    """🔴 A MEASURED ROW THAT CONTRADICTS THE PARAGRAPH IT SITS UNDER — TWICE on
    this one row, and both times it was caught by a human reading the rendered
    page rather than by anything here.

    (An earlier draft of this docstring said "the third time". Two is what the
    record supports, and inflating it is the same defect as the one being
    guarded — a sentence asserting a number nobody re-derived.)

    History, because it is the argument for a test rather than a sharper comment:
    the predicate once matched the bare token `requests` in prose and scored two
    readers HTTP-capable off the phrase "pull requests"; and when the reader list
    was updated for the cairn consolidation the count went 0 -> 1 while the
    `detail` still read "a subsystem can be complete, correct, well-tested and
    have no reader". `present-regen` republishes that page daily.

    🔴 WHAT THIS CAN AND CANNOT SEE. It pins ONE relationship — the count in
    `value` against the tense of the prose in `detail` — because that is the
    contradiction that has actually shipped, twice. It is not a general
    prose-checker and cannot tell whether the rest of the paragraph is true. A
    reword that keeps both halves honest passes; a reword that re-asserts
    readerlessness over a non-zero count fails.
    """
    env = measure.Env(repo=REPO_ROOT, home=Path.home(),
                      claude_dir=Path.home() / ".claude",
                      index_store=Path.home() / ".claude" / "nonexistent",
                      allow_systemd=False, allow_network=False)
    entry = next(e for e in measure.REGISTRY
                 if "reader(s) can speak" in (measure.take(env, _registry(e))
                                              .by_key(e[0]).value or ""))
    row = measure.take(env, _registry(entry)).by_key(entry[0])
    assert row.measured, row.reason

    count = int(row.value.split()[0])
    speaks = [r for r in row.rows if r[1] == "speaks HTTP"]
    # POSITIVE CONTROL on the parse: the number in the sentence and the rows it
    # is derived from must agree, or this guard is comparing prose to nothing.
    assert count == len(speaks), (
        f"the row's own value ({row.value!r}) disagrees with its rows "
        f"({speaks!r}) — this guard cannot say anything about the prose until "
        f"the number it compares against is the number the rows produce"
    )

    readerless = ("have no reader", "never written", "no reader")
    if count > 0:
        for phrase in readerless:
            assert phrase not in row.detail, (
                f"the row measures {count} local reader(s) that speak HTTP "
                f"({[r[0] for r in speaks]}) while its own `detail` still says "
                f"{phrase!r}. The page contradicts itself and present-regen "
                f"republishes it daily. Fix the prose to match what the row now "
                f"measures — and do not fix it by deleting the row: the "
                f"readerless period is the finding this row exists to have "
                f"recorded, it is just no longer the present tense."
            )
    else:
        assert any(p in row.detail for p in readerless), (
            "the row measures ZERO readers that speak HTTP and its `detail` no "
            "longer says so — the paragraph has drifted the other way, which is "
            "the same defect facing the other direction"
        )


def test_the_index_store_row_can_actually_be_MEASURED(tmp_path):
    """🔴 REGRESSION COVERAGE — the MEASURED branch was DEAD and 50 tests passed.

    `m_index_store` guarded itself with
    `if not (lib / "subsystem_recall.py").is_file(): raise Unmeasurable(...)`.
    devrc deleted that file when it consolidated onto the pinned `cairn` client,
    so the guard went PERMANENTLY TRUE: the row was UNMEASURED on every host, the
    `cairn_pin.ensure()` behind it could never execute, and the reason string
    pointed a reader at a file that had been deleted on purpose.

    🔴 NOTHING SAW IT, AND THE REASON IS THE LESSON. Every other `Env` in this
    file points `index_store` at a directory that does not exist, so every one of
    them takes the ABSENT branch and none has ever reached the parser. A suite
    that only ever exercises the "cannot answer" path cannot tell a measurer that
    is correctly silent from one that is structurally mute — which is this
    module's own headline property, applied to itself.

    So this builds a SYNTHETIC store and requires the row to come back MEASURED.
    Red at the base of the consolidation branch? No — at that base the module was
    not broken, so this is a guard on a hole the consolidation opened and the
    consolidation closed. It is REGRESSION coverage against the shipped defect:
    it fails on the tree as first pushed, and passes here.

    🔴 PUBLIC-REPO NOTE, and it is why the store is built rather than borrowed:
    the operator's real store holds client-confidential scope names. Nothing
    below reads it. The fixture names are invented and share no substring with
    any real scope.
    """
    store = tmp_path / "index-store"
    (store / "widget-cfg").mkdir(parents=True)
    (store / "widget-cfg" / "gizmo.md").write_text(
        "---\n"
        "service: gizmo\n"
        "scope: widget-cfg\n"
        "sensitivity: public\n"
        "created_by: handoff\n"
        "---\n"
        "# gizmo\n\n"
        "## What it is\n"
        "A synthetic entry, invented for this test.\n\n"
        "## Pointers\n"
        "- `nowhere/real.py` — a pointer.\n\n"
        "## Nuance / work-history\n"
        "- 2026-01-01: a bullet.\n",
        encoding="utf-8",
    )

    env = measure.Env(repo=REPO_ROOT, home=tmp_path,
                      claude_dir=tmp_path / ".claude", index_store=store,
                      allow_systemd=False, allow_network=False)
    entry = next(e for e in measure.REGISTRY if e[0] == "index.store")
    row = measure.take(env, _registry(entry)).by_key("index.store")

    assert row.measured, (
        f"the index.store row could not be measured against a store this test "
        f"just built: {row.reason}. That is the shipped defect — a precondition "
        f"naming a file the repo deliberately deleted — not a property of the "
        f"store."
    )
    # A POSITIVE CONTROL on the number, not just on `measured`: a parser wired to
    # nothing would also report a clean zero.
    assert "1 entr" in row.value, (
        f"the row is MEASURED but does not count the one entry built above "
        f"({row.value!r}) — a measurer that answers without reading is the "
        f"confident zero this module exists to catch"
    )


def test_the_index_store_row_is_UNMEASURED_when_the_pin_cannot_resolve(
    tmp_path, monkeypatch
):
    """The other arm: the row degrades with its OWN reading, not the parser's.

    🔴 A NEGATIVE CONTROL FOR THE TEST ABOVE. Without it, `measured=True` could be
    a fact about this host rather than about the code, and the two branches of
    `m_index_store` would be indistinguishable to the suite. It also pins the
    SEPARATION: a host with no pinned client is a STATE, and reporting it as "the
    index store did not load through its own parser" would claim a broken store
    where there is only a missing client.
    """
    store = tmp_path / "index-store"
    (store / "widget-cfg").mkdir(parents=True)

    # Route 1 set-but-unusable REFUSES rather than falling through to PATH, which
    # is what makes this reachable without touching PATH at all.
    monkeypatch.setenv("CAIRN_LIB", str(tmp_path / "no-such-lib"))
    env = measure.Env(repo=REPO_ROOT, home=tmp_path,
                      claude_dir=tmp_path / ".claude", index_store=store,
                      allow_systemd=False, allow_network=False)
    entry = next(e for e in measure.REGISTRY if e[0] == "index.store")
    row = measure.take(env, _registry(entry)).by_key("index.store")

    assert not row.measured, "the row claimed a measurement with no pinned client"
    assert "pinned cairn client is not available" in row.reason, (
        f"the row degraded, but not with the pin's own reading: {row.reason!r}"
    )


def test_a_constant_is_read_by_PARSING_its_owner_not_by_importing_it(tmp_path):
    """🔴 REGRESSION COVERAGE — this was measurably broken and shipped an absence.

    The owning modules are pytest test files. The first cut IMPORTED them to read
    their constants, so a module that does `import pytest` came back unreadable
    under a bare interpreter and the page rendered the ceiling as UNREAD — an
    absence caused by the measurement technique rather than by the tree, which
    is the worst kind because it looks like a finding.

    The fixture below is the exact shape that failed: a constant sitting behind
    an import that is not satisfiable here. Parsing sees the number; importing
    cannot.
    """
    owner = tmp_path / "owner.py"
    owner.write_text(
        "import a_module_that_certainly_does_not_exist_anywhere\n"
        "CEILING = 1_234\n",
        encoding="utf-8")
    assert measure._const(owner, "CEILING") == 1234


def test_reading_a_constant_that_is_not_a_literal_is_an_absence_not_a_guess(tmp_path):
    """NEGATIVE CONTROL for the parser: it must refuse rather than approximate."""
    owner = tmp_path / "owner.py"
    owner.write_text("CEILING = some_call()\n", encoding="utf-8")
    with pytest.raises(measure.Unmeasurable) as exc:
        measure._const(owner, "CEILING")
    assert "not a literal" in str(exc.value)


@pytest.mark.parametrize("source,why", [
    ("CEILING = 0\nOTHER = 1\nCEILING = 24_000\n", "a later rebinding"),
    ("CEILING = 1\nCEILING += 23_999\n", "an augmented assignment"),
    ("CEILING = 24_000\nif True:\n    pass\nCEILING = 0\n", "a rebinding after a block"),
])
def test_a_constant_bound_more_than_once_is_an_ABSENCE_not_a_stale_number(
        tmp_path, source, why):
    """🔴 REGRESSION COVERAGE. The parser returned the FIRST binding and stopped.

    Probed against the shipped code: `X = 0` followed later by `X = 24_000`
    returned **0**, and `X = 1; X += 23_999` returned **1**. Both render a WRONG
    NUMBER as a measured, timestamped fact — which is the single failure this
    whole page exists to prevent — and neither is distinguishable, on the page,
    from a correct read.

    The premise of parsing instead of importing is that a source this module
    cannot understand yields an ABSENCE. A name whose value depends on execution
    order is exactly that, so it must refuse rather than guess. Latent when
    written (each owner has one binding today) and closed anyway, because every
    number on the page depends on this refusing.
    """
    owner = tmp_path / "owner.py"
    owner.write_text(source, encoding="utf-8")
    with pytest.raises(measure.Unmeasurable) as exc:
        measure._const(owner, "CEILING")
    assert "binds CEILING" in str(exc.value), (why, str(exc.value))
    # CONTROL: the same file with the duplicate binding removed still reads.
    single = tmp_path / "single.py"
    single.write_text("CEILING = 24_000\n", encoding="utf-8")
    assert measure._const(single, "CEILING") == 24_000


def test_a_constant_bound_by_UNPACKING_is_an_absence_not_a_skip(tmp_path):
    """NEGATIVE CONTROL for the same rule, one syntax form over.

    `A, B = 1, 2` is a real definition of `A` that `literal_eval` cannot
    evaluate. Walking past it would resurrect the stale-value defect through a
    door the rebinding check does not watch: the parser would report "no longer
    defines CEILING" for a file that plainly defines it.
    """
    owner = tmp_path / "owner.py"
    owner.write_text("CEILING, FLOOR = 24_000, 1\n", encoding="utf-8")
    with pytest.raises(measure.Unmeasurable) as exc:
        measure._const(owner, "CEILING")
    assert "cannot evaluate" in str(exc.value), str(exc.value)


def test_an_annotation_without_a_value_is_not_a_binding(tmp_path):
    """CONTROL against over-refusing. `X: int` annotates; it does not bind.

    Without this, `CEILING: int` followed by `CEILING = 24_000` — an ordinary,
    correct way to write a typed constant — would be counted as two bindings and
    the number would go UNMEASURED for no reason. A guard that refuses valid
    input is how a measurement layer trains people to stop reading it.
    """
    owner = tmp_path / "owner.py"
    owner.write_text("CEILING: int = 24_000\n", encoding="utf-8")
    assert measure._const(owner, "CEILING") == 24_000
    both = tmp_path / "both.py"
    both.write_text("CEILING: int\nCEILING = 24_000\n", encoding="utf-8")
    assert measure._const(both, "CEILING") == 24_000


def test_a_binding_INSIDE_a_function_does_not_shadow_the_module_level_one(tmp_path):
    """CONTROL. The rule is about MODULE-level bindings, and only those.

    A local named the same thing is not a rebinding of the constant, and
    counting it would make the ceiling unreadable in any owner that happens to
    use the name as a local.
    """
    owner = tmp_path / "owner.py"
    owner.write_text("def f():\n    CEILING = 0\n    return CEILING\nCEILING = 24_000\n",
                     encoding="utf-8")
    assert measure._const(owner, "CEILING") == 24_000


def test_a_missing_constant_owner_is_reported_not_guessed(tmp_path):
    """NEGATIVE CONTROL for the constant-reading path.

    If the owning test disappears or stops defining the constant, the row must
    go UNMEASURED and SAY SO — never fall back to a remembered value.
    """
    repo = tmp_path / "repo"
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "claude").mkdir(parents=True)
    (repo / "claude" / "RULES.md").write_text("synthetic rules\n", encoding="utf-8")
    (repo / "scripts" / "tests" / "test_rules_size.py").write_text(
        "# an owner module with no ceiling in it\nSOMETHING_ELSE = 1\n", encoding="utf-8")
    env = measure.Env(repo=repo, home=tmp_path, claude_dir=tmp_path / ".claude",
                      index_store=tmp_path / "none", allow_systemd=False,
                      allow_network=False)
    with pytest.raises(measure.Unmeasurable) as exc:
        measure.m_rules_bytes(env)
    assert "MAX_BYTES" in str(exc.value)


def test_systemd_probing_can_be_disabled_and_says_why(synthetic_env):
    """INVARIANT GUARD. `--no-systemd` yields an UNMEASURED row with the reason.

    A build option that made a row vanish would be the omission failure wearing
    a flag.
    """
    with pytest.raises(measure.Unmeasurable) as exc:
        measure.m_timers(synthetic_env)
    assert "disabled" in str(exc.value).lower()


def test_an_empty_enumeration_is_a_failure_not_a_zero(tmp_path):
    """INVARIANT GUARD, in the shape this repo keeps re-learning.

    A `home.file` scan that matches nothing must RAISE, not report zero managed
    paths. An empty match set means the pattern is wrong; reporting it as zero
    is a confident, wrong claim about the deploy.
    """
    repo = tmp_path / "repo"
    (repo / "nix").mkdir(parents=True)
    (repo / "nix" / "home.nix").write_text("{ }\n", encoding="utf-8")
    env = measure.Env(repo=repo, home=tmp_path, claude_dir=tmp_path / ".c",
                      index_store=tmp_path / "n", allow_systemd=False,
                      allow_network=False)
    with pytest.raises(measure.Unmeasurable) as exc:
        measure.m_managed_paths(env)
    assert "empty match set" in str(exc.value)


def test_the_http_client_predicate_is_an_import_not_a_word():
    """🔴 REGRESSION COVERAGE. The first cut matched prose and reported a wrong number.

    Grepping for the bare token `requests` scored two local readers as
    HTTP-capable off the phrase "pull requests" in their own documentation — a
    measured row that directly contradicted the section it was rendered under.
    Both directions are pinned here, because a predicate that answers "no" to
    everything would pass the negative half alone.
    """
    # NEGATIVE CONTROL — prose that merely contains the words.
    for prose in ('"""work lands through pull requests, not commits."""\n',
                  "# see the requests below\n",
                  '"""urllib is deliberately NOT used here."""\n'):
        assert not measure.imports_http_client(prose), prose

    # POSITIVE CONTROL — the real shapes a client takes.
    for real in ("import urllib.request\n", "from urllib import request\n",
                 "import requests\n", "import httpx\n",
                 "    body = urlopen(url).read()\n",
                 "r = requests.get(url)\n"):
        assert measure.imports_http_client(real), real


def test_the_store_traffic_row_is_permanently_unmeasured_by_design(synthetic_env):
    """INVARIANT GUARD, and a deliberate one.

    A number for this was REPORTED to the page's author and appears nowhere in
    the tree. Rendering it would restate a claim as a measurement, which is the
    exact failure the page is built against. The row must therefore stay
    UNMEASURED against ANY tree — including the real one — until something in
    this repo can actually measure it.
    """
    with pytest.raises(measure.Unmeasurable):
        measure.m_store_api_traffic(synthetic_env)
    live = measure.Env(repo=REPO_ROOT, home=Path.home(),
                       claude_dir=Path.home() / ".claude",
                       index_store=Path.home() / ".claude" / "analyze-service-index",
                       allow_systemd=False, allow_network=False)
    with pytest.raises(measure.Unmeasurable):
        measure.m_store_api_traffic(live)


# --------------------------------------------------------------------------- #
# 🔴 THE SUITE'S OWN BLAST RADIUS
# --------------------------------------------------------------------------- #

#: Programs whose whole purpose is to leave this machine. `git` is deliberately
#: absent as a NAME — it is this module's most-used local tool — and is checked
#: by SUBCOMMAND instead, which is the only part of it that talks to a remote.
NETWORK_BINARIES = frozenset({"gh", "curl", "wget", "ssh", "scp", "nc",
                              "kubectl", "rsync", "httpie"})
NETWORK_GIT_SUBCOMMANDS = frozenset({"fetch", "clone", "ls-remote", "push", "pull"})


def _module_functions(name: str):
    import ast
    src = (SCRIPTS / "present" / f"{name}.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("m_"):
            yield node


def _measurers_that_leave_the_machine() -> set[str]:
    """Every `m_*` in `measure.py` whose body invokes a network program.

    Read off the SOURCE by PARSING, never by grepping for the word `gh`: this
    repo has already scored a measurer as HTTP-capable off the phrase "pull
    requests" in its own docstring, and the guard for that is just above.
    """
    import ast

    found: set[str] = set()
    for node in _module_functions("measure"):
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            argv = call.args[0]
            if not isinstance(argv, (ast.List, ast.Tuple)):
                continue
            words = [e.value for e in argv.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if not words:
                continue
            if words[0] in NETWORK_BINARIES:
                found.add(node.name)
            elif words[0] == "git" and NETWORK_GIT_SUBCOMMANDS & set(words[1:]):
                found.add(node.name)
    return found


def _measurers_gated_on_the_network_flag() -> set[str]:
    """Every `m_*` that BRANCHES on `env.allow_network`.

    🔴 A FIELD THAT EXISTS IS NOT A GUARD — only a branch on it is. Reading the
    branch is the difference between "the flag is declared" and "the flag stops
    something".
    """
    import ast

    gated: set[str] = set()
    for node in _module_functions("measure"):
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Attribute) and sub.attr == "allow_network"
                    and isinstance(sub.value, ast.Name) and sub.value.id == "env"):
                gated.add(node.name)
    return gated


def test_every_measurer_that_leaves_the_machine_is_gated_on_the_network_flag():
    """🔴 SEAM GUARD, asserted as a LEDGER so it fails when the set moves EITHER way.

    The finding: `m_branch_protection` shells `gh api … --timeout 45`, and it was
    reached by every test that called `take()` — five outbound GitHub calls per
    suite run, 45s ceiling each, no aggregate bound, inside a check that BLOCKS a
    merge. A required gate whose runtime depends on a third party's availability
    is a flake source, and a permanently-flaky required gate trains everyone to
    click through it.

    Pinned as a RELATIONSHIP, not a count: the set that leaves the machine must
    EQUAL the set gated on `env.allow_network`. It fails when the set GROWS (a
    new measurer shells out and nobody gated it) and when it SHRINKS (someone
    drops the gate but keeps the call).
    """
    leaves = _measurers_that_leave_the_machine()
    gated = _measurers_gated_on_the_network_flag()
    # POSITIVE CONTROL: a detector that finds nothing would satisfy equality
    # against an empty gate set and prove precisely nothing. Report the pair.
    assert "m_branch_protection" in leaves, (
        "the network-call detector found no outbound call in a module that "
        f"definitely makes one — the detector is broken, not the module. "
        f"detected={sorted(leaves)}")
    assert leaves == gated, (
        f"leaves the machine but NOT gated: {sorted(leaves - gated)}; "
        f"gated but no longer leaving: {sorted(gated - leaves)}")


def test_the_network_gate_actually_refuses_and_says_so(synthetic_env):
    """BEHAVIOURAL half of the guard above.

    A structural ledger type-checks past a gate wired to the wrong flag. This
    calls the measurer with the flag off and reads the reason — which is also
    what proves the row renders as an ABSENCE and not as "nothing protects this
    branch", the worst possible misreading of this particular row.
    """
    from dataclasses import replace

    env = replace(synthetic_env, repo=REPO_ROOT, allow_network=False)
    with pytest.raises(measure.Unmeasurable) as exc:
        measure.m_branch_protection(env)
    assert "network" in str(exc.value).lower(), str(exc.value)
    assert "no-network" in str(exc.value), str(exc.value)


def test_no_present_test_constructs_an_env_that_may_reach_the_network():
    """🔴 THE FINDING ITSELF, pinned on the SUITE rather than on the module.

    Gating the measurer is only half of it: an `Env` built in a test WITHOUT
    `allow_network=False` inherits the default `True` and the call goes out
    anyway. This reads every `test_present_*.py` and requires each
    `measure.Env(...)` construction — and each `generate.py` subprocess, which is
    a second door into the same measurer and is invisible to a search for `Env` —
    to turn the network off explicitly.

    A check on the CONSTRUCTION, deliberately, not on a count of calls: a count
    passes the day someone adds a fifth call site.
    """
    import ast

    offenders: list[str] = []
    seen_env = seen_cli = 0
    for path in sorted(Path(__file__).parent.glob("test_present_*.py")):
        text = path.read_text(encoding="utf-8")
        for call in ast.walk(ast.parse(text)):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr == "Env"
                    and isinstance(func.value, ast.Name) and func.value.id == "measure"):
                continue
            seen_env += 1
            if "allow_network" not in {k.arg for k in call.keywords}:
                offenders.append(
                    f"{path.name}:{call.lineno} measure.Env(...) with no allow_network")
        for m in re.finditer(r"str\(GENERATOR\)(.*?)\]", text, re.S):
            seen_cli += 1
            if "--no-network" not in m.group(1):
                offenders.append(
                    f"{path.name}:{text[:m.start()].count(chr(10)) + 1} "
                    "generator subprocess with no --no-network")
    # POSITIVE CONTROL for the scanner: a walk that found NOTHING would report a
    # reassuring zero. Report the pair — what it examined beside what it found.
    assert seen_env >= 5 and seen_cli >= 3, (
        f"the scanner examined too little to be believed: {seen_env} Env "
        f"constructions, {seen_cli} generator subprocesses")
    assert not offenders, (
        "these make a REQUIRED merge gate depend on GitHub being reachable:\n  "
        + "\n  ".join(offenders))
