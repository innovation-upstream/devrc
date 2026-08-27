"""`scripts/worktree-prune` — the worktree classifier, and the controls that make
its verdicts worth reading.

WHAT IS UNDER TEST
------------------
A tool that will eventually be pointed at ~750 real worktrees and asked which of
them can be deleted. The prior attempt at that classification reported 245
"orphans" using a containment checker that called a `git diff
--pathspec-from-file` which does not exist, exited 129, and therefore scored
EVERY branch unmerged. So the interesting question is not "does the tool run" —
it ran — but "can its verdicts distinguish the cases that matter".

🔴 THE HEADLINE TRAP, AND THE FIXTURE THAT PINS IT
---------------------------------------------------
A SQUASH merge never makes the branch head an ancestor of the base.
`git merge-base --is-ancestor` is FALSE for every squash-merged branch, forever.
An ancestry-only classifier therefore reports merged work as orphaned — the
exact failure that gets real work deleted.

`test_squash_merged_branch_is_dead_not_orphan` builds a REAL squash merge with
real git and asserts `dead`. And
`test_an_ancestry_only_classifier_calls_the_squash_an_orphan` is its MUTATION
CONTROL: it swaps in the broken, ancestry-only checker and watches the SAME
fixture flip to `orphan`. Without that control, `dead` could be produced by any
signal at all — including one that says `dead` unconditionally. With it, the
fixture is proven to be a genuine squash (not an ancestor in disguise) AND the
content check is proven to be the thing carrying the verdict.

THE TWO CONTROLS THE BRIEF ASKS FOR, NAMED
-------------------------------------------
  POSITIVE control (it MUST say dead): `test_squash_merged_branch_is_dead_not_orphan`
      and `test_true_merge_is_dead_by_ancestry`.
  NEGATIVE control (it MUST say orphan): `test_genuinely_unmerged_branch_is_an_orphan`.
  Neither is a general claim on its own — a classifier hardwired to one verdict
  passes exactly one of them. Both together, plus the mutation control, are what
  make the pair mean something.

🔴 AND THE LOUD-UNKNOWN CONTROL. `test_without_gh_the_orphan_becomes_cannot_tell`
feeds the SAME unmerged fixture with the PR lookup removed and requires the
verdict to degrade to `cannot-tell` rather than stay `orphan`. "No PR found" and
"we could not ask" are the same observable — an empty result — and they license
opposite verdicts.

🔴 AND THE PATH-EXCLUSION SECTION, WHICH EXISTS BECAUSE OF A MEASUREMENT
------------------------------------------------------------------------
One scan of this machine, dated, with the exact scope stated — see the section
comment above `two_dead` for the numbers and the command. Most of the `dead`
rows it found were trees under a `.claude/worktrees/` directory belonging to
OTHER LIVE Claude sessions: `dead` is a correct verdict about the BRANCH and a
catastrophic instruction about the DIRECTORY. Those rows are therefore spared BY
DEFAULT (`--include-agent-worktrees` opts back in), and `--exclude-path` spares
further rows without hiding them. The tests carry the same shape as the ones
above — a positive control that BOTH fixtures are removable unfiltered, a
both-halves test (the excluded one survives on disk AND the other is really
gone), and a mutation control that neuters `path_excluded` and watches the
excluded row become removable again.

HERMETIC BY CONSTRUCTION
------------------------
Every repository is created under `tmp_path` with real git. The only remote is a
NAME with a github.com URL that is never contacted (it exists so `gh --repo`
gets a slug), and every `gh` invocation goes to a stub script in the test's own
`tmp_path`. No network, no real gh, no `.git` read out of the source tree except
the tool FILE itself.
"""
from __future__ import annotations

import ast
import fnmatch
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import hermetic_git, mockbin  # noqa: E402

TOOL = REPO_ROOT / "scripts" / "worktree-prune"


def _load():
    loader = importlib.machinery.SourceFileLoader("_worktree_prune", str(TOOL))
    spec = importlib.util.spec_from_loader("_worktree_prune", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


wp = _load()


# ── hermetic environment ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """The tool shells out to git with the AMBIENT environment, so the pins have
    to live in `os.environ` for the duration of each test rather than in a dict
    passed to one helper."""
    for k, v in hermetic_git.HERMETIC.items():
        monkeypatch.setenv(k, v)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, check=False, timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {repo} failed rc={r.returncode}\n"
                             f"stdout={r.stdout}\nstderr={r.stderr}")
    return r.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def publish(repo: Path) -> None:
    """Point the remote-tracking refs at the current `main`.

    There is no real remote — `refs/remotes/origin/main` is written directly, so
    the tool resolves the default branch through the `origin-head-symref` path
    it will use in production without any network.
    """
    sha = git(repo, "rev-parse", "main")
    git(repo, "update-ref", "refs/remotes/origin/main", sha)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def new_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo / "README.md", "base\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "base")
    git(repo, "remote", "add", "origin", "https://github.com/fixture/repo.git")
    publish(repo)
    return repo


def commit_on_branch(repo: Path, branch: str, rel: str, text: str, msg: str) -> None:
    git(repo, "checkout", "-q", "-b", branch)
    write(repo / rel, text)
    git(repo, "add", rel)
    git(repo, "commit", "-qm", msg)
    git(repo, "checkout", "-q", "main")


def squash_merge(repo: Path, branch: str, rel: str, msg: str) -> None:
    """A REAL squash: the branch's file content lands on main as ONE new commit
    with different parents, so the branch head is not an ancestor of main."""
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", branch, "--", rel)
    git(repo, "add", rel)
    git(repo, "commit", "-qm", msg)
    publish(repo)


def add_worktree(repo: Path, path: Path, *args: str) -> Path:
    git(repo, "worktree", "add", "-q", str(path), *args)
    return path


# ── gh stubs ──────────────────────────────────────────────────────────────────

def gh_stub(tmp_path: Path, bulk: list, name: str = "gh") -> str:
    """A `gh` that answers `pr list` from a JSON file and nothing else."""
    payload = tmp_path / f"{name}-bulk.json"
    payload.write_text(json.dumps(bulk), encoding="utf-8")
    head = tmp_path / f"{name}-head.json"
    head.write_text("[]", encoding="utf-8")
    stub = tmp_path / name
    mockbin.write_exec(stub, f"""
prev=""
head_query=0
for a in "$@"; do
  if [ "$prev" = "--head" ]; then head_query=1; fi
  prev="$a"
done
if [ "$head_query" = "1" ]; then
  cat "{head}"
else
  cat "{payload}"
fi
exit 0
""")
    return str(stub)


def gh_broken(tmp_path: Path, name: str = "gh-broken") -> str:
    stub = tmp_path / name
    mockbin.write_exec(stub, """
echo "gh: To get started with GitHub CLI, please run: gh auth login" >&2
exit 4
""")
    return str(stub)


def scan(repo: Path, gh_cmd: str = "gh", use_gh: bool = True, pr_limit: int = 2000) -> dict:
    rows = wp.scan_repo(repo, gh_cmd, use_gh, pr_limit, jobs=1)
    return {r["path"]: r for r in rows}


def row_for(repo: Path, path: Path, **kw) -> dict:
    return scan(repo, **kw)[str(path)]


# ── the four required fixtures, one repo ──────────────────────────────────────

@pytest.fixture()
def universe(tmp_path: Path):
    """One repo carrying every scenario the brief names, plus the awkward ones.

    Returns (repo, {label: worktree_path}, gh_cmd).
    """
    repo = new_repo(tmp_path)
    wts = tmp_path / "wts"
    wts.mkdir()

    # (1) squash-merged — the headline trap. Two commits on the branch, ONE
    #     squash commit on main, so ancestry is false and content is identical.
    git(repo, "checkout", "-q", "-b", "feat/squashed")
    write(repo / "squashed.txt", "first\n")
    git(repo, "add", "squashed.txt")
    git(repo, "commit", "-qm", "squashed: part 1")
    write(repo / "squashed.txt", "first\nsecond\n")
    git(repo, "add", "squashed.txt")
    git(repo, "commit", "-qm", "squashed: part 2")
    git(repo, "checkout", "-q", "main")
    squash_merge(repo, "feat/squashed", "squashed.txt", "squash the branch (#42)")

    # (2) a genuinely unmerged branch with unique commits.
    commit_on_branch(repo, "feat/unmerged", "unmerged.txt", "unique work\n", "unmerged work")

    # (3) merged-and-clean, by a real merge commit (ancestry holds).
    commit_on_branch(repo, "feat/merged", "merged.txt", "merged\n", "mergeable work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/merged", "feat/merged")
    publish(repo)

    # (4) dirty — its content IS merged, so only the dirt keeps it alive.
    commit_on_branch(repo, "feat/dirty", "dirty.txt", "dirty branch\n", "dirty branch work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/dirty", "feat/dirty")
    publish(repo)

    paths = {
        "squashed": add_worktree(repo, wts / "squashed", "feat/squashed"),
        "unmerged": add_worktree(repo, wts / "unmerged", "feat/unmerged"),
        "merged": add_worktree(repo, wts / "merged", "feat/merged"),
        "dirty": add_worktree(repo, wts / "dirty", "feat/dirty"),
    }
    write(paths["dirty"] / "SCRATCH.md", "notes nobody committed\n")

    return repo, paths, gh_stub(tmp_path, [])


# ── POSITIVE CONTROL: the squash must be dead ─────────────────────────────────

def test_squash_merged_branch_is_dead_not_orphan(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["squashed"], gh_cmd=gh)
    assert r["verdict"] == "dead", r["verdict_reason"]


def test_the_squash_fixture_really_is_a_squash_and_not_an_ancestor(universe):
    """If ancestry held, the headline test above would be vacuous."""
    repo, paths, gh = universe
    head = git(repo, "rev-parse", "feat/squashed")
    rc = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                         head, "refs/remotes/origin/main"], check=False).returncode
    assert rc == 1, "the fixture is not a squash — the branch IS an ancestor of main"


def test_the_squash_verdict_rests_on_content_not_ancestry(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["squashed"], gh_cmd=gh)
    assert r["landed_signals"] == ["content-identical"], r["landed_signals"]
    assert "ancestor" in r["checks_run"], r["checks_run"]


def test_the_squash_row_is_removable(universe):
    repo, paths, gh = universe
    assert row_for(repo, paths["squashed"], gh_cmd=gh)["removable"] is True


def test_true_merge_is_dead_by_ancestry(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["merged"], gh_cmd=gh)
    assert r["verdict"] == "dead"
    assert "ancestor" in r["landed_signals"], r["landed_signals"]


# ── NEGATIVE CONTROL: the unmerged branch must be an orphan ───────────────────

def test_genuinely_unmerged_branch_is_an_orphan(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], gh_cmd=gh)
    assert r["verdict"] == "orphan", r["verdict_reason"]


def test_the_orphan_is_never_removable(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], gh_cmd=gh)
    assert r["removable"] is False
    assert r["blockers"] == []  # orphan is blocked by its VERDICT, not by a flag


def test_the_orphan_has_no_landing_signal(universe):
    repo, paths, gh = universe
    assert row_for(repo, paths["unmerged"], gh_cmd=gh)["landed_signals"] == []


def test_the_orphan_actually_carries_unique_commits(universe):
    """A branch with no unique commits would be an ancestor, making the negative
    control vacuous for the opposite reason to the positive one."""
    repo, paths, gh = universe
    ahead = git(repo, "rev-list", "--count", "refs/remotes/origin/main..feat/unmerged")
    assert int(ahead) >= 1


# ── the dirty worktree ────────────────────────────────────────────────────────

def test_dirty_worktree_is_live(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["dirty"], gh_cmd=gh)
    assert r["verdict"] == "live"
    assert r["dirty"] is True


def test_dirty_worktree_is_not_removable_even_though_its_content_landed(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["dirty"], gh_cmd=gh)
    assert r["removable"] is False
    assert any("uncommitted" in b or "untracked" in b for b in r["blockers"]), r["blockers"]


def test_an_untracked_file_alone_counts_as_dirty(tmp_path):
    """The case a tracked-only status check would silently discard."""
    repo = new_repo(tmp_path)
    commit_on_branch(repo, "feat/x", "x.txt", "x\n", "x")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat/x")
    publish(repo)
    wt = add_worktree(repo, tmp_path / "wt", "feat/x")
    clean = row_for(repo, wt, gh_cmd=gh_stub(tmp_path, []))
    assert clean["verdict"] == "dead"
    write(wt / "untracked.md", "notes\n")
    after = row_for(repo, wt, gh_cmd=gh_stub(tmp_path, []))
    assert after["dirty"] is True
    assert after["verdict"] == "live"


def test_the_main_worktree_is_always_live_and_never_removable(universe):
    repo, paths, gh = universe
    r = scan(repo, gh_cmd=gh)[str(repo)]
    assert r["is_main"] is True
    assert r["verdict"] == "live"
    assert r["removable"] is False


# ── 🔴 THE MUTATION CONTROL ───────────────────────────────────────────────────

def test_an_ancestry_only_classifier_calls_the_squash_an_orphan(universe, monkeypatch):
    """Watch the SAME fixture go wrong under the broken classifier.

    This is the red half of the headline test. It replaces `landing_signals`
    with the ancestry-only version — the shape of the checker that produced the
    245-orphan figure — and requires the squash-merged worktree to be
    misclassified as `orphan`. If this test ever passes with the real
    classifier in place, the `dead` verdict above is not being carried by the
    content check.
    """
    repo, paths, gh = universe

    def ancestry_only(repo_path, head, default_ref):
        rc = subprocess.run(["git", "-C", str(repo_path), "merge-base", "--is-ancestor",
                             head, default_ref], check=False).returncode
        return {"fired": ["ancestor"] if rc == 0 else [],
                "checked": ["ancestor"], "notes": ["ancestry only"]}

    monkeypatch.setattr(wp, "landing_signals", ancestry_only)
    r = row_for(repo, paths["squashed"], gh_cmd=gh)
    assert r["verdict"] == "orphan", (
        "the ancestry-only mutant did NOT misclassify the squash — the fixture "
        "is not exercising the squash path, so the headline test proves nothing")


def test_a_classifier_that_ignores_dirtiness_would_call_the_dirty_tree_dead(universe, monkeypatch):
    """The second mutation: drop the dirty check and watch the dirty worktree —
    whose content DID land — become removable. That is the shape of the bug this
    tool exists to not have."""
    repo, paths, gh = universe
    real = wp.worktree_dirty
    monkeypatch.setattr(wp, "worktree_dirty", lambda p: (False, 0, []))
    r = row_for(repo, paths["dirty"], gh_cmd=gh)
    assert r["verdict"] == "dead" and r["removable"] is True, (
        "the dirty fixture is not otherwise-dead, so the real test's `live` "
        "verdict could be coming from something other than the dirty check")
    monkeypatch.setattr(wp, "worktree_dirty", real)


# ── the loud unknown ──────────────────────────────────────────────────────────

def test_without_gh_the_orphan_becomes_cannot_tell(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], use_gh=False)
    assert r["verdict"] == "cannot-tell", r["verdict_reason"]
    assert "UNKNOWN" in r["verdict_reason"]


def test_without_gh_the_squash_is_still_dead(universe):
    """The content signal is git-only, so removing gh must not cost us the
    verdict that matters — only the orphan/cannot-tell distinction."""
    repo, paths, gh = universe
    assert row_for(repo, paths["squashed"], use_gh=False)["verdict"] == "dead"


def test_a_broken_gh_yields_cannot_tell_not_orphan(universe, tmp_path):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], gh_cmd=gh_broken(tmp_path))
    assert r["verdict"] == "cannot-tell"
    assert r["pr"]["answered"] is False


def test_a_missing_gh_binary_yields_cannot_tell_not_orphan(universe, tmp_path):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], gh_cmd=str(tmp_path / "no-such-gh"))
    assert r["verdict"] == "cannot-tell"


def test_cannot_tell_is_never_removable(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], use_gh=False)
    assert r["removable"] is False


def test_a_detached_worktree_with_unmerged_work_is_cannot_tell(universe, tmp_path):
    repo, paths, gh = universe
    sha = git(repo, "rev-parse", "feat/unmerged")
    wt = add_worktree(repo, tmp_path / "detached", "--detach", sha)
    r = row_for(repo, wt, gh_cmd=gh)
    assert r["detached"] is True
    assert r["branch"] is None
    assert r["verdict"] == "cannot-tell"
    assert "detached" in r["pr"]["why"]


def test_a_detached_worktree_whose_content_landed_is_still_dead(universe, tmp_path):
    """`cannot-tell` for a detached HEAD is about the PR lookup, not about
    containment — the git signals work without a branch name."""
    repo, paths, gh = universe
    sha = git(repo, "rev-parse", "feat/squashed")
    wt = add_worktree(repo, tmp_path / "detached-landed", "--detach", sha)
    assert row_for(repo, wt, gh_cmd=gh)["verdict"] == "dead"


def test_an_unresolvable_default_branch_is_cannot_tell(tmp_path):
    repo = tmp_path / "odd"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "wip-only")
    write(repo / "a.txt", "a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "a")
    git(repo, "checkout", "-q", "-b", "side")
    write(repo / "b.txt", "b\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-qm", "b")
    git(repo, "checkout", "-q", "wip-only")
    wt = add_worktree(repo, tmp_path / "wt-odd", "side")
    r = row_for(repo, wt, use_gh=False)
    assert r["default_ref"] is None
    assert r["default_how"] == "unresolved"
    assert r["verdict"] == "cannot-tell"


def test_a_prunable_worktree_is_cannot_tell_not_dead(universe, tmp_path):
    """The directory is gone. `git worktree prune` is the answer, not `remove`,
    and guessing `dead` here is how a tool starts removing things it cannot see."""
    repo, paths, gh = universe
    wt = add_worktree(repo, tmp_path / "vanishing", "-b", "feat/vanishing")
    for p in sorted(wt.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    wt.rmdir()
    r = row_for(repo, wt, gh_cmd=gh)
    assert r["verdict"] == "cannot-tell"
    assert r["removable"] is False


def test_a_locked_worktree_is_live(universe, tmp_path):
    repo, paths, gh = universe
    git(repo, "worktree", "lock", "--reason", "kept on purpose", str(paths["merged"]))
    try:
        r = row_for(repo, paths["merged"], gh_cmd=gh)
        assert r["locked"] is True
        assert r["verdict"] == "live"
        assert r["removable"] is False
    finally:
        git(repo, "worktree", "unlock", str(paths["merged"]))


# ── pull-request signals ──────────────────────────────────────────────────────

def test_an_open_pr_keeps_an_unmerged_branch_live(universe, tmp_path):
    repo, paths, _ = universe
    gh = gh_stub(tmp_path, [{"number": 7, "state": "OPEN", "headRefName": "feat/unmerged",
                             "mergedAt": None, "url": "u"}], name="gh-open")
    r = row_for(repo, paths["unmerged"], gh_cmd=gh)
    assert r["verdict"] == "live"
    assert "#7" in r["verdict_reason"]


def test_a_closed_unmerged_pr_leaves_the_branch_an_orphan(universe, tmp_path):
    repo, paths, _ = universe
    gh = gh_stub(tmp_path, [{"number": 8, "state": "CLOSED", "headRefName": "feat/unmerged",
                             "mergedAt": None, "url": "u"}], name="gh-closed")
    r = row_for(repo, paths["unmerged"], gh_cmd=gh)
    assert r["verdict"] == "orphan"
    assert "#8" in r["verdict_reason"]


def test_a_merged_pr_rescues_a_branch_no_git_signal_can_see(tmp_path):
    """The known false negative of `content-identical`, stated in the tool's own
    docstring: a squash that the default branch has since built on top of. Only
    the PR answer can see it, so this is the row that proves gh is load-bearing
    and not decoration."""
    repo = new_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feat/overwritten")
    write(repo / "f.txt", "branch version\n")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-qm", "branch work part 1")
    # 🔴 TWO commits, deliberately. A ONE-commit branch squashes to a commit
    # with the SAME patch-id, so `git cherry` sees it and the tool is not blind
    # after all — measured: this test passed for the wrong reason until the
    # second commit was added.
    write(repo / "f.txt", "branch version\nmore branch work\n")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-qm", "branch work part 2")
    git(repo, "checkout", "-q", "main")
    squash_merge(repo, "feat/overwritten", "f.txt", "squash (#9)")
    write(repo / "f.txt", "and then main changed it again\n")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-qm", "later work on the same path")
    publish(repo)
    wt = add_worktree(repo, tmp_path / "wt", "feat/overwritten")

    blind = row_for(repo, wt, gh_cmd=gh_stub(tmp_path, [], name="gh-empty"))
    assert blind["verdict"] == "orphan", (
        "the git signals were supposed to be blind here; if they see it, this "
        "test is not exercising the false negative it claims to")

    gh = gh_stub(tmp_path, [{"number": 9, "state": "MERGED", "headRefName": "feat/overwritten",
                             "mergedAt": "2026-01-01T00:00:00Z", "url": "u"}], name="gh-merged")
    seeing = row_for(repo, wt, gh_cmd=gh)
    assert seeing["verdict"] == "dead"
    assert "pr-merged" in seeing["landed_signals"]


def test_a_cherry_picked_branch_is_dead_by_patch_equivalence_alone(tmp_path):
    """The third signal, exercised where it is the ONLY one that can fire.

    🔴 This test exists because a mutation sweep deleted the `patch-equivalent`
    signal and the whole suite stayed GREEN — every other landing case was
    already covered by `ancestor` or `content-identical`, so the signal was
    decorative as far as the tests could see. The fixture cherry-picks the
    branch's commit onto main and then changes the same path AGAIN, which puts
    `content-identical` out of reach and leaves patch-equivalence carrying the
    verdict on its own.
    """
    repo = new_repo(tmp_path)
    write(repo / "p.txt", "orig\n")
    git(repo, "add", "p.txt")
    git(repo, "commit", "-qm", "add p")
    publish(repo)

    git(repo, "checkout", "-q", "-b", "feat/picked")
    write(repo / "p.txt", "orig\npicked\n")
    git(repo, "add", "p.txt")
    git(repo, "commit", "-qm", "the work")
    picked = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-q", "main")
    # 🔴 Main must DIVERGE before the pick. Cherry-picking straight onto the
    # branch's own parent reproduces the identical commit — same tree, same
    # parent, same identity, same second — so git hands back the SAME sha and
    # the branch becomes a genuine ancestor. Measured: this fixture asserted
    # `patch-equivalent` and got `ancestor` until this commit was added.
    write(repo / "unrelated.txt", "main did something else first\n")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-qm", "unrelated main work")
    git(repo, "cherry-pick", picked)
    write(repo / "p.txt", "orig\npicked\nlater\n")
    git(repo, "add", "p.txt")
    git(repo, "commit", "-qm", "main moves on over the same path")
    publish(repo)

    wt = add_worktree(repo, tmp_path / "wt", "feat/picked")
    r = row_for(repo, wt, use_gh=False)
    assert r["landed_signals"] == ["patch-equivalent"], r["evidence"]
    assert r["verdict"] == "dead"
    assert r["removable"] is True


def test_a_truncated_pr_index_falls_back_to_a_per_branch_query(universe, tmp_path):
    """An absence in a TRUNCATED list proves nothing. With `--pr-limit 1` the
    bulk index is at its limit, so the tool must ask directly rather than read
    the gap as 'no PR'."""
    repo, paths, _ = universe
    gh = gh_stub(tmp_path, [{"number": 1, "state": "MERGED", "headRefName": "unrelated",
                             "mergedAt": "2026-01-01T00:00:00Z", "url": "u"}], name="gh-trunc")
    r = row_for(repo, paths["unmerged"], gh_cmd=gh, pr_limit=1)
    assert r["pr"]["answered"] is True
    assert "direct query" in r["pr"]["why"]
    assert r["verdict"] == "orphan"


def test_a_complete_index_says_so_in_the_evidence(universe):
    repo, paths, gh = universe
    r = row_for(repo, paths["unmerged"], gh_cmd=gh)
    assert "COMPLETE index" in r["pr"]["why"]


def test_a_merged_state_outranks_a_stale_open_record_for_the_same_branch(universe, tmp_path):
    repo, paths, _ = universe
    gh = gh_stub(tmp_path, [
        {"number": 3, "state": "OPEN", "headRefName": "feat/unmerged", "mergedAt": None, "url": "u"},
        {"number": 4, "state": "MERGED", "headRefName": "feat/unmerged",
         "mergedAt": "2026-01-01T00:00:00Z", "url": "u"},
    ], name="gh-rank")
    r = row_for(repo, paths["unmerged"], gh_cmd=gh)
    assert r["verdict"] == "dead"
    assert r["pr"]["number"] == 4


# ── the empty-pathspec hazard ─────────────────────────────────────────────────

def test_paths_differ_refuses_an_empty_pathspec(tmp_path):
    """A pathspec-less `git diff` compares the WHOLE trees. That is the mirror
    image of the `--pathspec-from-file` bug: it answers a question nobody asked,
    reassuringly."""
    repo = new_repo(tmp_path)
    with pytest.raises(AssertionError, match="EMPTY pathspec"):
        wp._paths_differ(repo, "main", "refs/remotes/origin/main", [])


def test_a_branch_with_no_unique_content_is_landed_by_the_short_circuit(tmp_path):
    """A branch whose commits net out to no change must take the
    `no-content-change` path, NOT a whole-tree diff."""
    repo = new_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feat/noop")
    write(repo / "tmp.txt", "temporary\n")
    git(repo, "add", "tmp.txt")
    git(repo, "commit", "-qm", "add")
    (repo / "tmp.txt").unlink()
    git(repo, "add", "-A", "tmp.txt")
    git(repo, "commit", "-qm", "and remove it again")
    git(repo, "checkout", "-q", "main")
    write(repo / "elsewhere.txt", "main moved on\n")
    git(repo, "add", "elsewhere.txt")
    git(repo, "commit", "-qm", "main moves")
    publish(repo)
    wt = add_worktree(repo, tmp_path / "wt", "feat/noop")
    r = row_for(repo, wt, use_gh=False)
    assert r["landed_signals"] == ["no-content-change"], r["evidence"]
    assert r["verdict"] == "dead"


def test_pathspec_batching_survives_more_paths_than_one_argv_can_hold(tmp_path, monkeypatch):
    """`PATHSPEC_BATCH_BYTES` exists because a truncated argv would show up as
    'no differing paths' — a confident, wrong `dead`. Shrinking the batch size
    forces many batches over a real repo and the answer must not move."""
    repo = new_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feat/many")
    for i in range(40):
        write(repo / f"dir{i}" / f"file-with-a-long-name-{i}.txt", f"content {i}\n")
    git(repo, "add", "-A", ".")
    git(repo, "commit", "-qm", "many files")
    git(repo, "checkout", "-q", "main")
    base = git(repo, "rev-parse", "main")
    changed = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", base, "feat/many"],
                             capture_output=True, text=True, check=True).stdout.split()
    assert len(changed) == 40
    monkeypatch.setattr(wp, "PATHSPEC_BATCH_BYTES", 30)
    assert wp._paths_differ(repo, "feat/many", "main", changed) is True
    assert wp._paths_differ(repo, "feat/many", "feat/many", changed) is False


# ── porcelain parsing ─────────────────────────────────────────────────────────

PORCELAIN = """worktree /home/z/repo
HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
branch refs/heads/main

worktree /home/z/wt-detached
HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
detached

worktree /home/z/wt-locked
HEAD cccccccccccccccccccccccccccccccccccccccc
branch refs/heads/feat/keep
locked kept on purpose

worktree /home/z/wt-gone
HEAD dddddddddddddddddddddddddddddddddddddddd
branch refs/heads/feat/gone
prunable gitdir file points to non-existent location
"""


def test_porcelain_parser_reads_every_attribute():
    rows = wp.parse_worktree_porcelain(PORCELAIN)
    assert [r["path"] for r in rows] == [
        "/home/z/repo", "/home/z/wt-detached", "/home/z/wt-locked", "/home/z/wt-gone"]
    assert rows[0]["is_main"] is True and rows[0]["branch"] == "main"
    assert rows[1]["detached"] is True and rows[1]["branch"] is None
    assert rows[2]["locked"] is True and rows[2]["lock_reason"] == "kept on purpose"
    assert rows[3]["prunable"] is True
    assert rows[3]["prune_reason"] == "gitdir file points to non-existent location"
    assert [r["is_main"] for r in rows[1:]] == [False, False, False]


def test_porcelain_parser_handles_a_missing_trailing_blank_line():
    rows = wp.parse_worktree_porcelain("worktree /a\nHEAD " + "e" * 40 + "\nbranch refs/heads/x")
    assert len(rows) == 1 and rows[0]["branch"] == "x"


def test_porcelain_parser_marks_only_the_first_block_as_main():
    rows = wp.parse_worktree_porcelain(PORCELAIN)
    assert sum(1 for r in rows if r["is_main"]) == 1


# ── classify() as a pure function ─────────────────────────────────────────────

def base_row(**kw) -> dict:
    # 🔴 `submodules` is spelled out rather than left to default. `classify`
    # treats a MISSING key as UNKNOWN and blocks removal on it (#931) — an
    # unanswered question about a refusal must not be spent as permission — so
    # every caller has to answer it, and this fixture answers "no submodules".
    row = {"is_main": False, "bare": False, "prunable": False, "path_exists": True,
           "locked": False, "dirty": False, "dirty_count": 0, "submodules": False,
           "default_ref": "refs/remotes/origin/main", "landed_signals": [],
           "signal_error": False, "evidence": [],
           "pr": {"answered": True, "state": None, "number": None, "merged_at": None,
                  "why": "complete index"}}
    row.update(kw)
    return row


@pytest.mark.parametrize("kw,expected", [
    ({"is_main": True}, "live"),
    ({"bare": True}, "live"),
    ({"prunable": True}, "cannot-tell"),
    ({"path_exists": False}, "cannot-tell"),
    ({"locked": True}, "live"),
    ({"dirty": None}, "cannot-tell"),
    ({"dirty": True, "dirty_count": 3}, "live"),
    ({"default_ref": None}, "cannot-tell"),
    ({"landed_signals": ["ancestor"]}, "dead"),
    ({"landed_signals": ["content-identical"]}, "dead"),
    ({"landed_signals": ["patch-equivalent"]}, "dead"),
    ({"landed_signals": ["no-content-change"]}, "dead"),
    ({"signal_error": True}, "cannot-tell"),
    ({}, "orphan"),
])
def test_classify_covers_every_branch(kw, expected):
    assert wp.classify(base_row(**kw))["verdict"] == expected


@pytest.mark.parametrize("state,expected", [
    ("OPEN", "live"), ("MERGED", "dead"), ("CLOSED", "orphan"), (None, "orphan"),
])
def test_classify_pull_request_states(state, expected):
    row = base_row(pr={"answered": True, "state": state, "number": 5,
                       "merged_at": None, "why": "complete index"})
    assert wp.classify(row)["verdict"] == expected


def test_classify_never_returns_a_verdict_outside_the_vocabulary():
    for kw in ({}, {"is_main": True}, {"dirty": True}, {"prunable": True},
               {"landed_signals": ["ancestor"]}, {"pr": {"answered": False, "state": None,
                                                         "number": None, "merged_at": None,
                                                         "why": "x"}}):
        assert wp.classify(base_row(**kw))["verdict"] in wp.VERDICTS


def test_only_dead_rows_are_ever_removable():
    for kw in ({}, {"is_main": True}, {"dirty": True}, {"prunable": True},
               {"path_exists": False}, {"locked": True}, {"signal_error": True},
               {"default_ref": None}):
        r = wp.classify(base_row(**kw))
        assert r["removable"] is False, (kw, r["verdict"])


def test_an_open_pr_beats_a_landing_signal():
    """A branch can be both merged and have a newer open PR reusing the name.
    Live wins — the tool must not remove a tree someone is iterating in."""
    row = base_row(landed_signals=["ancestor"],
                   pr={"answered": True, "state": "OPEN", "number": 11,
                       "merged_at": None, "why": "complete index"})
    assert wp.classify(row)["verdict"] == "live"


# ── the safety contract ───────────────────────────────────────────────────────

def _executable_string_literals(path: Path) -> "list[str]":
    """Every string constant in the module EXCEPT docstrings and comments.

    A `--force` that reaches git has to be a string literal in executable code,
    so this is the exact surface — and it does not go red merely because the
    module's prose explains why `--force` is never passed.
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_the_tool_never_passes_force_to_worktree_remove():
    """Structural, and deliberately blunt. A verdict-level test cannot see a
    `--force` added to the removal call, because the fixture would be removed
    either way and the test would stay green."""
    literals = _executable_string_literals(TOOL)
    offenders = [s for s in literals if "--force" in s or s == "-f"]
    assert offenders == [], f"a force flag reached executable code: {offenders}"
    assert "worktree" in literals and "remove" in literals, (
        "the removal call is not where this test thinks it is — the scan above "
        "would then be looking at the wrong file")


def test_the_force_scanner_can_go_red(tmp_path):
    """🔴 Negative control on the scanner above. A check that cannot fail is
    indistinguishable from no check, and this one is a grep in disguise."""
    mutant = tmp_path / "mutant.py"
    mutant.write_text(
        '"""A docstring that says --force and must NOT trip the scanner."""\n'
        'def f():\n'
        '    """Also mentions --force."""\n'
        '    return _git(repo, "worktree", "remove", "--force", str(path))\n',
        encoding="utf-8")
    assert [s for s in _executable_string_literals(mutant) if "--force" in s] == ["--force"]


def test_dry_run_is_the_default_and_removes_nothing(universe, tmp_path, capsys):
    repo, paths, gh = universe
    before = set(git(repo, "worktree", "list", "--porcelain").splitlines())
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    assert rc == wp.RC_OK
    assert set(git(repo, "worktree", "list", "--porcelain").splitlines()) == before
    err = capsys.readouterr().err
    assert "DRY RUN" in err


def test_execute_without_confirm_refuses_and_removes_nothing(universe, tmp_path, capsys):
    repo, paths, gh = universe
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--execute"])
    assert rc == wp.RC_EXECUTE_REFUSED
    assert paths["squashed"].is_dir()
    err = capsys.readouterr().err
    assert "REFUSED" in err
    # The message must name the number, or the operator's next move is a guess.
    assert "--confirm 2" in err, err


def test_execute_with_a_wrong_confirm_refuses_and_removes_nothing(universe, capsys):
    repo, paths, gh = universe
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "99"])
    assert rc == wp.RC_EXECUTE_REFUSED
    assert paths["squashed"].is_dir() and paths["merged"].is_dir()
    assert "does not match" in capsys.readouterr().err


def test_execute_removes_only_the_dead_rows(universe, capsys):
    repo, paths, gh = universe
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1)
    n = sum(1 for r in rows if r["removable"])
    assert n == 2, [r["path"] for r in rows if r["removable"]]
    assert wp.execute_removals(rows, n) == wp.RC_OK
    assert not paths["squashed"].exists()
    assert not paths["merged"].exists()
    assert paths["unmerged"].is_dir(), "an orphan was removed"
    assert paths["dirty"].is_dir(), "a dirty worktree was removed"


def test_execute_rechecks_dirtiness_at_the_moment_of_removal(universe, capsys):
    """🔴 The scan is a claim about the PAST. A concurrent session can start
    editing between the survey and the delete, so the check runs immediately
    before the destructive step — not in the survey that motivated it."""
    repo, paths, gh = universe
    rows = [r for r in wp.scan_repo(repo, gh, True, 2000, jobs=1)
            if r["path"] == str(paths["squashed"])]
    assert rows[0]["removable"] is True
    write(paths["squashed"] / "SOMEONE_STARTED_WORKING.md", "in flight\n")
    assert wp.execute_removals(rows, 1) == wp.RC_OK
    assert paths["squashed"].is_dir(), "a worktree that went dirty after the scan was removed"
    assert "SKIP" in capsys.readouterr().err


def test_execute_refuses_a_row_whose_verdict_is_not_dead_even_if_removable_is_set(universe, capsys):
    """Defence in depth: `removable` is derived from the verdict, but the
    executor re-asserts the verdict rather than trusting the flag it was handed."""
    repo, paths, gh = universe
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1)
    victim = [r for r in rows if r["path"] == str(paths["unmerged"])]
    assert victim[0]["verdict"] == "orphan"
    victim[0]["removable"] = True
    assert wp.execute_removals(victim, 1) == wp.RC_OK
    assert paths["unmerged"].is_dir()
    assert "not dead" in capsys.readouterr().err


def test_execute_skips_a_worktree_git_no_longer_lists(universe, capsys):
    repo, paths, gh = universe
    rows = [r for r in wp.scan_repo(repo, gh, True, 2000, jobs=1)
            if r["path"] == str(paths["merged"])]
    git(repo, "worktree", "remove", str(paths["merged"]))
    assert wp.execute_removals(rows, 1) == wp.RC_OK
    assert "no longer lists" in capsys.readouterr().err


# ── cli surface ───────────────────────────────────────────────────────────────

def test_json_output_carries_the_evidence_for_every_row(universe, tmp_path, capsys):
    repo, paths, gh = universe
    out = tmp_path / "report.json"
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--format", "json", "--out", str(out)])
    assert rc == wp.RC_OK
    capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload) == {"summary", "rows"}
    assert payload["summary"]["counts"]["dead"] == 2
    assert payload["summary"]["counts"]["orphan"] == 1
    for row in payload["rows"]:
        assert row["evidence"], row["path"]
        assert row["verdict_reason"]
        assert "default branch" in row["evidence"][0]


def test_the_text_report_shouts_about_cannot_tell(universe, capsys):
    repo, paths, gh = universe
    wp.main(["--repo", str(repo), "--jobs", "1", "--no-gh"])
    out = capsys.readouterr().out
    assert "cannot-tell" in out
    assert "NOT dead" in out


def test_a_clean_run_with_no_unknowns_does_not_shout(universe, capsys):
    repo, paths, gh = universe
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    out = capsys.readouterr().out
    assert "NOT dead" not in out


def test_no_repos_named_is_an_error_not_an_empty_success(capsys):
    assert wp.main([]) == wp.RC_NOTHING_TO_SCAN
    assert "nothing to scan" in capsys.readouterr().err


def test_dry_run_and_execute_together_are_a_usage_error(universe):
    repo, paths, gh = universe
    assert wp.main(["--repo", str(repo), "--dry-run", "--execute"]) == wp.RC_USAGE


def test_confirm_without_execute_is_a_usage_error(universe):
    repo, paths, gh = universe
    assert wp.main(["--repo", str(repo), "--confirm", "1"]) == wp.RC_USAGE


def test_repos_from_file_reads_paths_and_ignores_comments(universe, tmp_path):
    repo, paths, gh = universe
    listing = tmp_path / "repos.txt"
    listing.write_text(f"# a comment\n\n{repo}  # trailing\n", encoding="utf-8")

    class A:
        repo = []
        repos_from = str(listing)
        scan_root = []
        scan_depth = 2
    assert wp.collect_repos(A()) == [Path(str(repo))]


def test_the_tool_runs_as_a_subprocess_and_exits_zero(universe, tmp_path):
    repo, paths, gh = universe
    r = subprocess.run([sys.executable, str(TOOL), "--repo", str(repo),
                        "--gh-cmd", gh, "--jobs", "1", "--format", "json"],
                       capture_output=True, text=True, check=False, timeout=180)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["summary"]["worktrees"] == 5


# ── discovery ─────────────────────────────────────────────────────────────────

def test_discovery_finds_main_checkouts_and_not_their_worktrees(universe, tmp_path):
    repo, paths, gh = universe
    found = wp.discover_repos([tmp_path], depth=3)
    assert repo.resolve() in found
    for p in paths.values():
        assert p.resolve() not in found, f"{p} is a linked worktree, not a checkout"


def test_discovery_accepts_a_root_that_is_itself_a_repo(universe, tmp_path):
    repo, paths, gh = universe
    assert wp.discover_repos([repo], depth=1) == [repo.resolve()]


def test_discovery_respects_the_depth_limit(universe, tmp_path):
    repo, paths, gh = universe
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    nested = new_repo(deep, "nested")
    assert nested.resolve() not in wp.discover_repos([tmp_path], depth=2)
    assert nested.resolve() in wp.discover_repos([tmp_path], depth=5)


def test_discovery_survives_a_directory_it_cannot_read(universe, tmp_path):
    """🔴 Found by the FIRST real run, not by a fixture. `Path.is_dir()` raises
    on EACCES — it swallows only ENOENT/ENOTDIR/EBADF/ELOOP — so a `lost+found`
    inside a scan root took the entire scan down with a traceback before one row
    was printed. An unreadable directory holds no worktrees we can classify; it
    is not a reason to abandon the other 750.
    """
    walled = tmp_path / "walled"
    walled.mkdir()
    (walled / "inner").mkdir()
    walled.chmod(0o000)
    try:
        found = wp.discover_repos([tmp_path], depth=3)
    finally:
        walled.chmod(0o755)
    repo, _, _ = universe
    assert repo.resolve() in found, "the readable repo was lost to the unreadable sibling"


@pytest.mark.parametrize("depth", [1, 3])
def test_discovery_survives_an_unreadable_root_itself(tmp_path, depth):
    walled = tmp_path / "root"
    walled.mkdir()
    walled.chmod(0o000)
    try:
        assert wp.discover_repos([walled], depth=depth) == []
    finally:
        walled.chmod(0o755)


def test_repo_slug_reads_the_github_owner_and_name(tmp_path):
    repo = new_repo(tmp_path)
    assert wp.repo_slug(repo) == "fixture/repo"
    git(repo, "remote", "set-url", "origin", "git@github.com:owner/name.git")
    assert wp.repo_slug(repo) == "owner/name"
    git(repo, "remote", "set-url", "origin", "https://gitlab.com/owner/name.git")
    assert wp.repo_slug(repo) is None


def test_default_branch_resolution_records_how_it_was_resolved(tmp_path):
    repo = new_repo(tmp_path)
    ref, how = wp.resolve_default_ref(repo)
    assert (ref, how) == ("refs/remotes/origin/main", "origin-head-symref")
    git(repo, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
    assert wp.resolve_default_ref(repo) == ("refs/remotes/origin/main", "remote-candidate:main")
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert wp.resolve_default_ref(repo) == ("refs/heads/main", "local-candidate:main")


# ── the instrument's own controls ─────────────────────────────────────────────

# ── 🔴 --exclude-path / the DEFAULT agent-worktree exclusion ──────────────────
#
# WHY THIS SECTION EXISTS, measured rather than imagined. ONE scan, dated, with
# its scope named — these are numbers from that run, not a standing fact about
# the box, and the command is here so anyone can re-derive them:
#
#     2026-08-27  worktree-prune --scan-root /home/zach/workspace --scan-depth 2
#     860 worktrees / 129 repos -> 236 dead, 44 orphan, 528 live, 52 cannot-tell
#     137 of the 236 dead rows are under a `.claude/worktrees/` directory, so the
#     default leaves 99 removable and --include-agent-worktrees leaves 236.
#
# 🔴 A RE-RUN THAT DISAGREES SLIGHTLY IS NOT A BUG. Two runs the same day gave
# `236 dead / 137 agent-dead / 99 removable` IDENTICALLY and moved
# orphan/cannot-tell 44/52 -> 43/53. The figures the design rests on are the
# stable ones; the soft two are ±1 on a box with live sessions working in it.
#
# 🔴 The numbers this comment used to carry — "870 worktrees / 128 repos -> 250
# dead … 131 agent … safe set of 106" — are RETIRED, not restated: 250 - 131 is
# 119, not 106, so at most two of the three could ever have been right, and the
# run that produced them cannot be re-derived. Do not resurrect them.
#
# Those 137 belong to OTHER LIVE Claude sessions: `dead` is a correct verdict
# about the BRANCH and a catastrophic instruction about the DIRECTORY. Excluding
# whole repos to dodge them also drops the ordinary dead rows those repos hold,
# because a repo like civit/civitai carries dozens of each.
#
# So the filter has to be per-ROW; it has to be visible (an excluded row that
# vanished from the report would read as "we covered everything"); and — because
# it is the majority case, not the exception — it has to be the DEFAULT. The
# dangerous run is the one an operator has to type `--include-agent-worktrees`
# for.

AGENT_GLOB = "*/.claude/worktrees/*"


@pytest.fixture()
def two_dead(tmp_path: Path):
    """One repo, TWO dead worktrees — one at an agent path, one not.

    🔴 Both halves matter. A fixture with only the agent worktree cannot tell
    "the filter spared it" from "the run removed nothing at all", and that is
    precisely the shape of a filter wired to nothing.

    Returns (repo, agent_worktree, plain_worktree, gh_cmd).
    """
    repo = new_repo(tmp_path)

    # squash-merged -> dead by content-identical
    git(repo, "checkout", "-q", "-b", "feat/agent-work")
    write(repo / "agentwork.txt", "one\n")
    git(repo, "add", "agentwork.txt")
    git(repo, "commit", "-qm", "agent work part 1")
    write(repo / "agentwork.txt", "one\ntwo\n")
    git(repo, "add", "agentwork.txt")
    git(repo, "commit", "-qm", "agent work part 2")
    git(repo, "checkout", "-q", "main")
    squash_merge(repo, "feat/agent-work", "agentwork.txt", "squash agent work (#1)")

    # true-merged -> dead by ancestry
    commit_on_branch(repo, "feat/plain", "plain.txt", "plain\n", "plain work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/plain", "feat/plain")
    publish(repo)

    # The agent worktree sits where the real ones do: <repo>/.claude/worktrees/agent-<id>.
    agent = add_worktree(repo, repo / ".claude" / "worktrees" / "agent-7f3a91",
                         "feat/agent-work")
    plain = add_worktree(repo, tmp_path / "wts" / "plain", "feat/plain")
    return repo, agent, plain, gh_stub(tmp_path, [], name="gh-excl")


@pytest.fixture()
def no_agent_worktrees(tmp_path: Path):
    """One repo, ONE ordinary dead worktree, and NO `.claude/worktrees` anywhere.

    🔴 The shape that broke round 2: the default agent glob matches zero rows
    here, which is the ordinary state of most repos on this box. Returns
    (repo, plain_worktree, gh_cmd).
    """
    repo = new_repo(tmp_path)
    commit_on_branch(repo, "feat/plain", "plain.txt", "plain\n", "plain work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/plain", "feat/plain")
    publish(repo)
    plain = add_worktree(repo, tmp_path / "wts" / "plain", "feat/plain")
    return repo, plain, gh_stub(tmp_path, [], name="gh-noagent")


def test_the_no_agent_fixture_really_holds_no_claude_worktrees(no_agent_worktrees):
    """Positive control on the fixture the four tests below rest on: if it DID
    hold an agent worktree, "the default glob matched zero" would be false and
    each of them would be passing for the wrong reason."""
    repo, plain, gh = no_agent_worktrees
    rows = _rows_by_path(repo, gh, [AGENT_GLOB])
    assert [r["excluded_by"] for r in rows.values()] == [None] * len(rows)
    assert rows[str(plain)]["verdict"] == "dead"
    assert rows[str(plain)]["removable"] is True


def _rows_by_path(repo: Path, gh: str, globs: "list[str] | None" = None) -> dict:
    return {r["path"]: r
            for r in wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=globs)}


# ── POSITIVE CONTROL on the fixture: unfiltered, BOTH are removable ───────────

def test_without_any_filter_both_dead_worktrees_are_removable(two_dead):
    """🔴 Without this, every assertion below is compatible with a fixture that
    was never removable in the first place."""
    repo, agent, plain, gh = two_dead
    rows = _rows_by_path(repo, gh)
    assert rows[str(agent)]["verdict"] == "dead", rows[str(agent)]["verdict_reason"]
    assert rows[str(plain)]["verdict"] == "dead", rows[str(plain)]["verdict_reason"]
    assert rows[str(agent)]["removable"] is True
    assert rows[str(plain)]["removable"] is True
    assert rows[str(agent)]["excluded_by"] is None


# ── the headline: one survives, the other is really gone ──────────────────────

@pytest.mark.parametrize("flag", [
    [],                                  # 🔴 the DEFAULT spares it — no flag at all
    ["--exclude-path", AGENT_GLOB],      # …and typing the same glob agrees
])
def test_execute_removes_the_plain_dead_tree_and_spares_the_excluded_one(two_dead, flag, capsys):
    """BOTH halves, in ONE run. The excluded worktree must still be on disk and
    the other must be GONE — a filter that spared everything would pass the first
    assertion on its own.

    The empty parametrisation is the one that matters most: it says the sparing
    happens with NO flag typed."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  *flag, "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_OK, err
    assert agent.is_dir(), "the EXCLUDED agent worktree was removed"
    assert (agent / "agentwork.txt").is_file(), (
        "the excluded worktree's directory survives but its contents do not")
    assert not plain.exists(), (
        "the non-excluded dead worktree was NOT removed — the run may have "
        "removed nothing at all, which would make the survival above meaningless")
    assert "removed=1" in err, err


def test_the_excluded_row_is_still_classified_dead_and_marked(two_dead):
    """Exclusion changes REMOVABILITY, not the verdict. A row that quietly became
    `live` or dropped out would hide a real dead worktree from the operator."""
    repo, agent, plain, gh = two_dead
    rows = _rows_by_path(repo, gh, [AGENT_GLOB])
    r = rows[str(agent)]
    assert r["verdict"] == "dead"
    assert r["excluded_by"] == AGENT_GLOB
    assert r["removable"] is False
    assert any("exclude" in b for b in r["blockers"]), r["blockers"]
    assert rows[str(plain)]["excluded_by"] is None
    assert rows[str(plain)]["removable"] is True


# ── --confirm N counts REMOVABLE rows, not dead rows ──────────────────────────

def test_confirm_counts_only_the_rows_that_will_actually_be_removed(two_dead, capsys):
    """N = non-excluded dead count SUCCEEDS — under the DEFAULT, no flag."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "1"])
    assert rc == wp.RC_OK, capsys.readouterr().err
    assert not plain.exists()
    assert agent.is_dir()


def test_confirm_with_the_total_dead_count_is_refused_when_one_is_excluded(two_dead, capsys):
    """N = TOTAL dead count (2) REFUSES, and removes nothing.

    This is the half that pins the meaning of --confirm: if excluded rows still
    counted, `--confirm 2` would be the accepted value and the operator's number
    would silently describe rows the run was never going to touch.

    🔴 This is also the `--confirm N` half of the DEFAULT INVERSION: the number
    an operator reads off an unfiltered mental model (2, the dead count) is now
    the REFUSED one, with no flag typed."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "2"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert "does not match the 1 row(s)" in err, err
    assert agent.is_dir() and plain.is_dir(), "a refused run removed something"


def test_opting_back_in_to_agent_worktrees_needs_confirm_two(two_dead, capsys):
    """The control on the test above: 2 is the RIGHT number once the operator
    TYPES --include-agent-worktrees, so the refusal there is caused by the
    default exclusion and not by the fixture happening to hold one dead row.

    🔴 And it is the only test that proves the opt-in flag does anything at all:
    without it, a `--include-agent-worktrees` wired to nothing would leave every
    other test in this file green."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--include-agent-worktrees", "--execute", "--confirm", "2"])
    assert rc == wp.RC_OK, capsys.readouterr().err
    assert not agent.exists() and not plain.exists()


# ── excluded rows stay VISIBLE in the report ──────────────────────────────────

def test_excluded_rows_still_appear_in_the_text_report(two_dead, capsys):
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--verbose"])
    out = capsys.readouterr().out
    assert "excluded" in out
    assert "1 row(s) matched at least one glob" in out, out
    assert "1 of them are `dead`" in out, out
    assert (f"(default; --include-agent-worktrees turns it off) {AGENT_GLOB!r}  ->  "
            f"matched 1 row(s), 1 of them dead") in out, out
    assert str(agent) in out, "the excluded worktree vanished from the report"
    assert "[dead (excluded)]" in out, out
    # …and the summary still counts it as dead, so the operator's totals do not
    # silently shrink when they add a filter.
    assert "2 dead" in out, out


def test_the_default_report_says_dead_rows_were_spared(two_dead, capsys):
    """🔴 THE DEFAULT-INVERSION READING HAZARD, pinned.

    With the exclusion on by default the removable count is routinely far below
    the dead count, and an operator who reads "1 row(s) would be removed" as
    "this run covered everything" is exactly the silent-truncation failure this
    tool exists not to have. The spared count must sit next to the removable one
    and say so, WITHOUT --verbose and WITHOUT any flag typed.
    """
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    i = next(i for i, ln in enumerate(lines) if "would be removed by --execute" in ln)
    spared = lines[i + 1]
    assert "1 further `dead` row(s) were SPARED" in spared, out
    assert "NOT covered by this run" in spared, out

    # NEGATIVE CONTROL: with nothing spared the line must be absent, or it is
    # printed unconditionally and says nothing.
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees"])
    assert "were SPARED" not in capsys.readouterr().out


def test_the_report_shouts_when_agent_worktrees_are_opted_back_in(two_dead, capsys):
    """An override invisible in the output is a hardcode wearing a costume."""
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees"])
    out = capsys.readouterr().out
    assert "--include-agent-worktrees IS IN FORCE" in out, out
    assert "in use RIGHT NOW" in out, out

    # NEGATIVE CONTROL: the default run must NOT shout it.
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    assert "IS IN FORCE" not in capsys.readouterr().out


def test_the_shout_is_gated_on_the_GLOB_not_on_the_flag(two_dead, capsys):
    """🔴 A 🔴-MARKED WARNING THAT THE NEXT LINES OF THE SAME REPORT CONTRADICT.

    `--include-agent-worktrees --exclude-path '*/.claude/worktrees/*'` drops the
    default and re-adds the IDENTICAL glob as a typed one, so agent worktrees are
    still spared — and the behaviour was always right. The report nonetheless
    shouted "the default exclusion is OFF for this run, so worktrees belonging to
    OTHER LIVE Claude sessions are ordinary removal candidates" three lines above
    its own filter table saying one dead row had been spared by that very glob.

    That is how a real warning gets tuned out, so it is gated on whether the glob
    is out of force, not on whether the flag was passed.

    🔴 The note that replaces the shout may only claim what is TRUE. It used to
    say "this run behaves exactly like the default. The flag is doing nothing
    here." — measurably false, and false in the direction that matters: typing
    the glob converts the constant from an IMPLIED glob into a TYPED one, and
    only typed globs are `--execute` refusal candidates. Pinned in
    `test_a_retyped_glob_with_the_flag_is_NOT_the_default_it_refuses`; here the
    note's own wording is pinned, and the run is taken past dry-run so the claim
    is asserted on a report whose `--execute` really did fire.
    """
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees", "--exclude-path", AGENT_GLOB])
    out = capsys.readouterr().out
    assert "IS IN FORCE" not in out, out
    assert "ordinary removal candidates" not in out, out
    # …and the report says what IS true: agent worktrees are still spared, and
    # this is NOT the same command line as the default.
    assert "agent worktrees are STILL spared" in out, out
    assert "It is NOT identical to the default" in out, out
    # 🔴 THE WHOLE NORMALISED CLAUSE, not its first half. The artifact under test
    # is prose: a guard on the opening words is walkable by rewording the tail,
    # and round 7 rewrote exactly that tail into a false sentence while this
    # assertion stayed green.
    assert ("can REFUSE --execute on that glob, which the tool's own implied copy never "
            "could — dropping the flag does not change that, because the glob is already "
            "in the list either way.") in out, out
    # …and NOT the two claims round 4 printed, both of which were false here.
    assert "The flag is doing nothing here." not in out, out
    assert "behaves exactly like the default" not in out, out
    # The contradiction it used to sit above, still present and still correct.
    assert f"{AGENT_GLOB!r}  ->  matched 1 row(s), 1 of them dead" in out, out

    # 🔴 PAST DRY-RUN. The old assertion only ever ran with `--execute` absent,
    # so it structurally could not see that the note's claim is about what
    # `--execute` does. Same report, same note, on a run that actually removed.
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--include-agent-worktrees", "--exclude-path", AGENT_GLOB,
                  "--execute", "--confirm", "1"])
    live = capsys.readouterr()
    assert rc == wp.RC_OK, live.err
    assert "agent worktrees are STILL spared" in live.out, live.out
    assert "IS IN FORCE" not in live.out, live.out
    assert agent.is_dir(), "the retyped glob did not spare the agent worktree"
    assert not plain.exists(), "nothing was removed, so this is still a dry run"

    # 🔴 BOTH DIRECTIONS. Same flag, glob genuinely out of force -> still shouts.
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees"])
    loud = capsys.readouterr().out
    assert "--include-agent-worktrees IS IN FORCE" in loud, loud
    assert "ordinary removal candidates" in loud, loud
    assert "agent worktrees are STILL spared" not in loud, loud


def test_the_retyped_glob_really_does_spare_the_agent_worktree(two_dead, capsys):
    """🔴 The BEHAVIOURAL half of the test above — without it, "the report no
    longer shouts" would be equally consistent with the warning having become
    wrong in the OTHER, catastrophic direction.

    `--confirm 1` is the assertion: two dead rows, one spared, exactly one
    removed, and the agent tree still on disk afterwards.
    """
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--include-agent-worktrees", "--exclude-path", AGENT_GLOB,
                  "--execute", "--confirm", "1"])
    assert rc == wp.RC_OK, capsys.readouterr().err
    assert agent.is_dir(), "the retyped glob did not spare the agent worktree"
    assert not plain.exists(), "nothing was removed, so --confirm 1 proved nothing"


def test_summarize_reports_whether_the_agent_glob_is_actually_in_force(two_dead):
    """The key the gate reads, at the unit level: passing the flag and retyping
    the glob are INDEPENDENT, and only their combination decides."""
    repo, agent, plain, gh = two_dead
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=[AGENT_GLOB])

    default_run = wp.summarize(rows, [AGENT_GLOB], [], agent_worktrees_included=False)
    assert default_run["agent_worktree_glob_in_force"] is True
    assert default_run["agent_worktrees_included"] is False

    retyped = wp.summarize(rows, [AGENT_GLOB], [AGENT_GLOB], agent_worktrees_included=True)
    assert retyped["agent_worktree_glob_in_force"] is True
    assert retyped["agent_worktrees_included"] is True

    opted_out = wp.summarize(rows, [], [], agent_worktrees_included=True)
    assert opted_out["agent_worktree_glob_in_force"] is False
    assert opted_out["agent_worktrees_included"] is True


def test_excluded_dead_counts_only_the_DEAD_excluded_rows(two_dead, capsys):
    """🔴 FOUND BY THE MUTATION BATTERY, NOT BY READING: forcing `excluded_dead`
    to `len(excluded)` left the whole suite GREEN.

    Every excluded row in every fixture above happened to be `dead`, so the two
    numbers were always EQUAL and no assertion could tell them apart — the
    fixture-can-only-produce-the-constant's-own-value trap, surviving a fully
    green run. The control is mechanical: give the filter a row it CANNOT count
    as dead and watch the numbers separate.

    The distinction is load-bearing, not cosmetic. `excluded_dead` is the number
    the report prints as "…and N further `dead` row(s) were SPARED — NOT covered
    by this run", i.e. the operator's estimate of the work still outstanding.
    Inflated by every live agent worktree on the box it would read as a backlog
    of removable trees that does not exist.
    """
    repo, agent, plain, gh = two_dead
    # A SECOND agent-path worktree that is emphatically NOT dead: unmerged, so
    # `orphan` at worst and never `dead`.
    commit_on_branch(repo, "feat/unlanded", "unlanded.txt", "nope\n", "unlanded work")
    live_agent = add_worktree(repo, repo / ".claude" / "worktrees" / "agent-beef01",
                              "feat/unlanded")

    rows = _rows_by_path(repo, gh, [AGENT_GLOB])
    assert rows[str(live_agent)]["excluded_by"] == AGENT_GLOB
    assert rows[str(live_agent)]["verdict"] != "dead", (
        "the fixture's second agent tree is dead too, so this test is back to "
        "comparing a number against itself")

    s = wp.summarize(list(rows.values()), [AGENT_GLOB], agent_worktrees_included=False)
    assert s["excluded"] == 2, s
    assert s["excluded_dead"] == 1, s
    assert s["excluded_dead"] != s["excluded"], (
        "the two numbers are equal, so this assertion cannot see a mutant that "
        "computes either one from the other")

    # …and the report prints the narrower number where it claims to.
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    out = capsys.readouterr().out
    assert "…and 1 further `dead` row(s) were SPARED" in out, out
    assert "2 row(s) matched at least one glob" in out, out
    assert "1 of them are `dead`" in out, out


def _repo_table_row(out: str, repo) -> "list[str]":
    """The per-repo table line for `repo`, as fields."""
    hits = [ln for ln in out.splitlines()
            if str(repo)[-58:] in ln and not ln.startswith("[")]
    assert len(hits) == 1, f"expected one table row for {repo}, got {hits}"
    return hits[0].split()


def test_the_per_repo_table_reports_the_excluded_count_in_its_column(two_dead, capsys):
    """🔴 A mutation sweep forced this column to compute ZERO and all 112 tests
    stayed green: the suite pinned the header, the summary sentence and the
    `[dead (excluded)]` label, but never a COLUMN VALUE.

    The failure that hides behind it: the table reads `0 excluded` for a repo
    where 59 rows were in fact spared, so the operator concludes the filter did
    nothing and widens or drops it.
    """
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    filtered = _repo_table_row(capsys.readouterr().out, repo)
    assert filtered[-1] == "1", filtered

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees"])
    unfiltered = _repo_table_row(capsys.readouterr().out, repo)
    assert unfiltered[-1] == "0", (
        "the column reads the same with and without a filter, so it is not "
        "reporting the exclusion", unfiltered)


def test_the_table_row_helper_can_go_red(two_dead, capsys):
    """Negative control on `_repo_table_row` — a parser that silently matched
    the wrong line would make the column assertions meaningless."""
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    out = capsys.readouterr().out
    with pytest.raises(AssertionError, match="expected one table row"):
        _repo_table_row(out, "/no/such/repo/anywhere")


def test_excluded_rows_still_appear_in_the_json_report(two_dead, tmp_path, capsys):
    repo, agent, plain, gh = two_dead
    out = tmp_path / "excl.json"
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--format", "json", "--out", str(out)])
    capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    by_path = {r["path"]: r for r in payload["rows"]}
    assert str(agent) in by_path, "the excluded row was dropped from the JSON rows"
    assert by_path[str(agent)]["verdict"] == "dead"
    assert by_path[str(agent)]["excluded_by"] == AGENT_GLOB
    assert payload["summary"]["counts"]["dead"] == 2
    assert payload["summary"]["removable"] == 1
    assert payload["summary"]["excluded"] == 1
    assert payload["summary"]["excluded_dead"] == 1
    assert payload["summary"]["exclude_globs"] == [
        {"glob": AGENT_GLOB, "typed": False, "matched": 1, "matched_dead": 1}]
    assert payload["summary"]["exclude_globs_matching_nothing"] == []
    assert payload["summary"]["exclude_globs_blocking_execute"] == []
    assert payload["summary"]["agent_worktrees_included"] is False
    assert payload["summary"]["allow_unmatched_globs"] is False


def test_the_json_marks_a_typed_glob_as_typed_and_the_default_as_not(two_dead, tmp_path, capsys):
    """🔴 `typed` is what decides refusal eligibility, so a consumer — and the
    report — must be able to tell an operator filter from the tool's own
    default. Both values, in one run, so a field hardwired either way fails."""
    repo, agent, plain, gh = two_dead
    out = tmp_path / "typed.json"
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--exclude-path", "*/wts/*", "--format", "json", "--out", str(out)])
    capsys.readouterr()
    per_glob = json.loads(out.read_text(encoding="utf-8"))["summary"]["exclude_globs"]
    assert {d["glob"]: d["typed"] for d in per_glob} == {
        "*/wts/*": True, AGENT_GLOB: False}


def test_an_opted_in_run_does_not_shout_about_exclusions(two_dead, capsys):
    """Negative control on the report lines above — they must be caused by an
    actual exclusion, not printed unconditionally. `--include-agent-worktrees`
    is now the only way to get a run with no glob in force at all."""
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees"])
    out = capsys.readouterr().out
    assert "exclusion filters in force" not in out
    assert "matched at least one glob" not in out
    assert "(excluded)" not in out


# ── 🔴 THE MUTATION CONTROL for the exclusion ─────────────────────────────────

def test_neutering_the_path_predicate_makes_the_agent_worktree_removable(two_dead, monkeypatch):
    """Watch the SAME fixture go wrong with the filter removed.

    If this fails, the agent row's `removable is False` above is being produced
    by something other than the exclusion — a dirty tree, an open PR, a verdict
    that was never `dead` — and every assertion in this section is vacuous.
    """
    repo, agent, plain, gh = two_dead
    monkeypatch.setattr(wp, "path_excluded", lambda path, globs: None)
    rows = _rows_by_path(repo, gh, [AGENT_GLOB])
    r = rows[str(agent)]
    assert r["excluded_by"] is None
    assert r["verdict"] == "dead" and r["removable"] is True, (
        "with the predicate neutered the agent worktree is STILL not removable, "
        "so the exclusion is not what is sparing it")


# ── 🔴 GLOB SEMANTICS: `*` CROSSES `/`. Chosen, documented, pinned. ───────────

def test_the_glob_star_crosses_slashes():
    """fnmatch semantics, NOT shell/pathlib globbing.

    This is a real fork: under pathlib/shell rules `*` stops at a separator and
    `*/.claude/worktrees/*` would match only a worktree exactly one level below
    the root — i.e. essentially none of the real ones. fnmatch is chosen because
    the agent-worktree case needs to match at ANY depth. The cost, pinned here so
    nobody discovers it by accident, is that a glob is easy to write too WIDE —
    which in this tool spares more and removes less, never the reverse.
    """
    assert wp.path_excluded("/home/z/a/b/c/.claude/worktrees/agent-1", [AGENT_GLOB]) == AGENT_GLOB
    assert wp.path_excluded("/home/z/.claude/worktrees/agent-1", [AGENT_GLOB]) == AGENT_GLOB
    assert wp.path_excluded("/home/z/.claude/worktrees/agent-1/deep/inside",
                            [AGENT_GLOB]) == AGENT_GLOB
    # `*` swallowing separators is the DEFINING behaviour, not an accident of the
    # agent glob: one `*` spans three components here.
    assert wp.path_excluded("/x/a/b/c/leaf", ["/x/*/leaf"]) == "/x/*/leaf"
    # …and the negative half, so the matcher is not simply always-true.
    assert wp.path_excluded("/home/z/repo/wt/agent-1", [AGENT_GLOB]) is None
    assert wp.path_excluded("/home/z/repo/.claude/other/agent-1", [AGENT_GLOB]) is None
    assert wp.path_excluded("/x/a/b/c/other", ["/x/*/leaf"]) is None


def test_a_non_agent_entry_under_claude_worktrees_is_still_matched():
    """🔴 The glob is `worktrees/*`, NOT `worktrees/agent-*`.

    Measured on this box 2026-08-27: of the 246 `.claude/worktrees/` entries the
    scan returned, TWO had no `agent-` prefix —
    `…/fast/comfyui/.claude/worktrees/card-ux` (a real registered worktree on
    `refs/heads/feat/prefopt-card-ux`) and
    `…/promptver/.claude/worktrees/fix-0421-image-edit-auth`. Under the narrower
    glob the first was spared only by the dirty check (three untracked
    `__pycache__` dirs); one `git clean` plus a squash merge and it would have
    become a removable row the exclusion's own help text claimed to cover. The
    count MOVED (1 of 246 a day earlier), which is the argument: the prefix is a
    convention nobody enforces, the DIRECTORY identifies these trees.
    """
    assert wp.path_excluded("/home/z/fast/comfyui/.claude/worktrees/card-ux",
                            [AGENT_GLOB]) == AGENT_GLOB
    assert wp.path_excluded("/home/z/repo/.claude/worktrees/keep-me",
                            [AGENT_GLOB]) == AGENT_GLOB


def test_the_matcher_does_not_route_through_normcase(monkeypatch):
    """🔴 BEHAVIOURAL pin on `fnmatchcase` vs `fnmatch`.

    A mutation sweep found `fnmatchcase` -> `fnmatch` SURVIVING the whole suite,
    because `os.path.normcase` is the identity on POSIX — the old test's
    docstring claimed it pinned platform-independent case handling and on Linux
    it pinned nothing. `fnmatch.fnmatch` calls `os.path.normcase` at match time
    and `fnmatchcase` does not, so making normcase actually fold case is what
    tells the two apart on this host.
    """
    monkeypatch.setattr(os.path, "normcase", str.lower)
    # Positive control on the monkeypatch itself: with normcase folding, the
    # `fnmatch` variant DOES match — so a green assertion below is about the
    # code's choice of matcher, not about the patch failing to take effect.
    import fnmatch as _fn
    assert _fn.fnmatch("/home/z/.CLAUDE/worktrees/agent-1", AGENT_GLOB) is True
    assert wp.path_excluded("/home/z/.CLAUDE/worktrees/agent-1", [AGENT_GLOB]) is None


def test_the_glob_match_is_case_sensitive():
    """Plain case sensitivity on this host. This says nothing about other
    platforms — `test_the_matcher_does_not_route_through_normcase` is the test
    that pins the matcher choice."""
    assert wp.path_excluded("/home/z/.CLAUDE/worktrees/agent-1", [AGENT_GLOB]) is None


def test_excluding_a_directory_covers_its_contents():
    """🔴 SUBTREE semantics. Before this, a glob naming a repo matched the repo's
    own row and NOTHING else — the report said "1 row(s) matched … 0 of them are
    dead" while both dead children stayed removable. A reassuring positive is
    worse than a silent zero, because nothing prompts a second look."""
    repo = "/home/z/workspace/civitai"
    assert wp.path_excluded(repo, [repo]) == repo
    assert wp.path_excluded(f"{repo}/.claude/worktrees/agent-1", [repo]) == repo
    assert wp.path_excluded(f"{repo}/deep/nested/tree", [repo]) == repo
    # A SIBLING with the same prefix is NOT covered — subtree, not string prefix.
    assert wp.path_excluded("/home/z/workspace/civitai-fork/wt", [repo]) is None
    assert wp.path_excluded("/home/z/workspace/other/wt", [repo]) is None


def test_a_trailing_slash_does_not_defeat_the_filter():
    """Rows carry `str(Path(...))`, which never ends in `/`, and tab-completion
    appends one to every directory. Unstripped, the glob matched nothing and the
    run was byte-identical to no filter."""
    assert wp.normalize_globs(["/home/z/repo/"]) == ["/home/z/repo"]
    assert wp.normalize_globs(["/home/z/repo///"]) == ["/home/z/repo"]
    # …and the stripped glob really does match, end to end.
    globs = wp.normalize_globs(["/home/z/repo/"])
    assert wp.path_excluded("/home/z/repo/wt", globs) == "/home/z/repo"


@pytest.mark.parametrize("padded", [
    "/home/z/repo/ ", " /home/z/repo/", "\t/home/z/repo/ \n", "/home/z/repo/  ",
])
def test_whitespace_around_a_glob_is_stripped_before_the_slash(padded):
    """🔴 The two spellings that disagreed. The resolver did `raw.rstrip('/')`
    then tested `.strip()`; the CLI's empty check did `.strip().rstrip('/')`. So
    `--exclude-path '/home/z/repo/ '` passed the CLI check (which saw
    `/home/z/repo`) and reached the matcher still carrying BOTH its trailing
    slash and its space — a glob that matches nothing, delivered through the two
    guards written to stop exactly that.

    Whitespace FIRST, then the slash, in ONE function used by both sites.
    """
    assert wp.normalize_glob(padded) == "/home/z/repo"
    assert wp.normalize_globs([padded]) == ["/home/z/repo"]
    assert wp.path_excluded("/home/z/repo/wt", wp.normalize_globs([padded])) == "/home/z/repo"


def test_a_whitespace_padded_glob_is_not_a_usage_error_and_does_not_refuse(two_dead, capsys):
    """The end-to-end half: the padded glob must behave EXACTLY like the clean
    one — accepted at the CLI, matching, and not tripping the zero-match
    refusal. Before the fix it was accepted and then matched nothing."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", f" {plain}/ ", "--execute", "--confirm", "0"])
    err = capsys.readouterr().err
    assert rc == wp.RC_OK, err
    assert "REFUSED" not in err, err
    assert plain.is_dir() and agent.is_dir()


def test_a_glob_on_the_resolved_path_matches_a_symlinked_repo(tmp_path):
    """git records the path a worktree was created with. An operator globbing
    the REAL path while git holds the symlinked one would otherwise get a silent
    zero — the failure mode this whole section exists to close."""
    real = tmp_path / "real"
    (real / "wt").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    via_link = str(link / "wt")
    assert wp.path_excluded(via_link, [str(real)]) == str(real)
    assert wp.path_excluded(via_link, [str(link)]) == str(link)


# ── 🔴 A PATH'S SHAPE MUST NOT BE ABLE TO KILL THE SCAN ──────────────────────

@pytest.fixture()
def symlink_loop(tmp_path: Path) -> str:
    """A REAL symlink loop: `a -> b -> a`. Returns a path UNDER it."""
    (tmp_path / "b").symlink_to(tmp_path / "a")
    (tmp_path / "a").symlink_to(tmp_path / "b")
    return str(tmp_path / "a" / "wt")


def test_the_symlink_loop_fixture_really_loops(symlink_loop):
    """🔴 POSITIVE CONTROL on the fixture, and the reason the `except` is wide.

    Measured 2026-08-27 at two interpreter versions on this exact construct:
    CPython 3.12.14 (this dev shell) raises RuntimeError('Symlink loop from …')
    from a NON-strict `Path.resolve()`; CPython 3.13.15 does not raise and
    returns the path unresolved. So on 3.13 this assertion would be vacuous —
    it says so rather than pretending to pin behaviour it cannot see there.

    Either way `os.path.realpath` reports the loop, which is the platform-level
    fact the fixture actually rests on.
    """
    assert os.path.islink(symlink_loop.rsplit("/", 1)[0])
    if sys.version_info < (3, 13):
        with pytest.raises(RuntimeError, match="Symlink loop"):
            Path(symlink_loop).resolve()
    else:
        pytest.skip("CPython >= 3.13 does not raise here; the guard is still required "
                    "because this tool runs under whatever interpreter it is given")


def test_a_symlink_loop_does_not_kill_path_excluded(symlink_loop):
    """🔴 A CRASH CLASS the exclusion feature INTRODUCED. Before it,
    `path_excluded` never touched the filesystem, so no path's SHAPE could take
    a scan down. It resolves paths now, and on 3.12 one looped symlink anywhere
    in one worktree's ancestry raised straight out of the classifier.
    """
    assert wp.path_excluded(symlink_loop, ["*/nope/*"]) is None
    # …and it still MATCHES on the recorded path, so the fallback degrades the
    # resolved candidate only — it does not turn the filter off.
    assert wp.path_excluded(symlink_loop, ["*/a/*"]) == "*/a/*"


def test_a_symlink_loop_does_not_kill_summarize(symlink_loop):
    """The SECOND call site, which crashed independently: `summarize` re-runs
    `path_excluded` per glob for the match counts, so fixing only `scan_repo`
    would have moved the traceback rather than removed it."""
    rows = [{"path": symlink_loop, "verdict": wp.DEAD, "repo": "/r", "excluded_by": None}]
    s = wp.summarize(rows, ["*/nope/*"], agent_worktrees_included=False)
    assert s["exclude_globs"] == [
        {"glob": "*/nope/*", "typed": True, "matched": 0, "matched_dead": 0}]


def test_a_looped_worktree_does_not_abort_the_whole_scan(tmp_path, capsys):
    """🔴 THE COST OF THE CRASH, end to end: the traceback did not lose ONE row,
    it lost the RUN — every other repo's verdict with it.

    Built the only way it can happen for real. git RESOLVES symlinks when it
    records a worktree path, so registering one *through* a link does not do it;
    instead an ordinary ancestor directory is REPLACED by a symlink loop after
    registration. That is what a rotated or re-pointed scratch directory looks
    like from the tool's side — the recorded path string is unchanged and only
    resolving it breaks.
    """
    repo = new_repo(tmp_path)
    for name, rel in (("feat/looped", "looped.txt"), ("feat/plain", "plain.txt")):
        commit_on_branch(repo, name, rel, f"{name}\n", f"{name} work")
        git(repo, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)
    publish(repo)

    holder = tmp_path / "holder"
    holder.mkdir()
    looped = add_worktree(repo, holder / "wt", "feat/looped")
    plain = add_worktree(repo, tmp_path / "wts" / "plain", "feat/plain")
    gh = gh_stub(tmp_path, [], name="gh-loop")

    # POSITIVE CONTROL: before the loop, both rows classify and both are dead.
    before = _rows_by_path(repo, gh, ["*/nope/*"])
    assert before[str(looped)]["verdict"] == "dead", before[str(looped)]["verdict_reason"]
    assert before[str(plain)]["verdict"] == "dead"

    # Turn `holder` into a loop. `git worktree list` still reports the same path.
    holder.rename(tmp_path / "holder-was-here")
    (tmp_path / "holder").symlink_to(tmp_path / "hop")
    (tmp_path / "hop").symlink_to(tmp_path / "holder")
    assert str(looped) in git(repo, "worktree", "list", "--porcelain"), (
        "git stopped reporting the looped path, so this fixture no longer "
        "exercises the crash")

    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=["*/nope/*"])
    by_path = {r["path"]: r for r in rows}
    assert str(looped) in by_path, "the looped row disappeared instead of being classified"
    assert by_path[str(plain)]["verdict"] == "dead", (
        "the ORDINARY row was lost too — which is the actual cost of the crash")
    assert wp.summarize(rows, ["*/nope/*"],
                        agent_worktrees_included=False)["worktrees"] == len(rows)


def test_a_looped_repo_path_on_the_command_line_does_not_crash_the_run(symlink_loop,
                                                                       tmp_path, capsys):
    """🔴 The OTHER reachable `.resolve()` — `collect_repos` de-duplicates
    `--repo` values by their resolved path, with NO `is_dir` gate in front of
    it, so a looped path typed on the command line reached `resolve()` directly
    and took `main()` down before one row was scanned.

    (`discover_repos`' resolve is guarded by `is_dir(p / ".git")`, which is
    already False under a loop, so its fallback is defensive rather than
    reachable — said here rather than implied by an absent test.)
    """
    assert wp.collect_repos(type("A", (), {
        "repo": [symlink_loop], "repos_from": None,
        "scan_root": [], "scan_depth": 2})()) == [Path(symlink_loop)]

    gh = gh_stub(tmp_path, [], name="gh-looped-repo")
    rc = wp.main(["--repo", symlink_loop, "--gh-cmd", gh, "--jobs", "1"])
    assert rc == wp.RC_OK, capsys.readouterr().err


def test_resolve_or_none_still_resolves_ordinary_paths(tmp_path):
    """The half of the helper that is TRUE ON EVERY INTERPRETER, and must never
    be behind a version guard. Swallowing everything would be indistinguishable
    from a helper that always returns None — which would silently disable the
    symlink-resolved candidate for EVERY path."""
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    assert wp._resolve_or_none(ordinary) == ordinary.resolve()


def test_resolve_or_none_never_raises_on_a_loop_on_any_interpreter(symlink_loop):
    """🔴 THE CONTRACT, stated at the width the helper actually guarantees: it
    does not RAISE. That holds on 3.12 (where it swallows a RuntimeError) and on
    3.13 (where `resolve()` never raises in the first place), so it is asserted
    unguarded — the interpreter-specific return VALUE is the next test's job."""
    wp._resolve_or_none(Path(symlink_loop))  # must not raise


def test_resolve_or_none_returns_none_for_a_loop_on_cpython_312(symlink_loop):
    """🔴 AN ASSERTION ABOUT CPython < 3.13, NOT ABOUT THE HELPER — guarded the
    same way the fixture's own control at the top of this section is, and for the
    same measured split (3.12.14 raises RuntimeError from a non-strict
    `resolve()`; 3.13.15 returns the path unresolved and never raises).

    🔴 It was NOT guarded for one round, which would have reddened the gate the
    moment the flake's python bumped — on a test whose NAME asserts
    `_resolve_or_none` is broken. The likely "fix" is deleting the helper that
    stops one looped symlink taking down an 860-row scan on 3.12, so the wrong
    thing gets removed to green a gate that was never about 3.13.

    On 3.13 the `None` never happens and the resolved candidate is simply the
    unresolved path — which is harmless: it is one of `_path_candidates`' forms
    already.
    """
    if sys.version_info >= (3, 13):
        pytest.skip("CPython >= 3.13 does not raise on a looped resolve(), so "
                    "_resolve_or_none returns the path rather than None. The wide `except` "
                    "is still required because this tool runs under whatever interpreter "
                    "the shell hands it.")
    assert wp._resolve_or_none(Path(symlink_loop)) is None


def test_a_row_whose_resolve_fails_is_cannot_tell_not_removable(tmp_path):
    """🔴 THE MECHANISM THAT ACTUALLY MAKES THE BEST-EFFORT RESOLVE SAFE, pinned
    rather than asserted in a comment — and for two rounds the comment named the
    WRONG one ("the conservative direction … a row is excluded LESS readily",
    which for an EXCLUSION filter is the deleting-MORE direction).

    Losing the resolved candidate genuinely does weaken the filter. What makes it
    moot is that the only paths that can lose it are paths whose ancestry cannot
    be walked, and NOTHING can stat such a path: `is_dir()` is False, so the row
    carries `path_exists=False`, and git cannot stat it either, so `git worktree
    list` flags it `prunable`. Both land `cannot-tell` in `classify()`, for a
    reason that has nothing to do with globs.

    🔴 MEASURED, not assumed, and the first draft of this test asserted the wrong
    one: on a real fixture BOTH signals are set and `prunable` is checked FIRST,
    so it is the blocker that actually appears. The disjunction below is asserted
    rather than the single branch the docstring in `_resolve_or_none` names,
    because which of the two wins is git's business — the invariant this pins is
    that the row is unremovable, and that both signals are present to make it so.

    Driven through the REAL scan, not `classify()` on a hand-built dict, because
    the claim is about what `scan_repo` produces for such a path.
    """
    repo = new_repo(tmp_path)
    commit_on_branch(repo, "feat/looped", "looped.txt", "looped\n", "looped work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/looped", "feat/looped")
    publish(repo)

    holder = tmp_path / "holder"
    holder.mkdir()
    looped = add_worktree(repo, holder / "wt", "feat/looped")
    gh = gh_stub(tmp_path, [], name="gh-loop-verdict")

    # POSITIVE CONTROL: intact, this row is dead AND removable — so the flip
    # below is caused by the loop and not by the fixture never being removable.
    before = _rows_by_path(repo, gh)[str(looped)]
    assert before["verdict"] == "dead", before["verdict_reason"]
    assert before["removable"] is True

    holder.rename(tmp_path / "holder-was-here")
    (tmp_path / "holder").symlink_to(tmp_path / "hop")
    (tmp_path / "hop").symlink_to(tmp_path / "holder")
    assert wp._resolve_or_none(Path(looped)) in (None, Path(looped)), (
        "the fixture no longer produces a path resolve() cannot walk")

    after = _rows_by_path(repo, gh)[str(looped)]
    # The signal the `_resolve_or_none` docstring names, asserted DIRECTLY so the
    # claim is pinned even though it is not the branch that fires first.
    assert after["path_exists"] is False, "is_dir() walked a loop it should not have"
    assert after["verdict"] == "cannot-tell", after["verdict_reason"]
    assert after["removable"] is False
    assert any("prunable" in b or "does not exist on disk" in b
               for b in after["blockers"]), after["blockers"]


def test_a_literal_path_with_fnmatch_metacharacters_needs_escaping():
    """Pinned rather than fixed, and stated in --help: `[v2]` is a character
    class, so a literal path containing one does not match itself.

    🔴 The escape helper is `glob.escape`, NOT `fnmatch.escape` — `fnmatch` has
    no `escape` at all. This test is the reason `--help` does not name a function
    that does not exist; asserting `hasattr` keeps it that way.
    """
    import glob as _glob
    assert not hasattr(fnmatch, "escape"), "fnmatch grew an escape(); --help should say so"
    weird = "/home/z/proj[v2]/wt"
    assert wp.path_excluded(weird, [weird]) is None
    escaped = _glob.escape(weird)
    assert escaped == "/home/z/proj[[]v2]/wt"
    assert wp.path_excluded(weird, [escaped]) == escaped


def test_the_help_names_an_escape_helper_that_actually_exists():
    """A `--help` that names a nonexistent function is worse than silence — the
    operator tries it, gets an AttributeError, and distrusts the rest."""
    import importlib
    text = wp.build_parser().format_help()
    named = [tok for tok in ("glob.escape", "fnmatch.escape") if tok in text]
    assert named == ["glob.escape"], named
    mod, _, attr = named[0].partition(".")
    assert hasattr(importlib.import_module(mod), attr)


def test_an_empty_glob_list_excludes_nothing():
    for globs in (None, [], [""]):
        assert wp.path_excluded("/home/z/.claude/worktrees/agent-1", globs) is None


def test_an_empty_glob_is_dropped_by_the_resolver():
    """The quiet half of the empty-glob guard (the CLI refuses one outright)."""
    assert wp.normalize_globs([""]) == []
    assert wp.normalize_globs(["   "]) == []
    assert wp.normalize_globs(["/"]) == []
    assert wp.normalize_globs([" / "]) == []
    assert wp.normalize_globs(["", "*/x/*", ""]) == ["*/x/*"]


def test_a_repeated_glob_is_not_double_counted():
    assert wp.normalize_globs(["*/x/*", "*/x/*"]) == ["*/x/*"]
    assert wp.normalize_globs(["*/x/*/", "*/x/*"]) == ["*/x/*"]
    assert wp.normalize_globs([" */x/* ", "*/x/*"]) == ["*/x/*"]


def test_an_empty_exclude_path_is_a_usage_error(two_dead, capsys):
    """🔴 Loud, not silent. An empty filter is a no-op filter, which is the exact
    class of failure this flag exists to prevent."""
    repo, agent, plain, gh = two_dead
    for bad in ("", "   ", "/"):
        assert wp.main(["--repo", str(repo), "--gh-cmd", gh, "--exclude-path", bad]) == wp.RC_USAGE
        assert "EMPTY glob" in capsys.readouterr().err


# ── 🔴 A GLOB THAT MATCHES NOTHING MUST NOT LOOK LIKE NO GLOB ────────────────
#
# Reproduced before the fix: a run with a mistyped glob and a run with no filter
# at all produced IDENTICAL output — same removable count, same zero column, no
# exclusion line. The operator then passes --confirm <that count>, and --confirm
# gives ZERO independent protection because it is derived from the same number.

def test_a_mistyped_glob_is_named_and_counted_not_silently_ignored(two_dead, capsys):
    repo, agent, plain, gh = two_dead
    typo = "*/.claude/wortrees/agent-*"
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--exclude-path", typo])
    assert rc == wp.RC_OK
    out = capsys.readouterr().out
    # Two filters: the typo, and the default agent exclusion.
    assert "exclusion filters in force (2):" in out, out
    assert f"--exclude-path {typo!r}  ->  matched 0 row(s)" in out, out
    assert "matched ZERO rows" in out, out
    assert "BYTE-IDENTICAL" in out, out


def test_the_report_of_a_mistyped_glob_differs_from_an_unfiltered_run(two_dead, capsys):
    """🔴 The exact comparison that failed before the fix, made mechanical: the
    two reports must not be the same bytes."""
    repo, agent, plain, gh = two_dead
    base = ["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"]
    wp.main(base)
    unfiltered = capsys.readouterr().out
    wp.main([*base, "--exclude-path", "*/.claude/wortrees/agent-*"])
    mistyped = capsys.readouterr().out
    assert mistyped != unfiltered, (
        "a mistyped glob produced output byte-identical to no filter at all")


def test_execute_refuses_while_a_TYPED_glob_matches_zero_rows(two_dead, capsys):
    """The warning is not enough on its own — it prints above a table the
    operator is skimming. This is the only check between a typo and deleting
    live sessions' working directories, so it REFUSES.

    🔴 TYPED, and the name now says so. It read "any glob" — round-2 residue from
    when the refusal really was scoped to every glob, which made the safety
    default refuse ITSELF (see `test_the_default_exclusion_matching_zero_rows_
    does_not_refuse`, the test that pins the opposite). A test name is a claim,
    and this one asserted the behaviour its own sibling forbids.
    """
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", "*/.claude/wortrees/agent-*",
                  "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert "matched ZERO" in err, err
    assert agent.is_dir() and plain.is_dir(), "a refused run removed something"


def test_a_working_glob_alongside_a_dud_still_refuses(two_dead, capsys):
    """PER-GLOB, not a total. A total is non-zero the moment one of two globs
    works, which is exactly how the broken one stays invisible."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB,
                  "--exclude-path", "*/nothing-here/*",
                  "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert "'*/nothing-here/*'" in err, err
    assert agent.is_dir() and plain.is_dir()


def test_a_glob_that_matches_does_not_trip_the_refusal(two_dead, capsys):
    """Negative control: the refusal must be caused by the zero, not by the
    presence of a glob. Without this, --execute could be refusing always."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB, "--execute", "--confirm", "1"])
    assert rc == wp.RC_OK, capsys.readouterr().err
    assert agent.is_dir() and not plain.exists()


# ── 🔴 …BUT THE REFUSAL MUST NOT BLOCK THE SAFETY DEFAULT ITSELF ─────────────
#
# Round 2 scoped the zero-match refusal to EVERY glob. The default agent
# exclusion is a glob, so on any repo with no `.claude/worktrees` directory the
# safety filter refused ITSELF — and the message's remedy ("fix the glob … or
# drop it") could only be satisfied by dropping the safety. That is a
# permanently-red gate whose click-through is the catastrophic action.

def test_the_default_exclusion_matching_zero_rows_does_not_refuse(no_agent_worktrees, capsys):
    """🔴 THE HEADLINE. One ordinary dead worktree, no `.claude/worktrees`
    anywhere: the default glob matches zero rows and the run must still fire."""
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_OK, err
    assert "REFUSED" not in err, err
    assert not plain.exists(), "the run refused, or removed nothing at all"


def test_the_default_exclusion_matching_zero_is_still_reported(no_agent_worktrees, capsys):
    """Not refusing is not the same as going quiet. The glob is still echoed
    with its zero — it just does not get the 🔴 typo shout, because a constant
    cannot be mistyped."""
    repo, plain, gh = no_agent_worktrees
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    out = capsys.readouterr().out
    assert f"{AGENT_GLOB!r}  ->  matched 0 row(s)" in out, out
    assert "matched ZERO rows" not in out, out


def test_a_typed_glob_matching_zero_still_refuses_in_the_same_scope(no_agent_worktrees, capsys):
    """🔴 THE CONTROL that makes the two tests above mean something. In the SAME
    zero-matching scope, a glob the OPERATOR typed still refuses — so the pass
    above is caused by the glob being a constant, not by the refusal having been
    deleted.

    Typing the agent glob by hand is the sharpest form: byte-identical pattern,
    byte-identical zero, opposite outcome. The only difference is who spelled it.
    """
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB, "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert "matched ZERO" in err, err
    assert plain.is_dir(), "a refused run removed something"


# ── 🔴 --allow-unmatched-globs: the OTHER half of the empty result ───────────
#
# A zero match cannot distinguish "mistyped" from "correct but out of THIS
# scan's scope". A fleet-wide exclude list aimed at a one-repo scan matches
# nothing and is entirely right; round 2 picked the dangerous reading and hard
# stopped, with no escape hatch.

def test_a_correct_fleet_wide_glob_on_a_narrow_scan_is_refused_without_the_flag(
        no_agent_worktrees, capsys):
    """The POSITIVE CONTROL on the pair below: without the flag this exact
    invocation refuses, so the pass below is caused by the flag."""
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB, "--exclude-path", "*/civitai/*",
                  "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert plain.is_dir()


def test_allow_unmatched_globs_warns_and_proceeds(no_agent_worktrees, capsys):
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB, "--exclude-path", "*/civitai/*",
                  "--allow-unmatched-globs", "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_OK, err
    assert "WARNING (--allow-unmatched-globs)" in err, err
    # Still NAMED, still PER-GLOB — the override downgrades the stop, it does
    # not make the zero-matchers invisible.
    assert repr(AGENT_GLOB) in err and "'*/civitai/*'" in err, err
    assert not plain.exists(), "the run did not actually proceed"


def test_the_override_is_echoed_prominently_in_the_report(no_agent_worktrees, capsys):
    """🔴 An override that is not visible in the output is a hardcode wearing a
    costume. Both halves: present when in force, absent when not."""
    repo, plain, gh = no_agent_worktrees
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--exclude-path", "*/civitai/*", "--allow-unmatched-globs"])
    out = capsys.readouterr().out
    assert "--allow-unmatched-globs IS IN FORCE" in out, out

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--exclude-path", "*/civitai/*"])
    assert "--allow-unmatched-globs IS IN FORCE" not in capsys.readouterr().out


def test_the_override_is_echoed_even_with_no_exclude_path_at_all(no_agent_worktrees, capsys):
    """🔴 A DESCRIPTION WIDER THAN ITS BODY. Both `--help` and the header claim
    the override "is echoed prominently in the report whenever it is in force" —
    but the echo lived inside `if summary.get("exclude_globs")`, and
    `--include-agent-worktrees` empties that list. So the one run with no glob of
    any kind was exactly the run where an override went silent.

    Nothing about the flag's behaviour changes here; the claim was simply true of
    the sentence and not of the code, which is the reading-as-coverage failure.
    """
    repo, plain, gh = no_agent_worktrees
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees", "--allow-unmatched-globs"])
    out = capsys.readouterr().out
    assert "exclusion filters in force" not in out, (
        "the fixture grew a glob, so this no longer exercises the empty-list case")
    assert "--allow-unmatched-globs IS IN FORCE" in out, out

    # NEGATIVE CONTROL: same empty-glob run without the flag stays quiet.
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--include-agent-worktrees"])
    assert "--allow-unmatched-globs IS IN FORCE" not in capsys.readouterr().out


def test_the_refusal_says_a_retyped_default_glob_is_free_to_drop(no_agent_worktrees, capsys):
    """🔴 EVERY REMEDY A REFUSAL OFFERS MUST BE VISIBLY SAFE. "Drop it" is one of
    the three, and for a hand-typed copy of the agent glob it costs nothing — the
    constant is applied to every run anyway. Unqualified, "drop it" reads as
    "drop your protection", which is the remedy phrasing that trained the
    click-through round 2 had to undo.

    🔴 …AND ONLY WHILE THE CONSTANT IS ACTUALLY APPLIED. With
    `--include-agent-worktrees` it is not, so the hand-typed copy is the only
    thing sparing agent worktrees on that command line and "drop it and lose
    nothing" advises the DELETING-MORE direction. Flag vs FACT, in a sentence
    that offers a remedy.

    🔴 BOTH SPELLINGS. The reassurance exists twice — once on stderr in the
    refusal, once in `render_text` on stdout — and round 4 shipped it with only
    the stderr copy asserted. Mutating the report copy's condition to `if False`
    gave a fully green suite: no test and no battery mutant could see it. It is
    the vehicle for the flag-in-force case below, so both are pinned here.
    """
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB, "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_EXECUTE_REFUSED, cap.err
    assert ("is applied by DEFAULT to every run, so dropping your hand-typed copy of it "
            "leaves it in force: the tool puts it back.") in cap.err, cap.err
    # …and the SECOND spelling, on stdout, which had zero coverage.
    assert "is applied by DEFAULT, so if this is a hand-typed copy of it you can " \
           "simply drop it and lose nothing" in cap.out, cap.out
    assert plain.is_dir(), "a refused run removed something"

    # NEGATIVE CONTROL: an ORDINARY dud glob gets no such reassurance — dropping
    # THAT one really does change what is spared.
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", "*/civitai/*", "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_EXECUTE_REFUSED, cap.err
    assert "applied by DEFAULT" not in cap.err, cap.err
    assert "lose nothing" not in cap.out, cap.out

    # 🔴 THE CASE THAT MAKES THE REASSURANCE FALSE, AND IN THE DELETING-MORE
    # DIRECTION. With `--include-agent-worktrees` the constant is NOT applied
    # (`resolve_exclude_globs`), so this hand-typed copy is the ONLY thing
    # sparing agent worktrees on this command line. Telling the operator it is
    # free to drop — twice, in a refusal they must get past — is advice that
    # turns 137 live sessions' working directories into removable rows.
    #
    # BEHAVIOURAL half first, so "the sentence is gone" cannot be confused with
    # "the sentence was always vacuous": with the glob, the agent tree is spared;
    # without it, it is removable. Measured on the resolver, which is the one
    # place that decides.
    assert wp.path_excluded("/x/.claude/worktrees/agent-1/y",
                            wp.resolve_exclude_globs([AGENT_GLOB], True)) == AGENT_GLOB
    assert wp.path_excluded("/x/.claude/worktrees/agent-1/y",
                            wp.resolve_exclude_globs([], True)) is None

    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--include-agent-worktrees", "--exclude-path", AGENT_GLOB,
                  "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_EXECUTE_REFUSED, cap.err
    assert "matched ZERO" in cap.err, cap.err
    assert "applied by DEFAULT" not in cap.err, cap.err
    assert "lose nothing" not in cap.out, cap.out
    assert plain.is_dir(), "a refused run removed something"
    # …and this is the run whose note claims exactly this: typed, therefore a
    # refusal candidate, which the tool's own implied copy never is.
    assert ("can REFUSE --execute on that glob, which the tool's own implied copy never "
            "could — dropping the flag does not change that, because the glob is already "
            "in the list either way.") in cap.out, cap.out


def test_the_free_to_drop_note_is_scoped_to_the_ONE_glob_it_is_true_of(
        no_agent_worktrees, capsys):
    """🔴 THE REFUSAL NAMES EVERY DUD, SO AN UNQUALIFIED PARENTHETICAL COVERS
    THEM ALL. "Drop it" is offered as a remedy for the whole list; saying "that
    one is applied by DEFAULT, dropping it costs nothing" without scoping reads
    as "dropping any of these costs nothing" — and for the ordinary dud it is
    false, in the direction that widens what gets removed.

    Pinned on a MIXED list, which is the only shape that can tell a per-glob
    note from a boolean one.

    🔴 AND THE CONTRAST HAS TO NAME THE REAL DISTINCTION. Round 5's scoping said
    "dropping your hand-typed copy of it changes nothing about what is spared;
    dropping '*/civitai/*' really would" — and `'*/civitai/*'` matched ZERO rows,
    which is what put it in this refusal at all. It spares nothing, so dropping
    it changes nothing about what is spared either, and the operator who believes
    otherwise keeps an inert filter and reaches for --allow-unmatched-globs to get
    past the refusal with a filter that filters nothing. The true distinction is
    AUTO-RESTORED vs not: `resolve_exclude_globs` puts the constant back, and
    puts nothing else back.

    🔴 PINNED AS A WHOLE NORMALISED STRING, not on keywords. The artifact under
    test is prose, and a guard on words is walkable by rewording — round 5's
    wording would have passed a `"applied by DEFAULT" in err` check. A cosmetic
    reword now fails this test; that is the price of a machine-readable claim.
    """
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", AGENT_GLOB, "--exclude-path", "*/civitai/*",
                  "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert (f"{AGENT_GLOB!r} — and ONLY that one — is applied by DEFAULT to every run, so "
            f"dropping your hand-typed copy of it leaves it in force: the tool puts it back. "
            f"Dropping '*/civitai/*' really removes it, from every future run too — though "
            f"none of the globs named in this refusal is sparing anything in THIS scan, "
            f"because every one of them matched zero rows.") in err, err
    # …and NOT the round-5 claim, which was false of BOTH globs named here.
    assert "really would" not in err, err
    assert "changes nothing about what is spared" not in err, err
    assert plain.is_dir(), "a refused run removed something"


def test_a_retyped_glob_with_the_flag_is_NOT_the_default_it_refuses(no_agent_worktrees, capsys):
    """🔴 THE MATRIX BEHIND THE `note:` LINE. Round 4's note claimed the run
    "behaves exactly like the default. The flag is doing nothing here." — printed
    on the stdout of a run whose stderr said REFUSED.

    Typing the glob converts the constant from an IMPLIED glob into a TYPED one,
    and only typed globs are `--execute` refusal candidates
    (`exclude_globs_blocking_execute`). Opposite exit code on the same scan — so
    the two command lines are NOT interchangeable, and the note may not say they
    are.

    🔴 WHAT THIS TEST DOES **NOT** COVER, said plainly, because the sentence used
    to claim it did. An earlier docstring read "same scan, same SPARED SET,
    opposite exit code" while the body checked only the exit codes — and on this
    fixture the spared set is EMPTY on both sides, so that half was vacuous and
    unassertable at once: the refusal exists precisely BECAUSE the glob matched
    zero rows, so no fixture can make this contrast refuse and spare something.
    The spared set is therefore asserted for what it is (empty, on both sides,
    and that emptiness is the mechanism), and the NON-VACUOUS spared-set contrast
    lives in `test_the_shout_is_gated_on_the_GLOB_not_on_the_flag`, where a real
    agent worktree survives an `--execute` run.

    Ordered refuse-then-default: the refusal must remove nothing, which is also
    what leaves the fixture intact for the default run to consume.
    """
    repo, plain, gh = no_agent_worktrees
    flagged = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                       "--include-agent-worktrees", "--exclude-path", AGENT_GLOB,
                       "--execute", "--confirm", "1"])
    flagged_cap = capsys.readouterr()
    assert flagged == wp.RC_EXECUTE_REFUSED, flagged_cap.err
    assert plain.is_dir(), "the refusal removed something"
    # The spared set, ASSERTED rather than assumed: zero on this side, and that
    # zero is what makes the typed glob a refusal candidate at all.
    assert f"{AGENT_GLOB!r}  ->  matched 0 row(s), 0 of them dead" in flagged_cap.out, \
        flagged_cap.out

    default = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                       "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert default == wp.RC_OK, cap.err
    # …and zero on the other side too, from the IMPLIED copy of the same glob —
    # same scan, same (empty) spared set, opposite exit code.
    assert f"{AGENT_GLOB!r}  ->  matched 0 row(s), 0 of them dead" in cap.out, cap.out
    assert "(default; --include-agent-worktrees turns it off)" in cap.out, cap.out
    assert not plain.exists(), "the default run removed nothing, so the contrast is vacuous"
    assert flagged != default


# ── 🔴 ROUND 6: ONE PREDICATE, ONE PLACE — AND THE CLAIMS THAT READ IT ───────
#
# Rounds 4 and 5 each exist because one copy of "is the agent constant applied by
# this tool on its own?" diverged from another. It was open-coded at THREE sites
# (`resolve_exclude_globs`, `render_text`, `main`). It is now computed once, by
# `agent_glob_applied_by_default`, carried in the summary, and read — never
# re-derived — by every message about it.

def test_the_note_does_not_claim_a_run_REFUSES_when_that_run_REMOVES(
        no_agent_worktrees, capsys):
    """🔴 THE ROUND-4 DEFECT, REINTRODUCED IN THE SENTENCE WRITTEN TO REPLACE IT.

    Round 5's note ended "…so on a scan holding no agent worktrees this run
    REFUSES where the default would have proceeded" — unconditional on
    `--allow-unmatched-globs`, which `main()` uses to downgrade exactly that
    refusal to a warning. MEASURED before the fix, hermetic tmpdir, all three
    flags plus `--execute --confirm 1`: rc 0 (not RC_EXECUTE_REFUSED), the
    worktree GONE from disk, and "this run REFUSES" on the stdout of that very
    run — three lines above the report's own "--allow-unmatched-globs IS IN
    FORCE: … it does not refuse --execute".

    The failure is not cosmetic: an operator who reads "REFUSES" treats the run
    as inert, stops re-reading `--confirm N`, and the run removes.

    🟡 AND THE REPLACEMENT SENTENCE CLAIMED REMOVAL PARITY IT DOES NOT HAVE.
    Round 6 wrote "…it does NOT refuse and this run removes exactly what the
    default would have removed". MEASURED on a one-repo/one-dead-worktree
    fixture, adding ONE more typed glob: `--include-agent-worktrees
    --exclude-path <agent glob> --exclude-path '*/wts/*' --allow-unmatched-globs
    --execute --confirm 0` -> rc 0, the dead worktree STILL PRESENT (removed 0),
    while the bare default `--execute --confirm 1` -> rc 0, worktree GONE
    (removed 1). The parity claim printed on the first run. The flag decides
    REFUSAL ELIGIBILITY; the glob list decides removals, so the sentence is now
    scoped to the refusal and says so. Under-deleting, so nothing was at risk —
    but it is a newly written false sentence about the run printing it, which is
    the entire subject of this PR. The parity case is exercised below.

    ORDERED control-then-live: the control must remove nothing, which is also
    what leaves the fixture intact for the live run to consume.
    """
    repo, plain, gh = no_agent_worktrees
    base = ["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
            "--include-agent-worktrees", "--exclude-path", AGENT_GLOB]

    # POSITIVE CONTROL: the same command line WITHOUT the override really does
    # refuse — so "REFUSE" is a claim the tool can legitimately make, and its
    # absence below is about the override and not about the sentence vanishing.
    rc = wp.main([*base, "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_EXECUTE_REFUSED, cap.err
    assert plain.is_dir(), "the control removed something"
    assert ("can REFUSE --execute on that glob, which the tool's own implied copy never "
            "could — dropping the flag does not change that, because the glob is already "
            "in the list either way.") in cap.out, cap.out
    # …and NOT round 7's replacement, which was false and structurally could not
    # be true — see `test_dropping_the_flag_cannot_change_whether_the_run_refuses`.
    assert "depends on the other globs you typed" not in cap.out, cap.out
    assert "WARNS where the default would have said nothing" not in cap.out, cap.out
    # 🔴 AND THE PREFIX THOSE ARMS HANG OFF, pinned as a WHOLE normalised string.
    # Every `differs` arm above is pinned in full; the shared prefix was not, and
    # `scripts/README.md` newly blesses the bounded claim it makes ("no more than")
    # while banning the EQUALITY shape. A guard on the arms alone is walkable by
    # rewording the head — which is the same defect, one clause to the left, that
    # rounds 6 and 7 each shipped. MEASURED (2026-08-27, hermetic copy, 3.12.14,
    # PYTHONDONTWRITEBYTECODE=1): rewriting this prefix to the banned equality
    # shape *with the word "bare" kept* — "…this run removes exactly what the bare
    # default would have removed." — SURVIVED the whole suite at 186 passed,
    # because the only guard was a word-level `not in` naming the wordING without
    # "bare". One word walked past it. The control: the same mutation WITHOUT
    # "bare" went red here, so the measurement can go red.
    assert ("note: --include-agent-worktrees was passed, but "
            f"{AGENT_GLOB!r} is in force anyway as a --exclude-path you typed — so "
            "agent worktrees are STILL spared and this run can remove no more than "
            "the bare default would. It is NOT identical to the default, though: a "
            "glob YOU typed is an --execute refusal candidate, so on a scan holding "
            "no agent worktrees this run ") in cap.out, cap.out
    # The banned shape is EQUALITY (README), in either spelling. "bare" is named
    # explicitly because it is the word the earlier guard did not see.
    assert "removes exactly what the bare default" not in cap.out, cap.out
    assert "removes exactly what the default" not in cap.out, cap.out

    # 🟡 THE PARITY-BREAKING RUN, FIRST, because it is the one whose sentence was
    # false: one MORE typed glob, matching the live row, so this command line
    # removes ZERO where the bare default removes ONE. It must not claim parity.
    rc = wp.main([*base, "--exclude-path", "*/wts/*", "--allow-unmatched-globs",
                  "--execute", "--confirm", "0"])
    cap = capsys.readouterr()
    assert rc == wp.RC_OK, cap.err
    # 🟢 THE MESSAGE MAY NOT CLAIM MORE THAN THE ASSERTION CHECKS. This used to
    # read "the extra glob did not spare the row, so the contrast is vacuous" —
    # wider than `is_dir()` can see: a `--confirm N` mismatch also returns
    # RC_EXECUTE_REFUSED and leaves the directory, so the directory surviving
    # cannot on its own tell "the glob spared it" from "confirm refused". What
    # rules that out is the `rc == RC_OK` above, asserted first; this one only
    # says the run did not remove.
    assert plain.is_dir(), "an RC_OK run removed the row, so the extra glob spared nothing"
    assert "removes exactly what the default would have removed" not in cap.out, cap.out
    assert ("That is a claim about the REFUSAL and nothing else") in cap.out, cap.out

    # …and with the override, on a run that REALLY REMOVES.
    rc = wp.main([*base, "--allow-unmatched-globs", "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_OK, cap.err
    assert not plain.exists(), "nothing was removed, so the note is not on a removing run"
    assert "can REFUSE --execute on that glob" not in cap.out, cap.out
    assert ("WARNS where the default would have said nothing — --allow-unmatched-globs is "
            "in force, so it does NOT refuse. That is a claim about the REFUSAL and nothing "
            "else: what gets removed is decided by the globs in force, so any other "
            "--exclude-path you typed can still spare rows the bare default would have "
            "removed.") in cap.out, cap.out
    # …and the line it used to contradict is still there, saying the same thing.
    assert "it does not refuse --execute" in cap.out, cap.out


#: Everything in the stderr refusal up to and including this substring is the
#: REASON — the dud list, the count, and the three remedies. Whatever follows is
#: the per-glob free-to-drop sentence, which is the only part the flag can move.
_REFUSAL_REASON_END = "--allow-unmatched-globs if it is correct but out of this scan's scope. "


def _refusal_reason(err: str) -> str:
    """The refusal's reason, sliced off the free-to-drop tail. Raises if absent,
    so a run that did not refuse cannot be compared as though it had."""
    i = err.index(_REFUSAL_REASON_END)
    return err[:i + len(_REFUSAL_REASON_END)]


def test_dropping_the_flag_cannot_change_whether_the_run_refuses(
        no_agent_worktrees, capsys, tmp_path):
    """🟡 ROUND 8 — THE MECHANISM UNDER THE `note:`, PINNED AS A RELATIONSHIP.

    Round 7 rewrote the note's `else` arm to "…though whether the same command
    line WITHOUT the flag also refuses depends on the other globs you typed".
    That is false, and structurally could not be true: the note only prints when
    `--include-agent-worktrees` was passed AND `AGENT_WORKTREE_GLOB` is a TYPED
    glob, and `resolve_exclude_globs` appends the constant only

        if agent_glob_applied_by_default(...) and AGENT_WORKTREE_GLOB not in globs

    — the second conjunct is already False on that command line, so dropping the
    flag skips an append that would have been a no-op. Nothing downstream can
    move.

    🟢 THIS IS AN INVARIANT GUARD, NOT A REGRESSION TEST, and is labelled as one:
    the code always behaved this way, the SENTENCE was the defect, and the
    sentence is pinned as a whole normalised string in the three tests that read
    it. What this adds is that the sentence's claim cannot become false without a
    test going red — a prose pin alone would keep asserting the words after the
    mechanism they describe had changed.

    Three shapes of "the other globs you typed" × {flag, no flag} = six command
    lines, each `--execute --confirm 1`. Asserted per shape:

      * the same exit code,
      * the same refusal REASON (dud list, count, remedies),
      * `--out` payloads BYTE-IDENTICAL once the only two keys the flag is
        allowed to move are removed — which covers the effective glob list, the
        rows, the per-glob counts, the `typed` set and
        `exclude_globs_blocking_execute` in one comparison,
      * and the one thing that DOES change, asserted rather than ignored: the
        free-to-drop sentence appears only WITHOUT the flag, where the constant
        really is auto-restored.

    The failure this closes is an operator's: hit the refusal, read that dropping
    the flag might clear it, drop it, refuse identically.
    """
    repo, plain, gh = no_agent_worktrees
    shapes = {
        "no second glob": [],
        # 🔴 Both kinds of "other glob", because the false sentence named them:
        # one that MATCHES a row (and so changes what would be removed) and one
        # that is a second DUD (and so changes the refusal's own dud list).
        # Neither can move the answer, and a single shape could not show that.
        "a second glob that MATCHES a row": ["--exclude-path", "*/wts/*"],
        "a second glob that is a DUD": ["--exclude-path", "*/civitai/*"],
    }
    for n, (label, extra) in enumerate(shapes.items()):
        sides = {}
        for side, flag in (("flag", ["--include-agent-worktrees"]), ("no-flag", [])):
            out = tmp_path / f"probe-{n}-{side}.json"
            rc = wp.main([*flag, "--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                          "--exclude-path", AGENT_GLOB, *extra,
                          "--out", str(out), "--execute", "--confirm", "1"])
            cap = capsys.readouterr()
            payload = json.loads(out.read_text(encoding="utf-8"))
            # The ONLY two keys the flag may move: the flag itself, and the fact
            # derived from it. Popped (so the comparison below covers everything
            # else) and asserted (so popping cannot hide a wrong value).
            assert payload["summary"].pop("agent_worktrees_included") is (side == "flag"), label
            assert payload["summary"].pop("agent_glob_applied_by_default") is (side != "flag"), \
                label
            sides[side] = (rc, cap.err, json.dumps(payload, sort_keys=True))

        assert sides["flag"][0] == sides["no-flag"][0] == wp.RC_EXECUTE_REFUSED, \
            f"{label}: {sides['flag'][0]} vs {sides['no-flag'][0]}"
        assert _refusal_reason(sides["flag"][1]) == _refusal_reason(sides["no-flag"][1]), label
        assert sides["flag"][2] == sides["no-flag"][2], label
        # …and the ONE thing the flag does move, named rather than left implicit:
        # without the flag the constant is auto-restored, so the refusal adds the
        # free-to-drop sentence. It changes the ADVICE, never the refusal.
        assert "is applied by DEFAULT to every run" not in sides["flag"][1], label
        assert "is applied by DEFAULT to every run" in sides["no-flag"][1], label
        assert plain.is_dir(), f"{label}: a refused run removed something"

    # 🔴 POSITIVE CONTROL on the comparison itself: a run that does NOT refuse
    # must be visibly different on every axis above, so "identical" is a finding
    # and not what this test says about any two runs. Same fixture, the typed
    # dud dropped — which is the remedy the refusal actually offers.
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_OK, cap.err
    assert _REFUSAL_REASON_END not in cap.err, cap.err
    assert not plain.exists(), "the control removed nothing, so the contrast is vacuous"


def test_the_zero_match_remedy_does_not_offer_a_flag_already_in_force(
        no_agent_worktrees, capsys):
    """🟢 SAME SHAPE, PRE-EXISTING, SAME PARAGRAPH. The per-glob zero-match line
    ended "--execute REFUSES while a typed glob is in this state — pass
    --allow-unmatched-globs if the glob is correct but out of this scan's scope"
    even when `--allow-unmatched-globs` was ALREADY in force and `--execute`
    therefore did not refuse. Advising the operator to pass a flag they already
    passed, on the stdout of a run that proceeded.

    Not in round 5's delta, but it is the same false-claim-about-this-very-run
    defect as the note above and it is fixed in the same round rather than left
    as the next round's finding.
    """
    repo, plain, gh = no_agent_worktrees
    base = ["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
            "--exclude-path", "*/civitai/*"]

    # POSITIVE CONTROL: without the override the remedy is the right one.
    rc = wp.main([*base, "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_EXECUTE_REFUSED, cap.err
    assert plain.is_dir(), "the control removed something"
    assert ("--execute REFUSES while a typed glob is in this state — pass "
            "--allow-unmatched-globs if the glob is correct but out of this scan's "
            "scope.") in cap.out, cap.out
    assert "ALREADY IN FORCE" not in cap.out, cap.out

    # …and with the override, on a run that REALLY REMOVES.
    rc = wp.main([*base, "--allow-unmatched-globs", "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_OK, cap.err
    assert not plain.exists(), "nothing was removed, so the remedy is not on a removing run"
    assert "--execute REFUSES while a typed glob is in this state" not in cap.out, cap.out
    assert ("--allow-unmatched-globs IS ALREADY IN FORCE, so --execute does NOT refuse on "
            "this — it proceeds with this glob filtering nothing.") in cap.out, cap.out


def test_a_messy_hand_typed_copy_of_the_default_still_gets_the_free_note(
        no_agent_worktrees, capsys):
    """🟢 THE INVARIANT THAT LICENSES DROPPING A `normalize_glob` CALL.

    Both copies of "is this glob the agent constant?" used to re-normalise `g`,
    on a value `resolve_exclude_globs` -> `normalize_globs` had already
    normalised. Harmless, but a mutant deleting the call would SURVIVE and no
    test claimed otherwise. The call is gone and the invariant is asserted
    BEHAVIOURALLY instead: a hand-typed copy with surrounding whitespace AND a
    trailing slash — the shape shell tab-completion and a fat-fingered paste
    actually produce — still reaches both messages as the bare constant.

    This is what would redden if normalisation moved, weakened, or changed order.
    """
    repo, plain, gh = no_agent_worktrees
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", f"  {AGENT_GLOB}/  ", "--execute", "--confirm", "1"])
    cap = capsys.readouterr()
    assert rc == wp.RC_EXECUTE_REFUSED, cap.err
    assert plain.is_dir(), "a refused run removed something"
    # ONE glob in the table, spelled as the bare constant — not two, and not the
    # messy spelling. Two would mean the typed copy failed to dedupe against the
    # constant the resolver appends.
    assert "exclusion filters in force (1):" in cap.out, cap.out
    assert f"--exclude-path {AGENT_GLOB!r}  ->  matched 0 row(s)" in cap.out, cap.out
    # …and BOTH reassurances fire, which is the thing the dropped call guarded.
    assert ("is applied by DEFAULT to every run, so dropping your hand-typed copy of it "
            "leaves it in force: the tool puts it back.") in cap.err, cap.err
    assert "simply drop it and lose nothing" in cap.out, cap.out
    # 🟡 …AND THE SOLO MESSAGE ENDS THERE. Every assertion on this sentence used
    # `in`, so appended text passed them all: mutating `if others:` to `if True:`
    # ran 183 passed while printing "…the tool puts it back. Dropping  really
    # removes it, from every future run too — though none of the globs named…" —
    # two contradicting sentences with an EMPTY glob list, on the stderr of an
    # --execute refusal. Pinning the JOIN (free note straight into "Nothing
    # removed.") is what makes the `others` clause's absence assertable.
    assert "the tool puts it back. Nothing removed." in cap.err, cap.err
    assert "really removes it" not in cap.err, cap.err


def test_summarize_separates_the_flag_from_the_two_facts_it_does_not_equal(two_dead):
    """🔴 THREE KEYS, THREE DIFFERENT QUESTIONS, and the whole PR's bug history is
    two of them being conflated:

      `agent_worktrees_included`      — was the FLAG typed?
      `agent_worktree_glob_in_force`  — is the constant in the effective list AT ALL?
      `agent_glob_applied_by_default` — is the TOOL applying it, i.e. can the
                                        operator drop their hand-typed copy for free?

    The retyped-glob command line is the row that separates all three: flag True,
    in force True, applied-by-default False. A summary that let any two of these
    collapse is how a 🔴 warning ended up contradicting the filter table three
    lines below it, and how "drop it, you lose nothing" ended up printed on the
    one command line where dropping it exposes every live session's worktree.
    """
    repo, agent, plain, gh = two_dead
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=[AGENT_GLOB])

    default_run = wp.summarize(rows, [AGENT_GLOB], [], agent_worktrees_included=False)
    assert default_run["agent_worktrees_included"] is False
    assert default_run["agent_worktree_glob_in_force"] is True
    assert default_run["agent_glob_applied_by_default"] is True

    # 🔴 THE SEPARATING ROW: every one of the three has a different value from at
    # least one other, so no pair can be silently equal.
    retyped = wp.summarize(rows, [AGENT_GLOB], [AGENT_GLOB], agent_worktrees_included=True)
    assert retyped["agent_worktrees_included"] is True
    assert retyped["agent_worktree_glob_in_force"] is True
    assert retyped["agent_glob_applied_by_default"] is False

    opted_out = wp.summarize(rows, [], [], agent_worktrees_included=True)
    assert opted_out["agent_worktrees_included"] is True
    assert opted_out["agent_worktree_glob_in_force"] is False
    assert opted_out["agent_glob_applied_by_default"] is False

    # …and the predicate itself, at the unit level, in both directions.
    assert wp.agent_glob_applied_by_default(False) is True
    assert wp.agent_glob_applied_by_default(True) is False


# ── the structural guard that stops a SEVENTH round of this class ────────────

_FLAG_SPELLINGS = ("include_agent_worktrees", "agent_worktrees_included")
_PREDICATE = "agent_glob_applied_by_default"

#: 🔴 EVERY reader of the consolidated fact, mapped to how many times it reads
#: it, asserted BOTH WAYS. It fails when the set GROWS — a new surface started
#: consuming the fact and nobody reviewed its wording — and when it SHRINKS — a
#: consumer quietly went back to deriving its own copy, which is the exact
#: regression rounds 4 and 5 were.
#:
#: Counted as `ast.Name` uses of the function plus `ast.Constant` strings equal
#: to the key EXACTLY, so prose mentioning the name in a docstring or comment
#: does not inflate it.
_PREDICATE_READERS = {
    # The site that MAKES it true: it appends the constant iff this is True.
    "resolve_exclude_globs": 1,
    # Computes the key (Name) and names it (dict key Constant).
    "summarize": 2,
    # The report's "you can simply drop it and lose nothing" parenthetical.
    "render_text": 1,
    # The stderr refusal's free-to-drop sentence.
    "main": 1,
}


def _owner_map(tree: ast.AST) -> dict:
    """id(node) -> the name of the innermost function enclosing it."""
    owner = {id(tree): "<module>"}

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            f = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            owner[id(child)] = f
            visit(child, f)

    visit(tree, "<module>")
    return owner


#: 🔴 THE SECOND LEDGER, and the one that catches what round 6's guard could not.
#: Every `if`/`while` in the tool whose TEST mentions a flag spelling, counted per
#: enclosing function and asserted EXACTLY.
#:
#: A statement-level branch on the flag is LEGITIMATE — `render_text` has two and
#: `resolve_exclude_globs` has one, all correct — so it cannot be banned the way
#: an expression conversion can. But a NEW one is a surface whose wording nobody
#: reviewed, and that is precisely the shape that walked past round 6's guard
#: fully green:
#:
#:     if summary.get("agent_worktrees_included"):
#:         free_to_drop = False
#:     else:
#:         free_to_drop = g == AGENT_WORKTREE_GLOB
#:
#: The reader ledger cannot see it either (the now-dead line above still reads
#: the key, so `render_text` stays at 1). Counting the branches does: 2 -> 3.
_FLAG_BRANCHES = {
    # `if agent_glob_applied_by_default(include_agent_worktrees) and …` — the
    # site that appends the constant. Reads the predicate; does not restate it.
    "resolve_exclude_globs": 1,
    # The loud opt-in shout (flag AND not-in-force) and the quiet retyped-glob
    # `note:` (flag, in force). Two branches, one `if`/`elif` chain.
    "render_text": 2,
}


def _flag_derivations(tree: ast.AST, src: str, owner: dict) -> list:
    """Nodes of five NAMED kinds whose operands mention a flag spelling.

    Returns `(node class name, enclosing function, source segment)`. The caller
    splits them by class:

      * `UnaryOp(Not)`, `Compare` against a bool `Constant` on EITHER side, and
        `IfExp` are EXPRESSION conversions of the flag into the boolean fact.
        Forbidden outside `agent_glob_applied_by_default`.
      * `If` and `While` are STATEMENT branches on the flag. Legitimate, so they
        are ledgered by count (`_FLAG_BRANCHES`) rather than banned.

    🔴 WHAT THIS WALKER CANNOT DO — stated because a stronger sentence stood here
    for a round and a re-derivation walked straight past it, fully green, guard
    and reader ledger included.

    WHICH ARM IS THE KILLER, WITH THE RECIPE THAT ACTUALLY REPRODUCES. Round 7
    wrote "delete the `If`/`While` arm below and the same insertion goes green
    again (185 passed)" — which does NOT reproduce as written, because
    `_CONTROL_HITS` still names `If` and `While` and the control then fails on
    itself. A recipe that does not reproduce is a claim like any other, so all
    four runs are given. Measured on a hermetic copy of tool + suite at 3.12.14,
    with this file's own test count as N (186 at the time of writing; re-derive
    it, do not trust the number):

      * the statement-form insertion alone .... 1 failed, N-1 passed, and the
        failure IS the branch ledger: `render_text: 3 != 2`. That is the killer.
      * the `If`/`While` arm deleted alone .... 1 failed, N-1 passed — but in the
        CONTROL's own assertion, `{'IfExp': 1, 'UnaryOp': 3, 'Compare': 2} !=
        {…'If': 1, 'While': 1}`. Not the ledger.
      * both together ......................... 1 failed, N-1 passed, still the
        control. So "delete the arm and it goes green" is two steps short.
      * both, PLUS `_CONTROL_HITS` reduced to the three expression kinds and
        `_FLAG_BRANCHES` emptied ............... N passed. Only here is the
        insertion invisible, which is what makes the arm the killer.

    THE SET OF WAYS TO SPELL "INVERT THIS FLAG" IS OPEN AND THIS KNOWS FIVE NODE
    KINDS. MEASURED against this walker, one spelling per line, all INVISIBLE to
    it: `operator.not_(flag)`, `flag ^ True`, `{True: False, False: True}[flag]`,
    `bool(1 - flag)`, `flag.__eq__(False)` — and so is any helper that takes the
    flag and returns the fact. (`next(iter([]) if flag else …)` is NOT in that
    list: it was, and the same measurement showed the walker SEES it, via the
    `IfExp`. A docstring claiming a blind spot the code does not have is the same
    defect as one claiming coverage it does not have.)

    🔴 AND THE CHEAPEST WALK-PAST IS NOT A HELPER AND NOT AN EXOTIC OPERATOR — it
    is a ONE-LINE LOCAL ALIAS, which is far likelier than any of the five above.
    Three more, each measured the same way — appended after the real assignment
    (so the reader ledger stays at 1, exactly like the round-7 statement-form
    mutant), each recomputing the SAME value, so each is behaviourally inert:

        included = summary.get("agent_worktrees_included")     # N passed
        if included:                    # the `If` test is `Name`, not the flag,
            free_to_drop = False        # so the branch ledger cannot see it
        else:
            free_to_drop = g == AGENT_WORKTREE_GLOB

        match summary.get("agent_worktrees_included"):         # N passed
            case True: ...                # `ast.Match` is not one of the five

        free_to_drop = (g == AGENT_WORKTREE_GLOB               # N passed
                        and summary.get("agent_worktrees_included") != 1)
                        # `Compare` against a NON-bool constant: `isinstance(1,
                        # bool)` is False, so the `Compare` arm `continue`s

    POSITIVE CONTROL on that measurement, run identically: the `IfExp` spelling
    `False if summary.get("agent_worktrees_included") else g == AGENT_WORKTREE_GLOB`
    is 1 failed, N-1 passed, killed by
    `test_the_agent_default_predicate_has_exactly_one_definition`. So the three
    N-passed results are about the walker's blindness, not about a measurement
    wired to nothing.

    So: this is a TRIPWIRE FOR THE LIKELY SPELLINGS — the ones rounds 4 and 5
    actually used, plus the statement form that beat round 6 — and NOT a proof
    that the predicate has exactly one definition. Nothing here licenses skipping
    review of a diff that touches the flag. The narrower claim is deliberate: the
    docstring is written from what the code below does, not from what the guard
    was hoped to be.
    """
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            probes = [node.operand]
        elif isinstance(node, ast.Compare):
            # 🔴 BOTH SIDES. Round 6 probed `node.left` only, so the mirror
            # spelling `False == flag` — same meaning, same hazard — was invisible.
            operands = [node.left, *node.comparators]
            def _is_bool(c):
                return isinstance(c, ast.Constant) and isinstance(c.value, bool)
            if not any(_is_bool(c) for c in operands):
                continue
            probes = [c for c in operands if not _is_bool(c)]
        elif isinstance(node, ast.IfExp):
            probes = [node.test]
        elif isinstance(node, (ast.If, ast.While)):
            probes = [node.test]
        else:
            continue
        if any(f in ast.dump(p) for p in probes for f in _FLAG_SPELLINGS):
            hits.append((type(node).__name__, owner.get(id(node), "?"),
                         ast.get_source_segment(src, node)))
    return hits


#: The exact per-node-class tally the positive control below MUST produce.
#:
#: 🟢 EXACT, NOT `>= n`. Round 6's control built three lines yielding FOUR hits
#: and asserted `>= 3`. RE-MEASURED on that exact control, detector by detector:
#: none dropped -> 4 (pass); `Compare` dropped -> 3 (pass); `IfExp` dropped -> 3
#: (pass); `Not` dropped -> 2 (fail). So a control advertising three kinds
#: actually proved one, and it had a full hit of slack. That is the
#: same "description wider than body" defect as the docstring above, in the
#: instrument meant to validate it. Per-class and exact: removing ANY ONE of the
#: five detectors now moves a number in this dict.
_CONTROL_HITS = {"UnaryOp": 3, "Compare": 2, "IfExp": 1, "If": 1, "While": 1}


def test_the_agent_default_predicate_has_exactly_one_definition():
    """🔴 THE POINT OF ROUND 6. "Is the agent constant applied by this tool on its
    own?" was open-coded at THREE sites, and rounds 4 and 5 are each one copy
    diverging from another: round 4 gated a message on the FLAG instead of the
    FACT, round 5 fixed two spellings and introduced two fresh false claims in
    the replacements.

    `claude/RULES.md` → "One rule, one place": a predicate open-coded at N sites
    is typically wrong at N−1 of them in the same direction, and the only thing
    that keeps N at 1 is a test that fails when it becomes 2. Consolidating
    without pinning it just resets the clock for round 7.

    Three halves — the second and third are the seam, and the third exists
    because the second was not enough:
      * NO function other than `agent_glob_applied_by_default` may derive the
        fact from the flag AS AN EXPRESSION — checked structurally over five
        named node kinds, so a rewording WITHIN those kinds cannot walk past it.
        🔴 A spelling OUTSIDE them still can: see `_flag_derivations` for the
        list. This is a tripwire for the likely spellings, NOT a proof.
      * every `if`/`while` branching on the flag is LEDGERED BY COUNT per
        function. A branch is legitimate, so it cannot be banned — but a NEW one
        is a surface nobody reviewed, and the statement form
        `if flag: free = False else: free = g == CONST` inserted into
        `render_text` ran GREEN against round 6's guard, reader ledger included
        (the dead line above it still reads the key, so that count never moves).
        MEASURED here: with the branch ledger in place the insertion is RED and
        the failure names `render_text: 3 != 2`; with the `If`/`While` arm of
        `_flag_derivations` removed AND both ledgers adjusted to match
        (`_CONTROL_HITS` down to the three expression kinds, `_FLAG_BRANCHES`
        emptied) it is green again. Removing the arm alone is red in the
        CONTROL's own assertion, not in the ledger — the four runs are tabulated
        in `_flag_derivations`, because the two-step version of this recipe does
        not reproduce.
      * the READER LEDGER is asserted in BOTH directions, so a new consumer or a
        vanished one both fail.
    """
    src = TOOL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    owner = _owner_map(tree)

    # 🔴 POSITIVE CONTROL FIRST. A walker wired to nothing reports a reassuring
    # empty list, indistinguishable from a clean tree. Every kind it advertises
    # is exercised, and the tally is asserted PER KIND — see `_CONTROL_HITS`.
    control_src = (
        "def render_text(summary):\n"
        "    a = (1 if not summary.get('agent_worktrees_included') else 2)\n"
        "    b = not args.include_agent_worktrees\n"
        "    c = args.include_agent_worktrees is False\n"
        "    d = False == args.include_agent_worktrees\n"
        "    if summary.get('agent_worktrees_included'):\n"
        "        e = 1\n"
        "    else:\n"
        "        e = 2\n"
        "    while not args.include_agent_worktrees:\n"
        "        break\n")
    control_tree = ast.parse(control_src)
    control_hits = _flag_derivations(control_tree, control_src, _owner_map(control_tree))
    by_kind: dict = {}
    for kind, _, _ in control_hits:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    assert by_kind == _CONTROL_HITS, (
        f"the walker does not see every kind it claims to, so its zeros are "
        f"meaningless for the kinds it missed: {by_kind} != {_CONTROL_HITS} "
        f"({control_hits})")

    hits = _flag_derivations(tree, src, owner)
    exprs = [(fn, seg) for kind, fn, seg in hits
             if kind in ("UnaryOp", "Compare", "IfExp")]
    offenders = [(fn, seg) for fn, seg in exprs if fn != _PREDICATE]
    assert not offenders, (
        "the agent-default predicate is being re-derived outside "
        f"`{_PREDICATE}`: {offenders}. Read summary['{_PREDICATE}'] instead — a "
        "second copy is how rounds 4 and 5 happened.")
    # …and the one definition really is there. Without this the assertion above
    # passes just as well on a tree where the predicate was deleted outright.
    assert [fn for fn, _ in exprs] == [_PREDICATE]

    branches: dict = {}
    for kind, fn, _ in hits:
        if kind in ("If", "While"):
            branches[fn] = branches.get(fn, 0) + 1
    assert branches == _FLAG_BRANCHES, (
        f"the flag-branch ledger moved: {branches} != {_FLAG_BRANCHES}. A NEW "
        "if/while on the flag is a message surface whose wording nobody "
        "reviewed — round 6's guard was green against exactly that shape. A "
        "vanished one means a branch that used to exist was reworded into "
        "something this walker cannot see. Either way, read the diff.")

    readers: dict = {}
    for node in ast.walk(tree):
        if ((isinstance(node, ast.Name) and node.id == _PREDICATE)
                or (isinstance(node, ast.Constant) and node.value == _PREDICATE)):
            fn = owner.get(id(node), "?")
            readers[fn] = readers.get(fn, 0) + 1
    assert readers == _PREDICATE_READERS, (
        f"the reader ledger moved: {readers} != {_PREDICATE_READERS}. A new reader "
        "means a new surface consuming the fact whose wording nobody reviewed; a "
        "vanished one means a consumer went back to its own copy.")


def test_the_override_does_not_change_which_rows_are_removable(no_agent_worktrees, capsys):
    """🔴 The flag relaxes ONE refusal and nothing else. If it also widened the
    removable set it would be a second, silent behaviour change riding on a
    diagnostics flag."""
    repo, plain, gh = no_agent_worktrees
    base = ["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
            "--exclude-path", "*/civitai/*", "--format", "json"]
    wp.main(base)
    without = json.loads(capsys.readouterr().out)
    wp.main([*base, "--allow-unmatched-globs"])
    with_flag = json.loads(capsys.readouterr().out)
    assert without["summary"]["removable"] == with_flag["summary"]["removable"]
    assert ([r["removable"] for r in without["rows"]]
            == [r["removable"] for r in with_flag["rows"]])
    assert without["summary"]["allow_unmatched_globs"] is False
    assert with_flag["summary"]["allow_unmatched_globs"] is True


# ── 🔴 A SCAN THAT FOUND NOTHING SAYS NOTHING ABOUT THE GLOB ─────────────────

def test_an_empty_scan_with_confirm_zero_is_a_no_op_not_a_refusal(tmp_path, capsys):
    """🔴 The regression round 2 introduced. With ZERO rows scanned, no glob
    could have matched anything — the zero is evidence about the SCAN, not about
    the glob — and `--execute --confirm 0` used to succeed as a no-op. It began
    refusing instead.
    """
    gh = gh_stub(tmp_path, [], name="gh-empty")
    rc = wp.main(["--repo", str(tmp_path / "no-such-repo"), "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", "*/civitai/*", "--execute", "--confirm", "0"])
    err = capsys.readouterr().err
    assert rc == wp.RC_OK, err
    assert "REFUSED" not in err, err
    assert "NO rows at all" in err, err


def test_an_empty_scan_without_any_glob_was_and_stays_a_no_op(tmp_path, capsys):
    """The control: the same empty scope with no typed glob succeeded before and
    must still, so the test above is about the glob and not about empty scopes
    being broken in general."""
    gh = gh_stub(tmp_path, [], name="gh-empty2")
    rc = wp.main(["--repo", str(tmp_path / "no-such-repo"), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "0"])
    assert rc == wp.RC_OK, capsys.readouterr().err


def test_a_zero_row_scan_does_not_print_that_execute_refuses(tmp_path, capsys):
    """🟡 THE THIRD SCOPING, MISSED BY THE ROUND-6 REMEDY.

    `main()` declines to refuse on a typed dud for THREE reasons — typed-globs
    only, `--allow-unmatched-globs`, and NOT WHEN THE SCAN PRODUCED NO ROWS — and
    round 6 made the report's remedy and its retyped-glob `note:` conditional on
    the SECOND of those, not the third. MEASURED before this fix, hermetic
    tmpdir:

        --repo <nonexistent> --exclude-path '*/civitai/*' --execute --confirm 0
          rc = 0                                        (the run PROCEEDED)
          stderr: "…matched zero rows, but this scan produced NO rows at all
                   … Not refusing."
          stdout: "--execute REFUSES while a typed glob is in this state — pass
                   --allow-unmatched-globs …"

    …and the same shape in the `note:` with `--include-agent-worktrees
    --exclude-path '*/.claude/worktrees/*'`: "…this run REFUSES" on the stdout of
    a run that returned RC_OK. Bounded — zero rows means zero removals are
    possible — but it is the exact class rounds 4-6 exist to close, and
    `scripts/README.md` stated the pair of conditionals as if it were closed.

    Both message sites, one test, asserted as whole normalised strings.
    """
    empty_repo = str(tmp_path / "no-such-repo")

    # (a) the per-glob zero-match remedy, on an ORDINARY typed dud.
    gh = gh_stub(tmp_path, [], name="gh-zero-remedy")
    rc = wp.main(["--repo", empty_repo, "--gh-cmd", gh, "--jobs", "1",
                  "--exclude-path", "*/civitai/*", "--execute", "--confirm", "0"])
    cap = capsys.readouterr()
    assert rc == wp.RC_OK, cap.err
    assert "Not refusing." in cap.err, cap.err
    assert "REFUSE" not in cap.out, cap.out
    assert ("This scan produced NO rows at all, so the zero says nothing about the glob and "
            "--execute does NOT refuse on it here — re-run against a scope that actually "
            "holds worktrees before trusting this filter.") in cap.out, cap.out

    # (b) the retyped-glob `note:`, which has the identical hole.
    gh = gh_stub(tmp_path, [], name="gh-zero-note")
    rc = wp.main(["--repo", empty_repo, "--gh-cmd", gh, "--jobs", "1",
                  "--include-agent-worktrees", "--exclude-path", AGENT_GLOB,
                  "--execute", "--confirm", "0"])
    cap = capsys.readouterr()
    assert rc == wp.RC_OK, cap.err
    assert "Not refusing." in cap.err, cap.err
    assert "REFUSE" not in cap.out, cap.out
    assert ("this run does NEITHER: this scan produced no rows at all, so nothing could have "
            "matched, the zero says nothing about the glob, and --execute refuses on neither "
            "command line.") in cap.out, cap.out


def test_summarize_will_not_guess_whether_the_flag_was_passed():
    """🟢 A DEFAULT THAT CAN ONLY EVER BE WRONG WHEN IT IS USED.

    `agent_worktrees_included` used to default to False while the glob list came
    from `resolve_exclude_globs(patterns, include_agent_worktrees)` — two places
    to say the same thing, one of them silent. Omitting it after opting the flag
    ON produced:

        wp.summarize([], wp.resolve_exclude_globs([], True), [])
          agent_worktree_glob_in_force   = False
          agent_glob_applied_by_default  = True

    …a summary asserting the tool applies a constant that is not in the list.
    Unreachable from a command line (`main()` always passed it), but the README
    publishes this JSON for consumers, and the whole subject of rounds 4-6 is
    these three keys disagreeing. It is now required and keyword-only.
    """
    with pytest.raises(TypeError):
        wp.summarize([], wp.resolve_exclude_globs([], True), [])
    # …and the consistent shape, when the caller says what it did.
    s = wp.summarize([], wp.resolve_exclude_globs([], True), [],
                     agent_worktrees_included=True)
    assert s["agent_worktree_glob_in_force"] is False
    assert s["agent_glob_applied_by_default"] is False
    # NEGATIVE CONTROL on the pair: with the flag OFF both are True, so the
    # assertion above is about the flag and not about `[]` making everything False.
    s = wp.summarize([], wp.resolve_exclude_globs([], False), [],
                     agent_worktrees_included=False)
    assert s["agent_worktree_glob_in_force"] is True
    assert s["agent_glob_applied_by_default"] is True


def test_per_glob_counts_are_independent_of_shadowing(two_dead):
    """A working glob listed AFTER a broader one still reports its own matches.
    Tallying `excluded_by` (which records only the FIRST match) would report zero
    and raise a false alarm — and a false alarm is how a real one gets ignored."""
    repo, agent, plain, gh = two_dead
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1,
                        exclude_globs=[AGENT_GLOB, "*/agent-7f3a91"])
    s = wp.summarize(rows, [AGENT_GLOB, "*/agent-7f3a91"], agent_worktrees_included=False)
    assert [d["glob"] for d in s["exclude_globs"]] == [AGENT_GLOB, "*/agent-7f3a91"]
    assert s["exclude_globs"][0]["matched"] == 1
    assert s["exclude_globs"][1]["matched"] == 1, (
        "the shadowed glob was counted from excluded_by and reported a false zero")
    assert s["exclude_globs_matching_nothing"] == []
    # The row itself still records the FIRST glob, unchanged.
    assert {r["path"]: r["excluded_by"] for r in rows}[str(agent)] == AGENT_GLOB


def test_the_summary_shape_does_not_depend_on_the_caller():
    """`exclude_globs` used to be bolted on by main() after summarize(), so a
    direct caller got a dict with a different shape."""
    keys_none = set(wp.summarize([], agent_worktrees_included=False))
    keys_globs = set(wp.summarize([], ["*/x/*"], agent_worktrees_included=False))
    assert keys_none == keys_globs
    assert {"exclude_globs", "exclude_globs_matching_nothing"} <= keys_none
    assert wp.summarize([], agent_worktrees_included=False)["exclude_globs"] == []


def test_the_first_matching_glob_is_the_one_reported():
    """The row records WHICH glob spared it; an operator debugging an
    over-broad filter needs the answer, not a bool."""
    assert wp.path_excluded("/a/b/c", ["/nope/*", "/a/*", "/a/b/*"]) == "/a/*"


def test_the_credited_glob_is_glob_major_not_candidate_major():
    """🔴 The mutant this pins SURVIVED all 132 tests: swapping the loop nesting
    in `path_excluded` from glob-major to candidate-major.

    The test above cannot see it. Every glob there matches the path DIRECTLY, so
    the candidate loop's first iteration settles it either way and the two
    nestings agree. The disagreement only appears when an EARLIER glob matches
    an ANCESTOR while a LATER one matches the path itself:

        glob-major      -> '/a/b'   (first glob in `globs` that matches anything)
        candidate-major -> '*/c'    (first candidate's match, i.e. the path's)

    `excluded_by` is what the report shows the operator as the reason a row was
    spared, so the credited glob is a claim about WHICH filter did it. Per-glob
    match counts are computed one glob at a time and are unaffected — verified,
    and asserted below so this test does not overstate its own scope.
    """
    assert wp.path_excluded("/a/b/c", ["/a/b", "*/c"]) == "/a/b"
    # The mirror: reversing the globs reverses the answer, so this is about
    # ORDER and not about one of the two patterns being preferred.
    assert wp.path_excluded("/a/b/c", ["*/c", "/a/b"]) == "*/c"
    # Ancestor-vs-self at a deeper level, so it is not a two-component special
    # case either.
    assert wp.path_excluded("/x/y/z/w", ["/x/y", "*/w"]) == "/x/y"

    # …and the scope disclaimer above, made mechanical: per-glob counts do not
    # move with the ordering.
    rows = [{"path": "/a/b/c", "verdict": wp.DEAD, "repo": "/a", "excluded_by": "/a/b"}]
    for order in (["/a/b", "*/c"], ["*/c", "/a/b"]):
        counts = {d["glob"]: d["matched"]
                  for d in wp.summarize(
                      rows, order, agent_worktrees_included=False)["exclude_globs"]}
        assert counts == {"/a/b": 1, "*/c": 1}, order


def test_multiple_exclude_paths_all_apply(two_dead):
    repo, agent, plain, gh = two_dead
    rows = _rows_by_path(repo, gh, ["*/nothing-matches-this/*", AGENT_GLOB])
    assert rows[str(agent)]["excluded_by"] == AGENT_GLOB
    assert rows[str(agent)]["removable"] is False


# ── ONE RULE, ONE PLACE: the default IS the glob ──────────────────────────────

def test_the_default_resolves_to_exactly_the_documented_glob():
    """🔴 The default is ON. `include_agent_worktrees=True` is the ONLY thing
    that takes the constant out of the list — there is no flag that puts it in,
    because it is not absent."""
    assert wp.AGENT_WORKTREE_GLOB == AGENT_GLOB
    assert wp.resolve_exclude_globs([], False) == [wp.AGENT_WORKTREE_GLOB]
    assert wp.resolve_exclude_globs(["*/x/*"], False) == ["*/x/*", wp.AGENT_WORKTREE_GLOB]
    # Opted back in: the constant is gone and only typed globs remain.
    assert wp.resolve_exclude_globs([], True) == []
    assert wp.resolve_exclude_globs(["*/x/*"], True) == ["*/x/*"]
    # Spelling it by hand must not double it up.
    assert wp.resolve_exclude_globs([AGENT_GLOB], False) == [AGENT_GLOB]
    # …and typing it explicitly OVERRIDES the opt-in, because a typed glob is a
    # typed glob. The operator asked for both; the filter wins, as everywhere
    # else in this tool.
    assert wp.resolve_exclude_globs([AGENT_GLOB], True) == [AGENT_GLOB]


def test_the_default_and_the_explicit_glob_produce_identical_rows(two_dead, capsys):
    """Behavioural, not structural: the two routes must agree row for row. A
    second open-coded matcher would pass a `==` on the constant and still drift
    here."""
    repo, agent, plain, gh = two_dead
    a = _rows_by_path(repo, gh, wp.resolve_exclude_globs([], False))
    b = _rows_by_path(repo, gh, [AGENT_GLOB])
    key = lambda rs: {p: (r["verdict"], r["excluded_by"], r["removable"])
                      for p, r in rs.items()}
    assert key(a) == key(b)
    assert key(a)[str(agent)] == ("dead", AGENT_GLOB, False)


def test_the_agent_glob_literal_is_spelled_exactly_once_in_executable_code():
    """Structural backstop for the one-rule-one-place claim. Both
    `--include-agent-worktrees`' help and the report's shout interpolate the
    CONSTANT, so a second occurrence of the literal means somebody re-typed the
    pattern."""
    literals = _executable_string_literals(TOOL)
    assert literals.count(AGENT_GLOB) == 1, (
        f"the agent glob is spelled {literals.count(AGENT_GLOB)} times in executable "
        "code — the convenience flag must reuse AGENT_WORKTREE_GLOB, not a copy")


# ── the executor's own floor ──────────────────────────────────────────────────

def test_execute_refuses_an_excluded_row_even_if_removable_was_forced(two_dead, capsys):
    """Defence in depth, matching the verdict re-assert next to it: the executor
    does not trust the `removable` flag it was handed."""
    repo, agent, plain, gh = two_dead
    rows = [r for r in wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=[AGENT_GLOB])
            if r["path"] == str(agent)]
    assert rows[0]["excluded_by"] == AGENT_GLOB
    rows[0]["removable"] = True
    assert wp.execute_removals(rows, 1) == wp.RC_OK
    assert agent.is_dir()
    assert "excluded by path filter" in capsys.readouterr().err


def test_a_full_execute_pass_over_the_scanned_rows_never_touches_an_excluded_path(two_dead, capsys):
    """🔴 THE SEAM TEST. `--confirm` and the exclusion are two guards in series,
    and a mutation sweep showed the confirm mismatch killing every end-to-end
    test FIRST — which leaves the `is_dir()` assertion, the one that actually
    says "we did not delete someone's working directory", UNREACHABLE under a
    mutant that lets the excluded row into the removable set.

    So this one asks the tool how many rows it intends to remove and passes that
    number back, taking --confirm out of the picture. The disk assertion then
    runs no matter which of the two exclusion floors is broken.
    """
    repo, agent, plain, gh = two_dead
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=[AGENT_GLOB])
    n = sum(1 for r in rows if r.get("removable"))
    assert wp.execute_removals(rows, n) == wp.RC_OK, capsys.readouterr().err
    assert agent.is_dir(), "an excluded worktree was removed by a full execute pass"
    assert (agent / "agentwork.txt").is_file()
    assert not plain.exists(), (
        "the non-excluded dead worktree survived too — this pass removed nothing, "
        "so the survival above says nothing about the exclusion")


def test_the_default_spares_a_non_agent_entry_end_to_end(tmp_path, capsys):
    """🔴 The no-`agent-`-prefix case, driven all the way through --execute.

    A real registered worktree at `.claude/worktrees/card-ux` — no `agent-`
    prefix, squash-merged so genuinely `dead`, and CLEAN, so the dirty check that
    happened to be sparing the real one on this box cannot be what spares it
    here. It must survive `--execute` WITH NO FLAG TYPED while an ordinary dead
    worktree in the same run is really removed.
    """
    repo = new_repo(tmp_path)
    for name, rel in (("feat/card-ux", "cardux.txt"), ("feat/agenty", "agenty.txt")):
        git(repo, "checkout", "-q", "-b", name)
        write(repo / rel, "one\n")
        git(repo, "add", rel)
        git(repo, "commit", "-qm", f"{name} part 1")
        write(repo / rel, "one\ntwo\n")
        git(repo, "add", rel)
        git(repo, "commit", "-qm", f"{name} part 2")
        git(repo, "checkout", "-q", "main")
        squash_merge(repo, name, rel, f"squash {name}")
    commit_on_branch(repo, "feat/ordinary", "ord.txt", "ord\n", "ordinary work")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat/ordinary", "feat/ordinary")
    publish(repo)

    card = add_worktree(repo, repo / ".claude" / "worktrees" / "card-ux", "feat/card-ux")
    agenty = add_worktree(repo, repo / ".claude" / "worktrees" / "agent-deadbeef", "feat/agenty")
    ordinary = add_worktree(repo, tmp_path / "wts" / "ordinary", "feat/ordinary")
    gh = gh_stub(tmp_path, [], name="gh-cardux")

    # Positive control: unfiltered, ALL THREE are removable — including the
    # non-agent one, and it is not being spared by dirt.
    rows = _rows_by_path(repo, gh)
    for p in (card, agenty, ordinary):
        assert rows[str(p)]["verdict"] == "dead", (p, rows[str(p)]["verdict_reason"])
        assert rows[str(p)]["removable"] is True, p
        assert rows[str(p)]["dirty"] is False, (p, "the fixture is spared by dirt, not by the flag")

    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "1"])
    err = capsys.readouterr().err
    assert rc == wp.RC_OK, err
    assert card.is_dir(), "the non-agent .claude/worktrees entry was REMOVED"
    assert agenty.is_dir(), "the agent worktree was removed"
    assert not ordinary.exists(), "the ordinary dead worktree was not removed"
    assert "removed=1" in err, err


def test_an_excluded_row_is_unremovable_whatever_its_verdict():
    for kw in ({"landed_signals": ["ancestor"]}, {}, {"dirty": True}, {"is_main": True}):
        r = wp.classify(base_row(excluded_by=AGENT_GLOB, **kw))
        assert r["removable"] is False, (kw, r["verdict"])
        assert any("exclude" in b for b in r["blockers"]), r["blockers"]


def test_exclusion_does_not_change_the_verdict_itself():
    """The mirror of the test above: sparing a row must not relabel it."""
    for kw in ({"landed_signals": ["ancestor"]}, {}, {"dirty": True}):
        plain = wp.classify(base_row(**kw))["verdict"]
        excl = wp.classify(base_row(excluded_by=AGENT_GLOB, **kw))["verdict"]
        assert plain == excl, (kw, plain, excl)


def test_verdict_label_marks_only_excluded_rows():
    assert wp.verdict_label({"verdict": "dead"}) == "dead"
    assert wp.verdict_label({"verdict": "dead", "excluded_by": None}) == "dead"
    assert wp.verdict_label({"verdict": "dead", "excluded_by": AGENT_GLOB}) == "dead (excluded)"
    assert wp.verdict_label({"verdict": "live", "excluded_by": AGENT_GLOB}) == "live (excluded)"


def test_the_help_text_documents_the_new_flags_and_the_glob_semantics():
    """`--help` is the only documentation most operators will read, and the
    slash-crossing choice is exactly the thing they will get wrong."""
    text = wp.build_parser().format_help()
    assert "--exclude-path" in text
    assert "--include-agent-worktrees" in text
    assert "--allow-unmatched-globs" in text
    assert AGENT_GLOB in text, text
    # Every semantic an operator can get wrong has to be stated where they will
    # actually read it. Each of these was a measured failure, not a hypothetical.
    for claim in ("CROSSES", "SUBTREE", "trailing", "symlink", "glob.escape",
                  "REFUSES", "ZERO"):
        assert claim in text, (claim, text)


def test_the_removed_opt_in_flag_is_gone_rather_than_a_no_op_alias():
    """🔴 `--skip-agent-worktrees` was the opt-IN spelling of a behaviour that is
    now the default. The PR is unmerged, so there is no compatibility to keep —
    and a flag that does nothing is worse than one that does not exist, because
    it reads as protection while providing none.

    Structural AND behavioural: absent from --help, and rejected by the parser.
    """
    assert "--skip-agent-worktrees" not in wp.build_parser().format_help()
    with pytest.raises(SystemExit):
        wp.build_parser().parse_args(["--repo", "/x", "--skip-agent-worktrees"])


def test_the_dangerous_flag_says_so_in_help():
    """🔴 The whole point of the inversion is that the dangerous action is the
    one you type. Its help must say what it costs, not merely what it does."""
    action = [a for a in wp.build_parser()._actions
              if "--include-agent-worktrees" in a.option_strings]
    assert len(action) == 1
    h = action[0].help
    assert "DANGEROUS" in h, h
    assert "in use RIGHT NOW" in h, h


@pytest.mark.parametrize("abbrev, full", [
    ("--i", "include_agent_worktrees"),
    ("--inc", "include_agent_worktrees"),
    ("--all", "allow_unmatched_globs"),
    ("--allow", "allow_unmatched_globs"),
    ("--exec", "execute"),
])
def test_dangerous_flags_cannot_be_reached_by_abbreviation(abbrev, full):
    """🔴 "THE DANGEROUS ACTION IS THE ONE THE OPERATOR HAS TO TYPE" — voided by
    an argparse default nobody set. `allow_abbrev` is True unless you say
    otherwise, and it accepts ANY unambiguous prefix: MEASURED on the previous
    parser, `--i` alone set --include-agent-worktrees (turning off the
    exclusion that spares other live sessions' working directories) and `--all`
    set --allow-unmatched-globs (turning a refusal into a warning). Neither was
    typed, and neither appears in a shell history a reviewer would recognise.

    Asserted as a REJECTION, not as "the attribute is False": argparse must
    error, so a future parser that quietly re-enabled abbreviation and happened
    to leave the default alone could not pass this.
    """
    with pytest.raises(SystemExit):
        wp.build_parser().parse_args(["--repo", "/tmp/x", abbrev])


def test_the_abbreviation_guard_can_go_red(monkeypatch):
    """🔴 POSITIVE CONTROL on the test above: with `allow_abbrev` back on, `--i`
    is ACCEPTED and sets the dangerous flag. Without this, the SystemExit could
    be coming from an unrelated usage error (a bad `--repo`, a typo in the
    abbreviation) and the guard would pass while proving nothing.
    """
    import argparse as _argparse
    loose = _argparse.ArgumentParser(allow_abbrev=True)
    loose.add_argument("--include-agent-worktrees", action="store_true")
    loose.add_argument("--allow-unmatched-globs", action="store_true")
    assert loose.parse_args(["--i"]).include_agent_worktrees is True
    assert loose.parse_args(["--all"]).allow_unmatched_globs is True


def test_the_production_default_job_count_scans_identically_to_serial(two_dead, capsys):
    """🔴 A CONFIG-PINNED DIMENSION. Every other CLI invocation in this file
    passes `--jobs 1`, and production defaults to 8 — so `scan_repo`'s
    ThreadPoolExecutor branch, which is what actually runs on this box, was
    exercised by NO test and every concurrency bug in it would pass vacuously.

    Pins the two things the parallel branch can get wrong that the serial one
    cannot: a dropped or duplicated row, and an exclusion decision that depends
    on which worker computed it. Asserted as EQUALITY against the serial run
    rather than against literals, because the claim is "the dimension does not
    change the answer".
    """
    repo, agent, plain, gh = two_dead
    serial = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=[AGENT_GLOB])
    parallel = wp.scan_repo(repo, gh, True, 2000, jobs=8, exclude_globs=[AGENT_GLOB])

    key = lambda rows: sorted(  # noqa: E731
        (r["path"], r["verdict"], r["excluded_by"], r["removable"]) for r in rows)
    assert key(parallel) == key(serial)
    # POSITIVE CONTROL that the comparison is over a non-trivial set: the fixture
    # really does carry one excluded dead row and one removable one at BOTH job
    # counts. An empty scan would compare equal and pin nothing.
    for rows, label in ((serial, "jobs=1"), (parallel, "jobs=8")):
        by_path = {r["path"]: r for r in rows}
        assert by_path[str(agent)]["excluded_by"] == AGENT_GLOB, label
        assert by_path[str(agent)]["removable"] is False, label
        assert by_path[str(plain)]["removable"] is True, label

    # …and end to end through main() at the production default, which is the
    # path no CLI test in this file took.
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh])
    assert rc == wp.RC_OK
    assert "1 row(s) would be removed by --execute" in capsys.readouterr().out


def test_the_default_job_count_is_the_one_production_uses(two_dead):
    """The test above is only about production if `--jobs` still defaults to 8 —
    otherwise it silently becomes a second serial test. Pins the coupling."""
    assert wp.build_parser().parse_args(["--repo", "/tmp/x"]).jobs == 8


def test_the_gh_stub_can_be_observed_to_answer(tmp_path):
    """A positive control on the harness. A stub wired to nothing produces the
    same empty PR index as a real gh with no PRs, and every orphan verdict in
    this file rests on telling those apart."""
    gh = gh_stub(tmp_path, [{"number": 1, "state": "OPEN", "headRefName": "b",
                             "mergedAt": None, "url": "u"}], name="gh-probe")
    r = subprocess.run([gh, "pr", "list", "--repo", "x/y", "--state", "all",
                        "--json", "number"], capture_output=True, text=True, check=False)
    assert r.returncode == 0
    assert json.loads(r.stdout)[0]["number"] == 1


def test_the_broken_gh_stub_really_fails(tmp_path):
    r = subprocess.run([gh_broken(tmp_path, "gh-probe-broken")], capture_output=True,
                       text=True, check=False)
    assert r.returncode != 0


def test_the_stub_interpreter_exists(tmp_path):
    assert mockbin.interpreter_is_executable(), (
        "the stub shebang interpreter is missing — every gh stub in this file "
        "would fail to exec and the tests that depend on one must go red, not "
        "silently empty")


# ── #931: submodule-bearing worktrees are never `removable` ───────────────────
#
# 🔴 THE FIXTURES ARE PAIRWISE DISTINCT ON PURPOSE, and that is the whole point
# of this section. `git worktree remove` refuses on a predicate that is NOT
# "does `.gitmodules` exist" — MEASURED against git 2.55.0:
#
#     uninitialised   .gitmodules yes, modules/ no,  gitlink unpopulated -> REMOVES
#     initialised     .gitmodules yes, modules/ yes, gitlink populated   -> refuses
#     deinitialised   .gitmodules yes, modules/ yes, gitlink unpopulated -> refuses
#     embedded .git   .gitmodules yes, modules/ NO,  gitlink populated   -> refuses
#
# So a `.gitmodules` check passes the three refusal cases and OVER-blocks the
# first; a populated-gitlink check misses the deinitialised case; a `modules/`
# check misses the embedded case. Each fixture below kills a different one of
# those wrong implementations, and the uninitialised case is the control that
# stops "block everything that smells of a submodule" from scoring green.

def git_allowing_file_submodules(repo: Path, *args: str) -> str:
    """`git`, with the `file://` submodule transport enabled.

    Git blocks that transport by default (CVE-2022-39253) and every local
    fixture needs it. Set through `GIT_CONFIG_COUNT` rather than `git -c`
    because `submodule add` clones in a CHILD process which `-c` does not
    reach — MEASURED: the `-c` form dies `fatal: unable to checkout submodule
    'vendor'`. `GIT_CONFIG_GLOBAL`/`_SYSTEM` isolation is untouched.
    """
    env = {**os.environ, "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, check=False, timeout=120, env=env)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {repo} failed rc={r.returncode}\n"
                             f"stdout={r.stdout}\nstderr={r.stderr}")
    return r.stdout.strip()


def submodule_universe(tmp_path: Path, name: str):
    """A repo with a submodule and ONE worktree whose branch has squash-merged.

    The worktree is `dead` on the landing evidence alone, so every assertion
    below is about `removable` moving while the verdict stays put.
    """
    sub = new_repo(tmp_path, f"{name}-subsrc")
    repo = new_repo(tmp_path, name)
    git_allowing_file_submodules(repo, "submodule", "add", "-q", str(sub), "vendor")
    git(repo, "commit", "-qm", "add submodule")
    publish(repo)
    commit_on_branch(repo, "topic", "feature.txt", "feature\n", "feature")
    wt = add_worktree(repo, tmp_path / f"{name}-wt", "topic")
    squash_merge(repo, "topic", "feature.txt", "squash: feature")
    gh = gh_stub(tmp_path, [], name=f"gh-{name}")
    return repo, wt, sub, gh


def _row(repo, wt, gh):
    return row_for(repo, wt, gh_cmd=gh)


def test_a_worktree_with_an_UNINITIALISED_submodule_is_still_removable(tmp_path):
    """THE CONTROL. `.gitmodules` is tracked and a gitlink is in the index, but
    git removes this worktree happily — so a fix that keys on either of those
    over-blocks, and this test is what says so.

    It also proves the fixture can reach `removable=True` at all, without which
    the three assertions below would be green against a tool that blocks
    unconditionally.
    """
    repo, wt, _sub, gh = submodule_universe(tmp_path, "uninit")
    assert (wt / ".gitmodules").is_file(), "fixture must carry a tracked .gitmodules"
    assert not (wt / "vendor" / ".git").exists(), "fixture must leave the submodule uninitialised"

    row = _row(repo, wt, gh)
    assert row["verdict"] == "dead", row["verdict_reason"]
    assert row["submodules"] is False, row["submodule_reasons"]
    assert row["removable"] is True, row["blockers"]

    # …and git agrees. This is the claim the tool is making, cashed.
    assert wp.execute_removals([row], 1) == wp.RC_OK
    assert not wt.exists()


def test_an_INITIALISED_submodule_makes_the_row_dead_but_not_removable(tmp_path):
    """The production shape: 54 of 107 rows on the first real fleet --execute."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "init")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")
    assert (wt / "vendor" / ".git").exists()

    row = _row(repo, wt, gh)
    # The verdict is about whether the branch LANDED, which is a different
    # question and must not move.
    assert row["verdict"] == "dead", row["verdict_reason"]
    assert row["submodules"] is True
    assert row["removable"] is False, row["blockers"]
    assert any("submodule" in b for b in row["blockers"]), row["blockers"]


def test_a_DEINITIALISED_submodule_still_blocks_because_the_modules_store_remains(tmp_path):
    """Kills the populated-gitlink-only implementation.

    After `submodule deinit` there is NO populated gitlink in the working tree,
    yet git still refuses — the per-worktree `modules/` store is what it sees.
    A fix that only walked the index would call this row removable and hand
    back `failed=1`.
    """
    repo, wt, _sub, gh = submodule_universe(tmp_path, "deinit")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")
    git(wt, "submodule", "deinit", "-f", "-q", "vendor")
    assert not (wt / "vendor" / ".git").exists(), "deinit must leave the gitlink unpopulated"

    row = _row(repo, wt, gh)
    assert row["verdict"] == "dead"
    assert row["submodules"] is True, row["submodule_reasons"]
    assert row["removable"] is False, row["blockers"]

    # The mechanism, not just the outcome: git itself must refuse this.
    r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(wt)],
                       capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "submodule" in r.stderr, r


def test_an_EMBEDDED_submodule_git_dir_blocks_even_with_no_modules_store(tmp_path):
    """Kills the `modules/`-only implementation.

    An old-style submodule carries a real `.git` DIRECTORY inside the worktree
    and creates no per-worktree `modules/` store. git refuses on it via its
    second arm; a fix that only stat'd `modules/` would miss it entirely.
    """
    repo, wt, sub, gh = submodule_universe(tmp_path, "embedded")
    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    subprocess.run(["git", "clone", "-q", str(sub), str(wt / "vendor")],
                   capture_output=True, text=True, check=True,
                   env={**os.environ, "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "protocol.file.allow",
                        "GIT_CONFIG_VALUE_0": "always"})
    assert (wt / "vendor" / ".git").is_dir(), "fixture must embed a real .git DIRECTORY"
    assert not (gitdir / "modules").exists(), "fixture must create NO modules/ store"

    row = _row(repo, wt, gh)
    assert row["submodules"] is True, row["submodule_reasons"]
    assert row["removable"] is False, row["blockers"]

    r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(wt)],
                       capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "submodule" in r.stderr, r


def test_confirm_counts_only_what_the_run_can_actually_remove(tmp_path, capsys):
    """#931's headline: `--confirm N` was a claim the tool could not cash.

    End to end through `main`, which is where the operator meets the number.
    The old tool offered `--confirm 1` and then reported `failed=1`; the fixed
    one offers 0, and 1 is refused.
    """
    repo, wt, _sub, gh = submodule_universe(tmp_path, "confirm")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")

    assert wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                    "--execute", "--confirm", "1"]) == wp.RC_EXECUTE_REFUSED
    assert wt.is_dir(), "nothing may be removed on a refused run"

    # The achievable number runs clean — this is the issue's closing condition
    # in miniature: `removed == N` and `failed=0`, with no rc=128 anywhere.
    capsys.readouterr()
    assert wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                    "--execute", "--confirm", "0"]) == wp.RC_OK
    err = capsys.readouterr().err
    assert "removed=0 skipped=0 failed=0" in err, err
    assert "cannot be moved or removed" not in err, err
    assert wt.is_dir()


def test_the_report_names_the_blocked_rows_instead_of_silently_dropping_them(tmp_path, capsys):
    """A row that is `dead` but not removable must not vanish into the gap
    between the two counts — that is the silent truncation this tool exists not
    to have. And `--verbose` must NAME the submodule, because the summary says
    it does."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "report")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--verbose"])
    out = capsys.readouterr().out
    assert "carry SUBMODULES" in out, out
    assert "1 further `dead` row(s)" in out, out
    # The summary's own promise, cashed: the submodule is named in --verbose.
    assert "vendor" in out, out


def test_a_submodule_appearing_after_the_scan_skips_rather_than_fails(tmp_path, capsys):
    """The scan is a claim about the PAST. If a concurrent session initialises a
    submodule between the scan and the removal, the executor must SKIP — the
    whole point of #931 is that `failed=` stops appearing."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "race")
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1)
    targets = [r for r in rows if r["removable"]]
    assert len(targets) == 1, [r["path"] for r in rows]

    # …the world moves under the scan.
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")

    capsys.readouterr()
    assert wp.execute_removals(rows, 1) == wp.RC_OK
    err = capsys.readouterr().err
    assert "removed=0 skipped=1 failed=0" in err, err
    assert "SKIP" in err and "submodules=True" in err, err
    assert wt.is_dir(), "the worktree must survive"


def test_an_unknowable_submodule_answer_blocks_rather_than_permits(tmp_path):
    """`None` is not `False`. An unanswered question about a refusal must not be
    spent as permission — the same direction `dirty=None` already takes."""
    row = wp.classify(base_row(landed_signals=["ancestor"], submodules=None,
                               submodule_reasons=["git ls-files failed rc=128"]))
    assert row["verdict"] == "dead"
    assert row["removable"] is False, row["blockers"]
    assert any("could not determine" in b for b in row["blockers"]), row["blockers"]


def test_a_row_that_never_answered_the_submodule_question_is_not_removable(tmp_path):
    """A missing key reads as UNKNOWN, not as False. A future scan path that
    forgets to gather the evidence must lose coverage, never gain permission."""
    row = dict(base_row(landed_signals=["ancestor"]))
    del row["submodules"]
    out = wp.classify(row)
    assert out["verdict"] == "dead"
    assert out["removable"] is False, out["blockers"]


def test_the_executor_skips_when_the_submodule_recheck_cannot_answer(tmp_path, capsys,
                                                                     monkeypatch):
    """`is not False`, not `is True`. If the re-check at the destructive step
    cannot answer, the executor must SKIP — an unknowable answer is not
    permission, and a `git` that fails here is exactly when it matters."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "recheck-unknown")
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1)
    assert sum(1 for r in rows if r["removable"]) == 1

    monkeypatch.setattr(wp, "worktree_submodules",
                        lambda p: (None, ["git ls-files failed rc=128"]))
    capsys.readouterr()
    assert wp.execute_removals(rows, 1) == wp.RC_OK
    err = capsys.readouterr().err
    assert "removed=0 skipped=1 failed=0" in err, err
    assert "submodules=None" in err, err
    assert wt.is_dir(), "the worktree must survive an unanswerable re-check"


def test_an_EMPTY_modules_store_blocks_because_git_blocks_on_it_too(tmp_path):
    """The one place the mirror could plausibly diverge from git.

    git refuses on the mere EXISTENCE of the per-worktree `modules/` directory
    — its own source calls that a knowingly-accepted false positive. A worktree
    with an empty store, no `.gitmodules` and no gitlink in the index is
    therefore unremovable, and a tool that tried to be cleverer than git here
    would hand back `failed=1`. The assertion is that the tool AGREES with git,
    and git's behaviour is measured in the same test rather than assumed.
    """
    repo = new_repo(tmp_path, "emptystore")
    commit_on_branch(repo, "topic", "feature.txt", "feature\n", "feature")
    wt = add_worktree(repo, tmp_path / "emptystore-wt", "topic")
    squash_merge(repo, "topic", "feature.txt", "squash: feature")
    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    (gitdir / "modules").mkdir()
    assert not (wt / ".gitmodules").exists()
    assert not [l for l in git(wt, "ls-files", "-s").splitlines() if l.startswith("160000 ")]

    blocked, reasons = wp.worktree_submodules(wt)
    assert blocked is True, reasons
    assert "EMPTY" in reasons[0], reasons

    gh = gh_stub(tmp_path, [], name="gh-emptystore")
    row = row_for(repo, wt, gh_cmd=gh)
    assert row["verdict"] == "dead"
    assert row["removable"] is False, row["blockers"]

    # …and git really does refuse, so the tool is agreeing rather than guessing.
    r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(wt)],
                       capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "submodule" in r.stderr, r


# ── #935 round 2: findings from the blind audit ──────────────────────────────

def test_a_submodule_path_git_QUOTES_is_still_seen(tmp_path):
    """AUDIT FINDING 1 — the index arm was blind to `core.quotePath`.

    `git ls-files -s` quotes non-ASCII paths by default: a submodule at
    `vendôr` is emitted as the literal `"vend\\303\\264r"`. Statting that name
    finds nothing, so the arm answered False and `--execute` then died rc=128 —
    the exact symptom #931 exists to remove, surviving in the one arm with no
    `modules/` store behind it.

    MEASURED before the fix: tool `(False, [])`, git rc=128. The fixture uses
    the OLD-STYLE embedded `.git` so the `modules/` arm is absent and the index
    arm is what answers; without that this test would pass on the wrong arm.
    """
    subpath = "vendôr"
    sub = new_repo(tmp_path, "quoted-subsrc")
    repo = new_repo(tmp_path, "quoted")
    git_allowing_file_submodules(repo, "submodule", "add", "-q", str(sub), subpath)
    git(repo, "commit", "-qm", "add submodule")
    publish(repo)
    commit_on_branch(repo, "topic", "feature.txt", "feature\n", "feature")
    wt = add_worktree(repo, tmp_path / "quoted-wt", "topic")
    squash_merge(repo, "topic", "feature.txt", "squash: feature")

    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    subprocess.run(["git", "clone", "-q", str(sub), str(wt / subpath)],
                   capture_output=True, text=True, check=True,
                   env={**os.environ, "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "protocol.file.allow",
                        "GIT_CONFIG_VALUE_0": "always"})
    assert not (gitdir / "modules").exists(), "must reach the INDEX arm, not the store arm"
    # The harness's own control: prove git really does quote this path, so the
    # test cannot pass because the fixture failed to reproduce the condition.
    raw = git(wt, "ls-files", "-s")
    assert '"vend' in raw, f"fixture did not reproduce quoting: {raw!r}"

    blocked, reasons = wp.worktree_submodules(wt)
    assert blocked is True, f"quoted path went unseen: {reasons}"
    assert subpath in reasons[0], reasons

    r = subprocess.run(["git", "-C", str(repo), "worktree", "remove", str(wt)],
                       capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "submodule" in r.stderr, r


def test_an_unanswered_row_is_not_described_as_carrying_submodules(tmp_path, capsys,
                                                                   monkeypatch):
    """AUDIT FINDING 2 — every claim in the carriers sentence is FALSE for an
    UNKNOWN row: it does not carry submodules, git does not refuse on it, its
    cause is transient, re-running IS the remedy, and --verbose names no
    submodule. The count deliberately includes it; the sentence must not."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "unanswered")
    monkeypatch.setattr(wp, "worktree_submodules",
                        lambda p: (None, ["git rev-parse --git-path failed rc=128"]))

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--verbose"])
    out = capsys.readouterr().out
    assert "held back by the submodule check" in out, out
    assert "could NOT be answered" in out, out
    assert "IS transient" in out, out
    # 🔴 The permanence claims must NOT be attached to this row.
    assert "PERMANENT" not in out, out
    assert "carry SUBMODULES" not in out, out


def test_the_two_further_lines_do_not_read_as_additive(tmp_path, capsys):
    """AUDIT FINDING 3 — `excluded_dead` and `submodule_blocked_dead` are
    counted independently, so one row that is BOTH printed `1 + 1` against a
    single `dead` row while the word `further` read additive. The union is the
    one figure that is always true, so it is printed."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "overlap")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--exclude-path", f"{wt}"])
    out = capsys.readouterr().out
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1,
                        exclude_globs=[wp.normalize_glob(str(wt))])
    s = wp.summarize(rows, exclude_globs=[wp.normalize_glob(str(wt))],
                     agent_worktrees_included=False)
    assert s["excluded_dead"] == 1 and s["submodule_blocked_dead"] == 1, s
    # …and yet only ONE dead row is actually held back.
    assert s["dead_not_removable"] == 1, s
    # 🔴 Round 3 corrected this wording: the line now STATES the measured
    # overlap rather than asserting one, because asserting it was false
    # whenever the two sets were disjoint. Here they genuinely do overlap.
    assert "1 row(s) are in BOTH" in out, out
    assert "1 `dead` row(s) are not removable in total" in out, out


def test_summarize_counts_an_unanswered_row_as_blocked(tmp_path):
    """AUDIT FINDING 4 — `submodule_blocked_dead`'s `is not False` had no test
    and no mutant: the auditor changed it to `is True` and it SURVIVED the full
    suite. This pins the UNKNOWN row into the count, which is the branch that
    made the report text false."""
    dead_unknown = base_row(landed_signals=["ancestor"], submodules=None,
                            submodule_reasons=["git ls-files failed rc=128"])
    dead_unknown["path"] = "/x/unknown"
    dead_carrying = base_row(landed_signals=["ancestor"], submodules=True,
                             submodule_reasons=["populated submodule(s): vendor"])
    dead_carrying["path"] = "/x/carrying"
    dead_clean = base_row(landed_signals=["ancestor"])
    dead_clean["path"] = "/x/clean"
    rows = [wp.classify(r) for r in (dead_unknown, dead_carrying, dead_clean)]
    for r in rows:
        r.setdefault("repo", "/x")

    s = wp.summarize(rows, agent_worktrees_included=False)
    assert s["submodule_blocked_dead"] == 2, s   # UNKNOWN + carrying
    assert s["submodule_unknown_dead"] == 1, s   # only the UNKNOWN one
    assert s["dead_not_removable"] == 2, s
    assert s["removable"] == 1, s


def test_verbose_marks_submodule_rows_without_widening_the_verdict_label(tmp_path, capsys):
    """AUDIT FINDING 6 — the summary sends the operator to --verbose, so the
    rows must be findable by eye. The marker goes on the listing line, NOT into
    `verdict_label()`, whose contract is pinned to excluded rows only and which
    is rendered into a column elsewhere."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "marker")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--verbose"])
    out = capsys.readouterr().out
    assert "[SUBMODULES — git will refuse to remove this]" in out, out
    # …and the verdict label itself is untouched.
    assert "[dead (submodules)]" not in out, out
    assert wp.verdict_label({"verdict": "dead", "submodules": True}) == "dead"


# ── #935 round 3: findings from the delta re-audit ───────────────────────────

def test_an_undecodable_tracked_path_does_not_abort_the_scan(tmp_path):
    """DELTA FINDING 1 (🔴) — round 2's `-z` fix traded a wrong answer for a
    WHOLE-SCAN CRASH.

    `core.quotePath` was not only hiding non-ASCII gitlinks, it was also
    guaranteeing git's output was ASCII. With `-z` git emits raw bytes, so
    strict utf-8 decoding raised `UnicodeDecodeError` inside `subprocess.run`
    — and there is no `try:` between there and `sys.exit(main())`, so the
    operator got a bare traceback and ZERO classified rows.

    🔴 The trigger is ANY tracked file, not a submodule: this repo has no
    submodules at all. That is why the regression was WIDER than the bug it
    fixed, and why the fixture deliberately contains none.
    """
    repo = new_repo(tmp_path, "undecodable")
    bad = os.fsdecode(b"caf\xe9.txt")            # not valid utf-8
    (repo / bad).write_bytes(b"data\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add an undecodable filename")
    publish(repo)
    commit_on_branch(repo, "topic", "feature.txt", "feature\n", "feature")
    wt = add_worktree(repo, tmp_path / "undecodable-wt", "topic")
    squash_merge(repo, "topic", "feature.txt", "squash: feature")

    # The control: prove the fixture really does produce undecodable bytes,
    # so a green here cannot mean "the condition never occurred".
    raw = subprocess.run(["git", "-C", str(wt), "ls-files", "-s", "-z"],
                         capture_output=True, check=True).stdout
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")

    blocked, reasons = wp.worktree_submodules(wt)
    assert blocked is False, reasons          # no submodules here, and no crash

    gh = gh_stub(tmp_path, [], name="gh-undecodable")
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1)   # must not raise
    assert len(rows) == 2, [r["path"] for r in rows]
    assert any(r["removable"] for r in rows), [r["verdict_reason"] for r in rows]


def test_an_undecodable_SUBMODULE_path_is_still_detected_and_printable(tmp_path, capsys):
    """The two decodings, both needed. `os.fsdecode` must REACH the path or the
    submodule goes undetected; `_printable` must render it or the report raises
    `UnicodeEncodeError` at `print()` and the abort simply moves."""
    subpath = os.fsdecode(b"vend\xe9r")          # undecodable, not merely non-ASCII
    sub = new_repo(tmp_path, "raw-subsrc")
    repo = new_repo(tmp_path, "raw")
    # `submodule add` will not take this byte sequence, so the gitlink is
    # written into the index directly — the index is what the arm reads.
    sha = git(sub, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{subpath}")
    git(repo, "commit", "-qm", "add raw-named gitlink")
    publish(repo)
    commit_on_branch(repo, "topic", "feature.txt", "feature\n", "feature")
    wt = add_worktree(repo, tmp_path / "raw-wt", "topic")
    squash_merge(repo, "topic", "feature.txt", "squash: feature")
    subprocess.run(["git", "clone", "-q", str(sub), str(wt / subpath)],
                   capture_output=True, check=True,
                   env={**os.environ, "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "protocol.file.allow",
                        "GIT_CONFIG_VALUE_0": "always"})
    assert (wt / subpath / ".git").is_dir()

    blocked, reasons = wp.worktree_submodules(wt)
    assert blocked is True, reasons
    # …and the reason survives printing, which a lone surrogate would not.
    gh = gh_stub(tmp_path, [], name="gh-raw")
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--verbose"])
    out = capsys.readouterr().out
    assert "�" in out, "the undecodable byte should render as U+FFFD"
    assert "[SUBMODULES" in out, out


def test_the_overlap_line_states_the_measured_overlap_not_an_asserted_one(tmp_path, capsys):
    """DELTA FINDING 2 — the line was gated on both causes being non-zero and
    then DECLARED they overlap. With two DIFFERENT rows they are disjoint and
    the numbers sum exactly, so the sentence was false about the very run
    printing it — this round's own defect class, one line below its fix.

    Round 2's test only ever built the overlapping case, so nothing saw it.
    """
    repo, wt, _sub, gh = submodule_universe(tmp_path, "disjoint")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")
    # a SECOND dead worktree, excluded, with no submodules of its own
    commit_on_branch(repo, "other", "other.txt", "other\n", "other")
    wt2 = add_worktree(repo, tmp_path / "disjoint-wt2", "other")
    squash_merge(repo, "other", "other.txt", "squash: other")

    globs = [wp.normalize_glob(str(wt2))]
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=globs)
    s = wp.summarize(rows, exclude_globs=globs, agent_worktrees_included=False)
    assert s["excluded_dead"] == 1 and s["submodule_blocked_dead"] == 1, s
    assert s["dead_not_removable"] == 2, s     # DISJOINT — they sum exactly

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--exclude-path", str(wt2)])
    out = capsys.readouterr().out
    assert "0 row(s) are in BOTH" in out, out
    assert "2 `dead` row(s) are not removable in total" in out, out
    assert "OVERLAP and do NOT sum" not in out, out


def test_an_excluded_row_with_another_blocker_is_not_credited_to_the_filter(tmp_path, capsys):
    """DELTA FINDING 3 — "N of them are `dead` and would otherwise have been
    removed" is false for an excluded row that ALSO carries submodules: drop
    the exclusion and it is still not removable, so the filter is credited
    with a sparing it did not do."""
    repo, wt, _sub, gh = submodule_universe(tmp_path, "credit")
    git_allowing_file_submodules(wt, "submodule", "update", "--init", "-q")

    globs = [wp.normalize_glob(str(wt))]
    rows = wp.scan_repo(repo, gh, True, 2000, jobs=1, exclude_globs=globs)
    s = wp.summarize(rows, exclude_globs=globs, agent_worktrees_included=False)
    assert s["excluded_dead"] == 1, s
    assert s["excluded_dead_only_blocker"] == 0, s   # the filter freed nothing

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--exclude-path", str(wt)])
    out = capsys.readouterr().out
    assert "0 of which would otherwise have been removed" in out, out


def test_a_vanished_worktree_is_not_marked_as_a_submodule_unknown(tmp_path, capsys):
    """DELTA FINDING 8 — a worktree whose directory is gone returns `(None, [])`
    from the `is_dir` guard: nothing was asked and no git call failed, so
    marking it "submodules UNKNOWN — held back" invents a submodule problem for
    a row that is `cannot-tell` for a different reason entirely."""
    repo = new_repo(tmp_path, "vanished")
    commit_on_branch(repo, "topic", "feature.txt", "feature\n", "feature")
    wt = add_worktree(repo, tmp_path / "vanished-wt", "topic")
    shutil.rmtree(wt)
    gh = gh_stub(tmp_path, [], name="gh-vanished")

    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1", "--verbose"])
    out = capsys.readouterr().out
    assert "cannot-tell" in out
    assert "submodules UNKNOWN — held back" not in out, out
