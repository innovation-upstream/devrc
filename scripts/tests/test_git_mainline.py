"""Direct tests for `scripts/lib/git_mainline.py` — "what is this repo's mainline?".

🔴 WHY THIS MODULE EXISTS, AND WHY IT HAS ITS OWN SUITE. The answer used to be a
four-name literal ladder open-coded at two sites inside `subsystem_touch.py`:

    BASE_REF_CANDIDATES = ("origin/main", "origin/master", "main", "master")

That ladder had already been extended once, reactively, the first time a repo
used `master`. On 2026-08-21 the next repo arrived — `homelab-infra`, mainline
`trunk` — and every consumer returned `no-base-ref` there, so the `--commit`
window escalation was INERT in exactly the repo it had been called for.

Two consumers now take the same answer from here (`subsystem_touch`'s commit
window and `handoff_doc`'s rule (h) currency check), which makes this a SEAM:
`claude/RULES.md` — "verified in isolation" is the new vacuous green, and the
defect lives in the seam nobody owns. So the ledger of importers is asserted
here, failing when the set grows OR shrinks, beside the behavioural cases.

Nothing here touches a real repository or the network: every fixture is a bare
repo plus a clone under pytest's tmp_path.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "scripts" / "lib" / "git_mainline.py"

# Hermetic git: the nix sandbox and the dev host must behave identically, so no
# ambient global/system config gets to decide whether a fixture can commit.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _load():
    spec = importlib.util.spec_from_file_location("git_mainline", MODULE)
    assert spec and spec.loader, MODULE
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gm = _load()


def _sh(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True,
        env=dict(os.environ, **GIT_ENV),
    )
    assert proc.returncode == 0, f"{args} failed: {proc.stderr or proc.stdout}"
    return proc.stdout


def clone_with_mainline(tmp_path: Path, mainline: str) -> Path:
    """A real clone of a real bare origin whose default branch is `mainline`.

    🔴 A REAL `git clone`, because `refs/remotes/origin/HEAD` — the ref the whole
    derivation rests on — is written by clone itself. Hand-writing that symref
    would test a shape invented here rather than the one the field repo has.
    """
    origin = tmp_path / f"{mainline}.git"
    _sh("git", "init", "-q", "--bare", "-b", mainline, str(origin), cwd=tmp_path)
    seed = tmp_path / f"{mainline}-seed"
    seed.mkdir()
    _sh("git", "init", "-q", "-b", mainline, cwd=seed)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _sh("git", "add", "--", "README.md", cwd=seed)
    _sh("git", "commit", "-q", "-m", "seed", cwd=seed)
    _sh("git", "remote", "add", "origin", str(origin), cwd=seed)
    _sh("git", "push", "-q", "origin", mainline, cwd=seed)
    work = tmp_path / f"{mainline}-work"
    _sh("git", "clone", "-q", str(origin), str(work), cwd=tmp_path)
    return work


class TestTheDerivationIsRead:
    def test_a_TRUNK_clone_derives_origin_trunk(self, tmp_path: Path) -> None:
        repo = clone_with_mainline(tmp_path, "trunk")
        assert gm.origin_head_ref(repo) == "origin/trunk"
        ref, ladder = gm.resolve_base_ref(repo)
        assert ref == "origin/trunk"
        assert ladder[0] == "origin/trunk", "the derived rung must be tried FIRST"

    def test_a_MAIN_clone_still_derives_origin_main(self, tmp_path: Path) -> None:
        """The other point. A derivation verified only on `trunk` proves nothing
        about the repos the literal ladder already got right."""
        repo = clone_with_mainline(tmp_path, "main")
        ref, _ = gm.resolve_base_ref(repo)
        assert ref == "origin/main"

    def test_a_repo_with_NO_origin_HEAD_falls_back(self, tmp_path: Path) -> None:
        """A clone can legitimately have no `origin/HEAD`: `git init` + `git
        remote add` + `git push` never writes one. The fallback is why the
        derivation is an addition rather than a replacement."""
        repo = tmp_path / "no-symref"
        repo.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=repo)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "f.txt", cwd=repo)
        _sh("git", "commit", "-q", "-m", "seed", cwd=repo)
        assert gm.origin_head_ref(repo) is None
        ref, ladder = gm.resolve_base_ref(repo)
        assert ref == "main" and ladder == gm.FALLBACK_BASE_REFS

    def test_a_NON_REPO_answers_None_and_never_raises(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert gm.origin_head_ref(plain) is None
        ref, ladder = gm.resolve_base_ref(plain)
        assert ref is None and ladder == gm.FALLBACK_BASE_REFS


class TestADanglingSymrefIsNeverBelieved:
    """🔴 NOT HYPOTHETICAL. Measured in `devrc` itself on 2026-08-21:
    `refs/remotes/origin/HEAD` pointed at `refs/remotes/origin/trunk` with no
    object behind it — a concurrent agent's fixture — while devrc's mainline is
    `main`. `git symbolic-ref` prints that target cheerfully at exit 0."""

    def _dangle(self, repo: Path) -> None:
        _sh("git", "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/trunk", cwd=repo)

    def test_the_raw_read_still_REPORTS_the_dangling_target(
        self, tmp_path: Path
    ) -> None:
        """`origin_head_ref` is documented as UNVALIDATED, and this pins that:
        the validation belongs to `resolve_base_ref`, in one place, not to every
        caller remembering to add it."""
        repo = clone_with_mainline(tmp_path, "main")
        self._dangle(repo)
        assert gm.origin_head_ref(repo) == "origin/trunk"
        assert subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
             "origin/trunk^{commit}"],
            capture_output=True, text=True,
        ).returncode != 0, "the fixture's dangling ref must not resolve"

    def test_resolution_FALLS_THROUGH_to_the_real_mainline(
        self, tmp_path: Path
    ) -> None:
        repo = clone_with_mainline(tmp_path, "main")
        self._dangle(repo)
        ref, ladder = gm.resolve_base_ref(repo)
        assert ref == "origin/main", "a dangling symref must not win"
        assert ladder[0] == "origin/trunk", "…but it must still have been TRIED"

    def test_the_LOCAL_counterpart_is_NOT_a_rung(self, tmp_path: Path) -> None:
        """🔴 THE NEAR-MISS. An earlier cut of this fix offered `trunk` after
        `origin/trunk`, reasoning that the fallback ladder has the same
        remote-then-local shape. Run against the real `devrc` that selected a
        stray local `trunk` sitting beside `main` — the same fixture that left
        the dangling symref — and the commit window came back with 11 commits off
        an unrelated branch: a plausible number, silently wrong, where the literal
        ladder it replaced had been RIGHT."""
        repo = clone_with_mainline(tmp_path, "main")
        self._dangle(repo)
        _sh("git", "branch", "trunk", "HEAD", cwd=repo)  # the decoy
        assert "trunk" not in gm.base_ref_ladder(repo)[1:], gm.base_ref_ladder(repo)
        assert gm.resolve_base_ref(repo)[0] == "origin/main"


class TestCommitsBehind:
    def test_it_counts_only_what_the_mainline_has(self, tmp_path: Path) -> None:
        repo = clone_with_mainline(tmp_path, "trunk")
        seed = tmp_path / "trunk-seed"
        for i in range(3):
            (seed / f"n{i}.txt").write_text("x\n", encoding="utf-8")
            _sh("git", "add", "--", f"n{i}.txt", cwd=seed)
            _sh("git", "commit", "-q", "-m", f"c{i}", cwd=seed)
        _sh("git", "push", "-q", "origin", "trunk", cwd=seed)
        _sh("git", "fetch", "-q", "origin", cwd=repo)
        assert gm.commits_behind(repo, "origin/trunk") == 3

    def test_a_PATHSPEC_narrows_it_to_one_file(self, tmp_path: Path) -> None:
        """The pathspec is the whole point for rule (h): a clone can be far
        behind on code with a perfectly current document, and warning on the
        repo-wide number would fire on nearly every agent worktree."""
        repo = clone_with_mainline(tmp_path, "trunk")
        seed = tmp_path / "trunk-seed"
        for name in ("code.py", "code2.py", "doc.md"):
            (seed / name).write_text("x\n", encoding="utf-8")
            _sh("git", "add", "--", name, cwd=seed)
            _sh("git", "commit", "-q", "-m", name, cwd=seed)
        _sh("git", "push", "-q", "origin", "trunk", cwd=seed)
        _sh("git", "fetch", "-q", "origin", cwd=repo)
        assert gm.commits_behind(repo, "origin/trunk") == 3
        assert gm.commits_behind(repo, "origin/trunk", path="doc.md") == 1
        assert gm.commits_behind(repo, "origin/trunk", path="never.md") == 0

    def test_an_UNCOUNTABLE_range_is_None_and_never_0(self, tmp_path: Path) -> None:
        """🔴 A ZERO AND AN UNANSWERED QUESTION MUST NOT SHARE A RETURN VALUE.
        `claude/RULES.md`: an empty result cannot distinguish two mechanisms, and
        `0 commits behind` reads as a clean bill of health. This is the guard's
        ONLY reachable exercise — `base_currency` verifies the ref before asking,
        so its own `doc_behind is None` branch is defensive and unreachable from
        that path; tested here instead of pretending it is covered there."""
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert gm.commits_behind(plain, "origin/main") is None
        repo = clone_with_mainline(tmp_path, "main")
        assert gm.commits_behind(repo, "refs/nope/does-not-exist") is None


class TestTheSeamIsOwned:
    def test_the_LEDGER_of_importers_is_pinned_both_ways(self) -> None:
        """🔴 A SEAM GUARD PINS A RELATIONSHIP, and fails when the set GROWS or
        SHRINKS. Two modules must agree about this repo's mainline; a third
        arriving without being enumerated here is a third opinion nobody
        compared, and one leaving means a site went back to open-coding it."""
        lib = REPO_ROOT / "scripts" / "lib"
        importers = {
            p.name
            for p in sorted(lib.glob("*.py"))
            if p.name != "git_mainline.py"
            and "git_mainline" in p.read_text(encoding="utf-8")
        }
        assert importers == {"subsystem_touch.py", "handoff_doc.py"}, importers

    def test_subsystem_touch_takes_its_FALLBACK_from_here(self) -> None:
        """Not merely an equal tuple — the SAME OBJECT as the `git_mainline` that
        `subsystem_touch` itself imported, so a re-declared copy that happens to
        hold the same four names fails here.

        ⚠ The comparison is against `sys.modules["git_mainline"]`, NOT this
        file's `gm`: this suite loads the module by path without registering it,
        so `gm` and the copy `subsystem_touch` imports are two distinct module
        objects with two distinct (equal) tuples. An `is` against `gm` therefore
        fails for a reason that has nothing to do with the invariant — measured,
        not reasoned: it did.
        """
        spec = importlib.util.spec_from_file_location(
            "subsystem_touch", REPO_ROOT / "scripts" / "lib" / "subsystem_touch.py"
        )
        assert spec and spec.loader
        st = importlib.util.module_from_spec(spec)
        # 🔴 REGISTERED BEFORE exec. `@dataclass` resolves a field's annotation
        # through `sys.modules[cls.__module__]`, so a module executed without
        # being registered raises `NoneType has no __dict__` from inside
        # dataclasses — a failure that looks like a defect in the module under
        # test and is entirely an artefact of how it was loaded.
        sys.modules.setdefault("subsystem_touch", st)
        try:
            spec.loader.exec_module(st)
            shared = sys.modules["git_mainline"]
            assert st.BASE_REF_CANDIDATES is shared.FALLBACK_BASE_REFS
            assert st.BASE_REF_CANDIDATES == gm.FALLBACK_BASE_REFS
        finally:
            sys.modules.pop("subsystem_touch", None)

    def test_the_module_is_tracked_by_git(self) -> None:
        """A new file the flake never sees deploys as an absence, silently — and
        both importers do a module-scope `import git_mainline`, so an untracked
        copy does not degrade the feature, it stops the tools starting."""
        assert MODULE.exists(), f"{MODULE} is missing from this tree"
        if not (REPO_ROOT / ".git").exists():
            return  # nix sandbox: the flake source is tracked files only
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--",
             "scripts/lib/git_mainline.py"],
            capture_output=True, text=True, env=dict(os.environ, **GIT_ENV),
        )
        assert out.stdout.strip() == "scripts/lib/git_mainline.py", (
            "scripts/lib/git_mainline.py is not tracked by git, so the flake "
            "omits it and the deployed handoff_doc.py / subsystem_touch.py raise "
            "ModuleNotFoundError on every run."
        )
