#!/usr/bin/env python3
"""`cairn doctor` — and the one property it exists for: an UNMEASURED answer.

🔴 WHAT THIS FILE GRADES. Every other diagnostic in this subsystem has been
caught reporting a zero it could not distinguish from a failure to look: the
frozen mirror's "ALL 26 entries, none omitted"; the pod's "ALL 5 entries in
devrc/" over a store holding 9; `cairn validate`'s "NOTHING WAS CHECKED" at exit
0. `doctor` exists to join those facts in one call, so it is exactly the place
that defect would arrive next, wearing a nicer word.

So the assertions here are almost all of one shape: **the same visible outcome,
reached two ways, must NOT produce the same report.** A cache with zero entries
and a cache that could not be read; a store that holds nothing for this token and
a store that never answered; a mirror that is absent and a mirror that is
unreadable.

🔴 EVERY EXPECTED VALUE IS A LITERAL WRITTEN OUT HERE. `assert X == module.X` —
a constant agreeing with itself — has shipped in this subsystem five times, each
fix narrower than the class. Where a fact is shared with another module
(`server.token_id`, `server.DEFAULT_TOKEN_FILE`) the test imports BOTH sides and
grades the RELATIONSHIP against a literal, rather than letting either define the
answer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import cairn_doctor as cd  # noqa: E402

CAIRN_CLI = REPO / "scripts" / "cairn"
SERVER_PY = REPO / "scripts" / "subsystem-store-api" / "server.py"


def _load_cairn_cli():
    """Exec `scripts/cairn` as a module — it has no .py extension."""
    spec = importlib.util.spec_from_loader(
        "cairn_cli_doctor", loader=None, origin=str(CAIRN_CLI)
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(CAIRN_CLI)
    exec(compile(CAIRN_CLI.read_text(encoding="utf-8"), str(CAIRN_CLI), "exec"), mod.__dict__)
    return mod


def _load_api():
    """Import `server.py` by path. `sys.modules[...]` BEFORE `exec_module` —
    without it the first `@dataclass` raises under `from __future__ import
    annotations`. Same idiom as `test_cairn_write._load_api`."""
    spec = importlib.util.spec_from_file_location("srv_doctor", SERVER_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _store(root: Path, scopes: dict[str, list[str]], *, stamp: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for scope, entries in scopes.items():
        (root / scope).mkdir(parents=True, exist_ok=True)
        for name in entries:
            (root / scope / name).write_text("# entry\n", encoding="utf-8")
    if stamp is not None:
        (root / ".sync-stamp").write_text(stamp, encoding="utf-8")
    return root


def _collect(**over):
    """`collect` with every argument defaulted to a benign, MEASURED fact.

    Each test overrides exactly the one thing it is about, which is what keeps a
    mutation isolated to the branch under test rather than to the fixture.
    """
    base = dict(
        resolved_root=Path("/nowhere/cache"),
        stamp_lines=("synced=1", "entries=2"),
        stamp_reason=None,
        cache_root=Path("/nowhere/cache"),
        mirror_root=Path("/nowhere/mirror"),
        pod=cd.PodFacts(reached=True, visible_entries=2, store_wide_entries=2,
                        visible_scopes=("alpha",), snapshot_header="entry-files=2"),
        token="tok",
        token_reason="",
        identity_remedy="ask the operator",
    )
    base.update(over)
    return cd.collect(**base)


def _by_name(checks) -> dict[str, cd.Check]:
    return {c.name: c for c in checks}


# =============================================================================
# The four states, and the contract on each
# =============================================================================

class TestTheStateVocabulary:
    def test_the_four_states_are_exactly_these_literals(self) -> None:
        """Written out here, never read off the module. A caller — a human or a
        `--json` consumer — branches on these strings."""
        assert cd.OK == "OK"
        assert cd.PROBLEM == "PROBLEM"
        assert cd.UNMEASURED == "UNMEASURED"
        assert cd.NOT_OBSERVABLE == "NOT-OBSERVABLE"
        assert cd.STATES == ("OK", "PROBLEM", "UNMEASURED", "NOT-OBSERVABLE")

    def test_the_exit_codes_are_exactly_these_literals(self) -> None:
        """🔴 DISJOINT FROM EVERY OTHER `cairn` CODE, and that is graded below
        against the CLI rather than asserted here alone."""
        assert cd.EXIT_DOCTOR_OK == 0
        assert cd.EXIT_DOCTOR_PROBLEM == 9
        assert cd.EXIT_DOCTOR_UNMEASURED == 10

    def test_doctors_codes_collide_with_NO_other_cairn_exit_code(self) -> None:
        """🔴 SEAM GUARD. `cairn`'s 4 already means two different things across
        two tools (`EXIT_REFRESH_FAILED` vs the reader's
        `EXIT_UNSTAMPED_READ_STORE`) and `/resume` carries a paragraph about it.
        A third collision is a defect this file can prevent for free."""
        cli = _load_cairn_cli()
        others = {
            cli.EXIT_OK, cli.EXIT_USAGE, cli.EXIT_UNREACHABLE_NO_CACHE,
            cli.EXIT_REFRESH_FAILED, cli.EXIT_CORRUPT, cli.EXIT_WRITE_REFUSED,
            cli.EXIT_WRITE_UNREACHABLE, cli.EXIT_WRITE_PRECONDITION,
        }
        assert others == {0, 2, 3, 4, 5, 6, 7, 8}, (
            f"cairn's existing exit codes moved: {sorted(others)}"
        )
        assert cd.EXIT_DOCTOR_PROBLEM not in others
        assert cd.EXIT_DOCTOR_UNMEASURED not in others

    def test_a_check_with_an_EMPTY_detail_is_refused(self) -> None:
        """🔴 A bare `UNMEASURED` with no reason is the reassuring zero wearing
        a different word. The refusal is at construction so no render path can
        emit one."""
        with pytest.raises(ValueError, match="empty detail"):
            cd.Check("x", cd.UNMEASURED, "   ")

    def test_an_unknown_state_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown check state"):
            cd.Check("x", "FINE", "detail")

    def test_PodFacts_ENFORCES_its_reason_it_does_not_merely_document_one(
        self,
    ) -> None:
        """🔴 THE ASYMMETRY WAS THE FINDING. `Check` refused an empty detail at
        construction; `PodFacts` said "Mandatory in that case" and checked
        nothing. An unreasoned `PodFacts(reached=False)` renders `… could not be
        established — .` and walks straight past `Check`'s guard, because the
        empty string is wrapped in literal f-string text before it gets there."""
        with pytest.raises(ValueError, match="requires a reason"):
            cd.PodFacts(reached=False)
        with pytest.raises(ValueError, match="requires a reason"):
            cd.PodFacts(reached=False, reason="   ")
        # A REACHED pod needs none — there is nothing unexplained about success.
        assert cd.PodFacts(reached=True).reason == ""


class TestTheVerdict:
    def test_a_problem_outranks_an_unmeasured(self) -> None:
        checks = [cd.Check("a", cd.UNMEASURED, "no"), cd.Check("b", cd.PROBLEM, "bad")]
        assert cd.exit_code(checks) == 9

    def test_an_unmeasured_outranks_an_ok(self) -> None:
        checks = [cd.Check("a", cd.OK, "fine"), cd.Check("b", cd.UNMEASURED, "no")]
        assert cd.exit_code(checks) == 10

    def test_all_ok_is_zero(self) -> None:
        assert cd.exit_code([cd.Check("a", cd.OK, "fine")]) == 0

    def test_NOT_OBSERVABLE_alone_is_ZERO_and_that_is_deliberate(self) -> None:
        """🔴 THE PERMANENTLY-RED-GATE GUARD. The pod exposes no identity route,
        so the credential check is NOT-OBSERVABLE on every healthy run forever.
        Escalating on it would make `doctor` non-zero always, which
        `claude/RULES.md` says trains everyone to click through. Pinned so the
        two states cannot be quietly merged."""
        checks = [cd.Check("a", cd.OK, "fine"), cd.Check("b", cd.NOT_OBSERVABLE, "ask")]
        assert cd.exit_code(checks) == 0

    def test_an_EMPTY_check_list_is_not_a_clean_bill(self) -> None:
        """A run that produced no checks exits 0 today, so this pins the shape
        the render carries instead: the counts are printed, and `OK=0` is
        visibly different from `OK=7`. Stated rather than left implied — this is
        an INVARIANT GUARD on the render, not a claim that zero checks is safe.
        """
        assert "OK=0" in cd.render([cd.Check("a", cd.NOT_OBSERVABLE, "x")])


# =============================================================================
# UNMEASURED is not a zero — the whole point, one check at a time
# =============================================================================

class TestAnUnmeasuredAnswerIsNotAZero:
    def test_an_unreached_pod_leaves_the_count_UNMEASURED_not_zero(self) -> None:
        checks = _by_name(_collect(
            pod=cd.PodFacts(reached=False, reason="connection refused")
        ))
        assert checks["cache-vs-pod"].state == cd.UNMEASURED
        assert "connection refused" in checks["cache-vs-pod"].detail
        assert checks["token-scopes"].state == cd.UNMEASURED

    def test_a_store_that_genuinely_holds_NOTHING_is_OK_not_unmeasured(
        self, tmp_path
    ) -> None:
        """🔴 THE OTHER HALF, and the one a lazier implementation gets wrong. An
        empty store REACHED is a measured fact, and reporting it as UNMEASURED
        would be the mirror-image lie: a working system described as unknowable.
        """
        cache = _store(tmp_path / "cache", {})
        checks = _by_name(_collect(
            cache_root=cache,
            pod=cd.PodFacts(reached=True, visible_entries=0, store_wide_entries=0,
                            visible_scopes=(), snapshot_header="entry-files=0"),
        ))
        assert checks["cache-vs-pod"].state == cd.OK
        assert "0 entry file(s) here" in checks["cache-vs-pod"].detail

    def test_the_two_zeroes_produce_DIFFERENT_reports(self, tmp_path) -> None:
        """The discriminating assertion: same visible count, two mechanisms."""
        cache = _store(tmp_path / "cache", {})
        reached = _by_name(_collect(
            cache_root=cache,
            pod=cd.PodFacts(reached=True, visible_entries=0, store_wide_entries=0,
                            snapshot_header="entry-files=0"),
        ))["cache-vs-pod"]
        unreached = _by_name(_collect(
            cache_root=cache,
            pod=cd.PodFacts(reached=False, reason="DNS failure"),
        ))["cache-vs-pod"]
        assert reached.state != unreached.state
        assert reached.detail != unreached.detail

    def test_a_missing_mirror_is_OK_and_an_UNREADABLE_one_is_UNMEASURED(
        self, tmp_path
    ) -> None:
        """Absent and unreadable are the classic pair. A fresh host has no
        mirror at all, which is fine; a mirror this process cannot read is a
        fact nobody has established, and the two must not render alike."""
        absent = _by_name(_collect(mirror_root=tmp_path / "never-existed"))["frozen-mirror"]
        assert absent.state == cd.OK

        blocked = tmp_path / "blocked"
        blocked.mkdir()
        (blocked / "scope").mkdir()
        blocked.chmod(0o000)
        try:
            unreadable = _by_name(_collect(mirror_root=blocked))["frozen-mirror"]
        finally:
            blocked.chmod(0o700)
        assert unreadable.state == cd.UNMEASURED
        assert absent.detail != unreadable.detail

    def test_the_absent_vs_unreadable_split_is_STRUCTURAL_not_a_sentence(
        self, tmp_path
    ) -> None:
        """🔴 A guard SPELLED rather than structural is walkable by rewording.
        The first version asked `reason.endswith("does not exist")`, so changing
        that message would have made an UNREADABLE mirror report as "nothing
        pre-cutover on this host" — an OK, silently. `Reading.absent` is a flag."""
        missing = cd._describe(tmp_path / "gone", cd.store_scopes)
        assert missing.absent is True and missing.value is None

        blocked = tmp_path / "blocked"
        blocked.mkdir()
        blocked.chmod(0o000)
        try:
            denied = cd._describe(blocked, cd.store_scopes)
        finally:
            blocked.chmod(0o700)
        assert denied.absent is False and denied.value is None
        assert not denied.ok and not missing.ok

        present = cd._describe(_store(tmp_path / "s", {"a": []}), cd.store_scopes)
        assert present.ok and present.absent is False and present.value == ("a",)

    def test_a_file_VANISHING_MID_WALK_is_not_reported_as_an_absent_store(
        self, tmp_path
    ) -> None:
        """🔴 `absent` IS RE-CHECKED, NOT INFERRED FROM THE EXCEPTION TYPE.

        Mapping any `FileNotFoundError` to `absent=True` produces a confident OK
        with a FALSE reason — `frozen-mirror OK … does not exist` for a directory
        that is right there. This store has two writers that make it happen: the
        hourly `analyze-service-index-commit.service`, and `cairn sync`, which
        replaces the cache root by rename.
        """
        root = _store(tmp_path / "s", {"alpha": ["a.md"]})

        def vanishing(_root):
            raise FileNotFoundError(2, "No such file or directory", "alpha/a.md")

        reading = cd._describe(root, vanishing)
        assert reading.absent is False, "a live root was reported as absent"
        assert not reading.ok
        assert "concurrent writer" in reading.reason
        # …and the root genuinely being gone still reads as absent.
        assert cd._describe(tmp_path / "gone", vanishing).absent is True

    def test_a_no_sync_run_says_SO_rather_than_reporting_an_outage(self) -> None:
        """`--no-sync` is a choice, not a failure. The reason has to name it, or
        an operator reads a deliberate offline run as a broken store."""
        checks = _by_name(_collect(pod=cd.PodFacts(
            reached=False,
            reason="--no-sync was given, so the store was never contacted",
        )))
        assert "--no-sync" in checks["pod"].detail
        assert checks["pod"].state == cd.UNMEASURED


# =============================================================================
# The individual checks
# =============================================================================

class TestTheReaderResolution:
    def test_an_unstamped_resolution_is_a_PROBLEM_naming_the_remedy(self) -> None:
        checks = _by_name(_collect(
            stamp_lines=None, stamp_reason="no `.sync-stamp` in /nowhere/cache",
        ))
        assert checks["reader-resolution"].state == cd.PROBLEM
        assert "cairn sync" in checks["reader-resolution"].detail
        assert checks["cache-stamp"].state == cd.PROBLEM

    def test_the_unstamped_detail_names_the_READERS_four_not_cairns(self) -> None:
        """🔴 THE TWO EXIT 4s. `cairn sync`'s own 4 (`EXIT_REFRESH_FAILED`)
        means the store was NOT reached but the cache survived — re-running
        `cairn sync` is the command that just failed. The 4 `cairn sync` FIXES
        is the reader's `EXIT_UNSTAMPED_READ_STORE`. Confusing them sends a
        reader to re-run the failure, so the detail names which four it is."""
        detail = _by_name(_collect(
            stamp_lines=None, stamp_reason="no stamp",
        ))["reader-resolution"].detail
        assert "EXIT_UNSTAMPED_READ_STORE" in detail
        assert "READER" in detail

    def test_a_stamped_resolution_relays_the_fields_UNPARSED(self) -> None:
        """The stamp's schema belongs to `cairn sync`. Doctor prints the lines;
        it does not interpret them, and it computes no age from them."""
        detail = _by_name(_collect(
            stamp_lines=("synced=1788000000", "coverage=ALL", "entries=7"),
        ))["cache-stamp"].detail
        assert "synced=1788000000" in detail
        assert "coverage=ALL" in detail
        assert "entries=7" in detail


class TestTheFrozenMirror:
    def test_a_WRITABLE_entry_file_is_a_PROBLEM(self, tmp_path) -> None:
        """🔴 THIS FOUND A LIVE DATA-LOSS PATH ON ITS FIRST RUN.

        Measured on the workbench 2026-09-03: **7** entry files under the
        supposedly-frozen mirror were still mode 644, an append was watched to
        SUCCEED on one, and six entries created on the dead mirror after the
        cutover existed nowhere else. The operator has since completed the
        freeze; the tree now measures 159 files, all `0444`, writable=0, and
        `cairn doctor` reports `frozen-mirror OK`.

        ⚠ Which is exactly why this builds its OWN fixture rather than asserting
        against the live tree: those numbers moved within a day of being taken,
        and a guard pinned to them would now be red for the wrong reason.
        """
        mirror = _store(tmp_path / "m", {"alpha": ["a.md", "b.md"]})
        (mirror / "alpha" / "a.md").chmod(0o444)
        (mirror / "alpha" / "b.md").chmod(0o644)
        check = _by_name(_collect(mirror_root=mirror))["frozen-mirror"]
        assert check.state == cd.PROBLEM
        assert "alpha/b.md" in check.detail
        assert "1 entry file" in check.detail

    def test_a_fully_frozen_mirror_is_OK(self, tmp_path) -> None:
        mirror = _store(tmp_path / "m", {"alpha": ["a.md"]})
        (mirror / "alpha" / "a.md").chmod(0o444)
        assert _by_name(_collect(mirror_root=mirror))["frozen-mirror"].state == cd.OK

    def test_the_walk_counts_FILES_not_directories(self, tmp_path) -> None:
        """A writable scope DIRECTORY is expected and correct — `subsystem-index`
        documents that a first-ever entry is still created locally. Only the
        entry files are frozen, so a directory mode must not raise a PROBLEM."""
        mirror = _store(tmp_path / "m", {"alpha": ["a.md"]})
        (mirror / "alpha" / "a.md").chmod(0o444)
        (mirror / "alpha").chmod(0o755)
        assert cd.writable_entry_files(mirror) == ()


class TestThePodProbe:
    def test_an_UNAUTHORISED_answer_is_not_an_outage(self) -> None:
        """🔴 THE DISCRIMINATION THE BRIEF NAMES. 401 means the host is UP and
        the credential is wrong — a completely different remedy from a DNS
        failure, and `resolve_state` deliberately collapses the two because a
        READ degrades to the cache either way. Doctor must not."""
        refused = _by_name(_collect(pod=cd.PodFacts(
            reached=False, reason="answered HTTP 401", http_status=401,
        )))["pod"]
        outage = _by_name(_collect(pod=cd.PodFacts(
            reached=False, reason="unreachable: [Errno -2] Name or service not known",
        )))["pod"]
        assert refused.state == cd.PROBLEM
        assert outage.state == cd.UNMEASURED
        assert "NOT an outage" in refused.detail

    def test_a_403_names_the_edge_as_a_rival_explanation(self) -> None:
        """MEASURED: this host's edge 403s urllib's default User-Agent, and that
        403 arrives looking like a bad token AND like the store being down. A
        report that named only the token would send an operator to rotate a
        credential that is fine."""
        detail = _by_name(_collect(pod=cd.PodFacts(
            reached=False, reason="answered HTTP 403", http_status=403,
        )))["pod"].detail
        assert "User-Agent" in detail

    def test_a_500_is_a_PROBLEM_that_names_the_status(self) -> None:
        detail = _by_name(_collect(pod=cd.PodFacts(
            reached=False, reason="answered HTTP 503", http_status=503,
        )))["pod"].detail
        assert "503" in detail


class TestTheScopeVisibility:
    def test_a_local_scope_the_store_did_not_send_is_a_PROBLEM(self, tmp_path) -> None:
        """🔴 MEASURED LIVE 2026-09-03: `civitai-app-requests` and
        `civitai-developer-docs` exist in the frozen mirror and are absent from
        the snapshot this token receives."""
        cache = _store(tmp_path / "c", {"alpha": ["a.md"]})
        mirror = _store(tmp_path / "m", {"alpha": ["a.md"], "orphan": ["o.md"]})
        check = _by_name(_collect(
            cache_root=cache, mirror_root=mirror,
            pod=cd.PodFacts(reached=True, visible_entries=1, store_wide_entries=1,
                            visible_scopes=("alpha",)),
        ))["token-scopes"]
        assert check.state == cd.PROBLEM
        assert "orphan" in check.detail

    def test_a_missing_scope_is_TAGGED_with_the_tree_it_came_from(
        self, tmp_path
    ) -> None:
        """🔴 THE TWO ROOTS MEAN DIFFERENT THINGS AND LEAD TO DIFFERENT ACTIONS.

        A scope in the SYNCED CACHE the pod no longer sends is a credential or a
        deletion. A scope in the FROZEN MIRROR only is a pre-cutover leftover
        that may never have reached the pod at all — so "add it to the allowlist"
        is the wrong first question. The first version merged the two sets and
        said "N scope(s) exist on this disk", and both scopes it named live were
        mirror-only.
        """
        cache = _store(tmp_path / "c", {"alpha": ["a.md"], "cache-only": ["c.md"]})
        mirror = _store(tmp_path / "m", {"alpha": ["a.md"], "mirror-only": ["m.md"]})
        check = _by_name(_collect(
            cache_root=cache, mirror_root=mirror,
            pod=cd.PodFacts(reached=True, visible_entries=1, store_wide_entries=1,
                            visible_scopes=("alpha",)),
        ))["token-scopes"]
        assert check.state == cd.PROBLEM
        assert "mirror-only [mirror]" in check.detail
        assert "cache-only [cache]" in check.detail
        # …and the mirror-only one gets the leftover reading spelled out, while
        # the cache-only one does not inherit it.
        assert "ONLY in the frozen pre-cutover mirror (mirror-only)" in check.detail

    def test_a_scope_in_BOTH_trees_is_tagged_with_both(self, tmp_path) -> None:
        cache = _store(tmp_path / "c", {"shared": ["a.md"]})
        mirror = _store(tmp_path / "m", {"shared": ["a.md"]})
        detail = _by_name(_collect(
            cache_root=cache, mirror_root=mirror,
            pod=cd.PodFacts(reached=True, visible_entries=0, store_wide_entries=0,
                            visible_scopes=()),
        ))["token-scopes"].detail
        assert "shared [cache+mirror]" in detail
        # Present in the live cache too, so it is NOT a pre-cutover leftover.
        assert "ONLY in the frozen" not in detail

    def test_it_refuses_to_GUESS_which_of_the_two_readings_is_right(
        self, tmp_path
    ) -> None:
        """🔴 The API answers "outside your allowlist" and "never existed" with
        BYTE-IDENTICAL bytes, deliberately, so that an error cannot enumerate
        the store. A report that picked one would be a coin flip recorded as a
        diagnosis — `claude/RULES.md` on an empty result naming no mechanism."""
        cache = _store(tmp_path / "c", {"alpha": ["a.md"]})
        mirror = _store(tmp_path / "m", {"orphan": ["o.md"]})
        detail = _by_name(_collect(
            cache_root=cache, mirror_root=mirror,
            pod=cd.PodFacts(reached=True, visible_entries=1, store_wide_entries=1,
                            visible_scopes=("alpha",)),
        ))["token-scopes"].detail
        assert "byte-identical" in detail
        assert "allowlist" in detail

    def test_a_store_wide_count_ABOVE_the_visible_one_is_reported(self) -> None:
        """The other channel, and the only client-side evidence that entries
        exist which this credential cannot reach: `X-Store-Snapshot` carries an
        UNFILTERED `entry-files=` while `X-Store-Entries` is this token's slice.
        """
        check = _by_name(_collect(pod=cd.PodFacts(
            reached=True, visible_entries=2, store_wide_entries=11,
            visible_scopes=("alpha",),
        )))["token-scopes"]
        assert check.state == cd.PROBLEM
        assert "11" in check.detail and "9 live in scopes" in check.detail

    def test_an_UNREADABLE_local_root_makes_this_UNMEASURED_not_OK(
        self, tmp_path
    ) -> None:
        """🔴 The first version returned OK with the hole named in its tail. An
        OK carrying a caveat is how a partial answer gets read as a clean one —
        "every scope on this disk is among them" is a claim about a set the walk
        could not finish building. Graded against the OK case below, which is
        identical except that both roots are readable."""
        cache = _store(tmp_path / "c", {"alpha": ["a.md"]})
        blocked = tmp_path / "m"
        blocked.mkdir()
        blocked.chmod(0o000)
        try:
            check = _by_name(_collect(
                cache_root=cache, mirror_root=blocked,
                pod=cd.PodFacts(reached=True, visible_entries=1, store_wide_entries=1,
                                visible_scopes=("alpha",)),
            ))["token-scopes"]
        finally:
            blocked.chmod(0o700)
        assert check.state == cd.UNMEASURED
        assert "could not be fully read" in check.detail

    def test_equal_counts_and_no_missing_scope_is_OK(self, tmp_path) -> None:
        cache = _store(tmp_path / "c", {"alpha": ["a.md"]})
        check = _by_name(_collect(
            cache_root=cache, mirror_root=tmp_path / "absent",
            pod=cd.PodFacts(reached=True, visible_entries=1, store_wide_entries=1,
                            visible_scopes=("alpha",)),
        ))["token-scopes"]
        assert check.state == cd.OK


class TestTheCredential:
    def test_no_token_configured_is_a_PROBLEM_carrying_the_config_reason(self) -> None:
        check = _by_name(_collect(
            token=None,
            token_reason="config incomplete: SUBSYSTEM_STORE_TOKEN not set",
        ))["token"]
        assert check.state == cd.PROBLEM
        assert "SUBSYSTEM_STORE_TOKEN" in check.detail

    def test_a_configured_token_is_NOT_OBSERVABLE_with_a_remedy(self) -> None:
        check = _by_name(_collect(token="tok", identity_remedy="RUN THIS THING"))["token"]
        assert check.state == cd.NOT_OBSERVABLE
        assert "RUN THIS THING" in check.detail

    def test_the_TOKEN_ITSELF_never_reaches_the_report(self) -> None:
        secret = "s3cr3t-not-in-any-output"
        rendered = cd.render(_collect(token=secret))
        assert secret not in rendered
        assert cd.token_fingerprint(secret) in rendered

    def test_the_fingerprint_is_the_SERVERS_token_id(self) -> None:
        """🔴 THE SEAM. The fingerprint is only useful because it MATCHES the
        handle the pod's audit log carries; two independent spellings that drift
        make it a number an operator cannot look up. `cairn` cannot import
        `server.py`, so the rule is spelled twice — and graded here against a
        literal digest, so the two agreeing with each other is not the test."""
        api = _load_api()
        sample = "a-known-fixture-token"
        # 🔴 THE EXPECTED DIGEST IS A LITERAL, PRODUCED BY A DIFFERENT TOOL:
        #     printf 'a-known-fixture-token' | sha256sum | cut -c1-12
        # Without it, `cd` and `server` agreeing with each other would satisfy
        # this test while both had drifted from sha256 — the "constant agreeing
        # with itself" shape this subsystem has shipped five times.
        expected = "d56f7752989e"
        assert hashlib.sha256(sample.encode("utf-8")).hexdigest()[:12] == expected
        assert cd.token_fingerprint(sample) == expected
        assert api.token_id(sample) == expected
        assert cd.TOKEN_FINGERPRINT_CHARS == 12

    def test_the_identity_remedy_names_the_pods_own_token_file(self) -> None:
        """The remedy is a command a human types under pressure. If it named a
        path the pod does not mount it would send them to an empty file and read
        as 'no rows', which is the same silent zero one layer out."""
        api = _load_api()
        cli = _load_cairn_cli()
        assert api.DEFAULT_TOKEN_FILE == "/run/secrets/subsystem-store/token"
        assert api.DEFAULT_TOKEN_FILE in cli.IDENTITY_REMEDY
        assert "subsystem-store" in cli.IDENTITY_REMEDY


# =============================================================================
# The rendered surface and the CLI wiring
# =============================================================================

class TestTheRenderedReport:
    def test_the_exit_legend_is_printed_by_the_COMMAND(self) -> None:
        """🔴 The brief's requirement: the codes are documented by the command,
        never by a skill. So every human-readable run carries them."""
        rendered = cd.render(_collect())
        for code, _why in cd.EXIT_LEGEND:
            assert f"{code} =" in rendered

    def test_every_state_is_counted_in_the_summary(self) -> None:
        rendered = cd.render(_collect())
        for state in cd.STATES:
            assert f"{state}=" in rendered

    def test_the_json_carries_the_legend_and_the_exit(self) -> None:
        payload = cd.to_dict(_collect())
        assert payload["exit"] in (0, 9, 10)
        assert set(payload["exit_legend"]) == {"0", "9", "10"}
        assert {c["name"] for c in payload["checks"]} == {
            "reader-resolution", "cache-stamp", "frozen-mirror", "pod",
            "cache-vs-pod", "token-scopes", "token",
        }

    def test_the_check_set_is_pinned_to_these_seven_names(self) -> None:
        """A LEDGER, not a count: a check silently disappearing is a fact nobody
        is checking any more, and a count would not say which."""
        assert [c.name for c in _collect()] == [
            "reader-resolution", "cache-stamp", "frozen-mirror", "pod",
            "cache-vs-pod", "token-scopes", "token",
        ]


class TestTheCliWiring:
    def test_doctor_is_a_real_subcommand_reaching_cmd_doctor(self) -> None:
        cli = _load_cairn_cli()
        args = cli.build_parser().parse_args(["doctor"])
        assert args.func is cli.cmd_doctor
        assert args.json is False and args.no_sync is False

    def test_doctor_takes_NO_scope_flag(self) -> None:
        """A `--scope` here would invite the filtered-cache mistake `cmd_sync`
        documents, and doctor asks about the HOST, not a scope."""
        cli = _load_cairn_cli()
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["doctor", "--scope", "devrc"])

    def test_an_ABSENT_doctor_module_does_not_kill_an_UNRELATED_subcommand(
        self, monkeypatch
    ) -> None:
        """🔴 MEASURED: with `cairn_doctor.py` absent, `cairn ls-entries` — which
        has nothing to do with doctor — died `ModuleNotFoundError`, exit 1, with
        a traceback. `build_parser` calls `_doctor_epilog()` eagerly to build a
        HELP STRING, so an unguarded import there put every verb behind a module
        only one of them needs.

        `cmd_doctor` itself must still fail loudly — that caller asked for it.
        """
        cli = _load_cairn_cli()

        def gone():
            raise ImportError("No module named 'cairn_doctor'")

        monkeypatch.setattr(cli, "_cairn_doctor", gone)
        epilog = cli._doctor_epilog()
        assert "UNAVAILABLE" in epilog and "cairn_doctor" in epilog
        # The parser still builds, so unrelated verbs still parse and dispatch.
        args = cli.build_parser().parse_args(["ls-entries"])
        assert args.func is cli.cmd_ls_entries
        # …and doctor itself does NOT quietly degrade.
        with pytest.raises(ImportError):
            cli.cmd_doctor(cli.build_parser().parse_args(["doctor"]))

    def test_the_help_epilog_carries_the_exit_codes(self) -> None:
        cli = _load_cairn_cli()
        epilog = cli._doctor_epilog()
        for code, _why in cd.EXIT_LEGEND:
            assert f"  {code}  " in epilog
        for state in cd.STATES:
            assert state in epilog

    def test_a_no_sync_run_still_reads_the_LOCAL_config(self, tmp_path, monkeypatch) -> None:
        """🔴 A FIXED DEFECT, PINNED. The first version skipped `load_config`
        under `--no-sync`, so a host with a perfectly good token in
        `~/.config/subsystem-store/env` was reported `token PROBLEM: no token is
        configured`. A check that says PROBLEM about a thing it never looked at
        is the same defect as one that says OK about it."""
        cli = _load_cairn_cli()
        monkeypatch.setenv("SUBSYSTEM_STORE_URL", "https://example.invalid")
        monkeypatch.setenv("SUBSYSTEM_STORE_TOKEN", "a-token-from-the-env")
        monkeypatch.setattr(cli.subsystem_touch, "DEFAULT_STORE_ROOT", tmp_path / "mirror")
        monkeypatch.setattr(cli._read_store, "DEFAULT_CACHE_ROOT", tmp_path / "cache")
        args = cli.build_parser().parse_args(["doctor", "--no-sync", "--json"])
        args.cache = tmp_path / "cache"
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_doctor(args)
        payload = json.loads(buf.getvalue())
        token_check = [c for c in payload["checks"] if c["name"] == "token"][0]
        assert token_check["state"] == "NOT-OBSERVABLE", token_check
        assert cd.token_fingerprint("a-token-from-the-env") in token_check["detail"]
        assert rc in (9, 10)

    def test_probe_store_NEVER_installs_a_snapshot(self, monkeypatch, tmp_path) -> None:
        """🔴 A diagnostic that repaired the cache as a side effect would destroy
        the staleness it was run to measure — and it would be the one command
        you must not run twice. Graded by watching `install_snapshot`."""
        cli = _load_cairn_cli()
        installed = []
        monkeypatch.setattr(
            cli, "install_snapshot",
            lambda *a, **k: installed.append(a) or 0,
        )

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"# entry\n"
            info = tarfile.TarInfo("alpha/a.md")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        body = buf.getvalue()
        headers = Message()
        headers["x-store-entries"] = "1"
        headers["x-store-snapshot"] = "seeded=X newest=Y entry-files=4"
        monkeypatch.setattr(cli, "fetch_snapshot", lambda *a, **k: (body, headers))

        facts = cli.probe_store("https://example.invalid", "tok", timeout=5)
        assert installed == [], "doctor installed a snapshot"
        assert facts.reached is True
        assert facts.visible_entries == 1
        assert facts.store_wide_entries == 4
        assert facts.visible_scopes == ("alpha",)

    def test_a_missing_X_Store_Entries_header_is_UNMEASURED_not_the_archive_count(
        self, monkeypatch, tmp_path
    ) -> None:
        """🔴 REACHABILITY OF THE `visible_entries is None` BRANCH, and a fixed
        defect. `probe_store` first fell back to counting the members it had
        received — which is the side of the comparison a TRUNCATED transfer
        moves, so cache-vs-pod would have agreed with itself and reported OK over
        a short answer. `install_snapshot` cross-checks against this header for
        exactly that reason. Without it, the count is unmeasured."""
        cli = _load_cairn_cli()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"# entry\n"
            info = tarfile.TarInfo("alpha/a.md")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        headers = Message()
        headers["x-store-snapshot"] = "entry-files=9"
        monkeypatch.setattr(cli, "fetch_snapshot", lambda *a, **k: (buf.getvalue(), headers))

        facts = cli.probe_store("https://example.invalid", "tok", timeout=5)
        assert facts.reached is True
        assert facts.visible_entries is None
        assert facts.visible_scopes == ("alpha",)
        cache = _store(tmp_path / "cache", {"alpha": ["a.md"]})
        check = _by_name(_collect(pod=facts, cache_root=cache))["cache-vs-pod"]
        assert check.state == cd.UNMEASURED
        assert "X-Store-Entries" in check.detail

    def test_probe_store_carries_the_HTTP_STATUS_not_a_parsed_message(
        self, monkeypatch
    ) -> None:
        """🔴 STRUCTURAL, NOT TEXTUAL. Before `StoreUnreachable.http_status` the
        only thing separating 401 from a DNS failure was the phrasing of an
        f-string, and a caller that greps a message has pinned a format."""
        cli = _load_cairn_cli()

        def boom(*a, **k):
            raise cli.StoreUnreachable("x answered HTTP 401", http_status=401)

        monkeypatch.setattr(cli, "fetch_snapshot", boom)
        facts = cli.probe_store("https://example.invalid", "tok", timeout=5)
        assert facts.reached is False
        assert facts.http_status == 401

    def test_a_connection_failure_carries_http_status_None(self, monkeypatch) -> None:
        cli = _load_cairn_cli()

        def boom(*a, **k):
            raise cli.StoreUnreachable("x unreachable: nope")

        monkeypatch.setattr(cli, "fetch_snapshot", boom)
        assert cli.probe_store("u", "t", timeout=5).http_status is None

    def test_fetch_snapshot_SETS_the_status_from_a_real_HTTPError(
        self, monkeypatch
    ) -> None:
        """The producing end of the same seam, so neither side is graded alone."""
        cli = _load_cairn_cli()

        def raise_http(*a, **k):
            raise urllib.error.HTTPError(
                "https://example.invalid", 401, "Unauthorized", Message(), io.BytesIO(b"no")
            )

        monkeypatch.setattr(cli.urllib.request, "urlopen", raise_http)
        with pytest.raises(cli.StoreUnreachable) as excinfo:
            cli.fetch_snapshot("https://example.invalid", "tok", scope=None, timeout=5)
        assert excinfo.value.http_status == 401

    def test_the_doctor_subcommand_is_TRACKED_and_the_module_ships(self) -> None:
        """🔴 A new file must be `git add`ed or the flake silently omits it from
        the deploy — the switch succeeds and the module is simply absent, which
        would make `cairn doctor` an ImportError on both hosts."""
        if not (REPO / ".git").exists():
            return
        for rel in ("scripts/lib/cairn_doctor.py", "scripts/cairn"):
            out = subprocess.run(
                ["git", "-C", str(REPO), "ls-files", "--error-unmatch", "--", rel],
                capture_output=True, text=True,
            )
            assert out.returncode == 0, f"{rel} is not tracked by git\n{out.stderr}"


class TestTheDiskHelpers:
    def test_the_entry_count_matches_the_SERVERS_walk_shape(self, tmp_path) -> None:
        """Depth 2, `*.md`, no dot-directories, no dot-files — the same shape
        `server.snapshot_freshness` counts, so this number and the pod's
        `entry-files=` are answers to one question. Fixture values are chosen so
        no wrong rule coincides: 3 is not the scope count, not the file count,
        and not the depth-any count."""
        root = tmp_path / "s"
        _store(root, {"alpha": ["a.md", "b.md"], "beta": ["c.md"]})
        (root / ".hidden").mkdir()
        (root / ".hidden" / "x.md").write_text("x", encoding="utf-8")
        (root / "alpha" / ".swap.md").write_text("x", encoding="utf-8")
        (root / "alpha" / "notes.txt").write_text("x", encoding="utf-8")
        (root / "alpha" / "deep").mkdir()
        (root / "alpha" / "deep" / "d.md").write_text("x", encoding="utf-8")
        (root / "top.md").write_text("x", encoding="utf-8")
        assert cd.store_entry_files(root) == 3

    def test_the_scope_list_skips_dot_directories_and_files(self, tmp_path) -> None:
        root = tmp_path / "s"
        _store(root, {"alpha": ["a.md"], "beta": []}, stamp="synced=1\n")
        (root / ".git").mkdir()
        assert cd.store_scopes(root) == ("alpha", "beta")

    def test_an_unreadable_root_RAISES_rather_than_counting_zero(self, tmp_path) -> None:
        """`Path.rglob` swallows a permission error and yields nothing, so an
        unreadable store would report `0` — the exact confusion
        `server.snapshot_freshness` uses `os.walk(onerror=…)` to avoid."""
        blocked = tmp_path / "b"
        blocked.mkdir()
        blocked.chmod(0o000)
        try:
            with pytest.raises(OSError):
                cd.store_entry_files(blocked)
        finally:
            blocked.chmod(0o700)
