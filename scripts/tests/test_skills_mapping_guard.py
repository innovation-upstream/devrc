"""Controls for `testlib.skills_mapping` — "is the ~/.claude/skills mapping
DECLARED, and not switched off?", the whole of what that predicate now claims.

WHAT CHANGED, AND WHY THESE CONTROLS SHRANK WITH IT
---------------------------------------------------
The predicate used to also trace whether the mapping's `source` RESOLVED to the
repo tree — `$out` analysis, `cp`/`rm` parsing, let-binding resolution, comment
stripping. That half is GONE on purpose (see the module docstring): it never
caught a real breakage, its only firing was a false positive, and the deployed
truth is measured against the real filesystem on both hosts by `ship.sh` /
`drift-check.sh`. So the fixtures that pinned it (wrong tree, `rm $out`, second
tree copied over, dead shell code) are gone too — a control for a check that no
longer exists is a claim about nothing.

What remains must still be shown able to go RED. Each fixture below is a
one-edit break of the deployment: the mapping deleted, its `source` deleted,
`enable = false`, a redirected `target`. Two of them are written in DOTTED form
on purpose — nix's own parser merges those into the same attrset, which is the
lexical hole a regex over the raw text would have.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from testlib import mockbin  # noqa: E402
from testlib.skills_mapping import (  # noqa: E402
    _DEFAULT_TIMEOUT_S,
    _TIMEOUT_ENV,
    assert_skills_mapping_declared,
    skills_mapping_problem,
)

HOME_NIX = ROOT / "nix" / "home.nix"


@pytest.fixture(autouse=True)
def _no_ambient_override(monkeypatch):
    """Every test states its own budget. An operator's exported value must not
    decide what the rest of this file measures — the honouring of that value is
    itself under test below, so leaking it in would make those cases circular."""
    monkeypatch.delenv(_TIMEOUT_ENV, raising=False)

# Every fixture is a home-manager module function, like the real file.
REAL_SHAPE = """{ ... }:
{
  home.file.".claude/RULES.md" = { source = ../claude/RULES.md; force = true; };
  home.file.".claude/skills" = {
    source = ../claude/skills;
    recursive = true;
    force = true;
  };
  home.file.".claude/skills/close-the-loop/STATE.md".source = ../x/STATE.md;
}
"""

DOTTED = """{ ... }:
{
  home.file.".claude/skills".source = ../claude/skills;
  home.file.".claude/skills".recursive = true;
}
"""

NO_MAPPING = """{ ... }:
{
  home.file.".claude/PRINCIPLES.md".source = ../claude/PRINCIPLES.md;
}
"""

NO_SOURCE = """{ ... }:
{
  home.file.".claude/skills" = { recursive = true; force = true; };
}
"""

DISABLED = """{ ... }:
{
  home.file.".claude/skills" = {
    source = ../claude/skills;
    enable = false;
    recursive = true;
  };
}
"""

# The dotted-form hole: the `enable = false` is nowhere near the block it kills.
DISABLED_ELSEWHERE = """{ ... }:
{
  home.file.".claude/skills" = { source = ../claude/skills; recursive = true; };
  home.file.".claude/PRINCIPLES.md".source = ../claude/PRINCIPLES.md;
  home.file.".claude/skills".enable = false;
}
"""

REDIRECTED = """{ ... }:
{
  home.file.".claude/skills" = {
    source = ../claude/skills;
    target = ".claude/skills-off";
    recursive = true;
  };
}
"""

#: Not valid nix. Stands in for every shape this check cannot decide.
UNPARSEABLE = """{ ... }:
{
  home.file.".claude/skills" = { source = ;
}
"""


def write(tmp_path: Path, body: str) -> Path:
    """A fixture module on disk. The `source` paths need not exist: nix is lazy
    and this predicate asks whether `source` is DECLARED, never what it is."""
    p = tmp_path / "home.nix"
    p.write_text(body, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# POSITIVE: the shipped file, and the two spellings of a live mapping.
# --------------------------------------------------------------------------

def test_the_real_home_nix_passes():
    """The load-bearing case: whatever nix/home.nix says today must satisfy it.

    If this goes red, either the mapping stopped deploying claude/skills (fix
    nix) or this check stopped understanding the file (fix the check) — do NOT
    relax it, and do NOT delete it, or the four modules that call it go back to
    pinning docs nothing ships.
    """
    assert skills_mapping_problem(HOME_NIX) is None


def test_the_attrset_form_with_unrelated_attributes_passes(tmp_path):
    """`force`/`recursive` and sibling `home.file` entries are not a problem."""
    assert skills_mapping_problem(write(tmp_path, REAL_SHAPE)) is None


def test_the_dotted_form_passes(tmp_path):
    """`home.file."…".source = …;` is the same mapping, differently spelled."""
    assert skills_mapping_problem(write(tmp_path, DOTTED)) is None


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS: one edit each, and each must go red for its OWN reason.
# --------------------------------------------------------------------------

def test_a_missing_mapping_fails(tmp_path):
    problem = skills_mapping_problem(write(tmp_path, NO_MAPPING))
    assert problem is not None, (
        "home.nix with NO ~/.claude/skills mapping was accepted — the predicate "
        "is wired to nothing and its passes mean nothing"
    )
    assert "no longer declares" in problem, problem


def test_a_mapping_without_a_source_fails(tmp_path):
    problem = skills_mapping_problem(write(tmp_path, NO_SOURCE))
    assert problem is not None, "a mapping with no `source` deploys nothing"
    assert "no `source =`" in problem, problem


def test_a_disabled_mapping_fails(tmp_path):
    """`enable = false` — declared, deployed by nothing, reads fine."""
    problem = skills_mapping_problem(write(tmp_path, DISABLED))
    assert problem is not None, (
        "`enable = false;` was accepted — one line silently stops the deploy "
        "while every SKILL.md pin stays green"
    )
    assert "switched OFF" in problem, problem


def test_a_mapping_disabled_from_a_DOTTED_line_elsewhere_fails(tmp_path):
    """The lexical hole: a regex reading the block would never see this line.

    nix merges `home.file."…".enable = false;` into the same attrset, so asking
    nix is what makes the distance between the two lines irrelevant.
    """
    problem = skills_mapping_problem(write(tmp_path, DISABLED_ELSEWHERE))
    assert problem is not None, (
        "a dotted `enable = false;` outside the mapping block was accepted — "
        "this check is reading text, not structure"
    )
    assert "switched OFF" in problem, problem


def test_a_redirected_target_fails(tmp_path):
    problem = skills_mapping_problem(write(tmp_path, REDIRECTED))
    assert problem is not None, (
        "`target = \".claude/skills-off\";` was accepted — the tree ships, just "
        "not where anything reads it"
    )
    assert "redirects `target`" in problem, problem


# --------------------------------------------------------------------------
# UNDECIDABLE INPUT MUST FAIL LOUDLY, never pass. This is the whole safety
# story of a checker this small: it has no fallback heuristics, so anything it
# cannot answer has to arrive as "fix the check", not as a green.
# --------------------------------------------------------------------------

def test_a_file_nix_cannot_evaluate_fails_and_says_do_not_delete(tmp_path):
    problem = skills_mapping_problem(write(tmp_path, UNPARSEABLE))
    assert problem is not None, "an unevaluatable home.nix passed silently"
    assert "cannot evaluate" in problem, problem
    assert "do NOT delete it" in problem, problem


def test_passing_the_files_TEXT_reads_as_a_broken_check(tmp_path):
    """The docstring below claims this case is covered; a short bogus path does
    not exercise it. Real nix source is ~160 KB, which makes `is_file()` raise
    ENAMETOOLONG rather than return False — pathlib does not swallow that errno.
    Must be a named failure, and must NOT echo the argument back."""
    text = HOME_NIX.read_text(encoding="utf-8")
    problem = skills_mapping_problem(text)
    assert problem is not None, "the file's TEXT passed silently"
    assert "do NOT delete it" in problem, problem
    assert len(problem) < 2_000, (
        f"the failure message is {len(problem):,} bytes — it is echoing the "
        "argument, which here is the whole of home.nix"
    )


#: The module is deliberately SMALL. Its predecessor reached 29,920 B chasing a
#: property it still got wrong, and was cut to ~4 KB by dropping that ambition.
#: Without a gate that ceiling is a prose intention, and this file's own history
#: shows prose intentions do not hold. Precedent: test_rules_size.py.
#:
#: Raised 5,600 -> 7,400 once, for `_budget()` + `_TIMEOUT_ENV` and the measured
#: justification of the default. That allowance is SPENT: it bought the timeout
#: knob and nothing else. It is not a general loosening, and it is not precedent
#: for re-growing the source-resolution tracing named below -- that ambition is
#: still forbidden at any byte count.
MAX_MODULE_BYTES = 7_400


def test_the_module_stays_under_its_ceiling():
    size = (ROOT / "scripts" / "testlib" / "skills_mapping.py").stat().st_size
    assert size <= MAX_MODULE_BYTES, (
        f"scripts/testlib/skills_mapping.py is {size:,} bytes\n"
        f"  ceiling: {MAX_MODULE_BYTES:,} bytes\n"
        "This module was cut from 8,846 B (and an abandoned 29,920 B rewrite) by\n"
        "DROPPING source-resolution tracing, not by golfing it. If you are over,\n"
        "the question is which ambition crept back -- $out analysis, cp/rm\n"
        "parsing, let-binding resolution, comment stripping -- not how to raise\n"
        "the number. That half is verified against reality by ship.sh and\n"
        "drift-check.sh; re-deriving it from nix source is strictly worse."
    )


# --------------------------------------------------------------------------
# THE TIMEOUT BUDGET.
#
# The old hardcoded 60 s was REACHED in CI (devrc-ci-ztn92): the check could not
# answer, said so correctly, and that correct answer was a red REQUIRED gate on
# unrelated PRs. The budget is now `DEVRC_SKILLS_MAPPING_TIMEOUT_S`.
#
# 🔴 These cases must not depend on how long real nix takes -- that is the very
# load-sensitivity being fixed, and pinning a test to it rebuilds the bug in the
# harness. So they run against a STUB `nix-instantiate` that sleeps a known
# duration and then emits the exact JSON the predicate parses. The stub is held
# fixed and the OVERRIDE is varied, so the override is the only moving part.
# --------------------------------------------------------------------------

#: Whole seconds, so the stub needs nothing beyond POSIX `sleep`. Long enough
#: that a narrow override cuts it off with room to spare, short enough that a
#: generous override finishes it well inside the suite's patience.
_STUB_SLEEP_S = 2

_LIVE_MAPPING_JSON = (
    '{"declared": true, "source": true, "enable": true, '
    '"target": ".claude/skills"}'
)


def _stub_nix_instantiate(tmp_path, monkeypatch, sleep_s=_STUB_SLEEP_S):
    """Put a slow-but-successful `nix-instantiate` at the front of PATH.

    It answers "the mapping is live", so anything the predicate returns while
    this is installed comes from the BUDGET, never from the fixture's content.
    """
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    stub = mockbin.write_exec(
        bindir / "nix-instantiate",
        f"sleep {sleep_s}\nprintf '%s' '{_LIVE_MAPPING_JSON}'\n",
    )
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return stub


def test_a_budget_narrower_than_the_call_cuts_it_off(tmp_path, monkeypatch):
    """REGRESSION. Before the fix the env var did not exist: the 60 s literal
    swallowed this stub whole and the predicate returned None -- a PASS. Red at
    base, green at HEAD."""
    _stub_nix_instantiate(tmp_path, monkeypatch)
    monkeypatch.setenv(_TIMEOUT_ENV, "0.2")
    problem = skills_mapping_problem(write(tmp_path, REAL_SHAPE))
    assert problem is not None, (
        f"{_TIMEOUT_ENV}=0.2 against a {_STUB_SLEEP_S}s call returned a PASS -- "
        "the override is being ignored, so the budget is still hardcoded"
    )
    assert "did not finish" in problem, problem


def test_a_budget_wider_than_the_call_lets_it_finish(tmp_path, monkeypatch):
    """The paired control for the case above -- NOT regression coverage: the old
    60 s default also cleared this stub, so it was green at base too.

    Its job is to isolate the variable. Same stub, same fixture, only the
    override's MAGNITUDE differs from the previous test, and the outcome
    inverts. Without it, "0.2 fails" would be equally well explained by the
    override merely being *present* rather than by its value being used.
    """
    _stub_nix_instantiate(tmp_path, monkeypatch)
    monkeypatch.setenv(_TIMEOUT_ENV, "300")
    assert skills_mapping_problem(write(tmp_path, REAL_SHAPE)) is None


def test_the_timeout_message_names_the_knob_and_refuses_deletion(tmp_path, monkeypatch):
    """REGRESSION. The pre-fix message named no knob, so the only actions it
    suggested were "fix the check" or (against its own advice) delete it. A
    maintainer hitting this in CI needs the third option spelled out."""
    _stub_nix_instantiate(tmp_path, monkeypatch)
    monkeypatch.setenv(_TIMEOUT_ENV, "0.2")
    problem = skills_mapping_problem(write(tmp_path, REAL_SHAPE))
    assert problem is not None
    assert _TIMEOUT_ENV in problem, (
        "the timeout message does not name the env var that widens it:\n" + problem
    )
    assert "do NOT delete it" in problem, problem


def test_the_timeout_path_RAISES_rather_than_passing_or_skipping(tmp_path, monkeypatch):
    """Fail-closed, measured at the CALLER's surface.

    The four modules that consume this call `assert_skills_mapping_declared`,
    not the predicate. "Could not answer" has to arrive there as a FAILURE --
    not None, and emphatically not `pytest.skip`, which is how a check quietly
    stops being one.
    """
    _stub_nix_instantiate(tmp_path, monkeypatch)
    monkeypatch.setenv(_TIMEOUT_ENV, "0.2")
    with pytest.raises(AssertionError) as excinfo:
        assert_skills_mapping_declared(write(tmp_path, REAL_SHAPE))
    assert "did not finish" in str(excinfo.value), excinfo.value


@pytest.mark.parametrize("junk", ["abc", "0", "-5", "nan", "inf", "1e400", "5s"])
def test_an_UNUSABLE_override_fails_closed_instead_of_falling_back(
    tmp_path, monkeypatch, junk
):
    """REGRESSION, and the reason this knob is not just `int(os.environ[...])`.

    A knob that silently reverts to its default when it cannot be read is a knob
    that disables the check on a typo: `…=0` would otherwise mean "budget zero"
    or "budget 180" depending on the parser, and nobody would be told which.
    Every value here must land as "cannot answer", naming the value.
    """
    _stub_nix_instantiate(tmp_path, monkeypatch, sleep_s=0)
    monkeypatch.setenv(_TIMEOUT_ENV, junk)
    problem = skills_mapping_problem(write(tmp_path, REAL_SHAPE))
    assert problem is not None, (
        f"{_TIMEOUT_ENV}={junk!r} was accepted -- an unreadable budget silently "
        "became the default, so a typo in this variable cannot be noticed"
    )
    assert _TIMEOUT_ENV in problem and repr(junk) in problem, problem
    assert "do NOT delete it" in problem, problem


@pytest.mark.parametrize("unset", ["", "   "])
def test_an_EMPTY_override_means_the_default_not_an_error(tmp_path, monkeypatch, unset):
    """An exported-but-empty variable is how a shell spells "I did not set
    this". Treating it as junk would fail the gate on an empty export."""
    _stub_nix_instantiate(tmp_path, monkeypatch, sleep_s=0)
    monkeypatch.setenv(_TIMEOUT_ENV, unset)
    assert skills_mapping_problem(write(tmp_path, REAL_SHAPE)) is None


def test_the_default_budget_stays_far_above_the_MEASURED_cost():
    """INVARIANT GUARD, not regression coverage -- a ratchet on a judgement call.

    Measured on an idle 24-core workbench: 0.02 s, cold == warm. Under CPU
    oversubscription it rises roughly linearly -- 0.06 s at 1x, 0.26 s at 4x,
    1.8 s at 10x -- and CI stacks cgroup throttling on top of that inside the
    `checks.pytests` sandbox, which is how 60 s was reached at all.

    The floor below is deliberately well under the shipped default: this pins
    the ORDER OF MAGNITUDE ("a deadman, not a performance budget"), and is not a
    restatement of the constant. Lowering the default back toward the contended
    measurements re-opens devrc-ci-ztn92.
    """
    assert _DEFAULT_TIMEOUT_S >= 120, (
        f"the default budget is {_DEFAULT_TIMEOUT_S}s. The old 60 s literal was "
        "REACHED in CI; anything near the contended measurements above makes "
        "'the check could not answer' a routine red gate on unrelated PRs."
    )


# --------------------------------------------------------------------------
# THE DISTINCTION THAT MAKES THIS CHECK WORTH HAVING.
# --------------------------------------------------------------------------

def test_cannot_answer_and_answer_is_no_stay_TELLABLE_APART(tmp_path, monkeypatch):
    """INVARIANT GUARD -- true before this change too, and load-bearing for it.

    "I could not answer" and "the answer is no" demand opposite responses: fix
    the harness, versus fix nix/home.nix. The predicate keeps them separable by
    appending `_FIX_IT` ("do NOT delete it") to every could-not-answer reason
    and to NO answer-is-no reason. Adding the timeout knob added a new member to
    each set, which is exactly when such a partition rots.

    Pinned as a PARTITION over both sets rather than one example from each, so a
    future reason cannot join the wrong side unnoticed.
    """
    stub_tmp = tmp_path / "stub"
    stub_tmp.mkdir()

    answer_is_no = {
        "no mapping": skills_mapping_problem(write(tmp_path, NO_MAPPING)),
        "no source": skills_mapping_problem(write(tmp_path, NO_SOURCE)),
        "disabled": skills_mapping_problem(write(tmp_path, DISABLED)),
        "redirected": skills_mapping_problem(write(tmp_path, REDIRECTED)),
    }
    cannot_answer = {
        "absent file": skills_mapping_problem(tmp_path / "nope.nix"),
        "unparseable": skills_mapping_problem(write(tmp_path, UNPARSEABLE)),
    }

    _stub_nix_instantiate(stub_tmp, monkeypatch)
    monkeypatch.setenv(_TIMEOUT_ENV, "0.2")
    cannot_answer["timed out"] = skills_mapping_problem(write(stub_tmp, REAL_SHAPE))
    monkeypatch.setenv(_TIMEOUT_ENV, "junk")
    cannot_answer["junk budget"] = skills_mapping_problem(write(stub_tmp, REAL_SHAPE))

    for name, problem in {**answer_is_no, **cannot_answer}.items():
        assert problem is not None, f"{name} produced a PASS"

    for name, problem in cannot_answer.items():
        assert "do NOT delete it" in problem, (
            f"could-not-answer reason {name!r} is missing the fix-the-check "
            f"marker, so it reads as a verdict on home.nix:\n{problem}"
        )
    for name, problem in answer_is_no.items():
        assert "do NOT delete it" not in problem, (
            f"answer-is-no reason {name!r} carries the fix-the-check marker, so "
            f"a real broken deploy now reads as a broken harness:\n{problem}"
        )


def test_a_path_that_is_not_a_file_fails(tmp_path):
    """Named as its own failure, not left to nix's "file not found" — a caller
    passing the wrong thing (the file's TEXT, say) must read as a broken check."""
    problem = skills_mapping_problem(tmp_path / "absent.nix")
    assert problem is not None, "a nonexistent home.nix passed silently"
    assert "is not a file" in problem, problem
    assert "do NOT delete it" in problem, problem
