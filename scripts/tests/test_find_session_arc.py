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
        rendered = fs.render_arc(report)
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
        rendered = fs.render_arc(self._report())
        assert fs.EXTRACTOR_REL in rendered
        assert "--arc handoff-arc-fixture.md" in rendered

    def test_the_command_carries_the_RESOLVED_doc_not_the_users_seed(self):
        """The seed may be a slug, a path, or a SESSION ID; only `report.doc` is
        the basename that resolved. Echoing the seed back would print a command
        whose `--arc` re-runs the session-id indirection for no reason, and would
        be flatly wrong for a seed that resolved via a genesis message."""
        rendered = fs.render_arc(self._report(doc="handoff-real-slug.md"))
        assert "--arc handoff-real-slug.md" in rendered

    def test_a_MEASURED_EMPTY_arc_names_NO_command(self):
        """🔴 NOT COSMETIC. The extractor exits non-zero on an arc that resolved
        with no members, so a command printed here is an invitation that cannot
        answer — the reassuring-command shape the coverage line exists to refuse.
        Asserted on the WHOLE rendering, not just the absence of a heading: a
        partial line still tells an agent the tool applies."""
        empty = ha.ArcReport(doc="handoff-arc-fixture.md", repo="devrc")
        rendered = fs.render_arc(empty)
        assert fs.extractor_next_command(empty) is None
        assert fs.EXTRACTOR_REL not in rendered
        assert "extract_user_msgs" not in rendered
        assert "NEXT" not in rendered

    def test_the_member_COUNT_is_the_real_one_and_singular_reads_right(self):
        """Pins the count against `len(members)` rather than a constant, and uses
        1 and 3 — never 2 alone, which cannot see a hardcoded plural."""
        assert "1 session of this arc" in fs.render_arc(self._report(1))
        assert "3 sessions of this arc" in fs.render_arc(self._report(3))

    def test_the_command_sits_BELOW_the_coverage_line(self):
        """The gaps qualify the chain the command extracts from. An agent that
        reads the command first and stops has skipped them, so ordering is a
        behavioural claim, not layout."""
        rendered = fs.render_arc(self._report())
        assert rendered.index("carry no session id") < rendered.index("NEXT —")

    # ---------------- the seam: the printed line must actually work ----------- #

    def test_the_NAMED_PATH_EXISTS_in_this_repo(self):
        """🔴 SEAM GUARD. `EXTRACTOR_REL` is a string; nothing else in this module
        would notice the script being renamed or moved, and the footer would keep
        printing a confident path to a file that is not there."""
        target = Path(__file__).resolve().parents[1] / fs.EXTRACTOR_REL.split(
            "scripts/", 1)[1]
        assert target.is_file(), (
            f"the arc footer names {fs.EXTRACTOR_REL}, which does not exist")

    def test_the_printed_SEED_is_one_the_EXTRACTOR_ACCEPTS(self):
        """🔴 SEAM GUARD, and the one a render test structurally cannot make.

        The footer promises `--arc <report.doc>` works. ⚠ An earlier version of this
        guard asserted `fs.arc_seed_to_doc(doc) == doc` and NEVER IMPORTED the
        extractor, so it was blind to the only way this seam actually breaks — the
        extractor ceasing to route its seed through that function. It read as a seam
        guard and was a unit test of `find-session` alone. It now loads
        `extract_user_msgs` the way the extractor's own `--arc` path does and
        asserts the function object is SHARED, not merely that two spellings agree.
        """
        import importlib.util as _ilu
        src = Path(__file__).resolve().parents[1] / fs.EXTRACTOR_REL.split(
            "scripts/", 1)[1]
        spec = _ilu.spec_from_file_location("eum_seam", str(src))
        eum = _ilu.module_from_spec(spec)
        spec.loader.exec_module(eum)

        # The extractor loads find-session through its OWN loader; that module
        # object is what its `--arc` path calls. Assert the seam by IDENTITY.
        their_fs = eum._load_find_session()
        assert hasattr(their_fs, "arc_seed_to_doc"), (
            "the extractor's find-session handle has no `arc_seed_to_doc` — the "
            "footer's `--arc <doc>` promise has no resolver behind it")
        doc = "handoff-arc-fixture.md"
        assert their_fs.arc_seed_to_doc(doc) == doc, (
            f"the extractor's own resolver rejects {doc!r}, which is exactly what "
            "every footer this module prints hands to it")
        # ...and that the extractor really does depend on it, so a future rewrite
        # that stops calling it fails HERE rather than silently at runtime.
        body = src.read_text(encoding="utf-8")
        assert "arc_seed_to_doc" in body, (
            "the extractor no longer names `arc_seed_to_doc`; the footer's seed "
            "contract is now unenforced — re-point this guard at the new resolver")


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
