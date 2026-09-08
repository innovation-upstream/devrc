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
import re
from pathlib import Path

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
    text = _home()
    # ⚠ NOT `split("\n", 1)[0]`. A nix module header may legally span lines, and
    # reading only the first would fail with "does not accept `cairnPackage`" on
    # a CORRECT tree — a red that names a cause the tree does not have, which is
    # the false-diagnosis failure this file's own helpers were written to avoid.
    # The header is everything up to the `:` that closes the argument set.
    depth = 0
    header = text
    for i, ch in enumerate(text):
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                header = text[:text.index(":", i) + 1] if ":" in text[i:] else text[:i + 1]
                break
    assert header.lstrip().startswith("{"), (
        f"nix/home.nix does not open with a module argument set: {header[:120]!r}")
    assert THREADED_NAME in header, (
        f"nix/home.nix does not accept `{THREADED_NAME}`, so the package the "
        f"flake exports is dropped on the floor.\ngot: {header!r}")
    assert not re.search(THREADED_NAME + r"\s*\?", header), (
        f"`{THREADED_NAME}` has a DEFAULT in nix/home.nix. A default makes an "
        "unthreaded package evaluate to a broken symlink instead of failing.\n"
        f"got: {header!r}")


# ---------------------------------------------------------------------------
# 3. The deploy relationship: `cairn` from the package, `cairn-who` NOT.
# ---------------------------------------------------------------------------

def test_cairn_deploys_from_the_package_and_cairn_who_stays_OUT_OF_STORE():
    """🔴 ONE TEST, BOTH SIDES, BECAUSE THE CHANGE IS THE RELATIONSHIP.

    Pinning them separately lets a later edit move both to the same mode and
    keeps one of the two assertions green while the shipped pair is broken:
    a store-copied `cairn-who` dies on import (its `lib/` is devrc's and is not
    deployed), and an out-of-store `cairn` re-forks the client this change just
    stopped forking.
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
