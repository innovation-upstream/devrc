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
is that step, and `TestStatusIsTheDiscriminator` proves all five are reachable
and that no two of them are spelled the same.

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
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "lib" / "subsystem_touch.py"
HANDOFF_DOC = ROOT / "claude" / "skills" / "handoff" / "SKILL.md"
ANALYZE_DOC = ROOT / "claude" / "skills" / "analyze-service" / "SKILL.md"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))

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
    """Five mechanisms produce an empty proposal list. `status` names which."""

    def test_looked_at_nothing(self, store: Path) -> None:
        rep = _report([], store)
        assert rep.status == "looked-at-nothing"
        assert rep.association is None
        assert not rep.writes_proposed

    def test_no_store(self, tmp_path: Path) -> None:
        with pytest.raises(st.StoreMissingError) as exc:
            _report(["a/b.yaml", "a/c.yaml"], tmp_path / "absent")
        assert "store root not found" in str(exc.value)

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

    def test_the_five_spellings_are_pairwise_distinct(self) -> None:
        """A discriminator whose values collide discriminates nothing."""
        assert len(set(st.STATUS_PRECEDENCE)) == len(st.STATUS_PRECEDENCE) == 5

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
        """When two components cover exactly the same paths, the deeper one is
        the more specific name for that set. `apps` is not a subsystem.

        ⚠ The fixture is chosen so ALPHABETICAL order DISAGREES with depth order
        (`apps` < `ingest`). An earlier version used `src/roster/…`, where
        `roster` < `src` — so it passed identically with the depth key deleted,
        and the mutation test for that key was unkillable. That is the
        "green for the wrong reason" shape, caught by trying to kill it."""
        rep = _report(["apps/ingest/a.yaml", "apps/ingest/b.yaml"], store)
        assert [n.ref for n in rep.nominations] == ["ingest", "apps"]
        assert rep.nominations[0].depth > rep.nominations[1].depth
        assert sorted(["ingest", "apps"]) == ["apps", "ingest"], "the fixture lost its bite"

    def test_a_broader_component_ranks_by_count_first(self, store: Path) -> None:
        """Honest, not clever: when the only thing four paths share is `src`,
        `src` really is the only candidate covering them all."""
        rep = _report(
            ["src/roster/a.py", "src/roster/b.py", "src/paging/c.py", "src/paging/d.py"],
            store,
        )
        assert rep.nominations[0].ref == "src"
        assert rep.nominations[0].path_count == 4
        assert {"roster", "paging"} <= {n.ref for n in rep.nominations}

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
        """The decision doc's reopening gate is "≥5 entries outside its current
        single scope" — a per-scope count, so the instrument reports one."""
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
    ]

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
        home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
        assert 'home.file.".claude/skills"' in home_nix
        assert "source = ../claude/skills;" in home_nix


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
            tmp_path, "m_nom_cap", [("    return tuple(out[:limit])", "    return tuple(out)")]
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
                    "    out.sort(key=lambda n: (not n.coherent, -n.path_count, -n.depth, n.ref))",
                    "    out.sort(key=lambda n: (not n.coherent, -n.path_count, n.ref))",
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
                    "    out.sort(key=lambda n: (not n.coherent, -n.path_count, -n.depth, n.ref))",
                    "    out.sort(key=lambda n: (-n.path_count, -n.depth, n.ref))",
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

    def test_kills_git_failure_guard(self, tmp_path: Path) -> None:
        """Without it a failed git returns an empty stdout and the report says
        "0 paths" — a broken instrument reading as a real zero."""
        mod = _load_mutant(
            tmp_path, "m_git", [("    if proc.returncode != 0:", "    if False:")]
        )
        plain = tmp_path / "plain-dir"
        plain.mkdir()
        src = mod.collect_git_paths(plain)
        assert src.paths == ()  # the silent zero the real guard prevents

    def test_kills_branch_window(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_branch",
            [
                (
                    "            commands.append(tuple(branch_args))\n"
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
