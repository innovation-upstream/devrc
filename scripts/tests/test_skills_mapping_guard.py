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

WHAT ISSUE #443 ADDED
---------------------
Two groups of fixtures, one per direction the parser was wrong.

*The dotted form.* nix spells one `home.file` entry two ways, and this file only
ever fixtured the attribute-set one. The predicate located the mapping's attrs
with "the next `{` ANYWHERE in the file", so for `home.file.".claude/skills"
.source = ../claudedocs;` it walked past the real source into an unrelated block
and returned CLEAN. Its absence here is precisely why that shipped, so the
dotted form is now fixtured in both directions — correct and wrong-tree — and
the wrong-tree case asserts on WHICH path the message names, because the failure
mode was a confident verdict about a block that was not the mapping's.

*Five legitimate shapes it rejected.* `src =`, `paths = [ … ]`, one more hop of
let-indirection, `rm -rf "$out/sub"`, and a file copied to `"$out/name"`. These
are MUST-NOT-FIRE controls: a guard that cries wolf on the real thing gets
deleted, so they are load-bearing in the same way the negatives are. Each is
paired with a wrong-tree twin below, so widening the parser to accept the shape
is shown not to have accepted the HAZARD in that shape.
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
# ISSUE #443, DIRECTION 1 — the DOTTED form. `home.file."…".source = <expr>;`
# has no attribute set of its own, and home.nix already uses it ~20 times.
# --------------------------------------------------------------------------

#: The dotted form, deploying the right tree.
DOTTED = '''
{
  home.file.".claude/skills".source = ../claude/skills;
  home.file.".claude/skills".recursive = true;
}
'''

#: The verified false ALL-CLEAR: dotted source pointing at an unrelated tree,
#: with an attrset mapping BELOW it that does name ../claude/skills. The old
#: `find("{", start)` scan skipped over the real source and read this one.
DOTTED_WRONG_TREE = '''
{
  home.file.".claude/skills".source = ../claudedocs;
  home.file.".config/opencode/skills" = { source = ../claude/skills; recursive = true; };
}
'''

#: The other verified variant: the next `{` in the file belongs to something
#: else entirely, and the predicate reported "declares no `source =` at all"
#: while quoting an unrelated block — confidently wrong about the wrong text.
DOTTED_WRONG_TREE_DECOY = '''
{
  home.file.".claude/skills".source = ../claudedocs;
  services.dunst = { enable = true; };
}
'''

#: The dotted form reaching the deployed tree through the let-binding, i.e. the
#: real home.nix's source expression written in the other spelling.
DOTTED_INDIRECT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
  \'\';
in
  home.file.".claude/skills".source = claudeSkills;
}
'''

#: Dotted, wrong tree, through the binding — the indirection must not launder it.
DOTTED_INDIRECT_WRONG_TREE = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claudedocs} "$out"
  \'\';
in
  home.file.".claude/skills".source = claudeSkills;
}
'''

#: The mapping named ONLY in a comment. Deleting the entry and leaving the note
#: must read as "no longer declares", not as a live mapping.
MAPPING_ONLY_IN_A_COMMENT = '''
{
  # was: home.file.".claude/skills".source = ../claude/skills;
  home.file.".claude/PRINCIPLES.md".source = ../claude/PRINCIPLES.md;
}
'''

#: A source expression that is neither a path nor an identifier. Undecidable —
#: and undecidable must be LOUD, or the mapping stops being checked at all.
UNREADABLE_SOURCE = '''
{
  home.file.".claude/skills".source =
    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/claude/skills";
}
'''

# --------------------------------------------------------------------------
# ISSUE #443, DIRECTION 2 — five legitimate shapes rejected with a confident,
# wrong "the deploy is broken". Each is followed by its wrong-tree twin.
# --------------------------------------------------------------------------

#: `mkDerivation { src = …; }` — the output tree comes from `src`, and the
#: builder never names `$out` in nix at all.
MKDERIVATION_SRC = '''
{
let
  claudeSkills = pkgs.stdenv.mkDerivation {
    name = "devrc-claude-skills";
    src = ../claude/skills;
    installPhase = "cp -R ./. $out";
  };
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

MKDERIVATION_SRC_WRONG_TREE = '''
{
let
  claudeSkills = pkgs.stdenv.mkDerivation {
    name = "devrc-claude-skills";
    src = ../claudedocs;
    installPhase = "cp -R ./. $out";
  };
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: `symlinkJoin { paths = [ … ]; }` — same, via a list. The second entry is a
#: derivation, not a repo tree, and must not be reported as one.
SYMLINKJOIN_PATHS = '''
{
let
  claudeSkills = pkgs.symlinkJoin {
    name = "devrc-claude-skills";
    paths = [ ../claude/skills clickupNodeModules ];
  };
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: Two REPO trees merged into one output root is the hazard the widening must
#: not have accepted: whichever wins the join decides what ships.
SYMLINKJOIN_SECOND_TREE = '''
{
let
  claudeSkills = pkgs.symlinkJoin {
    name = "devrc-claude-skills";
    paths = [ ../claude/skills ../claudedocs ];
  };
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: One more hop: the path is a let-binding of its own, interpolated by name.
EXTRA_LET_INDIRECTION = '''
{
let
  skillsSrc = ../claude/skills;
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${skillsSrc} "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

EXTRA_LET_INDIRECTION_WRONG_TREE = '''
{
let
  skillsSrc = ../claudedocs;
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${skillsSrc} "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: The source itself bound straight to a path by name — no derivation at all.
SOURCE_IDENT_IS_A_PATH = '''
{
let
  skillsSrc = ../claude/skills;
in
  home.file.".claude/skills".source = skillsSrc;
}
'''

SOURCE_IDENT_IS_THE_WRONG_PATH = '''
{
let
  skillsSrc = ../claudedocs;
in
  home.file.".claude/skills".source = skillsSrc;
}
'''

#: `rm -rf "$out/clickup/node_modules"` as a cleanup step — a removal UNDER the
#: output, one line from the code it guards. The most likely near-term trip.
RM_UNDER_OUT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    chmod -R u+w "$out"
    rm -rf "$out/clickup/node_modules"
    ln -sT ${clickupNodeModules}/node_modules "$out/clickup/node_modules"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: A single FILE added at a named path under $out. Additive — it cannot be the
#: "second tree written over the same output" the guard is about.
EXTRA_FILE_COPIED_IN = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    chmod -R u+w "$out"
    cp ${../claude/PRINCIPLES.md} "$out/PRINCIPLES.md"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: …and the twin: the same second tree written over the output ROOT instead of
#: to a name under it. Scoping the destination must not have cost the check.
EXTRA_TREE_OVER_ROOT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    chmod -R u+w "$out"
    cp -R ${../claudedocs}/. "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: The output root spelled `"$out/"` rather than `"$out"`, and the source with
#: the `cp -R src/. dst` idiom. Same write; the guard must read it as the root,
#: or a legitimate binding is rejected.
ROOT_SPELLED_WITH_A_SLASH = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    mkdir -p "$out"
    cp -R ${../claude/skills}/. "$out/"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: …and its twin: a second tree over `"$out/."`, which is still the root. If
#: only the bare `"$out"` spelling counts, this shape walks straight through.
SECOND_TREE_OVER_ROOT_DOT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    cp -R ${../claudedocs}/. "$out/."
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: A path literal written `../claude/skills/.` — VALID nix naming the same tree
#: (verified with `nix-instantiate --parse`; note `../claude/skills/` is a
#: syntax error, so the trailing `/.` is the only spelling to handle).
PATHS_WITH_A_TRAILING_DOT = '''
{
let
  claudeSkills = pkgs.symlinkJoin {
    name = "devrc-claude-skills";
    paths = [ ../claude/skills/. clickupNodeModules ];
  };
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: A let-binding whose NAME collides with a component of the path. Resolving
#: identifiers inside a `src =` value must not read `../claude/skills` as a
#: reference to a binding called `skills`.
PATH_COMPONENT_SHADOWED_BY_A_BINDING = '''
{
let
  skills = ../claudedocs;
  claudeSkills = pkgs.stdenv.mkDerivation {
    name = "devrc-claude-skills";
    src = ../claude/skills;
    installPhase = "cp -R ./. $out";
  };
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

#: An attrset mapping with NO source, followed by a sibling that HAS one. The
#: attribute-set twin of the dotted defect: if the brace scan runs past the
#: mapping's own `}`, the sibling's source is read as the mapping's.
#: (Found by mutating `if depth == 0` in the brace scan — the fixtures above
#: could not tell the difference.)
NO_SOURCE_WITH_A_SIBLING_THAT_HAS_ONE = '''
{
  home.file.".claude/skills" = {
    recursive = true;
    force = true;
  };
  home.file.".config/opencode/skills" = {
    source = ../claude/skills;
    recursive = true;
  };
}
'''

#: A binding that MENTIONS the tree but never writes the output at all — the
#: derivation ships an empty $out. Distinct from "copies the wrong tree": the
#: message has to say there is no repo tree in it, not name one.
NOTHING_WRITTEN_TO_OUT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    echo ${../claude/skills} > /dev/null
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
  };
}
'''

# --- malformed nix. It cannot be the real home.nix (it would not build), but
# "the parser fell off the end of the file" must never come out as a PASS. ---

#: A dotted source with no terminating `;`.
DOTTED_NO_SEMICOLON = '''
{
  home.file.".claude/skills".source = ../claude/skills
}
'''

#: An attribute set that never closes.
ATTRSET_NEVER_CLOSES = '''
{
  home.file.".claude/skills" = {
    source = ../claude/skills;
'''

#: An attribute set whose `source =` has no terminating `;`.
ATTRSET_SOURCE_NO_SEMICOLON = '''
{
  home.file.".claude/skills" = { source = ../claude/skills }
}
'''

#: A mapping wrapped in something this does not parse. Not a pass, and not a
#: claim that the deploy is broken either.
MAPPING_BEHIND_MKIF = '''
{
  home.file.".claude/skills" = lib.mkIf config.programs.claude.enable {
    source = ../claude/skills;
  };
}
'''

#: An UNRESOLVABLE identifier written over the output root. Not a repo path, so
#: nothing here can say what ships — the parser must say so, not stay silent.
OPAQUE_IDENT_OVER_ROOT = '''
{
let
  claudeSkills = pkgs.runCommandLocal "devrc-claude-skills" { } \'\'
    cp -R ${../claude/skills} "$out"
    cp -R ${someOtherDerivation}/. "$out"
  \'\';
in
  home.file.".claude/skills" = {
    source = claudeSkills;
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


# --------------------------------------------------------------------------
# ISSUE #443, DIRECTION 1 — the dotted form, which had NO fixture at all.
# --------------------------------------------------------------------------

def test_the_dotted_form_passes():
    """`home.file."…".source = ../claude/skills;` — the other legal spelling."""
    assert skills_mapping_problem(DOTTED) is None


def test_a_dotted_source_pointing_at_another_tree_fails():
    """The verified false ALL-CLEAR of #443.

    `find("{", start)` took the next `{` anywhere in the file, so with the
    dotted form it read the source of a LATER, unrelated mapping — one that does
    name ../claude/skills — and returned clean while ~/.claude/skills deployed
    ../claudedocs. Asserting on the named path, not merely on non-None: the
    defect was a confident verdict about text that was not the mapping's.
    """
    problem = skills_mapping_problem(DOTTED_WRONG_TREE)
    assert problem is not None, (
        "a DOTTED ~/.claude/skills mapping sourced from ../claudedocs was accepted "
        "because a LATER mapping in the file mentions ../claude/skills — the "
        "predicate is reading someone else's attribute set"
    )
    assert "../claudedocs" in problem, problem


def test_a_dotted_source_is_not_diagnosed_from_an_unrelated_block():
    """The second verified variant: right verdict, wrong reason, is not enough.

    Here the next `{` belonged to `services.dunst`, and the predicate reported
    "declares no `source =` at all" while quoting `{ enable = true; }`. It DID
    go red, so a non-None assertion passes on the broken parser — the message is
    what distinguishes reading the mapping from reading whatever came next.
    """
    problem = skills_mapping_problem(DOTTED_WRONG_TREE_DECOY)
    assert problem is not None, problem
    assert "../claudedocs" in problem, problem
    assert "no `source =`" not in problem, (
        "the dotted mapping HAS a source; reporting that it has none means the "
        f"parser is quoting an unrelated block: {problem}"
    )
    assert "enable = true" not in problem, problem


def test_the_dotted_form_follows_the_let_binding():
    assert skills_mapping_problem(DOTTED_INDIRECT) is None


def test_a_dotted_source_built_from_an_unrelated_tree_fails():
    problem = skills_mapping_problem(DOTTED_INDIRECT_WRONG_TREE)
    assert problem is not None, (
        "the dotted spelling laundered a binding built from ../claudedocs"
    )
    assert "never copies" in problem, problem


def test_a_mapping_named_only_in_a_comment_does_not_count():
    problem = skills_mapping_problem(MAPPING_ONLY_IN_A_COMMENT)
    assert problem is not None, (
        "a commented-out mapping was read as a live one — deleting the entry and "
        "leaving the note would then look healthy"
    )
    assert "no longer declares" in problem, problem


def test_an_unreadable_source_expression_says_the_parser_needs_updating():
    """Undecidable must be LOUD.

    A source this cannot resolve is not a pass. It is also not "the deploy is
    broken" — the message has to send the reader to the parser, because the one
    answer that silently stops checking anything is None.
    """
    problem = skills_mapping_problem(UNREADABLE_SOURCE)
    assert problem is not None, (
        "a source expression the parser cannot resolve was accepted — the check "
        "would then be wired to nothing"
    )
    assert "do NOT delete the check" in problem, problem


# --------------------------------------------------------------------------
# ISSUE #443, DIRECTION 2 — the five legitimate shapes it rejected.
# MUST-NOT-FIRE controls, each paired with its wrong-tree twin so the widening
# is shown to have kept the property.
# --------------------------------------------------------------------------

def test_a_derivation_whose_src_is_the_skills_tree_passes():
    """`src = ../claude/skills;` — a plain path attribute, no `${…}` anywhere."""
    assert skills_mapping_problem(MKDERIVATION_SRC) is None


def test_a_derivation_whose_src_is_another_tree_fails():
    problem = skills_mapping_problem(MKDERIVATION_SRC_WRONG_TREE)
    assert problem is not None, (
        "reading `src =` as a copy accepted a derivation built from ../claudedocs"
    )
    assert "never copies" in problem, problem


def test_a_symlinkjoin_over_the_skills_tree_passes():
    """`paths = [ ../claude/skills clickupNodeModules ]` — a path in a list."""
    assert skills_mapping_problem(SYMLINKJOIN_PATHS) is None


def test_a_symlinkjoin_that_merges_a_second_repo_tree_fails():
    problem = skills_mapping_problem(SYMLINKJOIN_SECOND_TREE)
    assert problem is not None, (
        "two repo trees merged into one output root were accepted — whichever wins "
        "the join decides what ships"
    )
    assert "as well as" in problem, problem


def test_one_more_hop_of_let_indirection_passes():
    """`skillsSrc = ../claude/skills;` then `${skillsSrc}`."""
    assert skills_mapping_problem(EXTRA_LET_INDIRECTION) is None


def test_one_more_hop_of_let_indirection_to_another_tree_fails():
    problem = skills_mapping_problem(EXTRA_LET_INDIRECTION_WRONG_TREE)
    assert problem is not None, (
        "following an identifier to a path accepted ../claudedocs — the extra hop "
        "laundered the wrong tree"
    )
    assert "never copies" in problem, problem


def test_a_source_bound_directly_to_the_skills_path_by_name_passes():
    assert skills_mapping_problem(SOURCE_IDENT_IS_A_PATH) is None


def test_a_source_bound_directly_to_another_path_by_name_fails():
    problem = skills_mapping_problem(SOURCE_IDENT_IS_THE_WRONG_PATH)
    assert problem is not None, (
        "`source = skillsSrc;` with `skillsSrc = ../claudedocs;` was accepted"
    )
    assert "../claudedocs" in problem, problem


def test_removing_a_path_UNDER_out_is_not_removing_out():
    """`rm -rf "$out/clickup/node_modules"` is a cleanup step, not a wipe.

    The old check matched any `rm` naming `$out`. This shape sits one line from
    the injection it guards, so it was the likeliest trip — and it arrives as
    "the binding removes its own output", which reads as a broken deploy.
    """
    assert skills_mapping_problem(RM_UNDER_OUT) is None


def test_a_file_copied_to_a_name_under_out_is_not_a_second_tree():
    """`cp ${../claude/PRINCIPLES.md} "$out/PRINCIPLES.md"` ADDS; it cannot
    replace the skills tree."""
    assert skills_mapping_problem(EXTRA_FILE_COPIED_IN) is None


def test_a_second_tree_written_over_the_output_ROOT_still_fails():
    """The twin of the fixture above — scoping the destination is the whole
    difference, so both directions have to be pinned."""
    problem = skills_mapping_problem(EXTRA_TREE_OVER_ROOT)
    assert problem is not None, (
        "distinguishing `$out/name` from `$out` cost the second-tree check: a tree "
        "copied over the output root was accepted"
    )
    assert "as well as" in problem, problem


def test_the_output_root_spelled_with_a_trailing_slash_is_still_the_root():
    """`"$out/"` and `cp -R src/. dst` — the same write, spelled the other way."""
    assert skills_mapping_problem(ROOT_SPELLED_WITH_A_SLASH) is None


def test_a_second_tree_over_out_dot_still_fails():
    """`"$out/."` is the output root too. Recognising only the bare `"$out"`
    spelling would let this shape through as an addition."""
    problem = skills_mapping_problem(SECOND_TREE_OVER_ROOT_DOT)
    assert problem is not None, (
        "a second repo tree copied over `\"$out/.\"` was read as an addition — the "
        "root-vs-under split is matching a spelling, not a destination"
    )
    assert "as well as" in problem, problem


def test_a_path_literal_with_a_trailing_dot_names_the_same_tree():
    """`../claude/skills/.` parses (nix-instantiate) and resolves to the skills
    tree; `../claude/skills/` does not parse at all, so this is the one variant
    spelling that can legitimately reach the guard."""
    assert skills_mapping_problem(PATHS_WITH_A_TRAILING_DOT) is None


def test_a_binding_named_like_a_path_component_is_not_a_reference_to_it():
    """`src = ../claude/skills;` names a PATH, not the binding `skills`.

    Identifiers in a derivation attribute are resolved one hop, so the scan has
    to stop at `/`. Without that, the `skills` component resolves to whatever a
    binding of that name holds and the guard reports a second tree that is not
    there — a confident, wrong "the deploy is broken".
    """
    assert skills_mapping_problem(PATH_COMPONENT_SHADOWED_BY_A_BINDING) is None


def test_a_sourceless_mapping_does_not_borrow_a_siblings_source():
    """The attribute-set twin of the dotted defect.

    The mapping has no `source` of its own; the NEXT one does, and names the
    right tree. A brace scan that runs past the mapping's own `}` reads the
    sibling's and reports a clean deploy — the same class of false all-clear as
    #443, in the shape the existing fixtures could not distinguish.
    """
    problem = skills_mapping_problem(NO_SOURCE_WITH_A_SIBLING_THAT_HAS_ONE)
    assert problem is not None, (
        "a ~/.claude/skills mapping with NO source was accepted because a LATER "
        "mapping has one — the brace scan is reading past the mapping's own block"
    )
    assert "no `source =`" in problem, problem


def test_a_binding_that_writes_nothing_to_out_fails():
    """A derivation that mentions the tree and ships an EMPTY output.

    Different failure from "copies the wrong tree", and it has to read that way:
    the message must say no repo tree reaches $out, not name one that does.
    """
    problem = skills_mapping_problem(NOTHING_WRITTEN_TO_OUT)
    assert problem is not None, (
        "a binding whose $out is never written was accepted — it deploys nothing"
    )
    assert "no repo tree at all" in problem, problem


def test_malformed_nix_is_reported_as_a_parser_problem_not_a_pass():
    """Truncated/unterminated input must not fall through to None.

    None is the one answer that silently stops checking anything, so every way
    the scans can run off the end is pinned — each of these branches was
    unreached until an automated sweep turned it into `return None` and no test
    noticed.
    """
    for name, fixture in (
        ("dotted source with no `;`", DOTTED_NO_SEMICOLON),
        ("attribute set that never closes", ATTRSET_NEVER_CLOSES),
        ("attrset `source =` with no `;`", ATTRSET_SOURCE_NO_SEMICOLON),
        ("mapping behind an unparsed wrapper", MAPPING_BEHIND_MKIF),
    ):
        problem = skills_mapping_problem(fixture)
        assert problem is not None, f"{name} was accepted as a valid deploy"
        assert "do NOT delete the check" in problem, f"{name}: {problem}"


def test_an_opaque_identifier_written_over_the_output_root_is_not_silently_ok():
    """`cp -R ${someOtherDerivation}/. "$out"` — not a repo path, so nothing here
    can say what ships. That is a case for the parser, not a pass."""
    problem = skills_mapping_problem(OPAQUE_IDENT_OVER_ROOT)
    assert problem is not None, (
        "an unresolvable derivation copied over the output ROOT was accepted — "
        "whichever write runs last decides what ships and this check cannot tell"
    )
    assert "do NOT delete the check" in problem, problem
