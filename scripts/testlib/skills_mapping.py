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
may be written over the output root. Where the parser cannot decide it FAILS,
saying it needs updating and must not be deleted, rather than returning None --
because None is the answer that silently stops checking anything.

WHAT AN ADVERSARIAL AUDIT OF THAT FIX THEN FOUND
------------------------------------------------
Eight constructible shapes still returned a clean bill of health, two of them
using idioms already in this file. Each is now a loud failure, and each has a
fixture:

  * ``#`` is only a comment at line start or after WHITESPACE. Blanking every
    ``#`` to end-of-line ate the ``}`` out of ``"''${bbOld##*.}"`` -- which is
    `nix/home.nix:548`, today -- unbalancing the braces the attrset scan counts
    and letting it run into the NEXT mapping's ``source``. Comments are blanked
    in place now (offsets preserved), never deleted.
  * The mapping literal occurring MORE THAN ONCE (in a ``''…''`` script, in
    prose) took the first match and never noticed the disagreement.
  * ``cd "$out"`` then ``cp -R ${../claudedocs}/. .``, a ``tar | tar`` pipe, and
    ``dst="$out"`` all write the root in a statement that does not name ``$out``.
    Statement-scoping cannot see those, so every repo path in the binding must
    now be ACCOUNTED FOR -- as a root write or as a write under the root -- and
    one that is neither is undecidable, not ignorable.
  * ``{ … } // lib.optionalAttrs cond { source = <other>; }``: the override was
    invisible because only the first attribute set was read.
  * A ``let`` binding whose name is declared more than once (shadowing).
  * ``${pkgs.other}`` over the root fell through both the path branch and the
    identifier branch and was dropped in silence.

And the headline false positive of #443 was still alive one character to the
left: ``rm -rf "$out"/clickup/node_modules`` (quote before the slash, not after)
read as a removal of the root.

KNOWN LIMITS -- read before trusting a PASS
-------------------------------------------
  * ``src``/``srcs``/``paths`` are matched by NAME, not by POSITION, so a
    ``src`` sitting in a ``runCommandLocal`` ENV attribute set is credited as
    the output tree even though it is only an environment variable there.
  * A repo path this cannot classify is reported as undecidable rather than
    ignored, which makes some legitimate-but-unusual builds (a ``tar`` pipe out
    of the right tree) fail asking for the parser. That direction is deliberate:
    the alternative is `cd "$out"` aliasing passing in silence.
  * It reads text, not nix. `nix eval` is the only thing that actually knows
    what a `home.file` source resolves to.
"""
from __future__ import annotations

import re
from typing import NamedTuple

MAPPING = 'home.file.".claude/skills"'

#: The attribute on its own, for telling "the entry is gone" apart from "the
#: entry is spelled some way this cannot read" (`home.file = { … }`, `mkMerge`).
ATTR_NAME = '".claude/skills"'

#: The repo path the mapping must ultimately deploy.
SKILLS_PATH = "../claude/skills"

#: The tail of the message every undecidable case carries. A wrong-but-confident
#: verdict gets a guard deleted; this one asks for the parser instead.
_NEEDS_UPDATING = "this parser needs updating, do NOT delete the check."

#: A nix path literal: `../claude/skills`, `./pkgs/foo.nix`.
_PATH_LITERAL = re.compile(r"\.{1,2}/[^\s;,\]\)\}\"']+")

#: A path OUT of the nix/ directory -- i.e. some other tree in this repo. `./x`
#: is excluded: inside a build script `./.` is the builder's cwd, not a repo
#: tree, and crediting it as one would reject every `installPhase`.
_REPO_PATH = re.compile(r"\.\./[^\s;,\]\)\}\"']+")

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
#:
#: 🔴 The optional quote before group 1 is load-bearing: `"$out"/clickup/x` and
#: `"$out/clickup/x"` are the same destination, and without it the first read as
#: the ROOT -- resurrecting #443's headline false positive one character to the
#: left of where it was fixed.
_OUT_REF = re.compile(r"\$\{?out\}?[\"']?(/[^\s\"';|&)]*)?")

#: Shell statement separators: newline and `;` only. `|`, `&&` and `||` are
#: deliberately NOT separators -- `tar -C ${…} -cf - . | tar -C "$out" -xf -` is
#: ONE write, and splitting it put the source and the destination in different
#: statements where nothing could relate them.
_STATEMENT_SPLIT = re.compile(r"[\n;]")

#: `rm`/`rmdir`, in a statement that may or may not aim at the output ROOT.
_RM = re.compile(r"\brm(?:dir)?\b")


#: A comment `#` -- at the start of the text or after WHITESPACE, and nowhere
#: else. 🔴 The lookbehind is the whole point. `#` mid-token is not a comment in
#: any of the languages in this file, and blanking from one to end-of-line ate
#: the closing brace out of `bbPid="''${bbOld##*.}"` -- `nix/home.nix:548`,
#: present today -- which unbalanced the braces the attrset scan counts and let
#: it run past the mapping into the NEXT one's `source`. It also ate the rest of
#: `echo "theme=#282828"`, taking any statement that followed on that line.
_COMMENT = re.compile(r"(?<![^\s])#[^\n]*")

#: `/* … */`, which nix allows anywhere.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _blank(m: re.Match[str]) -> str:
    """The match, with every character but `\\n` replaced by a space.

    Comments are BLANKED, not deleted: every offset and every line number in the
    text stays exactly where it was, so a scan can be run on the stripped text
    and its results quoted against the original.
    """
    return re.sub(r"[^\n]", " ", m.group(0))


def _strip_comments(text: str) -> str:
    """Blank nix `#` line comments and `/* … */` blocks, in place.

    A SHELL comment inside a `''…''` build script is blanked by the same rule,
    which is what we want: nix interpolates `${../claude/skills}` inside a
    `''…''` string even on a line the shell will treat as a comment, so "the
    path appears" and "the path is used" are different questions there too.

    Applied to the WHOLE file before anything is located, so a commented-out
    mapping cannot be mistaken for a live one -- and, because it blanks rather
    than deletes, without moving anything the brace and indentation scans read.
    """
    text = _BLOCK_COMMENT.sub(_blank, text)
    return _COMMENT.sub(_blank, text)


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
    hits = list(re.finditer(re.escape(MAPPING), text))
    if not hits:
        if ATTR_NAME in text:
            return None, (
                f"{ATTR_NAME} appears in nix/home.nix but not as `{MAPPING}` -- it "
                f"may be declared through `home.file = {{ … }}`, `mkMerge` or a "
                f"loop, which this cannot read. {_NEEDS_UPDATING}"
            )
        return None, (
            "nix/home.nix no longer declares the ~/.claude/skills mapping, so a "
            "doc pinned under claude/skills/ may not ship at all."
        )
    problem: str | None = None
    found: list[tuple[str, int]] = []
    for m in hits:
        rest = text[m.end() :]
        line_no = text[: m.start()].count("\n") + 1
        dotted = re.match(r"\s*\.\s*source\s*=\s*", rest)
        if dotted:
            expr = _expr_until_semicolon(rest, dotted.end())
            if expr:
                found.append((expr, line_no))
                continue
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
            after = rest[attrset.end() - 1 + len(block) :]
            if re.match(r"\s*//", after):
                # `{ source = A; } // lib.optionalAttrs cond { source = B; }`:
                # nix's effective source is B, and only A is in `block`.
                return None, (
                    f"the {MAPPING} mapping's attribute set is merged with `//`, so "
                    f"its effective `source` is not the one written here. "
                    f"{_NEEDS_UPDATING}"
                )
            src = re.search(r"(?<![\w.'-])source\s*=\s*", block)
            if not src:
                problem = problem or (
                    f"the {MAPPING} mapping declares no `source =` at all:\n{block}"
                )
                continue
            expr = _expr_until_semicolon(block, src.end())
            if expr:
                found.append((expr, line_no))
                continue
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

    # 🔴 Reading the FIRST occurrence is how a mapping quoted inside a `''…''`
    # script or an activation-script heredoc answered for the real declaration.
    # Several `.source` spellings are only safe when they AGREE; disagreement is
    # a question about which one nix takes, which this cannot answer.
    #
    # Several occurrences that do NOT each carry a source are fine and normal --
    # `home.file."…".source = X;` beside `home.file."…".recursive = true;` is one
    # entry written as two dotted attributes.
    distinct = {expr for expr, _ in found}
    if len(distinct) > 1:
        where = ", ".join(f"line {n}: `{e}`" for e, n in found)
        return None, (
            f"`{MAPPING}` is given {len(distinct)} DIFFERENT sources in "
            f"nix/home.nix ({where}); which one nix takes is not something this can "
            f"read, and taking the first is how a copy inside a string answers for "
            f"the real one. {_NEEDS_UPDATING}"
        )
    if found:
        return found[0][0], None
    return None, problem


def _binding_body(home_nix: str, ident: str) -> str:
    """The text of the `let` binding named `ident`.

    Delimited STRUCTURALLY -- from the binding's head to the next head at the
    same-or-shallower indentation, or to `in` -- rather than by hunting for a
    terminating `;`, which a multi-line nix string (`''…''`) makes unreliable.
    Returns "" when there is no such binding.

    This is a TEXT scan and knows nothing about nix scope, so a name declared
    more than once (a nested attribute set using it as a key, a shadowing
    binding) is not resolvable here -- see `_binding_head_count`, which makes
    that case fail loudly rather than silently taking the first one.
    """
    lines = home_nix.splitlines()
    heads = [i for i, line in enumerate(lines)
             if (m := _BINDING_HEAD.match(line)) and m.group(2) == ident]
    if not heads:
        return ""
    start = heads[0]
    indent = _BINDING_HEAD.match(lines[start]).group(1)  # type: ignore[union-attr]
    body = [lines[start]]
    for line in lines[start + 1 :]:
        if re.match(r"^in\b", line):
            break
        m = _BINDING_HEAD.match(line)
        if m and len(m.group(1)) <= len(indent):
            break
        body.append(line)
    return "\n".join(body)


def _binding_head_count(home_nix: str, ident: str) -> int:
    """How many `<ident> =` heads exist. More than one means this cannot say
    which one a reference resolves to -- nix scope is not a text property."""
    return sum(
        1 for line in home_nix.splitlines()
        if (m := _BINDING_HEAD.match(line)) and m.group(2) == ident
    )


def _resolve_path_ident(text: str, ident: str) -> str | None:
    """`ident`'s value when it is a bare path literal, else None.

    ONE hop, deliberately: `skillsSrc = ../claude/skills;` used as
    `${skillsSrc}` is the indirection real nix code uses. An identifier bound to
    a derivation (`clickupNodeModules = pkgs.callPackage …`) is not a repo tree
    and must not be reported as one.

    A name declared MORE THAN ONCE resolves to nothing here, in ONE place rather
    than at each call site: which declaration a reference sees is a question
    about nix scope, and this is a text scan. Callers get None and report it in
    their own terms.
    """
    if _binding_head_count(text, ident) != 1:
        return None
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


def _out_scope(statement: str) -> str | None:
    """`"root"`, `"under"`, or None -- what part of the output this writes.

    `"$out"`, `$out/` and `"$out/."` all name the output tree; `"$out/clickup/
    node_modules"` names one entry inside it. That distinction IS #443's `rm -rf
    "$out/clickup/node_modules"` false positive, and it is what makes a file
    copied to a name under $out an addition rather than a second tree. A root
    reference wins: a statement naming both replaces the tree.
    """
    scope: str | None = None
    for m in _OUT_REF.finditer(statement):
        tail = (m.group(1) or "").rstrip("\"'")
        if tail in ("", "/", "/."):
            return "root"
        scope = "under"
    return scope


def _paths_in(statement: str, text: str) -> tuple[set[str], set[str]]:
    """`(repo paths, expressions this cannot resolve)` interpolated here.

    Every `${…}` lands in one bucket or the other. An earlier version had a
    third, silent outcome -- an expression matching neither the path shape nor
    the identifier shape (`${pkgs.otherTree}`, `${inputs.x}`) fell off the end
    of the `elif` and was dropped -- so a whole derivation copied over the
    output root was invisible.
    """
    paths: set[str] = set()
    opaque: set[str] = set()
    for m in _INTERP.finditer(statement):
        expr = m.group(1).strip()
        if expr == "out":
            continue  # the output itself, not something being copied into it
        if _ONLY_PATH.match(expr):
            paths.add(_normalise(expr))
            continue
        if _ONLY_IDENT.match(expr):
            resolved = _resolve_path_ident(text, expr)
            if resolved is not None:
                paths.add(_normalise(resolved))
                continue
        opaque.add(expr)
    return paths, opaque


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


class _Writes(NamedTuple):
    """What a binding does to its output."""

    #: Repo paths written over the output ROOT -- these decide what ships.
    roots: set[str]
    #: Expressions written over the root that cannot be resolved to a tree.
    opaque: set[str]
    #: The first statement that removes the root, verbatim, or "".
    removes_root: str
    #: Repo paths in the binding that are neither of the above -- not written
    #: to the output as far as this can tell, which is not the same as harmless.
    unaccounted: set[str]


def _analyse_binding(body: str, text: str) -> _Writes:
    """What `body` writes into its output.

    🔴 Every repo path in the binding must come out CLASSIFIED -- as a root
    write or as a write under the root. A path that is neither is reported, not
    dropped, because statement-scoping cannot see a write whose statement does
    not name `$out`:

        cp -R ${../claude/skills} "$out"
        cd "$out"
        cp -R ${../claudedocs}/. .

    Nothing here relates that third line to the output; `../claudedocs` simply
    goes unaccounted for. Ignoring it made the above a clean PASS while the
    deployed tree was ../claudedocs.
    """
    roots = _root_attr_paths(body, text)
    opaque: set[str] = set()
    additions: set[str] = set()
    removes_root = ""
    for statement in _statements(body):
        scope = _out_scope(statement)
        if scope is None:
            continue
        paths, unresolved = _paths_in(statement, text)
        if scope == "root":
            if _RM.search(statement) and not removes_root:
                removes_root = statement.strip()
            roots |= paths
            opaque |= unresolved
        else:
            additions |= paths
    mentioned = {_normalise(p) for p in _REPO_PATH.findall(body)}
    return _Writes(roots, opaque, removes_root,
                   mentioned - roots - additions)


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
    declared = _binding_head_count(text, ident)
    if declared > 1:
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, and `{ident} =` is "
            f"declared {declared} times in nix/home.nix -- which one a reference "
            f"resolves to is a question about nix SCOPE, and this reads text. "
            f"{_NEEDS_UPDATING}"
        )

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
    writes = _analyse_binding(body, text)
    if SKILLS_PATH not in writes.roots:
        return (
            f"the ~/.claude/skills mapping sources `{ident}`, but that binding never "
            f"copies {SKILLS_PATH} into $out (it writes: "
            f"{sorted(writes.roots) or 'no repo tree at all'}) -- the pinned docs are "
            "not the deployed files."
        )
    others = sorted(writes.roots - {SKILLS_PATH})
    if others:
        return (
            f"the `{ident}` binding copies {others} into $out as well as "
            f"{SKILLS_PATH}. A second tree written over the same output decides what "
            "actually ships, and the pins under claude/skills/ stop being claims "
            "about the deployed files."
        )
    if writes.unaccounted:
        return (
            f"the `{ident}` binding names {sorted(writes.unaccounted)} in statements "
            f"this cannot relate to $out -- so it cannot say whether they land in the "
            f"deployed tree. A `cd \"$out\"` or a `dst=\"$out\"` reads exactly like "
            f"this, and it replaces everything. {_NEEDS_UPDATING}"
        )
    if writes.opaque:
        return (
            f"the `{ident}` binding writes {sorted(writes.opaque)} over the output "
            f"ROOT, and this check cannot tell what those resolve to -- whichever "
            f"write runs last decides what ships. {_NEEDS_UPDATING}"
        )
    if writes.removes_root:
        return (
            f"the `{ident}` binding both copies {SKILLS_PATH} into $out and REMOVES "
            f"$out (`{writes.removes_root}`). Which one wins is a question about "
            "order that this does not answer, and if the removal runs last the "
            "binding contains every string this check looks for and deploys none of "
            "it."
        )
    return None


def assert_skills_mapping_deploys_repo_skills(home_nix: str) -> None:
    """Raise AssertionError unless nix/home.nix deploys devrc/claude/skills."""
    problem = skills_mapping_problem(home_nix)
    assert problem is None, problem
