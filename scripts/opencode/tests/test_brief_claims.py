r"""Tests for `brief_claims` — the dispatch-brief citation guard.

WHAT IS UNDER TEST, and why. Every claim below is a MEASURED failure from a
14-day audit of 443 sessions, not a hypothetical:

  1. THE HARD FAIL. A brief with a load-bearing claim and no citation must be
     REFUSED (rc 6), not warned about. Wrong premises propagated into subagent
     briefs independently in FOUR OF SIX audit slices — the highest-cost error
     class found — and the adversarial audit ladder is structurally blind to it
     (four rounds read past one instance). A prose rule is the same mechanism
     that already failed, so the check has to refuse.

  2. THE POSITIVE CONTROL. A well-formed citation must PASS. A refuser that
     refuses everything is not a gate, and it is the shape that trains people to
     click through — which RULES.md says is worse than no gate.

  3. MEASUREMENT vs INFERENCE. The specific thing that failed: a homelab session
     INFERRED an auth constraint from a token prefix, reported it as established
     fact, wrote it into a handoff, and it propagated into a downstream session's
     opening brief ("my inference presented as fact"). So the distinction must be
     REPRESENTABLE, PRESERVED through the report, and SURFACED per-claim — not
     folded into a count, which reproduces the erasure.

  4. THE SOFT TIER. Unknown citation fields WARN and never reject, matching
     `session_insight/schema.py`'s decision O2 (closed enums hard-fail;
     extensible vocabularies soft-fail) rather than inventing a second idiom.

  5. 🔴 THE ORDERING TRAP. `opencode-dispatch` runs the PATH check before the
     citation check and returns on the first. So every CLI test here uses a
     brief with NO external path — otherwise the citation guard would never
     execute and the test would go green without reaching the code it names.
     `test_the_citation_guard_is_reachable_past_the_path_check` pins that
     reachability explicitly rather than leaving it to each fixture's care.

    run:  python -m pytest scripts/opencode/tests/test_brief_claims.py -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
OC_DIR = ROOT / "scripts" / "opencode"
CLI_PATH = OC_DIR / "opencode-dispatch"

sys.path.insert(0, str(OC_DIR / "lib"))

import brief_claims as BC  # noqa: E402


def _load_cli():
    spec = importlib.util.spec_from_loader(
        "opencode_dispatch_claims",
        importlib.machinery.SourceFileLoader(
            "opencode_dispatch_claims", str(CLI_PATH)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load_cli()


# --------------------------------------------------------------------------- #
# Fixtures — REALISTIC, taken from a brief that was actually dispatched.
#
# 🔴 Not a textbook fixture. RULES.md: a scanner allowlists its own canonical
# examples and scans clean, so the negative control has to be built from real
# data. This text is the opening of `20260821-185821-verify-pr-4260-switcher-
# avatar-refresh.md`, a brief that really ran — and its parenthetical "(dev
# database clone — safe to mutate; it is NOT production data)" is exactly the
# uncited load-bearing claim this guard exists for: acted on, never sourced,
# and catastrophic if stale.
# --------------------------------------------------------------------------- #
REAL_UNCITED_BRIEF = """\
# Verify the account-switcher avatar refresh on the PR-4260 preview

Goal: determine, by driving a real browser, whether the account-switcher avatar
updates WITHOUT a full page reload when the signed-in user's profile picture
changes. Preview URL: https://pr-4260.example.com (dev database clone — safe to
mutate; it is NOT production data).

## Steps

1. Read the session endpoint and record `user.image`.
2. Change the profile picture through the real UI.
3. Re-read both values without reloading.
"""


def _cited(*entries: dict) -> str:
    return "\n```claims\n" + json.dumps(list(entries), indent=2) + "\n```\n"


MEASURED = {
    "claim": "the preview DB is a clone, not production",
    "source": "https://pr-4260.example.com/api/health",
    "read_at": "2026-08-21",
    "basis": "measurement",
}
INFERRED = {
    "claim": "the avatar refresh is driven by the localStorage roster",
    "source": "src/components/AccountSwitcher.tsx:42",
    "read_at": "2026-08-21",
    "basis": "inference",
}


def _run_cli(args, stdin_text):
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        input=stdin_text, capture_output=True, text=True, timeout=120,
    )


# 🔴 THE REFUSAL SENTENCE, WRITTEN OUT HERE — deliberately NOT imported from
# `brief_claims`.
#
# MEASURED, in this file's own mutation sweep: the first version asserted
# `errs == [BC.NO_BLOCK_ERROR]`, and the control mutant that REWORDED the
# constant SURVIVED a fully green suite — mutating the constant mutated the
# expectation with it, so the test could not see the change. Seven other mutants
# died; this one walked, and only the positive control revealed it.
#
# That is RULES.md's "never derive a test's expectation from the implementation
# it tests", in the one place where the implementation IS a claim about itself —
# and `test_dispatch.py` had already recorded the identical finding about
# `VERDICT_CLEAN`. Reproducing it here is the evidence that the trap is a CLASS,
# not one file's mistake.
#
# A cosmetic reword now fails this file. That is the price of a machine-readable
# refusal, and it is worth paying.
LINE_NO_BLOCK = (
    "the brief declares no `claims` block. A dispatch brief must carry its "
    "sources: add a fenced ```claims block listing each load-bearing claim with "
    "`claim`, `source`, `read_at` and `basis` (measurement|inference), or "
    "declare `[]` if the brief genuinely asserts no premise the subagent would "
    "act on. Measured: wrong premises propagated into subagent briefs in four of "
    "six audit slices, and four adversarial audit rounds read past one."
)
LINE_NONE_DECLARED = (
    "  claim citations   : NONE DECLARED — the brief asserts it carries no "
    "load-bearing claim"
)
LINE_CITED_2 = ("  claim citations   : 2 claim(s) cited — "
                "1 measurement, 1 inference")


def test_the_refusal_constants_match_the_sentences_pinned_here():
    """The two-way pin. The literals above are the source of truth; this asserts
    the implementation still emits exactly them, so every other test in this file
    may use either — and a reword can no longer walk past the sweep."""
    assert BC.NO_BLOCK_ERROR == LINE_NO_BLOCK
    assert BC.NO_CLAIMS_DECLARED == LINE_NONE_DECLARED
    assert D.VERDICT_CITED.format(n=2, measurement=1, inference=1) == LINE_CITED_2


# --------------------------------------------------------------------------- #
# 1. THE HARD FAIL — an uncited load-bearing claim is REJECTED
# --------------------------------------------------------------------------- #
def test_a_brief_with_no_claims_block_is_rejected():
    """🔴 The core guard. The brief asserts the preview DB is safe to mutate and
    cites nothing; the dispatch must refuse rather than launder the premise."""
    claims, errs = BC.parse_claims(REAL_UNCITED_BRIEF)
    assert claims is None
    assert errs == [LINE_NO_BLOCK]


def test_a_json_fence_is_not_mistaken_for_a_declaration():
    """A brief full of JSON examples has still declared NOTHING. If a bare
    ```json fence satisfied the guard, every brief carrying an example payload
    would pass uncited — the guard would be walkable by coincidence."""
    text = 'Send this:\n\n```json\n[{"claim": "x"}]\n```\n'
    claims, errs = BC.parse_claims(text)
    assert claims is None and errs == [LINE_NO_BLOCK]


@pytest.mark.parametrize("missing", BC.REQUIRED_FIELDS)
def test_every_required_citation_field_is_individually_required(missing):
    """Each of the four fields on its own. Dropping `source` reproduces the stale
    -README instance; dropping `read_at` reproduces the stale-comment one;
    dropping `basis` reproduces the inference-as-fact one."""
    entry = {k: v for k, v in MEASURED.items() if k != missing}
    errs = BC.validate([entry])
    assert errs, f"a claim missing {missing!r} was accepted"
    assert any(f"claims[0].{missing}" in e for e in errs), errs


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_citation_field_is_not_a_citation(blank):
    """🔴 Present-but-empty must fail like absent. Otherwise the cheapest way
    past the guard is `"source": ""`, which is a citation in shape only."""
    errs = BC.validate([{**MEASURED, "source": blank}])
    assert any("claims[0].source" in e for e in errs), errs


def test_a_malformed_block_is_a_parse_ERROR_not_an_absence():
    """An unparseable block and an absent one are different facts. Degrading the
    first into the second would report a parse failure as "nothing to check"."""
    claims, errs = BC.parse_claims("```claims\n[{'claim': broken\n```\n")
    assert claims is None
    assert len(errs) == 1
    assert "not valid JSON" in errs[0] and "line" in errs[0]
    assert errs[0] != LINE_NO_BLOCK


def test_a_non_array_block_is_rejected():
    claims, errs = BC.parse_claims('```claims\n{"claim": "x"}\n```\n')
    assert claims is None
    assert "must be a JSON ARRAY" in errs[0]


def test_a_second_claims_block_is_rejected_rather_than_silently_unvalidated():
    """🔴 `extract_block` reads the FIRST fence. A second one would ship
    unchecked — precisely the shape where an uncited claim survives a green
    preflight — so two blocks is an error, not a merge."""
    text = _cited(MEASURED) + _cited({"claim": "uncited"})
    assert BC.count_blocks(text) == 2
    claims, errs = BC.parse_claims(text)
    assert claims is None
    assert "2 `claims` blocks" in errs[0]


@pytest.mark.parametrize("label,text,accepted", [
    # ACCEPTED — legal CommonMark spellings an author will actually produce.
    ("backtick", "```claims\n[]\n```\n", True),
    ("tilde", "~~~claims\n[]\n~~~\n", True),
    ("info-string", "```claims json\n[]\n```\n", True),
    ("indented", "  ```claims\n  []\n  ```\n", True),
    ("longer-fence", "````claims\n[]\n````\n", True),
    ("close-longer", "```claims\n[]\n````\n", True),
    # REFUSED — and note the DIRECTION: every malformed fence fails CLOSED, as
    # "no block declared", never as a silent empty declaration.
    ("unterminated", "```claims\n[]\n", False),
    ("close-shorter", "````claims\n[]\n```\n", False),
    ("wrong-word", "```json\n[]\n```\n", False),
    ("mixed-chars", "```claims\n[]\n~~~\n", False),
])
def test_the_fence_grammar_is_pinned_in_both_directions(label, text, accepted):
    """🔴 PROBED, not assumed — and pinned so the behaviour is deliberate.

    Two directions, because a fence grammar that accepts everything and one that
    accepts nothing are both useless. The refusals matter most: a malformed fence
    must land as "no block" (rc 6), never as an accepted `[]`.
    """
    claims, errs = BC.parse_claims(text)
    if accepted:
        assert errs == [], f"{label} should be a valid claims fence"
        assert claims == []
    else:
        assert claims is None, f"{label} was accepted as a declaration"
        assert errs and errs[0] == LINE_NO_BLOCK, errs


# --------------------------------------------------------------------------- #
# 2. THE POSITIVE CONTROL — a well-formed citation PASSES
# --------------------------------------------------------------------------- #
def test_a_well_formed_citation_passes():
    claims, errs = BC.parse_claims(REAL_UNCITED_BRIEF + _cited(MEASURED, INFERRED))
    assert errs == []
    assert len(claims) == 2
    assert BC.validate(claims) == []


def test_an_explicitly_empty_declaration_passes():
    """`[]` is an assertion the author made ON THE RECORD — "this brief carries
    no load-bearing claim" — as distinct from a silence. The same shape as
    schema.py's `unreadable=true`: flag it honestly rather than omit it."""
    claims, errs = BC.parse_claims("Tidy the code up.\n```claims\n[]\n```\n")
    assert errs == [] and claims == []
    assert BC.validate(claims) == []


# --------------------------------------------------------------------------- #
# 3. MEASUREMENT vs INFERENCE — represented, preserved, surfaced
# --------------------------------------------------------------------------- #
def test_the_basis_enum_is_exactly_the_two_kinds_of_knowing():
    assert BC.BASES == ("measurement", "inference")


@pytest.mark.parametrize("bad", [
    "measured",      # a plausible misspelling
    "Measurement",   # case
    "guess",         # an honest word that is not in the set
    "assumption",
])
def test_an_out_of_set_basis_hard_fails_rather_than_reading_as_a_measurement(bad):
    """🔴 The closed enum. A misspelled basis must NOT silently default to
    `measurement` — that is the inference-as-fact failure with extra steps."""
    errs = BC.validate([{**MEASURED, "basis": bad}])
    assert any("basis" in e and repr(bad) in e for e in errs), errs


def test_the_distinction_is_preserved_through_the_counts():
    claims, _ = BC.parse_claims(_cited(MEASURED, INFERRED, MEASURED))
    assert BC.basis_counts(claims) == {"measurement": 2, "inference": 1}


def test_both_bases_are_always_keyed_even_at_zero():
    """A report that omits `inference: 0` cannot be told apart from one where the
    field was never computed — RULES.md's reassuring-zero failure."""
    claims, _ = BC.parse_claims(_cited(MEASURED))
    assert BC.basis_counts(claims) == {"measurement": 1, "inference": 0}


def test_inference_claims_are_extracted_individually_not_just_counted():
    claims, _ = BC.parse_claims(_cited(MEASURED, INFERRED))
    inf = BC.inference_claims(claims)
    assert [c["claim"] for c in inf] == [INFERRED["claim"]]


def test_the_report_names_each_inference_and_tells_the_agent_to_reverify(tmp_path):
    """End to end: the receiving agent must be able to SEE which premises are
    inferences. A count would reproduce the erasure this guard exists to stop."""
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)],
                 "Do the thing." + _cited(MEASURED, INFERRED))
    assert p.returncode == D.RC_OK, p.stdout + p.stderr
    assert "[inference] " + INFERRED["claim"] in p.stdout
    assert "RE-VERIFY" in p.stdout
    # …and the MEASUREMENT must NOT be listed there — a section that named both
    # would be a section that distinguishes nothing.
    assert MEASURED["claim"] not in p.stdout


def test_the_json_report_carries_the_distinction_for_a_machine_reader(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d), "--json"],
                 "Do the thing." + _cited(MEASURED, INFERRED))
    rep = json.loads(p.stdout)
    assert rep["claims_declared"] == 2
    assert rep["claims_by_basis"] == {"measurement": 1, "inference": 1}
    assert [c["claim"] for c in rep["inference_claims"]] == [INFERRED["claim"]]
    assert rep["uncited"] is False


def test_claims_declared_is_None_not_zero_when_the_block_is_absent(tmp_path):
    """🔴 "declared nothing" and "declared an empty list" are different facts and
    must not share a JSON value — a consumer reading 0 for both cannot tell a
    refusal from an honest empty declaration."""
    d = tmp_path / "proj"
    d.mkdir()
    absent = json.loads(
        _run_cli(["preflight", "--dir", str(d), "--json"], "no block here").stdout)
    empty = json.loads(
        _run_cli(["preflight", "--dir", str(d), "--json"],
                 "none here\n```claims\n[]\n```\n").stdout)
    assert absent["claims_declared"] is None
    assert empty["claims_declared"] == 0


# --------------------------------------------------------------------------- #
# 4. THE SOFT TIER — unknown fields WARN, never reject
# --------------------------------------------------------------------------- #
def test_an_unknown_citation_field_warns_and_does_not_reject():
    """Decision O2, borrowed from session_insight/schema.py: the vocabulary has
    to be able to grow without breaking every dispatch."""
    entry = {**MEASURED, "hunch_level": "medium"}
    assert BC.validate([entry]) == []
    warns = BC.key_warnings([entry])
    assert any("hunch_level" in w for w in warns), warns


def test_an_unanchored_read_at_warns_and_does_not_reject():
    """"the commit before 6a1f2c3" is a legitimate temporal anchor. Rejecting it
    would push authors toward a FABRICATED date, which is strictly worse than an
    imprecise one."""
    entry = {**MEASURED, "read_at": "the commit before 6a1f2c3"}
    assert BC.validate([entry]) == []
    assert any("read_at" in w for w in BC.key_warnings([entry]))


def test_a_known_optional_field_produces_no_warning():
    """The negative half: a warner that warns about everything is noise, and
    noise is how a soft tier gets ignored."""
    assert BC.key_warnings([{**MEASURED, "note": "double-checked"}]) == []


def test_an_empty_declaration_READS_as_a_declaration_not_as_a_clean_bill(tmp_path):
    """🔴 The report must SAY "none declared", not fall silent. A silence beside
    a clean preflight is indistinguishable from a check that did not run — the
    same finding that produced `NOT EXAMINED` for the path scanner."""
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)],
                 "Tidy the code up.\n```claims\n[]\n```\n")
    assert p.returncode == D.RC_OK, p.stdout + p.stderr
    assert LINE_NONE_DECLARED in [ln.rstrip() for ln in p.stdout.split("\n")]
    assert "claim(s) cited" not in p.stdout


def test_the_cited_verdict_line_reports_the_split_not_just_a_total(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)],
                 "Do it." + _cited(MEASURED, INFERRED))
    assert LINE_CITED_2 in [ln.rstrip() for ln in p.stdout.split("\n")], p.stdout


def test_the_soft_tier_survives_the_cli_end_to_end(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)],
                 "Do it." + _cited({**MEASURED, "hunch_level": "medium"}))
    assert p.returncode == D.RC_OK, p.stdout + p.stderr
    assert "unknown citation field 'hunch_level'" in p.stdout
    assert "not blocking" in p.stdout


# --------------------------------------------------------------------------- #
# 5. 🔴 REACHABILITY + THE CLI CONTRACT
# --------------------------------------------------------------------------- #
def test_the_citation_guard_is_reachable_past_the_path_check(tmp_path):
    """🔴 THE MUTATION-TEST PRECONDITION, pinned as its own test.

    A guard that never executes still passes a mutation test — an earlier check
    wins, the mutant dies for the wrong reason, and the sweep reports a kill it
    did not earn. `opencode-dispatch` checks external paths FIRST and returns on
    the first failure, so this asserts the two facts that make every other CLI
    test here meaningful:

      * the uncited fixture trips NO path offender (`paths_examined` 0, so the
        path check CANNOT have rejected first), and
      * the rc that comes back is rc 6 specifically, not rc 3.
    """
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d), "--json"], REAL_UNCITED_BRIEF)
    rep = json.loads(p.stdout)
    assert rep["external_paths"] == [], (
        "the fixture trips the PATH check, so the citation guard never runs and "
        "every rc-6 assertion in this file would be green for the wrong reason")
    assert rep["blocked"] is False
    assert rep["uncited"] is True
    assert p.returncode == D.RC_UNCITED == 6, p.stdout + p.stderr


def test_preflight_refuses_the_real_uncited_brief_with_the_specific_rc(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    p = _run_cli(["preflight", "--dir", str(d)], REAL_UNCITED_BRIEF)
    assert p.returncode == D.RC_UNCITED == 6, p.stdout + p.stderr
    assert LINE_NO_BLOCK in p.stdout


def test_rc_6_is_distinct_from_rc_3_so_a_caller_can_branch():
    """The two failures are fixed by different edits: rc 3 by widening --dir,
    rc 6 by citing a source. A caller that conflated them would apply the wrong
    remedy to whichever it met."""
    assert D.RC_UNCITED != D.RC_PATH_ESCAPE
    assert len({D.RC_OK, D.RC_USAGE, D.RC_PATH_ESCAPE, D.RC_DIR,
                D.RC_LAUNCH, D.RC_UNCITED}) == 6


def test_run_refuses_an_uncited_brief_and_never_writes_it(tmp_path, monkeypatch):
    """🔴 "Not dispatched" must mean NOTHING WAS WRITTEN. An uncited brief left
    in the target directory could be picked up by a later `-c` continuation, so
    the refusal has to land before `install_brief`."""
    d = tmp_path / "proj"
    d.mkdir()

    def boom(*a, **k):
        raise AssertionError("dispatched despite an uncited brief")

    monkeypatch.setattr(D.subprocess, "Popen", boom)
    monkeypatch.setattr(D.sys, "stdin", _StdinText(REAL_UNCITED_BRIEF))
    assert D.main(["run", "--dir", str(d)]) == D.RC_UNCITED
    assert not (d / D.BRIEF_SUBDIR).exists(), (
        "an uncited brief was installed into the target directory anyway")


def test_run_dispatches_once_the_brief_carries_its_sources(tmp_path, monkeypatch):
    """The both-directions pin. A gate that only ever refuses is not a gate, and
    this is the half that proves the citation path can be satisfied."""
    d = tmp_path / "proj"
    d.mkdir()
    seen = {}
    monkeypatch.setattr(D.subprocess, "Popen",
                        lambda argv, **kw: seen.update(argv=argv)
                        or type("P", (), {"pid": 7})())
    monkeypatch.setattr(D.sys, "stdin",
                        _StdinText("Do it." + _cited(MEASURED)))
    assert D.main(["run", "--dir", str(d)]) == D.RC_OK
    assert seen["argv"][:2] == ["opencode", "run"]


def test_the_uncited_outcome_is_in_the_declared_ledger():
    """The telemetry seam: an outcome the ledger cannot see is one adoption-scan
    silently buckets as "unknown"."""
    assert "preflight-uncited" in D.OUTCOMES
    src = CLI_PATH.read_text()
    assert src.count('emit("preflight-uncited"') == 2, (
        "both cmd_preflight and cmd_run must emit it as a LITERAL — the ledger "
        "test in test_dispatch.py is a grep over call sites and cannot see a "
        "computed argument")


def test_the_uncited_outcome_is_counted_as_impact():
    """It PREVENTS a bad dispatch, so adoption-scan must be able to report it as
    a win rather than as unclassified noise."""
    adoption = (ROOT / "scripts" / "session-analysis" / "adoption-scan.py").read_text()
    row = adoption.split('"id": "opencode-dispatch"', 1)[1][:900]
    assert '"preflight-uncited"' in row
    assert '"impact_outcomes": ["preflight-blocked", "preflight-uncited"]' in row


class _StdinText:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t
