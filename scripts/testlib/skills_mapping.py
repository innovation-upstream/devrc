"""One place that answers "does nix/home.nix actually deploy `claude/skills/`?".

WHY THIS EXISTS
---------------
Three test modules (test_subsystem_recall, test_subsystem_resolver,
test_subsystem_touch) each pin a SKILL.md and then check that the file they
pinned is the one that SHIPS -- otherwise the pin is a claim about a file
nothing deploys. All three asked the same question with the same open-coded
substring::

    assert 'home.file.".claude/skills"' in home_nix
    assert "source = ../claude/skills;" in home_nix

That second line is a SPELLED guard, not a structural one: it pins how the
source is written, not what it resolves to. When the mapping's source became a
derivation built FROM `../claude/skills` (`claudeSkills`, which injects the
clickup skill's nix-built node_modules into the store copy -- node resolves
modules from the REALPATH, so a node_modules symlink at the deployed path is
invisible), the deployment property was unchanged and all three went red.

Three copies of one predicate is three chances to fix it differently. This is
the predicate, once. It accepts either shape and, for the indirection, insists
the binding is genuinely built from `../claude/skills` -- so a source pointed at
some UNRELATED tree still fails, which is the case the original was defending
against.

WHY IT IS NOT JUST A SUBSTRING SEARCH ANY MORE
----------------------------------------------
The first version of the indirect check was ``"../claude/skills" not in
_binding_body(...)`` -- weaker than the three open-coded assertions it replaced,
in three specific ways an audit named:

  * it read COMMENTS. A binding that copies ``${../claudedocs}`` and merely
    mentions ``../claude/skills`` in a ``#`` note satisfied it.
  * it read DEAD CODE, for the same reason.
  * it never asked what the path was DOING there. A binding that copies the
    skills tree, then ``rm -rf "$out"`` and copies something else over it,
    contains the string throughout and deploys none of it.

So the body is comment-stripped first, and the path has to be used as a SOURCE
INTO ``$out`` -- with no other repo tree copied there, and no removal of
``$out``. That is still structural (it says nothing about how the copy is
spelled, and the real binding's ``ln -sT ${clickupNodeModules}/...`` passes
untouched); it just stops treating "the characters appear somewhere" as proof.
"""
from __future__ import annotations

import re

MAPPING = 'home.file.".claude/skills"'

#: The repo path the mapping must ultimately deploy.
SKILLS_PATH = "../claude/skills"

#: The direct form: `source = ../claude/skills;` inside the mapping.
_DIRECT = re.compile(r"source\s*=\s*\.\./claude/skills\s*;")

#: The indirect form: `source = <ident>;` where <ident> is a let-binding whose
#: definition mentions `../claude/skills`.
_INDIRECT_SOURCE = re.compile(r"source\s*=\s*([A-Za-z_][A-Za-z0-9_'-]*)\s*;")


def _mapping_block(home_nix: str) -> str:
    """The `home.file.".claude/skills" = { … };` block, or "" if absent."""
    start = home_nix.find(MAPPING)
    if start == -1:
        return ""
    open_brace = home_nix.find("{", start)
    if open_brace == -1:
        return ""
    depth = 0
    for i in range(open_brace, len(home_nix)):
        if home_nix[i] == "{":
            depth += 1
        elif home_nix[i] == "}":
            depth -= 1
            if depth == 0:
                return home_nix[open_brace : i + 1]
    return ""


#: A `let`-binding head: `  name =` at some indentation.
_BINDING_HEAD = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_'-]*)\s*=")

#: A nix path interpolation into a build script: `${../claude/skills}`.
_PATH_INTERP = re.compile(r"\$\{\s*(\.{1,2}/[^}\s]+?)\s*\}")

#: Anything that writes the output tree: `"$out"`, `$out/x`, `${out}`.
_OUT_REF = re.compile(r"\$\{?out\b")

#: `rm`/`rmdir` aimed at $out. A binding that empties its own output and then
#: fills it from somewhere else contains every string this predicate looks for.
_RM_OUT = re.compile(r"\brm(?:dir)?\b[^\n;]*\$\{?out\b")

#: How far from a path interpolation a `$out` reference still counts as "this
#: path is being copied into the output". Wide enough for a `\`-continued
#: command, narrow enough that two unrelated statements are not conflated.
_NEAR = 200


def _strip_comments(text: str) -> str:
    """Remove nix `#` line comments and `/* … */` blocks.

    Both a nix comment and a SHELL comment inside a `''…''` build script are
    handled by the same rule, which is what we want: nix interpolates
    `${../claude/skills}` inside a `''…''` string even on a line the shell will
    treat as a comment, so "the path appears" and "the path is used" are
    different questions there too.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"#[^\n]*", "", text)


def _trees_copied_into_out(body: str) -> set[str]:
    """Repo paths interpolated near a `$out` reference — i.e. copied in."""
    out: set[str] = set()
    for m in _PATH_INTERP.finditer(body):
        lo = max(0, m.start() - _NEAR)
        hi = min(len(body), m.end() + _NEAR)
        if _OUT_REF.search(body[lo:hi]):
            out.add(m.group(1))
    return out


def _binding_body(home_nix: str, ident: str) -> str:
    """The text of the `let` binding named `ident`.

    Delimited STRUCTURALLY -- from the binding's head to the next head at the
    same-or-shallower indentation, or to `in` -- rather than by hunting for a
    terminating `;`, which a multi-line nix string (`''…''`) makes unreliable.
    Returns "" when there is no such binding.
    """
    lines = home_nix.splitlines()
    start = None
    indent = ""
    for i, line in enumerate(lines):
        m = _BINDING_HEAD.match(line)
        if m and m.group(2) == ident:
            start = i
            indent = m.group(1)
            break
    if start is None:
        return ""
    body = [lines[start]]
    for line in lines[start + 1 :]:
        if re.match(r"^in\b", line):
            break
        m = _BINDING_HEAD.match(line)
        if m and len(m.group(1)) <= len(indent):
            break
        body.append(line)
    return "\n".join(body)


def skills_mapping_problem(home_nix: str) -> str | None:
    """Return a failure reason, or None when the mapping deploys claude/skills.

    Structural on purpose: it reads the mapping's `source` and follows one level
    of let-binding indirection, rather than matching how the line is spelled.
    """
    if MAPPING not in home_nix:
        return (
            "nix/home.nix no longer declares the ~/.claude/skills mapping, so a "
            "doc pinned under claude/skills/ may not ship at all."
        )
    block = _strip_comments(_mapping_block(home_nix))
    if not block:
        return (
            f"found {MAPPING} but could not read its attribute set -- this parser "
            "needs updating, do NOT delete the check."
        )
    if _DIRECT.search(block):
        return None
    m = _INDIRECT_SOURCE.search(block)
    if not m:
        return (
            f"the {MAPPING} mapping declares no `source =` at all:\n{block}"
        )
    ident = m.group(1)
    body = _strip_comments(_binding_body(home_nix, ident))
    if not body:
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, but there is no such "
            "let-binding in nix/home.nix -- nothing resolves the deployed tree."
        )
    # Not "the characters appear somewhere in the binding" -- the path has to be
    # COPIED INTO the output. Comments and dead code are already gone above.
    copied = _trees_copied_into_out(body)
    if SKILLS_PATH not in copied:
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, but that binding never "
            f"copies {SKILLS_PATH} into $out (it writes: "
            f"{sorted(copied) or 'no repo tree at all'}) -- the pinned docs are not "
            "the deployed files."
        )
    others = sorted(copied - {SKILLS_PATH})
    if others:
        return (
            f"the `{ident}` binding copies {others} into $out as well as "
            f"{SKILLS_PATH}. A second tree written over the same output decides what "
            "actually ships, and the pins under claude/skills/ stop being claims "
            "about the deployed files."
        )
    rm = _RM_OUT.search(body)
    if rm:
        return (
            f"the `{ident}` binding removes its own output (`{rm.group(0).strip()}`). "
            f"A binding that copies {SKILLS_PATH} and then empties $out contains every "
            "string this check looks for and deploys none of it."
        )
    return None


def assert_skills_mapping_deploys_repo_skills(home_nix: str) -> None:
    """Raise AssertionError unless nix/home.nix deploys devrc/claude/skills."""
    problem = skills_mapping_problem(home_nix)
    assert problem is None, problem
