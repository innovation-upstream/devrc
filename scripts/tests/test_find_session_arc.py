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
        body = ("subject\n\nClaude-Session-Id: bbb\n\n* other\n\n"
                "Claude-Session-Id: aaa\n")
        assert ha.trailer_ids(body) == ("bbb", "aaa")

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
        the FIRST corpus measurement in this arc came out wrong."""
        commits = ha.doc_commits(str(arc_repo), DOC)
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
        assert "/" not in m.repo

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
        assert "(3 sessions)" in fs.arc_annotation(
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
