"""Tests for `scripts/regen-known-repos.py` — the mention-mapping generator.

🔴 THE FILE THIS GENERATES MUST NEVER BE TRACKED. `gh api user/repos` returns
private repos, and an earlier version of this generator wrote its output into
`scripts/collector/known_repos.py`, which was committed to this PUBLIC repo:
232 private repositories, 217 of them named nowhere else in the tree. The first
test below is the guard against that ever recurring; the rest pin the filters
that keep a wrong answer out of the mapping.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
GENERATOR = ROOT / "scripts" / "regen-known-repos.py"

_spec = importlib.util.spec_from_file_location("regen_known_repos_under_test", GENERATOR)
RG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RG)


# --------------------------------------------------------------------------- #
# 🔴 The disclosure guard
# --------------------------------------------------------------------------- #
_PAIR_RE = re.compile(
    r"""["']([A-Za-z0-9][A-Za-z0-9._-]*)["']\s*:\s*["']([A-Za-z0-9-]+/[A-Za-z0-9._-]+)["']""")

# A repo mapping is many DISTINCT names pointing at `owner/repo`. Counting
# occurrences instead flagged a dl-router fixture that repeats ONE key 45 times
# (`dupRelPath: "john-smith/75936.mov"`); the leaked file had 420 distinct keys.
MAPPING_KEY_THRESHOLD = 20


def looks_like_a_repo_mapping(text: str) -> int:
    """How many DISTINCT `name: "owner/repo"` keys `text` carries."""
    return len({k for k, _ in _PAIR_RE.findall(text)})


# 🔴 A SECOND SHAPE, BECAUSE THE GENERATOR NOW EMITS A SECOND FILE — AND THE
# DETECTOR ABOVE IS STRUCTURALLY BLIND TO IT. `known_universe.json` is a JSON
# LIST of `owner/repo` strings: it carries no `key: value` pairs at all, so
# `_PAIR_RE` finds nothing and `looks_like_a_repo_mapping` returns 0 for a file
# that is a WIDER disclosure than the mapping (it is deliberately unfiltered, so
# it names MORE private repositories). Committing one would have sailed past the
# guard that exists precisely to stop that — the same structural blindness that
# let the original incident through, in a new shape.
_FULL_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _owner_repo_count(items) -> int:
    return len({s for s in items
                if isinstance(s, str) and _FULL_NAME_RE.match(s)})


def looks_like_a_repo_universe(text: str) -> int:
    """How many DISTINCT `owner/repo` strings sit in a LIST LITERAL in `text`.

    🔴 STRUCTURAL, NOT TEXTUAL, AND THE FIRST VERSION WAS TEXTUAL AND USELESS.
    It counted every quoted `a/b` anywhere in the file and produced NINE false
    accusations on the first run — `claude/skills/clickup/package-lock.json` (46,
    every one an npm `node_modules/...` path) plus eight test files whose
    synthetic fixtures merely mention repos in passing. A guard that fires on
    ordinary files is a guard everyone learns to override.

    So the question asked here is the one that actually matters: is this file a
    LIST OF REPOSITORIES? That is the artefact `write_universe` emits, and it is
    a shape no lockfile and no docstring has. Both spellings are covered — a
    JSON document (the file itself, committed by accident) and a Python list
    literal (the shape the ORIGINAL incident took, in which a generator wrote
    its output into a `.py` module that was then committed).

    ⚠ RESIDUAL, STATED RATHER THAN PAPERED OVER: a universe split across several
    smaller literals, or built by a comprehension, is NOT counted. This detects
    the dump, which is the way this has actually gone wrong twice; it is not a
    general private-name scanner, and `claude/RULES.md` already records that no
    gate in this repo covers repo names in prose.
    """
    best = 0
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            doc = json.loads(stripped)
        except ValueError:
            doc = None
        if isinstance(doc, list):
            best = max(best, _owner_repo_count(doc))
    # The Python-literal spelling. `ast.parse` rather than a regex, so a list is
    # recognised as a list rather than as "some quoted strings near brackets".
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return best
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            best = max(best, _owner_repo_count(
                [e.value for e in node.elts if isinstance(e, ast.Constant)]))
    return best


def test_the_mapping_detector_FIRES_on_a_realistic_mapping():
    """🔴 THE NEGATIVE CONTROL — without it, the sweep below is a zero that may
    be indistinguishable from a detector wired to nothing. Built from realistic
    data in both spellings the generator emits, not a textbook fixture."""
    synthetic = "KNOWN_REPOS = {\n" + "".join(
        f"    '{n}{i}': 'gardenersguild/{n}{i}',\n    '{n}{i}'.lower(): "
        f"'gardenersguild/{n}{i}',\n"
        for i, n in enumerate(["Trowelcast", "SledgeHorn", "PloughShare"] * 9)
    ) + "}\n"
    assert looks_like_a_repo_mapping(synthetic) >= MAPPING_KEY_THRESHOLD
    # …and it does NOT fire on an ordinary small dict of the same shape.
    assert looks_like_a_repo_mapping(
        "{'a': 'o/a', 'b': 'o/b', 'c': 'o/c'}") < MAPPING_KEY_THRESHOLD


def test_the_UNIVERSE_detector_FIRES_and_the_mapping_one_is_BLIND_to_it():
    """🔴 THE NEGATIVE CONTROL FOR THE SECOND SHAPE — and it asserts the BLIND
    SPOT explicitly, because that is the finding rather than a side note.

    Built from a REALISTIC artefact: the exact JSON `write_universe` emits, not
    a textbook fixture. `looks_like_a_repo_mapping` returns 0 on it — a list has
    no `key: value` pairs — so before this detector existed, committing the
    picker universe to this public repo would have passed the incident guard
    that exists to prevent exactly that.
    """
    universe = json.dumps(sorted(
        f"gardenersguild/{n}{i}"
        for i, n in enumerate(["Trowelcast", "SledgeHorn", "PloughShare"] * 9)
    ), indent=1)
    assert looks_like_a_repo_universe(universe) >= MAPPING_KEY_THRESHOLD
    assert looks_like_a_repo_mapping(universe) == 0, (
        "the pair matcher must be shown BLIND to this shape — if it ever fires "
        "here, the second detector's justification has changed and this test "
        "is the one that should say so")
    # …and it does NOT fire on a file that merely mentions a few repos, which is
    # every docstring and comment in this tree.
    assert looks_like_a_repo_universe(
        "see 'civitai/talos-infra' and 'acme/widget'") < MAPPING_KEY_THRESHOLD


# Directories that exist only in a working checkout and are gitignored there.
# Only consulted on the WALK path — `git ls-files` already excludes them.
_WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", ".direnv",
              "result", ".mypy_cache", ".pytest_cache"}


def candidate_files(root: Path | None = None) -> tuple[str, list[Path]]:
    """(how, files) — every `.json`/`.py` this repo would publish.

    🔴 TWO TIERS, TWO VIEWS, AND THE GUARD MUST WORK IN BOTH. `git ls-files` is
    the view the other content gates use, but the sandbox check derivation
    builds from a `cp -r` store copy with NO `.git`, so git fails there. A skip
    would make a DISCLOSURE guard invisible in the very tier the merge gates on
    — so the fallback WALKS the tree instead, which is if anything a wider view
    (it is exactly what was copied in). `how` is returned so the assertion
    message can say which view produced the verdict.
    """
    root = root or ROOT
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, timeout=60)
    if tracked.returncode == 0 and tracked.stdout.strip():
        return "git ls-files", [
            root / r for r in tracked.stdout.split("\0")
            if r.endswith((".json", ".py"))]

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP]
        for fn in filenames:
            if fn.endswith((".json", ".py")):
                found.append(Path(dirpath) / fn)
    return "filesystem walk (no .git — sandbox tier)", found


def test_the_generated_mapping_is_NOT_published_anywhere_in_this_repo():
    """🔴 THE INCIDENT GUARD. This repo is public and the mapping names private
    repositories. Keyed on CONTENT rather than on a filename, so a copy under
    any name or in any directory fails it: the claim is 'no published file IS
    this mapping', not 'the old path is absent'.
    """
    how, files = candidate_files()
    assert len(files) > 100, (
        f"the sweep examined only {len(files)} file(s) via {how} — a zero from "
        f"a sweep that walked nothing is the failure, not the all-clear")
    offenders = []
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        keys = looks_like_a_repo_mapping(text)
        if keys >= MAPPING_KEY_THRESHOLD:
            offenders.append(f"{path.relative_to(ROOT)} ({keys} distinct repo keys)")
            continue
        # 🔴 THE UNIVERSE SHAPE, CHECKED ON THE SAME SWEEP RATHER THAN IN A
        # SECOND TEST — one walk, one `len(files) > 100` floor, one place for a
        # future third shape. A separate test would need its own floor and would
        # be the one nobody notices has stopped walking anything.
        fulls = looks_like_a_repo_universe(text)
        if fulls >= MAPPING_KEY_THRESHOLD:
            offenders.append(
                f"{path.relative_to(ROOT)} ({fulls} distinct owner/repo names)")
    assert offenders == [], (
        f"file(s) look like a repo mapping or picker universe (via {how}): "
        f"{offenders}. Both name PRIVATE repositories and this repo is PUBLIC — "
        f"they belong at {RG.DEFAULT_PATH} and {RG.DEFAULT_UNIVERSE_PATH}, "
        f"outside every checkout.")


def test_the_default_output_path_is_outside_every_checkout():
    """The path is the whole mitigation, so it is pinned. Under the operator's
    config dir, not under any repo, not under /tmp."""
    p = RG.DEFAULT_PATH
    assert p.is_absolute()
    assert p.name == "known_repos.json"
    assert "workspace" not in p.parts, p
    assert p.parent.name == "mention-open"


def test_the_written_file_is_not_world_readable(tmp_path):
    """It names private repositories, on a machine with other accounts possible."""
    out = tmp_path / "sub" / "known_repos.json"
    RG.write_mapping({"a": "o/a"}, out)
    assert json.loads(out.read_text()) == {"a": "o/a"}
    assert oct(os.stat(out).st_mode)[-3:] == "600"


# --------------------------------------------------------------------------- #
# build_mapping — the filters
# --------------------------------------------------------------------------- #
def _row(full, has_issues=True):
    return {"full_name": full, "has_issues": has_issues}


def test_a_repo_is_mapped_under_both_its_own_case_and_lowercase():
    """`mention_scan._resolve_repo` does an EXACT dict lookup with no case
    folding anywhere, and GitHub names are case-insensitive — so a
    canonical-case key alone cannot match `sledgehorn#12`."""
    out = RG.build_mapping([_row("gardenersguild/SledgeHorn")], {})
    assert out["SledgeHorn"] == "gardenersguild/SledgeHorn"
    assert out["sledgehorn"] == "gardenersguild/SledgeHorn"


def test_a_repo_with_issues_DISABLED_is_dropped_entirely():
    """🔴 `repo#N` builds an ISSUES url. A repo with issues disabled 404s for
    every N, so mapping it produces a confident wrong page rather than a
    refusal. Measured on the first version: 44 of 383, including the fork whose
    `#100` was the example used to justify the mapping."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast", has_issues=False),
         _row("gardenersguild/sledgehorn", has_issues=True)], {})
    assert "trowelcast" not in out and "sledgehorn" in out


def test_a_name_owned_by_TWO_owners_is_dropped_rather_than_arbitrated():
    """🔴 Last-write-wins silently picked one. Measured on the first version:
    7 collisions, and `bitdex` resolved to a third party rather than the
    client. An ambiguous name must resolve to NOTHING — the operator writes
    `owner/repo#N`, which is source 1."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast"), _row("hobbyist/trowelcast")], {})
    assert "trowelcast" not in out
    assert "Trowelcast" not in out
    # …and the collision does not take an unrelated repo down with it.
    out2 = RG.build_mapping(
        [_row("gardenersguild/trowelcast"), _row("hobbyist/trowelcast"),
         _row("gardenersguild/sledgehorn")], {})
    assert out2["sledgehorn"] == "gardenersguild/sledgehorn"


def test_a_local_checkout_settles_a_name_the_api_had_to_drop():
    """A checkout is a MEASUREMENT of this disk, so it is the authority on its
    own owner and outranks the ambiguity."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast"), _row("hobbyist/trowelcast")],
        {"trowelcast": "gardenersguild/trowelcast"})
    assert out["trowelcast"] == "gardenersguild/trowelcast"


def test_a_checkout_DISAGREEING_with_the_api_row_resolves_to_nothing():
    """🔴 THE GENERATOR IS NARROWER THAN THE HANDLER, ON PURPOSE — and these two
    tests used to contradict each other, which is how the contradiction was
    found. At click time `mention-open.py` has just MEASURED the remote, so a
    checkout wins there. This file is a SNAPSHOT that can be months old, so a
    checkout disagreeing with an API row is two claims about one name with no
    way to tell which is current: the module's answer to that is NOTHING, and
    the operator writes `owner/repo#N`."""
    out = RG.build_mapping([_row("hobbyist/sledgehorn")],
                           {"sledgehorn": "gardenersguild/sledgehorn"})
    assert "sledgehorn" not in out, out.get("sledgehorn")


def test_a_checkout_directory_named_differently_maps_BOTH_spellings():
    """A checkout whose DIRECTORY name differs from the repo name — the shape
    that occurs when a clone was named after the project rather than the repo.
    Both spellings must resolve, because either is what the operator types."""
    out = RG.build_mapping([], {"toolshed-cluster": "gardenersguild/toolshed-infra"})
    assert out["toolshed-cluster"] == "gardenersguild/toolshed-infra"
    assert out["toolshed-infra"] == "gardenersguild/toolshed-infra"


def test_a_malformed_row_is_skipped_not_crashed_on():
    out = RG.build_mapping(
        [{"full_name": None, "has_issues": True},
         {"has_issues": True},
         _row("no-slash-here"),
         _row("gardenersguild/sledgehorn")], {})
    assert out == {"sledgehorn": "gardenersguild/sledgehorn",
                   "sledgehorn".capitalize().lower(): "gardenersguild/sledgehorn"}


# --------------------------------------------------------------------------- #
# read_api_repos — the losslessness floor
# --------------------------------------------------------------------------- #
def test_a_gh_failure_RAISES_rather_than_yielding_a_smaller_mapping(monkeypatch):
    """🔴 The previous generator caught every exception, carried on, and wrote a
    file holding only the local overlay — then printed `wrote …` and exited 0.
    That is the same lossy-regeneration class this change exists to fix."""
    import types
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="gh: not authenticated"))
    with pytest.raises(RuntimeError, match="gh exited 1"):
        RG.read_api_repos()


def test_a_SHORT_result_is_refused_even_though_gh_exited_zero(monkeypatch):
    """A truncated page reads as success. The floor is what makes it loud."""
    import types
    rows = "".join(json.dumps({"full_name": f"o/r{i}", "has_issues": True}) + "\n"
                   for i in range(RG.MIN_API_REPOS - 1))
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=rows, stderr=""))
    with pytest.raises(RuntimeError, match="below the floor"):
        RG.read_api_repos()
    # One more row clears it — the floor is the boundary, not a blanket refusal.
    rows += json.dumps({"full_name": "o/last", "has_issues": True}) + "\n"
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=rows, stderr=""))
    assert len(RG.read_api_repos()) == RG.MIN_API_REPOS


def test_main_exits_nonzero_and_writes_NOTHING_when_gh_fails(monkeypatch, tmp_path, capsys):
    """🔴 THE READINESS CHECK IS STUBBED, AND WITHOUT THAT LINE THIS TEST READ
    THE HOST INSTEAD OF THE CODE — measured, in the tier that gates the merge.

    `main()` asks `require_gh_ready()` before it reaches `read_api_repos`, so
    stubbing only the latter makes the outcome depend on whether the MACHINE has
    `gh`: the dev host does (readiness passes, the stub throws, exit 3 — green),
    the nix sandbox does not (readiness fails first, exit 4 — red). It passed on
    the dev host and failed in the sandbox for that reason alone, which is the
    two-tier blindness `CLAUDE.md` names: each tier's environment silently
    decides what executes, so greening one while the other is unobservable moves
    the bug rather than removing it.

    The arm under test is the AUTHENTICATED-BUT-BROKEN one — gh is present and
    logged in, and the API call still failed. That is exit 3 and it must toast.
    Pinning readiness explicitly is what makes the assertion about the code.
    """
    out = tmp_path / "known_repos.json"
    monkeypatch.setattr(RG, "require_gh_ready", lambda: None)
    monkeypatch.setattr(RG, "read_api_repos",
                        lambda: (_ for _ in ()).throw(RuntimeError("gh exited 4: no auth")))
    assert RG.main(["--path", str(out)]) == RG.EXIT_FAILED == 3
    assert not out.exists()
    assert "no auth" in capsys.readouterr().err


def test_this_suite_never_asks_the_HOST_whether_gh_is_installed(monkeypatch,
                                                                tmp_path):
    """🔴 THE GUARD ON THE GUARD ABOVE, because the defect it fixes is invisible
    on the machine anyone develops on.

    Every `main()` path now runs `require_gh_ready()`, which shells out to
    `gh auth status`. A test that neither stubs `subprocess.run` nor patches
    `require_gh_ready` therefore reads the DEVELOPER'S MACHINE, and will keep
    passing here while failing in the sandbox — a whole class, not one test.

    This asserts the class is closed by construction: with the real readiness
    check in force and `gh` made unreachable, `main()` returns NOT_CONFIGURED.
    Any future test that forgets to stub gets that 4 rather than a plausible
    wrong answer, and this test says why."""
    def no_gh(cmd, *a, **k):
        if cmd[:3] == ["gh", "auth", "status"]:
            raise FileNotFoundError("gh")
        raise AssertionError(f"nothing may run after readiness fails: {cmd}")
    monkeypatch.setattr(RG.subprocess, "run", no_gh)
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 4


# --------------------------------------------------------------------------- #
# read_local_repos — worktrees
# --------------------------------------------------------------------------- #
def test_a_linked_worktree_is_skipped(tmp_path):
    """A linked worktree's `.git` is a FILE. It shares the base clone's remote,
    so it adds no owner — and its transient name (`devrc-integ-1261`) churned
    the generated file on every run."""
    real = tmp_path / "realclone"
    (real / ".git").mkdir(parents=True)
    wt = tmp_path / "somebranch-wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/somebranch-wt\n")
    seen = []

    def fake_run(cmd, cwd=None, **kw):
        import types
        seen.append(Path(cwd).name)
        return types.SimpleNamespace(
            returncode=0, stdout="git@github.com:gardenersguild/realclone.git\n", stderr="")

    RG.subprocess.run, saved = fake_run, RG.subprocess.run
    try:
        out = RG.read_local_repos(tmp_path)
    finally:
        RG.subprocess.run = saved
    assert out == {"realclone": "gardenersguild/realclone"}
    assert seen == ["realclone"], "the worktree must not even be probed"


def test_an_absent_workspace_is_empty_not_an_error(tmp_path):
    assert RG.read_local_repos(tmp_path / "nope") == {}


def _synthetic_mapping(n: int = 30) -> str:
    return "KNOWN_REPOS = {\n" + "".join(
        f"    'plot{i}': 'gardenersguild/plot{i}',\n" for i in range(n)) + "}\n"


def test_the_sweep_falls_back_to_a_WALK_where_there_is_no_git(tmp_path):
    """🔴 THE SANDBOX TIER HAS NO `.git` — it builds from a `cp -r` store copy.
    Measured: the first version of this guard shelled out to `git ls-files`,
    passed on the dev host, and FAILED in the sandbox with `fatal: not a git
    repository`. Had it been written to skip instead of fail, a disclosure
    guard would have been silently inert in the tier that gates the merge.

    Proven on a tree with no `.git` at all: the walk finds the file, and it
    still skips the directories git would have excluded."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "anything.py").write_text(_synthetic_mapping())
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "vendored.json").write_text(_synthetic_mapping())

    how, files = candidate_files(tmp_path)
    assert how.startswith("filesystem walk"), how
    rels = sorted(str(f.relative_to(tmp_path)) for f in files)
    assert rels == ["pkg/anything.py"], rels
    assert looks_like_a_repo_mapping(files[0].read_text()) >= MAPPING_KEY_THRESHOLD


def test_the_sweep_prefers_git_ls_files_where_there_IS_a_git(tmp_path):
    """The other branch, so neither is asserted only by absence. A real init +
    commit, because `ls-files` reports nothing for an unstaged file — which is
    exactly the empty-output case the fallback must not be fooled by."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    (tmp_path / "kept.py").write_text("x = 1\n")
    (tmp_path / "untracked.py").write_text(_synthetic_mapping())
    subprocess.run(["git", "-C", str(tmp_path), "add", "kept.py"], check=True, timeout=60)
    how, files = candidate_files(tmp_path)
    assert how == "git ls-files", how
    assert [f.name for f in files] == ["kept.py"], "an UNTRACKED file is not published"


# --------------------------------------------------------------------------- #
# 🔴 The local overlay obeys the SAME filters as the API path
#
# It did not, at first: local entries were written in unconditionally, which
# put an issues-disabled repo back into the mapping (measured: 1 such key in
# the file generated that day) and restored last-write-wins between two
# checkouts sharing a bare name — the two wrong answers this function exists to
# prevent, reintroduced through the back door.
# --------------------------------------------------------------------------- #
def test_a_local_checkout_of_an_ISSUES_DISABLED_repo_is_still_dropped():
    """A checkout is authoritative about its OWNER. It is not evidence that the
    repo accepts issues, and `repo#N` builds an issues URL."""
    out = RG.build_mapping(
        [_row("gardenersguild/trowelcast", has_issues=False)],
        {"trowelcast": "gardenersguild/trowelcast"})
    assert "trowelcast" not in out, out


def test_TWO_checkouts_sharing_a_bare_name_are_dropped_not_arbitrated():
    """The same ambiguity the API path drops. `~/workspace/plot-a` and
    `~/workspace/plot-b` both cloning a repo named `plotwidget` from different
    owners must resolve to NOTHING, not to whichever `iterdir()` returned last."""
    out = RG.build_mapping([], {"plot-a": "gardenersguild/plotwidget",
                                "plot-b": "rivalorg/plotwidget"})
    assert "plotwidget" not in out
    # …and each directory name, being unambiguous on its own, still resolves.
    assert out["plot-a"] == "gardenersguild/plotwidget"
    assert out["plot-b"] == "rivalorg/plotwidget"


def test_a_local_checkout_does_not_silently_replace_an_unambiguous_api_row():
    """A checkout whose BARE name collides with a different API repo must not
    overwrite it without the collision being noticed."""
    out = RG.build_mapping(
        [_row("gardenersguild/plotwidget")],
        {"vendored-plotwidget": "rivalorg/plotwidget"})
    # Two different repos now claim the bare name `plotwidget` — ambiguous, so
    # NEITHER gets it. Silently taking the checkout's is last-write-wins across
    # sources, and it is the API row that would vanish.
    assert "plotwidget" not in out, out.get("plotwidget")
    assert out["vendored-plotwidget"] == "rivalorg/plotwidget"


def test_a_checkout_AGREEING_with_the_api_row_is_not_a_collision():
    """The other side of the boundary: a checkout of the same repo the API
    already mapped is source 3 CONFIRMING the owner, not a second claimant. A
    rule that dropped this would empty the mapping of everything checked out."""
    out = RG.build_mapping([_row("gardenersguild/plotwidget")],
                           {"plotwidget": "gardenersguild/plotwidget"})
    assert out["plotwidget"] == "gardenersguild/plotwidget"


def test_THREE_checkouts_two_of_one_repo_and_a_namesake_still_refuse():
    """Three checkouts, two of one repo plus a namesake — the shape that
    exposed a redundant second clause: whichever entry writes the spelling
    first, the next disagreeing one must drop it, and the DROP MUST BE FINAL so
    the third entry cannot write it back. (The operator really does keep
    duplicate checkouts of one repo.)"""
    out = RG.build_mapping([], {"a-copy": "gardenersguild/plotwidget",
                                "b-copy": "rivalorg/plotwidget",
                                "c-copy": "gardenersguild/plotwidget"})
    assert "plotwidget" not in out, out.get("plotwidget")


def test_a_checkout_cloned_via_a_LOWERCASE_url_is_still_matched_to_its_api_row():
    """🔴 CASE-FOLDED COMPARISON. GitHub URLs are case-insensitive, so
    `git clone .../acme/plotwidget` yields a remote spelled differently from the
    API's `acme/PlotWidget`. An exact-case comparison treated them as unrelated
    and let an ISSUES-DISABLED repo back into the mapping."""
    out = RG.build_mapping([_row("acme/PlotWidget", has_issues=False)],
                           {"plotwidget": "acme/plotwidget"})
    assert "plotwidget" not in out, out.get("plotwidget")
    # The MIRROR spelling: the API row lowercase, the checkout's remote mixed.
    # Without this case a mutant that drops the `.lower()` on the LEFT of the
    # membership test survives, because `issues_off` is lowercased on the right
    # and the fixture above happens to be lowercase on both sides.
    out = RG.build_mapping([_row("acme/plotwidget", has_issues=False)],
                           {"plotwidget": "acme/PlotWidget"})
    assert "plotwidget" not in out, out.get("plotwidget")


def test_a_case_differing_checkout_does_not_silently_displace_an_api_row():
    out = RG.build_mapping([_row("gardenersguild/widget")],
                           {"Widget": "rivalorg/Widget"})
    assert out.get("widget") != "rivalorg/Widget", out.get("widget")
    assert out.get("Widget") != "rivalorg/Widget", out.get("Widget")


def test_read_local_repos_resolves_its_workspace_at_CALL_time(tmp_path, monkeypatch):
    """🔴 THE OTHER HALF OF THE SWEEP, WHICH HAD NO GUARD AT ALL. Two mutants
    survived the whole suite here: restoring the import-bound default, and
    deleting the resolution outright — the latter makes the ONLY production
    caller (`main()`, which passes no argument) die with
    `AttributeError: 'NoneType' object has no attribute 'iterdir'`, green suite
    and all. Every existing test passed `tmp_path` explicitly, so none of them
    ever exercised the default."""
    ws = tmp_path / "elsewhere"
    (ws / "plotwidget" / ".git").mkdir(parents=True)
    monkeypatch.setattr(RG, "WORKSPACE", ws)
    monkeypatch.setattr(RG.subprocess, "run", lambda *a, **k: __import__("types").SimpleNamespace(
        returncode=0, stdout="git@github.com:gardenersguild/plotwidget.git\n", stderr=""))
    assert RG.read_local_repos() == {"plotwidget": "gardenersguild/plotwidget"}


def test_a_case_differing_checkout_of_the_SAME_repo_still_resolves():
    """🔴 PINS THE FOLD ON THE COLLISION COMPARISON — un-folding it survived the
    suite, and it breaks the very example the fold was added for: a repo cloned
    via a lowercase URL is the SAME repo as the API's mixed-case row, so it must
    confirm the owner, not read as a second claimant."""
    out = RG.build_mapping([_row("acme/PlotWidget")], {"plotwidget": "acme/plotwidget"})
    assert out.get("plotwidget") == "acme/plotwidget", out
    assert out.get("PlotWidget") == "acme/PlotWidget", out


def test_a_DROPPED_name_does_not_survive_under_a_different_casing():
    """🔴 A DROP MUST REMOVE EVERY CASING. The API pass writes both `Name` and
    `name`; a drop that popped only the spellings it happened to hold left the
    other one resolving a name the code had just called ambiguous — so
    `plotwidget#12` refused while `PlotWidget#12` opened a page."""
    out = RG.build_mapping([_row("acme/PlotWidget")], {"plotwidget": "rival/plotwidget"})
    assert not [k for k in out if k.lower() == "plotwidget"], out


def test_two_checkouts_colliding_only_by_CASE_are_still_ambiguous():
    """⚠ INVARIANT GUARD, NOT REGRESSION COVERAGE FOR THE CLAUSE IT MENTIONS —
    and this label is the finding, not a formality. It passes with EITHER
    disjunct of that `if` removed, because it pins the disjunction, so a
    maintainer deleting the `local_owners` clause again gets a green suite.

    🔴 NO TEST CAN PIN THAT CLAUSE, because on the SHIPPED code it changes no
    output. Independently checked over 135,035 cases — exhaustive mixed-case
    plus randomized fuzz — with a positive control confirming the harness could
    see a difference: zero. The structural reason is that any spelling with two
    distinct local owners is necessarily visited by both claimants, so the
    second one's `existing != full` drop fires with or without it.

    The measurement that motivated restoring the clause was taken against the
    code BEFORE the case-folded lookup existed, where it genuinely was
    load-bearing. Three harnesses have since agreed on that direction and
    disagreed on the magnitude, and none of them is committed here — so this
    docstring quotes no count, and `regen-known-repos.py` says the same.

    What this test DOES pin is the outcome: two checkouts colliding only by
    case stay ambiguous."""
    out = RG.build_mapping([], {"mirror": "rivalorg/WIDGET", "Widget": "acme/Widget"})
    assert not [k for k in out if k.lower() == "widget"], out


def test_an_ISSUES_DISABLED_checkout_does_not_make_its_NAMESAKE_unresolvable():
    """🔴 `local_owners` is built from the FILTERED checkouts, and that choice
    was unpinned: building it from the raw `local_repos` survived the suite and
    is NOT equivalent — a repo excluded for having issues disabled would still
    count as a second claimant and take the resolvable one down with it.

    The real shape: a fork with issues disabled checked out beside the repo the
    operator actually files issues against."""
    out = RG.build_mapping(
        [_row("acme/plotwidget", has_issues=False)],
        # TWO checkouts sharing the bare name — one filtered out, one not. With
        # only one, the excluded checkout never reaches the clause and the
        # mutant is equivalent: the fixture has to make the FILTERED entry a
        # potential second claimant for the choice of source to matter.
        {"plotwidget-fork": "acme/plotwidget",
         "plotwidget": "upstream/plotwidget"})
    assert out.get("plotwidget") == "upstream/plotwidget", out


def test_a_checkout_with_NO_api_row_is_written_in_BOTH_spellings():
    """🔴 The local pass's dual-spelling write was unguarded — dropping the
    lowercase half survived the suite while changing the majority of mixed-case
    inputs. `mention_scan._resolve_repo` does an EXACT dict lookup, so a
    canonical-case key alone means `plotwidget#12` silently stops resolving for
    a checkout the operator has on disk. (The identical write in the API pass
    was already guarded; this one was not.)"""
    out = RG.build_mapping([], {"PlotWidget": "acme/PlotWidget"})
    assert out.get("PlotWidget") == "acme/PlotWidget", out
    assert out.get("plotwidget") == "acme/PlotWidget", out


# --------------------------------------------------------------------------- #
# 🔴 THE PICKER UNIVERSE — WIDER THAN THE MAPPING, ON PURPOSE
#
# `build_mapping` answers "what does the bare name `foo` mean?" and must drop an
# issues-disabled repo and a bare name two owners share. `build_universe`
# answers "which repos might be worth offering?", its rows are fully-qualified,
# and nothing opens without a selection — so it applies NEITHER filter.
#
# MEASURED on the operator's host 2026-09-07: 388 repos from `gh api
# user/repos`, 339 in the mapping, so 53 were unreachable from the picker.
# --------------------------------------------------------------------------- #
def _api(*rows) -> list[dict]:
    """`{full_name, has_issues}` rows in the shape `gh api user/repos` returns."""
    return [{"full_name": f, "has_issues": h} for f, h in rows]


def test_the_universe_KEEPS_a_repo_whose_issues_are_disabled():
    """🔴 THE 48-REPO HALF, and the assumption that hid it was in a comment.

    The mapping drops these, and the ORIGINAL justification said a bare `repo#N`
    "404s for EVERY N". That is false for PULL REQUESTS: measured 2026-09-07
    against the API with a positive control (2 issues-ENABLED repos first, both
    `PULL`), **6 of 6** issues-disabled repos resolved `/issues/<pr>` to the pull
    request. An earlier probe that saw 3 of 4 return 404 was confounded — an
    unauthenticated request 404s on a PRIVATE repo whatever the redirect does.

    The mapping still drops them, on the narrower true reason (no ISSUES exist,
    so a bare `#N` naming an issue is a wrong answer). The universe must not."""
    api = _api(("acme/widget", True), ("acme/noissues", False))
    assert RG.build_universe(api, {}) == ["acme/noissues", "acme/widget"]
    # THE CONTRAST IS THE POINT — pinned here so the two functions can never
    # quietly converge on one filter policy.
    assert "acme/noissues" not in RG.build_mapping(api, {}).values()


def test_the_universe_KEEPS_BOTH_SIDES_of_an_ambiguous_bare_name():
    """🔴 THE 7-COLLISION HALF. `build_mapping` drops `bitdex` entirely, because
    resolving it would mean guessing between two owners — measured once picking
    a third party's fork over the client's repo.

    A picker row is `owner/repo`, so there is no ambiguity left to arbitrate:
    offering both and letting the operator choose is exactly the question they
    are being asked. Dropping them makes a repo unreachable to protect against
    a guess nobody is making."""
    api = _api(("alice/bitdex", True), ("bob/bitdex", True))
    assert RG.build_universe(api, {}) == ["alice/bitdex", "bob/bitdex"]
    # The mapping's silence on the shared bare name is UNCHANGED.
    assert "bitdex" not in RG.build_mapping(api, {})


def test_the_universe_unions_local_checkouts_the_API_never_returned():
    """A clone of somebody else's repository is on this disk and not in
    `user/repos`. It cannot CONTRADICT an API row — this is a list, not a
    lookup — so it is simply added."""
    api = _api(("acme/widget", True))
    out = RG.build_universe(api, {"mirror": "elsewhere/stranger"})
    assert out == ["acme/widget", "elsewhere/stranger"]


def test_the_universe_dedupes_case_insensitively_keeping_the_API_spelling():
    """GitHub repo names are case-insensitive, so these are ONE repository. The
    API row wins because it is canonical; a remote URL preserves whatever the
    person who cloned happened to type, which is why the mapping has to write
    two spellings of every key in the first place."""
    api = _api(("civitai/ComfyUI", True))
    out = RG.build_universe(api, {"mirror": "civitai/comfyui"})
    assert out == ["civitai/ComfyUI"], out


def test_the_universe_drops_rows_that_are_not_owner_slash_repo():
    """Same predicate as the mapping's, and IMPORTED from `mention_scan` rather
    than re-spelled here — a row that is not exactly `owner/repo` builds a URL
    that 404s while looking authoritative."""
    api = _api(("acme/widget/", True), ("acme//widget", True), ("noslash", True),
               ("", True), ("acme/widget", True))
    assert RG.build_universe(api, {"mirror": "also/bad/three"}) == ["acme/widget"]


def test_the_universe_is_SORTED_case_insensitively():
    """The picker shows this list in order; sorting by raw codepoint puts every
    capitalised name in a block ahead of the lowercase ones, which reads as
    random to someone scanning for a name."""
    api = _api(("acme/zebra", True), ("acme/Apple", True), ("acme/mango", True))
    assert RG.build_universe(api, {}) == ["acme/Apple", "acme/mango", "acme/zebra"]


def test_write_universe_is_0600_because_it_names_private_repositories(tmp_path):
    """Identical posture to `write_mapping`. This file holds MORE private names
    than the mapping does — it is deliberately unfiltered — so a weaker mode
    here would be a wider disclosure than the one that started all of this."""
    out = tmp_path / "sub" / "known_universe.json"
    RG.write_universe(["acme/widget"], out)
    assert json.loads(out.read_text()) == ["acme/widget"]
    assert oct(out.stat().st_mode)[-3:] == "600", oct(out.stat().st_mode)
    assert oct(out.parent.stat().st_mode)[-3:] == "700"
    assert not list(out.parent.glob("*.tmp")), "the temp file must not survive"


# --------------------------------------------------------------------------- #
# 🔴 TWO KINDS OF FAILURE — THE SPLIT THAT MAKES THE TIMER POSSIBLE
#
# `mention-open.py` once argued AGAINST a scheduled run: with a single failure
# code, a host without `gh auth` would take a failing unit and a failure toast
# on every fire, and a permanently-red timer is worse than no timer. That was
# correct. It is answered by exit 4 ("not configured here" — which the unit
# declares a success) versus exit 3 ("configured and broken" — which toasts).
# --------------------------------------------------------------------------- #
def _stub_run(monkeypatch, *, auth_rc=0, auth_raises=None, api_rc=0, api_out=""):
    """Replace `subprocess.run` INSIDE the generator only, keyed on argv."""
    def fake(cmd, *a, **k):
        if cmd[:3] == ["gh", "auth", "status"]:
            if auth_raises is not None:
                raise auth_raises
            return subprocess.CompletedProcess(cmd, auth_rc, "", "")
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, api_rc, api_out, "boom")
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr(RG.subprocess, "run", fake)


def _enough_repos() -> str:
    return "".join(
        json.dumps({"full_name": f"acme/plot{i}", "has_issues": True}) + "\n"
        for i in range(RG.MIN_API_REPOS + 5))


def test_a_host_with_no_gh_exits_NOT_CONFIGURED_not_FAILED(monkeypatch, tmp_path):
    """🔴 THE WHOLE REASON THE TIMER CAN EXIST. `gh` absent is a legitimate host
    state — the click handler degrades to `owner/repo#N` plus local checkouts —
    so it must not be the code that toasts daily forever."""
    _stub_run(monkeypatch, auth_raises=FileNotFoundError("no gh"))
    rc = RG.main(["--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json")])
    assert rc == RG.EXIT_NOT_CONFIGURED == 4, rc
    assert not (tmp_path / "m.json").exists(), "nothing may be written"
    assert not (tmp_path / "u.json").exists()


def test_gh_present_but_LOGGED_OUT_is_also_NOT_CONFIGURED(monkeypatch, tmp_path):
    """The second spelling of the same host state, and the one a real host
    reaches by having a token expire rather than by lacking the binary."""
    _stub_run(monkeypatch, auth_rc=1)
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 4


def test_an_AUTHENTICATED_host_whose_API_call_fails_is_a_REAL_failure(
        monkeypatch, tmp_path):
    """🔴 THE OTHER ARM, AND WITHOUT IT THE SPLIT IS WORTHLESS. If everything
    returned 4 the unit would be permanently green and a genuinely broken
    refresh would be silent — which is the same failure as a permanently-red
    gate, wearing the other colour."""
    _stub_run(monkeypatch, auth_rc=0, api_rc=1)
    rc = RG.main(["--path", str(tmp_path / "m.json"),
                  "--universe-path", str(tmp_path / "u.json")])
    assert rc == RG.EXIT_FAILED == 3, rc


def test_an_AUTHENTICATED_host_returning_TOO_FEW_repos_is_a_REAL_failure(
        monkeypatch, tmp_path):
    """The floor is a truncation detector: a short list is indistinguishable
    from a fine one once written. Authenticated + short is broken, not
    unconfigured — so it must toast."""
    _stub_run(monkeypatch, auth_rc=0, api_out=json.dumps(
        {"full_name": "acme/widget", "has_issues": True}) + "\n")
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 3


def test_NotConfigured_is_caught_BEFORE_the_generic_RuntimeError(monkeypatch,
                                                                 tmp_path):
    """🔴 AN ORDERING GUARD, AND THE BUG IT PINS IS INVISIBLE BY INSPECTION.
    `NotConfigured` SUBCLASSES `RuntimeError`. Swap the two `except` clauses in
    `main()` and an unconfigured host reports exit 3 — a red unit and a daily
    toast, the exact outcome the split exists to prevent — while every other
    test in this file still passes, because they never exercise the ordering.

    So this asserts the CODE, not just a message: 4, and nothing written."""
    _stub_run(monkeypatch, auth_rc=1)
    assert RG.main(["--path", str(tmp_path / "m.json"),
                    "--universe-path", str(tmp_path / "u.json")]) == 4
    assert RG.EXIT_NOT_CONFIGURED != RG.EXIT_FAILED, (
        "the two codes must stay distinct or the unit cannot tell them apart")


def test_a_HEALTHY_run_writes_BOTH_files(monkeypatch, tmp_path):
    """🔴 THE POSITIVE CONTROL FOR EVERY REFUSAL ABOVE. Five tests assert that
    nothing is written; a generator that never wrote anything would pass all
    five. This is the one that proves the happy path still lands — and that the
    universe is written at all, which a `write_mapping`-only regression would
    otherwise leave to the reader's graceful `[]` fallback and hide completely."""
    _stub_run(monkeypatch, auth_rc=0, api_out=_enough_repos())
    m, u = tmp_path / "m.json", tmp_path / "u.json"
    assert RG.main(["--path", str(m), "--universe-path", str(u)]) == 0
    assert json.loads(m.read_text()), "the mapping must be written"
    universe = json.loads(u.read_text())
    assert isinstance(universe, list) and len(universe) == RG.MIN_API_REPOS + 5
    assert oct(u.stat().st_mode)[-3:] == "600"


def test_print_mode_writes_NOTHING_and_reports_the_universe(monkeypatch,
                                                            tmp_path, capsys):
    """`--print` must stay a read. It also reports how many repos are reachable
    ONLY through the picker — the number this whole change is about, and one
    nobody would check if it were never printed."""
    _stub_run(monkeypatch, auth_rc=0, api_out=_enough_repos())
    m, u = tmp_path / "m.json", tmp_path / "u.json"
    assert RG.main(["--print", "--path", str(m), "--universe-path", str(u)]) == 0
    assert not m.exists() and not u.exists()
    assert "picker universe" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 🔴 THE FILE-WIDE SPAWN PIN — required by this script's entry in
# `test_no_real_launchers.py::ACKNOWLEDGED_UNSTUBBED` under `home-manager`.
#
# This file names `home-manager` in ONE comment (why the picker universe is a
# second FILE rather than a new shape inside `known_repos.json`).
# `launcher_scan.hazard_hits` is a TEXTUAL scan, so that mention registers the
# script as "reaching home-manager" and had to be acknowledged.
#
# 🔴 AN ACKNOWLEDGEMENT BLINDS THE GUARD IT IS FILED UNDER — MEASURED on this
# very table, where `tmux-reply-agent`'s entry rested on a grep and an injected
# real call site left the whole suite green. So the entry gets a pin, and the
# pin is this.
# --------------------------------------------------------------------------- #
_SPAWN_FUNCS = {"run", "Popen", "call", "check_output", "check_call", "system",
                "execv", "execvp", "execve", "spawnv", "spawnvp"}

# gh  — `gh auth status` (readiness) and `gh api user/repos` (the rows)
# git — `git remote get-url origin`, per local checkout
EXPECTED_ARGV0 = {"gh", "git"}


def _spawn_argv0_literals(path: Path) -> set[str]:
    """Every literal argv[0] in a spawn-shaped call, from the SYNTAX TREE.

    `<computed>` rather than a skip for a non-literal argv[0]: a command built
    from a variable is how a literal-keyed ledger gets walked past, so it must
    fail loudly instead of leaving the set."""
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name not in _SPAWN_FUNCS:
            continue
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            head = first.elts[0]
            found.add(head.value if isinstance(head, ast.Constant)
                      else "<computed>")
        else:
            found.add("<not-a-list>")
    return found


def test_regen_SPAWNS_these_argv0_AND_NOTHING_ELSE():
    """GROWS-OR-SHRINKS. A new binary is the hazard the acknowledgement would
    otherwise hide; a vanished one means the justification has stopped
    describing the file."""
    assert _spawn_argv0_literals(GENERATOR) == EXPECTED_ARGV0


def test_home_manager_is_MENTIONED_but_never_SPAWNED():
    """Both halves of the acknowledgement's claim, asserted rather than left to
    a reader of prose: the mention must still EXIST (or the table entry has
    outlived the sentence it describes), and it must remain a mention."""
    text = GENERATOR.read_text()
    assert re.search(r"(?<![\w-])home-manager(?![\w-])", text), (
        "the ACKNOWLEDGED_UNSTUBBED entry for this file exists BECAUSE it names "
        "home-manager; if that is gone, remove the acknowledgement too")
    assert "home-manager" not in _spawn_argv0_literals(GENERATOR), (
        "regen-known-repos.py now SPAWNS home-manager — the acknowledgement "
        "covering it is an unreachability claim and is now FALSE")
