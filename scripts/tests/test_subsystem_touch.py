"""Tests for scripts/lib/subsystem_touch.py — the `/handoff`-side index writer.

WHAT IS BEING PROTECTED
-----------------------
`claudedocs/decision-subsystem-store-rejected-2026-08-11.md`: the subsystem store
works, but its ONLY writer is an infra-recon command, so 21 entries exist in one
scope while work spans ~12 repos. This module is the second writer — it reports
what a session touched so `/handoff` can propose an entry or a journal line.

🔴 THE FAILURE MODE IS A CONFIDENT ZERO, and it has FIVE distinct causes here:
no paths were collected; the store is absent; the repo has no scope dir yet; the
paths matched nothing; the paths matched something too weakly. All five render
as an empty proposal list. `claude/RULES.md` → "An EMPTY RESULT cannot
distinguish two mechanisms — go find the step that differs": `TouchReport.status`
is that step for four of them, `StoreMissingError` is the fifth (a broken
environment, not a reading), and `TestStatusIsTheDiscriminator` proves every
declared status is actually EMITTED — not merely declared — and that no two of
them are spelled the same.

🔴 THE STORE IS NEVER WRITTEN, AND THAT IS TESTED BEHAVIOURALLY, NOT BY GREP.
`~/.claude/analyze-service-index/` is curated, client-confidential and has no
off-machine backup. `TestNeverWrites` hashes a whole synthetic store tree either
side of every mode. A `grep` for `open(..., "w")` would be the "spelled rather
than structural" guard `claude/RULES.md` warns about — it passes while a
different spelling writes.

🔴 NO TEST HERE READS THE REAL STORE. Every fixture is synthetic, in `tmp_path`,
with names invented for this file. This repo is PUBLIC and
`scripts/testlib/client_host_scan.py` exists because six client subdomains had
already leaked into fixtures once. Shapes are reproduced; content never is.

🔴 REAL GIT, NOT A MOCK. The git window is the load-bearing design decision in
the module, and a mock of `subprocess` would test the mock. `git` is a pinned
`REQUIRED_TOOLS` entry in `run-tests.sh` and a `nativeBuildInputs` entry in
flake.nix's `checks.pytests`, so it is present in BOTH tiers; these tests build
real repositories in `tmp_path` with `git init`. Nothing here skips — the pinned
`EXPECTED_SKIPS` set in `run-tests.sh` is exact, so a skip added here breaks the
gate on purpose.

EVERY NEGATIVE CONTROL ASSERTS ITS OWN GUARD'S SENTINEL, and every guard is
broken on purpose in `TestMutationKillMatrix` — a control that passes because a
NEIGHBOURING guard fired is green for the wrong reason and stays green with the
guard it claims to test deleted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "lib" / "subsystem_touch.py"
HANDOFF_DOC = ROOT / "claude" / "skills" / "handoff" / "SKILL.md"
ANALYZE_DOC = ROOT / "claude" / "skills" / "analyze-service" / "SKILL.md"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.skills_mapping import (  # noqa: E402
    assert_skills_mapping_declared,
)

import subsystem_resolver as sr  # noqa: E402
import subsystem_touch as st  # noqa: E402


TODAY = "2026-08-11"  # fixed: the pure layer takes the date, it never reads a clock


# =============================================================================
# Synthetic store fixtures — realistic SHAPES, invented names.
# =============================================================================
#
# Field distinctness is deliberate (`claude/RULES.md`: "pick fixtures whose
# fields are pairwise distinct"). The scope name appears as no slug and no
# alias; no slug is another entry's alias except the one deliberate case.

SCOPE = "workbench-cfg"        # the repo-derived scope under test
OTHER_SCOPE = "hardware-notes"  # exists in the store, holds different entries


def _entry(service: str, scope: str, *, aliases=(), kind=None, created_by=None) -> str:
    lines = ["---", f"service: {service}", f"scope: {scope}"]
    if aliases:
        lines.append("aliases: [" + ", ".join(aliases) + "]")
    if kind:
        lines.append(f"kind: {kind}")
    if created_by:
        lines.append(f"created_by: {created_by}")
    lines += [
        "---",
        "",
        "## What it is",
        f"The {service} thing.",
        "",
        "## Pointers",
        "- somewhere — because",
        "",
        "## Nuance / work-history",
        "- 2026-01-01: an older bullet.",
        "",
    ]
    return "\n".join(lines)


def _make_store(root: Path) -> Path:
    """A store with two scopes and one deliberate ambiguity pair."""
    store = root / "index-store"
    a = store / SCOPE
    a.mkdir(parents=True)
    (a / "README.md").write_text("policy sheet, not an entry\n", encoding="utf-8")
    (a / "collector.md").write_text(
        _entry("collector", SCOPE, aliases=["telemetry-collector", "event_tap"]),
        encoding="utf-8",
    )
    (a / "status-bar.md").write_text(
        _entry("status-bar", SCOPE, aliases=["statusbar"]), encoding="utf-8"
    )
    # The ambiguity pair, from analyze-service/SKILL.md's own worked example
    # shape: one slug naming two KINDS of thing.
    (a / "weekly-digest.md").write_text(_entry("weekly-digest", SCOPE), encoding="utf-8")
    (a / "weekly-digest.process.md").write_text(
        _entry("weekly-digest", SCOPE, kind="process"), encoding="utf-8"
    )
    b = store / OTHER_SCOPE
    b.mkdir(parents=True)
    (b / "fan-curve.md").write_text(
        _entry("fan-curve", OTHER_SCOPE, created_by="handoff"), encoding="utf-8"
    )
    return store


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    return _make_store(tmp_path)


def _report(paths, store_root, scope=SCOPE, **kw) -> st.TouchReport:
    return st.build_report(
        st.caller_supplied(paths), store_root, scope, today=TODAY, **kw
    )


# =============================================================================
# Real-git helpers. No mocks: the git window is the design decision under test.
# =============================================================================


def _git_env(home: Path) -> dict:
    env = dict(os.environ)
    env.update(
        {
            # A hermetic identity + no ambient config: the sandbox has no global
            # gitconfig and the dev host's must not leak in either, or the two
            # tiers would be testing different things.
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    return env


def _run_git(repo: Path, *args: str, home: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=_git_env(home or repo.parent),
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _init_repo(tmp_path: Path, name: str = "some-repo", branch: str = "main") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _run_git(repo, "init", "-b", branch, home=tmp_path)
    _write(repo, "README.md", "seed\n")
    _run_git(repo, "add", "README.md", home=tmp_path)
    _run_git(repo, "commit", "-m", "seed", home=tmp_path)
    return repo


# =============================================================================
# Scope derivation — the worktree trap.
# =============================================================================


class TestScopeDerivation:
    """🔴 A worktree is not its own repo, and taking `--show-toplevel` literally
    would shard the store into one scope per agent worktree — each unresolvable
    from any later run. This repo runs dozens of them concurrently, so the bug
    would have been the NORMAL case, not an edge one."""

    def test_base_clone(self, tmp_path: Path) -> None:
        assert st.derive_scope("/w/some-repo", "/w/some-repo/.git") == "some-repo"

    def test_worktree_maps_to_the_SAME_scope_as_its_base_clone(self) -> None:
        base = st.derive_scope("/w/some-repo", "/w/some-repo/.git")
        wt = st.derive_scope(
            "/w/some-repo/.claude/worktrees/agent-a9f80ada5bf8837e4",
            "/w/some-repo/.git",
        )
        assert wt == base == "some-repo"

    def test_a_non_dot_git_common_dir_falls_back_to_the_repo_root(self) -> None:
        """A submodule's common dir is `<super>/.git/modules/<name>`; its parent
        basename is the meaningless `modules`, so the root's name is used."""
        assert (
            st.derive_scope("/w/super/sub", "/w/super/.git/modules/sub") == "sub"
        )

    def test_the_scope_is_normalized_by_the_SHARED_predicate(self) -> None:
        """Not a second normalizer: the store is addressed by normalized names on
        read and write, and two spellings of one repo would be two scopes."""
        assert st.derive_scope("/w/My_Repo", "/w/My_Repo/.git") == "my-repo"
        assert st.derive_scope("/w/My_Repo", "/w/My_Repo/.git") == sr.normalize_ref("My_Repo")

    def test_live_git_agrees(self, tmp_path: Path) -> None:
        """The unit tests above pass strings; this one asks REAL git for them,
        in a REAL worktree, because the whole guard rests on what
        `--git-common-dir` actually returns."""
        repo = _init_repo(tmp_path, "widget-cfg")
        wt = tmp_path / "wt-agent-1234"
        _run_git(repo, "worktree", "add", "-b", "topic", str(wt), home=tmp_path)

        def scope_of(d: Path) -> str:
            common = _run_git(
                d, "rev-parse", "--path-format=absolute", "--git-common-dir", home=tmp_path
            ).strip()
            top = _run_git(d, "rev-parse", "--show-toplevel", home=tmp_path).strip()
            return st.derive_scope(top, common)

        assert scope_of(repo) == "widget-cfg"
        assert scope_of(wt) == "widget-cfg"
        # The control: the naive derivation really would have differed, so the
        # guard above is not pinning something that was never at risk.
        assert sr.normalize_ref(wt.name) != "widget-cfg"


# =============================================================================
# The git path window.
# =============================================================================


class TestGitPathWindow:
    def test_positive_control_uncommitted_and_untracked_are_seen(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "README.md", "changed\n")
        _write(repo, "apps/thing/new.yaml")
        src = st.collect_git_paths(repo)
        assert set(src.paths) == {"README.md", "apps/thing/new.yaml"}
        assert src.kind == "git"

    def test_gitignored_files_are_not_paths(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, ".gitignore", "secrets/\n")
        _write(repo, "secrets/token.txt", "not a subsystem\n")
        src = st.collect_git_paths(repo)
        assert "secrets/token.txt" not in src.paths
        assert ".gitignore" in src.paths  # positive control: it saw SOMETHING

    def test_this_branch_s_commits_are_in_the_window(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "apps/ingest/deploy.yaml")
        _run_git(repo, "add", "apps/ingest/deploy.yaml", home=tmp_path)
        _run_git(repo, "commit", "-m", "add ingest", home=tmp_path)
        src = st.collect_git_paths(repo)
        assert "apps/ingest/deploy.yaml" in src.paths
        assert src.window == "branch"
        assert src.base_ref == "main"

    def test_another_branch_s_commits_are_NOT_in_the_window(self, tmp_path: Path) -> None:
        """The bound is what makes git usable at all: without it every report in
        a long-lived repo would name every subsystem anyone ever touched."""
        repo = _init_repo(tmp_path)
        _run_git(repo, "checkout", "-b", "someone-else", home=tmp_path)
        _write(repo, "apps/unrelated/deploy.yaml")
        _run_git(repo, "add", "apps/unrelated/deploy.yaml", home=tmp_path)
        _run_git(repo, "commit", "-m", "unrelated", home=tmp_path)
        _run_git(repo, "checkout", "main", home=tmp_path)
        _run_git(repo, "checkout", "-b", "mine", home=tmp_path)
        _write(repo, "apps/mine/deploy.yaml")
        _run_git(repo, "add", "apps/mine/deploy.yaml", home=tmp_path)
        _run_git(repo, "commit", "-m", "mine", home=tmp_path)

        src = st.collect_git_paths(repo)
        assert "apps/mine/deploy.yaml" in src.paths, "positive control: my own commit is missing"
        assert "apps/unrelated/deploy.yaml" not in src.paths

    def test_on_the_base_ref_the_window_DEGRADES_and_SAYS_SO(self, tmp_path: Path) -> None:
        """An empty commit window and a branch with no commits are different
        facts; the second must not be reported as the first."""
        repo = _init_repo(tmp_path)
        _write(repo, "apps/thing/x.yaml")
        src = st.collect_git_paths(repo)
        assert src.window == "worktree"
        assert any("merge-base" in n for n in src.notes)
        assert "committed work is NOT represented" in src.caveat
        assert src.paths == ("apps/thing/x.yaml",)  # it still collected

    def test_no_base_ref_at_all_is_named_not_silently_empty(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, "odd-repo", branch="trunk")
        _write(repo, "apps/thing/x.yaml")
        src = st.collect_git_paths(repo)
        assert src.base_ref is None
        assert src.window == "worktree"
        assert any("no base ref among" in n for n in src.notes)

    def test_a_path_with_a_space_survives_intact(self, tmp_path: Path) -> None:
        """`-z` on every invocation. A split on whitespace would turn one path
        into two plausible components and manufacture refs out of nothing."""
        repo = _init_repo(tmp_path)
        _write(repo, "apps/two words/x.yaml")
        src = st.collect_git_paths(repo)
        assert "apps/two words/x.yaml" in src.paths

    def test_called_with_a_SUBDIRECTORY_the_paths_stay_in_ONE_frame(
        self, tmp_path: Path
    ) -> None:
        """🔴 An audit finding, and a silent one. The three git commands do NOT
        share a path frame: `diff --name-only` is always repo-root-relative,
        `ls-files --others` is cwd-relative AND cwd-scoped. Called with a
        subdirectory the two disagree — an untracked `scripts/tests/x.py` comes
        back as `x.py` (a manufactured root-level component) while untracked
        files elsewhere vanish entirely."""
        repo = _init_repo(tmp_path)
        _write(repo, "scripts/tests/untracked_here.py")
        _write(repo, "apps/roster/untracked_elsewhere.yaml")
        _write(repo, "README.md", "changed\n")

        from_root = st.collect_git_paths(repo)
        from_subdir = st.collect_git_paths(repo / "scripts" / "tests")

        # Same answer from either directory — one frame, rooted at the toplevel.
        assert set(from_subdir.paths) == set(from_root.paths)
        assert "scripts/tests/untracked_here.py" in from_subdir.paths
        # the manufactured root-level component the old code produced
        assert "untracked_here.py" not in from_subdir.paths
        # the file outside the cwd that cwd-scoping would have dropped
        assert "apps/roster/untracked_elsewhere.yaml" in from_subdir.paths

    def test_excluded_paths_are_dropped_AND_counted(self, tmp_path: Path) -> None:
        """🔴 `/handoff` writes its doc in step 2 and asks what changed in step
        4, so without this the handoff doc is untracked in its own window and
        `claudedocs` is a nomination on every single run — the ritual nominating
        its own artifact. Exclusions are ACCOUNTED, never silently dropped: a
        path that vanishes with no note is a smaller number with no reason."""
        repo = _init_repo(tmp_path)
        _write(repo, "claudedocs/handoff-topic.md")
        _write(repo, "apps/roster/a.yaml")

        without = st.collect_git_paths(repo)
        assert "claudedocs/handoff-topic.md" in without.paths  # positive control

        with_ = st.collect_git_paths(repo, exclude=["claudedocs/handoff-topic.md"])
        assert "claudedocs/handoff-topic.md" not in with_.paths
        assert "apps/roster/a.yaml" in with_.paths
        assert any("excluded 1 caller-named path" in n for n in with_.notes)

    def test_excluding_a_path_that_is_not_there_is_silent(self, tmp_path: Path) -> None:
        """No note when nothing was dropped — an accounting line for a
        non-event trains the reader to skip the ones that matter."""
        repo = _init_repo(tmp_path)
        _write(repo, "apps/roster/a.yaml")
        src = st.collect_git_paths(repo, exclude=["claudedocs/handoff-nope.md"])
        assert src.paths == ("apps/roster/a.yaml",)
        assert not any("excluded" in n for n in src.notes)

    def test_a_failed_git_RAISES_rather_than_returning_zero_paths(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain-dir"
        not_a_repo.mkdir()
        with pytest.raises(st.GitError) as exc:
            st.collect_git_paths(not_a_repo)
        assert "git command failed" in str(exc.value)

    def test_the_caveat_never_claims_session_authorship(self, tmp_path: Path) -> None:
        """The proxy is declared, not rounded away. `claude/RULES.md`: a claim is
        stated at the scope you actually measured."""
        repo = _init_repo(tmp_path)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "a/b.yaml")
        _run_git(repo, "add", "a/b.yaml", home=tmp_path)
        _run_git(repo, "commit", "-m", "c", home=tmp_path)
        caveat = st.collect_git_paths(repo).caveat
        assert "what this BRANCH touched" in caveat
        assert "NOT what this SESSION touched" in caveat


# =============================================================================
# 🔴 THE POSITIVE CONTROL PAIR.
# =============================================================================


class TestPositiveControl:
    """`claude/RULES.md` → "Positive control — can it ever observe the thing?"

    A reassuring zero is indistinguishable from a helper wired to nothing. The
    two path sets below are the SAME shape at the SAME depth with the SAME
    filenames in the SAME scope against the SAME store — only the subsystem
    directory differs, and it names nothing."""

    POSITIVE = [
        "hosts/bench/apps/collector/unit.nix",
        "hosts/bench/apps/collector/config.toml",
        "hosts/bench/apps/collector/README.md",
    ]
    NEGATIVE = [
        "hosts/bench/apps/unlisted-widget/unit.nix",
        "hosts/bench/apps/unlisted-widget/config.toml",
        "hosts/bench/apps/unlisted-widget/README.md",
    ]

    def test_the_pair(self, store: Path) -> None:
        pos = _report(self.POSITIVE, store)
        neg = _report(self.NEGATIVE, store)

        # THE PAIR, reported together: 1 known under the positive control,
        # 0 under test — on the same code path.
        assert len(pos.known) == 1, "positive control produced no match — wired to nothing"
        assert len(neg.known) == 0

        assert pos.status == "resolved"
        assert neg.status == "no-match"
        assert [m.entry.ref for m in pos.known] == ["collector"]
        assert pos.entry_files["collector"] == "collector.md"

    def test_the_negative_zero_is_ACCOUNTED_for(self, store: Path) -> None:
        neg = _report(self.NEGATIVE, store)
        assert neg.association is not None
        assert neg.association.considered_paths == tuple(self.NEGATIVE)
        assert neg.association.unmatched_paths == tuple(self.NEGATIVE)
        assert neg.below_threshold == ()
        assert neg.ambiguous == ()

    def test_the_shared_components_name_nothing(self, store: Path) -> None:
        """`hosts`, `apps`, `unit.nix` appear in BOTH sets. If any of them
        resolved, the negative control above would be vacuous."""
        index = sr.load_index(store)
        for ref in ("hosts", "bench", "apps", "unit", "unit.nix", "readme", "readme.md"):
            assert sr.resolve_ref(ref, index, SCOPE) is None, ref

    def test_an_alias_reaches_the_entry_too(self, store: Path) -> None:
        """The alias tier is a second way in; if only filenames ever matched,
        every `_`-spelled directory in the fleet would silently miss."""
        rep = _report(
            ["src/event_tap/a.py", "src/event_tap/b.py"], store
        )
        assert [m.entry.ref for m in rep.known] == ["collector"]
        assert rep.known[0].evidence[0].tier == "alias"
        assert rep.known[0].evidence[0].matched_alias == "event_tap"


# =============================================================================
# The five zeros.
# =============================================================================


class TestStatusIsTheDiscriminator:
    """Several mechanisms produce an empty proposal list; `status` names which.

    Four of them are statuses. A missing store is the fifth mechanism and is
    deliberately NOT a status — see `test_a_missing_store_RAISES_and_is_not_a_status`."""

    def test_looked_at_nothing(self, store: Path) -> None:
        rep = _report([], store)
        assert rep.status == "looked-at-nothing"
        assert rep.association is None
        assert not rep.writes_proposed

    def test_a_missing_store_RAISES_and_is_not_a_status(self, tmp_path: Path) -> None:
        """🔴 There is no `no-store` status. An absent store root is a broken
        environment, not a reading — and a status constant nothing could emit
        would make `STATUS_PRECEDENCE` read as N reachable outcomes when one of
        them is a phantom. That is the "a declaration is not a code path" shape
        this module argues against elsewhere; it was one here until an audit
        pointed at it."""
        with pytest.raises(st.StoreMissingError) as exc:
            _report(["a/b.yaml", "a/c.yaml"], tmp_path / "absent")
        assert "store root not found" in str(exc.value)
        assert "no-store" not in st.STATUS_PRECEDENCE

    def test_EVERY_declared_status_is_actually_emitted(self, store: Path, tmp_path: Path) -> None:
        """The pin that keeps the tuple honest: each value must come back from a
        real `build_report` call, so no member can rot into a phantom."""
        emitted = {
            _report([], store).status,
            _report(["apps/roster/a.yaml", "apps/roster/b.yaml"], store, scope="brand-new").status,
            _report(["src/collector/a.py", "src/collector/b.py"], store).status,
            _report(["docs/a.md", "notes/b.md"], store).status,
        }
        assert emitted == set(st.STATUS_PRECEDENCE)

    def test_scope_absent_is_NOT_an_error(self, store: Path) -> None:
        """🔴 The most load-bearing decision in the module. Under the old writer
        a repo with no entries was an error; under this one it is the ordinary
        first run in every repo that is not the infra repo. An exception here
        would make the intended case the failing case."""
        rep = _report(["apps/roster/a.yaml", "apps/roster/b.yaml"], store, scope="brand-new-repo")
        assert rep.status == "scope-absent"
        assert [n.ref for n in rep.nominations][0] == "roster"

    def test_resolved(self, store: Path) -> None:
        rep = _report(["src/collector/a.py", "src/collector/b.py"], store)
        assert rep.status == "resolved"

    def test_no_match(self, store: Path) -> None:
        rep = _report(["docs/a.md", "notes/b.md"], store)
        assert rep.status == "no-match"

    def test_the_spellings_are_pairwise_distinct(self) -> None:
        """A discriminator whose values collide discriminates nothing."""
        assert len(set(st.STATUS_PRECEDENCE)) == len(st.STATUS_PRECEDENCE) == 4

    def test_looked_at_nothing_OUTRANKS_a_missing_store(self, tmp_path: Path) -> None:
        """Precedence, asserted rather than left to call order: with no paths,
        no store condition can change the answer, so the store is never even
        consulted — which is also what keeps an absent store from being reported
        as a matching failure."""
        rep = _report([], tmp_path / "definitely-absent")
        assert rep.status == "looked-at-nothing"

    def test_an_empty_window_and_a_real_miss_do_not_render_alike(self, store: Path) -> None:
        """🔴 The whole point. `claude/RULES.md`: distinguish "nothing touched an
        entry" from "nothing was looked at"."""
        empty = st.render_text(_report([], store))
        miss = st.render_text(_report(["docs/a.md", "notes/b.md"], store))
        assert "NOTHING WAS LOOKED AT" in empty
        assert "NOTHING WAS LOOKED AT" not in miss
        assert "NOTHING RESOLVED" in miss
        assert "NOTHING RESOLVED" not in empty


# =============================================================================
# Ambiguity — reported, never resolved, never nominated.
# =============================================================================


class TestAmbiguity:
    PATHS = ["etc/weekly-digest/a.yaml", "etc/weekly-digest/b.yaml"]

    def test_it_is_reported_and_nothing_is_proposed_for_it(self, store: Path) -> None:
        rep = _report(self.PATHS, store)
        assert len(rep.ambiguous) == 1
        amb = rep.ambiguous[0]
        assert amb.ref == "weekly-digest"
        assert amb.tier == "filename"
        assert set(amb.candidates) == {"weekly-digest.md", "weekly-digest.process.md"}
        assert [m.entry.ref for m in rep.known] == []

    def test_an_ambiguous_ref_is_NEVER_nominated(self, store: Path) -> None:
        """Nominating it would propose creating a THIRD entry for a name that
        already names two — the resolver's "ambiguity errors, never shadows"
        rule, undone one layer up."""
        rep = _report(self.PATHS, store)
        assert "weekly-digest" not in [n.ref for n in rep.nominations]
        # Positive control on the same call: nomination DID run and DID produce
        # something, so the absence above is a decision and not a dead path.
        assert [n.ref for n in rep.nominations] == ["etc"]

    def test_the_renderer_says_write_nothing(self, store: Path) -> None:
        text = st.render_text(_report(self.PATHS, store))
        assert "AMBIGUOUS — write NOTHING" in text
        assert "weekly-digest.md" in text and "weekly-digest.process.md" in text


# =============================================================================
# Nominations.
# =============================================================================


class TestNominations:
    def test_positive_and_negative_control(self, store: Path) -> None:
        """The pair: a cluster that MUST nominate, and one path short of it."""
        two = _report(["apps/roster/a.yaml", "apps/roster/b.yaml"], store)
        one = _report(["apps/roster/a.yaml"], store)
        assert [n.ref for n in two.nominations][0] == "roster"
        assert one.nominations == ()

    def test_min_paths_is_the_SHARED_default_not_a_new_constant(self, store: Path) -> None:
        """One rule, one place: a second threshold here would drift from the
        resolver's, and a name nominated under one would never match under the
        other."""
        assert st.DEFAULT_MIN_PATHS is sr.DEFAULT_MIN_PATHS
        rep = _report(["apps/roster/a.yaml", "apps/roster/b.yaml"], store)
        assert rep.min_paths == sr.DEFAULT_MIN_PATHS == 2

    def test_the_deeper_component_outranks_the_umbrella_on_the_SAME_paths(
        self, store: Path
    ) -> None:
        """When several components cover exactly the same paths, the deeper one
        is the more specific name for that set.

        ⚠ The fixture nests THREE deep on purpose. At two levels the fan-out key
        already separates `apps` from `ingest`, so the depth key would decide
        nothing and its mutation test would be unkillable. Here `apps` and
        `ingest` both fan out into exactly one child, so DEPTH is the only thing
        left to order them — and alphabetical order disagrees with it
        (`apps` < `ingest`), which is what gives the assertion its bite."""
        paths = ["apps/ingest/svc/a.yaml", "apps/ingest/svc/b.yaml"]
        rep = _report(paths, store)
        assert [n.ref for n in rep.nominations] == ["svc", "ingest", "apps"]
        assert [n.depth for n in rep.nominations] == [2, 1, 0]
        assert sorted(["ingest", "apps"]) == ["apps", "ingest"], "the fixture lost its bite"

    def test_the_TOP_LEVEL_UMBRELLA_does_not_win_on_count(self, store: Path) -> None:
        """🔴 The second half of replacing a stoplist, and an audit finding:
        coherence fixed the recurring-filename case and left its mirror open. A
        top-level directory covers everything beneath it, so it wins on count
        every time — on this module's own PR diff the top five were all
        `scripts`/`skills`/`claude`-shaped, and none is a thing anyone journals
        against.

        `src` here covers FOUR paths to `roster`/`paging`'s two, and still loses:
        it fans out into two subdirectories, so it is an index of subsystems
        rather than one."""
        rep = _report(
            ["src/roster/a.py", "src/roster/b.py", "src/paging/c.py", "src/paging/d.py"],
            store,
        )
        refs = [n.ref for n in rep.nominations]
        by_ref = {n.ref: n for n in rep.nominations}

        # positive control: `src` IS a candidate with the higher count, so its
        # position is a ranking decision and not a dropped path.
        assert by_ref["src"].path_count == 4
        assert by_ref["src"].fans_out is True
        assert by_ref["roster"].path_count == 2
        assert by_ref["roster"].fans_out is False

        assert refs.index("roster") < refs.index("src")
        assert refs.index("paging") < refs.index("src")

    def test_a_LEAF_directory_is_not_penalised_for_holding_several_files(
        self, store: Path
    ) -> None:
        """Fan-out counts SUBDIRECTORIES, not files. Counting files would
        penalise every leaf directory for being a directory — the exact
        opposite of the intent."""
        rep = _report(["apps/roster/a.yaml", "apps/roster/b.yaml", "apps/roster/c.yaml"], store)
        by_ref = {n.ref: n for n in rep.nominations}
        assert by_ref["roster"].fans_out is False
        assert by_ref["roster"].path_count == 3
        assert [n.ref for n in rep.nominations][0] == "roster"

    def test_ranking_is_deterministic_across_input_order(self, store: Path) -> None:
        """A re-run over the same session must produce the same proposal."""
        paths = ["src/roster/a.py", "src/paging/c.py", "src/roster/b.py", "src/paging/d.py"]
        first = [n.ref for n in _report(paths, store).nominations]
        second = [n.ref for n in _report(list(reversed(paths)), store).nominations]
        assert first == second

    def test_the_list_is_CAPPED(self, store: Path) -> None:
        """`analyze-service/SKILL.md`: "propose at most ~5-7 candidates, never a
        raw match list" — a confirm gate a human stops reading is not a gate."""
        paths = [f"d{i}/sub{i}/x.py" for i in range(20)] + [f"d{i}/sub{i}/y.py" for i in range(20)]
        rep = _report(paths, store)
        assert len(rep.nominations) == st.DEFAULT_NOMINATION_LIMIT == 5
        assert len(rep.nominations) < len(set(p.split("/")[0] for p in paths))

    def test_an_EXISTING_entry_is_never_nominated(self, store: Path) -> None:
        """The invariant, stated as a property. It holds because nominations are
        drawn only from `unmatched_paths` — a path lands there only when NO
        component of it resolved — so the re-check inside `nominate` is
        redundant-but-kept rather than a live guard (see its docstring). Asserted
        anyway: it is the property a future caller passing a different path set
        would break."""
        rep = _report(
            ["src/collector/a.py", "src/collector/b.py", "apps/roster/c.yaml", "apps/roster/d.yaml"],
            store,
        )
        assert [m.entry.ref for m in rep.known] == ["collector"]
        assert "collector" not in [n.ref for n in rep.nominations]
        assert "roster" in [n.ref for n in rep.nominations]

    def test_a_filename_stem_can_be_nominated(self, store: Path) -> None:
        """A file named for the thing is a good candidate; it is ranked at the
        last component's depth rather than dropped for not being a directory."""
        rep = _report(["lib/paging.py", "tests/paging.py"], store)
        assert "paging" in [n.ref for n in rep.nominations]
        assert rep.nominations[0].ref == "paging"
        assert rep.nominations[0].coherent is False  # spread across directories

    def test_the_TOP_NONCOHERENT_ref_is_never_CUT_by_the_limit(self, store: Path) -> None:
        """🔴 `Nomination.coherent` promises non-coherent refs are "ranked below,
        not dropped". As a PRIMARY sort key it dropped them — an audit measured a
        real cross-directory subsystem landing 11th against a limit of 5, i.e.
        the docstring was false about its own code.

        Fixed by reserving the last slot, not by demoting coherence: demoting it
        re-opens the recurring-filename case it exists to close."""
        # Five coherent directory clusters, plus ONE subsystem spread across
        # `lib/` and `tests/` — the shape the promise is about.
        paths = [f"c{i}/a{i}.py" for i in range(5)] + [f"c{i}/b{i}.py" for i in range(5)]
        paths += ["lib/paging.py", "tests/paging.py"]
        rep = _report(paths, store)
        refs = [n.ref for n in rep.nominations]

        assert len(refs) == st.DEFAULT_NOMINATION_LIMIT
        # Straight truncation WOULD have cut it: it sorts behind every coherent
        # ref, and there are more coherent refs than slots. That is the control
        # proving the reservation did something.
        straight = [n.ref for n in st.nominate(
            rep.association, sr.load_index(store), min_paths=2, limit=99
        )][: st.DEFAULT_NOMINATION_LIMIT]
        assert "paging" not in straight
        assert "paging" in refs
        assert refs[-1] == "paging"

    def test_the_reservation_is_a_NO_OP_when_nothing_was_cut(self, store: Path) -> None:
        """The ordinary case must be untouched — a reservation that always fires
        would silently displace a legitimate fifth candidate."""
        rep = _report(["apps/roster/a.yaml", "apps/roster/b.yaml"], store)
        assert len(rep.nominations) < st.DEFAULT_NOMINATION_LIMIT
        assert [n.ref for n in rep.nominations] == ["roster", "apps"]

    def test_the_reservation_is_a_NO_OP_when_a_noncoherent_ref_already_made_the_cut(
        self, store: Path
    ) -> None:
        rep = _report(["lib/paging.py", "tests/paging.py"], store)
        assert [n.ref for n in rep.nominations] == ["paging", "paging.py"]

    def test_a_RECURRING_GENERIC_FILENAME_does_not_outrank_a_real_directory(
        self, store: Path
    ) -> None:
        """🔴 The pair that motivated `coherent`, and a live defect found by
        writing it: on count alone, `values` covered SIX paths and outranked
        every directory, while those six sat in three DIFFERENT subsystems — the
        one thing `values` is certainly not.

        Fixed structurally, with no stoplist of "generic" names: a subsystem is
        a place in the tree, so a candidate for one is a ref whose covered paths
        agree on where that place is."""
        paths = [
            "apps/roster/values.yaml",
            "apps/roster/kustomization.yaml",
            "apps/paging/values.yaml",
            "apps/paging/kustomization.yaml",
            "apps/ledger/values.yaml",
            "apps/ledger/kustomization.yaml",
        ]
        # limit lifted so the ORDERING is what is under test, not the cap: with
        # the default cap the loser is simply absent, and "absent" would pass
        # whether the ranking worked or the list was truncated by luck.
        rep = _report(paths, store, limit=20)
        refs = [n.ref for n in rep.nominations]
        by_ref = {n.ref: n for n in rep.nominations}

        # positive control: `values` IS a candidate the matcher can see at all —
        # so its position below is a ranking decision, not a dead path.
        assert by_ref["values"].path_count == 3
        assert by_ref["values"].coherent is False
        assert by_ref["apps"].path_count == 6
        assert by_ref["apps"].coherent is True

        # THE PAIR: the coherent directory outranks the higher-count filename.
        assert refs.index("apps") < refs.index("values")
        for subsystem in ("ledger", "paging", "roster"):
            assert refs.index(subsystem) < refs.index("values"), subsystem


# =============================================================================
# Below threshold — the zero that is NOT a miss.
# =============================================================================


class TestBelowThreshold:
    def test_it_is_reported_not_dropped(self, store: Path) -> None:
        rep = _report(["src/collector/only.py", "docs/other.md"], store)
        assert [m.entry.ref for m in rep.below_threshold] == ["collector"]
        assert rep.known == ()
        assert rep.status == "no-match"

    def test_the_renderer_does_NOT_claim_nothing_named_an_entry(self, store: Path) -> None:
        """🔴 A live defect this test was written from: the tail said "none named
        an entry" while an entry HAD been named and had merely stayed under the
        threshold. A wrong explanation of a zero forecloses the next question."""
        text = st.render_text(_report(["src/collector/only.py", "docs/other.md"], store))
        assert "NOTHING CLEARED THE THRESHOLD" in text
        assert "NOTHING RESOLVED" not in text
        assert "none named an entry" not in text
        # positive control on the OTHER branch of the same tail
        other = st.render_text(_report(["docs/a.md", "notes/b.md"], store))
        assert "NOTHING RESOLVED" in other
        assert "NOTHING CLEARED THE THRESHOLD" not in other


# =============================================================================
# 🔴 The store is never written.
# =============================================================================


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        if p.is_file():
            h.update(p.read_bytes())
        h.update(b"\0\0")
    return h.hexdigest()


class TestNeverWrites:
    """Behavioural, not spelled. A grep for `open(..., "w")` passes while a
    different spelling writes; hashing the tree either side does not care how
    a write is spelled."""

    def test_the_hasher_can_observe_a_change(self, store: Path) -> None:
        """Positive control on the INSTRUMENT: a tree hash that never moves is
        indistinguishable from one wired to a constant."""
        before = _tree_hash(store)
        (store / SCOPE / "collector.md").write_text("mutated\n", encoding="utf-8")
        assert _tree_hash(store) != before

    @pytest.mark.parametrize(
        "paths,scope",
        [
            (["src/collector/a.py", "src/collector/b.py"], SCOPE),   # resolved
            (["docs/a.md", "notes/b.md"], SCOPE),                     # no-match
            (["apps/roster/a.yaml", "apps/roster/b.yaml"], "brand-new"),  # scope-absent
            (["etc/weekly-digest/a.yaml", "etc/weekly-digest/b.yaml"], SCOPE),  # ambiguous
            ([], SCOPE),                                              # looked-at-nothing
        ],
        ids=["resolved", "no-match", "scope-absent", "ambiguous", "looked-at-nothing"],
    )
    def test_every_report_mode_leaves_the_store_byte_identical(
        self, store: Path, paths, scope
    ) -> None:
        before = _tree_hash(store)
        rep = _report(paths, store, scope=scope)
        st.render_text(rep)
        json.dumps(st.report_json(rep))
        assert _tree_hash(store) == before

    def test_the_census_leaves_the_store_byte_identical(self, store: Path) -> None:
        before = _tree_hash(store)
        st.render_census(st.census(store))
        assert _tree_hash(store) == before

    def test_the_template_creates_no_file(self, store: Path) -> None:
        before = _tree_hash(store)
        st.new_entry_template("roster", SCOPE, today=TODAY)
        assert _tree_hash(store) == before


# =============================================================================
# The proposal shapes.
# =============================================================================


class TestProposalShapes:
    """Literal expectations, hand-written from the schema and the live corpus's
    bullet style — never read back out of the implementation."""

    def test_the_journal_shape_is_the_EXISTING_style(self) -> None:
        line = st.journal_line_shape("2026-08-11")
        assert line.startswith("- 2026-08-11: ")

    def test_the_renderer_says_where_the_bullet_goes(self, store: Path) -> None:
        text = st.render_text(_report(["src/collector/a.py", "src/collector/b.py"], store))
        assert "insert as the FIRST bullet under `## Nuance / work-history`." in text
        assert f"{SCOPE}/collector.md" in text

    def test_the_new_entry_template_is_MINIMAL_and_fail_safe(self) -> None:
        t = st.new_entry_template("roster", SCOPE, today=TODAY)
        assert t.startswith("---\n")
        assert "service: roster\n" in t
        assert f"scope: {SCOPE}\n" in t
        # 🔴 The fail-safe default, written EXPLICITLY. `public` is a deliberate
        # operator claim a writer may never infer.
        assert "sensitivity: client-confidential\n" in t
        assert "sensitivity: public" not in t
        assert "created_by: handoff\n" in t
        assert "## What it is" in t
        assert "## Pointers" in t
        # NOT the full schema — the strain test found the rich sections came out
        # empty for want of evidence.
        assert "## Config" not in t
        assert "depends_on" not in t
        assert "type:" not in t

    def test_the_template_offers_the_test_stem_ALIAS(self) -> None:
        """🔴 Matching is exact normalized-component equality, so a test file
        `test_<slug>.py` has the stem `test-<slug>` and does NOT reach `<slug>`.
        "The module plus its test" — the most common two-file change there is —
        therefore counts ONE path and stays under `min_paths` forever.

        The per-entry fix is an alias, and an alias nobody is told about is no
        fix at all: this writer creates every one of its entries from this
        template, so the affordance has to be IN it. Prefix-stripping in
        `path_refs` was the other option and stays rejected — it is the shared
        predicate inside the doc's hashed region and `/analyze-service` consumes
        it too."""
        t = st.new_entry_template("roster-sync", SCOPE, today=TODAY)
        assert "# aliases: [roster_sync, test_roster_sync]" in t
        # COMMENTED, so it is an affordance and not a wrong claim: an alias
        # asserted before anyone checked it is a live mis-address.
        assert "\naliases:" not in t

    def test_the_commented_alias_line_does_not_become_a_real_field(self) -> None:
        """The parser must skip it — a `#` line read as data would give every
        entry two aliases nobody wrote."""
        fm = sr.parse_front_matter(st.new_entry_template("roster-sync", SCOPE, today=TODAY))
        assert "aliases" not in fm
        assert fm["service"] == "roster-sync"
        # positive control: the parser IS reading this front matter at all
        assert fm["created_by"] == "handoff"

    def test_the_template_slug_is_normalized_by_the_shared_predicate(self) -> None:
        assert "service: my-new-thing\n" in st.new_entry_template(
            sr.normalize_ref("My New_Thing"), SCOPE, today=TODAY
        )

    def test_the_template_parses_back_as_a_valid_entry(self) -> None:
        """🔴 The seam. A template that the resolver cannot read produces an
        entry no later run can ever address — and each half tested alone would
        be green. `claude/RULES.md` → "the defect lives in the SEAM nobody
        owns"."""
        text = st.new_entry_template("roster", SCOPE, today=TODAY)
        fm = dict(sr.parse_front_matter(text))
        fm["filename"] = "roster.md"
        entry = sr.SubsystemEntry.from_mapping(fm)
        assert entry.slug == "roster"
        assert entry.scope == sr.normalize_ref(SCOPE)
        assert entry.ref == "roster"

    def test_a_written_template_is_RESOLVABLE_end_to_end(self, store: Path) -> None:
        """The other half of the seam: write the template into a scope dir, load
        the store from disk, and confirm the paths that nominated it now MATCH
        it. Without this, "nominated" and "addressable" are two claims and only
        one is tested."""
        paths = ["apps/roster/a.yaml", "apps/roster/b.yaml"]
        assert _report(paths, store).known == ()  # before
        (store / SCOPE / "roster.md").write_text(
            st.new_entry_template("roster", SCOPE, today=TODAY), encoding="utf-8"
        )
        after = _report(paths, store)
        assert [m.entry.ref for m in after.known] == ["roster"]
        assert after.status == "resolved"


# =============================================================================
# The census — what makes the experiment falsifiable.
# =============================================================================


class TestCensus:
    def test_the_pair(self, store: Path) -> None:
        """Positive control on the instrument: it MUST be able to see a stamped
        entry, or a future "0 handoff entries" would be unreadable."""
        c = st.census(store)
        assert c.by_writer.get("handoff") == 1, "the census cannot see a stamped entry"
        assert c.by_writer.get("analyze-service") is None  # none exist yet — a real zero

    def test_unstamped_entries_are_their_OWN_bucket(self, store: Path) -> None:
        """🔴 Never folded into a writer. The 21 live entries predate the stamp;
        attributing them to `analyze-service` would be an inference, and the
        whole point of the field is to stop inferring."""
        c = st.census(store)
        assert c.by_writer[st.UNSTAMPED] == 4
        assert c.total == 5

    def test_it_counts_per_scope_which_is_what_the_gate_asks(self, store: Path) -> None:
        """The decision doc's reopening gate reads a per-scope count, so the
        instrument reports one. The original entries-outside-one-scope threshold
        was met and superseded on 2026-08-13 by a COVERAGE question (does the
        index cover the repos where work happens); both read this same per-scope
        breakdown, so this test is unchanged. The criterion itself stays in the
        decision doc and is deliberately not restated here."""
        c = st.census(store)
        assert c.by_scope == {SCOPE: 4, OTHER_SCOPE: 1}
        assert c.scopes_with_stamped_entries[OTHER_SCOPE] == {"handoff": 1}

    def test_readmes_are_not_entries(self, store: Path) -> None:
        """A store-policy sheet counted as an entry would inflate every number
        the gate reads."""
        assert st.census(store).by_scope[SCOPE] == 4  # 5 .md files, one is README

    def test_it_moves_when_an_entry_is_added(self, store: Path) -> None:
        """The count must be able to CHANGE — a census wired to a constant would
        report the same numbers forever and the experiment would never resolve."""
        before = st.census(store)
        (store / SCOPE / "roster.md").write_text(
            st.new_entry_template("roster", SCOPE, today=TODAY), encoding="utf-8"
        )
        after = st.census(store)
        assert after.total == before.total + 1
        assert after.by_writer["handoff"] == before.by_writer["handoff"] + 1

    def test_a_missing_store_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(st.StoreMissingError) as exc:
            st.census(tmp_path / "absent")
        assert "store root not found" in str(exc.value)


# =============================================================================
# The CLI.
# =============================================================================


class TestCli:
    def _run(self, args, capsys, stdin: str | None = None):
        if stdin is not None:
            import io

            old = sys.stdin
            sys.stdin = io.StringIO(stdin)
            try:
                rc = st.main(args)
            finally:
                sys.stdin = old
        else:
            rc = st.main(args)
        return rc, capsys.readouterr()

    def test_json_mode_is_machine_readable(self, store: Path, capsys) -> None:
        rc, cap = self._run(
            ["--store", str(store), "--scope", SCOPE, "--paths-from", "-",
             "--today", TODAY, "--json"],
            capsys,
            stdin="src/collector/a.py\nsrc/collector/b.py\n",
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["status"] == "resolved"
        assert payload["known"][0]["ref"] == "collector"
        assert payload["known"][0]["file"] == "collector.md"
        assert payload["writer_id"] == "handoff"
        assert payload["source"]["kind"] == "caller"

    def test_a_bad_paths_from_value_is_REJECTED_not_defaulted(
        self, store: Path, capsys
    ) -> None:
        rc, cap = self._run(
            ["--store", str(store), "--scope", SCOPE, "--paths-from", "wat", "--today", TODAY],
            capsys,
        )
        assert rc == 2
        assert "--paths-from must be" in cap.err

    def test_a_missing_store_exits_3_naming_the_sentinel(self, tmp_path: Path, capsys) -> None:
        rc, cap = self._run(
            ["--store", str(tmp_path / "absent"), "--scope", SCOPE, "--paths-from", "-",
             "--today", TODAY],
            capsys,
            stdin="a/b.yaml\na/c.yaml\n",
        )
        assert rc == 3
        assert "store root not found" in cap.err

    def test_an_absolute_path_is_REJECTED_with_the_resolver_s_sentinel(
        self, store: Path, capsys
    ) -> None:
        """An absolute path drags `home`, `<user>`, `workspace` into the
        component set and manufactures matches. The resolver rejects it; the CLI
        must surface that rather than swallow it into a zero."""
        rc, cap = self._run(
            ["--store", str(store), "--scope", SCOPE, "--paths-from", "-", "--today", TODAY],
            capsys,
            stdin="/home/someone/workspace/repo/a.py\n/home/someone/workspace/repo/b.py\n",
        )
        assert rc == 3
        assert "invalid repo-relative path" in cap.err

    def test_census_mode(self, store: Path, capsys) -> None:
        rc, cap = self._run(["--store", str(store), "--census"], capsys)
        assert rc == 0
        assert "handoff: 1" in cap.out
        assert st.UNSTAMPED in cap.out

    def test_template_mode(self, store: Path, capsys) -> None:
        rc, cap = self._run(
            ["--store", str(store), "--scope", SCOPE, "--template", "Roster_Sync",
             "--today", TODAY],
            capsys,
        )
        assert rc == 0
        assert "service: roster-sync" in cap.out
        assert "created_by: handoff" in cap.out

    def test_end_to_end_against_a_REAL_repo(self, tmp_path: Path, capsys) -> None:
        """The whole path: real git → derived scope → real store → report. Every
        layer above is tested in isolation, and `claude/RULES.md` is explicit
        that "verified in isolation" is the new vacuous green."""
        repo = _init_repo(tmp_path, SCOPE)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "src/collector/a.py")
        _write(repo, "src/collector/b.py")
        _run_git(repo, "add", "src", home=tmp_path)
        _run_git(repo, "commit", "-m", "work", home=tmp_path)
        store = _make_store(tmp_path / "s")

        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["scope"] == SCOPE, "scope was not derived from the repo"
        assert payload["status"] == "resolved"
        assert payload["known"][0]["ref"] == "collector"
        assert payload["source"]["kind"] == "git"
        assert payload["source"]["window"] == "branch"


# =============================================================================
# The doc/code pair — the OTHER half of this predicate is prose.
# =============================================================================


class TestSkillDocsArePinned:
    """The helper is inert unless `/handoff` calls it, and the write protocol
    lives in prose because its executor is an LLM. Deleting the step would leave
    a tested module that nothing invokes — which is precisely the failure mode
    the decision doc measured (six commands never invoked once)."""

    HANDOFF_SENTENCES: list[tuple[str, str]] = [
        ("scripts/lib/subsystem_touch.py", "the step actually calls this module"),
        ("It **never writes**", "the helper's read-only contract, stated to its caller"),
        ("Write only on explicit confirm, diff first", "the confirm gate"),
        ("re-read the file and re-apply to current bytes", "no concurrent append is clobbered"),
        ("Never silent-mutate.", "the invariant carried over from analyze-service"),
        ("pointers, not copies", "the bloat rule"),
        ("never persist live status", "the anti-bloat rule that matters most"),
        (
            "persist the *derivation method and what a stale reading looks like*",
            "the liveness convention",
        ),
        ("Write the file and run no git command", "store safety"),
        ("Do not demand the full schema", "a thin entry that exists beats a rich one"),
        ("write nothing", "ambiguity writes nothing"),
        (
            "no path was examined at all",
            "the empty window is never reported as a matching failure",
        ),
        ("created_by: handoff", "the falsifiability stamp reaches the written file"),
        # 🔴 Added after an audit: the step told the agent WHAT to write and
        # never WHERE. The absolute target appeared nowhere in this file — the
        # agent saw it only on the tool's `store:` line, and `--template` prints
        # the body and nothing else. An agent inferring "next to the handoff
        # doc" puts client-confidential content into a PUBLIC repo.
        (
            "`~/.claude/analyze-service-index/<scope>/<slug>.md`",
            "the absolute write target, outside every repo",
        ),
        (
            "Never anywhere in the working tree",
            "the negative half of the target — devrc is PUBLIC",
        ),
        # 🔴 SUPERSEDES the old pin, which named a literal per-scope path:
        # "Read `~/.claude/analyze-service-index/<scope>/README.md` before
        # writing there". Measured 2026-08-13, only 1 of the store's 5 scopes has
        # that file, so the instruction was unfollowable in 80% of cases and an
        # agent meeting the gap had nothing to fall back on. The step now sends
        # the writer to the file the PROBE named, which is resolved
        # deterministically by `governing_policy` — so the pin follows the
        # tooling instead of a path that usually is not there.
        (
            "Read the policy file the probe named on its `policy:` line",
            "the governing policy sheet, resolved by the tool rather than assumed",
        ),
        (
            "do not go looking for one it did not name",
            "🔴 the 80%-absent case: an unfollowable instruction invites invention",
        ),
        (
            "--validate <path-you-just-wrote>",
            "🔴 the write-time parse check — the writer finds its own defect, "
            "not a different tool in a later session",
        ),
        (
            "if the message prints a `RECOVER —` block, run the command it gives you",
            "🔴 a refusal with no route out leaves the store broken until a human looks",
        ),
        (
            "Run the command it printed, not one you compose",
            "🔴 the blocking file is often in ANOTHER scope, where a composed "
            "--validate reports clean and changes nothing",
        ),
        (
            "Any non-zero exit ⇒ print the stderr line verbatim and write NOTHING",
            "a broken instrument is not a reading",
        ),
        (
            "Do **not** fall back to recollection",
            "the failure mode a non-zero exit invites",
        ),
        (
            "if a `NO ENTRY` block is printed",
            "nominations are keyed on the BLOCK, not on one status",
        ),
        (
            "It appears alongside `resolved` too",
            "why: entries must still accrue when something already matches",
        ),
        ("on decline, discard", "the analyze-service clause that was dropped"),
        (
            "use `Edit` anchored on `## Nuance / work-history`, not `Write`",
            "no whole-file retype of a curated unbacked-up entry",
        ),
        (
            "Emit this BEFORE step 4's confirm gate, unconditionally",
            "the kickoff block is the deliverable and must not sit behind a y/N",
        ),
        ("--exclude claudedocs/handoff-", "the ritual does not nominate its own artifact"),
        (
            "in particular the `test_<slug>` stem",
            "the alias that makes module-plus-its-test reach the entry",
        ),
        (
            "the **first-entry case, not a failure**",
            "scope-absent is the reason this step exists, not an error",
        ),
        # 🔴 The `already there` display is inert unless the step tells the agent
        # to READ it. Showing prior bullets and leaving "is this notable?" as a
        # feeling is the state this change started from, moved one layer up: the
        # instruction has to name the comparison, or the display is decoration.
        (
            "check your proposed bullet against them — this is a comparison, not a feeling",
            "the judgement call is a check against what is on screen",
        ),
        (
            "A bullet that adds nothing to what is on screen ⇒ propose nothing",
            "declining is the named, normal outcome",
        ),
        (
            "the heading has to be created as part of the append",
            "an entry with no work-history section has no `Edit` anchor yet",
        ),
        # 🔴 Measured twice on two new scopes (2026-08-12): the store's hourly
        # timer `git init`s, seeds an identity and commits a brand-new scope dir,
        # with no remote. Without the second half an agent that finds no `.git`
        # cannot tell "waiting for the timer" from "silently not backed up" —
        # which is the reading one session actually reached, at the cost of a
        # round trip.
        (
            "do not create the repository yourself",
            "the no-git rule covers a brand-new scope directory too",
        ),
        (
            "unversioned for up to an hour",
            "the normal window, so an absent `.git` is not read as a lost entry",
        ),
        # 🔴 The session source. The module is inert unless the step passes the
        # token, and there is no environment variable for it — so if these
        # sentences go, the tool silently reverts to the git window that
        # captured nothing on its first real run.
        (
            "--session <session-uuid>",
            "the step actually passes the session token",
        ),
        (
            "`<session-uuid>` is the basename of your scratchpad directory",
            "WHERE the agent gets the token — the one fact it cannot look up",
        ),
        (
            "Never pass a UUID you are unsure of",
            "the token is validated, and a wrong one must not be retried into passing",
        ),
        (
            "fails with a named error rather than silently reporting another "
            "session's paths",
            "the no-fallback contract, stated to its caller",
        ),
        # 🔴 The fallback is CONDITIONAL, and the unconditional version was
        # wrong for one of the two cases. "Drop --session and use the git
        # window" is right for a missing/stale/unreadable/wrong uuid; for a cwd
        # mismatch the session ran in ANOTHER repo, so this repo's branch window
        # is empty too and the advice routes the agent to a second source that
        # structurally cannot answer. A rule that reads correct and dead-ends is
        # the exact shape this step exists to avoid.
        (
            "`transcript cwd does not match` ⇒ do NOT fall back to the git window",
            "the cwd case is exempted from the git-window fallback BY NAME",
        ),
        (
            "Go to `--pr`/`--commit` over what you landed here",
            "the cwd case is given the source that CAN answer, not just a refusal",
        ),
        (
            "blind to work that has already merged",
            "why the git fallback is a fallback",
        ),
        # 🔴 The cross-repo window. The tool now ANSWERS a case the step used to
        # describe as a dead end, and an executor still holding the old rule
        # would treat a normal run as a failure — or worse, read a floor as a
        # complete list. Both halves are pinned: that the window exists, and
        # what its count means.
        (
            "the `session-absolute` window",
            "the cross-repo case has a window, not only a refusal",
        ),
        (
            "that window is a **floor**, not a list",
            "🔴 its count is a lower bound — the relative paths are excluded, "
            "not counted, so how much is missing is unknown",
        ),
        (
            "a **relative** path in a transcript is relative to its own session's cwd",
            "🔴 the guard's REAL scope, replacing a claim about *every* path that "
            "was false and threw away the absolute ones",
        ),
        (
            "Read the `caveat:` line before you write anything",
            "each source understates in its own direction",
        ),
        (
            "does **not** include what a **subagent** edited",
            "the session window's largest measured blind spot",
        ),
        # 🔴 The PR source. The module is inert unless the step passes the
        # numbers, and — unlike a session uuid — nothing on the machine knows
        # them; only the agent that opened the PRs does. If these sentences go,
        # the one source that can see a subagent's work is never invoked.
        (
            "--pr <n>[,<n>...]",
            "the step actually passes the PRs it landed",
        ),
        (
            "you know exactly which ones, and nothing else in the toolchain does",
            "WHERE the numbers come from — the fact only the agent has",
        ),
        (
            "only** source that sees a **subagent's** work",
            "why the PR run exists at all",
        ),
        (
            "A PR's file list is what the BRANCH LANDED, not what this session "
            "touched — never describe it as this session's work",
            "🔴 the crux: a PR-derived set must never be given session attribution",
        ),
        (
            "attribute it to the branch or the PR, not to \"this session\"",
            "the attribution rule stated as an instruction, not a caveat to read",
        ),
        (
            "Run it twice; never merge the two path sets",
            "the compose decision, stated to the executor",
        ),
        (
            "one caveat that is wrong about half its members",
            "WHY they do not compose — the reason, not just the rule",
        ),
        (
            "none of them ever returns an empty path set",
            "the network surface can fail, but never into a silent zero",
        ),
        (
            "Closed-unmerged PRs are refused by name",
            "which PR states are acceptable input",
        ),
        # 🔴 The COMMIT source. The one window that reaches a repo which lands
        # work without pull requests, or forbids committing in the primary
        # clone — where the other two are blind BY CONSTRUCTION (measured: 25
        # session paths outside the cwd and 0 inside; 144 of 200 mainline
        # commits with no PR). Like the PR numbers and unlike a session uuid,
        # nothing on the machine knows the shas; only the agent that made them
        # does. If these sentences go, the source is inert.
        (
            "--commit <sha>[,<sha>...]",
            "the step actually passes the shas it created",
        ),
        (
            "You know the shas you just made; nothing else in the toolchain does",
            "WHERE the shas come from — the fact only the agent has",
        ),
        (
            "This window is what those COMMITS changed",
            "🔴 the window's identity: neither a session nor a branch",
        ),
        (
            "refused by name; pass a merge's side commits, or use `--pr`",
            "the merge decision, surfaced to the executor with its alternative",
        ),
        (
            "run it separately, never merge the path sets",
            "the compose rule, restated for the third source",
        ),
    ]

    def test_EVERY_emitted_status_has_a_bullet_in_the_skill(self) -> None:
        """🔴 A status the tool prints and the skill never mentions leaves the
        agent to improvise at exactly the moment it is about to write into a
        client-confidential store. Derived from `STATUS_PRECEDENCE` rather than
        hand-listed, so a status added later cannot quietly go uncovered."""
        doc = HANDOFF_DOC.read_text(encoding="utf-8")
        for status in st.STATUS_PRECEDENCE:
            assert f"`{status}`" in doc, (
                f"claude/skills/handoff/SKILL.md never mentions the `{status}` status, "
                f"which subsystem_touch.build_report can emit."
            )

    def test_the_kickoff_block_precedes_the_confirm_gate(self) -> None:
        """Structural, not a phrase: a user who walks away from the y/N must
        still have the deliverable. Asserted on ORDER in the file, because the
        pin above only proves the sentence exists somewhere."""
        doc = HANDOFF_DOC.read_text(encoding="utf-8")
        kickoff = doc.index("**Output a kickoff block**")
        index_step = doc.index("**Record what this session touched")
        gate = doc.index("append this to the index? (y/N)")
        assert kickoff < index_step < gate

    @pytest.mark.parametrize(
        "sentence,why", HANDOFF_SENTENCES, ids=[w for _, w in HANDOFF_SENTENCES]
    )
    def test_handoff_step_sentence(self, sentence: str, why: str) -> None:
        doc = HANDOFF_DOC.read_text(encoding="utf-8")
        assert sentence in doc, (
            f"claude/skills/handoff/SKILL.md no longer contains the sentence pinning {why}.\n"
            f"  missing: {sentence!r}\n"
            f"  Either restore it or change scripts/lib/subsystem_touch.py in the SAME\n"
            f"  commit. The module cannot enforce a protocol its only caller stopped\n"
            f"  following, and the drift is silent: the step simply stops happening."
        )

    def test_the_pin_can_report_absence(self) -> None:
        """Negative control on the pin itself: a check against a doc that happens
        to contain everything is indistinguishable from one pointed at the wrong
        file."""
        doc = HANDOFF_DOC.read_text(encoding="utf-8")
        assert "a sentence deliberately absent from the handoff skill" not in doc

    def test_the_pinned_docs_are_the_DEPLOYED_ones(self) -> None:
        """Pinning a file that does not ship would be a vacuous green."""
        for d in (HANDOFF_DOC, ANALYZE_DOC):
            assert d.exists(), f"the pinned doc is gone: {d}"
            assert d.name == "SKILL.md"
            assert d.parent.parent.name == "skills"
        # Shared predicate — declared-and-not-switched-off only; see
        # testlib/skills_mapping.py for what it does NOT check, and why.
        assert_skills_mapping_declared(ROOT / "nix" / "home.nix")


class TestEntrySchemaAgreement:
    """`created_by:` was added to the hashed `entry-schema` block of
    analyze-service/SKILL.md. `test_subsystem_resolver.py` re-pinned that hash;
    this is the BEHAVIOURAL half of the same claim — a hash update read against
    nothing is the one way to make that guard worthless."""

    def test_the_field_is_declared_in_the_schema(self) -> None:
        doc = ANALYZE_DOC.read_text(encoding="utf-8")
        assert "`created_by:` — which writer created the entry" in doc
        assert "absent means the entry predates the stamp" in doc

    def test_the_resolver_IGNORES_it_rather_than_rejecting_it(self) -> None:
        """It is PROVENANCE, not identity: an entry must stay addressable
        without it, and an entry carrying it must not be malformed."""
        fm = dict(sr.parse_front_matter(st.new_entry_template("roster", SCOPE, today=TODAY)))
        assert fm["created_by"] == "handoff", "parse_front_matter dropped the field"
        entry = sr.SubsystemEntry.from_mapping({**fm, "filename": "roster.md"})
        assert entry.ref == "roster"
        # and without it, unchanged — so nothing depends on its presence
        without = {k: v for k, v in fm.items() if k != "created_by"}
        assert sr.SubsystemEntry.from_mapping({**without, "filename": "roster.md"}) == entry

    def test_both_writer_ids_are_named_in_the_schema(self) -> None:
        doc = ANALYZE_DOC.read_text(encoding="utf-8")
        for writer in st.KNOWN_WRITERS:
            assert f"`{writer}`" in doc, f"the schema does not name the writer {writer!r}"

    def test_analyze_service_stamps_itself_on_a_new_file(self) -> None:
        """Otherwise the census could not tell "created before the stamp" from
        "created by the other writer", and the experiment would not resolve."""
        doc = ANALYZE_DOC.read_text(encoding="utf-8")
        assert "stamp `created_by: analyze-service` in the front matter" in doc


class TestNoRealStoreIsRead:
    """🔴 No test here may touch `~/.claude/analyze-service-index/`: it is
    client-confidential, has no backup, and is rewritten hourly by an autocommit
    timer. Every fixture in this file is built under `tmp_path`, and every call
    is passed an explicit store root — the module's own default is never
    exercised, which is what these two assert."""

    def test_the_real_store_root_is_outside_this_repo(self) -> None:
        """A default that resolved inside the checkout would put client-sensitive
        content one `git add` away from a PUBLIC repo."""
        assert ROOT not in st.DEFAULT_STORE_ROOT.parents
        assert not str(st.DEFAULT_STORE_ROOT).startswith(str(ROOT))

    def test_the_default_points_where_the_skill_says_it_does(self) -> None:
        assert st.DEFAULT_STORE_ROOT.name == "analyze-service-index"
        assert st.DEFAULT_STORE_ROOT.parent.name == ".claude"
        assert f"~/.claude/{st.DEFAULT_STORE_ROOT.name}" in ANALYZE_DOC.read_text(
            encoding="utf-8"
        )


# =============================================================================
# THE SESSION SOURCE — the window git structurally cannot see.
# =============================================================================
#
# 🔴 WHY IT EXISTS, IN ONE MEASUREMENT: on this tool's FIRST real invocation it
# captured nothing from the session it was built during, because that session
# landed all its work through merged PRs — by `/handoff` time `git diff HEAD` was
# empty and HEAD sat at the merge-base. The tool said so honestly. Honest about
# seeing nothing and useful are different properties.
#
# 🔴 EVERY FIXTURE HERE IS SYNTHETIC. The session id below is invented and is
# not a real UUID from any host; this repo is PUBLIC. No transcript under
# `~/.claude/projects/` is read by any test in this file — `TestNoRealTranscript`
# is the guard, and `CLAUDE_PROJECTS_DIR` is repointed at `tmp_path` for every
# test that exercises id lookup.

SESSION_ID = "11111111-1111-4111-8111-111111111111"  # invented, not a real session
OTHER_SESSION_ID = "22222222-2222-4222-8222-222222222222"


def _edit_block(file_path: str) -> dict:
    return {
        "type": "tool_use",
        "name": "Edit",
        "input": {"file_path": file_path, "old_string": "a\n", "new_string": "b\n"},
    }


def _assistant(files, *, cwd: str | None = None, sidechain: bool = False) -> dict:
    obj = {
        "type": "assistant",
        "timestamp": "2026-08-12T10:00:00.000Z",
        "message": {
            "role": "assistant",
            "model": "test-model",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [_edit_block(f) for f in files],
        },
    }
    if cwd is not None:
        obj["cwd"] = cwd
    if sidechain:
        obj["isSidechain"] = True
    return obj


def _write_transcript(
    path: Path,
    cwd: str,
    files,
    *,
    sidechain_files=(),
    trailing_partial: bool = False,
    only_partial: bool = False,
    age_seconds: float = 0.0,
) -> Path:
    """A synthetic transcript in the real JSONL shape.

    `trailing_partial` cuts the LAST line mid-object, which is the ordinary state
    of a transcript that is being appended to while it is read — the exact
    condition `/handoff` creates by running this tool during the session the
    transcript belongs to.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(_assistant(files, cwd=cwd))]
    if sidechain_files:
        lines.append(json.dumps(_assistant(sidechain_files, sidechain=True)))
    body = "\n".join(lines) + "\n" if lines else ""
    if only_partial:
        body = json.dumps(_assistant(files, cwd=cwd))[:80]
    elif trailing_partial:
        body += json.dumps(_assistant(["/x/late.py"], cwd=cwd))[:80]
    path.write_text(body, encoding="utf-8")
    if age_seconds:
        t = path.stat().st_mtime - age_seconds
        os.utime(path, (t, t))
    return path


@pytest.fixture()
def tailer_cache():
    """The extractor is cached in a module global; restore it around each test."""
    saved = st._SESSION_TAILER
    yield
    st._SESSION_TAILER = saved


@pytest.fixture()
def projects_root(tmp_path: Path, monkeypatch):
    """Point the id lookup at a fixture tree. 🔴 Never the real one."""
    root = tmp_path / "projects" / "-synthetic-project"
    root.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    return root


def _session_source(repo: Path, transcript: Path, **kw):
    return st.collect_session_paths(repo, transcript=transcript, **kw)


def _CHANGED_PATHS_CAP() -> int:
    """The extractor's OWN cap, read through the extractor this module loads.

    🔴 Not restated as a literal here. The cap is `changed_paths`'s constant and
    the note under test quotes it; a second copy in the tests would let the two
    drift and turn a real truncation regression into a green boundary test.
    """
    return st._session_tailer().CP.CHANGED_PATHS_CAP


class TestSessionPositiveControl:
    """🔴 `claude/RULES.md` → "Positive control — can it ever observe the thing?"

    A reassuring empty path set is indistinguishable from an extractor wired to
    nothing — which is precisely the bug #398 fixed one source over, where the
    opencode summariser emitted `files_modified=0` for every session for months
    while its own tests were green. So the pair is reported: a transcript that
    MUST yield paths, against a control that MUST yield none, on the same code
    path with the same shape.
    """

    UNDER_CWD = ["src/collector/a.py", "src/collector/b.py", "src/collector/c.py"]

    def test_THE_PAIR_nonzero_under_test_zero_on_the_control(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        cwd = str(repo)

        # UNDER TEST: three edits inside the session cwd.
        pos = _write_transcript(
            tmp_path / "pos.jsonl", cwd, [f"{cwd}/{p}" for p in self.UNDER_CWD]
        )
        # CONTROL: the SAME three filenames at the SAME depth, edited in another
        # repo entirely. They have no repo-relative form here, so the honest
        # answer is zero — and it must be zero for that reason, not because
        # nothing was read.
        neg = _write_transcript(
            tmp_path / "neg.jsonl",
            cwd,
            [f"{tmp_path}/elsewhere/{p}" for p in self.UNDER_CWD],
        )

        pos_src = _session_source(repo, pos)
        neg_src = _session_source(repo, neg)

        assert len(pos_src.paths) == 3, "positive control yielded nothing — wired to nothing"
        assert len(neg_src.paths) == 0
        assert sorted(pos_src.paths) == sorted(self.UNDER_CWD)

    def test_the_control_zero_is_ACCOUNTED_for_not_merely_empty(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """The three dropped paths are COUNTED. An empty list with no count is
        the silent zero; an empty list beside `3 outside it` is a reading."""
        repo = _init_repo(tmp_path, SCOPE)
        cwd = str(repo)
        neg = _write_transcript(
            tmp_path / "neg.jsonl",
            cwd,
            [f"{tmp_path}/elsewhere/{p}" for p in self.UNDER_CWD],
        )
        src = _session_source(repo, neg)
        assert src.paths == ()
        assert any("3 outside it" in n for n in src.notes), src.notes
        assert any("0 distinct path(s) under the session cwd" in n for n in src.notes)

    def test_the_outside_count_is_emitted_AT_ZERO_too(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 The caveat refers to this note, so it must always exist to refer
        to — and a stated zero is a reading, while an absent line is
        indistinguishable from a counter wired to nothing."""
        repo = _init_repo(tmp_path, SCOPE)
        cwd = str(repo)
        t = _write_transcript(tmp_path / "t.jsonl", cwd, [f"{cwd}/{p}" for p in self.UNDER_CWD])
        src = _session_source(repo, t)
        assert any("0 outside it" in n for n in src.notes), src.notes

    def test_the_pair_survives_all_the_way_to_a_REPORT(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """The extractor and the matcher are two places a zero can come from.
        Both ends are pinned, so one being wired to nothing cannot hide behind
        the other."""
        repo = _init_repo(tmp_path, SCOPE)
        cwd = str(repo)
        store = _make_store(tmp_path / "s")
        pos = _write_transcript(
            tmp_path / "pos.jsonl", cwd, [f"{cwd}/{p}" for p in self.UNDER_CWD]
        )
        # Same count of paths, same depth, same store — only the subsystem
        # directory differs, and it names nothing.
        neg = _write_transcript(
            tmp_path / "neg.jsonl",
            cwd,
            [f"{cwd}/src/unlisted-widget/{Path(p).name}" for p in self.UNDER_CWD],
        )
        pos_rep = st.build_report(_session_source(repo, pos), store, SCOPE, today=TODAY)
        neg_rep = st.build_report(_session_source(repo, neg), store, SCOPE, today=TODAY)

        assert pos_rep.status == "resolved"
        assert [m.entry.ref for m in pos_rep.known] == ["collector"]
        assert neg_rep.status == "no-match"
        assert neg_rep.known == ()
        # …and the negative control is NOT an empty window, which would make the
        # zero uninformative.
        assert len(neg_rep.source.paths) == 3

    def test_a_MERGED_session_is_exactly_what_git_cannot_see(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 THE MOTIVATING CASE, as a test. The repo is clean and HEAD is at
        the base ref — the state a session leaves behind when it lands its work
        through merged PRs. Git's window is empty and says so; the session's is
        not. Both are run against the SAME repo at the SAME moment."""
        repo = _init_repo(tmp_path, SCOPE)  # clean tree, on `main`, nothing staged
        cwd = str(repo)
        t = _write_transcript(
            tmp_path / "t.jsonl", cwd, [f"{cwd}/{p}" for p in self.UNDER_CWD]
        )
        git_src = st.collect_git_paths(repo)
        ses_src = _session_source(repo, t)

        assert git_src.paths == (), "the premise is gone: git's window was not empty"
        assert git_src.window == "worktree"
        assert len(ses_src.paths) == 3
        assert ses_src.window == "session"


class TestSessionNegativeControls:
    """Each guard fails with ITS OWN sentinel, reached by an input no EARLIER
    guard rejects — otherwise a control passes because a neighbour fired, and
    stays green with the guard it claims to test deleted.

    Every test also asserts the OTHER sentinels are absent, which is what makes
    "this guard fired" a measurement rather than an inference.
    """

    SENTINELS = {
        "missing": "transcript not found",
        "ambiguous": "transcript id is ambiguous",
        "stale": "transcript is stale",
        "unreadable": "transcript unreadable",
        "cwd": "transcript cwd does not match",
        "extractor": "session path extractor not found",
    }

    def _only(self, exc: Exception, key: str) -> None:
        text = str(exc)
        assert self.SENTINELS[key] in text, f"expected the {key} sentinel, got: {text}"
        for other, phrase in self.SENTINELS.items():
            if other != key:
                assert phrase not in text, f"the {other} sentinel also fired: {text}"

    def test_no_two_sentinels_share_a_spelling(self) -> None:
        """The premise of `_only`. Two guards spelled alike would make every
        assertion above vacuous."""
        for a, pa in self.SENTINELS.items():
            for b, pb in self.SENTINELS.items():
                if a != b:
                    assert pa not in pb, f"{a} sentinel is a substring of {b}"

    def test_a_nonexistent_session_id_is_MISSING(
        self, tmp_path: Path, projects_root: Path, tailer_cache
    ) -> None:
        with pytest.raises(st.TranscriptMissingError) as exc:
            st.find_transcript(SESSION_ID)
        self._only(exc.value, "missing")

    def test_an_id_with_a_separator_is_MISSING_not_searched_for(
        self, tmp_path: Path, projects_root: Path, tailer_cache
    ) -> None:
        """A separator would let the glob escape the roots entirely."""
        with pytest.raises(st.TranscriptMissingError) as exc:
            st.find_transcript("../../etc/passwd")
        self._only(exc.value, "missing")

    def test_two_transcripts_with_ONE_id_is_AMBIGUOUS_not_a_pick(
        self, tmp_path: Path, projects_root: Path, tailer_cache
    ) -> None:
        """Reachable past the missing guard: the file EXISTS — twice."""
        for sub in ("a", "b"):
            _write_transcript(
                projects_root / sub / f"{SESSION_ID}.jsonl", str(tmp_path), ["x.py"]
            )
        with pytest.raises(st.TranscriptAmbiguousError) as exc:
            st.find_transcript(SESSION_ID)
        self._only(exc.value, "ambiguous")

    def test_a_stale_transcript_is_STALE(self, tmp_path: Path, tailer_cache) -> None:
        """Reachable past the missing guard: the file exists and is perfectly
        readable — it is simply not the session that is running now."""
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py"],
            age_seconds=st.MAX_TRANSCRIPT_AGE_SECONDS + 60,
        )
        with pytest.raises(st.TranscriptStaleError) as exc:
            _session_source(repo, t)
        self._only(exc.value, "stale")

    def test_the_stale_boundary_is_a_boundary_not_a_slope(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 Measured at TWO points either side, not one: a bound asserted only
        from beyond it is equally consistent with a guard that rejects
        everything."""
        repo = _init_repo(tmp_path, SCOPE)
        fresh = _write_transcript(
            tmp_path / "fresh.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py"],
            age_seconds=st.MAX_TRANSCRIPT_AGE_SECONDS - 60,
        )
        assert _session_source(repo, fresh).paths == ("src/collector/a.py",)

        stale = _write_transcript(
            tmp_path / "stale.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py"],
            age_seconds=st.MAX_TRANSCRIPT_AGE_SECONDS + 60,
        )
        with pytest.raises(st.TranscriptStaleError):
            _session_source(repo, stale)

    def test_a_corrupt_transcript_is_UNREADABLE(self, tmp_path: Path, tailer_cache) -> None:
        """Reachable past missing + stale: the file exists and is fresh; it just
        is not a session. 🔴 It must NOT come back as an empty path set — that
        would read as 'this session touched nothing'."""
        repo = _init_repo(tmp_path, SCOPE)
        t = tmp_path / "corrupt.jsonl"
        t.write_text("not json at all\nnor is this\n", encoding="utf-8")
        with pytest.raises(st.TranscriptUnreadableError) as exc:
            _session_source(repo, t)
        self._only(exc.value, "unreadable")

    def test_an_empty_transcript_is_UNREADABLE_not_an_empty_window(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        t = tmp_path / "empty.jsonl"
        t.write_text("", encoding="utf-8")
        with pytest.raises(st.TranscriptUnreadableError) as exc:
            _session_source(repo, t)
        self._only(exc.value, "unreadable")

    def test_a_FOREIGN_cwd_is_CWD_MISMATCH(self, tmp_path: Path, tailer_cache) -> None:
        """Reachable past missing + stale + unreadable: the transcript is fresh
        and reads perfectly — it belongs to a session in another repo."""
        repo = _init_repo(tmp_path, SCOPE)
        other = tmp_path / "another-repo"
        t = _write_transcript(
            tmp_path / "t.jsonl", str(other), [f"{other}/src/collector/a.py"]
        )
        with pytest.raises(st.TranscriptCwdMismatchError) as exc:
            _session_source(repo, t)
        self._only(exc.value, "cwd")

    def test_the_cwd_guard_is_reached_by_a_transcript_that_ALSO_reads_fine(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 The reachability proof, stated as a measurement: the SAME bytes
        with the cwd corrected produce a working report. So the guard above
        fired on the cwd, not on some incidental defect in the fixture."""
        repo = _init_repo(tmp_path, SCOPE)
        good = _write_transcript(
            tmp_path / "good.jsonl", str(repo), [f"{repo}/src/collector/a.py"]
        )
        assert _session_source(repo, good).paths == ("src/collector/a.py",)

    def test_a_transcript_with_NO_cwd_is_a_mismatch_not_a_match(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """`realpath("")` is the process cwd; without the empty-string guard a
        session that recorded no cwd would "match" whatever directory the tool
        happened to be launched from."""
        repo = _init_repo(tmp_path, SCOPE)
        t = tmp_path / "nocwd.jsonl"
        t.write_text(json.dumps(_assistant(["/x/a.py"])) + "\n", encoding="utf-8")
        with pytest.raises(st.TranscriptCwdMismatchError) as exc:
            _session_source(repo, t)
        self._only(exc.value, "cwd")
        assert st._same_dir("", os.getcwd()) is False

    def test_the_cwd_error_NAMES_THE_ALTERNATIVE_source(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 A refusal that does not say where to go next is a refusal the
        caller has to out-think, and the obvious next move here is WRONG.

        Every other session failure resolves to "drop --session, use the git
        window". This one must not: the session ran in ANOTHER repo, so this
        repo's branch window is empty too, and the caller would be reading a
        second source that structurally cannot answer. The work arrived here as
        PRs or commits, so the message has to name those.

        Observed live: an agent's session cwd was one repo while all of its work
        landed in another. The guard fired correctly and the agent then had to
        derive `--pr` for itself.
        """
        repo = _init_repo(tmp_path, SCOPE)
        other = tmp_path / "elsewhere-repo"
        t = _write_transcript(
            tmp_path / "t.jsonl", str(other), [f"{other}/src/collector/a.py"]
        )
        with pytest.raises(st.TranscriptCwdMismatchError) as exc:
            _session_source(repo, t)
        text = str(exc.value)
        self._only(exc.value, "cwd")
        assert "--pr" in text, f"the cwd refusal does not name --pr: {text}"
        assert "--commit" in text, f"the cwd refusal does not name --commit: {text}"
        # 🔴 And it must DISARM the usual fallback, not merely omit it. Asserting
        # the words "git window" are absent would be a SPELLED guard: this very
        # message contains them, in the clause that rules the fallback OUT. So
        # assert the STATE — the message says that window is empty — which a
        # rewrite cannot satisfy by accident while still pointing there.
        assert "git window is empty" in text, (
            f"the cwd refusal does not rule OUT the git window. It is the right "
            f"answer for every other session failure and a second dead source "
            f"here, so silence about it is not enough: {text}"
        )

    def test_a_missing_EXTRACTOR_names_itself(
        self, tmp_path: Path, monkeypatch, tailer_cache
    ) -> None:
        """A deploy that omitted the shared extractor is a broken environment,
        not a reading — and in THIS repo a new file that was never `git add`ed is
        exactly how that happens."""
        st._SESSION_TAILER = None
        monkeypatch.setattr(st, "_session_tailer_path", lambda: tmp_path / "gone.py")
        with pytest.raises(st.ExtractorMissingError) as exc:
            st._session_tailer()
        self._only(exc.value, "extractor")

    def test_EVERY_failure_is_a_TouchError_so_the_CLI_exits_nonzero(self) -> None:
        """🔴 `/handoff` step 4 keys on the exit code alone. A session error that
        escaped `TouchError` would crash with a traceback — still non-zero, but
        the skill's contract is that the stderr line is printable verbatim."""
        for cls in (
            st.ExtractorMissingError,
            st.TranscriptMissingError,
            st.TranscriptAmbiguousError,
            st.TranscriptStaleError,
            st.TranscriptUnreadableError,
            st.TranscriptCwdMismatchError,
        ):
            assert issubclass(cls, st.TouchError), cls


class TestCrossRepoAbsoluteWindow:
    """🔴 THE FALSE SENTENCE THE CWD GUARD THREW ATTRIBUTABLE PATHS AWAY WITH.

    The refusal read: *"Every path in a transcript is relative to the session's
    own cwd."* It is not. A transcript entry is the tool call's own `file_path`,
    ABSOLUTE whenever the caller passed an absolute path — which agents do
    constantly, because the harness tells them to. Such a path needs no
    inference to attribute, and it was refused anyway.

    ⚠ NO YIELD FIGURE IS ASSERTED HERE, deliberately. The 112 that motivated the
    work is RETRACTED — it counted devrc's own sibling worktree directories
    (`devrc-fix443`, `devrc-clickup`, …) as other repos. Reconciled: ~30
    genuinely cross-project, corroborated at 33 by a second script;
    `subsystem_touch.collect_session_paths` carries the derivation. These tests
    pin the BEHAVIOUR, which does not depend on it — the stated reason was false
    at ANY yield.

    So a cwd mismatch now yields the ABSOLUTE window instead of refusing, and
    refuses only when that window is empty. What is unchanged, and is the actual
    safety property, is that a RELATIVE path is NEVER re-anchored: `src/a.py` in
    session-cwd A and `src/a.py` under repo B are unrelated strings that happen
    to spell the same thing, and reading one as the other files another repo's
    work here — silently, because the manufactured path resolves perfectly.
    """

    UNDER_B = ["src/collector/a.py", "src/collector/b.py"]

    def _two_repos(self, tmp_path: Path):
        """B is the repo under test; A is where the session actually ran."""
        b = _init_repo(tmp_path, SCOPE)
        a = tmp_path / "the-other-checkout"
        a.mkdir()
        return a, b

    def test_THE_PAIR_absolute_paths_are_reported_relative_ones_are_refused(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 The measurement, as one test. Both arms are the SAME session shape
        in the SAME repos with the SAME filenames at the SAME depth — the single
        difference is whether the transcript spelled the path out.

        Without the negative arm the positive one is indistinguishable from a
        window that re-anchors everything, which is the defect the guard exists
        to prevent and would be invisible: every path would resolve.
        """
        a, b = self._two_repos(tmp_path)

        spelled = _write_transcript(
            tmp_path / "spelled.jsonl", str(a), [f"{b}/{p}" for p in self.UNDER_B]
        )
        src = _session_source(b, spelled)
        assert sorted(src.paths) == sorted(self.UNDER_B), (
            "the absolute window yielded nothing — it is wired to nothing"
        )

        implied = _write_transcript(
            tmp_path / "implied.jsonl", str(a), list(self.UNDER_B)
        )
        with pytest.raises(st.TranscriptCwdMismatchError):
            _session_source(b, implied)

    def test_it_is_a_DIFFERENT_WINDOW_not_a_flag_on_the_session_one(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(tmp_path / "t.jsonl", str(a), [f"{b}/{self.UNDER_B[0]}"])
        src = _session_source(b, t)
        assert src.kind == "session"
        assert src.window == "session-absolute"
        assert src.session_cwd == str(a)

    def test_ONE_transcript_two_repos_two_disjoint_windows(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 THE SEAM. The same bytes, read against two repos, must answer two
        different questions and never blend them: repo A gets what the session
        named relative to its own cwd; repo B gets only what it named absolutely
        under B. A window that leaked either way would be invisible in a test
        that only ever loaded one repo.
        """
        a_repo = _init_repo(tmp_path, "the-other-checkout")
        b = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(a_repo),
            ["local/only-in-a.py", f"{b}/src/collector/a.py"],
        )
        from_a = _session_source(a_repo, t)
        from_b = _session_source(b, t)

        assert from_a.window == "session"
        assert from_a.paths == ("local/only-in-a.py",)
        assert from_b.window == "session-absolute"
        assert from_b.paths == ("src/collector/a.py",)
        assert set(from_a.paths).isdisjoint(from_b.paths)

    def test_a_SIBLING_repo_sharing_a_name_prefix_is_not_this_repo(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """`<repo>-2/src/a.py` must not be read as `-2/src/a.py` under `<repo>`.
        A manufactured path that matches nothing is the better half of that bug;
        the worse half is one that matches something.

        ⚠ Green on the pre-change code too — everything was refused there — so it
        is not regression coverage for the old defect. It pins a hazard THIS
        change creates, which is why it is here rather than in the tailer suite
        alone."""
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(tmp_path / "t.jsonl", str(a), [f"{b}-2/src/collector/a.py"])
        with pytest.raises(st.TranscriptCwdMismatchError):
            _session_source(b, t)

    def test_the_notes_report_the_PAIR_not_just_the_yield(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """A count of what was reported, beside a count of what was excluded and
        WHY. Without the second number a thin window is indistinguishable from a
        thin session."""
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(a),
            [f"{b}/{self.UNDER_B[0]}", "only-in-a.py", "also-in-a.py"],
        )
        src = _session_source(b, t)
        joined = " ".join(src.notes)
        assert "1 path(s) named ABSOLUTELY" in joined, src.notes
        assert str(a) in joined, src.notes
        assert "2 path(s) expressible relative to that cwd" in joined, src.notes
        assert "EXCLUDED" in joined, src.notes

    def test_the_notes_ACCOUNT_FOR_EVERY_PATH_the_session_named(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 A BUCKET WENT UNREPORTED, so the numbers did not add up and a reader
        doing the arithmetic reached a smaller session than the real one.

        Fixture: FIVE distinct paths — 2 absolute under `--repo`, 2 expressible
        relative to the session's own cwd, 1 absolute in a sibling tree that is
        neither. The note used to print `2 … ABSOLUTELY` and `2 … relative`, and
        the fifth path appeared nowhere; the REFUSAL path already quoted all
        three counts, so the two halves of one guard disagreed about what they
        owed the reader.

        Asserted on the ACCOUNTING, not on a phrase: the distinct total is
        stated, and the two partitioning counts sum to it. A rewrite cannot
        satisfy that by accident while still dropping a bucket.
        """
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(a),
            [
                f"{b}/{self.UNDER_B[0]}",          # absolute, under --repo
                f"{b}/{self.UNDER_B[1]}",          # absolute, under --repo
                f"{a}/in-the-session-repo.py",     # absolute, under the session cwd
                "relative-in-a.py",                # relative -> the session cwd
                f"{tmp_path}/a-third-tree/x.py",   # absolute, neither
            ],
        )
        src = _session_source(b, t)
        joined = " ".join(src.notes)
        assert "5 distinct path(s)" in joined, src.notes
        assert "2 path(s) named ABSOLUTELY" in joined, src.notes
        assert "2 path(s) expressible relative to that cwd" in joined, src.notes
        assert "3 outside it" in joined, src.notes
        # 🔴 And it must WARN that the yield overlaps, or a reader adding all
        # three reaches 7 from a 5-path session. The absolute set is a re-reading
        # of paths already counted, under a different root — not a fourth bucket.
        assert "OVERLAPS" in joined, src.notes

    def test_the_note_and_the_REFUSAL_quote_the_same_counters(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """One guard, two exits, and they used to owe the reader different
        things. Both must name the same three numbers — a divergence here is how
        the omission survived review in the first place."""
        a, b = self._two_repos(tmp_path)
        shared = ["relative-in-a.py", f"{tmp_path}/a-third-tree/x.py"]
        refused = _write_transcript(tmp_path / "no.jsonl", str(a), shared)
        with pytest.raises(st.TranscriptCwdMismatchError) as exc:
            _session_source(b, refused)
        answered = _write_transcript(
            tmp_path / "yes.jsonl", str(a), shared + [f"{b}/{self.UNDER_B[0]}"]
        )
        note = " ".join(_session_source(b, answered).notes)
        for phrase in ("distinct path(s)", "expressible relative to that cwd", "outside it"):
            assert phrase in str(exc.value), f"the refusal dropped {phrase!r}"
            assert phrase in note, f"the success note dropped {phrase!r}"

    def test_the_caveat_calls_it_a_FLOOR_and_never_claims_the_session_frame(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 A FIFTH caveat, because the session one is FALSE here in a specific
        direction: it says the paths are "relative to the session cwd", and this
        window's whole premise is that they are not. Its blind spots are also a
        strict superset — every relative path the session named is not a counted
        remainder but an UNKNOWABLE one, so the count is a lower bound.
        """
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(tmp_path / "t.jsonl", str(a), [f"{b}/{self.UNDER_B[0]}"])
        src = _session_source(b, t)
        cav = src.caveat
        assert "relative to the session cwd" not in cav, cav
        assert str(a) in cav, "the caveat does not say where the session ran"
        assert "lower bound" in cav, cav
        assert "EXCLUDED" in cav and "UNKNOWN" in cav, cav
        # …and it is genuinely a different sentence from the session caveat, not
        # the same one with a word changed.
        same = _write_transcript(tmp_path / "same.jsonl", str(b), [f"{b}/x.py"])
        assert _session_source(b, same).caveat != cav

    def test_the_REFUSAL_no_longer_states_the_falsehood(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 The message is the deliverable here: two agents in one day read it,
        believed it, and correctly stopped — the guard's outcome was right and
        its stated reason was wrong, which is the failure mode that survives a
        green suite indefinitely. Asserted on the STATE it now claims (this
        session named nothing absolute under this repo), not merely on the
        absence of the old sentence.
        """
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(tmp_path / "t.jsonl", str(a), ["only/in/a.py"])
        with pytest.raises(st.TranscriptCwdMismatchError) as exc:
            _session_source(b, t)
        text = str(exc.value)
        assert "Every path in a transcript is relative" not in text, text
        assert "NONE of the paths it named are absolute paths under this repo" in text, text
        # The routing half is unchanged and still load-bearing — see
        # TestSessionNegativeControls for why the git window must be ruled OUT.
        assert "--pr" in text and "--commit" in text, text
        assert "git window is empty" in text, text

    def test_the_refusal_COUNTS_what_it_declined_to_re_anchor(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """A refusal that names no number is indistinguishable from one issued by
        a reader that saw nothing at all."""
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(
            tmp_path / "t.jsonl", str(a), ["one.py", "two.py", "/var/tmp/three.py"]
        )
        with pytest.raises(st.TranscriptCwdMismatchError) as exc:
            _session_source(b, t)
        text = str(exc.value)
        assert "of 3 distinct path(s) this session named" in text, text
        assert "2 path(s) expressible relative to that cwd" in text, text
        assert "1 outside it" in text, text

    def test_a_TRUNCATED_absolute_window_says_so(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 THE NOTE NOBODY HAD REACHED. A mutant that deleted the truncation
        branch outright SURVIVED the whole suite: the extractor's cap is 256 and
        no fixture went near it, so the note could have been dead code and every
        test would still have been green — the `>2-page index` defect one
        subsystem over, in this window's costume.

        Driven through the REAL cap rather than a monkeypatched one, because the
        cap is what a reader of the note will check against. The list is a
        lexicographic PREFIX, so the missing tail is a whole late-sorting subtree
        — `zzz/` here, chosen to sort after every `f####.py`.
        """
        a, b = self._two_repos(tmp_path)
        cap = _CHANGED_PATHS_CAP()
        files = [f"{b}/f{i:04d}.py" for i in range(cap)] + [f"{b}/zzz/late.py"]
        t = _write_transcript(tmp_path / "big.jsonl", str(a), files)
        src = _session_source(b, t)

        assert len(src.paths) == cap
        assert "zzz/late.py" not in src.paths, (
            "the fixture did not actually truncate — the note under test is unreached"
        )
        joined = " ".join(src.notes)
        assert "TRUNCATED" in joined, src.notes
        assert f"cap of {cap}" in joined, src.notes
        assert f"PREFIX of {cap + 1} paths" in joined, src.notes

    def test_a_window_AT_the_cap_does_NOT_say_truncated(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """The other arm at the same boundary — otherwise a note wired to fire
        always would pass the test above. Exactly `cap` paths is a COMPLETE
        list."""
        a, b = self._two_repos(tmp_path)
        cap = _CHANGED_PATHS_CAP()
        t = _write_transcript(
            tmp_path / "atcap.jsonl", str(a), [f"{b}/f{i:04d}.py" for i in range(cap)]
        )
        src = _session_source(b, t)
        assert len(src.paths) == cap
        assert not any("TRUNCATED" in n for n in src.notes), src.notes

    def test_UNREADABLE_still_wins_over_the_absolute_window(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 Guard order. The new window sits INSIDE guard 4, so guard 3 must
        still fire first — otherwise a transcript we could not read at all would
        be answered with an empty absolute window, which reads as 'this session
        touched nothing here'. Reached with a file that is unreadable AND whose
        (unreadable) content would have carried absolute paths under this repo.

        ⚠ Green pre-change by construction (guard 4 raised unconditionally
        there). Like the sibling-prefix case it pins a hazard this change
        introduces, and the mutation sweep — not the base run — is what shows it
        can go red.
        """
        a, b = self._two_repos(tmp_path)
        t = tmp_path / "torn.jsonl"
        t.write_text(
            json.dumps(_assistant([f"{b}/src/collector/a.py"], cwd=str(a)))[:60],
            encoding="utf-8",
        )
        with pytest.raises(st.TranscriptUnreadableError):
            _session_source(b, t)

    def test_STALE_still_wins_over_the_absolute_window(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """The same ordering claim one guard earlier: a transcript whose absolute
        paths are perfect is still another session's if it is not live.

        ⚠ Green pre-change by construction — see the note two tests up."""
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(
            tmp_path / "old.jsonl",
            str(a),
            [f"{b}/{self.UNDER_B[0]}"],
            age_seconds=st.MAX_TRANSCRIPT_AGE_SECONDS + 60,
        )
        with pytest.raises(st.TranscriptStaleError):
            _session_source(b, t)

    def test_a_SUBAGENTS_turns_are_excluded_here_too(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """The caveat claims it, so it is measured. A sidechain turn is a
        separate session and its paths are not this one's, absolute or not."""
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(a),
            [f"{b}/{self.UNDER_B[0]}"],
            sidechain_files=[f"{b}/src/collector/subagent-only.py"],
        )
        src = _session_source(b, t)
        assert src.paths == (self.UNDER_B[0],)

    def test_the_callers_exclusions_are_honoured_in_this_window_too(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """One predicate for every source — `/handoff` passes `--exclude` for its
        own doc, and a window that ignored it would nominate that doc on exactly
        the cross-repo runs."""
        a, b = self._two_repos(tmp_path)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(a),
            [f"{b}/{p}" for p in self.UNDER_B] + [f"{b}/claudedocs/handoff-x.md"],
        )
        src = _session_source(b, t, exclude=["claudedocs/handoff-x.md"])
        assert sorted(src.paths) == sorted(self.UNDER_B)
        assert any("excluded 1 caller-named path(s)" in n for n in src.notes), src.notes

    def test_it_survives_all_the_way_to_a_REPORT(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 End to end, because the extractor and the matcher are two places a
        zero can come from and either one being wired to nothing would hide
        behind the other. The pair again: a cross-repo session that touched a
        KNOWN subsystem resolves; one that touched an unlisted directory, from
        the same window, does not."""
        a, b = self._two_repos(tmp_path)
        store = _make_store(tmp_path / "s")
        hit = _write_transcript(
            tmp_path / "hit.jsonl", str(a), [f"{b}/{p}" for p in self.UNDER_B]
        )
        miss = _write_transcript(
            tmp_path / "miss.jsonl",
            str(a),
            [f"{b}/src/unlisted-widget/{Path(p).name}" for p in self.UNDER_B],
        )
        hit_rep = st.build_report(_session_source(b, hit), store, SCOPE, today=TODAY)
        miss_rep = st.build_report(_session_source(b, miss), store, SCOPE, today=TODAY)
        assert hit_rep.status == "resolved"
        assert [m.entry.ref for m in hit_rep.known] == ["collector"]
        assert miss_rep.status == "no-match"
        # …and the miss is NOT an empty window, which would make its zero
        # uninformative.
        assert len(miss_rep.source.paths) == 2

    def test_the_window_name_reaches_the_rendered_report(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """A reader deciding whether to trust the count needs to see WHICH window
        produced it; a window that renders as plain `session` would be read with
        the wrong caveat."""
        a, b = self._two_repos(tmp_path)
        store = _make_store(tmp_path / "s")
        t = _write_transcript(
            tmp_path / "t.jsonl", str(a), [f"{b}/{p}" for p in self.UNDER_B]
        )
        rep = st.build_report(_session_source(b, t), store, SCOPE, today=TODAY)
        assert "session-absolute" in st.render_text(rep)
        assert st.report_json(rep)["source"]["window"] == "session-absolute"


class TestTranscriptIsBeingAppendedTo:
    """🔴 The transcript is LIVE while this reads it. `/handoff` runs the tool
    during the very session the transcript belongs to, so the last line can be a
    half-written object. Three things must hold, and only the first is obvious.
    """

    def test_the_fixture_really_IS_partial(self, tmp_path: Path) -> None:
        """Positive control on the FIXTURE. A 'partial line' test whose last line
        happens to parse is testing nothing at all."""
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py"],
            trailing_partial=True,
        )
        last = t.read_text(encoding="utf-8").splitlines()[-1]
        with pytest.raises(json.JSONDecodeError):
            json.loads(last)

    def test_a_partial_trailing_line_does_not_CRASH_the_run(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py", f"{repo}/src/collector/b.py"],
            trailing_partial=True,
        )
        src = _session_source(repo, t)  # must not raise
        assert set(src.paths) == {"src/collector/a.py", "src/collector/b.py"}

    def test_a_partial_line_does_not_SILENTLY_TRUNCATE_the_complete_ones(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 The failure that would matter: a decoder that abandons the file at
        the first bad line would drop every complete line AFTER it. Asserted by
        comparing against the same transcript without the partial tail — the sets
        must be equal, so a truncating reader is visible as a smaller set."""
        repo = _init_repo(tmp_path, SCOPE)
        files = [f"{repo}/src/collector/{n}.py" for n in ("a", "b", "c")]
        whole = _write_transcript(tmp_path / "whole.jsonl", str(repo), files)
        torn = _write_transcript(
            tmp_path / "torn.jsonl", str(repo), files, trailing_partial=True
        )
        assert _session_source(repo, torn).paths == _session_source(repo, whole).paths
        assert len(_session_source(repo, torn).paths) == 3

    def test_a_transcript_that_is_ONLY_a_partial_line_is_UNREADABLE(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """The other end of the same edge: nothing parseable at all is UNKNOWN,
        and must raise rather than report an empty window."""
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl", str(repo), [f"{repo}/a.py"], only_partial=True
        )
        with pytest.raises(st.TranscriptUnreadableError):
            _session_source(repo, t)


class TestTheExtractorIsREUSED:
    """🔴 #398 already implements transcript → changed-paths, and a second
    implementation is the duplicated predicate that has ALREADY drifted once
    here: the opencode summariser read key names its store never used and emitted
    `files_modified=0` for every session for months, green against fixtures built
    in the same wrong shape.

    Pinned BEHAVIOURALLY — the tailer is loaded independently and its answer is
    compared — because a grep for "does this file define an extractor" is the
    spelled guard that passes while a differently-spelled one drifts.
    """

    def _tailer(self):
        claude_dir = ROOT / "scripts" / "collector" / "claude"
        for d in (str(claude_dir.parent), str(claude_dir)):
            if d not in sys.path:
                sys.path.insert(0, d)
        spec = importlib.util.spec_from_file_location(
            "independent_session_tailer", claude_dir / "session-tailer.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["independent_session_tailer"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_the_paths_are_BYTE_FOR_BYTE_the_shared_extractor_s(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        files = [f"{repo}/src/collector/{n}.py" for n in ("c", "a", "b")]
        t = _write_transcript(tmp_path / "t.jsonl", str(repo), files)
        rollup = self._tailer().summarize_transcript(str(t))
        assert list(_session_source(repo, t).paths) == rollup["changed_paths"]
        assert rollup["changed_paths"], "the comparison is vacuous — the shared side is empty"

    def test_the_cap_and_the_outside_count_come_from_the_shared_module_too(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py", f"{tmp_path}/elsewhere/b.py"],
        )
        rollup = self._tailer().summarize_transcript(str(t))
        src = _session_source(repo, t)
        assert rollup["changed_paths_outside_cwd"] == 1
        assert any("1 outside it" in n for n in src.notes)

    def test_a_SUBAGENT_s_edits_are_absent_AND_the_caveat_says_so(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 The blind spot, measured rather than assumed: the shared extractor
        skips `isSidechain` turns, so a subagent's edits are not in this window.
        That is the right call — a subagent typically works in a temp worktree
        whose paths would inject `.claude/worktrees/agent-<hash>` components and
        manufacture associations — but it MUST be stated, not discovered."""
        repo = _init_repo(tmp_path, SCOPE)
        cwd = str(repo)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            cwd,
            [f"{cwd}/src/collector/a.py"],
            sidechain_files=[f"{cwd}/src/status-bar/z.py"],
        )
        src = _session_source(repo, t)
        assert src.paths == ("src/collector/a.py",)
        assert "src/status-bar/z.py" not in src.paths
        assert "SUBAGENT" in src.caveat


class TestSessionCaveatIsAccuratePerSource:
    """🔴 The old caveat becomes FALSE IN THE OTHER DIRECTION under a session
    source: "what this BRANCH touched, NOT what this SESSION touched" would
    UNDERSTATE a window that is exactly per-session. A single hedging sentence
    covering both sources would be wrong about both."""

    def test_the_session_caveat_does_not_claim_to_be_a_branch_window(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(tmp_path / "t.jsonl", str(repo), [f"{repo}/a.py"])
        caveat = _session_source(repo, t).caveat
        assert "BRANCH" not in caveat
        assert "NOT what this SESSION touched" not in caveat
        assert "THIS SESSION's own turns" in caveat

    def test_the_git_caveat_is_UNCHANGED(self, tmp_path: Path) -> None:
        """The fallback keeps its own honest bound; adding a source must not
        soften the claim the other one makes."""
        repo = _init_repo(tmp_path, SCOPE)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "src/collector/a.py")
        _run_git(repo, "add", "src", home=tmp_path)
        _run_git(repo, "commit", "-m", "w", home=tmp_path)
        assert "NOT what this SESSION touched" in st.collect_git_paths(repo).caveat

    def test_the_session_caveat_names_ALL_THREE_blind_spots(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """A caveat that names one omission reads as if it were the only one."""
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(tmp_path / "t.jsonl", str(repo), [f"{repo}/a.py"])
        caveat = _session_source(repo, t).caveat
        for phrase in ("SUBAGENT", "Bash command", "outside the session cwd"):
            assert phrase in caveat, phrase

    @pytest.mark.parametrize("status_paths,expect", [
        (["src/collector/a.py", "src/collector/b.py"], "resolved"),
        (["docs/a.md", "docs/b.md"], "no-match"),
        ([], "looked-at-nothing"),
    ], ids=["resolved", "no-match", "looked-at-nothing"])
    def test_the_caveat_is_on_EVERY_output_path_of_BOTH_renderers(
        self, tmp_path: Path, tailer_cache, status_paths, expect
    ) -> None:
        """🔴 The property the #415 audit established and this change must keep:
        the caveat is on every output path and both renderers — including the
        early-return `looked-at-nothing` branch, which is the one that returns
        before most of the text is built."""
        repo = _init_repo(tmp_path, SCOPE)
        cwd = str(repo)
        store = _make_store(tmp_path / "s")
        files = [f"{cwd}/{p}" for p in status_paths] or [f"{tmp_path}/away/x.py"]
        t = _write_transcript(tmp_path / "t.jsonl", cwd, files)
        rep = st.build_report(_session_source(repo, t), store, SCOPE, today=TODAY)
        assert rep.status == expect
        caveat = rep.source.caveat
        assert caveat in st.render_text(rep)
        assert st.report_json(rep)["source"]["caveat"] == caveat
        assert st.report_json(rep)["source"]["session"] is not None


class TestSessionCli:
    """The CLI is the only surface `/handoff` touches."""

    def _run(self, args, capsys):
        rc = st.main(args)
        return rc, capsys.readouterr()

    def _fixture(self, tmp_path: Path, projects_root: Path, **kw):
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            projects_root / f"{SESSION_ID}.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py", f"{repo}/src/collector/b.py"],
            **kw,
        )
        return repo, t, _make_store(tmp_path / "s")

    def test_a_session_uuid_resolves_end_to_end(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys
    ) -> None:
        repo, _t, store = self._fixture(tmp_path, projects_root)
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
             "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["source"]["kind"] == "session"
        assert payload["source"]["window"] == "session"
        assert payload["source"]["session"] == SESSION_ID
        assert payload["status"] == "resolved"
        assert payload["known"][0]["ref"] == "collector"

    def test_transcript_and_session_agree(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys
    ) -> None:
        """`--transcript` is the SAME source with the lookup skipped — not a
        second, laxer path. Both must produce the same window."""
        repo, t, store = self._fixture(tmp_path, projects_root)
        base = ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json"]
        rc1, c1 = self._run(base + ["--session", SESSION_ID], capsys)
        rc2, c2 = self._run(base + ["--transcript", str(t)], capsys)
        assert rc1 == rc2 == 0
        p1, p2 = json.loads(c1.out), json.loads(c2.out)
        assert p1["source"]["paths"] == p2["source"]["paths"]
        assert p2["source"]["kind"] == "session"

    def test_transcript_runs_the_SAME_guards(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys
    ) -> None:
        """🔴 If `--transcript` skipped validation it would be the escape hatch
        that voids the whole check."""
        repo, t, store = self._fixture(
            tmp_path, projects_root, age_seconds=st.MAX_TRANSCRIPT_AGE_SECONDS + 60
        )
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--transcript", str(t),
             "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert "transcript is stale" in cap.err

    @pytest.mark.parametrize(
        "kw,sentinel",
        [
            ({}, "transcript not found"),
            ({"age_seconds": st.MAX_TRANSCRIPT_AGE_SECONDS + 60}, "transcript is stale"),
        ],
        ids=["missing", "stale"],
    )
    def test_a_validation_failure_EXITS_3_and_prints_NOTHING_to_stdout(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys, kw, sentinel
    ) -> None:
        """🔴 THE NO-FALLBACK CONTRACT, at the surface `/handoff` reads. A report
        on stdout beside a non-zero exit is how a fallback would look."""
        repo, t, store = self._fixture(tmp_path, projects_root, **kw)
        if sentinel == "transcript not found":
            t.unlink()
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
             "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert sentinel in cap.err
        assert cap.out.strip() == "", f"a failure still printed a report: {cap.out!r}"

    def test_the_git_window_is_NEVER_what_a_failed_session_returns(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys
    ) -> None:
        """The positive control for the test above: the repo has a REAL git
        window, so a fallback would have produced a visible, plausible report."""
        repo, t, store = self._fixture(tmp_path, projects_root)
        _write(repo, "src/collector/leftover.py")
        assert st.collect_git_paths(repo).paths, "no git window — the control is vacuous"
        t.unlink()
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
             "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert "collector" not in cap.out

    def test_session_and_transcript_are_mutually_exclusive(
        self, tmp_path: Path, projects_root: Path, capsys
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            st.main(["--session", SESSION_ID, "--transcript", "/x/y.jsonl"])
        assert exc.value.code == 2

    def test_session_plus_paths_from_is_REFUSED_not_resolved(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys
    ) -> None:
        """Two different windows asked for at once. Honouring either silently is
        the same wrong-answer class as falling back."""
        repo, _t, store = self._fixture(tmp_path, projects_root)
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
             "--paths-from", "-", "--today", TODAY],
            capsys,
        )
        assert rc == 2
        assert "cannot be combined with --paths-from" in cap.err
        assert cap.out.strip() == ""

    def test_git_remains_the_default_when_no_session_is_given(
        self, tmp_path: Path, capsys
    ) -> None:
        """The fallback is still the fallback — adding a source must not change
        what an unchanged call does."""
        repo = _init_repo(tmp_path, SCOPE)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "src/collector/a.py")
        _write(repo, "src/collector/b.py")
        store = _make_store(tmp_path / "s")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["source"]["kind"] == "git"
        assert payload["source"]["session"] is None

    def test_exclude_applies_to_the_SESSION_window_too(
        self, tmp_path: Path, projects_root: Path, tailer_cache, capsys
    ) -> None:
        """🔴 `/handoff` WRITES its own doc in step 2 with the Write tool, so the
        doc is in the session window as surely as it is untracked in git's. An
        exclusion honoured by one source and not the other makes the ritual
        nominate its own artifact on half the runs."""
        repo = _init_repo(tmp_path, SCOPE)
        doc = "claudedocs/handoff-topic.md"
        _write_transcript(
            projects_root / f"{SESSION_ID}.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py", f"{repo}/{doc}"],
        )
        store = _make_store(tmp_path / "s")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
             "--exclude", doc, "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["source"]["paths"] == ["src/collector/a.py"]
        assert any("excluded 1" in n for n in payload["source"]["notes"])

    def test_the_exclusion_is_the_SAME_predicate_as_git_s(
        self, tmp_path: Path, projects_root: Path, tailer_cache
    ) -> None:
        """Structural, not two spellings that happen to agree today: the same
        helper, reached from both sources."""
        repo = _init_repo(tmp_path, SCOPE)
        doc = "claudedocs/handoff-topic.md"
        _write(repo, doc)
        _write(repo, "src/collector/a.py")
        git_src = st.collect_git_paths(repo, exclude=[doc])
        t = _write_transcript(
            tmp_path / "t.jsonl", str(repo), [f"{repo}/{doc}", f"{repo}/src/collector/a.py"]
        )
        ses_src = _session_source(repo, t, exclude=[doc])
        assert doc not in git_src.paths and doc not in ses_src.paths
        assert any("excluded 1" in n for n in git_src.notes)
        assert any("excluded 1" in n for n in ses_src.notes)


class TestSessionSourceNeverWrites:
    """The store hash either side of the session path, for the same reason
    `TestNeverWrites` does it for every other mode: the module has no write call
    site and must acquire none."""

    def test_the_session_report_leaves_the_store_byte_identical(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        t = _write_transcript(
            tmp_path / "t.jsonl", str(repo), [f"{repo}/src/collector/{n}.py" for n in "ab"]
        )
        before = _tree_hash(store)
        rep = st.build_report(_session_source(repo, t), store, SCOPE, today=TODAY)
        st.render_text(rep)
        json.dumps(st.report_json(rep))
        assert rep.status == "resolved"
        assert _tree_hash(store) == before

    def test_the_transcript_itself_is_never_written_either(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        """🔴 `~/.claude/projects/` is LIVE session state. The tool reads it and
        must never touch it — pinned on bytes AND on mtime, since a read that
        rewrote identical content would pass a content check alone."""
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl", str(repo), [f"{repo}/src/collector/a.py"]
        )
        before, before_stat = t.read_bytes(), t.stat().st_mtime_ns
        _session_source(repo, t)
        assert t.read_bytes() == before
        assert t.stat().st_mtime_ns == before_stat


class TestNoRealTranscript:
    """🔴 No test in this file may read a real transcript. `~/.claude/projects/`
    is live session state and this repo is PUBLIC."""

    #: A real v4 UUID's 32 hex digits are ~uniformly random, so it carries ~15-16
    #: DISTINCT characters. A hand-built fixture id carries 3. The bound below
    #: cannot be met by accident, which is what makes it a check rather than a
    #: comment — and `test_the_id_check_can_report_a_REAL_uuid` is its negative
    #: control, without which a threshold that passes everything looks identical.
    MAX_DISTINCT_HEX_IN_A_SYNTHETIC_ID = 3

    def test_the_fixture_session_ids_are_not_real(self) -> None:
        """A real UUID committed here would name one of the user's own sessions."""
        for sid in (SESSION_ID, OTHER_SESSION_ID):
            digits = set(sid.replace("-", ""))
            assert len(digits) <= self.MAX_DISTINCT_HEX_IN_A_SYNTHETIC_ID, (
                f"{sid} looks like a real UUID, not a synthetic one"
            )

    def test_the_id_check_can_report_a_REAL_uuid(self) -> None:
        """Negative control on the check above: a threshold nothing can fail is
        indistinguishable from no check at all."""
        import uuid

        real_shaped = str(uuid.uuid4())
        assert (
            len(set(real_shaped.replace("-", "")))
            > self.MAX_DISTINCT_HEX_IN_A_SYNTHETIC_ID
        )

    def test_the_default_roots_are_outside_this_repo(self, tailer_cache) -> None:
        for root in st._session_tailer().projects_roots():
            assert not str(Path(root).resolve()).startswith(str(ROOT))

    def test_the_lookup_honours_the_test_override(self, tmp_path: Path, monkeypatch, tailer_cache) -> None:
        """The mechanism every test above relies on to stay off the real tree."""
        monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path))
        assert st._session_tailer().projects_roots() == [str(tmp_path)]


# =============================================================================
# 🔴 MUTATION KILL MATRIX — every guard broken on purpose, in-suite.
# =============================================================================


def _load_mutant(tmp_path: Path, name: str, replacements: list[tuple[str, str]]):
    """Import a copy of the module with the named guard(s) neutered.

    The anchor-uniqueness assert is not decoration: `claude/RULES.md` — "a
    count=1 text replace on a pattern that occurs more than once is a live
    hazard"; a mutation applied to the wrong occurrence produces a mutant that
    is green for reasons nobody inspected.
    """
    src = MODULE_PATH.read_text(encoding="utf-8")
    for old, new in replacements:
        n = src.count(old)
        assert n == 1, f"mutation anchor occurs {n}x, expected exactly 1: {old!r}"
        src = src.replace(old, new)
    path = tmp_path / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec_module and left registered: `@dataclass` resolves
    # string annotations by looking the defining class's module up in
    # `sys.modules`, and an unregistered mutant dies with an AttributeError that
    # would read as "the mutation broke the module".
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class TestMutationKillMatrix:
    """Each test deletes ONE guard and asserts the expectation above it dies —
    with THIS guard's own symptom, not a neighbour's error."""

    def test_kills_worktree_stable_scope(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path, "m_scope", [('    if common.name == ".git":', "    if False:")]
        )
        leaked = mod.derive_scope(
            "/w/some-repo/.claude/worktrees/agent-a9f80ada5bf8837e4", "/w/some-repo/.git"
        )
        assert leaked == "agent-a9f80ada5bf8837e4"
        assert leaked != mod.derive_scope("/w/some-repo", "/w/some-repo/.git")

    def test_kills_store_missing_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_store",
            [
                # Anchored on the RAISE, not on `if not store.is_dir():` — that
                # condition appears in `census` too, and the loader's
                # uniqueness assert caught the ambiguity rather than letting a
                # mutation land on the wrong occurrence.
                (
                    "        raise StoreMissingError(\n"
                    '            f"store root not found: {store} — expected the '
                    '`/analyze-service` index "',
                    "        raise ValueError(\n"
                    '            f"neutered: {store} "',
                )
            ],
        )
        with pytest.raises(Exception) as exc:
            mod.build_report(
                mod.caller_supplied(["a/b.yaml", "a/c.yaml"]),
                tmp_path / "absent",
                SCOPE,
                today=TODAY,
            )
        assert not isinstance(exc.value, mod.StoreMissingError)
        assert "store root not found" not in str(exc.value)

    def test_kills_looked_at_nothing_guard(self, tmp_path: Path) -> None:
        """Without it the two zeros conflate: an empty window reports as a
        matching failure, which is the exact silent zero this exists to stop."""
        mod = _load_mutant(
            tmp_path, "m_empty", [("    if not source.paths:", "    if False:")]
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(mod.caller_supplied([]), store, SCOPE, today=TODAY)
        assert rep.status != "looked-at-nothing"
        assert "NOTHING WAS LOOKED AT" not in mod.render_text(rep)

    def test_kills_scope_absent_catch(self, tmp_path: Path) -> None:
        """Without it the first run in every non-infra repo is an exception —
        the intended case becomes the failing case."""
        mod = _load_mutant(
            tmp_path,
            "m_scope_absent",
            [("    except UnknownScopeError:", "    except ZeroDivisionError:")],
        )
        store = _make_store(tmp_path / "s")
        with pytest.raises(sr.UnknownScopeError):
            mod.build_report(
                mod.caller_supplied(["apps/roster/a.yaml", "apps/roster/b.yaml"]),
                store,
                "brand-new-repo",
                today=TODAY,
            )

    def test_kills_ambiguous_nomination_guard(self, tmp_path: Path) -> None:
        """Without it an ambiguous ref is nominated — proposing a THIRD entry
        for a name that already names two."""
        mod = _load_mutant(
            tmp_path,
            "m_amb_nom",
            [
                (
                    "        except AmbiguousRefError:\n            continue",
                    "        except AmbiguousRefError:\n            entry = None",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(
                ["etc/weekly-digest/a.yaml", "etc/weekly-digest/b.yaml"]
            ),
            store,
            SCOPE,
            today=TODAY,
        )
        assert "weekly-digest" in [n.ref for n in rep.nominations]

    def test_kills_nomination_threshold(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_nom_min",
            [("        if len(paths) < min_paths:\n            continue", "        if False:\n            continue")],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(["apps/roster/a.yaml"]), store, SCOPE, today=TODAY
        )
        assert rep.nominations != ()

    def test_kills_nomination_cap(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path, "m_nom_cap", [("    return _reserve_slot_for_top_noncoherent(out, limit)", "    return tuple(out)")]
        )
        store = _make_store(tmp_path / "s")
        paths = [f"d{i}/sub{i}/x.py" for i in range(20)] + [f"d{i}/sub{i}/y.py" for i in range(20)]
        rep = mod.build_report(mod.caller_supplied(paths), store, SCOPE, today=TODAY)
        assert len(rep.nominations) > mod.DEFAULT_NOMINATION_LIMIT

    def test_kills_depth_tiebreak(self, tmp_path: Path) -> None:
        """Without it the umbrella outranks the specific name on identical path
        sets, and `src` gets proposed as a subsystem."""
        mod = _load_mutant(
            tmp_path,
            "m_depth",
            [
                (
                    "    out.sort(key=lambda n: (not n.coherent, n.fans_out, -n.path_count, -n.depth, n.ref))",
                    "    out.sort(key=lambda n: (not n.coherent, n.fans_out, -n.path_count, n.ref))",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(["apps/ingest/a.yaml", "apps/ingest/b.yaml"]),
            store,
            SCOPE,
            today=TODAY,
        )
        assert [n.ref for n in rep.nominations] == ["apps", "ingest"]

    def test_kills_coherence_key(self, tmp_path: Path) -> None:
        """Without it a filename that recurs across unrelated directories
        outranks the directories the work actually lives in."""
        mod = _load_mutant(
            tmp_path,
            "m_coherent",
            [
                (
                    "    out.sort(key=lambda n: (not n.coherent, n.fans_out, -n.path_count, -n.depth, n.ref))",
                    "    out.sort(key=lambda n: (n.fans_out, -n.path_count, -n.depth, n.ref))",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(
                [
                    "apps/roster/values.yaml",
                    "apps/roster/kustomization.yaml",
                    "apps/paging/values.yaml",
                    "apps/paging/kustomization.yaml",
                    "apps/ledger/values.yaml",
                    "apps/ledger/kustomization.yaml",
                ]
            ),
            store,
            SCOPE,
            today=TODAY,
            limit=20,
        )
        refs = [n.ref for n in rep.nominations]
        assert refs.index("values") < refs.index("roster")

    def test_kills_coherence_computation(self, tmp_path: Path) -> None:
        """The flag itself, not only the sort that reads it: pinned TRUE, every
        ref is 'coherent' and the ordering above silently stops discriminating."""
        mod = _load_mutant(
            tmp_path,
            "m_coherent_calc",
            [("                coherent=len(prefixes_by_ref[ref]) == 1,", "                coherent=True,")],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(["lib/paging.py", "tests/paging.py"]), store, SCOPE, today=TODAY
        )
        assert rep.nominations[0].coherent is True  # the real module says False

    def test_kills_git_failure_guard(self, tmp_path: Path, monkeypatch) -> None:
        """Without it a failed git returns an empty stdout and the report says
        "0 paths" — a broken instrument reading as a real zero.

        ⚠ `monkeypatch.chdir` is load-bearing, not tidiness. With the guard gone
        the toplevel lookup also returns "", so `git -C ""` falls through to the
        CWD — and when the suite runs inside this repo the mutant reported SIX
        real paths from devrc itself. That is worse than the zero it is supposed
        to demonstrate, and it made the assertion depend on where pytest was
        launched from. Pinning the cwd to a non-repo fixes the dimension."""
        mod = _load_mutant(
            tmp_path,
            "m_git",
            # 🔴 Anchored on the RAISE as well as the condition. `if
            # proc.returncode != 0:` alone became AMBIGUOUS when the PR source
            # added a second subprocess wrapper, and the loader's uniqueness
            # assert caught it rather than letting the mutation land on the
            # wrong occurrence.
            [("    if proc.returncode != 0:\n        raise GitError(", "    if False:\n        raise GitError(")],
        )
        plain = tmp_path / "plain-dir"
        plain.mkdir()
        monkeypatch.chdir(plain)

        # the pair, on the same input: the real module NAMES the failure...
        with pytest.raises(st.GitError) as exc:
            st.collect_git_paths(plain)
        assert "git command failed" in str(exc.value)
        # ...the mutant does not raise at all.
        assert mod.collect_git_paths(plain).paths == ()

    def test_kills_branch_window(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_branch",
            [
                (
                    '            commands.append(("git", *branch_args))\n'
                    "            add(_nul_list(_git(repo, branch_args)))\n"
                    '            window = "branch"',
                    "            pass",
                )
            ],
        )
        repo = _init_repo(tmp_path)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "apps/ingest/deploy.yaml")
        _run_git(repo, "add", "apps/ingest/deploy.yaml", home=tmp_path)
        _run_git(repo, "commit", "-m", "c", home=tmp_path)
        src = mod.collect_git_paths(repo)
        assert "apps/ingest/deploy.yaml" not in src.paths
        assert src.window != "branch"

    def test_kills_census_unstamped_bucket(self, tmp_path: Path) -> None:
        """Without it, entries that predate the stamp are folded into a writer
        and the experiment answers itself with an inference."""
        mod = _load_mutant(
            tmp_path,
            "m_census",
            [("else UNSTAMPED", 'else "analyze-service"')],
        )
        store = _make_store(tmp_path / "s")
        c = mod.census(store)
        assert mod.UNSTAMPED not in c.by_writer
        assert c.by_writer["analyze-service"] == 4

    def test_kills_below_threshold_message(self, tmp_path: Path) -> None:
        """Without it the tail claims "none named an entry" when one was named."""
        mod = _load_mutant(
            tmp_path,
            "m_tail",
            [
                (
                    "        if report.below_threshold:\n"
                    "            out.append(\n"
                    '                f"NOTHING CLEARED THE THRESHOLD — {examined}; "',
                    "        if False:\n"
                    "            out.append(\n"
                    '                f"NOTHING CLEARED THE THRESHOLD — {examined}; "',
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(["src/collector/only.py", "docs/other.md"]),
                store,
                SCOPE,
                today=TODAY,
            )
        )
        assert "NOTHING CLEARED THE THRESHOLD" not in text
        assert "none named an entry" in text

    def test_kills_paths_from_validation(self, tmp_path: Path, capsys) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_pathsfrom",
            [
                (
                    "            print(\"subsystem-touch: --paths-from must be `git` or `-`\", "
                    "file=sys.stderr)\n            return 2",
                    "            source = collect_git_paths(repo)",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        repo = _init_repo(tmp_path)
        rc = mod.main(
            ["--repo", str(repo), "--store", str(store), "--scope", SCOPE,
             "--paths-from", "wat", "--today", TODAY]
        )
        capsys.readouterr()
        assert rc != 2

    def test_kills_fanout_sort_key(self, tmp_path: Path) -> None:
        """The KEY's position in the sort, separately from the flag's value.

        ⚠ Added because an independent sweep showed this mutation dying only to
        behavioural tests while every other sort key had a named one — so the
        matrix looked complete and had a hole exactly where symmetry suggested
        it did not."""
        mod = _load_mutant(
            tmp_path,
            "m_fanout_key",
            [
                (
                    "    out.sort(key=lambda n: (not n.coherent, n.fans_out, -n.path_count, "
                    "-n.depth, n.ref))",
                    "    out.sort(key=lambda n: (not n.coherent, -n.path_count, -n.depth, n.ref))",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(
                ["src/roster/a.py", "src/roster/b.py", "src/paging/c.py", "src/paging/d.py"]
            ),
            store,
            SCOPE,
            today=TODAY,
        )
        assert [n.ref for n in rep.nominations][0] == "src"

    def test_kills_fanout_computation(self, tmp_path: Path) -> None:
        """Pinned FALSE, the top-level umbrella wins on count again — which is
        the state the audit measured on this module's own PR diff."""
        mod = _load_mutant(
            tmp_path,
            "m_fanout",
            [
                (
                    "                fans_out=len(children_by_ref.get(ref, ())) >= 2,",
                    "                fans_out=False,",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(
                ["src/roster/a.py", "src/roster/b.py", "src/paging/c.py", "src/paging/d.py"]
            ),
            store,
            SCOPE,
            today=TODAY,
        )
        assert [n.ref for n in rep.nominations][0] == "src"

    def test_kills_fanout_counts_only_SUBDIRECTORIES(self, tmp_path: Path) -> None:
        """Counting terminal children too penalises every leaf directory for
        holding more than one file — the opposite of the intent, and green
        against the umbrella test above, so it needs its own mutant."""
        mod = _load_mutant(
            tmp_path,
            "m_fanout_terminal",
            [("            if depth + 1 < len(parts) - 1:", "            if depth + 1 < len(parts):")],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(
                ["apps/roster/a.yaml", "apps/roster/b.yaml", "apps/roster/c.yaml"]
            ),
            store,
            SCOPE,
            today=TODAY,
        )
        assert {n.ref: n.fans_out for n in rep.nominations}["roster"] is True

    def test_kills_the_reserved_slot(self, tmp_path: Path) -> None:
        """Without it the coherence key DELETES the only sensible candidate for
        a cross-directory subsystem, and `Nomination.coherent`'s docstring
        becomes false about its own code."""
        mod = _load_mutant(
            tmp_path,
            "m_reserve",
            [
                (
                    "    return _reserve_slot_for_top_noncoherent(out, limit)",
                    "    return tuple(out[:limit])",
                )
            ],
        )
        store = _make_store(tmp_path / "s")
        paths = [f"c{i}/a{i}.py" for i in range(5)] + [f"c{i}/b{i}.py" for i in range(5)]
        paths += ["lib/paging.py", "tests/paging.py"]
        rep = mod.build_report(mod.caller_supplied(paths), store, SCOPE, today=TODAY)
        assert "paging" not in [n.ref for n in rep.nominations]

    def test_kills_the_toplevel_path_frame(self, tmp_path: Path) -> None:
        """Without it `ls-files --others` runs cwd-relative and cwd-scoped while
        `diff --name-only` stays root-relative — two frames in one path set."""
        mod = _load_mutant(
            tmp_path,
            "m_frame",
            [
                (
                    '    return Path(_git(Path(repo), ["rev-parse", "--show-toplevel"]).strip())',
                    "    return Path(repo)",
                )
            ],
        )
        repo = _init_repo(tmp_path)
        _write(repo, "scripts/tests/untracked_here.py")
        _write(repo, "apps/roster/untracked_elsewhere.yaml")
        src = mod.collect_git_paths(repo / "scripts" / "tests")
        # the manufactured root-level component, and the file that vanished
        assert "untracked_here.py" in src.paths
        assert "apps/roster/untracked_elsewhere.yaml" not in src.paths

    def test_kills_the_exclusion_accounting(self, tmp_path: Path) -> None:
        """A path that vanishes with no note is a smaller number with no reason."""
        # Anchored on the assignment ABOVE the `if`, not on the `if` alone: since
        # the session source shares `_filter_excluded`, `    if dropped:` now
        # occurs twice and the loader's uniqueness assert caught the ambiguity
        # rather than letting the mutation land on the wrong occurrence.
        mod = _load_mutant(
            tmp_path,
            "m_excl_note",
            [
                (
                    "    paths, dropped = _filter_excluded(raw, exclude)\n    if dropped:",
                    "    paths, dropped = _filter_excluded(raw, exclude)\n    if False:",
                )
            ],
        )
        repo = _init_repo(tmp_path)
        _write(repo, "claudedocs/handoff-topic.md")
        src = mod.collect_git_paths(repo, exclude=["claudedocs/handoff-topic.md"])
        assert "claudedocs/handoff-topic.md" not in src.paths
        assert not any("excluded" in n for n in src.notes)

    def test_the_mutant_loader_refuses_an_ambiguous_anchor(self, tmp_path: Path) -> None:
        """Negative control on the mutation harness itself: an anchor that is
        not unique must abort, not silently mutate the first hit."""
        with pytest.raises(AssertionError) as exc:
            _load_mutant(tmp_path, "m_bad", [("    return ", "    return ")])
        assert "mutation anchor occurs" in str(exc.value)

    def test_the_mutant_loader_can_produce_a_WORKING_module(self, tmp_path: Path) -> None:
        """Positive control on the harness: if every mutant were simply broken
        on import, every kill above would be green for the wrong reason."""
        mod = _load_mutant(tmp_path, "m_noop", [('WRITER_ID = "handoff"', 'WRITER_ID = "handoff"  # noop')])
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(
            mod.caller_supplied(["src/collector/a.py", "src/collector/b.py"]),
            store,
            SCOPE,
            today=TODAY,
        )
        assert rep.status == "resolved"
        assert [m.entry.ref for m in rep.known] == ["collector"]


# =============================================================================
# 🔴 MUTATION KILL MATRIX — the SESSION source's guards.
# =============================================================================


def _session_mutant(tmp_path: Path, name: str, replacements, tailer):
    """A mutant that can reach the session source.

    ⚠ THE INJECTION IS NOT PART OF THE MUTATION. A mutant is written to
    `tmp_path`, so its `__file__` traversal to `scripts/collector/claude/` lands
    nowhere and `_session_tailer()` would raise `ExtractorMissingError` for every
    mutant alike — making every kill below green for the wrong reason. Handing it
    the already-loaded extractor removes that confound and changes nothing about
    the guard under test.
    """
    mod = _load_mutant(tmp_path, name, replacements)
    mod._SESSION_TAILER = tailer
    return mod


class TestSessionMutationKillMatrix:
    """Each session guard deleted on purpose, each dying to ITS OWN test.

    🔴 The confound this class is built around: these guards sit in a CHAIN, and
    a mutant with one removed usually trips the NEXT one. A kill that merely
    asserts "something raised" would be green with the guard it names deleted, so
    every test below asserts the specific sentinel is GONE — and, where the chain
    would otherwise swallow the effect, that the run now SUCCEEDS with the wrong
    answer, which is the actual hazard.
    """

    @pytest.fixture()
    def tailer(self, tailer_cache):
        return st._session_tailer()

    def test_the_session_mutant_harness_WORKS(self, tmp_path: Path, tailer) -> None:
        """Positive control on this class's own loader: an unmutated copy must
        reach the session source successfully, or every kill is vacuous."""
        mod = _session_mutant(
            tmp_path, "ms_noop", [('WRITER_ID = "handoff"', 'WRITER_ID = "handoff"  # noop')], tailer
        )
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl", str(repo), [f"{repo}/src/collector/a.py"]
        )
        assert mod.collect_session_paths(repo, transcript=t).paths == ("src/collector/a.py",)

    def test_kills_the_STALE_guard(self, tmp_path: Path, tailer) -> None:
        """Without it a yesterday's-uuid paste is reported as this session's
        work — silently, and with a perfectly well-formed answer."""
        mod = _session_mutant(
            tmp_path, "ms_stale", [("    if age > max_age_seconds:", "    if False:")], tailer
        )
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(
            tmp_path / "t.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py"],
            age_seconds=st.MAX_TRANSCRIPT_AGE_SECONDS * 100,
        )
        src = mod.collect_session_paths(repo, transcript=t)
        assert src.paths == ("src/collector/a.py",), "the stale guard was not the thing removed"
        with pytest.raises(st.TranscriptStaleError):
            st.collect_session_paths(repo, transcript=t)

    def test_kills_the_UNREADABLE_guard(self, tmp_path: Path, tailer) -> None:
        """🔴 Without it an unobservable file set becomes an EMPTY one, and
        `looked-at-nothing` — a confident 'this session touched nothing' from a
        transcript that was never read. The chain does not save it: an unreadable
        transcript has cwd '' and the mutant is shown to die on the NEXT guard,
        so this asserts the STALE/UNREADABLE sentinel is gone specifically."""
        mod = _session_mutant(
            tmp_path,
            "ms_unread",
            [('    if rollup.get("unreadable") or observed is None:', "    if False:")],
            tailer,
        )
        repo = _init_repo(tmp_path, SCOPE)
        t = tmp_path / "corrupt.jsonl"
        t.write_text("not json\n", encoding="utf-8")
        with pytest.raises(Exception) as exc:
            mod.collect_session_paths(repo, transcript=t)
        assert "transcript unreadable" not in str(exc.value)
        # The mutant's OWN class — an exec'd copy defines its own exceptions.
        assert isinstance(exc.value, mod.TranscriptCwdMismatchError)
        with pytest.raises(st.TranscriptUnreadableError):
            st.collect_session_paths(repo, transcript=t)

    def test_kills_the_UNREADABLE_guard_where_it_ALONE_stands(
        self, tmp_path: Path, tailer
    ) -> None:
        """🔴 The kill above is reached through the CWD guard, because a corrupt
        transcript also has no cwd — so on its own it cannot show WHICH guard is
        load-bearing. This one removes that confound: the extractor is replaced
        by one that reads the session fine (cwd intact) but reports an
        UNOBSERVABLE file set, which is the extractor's own contract for "we could
        not see the files". Nothing downstream rejects that input, so this guard
        is the only thing standing.

        ⚠ MEASURED, NOT ASSUMED: with the guard removed the run does NOT report a
        silent empty window — `None` reaches `_filter_excluded` and dies with a
        bare `TypeError`. That is still a kill, and it is the honest one: the
        real module fails with a NAMED, printable sentence, and `/handoff` step 4
        is instructed to print that stderr line verbatim. A traceback is not
        that.
        """

        class _Unobservable:
            """The extractor's own 'we could not observe this' shape."""

            @staticmethod
            def summarize_transcript(path, *, absolute_root=""):
                r = tailer.summarize_transcript(path, absolute_root=absolute_root)
                # 🔴 The ABSOLUTE block is nulled with the rest. Leaving it
                # populated would let the mutant fall through to the
                # session-absolute window and survive on a DIFFERENT window's
                # paths — a mutant kept alive by the fixture, not by the code.
                r.update({"changed_paths": None, "changed_paths_total": None,
                          "changed_paths_outside_cwd": None,
                          "changed_paths_absolute": None,
                          "changed_paths_absolute_total": None})
                return r

            projects_roots = staticmethod(tailer.projects_roots)

        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(tmp_path / "t.jsonl", str(repo), [f"{repo}/a.py"])

        mod = _session_mutant(
            tmp_path,
            "ms_unread2",
            [('    if rollup.get("unreadable") or observed is None:', "    if False:")],
            _Unobservable,
        )
        with pytest.raises(Exception) as exc:
            mod.collect_session_paths(repo, transcript=t)
        assert isinstance(exc.value, TypeError)
        assert "transcript unreadable" not in str(exc.value)

        # The unmutated module, same input, same replaced extractor: a named
        # error. This is what proves the guard — not the mutant — is what turns
        # an unobservable file set into a printable refusal.
        real = _session_mutant(
            tmp_path,
            "ms_unread3",
            [('WRITER_ID = "handoff"', 'WRITER_ID = "handoff"  # noop')],
            _Unobservable,
        )
        # `real.` and not `st.`: an exec'd copy defines its OWN exception
        # classes, so `st.TranscriptUnreadableError` would never match and the
        # assertion would be about module identity rather than behaviour.
        with pytest.raises(real.TranscriptUnreadableError) as ok:
            real.collect_session_paths(repo, transcript=t)
        assert "transcript unreadable" in str(ok.value)

    def test_kills_the_CWD_guard(self, tmp_path: Path, tailer) -> None:
        """🔴 The most consequential one. Without it, another repo's paths are
        reported — repo-relative to the WRONG root — and they RESOLVE, producing
        a fully plausible report attributing someone else's work to this repo."""
        mod = _session_mutant(
            tmp_path,
            "ms_cwd",
            [("    if not _same_dir(session_cwd, toplevel):", "    if False:")],
            tailer,
        )
        repo = _init_repo(tmp_path, SCOPE)
        other = tmp_path / "another-repo"
        t = _write_transcript(
            tmp_path / "t.jsonl", str(other), [f"{other}/src/collector/a.py"]
        )
        leaked = mod.collect_session_paths(repo, transcript=t)
        assert leaked.paths == ("src/collector/a.py",), "no leak — wrong thing removed"
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(leaked, store, SCOPE, today=TODAY, min_paths=1)
        assert [m.entry.ref for m in rep.known] == ["collector"], (
            "the leaked path did not even resolve — this kill would be vacuous"
        )
        with pytest.raises(st.TranscriptCwdMismatchError):
            st.collect_session_paths(repo, transcript=t)

    def test_kills_the_EMPTY_CWD_guard_inside_same_dir(self, tmp_path: Path, tailer) -> None:
        """`realpath("")` is the process cwd, so without the empty check a
        session that recorded no cwd matches wherever the tool was launched."""
        mod = _session_mutant(
            tmp_path, "ms_empty", [("    if not a or not b:", "    if False:")], tailer
        )
        assert mod._same_dir("", os.getcwd()) is True
        assert st._same_dir("", os.getcwd()) is False

    def test_kills_the_MISSING_guard(self, tmp_path: Path, projects_root: Path, tailer) -> None:
        """Without it an unresolvable id falls off the end of the function."""
        mod = _session_mutant(
            tmp_path, "ms_missing", [("    if not hits:", "    if False:")], tailer
        )
        with pytest.raises(Exception) as exc:
            mod.find_transcript(SESSION_ID)
        assert "transcript not found: no `" not in str(exc.value)
        with pytest.raises(st.TranscriptMissingError):
            st.find_transcript(SESSION_ID)

    def test_kills_the_AMBIGUITY_guard(self, tmp_path: Path, projects_root: Path, tailer) -> None:
        """🔴 Without it the resolver PICKS — silently harvesting whichever
        transcript sorted first, which is the coin flip this refuses to make."""
        mod = _session_mutant(
            tmp_path, "ms_ambig", [("    if len(hits) > 1:", "    if False:")], tailer
        )
        for sub in ("a", "b"):
            _write_transcript(
                projects_root / sub / f"{SESSION_ID}.jsonl", str(tmp_path), ["x.py"]
            )
        picked = mod.find_transcript(SESSION_ID)
        assert picked.name == f"{SESSION_ID}.jsonl"
        with pytest.raises(st.TranscriptAmbiguousError):
            st.find_transcript(SESSION_ID)

    def test_kills_the_SESSION_CAVEAT_branch(self, tmp_path: Path, tailer) -> None:
        """🔴 With the branch gone the caveat falls through to the GIT text —
        'what this BRANCH touched, NOT what this SESSION touched' — printed over
        a per-session window, which understates it in the opposite direction and
        is the specific wrongness this change exists to remove."""
        mod = _session_mutant(
            tmp_path, "ms_caveat", [('        if self.kind == "session":', "        if False:")], tailer
        )
        repo = _init_repo(tmp_path, SCOPE)
        t = _write_transcript(tmp_path / "t.jsonl", str(repo), [f"{repo}/a.py"])
        wrong = mod.collect_session_paths(repo, transcript=t).caveat
        assert "NOT what this SESSION touched" in wrong or "uncommitted work only" in wrong
        assert "THIS SESSION's own turns" not in wrong
        assert "THIS SESSION's own turns" in st.collect_session_paths(
            repo, transcript=t
        ).caveat

    def test_kills_the_SESSION_EXCLUSION(self, tmp_path: Path, tailer) -> None:
        """The ritual's own artifact leaks back into the window it wrote."""
        mod = _session_mutant(
            tmp_path,
            "ms_excl",
            [("    paths, dropped = _filter_excluded(observed, exclude)",
              "    paths, dropped = list(observed), []")],
            tailer,
        )
        repo = _init_repo(tmp_path, SCOPE)
        doc = "claudedocs/handoff-topic.md"
        t = _write_transcript(
            tmp_path / "t.jsonl", str(repo), [f"{repo}/{doc}", f"{repo}/src/collector/a.py"]
        )
        assert doc in mod.collect_session_paths(repo, transcript=t, exclude=[doc]).paths
        assert doc not in st.collect_session_paths(repo, transcript=t, exclude=[doc]).paths

    def test_kills_the_NO_FALLBACK_contract(
        self, tmp_path: Path, projects_root: Path, tailer, capsys
    ) -> None:
        """🔴 THE CENTRAL PROPERTY, mutated directly: a validation failure that
        falls back to git returns 0 and prints a plausible report — an answer to
        a question the caller did not ask, from a window that overlaps enough to
        look right. `/handoff` step 4 keys on the exit code, so this mutant would
        cause a WRITE."""
        mod = _session_mutant(
            tmp_path,
            "ms_fallback",
            [
                (
                    "    except (TouchError, ResolverError) as exc:\n"
                    '        print(f"subsystem-touch: {exc}", file=sys.stderr)\n'
                    "        return 3",
                    "    except (TouchError, ResolverError) as exc:\n"
                    '        print(f"subsystem-touch: {exc}", file=sys.stderr)\n'
                    "        print(render_text(build_report(collect_git_paths(repo), "
                    "args.store, scope, today=stamp)))\n"
                    "        return 0",
                )
            ],
            tailer,
        )
        repo = _init_repo(tmp_path, SCOPE)
        _write(repo, "src/collector/a.py")
        _write(repo, "src/collector/b.py")
        store = _make_store(tmp_path / "s")
        argv = ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
                "--today", TODAY]

        capsys.readouterr()
        assert mod.main(argv) == 0
        leaked = capsys.readouterr()
        assert "collector" in leaked.out, "the fallback produced nothing — kill is vacuous"

        assert st.main(argv) == 3
        real = capsys.readouterr()
        assert real.out.strip() == ""
        assert "transcript not found" in real.err

    def test_kills_the_PATHS_FROM_conflict_guard(
        self, tmp_path: Path, projects_root: Path, tailer, capsys
    ) -> None:
        """Without it, `--session X --paths-from -` silently honours ONE of two
        contradictory windows."""
        mod = _session_mutant(
            tmp_path,
            "ms_conflict",
            [('        if (wants_session or wants_pr or wants_commit) and args.paths_from != "git":',
              "        if False:")],
            tailer,
        )
        repo = _init_repo(tmp_path, SCOPE)
        _write_transcript(
            projects_root / f"{SESSION_ID}.jsonl",
            str(repo),
            [f"{repo}/src/collector/a.py", f"{repo}/src/collector/b.py"],
        )
        store = _make_store(tmp_path / "s")
        argv = ["--repo", str(repo), "--store", str(store), "--session", SESSION_ID,
                "--paths-from", "-", "--today", TODAY]
        capsys.readouterr()
        assert mod.main(argv) == 0
        assert st.main(argv) == 2
        assert "cannot be combined" in capsys.readouterr().err

    def test_kills_the_EXTRACTOR_presence_guard(
        self, tmp_path: Path, monkeypatch, tailer_cache
    ) -> None:
        """Without it a missing extractor dies with an unprintable traceback
        instead of the sentence `/handoff` is told to print verbatim."""
        mod = _load_mutant(
            tmp_path, "ms_extractor", [("    if not mod_path.is_file():", "    if False:")]
        )
        mod._SESSION_TAILER = None
        mod._session_tailer_path = lambda: tmp_path / "gone.py"
        with pytest.raises(Exception) as exc:
            mod._session_tailer()
        assert not isinstance(exc.value, st.ExtractorMissingError)
        assert "session path extractor not found" not in str(exc.value)


# =============================================================================
# THE PR SOURCE — the window that sees a SUBAGENT's work, and only a branch's.
# =============================================================================
#
# 🔴 WHY IT EXISTS, IN ONE MEASUREMENT: the standing default in this environment
# is to DELEGATE non-trivial work to a subagent, and a subagent's turns live in a
# SEPARATE transcript that the session source excludes by construction (196 of
# 733 file-tool calls across the 40 most recent transcripts). So on exactly the
# sessions worth recording, `--session` reports the docs the parent wrote and
# none of the implementation. A PR's file list does not care which agent, which
# session or which tool wrote the bytes.
#
# 🔴 AND IT IS BLIND IN THE OPPOSITE DIRECTION, which is what most of the
# assertions below are spent on: a PR's file list is the UNION of everything on
# the branch, including another session's commits.
# `TestPrCaveatAnswersTheRightQuestion` pins the wording that says so, because a
# set described as a session's is worse than no set at all — it would put another
# session's work into a dated work-history bullet in a curated,
# client-confidential store.
#
# 🔴 NO TEST BELOW RUNS `gh`, AND NONE CAN. `gh` is NOT in `REQUIRED_TOOLS` in
# scripts/run-tests.sh and NOT in `nativeBuildInputs` for flake.nix's
# `checks.pytests` — so the hermetic tier has no `gh` binary, and a test that
# shelled out would SKIP there, which the gate fails on purpose (EXPECTED_SKIPS
# is pinned exactly). `no_live_gh` is the structural guard: it intercepts any
# `gh` argv and fails loudly, so a test that forgot to inject a fetcher cannot
# quietly become a network test. Real `git` still runs — the repository identity
# is derived from a real remote on a real repo, and a mock would test the mock.
#
# 🔴 EVERY FIXTURE IS SYNTHETIC. `synthetic-org` is not a real GitHub owner and
# no real PR number, title or body appears here; this repo is PUBLIC.

PR_HOST = "github.com"
PR_OWNER = "synthetic-org"
PR_SLUG = f"{PR_HOST}/{PR_OWNER}/{SCOPE}"
PR_REMOTE = f"git@{PR_HOST}:{PR_OWNER}/{SCOPE}.git"

_UNSET = object()


@pytest.fixture(autouse=True)
def no_live_gh(monkeypatch):
    """🔴 Structural: a `gh` invocation from ANY test in this file is a failure.

    Autouse over the whole module rather than one class, because the hazard is a
    test that FORGOT to inject a fetcher — and such a test would by definition
    not be in the class that remembered. Real `git` passes through untouched.
    """
    real = subprocess.run

    def guard(argv, *a, **kw):
        prog = str(argv[0]) if isinstance(argv, (list, tuple)) and argv else ""
        assert Path(prog).name != "gh", (
            f"a test reached the LIVE `gh` binary: {argv!r}. Inject a fetcher — "
            f"`gh` is absent in the hermetic tier, so this would SKIP there and "
            f"each tier would be testing a different thing."
        )
        return real(argv, *a, **kw)

    monkeypatch.setattr(st.subprocess, "run", guard)


def test_the_no_live_gh_guard_can_actually_fire() -> None:
    """🔴 Positive control on the guard above. A guard nobody has watched fire is
    indistinguishable from one wired to nothing — and this one's whole job is to
    be silent."""
    with pytest.raises(AssertionError) as exc:
        st.subprocess.run(["gh", "--version"], capture_output=True, text=True)
    assert "reached the LIVE `gh` binary" in str(exc.value)


def test_the_no_live_gh_guard_lets_real_git_through(tmp_path: Path) -> None:
    """The other half of the pair: a guard that blocked everything would make
    every git-backed test below fail for the wrong reason."""
    proc = st.subprocess.run(["git", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0 and "git version" in proc.stdout


def _init_pr_repo(tmp_path: Path, *, remote: str | None = PR_REMOTE) -> Path:
    """A real git repo whose `origin` names a synthetic GitHub project."""
    repo = _init_repo(tmp_path, SCOPE)
    if remote is not None:
        _run_git(repo, "remote", "add", "origin", remote, home=tmp_path)
    return repo


def _pr_payload(
    number: int,
    files,
    *,
    slug: str = PR_SLUG,
    state: str = "MERGED",
    changed: int | None = None,
    url: object = _UNSET,
) -> dict:
    """A payload in the SHAPE `gh pr view --json` really returns.

    Verified against the live API 2026-08-12: `files` entries carry `path`,
    `additions`, `deletions` and `changeType`, and `changedFiles` is a sibling
    integer — which is the only signal that `files` was truncated.
    """
    return {
        "number": number,
        "url": f"https://{slug}/pull/{number}" if url is _UNSET else url,
        "state": state,
        "changedFiles": len(files) if changed is None else changed,
        "files": [
            {"path": p, "additions": 1, "deletions": 0, "changeType": "MODIFIED"}
            for p in files
        ],
    }


def _fetch(mapping):
    """An injected fetcher over `{number: payload}`; an Exception value raises."""

    def fetcher(slug, number):
        got = mapping[number]
        if isinstance(got, BaseException):
            raise got
        return got

    return fetcher


def _pr_source(repo: Path, mapping, numbers=None, **kw):
    return st.collect_pr_paths(
        repo,
        numbers if numbers is not None else list(mapping),
        fetch=_fetch(mapping),
        **kw,
    )


class TestPrPositiveControl:
    """🔴 `claude/RULES.md` → "Positive control — can it ever observe the thing?"

    A reassuring empty path set is indistinguishable from a fetcher wired to
    nothing, and this source has MORE ways to reach an honest-looking zero than
    any other here — ten of them raise. So the pair is reported: a payload that
    MUST yield paths against one that MUST yield none, on the same code path,
    through the same fetcher, in the same shape.
    """

    UNDER_TEST = ["src/collector/a.py", "src/collector/b.py", "src/collector/c.py"]

    def test_THE_PAIR_nonzero_under_test_zero_on_the_control(self, tmp_path: Path) -> None:
        repo = _init_pr_repo(tmp_path)
        pos = _pr_source(repo, {421: _pr_payload(421, self.UNDER_TEST)})
        # CONTROL: a real, well-formed, MERGED pull request that GitHub says
        # changed nothing. Identical machinery; the honest answer is zero, and it
        # must be zero for THAT reason rather than because nothing was read.
        neg = _pr_source(repo, {350: _pr_payload(350, [])})

        assert len(pos.paths) == 3, "positive control yielded nothing — wired to nothing"
        assert sorted(pos.paths) == sorted(self.UNDER_TEST)
        assert len(neg.paths) == 0

    def test_the_control_zero_is_ACCOUNTED_for_not_merely_empty(self, tmp_path: Path) -> None:
        """An empty list beside `0 file(s) reported by the API` is a reading; an
        empty list with no count is the silent zero."""
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {350: _pr_payload(350, [])})
        assert src.paths == ()
        assert any(
            "#350 (MERGED): 0 file(s) reported by the API, 0 read" in n for n in src.notes
        ), src.notes
        assert any("0 distinct path(s) across 1 pull request(s)" in n for n in src.notes)

    def test_the_per_pr_count_is_emitted_AT_NONZERO_too(self, tmp_path: Path) -> None:
        """The other half of the pair: a counter that only ever prints 0 is
        indistinguishable from one wired to a constant."""
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {421: _pr_payload(421, self.UNDER_TEST)})
        assert any(
            "#421 (MERGED): 3 file(s) reported by the API, 3 read" in n for n in src.notes
        ), src.notes
        assert any("3 distinct path(s) across 1 pull request(s)" in n for n in src.notes)

    def test_the_pair_survives_all_the_way_to_a_REPORT(self, tmp_path: Path) -> None:
        """The fetcher and the matcher are two places a zero can come from; both
        ends are pinned so one being wired to nothing cannot hide behind the
        other."""
        repo = _init_pr_repo(tmp_path)
        store = _make_store(tmp_path / "s")
        pos = _pr_source(repo, {421: _pr_payload(421, self.UNDER_TEST)})
        # Same count, same depth, same store — only the subsystem directory
        # differs, and it names nothing.
        neg = _pr_source(
            repo,
            {
                350: _pr_payload(
                    350, [f"src/unlisted-widget/{Path(p).name}" for p in self.UNDER_TEST]
                )
            },
        )
        pos_rep = st.build_report(pos, store, SCOPE, today=TODAY)
        neg_rep = st.build_report(neg, store, SCOPE, today=TODAY)

        assert pos_rep.status == "resolved"
        assert [m.entry.ref for m in pos_rep.known] == ["collector"]
        assert neg_rep.status == "no-match"
        # …and the negative control is NOT an empty window, which would make the
        # zero uninformative.
        assert len(neg_rep.source.paths) == 3

    def test_SEVERAL_prs_union_in_first_seen_order(self, tmp_path: Path) -> None:
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(
            repo,
            {421: _pr_payload(421, ["a.py", "b.py"]), 350: _pr_payload(350, ["b.py", "c.py"])},
            numbers=[421, 350],
        )
        assert src.paths == ("a.py", "b.py", "c.py")
        assert src.prs == (421, 350)

    def test_a_repeated_number_is_read_ONCE_and_SAID(self, tmp_path: Path) -> None:
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {421: _pr_payload(421, ["a.py"])}, numbers=[421, 421])
        assert src.prs == (421,)
        assert any("named more than once" in n for n in src.notes)

    def test_the_MEASURED_shape_of_a_real_response_is_what_the_fixture_uses(self) -> None:
        """🔴 A harness fed a textbook fixture proves nothing about live data
        (`claude/RULES.md`: build the bad case from REALISTIC data). These are the
        exact keys `gh pr view --json` returned for a real PR on 2026-08-12; if
        the fixture drifts from them, every control above tests a shape that does
        not exist."""
        payload = _pr_payload(1, ["x.py"])
        assert set(payload) == {"number", "url", "state", "changedFiles", "files"}
        assert set(payload["files"][0]) == {"path", "additions", "deletions", "changeType"}
        assert set(st.PR_JSON_FIELDS.split(",")) == set(payload)

    def test_excluded_paths_are_dropped_AND_counted(self, tmp_path: Path) -> None:
        """The SHARED exclusion predicate, not a third copy of it."""
        repo = _init_pr_repo(tmp_path)
        doc = "claudedocs/handoff-topic.md"
        src = _pr_source(
            repo, {421: _pr_payload(421, [doc, "src/collector/a.py"])}, exclude=[doc]
        )
        assert doc not in src.paths
        assert "src/collector/a.py" in src.paths
        assert any("excluded 1 caller-named path(s)" in n for n in src.notes)

    def test_the_store_is_never_written(self, tmp_path: Path) -> None:
        """The module's central invariant, exercised through the NEW source."""
        repo = _init_pr_repo(tmp_path)
        store = _make_store(tmp_path / "s")
        before = _tree_hash(store)
        rep = st.build_report(
            _pr_source(repo, {421: _pr_payload(421, self.UNDER_TEST)}),
            store,
            SCOPE,
            today=TODAY,
        )
        st.render_text(rep)
        json.dumps(st.report_json(rep))
        assert _tree_hash(store) == before


class TestPrNegativeControls:
    """Each guard fails with ITS OWN sentinel, reached by an input no EARLIER
    guard rejects — otherwise a control passes because a neighbour fired and
    stays green with the guard it claims to test deleted.

    🔴 THE SENTINEL MAP SPANS BOTH FAMILIES. A PR sentinel colliding with a
    session one would make BOTH families' `_only` assertions vacuous, so the
    premise test runs over the union, not over the PR half alone.
    """

    SENTINELS = {
        # session-source sentinels, so a cross-family collision is caught
        "t-missing": "transcript not found",
        "t-ambiguous": "transcript id is ambiguous",
        "t-stale": "transcript is stale",
        "t-unreadable": "transcript unreadable",
        "t-cwd": "transcript cwd does not match",
        "extractor": "session path extractor not found",
        # base sentinels
        "store": "store root not found",
        "git": "git command failed",
        # ⚠ Shares no spelling with "transcript unreadable" — checked by
        # `test_no_two_sentinels_share_a_spelling`, which is what keeps `_only`
        # from passing on a neighbour's error.
        "entry": "index entry unreadable",
        # ⚠ Shares no spelling with "index entry unreadable" (a file that could
        # not be OPENED) nor with the resolver's "malformed index entry" (a file
        # that could not be PARSED). Three conditions, three phrases, three
        # fixes; `test_no_two_sentinels_share_a_spelling` is what keeps them
        # from collapsing into one.
        "entry-file": "index entry file not found",
        # PR-source sentinels
        "remote": "repo has no usable github remote",
        "gh-missing": "gh cli not found",
        "gh-auth": "gh is not authenticated",
        "rate": "github api rate limit",
        "api": "github api call failed",
        "notfound": "pull request not found",
        "mismatch": "pull request belongs to another repository",
        "closed": "pull request is closed unmerged",
        "malformed": "pull request response is malformed",
        "truncated": "pull request file list is truncated",
        # commit-source sentinels. Here rather than in a fourth map because
        # `_only` is only a measurement if every OTHER guard's phrase is asserted
        # ABSENT — a commit sentinel missing from this dict would silently stop
        # being checked for by the PR and session controls above.
        # ⚠ "commit sha is malformed" shares no spelling with "pull request
        # response is malformed", and "commit not found" none with "pull request
        # not found" / "transcript not found" / "store root not found" / "gh cli
        # not found"; `test_no_two_sentinels_share_a_spelling` is what proves it.
        "c-malformed": "commit sha is malformed",
        "c-missing": "commit not found",
        "c-ambiguous": "commit sha is ambiguous",
        "c-type": "object is not a commit",
        "c-merge": "commit is a merge",
    }

    def _only(self, exc: Exception, key: str) -> None:
        text = str(exc)
        assert self.SENTINELS[key] in text, f"expected the {key} sentinel, got: {text}"
        for other, phrase in self.SENTINELS.items():
            if other != key:
                assert phrase not in text, f"the {other} sentinel also fired: {text}"

    def test_no_two_sentinels_share_a_spelling(self) -> None:
        """The premise of `_only`. Two guards spelled alike would make every
        assertion in this class — and in `TestSessionNegativeControls` — vacuous."""
        for a, pa in self.SENTINELS.items():
            for b, pb in self.SENTINELS.items():
                if a != b:
                    assert pa not in pb, f"{a} sentinel is a substring of {b}"

    def test_the_sentinel_map_COVERS_every_declared_error(self) -> None:
        """🔴 A hand-maintained map goes stale silently: a new error class with no
        entry here would never be asserted ABSENT, and every `_only` above would
        quietly stop being a measurement for it. Derived from the module's own
        `__all__` rather than trusted."""
        declared = {
            name for name in st.__all__ if name.endswith("Error") and name != "TouchError"
        }
        covered = {
            "RepoRemoteError", "GhMissingError", "GhAuthError", "GhRateLimitError",
            "GhApiError", "PrNotFoundError", "PrRepoMismatchError", "PrNotLandedError",
            "PrResponseMalformedError", "PrFileListTruncatedError",
            "CommitRefMalformedError", "CommitMissingError", "CommitAmbiguousError",
            "CommitWrongTypeError", "CommitIsMergeError",
            "StoreMissingError", "GitError", "ExtractorMissingError",
            "TranscriptMissingError", "TranscriptAmbiguousError", "TranscriptStaleError",
            "TranscriptUnreadableError", "TranscriptCwdMismatchError",
            # Re-exported from `subsystem_resolver`, not declared here — it is
            # the SAME class `subsystem_recall` raises for the same condition.
            # It is in `__all__`, so it is in scope for this map by the same rule
            # as everything else: a caller catching it does not care which module
            # the `class` statement is in.
            "EntryUnreadableError",
            # `--validate <path>` naming something that is not a file. Its own
            # sentinel ('entry file not found') rather than reusing the malformed
            # one: a missing path and a file that will not parse have different
            # fixes, and the validator's own output must not conflate them.
            "EntryFileMissingError",
        }
        assert declared == covered, (
            "subsystem_touch declares an error class this sentinel map does not "
            "account for; add it here AND give it its own sentinel entry."
        )
        assert len(self.SENTINELS) == len(covered)

    # --- the local guard, before anything leaves the machine ---------------------

    def test_a_repo_with_NO_remote_is_REMOTE(self, tmp_path: Path) -> None:
        """Reached first of all: a PR number cannot be interpreted without a
        repository, so nothing is fetched at all."""
        repo = _init_pr_repo(tmp_path, remote=None)
        with pytest.raises(st.RepoRemoteError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["a.py"])})
        self._only(exc.value, "remote")

    def test_a_remote_that_names_no_project_is_REMOTE(self, tmp_path: Path) -> None:
        """Reachable past the no-remote guard: a remote EXISTS, it simply does not
        name a host/owner/name."""
        repo = _init_pr_repo(tmp_path, remote="/srv/local-mirror")
        with pytest.raises(st.RepoRemoteError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["a.py"])})
        self._only(exc.value, "remote")

    def test_the_remote_error_QUOTES_git_without_carrying_gits_SENTINEL(
        self, tmp_path: Path
    ) -> None:
        """🔴 Found by `_only` on this suite's first run, and it is the general
        lesson: a wrapping error that interpolates the exception it wraps carries
        TWO sentinels, and "which guard fired" stops being a measurement. So
        `GitError` exposes `stderr` as an attribute and the wrapper quotes THAT.
        Both halves are asserted — the diagnostic survives, the sentinel does
        not."""
        repo = _init_pr_repo(tmp_path, remote=None)
        with pytest.raises(st.RepoRemoteError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["a.py"])})
        assert "git command failed" not in str(exc.value)
        assert "No such remote" in str(exc.value), "git's own diagnostic was lost"
        assert isinstance(exc.value.__cause__, st.GitError)
        assert exc.value.__cause__.stderr, "GitError.stderr is empty — nothing to quote"

    def test_the_remote_guard_is_reached_by_a_repo_that_ALSO_works(self, tmp_path: Path) -> None:
        """🔴 The reachability proof as a measurement: the same fixture with a
        usable remote produces a working source, so the two above fired on the
        remote and not on some incidental defect."""
        repo = _init_pr_repo(tmp_path)
        assert _pr_source(repo, {421: _pr_payload(421, ["a.py"])}).paths == ("a.py",)

    # --- the argument guard ------------------------------------------------------

    @pytest.mark.parametrize("token", ["abc", "", "-3", "0", "4.2", "١٢"])
    def test_a_token_that_is_not_a_pr_NUMBER_is_NOTFOUND(self, tmp_path: Path, token) -> None:
        """Never searched for. Note the last one: `str.isdigit()` is True for
        Arabic-Indic digits and `int()` accepts them, so an `isdigit()`-only check
        would silently accept a number nobody typed."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrNotFoundError) as exc:
            st.collect_pr_paths(repo, [token], fetch=_fetch({}))
        self._only(exc.value, "notfound")

    def test_NO_pr_numbers_at_all_is_NOTFOUND_not_an_empty_report(self, tmp_path: Path) -> None:
        """🔴 Otherwise: a perfectly well-formed report over ZERO pull requests —
        the confident zero, arriving through argument parsing."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrNotFoundError) as exc:
            st.collect_pr_paths(repo, [], fetch=_fetch({}))
        self._only(exc.value, "notfound")
        assert "no pull request number was given" in str(exc.value)

    # --- the response guards, in reachability order ------------------------------

    def test_a_NON_OBJECT_response_is_MALFORMED(self, tmp_path: Path) -> None:
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: ["not", "an", "object"]})
        self._only(exc.value, "malformed")

    def test_a_response_for_ANOTHER_pr_is_MALFORMED(self, tmp_path: Path) -> None:
        """Reachable past the object guard: a well-formed payload that simply is
        not the pull request that was asked for."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: _pr_payload(999, ["a.py"])}, numbers=[421])
        self._only(exc.value, "malformed")

    def test_a_response_with_NO_url_is_MALFORMED(self, tmp_path: Path) -> None:
        """Reachable past both: right number, everything else intact — the
        repository simply cannot be checked, and an unchecked repository is what
        makes a bare number dangerous."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["a.py"], url=None)})
        self._only(exc.value, "malformed")

    def test_ANOTHER_REPOSITORY_is_MISMATCH_not_malformed(self, tmp_path: Path) -> None:
        """🔴 Reachable past every guard above: the response is well-formed, has
        the right number and a perfectly good url — it just describes a different
        project. Every repository has a #1."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrRepoMismatchError) as exc:
            _pr_source(
                repo,
                {
                    421: _pr_payload(
                        421, ["src/collector/a.py"], slug=f"{PR_HOST}/other-org/other-repo"
                    )
                },
            )
        self._only(exc.value, "mismatch")

    def test_the_SAME_owner_and_name_on_ANOTHER_HOST_is_MISMATCH(self, tmp_path: Path) -> None:
        """🔴 The host is part of the identity. A repo mirrored on another forge
        can share `owner/name` with an unrelated GitHub project, and an
        owner/name-only comparison would call that a match and read its files."""
        repo = _init_pr_repo(tmp_path, remote=f"git@git.example.invalid:{PR_OWNER}/{SCOPE}.git")
        with pytest.raises(st.PrRepoMismatchError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["src/collector/a.py"])})
        self._only(exc.value, "mismatch")

    def test_a_CLOSED_unmerged_pr_is_NOT_LANDED(self, tmp_path: Path) -> None:
        """Reachable past the repository guard: right repo, right number — the
        branch was proposed and REJECTED, so its files exist in no tree."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrNotLandedError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["a.py"], state="CLOSED")})
        self._only(exc.value, "closed")

    @pytest.mark.parametrize("state", ["MERGED", "OPEN"])
    def test_MERGED_and_OPEN_are_BOTH_accepted(self, tmp_path: Path, state) -> None:
        """🔴 Measured at both accepted points, not one. OPEN is the ordinary case
        at `/handoff` time — CI still running, review not done — and refusing it
        would make the source useless exactly when it is invoked."""
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {421: _pr_payload(421, ["a.py"], state=state)})
        assert src.paths == ("a.py",)
        assert any(f"#421 ({state})" in n for n in src.notes)

    def test_an_UNKNOWN_state_is_MALFORMED_not_silently_accepted(self, tmp_path: Path) -> None:
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: _pr_payload(421, ["a.py"], state="DRAFTED")})
        self._only(exc.value, "malformed")

    def test_an_ABSENT_files_key_is_MALFORMED_not_an_empty_pr(self, tmp_path: Path) -> None:
        """🔴 The distinction this module is built on, at the other end of the
        pipe: an ABSENT list means the changed files are UNKNOWN, `[]` means
        GitHub says nothing changed. Reporting the second for the first is the
        silent zero."""
        repo = _init_pr_repo(tmp_path)
        payload = _pr_payload(421, [])
        del payload["files"]
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: payload})
        self._only(exc.value, "malformed")

    def test_an_EMPTY_files_list_is_a_READING_not_an_error(self, tmp_path: Path) -> None:
        """The other side of the pair above — without it the guard is merely a ban
        on empty PRs and the distinction it claims to draw is untested."""
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {421: _pr_payload(421, [])})
        assert src.paths == ()
        assert src.prs == (421,)

    def test_a_file_entry_with_NO_path_is_MALFORMED(self, tmp_path: Path) -> None:
        """Reachable past the list guard: `files` is present and IS a list — one
        entry in it is unreadable, and a list with an unreadable entry is not a
        shorter list."""
        repo = _init_pr_repo(tmp_path)
        payload = _pr_payload(421, ["a.py", "b.py"])
        payload["files"][1] = {"additions": 1}
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: payload})
        self._only(exc.value, "malformed")

    def test_a_missing_changedFiles_is_MALFORMED(self, tmp_path: Path) -> None:
        """Reachable past every guard above: without the count, truncation is
        UNDETECTABLE — and an undetectable truncation is a silently short set."""
        repo = _init_pr_repo(tmp_path)
        payload = _pr_payload(421, ["a.py"])
        del payload["changedFiles"]
        with pytest.raises(st.PrResponseMalformedError) as exc:
            _pr_source(repo, {421: payload})
        self._only(exc.value, "malformed")

    def test_a_TRUNCATED_file_list_REFUSES(self, tmp_path: Path) -> None:
        """🔴 THE MEASURED HAZARD. `gh pr view --json files` caps at 100 while
        `changedFiles` reports the truth — measured live at 411/100 and 301/100 on
        2026-08-12, and 39/39 under the cap. Reported as a prefix it would be a
        plausible, silently wrong answer."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.PrFileListTruncatedError) as exc:
            _pr_source(
                repo, {421: _pr_payload(421, [f"f{i}.py" for i in range(100)], changed=411)}
            )
        self._only(exc.value, "truncated")
        assert "changed 411 files but the API returned only 100" in str(exc.value)

    def test_the_truncation_guard_is_a_BOUNDARY_not_a_slope(self, tmp_path: Path) -> None:
        """🔴 Measured at TWO points, not one: a guard asserted only from beyond
        the boundary is equally consistent with one that rejects everything. The
        equal case is the live 39/39 reading."""
        repo = _init_pr_repo(tmp_path)
        equal = _pr_source(repo, {421: _pr_payload(421, ["a.py", "b.py"], changed=2)})
        assert equal.paths == ("a.py", "b.py")
        with pytest.raises(st.PrFileListTruncatedError):
            _pr_source(repo, {350: _pr_payload(350, ["a.py", "b.py"], changed=3)})

    def test_MORE_files_than_changedFiles_is_NOT_an_error(self, tmp_path: Path) -> None:
        """The guard is one-sided on purpose: it detects a SHORT list. A list
        LONGER than the count is not a truncation, and inventing an error for it
        would refuse a reading nothing is wrong with."""
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {421: _pr_payload(421, ["a.py", "b.py"], changed=1)})
        assert src.paths == ("a.py", "b.py")

    # --- the environment guards, through the fetcher -----------------------------

    @pytest.mark.parametrize(
        "factory,key",
        [
            (lambda: st.GhMissingError("gh cli not found: no `gh` on PATH"), "gh-missing"),
            (lambda: st.GhAuthError("gh is not authenticated: HTTP 401"), "gh-auth"),
            (lambda: st.GhRateLimitError("github api rate limit: HTTP 403"), "rate"),
            (lambda: st.GhApiError("github api call failed: error connecting"), "api"),
            (lambda: st.PrNotFoundError("pull request not found: no such PR"), "notfound"),
        ],
        ids=["gh-missing", "gh-auth", "rate-limit", "api-error", "not-found"],
    )
    def test_a_fetcher_failure_PROPAGATES_and_never_becomes_an_empty_set(
        self, tmp_path: Path, factory, key
    ) -> None:
        """🔴 THE CENTRAL PROPERTY OF THE NETWORK SURFACE. Every environmental
        failure reaches the caller as ITS OWN named error — an empty path set
        would be indistinguishable from "this PR changed nothing"."""
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(st.TouchError) as exc:
            _pr_source(repo, {421: factory()})
        self._only(exc.value, key)

    def test_EVERY_pr_failure_is_a_TouchError_so_the_CLI_exits_nonzero(self) -> None:
        """🔴 `/handoff` step 4 keys on the exit code alone, and its contract is
        that the stderr line is printable verbatim. A traceback is not that."""
        for cls in (
            st.RepoRemoteError, st.GhMissingError, st.GhAuthError, st.GhRateLimitError,
            st.GhApiError, st.PrNotFoundError, st.PrRepoMismatchError, st.PrNotLandedError,
            st.PrResponseMalformedError, st.PrFileListTruncatedError,
        ):
            assert issubclass(cls, st.TouchError), cls


class TestGhFetcherClassification:
    """The one thing a fixture payload cannot reach: `_gh_fetch_pr` reading `gh`'s
    OWN exit code and stderr.

    🔴 PINNED AGAINST MEASURED STRINGS, NOT INVENTED ONES (`claude/RULES.md`:
    "build the bad case from REALISTIC data, not a textbook fixture"). Every
    stderr below was captured from gh 2.96.0 on 2026-08-12 by provoking the real
    failure; the classifier is then exercised against those bytes with the
    subprocess seam replaced, so it runs in a tier that has no `gh` at all.
    """

    MEASURED = [
        ("no-credentials", 4,
         "To get started with GitHub CLI, please run:  gh auth login\n"
         "Alternatively, populate the GH_TOKEN environment variable with a GitHub "
         "API authentication token.", "GhAuthError"),
        ("bad-token", 1,
         "HTTP 401: Bad credentials (https://api.github.com/graphql)\n"
         "Try authenticating with:  gh auth login", "GhAuthError"),
        ("unreachable-host", 1,
         "error connecting to nonexistent.invalid\n"
         "check your internet connection or https://githubstatus.com", "GhApiError"),
        ("nonexistent-pr", 1,
         "GraphQL: Could not resolve to a PullRequest with the number of 999999. "
         "(repository.pullRequest)", "PrNotFoundError"),
    ]

    @pytest.mark.parametrize(
        "label,rc,stderr,expected", MEASURED, ids=[m[0] for m in MEASURED]
    )
    def test_the_measured_failures_classify(self, label, rc, stderr, expected) -> None:
        got = st._classify_gh_failure(rc, stderr, PR_SLUG, 421)
        assert type(got) is getattr(st, expected), (
            f"{label} classified as {type(got).__name__}, expected {expected}"
        )
        assert stderr.splitlines()[0] in str(got), "gh's own words are not carried through"

    def test_a_RATE_LIMIT_is_not_read_as_an_auth_failure(self) -> None:
        """🔴 THE ORDERING TRAP, as a test. A 403 rate-limit body ALSO carries
        gh's "gh auth login" hint, so an auth-first classifier reports a permanent
        failure for a temporary one and the caller stops retrying."""
        stderr = (
            "HTTP 403: API rate limit exceeded for user ID 1. "
            "(https://api.github.com/graphql)\nTry authenticating with:  gh auth login"
        )
        assert type(st._classify_gh_failure(1, stderr, PR_SLUG, 421)) is st.GhRateLimitError

    def test_an_UNRECOGNISED_failure_falls_through_to_the_WIDE_error(self) -> None:
        """Not guessed onto a specific diagnosis: a wrong explanation of a failure
        forecloses the next question."""
        got = st._classify_gh_failure(1, "HTTP 502: upstream exploded", PR_SLUG, 421)
        assert type(got) is st.GhApiError, "a 502 was mapped onto a narrower diagnosis"

    def test_an_EMPTY_stderr_still_classifies_rather_than_crashing(self) -> None:
        got = st._classify_gh_failure(1, "", PR_SLUG, 421)
        assert type(got) is st.GhApiError
        assert "(no stderr)" in str(got)

    def test_a_MISSING_gh_BINARY_is_named_not_a_traceback(self, monkeypatch) -> None:
        """🔴 A LIVE CASE: `gh` is absent from `REQUIRED_TOOLS` and from the
        flake's `nativeBuildInputs`, so the hermetic tier has none. Simulated at
        the subprocess seam rather than by running anything."""

        def boom(argv, *a, **kw):
            raise FileNotFoundError(2, "No such file or directory", "gh")

        monkeypatch.setattr(st.subprocess, "run", boom)
        with pytest.raises(st.GhMissingError) as exc:
            st._gh_fetch_pr(PR_SLUG, 421)
        assert "gh cli not found" in str(exc.value)

    def test_a_ZERO_exit_with_NON_JSON_stdout_is_MALFORMED(self, monkeypatch) -> None:
        """A success that returns rubbish is not a success. Reachable past every
        exit-code branch: rc IS 0, so only the decode can object."""
        monkeypatch.setattr(
            st.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "<html>nope</html>", ""),
        )
        with pytest.raises(st.PrResponseMalformedError) as exc:
            st._gh_fetch_pr(PR_SLUG, 421)
        assert "pull request response is malformed" in str(exc.value)

    def test_the_fetcher_POSITIVE_control(self, monkeypatch) -> None:
        """🔴 Every case above is a failure; a fetcher that failed on EVERYTHING
        would pass them all. This is the case that must SUCCEED."""
        payload = _pr_payload(421, ["src/collector/a.py"])
        monkeypatch.setattr(
            st.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, json.dumps(payload), ""),
        )
        assert st._gh_fetch_pr(PR_SLUG, 421)["number"] == 421

    def test_the_argv_asks_for_the_fields_the_reader_READS(self) -> None:
        """A field dropped from the query but still read would come back as a
        malformed response on every single run."""
        argv = st._gh_argv(PR_SLUG, 421)
        assert argv[0] == "gh" and "--repo" in argv and PR_SLUG in argv
        assert st.PR_JSON_FIELDS in argv
        for field_name in ("number", "url", "state", "changedFiles", "files"):
            assert field_name in st.PR_JSON_FIELDS.split(",")


class TestRepoSlugDerivation:
    """The repository a PR number is interpreted against — DERIVED, never assumed
    and never taken from the caller."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            (f"git@{PR_HOST}:{PR_OWNER}/{SCOPE}.git", PR_SLUG),
            (f"git@{PR_HOST}:{PR_OWNER}/{SCOPE}", PR_SLUG),
            (f"ssh://git@{PR_HOST}/{PR_OWNER}/{SCOPE}.git", PR_SLUG),
            (f"https://{PR_HOST}/{PR_OWNER}/{SCOPE}.git", PR_SLUG),
            (f"https://{PR_HOST}/{PR_OWNER}/{SCOPE}/", PR_SLUG),
            (f"https://user@{PR_HOST}/{PR_OWNER}/{SCOPE}", PR_SLUG),
            ("git@git.example.invalid:o/n.git", "git.example.invalid/o/n"),
            ("/srv/local-mirror", None),
            ("", None),
            ("https://github.com/", None),
        ],
        ids=["scp", "scp-no-suffix", "ssh-url", "https", "trailing-slash",
             "https-userinfo", "other-host", "local-path", "empty", "no-project"],
    )
    def test_the_spellings_git_actually_emits(self, url, expected) -> None:
        assert st._parse_remote_slug(url) == expected

    def test_the_HOST_is_part_of_the_slug(self) -> None:
        """🔴 Two projects sharing owner/name on different forges must not produce
        the same slug — that is the entire basis of the repository guard."""
        a = st._parse_remote_slug(f"git@{PR_HOST}:o/n.git")
        b = st._parse_remote_slug("git@git.example.invalid:o/n.git")
        assert a is not None and b is not None and a != b

    def test_it_reads_a_REAL_remote(self, tmp_path: Path) -> None:
        """Real git, not a mock: the derivation is the design decision."""
        repo = _init_pr_repo(tmp_path)
        assert st._repo_slug(repo) == PR_SLUG

    def test_a_pr_url_yields_the_SAME_slug_SHAPE_the_remote_does(self) -> None:
        """🔴 The repository comparison is only meaningful if both sides are built
        to the same shape — otherwise it would never match and the guard would
        fire on everything, which is a guard nobody can use."""
        assert st._slug_from_pr_url(f"https://{PR_SLUG}/pull/421") == PR_SLUG
        assert st._slug_from_pr_url("nonsense") is None
        assert st._slug_from_pr_url(None) is None


class TestPrCaveatAnswersTheRightQuestion:
    """🔴 THE CRUX. A PR's file list is NOT a session's, and the caveat is the only
    thing standing between that fact and an LLM about to write a dated
    work-history bullet into a curated, client-confidential store."""

    def _caveat(self, tmp_path: Path, **kw) -> str:
        repo = _init_pr_repo(tmp_path)
        return _pr_source(repo, {421: _pr_payload(421, ["a.py"])}, **kw).caveat

    def test_it_says_BRANCH_and_never_claims_SESSION_attribution(self, tmp_path: Path) -> None:
        caveat = self._caveat(tmp_path)
        assert "what the BRANCH LANDED" in caveat
        assert "NOT what a SESSION touched" in caveat
        # …and none of the session source's affirmative language leaks in.
        assert "THIS SESSION's own turns" not in caveat

    def test_it_names_the_OVER_reporting_the_union_causes(self, tmp_path: Path) -> None:
        """A caveat naming only the blind spot would read as if over-reporting
        were not the other half — and over-reporting is the half that puts
        someone else's work into the journal."""
        caveat = self._caveat(tmp_path)
        for phrase in ("UNION of every commit", "ANOTHER session", "SUBAGENT", "by hand"):
            assert phrase in caveat, phrase

    def test_it_names_what_the_pr_window_CANNOT_see(self, tmp_path: Path) -> None:
        assert (
            "EXCLUDES anything a session did that did not reach one of these PRs"
            in self._caveat(tmp_path)
        )

    def test_it_names_the_REPOSITORY_and_the_PRS(self, tmp_path: Path) -> None:
        """"#4" is ambiguous across repositories; the identity is the point."""
        repo = _init_pr_repo(tmp_path)
        caveat = _pr_source(
            repo,
            {421: _pr_payload(421, ["a.py"]), 350: _pr_payload(350, ["b.py"])},
            numbers=[421, 350],
        ).caveat
        assert "#421, #350" in caveat
        assert PR_SLUG in caveat

    def test_the_OTHER_sources_caveats_are_UNCHANGED(self, tmp_path: Path) -> None:
        """Adding a source must not soften the claim another one makes."""
        repo = _init_pr_repo(tmp_path)
        _run_git(repo, "checkout", "-b", "topic", home=tmp_path)
        _write(repo, "src/collector/a.py")
        _run_git(repo, "add", "src", home=tmp_path)
        _run_git(repo, "commit", "-m", "w", home=tmp_path)
        assert "NOT what this SESSION touched" in st.collect_git_paths(repo).caveat
        assert "provenance is the caller's" in st.caller_supplied(["a.py"]).caveat

    @pytest.mark.parametrize(
        "paths,expect",
        [
            (["src/collector/a.py", "src/collector/b.py"], "resolved"),
            (["docs/a.md", "docs/b.md"], "no-match"),
            ([], "looked-at-nothing"),
        ],
        ids=["resolved", "no-match", "looked-at-nothing"],
    )
    def test_the_caveat_is_on_EVERY_output_path_of_BOTH_renderers(
        self, tmp_path: Path, paths, expect
    ) -> None:
        """🔴 The property the #415 audit established and #421 kept: the caveat is
        on every output path of both renderers — INCLUDING the early-return
        `looked-at-nothing` branch, which returns before most of the text is
        built. A third source must not be the one that breaks it."""
        repo = _init_pr_repo(tmp_path)
        store = _make_store(tmp_path / "s")
        rep = st.build_report(
            _pr_source(repo, {421: _pr_payload(421, paths)}), store, SCOPE, today=TODAY
        )
        assert rep.status == expect
        caveat = rep.source.caveat
        assert caveat in st.render_text(rep)
        payload = st.report_json(rep)
        assert payload["source"]["caveat"] == caveat
        assert payload["source"]["prs"] == [421]
        assert payload["source"]["repo_slug"] == PR_SLUG


class TestCommandsAreTheRealArgv:
    """`PathSource.commands` exists so a reader can check what was ACTUALLY asked.
    It used to omit the program name because every source ran `git` and the
    renderer hardcoded that word — which became a FALSE line the moment a source
    ran something else."""

    def test_the_git_source_records_its_program(self, tmp_path: Path) -> None:
        repo = _init_pr_repo(tmp_path)
        _write(repo, "a.py")
        src = st.collect_git_paths(repo)
        assert src.commands and all(c[0] == "git" for c in src.commands), src.commands

    def test_the_session_source_records_its_program(
        self, tmp_path: Path, tailer_cache
    ) -> None:
        repo = _init_pr_repo(tmp_path)
        t = _write_transcript(tmp_path / "t.jsonl", str(repo), [f"{repo}/a.py"])
        src = st.collect_session_paths(repo, transcript=t)
        assert src.commands and all(c[0] == "git" for c in src.commands), src.commands

    def test_the_pr_source_records_gh_NOT_git(self, tmp_path: Path, monkeypatch) -> None:
        """🔴 The regression this fixes: rendered under the old hardcoded `git`,
        this line read `ran: git gh pr view 421` — a provenance line false about
        the one thing it exists to report. The LIVE path is what records an argv,
        so the swap is at the subprocess seam; an injected fetcher records
        nothing (see below) and could not exercise this at all."""
        repo = _init_pr_repo(tmp_path)
        store = _make_store(tmp_path / "s")
        payload = _pr_payload(421, ["src/collector/a.py", "src/collector/b.py"])
        real = subprocess.run

        def fake(argv, *a, **kw):
            if Path(str(argv[0])).name == "gh":
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            return real(argv, *a, **kw)

        monkeypatch.setattr(st.subprocess, "run", fake)
        src = st.collect_pr_paths(repo, [421])
        assert src.commands and src.commands[0][0] == "gh"
        rendered = st.render_text(st.build_report(src, store, SCOPE, today=TODAY))
        assert "ran: gh pr view 421" in rendered
        assert "ran: git gh" not in rendered

    def test_an_INJECTED_fetcher_records_NO_argv_and_SAYS_so(self, tmp_path: Path) -> None:
        """🔴 With a fetcher injected NOTHING ran, so recording `gh pr view …`
        would fabricate provenance in the one field whose purpose is to report
        what was actually executed."""
        repo = _init_pr_repo(tmp_path)
        src = _pr_source(repo, {421: _pr_payload(421, ["a.py"])})
        assert src.commands == ()
        assert any("INJECTED fetcher" in n for n in src.notes)


class TestPrCli:
    """The CLI is the only surface `/handoff` touches."""

    def _run(self, args, capsys):
        rc = st.main(args)
        return rc, capsys.readouterr()

    def _fixture(self, tmp_path: Path):
        return _init_pr_repo(tmp_path), _make_store(tmp_path / "s")

    def _patched(self, monkeypatch, payloads):
        """Swap the live fetcher at the module seam the CLI actually reaches. The
        CLI takes no `fetch` argument deliberately: a production flag that
        replaces the data source is a flag that can lie."""
        monkeypatch.setattr(st, "_gh_fetch_pr", _fetch(payloads))

    def test_a_pr_resolves_end_to_end(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo, store = self._fixture(tmp_path)
        self._patched(
            monkeypatch,
            {421: _pr_payload(421, ["src/collector/a.py", "src/collector/b.py"])},
        )
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--pr", "421",
             "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["source"]["kind"] == "pr"
        assert payload["source"]["window"] == "pull-requests"
        assert payload["source"]["prs"] == [421]
        assert payload["source"]["repo_slug"] == PR_SLUG
        assert payload["status"] == "resolved"
        assert payload["known"][0]["ref"] == "collector"

    @pytest.mark.parametrize(
        "argv_pr", [["--pr", "421,350"], ["--pr", "421", "--pr", "350"]],
        ids=["comma", "repeated"],
    )
    def test_both_spellings_of_several_prs(
        self, tmp_path: Path, monkeypatch, capsys, argv_pr
    ) -> None:
        repo, store = self._fixture(tmp_path)
        self._patched(
            monkeypatch,
            {421: _pr_payload(421, ["src/collector/a.py"]),
             350: _pr_payload(350, ["src/collector/b.py"])},
        )
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json", *argv_pr],
            capsys,
        )
        assert rc == 0
        assert json.loads(cap.out)["source"]["prs"] == [421, 350]

    def test_a_trailing_comma_is_the_typo_it_looks_like(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        repo, store = self._fixture(tmp_path)
        self._patched(monkeypatch, {421: _pr_payload(421, ["a.py"])})
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--pr", "421,",
             "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        assert json.loads(cap.out)["source"]["prs"] == [421]

    def test_an_EMPTY_pr_argument_exits_3_naming_the_sentinel(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Reaches the "no number was given" guard rather than the "not a usable
        number" one — a different fact, and the empty-token filter is what makes
        it reachable."""
        repo, store = self._fixture(tmp_path)
        self._patched(monkeypatch, {})
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--pr", ",", "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert "no pull request number was given" in cap.err
        assert cap.out.strip() == ""

    @pytest.mark.parametrize(
        "failure,phrase",
        [
            (st.GhMissingError("gh cli not found: nope"), "gh cli not found"),
            (st.GhAuthError("gh is not authenticated: nope"), "gh is not authenticated"),
            (st.GhRateLimitError("github api rate limit: nope"), "github api rate limit"),
            (st.GhApiError("github api call failed: nope"), "github api call failed"),
            (st.PrNotFoundError("pull request not found: nope"), "pull request not found"),
            (st.PrRepoMismatchError("pull request belongs to another repository: nope"),
             "pull request belongs to another repository"),
            (st.PrNotLandedError("pull request is closed unmerged: nope"),
             "pull request is closed unmerged"),
            (st.PrResponseMalformedError("pull request response is malformed: nope"),
             "pull request response is malformed"),
            (st.PrFileListTruncatedError("pull request file list is truncated: nope"),
             "pull request file list is truncated"),
        ],
        ids=["gh-missing", "gh-auth", "rate-limit", "api-error", "not-found",
             "repo-mismatch", "closed", "malformed", "truncated"],
    )
    def test_every_failure_exits_3_with_a_PRINTABLE_line_and_NO_report(
        self, tmp_path: Path, monkeypatch, capsys, failure, phrase
    ) -> None:
        """🔴 THE CONTRACT `/handoff` KEYS ON: non-zero, one printable stderr line,
        and NOTHING on stdout — a report printed beside an error is a report
        someone will act on."""
        repo, store = self._fixture(tmp_path)
        self._patched(monkeypatch, {421: failure})
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--pr", "421", "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert phrase in cap.err
        assert cap.out.strip() == ""

    def test_a_network_failure_NEVER_falls_back_to_git(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """🔴 THE CENTRAL PROPERTY, inherited from the session source. The repo has
        real uncommitted work that git WOULD report, so a fallback would produce a
        full, plausible report — an answer to a question the caller did not ask,
        and `/handoff` would write from it."""
        repo, store = self._fixture(tmp_path)
        _write(repo, "src/collector/a.py")
        _write(repo, "src/collector/b.py")
        assert len(st.collect_git_paths(repo).paths) >= 2, "premise gone: git sees nothing"
        self._patched(monkeypatch, {421: st.GhApiError("github api call failed: down")})
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--pr", "421", "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert cap.out.strip() == ""
        assert "collector" not in cap.out

    def test_the_text_renderer_prints_the_caveat_and_the_notes(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        repo, store = self._fixture(tmp_path)
        self._patched(
            monkeypatch,
            {421: _pr_payload(421, ["src/collector/a.py", "src/collector/b.py"])},
        )
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--pr", "421", "--today", TODAY],
            capsys,
        )
        assert rc == 0
        assert "caveat: pull request(s) #421" in cap.out
        assert "what the BRANCH LANDED, NOT what a SESSION touched" in cap.out
        assert "2 file(s) reported by the API" in cap.out


class TestTheSourcesDoNotCompose:
    """🔴 ONE QUESTION PER RUN. `--session` and `--pr` answer different questions
    with OPPOSITE biases, and a union would carry ONE caveat line asserting
    session attribution for some members and denying it for others, with no way
    to tell which is which. The composition that IS available — run it twice and
    read two caveats — keeps exactly the attribution a union destroys.
    """

    @pytest.mark.parametrize(
        "extra",
        [["--session", SESSION_ID], ["--transcript", "/nonexistent.jsonl"]],
        ids=["session", "transcript"],
    )
    def test_pr_with_a_session_is_REFUSED_by_argparse(self, tmp_path: Path, extra) -> None:
        """Exit 2, the same enforcement `--session` vs `--transcript` already uses:
        they name different windows and picking one would be a guess about which
        the caller meant."""
        with pytest.raises(SystemExit) as exc:
            st.main(["--repo", str(tmp_path), "--pr", "421", *extra])
        assert exc.value.code == 2

    def test_pr_with_paths_from_is_REFUSED_with_a_printable_line(
        self, tmp_path: Path, capsys
    ) -> None:
        repo = _init_pr_repo(tmp_path)
        assert st.main(["--repo", str(repo), "--pr", "421", "--paths-from", "-"]) == 2
        assert "cannot be combined" in capsys.readouterr().err

    def test_the_session_refusal_STILL_names_itself(self, tmp_path: Path, capsys) -> None:
        """The message was widened to cover `--pr`; it must not have stopped
        naming the flags it already covered."""
        repo = _init_pr_repo(tmp_path)
        assert st.main(["--repo", str(repo), "--session", SESSION_ID, "--paths-from", "-"]) == 2
        assert "--session/--transcript/--pr" in capsys.readouterr().err

    def test_running_it_TWICE_is_the_supported_composition(
        self, tmp_path: Path, monkeypatch, projects_root: Path, tailer_cache, capsys
    ) -> None:
        """🔴 The decision, as a measurement: two runs give two path sets EACH WITH
        ITS OWN CAVEAT. The two windows deliberately DISAGREE here — the PR
        carries an implementation file the session transcript never saw (the
        subagent case), and the session carries a doc that reached no PR — which
        is precisely why a union could not describe either honestly."""
        repo = _init_pr_repo(tmp_path)
        store = _make_store(tmp_path / "s")
        _write_transcript(
            projects_root / f"{SESSION_ID}.jsonl",
            str(repo),
            [f"{repo}/claudedocs/handoff-x.md"],
        )
        monkeypatch.setattr(
            st, "_gh_fetch_pr", _fetch({421: _pr_payload(421, ["src/collector/a.py"])})
        )
        base = ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json"]

        assert st.main(base + ["--session", SESSION_ID]) == 0
        ses = json.loads(capsys.readouterr().out)
        assert st.main(base + ["--pr", "421"]) == 0
        pr = json.loads(capsys.readouterr().out)

        assert ses["source"]["paths"] == ["claudedocs/handoff-x.md"]
        assert pr["source"]["paths"] == ["src/collector/a.py"]
        # The point: each set arrives with its OWN account of what it is.
        assert ses["source"]["caveat"] != pr["source"]["caveat"]
        assert "THIS SESSION's own turns" in ses["source"]["caveat"]
        assert "what the BRANCH LANDED" in pr["source"]["caveat"]
        assert ses["source"]["kind"] == "session" and pr["source"]["kind"] == "pr"


class TestPrMutationKillMatrix:
    """Each PR guard deleted on purpose, each dying to ITS OWN test.

    🔴 The confound this class is built around, as in the session chain: these
    guards run in sequence over one payload, and a mutant with one removed
    usually trips the NEXT one. A kill that merely asserts "something raised"
    would be green with the guard it names deleted — so every test below asserts
    the specific sentinel is GONE and, where the chain would otherwise swallow
    the effect, that the run now SUCCEEDS with the wrong answer, which is the
    actual hazard.
    """

    def test_the_pr_mutant_harness_WORKS(self, tmp_path: Path) -> None:
        """Positive control on this class's own loader: an unmutated copy must
        reach the PR source successfully, or every kill below is vacuous."""
        mod = _load_mutant(
            tmp_path, "mp_noop", [('WRITER_ID = "handoff"', 'WRITER_ID = "handoff"  # noop')]
        )
        repo = _init_pr_repo(tmp_path)
        src = mod.collect_pr_paths(repo, [421], fetch=_fetch({421: _pr_payload(421, ["a.py"])}))
        assert src.paths == ("a.py",)

    def test_kills_the_REPOSITORY_guard(self, tmp_path: Path) -> None:
        """🔴 THE MOST CONSEQUENTIAL ONE. Without it another project's file list is
        read AND reported — repo-relative, well-formed, entirely unrelated — and
        it RESOLVES, manufacturing an association out of a bare number."""
        mod = _load_mutant(
            tmp_path, "mp_repo",
            [("    if got_slug.lower() != slug.lower():", "    if False:")],
        )
        repo = _init_pr_repo(tmp_path)
        foreign = {
            421: _pr_payload(
                421,
                ["src/collector/a.py", "src/collector/b.py"],
                slug=f"{PR_HOST}/other-org/other-repo",
            )
        }
        leaked = mod.collect_pr_paths(repo, [421], fetch=_fetch(foreign))
        assert leaked.paths == ("src/collector/a.py", "src/collector/b.py"), "wrong thing removed"
        store = _make_store(tmp_path / "s")
        rep = mod.build_report(leaked, store, SCOPE, today=TODAY)
        assert [m.entry.ref for m in rep.known] == ["collector"], (
            "the leaked paths did not even resolve — this kill would be vacuous"
        )
        with pytest.raises(st.PrRepoMismatchError):
            st.collect_pr_paths(repo, [421], fetch=_fetch(foreign))

    def test_kills_the_HOST_half_of_the_repository_identity(self, tmp_path: Path) -> None:
        """🔴 Reached by an input the comparison itself accepts. Both slug builders
        are collapsed to `owner/name`, which is what an owner/name-only identity
        would look like — and a repo on ANOTHER FORGE then matches an unrelated
        GitHub project and its file list is read and reported."""
        mod = _load_mutant(
            tmp_path,
            "mp_host",
            [
                ('    return f"{host}/{parts[-2]}/{parts[-1]}"',
                 '    return f"{parts[-2]}/{parts[-1]}"'),
                ('    return f"{host}/{parts[1]}/{parts[2]}"',
                 '    return f"{parts[1]}/{parts[2]}"'),
            ],
        )
        repo = _init_pr_repo(tmp_path, remote=f"git@git.example.invalid:{PR_OWNER}/{SCOPE}.git")
        payloads = {421: _pr_payload(421, ["src/collector/a.py"])}  # a github.com url
        leaked = mod.collect_pr_paths(repo, [421], fetch=_fetch(payloads))
        assert leaked.paths == ("src/collector/a.py",), "no leak — wrong thing removed"
        with pytest.raises(st.PrRepoMismatchError):
            st.collect_pr_paths(repo, [421], fetch=_fetch(payloads))

    def test_kills_the_TRUNCATION_guard(self, tmp_path: Path) -> None:
        """🔴 Without it a 411-file PR reports 100 paths and looks perfect. Not a
        hypothetical: the cap is live and measured."""
        mod = _load_mutant(
            tmp_path, "mp_trunc", [("    if len(paths) < changed:", "    if False:")]
        )
        repo = _init_pr_repo(tmp_path)
        payloads = {421: _pr_payload(421, [f"f{i}.py" for i in range(100)], changed=411)}
        short = mod.collect_pr_paths(repo, [421], fetch=_fetch(payloads))
        assert len(short.paths) == 100, "the truncation guard was not the thing removed"
        with pytest.raises(st.PrFileListTruncatedError):
            st.collect_pr_paths(repo, [421], fetch=_fetch(payloads))

    def test_kills_the_FILES_PRESENCE_guard(self, tmp_path: Path) -> None:
        """🔴 Without it an ABSENT file list becomes an EMPTY one — a confident
        "this PR changed nothing" from a response that never described the files.

        ⚠ MEASURED, NOT ASSUMED: with the guard removed the run does not report a
        tidy empty window, it dies on a bare `TypeError`. That is still a kill and
        it is the honest one — the real module fails with a NAMED, printable
        sentence, and `/handoff` is instructed to print that line verbatim."""
        mod = _load_mutant(
            tmp_path, "mp_files", [("    if not isinstance(files, list):", "    if False:")]
        )
        repo = _init_pr_repo(tmp_path)
        payload = _pr_payload(421, [])
        del payload["files"]
        with pytest.raises(Exception) as exc:
            mod.collect_pr_paths(repo, [421], fetch=_fetch({421: payload}))
        assert isinstance(exc.value, TypeError)
        assert "pull request response is malformed" not in str(exc.value)
        with pytest.raises(st.PrResponseMalformedError):
            st.collect_pr_paths(repo, [421], fetch=_fetch({421: payload}))

    def test_kills_the_CLOSED_UNMERGED_guard(self, tmp_path: Path) -> None:
        """Without it, work that was proposed and REJECTED is reported as landed,
        and a journal bullet records a change that exists in no tree."""
        mod = _load_mutant(
            tmp_path, "mp_closed",
            [("    if state not in PR_ACCEPTED_STATES:", "    if False:")],
        )
        repo = _init_pr_repo(tmp_path)
        payloads = {421: _pr_payload(421, ["src/collector/a.py"], state="CLOSED")}
        assert mod.collect_pr_paths(repo, [421], fetch=_fetch(payloads)).paths == (
            "src/collector/a.py",
        )
        with pytest.raises(st.PrNotLandedError):
            st.collect_pr_paths(repo, [421], fetch=_fetch(payloads))

    def test_kills_the_PR_IDENTITY_guard(self, tmp_path: Path) -> None:
        """Without it, the answer to "what did #421 land" can be #999's files."""
        mod = _load_mutant(
            tmp_path,
            "mp_num",
            [("    if isinstance(got, bool) or not isinstance(got, int) or got != number:",
              "    if False:")],
        )
        repo = _init_pr_repo(tmp_path)
        payloads = {421: _pr_payload(999, ["src/collector/a.py"])}
        assert mod.collect_pr_paths(repo, [421], fetch=_fetch(payloads)).paths == (
            "src/collector/a.py",
        )
        with pytest.raises(st.PrResponseMalformedError):
            st.collect_pr_paths(repo, [421], fetch=_fetch(payloads))

    def test_kills_the_EMPTY_PR_LIST_guard(self, tmp_path: Path) -> None:
        """Without it, ZERO pull requests produce a perfectly well-formed report
        over nothing — the confident zero arriving through argument parsing."""
        mod = _load_mutant(tmp_path, "mp_none", [("    if not read:", "    if False:")])
        repo = _init_pr_repo(tmp_path)
        src = mod.collect_pr_paths(repo, [], fetch=_fetch({}))
        assert src.paths == () and src.prs == ()
        with pytest.raises(st.PrNotFoundError):
            st.collect_pr_paths(repo, [], fetch=_fetch({}))

    def test_kills_the_PR_NUMBER_guard(self, tmp_path: Path) -> None:
        """Without it a junk token is handed to the fetcher as if it named a PR."""
        mod = _load_mutant(
            tmp_path,
            "mp_token",
            [("    if not (t.isascii() and t.isdigit()) or int(t) < 1:", "    if False:")],
        )
        repo = _init_pr_repo(tmp_path)
        with pytest.raises(Exception) as exc:
            mod.collect_pr_paths(repo, ["not-a-number"], fetch=_fetch({}))
        assert "pull request not found" not in str(exc.value)
        with pytest.raises(st.PrNotFoundError):
            st.collect_pr_paths(repo, ["not-a-number"], fetch=_fetch({}))

    def test_kills_the_REPO_SLUG_guard(self, tmp_path: Path) -> None:
        """Without it an unparseable remote yields `None` as the repository, and
        every subsequent comparison is against nothing."""
        mod = _load_mutant(
            tmp_path, "mp_slug", [("    if slug is None:", "    if False:")]
        )
        repo = _init_pr_repo(tmp_path, remote="/srv/local-mirror")
        with pytest.raises(Exception) as exc:
            mod.collect_pr_paths(repo, [421], fetch=_fetch({421: _pr_payload(421, ["a.py"])}))
        assert "repo has no usable github remote" not in str(exc.value)
        with pytest.raises(st.RepoRemoteError):
            st.collect_pr_paths(repo, [421], fetch=_fetch({421: _pr_payload(421, ["a.py"])}))

    def test_kills_the_RATE_LIMIT_ORDERING(self, tmp_path: Path) -> None:
        """🔴 The ordering, mutated directly. With the rate-limit branch gone a 403
        rate-limit body — which carries gh's "gh auth login" hint — classifies as
        a PERMANENT auth failure, and the caller stops retrying something that
        would succeed in an hour."""
        mod = _load_mutant(
            tmp_path, "mp_rate", [('    if "rate limit" in low:', "    if False:")]
        )
        stderr = (
            "HTTP 403: API rate limit exceeded for user ID 1.\n"
            "Try authenticating with:  gh auth login"
        )
        assert type(mod._classify_gh_failure(1, stderr, PR_SLUG, 421)) is mod.GhAuthError
        assert type(st._classify_gh_failure(1, stderr, PR_SLUG, 421)) is st.GhRateLimitError

    def test_kills_the_GH_MISSING_guard(self, tmp_path: Path, monkeypatch) -> None:
        """Without it an absent `gh` dies with an unprintable traceback instead of
        the sentence `/handoff` is told to print verbatim — and `gh` is genuinely
        absent in the hermetic tier."""
        mod = _load_mutant(
            tmp_path,
            "mp_ghmiss",
            [("        raise GhMissingError(\n"
              '            f"gh cli not found: `{argv[0]}` is not on PATH, so pull '
              'request #{number} "',
              "        raise ValueError(\n"
              '            f"neutered: {argv[0]} "')],
        )

        def boom(argv, *a, **kw):
            raise FileNotFoundError(2, "No such file or directory", "gh")

        monkeypatch.setattr(mod.subprocess, "run", boom)
        with pytest.raises(Exception) as exc:
            mod._gh_fetch_pr(PR_SLUG, 421)
        assert not isinstance(exc.value, st.GhMissingError)
        assert "gh cli not found" not in str(exc.value)

    def test_kills_the_PR_CAVEAT_branch(self, tmp_path: Path) -> None:
        """🔴 With the branch gone the caveat falls through to the GIT text — "what
        this BRANCH touched, NOT what this SESSION touched" — which is accidentally
        close enough to read as correct while naming the wrong window, no
        repository and no pull request at all."""
        mod = _load_mutant(
            tmp_path, "mp_caveat", [('        if self.kind == "pr":', "        if False:")]
        )
        repo = _init_pr_repo(tmp_path)
        payloads = {421: _pr_payload(421, ["a.py"])}
        wrong = mod.collect_pr_paths(repo, [421], fetch=_fetch(payloads)).caveat
        assert "what the BRANCH LANDED" not in wrong
        assert "#421" not in wrong
        assert PR_SLUG not in wrong
        assert "what the BRANCH LANDED" in st.collect_pr_paths(
            repo, [421], fetch=_fetch(payloads)
        ).caveat

    def test_kills_the_FULL_ARGV_renderer(self, tmp_path: Path) -> None:
        """🔴 The regression the PR source exposed: with `git` hardcoded back into
        the renderer, a `gh` invocation renders as `ran: git gh pr view 421`."""
        mod = _load_mutant(
            tmp_path,
            "mp_argv",
            [('        out.append(f"  ran: {\' \'.join(cmd)}")',
              '        out.append(f"  ran: git {\' \'.join(cmd)}")')],
        )
        store = _make_store(tmp_path / "s")
        src = st.PathSource(
            kind="pr",
            window="pull-requests",
            paths=("src/collector/a.py",),
            commands=(("gh", "pr", "view", "421"),),
            prs=(421,),
            repo_slug=PR_SLUG,
        )
        rep = st.build_report(src, store, SCOPE, today=TODAY, min_paths=1)
        assert "ran: git gh pr view 421" in mod.render_text(rep)
        assert "ran: gh pr view 421" in st.render_text(rep)

    def test_kills_the_COMMANDS_HONESTY_guard(self, tmp_path: Path) -> None:
        """🔴 Without it an injected fetcher still records `gh pr view …` — a
        fabricated provenance line in the one field that exists to report what was
        actually executed."""
        mod = _load_mutant(
            tmp_path,
            "mp_cmdhonest",
            [("        if live:\n            commands.append(_gh_argv(slug, n))",
              "        if True:\n            commands.append(_gh_argv(slug, n))")],
        )
        repo = _init_pr_repo(tmp_path)
        payloads = {421: _pr_payload(421, ["a.py"])}
        fabricated = mod.collect_pr_paths(repo, [421], fetch=_fetch(payloads))
        assert fabricated.commands and fabricated.commands[0][0] == "gh"
        assert st.collect_pr_paths(repo, [421], fetch=_fetch(payloads)).commands == ()

    def test_kills_the_PATHS_FROM_conflict_guard_for_PR(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Without it, `--pr 421 --paths-from -` silently honours ONE of two
        contradictory windows."""
        mod = _load_mutant(
            tmp_path,
            "mp_conflict",
            [('        if (wants_session or wants_pr or wants_commit) and args.paths_from != "git":',
              "        if False:")],
        )
        repo = _init_pr_repo(tmp_path)
        store = _make_store(tmp_path / "s")
        monkeypatch.setattr(mod, "_gh_fetch_pr", _fetch({421: _pr_payload(421, ["a.py"])}))
        argv = ["--repo", str(repo), "--store", str(store), "--pr", "421",
                "--paths-from", "-", "--today", TODAY]
        capsys.readouterr()
        assert mod.main(argv) == 0
        assert st.main(argv) == 2
        assert "cannot be combined" in capsys.readouterr().err


# =============================================================================
# THE COMMIT SOURCE — paths from what THESE COMMITS changed.
#
# 🔴 THE SOURCE WITH THE MOST WAYS TO REACH A SILENT ZERO, AND FOUR OF THEM ARE
# GIT'S OWN EXIT-0 DEFAULTS RATHER THAN ANYTHING THE MODULE DOES. Measured
# against git 2.55.0 on a synthetic repo, 2026-08-12 — each prints an EMPTY file
# list and EXITS 0, so `GitError` (which keys on a non-zero exit) cannot see any
# of them:
#
#     diff-tree <merge>          nothing  (a merge has no single diff)
#     diff-tree <root-commit>    nothing  without `--root`
#     diff-tree <blob-sha>       nothing; the complaint goes to STDERR, rc=0
#     diff-tree <tree-sha>       nothing; same
#
# Every one of those is reproduced below as a FIXTURE SELF-CHECK before the guard
# that closes it is tested, so the guard is never asserted against a hazard
# nobody demonstrated. `claude/RULES.md` → "Validate the INSTRUMENT before you
# read its verdict".
#
# 🔴 EVERY FIXTURE IS SYNTHETIC AND EVERY SHA IS BUILT IN `tmp_path`. No real
# commit sha, repo name, path or host appears here; this repo is PUBLIC. The
# repos whose measurements motivated this source are described by their SHAPE
# ("a repo whose own rules mandate committing from a throwaway worktree") and
# never named.
# =============================================================================


def _commit(repo: Path, *rel: str, message: str = "w", tmp_home: Path | None = None) -> str:
    """Write + stage + commit the named repo-relative paths; return the full sha."""
    for r in rel:
        _write(repo, r)
    for r in rel:
        _run_git(repo, "add", "--", r, home=tmp_home)
    _run_git(repo, "commit", "-m", message, home=tmp_home)
    return _run_git(repo, "rev-parse", "HEAD", home=tmp_home).strip()


def _empty_commit(repo: Path, *, message: str = "empty", tmp_home: Path | None = None) -> str:
    """A well-formed commit that changed NOTHING — the control half of the pair."""
    _run_git(repo, "commit", "--allow-empty", "-m", message, home=tmp_home)
    return _run_git(repo, "rev-parse", "HEAD", home=tmp_home).strip()


def _blob_sha(content: bytes) -> str:
    """git's blob object name for `content`, computed WITHOUT git.

    Used to search for a 4-hex prefix collision offline: sha1 over fixed bytes is
    fixed forever, so the search below is DETERMINISTIC — there is no random
    fixture here and no flake, only a bounded loop over a fixed sequence.
    """
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()


def _colliding_blob_pair(bound: int = 20000) -> tuple[bytes, bytes, str]:
    """Two byte strings whose blob shas share a 4-hex prefix, plus that prefix."""
    seen: dict[str, bytes] = {}
    for i in range(bound):
        c = b"ambiguity-fixture-%d\n" % i
        p = _blob_sha(c)[:4]
        if p in seen:
            return seen[p], c, p
        seen[p] = c
    raise AssertionError(  # pragma: no cover - the search is deterministic
        f"no 4-hex blob-prefix collision within {bound} candidates; the fixture "
        f"cannot construct an ambiguous sha, so every ambiguity test below would "
        f"be vacuous."
    )


class TestCommitPositiveControl:
    """🔴 `claude/RULES.md` → "Positive control — can it ever observe the thing?"

    An empty path set from this source is indistinguishable from a differ wired
    to nothing, and git hands back exactly that empty set, at exit 0, in four
    separate situations (see the banner). So the pair is reported: a commit that
    MUST yield paths against a commit that MUST yield none, on the same code
    path, in the same repo, through the same `diff-tree` invocation.
    """

    UNDER_TEST = ("src/collector/a.py", "src/collector/b.py", "src/collector/c.py")

    def test_THE_PAIR_nonzero_under_test_zero_on_the_control(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, *self.UNDER_TEST)
        # CONTROL: a real, well-formed commit that genuinely changed nothing.
        # Identical machinery; the honest answer is zero, and it must be zero for
        # THAT reason rather than because nothing was read.
        empty = _empty_commit(repo)

        pos = st.collect_commit_paths(repo, [sha])
        neg = st.collect_commit_paths(repo, [empty])

        assert len(pos.paths) == 3, "positive control yielded nothing — wired to nothing"
        assert sorted(pos.paths) == sorted(self.UNDER_TEST)
        assert len(neg.paths) == 0

    def test_the_control_zero_is_ACCOUNTED_for_not_merely_empty(self, tmp_path: Path) -> None:
        """An empty list beside `0 file(s) changed` is a reading; an empty list
        with no count is the silent zero."""
        repo = _init_repo(tmp_path, SCOPE)
        empty = _empty_commit(repo)
        src = st.collect_commit_paths(repo, [empty])
        assert src.paths == ()
        assert any(f"commit {empty[:12]}: 0 file(s) changed" in n for n in src.notes), src.notes
        assert any("0 distinct path(s) across 1 commit(s)" in n for n in src.notes)

    def test_the_per_commit_count_is_emitted_AT_NONZERO_too(self, tmp_path: Path) -> None:
        """The other half of the pair: a counter that only ever prints 0 is
        indistinguishable from one wired to a constant."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, *self.UNDER_TEST)
        src = st.collect_commit_paths(repo, [sha])
        assert any(f"commit {sha[:12]}: 3 file(s) changed" in n for n in src.notes), src.notes
        assert any("3 distinct path(s) across 1 commit(s)" in n for n in src.notes)

    def test_the_pair_survives_all_the_way_to_a_REPORT(self, tmp_path: Path) -> None:
        """🔴 The brief's other zero: a commit touching paths OUTSIDE any subsystem
        must be a REAL zero — three paths examined, none naming an entry — not an
        empty window. The differ and the matcher are two places a zero can come
        from; both ends are pinned so one being wired to nothing cannot hide
        behind the other."""
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        known = _commit(repo, *self.UNDER_TEST, message="known")
        # Same count, same repo — but three paths in three DIFFERENT directories
        # with three different stems, so no ref reaches `min_paths` and nothing
        # is even nominated. That is what makes the renderer print its
        # `real zero` sentence rather than a NO ENTRY block.
        unknown = _commit(
            repo, "docs/alpha.md", "notes/beta.md", "misc/gamma.md", message="unknown"
        )
        pos_rep = st.build_report(st.collect_commit_paths(repo, [known]), store, SCOPE, today=TODAY)
        neg_rep = st.build_report(
            st.collect_commit_paths(repo, [unknown]), store, SCOPE, today=TODAY
        )

        assert pos_rep.status == "resolved"
        assert [m.entry.ref for m in pos_rep.known] == ["collector"]
        assert neg_rep.status == "no-match"
        # …and the negative control is NOT an empty window, which would make the
        # zero uninformative.
        assert len(neg_rep.source.paths) == 3
        rendered = st.render_text(neg_rep)
        assert "This is a real zero, not an empty window." in rendered
        assert "3 paths examined" in rendered

    def test_a_commit_OUTSIDE_every_subsystem_still_NOMINATES_when_it_clusters(
        self, tmp_path: Path
    ) -> None:
        """The other half of the zero above: paths that name no entry but DO
        agree on a directory are a nomination, not a bare zero — otherwise the
        first entry in a repo could never come from this source."""
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        sha = _commit(
            repo, "src/unlisted-widget/a.py", "src/unlisted-widget/b.py", message="new"
        )
        rep = st.build_report(st.collect_commit_paths(repo, [sha]), store, SCOPE, today=TODAY)
        assert rep.status == "no-match"
        assert [n.ref for n in rep.nominations][:1] == ["unlisted-widget"]

    def test_SEVERAL_commits_union_in_first_seen_order(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        first = _commit(repo, "a.py", "b.py", message="first")
        second = _commit(repo, "b.py", "c.py", message="second")
        src = st.collect_commit_paths(repo, [first, second])
        assert src.paths == ("a.py", "b.py", "c.py")
        assert src.commits == (first, second)

    def test_a_repeated_sha_is_read_ONCE_and_SAID(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "a.py")
        src = st.collect_commit_paths(repo, [sha, sha])
        assert src.commits == (sha,)
        assert any("named more than once" in n for n in src.notes)

    def test_the_dedupe_is_on_the_RESOLVED_sha_not_the_TOKEN(self, tmp_path: Path) -> None:
        """🔴 `a1b2c3d` and its own 40-char form are ONE commit. Deduping on the
        token would read it twice and double the per-commit accounting, so the
        `1 commit(s)` line — which a reader uses to check the window — would be
        wrong about a run that is otherwise fine."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "a.py")
        src = st.collect_commit_paths(repo, [sha[:8], sha])
        assert src.commits == (sha,)
        assert any("1 distinct path(s) across 1 commit(s)" in n for n in src.notes), src.notes

    def test_a_SHORT_sha_is_EXPANDED_to_the_full_one(self, tmp_path: Path) -> None:
        """Expanding the token is part of validating it — and `commits` feeds the
        caveat, which must state what the argument RESOLVED to, not echo it."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        src = st.collect_commit_paths(repo, [sha[:7]])
        assert src.commits == (sha,)
        assert len(src.commits[0]) == 40
        assert src.paths == ("src/collector/a.py",)

    def test_a_ROOT_commit_reports_the_files_it_INTRODUCED(self, tmp_path: Path) -> None:
        """🔴 POSITIVE CONTROL ON `--root`, paired with the measurement below that
        shows what git does without it."""
        repo = _init_repo(tmp_path, SCOPE)  # its seed commit IS the root
        root = _run_git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        src = st.collect_commit_paths(repo, [root])
        assert src.paths == ("README.md",)

    def test_the_ROOT_hazard_is_REAL_git_prints_nothing_without_the_flag(
        self, tmp_path: Path
    ) -> None:
        """The fixture self-check behind the flag above: without `--root`, git
        exits 0 and prints an empty file list for a root commit. A guard asserted
        against a hazard nobody demonstrated is a guard nobody can size."""
        repo = _init_repo(tmp_path, SCOPE)
        root = _run_git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        without = _run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", root)
        assert without.strip() == ""
        with_root = _run_git(
            repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", root
        )
        assert with_root.strip() == "README.md"

    def test_a_DELETION_counts_as_touched(self, tmp_path: Path) -> None:
        """`--name-only` is not `--diff-filter=d`: removing a file is touching
        the subsystem it lived in, and dropping it would understate exactly the
        change most worth a journal bullet."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/gone.py")
        _run_git(repo, "rm", "-q", "--", "src/collector/gone.py")
        _run_git(repo, "commit", "-m", "drop")
        sha = _run_git(repo, "rev-parse", "HEAD").strip()
        assert st.collect_commit_paths(repo, [sha]).paths == ("src/collector/gone.py",)

    def test_a_RENAME_reports_BOTH_ends(self, tmp_path: Path) -> None:
        """Rename detection is deliberately OFF (`diff-tree` does not detect them
        by default): both directories were touched, and a rename that collapsed
        to one path would hide the one the code LEFT."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/old.py")
        (repo / "src" / "status-bar").mkdir(parents=True, exist_ok=True)
        _run_git(repo, "mv", "src/collector/old.py", "src/status-bar/new.py")
        _run_git(repo, "commit", "-m", "move")
        sha = _run_git(repo, "rev-parse", "HEAD").strip()
        assert sorted(st.collect_commit_paths(repo, [sha]).paths) == [
            "src/collector/old.py",
            "src/status-bar/new.py",
        ]

    def test_a_path_with_a_space_survives_intact(self, tmp_path: Path) -> None:
        """`-z`, for the same reason the git source uses it: a split path would
        MANUFACTURE two refs out of one."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/two words.py")
        assert st.collect_commit_paths(repo, [sha]).paths == ("src/collector/two words.py",)

    def test_called_with_a_SUBDIRECTORY_the_paths_stay_repo_root_relative(
        self, tmp_path: Path
    ) -> None:
        """🔴 …and the reason is `diff-tree` ITSELF, not this module's frame call.

        Stated because an independent mutation sweep proved it: neutering the
        shared `_toplevel()` changes NOTHING observable here — `diff-tree
        --name-only` is repo-root-relative wherever it runs — while it breaks the
        git source outright, because `ls-files --others` is cwd-relative AND
        cwd-scoped. So the `_toplevel()` call in `collect_commit_paths` is
        normalisation and consistency, NOT the thing that makes this assertion
        true, and claiming otherwise would be a comment the code does not support.
        The behaviour is still pinned here: it is what a caller relies on.
        """
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        src = st.collect_commit_paths(repo / "src", [sha])
        assert src.paths == ("src/collector/a.py",)
        # The measurement behind the paragraph above: git's own output, from the
        # subdirectory, with no help from this module.
        raw = _run_git(
            repo / "src", "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha,
            home=tmp_path,
        )
        assert raw.strip() == "src/collector/a.py"

    def test_excluded_paths_are_dropped_AND_counted(self, tmp_path: Path) -> None:
        """The SHARED exclusion predicate, not a fourth copy of it."""
        repo = _init_repo(tmp_path, SCOPE)
        doc = "claudedocs/handoff-topic.md"
        sha = _commit(repo, doc, "src/collector/a.py")
        src = st.collect_commit_paths(repo, [sha], exclude=[doc])
        assert doc not in src.paths
        assert "src/collector/a.py" in src.paths
        assert any("excluded 1 caller-named path(s)" in n for n in src.notes)

    def test_the_store_is_never_written(self, tmp_path: Path) -> None:
        """The module's central invariant, exercised through the NEW source."""
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        sha = _commit(repo, *self.UNDER_TEST)
        before = _tree_hash(store)
        rep = st.build_report(st.collect_commit_paths(repo, [sha]), store, SCOPE, today=TODAY)
        st.render_text(rep)
        json.dumps(st.report_json(rep))
        assert _tree_hash(store) == before

    def test_the_repo_itself_is_never_written_either(self, tmp_path: Path) -> None:
        """🔴 The other half: this source runs FIVE git commands per sha, and a
        single non-read-only one would mutate a repo whose working tree is a
        deploy target. Hashed, not grepped."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, *self.UNDER_TEST)
        before = _tree_hash(repo)
        st.collect_commit_paths(repo, [sha])
        assert _tree_hash(repo) == before


class TestCommitDecisionsAreTESTEDNotAsserted:
    """The three judgement calls in this source, each with the measurement that
    forced it. They are decisions, so they are pinned behaviourally — a later
    reader changing one has to change a test that says why."""

    def test_a_MERGE_commit_is_REFUSED_by_name(self, tmp_path: Path) -> None:
        """🔴 DECISION: refuse. Alternatives rejected in `CommitIsMergeError` —
        first-parent (the whole other branch's work under one sha), `--cc` (a
        third question, empty for a clean merge), and empty-with-a-note (does not
        compose: `--commit a,b,<merge>` would under-report inside a confident
        union)."""
        repo = _init_repo(tmp_path, SCOPE)
        base = _run_git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, "src/collector/a.py", message="mainline")
        _run_git(repo, "checkout", "-q", "-b", "side", base)
        _commit(repo, "src/status-bar/s.py", message="side")
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
        merge = _run_git(repo, "rev-parse", "HEAD").strip()

        with pytest.raises(st.CommitIsMergeError) as exc:
            st.collect_commit_paths(repo, [merge])
        assert "2 parents" in str(exc.value)
        # The alternative is NAMED, because a refusal with no next step is how a
        # caller ends up merging path sets by hand.
        assert "--pr" in str(exc.value)

    def test_the_MERGE_hazard_is_REAL_git_prints_nothing_for_one(self, tmp_path: Path) -> None:
        """The fixture self-check behind the refusal: `diff-tree` on a merge
        exits 0 with an EMPTY file list, so "do nothing special" is not a
        different design — it is the silent zero, with no note to read."""
        repo = _init_repo(tmp_path, SCOPE)
        base = _run_git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, "src/collector/a.py", message="mainline")
        _run_git(repo, "checkout", "-q", "-b", "side", base)
        _commit(repo, "src/status-bar/s.py", message="side")
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
        merge = _run_git(repo, "rev-parse", "HEAD").strip()
        out = _run_git(
            repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", merge
        )
        assert out.strip() == "", "git no longer hides a merge's diff — re-read the decision"

    def test_an_EMPTY_commit_is_a_READING_not_an_error(self, tmp_path: Path) -> None:
        """🔴 DECISION: a reading. It matches the PR source's `files: []` (a
        well-formed PR that changed nothing), and refusing would break
        composition — one empty sha would kill an otherwise good multi-sha run.
        Here the empty one is named ALONGSIDE a real one and the run still
        succeeds with the real one's paths."""
        repo = _init_repo(tmp_path, SCOPE)
        real = _commit(repo, "src/collector/a.py")
        empty = _empty_commit(repo)
        src = st.collect_commit_paths(repo, [empty, real])
        assert src.paths == ("src/collector/a.py",)
        assert src.commits == (empty, real)
        assert any(f"commit {empty[:12]}: 0 file(s) changed" in n for n in src.notes)

    def test_an_UNREACHABLE_commit_is_ACCEPTED_and_the_note_SAYS_so(
        self, tmp_path: Path
    ) -> None:
        """🔴 DECISION: accept. A commit made in a throwaway worktree that has
        since been removed, or one rebased away, is reachable from no ref — and
        it is exactly the case this flag exists for, since the work happened and
        the object is still in the shared object database. The real consequence
        (it can be GC'd, so the run is not reproducible later) is REPORTED."""
        repo = _init_repo(tmp_path, SCOPE)
        base = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "--detach")
        orphan = _commit(repo, "src/collector/orphan.py", message="detached")
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "reset", "-q", "--keep", base)

        src = st.collect_commit_paths(repo, [orphan])
        assert src.paths == ("src/collector/orphan.py",)
        note = next(n for n in src.notes if n.startswith(f"commit {orphan[:12]}"))
        assert "NOT reachable from any ref" in note
        assert "not reproducible later" in note

    def test_the_reachability_note_prints_the_OTHER_value_too(self, tmp_path: Path) -> None:
        """🔴 Report the pair. A field that only ever says `NOT reachable` is
        indistinguishable from a probe wired to a constant, and nobody would ever
        have seen it say anything else."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        note = next(
            n for n in st.collect_commit_paths(repo, [sha]).notes
            if n.startswith(f"commit {sha[:12]}")
        )
        assert "reachable from a ref" in note
        assert "NOT reachable" not in note

    def test_a_commit_from_a_WORKTREE_of_THIS_repo_IS_visible(self, tmp_path: Path) -> None:
        """🔴 THE MOTIVATING CASE, and the reason the missing-sha error hedges the
        way it does. Worktrees of one repo share ONE object database, so a commit
        made in a `/tmp/wt-*` worktree is present in the primary clone the moment
        it exists — which is why `--commit` reaches work `--session` cannot see
        (its paths have no repo-relative form against the primary clone) and
        `--pr` need never have seen."""
        repo = _init_repo(tmp_path, SCOPE)
        wt = tmp_path / "wt-topic"
        _run_git(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
        made_elsewhere = _commit(wt, "src/collector/from-worktree.py", tmp_home=tmp_path)

        src = st.collect_commit_paths(repo, [made_elsewhere])
        assert src.paths == ("src/collector/from-worktree.py",)

    def test_a_sha_from_ANOTHER_repo_is_NOT_visible(self, tmp_path: Path) -> None:
        """The contrast that makes the sentence above a claim rather than a hope:
        a separate clone does NOT share objects, so its sha is simply absent."""
        repo = _init_repo(tmp_path, SCOPE)
        other = _init_repo(tmp_path, "unrelated-project")
        foreign = _commit(other, "src/collector/a.py", message="theirs")
        # It is a perfectly good commit — over THERE.
        assert st.collect_commit_paths(other, [foreign]).paths == ("src/collector/a.py",)
        with pytest.raises(st.CommitMissingError):
            st.collect_commit_paths(repo, [foreign])


class TestCommitNegativeControls:
    """Each guard fails with ITS OWN sentinel, reached by an input no EARLIER
    guard rejects — otherwise a control passes because a neighbour fired and
    stays green with the guard it claims to test deleted.
    """

    #: 🔴 THE SAME MAP THE OTHER TWO FAMILIES USE, deliberately not a fourth copy.
    #: `_only` is a measurement only if EVERY other guard's phrase is asserted
    #: absent, so the map has to span all three families at once; a per-family
    #: copy would make each family's controls blind to the other two.
    SENTINELS = TestPrNegativeControls.SENTINELS

    def _only(self, exc: Exception, key: str) -> None:
        text = str(exc)
        assert self.SENTINELS[key] in text, f"expected the {key} sentinel, got: {text}"
        for other, phrase in self.SENTINELS.items():
            if other != key:
                assert phrase not in text, f"the {other} sentinel also fired: {text}"

    def test_the_sentinel_map_is_the_SHARED_one(self) -> None:
        """The premise of the note above, as an assertion rather than a comment:
        two maps that drift apart would let a commit sentinel collide with a PR
        one and nothing would notice."""
        assert self.SENTINELS is TestPrNegativeControls.SENTINELS
        for key in ("c-malformed", "c-missing", "c-ambiguous", "c-type", "c-merge"):
            assert key in self.SENTINELS

    # --- guard 1: the token is SHAPED like a sha --------------------------------

    @pytest.mark.parametrize(
        "token", ["HEAD", "main", "HEAD~3", "HEAD^", "@{u}", "origin/main", "v1.0"],
        ids=["head", "branch", "tilde", "caret", "upstream", "remote-branch", "tag-name"],
    )
    def test_a_revision_EXPRESSION_is_MALFORMED_not_resolved(
        self, tmp_path: Path, token
    ) -> None:
        """🔴 Refused rather than resolved: an expression names a DIFFERENT commit
        on a re-run, in another worktree, or on another host, so the window would
        move under a source whose whole claim is that it is deterministic."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/a.py")
        with pytest.raises(st.CommitRefMalformedError) as exc:
            st.collect_commit_paths(repo, [token])
        self._only(exc.value, "c-malformed")

    @pytest.mark.parametrize(
        "token", ["", "   ", "zzzz", "deadbeefg", "0x1234", "abc def", "-r", "../etc/passwd",
                  "0" * 41],
        ids=["empty", "spaces", "nonhex", "trailing-nonhex", "prefixed", "spaced",
             "flaglike", "path", "too-long"],
    )
    def test_a_NON_HEX_or_OVERLONG_token_is_MALFORMED(self, tmp_path: Path, token) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        with pytest.raises(st.CommitRefMalformedError) as exc:
            st.collect_commit_paths(repo, [token])
        self._only(exc.value, "c-malformed")

    def test_a_TOO_SHORT_prefix_is_MALFORMED(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        with pytest.raises(st.CommitRefMalformedError) as exc:
            st.collect_commit_paths(repo, [sha[:3]])
        self._only(exc.value, "c-malformed")

    def test_the_LENGTH_bound_is_not_vacuous_git_WOULD_have_expanded_it(
        self, tmp_path: Path
    ) -> None:
        """🔴 The measurement behind the bound. `rev-parse --disambiguate` expands
        a 3-character prefix in a small repo — so without the bound a typo
        RESOLVES to a real commit and is reported as a deliberate argument. The
        guard is not restating git's own refusal; git does not refuse."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        expanded = _run_git(repo, "rev-parse", f"--disambiguate={sha[:3]}").split()
        assert expanded == [sha], expanded

    def test_the_boundary_is_a_boundary_not_a_slope(self, tmp_path: Path) -> None:
        """4 is accepted and 3 is not, at the same repo, on the same sha."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        assert st.collect_commit_paths(repo, [sha[:4]]).commits == (sha,)
        with pytest.raises(st.CommitRefMalformedError):
            st.collect_commit_paths(repo, [sha[:3]])

    def test_UPPERCASE_hex_is_accepted_and_normalized(self, tmp_path: Path) -> None:
        """A sha pasted out of a UI is not a different commit."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        assert st.collect_commit_paths(repo, [sha.upper()]).commits == (sha,)

    # --- guard 2: the prefix names ONE object -----------------------------------

    def test_the_AMBIGUITY_fixture_really_IS_ambiguous(self, tmp_path: Path) -> None:
        """🔴 Positive control on the fixture. An ambiguity test built on a prefix
        that turns out to be unique would pass for the wrong reason forever."""
        repo = _init_repo(tmp_path, SCOPE)
        a, b, prefix = _colliding_blob_pair()
        assert _blob_sha(a)[:4] == _blob_sha(b)[:4] == prefix
        for content in (a, b):
            _write(repo, "scratch.txt", content.decode())
            _run_git(repo, "hash-object", "-w", "--", "scratch.txt")
        assert len(_run_git(repo, "rev-parse", f"--disambiguate={prefix}").split()) >= 2

    def test_an_AMBIGUOUS_short_sha_is_REFUSED_BY_NAME(self, tmp_path: Path) -> None:
        """🔴 DECISION: refuse, and count candidates of EVERY object type. Letting
        `<prefix>^{commit}` pick would inherit git's type-peeling tiebreak as a
        silent dependency, and the caller always has the full sha in hand."""
        repo = _init_repo(tmp_path, SCOPE)
        a, b, prefix = _colliding_blob_pair()
        for content in (a, b):
            _write(repo, "scratch.txt", content.decode())
            _run_git(repo, "hash-object", "-w", "--", "scratch.txt")
        with pytest.raises(st.CommitAmbiguousError) as exc:
            st.collect_commit_paths(repo, [prefix])
        self._only(exc.value, "c-ambiguous")
        assert "names 2 objects" in str(exc.value) or "names 3 objects" in str(exc.value)

    # --- guard 3: something answers to the name ---------------------------------

    def test_a_NONEXISTENT_sha_is_NOT_FOUND(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        with pytest.raises(st.CommitMissingError) as exc:
            st.collect_commit_paths(repo, ["dead" * 10])
        self._only(exc.value, "c-missing")

    def test_a_sha_from_ANOTHER_repo_is_NOT_FOUND_and_the_message_names_BOTH(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE DELIBERATE NON-DISTINCTION, and it is the honest one.
        `claude/RULES.md` → "an EMPTY RESULT cannot distinguish two mechanisms":
        a sha that was never created and one created in another clone are the
        same observable — the object is absent — and no local signal separates
        them. A `CommitForeignRepoError` would be a diagnosis with no code path
        behind it, so the message names BOTH mechanisms instead."""
        repo = _init_repo(tmp_path, SCOPE)
        other = _init_repo(tmp_path, "unrelated-project")
        foreign = _commit(other, "src/collector/a.py", message="theirs")
        with pytest.raises(st.CommitMissingError) as exc:
            st.collect_commit_paths(repo, [foreign])
        self._only(exc.value, "c-missing")
        assert "ANOTHER repository" in str(exc.value)
        assert "does not exist" in str(exc.value)

    def test_NO_sha_at_all_is_NOT_FOUND_not_an_empty_report(self, tmp_path: Path) -> None:
        """Reachable through `--commit ,`. Falling through would return a
        well-formed report over ZERO commits — the confident zero arriving by
        argument parsing."""
        repo = _init_repo(tmp_path, SCOPE)
        with pytest.raises(st.CommitMissingError) as exc:
            st.collect_commit_paths(repo, [])
        self._only(exc.value, "c-missing")
        assert "no commit sha was given" in str(exc.value)

    # --- guard 4: it is a COMMIT -------------------------------------------------

    @pytest.mark.parametrize("kind", ["blob", "tree", "tag"])
    def test_a_NON_COMMIT_object_is_WRONG_TYPE(self, tmp_path: Path, kind) -> None:
        """Reached by a sha that EXISTS and is unambiguous — no earlier guard
        rejects it, so the failure is this guard's own."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/a.py")
        if kind == "blob":
            sha = _run_git(repo, "rev-parse", "HEAD:src/collector/a.py").strip()
        elif kind == "tree":
            sha = _run_git(repo, "rev-parse", "HEAD^{tree}").strip()
        else:
            _run_git(repo, "tag", "-a", "annotated", "-m", "t")
            sha = _run_git(repo, "rev-parse", "annotated").strip()
        assert _run_git(repo, "cat-file", "-t", sha).strip() == kind
        with pytest.raises(st.CommitWrongTypeError) as exc:
            st.collect_commit_paths(repo, [sha])
        self._only(exc.value, "c-type")
        assert f"names a {kind}" in str(exc.value)

    def test_the_WRONG_TYPE_hazard_is_REAL_git_exits_ZERO_on_a_blob(
        self, tmp_path: Path
    ) -> None:
        """🔴 The measurement behind the guard, and the reason `GitError` cannot
        stand in for it: `diff-tree` on a blob EXITS 0 with an empty file list
        and complains only on stderr. Without the guard the run succeeds and
        reports a commit that changed nothing."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/a.py")
        blob = _run_git(repo, "rev-parse", "HEAD:src/collector/a.py").strip()
        proc = subprocess.run(
            ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only",
             "-r", "--root", blob],
            capture_output=True, text=True, env=_git_env(tmp_path),
        )
        assert proc.returncode == 0, "git now FAILS on a blob — re-read the guard"
        assert proc.stdout.strip() == ""
        assert "not a commit" in proc.stderr

    # --- guard 5: it is not a merge ---------------------------------------------

    def test_a_MERGE_is_refused_with_ITS_OWN_sentinel(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        base = _run_git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, "src/collector/a.py", message="mainline")
        _run_git(repo, "checkout", "-q", "-b", "side", base)
        _commit(repo, "src/status-bar/s.py", message="side")
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
        merge = _run_git(repo, "rev-parse", "HEAD").strip()
        with pytest.raises(st.CommitIsMergeError) as exc:
            st.collect_commit_paths(repo, [merge])
        self._only(exc.value, "c-merge")

    def test_the_type_guard_PRECEDES_the_merge_guard(self, tmp_path: Path) -> None:
        """🔴 The ordering constraint, as a measurement rather than a comment:
        `rev-list --parents` on a blob prints NOTHING at exit 0, which this code
        would read as "no parents" — a root commit. A merge check placed first
        would therefore wave every blob through, and the wrong-type control above
        would go green on a neighbour's silence."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/a.py")
        blob = _run_git(repo, "rev-parse", "HEAD:src/collector/a.py").strip()
        assert _run_git(repo, "rev-list", "--parents", "-n", "1", blob).strip() == ""

    # --- the family-wide contracts ------------------------------------------------

    def test_a_git_failure_PROPAGATES_and_never_becomes_an_empty_set(
        self, tmp_path: Path
    ) -> None:
        """A broken environment is not a reading. `--repo` pointing at a
        non-repository must raise, not report zero paths."""
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        with pytest.raises(st.GitError) as exc:
            st.collect_commit_paths(not_a_repo, ["dead" * 10])
        self._only(exc.value, "git")

    def test_EVERY_commit_failure_is_a_TouchError_so_the_CLI_exits_nonzero(self) -> None:
        """`/handoff` step 4 treats any non-zero exit as "write nothing". An error
        outside the hierarchy would escape `main`'s handler as a traceback."""
        for cls in (
            st.CommitRefMalformedError, st.CommitMissingError, st.CommitAmbiguousError,
            st.CommitWrongTypeError, st.CommitIsMergeError,
        ):
            assert issubclass(cls, st.TouchError)

    def test_NO_failure_can_return_an_EMPTY_path_set(self, tmp_path: Path) -> None:
        """🔴 The family contract, swept rather than argued: every rejected input
        RAISES. A source that returned `()` for a bad sha would be
        indistinguishable from an empty commit."""
        repo = _init_repo(tmp_path, SCOPE)
        _commit(repo, "src/collector/a.py")
        blob = _run_git(repo, "rev-parse", "HEAD:src/collector/a.py").strip()
        for token in ("HEAD", "zzzz", "dead" * 10, blob):
            with pytest.raises(st.TouchError):
                st.collect_commit_paths(repo, [token])


class TestCommitCaveatAnswersTheRightQuestion:
    """🔴 The caveat is the deliverable. This window is a THIRD thing — not a
    session (it does not know who authored the commit) and not a branch (a
    SIBLING commit is outside it) — so neither existing sentence could be reused
    without being wrong in a new direction."""

    def _caveat(self, tmp_path: Path) -> str:
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        return st.collect_commit_paths(repo, [sha]).caveat

    def test_it_says_COMMITS_and_denies_BOTH_other_attributions(self, tmp_path: Path) -> None:
        c = self._caveat(tmp_path)
        assert "THESE COMMITS CHANGED" in c
        assert "neither a SESSION nor a BRANCH" in c
        assert "never to a session and never to a branch" in c

    def test_it_names_what_the_window_EXCLUDES(self, tmp_path: Path) -> None:
        c = self._caveat(tmp_path)
        assert "EXCLUDES uncommitted work" in c
        assert "never became one of these commits" in c
        assert "SIBLING commit on the same branch is NOT in this window" in c

    def test_it_names_what_the_window_can_WRONGLY_INCLUDE(self, tmp_path: Path) -> None:
        c = self._caveat(tmp_path)
        assert "WRONGLY INCLUDE" in c
        assert "formatting sweep" in c
        assert "cannot see intent" in c

    def test_it_names_the_COMMITS_it_read(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        c = st.collect_commit_paths(repo, [sha]).caveat
        assert sha[:12] in c

    def test_the_SIBLING_claim_is_TRUE_not_merely_stated(self, tmp_path: Path) -> None:
        """🔴 `claude/RULES.md` → "Write the claim AFTER the code, from what the
        function does". The caveat asserts a sibling commit is outside the
        window; this measures it, so the sentence cannot drift into a promise the
        code stopped keeping."""
        repo = _init_repo(tmp_path, SCOPE)
        mine = _commit(repo, "src/collector/mine.py", message="mine")
        _commit(repo, "src/status-bar/theirs.py", message="theirs")
        src = st.collect_commit_paths(repo, [mine])
        assert src.paths == ("src/collector/mine.py",)

    def test_the_OTHER_sources_caveats_are_UNCHANGED(self, tmp_path: Path) -> None:
        """Adding a fourth source must not soften the claim another one makes."""
        repo = _init_repo(tmp_path, SCOPE)
        _run_git(repo, "checkout", "-b", "topic")
        # Committed on a TOPIC branch, so git's window is the `branch` one — the
        # caveat this asserts only exists on that branch of `PathSource.caveat`.
        _commit(repo, "src/collector/a.py")
        assert "NOT what this SESSION touched" in st.collect_git_paths(repo).caveat
        assert "provenance is the caller's" in st.caller_supplied(["a.py"]).caveat

    @pytest.mark.parametrize(
        "paths,expect",
        [
            (["src/collector/a.py", "src/collector/b.py"], "resolved"),
            (["docs/a.md", "docs/b.md"], "no-match"),
            ([], "looked-at-nothing"),
        ],
        ids=["resolved", "no-match", "looked-at-nothing"],
    )
    def test_the_caveat_is_on_EVERY_output_path_of_BOTH_renderers(
        self, tmp_path: Path, paths, expect
    ) -> None:
        """🔴 The property the #415 audit established and #421/#424 kept: the
        caveat is on every output path of both renderers — INCLUDING the
        early-return `looked-at-nothing` branch. A fourth source must not be the
        one that breaks it. The empty case is a REAL empty commit, so this is
        also the only place `looked-at-nothing` is reached through this source."""
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        sha = _commit(repo, *paths) if paths else _empty_commit(repo)
        rep = st.build_report(st.collect_commit_paths(repo, [sha]), store, SCOPE, today=TODAY)
        assert rep.status == expect
        caveat = rep.source.caveat
        assert caveat in st.render_text(rep)
        payload = st.report_json(rep)
        assert payload["source"]["caveat"] == caveat
        assert payload["source"]["commits"] == [sha]
        assert payload["source"]["kind"] == "commit"
        assert payload["source"]["window"] == "commits"

    def test_the_commit_source_records_its_ARGV(self, tmp_path: Path) -> None:
        """`commands` is the argv that RAN, so a reader can re-run it. Every
        source's is checked; this one's program is `git`, and the subcommand has
        to be the one that produced the paths."""
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py")
        src = st.collect_commit_paths(repo, [sha])
        assert src.commands and all(c[0] == "git" for c in src.commands), src.commands
        assert src.commands[0][1] == "diff-tree"
        assert sha in src.commands[0]
        store = _make_store(tmp_path / "s")
        assert "ran: git diff-tree" in st.render_text(
            st.build_report(src, store, SCOPE, today=TODAY)
        )


class TestCommitCli:
    """The CLI is the only surface `/handoff` touches."""

    def _run(self, args, capsys):
        rc = st.main(args)
        return rc, capsys.readouterr()

    def _fixture(self, tmp_path: Path):
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        return repo, store

    def test_a_commit_resolves_end_to_end(self, tmp_path: Path, capsys) -> None:
        repo, store = self._fixture(tmp_path)
        sha = _commit(repo, "src/collector/a.py", "src/collector/b.py")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", sha,
             "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        payload = json.loads(cap.out)
        assert payload["source"]["kind"] == "commit"
        assert payload["source"]["commits"] == [sha]
        assert payload["status"] == "resolved"
        assert [k["ref"] for k in payload["known"]] == ["collector"]

    @pytest.mark.parametrize("spelling", ["comma", "repeated"], ids=["comma", "repeated"])
    def test_both_spellings_of_several_commits(
        self, tmp_path: Path, capsys, spelling
    ) -> None:
        repo, store = self._fixture(tmp_path)
        first = _commit(repo, "src/collector/a.py", message="first")
        second = _commit(repo, "src/status-bar/s.py", message="second")
        flags = (
            ["--commit", f"{first},{second}"]
            if spelling == "comma"
            else ["--commit", first, "--commit", second]
        )
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), *flags, "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        assert json.loads(cap.out)["source"]["commits"] == [first, second]

    def test_a_trailing_comma_is_the_typo_it_looks_like(self, tmp_path: Path, capsys) -> None:
        """The SHARED splitter's decision: an empty token is dropped rather than
        becoming a second, unusable sha."""
        repo, store = self._fixture(tmp_path)
        sha = _commit(repo, "src/collector/a.py")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", f"{sha},",
             "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        assert json.loads(cap.out)["source"]["commits"] == [sha]

    def test_an_EMPTY_commit_argument_exits_3_naming_the_sentinel(
        self, tmp_path: Path, capsys
    ) -> None:
        """…and reaches the "nothing was given" guard, not the "unusable token"
        one — which is the other half of the splitter's decision."""
        repo, store = self._fixture(tmp_path)
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", ",", "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert "commit not found" in cap.err
        assert "no commit sha was given" in cap.err
        assert cap.out == ""

    @pytest.mark.parametrize(
        "token,phrase",
        [
            ("HEAD", "commit sha is malformed"),
            ("zzzz", "commit sha is malformed"),
            ("dead" * 10, "commit not found"),
        ],
        ids=["expression", "nonhex", "missing"],
    )
    def test_every_failure_exits_3_with_a_PRINTABLE_line_and_NO_report(
        self, tmp_path: Path, capsys, token, phrase
    ) -> None:
        """`/handoff` prints the stderr line verbatim and writes nothing, so it
        must be one line a human can read — and stdout must carry no report a
        reader could mistake for a result."""
        repo, store = self._fixture(tmp_path)
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", token, "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert phrase in cap.err
        assert cap.out == ""

    def test_a_failure_NEVER_falls_back_to_git(self, tmp_path: Path, capsys) -> None:
        """🔴 The no-fallback contract, at the CLI. The git window here is
        NON-EMPTY, so a fallback would produce a plausible, well-formed report of
        a question nobody asked."""
        repo, store = self._fixture(tmp_path)
        _write(repo, "src/collector/uncommitted.py")
        assert st.collect_git_paths(repo).paths, "the fallback window must be non-empty"
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", "dead" * 10,
             "--today", TODAY],
            capsys,
        )
        assert rc == 3
        assert cap.out == ""
        assert "uncommitted" not in cap.err

    def test_the_text_renderer_prints_the_caveat_and_the_notes(
        self, tmp_path: Path, capsys
    ) -> None:
        repo, store = self._fixture(tmp_path)
        sha = _commit(repo, "src/collector/a.py", "src/collector/b.py")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", sha, "--today", TODAY],
            capsys,
        )
        assert rc == 0
        assert "THESE COMMITS CHANGED" in cap.out
        assert f"commit {sha[:12]}: 2 file(s) changed" in cap.out
        assert "ran: git diff-tree" in cap.out
        assert "window=commits" in cap.out

    def test_git_remains_the_default_when_no_commit_is_given(
        self, tmp_path: Path, capsys
    ) -> None:
        """Adding a flag must not change what happens without it."""
        repo, store = self._fixture(tmp_path)
        _write(repo, "src/collector/a.py")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json"], capsys
        )
        assert rc == 0
        assert json.loads(cap.out)["source"]["kind"] == "git"

    def test_exclude_applies_to_the_COMMIT_window_too(self, tmp_path: Path, capsys) -> None:
        repo, store = self._fixture(tmp_path)
        doc = "claudedocs/handoff-topic.md"
        sha = _commit(repo, doc, "src/collector/a.py")
        rc, cap = self._run(
            ["--repo", str(repo), "--store", str(store), "--commit", sha,
             "--exclude", doc, "--today", TODAY, "--json"],
            capsys,
        )
        assert rc == 0
        assert json.loads(cap.out)["source"]["paths"] == ["src/collector/a.py"]

    def test_the_commit_source_leaves_the_store_byte_identical(
        self, tmp_path: Path, capsys
    ) -> None:
        repo, store = self._fixture(tmp_path)
        sha = _commit(repo, "src/collector/a.py", "src/collector/b.py")
        before = _tree_hash(store)
        for extra in ([], ["--json"]):
            assert st.main(
                ["--repo", str(repo), "--store", str(store), "--commit", sha,
                 "--today", TODAY, *extra]
            ) == 0
        capsys.readouterr()
        assert _tree_hash(store) == before


class TestTheCOMMITSourceDoesNotComposeEither:
    """🔴 ONE QUESTION PER RUN, one flag further along. A three-way union would
    need three caveat sentences in one line — asserting commit attribution for
    some members, branch attribution for others and session attribution for the
    rest, with no way for a reader to tell which is which."""

    @pytest.mark.parametrize(
        "extra",
        [["--session", SESSION_ID], ["--transcript", "/nonexistent.jsonl"], ["--pr", "421"]],
        ids=["session", "transcript", "pr"],
    )
    def test_commit_with_ANOTHER_source_is_REFUSED_by_argparse(
        self, tmp_path: Path, extra
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            st.main(["--repo", str(tmp_path), "--commit", "dead" * 10, *extra])
        assert exc.value.code == 2

    def test_commit_with_paths_from_is_REFUSED_with_a_printable_line(
        self, tmp_path: Path, capsys
    ) -> None:
        repo = _init_repo(tmp_path, SCOPE)
        assert st.main(["--repo", str(repo), "--commit", "dead" * 10, "--paths-from", "-"]) == 2
        err = capsys.readouterr().err
        assert "cannot be combined with --paths-from" in err
        assert "--commit" in err

    def test_the_EARLIER_refusals_STILL_name_themselves(
        self, tmp_path: Path, capsys
    ) -> None:
        """The message was widened to cover `--commit`; it must not have stopped
        naming the flags it already covered."""
        repo = _init_repo(tmp_path, SCOPE)
        assert st.main(["--repo", str(repo), "--session", SESSION_ID, "--paths-from", "-"]) == 2
        assert "--session/--transcript/--pr" in capsys.readouterr().err

    def test_running_it_TWICE_is_the_supported_composition(
        self, tmp_path: Path, capsys
    ) -> None:
        """🔴 The decision as a measurement: two runs, two path sets, EACH WITH ITS
        OWN CAVEAT. The two windows deliberately DISAGREE here — the commit
        carries an implementation file, the git window carries an uncommitted doc
        that is in no commit — which is precisely why a union could not describe
        either honestly."""
        repo = _init_repo(tmp_path, SCOPE)
        store = _make_store(tmp_path / "s")
        _run_git(repo, "checkout", "-q", "-b", "topic")
        sha = _commit(repo, "src/collector/a.py")
        _write(repo, "claudedocs/handoff-x.md")  # written, never committed
        base = ["--repo", str(repo), "--store", str(store), "--today", TODAY, "--json"]

        assert st.main(base + ["--commit", sha]) == 0
        by_commit = json.loads(capsys.readouterr().out)
        assert st.main(base) == 0
        by_git = json.loads(capsys.readouterr().out)

        assert by_commit["source"]["paths"] == ["src/collector/a.py"]
        # The windows genuinely DISAGREE: git carries an uncommitted doc that is
        # in no commit, which is exactly the fact a union would erase.
        assert "claudedocs/handoff-x.md" in by_git["source"]["paths"]
        assert "claudedocs/handoff-x.md" not in by_commit["source"]["paths"]
        assert by_commit["source"]["caveat"] != by_git["source"]["caveat"]
        assert "THESE COMMITS CHANGED" in by_commit["source"]["caveat"]
        assert "NOT what this SESSION touched" in by_git["source"]["caveat"]


class TestCommitMutationKillMatrix:
    """Each commit guard deleted on purpose, each dying to ITS OWN test.

    🔴 The confound this class is built around: these guards run in sequence over
    one token, and a mutant with one removed usually trips the NEXT one. A kill
    that merely asserted "something raised" would be green with the guard it
    names deleted — so every test below asserts the specific sentinel is GONE,
    and, where git's exit-0 defaults make it possible, that the run now SUCCEEDS
    WITH A WRONG ANSWER, which is the actual hazard.
    """

    def _repo_with(self, tmp_path: Path) -> tuple[Path, str]:
        repo = _init_repo(tmp_path, SCOPE)
        sha = _commit(repo, "src/collector/a.py", "src/collector/b.py")
        return repo, sha

    def test_the_commit_mutant_harness_WORKS(self, tmp_path: Path) -> None:
        """🔴 THE NO-OP CONTROL. An unmutated copy must reach the commit source
        successfully, or every kill below is a claim about a module that never
        loaded. Without this a sweep wired to nothing is indistinguishable from a
        sweep that killed everything."""
        mod = _load_mutant(
            tmp_path, "mc_noop", [('WRITER_ID = "handoff"', 'WRITER_ID = "handoff"  # noop')]
        )
        repo, sha = self._repo_with(tmp_path)
        src = mod.collect_commit_paths(repo, [sha])
        assert src.paths == ("src/collector/a.py", "src/collector/b.py")
        assert src.commits == (sha,)

    def test_kills_the_LENGTH_guard(self, tmp_path: Path) -> None:
        """🔴 A CONFIDENT WRONG ANSWER, not merely a wrong diagnosis. Without the
        length bound a 3-character typo RESOLVES to a real commit — git's own
        `--disambiguate` expands it — and its diff is reported as though the
        caller had named it."""
        mod = _load_mutant(
            tmp_path, "mc_len",
            [("    if not COMMIT_SHA_MIN_CHARS <= len(t) <= COMMIT_SHA_MAX_CHARS:",
              "    if False:")],
        )
        repo, sha = self._repo_with(tmp_path)
        wrong = mod.collect_commit_paths(repo, [sha[:3]])
        assert wrong.commits == (sha,), "wrong thing removed"
        assert wrong.paths == ("src/collector/a.py", "src/collector/b.py")
        with pytest.raises(st.CommitRefMalformedError):
            st.collect_commit_paths(repo, [sha[:3]])

    def test_kills_the_HEX_SHAPE_guard(self, tmp_path: Path) -> None:
        """🔴 A SEPARATE guard from the length bound, killed separately — one
        combined condition would die to one mutation and neither half would be
        measured. Without it `HEAD` stops refusing by name and misdiagnoses as
        `commit not found`, a line that sends the caller looking for a commit
        that is not missing."""
        mod = _load_mutant(
            tmp_path, "mc_hex",
            [("    if not t or not all(c in _HEX_DIGITS for c in t):", "    if False:")],
        )
        repo, _sha = self._repo_with(tmp_path)
        with pytest.raises(mod.CommitMissingError) as exc:
            mod.collect_commit_paths(repo, ["HEAD"])
        assert "commit sha is malformed" not in str(exc.value)
        with pytest.raises(st.CommitRefMalformedError):
            st.collect_commit_paths(repo, ["HEAD"])

    def test_kills_the_AMBIGUITY_guard(self, tmp_path: Path) -> None:
        """Without it an ambiguous prefix is misdiagnosed as `commit not found` —
        the caller is told the object is absent when two of them are right
        there."""
        mod = _load_mutant(
            tmp_path, "mc_ambig", [("    if len(candidates) > 1:", "    if False:")]
        )
        repo = _init_repo(tmp_path, SCOPE)
        a, b, prefix = _colliding_blob_pair()
        for content in (a, b):
            _write(repo, "scratch.txt", content.decode())
            _run_git(repo, "hash-object", "-w", "--", "scratch.txt")
        with pytest.raises(mod.CommitWrongTypeError) as exc:
            mod.collect_commit_paths(repo, [prefix])
        assert "commit sha is ambiguous" not in str(exc.value)
        with pytest.raises(st.CommitAmbiguousError):
            st.collect_commit_paths(repo, [prefix])

    def test_kills_the_MISSING_guard(self, tmp_path: Path) -> None:
        """Without it a nonexistent sha is misdiagnosed as a non-commit OBJECT —
        a diagnosis about a thing that is not there at all."""
        mod = _load_mutant(tmp_path, "mc_missing", [("    if not full:", "    if False:")])
        repo, _sha = self._repo_with(tmp_path)
        with pytest.raises(mod.CommitWrongTypeError) as exc:
            mod.collect_commit_paths(repo, ["dead" * 10])
        assert "commit not found" not in str(exc.value)
        with pytest.raises(st.CommitMissingError):
            st.collect_commit_paths(repo, ["dead" * 10])

    def test_kills_the_WRONG_TYPE_guard(self, tmp_path: Path) -> None:
        """🔴 THE SILENT-ZERO KILL, asserted AT THE SEAM rather than end-to-end,
        because end-to-end would be green for the wrong reason.

        With the guard gone a blob sha resolves, and `diff-tree` on it exits 0
        with an EMPTY file list — the silent zero, measured directly below. The
        RUN then happens to die later, on the unrelated `for-each-ref
        --contains` reachability probe, which git rejects for a non-commit. That
        is an ACCIDENT of a neighbouring command, not this guard: `claude/RULES.md`
        → "a mutation test still passes when a DIFFERENT guard's error kills your
        test". So the kill pins the seam (the guard is gone, and what it was
        protecting is silent) and only then records that the end-to-end
        misdiagnosis no longer names the type.
        """
        mod = _load_mutant(
            tmp_path, "mc_type", [('    if otype != "commit":', "    if False:")]
        )
        repo, _sha = self._repo_with(tmp_path)
        blob = _run_git(repo, "rev-parse", "HEAD:src/collector/a.py").strip()

        # 1. the guard is gone: the blob now resolves as if it were a commit.
        assert mod._resolve_commit(repo, blob) == blob
        # 2. …and what it was protecting is a SILENT zero, not an error.
        assert mod._nul_list(
            mod._git(
                repo,
                ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", blob],
            )
        ) == []
        # 3. end-to-end the mutant dies elsewhere, WITHOUT naming the type.
        with pytest.raises(mod.GitError) as exc:
            mod.collect_commit_paths(repo, [blob])
        assert "object is not a commit" not in str(exc.value)
        # 4. the honest module names it before anything is diffed at all.
        with pytest.raises(st.CommitWrongTypeError):
            st.collect_commit_paths(repo, [blob])

    def test_kills_the_MERGE_guard(self, tmp_path: Path) -> None:
        """🔴 THE OTHER SILENT-ZERO KILL, and the one that justifies refusing
        rather than 'handling' a merge: without the guard the run SUCCEEDS and
        reports a merge as having changed no files."""
        mod = _load_mutant(
            tmp_path, "mc_merge", [("    if len(parents) > 1:", "    if False:")]
        )
        repo = _init_repo(tmp_path, SCOPE)
        base = _run_git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, "src/collector/a.py", message="mainline")
        _run_git(repo, "checkout", "-q", "-b", "side", base)
        _commit(repo, "src/status-bar/s.py", message="side")
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
        merge = _run_git(repo, "rev-parse", "HEAD").strip()

        silent = mod.collect_commit_paths(repo, [merge])
        assert silent.paths == (), "the mutant did not reach the silent zero"
        with pytest.raises(st.CommitIsMergeError):
            st.collect_commit_paths(repo, [merge])

    def test_kills_the_ROOT_flag(self, tmp_path: Path) -> None:
        """Without `--root`, a root commit reports having changed nothing — git's
        default, and the third silent zero in this source."""
        mod = _load_mutant(
            tmp_path, "mc_root",
            [('        args = ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", full]',
              '        args = ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", full]')],
        )
        repo = _init_repo(tmp_path, SCOPE)
        root = _run_git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
        assert mod.collect_commit_paths(repo, [root]).paths == ()
        assert st.collect_commit_paths(repo, [root]).paths == ("README.md",)

    def test_kills_the_FULL_SHA_expansion(self, tmp_path: Path) -> None:
        """Without it `commits` — and therefore the CAVEAT — echoes the caller's
        abbreviation instead of stating what it resolved to, and the dedupe stops
        recognising one commit named two ways."""
        mod = _load_mutant(tmp_path, "mc_expand", [("    full = candidates[0]", "    full = t")])
        repo, sha = self._repo_with(tmp_path)
        echoed = mod.collect_commit_paths(repo, [sha[:8]])
        assert echoed.commits == (sha[:8],)
        assert st.collect_commit_paths(repo, [sha[:8]]).commits == (sha,)
        # …and the dedupe silently reads one commit twice.
        assert len(mod.collect_commit_paths(repo, [sha[:8], sha]).commits) == 2
        assert len(st.collect_commit_paths(repo, [sha[:8], sha]).commits) == 1

    def test_kills_the_PER_COMMIT_accounting(self, tmp_path: Path) -> None:
        """Without the note an empty commit's zero has nothing beside it, and is
        indistinguishable from a differ wired to nothing."""
        mod = _load_mutant(
            tmp_path, "mc_note",
            [('        notes.append(f"commit {full[:12]}: {len(paths)} file(s) changed; {where}")',
              "        pass")],
        )
        repo = _init_repo(tmp_path, SCOPE)
        empty = _empty_commit(repo)
        assert not any("file(s) changed" in n for n in mod.collect_commit_paths(repo, [empty]).notes)
        assert any(
            "0 file(s) changed" in n for n in st.collect_commit_paths(repo, [empty]).notes
        )

    def test_kills_the_EMPTY_COMMIT_LIST_guard(self, tmp_path: Path) -> None:
        """Without it `--commit ,` returns a well-formed report over ZERO commits
        — the confident zero, arriving through argument parsing."""
        mod = _load_mutant(tmp_path, "mc_nolist", [("    if not resolved:", "    if False:")])
        repo = _init_repo(tmp_path, SCOPE)
        empty_run = mod.collect_commit_paths(repo, [])
        assert empty_run.paths == () and empty_run.commits == ()
        with pytest.raises(st.CommitMissingError):
            st.collect_commit_paths(repo, [])

    def test_kills_the_COMMIT_CAVEAT_branch(self, tmp_path: Path) -> None:
        """🔴 Without it the commit window falls through to GIT's caveat, which
        claims a BRANCH window and names a base ref — a sentence that is wrong
        about the set printed beneath it, in the field the consumer is told to
        read before writing."""
        mod = _load_mutant(
            tmp_path, "mc_caveat", [('        if self.kind == "commit":', "        if False:")]
        )
        repo, sha = self._repo_with(tmp_path)
        wrong = mod.collect_commit_paths(repo, [sha]).caveat
        assert "THESE COMMITS CHANGED" not in wrong
        assert "git:" in wrong
        assert "THESE COMMITS CHANGED" in st.collect_commit_paths(repo, [sha]).caveat

    def test_kills_the_SHARED_comma_splitter(self, tmp_path: Path) -> None:
        """Without the empty-token drop, `--commit <sha>,` becomes a second,
        unusable sha and a good run fails on a trailing comma."""
        mod = _load_mutant(
            tmp_path,
            "mc_split",
            [('    return [tok for group in groups for tok in str(group).split(",") if tok.strip()]',
              '    return [tok for group in groups for tok in str(group).split(",")]')],
        )
        repo, sha = self._repo_with(tmp_path)
        store = _make_store(tmp_path / "s")
        argv = ["--repo", str(repo), "--store", str(store), "--commit", f"{sha},",
                "--today", TODAY]
        assert mod.main(argv) == 3
        assert st.main(argv) == 0

    def test_kills_the_SHARED_toplevel_frame_from_the_COMMIT_side(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE CONSOLIDATION THIS BRANCH FORCED, pinned from the new source's
        side. `--show-toplevel` was open-coded at four call sites; the mutation
        harness found it because the git source's frame anchor started matching
        TWICE the moment a second source needed the same two lines — i.e. the
        mutant that had been pinning it could no longer be applied at all, which
        is a guard silently retiring itself. It is one helper now, and BOTH
        sources' kills use the same anchor.

        🔴 WHAT THIS KILL DOES *NOT* CLAIM, because an independent sweep measured
        it: deleting the frame changes NOTHING about the commit source's own path
        set. `diff-tree` is repo-root-relative wherever it runs, so the mutant
        still returns the right paths; the source that actually breaks is the git
        one (`ls-files --others` is cwd-scoped), and its own kill covers that.
        What is pinned from THIS side is the helper's behaviour — the thing both
        sources now share — and that the commit source is unaffected, which is a
        finding rather than a guarantee.
        """
        mod = _load_mutant(
            tmp_path,
            "mc_frame",
            [('    return Path(_git(Path(repo), ["rev-parse", "--show-toplevel"]).strip())',
              "    return Path(repo)")],
        )
        repo, sha = self._repo_with(tmp_path)
        assert mod._toplevel(repo / "src") == repo / "src", "the mutation did not apply"
        assert st._toplevel(repo / "src") == repo
        # …and the commit source survives it, which is the finding, not the claim.
        assert mod.collect_commit_paths(repo / "src", [sha]).paths == (
            st.collect_commit_paths(repo / "src", [sha]).paths
        )

    def test_kills_the_PATHS_FROM_conflict_guard_for_COMMIT(
        self, tmp_path: Path, capsys
    ) -> None:
        """Without it, `--commit <sha> --paths-from -` silently honours ONE of two
        contradictory windows."""
        mod = _load_mutant(
            tmp_path,
            "mc_conflict",
            [('        if (wants_session or wants_pr or wants_commit) and args.paths_from != "git":',
              "        if False:")],
        )
        repo, sha = self._repo_with(tmp_path)
        store = _make_store(tmp_path / "s")
        argv = ["--repo", str(repo), "--store", str(store), "--commit", sha,
                "--paths-from", "-", "--today", TODAY]
        capsys.readouterr()
        assert mod.main(argv) == 0
        assert st.main(argv) == 2
        assert "cannot be combined" in capsys.readouterr().err


# =============================================================================
# WHAT THE ENTRY ALREADY SAYS — the `already there` block.
#
# 🔴 THE MEASURED DEFECT. `KNOWN ENTRIES` printed the append SHAPE and the
# insertion point and nothing else, so the agent deciding whether to append could
# not see the bullet it was about to duplicate. The only guard was prose in
# `handoff/SKILL.md`, asking an agent to judge notability right after work it
# feels good about, with the prior bullet invisible.
#
# 🔴 SHAPES ARE MEASURED, CONTENT IS INVENTED. From a read-only pass over the
# live corpus on 2026-08-12: 26 entries, 110 top-level bullets (all at indent 0),
# 250 continuation lines (all at indent 2), 62 dated and 48 not, longest bullet
# 19 lines, median 4 bullets per entry — and one entry ALREADY carrying 6 bullets
# that share a single date, with 12 of 26 carrying at least 2. The accumulation
# this block exists to stop is not hypothetical. Nothing below reproduces a real
# entry name, path or sentence: this repo is PUBLIC.
# =============================================================================

# Wrapped multi-line prose, mixed dated/undated — the shape the corpus has.
# Two bullets share TODAY, which is the repeat-handoff case.
BUSY_NUANCE = (
    f"- {TODAY}: the batch retry budget is per-batch and not per-item, so one\n"
    "  poison record burns the whole budget and the rest of the batch is never\n"
    "  attempted.\n"
    f"- {TODAY}: a second line the earlier run of the day already wrote.\n"
    "- an undated note, of the kind 48 of the corpus's 110 bullets are.\n"
    "- 2026-07-02: the flush interval is a floor, not a schedule.\n"
    "- 2026-06-30: the oldest one.\n"
)


def _journal_entry(service: str, scope: str, *, nuance: str | None, created_by=None) -> str:
    """An entry whose `## Nuance / work-history` is under the test's control.

    `nuance=None` omits the heading entirely — the `section-absent` case that an
    `/analyze-service`-written entry really can have.
    """
    lines = ["---", f"service: {service}", f"scope: {scope}"]
    if created_by:
        lines.append(f"created_by: {created_by}")
    lines += ["---", "", "## What it is", f"The {service} thing.", "", "## Pointers", "- x — y", ""]
    if nuance is not None:
        lines += ["## Nuance / work-history", nuance]
    return "\n".join(lines)


def _journal_store(root: Path) -> Path:
    """One scope, four entries — one per journal state, so a single render is the
    positive control AND its pair."""
    store = root / "journal-store"
    d = store / SCOPE
    d.mkdir(parents=True)
    (d / "batcher.md").write_text(
        _journal_entry("batcher", SCOPE, nuance=BUSY_NUANCE, created_by="handoff"),
        encoding="utf-8",
    )
    (d / "quiet-thing.md").write_text(
        _journal_entry("quiet-thing", SCOPE, nuance=""), encoding="utf-8"
    )
    (d / "headless-thing.md").write_text(
        _journal_entry("headless-thing", SCOPE, nuance=None, created_by="analyze-service"),
        encoding="utf-8",
    )
    (d / "prosy-thing.md").write_text(
        _journal_entry("prosy-thing", SCOPE, nuance="a paragraph, with no bullet at all.\n"),
        encoding="utf-8",
    )
    return store


def _journal_paths(*services: str) -> list[str]:
    """Two paths per service — enough to clear `min_paths`."""
    return [f"src/{s}/{f}.py" for s in services for f in ("one", "two")]


def _journal_render(store: Path, *services: str, today: str = TODAY) -> str:
    rep = st.build_report(
        st.caller_supplied(_journal_paths(*services)), store, SCOPE, today=today
    )
    return st.render_text(rep)


class TestJournalPositiveControlAndItsPair:
    """🔴 ONE RUN, BOTH READINGS. An entry that surfaces nothing is only
    informative next to an entry that surfaces something through the SAME code
    path — otherwise an empty display is indistinguishable from a display wired
    to nothing, which is the class this toolchain keeps hitting."""

    def test_POSITIVE_CONTROL_an_entry_with_bullets_SURFACES_them(self, tmp_path) -> None:
        text = _journal_render(_journal_store(tmp_path), "batcher")
        assert "already there — READ THESE BEFORE PROPOSING" in text
        assert "| - " in text, "no existing bullet reached the display"
        assert "poison record burns the whole budget" in text

    def test_ITS_PAIR_an_entry_with_an_EMPTY_section_surfaces_none(self, tmp_path) -> None:
        """Reported WITH the positive control: bullets shown for `batcher`, none
        for `quiet-thing`, from one render of one store."""
        text = _journal_render(_journal_store(tmp_path), "batcher", "quiet-thing")
        assert "| - " in text, "the positive half of the pair did not fire"
        quiet = text.split("- quiet-thing")[1].split("\n  - ")[0]
        assert "present and EMPTY" in quiet
        assert "| " not in quiet, "an empty section rendered bullets from somewhere"

    def test_the_shape_alone_is_no_longer_the_whole_block(self, tmp_path) -> None:
        """The regression this exists for: before the change the block was the
        append shape plus the insertion point, and NOTHING about the entry."""
        text = _journal_render(_journal_store(tmp_path), "batcher")
        assert "append shape:" in text          # still there
        assert "COMPARE what you write against" in text
        assert text.index("already there") < text.index("append shape:"), (
            "the existing bullets must be read BEFORE the shape to fill in"
        )


class TestJournalRecencySignal:
    def test_the_newest_date_is_the_MAX_not_the_first_bullets_position(self, tmp_path) -> None:
        """🔴 Newest-first is the store's CONVENTION, not an invariant. Reading
        position as recency reports the oldest bullet as the newest the moment a
        past appender put its line at the bottom."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry(
                "batcher", SCOPE, nuance="- 2026-01-01: written first\n- 2026-08-09: newer\n"
            ),
            encoding="utf-8",
        )
        text = _journal_render(store, "batcher")
        assert "newest dated 2026-08-09" in text
        assert "2 days ago" in text

    def test_a_bullet_dated_TODAY_is_called_out_LOUDLY_with_its_count(self, tmp_path) -> None:
        """The measured failure: a second or third `/handoff` in one day adding
        another same-dated bullet with the existing ones invisible."""
        text = _journal_render(_journal_store(tmp_path), "batcher")
        assert f"2 bullets on this entry are ALREADY dated {TODAY}" in text
        assert "accumulation this block exists to prevent" in text

    def test_an_entry_with_NO_dated_bullet_says_recency_is_UNKNOWN(self, tmp_path) -> None:
        """~44% of real bullets carry no date. Inventing a recency for them would
        be worse than saying there is none."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry("batcher", SCOPE, nuance="- no date anywhere\n- nor here\n"),
            encoding="utf-8",
        )
        text = _journal_render(store, "batcher")
        assert "NONE dated — recency is UNKNOWN" in text
        assert "mtime is deliberately not used" in text

    def test_MTIME_IS_NOT_THE_SOURCE(self, tmp_path) -> None:
        """🔴 The structural version of the claim, not the prose one. The store is
        a git working tree under an hourly autocommit that other sessions also
        write to, so mtime moves for a checkout or an edit to another section —
        every one of which would report an append that never happened."""
        store = _journal_store(tmp_path)
        before = _journal_render(store, "batcher")
        os.utime(store / SCOPE / "batcher.md", (0, 0))
        assert _journal_render(store, "batcher") == before
        os.utime(store / SCOPE / "batcher.md", (2_000_000_000, 2_000_000_000))
        assert _journal_render(store, "batcher") == before

    def test_created_by_attributes_the_ENTRY_and_says_so(self, tmp_path) -> None:
        """There is no per-bullet writer in the schema. Presenting `created_by`
        as the newest bullet's author would be a claim the store cannot make."""
        text = _journal_render(_journal_store(tmp_path), "batcher")
        assert "entry created_by=handoff" in text
        assert "records no per-bullet writer" in text

    def test_an_entry_predating_the_stamp_says_so_rather_than_guessing(self, tmp_path) -> None:
        text = _journal_render(_journal_store(tmp_path), "quiet-thing")
        assert "created_by not recorded (predates the stamp)" in text

    def test_a_FUTURE_dated_bullet_is_reported_as_future_not_as_negative_days(
        self, tmp_path
    ) -> None:
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry("batcher", SCOPE, nuance="- 2027-01-01: from the future\n"),
            encoding="utf-8",
        )
        assert "in the FUTURE relative to" in _journal_render(store, "batcher")

    def test_an_UNPARSEABLE_today_degrades_to_not_computable_not_to_a_wrong_number(
        self, tmp_path
    ) -> None:
        text = _journal_render(_journal_store(tmp_path), "batcher", today="not-a-date")
        assert "age not computable" in text
        assert "days ago" not in text


class TestJournalCoversTheSHAPESTheCorpusHas:
    def test_a_WRAPPED_bullet_is_shown_whole_not_clipped_to_its_first_line(
        self, tmp_path
    ) -> None:
        """110 bullets carry 250 continuation lines. A one-line-per-bullet
        display would truncate most real entries."""
        text = _journal_render(_journal_store(tmp_path), "batcher")
        assert "|   poison record burns the whole budget and the rest of the batch is never" in text
        assert "|   attempted." in text

    def test_an_UNDATED_bullet_is_shown_like_any_other(self, tmp_path) -> None:
        text = _journal_render(_journal_store(tmp_path), "batcher")
        assert "| - an undated note" in text

    def test_the_bullet_cap_PRINTS_what_it_hid(self, tmp_path) -> None:
        """The corpus's longest bullet is 19 lines. Clipping is fine; clipping
        silently is how an agent fails to recognize its own line from an hour
        ago."""
        long_bullet = f"- {TODAY}: line one\n" + "".join(
            f"  continuation {i}\n" for i in range(2, 12)
        )
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry("batcher", SCOPE, nuance=long_bullet), encoding="utf-8"
        )
        text = _journal_render(store, "batcher")
        assert f"… +{11 - st.JOURNAL_BULLET_MAX_LINES} more lines of this bullet" in text

    def test_the_entry_cap_PRINTS_what_it_hid(self, tmp_path) -> None:
        text = _journal_render(_journal_store(tmp_path), "batcher")
        shown = st.JOURNAL_BULLETS_SHOWN
        assert f"top {shown} of 5 in stored order" in text
        assert f"… {5 - shown} further bullets not shown" in text

    def test_a_SHORT_history_is_labelled_ALL_not_TOP_N(self, tmp_path) -> None:
        """11 of the 26 real entries hold 3 bullets or fewer; calling those a
        'top 3 of 3' would imply a hidden remainder that does not exist."""
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry("batcher", SCOPE, nuance="- 2026-05-05: only one\n"), encoding="utf-8"
        )
        text = _journal_render(store, "batcher")
        assert "(all 1):" in text
        assert "not shown" not in text

    def test_existing_bullets_are_PREFIXED_and_the_proposal_is_not(self, tmp_path) -> None:
        """The one thing that must never blur: which lines the entry already has,
        and which line the agent is about to invent."""
        text = _journal_render(_journal_store(tmp_path), "batcher")
        for line in text.splitlines():
            if "poison record" in line:
                assert line.lstrip().startswith("| ")
        assert not st.journal_line_shape(TODAY).startswith("|")


class TestJournalNegativeControls:
    """Each with its OWN named error or named state, each proven reachable by an
    input no earlier guard rejects."""

    def test_a_MALFORMED_entry_raises_the_resolvers_own_error(self, tmp_path) -> None:
        """Not caught and not reworded: the loader is fail-closed on purpose, and
        an interactive caller must be told the store is broken rather than handed
        a silently short index."""
        store = _journal_store(tmp_path)
        (store / SCOPE / "nameless.md").write_text("---\nscope: x\n---\n", encoding="utf-8")
        with pytest.raises(sr.MalformedEntryError) as exc:
            _journal_render(store, "batcher")
        assert "malformed index entry" in str(exc.value)
        assert "index entry unreadable" not in str(exc.value)

    def test_an_UNREADABLE_entry_raises_the_named_error_from_build_report(
        self, tmp_path
    ) -> None:
        """Reachable through the whole flow: the loader reads every `*.md`, so a
        directory sitting where one is expected fails there — with a NAME, not as
        a bare `IsADirectoryError`."""
        store = _journal_store(tmp_path)
        (store / SCOPE / "half-written.md").mkdir()
        with pytest.raises(st.EntryUnreadableError) as exc:
            _journal_render(store, "batcher")
        assert "index entry unreadable" in str(exc.value)
        assert "malformed index entry" not in str(exc.value)

    def test_an_UNREADABLE_entry_raises_the_named_error_from_read_entry_journal(
        self, tmp_path
    ) -> None:
        """The second wrap, reached by direct call. In `build_report`'s flow the
        loader's read wins; this one covers the case it cannot — the file
        becoming unreadable BETWEEN the two reads — and `read_entry_journal` is a
        public entry point in its own right."""
        store = _journal_store(tmp_path)
        (store / SCOPE / "vanished.md").mkdir()
        entry = sr.SubsystemEntry.from_mapping(
            {"service": "vanished", "scope": SCOPE, "filename": "vanished.md"}
        )
        with pytest.raises(st.EntryUnreadableError) as exc:
            st.read_entry_journal(store, entry)
        assert "index entry unreadable" in str(exc.value)
        assert "propose no append" in str(exc.value)

    def test_a_MISSING_section_is_a_named_STATE_not_an_exception(self, tmp_path) -> None:
        """🔴 Deliberately not an error, and this is the pushback worth recording.
        An entry with no `## Nuance / work-history` is ordinary — nothing has been
        journalled against it yet, which is exactly the case the append exists to
        serve — so raising would abort the whole `/handoff` step on the ordinary
        first append. It is LOUD instead, and it says the thing the agent
        actually needs: the skill anchors its `Edit` on that heading, so the
        heading has to be created as part of the write."""
        text = _journal_render(_journal_store(tmp_path), "headless-thing")
        assert "NO `## Nuance / work-history` SECTION" in text
        assert "`Edit` anchor does not exist yet" in text

    def test_an_UNBULLETED_section_is_its_own_state_not_an_empty_history(
        self, tmp_path
    ) -> None:
        """Prose with no `- ` bullet: a schema violation the agent should see,
        never a blank that reads as 'nothing recorded'."""
        text = _journal_render(_journal_store(tmp_path), "prosy-thing")
        assert "has content but NO top-level `- ` bullet" in text
        assert "| " not in text.split("- prosy-thing")[1]

    def test_the_FOUR_states_share_no_spelling(self, tmp_path) -> None:
        """The premise of every assertion above: four mechanisms produce 'no
        bullets on screen', and collapsing any two would make one of them
        unobservable."""
        text = _journal_render(
            _journal_store(tmp_path), "batcher", "quiet-thing", "headless-thing", "prosy-thing"
        )
        for phrase in (
            "newest dated",
            "present and EMPTY",
            "NO `## Nuance / work-history` SECTION",
            "has content but NO top-level",
        ):
            assert text.count(phrase) == 1, f"{phrase!r} did not fire exactly once"


class TestJournalIsReadOnly:
    def test_reading_the_journals_writes_NOTHING(self, tmp_path) -> None:
        """🔴 The property that lets this be pointed at a curated,
        client-confidential, unbacked-up store. Hashed either side, over every
        journal state at once."""
        store = _journal_store(tmp_path)
        before = _tree_hash(store)
        _journal_render(store, "batcher", "quiet-thing", "headless-thing", "prosy-thing")
        st.report_json(
            st.build_report(
                st.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert _tree_hash(store) == before

    def test_a_journal_is_read_ONLY_for_entries_a_bullet_is_proposed_against(
        self, tmp_path
    ) -> None:
        """Below-threshold and ambiguous entries get none: no append is proposed
        for them, so there is nothing to compare against, and reading them would
        put more of a confidential store on screen than the decision needs."""
        store = _journal_store(tmp_path)
        rep = st.build_report(
            st.caller_supplied(["src/batcher/one.py", "src/batcher/two.py", "src/quiet-thing/a.py"]),
            store,
            SCOPE,
            today=TODAY,
        )
        assert set(rep.journals) == {"batcher"}
        assert [m.entry.ref for m in rep.below_threshold] == ["quiet-thing"]


class TestJournalJson:
    def test_json_carries_the_WHOLE_history_uncapped(self, tmp_path) -> None:
        """The text caps are a display bound. A consumer diffing a proposed line
        against the full history must not have to re-read the store to get it."""
        rep = st.build_report(
            st.caller_supplied(_journal_paths("batcher")),
            _journal_store(tmp_path),
            SCOPE,
            today=TODAY,
        )
        j = st.report_json(rep)["known"][0]["journal"]
        assert j["state"] == "journalled"
        assert j["bullet_count"] == 5 and len(j["bullets"]) == 5
        assert j["dated_count"] == 4
        assert j["dated_today"] == 2
        assert j["newest_date"] == TODAY and j["days_since_newest"] == 0
        assert j["recency_source"] == "newest bullet date (NOT file mtime)"

    def test_json_is_serializable_and_names_the_empty_states(self, tmp_path) -> None:
        rep = st.build_report(
            st.caller_supplied(_journal_paths("headless-thing", "quiet-thing")),
            _journal_store(tmp_path),
            SCOPE,
            today=TODAY,
        )
        states = {k["ref"]: k["journal"]["state"] for k in json.loads(json.dumps(st.report_json(rep)))["known"]}
        assert states == {"headless-thing": "section-absent", "quiet-thing": "section-empty"}


class TestJournalMutationKillMatrix:
    """One kill per new guard, each dying to its own test. The confound handled
    throughout: a display guard removed usually leaves the SURROUNDING text
    intact, so every assertion below names the specific line that must vanish
    rather than checking that the block got shorter."""

    def test_the_journal_mutant_harness_WORKS(self, tmp_path) -> None:
        """Positive control on this class's loader: an unmutated copy must still
        surface bullets, or every kill below is vacuous."""
        mod = _load_mutant(
            tmp_path, "mj_noop", [("JOURNAL_BULLETS_SHOWN = 3", "JOURNAL_BULLETS_SHOWN = 3  # noop")]
        )
        store = _journal_store(tmp_path)
        rep = mod.build_report(
            mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
        )
        assert "poison record" in mod.render_text(rep)

    def test_kills_the_JOURNAL_POPULATION(self, tmp_path) -> None:
        """🔴 The one that matters. Without it the block is back to a shape and an
        insertion point — and the `NOT READ` line is what keeps that from being
        SILENT."""
        mod = _load_mutant(
            tmp_path,
            "mj_pop",
            [("        journals={m.entry.ref: read_entry_journal(store, m.entry) "
              "for m in assoc.matched},",
              "        journals={},")],
        )
        store = _journal_store(tmp_path)
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "poison record" not in text
        assert "journal: NOT READ" in text, "the loss of the journal was SILENT"

    def test_kills_the_NOT_READ_fallback_leaving_it_silent(self, tmp_path) -> None:
        """Proves the fallback is load-bearing rather than decorative: with the
        journals gone AND the fallback neutered, a matched entry renders exactly
        like one with an empty history."""
        mod = _load_mutant(
            tmp_path,
            "mj_notread",
            [
                ("        journals={m.entry.ref: read_entry_journal(store, m.entry) "
                 "for m in assoc.matched},",
                 "        journals={},"),
                ('                    "      journal: NOT READ — this entry matched but its '
                 'existing "',
                 '                    "      "'),
            ],
        )
        store = _journal_store(tmp_path)
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "NOT READ" not in text and "poison record" not in text

    def test_kills_the_SAME_DAY_warning(self, tmp_path) -> None:
        """Without it the repeat-handoff case renders identically to a first
        append — the exact defect this change exists for."""
        mod = _load_mutant(tmp_path, "mj_repeat", [("    if repeats:", "    if False:")])
        store = _journal_store(tmp_path)
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "ALREADY dated" not in text

    def test_kills_the_MAX_date_rule(self, tmp_path) -> None:
        """Without it recency is read from POSITION, and an entry whose bullets
        were appended bottom-first reports its oldest line as its newest."""
        mod = _load_mutant(
            tmp_path,
            "mj_max",
            [("        return max(self.dated) if self.dated else None",
              "        return self.dated[0] if self.dated else None")],
        )
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry(
                "batcher", SCOPE, nuance="- 2026-01-01: written first\n- 2026-08-09: newer\n"
            ),
            encoding="utf-8",
        )
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "newest dated 2026-01-01" in text, "position was not what produced the date"

    def test_kills_the_PER_BULLET_clip_notice(self, tmp_path) -> None:
        """Without it a long bullet is cut off mid-sentence with nothing saying
        so — a bullet an agent then fails to recognize as its own."""
        mod = _load_mutant(tmp_path, "mj_clip", [("        if clipped:", "        if False:")])
        store = tmp_path / "s"
        (store / SCOPE).mkdir(parents=True)
        (store / SCOPE / "batcher.md").write_text(
            _journal_entry(
                "batcher",
                SCOPE,
                nuance=f"- {TODAY}: line one\n"
                + "".join(f"  continuation {i}\n" for i in range(2, 12)),
            ),
            encoding="utf-8",
        )
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "more lines of this bullet" not in text
        assert "continuation 2" in text, "the clip itself stopped happening, not just its notice"

    def test_kills_the_ENTRY_cap_notice(self, tmp_path) -> None:
        """Without it bullets vanish at the display cap silently — a filter
        wearing a cap's clothes."""
        mod = _load_mutant(
            tmp_path, "mj_cap", [("    if total > len(shown):", "    if False:")]
        )
        store = _journal_store(tmp_path)
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "further bullet" not in text
        assert "top 3 of 5" in text, "the cap stopped applying, so this is not the notice's kill"

    def test_kills_the_UNREADABLE_wrap_in_build_report(self, tmp_path) -> None:
        """Without it an `IsADirectoryError` escapes with nothing saying the
        SUBSYSTEM STORE was what failed."""
        mod = _load_mutant(
            tmp_path,
            "mj_unreadable",
            [('        raise EntryUnreadableError(\n            f"index entry unreadable: '
              'under {store} ',
              '        raise RuntimeError(\n            f"neutered: under {store} ')],
        )
        store = _journal_store(tmp_path)
        (store / SCOPE / "half-written.md").mkdir()
        with pytest.raises(Exception) as exc:
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        assert not isinstance(exc.value, st.EntryUnreadableError)
        assert "index entry unreadable" not in str(exc.value)

    def test_kills_the_MALFORMED_refusal_wording(self, tmp_path) -> None:
        """🔴 SUPERSEDES `test_the_MALFORMED_reraise_is_UNKILLABLE_and_that_is_the
        _finding`, and the supersession is the point.

        That test recorded a real finding: `build_report`'s
        `except MalformedEntryError: raise` was a NO-OP, because
        `MalformedEntryError` is a `ResolverError` and the `OSError` clause below
        could never have caught it — a mutation aimed at the clause proved it by
        refusing to change anything, and the honest thing was to say so rather
        than dress an unkillable clause up as a green kill.

        The clause is LOAD-BEARING now. It rewords the refusal so it names the
        recovery, so the same mutation that used to change nothing now strips the
        route out and leaves the agent at the dead end `handoff/SKILL.md` step 4
        walks it into. Reachable by construction: a malformed entry is the only
        way in, and no earlier guard rejects one.

        The premise assertion is kept — if `MalformedEntryError` ever became an
        `OSError` this kill would be measuring clause ORDER instead of wording.
        """
        mod = _load_mutant(
            tmp_path,
            "mj_malformed",
            [("    except MalformedEntryError as exc:\n        # Re-raised as the SAME class",
              "    except ZeroDivisionError as exc:\n        # Re-raised as the SAME class")],
        )
        store = _journal_store(tmp_path)
        (store / SCOPE / "nameless.md").write_text("---\nscope: x\n---\n", encoding="utf-8")

        with pytest.raises(sr.MalformedEntryError) as mutant:
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        assert "RECOVER" not in str(mutant.value)
        assert not _recovery_commands(str(mutant.value))

        with pytest.raises(sr.MalformedEntryError) as real:
            st.build_report(
                st.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        assert "RECOVER" in str(real.value)
        assert _recovery_commands(str(real.value))

        # BOTH still refuse with the same sentinel — the kill is about the route
        # out, not about the refusal, which must not have moved.
        for exc in (mutant, real):
            assert str(exc.value).startswith("malformed index entry ")
        assert not issubclass(sr.MalformedEntryError, OSError), (
            "the premise: if MalformedEntryError were an OSError this kill would be "
            "measuring clause ORDER rather than the wording"
        )

    def test_kills_the_COMPARE_instruction(self, tmp_path) -> None:
        """The bullets on screen with nothing telling the agent to read them is
        the state this change started from, one layer up."""
        mod = _load_mutant(
            tmp_path,
            "mj_compare",
            [('            "  🔴 COMPARE what you write against the `already there` lines '
              'above. Restating "',
              '            "  "')],
        )
        store = _journal_store(tmp_path)
        text = mod.render_text(
            mod.build_report(
                mod.caller_supplied(_journal_paths("batcher")), store, SCOPE, today=TODAY
            )
        )
        assert "COMPARE what you write" not in text


# =============================================================================
# --validate: the WRITER finds its own defect, in its own session.
# =============================================================================
#
# 🔴 WHY IT EXISTS. A wrapped `aliases:` list was written by one session,
# approved through a confirm gate that showed a diff CONTAINING the defect while
# being structurally incapable of revealing it, and diagnosed hours later by a
# different tool in a different session. Nothing in the write loop parsed the
# bytes.
#
# 🔴 ONE RULE, ONE PLACE. It reuses `entry_mapping` + `SubsystemEntry.from_mapping`
# + `load_index(COLLECT)` and implements NO second validator. The failure mode of
# a duplicated one is the worst possible for a checker: it starts blessing
# entries the reader rejects.
#
# Every fixture is SYNTHETIC — `devrc` is PUBLIC and real entries are
# client-confidential.

WRAPPED_ALIASES = (
    "---\n"
    "service: widget-index\n"
    f"scope: {SCOPE}\n"
    "aliases: [widget_touch, widget_recall, widget_resolver,\n"
    "          test_widget_touch, test_widget_recall]\n"
    "---\n"
    "\n"
    "## What it is\n"
    "The entry whose front matter the writer wrapped.\n"
)


def _break_one(store: Path, scope: str = SCOPE, name: str = "widget-index.md") -> Path:
    p = store / scope / name
    p.write_text(WRAPPED_ALIASES.replace(f"scope: {SCOPE}", f"scope: {scope}"), encoding="utf-8")
    return p


class TestValidate:
    def test_the_reported_defect_is_CAUGHT_at_write_time(self, store: Path) -> None:
        """🔴 THE REGRESSION. At base there was no write-time check at all."""
        bad = _break_one(store)
        got = st.validate_entry_file(bad)
        assert got is not None
        assert got.filename == "widget-index.md"
        assert got.reason == "`aliases:` must be a list, not a bare string"

    def test_a_GOOD_entry_passes(self, store: Path) -> None:
        """NEGATIVE CONTROL on the validator: one that reported every file bad
        would satisfy the test above and be useless."""
        assert st.validate_entry_file(store / SCOPE / "collector.md") is None

    def test_the_same_aliases_on_ONE_line_pass(self, store: Path) -> None:
        """🔴 The control that makes the fixture a measurement: the WRAP is what
        breaks it, not the names or the underscores in it."""
        p = store / SCOPE / "widget-index.md"
        p.write_text(
            WRAPPED_ALIASES.replace(
                "aliases: [widget_touch, widget_recall, widget_resolver,\n"
                "          test_widget_touch, test_widget_recall]\n",
                "aliases: [widget_touch, widget_recall, test_widget_touch]\n",
            ),
            encoding="utf-8",
        )
        assert st.validate_entry_file(p) is None

    def test_a_missing_path_has_its_OWN_sentinel(self, store: Path) -> None:
        """Not reported as malformed: a path that is not there and a file that
        will not parse have different fixes, and the validator's own output must
        not be the thing that misleads."""
        with pytest.raises(st.EntryFileMissingError) as exc:
            st.validate_entry_file(store / SCOPE / "no-such-file.md")
        assert "index entry file not found" in str(exc.value)
        assert "malformed index entry" not in str(exc.value)

    def test_scope_validation_counts_EVERY_file_it_walked(self, store: Path) -> None:
        """🔴 `checked` is files WALKED, not entries indexed. A "0 malformed"
        with no denominator beside it is the reassuring zero from an instrument
        wired to nothing."""
        _break_one(store)
        checked, malformed = st.validate_scope(store, SCOPE)
        assert len(checked) == 5, "README.md is not an entry; the four good ones plus the bad"
        assert [m.filename for m in malformed] == ["widget-index.md"]

    def test_scope_validation_catches_a_DUPLICATE_a_single_file_cannot(
        self, store: Path
    ) -> None:
        """The reason the no-path form exists: a duplicate is a RELATIONSHIP
        between two files, so a per-file check structurally cannot see it."""
        dupe = store / SCOPE / "status_bar.md"
        dupe.write_text(_entry("status_bar", SCOPE), encoding="utf-8")
        assert st.validate_entry_file(dupe) is None, "one file alone cannot see it"
        _checked, malformed = st.validate_scope(store, SCOPE)
        assert [m.filename for m in malformed] == ["status_bar.md"]
        assert "duplicate" in malformed[0].reason

    def test_a_clean_scope_reports_a_ZERO_WITH_its_denominator(self, store: Path) -> None:
        """POSITIVE + NEGATIVE CONTROL, reported as a pair."""
        checked, malformed = st.validate_scope(store, SCOPE)
        assert len(checked) == 4 and malformed == ()
        _break_one(store)
        checked2, malformed2 = st.validate_scope(store, SCOPE)
        assert len(checked2) == 5 and len(malformed2) == 1

    def test_an_absent_scope_checks_NOTHING_and_says_so(self, store: Path) -> None:
        checked, malformed = st.validate_scope(store, "never-indexed")
        assert (checked, malformed) == ((), ())
        text = st.render_validation(
            st.ValidationReport(
                store_root=str(store), target="`never-indexed/`", scope="never-indexed",
                checked=checked, malformed=malformed,
            )
        )
        assert "NOTHING WAS CHECKED" in text
        assert "NOT a clean bill of health" in text
        assert "0 malformed" not in text, "a bare zero over nothing walked"

    def test_a_missing_store_root_is_NOT_a_clean_scope(self, tmp_path: Path) -> None:
        with pytest.raises(st.StoreMissingError) as exc:
            st.validate_scope(tmp_path / "absent", SCOPE)
        assert "store root not found" in str(exc.value)


class TestValidateCli:
    def test_a_bad_file_exits_3_and_names_it(self, store: Path, capsys) -> None:
        bad = _break_one(store)
        code = st.main(["--store", str(store), "--scope", SCOPE, "--validate", str(bad)])
        out = capsys.readouterr().out
        assert code == 3
        assert "malformed index entry" in out
        assert "widget-index.md" in out
        assert "must be a list, not a bare string" in out

    def test_a_good_file_exits_0(self, store: Path, capsys) -> None:
        code = st.main(
            ["--store", str(store), "--scope", SCOPE, "--validate",
             str(store / SCOPE / "collector.md")]
        )
        assert code == 0
        assert "OK — 1 of 1" in capsys.readouterr().out

    def test_the_whole_scope_form_takes_no_argument(self, store: Path, capsys) -> None:
        assert st.main(["--store", str(store), "--scope", SCOPE, "--validate"]) == 0
        assert "checked: 4 entry file(s)" in capsys.readouterr().out
        _break_one(store)
        assert st.main(["--store", str(store), "--scope", SCOPE, "--validate"]) == 3
        assert "1 of 5" in capsys.readouterr().out

    def test_the_single_file_form_SAYS_what_it_did_not_check(
        self, store: Path, capsys
    ) -> None:
        """A narrower check must not look total."""
        st.main(
            ["--store", str(store), "--scope", SCOPE, "--validate",
             str(store / SCOPE / "collector.md")]
        )
        assert "NOT checked for duplicate refs" in capsys.readouterr().out

    def test_a_missing_path_exits_3_on_STDERR(self, store: Path, capsys) -> None:
        code = st.main(
            ["--store", str(store), "--scope", SCOPE, "--validate",
             str(store / SCOPE / "nope.md")]
        )
        assert code == 3
        assert "index entry file not found" in capsys.readouterr().err

    def test_json_is_parseable_and_carries_the_denominator(
        self, store: Path, capsys
    ) -> None:
        _break_one(store)
        assert st.main(
            ["--store", str(store), "--scope", SCOPE, "--validate", "--json"]
        ) == 3
        blob = json.loads(capsys.readouterr().out)
        assert blob["checked_count"] == 5
        assert blob["malformed_count"] == 1
        assert blob["malformed"][0]["file"] == "widget-index.md"

    def test_it_conflicts_with_the_other_exit_modes(self, store: Path, capsys) -> None:
        """🔴 One rule, one place: three pairwise conflicts from one table."""
        for argv in (
            ["--census", "--validate"],
            ["--template", "widget", "--validate"],
            ["--census", "--template", "widget"],
        ):
            assert st.main(["--store", str(store), "--scope", SCOPE, *argv]) == 2
            assert "select different things" in capsys.readouterr().err

    def test_validate_writes_NOTHING(self, store: Path) -> None:
        before = _tree_hash(store)
        _break_one(store)
        after_fixture = _tree_hash(store)
        st.main(["--store", str(store), "--scope", SCOPE, "--validate"])
        st.main(
            ["--store", str(store), "--scope", SCOPE, "--validate",
             str(store / SCOPE / "widget-index.md")]
        )
        assert _tree_hash(store) == after_fixture
        assert after_fixture != before  # the fixture moved it; the validator did not


def _recovery_commands(message: str) -> list[list[str]]:
    """Every `python3 <self> …` line in a refusal, as argv.

    Parsed with `shlex`, not sliced with string ops, so a command that is not
    actually well-formed shell fails HERE rather than being asserted about.
    """
    out = []
    for line in message.splitlines():
        line = line.strip()
        if line.startswith("python3 "):
            out.append(shlex.split(line))
    return out


class TestMalformedRefusalNamesTheRecovery:
    """🔴 THE GUARD WAS RIGHT TO REFUSE; THE REFUSAL WAS A DEAD END.

    `handoff/SKILL.md` step 4: any non-zero exit means "print the stderr line
    verbatim and write NOTHING". So a bare `malformed index entry 'bad.md': …`
    left the agent with no route out and no way to tell whether the offending
    file was the entry it was about to touch or something unrelated — and the
    store stayed broken until a human happened to look.

    Same defect class #436 fixed in this skill for the cwd-mismatch refusal: the
    fix is to NAME THE ALTERNATIVE, not to stop at the hazard. Behaviour and exit
    code are unchanged; this is wording, and these tests are about the wording
    being ACTIONABLE rather than merely present.
    """

    def test_it_still_REFUSES_and_still_exits_3(
        self, store: Path, capsys, monkeypatch
    ) -> None:
        """INVARIANT GUARD, labelled: the fail-closed decision did not move, and
        neither did the exit code. Not regression coverage — it was already true
        at branch-head and must stay true; only the WORDING was asked to change."""
        _break_one(store)
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        assert str(exc.value).startswith("malformed index entry "), (
            "the sentinel must still LEAD the message — every existing `except` and "
            "`in str(exc)` assertion depends on it"
        )
        # …and through the CLI, which is where the exit code lives.
        monkeypatch.setattr(
            sys, "stdin", io.StringIO("scripts/collector/a.py\nscripts/collector/b.py\n")
        )
        code = st.main(["--store", str(store), "--scope", SCOPE, "--paths-from", "-"])
        assert code == 3
        assert "malformed index entry" in capsys.readouterr().err

    def test_the_refusal_NAMES_the_recovery(self, store: Path) -> None:
        """🔴 THE REGRESSION. At branch-head the message was the bare sentinel."""
        _break_one(store)
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        msg = str(exc.value)
        assert "RECOVER" in msg
        assert _recovery_commands(msg), "no runnable recovery command was emitted"

    def test_the_recovery_command_ACTUALLY_RUNS_and_reproduces_the_diagnosis(
        self, store: Path, capsys
    ) -> None:
        """🔴 SPELLED vs STRUCTURAL, settled by EXECUTION. Asserting that the word
        "validate" appears somewhere would be satisfied by any unrelated sentence
        containing it. So the command is parsed out of the message and RUN: a
        sentence cannot survive being executed, and a stale flag cannot survive
        this parser. It must exit 3 AND name the same file the refusal named."""
        bad = _break_one(store)
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        cmds = _recovery_commands(str(exc.value))
        assert len(cmds) == 1

        argv = cmds[0]
        # The path component is real — a command naming a file that does not
        # exist is not actionable however well it parses.
        assert Path(argv[1]).is_file(), f"the recovery command names a missing script: {argv[1]}"
        assert argv[1].endswith("subsystem_touch.py")
        assert "--validate" in argv

        capsys.readouterr()
        code = st.main(argv[2:])          # everything after `python3 <script>`
        out = capsys.readouterr().out
        assert code == 3, "the recovery command did not reproduce the failure"
        assert bad.name in out, "the recovery command ran but named a different file"
        assert "must be a list, not a bare string" in out

    def test_it_names_WHICH_files_and_whether_they_are_IN_THIS_SCOPE(
        self, store: Path
    ) -> None:
        """The second half of the gap: an agent could not tell whether the
        malformed entry was the one it was about to touch or an unrelated file."""
        _break_one(store)
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        msg = str(exc.value)
        assert "widget-index.md" in msg
        assert "EVERY reader skips" in msg
        assert f"THIS repo's scope `{SCOPE}/`" in msg
        assert "do not conclude this session touched nothing" in msg

    def test_a_reject_in_ANOTHER_scope_gets_a_command_for_THAT_scope(
        self, store: Path, capsys
    ) -> None:
        """🔴 THE TRAP THE OBVIOUS FIX WALKS INTO. "Run --validate on your scope"
        is UNFOLLOWABLE precisely when the blocking file is elsewhere: the loader
        reads the whole store, so another scope's reject aborts this repo's probe
        — and validating this scope would report it CLEAN while the probe kept
        failing. That is the same unfollowable-instruction shape this PR exists to
        fix, so the command has to follow the reject, not the caller."""
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        msg = str(exc.value)
        cmds = _recovery_commands(msg)
        assert len(cmds) == 1
        assert cmds[0][cmds[0].index("--scope") + 1] == OTHER_SCOPE, (
            "the recovery command named the CALLER's scope, which is clean — "
            "an instruction that cannot reach the blocking file"
        )
        assert "would report it clean and change nothing" in msg

        # THE NEGATIVE CONTROL that proves the trap is real: the caller's own
        # scope genuinely does validate clean, so the naive command would have
        # sent the agent in a circle.
        capsys.readouterr()
        assert st.main(["--store", str(store), "--scope", SCOPE, "--validate"]) == 0
        # …while the emitted one reproduces the failure.
        capsys.readouterr()
        assert st.main(cmds[0][2:]) == 3

    def test_rejects_in_BOTH_gives_a_command_per_scope_this_repo_FIRST(
        self, store: Path
    ) -> None:
        _break_one(store)
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        cmds = _recovery_commands(str(exc.value))
        scopes = [c[c.index("--scope") + 1] for c in cmds]
        assert scopes == [SCOPE, OTHER_SCOPE], "deterministic order, this repo first"

    def test_every_emitted_command_runs_for_EVERY_affected_scope(
        self, store: Path, capsys
    ) -> None:
        """The actionability check, generalised: not just the first command."""
        _break_one(store)
        _break_one(store, OTHER_SCOPE, "widget-index.md")
        with pytest.raises(sr.MalformedEntryError) as exc:
            _report(["scripts/collector/a.py", "scripts/collector/b.py"], store)
        cmds = _recovery_commands(str(exc.value))
        assert len(cmds) == 2
        for argv in cmds:
            capsys.readouterr()
            assert st.main(argv[2:]) == 3
            assert "widget-index.md" in capsys.readouterr().out

    def test_the_enumeration_NEVER_masks_the_original_diagnosis(
        self, store: Path, monkeypatch
    ) -> None:
        """🔴 A message-builder that can itself fail must not turn one failure
        into a different one. With the second read broken, the caller still gets
        the real sentinel and the real reason — just without the enumeration."""
        _break_one(store)

        def _boom(*_a, **_kw):
            raise RuntimeError("second read exploded")

        monkeypatch.setattr(st, "load_index", _boom)
        msg = st.malformed_refusal(
            store, SCOPE, sr.MalformedEntryError(
                "malformed index entry 'widget-index.md': `aliases:` must be a list, "
                "not a bare string",
                source="widget-index.md",
                why="`aliases:` must be a list, not a bare string",
            )
        )
        assert msg.startswith("malformed index entry 'widget-index.md'")
        assert "must be a list, not a bare string" in msg
        assert "second read exploded" not in msg

    def test_a_vanished_reject_is_SAID_not_printed_as_an_empty_list(
        self, store: Path
    ) -> None:
        """The other best-effort branch: the first read raised, the second found
        nothing. A heading promising a list, over no list, would be worse than
        saying the store moved."""
        msg = st.malformed_refusal(
            store, SCOPE, sr.MalformedEntryError(
                "malformed index entry 'gone.md': whatever", source="gone.md", why="whatever"
            )
        )
        assert "found NONE" in msg
        assert "Re-run the probe" in msg
        assert "RECOVER" not in msg, "no command can be offered for a file that is not there"

    def test_the_command_is_BUILT_not_typed(self, store: Path) -> None:
        """🔴 ONE SPELLING. `validate_command` is the only place the invocation is
        composed, so a flag rename cannot leave a refusal quoting a command that
        no longer parses — which is exactly what a hand-typed "just run
        --validate" sentence has no defence against."""
        src = MODULE_PATH.read_text(encoding="utf-8")
        fn = src[src.index("def malformed_refusal("):src.index("def render_validation(")]
        assert "validate_command(" in fn
        assert '"--validate"' not in fn and "'--validate'" not in fn, (
            "malformed_refusal spells the flag itself instead of building the command"
        )
        # …and what it builds is parseable by THIS parser, derived not asserted.
        argv = shlex.split(st.validate_command(store, SCOPE))
        st._build_parser().parse_args(argv[2:])  # must not SystemExit


class TestValidatorReusesTheReadersParser:
    """🔴 ONE RULE, ONE PLACE. Structural AND behavioural, because a behavioural
    test alone cannot tell "it reuses the parser" from "it agrees with the parser
    today" — and agreement is precisely what drifts."""

    def test_it_calls_the_shared_helpers_and_declares_no_second_parser(self) -> None:
        src = MODULE_PATH.read_text(encoding="utf-8")
        fn = src[src.index("def validate_entry_file("):src.index("def validate_scope(")]
        assert "entry_mapping(" in fn
        assert "SubsystemEntry.from_mapping(" in fn
        for reimplementation in ("_FRONT_MATTER", "splitlines()", "partition(", "startswith(\"[\""):
            assert reimplementation not in fn, (
                f"validate_entry_file spells {reimplementation!r} — that is a SECOND parser, "
                f"and the day it drifts it starts blessing entries the reader rejects"
            )
        scope_fn = src[src.index("def validate_scope("):src.index("def render_validation(")]
        assert "load_index(" in scope_fn, "the scope form must go through the loader"

    def test_the_two_agree_on_EVERY_file_of_a_mixed_scope(self, store: Path) -> None:
        """BEHAVIOURAL half: the validator's verdict per file is exactly the
        reader's, over a scope with both kinds in it."""
        _break_one(store)
        index = sr.load_index(store, on_malformed=sr.ON_MALFORMED_COLLECT)
        reader_rejects = {m.filename for m in index.malformed_in(SCOPE)}
        validator_rejects = {
            p.name
            for p in sorted((store / SCOPE).glob("*.md"))
            if p.name != "README.md" and st.validate_entry_file(p) is not None
        }
        assert validator_rejects == reader_rejects == {"widget-index.md"}


class TestGoverningPolicy:
    """🔴 MEASURED 2026-08-13: 1 of the store's 5 scopes has a README, while both
    the store-root README and `/handoff` step 4 tell the writer to read the
    scope's own. The instruction was unfollowable in 80% of cases. The fix states
    which file GOVERNS; it does not generate one, because a scope README is a
    human policy statement and writing it would manufacture authority."""

    def test_a_scope_README_wins(self, store: Path) -> None:
        path, basis = st.governing_policy(store, SCOPE)
        assert path == str(store / SCOPE / "README.md")
        assert basis == st.POLICY_SCOPE

    def test_the_store_root_README_is_the_FALLBACK_and_says_so(self, store: Path) -> None:
        (store / "README.md").write_text("store policy\n", encoding="utf-8")
        path, basis = st.governing_policy(store, OTHER_SCOPE)
        assert path == str(store / "README.md")
        assert basis == st.POLICY_STORE_ROOT
        assert "this scope has none" in basis, (
            "the fallback must not read as a statement BY this scope"
        )

    def test_neither_is_a_STATED_case_not_a_silent_one(self, store: Path) -> None:
        path, basis = st.governing_policy(store, OTHER_SCOPE)
        assert path is None
        assert basis == st.POLICY_NONE

    def test_the_three_bases_share_no_spelling(self) -> None:
        """The premise of every assertion above: two bases that read alike would
        make the distinction unobservable."""
        bases = (st.POLICY_SCOPE, st.POLICY_STORE_ROOT, st.POLICY_NONE)
        assert len(set(bases)) == 3
        for a in bases:
            for b in bases:
                if a != b:
                    assert a not in b

    def test_the_probe_PRINTS_it_on_every_status(self, store: Path) -> None:
        """The line has to be on the output the writer already reads, or the
        instruction is still unfollowable."""
        for paths in ([], ["scripts/collector/x.py", "scripts/collector/y.py"], ["nope/z.py"]):
            text = st.render_text(_report(paths, store))
            assert f"policy: {store / SCOPE / 'README.md'}" in text
            assert st.POLICY_SCOPE in text

    def test_it_is_in_the_JSON_too(self, store: Path) -> None:
        blob = st.report_json(_report(["scripts/collector/x.py"], store))
        assert blob["policy_file"] == str(store / SCOPE / "README.md")
        assert blob["policy_basis"] == st.POLICY_SCOPE

    def test_a_scope_with_no_README_of_its_own_still_gets_an_answer(
        self, store: Path
    ) -> None:
        """The 80% case, end to end through the probe."""
        (store / "README.md").write_text("store policy\n", encoding="utf-8")
        text = st.render_text(_report(["a/b.py"], store, scope=OTHER_SCOPE))
        assert f"policy: {store / 'README.md'}" in text
        assert st.POLICY_STORE_ROOT in text


class TestValidateMutationKills:
    def test_kills_the_validator(self, tmp_path: Path) -> None:
        """With the rejection swallowed, `--validate` blesses the exact entry the
        reader refuses — the drift a shared validator exists to prevent."""
        mod = _load_mutant(
            tmp_path,
            "mv_validate",
            [("    except MalformedEntryError as exc:\n        return MalformedEntry(",
              "    except MalformedEntryError as exc:\n        return None or MalformedEntry(")],
        )
        store = _make_store(tmp_path / "s")
        bad = _break_one(store)
        # Control first: the anchor above is a NO-OP, so the mutant must still
        # catch it — otherwise the real kill below would be unattributable.
        assert mod.validate_entry_file(bad) is not None

        killed = _load_mutant(
            tmp_path,
            "mv_validate2",
            [("        return MalformedEntry(\n            scope=normalize_ref(p.parent.name), filename=p.name, reason=exc.why\n        )",
              "        return None")],
        )
        assert killed.validate_entry_file(bad) is None
        assert st.validate_entry_file(bad) is not None

    def test_kills_the_nothing_was_checked_guard(self, tmp_path: Path) -> None:
        """Reachable: an absent scope walks zero files past every earlier check.
        With the guard gone it renders the clean verdict over a denominator of
        zero — the reassuring zero from an instrument wired to nothing."""
        mod = _load_mutant(
            tmp_path,
            "mv_nothing",
            [("    if not report.checked:", "    if False:")],
        )
        store = _make_store(tmp_path / "s")
        rep = mod.ValidationReport(
            store_root=str(store), target="`never-indexed/`", scope="never-indexed",
            checked=(), malformed=(),
        )
        text = mod.render_validation(rep)
        assert "NOTHING WAS CHECKED" not in text
        assert "0 malformed" in text, "the mutant must print the bare zero"

    def test_kills_the_validate_exit_code(self, tmp_path: Path, capsys) -> None:
        """Reachable past the mode-conflict guard and the store guard: a valid
        `--validate` over a scope holding one reject."""
        mod = _load_mutant(
            tmp_path,
            "mv_exit",
            [("            return 0 if report.clean else 3", "            return 0")],
        )
        store = _make_store(tmp_path / "s")
        _break_one(store)
        argv = ["--store", str(store), "--scope", SCOPE, "--validate"]
        assert mod.main(argv) == 0
        capsys.readouterr()
        assert st.main(argv) == 3
        assert "malformed index entry" in capsys.readouterr().out

    def test_kills_the_policy_precedence(self, tmp_path: Path) -> None:
        """With the scope branch gone, a scope that HAS its own policy sheet is
        told the store-root one governs it."""
        mod = _load_mutant(
            tmp_path,
            "mv_policy",
            [("    if scoped.is_file():", "    if False:")],
        )
        store = _make_store(tmp_path / "s")
        (store / "README.md").write_text("store policy\n", encoding="utf-8")
        assert mod.governing_policy(store, SCOPE)[1] == mod.POLICY_STORE_ROOT
        assert st.governing_policy(store, SCOPE)[1] == st.POLICY_SCOPE

    def test_the_control_for_this_section(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL on the harness for these anchors."""
        mod = _load_mutant(tmp_path, "mv_noop", [])
        store = _make_store(tmp_path / "s")
        bad = _break_one(store)
        assert mod.validate_entry_file(bad) is not None
        assert mod.validate_entry_file(store / SCOPE / "collector.md") is None
        assert mod.governing_policy(store, SCOPE)[1] == mod.POLICY_SCOPE


# =============================================================================
# The route out of a dead end.
# =============================================================================


class TestRouteOutOfADeadEnd:
    """🔴 MEASURED 2026-08-14, and this class is that incident.

    A session in a repo whose rules force every edit into a throwaway worktree
    got `looked-at-nothing` from `--session` (0 paths under the session cwd, 12
    outside it — structural there, not occasional), correctly fell through to
    `--commit` over the shas it had made, and got `no-match` over ONE
    `claudedocs/` path from five commits. It reported that as "a real zero,
    index unchanged, correctly".

    The zero was real FOR THE WINDOW READ, and the window was the wrong one:
    worktree work is committed in the worktree and lands as a PR, so the
    base-clone shas were just the handoff doc. `--pr` — the only source that
    sees it — was never tried, and nothing on screen named it. The skill said so
    in prose; the agent was being told "no" at the moment it needed to remember.
    So the tool says it instead.
    """

    def test_a_no_match_names_the_windows_it_did_NOT_read(self, store: Path) -> None:
        text = st.render_text(_report(["docs/a.md", "notes/b.md"], store))
        assert "ROUTE OUT" in text
        for flag in ("--pr", "--commit", "--session"):
            assert flag in text, flag

    def test_looked_at_nothing_names_them_too(self, store: Path) -> None:
        """The other dead end, and the one the incident hit FIRST. It returns
        early from the renderer, so it needs its own assertion — a block added
        to one exit and not the other is exactly the shape that leaves the
        commonest path uncovered."""
        text = st.render_text(_report([], store))
        assert "ROUTE OUT" in text and "--pr" in text

    def test_a_RESOLVED_run_stays_silent(self, store: Path) -> None:
        """🔴 The negative control, and the reason this is worth having: a block
        printed on every run is boilerplate, and boilerplate is skipped. It
        appears only where the agent is stuck."""
        text = st.render_text(_report(["src/collector/a.py", "src/collector/b.py"], store))
        assert "ROUTE OUT" not in text

    def test_the_source_that_just_FAILED_is_not_suggested_back(self) -> None:
        """Re-running the window that came back empty is the one move that
        cannot help. Asserted per window, including both spellings that share a
        source — `session`/`session-absolute` and `branch`/`worktree` — because
        a mapping that covers one spelling and not its twin fails only on the
        rarer one."""
        cases = {
            "commits": "--commit",
            "pull-requests": "--pr",
            "session": "--session",
            "session-absolute": "--session",
            "branch": "(no flag)",
            "worktree": "(no flag)",
        }
        for window, own in cases.items():
            body = "\n".join(st.render_route_out(window)[3:-1])
            assert own not in body, f"{window} suggested its own source back"
            # ...and it still offers the other three, so exclusion never empties
            # the block
            assert len(body.strip().splitlines()) == 3, window

    def test_an_unknown_window_excludes_NOTHING_rather_than_guessing(self) -> None:
        """🔴 The fail-safe direction. `supplied` is the in-process entry point
        and belongs to no flag; a future window name nobody mapped lands here
        too. Offering one source too many costs a reader a line; silently
        dropping the one they needed costs them the lesson.

        🔴 ASSERTED ON THE ROWS, NOT THE WHOLE BLOCK — and that is the fix for a
        defect in this very test. It used to search the joined block, which
        includes the `read:` header naming the source that was used. A mutant
        defaulting the unknown window to `"pr"` therefore dropped the `--pr` ROW
        while `--pr` still appeared in the header, and the assertion passed: a
        guard satisfied by the same word somewhere else in the output. Counting
        the rows is what makes it structural.
        """
        for window in ("supplied", "some-window-added-later"):
            rows = st.render_route_out(window)[3:-1]
            assert len(rows) == 4, (window, rows)
            body = "\n".join(rows)
            for flag in ("--pr", "--commit", "--session", "(no flag)"):
                assert flag in body, (window, flag)

    def test_every_window_the_tool_can_emit_is_mapped(self) -> None:
        """🔴 A ledger, failing in both directions. An unmapped window falls back
        to naming the raw window string in `read:`, which is honest but tells the
        reader nothing about which flag produced it — and a mapping for a window
        that no longer exists is a phantom."""
        emitted = {
            "session", "session-absolute", "branch", "worktree",
            "pull-requests", "commits", "supplied",
        }
        assert set(st._WINDOW_SOURCE) == emitted
        # every mapped source (bar the deliberate blank) has exactly one row
        mapped = {s for s in st._WINDOW_SOURCE.values() if s}
        assert mapped == {src for src, _, _ in st._ROUTE_OUT}
