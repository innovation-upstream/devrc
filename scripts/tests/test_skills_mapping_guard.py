"""Controls for `testlib.skills_mapping` — the shared "does nix actually deploy
claude/skills?" predicate that three subsystem test modules rest on.

WHY THIS EXISTS
---------------
The predicate is what makes those modules' SKILL.md pins claims about the
DEPLOYED file rather than about a path in the repo. It used to be three
open-coded copies of ``"source = ../claude/skills;" in home_nix`` — a SPELLED
guard: it pinned how the source is written, not what it resolves to. It went red
for a change that did not touch the property (the source became a derivation
built from that path, injecting the clickup skill's nix-built node_modules into
the store copy).

A predicate that is now permissive enough to accept an indirection has to be
shown it can still REJECT. So: the real home.nix must pass, and each way of
actually breaking the deployment must fail — including the one the loosening
could plausibly have let through, a source bound to an unrelated tree.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.skills_mapping import skills_mapping_problem  # noqa: E402

HOME_NIX = ROOT / "nix" / "home.nix"

DIRECT = '''
{
  home.file.".claude/skills" = {
    source = ../claude/skills;
    recursive = true;
    force = true;
  };
}
'''

INDIRECT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
    recursive = true;
    force = true;
  };
}
'''

WRONG_TREE = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claudedocs} "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
    recursive = true;
    force = true;
  };
}
'''

NO_MAPPING = '''
{
  home.file.".claude/PRINCIPLES.md".source = ../claude/PRINCIPLES.md;
}
'''

NO_SOURCE = '''
{
  home.file.".claude/skills" = {
    recursive = true;
    force = true;
  };
}
'''


# --------------------------------------------------------------------------
# POSITIVE: the shipped file, and both accepted shapes.
# --------------------------------------------------------------------------

def test_the_real_home_nix_passes():
    """The load-bearing case: whatever nix/home.nix says today must satisfy it.

    If this goes red, either the mapping stopped deploying claude/skills (fix
    nix) or the parser stopped understanding it (fix the parser) — do NOT relax
    the predicate to make it green, or the three modules that call it go back to
    pinning docs nothing ships.
    """
    assert skills_mapping_problem(HOME_NIX.read_text(encoding="utf-8")) is None


def test_the_direct_form_passes():
    assert skills_mapping_problem(DIRECT) is None


def test_the_indirect_form_passes():
    """A source bound to a derivation BUILT from ../claude/skills."""
    assert skills_mapping_problem(INDIRECT) is None


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS: it must still be able to go red, for each real breakage.
# --------------------------------------------------------------------------

def test_a_source_built_from_an_unrelated_tree_fails():
    """The case the loosening could have let through.

    Accepting `source = <ident>;` is only safe while the binding is checked. A
    mapping sourcing a derivation built from somewhere else deploys a different
    tree, and every SKILL.md pin under claude/skills/ becomes vacuous.
    """
    problem = skills_mapping_problem(WRONG_TREE)
    assert problem is not None, (
        "a ~/.claude/skills mapping sourced from an UNRELATED tree was accepted — "
        "the predicate is wired to nothing and its passes mean nothing"
    )
    assert "claudeSkills" in problem


def test_a_missing_mapping_fails():
    problem = skills_mapping_problem(NO_MAPPING)
    assert problem is not None
    assert "no longer declares" in problem


def test_a_mapping_without_a_source_fails():
    problem = skills_mapping_problem(NO_SOURCE)
    assert problem is not None
    assert "no `source =`" in problem
