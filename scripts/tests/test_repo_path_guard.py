"""`--repo <BARE NAME>` must name the mistake, not dump git's stderr.

WHAT IS BEING PROTECTED
-----------------------
`scripts/lib/subsystem_recall.py` is the subsystem store's ONLY read surface, and
`/resume` prescribes it as a first command. MEASURED 2026-08-28 from `/tmp`, at
`dec0a939`:

    $ subsystem_recall.py --repo datapacket-talos
    subsystem-recall: git command failed (git -C /tmp/datapacket-talos rev-parse
      --path-format=absolute --git-common-dir): exit 128: fatal: cannot change to
      '/tmp/datapacket-talos': No such file or directory

exit 3, zero stdout. `--repo` takes a PATH and is resolved against the cwd, so a
bare repo NAME silently becomes `$PWD/<name>` — and that answer names neither the
rule that was broken nor either way out. The store already spent its early life
with two writers and no reader; a prescribed first command that answers with git
internals is how it goes back to unread. #965 fixed the DOCS. This file pins the
deterministic half.

🔴 THE GUARD LIVES IN THE WRITER'S `scope_for_repo`, WHICH IS THE SEAM.
`subsystem_recall` imports that function precisely so a reader and a writer can
never disagree about the scope. A second copy of the guard in the reader would
re-introduce exactly what the shared function exists to prevent, so
`TestBothCliSurfaces` asserts the two CLIs emit ONE byte-identical message and
`TestTheWordingLivesInOnePlace` scans the repo for a second copy of the prose.

🔴 EVERY EXPECTATION HERE IS A WHOLE NORMALISED STRING, WRITTEN BY HAND. A
`"scope" in msg` guard is walkable by a reword that drops the load-bearing half —
and the load-bearing half is the LAST sentence, the one that turns a dead end
into a route by naming the scope the caller probably wanted. Nothing below is
computed from the implementation: the only interpolated values are the tmp paths
this file itself created.

🔴 NO TEST HERE READS THE REAL STORE. `~/.claude/analyze-service-index/` is
client-confidential and this repo is PUBLIC; every store below is a synthetic
tree under `tmp_path` whose scope names were invented for this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from testlib import hermetic_git  # noqa: E402

import subsystem_recall as rc  # noqa: E402
import subsystem_touch as st  # noqa: E402


# =============================================================================
# Fixtures — synthetic, invented names, pairwise distinct.
# =============================================================================
#
# `SCOPE_IN_STORE` and `SCOPE_NOT_IN_STORE` share no substring, so a suggestion
# that fired on the wrong one cannot pass by coincidence. Neither is the name of
# any real repo or any real scope on either host.

SCOPE_IN_STORE = "ghost-repo"
SCOPE_NOT_IN_STORE = "absent-widget"


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """A store root holding exactly one scope directory."""
    root = tmp_path / "index-store"
    (root / SCOPE_IN_STORE).mkdir(parents=True)
    return root


@pytest.fixture()
def cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cwd that is NOT a git repo and contains neither fixture name.

    The premise is asserted rather than assumed: a tmp dir that happened to sit
    inside a checkout would let a `--repo <name>` here resolve to something real.
    """
    here = tmp_path / "elsewhere"
    here.mkdir()
    monkeypatch.chdir(here)
    assert not (here / SCOPE_IN_STORE).exists()
    return Path.cwd()


# =============================================================================
# The refusal, pinned whole.
# =============================================================================


class TestTheRefusalNamesTheMistake:
    def test_a_bare_name_that_MATCHES_a_scope_routes_the_caller_there(
        self, store: Path, cwd: Path, capsys
    ) -> None:
        """🔴 THE HIGHEST-VALUE CASE, and the one the defect was reported on.

        The reader typed a repo NAME. The store HAS that scope. Every fact needed
        to finish the job is on hand, so the refusal hands it over instead of
        printing a `git -C` invocation the caller never wrote.
        """
        code = rc.main(["--repo", SCOPE_IN_STORE, "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-recall: repo path does not exist: 'ghost-repo' → "
            f"'{cwd}/ghost-repo' (a bare name is resolved against the current "
            f"directory). --repo takes a PATH, not a repo NAME. Pass an absolute "
            f"path, one of the pre-exported handles ($DEVRC, $HOMELAB, "
            f"$DATAPACKET, $CIVITAI), or --scope <name>, which names the store "
            f"directory directly and runs no git at all. The store HAS a "
            f"`ghost-repo/` scope — you probably meant `--scope ghost-repo`."
        )

    def test_a_bare_name_with_NO_scope_says_so_instead_of_suggesting_one(
        self, store: Path, cwd: Path, capsys
    ) -> None:
        """The discriminating half. A suggestion that fires either way is not a
        suggestion — it is decoration, and it would send the caller to a
        `--scope` that recalls nothing."""
        code = rc.main(["--repo", SCOPE_NOT_IN_STORE, "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-recall: repo path does not exist: 'absent-widget' → "
            f"'{cwd}/absent-widget' (a bare name is resolved against the current "
            f"directory). --repo takes a PATH, not a repo NAME. Pass an absolute "
            f"path, one of the pre-exported handles ($DEVRC, $HOMELAB, "
            f"$DATAPACKET, $CIVITAI), or --scope <name>, which names the store "
            f"directory directly and runs no git at all. The store has no "
            f"`absent-widget/` scope, so `--scope absent-widget` would not help "
            f"either."
        )

    def test_an_UNREADABLE_store_says_NOT_CHECKED_rather_than_no(
        self, tmp_path: Path, cwd: Path, capsys
    ) -> None:
        """🔴 THE CONFIDENT ZERO THIS REPO IS MOSTLY ABOUT. "the store has no such
        scope" and "the store was never looked at" are different facts with
        different next moves, and a scan that walked nothing must never render as
        an all-clear."""
        missing_store = tmp_path / "no-store-here"
        code = rc.main(["--repo", SCOPE_IN_STORE, "--store", str(missing_store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-recall: repo path does not exist: 'ghost-repo' → "
            f"'{cwd}/ghost-repo' (a bare name is resolved against the current "
            f"directory). --repo takes a PATH, not a repo NAME. Pass an absolute "
            f"path, one of the pre-exported handles ($DEVRC, $HOMELAB, "
            f"$DATAPACKET, $CIVITAI), or --scope <name>, which names the store "
            f"directory directly and runs no git at all. Whether the store has a "
            f"`ghost-repo/` scope was NOT CHECKED: store root "
            f"'{missing_store}' is not a directory."
        )

    def test_an_ABSOLUTE_path_drops_the_cwd_clause(
        self, tmp_path: Path, store: Path, cwd: Path, capsys
    ) -> None:
        """The arrow exists to make the cwd-join visible. There is no join here,
        so printing `'/x' → '/x'` and blaming the current directory would be a
        false explanation of a real failure."""
        target = tmp_path / "not-there"
        code = rc.main(["--repo", str(target), "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-recall: repo path does not exist: '{target}'. --repo "
            f"takes a PATH, not a repo NAME. Pass an absolute path, one of the "
            f"pre-exported handles ($DEVRC, $HOMELAB, $DATAPACKET, $CIVITAI), or "
            f"--scope <name>, which names the store directory directly and runs "
            f"no git at all. The store has no `not-there/` scope, so `--scope "
            f"not-there` would not help either."
        )

    def test_an_ABSOLUTE_path_that_RESOLVES_ELSEWHERE_still_drops_the_clause(
        self, tmp_path: Path, store: Path, cwd: Path, capsys
    ) -> None:
        """🔴 THE SIBLING GUARD ABOVE IS NARROWER THAN ITS NAME, and this is the
        gap it leaves.

        It passes a path that does not exist, so `resolve()` returns it unchanged
        and `given == resolved` — the clause is dropped by the equality branch,
        not because the path was absolute. An absolute path that resolves
        SOMEWHERE ELSE takes the other branch entirely.

        MEASURED on NixOS: `--repo /etc/hostname` resolves to
        `/nix/store/…-etc-hostname`, and the shipped message told that caller
        their absolute path "is resolved against the current directory" — a false
        explanation of a real failure, in the one message whose whole job is to
        name the mistake correctly. The predicate was "the two strings differ";
        it had to be "the input was relative".
        """
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        given = link / "missing"
        assert given.is_absolute()
        code = rc.main(["--repo", str(given), "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert str(given) != str(given.resolve()), (
            "fixture is not exercising the branch it exists for — the symlink "
            "did not make `resolve()` differ from the input"
        )
        assert "→" in err, "the arrow should still show where the path landed"
        assert "current directory" not in err, (
            "an ABSOLUTE path was blamed on the current directory. The two "
            "strings differ here because of a SYMLINK, not a cwd-join."
        )

    def test_a_path_that_EXISTS_AS_A_FILE_is_not_reported_as_absent(
        self, tmp_path: Path, store: Path, cwd: Path, capsys
    ) -> None:
        """Two mistakes, two spellings. Telling somebody their file "does not
        exist" while they are looking at it is the confidently-wrong line that
        makes a reader distrust the rest of the message."""
        target = tmp_path / "notes.md"
        target.write_text("not a repo\n", encoding="utf-8")
        code = rc.main(["--repo", str(target), "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-recall: repo path is not a directory: '{target}'. --repo "
            f"takes a PATH, not a repo NAME. Pass an absolute path, one of the "
            f"pre-exported handles ($DEVRC, $HOMELAB, $DATAPACKET, $CIVITAI), or "
            f"--scope <name>, which names the store directory directly and runs "
            f"no git at all. The store has no `notes.md/` scope, so `--scope "
            f"notes.md` would not help either."
        )

    def test_a_value_that_NORMALIZES_AWAY_says_NOT_CHECKED_too(
        self, store: Path, cwd: Path, capsys
    ) -> None:
        """`normalize_ref` returns "" for input that folds away entirely, and
        every caller in this codebase treats "" as "not a ref" rather than as a
        wildcard. Checking the store for a `/` scope, or claiming one is absent,
        would both be answers to a question nobody can ask."""
        code = rc.main(["--repo", "!!!", "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-recall: repo path does not exist: '!!!' → '{cwd}/!!!' (a "
            f"bare name is resolved against the current directory). --repo takes "
            f"a PATH, not a repo NAME. Pass an absolute path, one of the "
            f"pre-exported handles ($DEVRC, $HOMELAB, $DATAPACKET, $CIVITAI), or "
            f"--scope <name>, which names the store directory directly and runs "
            f"no git at all. Whether the store has a matching scope was NOT "
            f"CHECKED: that value does not normalize to a scope name."
        )


# =============================================================================
# The seam: ONE guard, at the shared function, reached by BOTH CLIs.
# =============================================================================


class TestBothCliSurfaces:
    def test_the_WRITER_cli_refuses_with_THE_SAME_SENTENCE(
        self, store: Path, cwd: Path, capsys
    ) -> None:
        """🔴 THE SEAM GUARD, and the reason the fix went into `scope_for_repo`
        rather than into the reader. Two CLIs answering one mistake with two
        different remedies is the reader/writer disagreement the shared function
        exists to make impossible — and `claude/RULES.md` is explicit that a
        component verified in isolation says nothing about the seam.

        The only permitted difference is the program prefix.
        """
        code = st.main(
            ["--repo", SCOPE_IN_STORE, "--store", str(store), "--template", "widget"]
        )
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err == (
            f"subsystem-touch: repo path does not exist: 'ghost-repo' → "
            f"'{cwd}/ghost-repo' (a bare name is resolved against the current "
            f"directory). --repo takes a PATH, not a repo NAME. Pass an absolute "
            f"path, one of the pre-exported handles ($DEVRC, $HOMELAB, "
            f"$DATAPACKET, $CIVITAI), or --scope <name>, which names the store "
            f"directory directly and runs no git at all. The store HAS a "
            f"`ghost-repo/` scope — you probably meant `--scope ghost-repo`."
        )

    def test_the_two_cli_bodies_differ_ONLY_by_their_program_prefix(
        self, store: Path, cwd: Path, capsys
    ) -> None:
        """INVARIANT GUARD — measured green at `9bc7f5eb` too, because the raw
        `GitError` it used to print was also identical across the two CLIs. It is
        counted as one, not as regression coverage.

        It earns its place anyway: the pin above is two hand-written literals, so
        it would survive the two CLIs drifting apart as long as somebody updated
        both. This one compares the emitted bytes to each other, and so fails on
        a drift no literal was updated for.
        """
        rc.main(["--repo", SCOPE_IN_STORE, "--store", str(store)])
        read_err = capsys.readouterr().err.strip()
        st.main(
            ["--repo", SCOPE_IN_STORE, "--store", str(store), "--template", "widget"]
        )
        write_err = capsys.readouterr().err.strip()
        assert read_err.removeprefix("subsystem-recall: ") == write_err.removeprefix(
            "subsystem-touch: "
        )
        assert read_err != write_err, "premise gone: the prefixes are identical"

    def test_git_is_NEVER_INVOKED_for_a_path_that_is_not_a_directory(
        self, store: Path, cwd: Path, capsys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 "DETECT BEFORE INVOKING GIT" IS THE REQUIREMENT, and a message
        assertion alone cannot see it: the old code could be left in place and
        the new text layered on top of git's failure, which would still spawn a
        subprocess per bad argument and would still be a `GitError` underneath.

        So git is made UNCALLABLE and the refusal is required to arrive anyway.
        """

        def _no_git(*_a, **_k):  # pragma: no cover - the point is that it is not hit
            raise AssertionError("git was invoked for a path that is not a directory")

        monkeypatch.setattr(st, "_git", _no_git)
        code = rc.main(["--repo", SCOPE_IN_STORE, "--store", str(store)])
        err = capsys.readouterr().err.strip()
        assert code == 3
        assert err.startswith("subsystem-recall: repo path does not exist:")

    def test_BOTH_flags_describe_themselves_as_a_PATH(self, capsys) -> None:
        """The refusal explains the rule after the fact; `--help` is where the
        rule is read BEFORE the mistake. Both said "repo", which is the word that
        invited a repo name. Pinned whole, on both CLIs — one fixed and one not
        is the half-fix that reads as done."""
        for module, expected in (
            (rc, "PATH to the repo whose scope to read — not a repo name (default: cwd)"),
            (st, "PATH to the repo to read paths from — not a repo name (default: cwd)"),
        ):
            with pytest.raises(SystemExit):
                module.main(["--help"])
            text = " ".join(capsys.readouterr().out.split())
            assert expected in text, f"{module.__name__} help: {text[:400]}"

    def test_the_refusal_is_a_TouchError_so_the_existing_handlers_catch_it(
        self,
    ) -> None:
        """Both CLIs already map `TouchError` to exit 3. A new error class outside
        that hierarchy would escape as a traceback with exit 1 — a different
        contract for `/resume`, which treats a non-zero exit as "recall nothing"
        and a traceback as a broken tool."""
        assert issubclass(st.RepoPathMissingError, st.TouchError)


# =============================================================================
# What must NOT have changed. Invariant guards, labelled as such.
# =============================================================================
#
# 🔴 NEITHER OF THESE IS REGRESSION COVERAGE. Both pass on pre-change code, by
# construction: they pin behaviour the fix had to leave alone. Counting them as
# red-to-green would be the vacuous-guard shape `claude/RULES.md` names.


class TestPreservedBehaviour:
    def test_a_REAL_repo_still_derives_its_scope_through_the_new_signature(
        self, tmp_path: Path, store: Path
    ) -> None:
        """INVARIANT GUARD. The guard sits in front of the git call, so the git
        call must still happen for every path that is a directory — including
        when the two new keyword arguments are supplied."""
        repo = tmp_path / "real-checkout"
        repo.mkdir()
        env = {
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            **hermetic_git.MAINTENANCE_OFF,
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        subprocess.run(
            ["git", "-C", str(repo), "init", "-q", "-b", "main"], env=env, check=True
        )
        assert st.scope_for_repo(repo) == "real-checkout"
        assert (
            st.scope_for_repo(repo, store_root=store, given="real-checkout")
            == "real-checkout"
        )

    def test_an_explicit_scope_still_bypasses_repo_derivation_entirely(
        self, store: Path, cwd: Path, capsys
    ) -> None:
        """INVARIANT GUARD, and the reason the last remedy in the message is
        honest: `--scope` names the store directory directly, so it works even
        while `--repo` still holds the value that could not be derived from."""
        code = rc.main(
            ["--repo", SCOPE_IN_STORE, "--scope", SCOPE_IN_STORE, "--store", str(store)]
        )
        out = capsys.readouterr().out
        # 0, pinned as a literal this file owns rather than as `!= 3`: an
        # inequality is satisfied by every OTHER refusal too, so it would stay
        # green if this path started failing for a different reason.
        assert code == 0
        assert f"scope={SCOPE_IN_STORE}" in out


# =============================================================================
# ONE SPELLING — the repo-wide ledger, plus its positive control.
# =============================================================================
#
# The requirement is that the wording is built in exactly ONE place. A ledger
# that inspected a hardcoded file list would read as coverage while providing
# none, so this SCANS the tree.

#: Load-bearing fragments of the refusal. Owned by this file: each is a phrase a
#: second implementation would have to re-type to say the same thing, and none of
#: them appears in unrelated prose (asserted by the ledger itself, which would
#: report the extra file).
WORDING_FRAGMENTS = (
    "--repo takes a PATH, not a repo NAME.",
    "a bare name is resolved against the current directory",
    "you probably meant",
    "which names the store directory directly and runs no git at all",
)

#: The files permitted to carry that wording, repo-relative POSIX paths.
#: `subsystem_touch.py` DEFINES it; this file PINS it. Two-way: a third file
#: fails the ledger, and an entry here that no longer carries the wording fails
#: it too — a stale allowlist is how a ledger stops describing the repo.
WORDING_OWNERS = frozenset(
    {
        "scripts/lib/subsystem_touch.py",
        "scripts/tests/test_repo_path_guard.py",
    }
)

#: Extensions scanned. Stated so the guard's coverage is no wider than its
#: implementation: a second copy in a language outside this set is NOT seen.
SCANNED_SUFFIXES = (".py", ".sh", ".md")

_SKIP_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", "node_modules", "result", ".direnv"}
)


def _repo_text_files(root: Path) -> list[tuple[str, Path]]:
    """Every scanned-suffix file under `root`, `git ls-files` first.

    The fallback is not decoration: `nix build .#checks…` copies the tree WITHOUT
    `.git`, so the fallback is what actually runs in the sandbox tier — the tier
    the merge is gated on. Both tiers are run before this is claimed merge-safe.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode == 0:
            rels = [r for r in out.stdout.split("\0") if r]
            if rels:
                return [
                    (r, root / r)
                    for r in rels
                    if r.endswith(SCANNED_SUFFIXES) and (root / r).is_file()
                ]
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        pass
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not path.name.endswith(SCANNED_SUFFIXES):
            continue
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts[:-1]):
            continue
        found.append((rel.as_posix(), path))
    return found


def _files_carrying_the_wording(root: Path) -> set[str]:
    return {
        rel
        for rel, path in _repo_text_files(root)
        if any(f in path.read_text(errors="replace") for f in WORDING_FRAGMENTS)
    }


class TestTheWordingLivesInOnePlace:
    def test_the_owner_ledger_is_pinned_two_way(self) -> None:
        """🔴 GROWTH *AND* SHRINK. A second module spelling the same remedy is
        the defect (`claude/RULES.md`: "one rule, one place" — a predicate
        open-coded at N sites is typically wrong at N−1 of them). A listed file
        that no longer carries it is a ledger entry describing a repo that has
        moved on."""
        assert _files_carrying_the_wording(ROOT) == set(WORDING_OWNERS)

    def test_the_scanner_can_SEE_a_planted_second_copy(self, tmp_path: Path) -> None:
        """🔴 THE POSITIVE CONTROL. A clean ledger above is indistinguishable
        from a scanner wired to nothing — a wrong glob, a suffix filter that
        matches no file, a `read_text` that swallows everything. So a tree with a
        KNOWN duplicate is scanned through the same function, and the number is
        required to move.

        The fixture is a second copy of ONE fragment in a file no allowlist
        covers, which is exactly the shape a real regression would take.
        """
        planted = tmp_path / "scripts" / "lib" / "impostor.py"
        planted.parent.mkdir(parents=True)
        planted.write_text(
            'BAD = "--repo takes a PATH, not a repo NAME."\n', encoding="utf-8"
        )
        clean = tmp_path / "scripts" / "lib" / "innocent.py"
        clean.write_text("OK = 1\n", encoding="utf-8")
        found = _files_carrying_the_wording(tmp_path)
        assert found == {"scripts/lib/impostor.py"}

    def test_every_fragment_is_present_in_the_defining_module(self) -> None:
        """Each fragment must actually occur, or the ledger above is asserting
        the absence of prose that was never there — green for the wrong reason,
        and still green with the whole message deleted."""
        source = (ROOT / "scripts" / "lib" / "subsystem_touch.py").read_text(
            encoding="utf-8"
        )
        missing = [f for f in WORDING_FRAGMENTS if f not in source]
        assert missing == []


# =============================================================================
# The message builder, called directly.
# =============================================================================


class TestTheMessageBuilder:
    def test_it_falls_back_to_the_resolved_path_when_no_raw_value_was_carried(
        self, store: Path
    ) -> None:
        """Internal callers — `service_recon._scope_of` among them — never had a
        raw string. They must still get the remedy, without a `'None' → …` line
        that would read as a value somebody typed."""
        msg = st.repo_path_missing_message(
            None, "/var/empty/ghost-repo", store_root=store
        )
        assert msg == (
            "repo path does not exist: '/var/empty/ghost-repo'. --repo takes a "
            "PATH, not a repo NAME. Pass an absolute path, one of the "
            "pre-exported handles ($DEVRC, $HOMELAB, $DATAPACKET, $CIVITAI), or "
            "--scope <name>, which names the store directory directly and runs "
            "no git at all. The store HAS a `ghost-repo/` scope — you probably "
            "meant `--scope ghost-repo`."
        )

    def test_no_store_root_is_NOT_CHECKED_and_not_a_denial(self) -> None:
        """The default. `store_root=None` means nobody offered a store, which is
        a third reading — not "absent"."""
        msg = st.repo_path_missing_message("ghost-repo", "/var/empty/ghost-repo")
        assert msg == (
            "repo path does not exist: 'ghost-repo' → '/var/empty/ghost-repo' (a "
            "bare name is resolved against the current directory). --repo takes "
            "a PATH, not a repo NAME. Pass an absolute path, one of the "
            "pre-exported handles ($DEVRC, $HOMELAB, $DATAPACKET, $CIVITAI), or "
            "--scope <name>, which names the store directory directly and runs "
            "no git at all. Whether the store has a `ghost-repo/` scope was NOT "
            "CHECKED: no store root was given."
        )

    def test_every_handle_the_remedy_names_is_one_the_config_really_exports(
        self,
    ) -> None:
        """🔴 A REMEDY NAMING A HANDLE THAT DOES NOT EXIST IS WORSE THAN NO
        REMEDY: the caller pastes it, `$NOPE` expands to nothing, `--repo` gets
        the empty string, and they land right back here — now believing the
        advice was followed.

        So this reads `nix/agent-handles.nix`, the single source of truth for the
        handles exported into every agent shell, rather than restating the
        literals. SUBSET, not equality, and deliberately: that file also defines
        `CIVITAI_CLI`, which the message omits to stay the length a person reads.
        Widening the message is allowed; naming something that is not there is
        not.
        """
        handles_nix = (ROOT / "nix" / "agent-handles.nix").read_text(encoding="utf-8")
        repos_block = handles_nix.split("repos = {", 1)[1].split("};", 1)[0]
        declared = {
            line.split("=", 1)[0].strip()
            for line in repos_block.splitlines()
            if "=" in line
        }
        assert "DEVRC" in declared, (
            "premise gone: nix/agent-handles.nix no longer parses this way, so "
            "this guard is asserting against an empty set"
        )
        assert {h.lstrip("$") for h in st.REPO_PATH_HANDLES} <= declared
