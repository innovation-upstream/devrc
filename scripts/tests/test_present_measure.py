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
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

present = pytest.importorskip("present")
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
        allow_systemd=False,
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
        allow_systemd=False,
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
                      allow_systemd=False)
    row = measure.take(env, _registry(measure.REGISTRY[1])).by_key("rules.bytes")
    assert row.measured, row.reason
    assert f"{mod.MAX_BYTES:,}" in row.value, (
        "the rendered ceiling is not the one its owning test defines — a second "
        "hand-maintained copy of that number is exactly how the drift regrows"
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
                      index_store=tmp_path / "none", allow_systemd=False)
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
                      index_store=tmp_path / "n", allow_systemd=False)
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
                       allow_systemd=False)
    with pytest.raises(measure.Unmeasurable):
        measure.m_store_api_traffic(live)
