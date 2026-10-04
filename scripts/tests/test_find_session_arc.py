"""Guards for `scripts/lib/handoff_arc.py` — handoff ARC resolution.

🔴 THE LOAD-BEARING TEST IN THIS MODULE IS
`TestTheTrailerParserTrap::test_gits_own_trailer_parser_MISSES_what_this_reader_finds`.
It is not a unit test of a regex; it is the CONTROL that proves the reader had to
be written this way. It builds a real commit in the GitHub-squash shape and
measures BOTH readers against it — the body scan finds the id, `%(trailers:…)`
returns empty. Without that second half the module's central design decision
rests on an assertion nobody watched fail.

Everything asserting exact membership runs against a SYNTHETIC fixture repo, never
the live corpus: a test keyed on real transcripts would drift the day one is
pruned. The live corpus is checked separately, and loosely, by the smoke check the
task's criterion 11 names.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import handoff_arc as ha  # noqa: E402
from testlib.hermetic_git import hermetic_git_env  # noqa: E402

DOC = "claudedocs/handoff-arc-fixture.md"

#: The shape a GitHub squash produces from a branch of N commits: each becomes a
#: `*` bullet CARRYING ITS OWN TRAILER, so an id lands MID-MESSAGE and the final
#: paragraph is ordinary prose rather than a trailer block. Git's parser reads
#: only that final block, finds no trailers in it, and returns empty.
#:
#: 🔴 COPIED FROM A REAL COMMIT (`f9ef66e4`), NOT IMAGINED — and that distinction
#: is the whole reason this fixture is trustworthy. The first draft of it put the
#: trailer LAST, which is a perfectly plausible-looking squash body and which
#: git's parser reads just fine: the control went GREEN for both readers and
#: correctly reported itself as wired to nothing. The trailing prose is not
#: decoration; it is the entire mechanism.
SQUASH_BODY = """docs(handoff): two things at once (#1663)

* docs(handoff): the first commit on the branch

Claude-Session-Id: 11111111-1111-4111-8111-111111111111

* docs(handoff): the second commit on the branch

A paragraph of ordinary prose explaining the second commit, which is what
makes the message's FINAL block a non-trailer block.

Verified: the thing was checked and it held.
"""

#: The ordinary shape: one trailing block git's own parser handles fine.
PLAIN_BODY = """docs(handoff): an ordinary single commit (#1751)

Claude-Session-Id: 22222222-2222-4222-8222-222222222222
"""


def _sh(*args: str, cwd: Path) -> str:
    res = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                         env=hermetic_git_env(), timeout=60)
    assert res.returncode == 0, f"{args} failed: {res.stderr}"
    return res.stdout


@pytest.fixture()
def arc_repo(tmp_path: Path) -> Path:
    """A repo whose handoff doc has three commits: unstamped, squash, plain."""
    work = tmp_path / "arcwork"
    work.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=work)
    (work / "claudedocs").mkdir()
    doc = work / DOC

    doc.write_text("# fixture\n\nfirst\n", encoding="utf-8")
    _sh("git", "add", "--", DOC, cwd=work)
    # 🔴 UNSTAMPED ON PURPOSE — it is the ORIGINATING commit, and the whole
    # coverage gap this module reports is that such commits exist.
    _sh("git", "commit", "-q", "-m", "docs(handoff): new doc, no trailer", cwd=work)

    doc.write_text("# fixture\n\nsecond\n", encoding="utf-8")
    _sh("git", "add", "--", DOC, cwd=work)
    _sh("git", "commit", "-q", "-m", SQUASH_BODY, cwd=work)

    doc.write_text("# fixture\n\nthird\n", encoding="utf-8")
    _sh("git", "add", "--", DOC, cwd=work)
    _sh("git", "commit", "-q", "-m", PLAIN_BODY, cwd=work)
    return work


class TestTheTrailerParserTrap:
    """The control behind this module's central design decision."""

    def test_gits_own_trailer_parser_MISSES_what_this_reader_finds(self, arc_repo):
        """🔴 THE MATRIX, MEASURED IN ONE TEST: red for `%(trailers:…)`, green here.

        If this ever starts passing for BOTH readers, git's parser has changed
        and the module's docstring is overstating the hazard — but do not
        "simplify" the reader on that basis without re-running it against the
        real corpus, where the 33%-vs-55% gap was measured.
        """
        sha = _sh("git", "log", "--format=%H", "-1", "--skip=1", "--", DOC,
                  cwd=arc_repo).strip()

        via_parser = _sh("git", "log", "-1",
                         "--format=%(trailers:key=Claude-Session-Id,valueonly=true)",
                         sha, cwd=arc_repo).strip()
        body = _sh("git", "log", "-1", "--format=%B", sha, cwd=arc_repo)
        via_reader = ha.trailer_ids(body)

        assert via_parser == "", (
            "the control is wired to nothing: git's trailer parser was expected "
            "to MISS this squash-shaped body, but it returned "
            f"{via_parser!r}. Re-measure before trusting this module's premise.")
        assert via_reader == ("11111111-1111-4111-8111-111111111111",), (
            "the body reader failed on the exact shape it exists for")

    def test_the_parser_and_the_reader_AGREE_on_an_ordinary_commit(self, arc_repo):
        """The positive control: the trap is shape-specific, not universal.

        Without this, a reader that simply returned a hardcoded id would pass the
        test above.
        """
        sha = _sh("git", "log", "--format=%H", "-1", "--", DOC, cwd=arc_repo).strip()
        via_parser = _sh("git", "log", "-1",
                         "--format=%(trailers:key=Claude-Session-Id,valueonly=true)",
                         sha, cwd=arc_repo).strip()
        body = _sh("git", "log", "-1", "--format=%B", sha, cwd=arc_repo)
        assert via_parser == "22222222-2222-4222-8222-222222222222"
        assert ha.trailer_ids(body) == (via_parser,)


class TestTrailerIds:
    def test_deduplicates_within_one_body(self):
        assert ha.trailer_ids(SQUASH_BODY) == (
            "11111111-1111-4111-8111-111111111111",)

    def test_keeps_distinct_ids_in_first_appearance_order(self):
        # Real-shaped ids: `trailer_ids` validates on READ (a value here becomes
        # `claude --resume <value>` for someone to paste), so a placeholder like
        # `bbb` is dropped by design.
        b = "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb"
        a = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa"
        body = (f"subject\n\nClaude-Session-Id: {b}\n\n* other\n\n"
                f"Claude-Session-Id: {a}\n")
        assert ha.trailer_ids(body) == (b, a)

    def test_an_INDENTED_trailer_does_NOT_count(self):
        """Column 0 only — an indented line is a quotation, not a trailer.

        This is what stops a commit message that QUOTES a trailer (explaining
        this very mechanism, say) from minting a session id.
        """
        assert ha.trailer_ids("subject\n\n    Claude-Session-Id: nope\n") == ()

    def test_a_trailer_with_no_value_does_NOT_count(self):
        assert ha.trailer_ids("subject\n\nClaude-Session-Id:\n") == ()

    @pytest.mark.parametrize("body", ["", None])
    def test_empty_input_is_empty_output_not_a_crash(self, body):
        assert ha.trailer_ids(body) == ()


class TestDocCommits:
    def test_reads_every_commit_and_its_ids(self, arc_repo):
        commits = ha.doc_commits(str(arc_repo), DOC)
        assert len(commits) == 3
        assert commits[0].session_ids == ("22222222-2222-4222-8222-222222222222",)
        assert commits[1].session_ids == ("11111111-1111-4111-8111-111111111111",)
        assert commits[2].session_ids == ()          # the originating commit
        assert commits[2].stamped is False

    def test_a_body_with_blank_lines_does_not_split_a_record(self, arc_repo):
        """The squash body has blank lines and bullets; a naive line-split
        parser reports extra phantom commits. Pinned because that is exactly how
        the FIRST corpus measurement in this arc came out wrong.

        ⚠ THE COUNT ASSERTION IS THE ONE THAT CATCHES A PHANTOM RECORD, and an
        earlier version of this test asserted only the sha shape — a docstring
        claiming wider coverage than its body, which an audit caught. Both
        assertions are here now."""
        commits = ha.doc_commits(str(arc_repo), DOC)
        assert len(commits) == 3, (
            "a phantom record appeared: the squash body's blank lines or bullets "
            "were parsed as a record boundary")
        assert all(c.sha and len(c.sha) == 40 for c in commits)

    def test_an_unreadable_repo_RAISES_rather_than_returning_empty(self, tmp_path):
        with pytest.raises(ha.GitUnavailable):
            ha.doc_commits(str(tmp_path / "not-a-repo"), DOC)


class TestGenesisDiscriminator:
    def test_a_genesis_naming_the_doc_matches(self):
        g = ("/resume — continue the foo work. Canonical handoff (read first): "
             "~/workspace/devrc/claudedocs/handoff-arc-fixture.md")
        assert ha.genesis_names_doc(g, "handoff-arc-fixture.md") is True

    def test_a_genesis_that_merely_mentions_another_doc_does_NOT_match(self):
        g = "/resume — continue the bar work. claudedocs/handoff-something-else.md"
        assert ha.genesis_names_doc(g, "handoff-arc-fixture.md") is False

    def test_an_archived_path_still_matches(self):
        g = "see claudedocs/archive/handoff-arc-fixture.md for the history"
        assert ha.genesis_names_doc(g, "handoff-arc-fixture.md") is True

    @pytest.mark.parametrize("genesis,basename", [("", "x.md"), ("x", "")])
    def test_empty_operands_are_false_not_a_crash(self, genesis, basename):
        assert ha.genesis_names_doc(genesis, basename) is False


class TestDocBasename:
    @pytest.mark.parametrize("seed", [
        "handoff-arc-fixture",
        "handoff-arc-fixture.md",
        "claudedocs/handoff-arc-fixture.md",
        "/abs/path/claudedocs/handoff-arc-fixture.md",
        "arc-fixture",
    ])
    def test_every_seed_spelling_resolves_to_one_basename(self, seed):
        assert ha.doc_basename(seed) == "handoff-arc-fixture.md"

    def test_a_non_handoff_md_resolves_to_nothing(self):
        assert ha.doc_basename("README.md") == ""

    def test_empty_is_empty(self):
        assert ha.doc_basename("") == ""


class TestMembersAndMerge:
    def test_the_oldest_stamped_commit_is_the_originator(self, arc_repo):
        members = ha.writer_members(ha.doc_commits(str(arc_repo), DOC))
        assert [m.role for m in members] == [ha.ROLE_ORIGINATED, ha.ROLE_WROTE]
        assert members[0].session_id == "11111111-1111-4111-8111-111111111111"

    def test_reader_rows_are_filtered_by_GENESIS_not_by_mention(self):
        rows = [
            {"session_id": "reader-1", "genesis": "… handoff-arc-fixture.md …",
             "first": "2026-09-02T00:00:00Z", "cwd": "/x/devrc"},
            {"session_id": "noise-1", "genesis": "unrelated opening",
             "first": "2026-09-01T00:00:00Z", "cwd": "/x/devrc",
             "snippets": {"t": ("assistant", "handoff-arc-fixture.md")}},
        ]
        got = ha.reader_members(rows, "handoff-arc-fixture.md")
        assert [m.session_id for m in got] == ["reader-1"]

    def test_a_git_role_WINS_over_a_reader_role_for_the_same_session(self):
        w = [ha.ArcMember("s1", ha.ROLE_ORIGINATED, first_seen="2026-09-01T00:00:00Z")]
        r = [ha.ArcMember("s1", ha.ROLE_RESUMED, first_seen="2026-09-05T00:00:00Z")]
        merged = ha.merge_members(w, r)
        assert len(merged) == 1
        assert merged[0].role == ha.ROLE_ORIGINATED

    def test_members_are_ordered_by_first_seen(self):
        a = [ha.ArcMember("late", ha.ROLE_WROTE, first_seen="2026-09-09T00:00:00Z")]
        b = [ha.ArcMember("early", ha.ROLE_RESUMED, first_seen="2026-09-01T00:00:00Z")]
        assert [m.session_id for m in ha.merge_members(a, b)] == ["early", "late"]

    def test_an_unmeasured_timestamp_sorts_LAST_not_first(self):
        """An empty string sorts before every date; a session whose time was not
        measured would otherwise be presented as the head of the chain — i.e. as
        the originator."""
        a = [ha.ArcMember("dated", ha.ROLE_WROTE, first_seen="2026-09-09T00:00:00Z")]
        b = [ha.ArcMember("undated", ha.ROLE_RESUMED, first_seen="")]
        assert [m.session_id for m in ha.merge_members(a, b)] == ["dated", "undated"]


class TestCoverageIsNeverSilent:
    def test_the_coverage_line_is_printed_even_when_nothing_is_missing(self):
        r = ha.ArcReport(total_commits=7, unstamped_commits=0)
        line = ha.coverage_line(r)
        assert "0 of 7" in line
        assert line.strip(), "a zero must still produce a sentence"

    def test_the_coverage_line_is_printed_for_an_empty_corpus(self):
        assert "0 of 0" in ha.coverage_line(ha.ArcReport())

    def test_resolve_arc_counts_the_unstamped_commit(self, arc_repo):
        rep = ha.resolve_arc(str(arc_repo), DOC)
        assert rep.total_commits == 3
        assert rep.unstamped_commits == 1
        assert rep.stamped_commits == 2
        assert "1 of 3" in ha.coverage_line(rep)

    def test_an_unwalked_transcript_corpus_is_reported_as_UNMEASURED(self, arc_repo):
        rep = ha.resolve_arc(str(arc_repo), DOC, readers_measured=False)
        assert rep.readers_measured is False
        assert any("NOT walked" in n for n in rep.unmeasured_notes)

    def test_a_walked_corpus_adds_no_unmeasured_note(self, arc_repo):
        rep = ha.resolve_arc(str(arc_repo), DOC, reader_rows=[],
                             readers_measured=True)
        assert not any("NOT walked" in n for n in rep.unmeasured_notes)

    def test_git_failing_is_a_NOTE_not_a_silent_empty_arc(self, tmp_path):
        rep = ha.resolve_arc(str(tmp_path / "nope"), DOC, readers_measured=True)
        assert rep.members == []
        assert any("NOT measured" in n for n in rep.unmeasured_notes), (
            "an unreadable repo produced an empty arc with no note — which is "
            "indistinguishable from a doc nobody ever committed")


class TestNoTranscriptPathsLeak:
    def test_an_arc_member_carries_no_path_field(self):
        """devrc is PUBLIC and a transcript path names the client repo it sits
        under (`-home-zach-workspace-civit-…`). The member record has ids and a
        repo LABEL by construction, so there is no path for a renderer to leak.
        """
        m = ha.ArcMember("s1", ha.ROLE_WROTE, repo="devrc")
        assert not hasattr(m, "path")
        # (the `"/" not in m.repo` assertion that used to sit here was removed:
        #  it asserted this test's OWN literal. The real behaviour is covered by
        #  `test_reader_members_reduce_a_cwd_to_its_basename`.)

    def test_reader_members_reduce_a_cwd_to_its_basename(self):
        rows = [{"session_id": "s1", "genesis": "handoff-arc-fixture.md",
                 "first": "2026-09-01T00:00:00Z",
                 "cwd": "/home/zach/workspace/civit/datapacket-talos"}]
        got = ha.reader_members(rows, "handoff-arc-fixture.md")
        assert got[0].repo == "datapacket-talos"
        assert "/home/zach" not in got[0].repo


# =========================================================================== #
# THE CLI LAYER — the flag, the annotation, and the window exemption.
# =========================================================================== #
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fs_arc", str(Path(__file__).resolve().parents[1] / "find-session.py"))
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

# 🔴 `fs.handoff_arc` IS NOT `ha`. `find-session.py` does `import handoff_arc`
# off `scripts/lib` on `sys.path`; this file does `from lib import handoff_arc`.
# Two module objects, two copies of every attribute — so a `monkeypatch.setattr`
# on one leaves the other untouched. Every patch below names both.
_ARC_MODULES = (ha, fs.handoff_arc)


@pytest.fixture(autouse=True)
def _no_ambient_checkout_walk(monkeypatch, tmp_path_factory):
    """🔴 GUARD: NO TEST IN THIS FILE MAY WALK THE OPERATOR'S REAL CHECKOUTS.

    `render_arc` COMPUTES `arc_cross_docs(report)` from the ambient
    `os.environ` when `cross` is not supplied — deliberately, so a caller
    cannot ship an arc without its footer. The cost landed on SEVEN
    pre-existing `render_arc(report)` call sites here that patch no env:
    MEASURED on this host at load ~5, the two find-session arc files went
    **153 passed in 3.45s** at `3e7725bc` to **183 passed in 30.19s** at
    `14c4cadc`, with those sites the six slowest tests in the run. Two
    separate defects, not one: the tests stopped being hermetic (their result
    depended on four real checkouts' CONTENTS), and one of those handles is a
    CLIENT repo while this repo is PUBLIC.

    🔴 AND THE `nix build` SANDBOX TIER IS STRUCTURALLY BLIND TO IT — the
    handles are unset there, so every handle short-circuits and the walk never
    happens. A CI green could never have caught this; only a dev-host run can.

    So: the handles are deleted for every test (a test that wants them points
    them at a tmp repo itself, via `_set_handles`, which runs later and wins),
    AND `_git` refuses a repo outside this session's tmp root. The second half
    is the LOUD one: unsetting alone makes an ambient walk cheap and silent,
    which is how the next one would be reintroduced.

    ⚠ An injected `run` is allowed through — it reaches no disk, and the
    `GitUnavailable` tests depend on it.

    ⚠ FILE-SCOPED, NOT SUITE-WIDE, AND THAT IS A DELIBERATE STOPPING POINT.
    The right home for this is a sixth `testlib/*_plugin.py` registered by
    `scripts/run-tests.sh` for every target, the way GUARDs 7-10 are — reading
    a real checkout is the same class of hazard as writing one. That means
    touching the runner's guard ledger and its two-way pins, which is a wider
    blast radius than the seven sites it would protect and belongs in its own
    change with its own controls. Filed rather than smuggled in here.
    """
    for handle in hi.REPO_ENV_HANDLES:
        monkeypatch.delenv(handle, raising=False)
    tmp_root = str(Path(tmp_path_factory.getbasetemp()).resolve())

    def _guarded(real):
        def guarded(repo, args, run=None):
            if run is None:
                p = Path(str(repo))
                resolved = str(p.resolve())
                # ⚠ A path that does not EXIST is let through: git exits 128 and
                # the test reads nothing. Several tests pass
                # `/nonexistent/repo` on purpose to produce `GitUnavailable`,
                # which is hermetic.
                if p.exists() and not (resolved == tmp_root
                                       or resolved.startswith(tmp_root + "/")):
                    raise AssertionError(
                        "a test reached a git checkout OUTSIDE this session's "
                        f"tmp root: {resolved!r}. That is not hermetic — its "
                        "result depends on a real repo's contents, one of the "
                        "handles is a CLIENT repo, and the nix-build tier "
                        "cannot see it because the handles are unset there. "
                        "Pass an explicit `cross=` (or `env=`) rather than "
                        "letting `render_arc` read `os.environ`.")
            return real(repo, args, run=run)
        return guarded

    for mod in _ARC_MODULES:
        monkeypatch.setattr(mod, "_git", _guarded(mod._git))


def _hermetic_cross(report):
    """`arc_cross_docs` over NO handles — the footer without a git walk.

    🔴 EXPLICIT AT THE CALL SITE, not left to the autouse guard above. A test
    whose subject is the extractor line or an ANSI escape has no business
    deciding what the cross-arc footer says, and `render_arc(report)` with no
    `cross` is the spelling that reads as "I do not care" while meaning "read
    four of the operator's checkouts".
    """
    return fs.arc_cross_docs(report, env={})


class TestArcIsWindowExempt:
    def test_arc_lifts_the_default_window(self):
        """🔴 An arc spans the whole effort — days to weeks, and one measured doc
        has 64 commits. Under the 12-day default the chain would be truncated to
        its recent tail and PRESENTED AS THE WHOLE THING."""
        a = fs.parse_args(["--arc", "handoff-x"])
        since, source = fs.resolve_window(a)
        assert since is None
        assert source == fs.WINDOW_ARC_EXEMPT

    def test_an_EXPLICIT_since_still_wins_over_the_exemption(self):
        """The exemption removes a default nobody asked for; it does not
        override an instruction somebody gave. Same rule as `--skill`."""
        a = fs.parse_args(["--arc", "handoff-x", "--since", "2026-01-01"])
        since, source = fs.resolve_window(a)
        assert since is not None
        assert source == fs.WINDOW_EXPLICIT


class TestArcIsInTheArchiveOnlyLedger:
    def test_arc_is_declared_archive_only(self):
        assert "arc" in {dest for dest, _, _ in fs.ARCHIVE_ONLY_FLAGS}

    def test_every_parser_dest_is_classified(self):
        """The two-way partition the ledger's own comment promises — adding a
        flag without deciding which half it is in must fail."""
        classified = ({d for d, _, _ in fs.ARCHIVE_ONLY_FLAGS}
                      | set(fs.LIVE_AWARE_DESTS))
        assert fs.parser_dests() <= classified


class TestTheAnnotation:
    def test_a_hit_whose_GENESIS_names_a_doc_is_annotated(self):
        r = {"genesis": "/resume — continue the x work. Canonical handoff "
                        "(read first): ~/w/devrc/claudedocs/handoff-x-y.md"}
        note = fs.arc_annotation(r)
        assert note is not None
        assert "handoff-x-y.md" in note
        assert "--arc handoff-x-y.md" in note

    def test_a_hit_that_only_MENTIONS_a_doc_is_NOT_annotated(self):
        """45 of 48 hits in the measured case are this. Annotating them would
        make the signal unreadable."""
        r = {"genesis": "do the unrelated thing",
             "snippets": {"t": ("assistant", "claudedocs/handoff-x-y.md")}}
        assert fs.arc_annotation(r) is None

    def test_an_UNMEASURED_count_prints_NO_count_rather_than_zero(self):
        """🔴 `(0 sessions)` would read as 'this arc is empty' for a doc with a
        dozen members. An absent count must be absent, not zero."""
        r = {"genesis": "… claudedocs/handoff-x-y.md …"}
        assert "sessions" not in fs.arc_annotation(r, arc_counts={})
        # `3+`, not `3` — the count is a FLOOR from git trailers alone; see
        # `TestTheAnnotationCountIsAFloor`.
        assert "(3+ sessions)" in fs.arc_annotation(
            r, arc_counts={"handoff-x-y.md": 3})

    def test_the_annotator_and_the_resolver_agree_about_what_names_a_doc(self):
        """One predicate, one place: the CLI delegates to `handoff_arc` rather
        than carrying a second copy of the pattern."""
        g = "opened with claudedocs/archive/handoff-z.md today"
        assert fs._arc_doc_in_genesis(g) == ha.doc_in_text(g) == "handoff-z.md"


class TestArcSeedResolution:
    def test_a_slug_resolves_directly(self):
        assert fs.arc_seed_to_doc("handoff-x") == "handoff-x.md"

    def test_a_non_uuid_non_doc_seed_resolves_to_nothing(self):
        assert fs.arc_seed_to_doc("README.md") == ""

    def test_a_uuid_seed_reads_the_sessions_OPENING_message(self, tmp_path):
        """The indirection the flag exists for: the operator starts from a
        session they found, not from a doc they already know."""
        proj = tmp_path / "-home-zach-workspace-devrc"
        proj.mkdir()
        sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        (proj / f"{sid}.jsonl").write_text(
            '{"type":"user","message":{"content":"/resume — Canonical handoff '
            '(read first): ~/w/devrc/claudedocs/handoff-seeded.md"}}\n',
            encoding="utf-8")
        assert fs.arc_seed_to_doc(sid, root=tmp_path) == "handoff-seeded.md"

    def test_a_uuid_with_no_transcript_resolves_to_nothing_not_a_crash(self, tmp_path):
        assert fs.arc_seed_to_doc("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                  root=tmp_path) == ""


# =========================================================================== #
# ROUND-0 AUDIT FIXES — each pins a defect the audit found, not a new feature.
# =========================================================================== #
class TestTheOriginatedLabelDoesNotOverclaim:
    """🔴 `originated` is an INFERENCE from commit order and is unsound exactly
    when a writer is missing — which is the COMMON case (45% of corpus doc
    commits are unstamped), not a corner."""

    def test_with_an_unstamped_commit_the_label_is_DEMOTED(self, arc_repo):
        rep = ha.resolve_arc(str(arc_repo), DOC, readers_measured=True)
        assert rep.unstamped_commits == 1
        roles = [m.role for m in rep.members]
        assert ha.ROLE_ORIGINATED not in roles, (
            "the arc claims to know who ORIGINATED it while also reporting that "
            "a writer is invisible — those cannot both be asserted")
        assert ha.ROLE_EARLIEST_STAMPED in roles

    def test_with_FULL_coverage_the_label_stands(self, tmp_path):
        """The negative control: demotion must be caused by the missing writer,
        not applied unconditionally. Without this, deleting the whole inference
        would pass the test above."""
        work = tmp_path / "full"
        work.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=work)
        (work / "claudedocs").mkdir()
        (work / DOC).write_text("a\n", encoding="utf-8")
        _sh("git", "add", "--", DOC, cwd=work)
        _sh("git", "commit", "-q", "-m", PLAIN_BODY, cwd=work)
        rep = ha.resolve_arc(str(work), DOC, readers_measured=True)
        assert rep.unstamped_commits == 0
        assert [m.role for m in rep.members] == [ha.ROLE_ORIGINATED]


class TestTheAnnotationCountIsAFloor:
    def test_the_count_renders_with_a_PLUS(self):
        """🔴 It comes from git trailers alone — a strict SUBSET of the arc. A
        bare `(3 sessions)` would be a precise-looking undercount of exactly the
        kind the coverage line exists to refuse."""
        r = {"genesis": "… claudedocs/handoff-x-y.md …"}
        note = fs.arc_annotation(r, {"handoff-x-y.md": 3})
        assert "(3+ sessions)" in note

    def test_writer_counts_walk_each_DISTINCT_doc_once(self):
        """A result set of N hits naming one doc costs ONE git walk, not N."""
        calls = []

        def lookup(basename):
            calls.append(basename)
            return None, None

        rows = [{"genesis": "claudedocs/handoff-same.md"} for _ in range(5)]
        fs.arc_writer_counts(rows, repo_lookup=lookup)
        assert calls == ["handoff-same.md"]

    def test_a_doc_resolving_to_no_repo_gets_NO_count_not_a_zero(self):
        counts = fs.arc_writer_counts(
            [{"genesis": "claudedocs/handoff-absent.md"}],
            repo_lookup=lambda b: (None, None))
        assert "handoff-absent.md" not in counts
        note = fs.arc_annotation({"genesis": "claudedocs/handoff-absent.md"},
                                 counts)
        assert "sessions" not in note


class TestTheExcludedCorpusIsNamed:
    """🔴 THE EARLIER VERSION OF THIS TEST WAS VACUOUS AND AN AUDIT PROVED IT BY
    MUTATION. It appended the note to the report ITSELF and then asserted the note
    was there — never calling `run_arc`. Deleting the production append left the
    whole module green. It now drives `run_arc` and reads its real stdout."""

    def test_run_arc_PRINTS_that_the_opencode_corpus_was_not_searched(
            self, arc_repo, monkeypatch, capsys):
        monkeypatch.setattr(fs, "arc_repo_for",
                            lambda basename: (str(arc_repo), DOC))
        monkeypatch.setattr(fs, "archive_search", lambda a, since: [])
        a = fs.parse_args(["--arc", "handoff-arc-fixture"])
        a.arc = "handoff-arc-fixture.md"
        assert fs.run_arc(a) == fs.EXIT_OK
        out = capsys.readouterr().out
        assert "opencode corpus was NOT searched" in out, (
            "run_arc did not disclose the corpus it skipped; a chain that omits "
            "a whole runtime silently reads as complete")


class TestSessionGenesisUsesTheSharedWalk:
    def test_a_SUBAGENT_transcript_does_not_resolve(self, tmp_path):
        """🔴 The private glob returned one; `find_transcript` applies
        `is_corpus_member`, so it does not. A subagent is not a resumable
        session, and this is the behaviour difference that made deleting the
        glob a fix rather than a refactor."""
        sub = tmp_path / "-proj" / "subagents"
        sub.mkdir(parents=True)
        sid = "cccccccc-dddd-4eee-8fff-999999999999"
        (sub / f"{sid}.jsonl").write_text(
            '{"type":"user","message":{"content":"claudedocs/handoff-sub.md"}}\n',
            encoding="utf-8")
        assert fs.session_genesis(sid, root=tmp_path) == ""


class TestTheAnnotationIsWIREDIntoTheClassicPath:
    """🔴 `arc_annotation` WAS TESTED AS A PURE FUNCTION AND WIRED UP BY NOTHING.
    An audit replaced the call site with `note = None` and the suite stayed fully
    green — deleting "the half that fixes the reported pain" was invisible. A
    function tested in isolation is not a feature; this drives `main`."""

    def _run(self, monkeypatch, capsys, rows):
        monkeypatch.setattr(fs, "archive_search", lambda a, since: rows)
        monkeypatch.setattr(fs, "arc_writer_counts",
                            lambda shown, repo_lookup=None: {})
        rc = fs.main(["handoff-wired"])
        return rc, capsys.readouterr().out

    def _row(self, genesis):
        return {"session_id": "s1", "cwd": "/x/devrc", "project_dir": "devrc",
                "branch": "main", "first": "2026-09-01T00:00:00Z",
                "last": "2026-09-01T01:00:00Z", "genesis": genesis,
                "matched_terms": ["handoff-wired"], "total_hits": 1,
                "snippets": {}, "path": "/x/s1.jsonl"}

    def test_an_ordinary_hit_naming_a_doc_IS_annotated_in_real_output(
            self, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys,
                            [self._row("/resume — claudedocs/handoff-wired.md")])
        assert rc == fs.EXIT_OK
        assert "arc: handoff-wired.md" in out
        assert "--arc handoff-wired.md" in out, (
            "the annotation printed no pasteable command — that command IS the "
            "feature; the count is decoration")

    def test_a_hit_naming_NO_doc_is_NOT_annotated(self, monkeypatch, capsys):
        """The negative control. Without it, an annotator that printed on every
        row would pass the test above."""
        rc, out = self._run(monkeypatch, capsys, [self._row("just some text")])
        assert rc == fs.EXIT_OK
        assert "arc: " not in out


class TestTheMeasuredZeroIsDistinguishable:
    def test_zero_stamped_writers_reads_differently_from_UNMEASURED(self):
        """🔴 `n == 0` means the history WAS read and holds no ids; `n is None`
        means nothing was looked at. Rendering both as "" made a measured reading
        byte-identical to an absent one."""
        r = {"genesis": "claudedocs/handoff-z.md"}
        measured = fs.arc_annotation(r, {"handoff-z.md": 0})
        unmeasured = fs.arc_annotation(r, {})
        assert measured != unmeasured
        assert "0 stamped writers" in measured
        assert "sessions" not in unmeasured


class TestGitIsCalledWithoutAmbientOverrides:
    def test_GIT_DIR_in_the_environment_cannot_redirect_the_walk(
            self, arc_repo, monkeypatch, tmp_path):
        """🔴 `git -C <path>` DOES NOT override `$GIT_DIR`. MEASURED before the
        fix: with GIT_DIR exported the arc printed `0 of 0 commit(s)` at exit 0 —
        a confident empty writer set, which is exactly the conflation this
        module's GitUnavailable docstring claims the design prevents."""
        other = tmp_path / "other"
        other.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=other)
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(other))
        rep = ha.resolve_arc(str(arc_repo), DOC, readers_measured=True)
        assert rep.total_commits == 3, (
            "an ambient GIT_DIR redirected the walk and the arc rendered as "
            "measured-empty")

    def test_the_override_ledger_is_scrubbed_from_the_child_env(self, monkeypatch):
        """🔴 THE NAMES ARE LITERAL HERE, NOT READ BACK FROM THE LEDGER.

        An earlier version built its environment BY ITERATING
        `_GIT_ENV_OVERRIDES` and then asserted none of `_GIT_ENV_OVERRIDES`
        survived — which is true of ANY ledger, an empty one included. Measured:
        emptying the ledger left that version GREEN while the behavioural test
        went red. It read as coverage of five names and covered none.
        """
        names = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")
        for name in names:
            monkeypatch.setenv(name, "/nonexistent/decoy")
        scrubbed = ha._git_env()
        for name in names:
            assert name not in scrubbed, f"{name} reached the child environment"
        assert "PATH" in scrubbed, "the scrub removed more than the git overrides"



class TestTrailerValuesAreValidatedOnREAD:
    """🔴 SAFETY ONLY — and an earlier revision of these tests pinned SHAPE.

    That revision asserted a UUID-or-`ses_` filter, which contradicts an explicit
    🔴 in `session_trailer.py` ("DO NOT ASSUME UUID SHAPE … never shape-checked")
    and made the reader STRICTER than the writer, so a legitimately-stamped commit
    was re-reported as carrying no session id. The hazard it was reaching for is
    real but lives at RENDER; see the quoting test below.
    """

    @pytest.mark.parametrize("bad", ["a" * 5000, "x\x00y", "a\x1bb", "a\x07b"])
    def test_an_UNSAFE_value_is_DROPPED(self, bad):
        r"""Length and control characters — exactly what the WRITER refuses.

        ⚠ `\r`, `\n` and `\t` are NOT usable here: `_TRAILER_RE`'s `(\S+)` can
        never capture a value containing them, so such a param passes with the
        filter entirely DELETED. An earlier revision used a space for this reason
        and an audit caught it; the replacement used a tab, which has the same
        defect. ESC and BEL are reachable AND dangerous — they are the ones that
        reach a terminal.
        """
        assert ha.trailer_ids(f"subject\n\n{ha.TRAILER_KEY}: {bad}\n") == ()

    @pytest.mark.parametrize("bad", ["a\tb", "a\rb", "a\nb"])
    def test_a_control_char_the_REGEX_cannot_carry_is_still_refused(self, bad):
        """Tested against the PREDICATE directly, because the parser can never
        deliver these — asserting them through `trailer_ids` would pass with no
        predicate at all."""
        assert ha._is_safe_id(bad) is False

    def test_the_predicate_IS_the_writers_not_a_copy_of_it(self):
        r"""🔴 THE REGRESSION GUARD FOR A DUPLICATED PREDICATE THAT DIVERGED.

        An earlier revision re-spelled the check as four characters and claimed
        in four places that it was the writer's. It was looser: `valid_id`
        refuses every C0 control, the copy refused `\r\n\t\x00` — and three of
        those four are unreachable through the trailer regex. Of the **24**
        control characters that ARE reachable through `(\S+)` (`0x00-0x08`,
        `0x0e-0x1b`, `0x7f`), the copy checked exactly **one** — NUL — and missed
        23, so an ANSI escape from any commit body reached the terminal raw.

        ⚠ This docstring is the TWIN of the comment at `handoff_arc.py`'s
        `_UNSAFE_CHARS` note, and it carried the same false "every reachable
        control character" claim for one round AFTER that one was corrected: the
        fix landed at one site and not the other. `grep -n "every REACHABLE
        control"` returns ZERO here because the phrase is line-wrapped — a
        single-line grep over wrapped prose is a confident false zero, and
        `git log -S` is what found it.
        """
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import session_trailer as st
        for value in ("6b88ffe8-ec33-4662-b169-a42e8008a69a", "ses_abc123",
                      "fable_x", "x;rm", "a\x1bb", "a\x07b", "a\x00b",
                      "a" * 300, "", "a\tb"):
            assert ha._is_safe_id(value) == bool(st.valid_id(value)), (
                f"reader and writer disagree about {value!r}")

    def test_an_ANSI_escape_never_reaches_the_RENDERED_arc(self, arc_repo):
        """🔴 THIS ONE ACTUALLY RENDERS. An earlier version requested `arc_repo`,
        `monkeypatch` and `capsys`, used NONE of them, and asserted only
        `trailer_ids(...) == ()` — a duplicate of the parametrised ESC case
        wearing an end-to-end docstring. It read as coverage of the render path
        and provided none.

        The render path is the one that matters here: `shlex.quote` does NOT
        neutralise an escape, because an escape inside quotes still executes when
        written to a tty. So the guarantee is that no escape survives to be
        rendered at all.
        """
        hostile = "\x1b[2J\x1b]0;PWNED\x07evil"
        doc = arc_repo / DOC
        doc.write_text("# fixture\n\nhostile\n", encoding="utf-8")
        _sh("git", "add", "--", DOC, cwd=arc_repo)
        _sh("git", "commit", "-q", "-m",
            f"docs(handoff): hostile\n\n{ha.TRAILER_KEY}: {hostile}\n", cwd=arc_repo)

        report = ha.resolve_arc(str(arc_repo), DOC, readers_measured=True)
        rendered = fs.render_arc(report, cross=_hermetic_cross(report))
        assert "\x1b" not in rendered and "\x07" not in rendered, (
            "an escape from a commit body reached the rendered arc")
        assert "PWNED" not in rendered
        for m in report.members:
            assert "\x1b" not in m.resume_command()

    def test_a_real_uuid_still_parses(self):
        sid = "6b88ffe8-ec33-4662-b169-a42e8008a69a"
        assert ha.trailer_ids(f"s\n\n{ha.TRAILER_KEY}: {sid}\n") == (sid,)

    def test_an_opencode_session_id_still_parses(self):
        sid = "ses_abc123"
        assert ha.trailer_ids(f"s\n\n{ha.TRAILER_KEY}: {sid}\n") == (sid,)

    def test_an_UNKNOWN_SHAPE_id_is_KEPT(self):
        """🔴 THE REGRESSION GUARD FOR THE FIX THAT WAS ITSELF WRONG. A future
        runtime's id must survive the read, or this tool silently reports its
        commits as unstamped — the measured failure `cairn_who.py` records."""
        sid = "fable_2026_09_18_abcdef"
        assert ha.trailer_ids(f"s\n\n{ha.TRAILER_KEY}: {sid}\n") == (sid,)

    def test_the_READ_side_is_no_stricter_than_the_WRITE_side(self):
        """Asserted against the writer's OWN predicate rather than restated —
        the symmetry an earlier comment claimed falsely."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import session_trailer as st
        for sid in ("6b88ffe8-ec33-4662-b169-a42e8008a69a", "ses_abc123",
                    "fable_2026_09_18_abcdef", "x;rm"):
            assert st.valid_id(sid) is True
            assert ha.trailer_ids(f"s\n\n{ha.TRAILER_KEY}: {sid}\n") == (sid,), (
                f"the writer accepts {sid!r} but the reader refuses it")

    def test_a_hostile_id_is_QUOTED_where_it_becomes_a_command(self):
        """The paste hazard, handled at the layer where it exists."""
        cmd = ha.ArcMember("x;rm -rf /", ha.ROLE_WROTE).resume_command()
        assert cmd != "claude --resume x;rm -rf /"
        assert "'" in cmd, f"an odd id reached a pasteable command unquoted: {cmd}"


def _extractor_path():
    """`EXTRACTOR_REL` resolved against the repo root.

    ⚠ Two call sites used to spell this `fs.EXTRACTOR_REL.split("scripts/", 1)[1]`
    joined onto `parents[1]`, which raises a bare `IndexError` naming the idiom
    rather than the problem if the constant ever stops containing `scripts/`. The
    constant is repo-relative, so the repo root is the direct base.
    """
    return Path(__file__).resolve().parents[2] / fs.EXTRACTOR_REL


def _load_extractor():
    """Import `extract_user_msgs.py` the way its own `--arc` path is reached.

    Import-time effects are limited to a `sys.path.insert` that `find-session.py`
    (already exec'd above) performs identically — no argparse, no writes, no
    network — so this is order-independent under `--dist loadfile`.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("eum_seam", str(_extractor_path()))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTheArcNamesTheExtractor:
    """🔴 THE ROUTING GUARDS FOR THE SURFACE WITH THE WIDER MEASURED REACH.

    #1870 shipped `extract_user_msgs.py --arc` and routed it from a "Load when" row
    in `/resume`'s SKILL.md, readable only where that skill's body loads. Measured
    over the **6** sessions that asked the operator's standing end-of-arc question
    after #1870 merged: `--arc` ran in **6 of 6**, the reference file was read in
    **3 of 6**, and two sessions re-found the script with
    `find $DEVRC/scripts -name 'extract_user_msgs*'`. These guards pin the footer
    that goes where all six already looked. ⚠ n=6, a COUNT and not a rate.

    ⚠ This docstring said "3 of 3 / 1 of 3" and named an end-of-arc MECHANISM. The
    counts were stale within minutes and the mechanism was refuted; the retraction
    lives once, in `find_session.extractor_next_command`, and is deliberately not
    restated here.

    🔴 THE SEAM GUARD IS THE LOAD-BEARING ONE. A footer that renders perfectly
    while naming a path that does not exist, or a seed the extractor rejects, is
    inert in exactly the way a rendering test cannot see — the "verified in
    isolation" hazard, two components each fine and the seam broken. ⚠ There is
    **ONE** such guard, and this docstring claimed TWO: the seed test asserted
    `arc_seed_to_doc(doc) == doc` without importing the extractor at all, so it
    could not see the extractor stopping to call it. That was a description wider
    than its implementation — the `guards-narrower` shape — found by round 0 of the
    audit ladder. It now imports `extract_user_msgs` and crosses the seam for real.
    """

    @staticmethod
    def _report(n_members=2, doc="handoff-arc-fixture.md"):
        r = ha.ArcReport(doc=doc, repo="devrc", total_commits=3,
                         unstamped_commits=0)
        r.members = [ha.ArcMember(f"{i}" * 8 + "-1111-4111-8111-111111111111",
                                  ha.ROLE_WROTE)
                     for i in range(1, n_members + 1)]
        return r

    def test_a_resolved_arc_NAMES_the_extractor_command(self):
        report = self._report()
        rendered = fs.render_arc(report, cross=_hermetic_cross(report))
        assert fs.EXTRACTOR_REL in rendered
        assert "--arc handoff-arc-fixture.md" in rendered

    def test_the_command_carries_the_RESOLVED_doc_not_the_users_seed(self):
        """The seed may be a slug, a path, or a SESSION ID; only `report.doc` is
        the basename that resolved. Echoing the seed back would print a command
        whose `--arc` re-runs the session-id indirection for no reason, and would
        be flatly wrong for a seed that resolved via a genesis message."""
        report = self._report(doc="handoff-real-slug.md")
        rendered = fs.render_arc(report, cross=_hermetic_cross(report))
        assert "--arc handoff-real-slug.md" in rendered

    def test_a_MEASURED_EMPTY_arc_names_NO_command(self):
        """🔴 NOT COSMETIC. The extractor exits non-zero on an arc that resolved
        with no members, so a command printed here is an invitation that cannot
        answer — the reassuring-command shape the coverage line exists to refuse.
        Asserted on the WHOLE rendering, not just the absence of a heading: a
        partial line still tells an agent the tool applies."""
        empty = ha.ArcReport(doc="handoff-arc-fixture.md", repo="devrc")
        rendered = fs.render_arc(empty, cross=_hermetic_cross(empty))
        assert fs.extractor_next_command(empty) is None
        assert fs.EXTRACTOR_REL not in rendered
        assert "extract_user_msgs" not in rendered
        assert "NEXT" not in rendered

    def test_the_member_COUNT_is_the_real_one_and_singular_reads_right(self):
        """Pins the count against `len(members)` rather than a constant, and uses
        1 and 3 — never 2 alone, which cannot see a hardcoded plural."""
        r1, r3 = self._report(1), self._report(3)
        assert "1 session of this arc" in fs.render_arc(r1, cross=_hermetic_cross(r1))
        assert "3 sessions of this arc" in fs.render_arc(r3, cross=_hermetic_cross(r3))

    def test_the_command_sits_BELOW_the_coverage_line(self):
        """The gaps qualify the chain the command extracts from. An agent that
        reads the command first and stops has skipped them, so ordering is a
        behavioural claim, not layout."""
        report = self._report()
        rendered = fs.render_arc(report, cross=_hermetic_cross(report))
        assert rendered.index("carry no session id") < rendered.index("NEXT —")

    # ---------------- the seam: the printed line must actually work ----------- #

    def test_the_NAMED_PATH_EXISTS_in_this_repo(self):
        """🔴 SEAM GUARD. `EXTRACTOR_REL` is a string; nothing else in this module
        would notice the script being renamed or moved, and the footer would keep
        printing a confident path to a file that is not there."""
        target = _extractor_path()
        assert target.is_file(), (
            f"the arc footer names {fs.EXTRACTOR_REL}, which does not exist")

    def test_the_printed_SEED_is_one_the_EXTRACTOR_ACCEPTS(self):
        """🔴 SEAM GUARD. It pins a RELATIONSHIP — that the extractor ROUTES its
        `--arc` seed through `find_session.arc_seed_to_doc` — not a word, and not
        either component on its own.

        ⚠ TWO EARLIER VERSIONS WERE BOTH WRONG, each written while fixing the one
        before it. Recorded so a third is not derived.

        v1 asserted `fs.arc_seed_to_doc(doc) == doc` and never imported the
        extractor: a unit test of `find-session` alone, blind to the seam.

        v2 imported the extractor and added `assert "arc_seed_to_doc" in body` — a
        SPELLED guard, and the hazard walked straight past it. MEASURED (battery
        `X1`): replace the extractor's call with `handoff_arc.doc_basename(seed)`
        and leave `# was fs.arc_seed_to_doc(...)` above it, and v2 stays GREEN with
        the seam broken — after which `--arc <session-uuid>` resolves in
        `find-session` and exits 3 in the extractor, because the UUID branch lives
        only in `arc_seed_to_doc`. v2's docstring also claimed the resolver was
        asserted "by IDENTITY", which was false about the objects:
        `_load_find_session()` returns a THIRD module instance, so `their_fs is fs`
        is False and no `is` check was ever present. `claude/RULES.md` — "a guard
        can be SPELLED rather than STRUCTURAL: assert the STATE, never a word
        another feature can spell."

        v3 injects a recording stub through `arc_sessions`' own `find_session`
        parameter and asserts the call HAPPENED, carrying the seed. A rewrite that
        stops calling the resolver records no call and fails here. The behavioural
        half is kept too, because a relationship check alone would happily pass a
        resolver that rejects every seed the footer prints.
        """
        eum = _load_extractor()

        # --- the RELATIONSHIP: the extractor's seed MUST reach this resolver ---
        class _Reached(Exception):
            """Raised just past the seam, so the case needs no repo and no git."""

        calls = []

        class _RecordingFS:
            # The REAL class, so the extractor's `except fs.ArcUnmeasured` is valid.
            ArcUnmeasured = fs.ArcUnmeasured
            # 🔴 `handoff_arc` IS PRESENT SO THE MUTANT REACHES THE ASSERTION BELOW
            # RATHER THAN DYING ON AN AttributeError. Without it, mutant `X1` —
            # which rewrites the seam to `fs.handoff_arc.doc_basename(seed)` — blew
            # up in the extractor at `extract_user_msgs.py:269` and the guard went
            # red for a reason that says nothing about the seam. MEASURED: with
            # `assert calls == [seed]` DELETED, X1 was still red, so the battery
            # reported a kill for an assertion that was not there. That is the
            # "still red with your guard deleted" shape — `claude/RULES.md`,
            # unreachable-guards. The extractor's own suite's fake carries this
            # attribute for the same reason.
            handoff_arc = fs.handoff_arc
            ROOT = None

            @staticmethod
            def arc_seed_to_doc(seed, root=None):
                calls.append(seed)
                return "handoff-arc-fixture.md"

            @staticmethod
            def arc_report(basename):
                raise _Reached(basename)

        seed = "a-seed-only-the-resolver-can-map"
        with pytest.raises(_Reached) as caught:
            eum.arc_sessions(seed, find_session=_RecordingFS())
        assert calls == [seed], (
            "the extractor did NOT route its --arc seed through "
            "`arc_seed_to_doc`, so every footer this module prints hands its seed "
            f"to a resolver the extractor no longer calls (calls={calls!r})")
        assert str(caught.value) == "handoff-arc-fixture.md", (
            "the basename the resolver returned did not reach `arc_report`")

        # --- and BEHAVIOURALLY: that resolver accepts what the footer prints ---
        doc = "handoff-arc-fixture.md"
        assert eum._load_find_session().arc_seed_to_doc(doc) == doc, (
            f"the extractor's own resolver rejects {doc!r}, which is exactly what "
            "every footer this module prints hands to it")


class TestEverySessionOnTheOldestCommitIsOriginated:
    def test_a_squash_carrying_TWO_sessions_labels_BOTH(self):
        """🔴 The docstring said "session_S_" while the body labelled the first.
        A squash putting several sessions in one body is this module's founding
        premise, so the singular was on-path, not theoretical."""
        c = ha.ArcCommit(sha="a" * 40, date="2026-09-01T00:00:00Z", subject="s",
                         session_ids=("11111111-1111-4111-8111-111111111111",
                                      "22222222-2222-4222-8222-222222222222"))
        members = ha.writer_members([c])
        assert {m.role for m in members} == {ha.ROLE_ORIGINATED}


class TestTheRoundTwoFixesAreActuallyWired:
    """🔴 ROUND 2 MEASURED THAT THREE OF THE PREVIOUS ROUND'S OWN FIXES COULD BE
    DELETED WHOLESALE WITH THE SUITE STILL GREEN — the same "tested in isolation,
    wired up by nothing" shape round 1 had just fixed twice. A round that writes
    guards is a round that can write vacuous ones; these are the guards for the
    guards, and each was confirmed by re-running the mutation that survived."""

    def test_the_doc_walk_BUDGET_bounds_the_ordinary_path(self):
        calls = []

        def lookup(basename):
            calls.append(basename)
            return "/nonexistent/repo", "claudedocs/x.md"

        rows = [{"genesis": f"claudedocs/handoff-d{i}.md"} for i in range(40)]
        fs.arc_writer_counts(rows, repo_lookup=lookup)
        assert len(calls) <= fs.MAX_ANNOTATION_DOC_WALKS, (
            f"the bound did not bind: {len(calls)} lookups for 40 docs")

    def test_a_doc_that_costs_NO_git_walk_does_not_spend_budget(self):
        """The decrement must sit BELOW the lookup, or docs that resolve nowhere
        — which cost zero git calls — exhaust a budget measured in git walks."""
        calls = []

        def lookup(basename):
            calls.append(basename)
            return None, None

        rows = [{"genesis": f"claudedocs/handoff-n{i}.md"} for i in range(30)]
        fs.arc_writer_counts(rows, repo_lookup=lookup)
        assert len(calls) == 30, (
            f"budget was spent on docs that cost nothing: only {len(calls)} of "
            "30 were looked at")

    def test_arc_NAMES_every_input_it_ignores_including_tail_since_and_limit(self):
        """The hand-written list omitted `--tail`, `--since` and `--limit`, all
        of which `--arc` silently discards."""
        a = fs.parse_args(["--arc", "handoff-x", "--live", "--tail", "80",
                           "--since", "2026-01-01", "--limit", "99",
                           "--project", "p", "--any", "redis"])
        ignored = fs.arc_ignored_inputs(a)
        for flag in ("--tail", "--since", "--limit", "--project", "--any",
                     "--live"):
            assert flag in ignored, f"{flag} is discarded but not named: {ignored}"
        assert "--arc" not in ignored

    def test_the_ignored_list_is_DERIVED_so_a_new_flag_cannot_be_missed(self,
                                                                        monkeypatch):
        """🔴 ASSERTS THE DERIVATION, NOT ITS OUTPUT. An earlier version checked
        only that honoured dests exist and that a bare `--arc` names nothing —
        both true of a hand-written tuple, which an audit proved by swapping the
        derived body for one and watching the suite stay green. This adds a
        SYNTHETIC flag the parser has never seen and requires it to appear."""
        real_build = fs.build_parser

        def parser_with_extra():
            p = real_build()
            p.add_argument("--zz-synthetic", default="", help="test-only")
            return p

        monkeypatch.setattr(fs, "build_parser", parser_with_extra)
        a = parser_with_extra().parse_args(["--arc", "handoff-x",
                                            "--zz-synthetic", "v"])
        assert "--zz-synthetic" in fs.arc_ignored_inputs(a), (
            "a flag the parser declares was not picked up — the list is not "
            "derived from the parser")

    def test_the_honoured_dests_are_really_applied_by_the_arc_walk(self):
        """🔴 The other direction, and it caught a false sentence in OUTPUT:
        `--all-time` and `--claude-only` were reported as NOT applied while
        `run_arc` hardcodes both."""
        assert fs.ARC_HONOURED_DESTS <= fs.parser_dests()
        src = Path(fs.__file__).read_text(encoding="utf-8") if hasattr(
            fs, "__file__") else ""
        assert '"--all-time", "--claude-only"' in src, (
            "run_arc no longer hardcodes these; re-check ARC_HONOURED_DESTS")
        a = fs.parse_args(["--arc", "handoff-x", "--all-time", "--claude-only"])
        ignored = fs.arc_ignored_inputs(a)
        assert "--all-time" not in ignored and "--claude-only" not in ignored

    def test_live_arc_does_NOT_print_the_archive_only_notice(self, capsys,
                                                             monkeypatch):
        """Reverting the `and not a.arc` guard makes the run promise a LIVE
        section that `run_arc` then never emits."""
        monkeypatch.setattr(fs, "arc_repo_for", lambda b: (None, None))
        fs.main(["--arc", "handoff-x", "--live", "redis"])
        err = capsys.readouterr().err
        assert "LIVE section below" not in err
        assert "NOT applied" in err


def test_this_module_compiles_clean_under_W_error_SyntaxWarning():
    r"""🔴 THIS DEFECT WAS FIXED AND THEN REINTRODUCED ONE ROUND LATER, BY ME.

    A docstring here containing `\S` or `\x1b` outside a raw string raises a
    `SyntaxWarning` today and a `SyntaxError` when CPython promotes it. Round 4
    fixed one occurrence; the round-5 fix for a DIFFERENT finding wrote a new one
    into the docstring three lines above, in the very edit that was correcting a
    false claim about escape sequences. Nothing caught it but a hand-run
    `py_compile` — pytest does not fail on `SyntaxWarning`, so the module stayed
    green both times.

    ⚠ DELIBERATELY SCOPED TO THIS FILE. A repo-wide version would be RED ON DAY
    ONE — `scripts/tests/test_transcript_search.py` emits two such warnings today
    and is untouched by this work — and a permanently-red gate is worse than no
    gate. Widening it means fixing those first; that is a separate change.
    """
    import py_compile
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        py_compile.compile(str(Path(__file__).resolve()), doraise=True,
                           cfile=str(Path(__file__).with_suffix(".guardcheck")))
    Path(__file__).with_suffix(".guardcheck").unlink(missing_ok=True)


@pytest.fixture()
def stale_clone_repo(tmp_path: Path) -> Path:
    """A clone whose LOCAL branch is rewound behind its upstream.

    🔴 This is the shape the handoff flow itself produces, not a contrivance. Rule
    10 of `datapacket-talos` forbids committing in the primary clone, so every
    handoff lands via `git worktree add --detach origin/<branch>` +
    `push HEAD:<branch>` — which advances the REMOTE ref and never the local one.
    A clone that only ever reads is therefore permanently behind, and the doc's
    commits are reachable from `origin/<branch>` alone.
    """
    _sh("git", "init", "-q", "--bare", "-b", "main", "origin.git", cwd=tmp_path)
    _sh("git", "clone", "-q", str(tmp_path / "origin.git"), "work", cwd=tmp_path)
    work = tmp_path / "work"

    # A commit BEFORE the doc exists, so rewinding to it makes the doc absent
    # from HEAD entirely — the exact state that returns a confident empty arc.
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _sh("git", "add", "--", "README.md", cwd=work)
    _sh("git", "commit", "-q", "-m", "chore: base commit, no doc", cwd=work)
    base = _sh("git", "rev-parse", "HEAD", cwd=work).strip()

    (work / "claudedocs").mkdir()
    doc = work / DOC
    doc.write_text("# fixture\n\nfirst\n", encoding="utf-8")
    _sh("git", "add", "--", DOC, cwd=work)
    _sh("git", "commit", "-q", "-m", "docs(handoff): new doc, no trailer", cwd=work)
    doc.write_text("# fixture\n\nsecond\n", encoding="utf-8")
    _sh("git", "add", "--", DOC, cwd=work)
    _sh("git", "commit", "-q", "-m", SQUASH_BODY, cwd=work)
    doc.write_text("# fixture\n\nthird\n", encoding="utf-8")
    _sh("git", "add", "--", DOC, cwd=work)
    _sh("git", "commit", "-q", "-m", PLAIN_BODY, cwd=work)

    _sh("git", "push", "-q", "origin", "main", cwd=work)
    # Rewind the LOCAL branch only. `origin/main` keeps all four commits.
    _sh("git", "reset", "--hard", "-q", base, cwd=work)
    return work


class TestStaleLocalBranchDoesNotHideTheArc:
    """🔴 The regression this module's worst measured failure produced.

    Red at the pre-fix `doc_commits` (which walked `HEAD` implicitly), green now.
    """

    def test_the_BUG_STATE_is_real__HEAD_alone_sees_nothing(self, stale_clone_repo):
        """The control that makes the next test meaningful rather than tautological.

        If `HEAD` could see these commits the fixture would not reproduce the
        defect, and a green next test would prove nothing. Measured here so the
        regression test's redness at base is attributable to the walk's REF and
        not to the fixture being wrong.
        """
        out = _sh("git", "log", "--format=%H", "--follow", "--", DOC,
                  cwd=stale_clone_repo)
        assert out.strip() == "", (
            "the fixture does not reproduce the bug: HEAD can still reach the "
            "doc's commits, so this file cannot test the stale-branch case")
        # …and the upstream genuinely has them, or the fix has nothing to find.
        up = _sh("git", "log", "--format=%H", "--follow", "origin/main", "--", DOC,
                 cwd=stale_clone_repo)
        assert len(up.strip().splitlines()) == 3

    def test_doc_commits_FINDS_all_three_via_the_upstream(self, stale_clone_repo):
        commits = ha.doc_commits(str(stale_clone_repo), DOC)
        assert len(commits) == 3, (
            "the writer half walked HEAD alone: a stale local branch hid the "
            f"whole arc (got {len(commits)} of 3)")

    def test_the_ORIGINATING_session_is_recovered(self, stale_clone_repo):
        """The one writer the reader half structurally cannot see.

        An originating session never resumed the doc it created, so it appears in
        no transcript search. Losing it to a stale ref loses it entirely.
        """
        commits = ha.doc_commits(str(stale_clone_repo), DOC)
        oldest = commits[-1]
        assert oldest.stamped is False
        ids = [i for c in commits for i in c.session_ids]
        assert "11111111-1111-4111-8111-111111111111" in ids
        assert "22222222-2222-4222-8222-222222222222" in ids

    def test_commits_are_NEWEST_FIRST_across_the_merge(self, stale_clone_repo):
        """The single-rev walk gave this ordering for free; the merge must keep it.

        Pinned because `writer_members` reads `commits[-1]` as the ORIGINATING
        commit, so an ordering regression silently relabels who originated an arc.

        🔴 ASSERTED AGAINST GIT'S OWN ORDER, NOT AGAINST `%aI`. Every commit in
        this fixture is created inside the same second, so a
        `dates == sorted(dates, reverse=True)` assertion is VACUOUS here — it
        holds for any permutation. That is not a fixture wart to work around: it
        is the exact condition under which the first implementation of the merge
        mis-ordered (it sorted on the second-resolution date and tie-broke by
        sha), so the fixture is the bug's natural habitat and the assertion has
        to be structural.
        """
        expected = _sh("git", "log", "--format=%H", "--follow", "origin/main",
                       "--", DOC, cwd=stale_clone_repo).split()
        commits = ha.doc_commits(str(stale_clone_repo), DOC)
        assert [c.sha for c in commits] == expected, (
            "the merged walk does not reproduce git's own newest-first order")
        assert commits[-1].session_ids == (), (
            "the oldest commit is not the unstamped originating one — ordering "
            "inverted, which would relabel the arc's originator")

    def test_a_commit_on_BOTH_refs_is_counted_ONCE(self, stale_clone_repo):
        """Dedup. Without it every commit reachable from both refs doubles, and
        `total_commits` — which the report prints as a coverage denominator —
        overstates.

        ⚠ INVARIANT GUARD, NOT REGRESSION COVERAGE: green at the pre-fix module
        too, because a single-rev walk cannot double-count in the first place. It
        pins the property the two-rev walk newly has to maintain.
        """
        _sh("git", "merge", "-q", "--ff-only", "origin/main", cwd=stale_clone_repo)
        commits = ha.doc_commits(str(stale_clone_repo), DOC)
        shas = [c.sha for c in commits]
        assert len(shas) == len(set(shas)) == 3, (
            f"a commit reachable from both HEAD and the upstream was counted "
            f"more than once: {shas}")


class TestDocCommitRevs:
    """⚠ NEW-EXPORT SPECS, NOT REGRESSION COVERAGE.

    `doc_commit_revs` does not exist at the pre-fix module, so every test here is
    red there only as `AttributeError` — which proves the symbol is new, not that
    any assertion bites. The regression weight is carried entirely by
    `TestStaleLocalBranchDoesNotHideTheArc`, whose central test fails at base with
    its own message (`got 0 of 3`).
    """

    def test_returns_HEAD_AND_the_upstream_with_no_note(self, stale_clone_repo):
        revs, note = ha.doc_commit_revs(str(stale_clone_repo))
        assert revs[0] == "HEAD"
        assert "origin/main" in revs
        assert note is None

    def test_a_repo_with_NO_upstream_gets_HEAD_ONLY_and_a_NOTE(self, arc_repo):
        """`arc_repo` is a bare `git init` — no remote, no upstream.

        🔴 The note is the point: falling back to `HEAD` alone is exactly the
        blind state, so a caller must be able to say the coverage is narrower.
        Returning `('HEAD',)` silently would reintroduce the defect as a default.
        """
        revs, note = ha.doc_commit_revs(str(arc_repo))
        assert revs == ("HEAD",)
        assert note and "HEAD` alone" in note

    def test_the_note_reaches_the_REPORT(self, arc_repo):
        report = ha.resolve_arc(str(arc_repo), DOC, reader_rows=[],
                                readers_measured=True)
        assert any("HEAD` alone" in n for n in report.unmeasured_notes), (
            "the coverage note was computed and then dropped, so the report "
            f"reads as complete: {report.unmeasured_notes!r}")


# =========================================================================== #
# THE ARC'S REPO HANDLES — DERIVED FROM ONE TUPLE, AT EVERY SITE
# =========================================================================== #
# 🔴 WHY THIS SECTION EXISTS. `arc_repo_for` carried an inline four-element copy
# of `handoff_index.REPO_ENV_HANDLES` while its own docstring said it "searches
# every repo in `handoff_index.REPO_ENV_HANDLES`" — a description wider than its
# implementation, the `guards-narrower` shape. When `CIVITAI_CLI` became the
# fifth entry of that tuple, a doc living in that checkout was UNREACHABLE and
# `--arc` reported `nothing was measured at all` about a doc sitting on that
# repo's mainline. Three prose sites enumerated the same four handles and so told
# the operator the search had covered every checkout it can see.
#
# ⚠ THE PROSE PINS BELOW TAKE THE WHOLE NORMALISED SENTENCE, NOT A SUBSTRING.
# `claude/RULES.md` → "a guard on WORDS is walkable by REWORDING": a check that
# only asked whether `$CIVITAI_CLI` appeared would pass a sentence naming the
# handle while claiming the opposite about it. The literal prose is written out
# here and only the HANDLE LIST is derived, so the expectation is not read off
# the implementation it grades. A cosmetic reword fails this test; that is the
# price of a machine-readable claim.
from lib import handoff_index as hi  # noqa: E402


def _norm(text: str) -> str:
    """Whitespace-run normalisation and nothing else — line WRAPPING is cosmetic
    and must not decide a verdict; wording is."""
    return " ".join(text.split())


class TestEveryHandleInTheTupleIsSearchable:
    """`arc_repo_for` must reach a doc under EVERY `REPO_ENV_HANDLES` entry.

    🔴 AS WIDE AS ITS DOCSTRING, DELIBERATELY. A test naming only `CIVITAI_CLI`
    would pin the instance and not the class: the next handle appended to that
    tuple would be just as unreachable, and this guard would stay green. The loop
    is over the tuple itself, so coverage grows with it.
    """

    DOCNAME = "handoff-plimforth-widget.md"

    @classmethod
    def _plant(cls, tmp_path, handle, subdir="claudedocs"):
        """A synthetic checkout holding one handoff doc, and an env naming it.

        ONE handle is set at a time — a run with every handle set resolves on the
        FIRST, and a loop like that cannot see a handle the walk skips.
        """
        root = tmp_path / f"repo-{handle.lower()}"
        (root / subdir).mkdir(parents=True)
        (root / subdir / cls.DOCNAME).write_text("# LEAKCANARY-synthetic\n",
                                                 encoding="utf-8")
        return root, {handle: str(root)}

    def test_a_doc_under_ANY_declared_handle_RESOLVES(self, tmp_path):
        """🔴 THE REGRESSION TEST. Red at the pre-fix tree, naming
        `['CIVITAI_CLI']`; green once the loop walks the tuple.

        Mutating the production loop to `handoff_index.REPO_ENV_HANDLES[:4]`
        reproduces the original defect and fails HERE, with this assertion's own
        message, rather than somewhere downstream.
        """
        assert hi.REPO_ENV_HANDLES, (
            "POSITIVE CONTROL: REPO_ENV_HANDLES is empty, so the loop below "
            "walks nothing and every assertion in this class is vacuous")
        unreachable = []
        for handle in hi.REPO_ENV_HANDLES:
            root, env = self._plant(tmp_path, handle)
            got = fs.arc_repo_for(self.DOCNAME, env=env)
            if got != (str(root), f"claudedocs/{self.DOCNAME}"):
                unreachable.append(handle)
        assert not unreachable, (
            f"`arc_repo_for` cannot reach a doc in the {unreachable} "
            f"checkout(s), so `--arc` reports `nothing was measured at all` for "
            f"a doc that is right there. The loop must walk "
            f"`handoff_index.REPO_ENV_HANDLES` ({list(hi.REPO_ENV_HANDLES)}) "
            f"rather than an inline copy of it.")

    def test_the_archive_subdir_is_searched_under_ANY_handle_too(self, tmp_path):
        """The second relpath (`claudedocs/archive/`) is tried per handle as
        well, so an ARCHIVED doc in the fifth checkout was equally unreachable —
        the same defect one directory deeper."""
        unreachable = []
        for handle in hi.REPO_ENV_HANDLES:
            root, env = self._plant(tmp_path, handle, subdir="claudedocs/archive")
            got = fs.arc_repo_for(self.DOCNAME, env=env)
            if got != (str(root), f"claudedocs/archive/{self.DOCNAME}"):
                unreachable.append(handle)
        assert not unreachable, (
            f"an ARCHIVED doc in the {unreachable} checkout(s) does not resolve")

    def test_an_EMPTY_handle_is_skipped_rather_than_joined_onto_cwd(
            self, tmp_path, monkeypatch):
        """NEGATIVE CONTROL: the walk must still return `(None, None)` when no
        handle holds the doc. A loop that treated an empty handle as `Path("")`
        would resolve against the CWD and answer about whatever repo the operator
        happens to be standing in.

        🔴 THE PLANTED CWD DOC IS WHAT MAKES THIS GUARD OBSERVABLE, AND WITHOUT
        IT THE GUARD WAS VACUOUS. MEASURED: deleting the `if not root: continue`
        skip from `arc_repo_for` left this whole file GREEN at 107 passed,
        because `Path("") / "claudedocs/<synthetic>.md"` does not exist under any
        cwd either — so the guarded and the unguarded walk both returned
        `(None, None)` and this assertion could not tell them apart. It read as
        a negative control while providing none. With the doc planted in the cwd
        the unguarded walk RESOLVES it, and that is the only arrangement in which
        this test can fail for its own reason.
        """
        (tmp_path / "claudedocs").mkdir()
        (tmp_path / "claudedocs" / self.DOCNAME).write_text(
            "# LEAKCANARY-synthetic\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        env = {h: "" for h in hi.REPO_ENV_HANDLES}
        assert fs.arc_repo_for(self.DOCNAME, env=env) == (None, None), (
            "an EMPTY handle was joined onto the CWD, so the walk answered about "
            "whatever repo the operator happens to be standing in rather than "
            "skipping the unset handle")

    def test_the_handles_are_searched_in_the_TUPLE_order(self, tmp_path):
        """Order is part of the contract: two checkouts holding a doc of the
        same name must resolve to the EARLIER handle, deterministically. A
        `set()` would make the answer depend on hash order."""
        if len(hi.REPO_ENV_HANDLES) < 2:
            pytest.skip("needs at least two declared handles")
        env, roots = {}, {}
        for handle in hi.REPO_ENV_HANDLES:
            root, one = self._plant(tmp_path, handle)
            roots[handle] = root
            env.update(one)
        first = hi.REPO_ENV_HANDLES[0]
        assert fs.arc_repo_for(self.DOCNAME, env=env) == (
            str(roots[first]), f"claudedocs/{self.DOCNAME}")


class TestTheSearchWALKSTheTupleRatherThanACopy:
    """🔴 THE STRUCTURAL HALF, AND IT IS NOT REDUNDANT WITH THE BEHAVIOURAL ONE.

    The tests above grade the OUTCOME against the tuple as it stands today, so a
    re-added inline copy that happens to agree today passes every one of them and
    rots silently the next time the tuple grows — which is exactly how this
    defect shipped. This asserts the loop's iterable IS the attribute, read off
    the AST, so a copy cannot come back at all.
    """

    @staticmethod
    def _arc_repo_for_loops():
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(fs))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "arc_repo_for"), None)
        assert fn is not None, (
            "no `arc_repo_for` function found in the module — this guard is "
            "reading nothing; re-point it rather than deleting it")
        return [n for n in ast.walk(fn) if isinstance(n, ast.For)]

    def test_the_handle_loop_iterates_the_SHARED_TUPLE(self):
        import ast
        loops = self._arc_repo_for_loops()
        assert loops, "`arc_repo_for` has no `for` loop at all"
        iters = [ast.unparse(n.iter) for n in loops]
        assert "handoff_index.REPO_ENV_HANDLES" in iters, (
            "`arc_repo_for` no longer iterates "
            "`handoff_index.REPO_ENV_HANDLES`; it iterates "
            f"{iters}. An inline copy of the handle tuple agrees with it on the "
            "day it is written and silently stops when the tuple grows — that "
            "is the defect this section exists for.")

    def test_this_scan_WOULD_catch_the_original_inline_copy(self):
        """POSITIVE CONTROL, built from the ORIGINAL defect rather than a
        textbook fixture: the real pre-fix line, graded by the same predicate."""
        import ast
        original = (
            "def arc_repo_for(basename, env=None):\n"
            '    for handle in ("DEVRC", "HOMELAB", "DATAPACKET", "CIVITAI"):\n'
            "        pass\n")
        tree = ast.parse(original)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef))
        iters = [ast.unparse(n.iter) for n in ast.walk(fn)
                 if isinstance(n, ast.For)]
        assert "handoff_index.REPO_ENV_HANDLES" not in iters, (
            "the predicate cannot distinguish the shared tuple from an inline "
            f"copy of it — it accepted {iters}")


class TestTheHandleProseNamesEveryHandle:
    """Every operator-facing sentence about the arc search, pinned WHOLE.

    🔴 THREE SITES, AND A SWEEP THAT REACHES TWO OF THEM IS THE DEFECT ITSELF.
    The exit-contract sentence, the refusal an operator actually reads, and the
    sibling extractor's own contract each enumerated the handles by hand. Each is
    pinned against the same derived list here, so a handle added to
    `REPO_ENV_HANDLES` with any of these left behind fails.
    """

    @staticmethod
    def _spelled(sep=", "):
        return sep.join(f"${h}" for h in hi.REPO_ENV_HANDLES)

    def test_the_renderer_itself_names_every_handle_in_order(self):
        """POSITIVE CONTROL for the three pins below, which all route through
        this one function: if it can drop a handle, they are graded against a
        list that is already wrong."""
        assert fs.arc_handles_spelled() == self._spelled()
        assert fs.arc_handles_spelled("/") == self._spelled("/")
        for handle in hi.REPO_ENV_HANDLES:
            assert f"${handle}" in fs.arc_handles_spelled()

    def test_the_EXIT_ARC_UNMEASURED_sentence_is_pinned_WHOLE(self):
        """🔴 WHOLE NORMALISED STRING, NOT A SUBSTRING.
        `claude/skills/find-session/SKILL.md` copies this sentence verbatim and
        `test_find_session_skill_contract.py` pins the copy, so the doc and the
        constant move together — but only this test says the sentence must name
        the handles the search really walks.
        """
        expected = (
            "`--arc` ONLY: the doc was named but NOT MEASURED — no repo handle "
            f"({self._spelled()}) this shell can see holds it. 🔴 This is not "
            "an empty arc and must never be reported as one: nothing was read "
            "at all.")
        got = _norm(dict(fs.EXIT_CONTRACT)[fs.EXIT_ARC_UNMEASURED])
        assert got == _norm(expected), (
            "the exit-5 contract sentence no longer matches the handles the arc "
            "search walks, or was reworded:\n"
            f"  expected: {_norm(expected)!r}\n"
            f"  got     : {got!r}\n"
            "Render the handle list from `handoff_index.REPO_ENV_HANDLES` (see "
            "`find_session.arc_handles_spelled`), then copy the rendered "
            "sentence into `claude/skills/find-session/SKILL.md`'s exit table.")

    def test_the_operator_facing_REFUSAL_names_every_handle(self, monkeypatch):
        """The sentence a human actually sees on exit 5 — raised by `arc_report`
        and printed verbatim by `run_arc`. It was the site NOTHING renders on a
        successful run, which is why it was the easiest of the three to miss."""
        monkeypatch.setattr(fs, "arc_repo_for", lambda b: (None, None))
        with pytest.raises(fs.ArcUnmeasured) as exc:
            fs.arc_report("handoff-plimforth-widget.md")
        expected = (
            "no repo handle holds claudedocs/handoff-plimforth-widget.md. 🔴 "
            "This is NOT 'the arc is empty' — it means every "
            f"{self._spelled('/')} checkout this shell can see lacks the doc, "
            "so nothing was measured at all.")
        assert _norm(str(exc.value)) == _norm(expected), (
            "the exit-5 refusal no longer matches the handles the search "
            "walks, or was reworded:\n"
            f"  expected: {_norm(expected)!r}\n"
            f"  got     : {_norm(str(exc.value))!r}")

    def test_the_EXTRACTOR_exit_3_sentence_is_pinned_WHOLE(self):
        """🔴 THE SEAM SITE. `extract_user_msgs.py` imports `arc_report` and
        inherits the search, so its own exit-3 sentence is a claim about THIS
        module's reach. It enumerated four handles while the search walked five
        — a sentence that told the operator nothing had been missed. Pinned here
        because the extractor's own suite pins meaning TOKENS only, which a
        stale handle list satisfies."""
        eum = _load_extractor()
        expected = (
            "`--arc` ONLY: the seed named no handoff doc, or no "
            f"{self._spelled('/')} checkout holds it. 🔴 NOTHING WAS MEASURED "
            "— this is not an empty arc, and a wrong name lands here, not on "
            "exit 4.")
        got = _norm(dict(eum.EXIT_CONTRACT)[eum.EXIT_ARC_UNMEASURED])
        assert got == _norm(expected), (
            "`extract_user_msgs.py`'s exit-3 sentence no longer matches the "
            "handles `find-session.arc_repo_for` really walks, or was "
            "reworded:\n"
            f"  expected: {_norm(expected)!r}\n"
            f"  got     : {got!r}")


# =========================================================================== #
# A SESSION DRIFTS — the reverse (session -> docs) lookup and the CROSS-ARC
# footer it feeds.
#
# 🔴 THE DEFECT, MEASURED. A session opens resumed from handoff-A, does that
# work, then moves on and ends by writing handoff-B. `--arc handoff-B` lists it
# as a member and NEVER NAMES handoff-A, because every resolver here was keyed
# on the session's GENESIS and is single-valued.
#
# 🔴 THE RATE, AT THE SCOPE MEASURED — **29 of 291 (~1 in 10)**. An earlier
# version of this comment said "36 of 291 (~1 in 8)", a figure for a WIDER
# population than the word "drift" names; this is the correction. Measured
# 2026-10-03 over EVERY SET HANDLE ($DEVRC, $HOMELAB, $DATAPACKET, $CIVITAI;
# $CIVITAI_CLI unset => UNMEASURED, so the denominator is scoped to four), at
# the scope `doc_commit_revs` walks — HEAD + upstream per handle, NOT `--all`,
# which would credit commits on unmerged branches no shipped reader can see:
#   291  stamped writer sessions
#    36  touched >=2 DISTINCT handoff docs            <- the old headline
#    29  have some doc pair with DISJOINT commit sets <- DRIFTED (~1 in 10)
#     7  the complement: NO doc pair with disjoint commit sets
#     2  of those 7 wrote every doc in ONE commit (7f1c2b2a, ses_f0fc3e87)
#    28  the drifted set excluding the 37-doc bulk-move session (6b88ffe8)
#     4  of the 29 drifted ACROSS repos
# Histogram: {1: 255, 2: 25, 3: 9, 4: 1, 37: 1}.
# ⚠ THE 7 IS NOT "WROTE EVERY DOC IN ONE COMMIT", AND THIS IS THE THIRD
# CORRECTION TO THAT ONE LINE. It is `36 - 29`, the complement of "drifted",
# which is a different predicate: enumerated, FIVE of the seven spread their
# docs over 2-5 SEPARATE commits and are excluded only because every doc PAIR
# happens to share a commit. Only two are one-commit bulk moves. The 29 never
# depended on the gloss — the code applies disjointness correctly.
# ⚠ THE DISJOINTNESS CRITERION IS WHAT MAKES THE CLAIM HONEST: a session whose
# every doc pair shares a commit did not demonstrably change subject.
# Quoting the `>=2 docs` count as the drift rate overstates it by ~25%.
# ⚠ AND THE `ses_…` CASE IS A POPULATION OF THE WIDER SET ONLY — 1 of the 36
# multi-doc sessions, **0 of the 29 drifted**. The cross-repo case is a
# population of both (4 either way). Said here because the fixture below is
# justified by the multi-doc population and a reader must not upgrade that.
# An earlier draft also carried {1: 256, 2: 27, 3: 7, 4: 1} and a "35/289
# excluding 18 bulk-move commits" figure from the original recon; both are at a
# scope this branch did not reproduce, so they stay REMOVED. ⚠ That recon
# figure was the one that would have caught this very overstatement — dropping
# it as unreproduced was right, and RE-DERIVING it is what was owed.
#
# The edge needed no new capture: the commit's trailer carries the id and the
# commit's FILE LIST names the doc. `doc_commits` already pairs them, but
# DOC-FIRST. `sessions_docs` is the same pairing read SESSION-FIRST.
# =========================================================================== #

#: 🔴 EVERY ELEMENT OF `drift_repo` IS LOAD-BEARING — the ledger, so a later
#: round cannot "simplify" one away without reading what it buys:
#:
#:  1. two docs in ONE repo, so drift inside a repo is separable from drift
#:     across repos;
#:  2. an UNSTAMPED first commit on alpha, so alpha's arc keeps the
#:     `originated -> earliest-stamped` DEMOTION in play (45% of the real corpus
#:     is unstamped) and no assertion here can accidentally depend on full
#:     coverage;
#:  3. alpha and beta stamped `SID_DRIFT` in **SEPARATE, non-adjacent** commits
#:     — a both-in-one-commit fixture is the BULK-MOVE shape (18 real ones) and
#:     a `--name-only`-only implementation would pass it without the reverse
#:     lookup ever running;
#:  4. alpha's stamp is in GITHUB-SQUASH shape (trailer mid-message, trailing
#:     prose). A trailer-LAST body passes whether the reader uses `%B` or
#:     `%(trailers:…)`; that is the trap, and this is what proves `--grep`;
#:  5. `SID_SINGLE` writes beta ONLY, so the member list is not uniformly
#:     drifted and the "no other arc" branch is exercised;
#:  6. `SID_ARCHIVE` writes a doc under `claudedocs/archive/`, so the path
#:     alternation is pinned BY ITSELF rather than only through the cross-repo
#:     case;
#:  7. a SECOND REPO with `SID_DRIFT` writing a doc there — a single-repo
#:     implementation passes every other assertion in this file;
#:  8. an `ses_…`-shaped id, so nothing reintroduces the shape filtering
#:     `session_trailer` forbids;
#:  9. an INDENTED trailer on a third doc, so the `^` anchor and the
#:     `trailer_ids` RE-VERIFICATION are both observable.
SID_DRIFT = "d1d1d1d1-2222-4333-8444-555555555555"
SID_SINGLE = "51515151-2222-4333-8444-555555555555"
SID_ARCHIVE = "a1a1a1a1-2222-4333-8444-555555555555"
SID_NO_TRANSCRIPT = "e0e0e0e0-2222-4333-8444-555555555555"
#: 1 of the 36 measured drifting sessions carries this shape.
SID_SESO = "ses_7f3b9c1d4e2a"

ALPHA = "claudedocs/handoff-alpha.md"
BETA = "claudedocs/handoff-beta.md"
GAMMA = "claudedocs/handoff-gamma.md"
DELTA_ARCHIVED = "claudedocs/archive/handoff-delta.md"
EPSILON = "claudedocs/handoff-epsilon.md"

#: Alpha's stamp, in the shape git's own trailer parser cannot read. The
#: trailing prose is the MECHANISM, not decoration — see `SQUASH_BODY`.
DRIFT_SQUASH_BODY = f"""docs(handoff): alpha, squashed (#2001)

* docs(handoff): the first commit on the branch

{ha.TRAILER_KEY}: {SID_DRIFT}

* docs(handoff): the second commit on the branch

A closing paragraph of ordinary prose, which is what makes this message's
FINAL block a non-trailer block.
"""

#: 🔴 TWO TRAILERS, ONE INDENTED AND ONE AT COLUMN 0, in one body. The indented
#: one must mint NO edge; the flush-left one must mint one. A fixture carrying
#: only the indented line cannot tell "the anchor held" from "the walk found
#: nothing at all".
GAMMA_BODY = f"""docs(handoff): gamma, quoting a trailer inside prose (#2002)

A paragraph that QUOTES a trailer, indented, the way a commit message explains
one:

    {ha.TRAILER_KEY}: {SID_DRIFT}

and the real stamp, at column 0:

{ha.TRAILER_KEY}: {SID_SESO}
"""


def _give_upstream(repo: Path, at: str = "HEAD") -> None:
    """Make `doc_commit_revs` resolve an upstream for a `git init` fixture.

    🔴 WITHOUT THIS EVERY FIXTURE HANDLE IS **NARROWED**, AND THAT IS NOT A
    DETAIL OF THE FIXTURE — it is what made the shipped footer's
    `every repo handle answered` sentence unobservably wrong. A bare `git init`
    resolves neither `@{upstream}` nor `refs/remotes/origin/HEAD`, so the five
    handles `_all_handles_on` points at were ALL walking `HEAD` alone while the
    footer affirmed a complete walk — and the test that pinned that sentence
    could not tell, because the narrowing was never surfaced.

    `at="HEAD~1"` leaves the upstream genuinely BEHIND, so two distinct revs are
    walked; the default puts it ON `HEAD`, where `doc_commit_revs` collapses the
    duplicate. Both are real states and the tests want both. No clone and no
    network: `origin/main` is written with plumbing and `origin/HEAD` points at
    it, which is the second probe.
    """
    oid = _sh("git", "rev-parse", at, cwd=repo).strip()
    _sh("git", "update-ref", "refs/remotes/origin/main", oid, cwd=repo)
    _sh("git", "symbolic-ref", "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main", cwd=repo)


def _commit(work: Path, relpath: str, text: str, body: str) -> None:
    target = work / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _sh("git", "add", "--", relpath, cwd=work)
    _sh("git", "commit", "-q", "-m", body, cwd=work)


@pytest.fixture()
def drift_repo(tmp_path: Path):
    """Two synthetic checkouts in which one session wrote four docs.

    Returns `(repo_a, repo_b, filler_repos)`. `filler_repos` exist so a test can
    set EVERY `REPO_ENV_HANDLES` entry to a readable repo and therefore observe
    the fully-MEASURED cross-arc footer — on this host `$CIVITAI_CLI` is unset,
    and an unset handle is UNMEASURED rather than zero, so a footer pinned
    against the ambient environment could never see the complete sentence.
    """
    a = tmp_path / "repo-a"
    a.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=a)
    # (2) UNSTAMPED, and it is alpha's ORIGINATING commit.
    _commit(a, ALPHA, "# alpha\n\nfirst\n",
            "docs(handoff): new doc, no trailer")
    # (4) alpha stamped SID_DRIFT, in squash shape.
    _commit(a, ALPHA, "# alpha\n\nsecond\n", DRIFT_SQUASH_BODY)
    # (5) beta's originating commit, a DIFFERENT session.
    _commit(a, BETA, "# beta\n\nfirst\n",
            f"docs(handoff): beta begins (#2003)\n\n{ha.TRAILER_KEY}: {SID_SINGLE}\n")
    # (9) the indented/flush-left pair, on a doc of its own.
    _commit(a, GAMMA, "# gamma\n\nfirst\n", GAMMA_BODY)
    # (3) beta stamped SID_DRIFT — a SEPARATE, LATER commit. This is the drift.
    _commit(a, BETA, "# beta\n\nsecond\n",
            f"docs(handoff): beta, continued (#2004)\n\n{ha.TRAILER_KEY}: {SID_DRIFT}\n")
    # (6) an ARCHIVED doc, so the `archive/` alternation is pinned alone.
    _commit(a, DELTA_ARCHIVED, "# delta\n\nfirst\n",
            f"docs(handoff): delta, archived (#2005)\n\n{ha.TRAILER_KEY}: {SID_ARCHIVE}\n")

    _give_upstream(a, at="HEAD~1")
    # (7) the SECOND repo — SID_DRIFT drifted ACROSS repos (4 of 36 real).
    b = tmp_path / "repo-b"
    b.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=b)
    _commit(b, EPSILON, "# epsilon\n\nfirst\n",
            f"docs(handoff): epsilon (#2006)\n\n{ha.TRAILER_KEY}: {SID_DRIFT}\n")
    _give_upstream(b)

    fillers = []
    for i in range(3):
        f = tmp_path / f"repo-filler{i}"
        f.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=f)
        # One unrelated commit: a repo with NO commits at all makes `git log`
        # exit non-zero, which is UNMEASURED — a different fact from "answered
        # with nothing", and this fixture wants the latter.
        _commit(f, "README.md", f"filler {i}\n", "chore: filler")
        _give_upstream(f)
        fillers.append(f)
    return a, b, fillers


def _set_handles(monkeypatch, mapping):
    """Point every `REPO_ENV_HANDLES` entry at a path, or unset it.

    🔴 DERIVED FROM THE TUPLE, never an inline list of five names — the reason
    `arc_repo_for`'s own inline copy is called out as a fix in this file.
    """
    for handle in hi.REPO_ENV_HANDLES:
        value = mapping.get(handle)
        if value is None:
            monkeypatch.delenv(handle, raising=False)
        else:
            monkeypatch.setenv(handle, str(value))


def _all_handles_on(monkeypatch, repo_a, repo_b, fillers):
    """Every handle set and readable — the only state with NO unmeasured gap."""
    paths = [repo_a, repo_b, *fillers]
    mapping = {h: paths[i] if i < len(paths) else fillers[-1]
               for i, h in enumerate(hi.REPO_ENV_HANDLES)}
    _set_handles(monkeypatch, mapping)
    return mapping


class TestSessionDocsReadsTheEdgeSESSIONFIRST:
    """`handoff_arc.sessions_docs` — the reverse of `doc_commits`.

    ⚠ EVERY SINGLE-SESSION CASE HERE CALLS THE PLURAL, as
    `sessions_docs(repo, (sid,))[sid]`. The singular `session_docs(repo, sid)`
    door was deleted (D3, 2026-10-03): zero production callers — the cross-arc
    footer calls the plural — so it was kept alive by these ten tests and a
    named FOLLOW-ON, which is not a named CONSUMER. 🔴 THE PORT CHANGED NO
    ASSERTION: each of the ten still fails for exactly the reason it failed
    before, because `sessions_docs(repo, (sid,))[sid]` IS what the deleted
    function returned, verbatim. ⚠ But note `test_a_SINGLE_doc_session…`'s own
    warning below — a ONE-id call is structurally blind to cross-crediting, and
    `test_ONE_PASS_over_SEVERAL_ids_does_NOT_CROSS_CREDIT` is the only guard
    here that is not. That was true before the port and is unchanged by it.
    """

    def test_a_DRIFTING_session_reports_BOTH_docs_newest_first(self, drift_repo):
        """🔴 THE DEFECT, DIRECTLY. One session, two docs, two separate commits.
        Newest-first, because that is `git log`'s own order and every other
        ordering in this module is git's."""
        a, _b, _f = drift_repo
        assert ha.sessions_docs(str(a), (SID_DRIFT,))[SID_DRIFT] == (
            "handoff-beta.md", "handoff-alpha.md")

    def test_a_SQUASH_shaped_stamp_STILL_mints_an_edge(self, drift_repo):
        """🔴 THE KILLER FOR A `%(trailers:…)` IMPLEMENTATION. Alpha's stamp is
        mid-message with trailing prose, so git's own trailer parser returns
        EMPTY for it — measured at a 40% relative undercount corpus-wide. If this
        assertion is the only one that moves, the reader stopped reading `%B`."""
        a, _b, _f = drift_repo
        assert "handoff-alpha.md" in ha.sessions_docs(
            str(a), (SID_DRIFT,))[SID_DRIFT], (
            "the squash-shaped stamp minted no edge — the reverse lookup is "
            "reading git's trailer parser instead of the whole body")

    def test_a_SINGLE_doc_session_reports_EXACTLY_that_doc(self, drift_repo):
        """The single-session door, on a session that did NOT drift.

        ⚠ THIS IS NOT THE RE-VERIFICATION GUARD, and an earlier docstring here
        claimed it was — the measured correction. With ONE id requested, a
        reader that credits every matched commit to every requested id produces
        the SAME answer, so this assertion is structurally blind to that mutant:
        it SURVIVED a sweep against this test and was killed only by the test
        below. A description wider than the implementation it grades.
        """
        a, _b, _f = drift_repo
        assert ha.sessions_docs(str(a), (SID_SINGLE,))[SID_SINGLE] == (
            "handoff-beta.md",)

    def test_ONE_PASS_over_SEVERAL_ids_does_NOT_CROSS_CREDIT(self, drift_repo):
        """🔴 THE KILLER FOR A DROPPED `trailer_ids` RE-VERIFICATION, and it has
        to be a MULTI-id call to be one.

        `--grep` is a PREFILTER: the footer ORs every member's pattern into ONE
        walk (per-member is ~3x slower), so git returns a commit matching ANY
        of them. Crediting each matched commit to every REQUESTED id — rather
        than to the ids its body really carries — hands each session the whole
        arc's docs. Fixtures chosen pairwise distinct so no two expectations can
        be satisfied by one wrong value.
        """
        a, _b, _f = drift_repo
        got = ha.sessions_docs(str(a), [SID_DRIFT, SID_SINGLE, SID_ARCHIVE])
        assert got == {
            SID_DRIFT: ("handoff-beta.md", "handoff-alpha.md"),
            SID_SINGLE: ("handoff-beta.md",),
            SID_ARCHIVE: ("handoff-delta.md",),
        }, (
            "a session was credited with a doc it never stamped — the bodies "
            f"matched by the ORed --grep prefilter are not being re-parsed "
            f"with trailer_ids(): {got!r}")

    def test_an_ARCHIVED_doc_is_REACHED(self, drift_repo):
        """🔴 THE KILLER FOR A DROPPED `archive/` ALTERNATION. #1627 renamed 35
        docs under `claudedocs/archive/`; a lookup that cannot see that prefix
        reports a drifting session as single-doc for every archived arc."""
        a, _b, _f = drift_repo
        assert ha.sessions_docs(str(a), (SID_ARCHIVE,))[SID_ARCHIVE] == (
            "handoff-delta.md",), (
            "a doc under claudedocs/archive/ was not reached — the path "
            "alternation is gone and every archived arc now reads as absent")

    def test_an_INDENTED_trailer_mints_NO_edge(self, drift_repo):
        """The RE-VERIFICATION. Gamma's body quotes a SID_DRIFT trailer
        indented inside prose; that is not a stamp.

        ⚠ IT DOES NOT PIN THE `^` ANCHOR IN `_trailer_grep`, and an earlier
        docstring here said "the `^` anchor plus the re-verification" — a
        description wider than the implementation it grades. MEASURED
        2026-10-03: deleting the `^` from `_trailer_grep` leaves this test GREEN
        (the mutant SURVIVED), because `trailer_ids()` rejects an indented
        trailer on its own — over a body carrying BOTH an indented and a
        flush-left stamp it returns only the flush-left one. So the anchor is a
        PREFILTER WIDTH choice (how much git hands back, i.e. a cost question)
        and `trailer_ids` is the guard. The assertion is unchanged and still
        kills every mutant that drops the re-verification; only the claim about
        what it covers is corrected. 🔴 The `^` anchor is consequently
        UNGUARDED — widening it back would be caught by nothing here.
        """
        a, _b, _f = drift_repo
        assert "handoff-gamma.md" not in ha.sessions_docs(
            str(a), (SID_DRIFT,))[SID_DRIFT], (
            "an INDENTED quotation of a trailer minted a session->doc edge")

    def test_the_COLUMN_0_trailer_in_the_SAME_body_DOES_mint_one(self, drift_repo):
        """🔴 THE POSITIVE CONTROL FOR THE TEST ABOVE, and it also pins that no
        SHAPE filtering came back: this id is `ses_…`, from the opencode runtime
        (1 of the 36 measured drifting sessions). Without this assertion the
        indented-trailer test is satisfied by a walk that found nothing at all."""
        a, _b, _f = drift_repo
        assert ha.sessions_docs(str(a), (SID_SESO,))[SID_SESO] == (
            "handoff-gamma.md",), (
            "the flush-left trailer in the same body minted no edge — either "
            "the walk is wired to nothing, or `ses_…` ids are being "
            "shape-filtered again")

    def test_a_session_that_wrote_NOTHING_here_is_an_EMPTY_tuple(self, drift_repo):
        a, _b, _f = drift_repo
        assert ha.sessions_docs(
            str(a), (SID_NO_TRANSCRIPT,))[SID_NO_TRANSCRIPT] == ()

    def test_an_UNREADABLE_repo_RAISES_rather_than_returning_empty(self, tmp_path):
        """Same posture as `doc_commits`: an unreadable repo and a session that
        wrote nothing produce the same zero, so they must not be the same
        return."""
        with pytest.raises(ha.GitUnavailable):
            ha.sessions_docs(str(tmp_path / "nope"), (SID_DRIFT,))

    def test_the_SECOND_repo_is_only_reachable_by_WALKING_it(self, drift_repo):
        """🔴 THE KILLER FOR A SEED-REPO-ONLY IMPLEMENTATION. 4 of 36 measured
        drifting sessions cross repos; a lookup scoped to the seed doc's own repo
        reports them as having stayed put."""
        _a, b, _f = drift_repo
        assert ha.sessions_docs(str(b), (SID_DRIFT,))[SID_DRIFT] == (
            "handoff-epsilon.md",)

    def test_the_run_is_INJECTABLE_and_the_function_is_otherwise_pure(self,
                                                                     drift_repo):
        """Same injected-`run` shape as `doc_commits`, so the footer above it can
        be tested without a repo."""
        a, _b, _f = drift_repo
        seen = []

        def run(argv):
            seen.append(argv)
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=60, env=ha._git_env())

        assert ha.sessions_docs(str(a), (SID_DRIFT,), run=run)[SID_DRIFT] == (
            "handoff-beta.md", "handoff-alpha.md")
        assert seen, "the injected runner was never called"


class TestTheCrossArcFooterNamesTheOtherArcs:
    """🔴 THE USER-VISIBLE WIN, ASSERTED ON THE RENDERING.

    Live reproduction before this change: session
    `bf060fd1-43f4-4725-abc3-ebb78c775aa7` has a genesis naming
    `handoff-laptop-airvpn-tunnel.md` and commits on four docs;
    `--arc handoff-mesh-blackouts-laptop-workbench.md` listed it as a member and
    never named the airvpn arc. A reader could not get back.
    """

    @staticmethod
    def _beta_report(monkeypatch, repo_a):
        monkeypatch.setattr(fs, "arc_repo_for",
                            lambda basename: (str(repo_a), BETA))
        monkeypatch.setattr(fs, "archive_search", lambda a, since: [])
        return fs.arc_report("handoff-beta.md")

    def test_the_rendering_NAMES_the_other_doc_AND_its_command(self, drift_repo,
                                                              monkeypatch):
        """🔴 ASSERTED ON `render_arc`, NOT ON A HELPER. A footer computed by a
        pure function and wired up by nothing is this file's recurring defect —
        twice in round 1, three times in round 2."""
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        rendered = fs.render_arc(self._beta_report(monkeypatch, a))
        assert "handoff-alpha.md" in rendered, (
            "the arc of beta never named alpha, which the same session wrote — "
            "the drifting-session defect is unfixed")
        assert "--arc handoff-alpha.md" in rendered, (
            "the other arc was named without a PASTEABLE command, so the reader "
            "still has to re-derive the invocation")
        assert "CROSS-ARC" in rendered

    def test_a_CROSS_REPO_other_arc_is_named_too(self, drift_repo, monkeypatch):
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        rendered = fs.render_arc(self._beta_report(monkeypatch, a))
        assert "--arc handoff-epsilon.md" in rendered, (
            "a doc the member wrote in ANOTHER repo was not named — the footer "
            "walks only the seed doc's repo")

    def test_the_ARC_S_OWN_doc_is_NOT_listed_as_an_other_arc(self, drift_repo,
                                                            monkeypatch):
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        rendered = fs.render_arc(self._beta_report(monkeypatch, a))
        # The FOOTER only — the `NEXT —` line below it legitimately carries
        # `--arc handoff-beta.md`, so a slice to end-of-string is vacuous.
        cross = rendered.split("CROSS-ARC", 1)[1].split("NEXT \u2014", 1)[0]
        assert "--arc handoff-beta.md" not in cross

    def test_REPO_LABELS_ONLY__no_checkout_PATH_reaches_the_footer(self,
                                                                   drift_repo,
                                                                   monkeypatch):
        """Some handles are CLIENT repos and this repo is PUBLIC. `ArcMember` has
        no path field precisely so this is enforced rather than remembered; the
        footer resolves paths itself, so it needs its own guard."""
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        rendered = fs.render_arc(self._beta_report(monkeypatch, a))
        for path in (str(a), str(b), *(str(f) for f in fillers)):
            assert path not in rendered, f"a checkout path leaked: {path}"

    def test_the_footer_sits_BELOW_coverage_and_ABOVE_the_extractor_line(
            self, drift_repo, monkeypatch):
        """Ordering is a behavioural claim here, the same one
        `test_the_command_sits_BELOW_the_coverage_line` makes: the gaps qualify
        the chain the extractor command is about to read."""
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        rendered = fs.render_arc(self._beta_report(monkeypatch, a))
        assert (rendered.index("carry no session id")
                < rendered.index("CROSS-ARC")
                < rendered.index("NEXT —"))


class TestTheCrossArcFooterIsNeverSILENTLYEMPTY:
    """🔴 THIS MODULE'S WHOLE POSTURE, APPLIED TO THE NEW SURFACE.

    `coverage_line` prints `0 of 0` in full because "no line" is
    indistinguishable from "nothing missing". The same holds here: an arc whose
    members wrote nothing else must SAY so.
    """

    def test_an_arc_with_NO_other_docs_still_prints_its_SENTENCE_IN_FULL(
            self, drift_repo, monkeypatch):
        """Gamma's only member is `SID_SESO`, which wrote gamma and nothing else.
        Mirrors `test_the_coverage_line_is_printed_for_an_empty_corpus`."""
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        monkeypatch.setattr(fs, "arc_repo_for",
                            lambda basename: (str(a), GAMMA))
        monkeypatch.setattr(fs, "archive_search", lambda a_, since: [])
        rendered = fs.render_arc(fs.arc_report("handoff-gamma.md"))
        assert "CROSS-ARC" in rendered, (
            "the footer vanished for an arc whose members wrote nothing else — "
            "no line is indistinguishable from nothing missing")
        assert "1 of 1 members have no stamped commit outside this doc" in rendered

    def test_an_arc_with_NO_MEMBERS_AT_ALL_still_prints_the_sentence(self):
        """The `0 of 0` case, on the new surface."""
        empty = ha.ArcReport(doc="handoff-nobody.md", repo="devrc")
        rendered = fs.render_arc(empty, cross=fs.arc_cross_docs(
            empty, env={}, run=None))
        assert "0 of 0 members have no stamped commit outside this doc" in rendered

    # ⚠ TWO TESTS DELETED HERE 2026-10-03, and the deletion is the point.
    # `test_cross_arc_is_PRESENT_AND_EMPTY_in_json_never_absent` and
    # `test_cross_arc_CARRIES_the_other_docs_in_json` asserted a `--json` key
    # that no longer exists: the cross-arc walk is now computed on the HUMAN
    # rendering branch only, so there is nothing on the `--json` path to be
    # present-and-empty ABOUT. They were deleted outright rather than hollowed
    # out — a test retained in a form that can no longer fail for its own reason
    # is worse than a deleted one, because it reads as coverage. What replaces
    # them is the POSITIVE key-set pin below, which is red if either key
    # returns. The programmatic surface for cross-arc data is now
    # `handoff_arc.sessions_docs()` and nothing else.

    def test_the_json_key_set_is_PINNED_and_NO_cross_arc_key_RETURNED(
            self, capsys, drift_repo, monkeypatch):
        """🔴 BACK-COMPAT, AND THE GUARD THAT REPLACES TWO DELETED ONES.

        `run_arc --json`'s comment records that 5 of 6 real consumer sessions
        parsed `members` and discarded everything else. ADDING a top-level key
        is safe; changing `doc` from a string or `members` from a flat array is
        NOT, so both are pinned by TYPE here.

        🔴 THE SET IS PINNED POSITIVELY AND EXACTLY — `==`, not a subset check —
        so this is the assertion that goes red if `cross_arc` or
        `cross_arc_gaps` silently returns. Both were deleted (D1/D2, 2026-10-03)
        because the walk that fed them costs ~4-15s by load and neither key
        ever had a named consumer; a re-add must be a deliberate change to this
        line, not a quiet reappearance. The explicit absence assertions below
        are there so the FAILURE MESSAGE names the hazard rather than just
        dumping two sets.
        """
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        monkeypatch.setattr(fs, "arc_repo_for", lambda basename: (str(a), BETA))
        monkeypatch.setattr(fs, "archive_search", lambda a_, since: [])
        arg = fs.parse_args(["--arc", "handoff-beta", "--json"])
        arg.arc = "handoff-beta.md"
        assert fs.run_arc(arg) == fs.EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        for gone in ("cross_arc", "cross_arc_gaps"):
            assert gone not in payload, (
                f"`{gone}` is back on the --json path. It was deleted with the "
                "walk that feeds it (~4-15s by load, zero named consumers); "
                "if a "
                "caller now exists, NAME it in run_arc and update this pin "
                "deliberately — and do not emit a fabricated empty list, which "
                "is indistinguishable from 'no member wrote another doc'")
        assert set(payload) == {
            "doc", "repo", "members", "total_commits", "unstamped_commits",
            "coverage", "readers_measured", "unmeasured"}, sorted(payload)
        assert isinstance(payload["doc"], str)
        assert isinstance(payload["members"], list)
        assert all(isinstance(m, dict) for m in payload["members"])


class TestUNMEASUREDIsNotEMPTYOnTheNewSurface:
    """🔴 A member whose reverse walk could not run must be reported as NOT
    MEASURED, TEXTUALLY DISTINGUISHABLE from "this member wrote only this doc".
    Mirrors `test_git_failing_is_a_NOTE_not_a_silent_empty_arc`.
    """

    @staticmethod
    def _report():
        r = ha.ArcReport(doc="handoff-beta.md", repo="repo-a", total_commits=2)
        r.members = [ha.ArcMember(SID_DRIFT, ha.ROLE_WROTE),
                     ha.ArcMember(SID_SINGLE, ha.ROLE_WROTE)]
        return r

    def test_a_FAILING_git_walk_reads_NOT_MEASURED_not_zero(self, tmp_path):
        def failing_run(argv):
            raise OSError("git is not available in this test")

        report = self._report()
        cross = fs.arc_cross_docs(report, env={"DEVRC": str(tmp_path)},
                                  run=failing_run)
        rendered = fs.render_arc(report, cross=cross)
        assert "NOT MEASURED" in rendered, (
            "a reverse walk that never ran rendered as 'wrote only this doc' — "
            "the exact measured-vs-unmeasured conflation this module refuses")
        assert "2 of 2 members' other docs were NOT MEASURED" in rendered

    def test_NOT_MEASURED_and_WROTE_ONLY_THIS_DOC_are_DIFFERENT_STRINGS(
            self, drift_repo, monkeypatch, tmp_path):
        """🔴 THE DISTINGUISHABILITY ASSERTION ITSELF. Two renderings of the SAME
        report — one where git answered, one where it could not — must not be
        byte-identical. Mirrors
        `test_zero_stamped_writers_reads_differently_from_UNMEASURED`."""
        a, b, fillers = drift_repo

        def failing_run(argv):
            raise OSError("git is not available in this test")

        report = self._report()
        mapping = _all_handles_on(monkeypatch, a, b, fillers)
        measured = fs.render_arc(report, cross=fs.arc_cross_docs(
            report, env={k: str(v) for k, v in mapping.items()}))
        unmeasured = fs.render_arc(report, cross=fs.arc_cross_docs(
            report, env={"DEVRC": str(a)}, run=failing_run))
        assert measured != unmeasured
        # 🔴 THE TWO READINGS, SPELLED. The NOT-MEASURED line is printed in BOTH
        # (never-silent-zero), so its mere presence cannot be the discriminator —
        # the COUNT and the reason are. A fully-measured walk says so explicitly.
        assert ("0 of 2 members' other docs were NOT MEASURED: every repo "
                "handle answered") in measured, (
            "a fully-measured walk did not say so, so a reader cannot tell it "
            "from one that silently skipped a checkout")
        assert "2 of 2 members' other docs were NOT MEASURED" in unmeasured
        # SID_DRIFT really did write elsewhere, SID_SINGLE did not — so the
        # measured rendering must say 1 of 2, not 0 and not 2.
        assert ("1 of 2 members have no stamped commit outside this doc"
                in measured), measured

    def test_an_UNSET_handle_is_UNMEASURED_not_ZERO(self, drift_repo,
                                                    monkeypatch):
        """🔴 `$CIVITAI_CLI` IS UNSET ON THIS HOST. A handle nobody exported is
        a checkout that was never read; counting it as "this member wrote
        nothing there" is a scoped zero reported as an absence."""
        a, _b, _f = drift_repo
        report = self._report()
        cross = fs.arc_cross_docs(report, env={"DEVRC": str(a)})
        rendered = fs.render_arc(report, cross=cross)
        assert "NOT MEASURED" in rendered
        for handle in hi.REPO_ENV_HANDLES:
            if handle == "DEVRC":
                continue
            assert f"${handle}" in rendered, (
                f"the unset handle ${handle} was silently treated as measured")

    def test_the_handle_names_in_the_footer_are_DERIVED_not_TYPED(self,
                                                                  monkeypatch,
                                                                  drift_repo):
        """🔴 ONE RENDERER over `REPO_ENV_HANDLES`, the same discipline
        `arc_handles_spelled` exists for: a sentence describing the search must
        not be able to name a different set from the one the search walks. A
        SYNTHETIC handle appended to the tuple must appear."""
        a, _b, _f = drift_repo
        monkeypatch.setattr(hi, "REPO_ENV_HANDLES",
                            tuple(hi.REPO_ENV_HANDLES) + ("ZZ_SYNTHETIC",))
        monkeypatch.setattr(fs.handoff_index, "REPO_ENV_HANDLES",
                            tuple(fs.handoff_index.REPO_ENV_HANDLES)
                            + ("ZZ_SYNTHETIC",))
        report = self._report()
        rendered = fs.render_arc(report, cross=fs.arc_cross_docs(
            report, env={"DEVRC": str(a)}))
        assert "$ZZ_SYNTHETIC" in rendered, (
            "a handle the tuple declares was not named — the footer's handle "
            "list is typed rather than derived")


@pytest.fixture()
def narrowed_repo(tmp_path: Path) -> Path:
    """A checkout where `doc_commit_revs` can resolve NO upstream.

    🔴 THE SHAPE THAT MAKES A NARROWED WALK OBSERVABLE, and it is a real one:
    `git init` leaves the branch with no `@{upstream}` and no
    `refs/remotes/origin/HEAD`, which is exactly the state `drift-check.sh`
    rc 18 reports for `tmux-fuzzyclaw` ("a local branch with no upstream") and
    the state a detached handoff worktree leaves behind. One session writes two
    docs; only ONE of them is reachable from the checked-out branch, so a walk
    narrowed to `HEAD` returns a STRICT SUBSET of the true answer.
    """
    r = tmp_path / "narrowed"
    r.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=r)
    _commit(r, ALPHA, "# alpha\n\non main\n",
            f"docs(handoff): alpha on main\n\n{ha.TRAILER_KEY}: {SID_DRIFT}\n")
    # BETA lives on a branch HEAD cannot reach. With an upstream resolved this
    # would still be invisible — the point is that the walk cannot SAY so.
    _sh("git", "checkout", "-q", "-b", "sidebranch", cwd=r)
    _commit(r, BETA, "# beta\n\noff main\n",
            f"docs(handoff): beta off main\n\n{ha.TRAILER_KEY}: {SID_DRIFT}\n")
    _sh("git", "checkout", "-q", "main", cwd=r)
    revs, note = ha.doc_commit_revs(str(r))
    assert revs == ("HEAD",) and note, (
        "the fixture resolved an upstream after all, so every assertion below "
        f"is vacuous: revs={revs!r} note={note!r}")
    return r


@pytest.fixture()
def upstream_repo(tmp_path: Path):
    """A clone with a REAL upstream, one session, two docs. `(work, origin)`.

    🔴 THE NEGATIVE CONTROL'S FIXTURE, AND IT CANNOT BE A `git init`. Every
    other repo fixture in this module is a bare `git init`, which resolves
    neither `@{upstream}` nor `refs/remotes/origin/HEAD` — so a test asserting
    "no note when the walk was complete" over one of those is vacuous: it
    passes because the note is absent for the WRONG reason, or skips. HEAD is
    left ONE commit AHEAD of the pushed branch, so the two revs are distinct
    objects that share history — which is also the shape that keeps the
    per-commit dedup below observable.
    """
    _sh("git", "init", "-q", "--bare", "-b", "main", "origin.git", cwd=tmp_path)
    _sh("git", "clone", "-q", str(tmp_path / "origin.git"), "work", cwd=tmp_path)
    work = tmp_path / "work"
    _commit(work, ALPHA, "# alpha\n\nfirst\n",
            f"docs(handoff): alpha\n\n{ha.TRAILER_KEY}: {SID_DRIFT}\n")
    _sh("git", "push", "-q", "-u", "origin", "main", cwd=work)
    # UNPUSHED, so `HEAD` is strictly ahead — `doc_commit_revs`'s own reason for
    # putting `HEAD` first.
    _commit(work, BETA, "# beta\n\nfirst\n",
            f"docs(handoff): beta\n\n{ha.TRAILER_KEY}: {SID_DRIFT}\n")
    return work, tmp_path / "origin.git"


#: Ids carrying POSIX-ERE metacharacters. 🔴 NOT A SHAPE VIOLATION — an id is an
#: OPAQUE STRING (`session_trailer`'s 🔴, citing a measured case where a shape
#: assumption "silently matches nothing and reports a clean 'no live window'"),
#: and `session_trailer.valid_id` accepts both of these: it rejects what could
#: CORRUPT a commit message, never what an id looks like. So the write side can
#: legitimately stamp either.
#:
#: The first is BALANCED, so without escaping it is a VALID but WRONG ERE —
#: `(a)` a group, `[b]` a class, `.` any, `c*` a quantifier — which matches
#: `id-abXd` and NOT the literal id. That failure is SILENT: the session's own
#: commits stop matching and it reports as having written nothing.
#: The second is UNBALANCED, so without escaping git exits 128
#: (`fatal: Unmatched ( or \(`) and the whole walk raises.
HOSTILE_ID_BALANCED = "id-(a)[b].c*d"
HOSTILE_ID_UNBALANCED = "id-(oops"


@pytest.fixture()
def hostile_id_repo(tmp_path: Path) -> Path:
    r = tmp_path / "hostile"
    r.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=r)
    _commit(r, ALPHA, "# alpha\n\nfirst\n",
            f"docs(handoff): alpha\n\n{ha.TRAILER_KEY}: {HOSTILE_ID_BALANCED}\n")
    _commit(r, BETA, "# beta\n\nfirst\n",
            f"docs(handoff): beta\n\n{ha.TRAILER_KEY}: {HOSTILE_ID_UNBALANCED}\n")
    # A DECOY the unescaped balanced pattern DOES match, so a mutant cannot be
    # green by accident of there being nothing else to hit.
    _commit(r, GAMMA, "# gamma\n\nfirst\n",
            f"docs(handoff): gamma\n\n{ha.TRAILER_KEY}: id-abXd\n")
    _give_upstream(r)
    return r


class TestEREEscapingOfAnOPAQUEId:
    """🔴 `_ere_escape` WAS PINNED BY NOTHING. Mutating it to the identity left
    the suite GREEN (mutant SURVIVED, under `PYTHONDONTWRITEBYTECODE=1` with a
    positive control in the batch): every fixture id in this module is
    `[A-Za-z0-9_-]`-only, so no test could reach the function at all.

    The function is CORRECT; this is the missing guard. 🔴 And the blast radius
    is wider than one id: the patterns are ORed into ONE walk per rev, so a
    single odd id anywhere in the member list takes down the walk for EVERY
    handle and the footer degrades to "n of n NOT MEASURED, all handles could
    not be read" for every member of the arc.

    ⚠ FIXED BY ESCAPING, NEVER BY VALIDATING SHAPE — `session_trailer.py` is
    explicit that shape must not be assumed, and the read side being stricter
    than the write side is a defect this module has already had once.
    """

    def test_a_BALANCED_metacharacter_id_is_matched_EXACTLY(self,
                                                            hostile_id_repo):
        """🔴 THE SILENT HALF. Unescaped, the pattern is a valid ERE that does
        not match its own id — so the session reports as having written nothing
        and `git` exits 0, which is the measured-vs-unmeasured conflation with
        no error anywhere to notice."""
        got = ha.sessions_docs(str(hostile_id_repo), (HOSTILE_ID_BALANCED,))
        assert got[HOSTILE_ID_BALANCED] == ("handoff-alpha.md",), (
            "an id carrying balanced ERE metacharacters did not match its own "
            f"commit — `_ere_escape` is not being applied: {got!r}")

    def test_an_UNBALANCED_PAREN_id_does_not_make_git_EXIT_128(
            self, hostile_id_repo):
        """🔴 THE LOUD HALF, which surfaces as `GitUnavailable` — i.e. as "this
        checkout could not be read" — for a perfectly ordinary session."""
        got = ha.sessions_docs(str(hostile_id_repo), (HOSTILE_ID_UNBALANCED,))
        assert got[HOSTILE_ID_UNBALANCED] == ("handoff-beta.md",), (
            "an id carrying an unmatched `(` did not match its own commit; if "
            "this raised `GitUnavailable` instead, the walk crashed on an "
            f"unescaped metacharacter: {got!r}")

    def test_ONE_hostile_id_does_not_credit_a_DIFFERENT_sessions_doc(
            self, hostile_id_repo):
        """The decoy `id-abXd` is matched by the UNESCAPED balanced pattern.
        `trailer_ids` re-parsing is what stops it being credited, so this pins
        the two halves together rather than either alone."""
        got = ha.sessions_docs(str(hostile_id_repo),
                               (HOSTILE_ID_BALANCED, HOSTILE_ID_UNBALANCED))
        assert "handoff-gamma.md" not in got[HOSTILE_ID_BALANCED], got
        assert "handoff-gamma.md" not in got[HOSTILE_ID_UNBALANCED], got

    def test_ONE_hostile_id_does_not_TAKE_DOWN_THE_WHOLE_ARC(
            self, hostile_id_repo, monkeypatch):
        """🔴 THE BLAST RADIUS, ASSERTED. All the ids go into ONE ORed walk, so
        an unescaped `(` from any single member makes the handle UNREADABLE for
        every member — a footer reading `n of n NOT MEASURED` over an arc whose
        other members are perfectly readable."""
        for mod in (hi, fs.handoff_index):
            monkeypatch.setattr(mod, "REPO_ENV_HANDLES", ("DEVRC",))
        r = ha.ArcReport(doc="handoff-gamma.md", repo="hostile",
                         total_commits=1)
        r.members = [ha.ArcMember(HOSTILE_ID_UNBALANCED, ha.ROLE_WROTE),
                     ha.ArcMember("id-abXd", ha.ROLE_WROTE)]
        cross = fs.arc_cross_docs(r, env={"DEVRC": str(hostile_id_repo)})
        rendered = fs.render_arc(r, cross=cross)
        assert fs.CROSS_ARC_UNREADABLE not in rendered, (
            "one member's odd id made the whole handle unreadable, so every "
            f"member's arc went unmeasured:\n{rendered}")
        assert "--arc handoff-beta.md" in rendered, (
            f"the hostile-id member's other doc was never found:\n{rendered}")
        assert ("0 of 2 members' other docs were NOT MEASURED") in rendered, (
            f"a readable handle was reported as a gap:\n{rendered}")


class TestANarrowedWalkIsAThirdREASONNotACleanOne:
    """🔴 A WALK THAT FELL BACK TO `HEAD` ALONE IS A GAP, AND IT USED TO READ AS
    A CLEAN ANSWER.

    `doc_commit_revs` returns a NOTE precisely when no upstream resolved — i.e.
    when the walk is back to `HEAD` alone and a commit pushed from a worktree is
    invisible. `resolve_arc` surfaces that note for the arc's OWN repo
    (`unmeasured_notes`); `sessions_docs` discarded it, so `arc_cross_docs` had
    no path to it and a structurally narrowed handle was reported as having
    fully answered — `! 0 of n members' other docs were NOT MEASURED: every
    repo handle answered`, over a walk that had provably not read the doc.

    That is the exact conflation `arc_cross_docs`'s own docstring rates 🔴
    ("EVERY GAP IS CARRIED, because the alternative is a scoped zero read as an
    absence"). It is latent on this host only because all four SET handles
    currently resolve an upstream — a property of today's checkouts, not of the
    code.
    """

    @staticmethod
    def _report():
        r = ha.ArcReport(doc="handoff-alpha.md", repo="narrowed",
                         total_commits=1)
        r.members = [ha.ArcMember(SID_DRIFT, ha.ROLE_WROTE)]
        return r

    def test_sessions_docs_RETURNS_the_narrowing_note(self, narrowed_repo):
        """🔴 THE NOTE IS THE WHOLE FIX. Without it the caller cannot tell a
        handle that answered from one that answered about `HEAD` only."""
        got = ha.sessions_docs(str(narrowed_repo), (SID_DRIFT,))
        assert got[SID_DRIFT] == ("handoff-alpha.md",), got
        assert "handoff-beta.md" not in got[SID_DRIFT], (
            "the fixture's off-HEAD doc was reachable after all")
        assert got.narrowed_note, (
            "`sessions_docs` returned a walk narrowed to `HEAD` with no note, "
            "so a caller cannot report the gap — the discarded-note defect")
        assert "HEAD" in got.narrowed_note

    def test_a_FULLY_WALKED_handle_returns_NO_note(self, upstream_repo):
        """🔴 THE NEGATIVE CONTROL, AND IT NEEDS A REAL UPSTREAM.

        A note on EVERY call would make the new reason fire for every handle and
        the footer permanently red, which is a gate nobody reads. This is the
        assertion that would catch that, so the fixture must be one where an
        upstream genuinely resolves — `drift_repo`'s bare `git init` repos do
        not, which is why this does not use them.
        """
        work, _origin = upstream_repo
        revs, note = ha.doc_commit_revs(str(work))
        assert note is None and len(revs) == 2, (
            f"the fixture resolved no upstream, so this control is vacuous: "
            f"revs={revs!r} note={note!r}")
        got = ha.sessions_docs(str(work), (SID_DRIFT,))
        assert got.narrowed_note is None, (
            f"a handle that walked {revs!r} reported itself narrowed")
        assert got[SID_DRIFT] == ("handoff-beta.md", "handoff-alpha.md"), got

    def test_an_UPSTREAM_THAT_IS_HEAD_is_ONE_REV_and_STILL_NOT_narrowed(
            self, upstream_repo):
        """🔴 THE DISTINCTION THE NOTE EXISTS FOR, now that identical revs are
        collapsed. `revs == ("HEAD",)` has TWO causes — no upstream resolved
        (a gap) and the upstream IS this commit (no gap) — so a caller reading
        `len(revs)` would report a healthy, fully-pushed clone as narrowed, on
        every handle that is up to date. Only the note tells them apart."""
        work, _origin = upstream_repo
        _sh("git", "push", "-q", "origin", "main", cwd=work)
        revs, note = ha.doc_commit_revs(str(work))
        assert revs == ("HEAD",), (
            f"two revs resolving to one commit were not collapsed: {revs!r}")
        assert note is None, (
            "a clone whose upstream IS its HEAD was reported as narrowed — the "
            "rev COUNT was used as the coverage signal")
        got = ha.sessions_docs(str(work), (SID_DRIFT,))
        assert got.narrowed_note is None
        assert got[SID_DRIFT] == ("handoff-beta.md", "handoff-alpha.md"), (
            f"collapsing the duplicate rev changed the ANSWER: {got!r}")

    def test_the_footer_names_the_HANDLE_and_says_narrowed_to_HEAD(
            self, narrowed_repo):
        report = self._report()
        cross = fs.arc_cross_docs(report, env={"DEVRC": str(narrowed_repo)})
        assert ("DEVRC", fs.CROSS_ARC_NARROWED) in cross["unmeasured_handles"], (
            f"the narrowed handle is not carried as a gap: {cross!r}")
        rendered = fs.render_arc(report, cross=cross)
        assert "$DEVRC" in rendered
        assert fs.CROSS_ARC_NARROWED in rendered
        assert "HEAD" in rendered
        assert "1 of 1 members' other docs were NOT MEASURED" in rendered, (
            "a narrowed handle left every member's doc set incomplete and the "
            "footer still counted zero unmeasured members")
        assert "every repo handle answered" not in rendered, (
            "the footer affirmatively claimed a complete walk over a handle "
            "that provably read `HEAD` alone")

    def test_NARROWED_is_a_THIRD_reason_distinct_from_UNSET_and_UNREADABLE(
            self, narrowed_repo, drift_repo, monkeypatch):
        """🔴 THREE READINGS, THREE STRINGS, EACH IN ISOLATION. "I never
        looked", "I looked and git refused" and "I looked at `HEAD` only" are
        different facts, and the operator's next action differs for each: export
        the handle, fix the checkout, or fetch the upstream.

        🔴 `REPO_ENV_HANDLES` IS NARROWED TO ONE ENTRY HERE, and that is what
        makes the exclusion assertions bite. Over the real five-handle tuple
        every rendering also carries four UNSET handles, so `CROSS_ARC_UNSET`
        appears in all three and "two of these do not mention the third's
        reason" is unprovable — the kind of assertion that passes for a reason
        unrelated to its claim.
        """
        a, _b, _f = drift_repo
        for mod in (hi, fs.handoff_index):
            monkeypatch.setattr(mod, "REPO_ENV_HANDLES", ("DEVRC",))

        def failing_run(argv):
            raise OSError("git is not available in this test")

        report = self._report()
        narrowed = fs.render_arc(report, cross=fs.arc_cross_docs(
            report, env={"DEVRC": str(narrowed_repo)}))
        unset = fs.render_arc(report, cross=fs.arc_cross_docs(report, env={}))
        unreadable = fs.render_arc(report, cross=fs.arc_cross_docs(
            report, env={"DEVRC": str(a)}, run=failing_run))
        assert len({narrowed, unset, unreadable}) == 3, (
            "two of the three gap readings render identically")
        reasons = (fs.CROSS_ARC_NARROWED, fs.CROSS_ARC_UNSET,
                   fs.CROSS_ARC_UNREADABLE)
        for mine, rendered in zip(reasons, (narrowed, unset, unreadable)):
            assert mine in rendered, f"{mine!r} never printed:\n{rendered}"
            for other in reasons:
                if other == mine:
                    continue
                assert other not in rendered, (
                    f"the reason {other!r} leaked into the {mine!r} rendering, "
                    f"so the three are not distinguishable:\n{rendered}")

    def test_a_narrowed_handle_STILL_CONTRIBUTES_the_docs_it_DID_find(
            self, narrowed_repo):
        """🔴 NOT SHORT-CIRCUITED, unlike UNSET and UNREADABLE. The union over
        revs can only ADD writers (`doc_commit_revs`'s own 🔴), so the docs a
        narrowed walk found are real; discarding them would turn a partial
        answer into no answer. The gap is reported ALONGSIDE them."""
        r = ha.ArcReport(doc="handoff-beta.md", repo="narrowed",
                         total_commits=1)
        r.members = [ha.ArcMember(SID_DRIFT, ha.ROLE_WROTE)]
        cross = fs.arc_cross_docs(r, env={"DEVRC": str(narrowed_repo)})
        docs = [d for e in cross["entries"] for d in e["docs"]]
        assert docs == ["handoff-alpha.md"], (
            f"a narrowed walk's real findings were dropped: {cross!r}")
        rendered = fs.render_arc(r, cross=cross)
        assert "--arc handoff-alpha.md" in rendered
        assert fs.CROSS_ARC_NARROWED in rendered


class TestTheORDINARYPathPaysNOTHINGForThis:
    """🔴 `arc_writer_counts` / `arc_annotation` run on the NON-`--arc` path under
    a measured budget (`MAX_ANNOTATION_DOC_WALKS = 12`, +3.24s over 11 docs). The
    reverse lookup over the four readable handles costs ~4-15s for a six-member
    arc (~4s at load ~5, ~15s at load ~18; 2026-10-03, laptop), so a 20-hit
    query that reached it would be minutes. Same shape as
    `test_a_doc_that_costs_NO_git_walk_does_not_spend_budget`.

    ⚠ ONLY `sessions_docs` IS PATCHED NOW. The singular `session_docs` was
    deleted (D3, 2026-10-03), and `monkeypatch.setattr` on a name the module no
    longer defines raises `AttributeError` — which would have turned both of
    these green-for-the-wrong-reason into collection-time errors.
    """

    def test_arc_writer_counts_makes_NO_sessions_docs_CALL(self, monkeypatch):
        calls = []

        def forbidden(*args, **kwargs):
            calls.append(args)
            raise AssertionError(
                "the ordinary annotation path called the reverse session->doc "
                "lookup; that is seconds per arc-shaped call and the budget is "
                "measured in doc walks")

        monkeypatch.setattr(fs.handoff_arc, "sessions_docs", forbidden)
        rows = [{"genesis": f"claudedocs/handoff-b{i}.md"} for i in range(20)]
        fs.arc_writer_counts(rows, repo_lookup=lambda b: (None, None))
        assert calls == []

    def test_arc_annotation_makes_NO_sessions_docs_CALL(self, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError("arc_annotation reached the reverse lookup")

        monkeypatch.setattr(fs.handoff_arc, "sessions_docs", forbidden)
        assert fs.arc_annotation({"genesis": "claudedocs/handoff-q.md"},
                                 {"handoff-q.md": 3})


class TestTheJSONPathPaysNOTHINGForThisEITHER:
    """🔴 D2, ASSERTED ON CONTROL FLOW RATHER THAN ON A COMMENT.

    `run_arc` used to hoist `cross = arc_cross_docs(report)` ABOVE `if a.json:`,
    so every machine consumer paid the reverse walk to populate two keys none
    of them read. MEASURED 2026-10-03 on the laptop, this arc's shape (6
    members, 4 readable handles): an `--arc --json` run went 25.31s -> 23.79s
    median, 1.52s saved. ⚠ The walk in ISOLATION costs ~4s at load ~5 and ~15s
    at load ~18 — a ~4x swing with load, which is why the end-to-end delta is
    the number quoted here and the ratio over per-member (~3x) is the claim
    made about the DESIGN. Both keys are gone and so is the call.

    🔴 CALL-RECORDING, NOT AN AST READ OF THE SOURCE. Same shape as
    `test_a_doc_that_costs_NO_git_walk_does_not_spend_budget`: a structural
    check on the text passes while a second caller one frame down still walks.
    And the pair below is a POSITIVE-AND-NEGATIVE control — the zero on the
    `--json` path means nothing unless the same recorder counts non-zero on the
    human path, which would otherwise be indistinguishable from a recorder
    wired to nothing.
    """

    @staticmethod
    def _recorder(monkeypatch):
        """Count `sessions_docs` calls, still returning the real answer."""
        real = fs.handoff_arc.sessions_docs
        seen = []

        def counted(repo, session_ids, run=None):
            ids = tuple(session_ids)
            seen.append((repo, ids))
            return real(repo, ids, run=run)

        monkeypatch.setattr(fs.handoff_arc, "sessions_docs", counted)
        return seen

    @staticmethod
    def _git_recorder(monkeypatch):
        """Record every REVERSE-WALK `git log` invocation, still running it.

        🔴 THE UNIT HAS TO BE A GIT INVOCATION, NOT A `sessions_docs` CALL.
        Counting the Python call could not see the thing the docstring claimed:
        `sessions_docs` loops over `doc_commit_revs`'s revs, so ONE call issues
        up to TWO walks per handle. The old guard read 1 either way, and the
        fixtures it used had no upstream, so even a git-level counter would have
        read 1 there — the claim and the measurement were about different
        things in two independent ways.
        """
        real = fs.handoff_arc._git
        walks = []

        def counted(repo, args, run=None):
            if "--name-only" in args and any(
                    a.startswith("--grep=") for a in args):
                walks.append((repo, tuple(args)))
            return real(repo, args, run=run)

        monkeypatch.setattr(fs.handoff_arc, "_git", counted)
        return walks

    def test_the_JSON_path_makes_ZERO_reverse_walk_calls(self, capsys,
                                                         drift_repo,
                                                         monkeypatch):
        a, b, fillers = drift_repo
        _all_handles_on(monkeypatch, a, b, fillers)
        monkeypatch.setattr(fs, "arc_repo_for", lambda basename: (str(a), BETA))
        monkeypatch.setattr(fs, "archive_search", lambda a_, since: [])
        seen = self._recorder(monkeypatch)
        arg = fs.parse_args(["--arc", "handoff-beta", "--json"])
        arg.arc = "handoff-beta.md"
        assert fs.run_arc(arg) == fs.EXIT_OK
        capsys.readouterr()
        assert seen == [], (
            "the --json path walked the session->doc edge after all — that is "
            f"seconds charged to a consumer that reads no cross-arc key: {seen!r}")

    def test_the_HUMAN_path_makes_ONE_GIT_WALK_PER_REV_PER_HANDLE(
            self, capsys, drift_repo, monkeypatch):
        """🔴 THE POSITIVE CONTROL, AND THE ORed-PASS PROPERTY, MEASURED IN GIT
        INVOCATIONS.

        🔴 THE UNIT IS A `git log` CALL AND THE EXPECTATION IS DERIVED. The
        shipped docstrings said "ONE `--grep` PASS PER SET HANDLE" and the guard
        counted `sessions_docs` CALLS — a different unit, and the prose was
        simply false: the function loops over `doc_commit_revs`'s revs, so a
        handle whose upstream differs from `HEAD` pays TWO walks. The old guard
        could not see that twice over, because its fixtures had no upstream and
        so resolved one rev regardless. `drift_repo` now gives `repo-a` an
        upstream one commit BEHIND (two distinct revs) and the rest one ON
        `HEAD` (collapsed to one), so the expectation is a NON-UNIFORM number
        this test asks git for rather than typing.

        What the count still catches: a per-MEMBER implementation (`members x
        revs x handles`, ~3x slower), a handle walked twice over the same rev,
        and a regression in the identical-rev collapse. The id-set assertion is
        what makes the count mean "one ORed pass" rather than "one pass that
        forgot four members".
        """
        a, b, fillers = drift_repo
        mapping = _all_handles_on(monkeypatch, a, b, fillers)
        readable = [h for h in hi.REPO_ENV_HANDLES if mapping.get(h)]
        # DERIVED: ask each readable checkout how many revs it resolves.
        want_walks = sum(len(ha.doc_commit_revs(str(mapping[h]))[0])
                         for h in readable)
        assert want_walks > len(readable), (
            "every readable handle resolved a single rev, so this count cannot "
            "tell one ORed pass from a second walk — the exact blindness this "
            f"test was rewritten to remove: {want_walks} over {len(readable)}")
        monkeypatch.setattr(fs, "arc_repo_for", lambda basename: (str(a), BETA))
        monkeypatch.setattr(fs, "archive_search", lambda a_, since: [])
        seen = self._recorder(monkeypatch)
        walks = self._git_recorder(monkeypatch)
        arg = fs.parse_args(["--arc", "handoff-beta"])
        arg.arc = "handoff-beta.md"
        assert fs.run_arc(arg) == fs.EXIT_OK
        rendered = capsys.readouterr().out
        assert "CROSS-ARC" in rendered, "the human path printed no footer"
        assert len(seen) == len(readable), (
            f"expected exactly one `sessions_docs` call per readable handle "
            f"({len(readable)}), got {len(seen)} — a per-MEMBER walk: "
            f"{[(r, len(i)) for r, i in seen]}")
        assert len(walks) == want_walks, (
            f"expected {want_walks} reverse-walk `git log` invocations (one per "
            f"rev per readable handle, derived from `doc_commit_revs`), got "
            f"{len(walks)}: {[r for r, _ in walks]}")
        report = fs.arc_report("handoff-beta.md")
        want = {m.session_id for m in report.members}
        assert want, "fixture produced no members, so the count below is vacuous"
        for repo, ids in seen:
            assert set(ids) == want, (
                f"a pass over {repo!r} carried {set(ids)!r}, not every member "
                f"{want!r} — the ORed one-pass property is gone")
        # Every GIT walk must carry every member too — the `sessions_docs`
        # assertion above cannot see a per-member loop INSIDE the function.
        for repo, args in walks:
            greps = [x for x in args if x.startswith("--grep=")]
            assert len(greps) == len(want), (
                f"a walk over {repo!r} ORed {len(greps)} patterns, not "
                f"{len(want)} — the ids are being passed one at a time")


class TestTheExit2CauseListNamesTheCROSSHOSTCause:
    """🔴 THE SHIPPED CAUSE LIST WAS A FALSE CLAIM BY OMISSION.

    It offered two causes for a UUID seed that resolves to nothing — "never
    handed a handoff doc" and "transcript pruned" — and omitted the MEASURED
    DOMINANT one: the transcript lives on the OTHER HOST. Measured 2026-10-03
    from the laptop (`peer-host ssh-target --json` reports `laptop: via local`,
    `workbench: via zach@…`): of the stamped writer sessions, only a handful
    have a transcript here and the large majority are on the workbench. So the
    sentence named the rare causes and skipped the common one — and it is the
    one an agent reads before deciding the seed was bad.

    ⚠ WHAT THESE GUARDS PIN IS THE CAUSE AND THE ESCAPE, NOT A COUNT, and that
    is a correction. The shipped string used to carry four corpus literals
    (`6`/`245`/`291`/`40`) and the guard was `assert "245" in err` — a literal
    pinned to a literal, which re-measures nothing and would have stayed GREEN
    through any corpus drift while the sentence it blessed went false. The
    ranking ("dominant cause is the other host") and the `git log --grep`
    escape are the half that is actionable and the half that makes the sentence
    TRUE, so they are what is asserted.

    Verified live at `3e7725bc`: `--arc 0049ef1b-…` refuses, while the git
    reverse lookup answers instantly from `$DATAPACKET`.
    """

    def test_a_seed_with_NO_LOCAL_transcript_is_refused_naming_the_PEER(
            self, tmp_path, monkeypatch, capsys):
        proj = tmp_path / "-home-zach-workspace-devrc"
        proj.mkdir()
        monkeypatch.setattr(fs, "ROOT", tmp_path)
        rc = fs.main(["--arc", SID_NO_TRANSCRIPT])
        assert rc == fs.EXIT_USAGE
        err = capsys.readouterr().err
        assert "OTHER HOST" in err, (
            "the refusal still omits the measured DOMINANT cause — most "
            "stamped writers' transcripts are on the peer host — so an agent "
            "reads it as 'the seed was bad'")
        # 🔴 THE CAUSE AS A RANKING, not a census. These three phrases are what
        # make the sentence an instruction rather than a shrug: it is the
        # dominant cause, it is NOT the seed, and here is who the peer is.
        assert "MEASURED DOMINANT cause" in err, (
            "the other-host cause is mentioned without being RANKED, so a "
            "reader cannot tell it from the two rare causes listed after it")
        assert "not that the seed is wrong" in err, (
            "the refusal names a cause but does not exonerate the seed, which "
            "is the inference an agent actually draws from an exit 2")
        assert "peer-host ssh-target --json" in err, (
            "the refusal says 'the other host' without naming the command "
            "that resolves WHICH host")
        # 🔴 AND NO CORPUS CENSUS CAME BACK. A count in this string is pinned to
        # nothing that re-measures it, so it goes stale silently and green.
        for stale in ("245", "291"):
            assert stale not in err, (
                f"a hardcoded corpus count ({stale}) is back in the refusal "
                "string; it is re-measured by nothing and will go stale green")

    def test_the_refusal_POINTS_AT_the_git_derived_path(self, tmp_path,
                                                        monkeypatch, capsys):
        """🔴 A CAUSE WITHOUT A NEXT STEP IS STILL A DEAD END. The git reverse
        lookup needs no transcript at all, so the refusal must name it."""
        monkeypatch.setattr(fs, "ROOT", tmp_path)
        assert fs.main(["--arc", SID_NO_TRANSCRIPT]) == fs.EXIT_USAGE
        err = capsys.readouterr().err
        assert ha.TRAILER_KEY in err and "--grep" in err, (
            "the refusal names the cross-host cause but no way around it")
        # 🔴 A FOURTH PROSE SITE NAMING THE HANDLES, and it must be DERIVED like
        # the three `TestTheHandleProseNamesEveryHandle` pins — the refusal now
        # tells the operator which checkouts to ask, so a stale copy would send
        # them to four of five.
        # ⚠ `", "`, NOT `"/"`. The `/` join rendered as
        # `$DEVRC/$HOMELAB/$DATAPACKET/$CIVITAI/$CIVITAI_CLI checkout`, which an
        # operator reads as a filesystem PATH rather than five alternatives.
        # Still DERIVED — the separator moved, the renderer did not.
        assert fs.arc_handles_spelled(", ") in err, (
            "the git-derived next step names a handle list that is typed "
            "rather than rendered from REPO_ENV_HANDLES")
        # 🔴 AND THE PASTEABLE COMMAND MUST MATCH THE SHIPPED READER. These two
        # are the measured divergences: a literal space where `_trailer_grep`
        # uses `[[:space:]]*`, and `--all`, which `sessions_docs`'s docstring
        # rejects because it credits unmerged branches no reader can see. The
        # printed command was therefore both narrower and wider than the code.
        assert "[[:space:]]*" in err, (
            "the pasteable command matches a literal single space where the "
            "shipped reader matches any blank run — it would miss a trailer "
            "this tool finds")
        assert f"--grep='^{ha.TRAILER_KEY}: " not in err, (
            "the literal-space pattern is back")
        assert "--all" not in err, (
            "the pasteable command passes `--all`, which credits commits on "
            "unmerged branches no shipped reader can see — a wider answer than "
            "the tool's own")
        assert "@{upstream}" in err, (
            "the command does not spell the revs `doc_commit_revs` resolves")

    def test_a_seed_WITH_a_local_transcript_still_RESOLVES(self, tmp_path):
        """The positive control: the refusal path must not have widened."""
        proj = tmp_path / "-home-zach-workspace-devrc"
        proj.mkdir()
        (proj / f"{SID_DRIFT}.jsonl").write_text(
            '{"type":"user","message":{"content":"/resume — Canonical handoff '
            '(read first): ~/w/devrc/claudedocs/handoff-alpha.md"}}\n',
            encoding="utf-8")
        assert fs.arc_seed_to_doc(SID_DRIFT, root=tmp_path) == "handoff-alpha.md"
