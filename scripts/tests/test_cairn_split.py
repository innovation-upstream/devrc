#!/usr/bin/env python3
"""Gate on the SEAM between `cairn` and `cairn-who` — two binaries, one predicate.

WHAT THIS FILE IS DEFENDING. `who` used to be a `cairn` subcommand, and the store
path reached the shared "is this a usable timeout" predicate by importing the
`who` module — a reach that worked only while the two were one program. Splitting
them therefore had a specific, plausible failure: move `cairn_who.py` out of
reach and either break the store client's import outright, or "fix" it by
re-open-coding the check. The second is the state that already shipped a bug
once: the two copies DISAGREED (only one excluded `bool`), while a comment
claimed they mirrored each other.

🔴 THESE ARE SEAM GUARDS, NOT COMPONENT GUARDS. `test_cairn_cli.py` and
`test_cairn_who.py` each test one surface hermetically, and both were green while
`who` was still welded into `cairn` — neither can see "the split left half the
system behind", because neither loads the other side. Every test here pins a
RELATIONSHIP between the two: what one exposes and the other must not, which file
owns the rule both import, and the deploy mode that has to hold for BOTH or the
shipped command does not start.

Every identifier here is INVENTED; nothing is read out of a real deployment.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CAIRN = REPO_ROOT / "scripts" / "cairn"
CAIRN_WHO = REPO_ROOT / "scripts" / "cairn-who"
LIB = REPO_ROOT / "scripts" / "lib"
TIMEOUTS = LIB / "timeouts.py"
NIX_HOME = REPO_ROOT / "nix" / "home.nix"
SKILL = REPO_ROOT / "claude" / "skills" / "cairn" / "SKILL.md"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _load_cairn_cli():
    """Exec `scripts/cairn` as a module — it has no .py extension."""
    import importlib.util

    spec = importlib.util.spec_from_loader("cairn_cli_split", loader=None,
                                           origin=str(CAIRN))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(CAIRN)
    exec(compile(CAIRN.read_text(encoding="utf-8"), str(CAIRN), "exec"), mod.__dict__)
    return mod


def _run(argv, cwd=None):
    """Drive a REAL binary in a subprocess, from an arbitrary cwd.

    🔴 `cwd` IS PART OF THE TEST, NOT SCAFFOLDING. Both scripts find `lib/`
    through `Path(__file__).resolve().parent / "lib"` precisely so they work from
    any working directory — that is the property `mkOutOfStoreSymlink` exists to
    preserve. Running them from the repo root only would pass even if they had
    been rewritten to resolve `lib/` relative to the cwd.
    """
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                          timeout=60, cwd=str(cwd) if cwd else None)


# --------------------------------------------------------------------------- #
# 1. What each binary exposes — the split itself
# --------------------------------------------------------------------------- #


def test_the_store_client_no_longer_exposes_a_who_SUBCOMMAND(tmp_path):
    """🔴 `cairn who` must be gone, and gone LOUDLY.

    Asserted on the real argparse surface rather than on the source text: a
    `who` parser could be re-added under any spelling, and a source grep for the
    string `"who"` matches this file's own comments and several unrelated words.
    argparse rejects an unknown subcommand with exit 2 and an "invalid choice"
    on stderr, so the failure is a diagnosis rather than a traceback.
    """
    proc = _run([str(CAIRN), "who", "42"], cwd=tmp_path)
    assert proc.returncode == 2, (
        f"`cairn who 42` exited {proc.returncode}, not argparse's 2 — the "
        f"subcommand looks like it is still there.\n"
        f"stdout: {proc.stdout[:400]}\nstderr: {proc.stderr[:400]}")
    assert "invalid choice" in proc.stderr, (
        f"the refusal does not name the problem:\n{proc.stderr[:600]}")

    # …and the parser really has no `who` action, not merely a renamed one.
    parser = _load_cairn_cli().build_parser()
    choices = set()
    for action in parser._actions:
        if getattr(action, "choices", None) and hasattr(action, "_name_parser_map"):
            choices |= set(action.choices)
    assert choices, "could not read the subcommand list — this guard checked NOTHING"
    assert "who" not in choices, f"`who` is still a cairn subcommand: {sorted(choices)}"


def test_the_cairn_who_BINARY_exists_and_actually_runs(tmp_path):
    """🔴 THE OTHER HALF. Removing `who` from `cairn` without a working
    `cairn-who` deletes a command rather than re-homing one.

    `--help` is the cheapest invocation that proves the whole import chain
    (`cairn-who` -> `lib/cairn_who.py` -> `lib/timeouts.py`) resolved and the
    parser built. Run from a tmp cwd, for the reason in `_run`.
    """
    assert CAIRN_WHO.exists(), "scripts/cairn-who does not exist"
    proc = _run([str(CAIRN_WHO), "--help"], cwd=tmp_path)
    assert proc.returncode == 0, (
        f"`cairn-who --help` exited {proc.returncode}\nstderr: {proc.stderr[:600]}")
    assert "task" in proc.stdout, f"the parser has no task positional:\n{proc.stdout}"
    assert "cairn-who" in proc.stdout, (
        f"the binary does not identify itself as `cairn-who`:\n{proc.stdout}")


def test_the_binary_is_EXECUTABLE_and_carries_a_shebang():
    """It goes on PATH as a bare command; a non-executable file there is a
    `command not found` that reads as a broken deploy."""
    import os

    assert os.access(CAIRN_WHO, os.X_OK), "scripts/cairn-who is not executable"
    first = CAIRN_WHO.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!"), f"no shebang: {first!r}"


def test_the_launcher_does_NOT_re_declare_the_parser():
    """🔴 ONE PARSER, IN THE MODULE THE TESTS IMPORT.

    `test_cairn_who.py` drives `cairn_who.main`. A second `ArgumentParser`
    declared in the launcher would be the copy that drifts, and that suite could
    not see it drift — every one of its assertions would still pass against the
    module's parser while the shipped binary served a different one.
    """
    src = CAIRN_WHO.read_text(encoding="utf-8")
    assert "ArgumentParser" not in src, (
        "scripts/cairn-who declares its own parser. Delegate to "
        "`cairn_who.main` instead — that is the one the suite exercises.")
    assert "cairn_who.main" in src, (
        "scripts/cairn-who does not delegate to `cairn_who.main`")


# --------------------------------------------------------------------------- #
# 2. The predicate the two still share
# --------------------------------------------------------------------------- #


def test_the_shared_predicate_lives_in_its_OWN_module_reachable_by_both():
    """🔴 THE REASON HALF of the seam below. If this fails, the import guards
    are pinning a structure that no longer exists — re-derive them, don't loosen.
    """
    assert TIMEOUTS.exists(), "scripts/lib/timeouts.py is gone"
    from timeouts import unbounded_timeout_reason  # noqa: PLC0415

    assert unbounded_timeout_reason(5) is None
    assert unbounded_timeout_reason(None) is not None


def test_NEITHER_binary_reaches_the_predicate_THROUGH_the_who_module():
    """🔴 THE COUPLING THIS SPLIT REMOVED, PINNED SO IT CANNOT COME BACK.

    `scripts/cairn` used to call `_cairn_who().unbounded_timeout_reason(...)`,
    importing the whole `who` module to borrow one function. That is what made
    `cairn_who.py` undeletable from the store client's side. The store client
    must not import it at all any more; `cairn-who` is the only thing that does.
    """
    store_src = CAIRN.read_text(encoding="utf-8")
    assert "import cairn_who" not in store_src, (
        "`scripts/cairn` imports the `who` module again. The predicate lives in "
        "`lib/timeouts.py`; import it from there rather than re-coupling the "
        "store client to a command it no longer owns.")
    assert "_cairn_who(" not in store_src, (
        "the `_cairn_who()` shim is back in `scripts/cairn`")


def test_BOTH_sides_import_the_predicate_from_the_module_that_owns_it():
    """🔴 ASSERTED AS AN ENUMERATED LEDGER, BOTH DIRECTIONS.

    The hazard is not only "a caller stops importing it" but "a new caller
    open-codes it instead". `test_cairn_who.py` owns the structural no-copies
    scan; this owns the positive side — every file that USES the rule gets it
    from `timeouts`.
    """
    # ⚠ BOTH IMPORT SPELLINGS PASS, DELIBERATELY. An earlier version required
    # the literal `from timeouts import unbounded_timeout_reason`, which reddens
    # on `import timeouts` + `timeouts.unbounded_timeout_reason(...)` — an
    # equally correct refactor producing identical behaviour. A guard that
    # reddens on a correct tree reports a problem the tree does not have, and
    # the next author's fix is to weaken the guard. What must hold is that the
    # name resolves to THIS module, not how it was spelled.
    # 🔴 PARSED, NOT GREPPED — a substring check here was satisfied by PROSE.
    # MEASURED: replacing `cairn_who.py`'s real import with a locally
    # re-open-coded predicate plus the comment "this used to import timeouts
    # and call timeouts.unbounded_timeout_reason(...)" left the substring
    # version GREEN on a file that no longer imports the predicate at all.
    import ast

    # 🔴 THE CALL MUST COME FROM THE IMPORT — and the previous AST version did
    # not require that, which made it WEAKER than the substring check it
    # replaced even though it was wider against prose. Wider on one axis,
    # narrower on another. MEASURED: keep `import timeouts`, add a local
    # `def unbounded_timeout_reason(value)` with byte-identical messages spelled
    # `type(value) is int` so the sibling `isinstance(_, int)` scan sees
    # nothing, and the suite was 103 passed / 0 failed — two copies free to
    # drift, the exact state `timeouts.py` exists to prevent. The substring
    # version this replaced went RED on that same file.
    for path in (CAIRN, LIB / "cairn_who.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        from_form = module_form = False
        bare_calls = attr_calls = local_def = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.module == "timeouts"
                    and any(a.name == "unbounded_timeout_reason"
                            for a in node.names)):
                from_form = True
            elif (isinstance(node, ast.Import)
                    and any(a.name == "timeouts" for a in node.names)):
                module_form = True
            elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "unbounded_timeout_reason"):
                local_def = True
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "unbounded_timeout_reason":
                    bare_calls = True
                elif (isinstance(fn, ast.Attribute)
                        and fn.attr == "unbounded_timeout_reason"
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id == "timeouts"):
                    attr_calls = True
        assert not local_def, (
            f"{path.name} DEFINES its own `unbounded_timeout_reason` — that is "
            f"the second copy, whatever it also imports. Delete it and use "
            f"`timeouts`.")
        assert from_form or module_form, (
            f"{path.name} does not import `timeouts` — neither "
            f"`from timeouts import unbounded_timeout_reason` nor "
            f"`import timeouts`")
        assert (from_form and bare_calls) or (module_form and attr_calls), (
            f"{path.name} imports `timeouts` but its call to "
            f"`unbounded_timeout_reason` does not come from that import — a "
            f"dead import beside a locally-resolved call is not a guard "
            f"(from-import={from_form}, module-import={module_form}, "
            f"bare call={bare_calls}, `timeouts.`-qualified call={attr_calls})")


#: Values that must be refused as a timeout. Kept in step with
#: `test_cairn_who.py::UNBOUNDED_TIMEOUTS`; the two files assert different
#: things about the same set.
UNBOUNDED = [None, 0, -1, "60", True, False, 1.5]


@pytest.mark.parametrize("bad", UNBOUNDED)
def test_the_STORE_fetch_STILL_refuses_an_unbounded_timeout_after_the_split(bad):
    """The regression the split could most plausibly have broken.

    ⚠ LABELLED HONESTLY: with respect to `origin/main` this is an INVARIANT
    GUARD, not regression coverage — the guard predates the split and all 7
    cases pass on the pre-change tree. What it CAN see is the split going wrong,
    and that was measured, not assumed. Two mutations, both run with
    `PYTHONDONTWRITEBYTECODE=1`:

      * delete the `bad = unbounded_timeout_reason(timeout)` / `raise` pair from
        `fetch_snapshot` -> all 7 cases RED, each with this test's own message
        ("did not refuse … it failed for some other reason: … Connection
        refused"), i.e. red for the intended reason and not another guard's;
      * delete ONLY the `isinstance(value, bool)` branch from `timeouts.py` ->
        exactly `[True]` RED here AND `[True]` red in three tests in
        `test_cairn_who.py`, with every other case green. That is the tight
        isolation: it proves this call site reaches the SHARED predicate, that
        `cairn-who` reaches the same one, and that `[False]` survives because a
        DIFFERENT branch (`value <= 0`) catches it — so no case is passing on
        another guard's error.

    `bool` is the case that matters most: it subclasses `int`, so a
    "simplification" to `isinstance(timeout, int)` accepts `True` and runs with a
    silent ONE-SECOND bound.
    """
    cli = _load_cairn_cli()
    with pytest.raises(cli.StoreUnreachable) as exc:
        cli.fetch_snapshot("http://127.0.0.1:1", "tok", scope=None, timeout=bad)
    assert "refusing to fetch" in str(exc.value), (
        f"the store fetch did not refuse timeout={bad!r}; it failed for some "
        f"other reason: {exc.value}")


def test_a_POSITIVE_bound_is_NOT_refused_by_the_store_fetch():
    """🔴 POSITIVE CONTROL. A guard that refuses EVERYTHING also passes every
    case above, and `fetch_snapshot` raises `StoreUnreachable` for a genuine
    outage too — so the parametrized test cannot, on its own, tell "refused the
    bound" from "could not reach the host". This pins that a valid bound gets
    past the predicate and fails later, on the network.
    """
    cli = _load_cairn_cli()
    with pytest.raises(cli.StoreUnreachable) as exc:
        cli.fetch_snapshot("http://127.0.0.1:1", "tok", scope=None, timeout=5)
    assert "refusing to fetch" not in str(exc.value), (
        f"a valid bound was refused: {exc.value}")


def test_the_TWO_defaults_stay_DIFFERENT_and_both_are_usable():
    """🔴 THE PREDICATE IS SHARED; THE DEFAULTS ARE NOT, DELIBERATELY.

    20s is tuned for an HTTP snapshot fetch, 60s for shelling into tmux on two
    hosts with one possibly asleep. Consolidating them into one constant would
    erase the reason each was chosen — the mirror image of the copy-drift bug,
    and the tempting "cleanup" now that a shared module exists. Asserted as a
    relationship (who's is strictly the longer) rather than as two literals,
    which would just be the constants restated in a second place.
    """
    import cairn_who as W  # noqa: PLC0415
    from timeouts import unbounded_timeout_reason  # noqa: PLC0415

    cli = _load_cairn_cli()
    assert unbounded_timeout_reason(cli.DEFAULT_TIMEOUT) is None
    assert unbounded_timeout_reason(W.DEFAULT_TIMEOUT) is None
    assert W.DEFAULT_TIMEOUT > cli.DEFAULT_TIMEOUT, (
        f"cairn-who's default ({W.DEFAULT_TIMEOUT}s) is no longer longer than "
        f"the store's ({cli.DEFAULT_TIMEOUT}s) — the sleeping-laptop rationale "
        "in cairn_who.DEFAULT_TIMEOUT's docstring is not what ships")


# --------------------------------------------------------------------------- #
# 3. The deploy, and the doc that routes people to it
# --------------------------------------------------------------------------- #


def test_cairn_who_still_resolves_its_lib_relative_to_its_OWN_file():
    """The REASON half of the deploy seam, same shape as the `cairn` pair in
    `test_cairn_cli.py`. If this fails, the out-of-store requirement below may be
    obsolete — re-derive it rather than editing the assertion."""
    src = CAIRN_WHO.read_text(encoding="utf-8")
    assert 'Path(__file__).resolve().parent / "lib"' in src, (
        "scripts/cairn-who no longer derives its lib/ path from __file__")
    assert "import cairn_who" in src, "scripts/cairn-who imports nothing from lib/"
    assert (LIB / "cairn_who.py").exists()


def test_cairn_who_is_deployed_OUT_OF_STORE_unlike_its_sibling():
    """🔴 THE DEPLOY half. A store copy ships a `cairn-who` that cannot start.

    ⚠ UNLIKE, NOT LIKE — this test was renamed when `cairn` moved to the pinned
    flake package. The two deploy modes now DIFFER on purpose: `cairn` ships as
    a store path because its package installs the real script and `lib/`
    together, and `cairn-who` cannot, because it is devrc-only, absent from the
    OSS package, and the `lib/` it resolves is `scripts/lib/`. Reading that
    difference as an inconsistency and "fixing" it in either direction breaks
    one of the two binaries.

    This test still owns only ONE side. The RELATIONSHIP — `cairn` from the
    package AND `cairn-who` out-of-store, asserted together so a change that
    moved both cannot leave half the pair green — is pinned in
    `test_cairn_flake_pin.py`.
    """
    nix = NIX_HOME.read_text(encoding="utf-8")
    key = 'home.file.".local/bin/cairn-who".source'
    assert key in nix, "the `cairn-who` PATH entry is missing from nix/home.nix"
    assignment = nix.split(key, 1)[1].split(";", 1)[0]
    assert "mkOutOfStoreSymlink" in assignment, (
        "`cairn-who` is deployed as a STORE COPY. Its "
        "`Path(__file__).resolve()` lib lookup resolves into /nix/store, where "
        "scripts/lib/ is not deployed, and the command fails on import.\n"
        f"got: {assignment.strip()!r}")
    assert "${workspace}/devrc/scripts/cairn-who" in assignment, (
        f"`cairn-who` points somewhere unexpected: {assignment.strip()!r}")


#: The `cairn` deploy comment's WHY paragraph, whitespace-normalised. Pinned
#: WHOLE and verbatim: this artifact is PROSE, and every partial predicate tried
#: against it was walkable by rewording (the withdrawn drafts are listed in the
#: test below). A cosmetic edit to the comment now fails this test — that cost is
#: the price of a machine-readable claim, and updating this string is the fix.
def _normalised_why(block):
    """The WHY paragraph out of a `#` comment block, or None if it is absent.

    The paragraph runs from the `mkOutOfStoreSymlink is NOT a preference` line
    to the next bare `#`, with comment markers stripped and whitespace
    collapsed — so re-wrapping the comment is not a failure, but changing a
    word is.
    """
    lines = block.splitlines()
    start = next(
        (i for i, ln in enumerate(lines)
         if "mkOutOfStoreSymlink is NOT a preference" in ln), None)
    if start is None:
        return None
    para = []
    for ln in lines[start:]:
        stripped = ln.strip()
        if stripped == "#":
            break
        para.append(stripped.lstrip("#").strip())
    return " ".join(" ".join(para).split())


# 🔴 UPDATED WHEN `cairn` MOVED TO THE PINNED FLAKE PACKAGE, and the old text is
# worth recording because it was TRUE and is now FALSE. It read: "it is REQUIRED.
# `scripts/cairn` reaches its siblings through `Path(__file__).resolve().parent /
# "lib"` … A store copy would resolve `__file__` into /nix/store, where `lib/` is
# NOT deployed — the import would fail outright." Every clause of that is STILL
# TRUE of `scripts/cairn`; what changed is that `scripts/cairn` is no longer the
# deployed artifact. The package satisfies the same requirement by a different
# mechanism — real script and `lib/` installed TOGETHER under libexec — so the
# paragraph had to be rewritten rather than deleted, and this test going red on
# that commit was the design working, not an obstacle to route around.
NIX_DEPLOY_WHY = (
    "🔴 mkOutOfStoreSymlink is NOT a preference here — it is REQUIRED for "
    "`cairn-who` below, and NO LONGER required for `cairn`. That asymmetry is "
    "the whole point of this pair, so read both lines together. The underlying "
    "constraint is unchanged and belongs to BOTH scripts: each reaches its "
    'siblings through `Path(__file__).resolve().parent / "lib"`, and '
    "`.resolve()` follows symlinks, so what must hold is that the directory "
    "holding the REAL file also holds `lib/`. Out-of-store satisfies that by "
    "resolving back into the checkout. The pinned `cairn` flake package "
    "satisfies it a SECOND way — it installs the real script and its `lib/` "
    "together under `libexec` and puts a wrapper in `bin/` — which is why a "
    "store path is now correct for that binary and only that binary. "
    "`cairn-who` has no such package: it is devrc-only, deliberately absent "
    "from the OSS repo, and the `lib/` it resolves is `scripts/lib/`, which "
    "must not be deployed, same rule as opencode's `lib/`. Deploying it "
    "in-store would leave HALF the split working."
)


def test_the_nix_deploy_comment_pins_its_WHY_paragraph_verbatim():
    """The `cairn` deploy comment must keep saying WHERE `mkOutOfStoreSymlink`
    is still REQUIRED and where it no longer is — the constraint is the same for
    both binaries (the directory holding the REAL file must hold `lib/`) and only
    `cairn` has a package that satisfies it a second way. A reader who takes the
    asymmetry for an oversight breaks whichever side they "tidy".
    """
    nix = NIX_HOME.read_text(encoding="utf-8")
    lines = nix.splitlines()
    anchor = next(
        (i for i, ln in enumerate(lines)
         if 'home.file.".local/bin/cairn".source' in ln), None)
    assert anchor is not None, (
        'nix/home.nix has no `home.file.".local/bin/cairn".source` entry')

    # The comment block is however many contiguous `#` lines sit immediately
    # above the entry — no character budget to slide off.
    start = anchor
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    block = "\n".join(lines[start:anchor])
    assert block.strip(), (
        "the `cairn` deploy entry has no comment above it at all — the "
        "requirement it documents is load-bearing, so its disappearance is "
        "the same defect as its going stale")

    # 🔴 FOUR PARTIAL PREDICATES WERE TRIED AND NONE HELD. Count the numbered
    # items rather than trusting this sentence — an earlier draft said THREE
    # above a list of four, in the very commit whose subject was "four
    # self-descriptions disagreeing".
    #
    # 🔴 AND THEY FAILED IN TWO OPPOSITE DIRECTIONS. Reading them as a single
    # "all too weak" set is what invites the next author to re-derive #3 as a
    # tightened version — so the direction is named per item:
    #
    #   1. TOO WEAK — a SPELLING check (`"for `cairn_who`" not in block`). The
    #      shipped comment says "for the `cairn_who` importers" and passed on
    #      the inserted "the" alone.
    #   2. TOO WEAK — "does the code actually reach it", by substring, satisfied
    #      by the script's OWN PROSE (`_store_timeout`'s docstring names
    #      `cairn_who._run`), so the assertion was unreachable. MEASURED: the
    #      reworded justification left it GREEN.
    #   3. TOO STRICT — the same by AST. Accurate about the code, and wrong
    #      here anyway: it forbids the RETRACTION this comment legitimately
    #      carries, so it went RED on a correct tree. A live justification and
    #      a record of one being removed are the same tokens in a different
    #      mood. Do NOT re-derive this one as a tighter AST check; the problem
    #      was never its precision.
    #   4. TOO WEAK — `"mkOutOfStoreSymlink" in block or "REQUIRED" in block`. MEASURED:
    #      deleting the whole WHY paragraph and leaving
    #      "# Deployed with mkOutOfStoreSymlink, same as claim-work above."
    #      kept it GREEN, while the test's NAME claimed the reason was pinned.
    #      ⚠ UNITS: that is a net delete of 468 CHARACTERS / 475 bytes (the
    #      paragraph was 532 chars / 539 bytes AT THAT TIME — it has since been
    #      rewritten for the flake-package cutover, so do not re-measure the
    #      current text against those figures). Earlier receipts said "473-byte"
    #      and "468 B"; the first matched nothing and the second was `len(str)`
    #      labelled as bytes. `len()` on a str is characters — the em-dashes in
    #      this paragraph are 3 bytes each, so the two never agree here.
    #
    # So the paragraph is pinned WHOLE, per "when the artifact under test IS
    # prose, pin the whole normalised string".
    why = _normalised_why(block)
    assert why is not None, (
        "the `cairn` deploy comment no longer contains its WHY paragraph — the "
        "line beginning `mkOutOfStoreSymlink is NOT a preference here` is gone. "
        "That paragraph is where the deploy ASYMMETRY is explained: `cairn` "
        "ships from the pinned package, `cairn-who` must stay out-of-store or "
        "it dies on import because scripts/lib/ is not deployed.")
    assert why == NIX_DEPLOY_WHY, (
        "the `cairn` deploy comment's WHY paragraph changed. If the edit was "
        "deliberate, update NIX_DEPLOY_WHY in this file to match — the "
        "paragraph is pinned whole because every partial check of it was "
        f"walkable by rewording.\n\ngot:      {why!r}\nexpected: {NIX_DEPLOY_WHY!r}")


def test_the_SKILL_routes_to_the_binary_not_the_dead_subcommand():
    """🔴 THE ROUTER MUST NOT NAME A COMMAND THAT EXITS 2.

    The skill is a router: a verb table row that says `cairn who <task>` sends a
    session to a command argparse rejects. Pinned on the table row and the
    frontmatter description together, because they are read at different times —
    the description decides whether the skill loads at all.
    """
    text = SKILL.read_text(encoding="utf-8")
    assert "`cairn-who" in text, "the skill never names the `cairn-who` binary"
    assert "`cairn who" not in text, (
        "the skill still routes to `cairn who`, which argparse now rejects. "
        "Say `cairn-who`; do not spell the dead subcommand anywhere, not even "
        "to explain that it is dead — a router is read for what to TYPE.")

    row = [ln for ln in text.splitlines()
           if ln.startswith("|") and "transcripts worked a task" in ln]
    assert len(row) == 1, f"expected exactly one verb-table row for it, got {row}"
    assert "`cairn-who <task>`" in row[0], f"the verb table still says: {row[0]}"

    desc = [ln for ln in text.splitlines() if ln.startswith("description:")]
    assert len(desc) == 1, "could not read the frontmatter description"
    assert "cairn-who" in desc[0], (
        f"the frontmatter description does not name the binary: {desc[0]}")
