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

# The real binding's shape: a second store path is symlinked in by IDENT, which
# is not a repo tree and must not be mistaken for one.
WITH_INJECTION = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    chmod -R u+w "$out"
    ln -sT ${clickupNodeModules}/node_modules "$out/clickup/node_modules"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
    recursive = true;
    force = true;
  };
}
'''

# A `\`-continued copy: the path and the $out reference are on DIFFERENT lines.
CONTINUED = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R \\
      ${../claude/skills} \\
      "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

# --- the three ways the OLD substring check was too weak -------------------

#: Mentions the path in a nix COMMENT while copying somewhere else.
COMMENT_ONLY = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    # was: cp -R ${../claude/skills} "$out"
    cp -R ${../claudedocs} "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: Mentions it in a SHELL comment inside the build script -- dead code that nix
#: still interpolates, so even a "does the path resolve" check would be fooled.
SHELL_COMMENT_ONLY = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claudedocs} "$out"
    # keep for reference: ${../claude/skills}
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: Copies the right tree, then empties $out and substitutes another.
RM_AND_SUBSTITUTE = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    rm -rf "$out"
    cp -R ${../claudedocs} "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: Copies a SECOND repo tree over the same output, without removing anything.
SECOND_TREE = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    cp -R ${../claudedocs}/. "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: Copies the right tree and then empties $out, with NO second tree involved.
#: Needed as a distinct fixture so the `rm $out` check is REACHABLE: in
#: RM_AND_SUBSTITUTE the second-tree check fires first, so that fixture alone
#: would leave the removal check untested behind an earlier guard.
RM_ONLY = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    rm -rf "$out"
    mkdir -p "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: A source bound to an ident that has no binding at all.
DANGLING_IDENT = '''
{
  home.file.".claude/skills" = {
    source = somethingUndefined;
    recursive = true;
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


# --------------------------------------------------------------------------
# POSITIVE, continued: shapes the TIGHTENED predicate must still accept.
# A guard that rejects the legitimate case gets loosened back, so these matter
# as much as the negatives below.
# --------------------------------------------------------------------------

def test_a_symlinked_store_path_is_not_mistaken_for_a_second_tree():
    """`ln -sT ${clickupNodeModules}/… "$out/…"` is the real binding's shape."""
    assert skills_mapping_problem(WITH_INJECTION) is None


def test_a_line_continued_copy_passes():
    """The path and `$out` on different lines is still one copy."""
    assert skills_mapping_problem(CONTINUED) is None


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS for the three weaknesses of the substring version.
# Each was ACCEPTED before the tightening: the predicate asked whether the
# characters `../claude/skills` occurred in the binding, not what they did.
# --------------------------------------------------------------------------

def test_a_path_named_only_in_a_nix_comment_fails():
    problem = skills_mapping_problem(COMMENT_ONLY)
    assert problem is not None, (
        "a binding that copies ../claudedocs and only MENTIONS ../claude/skills in a "
        "comment was accepted — the predicate is reading prose as if it were code"
    )
    assert "never copies" in problem, problem


def test_a_path_named_only_in_dead_shell_code_fails():
    problem = skills_mapping_problem(SHELL_COMMENT_ONLY)
    assert problem is not None, (
        "a binding whose only use of ../claude/skills is a commented-out shell line "
        "was accepted"
    )
    assert "never copies" in problem, problem


def test_copying_the_tree_and_then_emptying_out_fails():
    problem = skills_mapping_problem(RM_AND_SUBSTITUTE)
    assert problem is not None, (
        "a binding that copies claude/skills, then `rm -rf \"$out\"` and copies another "
        "tree over it was accepted — it contains every string the check looks for and "
        "deploys none of them"
    )
    # It must fail for one of the two REAL reasons, not incidentally.
    assert ("removes its own output" in problem) or ("as well as" in problem), problem


def test_copying_a_second_repo_tree_over_the_output_fails():
    problem = skills_mapping_problem(SECOND_TREE)
    assert problem is not None, (
        "a binding that copies a SECOND repo tree over $out was accepted — whichever "
        "copy runs last decides what ships"
    )
    assert "as well as" in problem, problem


def test_emptying_out_after_copying_fails_on_its_own():
    """REACHABILITY for the `rm $out` check.

    RM_AND_SUBSTITUTE trips the second-tree check first, so it would pass with
    the removal check deleted — a guard sitting behind an earlier one that
    always wins. This fixture reaches it: one tree, copied, then removed.
    """
    problem = skills_mapping_problem(RM_ONLY)
    assert problem is not None, (
        "a binding that copies claude/skills and then `rm -rf \"$out\"` was accepted — "
        "it deploys an EMPTY tree"
    )
    assert "removes its own output" in problem, problem


def test_a_source_bound_to_a_nonexistent_ident_fails():
    problem = skills_mapping_problem(DANGLING_IDENT)
    assert problem is not None
    assert "no such let-binding" in problem, problem
