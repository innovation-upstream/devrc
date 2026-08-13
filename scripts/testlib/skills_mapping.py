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
"""
from __future__ import annotations

import re

MAPPING = 'home.file.".claude/skills"'

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
    block = _mapping_block(home_nix)
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
    # The binding must exist AND be built from ../claude/skills. Anything else is
    # a source pointing somewhere the pinned docs do not live.
    if "../claude/skills" not in _binding_body(home_nix, ident):
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, but that binding is "
            "not built from ../claude/skills -- the pinned docs are not the "
            "deployed files."
        )
    return None


def assert_skills_mapping_deploys_repo_skills(home_nix: str) -> None:
    """Raise AssertionError unless nix/home.nix deploys devrc/claude/skills."""
    problem = skills_mapping_problem(home_nix)
    assert problem is None, problem
