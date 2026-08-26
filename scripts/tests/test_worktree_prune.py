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
A full scan of this machine reported 870 worktrees / 128 repos -> 250 dead. 131
of those 250 were `.claude/worktrees/agent-*` trees belonging to OTHER LIVE
Claude sessions: `dead` is a correct verdict about the BRANCH and a catastrophic
instruction about the DIRECTORY. `--exclude-path` / `--skip-agent-worktrees`
spare a row without hiding it, and the tests for them carry the same shape as the
ones above — a positive control that BOTH fixtures are removable unfiltered, a
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

import importlib.machinery
import importlib.util
import json
import os
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
    row = {"is_main": False, "bare": False, "prunable": False, "path_exists": True,
           "locked": False, "dirty": False, "dirty_count": 0,
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

# ── 🔴 --exclude-path / --skip-agent-worktrees ────────────────────────────────
#
# WHY THIS SECTION EXISTS, measured rather than imagined. A full scan of this
# machine reported 870 worktrees / 128 repos -> 250 dead, 40 orphan, 528 live,
# 52 cannot-tell. 131 of the 250 `dead` rows were `.claude/worktrees/agent-*`
# trees belonging to OTHER LIVE Claude sessions: `dead` is a correct verdict
# about the BRANCH and a catastrophic instruction about the DIRECTORY. Excluding
# whole repos to dodge them collapsed the safe set from 106 rows to 24, because
# a repo like civit/civitai holds 59 ordinary dead rows AND agent worktrees.
#
# So the filter has to be per-ROW, and it has to be visible: an excluded row that
# vanished from the report would read as "we covered everything".

AGENT_GLOB = "*/.claude/worktrees/agent-*"


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
    ["--skip-agent-worktrees"],
    ["--exclude-path", AGENT_GLOB],
])
def test_execute_removes_the_plain_dead_tree_and_spares_the_excluded_one(two_dead, flag, capsys):
    """BOTH halves, in ONE run. The excluded worktree must still be on disk and
    the other must be GONE — a filter that spared everything would pass the first
    assertion on its own."""
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
    """N = non-excluded dead count SUCCEEDS."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--skip-agent-worktrees", "--execute", "--confirm", "1"])
    assert rc == wp.RC_OK, capsys.readouterr().err
    assert not plain.exists()


def test_confirm_with_the_total_dead_count_is_refused_when_one_is_excluded(two_dead, capsys):
    """N = TOTAL dead count (2) REFUSES, and removes nothing.

    This is the half that pins the meaning of --confirm: if excluded rows still
    counted, `--confirm 2` would be the accepted value and the operator's number
    would silently describe rows the run was never going to touch."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--skip-agent-worktrees", "--execute", "--confirm", "2"])
    err = capsys.readouterr().err
    assert rc == wp.RC_EXECUTE_REFUSED, err
    assert "does not match the 1 row(s)" in err, err
    assert agent.is_dir() and plain.is_dir(), "a refused run removed something"


def test_without_the_filter_the_same_scope_needs_confirm_two(two_dead, capsys):
    """The control on the test above: 2 is the RIGHT number when nothing is
    excluded, so the refusal there is caused by the exclusion and not by the
    fixture happening to hold one dead row."""
    repo, agent, plain, gh = two_dead
    rc = wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
                  "--execute", "--confirm", "2"])
    assert rc == wp.RC_OK, capsys.readouterr().err
    assert not agent.exists() and not plain.exists()


# ── excluded rows stay VISIBLE in the report ──────────────────────────────────

def test_excluded_rows_still_appear_in_the_text_report(two_dead, capsys):
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--skip-agent-worktrees", "--verbose"])
    out = capsys.readouterr().out
    assert "excluded" in out
    assert "1 row(s) matched an --exclude-path glob" in out, out
    assert "1 of them are `dead`" in out, out
    assert str(agent) in out, "the excluded worktree vanished from the report"
    assert "[dead (excluded)]" in out, out
    # …and the summary still counts it as dead, so the operator's totals do not
    # silently shrink when they add a filter.
    assert "2 dead" in out, out


def test_excluded_rows_still_appear_in_the_json_report(two_dead, tmp_path, capsys):
    repo, agent, plain, gh = two_dead
    out = tmp_path / "excl.json"
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1",
             "--skip-agent-worktrees", "--format", "json", "--out", str(out)])
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
    assert payload["summary"]["exclude_globs"] == [AGENT_GLOB]


def test_an_unfiltered_run_does_not_shout_about_exclusions(two_dead, capsys):
    """Negative control on the report line above — it must be caused by an actual
    exclusion, not printed unconditionally."""
    repo, agent, plain, gh = two_dead
    wp.main(["--repo", str(repo), "--gh-cmd", gh, "--jobs", "1"])
    out = capsys.readouterr().out
    assert "matched an --exclude-path glob" not in out
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
    `*/.claude/worktrees/agent-*` would match only a worktree exactly one level
    below the root — i.e. essentially none of the real ones. fnmatch is chosen
    because the agent-worktree case needs to match at ANY depth. The cost, pinned
    here so nobody discovers it by accident, is that a glob is easy to write too
    WIDE — which in this tool spares more and removes less, never the reverse.
    """
    assert wp.path_excluded("/home/z/a/b/c/.claude/worktrees/agent-1", [AGENT_GLOB]) == AGENT_GLOB
    assert wp.path_excluded("/home/z/.claude/worktrees/agent-1", [AGENT_GLOB]) == AGENT_GLOB
    assert wp.path_excluded("/home/z/.claude/worktrees/agent-1/deep/inside",
                            [AGENT_GLOB]) == AGENT_GLOB
    # `*` swallowing separators is the DEFINING behaviour, not an accident of the
    # agent glob: one `*` spans three components here.
    assert wp.path_excluded("/x/a/b/c/leaf", ["/x/*/leaf"]) == "/x/*/leaf"
    # …and the negative half, so the matcher is not simply always-true.
    assert wp.path_excluded("/home/z/repo/.claude/worktrees/keep-me", [AGENT_GLOB]) is None
    assert wp.path_excluded("/home/z/repo/wt/agent-1", [AGENT_GLOB]) is None
    assert wp.path_excluded("/x/a/b/c/other", ["/x/*/leaf"]) is None


def test_the_glob_match_is_case_sensitive():
    """`fnmatchcase`, so the verdict does not depend on the host filesystem's
    case-folding — a filter that behaves differently per platform is a filter
    nobody can reason about."""
    assert wp.path_excluded("/home/z/.CLAUDE/worktrees/agent-1", [AGENT_GLOB]) is None


def test_an_empty_glob_list_excludes_nothing():
    for globs in (None, [], [""]):
        assert wp.path_excluded("/home/z/.claude/worktrees/agent-1", globs) is None


def test_the_first_matching_glob_is_the_one_reported():
    """The row records WHICH glob spared it; an operator debugging an
    over-broad filter needs the answer, not a bool."""
    assert wp.path_excluded("/a/b/c", ["/nope/*", "/a/*", "/a/b/*"]) == "/a/*"


def test_multiple_exclude_paths_all_apply(two_dead):
    repo, agent, plain, gh = two_dead
    rows = _rows_by_path(repo, gh, ["*/nothing-matches-this/*", AGENT_GLOB])
    assert rows[str(agent)]["excluded_by"] == AGENT_GLOB
    assert rows[str(agent)]["removable"] is False


# ── ONE RULE, ONE PLACE: the convenience flag IS the glob ─────────────────────

def test_skip_agent_worktrees_resolves_to_exactly_the_documented_glob():
    assert wp.resolve_exclude_globs([], True) == [wp.AGENT_WORKTREE_GLOB]
    assert wp.AGENT_WORKTREE_GLOB == AGENT_GLOB
    assert wp.resolve_exclude_globs(["*/x/*"], True) == ["*/x/*", wp.AGENT_WORKTREE_GLOB]
    assert wp.resolve_exclude_globs([], False) == []
    # Spelling it both ways must not double it up.
    assert wp.resolve_exclude_globs([AGENT_GLOB], True) == [AGENT_GLOB]


def test_the_convenience_flag_and_the_explicit_glob_produce_identical_rows(two_dead, capsys):
    """Behavioural, not structural: the two routes must agree row for row. A
    second open-coded matcher would pass a `==` on the constant and still drift
    here."""
    repo, agent, plain, gh = two_dead
    a = _rows_by_path(repo, gh, wp.resolve_exclude_globs([], True))
    b = _rows_by_path(repo, gh, [AGENT_GLOB])
    key = lambda rs: {p: (r["verdict"], r["excluded_by"], r["removable"])
                      for p, r in rs.items()}
    assert key(a) == key(b)
    assert key(a)[str(agent)] == ("dead", AGENT_GLOB, False)


def test_the_agent_glob_literal_is_spelled_exactly_once_in_executable_code():
    """Structural backstop for the one-rule-one-place claim. `--skip-agent-worktrees`
    interpolates the CONSTANT into its help text, so a second occurrence of the
    literal means somebody re-typed the pattern."""
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
    assert "--skip-agent-worktrees" in text
    assert "CROSSES" in text, text
    assert AGENT_GLOB in text, text


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
