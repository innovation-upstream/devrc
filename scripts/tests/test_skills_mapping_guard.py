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

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.skills_mapping import skills_mapping_problem  # noqa: E402

HOME_NIX = ROOT / "nix" / "home.nix"

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


def test_a_path_that_is_not_a_file_fails(tmp_path):
    """Named as its own failure, not left to nix's "file not found" — a caller
    passing the wrong thing (the file's TEXT, say) must read as a broken check."""
    problem = skills_mapping_problem(tmp_path / "absent.nix")
    assert problem is not None, "a nonexistent home.nix passed silently"
    assert "is not a file" in problem, problem
    assert "do NOT delete it" in problem, problem
