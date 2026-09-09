#!/usr/bin/env python3
"""Gate on the SEAM between devrc's flake and the PINNED `cairn` package.

WHAT THIS FILE IS DEFENDING. `cairn` used to be deployed as an
`mkOutOfStoreSymlink` into `devrc/scripts/cairn`, because the client finds its
modules with `Path(__file__).resolve().parent / "lib"` and a `home.file` COPY
resolves `__file__` into /nix/store where `scripts/lib/` is not deployed. It is
now the packaged client from the `cairn` flake input, which solves that same
problem a different way: the package installs the real script and `lib/`
TOGETHER under `libexec` and puts a wrapper in `bin/`, so `.resolve()` lands
beside `lib/` inside the store.

🔴 THESE ARE SEAM GUARDS, NOT COMPONENT GUARDS. Two failure modes live in the
gaps between files and neither is visible from inside one of them:

  1. DECLARED-BUT-NOT-WIRED. An input can be added to `flake.nix` and locked,
     while nothing consumes it — the deploy line still points at the checkout
     and the lock entry is decoration. A binding of the right NAME in the WRONG
     PLACE reads correct in every file individually. So the thread is pinned
     end to end: input -> outputs argument -> extraSpecialArgs -> the module
     header of `nix/home.nix` -> the deploy line that interpolates it.

  2. HALF THE SPLIT. `cairn` moves to the package; `cairn-who` MUST NOT. It is
     devrc-only, is deliberately absent from the OSS repo, and resolves
     `scripts/lib/cairn_who.py` through its own `__file__` — so it still needs
     out-of-store deployment. Asserting either side alone cannot see a change
     that moved both, which is why the two modes are pinned as ONE
     RELATIONSHIP here rather than as two independent facts.

🔴 AND ONE CHECK THAT STOPPED LOOKING. `CAIRN_MIRROR_ROOT` is not decoration.
The OSS client reads the frozen pre-cutover mirror's path from that variable
(devrc's own copy had it hardcoded), and UNSET does not mean "no mirror
problem" — `doctor` reports `frozen-mirror NOT-OBSERVABLE`, which contributes
nothing to the verdict. Measured on this host against a real cache root:
devrc's client said `frozen-mirror OK`, the packaged client with the variable
unset said `NOT-OBSERVABLE — no mirror is configured`, and with it set said
`OK` again. A silent downgrade from a check that PASSES to a check that is not
RUN is exactly the kind of green nobody re-reads, so the export is pinned.

Every identifier here is read out of this repo's own tracked files; nothing is
invented and nothing is read out of a live deployment.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAKE = REPO_ROOT / "flake.nix"
FLAKE_LOCK = REPO_ROOT / "flake.lock"
NIX_HOME = REPO_ROOT / "nix" / "home.nix"
SESSION_VARS = REPO_ROOT / "nix" / "sessionVariables.nix"

#: The attribute name the flake input carries, and the name the package is
#: threaded under. Two DIFFERENT names on purpose: `cairn` is the flake input,
#: `cairnPackage` is the derivation handed to the module. Collapsing them would
#: make "is the input wired" and "is the package wired" the same substring, and
#: this file's whole job is telling those two apart.
INPUT_NAME = "cairn"
THREADED_NAME = "cairnPackage"


def _flake() -> str:
    return FLAKE.read_text(encoding="utf-8")


def _home() -> str:
    return NIX_HOME.read_text(encoding="utf-8")


def _lock() -> dict:
    return json.loads(FLAKE_LOCK.read_text(encoding="utf-8"))


def _assignment(text: str, key: str) -> str:
    """The whole right-hand side of `key = …;`, however many lines it spans.

    🔴 BRACE-AWARE, AND THE NAIVE VERSION WAS MEASURED WRONG. Splitting on the
    first `;` is right for a scalar and silently truncating for an attrset:
    against `extraSpecialArgs = { isNixOS = true; cairnPackage = …; };` it
    returns `{ isNixOS = true` and every assertion about the rest of the set
    fails for a reason that has nothing to do with the code — a red that reads
    like a real finding. So the terminator is a `;` at brace depth 0.
    """
    assert key in text, f"{key!r} is absent"
    rest = text.split(key, 1)[1]
    depth = 0
    for i, ch in enumerate(rest):
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == ";" and depth == 0:
            return rest[:i]
    raise AssertionError(f"unterminated assignment for {key!r}")


def _module_header(text: str) -> str:
    """A nix module's argument set — from its opening `{` through the closing `:`.

    🔴 IT RAISES RATHER THAN FALLING BACK, AND THE FALLBACK IS WHAT MADE THE
    CALLER VACUOUS. The first version was an inline bracket walk whose loop
    simply ended when the depth never returned to 0, leaving `header` at its
    initial value — the WHOLE FILE. Every assertion downstream then read the
    module BODY: `cairnPackage` occurs there twice (the deploy line and its
    comment), so `THREADED_NAME in header` passed while the argument was dropped
    from the header entirely. MEASURED: a legal multi-line header that dropped
    `cairnPackage` and carried one unbalanced `(` inside a prose comment left the
    whole file green at 8 passed. A guard that cannot distinguish "found the
    header" from "read everything" is not asserting about a header.

    🔴 COMMENTS AND STRINGS DO NOT CARRY BRACKET DEPTH. `#` to end of line and
    `/* … */` are nix comments; `"…"` and `''…''` are nix strings. A `(` in any
    of them is prose or data, not structure, and counting it desynchronises the
    walk for the rest of the file. The same reason forbids `lstrip()` as the
    "does it open with `{`" test — `lstrip` strips whitespace, not comments, so a
    perfectly correct file with an explanatory line above its header failed with
    `does not open with a module argument set`, a red naming a cause the tree did
    not have. Both faults are the same class the callers' helpers exist to avoid:
    a false diagnosis costs the reader more than no check.

    ⚠ SCOPE OF THE STRING HANDLING, STATED SO IT IS NOT READ AS A NIX LEXER.
    `''` is treated as an indented-string delimiter, so `'''` and `''${` escapes
    inside one are not modelled; that shape is below the header in every module
    here and the walk returns before reaching it. Strings are skipped only AFTER
    the argument set has opened — a file whose first code token is a string is
    reported as not opening with an argument set, not silently walked past. What
    is pinned is the header, and the walk is deliberately positional so the slice
    it returns is the file's own bytes rather than a rewritten copy.
    """
    n = len(text)
    i = 0
    depth = 0
    opened_at: int | None = None
    while i < n:
        ch = text[i]
        if ch == "#":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if opened_at is None:
            # Nothing but whitespace and comments may precede the argument set;
            # the FIRST code character decides, and it must be `{`. The string
            # skips are deliberately BELOW this line rather than above it: a file
            # that opens with a string literal is not a module, and skipping it
            # here would hide that behind whatever token came next.
            if ch.isspace():
                i += 1
                continue
            assert ch == "{", (
                "nix/home.nix does not open with a module argument set — its "
                f"first code token is {ch!r}, not `{{`: {text[i:i + 120]!r}")
            opened_at = i
            depth = 1
            i += 1
            continue
        if text.startswith("''", i):
            end = text.find("''", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                colon = text.find(":", i)
                assert colon >= 0, (
                    "nix/home.nix's argument set closes but no `:` follows it, so "
                    f"this is not a module header: {text[opened_at:i + 1]!r}")
                return text[opened_at:colon + 1]
        i += 1
    raise AssertionError(
        "nix/home.nix's module argument set is never closed: the bracket walk "
        "reached end of file at depth "
        f"{depth}. It must NOT fall back to reading the whole file — the body "
        "mentions every name the header is supposed to bind, so that fallback "
        "makes the argument checks pass vacuously.")


def _cairn_input_region(text: str) -> str:
    """Everything flake.nix says about the `cairn` INPUT, in either spelling.

    A flake input can be written flat (`cairn.url = …;` plus any number of
    `cairn.inputs.… = …;` lines) or as a set (`cairn = { url = …; inputs.… =
    …; };`). Both are legal and mean the same thing, so a check that reads only
    one of them is a check on a SPELLING. This returns the union of every
    `cairn`-prefixed input declaration, and asserting over the region catches a
    `follows` in whichever form the author reached for.
    """
    inputs = _assignment(text, "inputs =")
    out = []
    if re.search(r"^\s*cairn\s*=\s*\{", inputs, re.M):
        out.append(_assignment(inputs, "cairn ="))
    for line in inputs.splitlines():
        if re.match(r"\s*cairn\s*\.", line):
            out.append(line)
    assert out, "flake.nix declares no `cairn` input"
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 1. The input exists, and it is a PIN.
# ---------------------------------------------------------------------------

def test_the_flake_declares_a_cairn_input_pointing_at_the_OSS_repo():
    """The client is consumed from `ZacxDev/cairn`, not from this checkout."""
    text = _flake()
    assert re.search(r"^\s*cairn\.url\s*=", text, re.M) or re.search(
        r"^\s*cairn\s*=\s*\{", text, re.M), (
        "flake.nix declares no `cairn` input. The client is supposed to come "
        "from the pinned OSS flake, not from devrc's own scripts/ directory.")
    assert "github:ZacxDev/cairn" in text, (
        "the `cairn` input does not name github:ZacxDev/cairn — if the repo "
        "moved, move this assertion with it deliberately.")


def test_the_cairn_input_is_LOCKED_to_a_revision():
    """🔴 A URL WITHOUT A LOCK ENTRY IS NOT A PIN.

    `flake.nix` names a branch; `flake.lock` is what makes the build
    reproducible. An input added to flake.nix and never locked resolves to
    whatever the branch holds at build time — the version-drift failure the
    whole cutover exists to remove, since the packaged client's VERSION IS the
    git revision.
    """
    nodes = _lock()["nodes"]
    assert INPUT_NAME in nodes, (
        "flake.lock has no `cairn` node — the input is unlocked. Run "
        "`nix flake lock` and commit the result.")
    locked = nodes[INPUT_NAME].get("locked", {})
    assert locked.get("type") == "github", (
        f"the locked `cairn` input is not a github fetch: {locked!r}")
    assert locked.get("owner") == "ZacxDev" and locked.get("repo") == "cairn", (
        f"the locked `cairn` input points somewhere unexpected: {locked!r}")
    assert re.fullmatch(r"[0-9a-f]{40}", locked.get("rev", "")), (
        f"the locked `cairn` input carries no full revision: {locked!r}")


def test_the_cairn_input_does_NOT_follow_devrc_nixpkgs():
    """🔴 DELIBERATE, AND IT LOOKS LIKE AN OVERSIGHT — hence a guard.

    Every other input here either follows `nixpkgs` (home-manager) or is a
    frozen version pin that says in prose why it must not. `cairn` is the third
    case and the reason is upstream's, not ours: cairn's own flake pins
    `pkgs.python312` because `server/Dockerfile` is `python:3.12-slim` and its
    CI pins 3.12. A bare `pkgs.python3` there once followed nixpkgs to 3.14 and
    shipped an interpreter nothing in that repo had ever run its suite under.
    Adding `inputs.cairn.inputs.nixpkgs.follows = "nixpkgs"` would rebuild the
    client against devrc's `nixpkgs-unstable` — a nixpkgs cairn's CI has never
    tested — for the sole benefit of one fewer nixpkgs in the lock.

    ⚠ LABEL: this is an INVARIANT GUARD for the follows dimension. The `follows`
    line never existed, so this test never caught a live bug; it is red on the
    base commit only because the input itself is absent there. Its
    discriminating power was established by mutation instead — adding the
    `follows` line makes it red with THIS assertion's message.
    """
    # ⚠ VALIDATE THE HARNESS BEFORE READING A SURVIVED MUTANT HERE. `nix develop
    # -c pytest` REWRITES flake.lock when flake.nix's inputs have changed, so a
    # lock-only mutant is silently reverted before pytest opens the file and is
    # scored SURVIVED without ever being tested. Both halves below were killed
    # only after the battery switched to a bare interpreter (or
    # `nix develop --no-write-lock-file`). The lock is data the test reads, so
    # this is the mtime-cache trap in a different costume: the mutant never ran.
    #
    # 🔴 THE REGION, NOT A DOTTED SPELLING — MEASURED. The first draft matched
    # only `cairn.inputs.nixpkgs.follows`, and a mutant written the OTHER legal
    # way sailed past it:
    #     cairn = { url = "…"; inputs.nixpkgs.follows = "nixpkgs"; };
    # which is how `home-manager` is spelled twenty lines above, i.e. the FORM
    # an author is most likely to copy. A guard that a rewording walks is a
    # guard on WORDS; this one reads the whole input declaration instead.
    region = _cairn_input_region(_flake())
    assert "follows" not in region, (
        "flake.nix makes the cairn input follow devrc's nixpkgs. Don't: it "
        "rebuilds the client against a nixpkgs its CI never tested, and cairn "
        f"pins its interpreter on purpose.\ngot: {region.strip()!r}")

    nodes = _lock()["nodes"]
    assert INPUT_NAME in nodes, "flake.lock has no `cairn` node"
    dep = nodes[INPUT_NAME].get("inputs", {}).get("nixpkgs")
    assert dep is not None, (
        "the locked `cairn` node declares no nixpkgs input at all — upstream "
        "changed shape; re-read cairn's flake.nix before editing this.")
    # A FOLLOW is spelled as a list of path components (`["nixpkgs"]`); an
    # unfollowed input is a plain node name (`"nixpkgs_2"`). Structural, not a
    # spelling: a rename of the node cannot walk it.
    assert isinstance(dep, str), (
        "the locked `cairn` node's nixpkgs is a FOLLOW, so the client would be "
        f"built against devrc's nixpkgs: {dep!r}")


# ---------------------------------------------------------------------------
# 2. The package is THREADED — the declared-but-not-wired guard.
# ---------------------------------------------------------------------------

def test_the_outputs_function_BINDS_the_cairn_input_by_name():
    """`outputs = { … , ... }` swallows an unbound input silently.

    The argument set ends in `...`, so a `cairn` input that is declared and
    locked but never named in the outputs header is invisible: the flake
    evaluates, the lock grows an entry, and nothing consumes it.
    """
    text = _flake()
    header = re.search(r"outputs\s*=\s*\{([^}]*)\}", text)
    assert header is not None, "flake.nix has no `outputs = { … }` header"
    names = [n.strip() for n in header.group(1).split(",")]
    assert INPUT_NAME in names, (
        "the `outputs` function never binds `cairn`, so the input cannot be "
        f"consumed no matter what the lock says. bound: {names!r}")


def test_the_package_is_passed_to_the_home_module_through_extraSpecialArgs():
    """The middle link, and the one with two ways to be wrong.

    `extraSpecialArgs` is a free-form attrset: a typo'd name is not an error at
    this end, it is an unused argument. So both halves are pinned — the name is
    exported here, and the SAME name is consumed in nix/home.nix (next test).
    """
    text = _flake()
    args = _assignment(text, "extraSpecialArgs =")
    assert f"{THREADED_NAME} =" in args, (
        f"`extraSpecialArgs` does not export `{THREADED_NAME}`, so nix/home.nix "
        f"cannot see the package.\ngot: {args.strip()!r}")
    # 🔴 BIND THE REGEX TO THIS NAME'S OWN RIGHT-HAND SIDE. An earlier version
    # ran the two checks INDEPENDENTLY over the whole region, so a decoy
    # satisfied both while the deploy pointed elsewhere. MEASURED SURVIVED:
    #     cairnPackage = pkgs.hello;
    #     cairnUnused  = cairn.packages.${system}.cairn;
    # all 8 tests passed, and `home.file.".local/bin/cairn".source` became
    # `${pkgs.hello}/bin/cairn` — home-manager's `insertFileEntry` does an
    # unconditional `ln -s`, so that BUILDS and deploys a DANGLING symlink,
    # which is the outcome the required-argument design claims to prevent.
    # ⚠ `cairnPackage = pkgs.hello;` ALONE was KILLED, so the guard was narrower
    # than its own docstring rather than inert — the failure only appears when a
    # decoy carries the string the second assertion looks for.
    count = len(re.findall(rf"(?m)^\s*{re.escape(THREADED_NAME)}\s*=", args))
    assert count == 1, (
        f"expected exactly one `{THREADED_NAME} =` binding in "
        f"`extraSpecialArgs`, found {count}. A second one lets the asserted "
        f"binding and the threaded one be different lines.\ngot: {args.strip()!r}")
    rhs = _assignment(args, f"{THREADED_NAME} =")
    assert re.search(
        r"cairn\s*\.\s*packages\s*\.\s*\$\{system\}\s*\.\s*cairn", rhs), (
        f"`{THREADED_NAME}` is bound to something other than the cairn input's "
        "package — a binding of the right name pointing at the wrong thing is "
        f"the exact failure this file exists for.\ngot: {rhs.strip()!r}")


def test_home_nix_REQUIRES_the_package_argument_with_no_default():
    """🔴 NO `? null` DEFAULT, AND THAT IS THE POINT.

    A defaulted argument turns "the flake stopped passing the package" from an
    evaluation error into a silent null that interpolates as an empty string —
    i.e. a `~/.local/bin/cairn` symlink pointing at `/bin/cairn`. Required, so
    the thread breaking is loud.
    """
    # ⚠ NOT `split("\n", 1)[0]`. A nix module header may legally span lines, and
    # reading only the first would fail with "does not accept `cairnPackage`" on
    # a CORRECT tree — a red that names a cause the tree does not have, which is
    # the false-diagnosis failure this file's own helpers were written to avoid.
    # The header is everything up to the `:` that closes the argument set, and
    # `_module_header` RAISES rather than handing back the whole file when it
    # cannot find that `:` — see its docstring for what the fallback made vacuous.
    header = _module_header(_home())
    assert THREADED_NAME in header, (
        f"nix/home.nix does not accept `{THREADED_NAME}`, so the package the "
        f"flake exports is dropped on the floor.\ngot: {header!r}")
    assert not re.search(THREADED_NAME + r"\s*\?", header), (
        f"`{THREADED_NAME}` has a DEFAULT in nix/home.nix. A default makes an "
        "unthreaded package evaluate to a broken symlink instead of failing.\n"
        f"got: {header!r}")


# A module BODY that names `cairnPackage` — which every real nix/home.nix does,
# on the deploy line and in the comment above it. It is the reason a walk that
# falls back to the whole file passes: the name the header is supposed to bind is
# present further down, so `THREADED_NAME in header` is satisfied by the body.
_BODY_NAMING_THE_PACKAGE = """let
  workspace = "/tmp/w";
in
{
  # the pinned client, from the flake package
  home.file.".local/bin/cairn".source = "${cairnPackage}/bin/cairn";
}
"""


def _with_home(monkeypatch, tmp_path: Path, text: str) -> None:
    """Point this module's NIX_HOME at a synthetic module and nothing else."""
    fake = tmp_path / "home.nix"
    fake.write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "NIX_HOME", fake)


def test_the_header_walk_REFUSES_to_fall_back_to_the_WHOLE_FILE(monkeypatch, tmp_path):
    """🔴 THE GUARD ABOVE WENT VACUOUS EXACTLY WHERE IT MATTERS — MEASURED.

    A legal multi-line argument set that DROPS `cairnPackage` and carries one
    unbalanced `(` inside a prose comment never returns the bracket walk to depth
    0. The previous walk simply ran off the end and left `header` at its initial
    value, the whole file; both surviving assertions then read the BODY, where
    `cairnPackage` occurs, and the suite reported 8 passed while the argument the
    deploy line interpolates was gone from the header. The two defects compose:
    the comment is what breaks the walk, the body is what makes the failure
    silent.

    So this pins the RELATIONSHIP the fallback destroyed: the header check must
    fail when the header is unreadable, never pass on the file's other half.
    """
    _with_home(monkeypatch, tmp_path, (
        "{ config\n"
        ", pkgs\n"
        ", lib\n"
        "  # the package argument (threaded from flake.nix\n"
        ", isNixOS ? false\n"
        ", ...\n"
        "}:\n"
    ) + _BODY_NAMING_THE_PACKAGE)
    with pytest.raises(AssertionError) as exc:
        test_home_nix_REQUIRES_the_package_argument_with_no_default()
    assert "does not accept `cairnPackage`" in str(exc.value), (
        "the header walk did not report the DROPPED argument. If it raised "
        "`is never closed` instead, the comment is still carrying bracket depth; "
        f"if it passed, the walk fell back to the body again.\ngot: {exc.value}")


def test_an_UNCLOSED_argument_set_raises_instead_of_reading_the_whole_file(
        monkeypatch, tmp_path):
    """The fallback branch itself, reached by a case the test above cannot reach.

    ⚠ WHY BOTH. The test above supplies a header that DOES close once comments
    stop carrying depth, so it exercises the comment handling and never touches
    the "walk ran off the end" branch — a mutant that reinstates the whole-file
    fallback SURVIVES it, measured. This fixture is an argument set with no
    closing `}` at all, which no earlier assertion rejects and which nothing but
    that branch can answer. Its header names `cairnPackage`, so a fallback to the
    whole file would report the tree CLEAN.
    """
    _with_home(monkeypatch, tmp_path, (
        "{ config\n"
        ", pkgs\n"
        ", cairnPackage\n"
        ", ...\n"
    ) + _BODY_NAMING_THE_PACKAGE)
    with pytest.raises(AssertionError) as exc:
        test_home_nix_REQUIRES_the_package_argument_with_no_default()
    assert "is never closed" in str(exc.value), (
        "an argument set that never closes must be reported as such. Anything "
        "else means the walk fell back to reading the module body, which names "
        f"every argument the header is supposed to bind.\ngot: {exc.value}")


def test_a_leading_FILE_COMMENT_is_not_a_missing_argument_set(monkeypatch, tmp_path):
    """🔴 THE FALSE DIAGNOSIS, WHICH COSTS MORE THAN NO CHECK.

    `lstrip()` strips whitespace, not comments, so any `# …` line above the
    argument set failed with `does not open with a module argument set` on a
    tree whose header is CORRECT — sending the reader to look for a fault that is
    not there. Same class as the stale line numbers this pair of files already
    carries a note about: a red naming a cause the tree does not have.
    """
    _with_home(monkeypatch, tmp_path, (
        "# home-manager module for this host's user environment.\n"
        "/* and a block comment, which is also legal here. */\n"
        "{ config, pkgs, lib, isNixOS ? false, cairnPackage, ... }:\n"
    ) + _BODY_NAMING_THE_PACKAGE)
    test_home_nix_REQUIRES_the_package_argument_with_no_default()


# ---------------------------------------------------------------------------
# 3. The deploy relationship: `cairn` from the package, the two devrc-only
#    launchers NOT.
# ---------------------------------------------------------------------------

def test_cairn_deploys_from_the_package_and_the_devrc_only_launchers_stay_OUT_OF_STORE():
    """🔴 ONE TEST, ALL THREE, BECAUSE THE CHANGE IS THE RELATIONSHIP.

    Pinning them separately lets a later edit move them to the same mode and
    keeps some of the assertions green while the shipped set is broken: a
    store-copied `cairn-who` or `cairn-validate` dies on import (the `lib/` each
    resolves is devrc's, which is not deployed), and an out-of-store `cairn`
    re-forks the client this change just stopped forking.

    The split is 1-against-2 and the reason is per-binary, not per-taste:
    `cairn` is in-store because its flake package installs the script and `lib/`
    together under libexec; `cairn-who` and `cairn-validate` are out-of-store
    because nothing packages `lib/cairn_who.py` or `lib/subsystem_touch.py` —
    the OSS extraction took the READER only, and the writer is devrc-only by
    design.
    """
    text = _home()

    cairn = _assignment(text, 'home.file.".local/bin/cairn".source')
    assert "mkOutOfStoreSymlink" not in cairn, (
        "`cairn` is still deployed out-of-store from the checkout. It is "
        "supposed to come from the pinned flake package, which installs the "
        f"script and lib/ together under libexec.\ngot: {cairn.strip()!r}")
    assert f"${{{THREADED_NAME}}}" in cairn, (
        "`cairn` is not deployed from the threaded package — whatever it "
        f"points at, it is not the pinned client.\ngot: {cairn.strip()!r}")
    assert "/bin/cairn" in cairn, (
        "`cairn` does not name the package's `bin/cairn` wrapper. The wrapper "
        "is what supplies gitMinimal on PATH and execs the real script beside "
        f"its lib/.\ngot: {cairn.strip()!r}")

    who = _assignment(text, 'home.file.".local/bin/cairn-who".source')
    assert "mkOutOfStoreSymlink" in who, (
        "`cairn-who` is deployed as a STORE COPY. It is devrc-only, is absent "
        "from the OSS package, and resolves scripts/lib/cairn_who.py through "
        f"its own __file__ — it dies on import.\ngot: {who.strip()!r}")
    assert "${workspace}/devrc/scripts/cairn-who" in who, (
        f"`cairn-who` points somewhere unexpected: {who.strip()!r}")

    # 🔴 THE THIRD MEMBER, AND THE ONE THE CUTOVER CREATED. `cairn validate` is
    # no longer the write-protocol check — the packaged client reimplements it on
    # the READER's resolver — so `subsystem-index` has to name the WRITER, and a
    # bare command is the only spelling that resolves for an agent in another
    # repo. It carries `cairn-who`'s constraint exactly: it reaches
    # `lib/subsystem_touch.py` through its own `__file__`, and nothing ships that
    # module beside a store copy.
    validate = _assignment(text, 'home.file.".local/bin/cairn-validate".source')
    assert "mkOutOfStoreSymlink" in validate, (
        "`cairn-validate` is deployed as a STORE COPY. It resolves "
        "scripts/lib/subsystem_touch.py through its own __file__, and that "
        "module is the devrc-only WRITER — absent from the OSS package — so a "
        f"store copy dies on import.\ngot: {validate.strip()!r}")
    assert "${workspace}/devrc/scripts/cairn-validate" in validate, (
        f"`cairn-validate` points somewhere unexpected: {validate.strip()!r}")


# ---------------------------------------------------------------------------
# 3b. `cairn-validate` as a BINARY — the half no text check can see.
#
# 🔴 EVERY OTHER GUARD IN THIS FILE READS TEXT. That is right for a deploy line,
# and it is exactly the gap a standing finding names: nothing in devrc's gate has
# ever EXECUTED a cairn binary, so a structural check type-checks past a wrong
# argument — a launcher that assembled `--validate` in the wrong position, or
# defaulted `--store` to the frozen mirror, would satisfy every assertion above
# and print the wrong answer at exit 0. The tests below run the real file.
# ---------------------------------------------------------------------------

CAIRN_VALIDATE = REPO_ROOT / "scripts" / "cairn-validate"

#: The three advisory blocks the write protocol tells a writer to READ. They are
#: the whole reason the mandated check names the writer instead of the packaged
#: client, whose `validate` prints none of them and still exits 0.
WRITER_CONTRACT_BLOCKS = ("entry shape:", "marker reachability:", "dropped lines:")


def _fixture_store(tmp_path: Path, body: str) -> Path:
    """A minimal store with one scope and one entry file. Synthetic names only."""
    scope = tmp_path / "store" / "widgets"
    scope.mkdir(parents=True)
    (scope / "roster.md").write_text(body, encoding="utf-8")
    return tmp_path / "store"


_WELL_FORMED_ENTRY = (
    "---\n"
    "service: roster\n"
    "scope: widgets\n"
    "---\n"
    "\n"
    "## What it is\n"
    "The roster thing.\n"
    "\n"
    "## Pointers\n"
    "- `example/path` — synthetic.\n"
    "\n"
    "## Nuance / work-history\n"
    "- 2000-01-01: OPEN: synthetic.\n"
)


def _run_validate(store: Path, tmp_path: Path, *extra: str):
    import subprocess

    return subprocess.run(
        [sys.executable, str(CAIRN_VALIDATE), "--store", str(store),
         "--scope", "widgets", *extra],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_cairn_validate_RUNS_and_emits_the_writers_own_contract_blocks(tmp_path):
    """🔴 BEHAVIOURAL, AGAINST A REAL STORE — the claim is what it PRINTS.

    The packaged `cairn validate` and this launcher are both "green" on a clean
    store; what distinguishes them is that only one prints the three advisory
    blocks the protocol tells a writer to read, and `dropped lines:` is the one
    that means content is ALREADY LOST. A guard that only checked the exit code
    could not tell the two apart — which is precisely how the check stopped being
    run without anything going red.

    Run from a tmp cwd with `--scope`, so no git repo is needed and the answer
    cannot come from whatever repo the suite happens to sit in.
    """
    store = _fixture_store(tmp_path, _WELL_FORMED_ENTRY)
    proc = _run_validate(store, tmp_path)
    assert proc.returncode == 0, (
        f"`cairn-validate` exited {proc.returncode} on a well-formed entry\n"
        f"stdout: {proc.stdout[:800]}\nstderr: {proc.stderr[:800]}")
    missing = [b for b in WRITER_CONTRACT_BLOCKS if b not in proc.stdout]
    assert not missing, (
        f"`cairn-validate` printed none of {missing} — it is not running the "
        "WRITER's validate path. That is the whole reason this binary exists: "
        "the packaged client's `validate` is green and silent on exactly these "
        f"blocks.\nstdout: {proc.stdout[:800]}")
    assert str(store) in proc.stdout, (
        "the run did not report the store it was given, so it validated "
        f"something else.\nstdout: {proc.stdout[:800]}")


def test_cairn_validate_returns_the_WRITERS_exit_3_on_a_malformed_entry(tmp_path):
    """🔴 3, NOT 5 — the two tables must not be read for each other.

    `subsystem-index` says any non-zero exit means print the line and write
    NOTHING, and it names 3 for a malformed entry. The packaged client returns 5
    (`EXIT_CORRUPT`) for that condition and uses 3 for
    `EXIT_UNREACHABLE_NO_CACHE`, so a launcher that translated, clamped or
    re-numbered would hand the protocol a code meaning something else. The
    fixture is the wrapped `aliases: [...]` list — the real defect the front
    matter's line-by-line parser rejects, not an invented one.
    """
    store = _fixture_store(tmp_path, (
        "---\n"
        "service: roster\n"
        "scope: widgets\n"
        "aliases: [a,\n"
        "  b]\n"
        "---\n"
        "\n"
        "## Nuance / work-history\n"
        "- 2000-01-01: OPEN: synthetic.\n"
    ))
    proc = _run_validate(store, tmp_path)
    assert proc.returncode == 3, (
        f"expected the writer's exit 3 for a malformed entry, got "
        f"{proc.returncode}\nstdout: {proc.stdout[:800]}\n"
        f"stderr: {proc.stderr[:800]}")


def test_cairn_validate_defaults_its_store_to_the_SYNCED_CACHE_not_the_mirror(
        tmp_path):
    """🔴 THE DEFAULT IS THE POINT OF THE LAUNCHER, AND IT IS NOT THE WRITER'S.

    `subsystem_touch`'s own `--store` default is the FROZEN pre-cutover mirror
    (`~/.claude/analyze-service-index`, `0444`), which does not move when you
    write. Defaulting there would make the mandated post-write check parse the
    PRE-write bytes and report a clean entry that is not the one you wrote — a
    check that passes for the wrong reason, which is the failure mode this whole
    round is about.

    Asserted against `subsystem_read_store.read_store_root()` rather than a
    literal path: that function is THE one definition of the synced cache root,
    and a literal here would be the second copy. Run with NO `--store`, from a
    tmp cwd, and read the store the tool reports.

    🔴 READ BOTH STREAMS, BECAUSE THE SANDBOX HAS NO CACHE ROOT AND THAT IS NOT
    A FAILURE OF THE THING UNDER TEST. The first version of this asserted
    `f"store: {expected}"` on STDOUT, which holds on a dev host — where the
    cache exists and the tool takes its success path — and is structurally
    impossible in the `nix build` tier, whose `$HOME` is `/build/home` and
    carries no `~/.cache/subsystem-store`. There the tool exits down the
    not-found path and names the resolved root on STDERR instead. The claim
    being made is WHICH store the launcher chose, never whether one exists, and
    the tool names its choice on both paths — so the assertion belongs on the
    combined output. Pinning it to one stream made a guard that could only ever
    be green in one of the two tiers this suite runs in.

    The negative half is what kills the mutant: the FROZEN mirror's path must
    NOT appear. A launcher that dropped the `--store` prepend and inherited
    `subsystem_touch`'s own default would name the mirror here, and asserting
    only the positive half would let it through whenever both paths happened to
    be printed.
    """
    import subprocess

    sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
    import subsystem_read_store  # noqa: E402  (path set immediately above)
    import subsystem_touch  # noqa: E402

    expected = str(subsystem_read_store.read_store_root())
    assert expected != str(subsystem_touch.DEFAULT_STORE_ROOT), (
        "the synced cache and the frozen mirror resolve to the SAME path, so "
        "this test cannot tell the two defaults apart and is asserting nothing. "
        f"both: {expected!r}")
    proc = subprocess.run(
        [sys.executable, str(CAIRN_VALIDATE), "--scope", "widgets"],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    combined = proc.stdout + proc.stderr
    assert expected in combined, (
        "`cairn-validate` with no `--store` did not resolve the synced cache "
        f"({expected}). It names the store it chose on BOTH its success and its "
        "not-found path, so this holds whether or not a cache exists here.\n"
        f"stdout: {proc.stdout[:800]}\nstderr: {proc.stderr[:800]}")
    assert str(subsystem_touch.DEFAULT_STORE_ROOT) not in combined, (
        f"`cairn-validate` named the FROZEN mirror "
        f"({subsystem_touch.DEFAULT_STORE_ROOT}) — it inherited the writer's "
        "own `--store` default instead of prepending the synced cache, so the "
        "mandated post-write check would parse the PRE-write bytes.\n"
        f"stdout: {proc.stdout[:800]}\nstderr: {proc.stderr[:800]}")


def test_cairn_validate_is_a_LAUNCHER_and_declares_no_parser_of_its_own():
    """One parser, in the module the suite imports — the `cairn-who` rule.

    A second `ArgumentParser` here would be the copy that drifts, and
    `test_subsystem_touch.py` could not see it drift: every assertion there would
    still hold against the module's parser while the shipped binary served
    another. It is also what keeps the exit codes the writer's — a launcher that
    parsed would be a launcher that could decide what to return.
    """
    src = CAIRN_VALIDATE.read_text(encoding="utf-8")
    assert "ArgumentParser" not in src, (
        "scripts/cairn-validate declares its own parser. Delegate to "
        "`subsystem_touch.main` instead — that is the one the suite exercises.")
    assert "subsystem_touch.main" in src, (
        "scripts/cairn-validate does not delegate to `subsystem_touch.main`")
    assert os.access(CAIRN_VALIDATE, os.X_OK), (
        "scripts/cairn-validate is not executable — on PATH that is a "
        "`command not found` that reads as a broken deploy.")
    # 🔴 PIN THE RELATIONSHIP TO THE SIBLING, NOT A LITERAL. Both launchers are
    # deployed the same way (`mkOutOfStoreSymlink`) and are invoked as bare
    # commands from PATH, so they must agree on how they find an interpreter;
    # asserting `cairn-validate`'s first line in isolation would pass while the
    # two drifted apart. Comparing them also keeps this file free of a spelled
    # shebang, which `test_runtime_shebangs.py` scans every `test_*.py` for —
    # its allowlist is for sites that solve the problem a verified way, not a
    # place to register a string this assertion never needed.
    who_first = (REPO_ROOT / "scripts" / "cairn-who").read_text(
        encoding="utf-8").splitlines()[0]
    assert src.splitlines()[0] == who_first, (
        "scripts/cairn-validate's interpreter line disagrees with its sibling "
        f"scripts/cairn-who's ({who_first!r}). Both are out-of-store launchers "
        "run as bare commands from PATH; a difference here is a deploy "
        f"difference nobody chose.\ngot: {src.splitlines()[0]!r}")


# ---------------------------------------------------------------------------
# 4. The check that would otherwise stop looking.
# ---------------------------------------------------------------------------

def test_CAIRN_MIRROR_ROOT_is_exported_so_frozen_mirror_stays_OBSERVABLE():
    """🔴 UNSET IS NOT "PASSING" — IT IS "NOT ASKED".

    devrc's forked client had the frozen pre-cutover mirror's path hardcoded;
    the extracted one reads `CAIRN_MIRROR_ROOT` and, when it is empty, reports
    `frozen-mirror NOT-OBSERVABLE`, which contributes nothing to the verdict.
    Adopting the package without this export silently retires a check that was
    passing.
    """
    text = SESSION_VARS.read_text(encoding="utf-8")
    assert "CAIRN_MIRROR_ROOT" in text, (
        "nix/sessionVariables.nix does not export CAIRN_MIRROR_ROOT, so "
        "`cairn doctor` reports `frozen-mirror NOT-OBSERVABLE` and the "
        "read-only check on the frozen mirror silently stops running.")
    value = _assignment(text, "CAIRN_MIRROR_ROOT =")
    assert "analyze-service-index" in value, (
        "CAIRN_MIRROR_ROOT does not name the frozen pre-cutover mirror "
        f"(~/.claude/analyze-service-index).\ngot: {value.strip()!r}")
    assert "${homePath}" in value, (
        "CAIRN_MIRROR_ROOT is not derived from homePath — this file is "
        "evaluated for more than one home directory, and a literal /home/zach "
        f"is a claim about one host.\ngot: {value.strip()!r}")
