"""Tests for `scripts/regen-known-repos.py` — the mention-mapping generator.

🔴 THE FILE THIS GENERATES MUST NEVER BE TRACKED. `gh api user/repos` returns
private repos, and an earlier version of this generator wrote its output into
`scripts/collector/known_repos.py`, which was committed to this PUBLIC repo:
232 private repositories, 217 of them named nowhere else in the tree. The first
test below is the guard against that ever recurring; the rest pin the filters
that keep a wrong answer out of the mapping.
"""
from __future__ import annotations

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
    assert offenders == [], (
        f"file(s) look like a repo mapping (via {how}): {offenders}. "
        f"The mapping names PRIVATE repositories and this repo is PUBLIC — it "
        f"belongs at {RG.DEFAULT_PATH}, outside every checkout.")


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
    """`~/workspace/homelab-talos` whose remote is `owner/homelab-infra` must
    resolve under the directory name AND the real repo name."""
    out = RG.build_mapping([], {"homelab-talos": "gardenersguild/homelab-infra"})
    assert out["homelab-talos"] == "gardenersguild/homelab-infra"
    assert out["homelab-infra"] == "gardenersguild/homelab-infra"


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
    out = tmp_path / "known_repos.json"
    monkeypatch.setattr(RG, "read_api_repos",
                        lambda: (_ for _ in ()).throw(RuntimeError("gh exited 4: no auth")))
    assert RG.main(["--path", str(out)]) == 3
    assert not out.exists()
    assert "no auth" in capsys.readouterr().err


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
