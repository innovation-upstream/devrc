"""Direct tests for `scripts/lib/shared_clone_sync.py` + `scripts/sync-clones.py`.

🔴 THE FALSE GREEN THIS SUITE EXISTS TO REFUSE. The helper under test replaces a
`git merge --ff-only` in a cron, whose defining defect is that its four
interesting outcomes are INDISTINGUISHABLE: fast-forwarded 606 commits, already
current, refused because the tree is dirty, and refused because the branch has
local commits all end the same line with nothing on stdout, and the no-op exits
0. So it is not enough for these tests to assert "the helper ran and said
something" — every case here asserts WHICH status, WHICH sentinel, WHICH exit
code, and, for the two that matter most, whether the local HEAD actually MOVED.

🔴 THE INSTRUMENT-VALIDATION PAIR, both in `TestTheControls`:
  * POSITIVE CONTROL — a fixture that MUST report `synced`, asserting the local
    HEAD commit COUNT before and after. The number must move (1 -> 4). A status
    string alone cannot tell a real fast-forward from a stub that returns it.
  * NEGATIVE CONTROL — a fixture that MUST refuse (dirty), asserting the
    specific sentinel and exit code AND that the HEAD commit count did NOT move.
    A helper that reports "nothing to do" instead of refusing is exactly the
    false green this must not have, so `TestNotConfusable` additionally pins
    that `refused-dirty` is not reported as `current`, and that `current` and
    `synced` share no status, no sentinel and no `moved` value.

Every fixture is a real bare "remote" plus real clones under pytest's
`tmp_path`. Nothing here touches a real repository or the network. Git identity
is set PER FIXTURE REPO rather than taken from global config: on this host a
`git commit` with no identity fails "Author identity unknown", and the nix
sandbox has no global config at all — a suite that depended on ambient config
would pass on the dev host and collapse in the authoritative tier.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "scripts" / "lib" / "shared_clone_sync.py"
CLI = REPO_ROOT / "scripts" / "sync-clones.py"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _load():
    spec = importlib.util.spec_from_file_location("shared_clone_sync", MODULE)
    assert spec and spec.loader, MODULE
    mod = importlib.util.module_from_spec(spec)
    # 🔴 REGISTERED BEFORE exec, not after: `@dataclass` resolves its own module
    # out of `sys.modules[cls.__module__]` while the class body executes, so a
    # module loaded by path alone dies with a bare `'NoneType' has no attribute
    # '__dict__'` that names neither the dataclass nor the loader.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


scs = _load()


# --------------------------------------------------------------------------- #
# Fixture plumbing
# --------------------------------------------------------------------------- #
def _sh(*args: str, cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV)
    )
    if check:
        assert proc.returncode == 0, f"{args} failed: {proc.stderr or proc.stdout}"
    return proc.stdout


def _identify(repo: Path) -> None:
    """Per-repo identity. See the module docstring: NOT from global config."""
    _sh("git", "config", "user.email", "test@example.invalid", cwd=repo)
    _sh("git", "config", "user.name", "Test", cwd=repo)
    _sh("git", "config", "commit.gpgsign", "false", cwd=repo)


def commit_count(repo: Path) -> int:
    """The measurement the positive/negative control pair moves (or does not)."""
    return int(_sh("git", "rev-list", "--count", "HEAD", cwd=repo).strip())


def head(repo: Path) -> str:
    return _sh("git", "rev-parse", "HEAD", cwd=repo).strip()


def make_origin(tmp_path: Path, *, branch: str = "main", name: str = "origin") -> Path:
    """A bare remote seeded with one commit, plus a `<name>-seed` working clone."""
    origin = tmp_path / f"{name}.git"
    _sh("git", "init", "-q", "--bare", "-b", branch, str(origin), cwd=tmp_path)
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _sh("git", "init", "-q", "-b", branch, cwd=seed)
    _identify(seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    (seed / "shared.txt").write_text("v0\n", encoding="utf-8")
    _sh("git", "add", "--", "README.md", "shared.txt", cwd=seed)
    _sh("git", "commit", "-q", "-m", "seed", cwd=seed)
    _sh("git", "remote", "add", "origin", str(origin), cwd=seed)
    _sh("git", "push", "-q", "origin", branch, cwd=seed)
    return origin


def clone(tmp_path: Path, origin: Path, name: str = "clone") -> Path:
    """A REAL `git clone` — the upstream tracking config the helper reads is
    written by clone itself, and hand-writing it would test a shape invented
    here rather than the one every field clone has."""
    work = tmp_path / name
    _sh("git", "clone", "-q", str(origin), str(work), cwd=tmp_path)
    _identify(work)
    return work


def advance_origin(tmp_path: Path, *, n: int, path: str = "other.txt",
                   name: str = "origin", branch: str = "main") -> None:
    """Push `n` new commits to the remote, each touching `path`."""
    seed = tmp_path / f"{name}-seed"
    for i in range(n):
        (seed / path).write_text(f"upstream {i}\n", encoding="utf-8")
        _sh("git", "add", "--", path, cwd=seed)
        _sh("git", "commit", "-q", "-m", f"upstream {path} {i}", cwd=seed)
    _sh("git", "push", "-q", "origin", branch, cwd=seed)


# --------------------------------------------------------------------------- #
# THE FOUR-STATE MATRIX — the states the brief names, one class each.
# --------------------------------------------------------------------------- #
class TestFourStateMatrix:
    def test_CURRENT_when_upstream_has_nothing_new(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_CURRENT
        assert res.sentinel == "already current"
        assert res.exit_code == 0
        assert res.behind == 0 and res.ahead == 0
        assert res.moved == 0, "nothing moved, and the report must say so"
        assert commit_count(repo) == before

    def test_SYNCED_fast_forwards_and_reports_the_delta(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=3)
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_SYNCED
        assert res.sentinel == "fast-forwarded"
        assert res.exit_code == 0
        assert res.behind == 3
        assert res.moved == 3, "the delta that moved must be REPORTED, not implied"
        assert commit_count(repo) == before + 3
        assert res.head_after != res.head_before

    def test_REFUSED_DIRTY_when_dirt_overlaps_the_incoming_commits(
        self, tmp_path: Path
    ) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2, path="shared.txt")
        (repo / "shared.txt").write_text("my uncommitted work\n", encoding="utf-8")
        before_head, before_n = head(repo), commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY
        assert res.sentinel == "working tree is dirty"
        assert res.exit_code == 3
        assert "shared.txt" in res.blocking_paths
        assert res.blocking_count == 1
        assert head(repo) == before_head and commit_count(repo) == before_n
        assert (repo / "shared.txt").read_text(encoding="utf-8") == "my uncommitted work\n"

    def test_REFUSED_DIVERGED_when_local_has_commits_upstream_does_not(
        self, tmp_path: Path
    ) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2)
        (repo / "local.txt").write_text("mine\n", encoding="utf-8")
        _sh("git", "add", "--", "local.txt", cwd=repo)
        _sh("git", "commit", "-q", "-m", "local work", cwd=repo)
        before_head, before_n = head(repo), commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIVERGED
        assert res.sentinel == "local commits are not upstream"
        assert res.exit_code == 4
        assert res.ahead == 1 and res.behind == 2
        assert head(repo) == before_head and commit_count(repo) == before_n

    def test_the_two_refusals_have_DISTINCT_non_zero_exit_codes(self) -> None:
        """A caller must be able to branch on WHICH refusal without parsing text."""
        dirty = scs.EXIT_CODES[scs.STATUS_REFUSED_DIRTY]
        diverged = scs.EXIT_CODES[scs.STATUS_REFUSED_DIVERGED]
        assert dirty != 0 and diverged != 0
        assert dirty != diverged


# --------------------------------------------------------------------------- #
# THE INSTRUMENT-VALIDATION PAIR
# --------------------------------------------------------------------------- #
class TestTheControls:
    def test_POSITIVE_CONTROL_the_head_count_MOVES(self, tmp_path: Path) -> None:
        """Feed it a case that MUST produce a non-zero delta and watch the number
        move. A status string is a claim; the commit count is the measurement."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=3)

        before = commit_count(repo)
        assert before == 1, "precondition: the clone starts at the seed commit"

        res = scs.sync_repo(repo)
        after = commit_count(repo)

        assert res.status == scs.STATUS_SYNCED
        assert (before, after) == (1, 4), f"HEAD must MOVE 1 -> 4, got {before} -> {after}"
        assert after - before == res.moved == 3

    def test_NEGATIVE_CONTROL_it_refuses_and_the_head_count_does_NOT_move(
        self, tmp_path: Path
    ) -> None:
        """The same instrument, fed a case it MUST refuse. A checker that cannot
        go red is testing nothing — and the refusal must be attributable, so the
        specific sentinel and code are asserted, not merely non-zero-ness."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=3, path="shared.txt")
        (repo / "shared.txt").write_text("local edit\n", encoding="utf-8")

        before = commit_count(repo)
        assert before == 1

        res = scs.sync_repo(repo)
        after = commit_count(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY
        assert res.sentinel == "working tree is dirty"
        assert res.exit_code == 3
        assert (before, after) == (1, 1), f"HEAD must NOT move, got {before} -> {after}"
        assert res.moved == 0


# --------------------------------------------------------------------------- #
# NOT CONFUSABLE — the pairs whose collapse would recreate the original defect
# --------------------------------------------------------------------------- #
class TestNotConfusable:
    def test_current_and_synced_share_no_status_sentinel_or_moved_value(
        self, tmp_path: Path
    ) -> None:
        # Two INDEPENDENT remotes, so one clone can be genuinely current while
        # the other is genuinely behind — without rewinding either with a
        # `reset --hard`, which this repo's rules forbid even in a fixture.
        quiet_origin = make_origin(tmp_path, name="quiet")
        busy_origin = make_origin(tmp_path, name="busy")
        untouched = clone(tmp_path, quiet_origin, name="untouched")
        behind = clone(tmp_path, busy_origin, name="behind")
        advance_origin(tmp_path, n=2, name="busy")

        cur = scs.sync_repo(untouched)
        syn = scs.sync_repo(behind)

        assert cur.status == scs.STATUS_CURRENT and syn.status == scs.STATUS_SYNCED
        assert cur.status != syn.status
        assert cur.sentinel != syn.sentinel
        assert cur.moved == 0 and syn.moved == 2
        # Both exit 0 — which is exactly why the exit code cannot be the only
        # thing a caller reads, and why these other fields exist.
        assert cur.exit_code == syn.exit_code == 0

    def test_refused_dirty_is_NOT_reported_as_current(self, tmp_path: Path) -> None:
        """The specific false green: "nothing to do" standing in for "I refused".
        A dirty, behind clone must never share a status, sentinel or exit code
        with a clone that was genuinely up to date."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=1, path="shared.txt")
        (repo / "shared.txt").write_text("local\n", encoding="utf-8")

        res = scs.sync_repo(repo)

        assert res.status != scs.STATUS_CURRENT
        assert res.sentinel != scs.SENTINELS[scs.STATUS_CURRENT]
        assert res.exit_code != 0
        assert res.behind == 1, "and it must still report HOW stale it is"

    def test_every_sentinel_is_pairwise_distinct(self) -> None:
        phrases = list(scs.SENTINELS.values())
        assert len(set(phrases)) == len(phrases), f"duplicate sentinel: {phrases}"

    def test_no_sentinel_is_a_substring_of_another(self) -> None:
        """A grep for one sentinel must not match another's line. Substring
        containment is the way a "distinct" phrase set silently stops being one."""
        for a in scs.SENTINELS.values():
            for b in scs.SENTINELS.values():
                if a is not b:
                    assert a not in b, f"{a!r} is contained in {b!r}"

    def test_every_refusal_has_its_own_non_zero_exit_code(self) -> None:
        refusals = [s for s in scs.ALL_STATUSES if s.startswith("refused-")]
        codes = [scs.EXIT_CODES[s] for s in refusals]
        assert all(c > 2 for c in codes), "1 and 2 belong to the CLI, not a verdict"
        assert len(set(codes)) == len(codes), f"colliding refusal codes: {codes}"


# --------------------------------------------------------------------------- #
# THE LEDGER — pinned two-way, so a declaration cannot outlive its code path
# --------------------------------------------------------------------------- #
class TestTheStatusLedger:
    def test_every_status_has_a_sentinel_and_an_exit_code_both_ways(self) -> None:
        assert set(scs.SENTINELS) == set(scs.ALL_STATUSES)
        assert set(scs.EXIT_CODES) == set(scs.ALL_STATUSES)

    def test_the_module_docstring_names_every_status(self) -> None:
        """A header table that drifts from the code reads as coverage while
        providing none. Pinned against the code, not against a copy of itself."""
        doc = scs.__doc__ or ""
        for status in scs.ALL_STATUSES:
            const = "STATUS_" + status.upper().replace("-", "_")
            assert const in doc, f"{const} is not documented in the module header"
        for phrase in scs.SENTINELS.values():
            assert f'"{phrase}"' in doc, f"sentinel {phrase!r} is not in the header table"

    def test_the_CLI_docstring_names_every_refusal_code(self) -> None:
        doc = CLI.read_text(encoding="utf-8")
        for status in scs.ALL_STATUSES:
            if not status.startswith("refused-"):
                continue
            code = scs.EXIT_CODES[status]
            assert f"{code}   {status}" in doc, f"CLI header omits {code} {status}"

    def test_a_result_can_render_a_message_for_every_status(self) -> None:
        """`message()` branches per status; a status with no branch would fall
        through to a bare sentinel, which is a silent gap rather than a crash."""
        for status in scs.ALL_STATUSES:
            res = scs.SyncResult(repo="/x", status=status, upstream="origin/main")
            msg = res.message()
            assert msg.startswith("/x: ")
            assert scs.SENTINELS[status] in msg


# --------------------------------------------------------------------------- #
# THE DIRT POLICY — overlap, not a boolean
# --------------------------------------------------------------------------- #
class TestDirtIsAnOverlapQuestion:
    def test_NON_blocking_dirt_does_not_refuse_but_IS_reported(
        self, tmp_path: Path
    ) -> None:
        """The design decision that keeps this from being a permanently-red gate:
        the fleet's clones are chronically dirty, so refusing on ANY dirt would
        make the helper inert. Dirt that the ff cannot touch is advisory."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2, path="other.txt")
        (repo / "scratch.md").write_text("my notes\n", encoding="utf-8")  # untracked
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_SYNCED
        assert commit_count(repo) == before + 2
        assert res.blocking_paths == []
        assert "scratch.md" in res.advisory_paths, "non-blocking dirt is still REPORTED"
        assert (repo / "scratch.md").read_text(encoding="utf-8") == "my notes\n"

    def test_an_untracked_DIRECTORY_blocks_a_changed_path_beneath_it(
        self, tmp_path: Path
    ) -> None:
        """git reports `dir/`, not `dir/file`, so a plain set intersection scores
        this as no overlap and lets the merge run into it."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        seed = tmp_path / "origin-seed"
        (seed / "pkg").mkdir()
        (seed / "pkg" / "mod.py").write_text("upstream\n", encoding="utf-8")
        _sh("git", "add", "--", "pkg/mod.py", cwd=seed)
        _sh("git", "commit", "-q", "-m", "add pkg/mod.py", cwd=seed)
        _sh("git", "push", "-q", "origin", "main", cwd=seed)

        (repo / "pkg").mkdir()
        (repo / "pkg" / "mod.py").write_text("MINE\n", encoding="utf-8")
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY
        assert res.blocking_paths == ["pkg/"]
        assert commit_count(repo) == before
        assert (repo / "pkg" / "mod.py").read_text(encoding="utf-8") == "MINE\n"

    def test_a_STAGED_change_to_an_incoming_path_also_blocks(
        self, tmp_path: Path
    ) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=1, path="shared.txt")
        (repo / "shared.txt").write_text("staged\n", encoding="utf-8")
        _sh("git", "add", "--", "shared.txt", cwd=repo)
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY
        assert res.blocking_paths == ["shared.txt"]
        assert commit_count(repo) == before

    def test_a_RENAME_upstream_blocks_on_the_SOURCE_path_too(
        self, tmp_path: Path
    ) -> None:
        """🔴 Rename detection reports only the DESTINATION, but the ff writes
        BOTH ends — it deletes the source. A rename-detected path list omits the
        source, so local work sitting there scores as non-blocking and the run
        proceeds into a merge git will refuse. Asserting `refused-dirty` (and NOT
        `refused-ff-failed`) is what pins `--no-renames`."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        seed = tmp_path / "origin-seed"
        _sh("git", "mv", "shared.txt", "renamed.txt", cwd=seed)
        _sh("git", "commit", "-q", "-m", "rename shared.txt", cwd=seed)
        _sh("git", "push", "-q", "origin", "main", cwd=seed)
        (repo / "shared.txt").write_text("local work on the OLD name\n", encoding="utf-8")
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY, res.detail
        assert res.blocking_paths == ["shared.txt"]
        assert commit_count(repo) == before
        assert (repo / "shared.txt").read_text(encoding="utf-8") == (
            "local work on the OLD name\n"
        )

    def test_the_blocking_path_list_is_CAPPED_but_the_count_is_not(
        self, tmp_path: Path
    ) -> None:
        """A refusal a human cannot act on is barely better than a silent one, so
        the paths are named — but an unbounded list in a fleet report is noise."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        seed = tmp_path / "origin-seed"
        n = scs.DIRTY_PATH_CAP + 5
        for i in range(n):
            (seed / f"f{i:02d}.txt").write_text("upstream\n", encoding="utf-8")
        _sh("git", "add", "--", *[f"f{i:02d}.txt" for i in range(n)], cwd=seed)
        _sh("git", "commit", "-q", "-m", "many files", cwd=seed)
        _sh("git", "push", "-q", "origin", "main", cwd=seed)
        for i in range(n):
            (repo / f"f{i:02d}.txt").write_text("mine\n", encoding="utf-8")

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY
        assert res.blocking_count == n
        assert len(res.blocking_paths) == scs.DIRTY_PATH_CAP
        assert f"+{n - scs.DIRTY_PATH_CAP} more" in res.message()

    def test_it_never_stashes(self, tmp_path: Path) -> None:
        """🔴 `refs/stash` is repo-GLOBAL in these clones and a concurrent agent
        can pop it. A refusal that quietly stashed would look identical from the
        outside — so assert the stash stack is untouched, and that a PRE-EXISTING
        entry survives (a helper that dropped someone else's stash would pass a
        test that only checked its own)."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        (repo / "shared.txt").write_text("someone elses wip\n", encoding="utf-8")
        _sh("git", "stash", "push", "-q", "-m", "foreign", cwd=repo)
        before_stash = _sh("git", "stash", "list", cwd=repo)
        assert before_stash.strip(), "precondition: the fixture stash exists"

        advance_origin(tmp_path, n=2, path="shared.txt")
        (repo / "shared.txt").write_text("live local edit\n", encoding="utf-8")
        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIRTY
        assert _sh("git", "stash", "list", cwd=repo) == before_stash
        assert (repo / "shared.txt").read_text(encoding="utf-8") == "live local edit\n"


# --------------------------------------------------------------------------- #
# THE OTHER REFUSALS — each reachable, each with its own sentinel
# --------------------------------------------------------------------------- #
class TestTheEnvironmentalRefusals:
    def test_NOT_A_REPO(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        res = scs.sync_repo(plain)
        assert res.status == scs.STATUS_REFUSED_NOT_A_REPO
        assert res.exit_code == 7
        assert res.sentinel in res.message()

    def test_a_MISSING_PATH_is_not_reported_as_current(self, tmp_path: Path) -> None:
        res = scs.sync_repo(tmp_path / "gone")
        assert res.status == scs.STATUS_REFUSED_NOT_A_REPO
        assert res.exit_code != 0

    def test_DETACHED_head(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=1)
        _sh("git", "fetch", "-q", "origin", cwd=repo)
        _sh("git", "checkout", "-q", "--detach", "HEAD", cwd=repo)
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DETACHED
        assert res.exit_code == 6
        assert commit_count(repo) == before

    def test_NO_UPSTREAM_refuses_rather_than_guessing_a_mainline(
        self, tmp_path: Path
    ) -> None:
        """🔴 The reason this module does NOT reuse `git_mainline.resolve_base_ref`:
        a clone parked on a feature branch must not be fast-forwarded onto
        `origin/main`. That is a different question, and answering it here would
        silently abandon the branch's identity."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2)
        _sh("git", "checkout", "-q", "-b", "feature/no-upstream", cwd=repo)
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_NO_UPSTREAM
        assert res.exit_code == 5
        assert res.branch == "feature/no-upstream"
        assert commit_count(repo) == before

    def test_FETCH_FAILED_when_the_remote_is_gone(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=1)
        _sh("git", "remote", "set-url", "origin", str(tmp_path / "vanished.git"), cwd=repo)
        before = commit_count(repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_FETCH_FAILED
        assert res.exit_code == 8
        assert commit_count(repo) == before

    def test_NO_FETCH_measures_the_refs_as_they_stand(self, tmp_path: Path) -> None:
        """`--no-fetch` must be offline-safe: a dead remote is not a failure when
        the run was never going to reach it."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        _sh("git", "remote", "set-url", "origin", str(tmp_path / "vanished.git"), cwd=repo)

        res = scs.sync_repo(repo, fetch=False)

        assert res.status == scs.STATUS_CURRENT
        assert res.exit_code == 0

    def test_AHEAD_ONLY_is_diverged_not_current(self, tmp_path: Path) -> None:
        """behind == 0 but ahead > 0: HEAD and upstream genuinely differ, so
        `current` would be a lie even though there is nothing to fast-forward."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        (repo / "mine.txt").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "mine.txt", cwd=repo)
        _sh("git", "commit", "-q", "-m", "unpushed", cwd=repo)

        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_DIVERGED
        assert (res.ahead, res.behind) == (1, 0)
        assert res.exit_code == 4


# --------------------------------------------------------------------------- #
# THE LAST-LINE CHECKS — unreachable from a fixture, pinned through the seam
# --------------------------------------------------------------------------- #
class TestTheLastLineChecks:
    """Two guards a real git can never be persuaded to trip from a fixture.

    🔴 Both were found by MUTATING them and watching the whole suite stay green
    — `claude/RULES.md`: a guard that is merely unmutated is not a pinned guard,
    and a green sweep is only a claim about the mutations you imagined. They are
    reached here through the module's OWN git seam (`scs._git`), which is the
    narrowest thing that can produce the state git will not.
    """

    def test_a_merge_that_reports_SUCCESS_without_moving_HEAD_is_not_a_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact false green the module exists against, in its purest form:
        exit 0 from the merge, and HEAD exactly where it started. Reading that as
        `synced` would report a fast-forward that did not happen."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2)
        real_git = scs._git

        def fake_git(repo_arg, args, **kw):
            if args and args[0] == "merge":
                return subprocess.CompletedProcess(
                    args=["git", *args], returncode=0, stdout="", stderr=""
                )
            return real_git(repo_arg, args, **kw)

        monkeypatch.setattr(scs, "_git", fake_git)
        before = commit_count(repo)
        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_FF_FAILED
        assert res.sentinel == "ff refused by git"
        assert res.exit_code == 9
        assert res.status != scs.STATUS_SYNCED
        assert commit_count(repo) == before
        assert "did not move" in res.detail

    def test_a_merge_that_FAILS_is_reported_as_ff_failed_not_as_dirty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Its own status, never folded into `refused-dirty`: a pre-check that
        silently disagrees with the tool it predicts is the bug this surfaces."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2)
        real_git = scs._git

        def fake_git(repo_arg, args, **kw):
            if args and args[0] == "merge":
                return subprocess.CompletedProcess(
                    args=["git", *args], returncode=128, stdout="",
                    stderr="fatal: Not possible to fast-forward, aborting.",
                )
            return real_git(repo_arg, args, **kw)

        monkeypatch.setattr(scs, "_git", fake_git)
        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_REFUSED_FF_FAILED
        assert res.status != scs.STATUS_REFUSED_DIRTY
        assert res.exit_code == 9
        assert "Not possible to fast-forward" in res.detail

    def test_the_merge_is_invoked_with_FF_ONLY(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 A STRUCTURAL pin, and labelled as one. Dropping `--ff-only` is
        BEHAVIOURALLY invisible to every fixture here, because the `ahead > 0`
        guard already returned before the merge line is ever reached — so in
        every reachable state a plain `git merge` and a `--ff-only` merge do the
        same thing. `--ff-only` earns its place only against the race where the
        branch gains a commit between the measurement and the merge, which no
        fixture can stage; so the flag is pinned by inspecting the argv rather
        than by an outcome, and this docstring is the honest statement of what
        that does and does not buy."""
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=1)
        real_git = scs._git
        seen: list[list[str]] = []

        def recording_git(repo_arg, args, **kw):
            seen.append(list(args))
            return real_git(repo_arg, args, **kw)

        monkeypatch.setattr(scs, "_git", recording_git)
        res = scs.sync_repo(repo)

        assert res.status == scs.STATUS_SYNCED
        merges = [a for a in seen if a and a[0] == "merge"]
        assert merges == [["merge", "--ff-only", "origin/main"]], merges


# --------------------------------------------------------------------------- #
# FLEET SCOPE — several clones drift independently
# --------------------------------------------------------------------------- #
class TestTheFleet:
    def test_sync_many_classifies_each_clone_independently(
        self, tmp_path: Path
    ) -> None:
        quiet_origin = make_origin(tmp_path, name="quiet")
        busy_origin = make_origin(tmp_path, name="busy")
        a = clone(tmp_path, quiet_origin, name="a")  # nothing upstream: current
        b = clone(tmp_path, busy_origin, name="b")  # will fast-forward
        c = clone(tmp_path, busy_origin, name="c")  # will refuse: dirt overlaps
        (c / "shared.txt").write_text("dirty\n", encoding="utf-8")
        advance_origin(tmp_path, n=2, path="shared.txt", name="busy")

        run = scs.sync_many([str(a), str(b), str(c)])
        by_repo = {r.repo: r.status for r in run.results}

        assert by_repo[str(a)] == scs.STATUS_CURRENT
        assert by_repo[str(b)] == scs.STATUS_SYNCED
        assert by_repo[str(c)] == scs.STATUS_REFUSED_DIRTY
        assert len(run.results) == 3
        assert len({by_repo[str(p)] for p in (a, b, c)}) == 3, (
            "three clones off the same run must be classified independently"
        )

    def test_the_run_exit_code_is_the_WORST_repo_not_an_average(
        self, tmp_path: Path
    ) -> None:
        """🔴 A refusal must never be averaged away by successes: nine green
        clones and one that could not be synced is not a green fleet."""
        origin = make_origin(tmp_path)
        good = [clone(tmp_path, origin, name=f"g{i}") for i in range(3)]
        bad = clone(tmp_path, origin, name="bad")
        advance_origin(tmp_path, n=1, path="shared.txt")
        (bad / "shared.txt").write_text("dirty\n", encoding="utf-8")

        run = scs.sync_many([*(str(p) for p in good), str(bad)])

        assert run.exit_code == scs.EXIT_CODES[scs.STATUS_REFUSED_DIRTY]
        assert sum(1 for r in run.results if r.ok) == 3

    def test_worst_exit_code_of_an_EMPTY_run_is_zero_which_is_why_the_CLI_guards_it(
        self,
    ) -> None:
        assert scs.worst_exit_code([]) == 0


# --------------------------------------------------------------------------- #
# DISCOVERY — a default that is not printed is a hardcode wearing a costume
# --------------------------------------------------------------------------- #
class TestDiscovery:
    def test_it_finds_primary_clones_and_SKIPS_linked_worktrees(
        self, tmp_path: Path
    ) -> None:
        """A linked worktree's `.git` is a FILE holding `gitdir: …`. The audit
        found 43 of them nested under one clone; syncing one would move a branch
        somebody is working on."""
        root = tmp_path / "ws"
        root.mkdir()
        origin = make_origin(tmp_path)
        primary = clone(root, origin, name="primary")
        wt = root / "primary-wt"
        _sh("git", "worktree", "add", "-q", "-b", "topic", str(wt), cwd=primary)
        (root / "plain-dir").mkdir()

        scanned, found = scs.discover_clones([str(root)])

        assert scanned == (str(root),)
        assert str(primary) in found
        assert str(wt) not in found, "a linked worktree is not a base clone"
        assert str(root / "plain-dir") not in found

    def test_a_MISSING_root_is_scanned_and_yields_nothing_without_raising(
        self, tmp_path: Path
    ) -> None:
        scanned, found = scs.discover_clones([str(tmp_path / "nope")])
        assert scanned == (str(tmp_path / "nope"),)
        assert found == ()

    def test_DEVRC_CLONE_ROOTS_set_but_EMPTY_raises_rather_than_defaulting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 `os.environ.get(V) or DEFAULT` cannot tell UNSET from SET-BUT-EMPTY,
        and the fall-back here is the operator's entire workspace. Same shape
        `test_repo_path_defaults.py` pins for ship.sh / drift-check.sh."""
        monkeypatch.setenv(scs.CLONE_ROOTS_ENV, "")
        with pytest.raises(scs.EmptyRootsError):
            scs.discover_clones()

        monkeypatch.setenv(scs.CLONE_ROOTS_ENV, "   ")
        with pytest.raises(scs.EmptyRootsError):
            scs.discover_clones()

    def test_UNSET_still_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The other half of the pair: unset must keep working, or the guard has
        merely broken the feature it was protecting."""
        monkeypatch.delenv(scs.CLONE_ROOTS_ENV, raising=False)
        scanned, _ = scs.discover_clones()
        assert scanned == tuple(str(Path(r).expanduser()) for r in scs.DEFAULT_CLONE_ROOTS)

    def test_the_env_var_overrides_the_default_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        monkeypatch.setenv(scs.CLONE_ROOTS_ENV, f"{a}:{b}")
        scanned, _ = scs.discover_clones()
        assert scanned == (str(a), str(b))

    def test_sync_many_RECORDS_that_it_defaulted_and_what_it_chose(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        origin = make_origin(tmp_path)
        primary = clone(root, origin, name="only")

        run = scs.sync_many(None, roots=[str(root)], fetch=False)

        assert run.defaulted_from is not None
        scanned, found = run.defaulted_from
        assert scanned == (str(root),) and found == (str(primary),)

    def test_an_EXPLICIT_repo_list_records_no_default(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        run = scs.sync_many([str(repo)], fetch=False)
        assert run.defaulted_from is None


# --------------------------------------------------------------------------- #
# THE CLI — the surface a cron or another script actually calls
# --------------------------------------------------------------------------- #
def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=dict(os.environ, **GIT_ENV),
    )


class TestTheCLI:
    def test_it_exits_with_the_repo_status_code(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=1, path="shared.txt")
        (repo / "shared.txt").write_text("dirty\n", encoding="utf-8")

        proc = _cli(str(repo), cwd=tmp_path)

        assert proc.returncode == 3, proc.stdout + proc.stderr
        assert "working tree is dirty" in proc.stdout

    def test_a_run_that_examined_ZERO_repos_exits_2_NOT_0(
        self, tmp_path: Path
    ) -> None:
        """🔴 The positive control, at the CLI. "0 repositories" and "every
        repository was fine" are the same observable; a checker wired to nothing
        must not report success."""
        empty = tmp_path / "empty-root"
        empty.mkdir()
        proc = _cli("--roots", str(empty), cwd=tmp_path)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "examined 0 repositories" in proc.stderr

    def test_it_PRINTS_the_repos_it_defaulted_to(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        origin = make_origin(tmp_path)
        primary = clone(root, origin, name="only")

        proc = _cli("--roots", str(root), "--no-fetch", cwd=tmp_path)

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "no repo arguments" in proc.stdout
        assert str(root) in proc.stdout
        assert str(primary) in proc.stdout

    def test_json_output_carries_status_sentinel_and_exit_code(
        self, tmp_path: Path
    ) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)
        advance_origin(tmp_path, n=2)

        proc = _cli("--json", str(repo), cwd=tmp_path)
        payload = json.loads(proc.stdout)

        assert proc.returncode == 0
        assert payload["exit_code"] == 0
        (row,) = payload["repos"]
        assert row["status"] == "synced"
        assert row["sentinel"] == "fast-forwarded"
        assert row["moved"] == 2
        assert row["head_before"] != row["head_after"]

    def test_json_distinguishes_current_from_synced(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        repo = clone(tmp_path, origin)

        proc = _cli("--json", "--no-fetch", str(repo), cwd=tmp_path)
        (row,) = json.loads(proc.stdout)["repos"]

        assert row["status"] == "current"
        assert row["moved"] == 0
        assert row["sentinel"] != "fast-forwarded"

    def test_the_summary_tally_names_each_status(self, tmp_path: Path) -> None:
        origin = make_origin(tmp_path)
        good = clone(tmp_path, origin, name="good")
        bad = clone(tmp_path, origin, name="bad")
        advance_origin(tmp_path, n=1, path="shared.txt")
        (bad / "shared.txt").write_text("dirty\n", encoding="utf-8")

        proc = _cli(str(good), str(bad), cwd=tmp_path)

        assert proc.returncode == 3
        assert "synced=1" in proc.stdout
        assert "refused-dirty=1" in proc.stdout
        assert "(exit=3)" in proc.stdout

    def test_DEVRC_CLONE_ROOTS_set_but_empty_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        proc = subprocess.run(
            [sys.executable, str(CLI)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=dict(os.environ, **GIT_ENV, DEVRC_CLONE_ROOTS=""),
        )
        assert proc.returncode == 2
        assert "set but empty" in proc.stderr
