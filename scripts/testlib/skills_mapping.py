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

WHAT ISSUE #443 FOUND, AND HOW THE PARSER CHANGED
--------------------------------------------------
The first structural version was both too loose and too strict, in one file.

*Too loose.* It located the mapping's attribute set with
``home_nix.find("{", start)`` -- the next ``{`` ANYWHERE in the file. nix's
DOTTED form has no attribute set of its own::

    home.file.".claude/skills".source = ../claudedocs;
    home.file.".config/opencode/skills" = { source = ../claude/skills; ... };

so the scan walked past the real (wrong) source into an unrelated block and
returned a clean bill of health while ``~/.claude/skills`` deployed
``../claudedocs``. The dotted form is not hypothetical -- home.nix already uses
it about twenty times. The mapping's source is now read from the mapping's OWN
syntax: either ``."…".source = <expr>;`` or ``."…" = { source = <expr>; };``,
and nothing else is guessed at.

*Too strict.* Copy-detection saw only ``${…}`` interpolations sitting within
200 characters of a ``$out`` reference, and any ``rm`` naming ``$out`` counted
as emptying the output. Five legitimate shapes were therefore rejected with a
confident, wrong "the deploy is broken" -- which is how a guard gets deleted
rather than fixed. Now:

  * the root-producing derivation attributes (``src``/``srcs``/``paths``) count
    as writing the output, so ``mkDerivation { src = ../claude/skills; }`` and
    ``symlinkJoin { paths = [ ../claude/skills … ]; }`` are understood;
  * one further hop of let-indirection is followed, so ``skillsSrc =
    ../claude/skills;`` used as ``${skillsSrc}`` is understood;
  * proximity is gone. The build script is split into STATEMENTS, and a repo
    path counts as written to the output only in a statement that also names
    ``$out``. That also makes the destination legible: writing ``$out`` itself
    replaces the tree, writing ``$out/sub`` adds to it. So ``rm -rf
    "$out/clickup/node_modules"`` and ``cp ${../claude/PRINCIPLES.md}
    "$out/PRINCIPLES.md"`` are additions, while ``rm -rf "$out"`` and ``cp -R
    ${../claudedocs} "$out"`` still fail.

The property being defended is unchanged and deliberately NOT softened: the
deployed tree must be built from ``../claude/skills``, and no other repo tree
may be written over the output root. Where the parser cannot decide -- an
unrecognised source expression, or an unresolvable identifier written over the
output root -- it FAILS, saying it needs updating and must not be deleted.
Silence is the one answer it must never give.
"""
from __future__ import annotations

import re

MAPPING = 'home.file.".claude/skills"'

#: The repo path the mapping must ultimately deploy.
SKILLS_PATH = "../claude/skills"

#: The tail of the message every undecidable case carries. A wrong-but-confident
#: verdict gets a guard deleted; this one asks for the parser instead.
_NEEDS_UPDATING = "this parser needs updating, do NOT delete the check."

#: A nix path literal: `../claude/skills`, `./pkgs/foo.nix`.
_PATH_LITERAL = re.compile(r"\.{1,2}/[^\s;,\]\)\}\"']+")

#: An expression that is EXACTLY a path literal.
_ONLY_PATH = re.compile(r"^\.{1,2}/[^\s;,\]\)\}\"']+$")

#: An expression that is exactly a nix identifier.
_ONLY_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_'-]*$")

#: A `let`-binding head: `  name =` at some indentation.
_BINDING_HEAD = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_'-]*)\s*=")

#: A binding whose whole value is a path literal: `skillsSrc = ../claude/skills;`.
_BARE_PATH_BINDING = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_'-]*\s*=\s*(\.{1,2}/[^\s;]+)\s*;\s*$"
)

#: A nix interpolation, `${…}`. The braces bound the expression: in
#: `${clickupNodeModules}/node_modules` only the identifier is interpolated.
_INTERP = re.compile(r"\$\{\s*([^{}]*?)\s*\}")

#: Derivation attributes whose value BECOMES the output tree. `src`/`srcs` for a
#: stdenv build, `paths` for symlinkJoin/buildEnv. A path in `buildInputs` is an
#: input, not the output, which is why this is an enumeration and not "any path".
_ROOT_ATTR = re.compile(r"(?<![\w.'-])(src|srcs|paths)\s*=\s*")

#: A `$out` reference, with whatever path follows it. Group 1 empty/absent means
#: the reference is to the output ROOT (`"$out"`, `$out/.`); a non-trivial group
#: 1 means a path UNDER the output (`"$out/clickup/node_modules"`).
_OUT_REF = re.compile(r"\$\{?out\}?(/[^\s\"';|&)]*)?")

#: Shell statement separators. Line continuations are folded first, so a
#: `\`-continued `cp` is one statement.
_STATEMENT_SPLIT = re.compile(r"[\n;|&]")

#: `rm`/`rmdir`, in a statement that may or may not aim at the output ROOT.
_RM = re.compile(r"\brm(?:dir)?\b")


def _strip_comments(text: str) -> str:
    """Remove nix `#` line comments and `/* … */` blocks.

    Both a nix comment and a SHELL comment inside a `''…''` build script are
    handled by the same rule, which is what we want: nix interpolates
    `${../claude/skills}` inside a `''…''` string even on a line the shell will
    treat as a comment, so "the path appears" and "the path is used" are
    different questions there too.

    Applied to the WHOLE file before anything is located, so a commented-out
    mapping cannot be mistaken for a live one. Line structure is preserved (a
    `#` comment is replaced to end-of-line only), which `_binding_body`'s
    indentation scan depends on.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"#[^\n]*", "", text)


def _brace_block(text: str, open_brace: int) -> str:
    """The `{ … }` starting at `open_brace`, or "" if it never closes."""
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : i + 1]
    return ""


def _expr_until_semicolon(text: str, start: int) -> str:
    """The expression from `start` to the next `;`, or "" if there is none."""
    end = text.find(";", start)
    if end == -1:
        return ""
    return text[start:end].strip()


def _source_expr(text: str) -> tuple[str | None, str | None]:
    """The expression the ~/.claude/skills mapping's `source` is bound to.

    Returns `(expr, None)` or `(None, problem)`. Read from the mapping's OWN
    syntax -- both spellings nix allows for one `home.file` entry::

        home.file.".claude/skills".source = <expr>;      # dotted
        home.file.".claude/skills" = { source = <expr>; };  # attribute set

    and NOTHING else. The predecessor took "the next `{` anywhere in the file",
    which for the dotted form walked into an unrelated block and read a source
    that was not the mapping's (issue #443).

    `text` must already be comment-stripped.
    """
    problem: str | None = None
    seen = False
    for m in re.finditer(re.escape(MAPPING), text):
        seen = True
        rest = text[m.end() :]
        dotted = re.match(r"\s*\.\s*source\s*=\s*", rest)
        if dotted:
            expr = _expr_until_semicolon(rest, dotted.end())
            if expr:
                return expr, None
            problem = problem or (
                f"the {MAPPING} mapping's dotted `.source =` has no terminating "
                f"`;` -- {_NEEDS_UPDATING}"
            )
            continue
        attrset = re.match(r"\s*=\s*\{", rest)
        if attrset:
            block = _brace_block(rest, attrset.end() - 1)
            if not block:
                problem = problem or (
                    f"found {MAPPING} but its attribute set never closes -- "
                    f"{_NEEDS_UPDATING}"
                )
                continue
            src = re.search(r"(?<![\w.'-])source\s*=\s*", block)
            if not src:
                problem = problem or (
                    f"the {MAPPING} mapping declares no `source =` at all:\n{block}"
                )
                continue
            expr = _expr_until_semicolon(block, src.end())
            if expr:
                return expr, None
            problem = problem or (
                f"the {MAPPING} mapping's `source =` has no terminating `;` -- "
                f"{_NEEDS_UPDATING}\n{block}"
            )
            continue
        # Neither spelling: `= mkIf …`, `.text =`, `.recursive = true;` on its
        # own. Keep looking -- another occurrence may carry the source.
        problem = problem or (
            f"found {MAPPING} but it is neither `.source = <expr>;` nor "
            f"`= {{ source = <expr>; … }}` -- {_NEEDS_UPDATING}"
        )
    if not seen:
        return None, (
            "nix/home.nix no longer declares the ~/.claude/skills mapping, so a "
            "doc pinned under claude/skills/ may not ship at all."
        )
    return None, problem


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


def _resolve_path_ident(text: str, ident: str) -> str | None:
    """`ident`'s value when it is a bare path literal, else None.

    ONE hop, deliberately: `skillsSrc = ../claude/skills;` used as
    `${skillsSrc}` is the indirection real nix code uses. An identifier bound to
    a derivation (`clickupNodeModules = pkgs.callPackage …`) is not a repo tree
    and must not be reported as one.
    """
    body = _binding_body(text, ident)
    if not body:
        return None
    m = _BARE_PATH_BINDING.match(body.strip())
    return m.group(1) if m else None


def _normalise(path: str) -> str:
    """`../claudedocs/.` and `../claudedocs/` both name `../claudedocs`."""
    path = path.rstrip("/")
    if path.endswith("/."):
        path = path[:-2]
    return path.rstrip("/")


def _statements(body: str) -> list[str]:
    """The build script's statements, with `\\`-continuations folded in.

    Replaces the predecessor's 200-character proximity window, which conflated
    neighbouring lines: a `cp ${../claude/PRINCIPLES.md} "$out/PRINCIPLES.md"`
    two lines below a `cp -R ${../claude/skills} "$out"` looked like a second
    tree written over the output root.
    """
    folded = re.sub(r"\\\n\s*", " ", body)
    return [s for s in _STATEMENT_SPLIT.split(folded) if s.strip()]


def _writes_output_root(statement: str) -> bool:
    """True when the statement writes `$out` ITSELF, not a path under it.

    `"$out"`, `$out/` and `"$out/."` all name the output tree; `"$out/clickup/
    node_modules"` names one entry inside it. That distinction IS #443's `rm -rf
    "$out/clickup/node_modules"` false positive, and it is what makes a file
    copied to a name under $out an addition rather than a second tree.
    """
    for m in _OUT_REF.finditer(statement):
        tail = (m.group(1) or "").rstrip("\"'")
        if tail in ("", "/", "/."):
            return True
    return False


def _paths_in(statement: str, text: str) -> tuple[set[str], set[str]]:
    """`(repo paths, unresolvable identifiers)` interpolated in `statement`."""
    paths: set[str] = set()
    idents: set[str] = set()
    for m in _INTERP.finditer(statement):
        expr = m.group(1).strip()
        if _ONLY_PATH.match(expr):
            paths.add(_normalise(expr))
        elif _ONLY_IDENT.match(expr):
            resolved = _resolve_path_ident(text, expr)
            if resolved is not None:
                paths.add(_normalise(resolved))
            else:
                idents.add(expr)
    return paths, idents


#: An identifier in a derivation-attribute value. `/` is in the lookbehind so
#: the components of a path literal (`../claude/skills` -> `claude`, `skills`)
#: are not read as identifiers to resolve.
_ATTR_WORD = re.compile(r"(?<![\w.'/-])[A-Za-z_][A-Za-z0-9_'-]*")


def _root_attr_paths(body: str, text: str) -> set[str]:
    """Repo paths bound to `src`/`srcs`/`paths` -- which BECOME the output.

    `mkDerivation { src = ../claude/skills; }` and `symlinkJoin { paths = [
    ../claude/skills … ]; }` write the output root without ever naming `$out`,
    which is why the predecessor called both "never copies ../claude/skills into
    $out". A path in `buildInputs` is an INPUT, not the output, which is why
    this is an enumeration of attributes and not "any path in the binding".
    """
    paths: set[str] = set()
    for m in _ROOT_ATTR.finditer(body):
        value = _expr_until_semicolon(body, m.end())
        if not value:
            continue
        for lit in _PATH_LITERAL.findall(value):
            paths.add(_normalise(lit))
        for word in _ATTR_WORD.findall(value):
            resolved = _resolve_path_ident(text, word)
            if resolved is not None:
                paths.add(_normalise(resolved))
    return paths


def _analyse_binding(body: str, text: str) -> tuple[set[str], set[str], str]:
    """What `body` writes over its output ROOT: repo paths, opaque identifiers,
    and the first statement that REMOVES the root, if any.

    Only root writes are collected. A statement that writes a path UNDER $out
    adds to the tree and cannot replace it, so it is not a hazard and not
    tracked -- see `_writes_output_root`.
    """
    roots = _root_attr_paths(body, text)
    root_idents: set[str] = set()
    removes_root = ""
    for statement in _statements(body):
        if not _writes_output_root(statement):
            continue
        if _RM.search(statement) and not removes_root:
            removes_root = statement.strip()
        paths, idents = _paths_in(statement, text)
        roots |= paths
        root_idents |= idents
    return roots, root_idents, removes_root


def skills_mapping_problem(home_nix: str) -> str | None:
    """Return a failure reason, or None when the mapping deploys claude/skills.

    Structural on purpose: it reads the mapping's own `source` -- in either of
    the two spellings nix allows -- and follows let-binding indirection, rather
    than matching how the line is spelled.
    """
    text = _strip_comments(home_nix)
    expr, problem = _source_expr(text)
    if expr is None:
        return problem

    if _ONLY_PATH.match(expr):
        if _normalise(expr) == SKILLS_PATH:
            return None
        return (
            f"the ~/.claude/skills mapping sources `{expr}`, not {SKILLS_PATH} -- "
            "the pinned docs under claude/skills/ are not the deployed files."
        )

    if not _ONLY_IDENT.match(expr):
        return (
            f"the ~/.claude/skills mapping's source is `{expr}`, which this check "
            f"cannot resolve to a repo tree -- {_NEEDS_UPDATING}"
        )

    ident = expr
    direct = _resolve_path_ident(text, ident)
    if direct is not None:
        if _normalise(direct) == SKILLS_PATH:
            return None
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, which is bound to "
            f"`{direct}`, not {SKILLS_PATH} -- the pinned docs are not the "
            "deployed files."
        )

    body = _binding_body(text, ident)
    if not body:
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, but there is no such "
            "let-binding in nix/home.nix -- nothing resolves the deployed tree."
        )

    # Not "the characters appear somewhere in the binding" -- the path has to
    # BECOME the output. Comments and dead code are already gone above.
    roots, opaque, removes_root = _analyse_binding(body, text)
    if SKILLS_PATH not in roots:
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, but that binding never "
            f"copies {SKILLS_PATH} into $out (it writes: "
            f"{sorted(roots) or 'no repo tree at all'}) -- the pinned docs are not "
            "the deployed files."
        )
    others = sorted(roots - {SKILLS_PATH})
    if others:
        return (
            f"the `{ident}` binding copies {others} into $out as well as "
            f"{SKILLS_PATH}. A second tree written over the same output decides what "
            "actually ships, and the pins under claude/skills/ stop being claims "
            "about the deployed files."
        )
    if opaque:
        return (
            f"the `{ident}` binding writes {sorted(opaque)} over the output ROOT, and "
            f"this check cannot tell what those resolve to -- whichever write runs "
            f"last decides what ships. {_NEEDS_UPDATING}"
        )
    if removes_root:
        return (
            f"the `{ident}` binding removes its own output (`{removes_root}`). "
            f"A binding that copies {SKILLS_PATH} and then empties $out contains every "
            "string this check looks for and deploys none of it."
        )
    return None


def assert_skills_mapping_deploys_repo_skills(home_nix: str) -> None:
    """Raise AssertionError unless nix/home.nix deploys devrc/claude/skills."""
    problem = skills_mapping_problem(home_nix)
    assert problem is None, problem
