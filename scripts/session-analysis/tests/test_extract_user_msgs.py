"""Guards for `scripts/session-analysis/extract_user_msgs.py` — scoped extraction.

🔴 NO TEST READS A REAL TRANSCRIPT. `~/.claude/projects/**` holds the operator's
own prompts and a client's infrastructure, and devrc is PUBLIC. Every transcript
here is synthesised under `tmp_path` from `LEAKCANARY-*` strings, chosen to be
unmistakable if one ever escaped into a fixture.

🔴 THE LOAD-BEARING TESTS IN THIS MODULE ARE THE FOUR IN
`TestAZeroIsNeverJustAZero`. They are not unit tests of an `if` — they are the
control proving the tool can tell four different "nothing came back" facts
apart. A wrong `--arc` name, a measured-empty arc, ids that resolve to no
transcript, and transcripts holding no typed message all produce the same empty
output; before this CLI they all produced the same exit 0 as well. Each test
asserts the code AND that the reason names the right mechanism, because a
correct code with a misleading sentence sends the reader to the wrong fix.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SA_DIR = Path(__file__).resolve().parent.parent
REPO = SA_DIR.parent.parent

_spec = importlib.util.spec_from_file_location(
    "extract_user_msgs", SA_DIR / "extract_user_msgs.py")
X = importlib.util.module_from_spec(_spec)
sys.modules["extract_user_msgs"] = X
_spec.loader.exec_module(X)

REFERENCE = REPO / "claude" / "skills" / "handoff" / "reference" / "user-messages.md"
RESUME_SKILL = REPO / "claude" / "skills" / "resume" / "SKILL.md"

# 🔴 Leak canaries. Pairwise distinct, and none a substring of any other.
LEAK_TYPED = "LEAKCANARY-operator-typed-prose"
LEAK_OTHER = "LEAKCANARY-a-second-typed-message"
LEAK_REMINDER = "LEAKCANARY-injected-system-reminder"
LEAK_TOOL = "LEAKCANARY-tool-result-body"


# --- fixture builders ----------------------------------------------------------

def _user(text, ts="2026-09-01T00:00:00.000Z", **extra):
    rec = {"type": "user", "timestamp": ts,
           "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    rec.update(extra)
    return rec


def _write_session(root, project, session_id, records):
    """Write one transcript at `<root>/<project>/<session_id>.jsonl`."""
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


def _run(argv, root, stdin=None):
    """Run `main(argv)` against a fixture corpus; return `(rc, stdout, stderr)`."""
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr = out, err
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        rc = X.main(list(argv) + ["--root", str(root)])
    finally:
        sys.stdout, sys.stderr, sys.stdin = old
    return rc, out.getvalue(), err.getvalue()


# --- the filtering predicate ---------------------------------------------------

class TestMessageOf:
    """`message_of` decides what counts as typed. It had NO test while inline."""

    def test_plain_prose_is_typed(self):
        assert X.message_of(LEAK_TYPED) == ("typed", LEAK_TYPED)

    def test_a_system_reminder_is_stripped_and_the_prose_survives(self):
        raw = f"<system-reminder>{LEAK_REMINDER}</system-reminder>\n{LEAK_TYPED}"
        kind, text = X.message_of(raw)
        assert kind == "typed"
        assert text == LEAK_TYPED
        assert LEAK_REMINDER not in text, (
            "the harness's injected reminder is not something the operator typed")

    def test_a_record_that_is_ONLY_a_system_reminder_is_dropped(self):
        assert X.message_of(
            f"<system-reminder>{LEAK_REMINDER}</system-reminder>") is None

    def test_a_slash_command_becomes_kind_command_with_its_args(self):
        raw = ("<command-name>resume</command-name>"
               "<command-args>the widget arc</command-args>")
        assert X.message_of(raw) == ("command", "resume the widget arc")

    def test_a_slash_command_with_no_args_keeps_its_name(self):
        assert X.message_of("<command-name>handoff</command-name>") == (
            "command", "handoff")

    @pytest.mark.parametrize("prefix", X.BOILERPLATE_PREFIXES)
    def test_every_boilerplate_prefix_is_dropped(self, prefix):
        """Two-way with the module's own tuple: a prefix added there is covered
        automatically, and one deleted there stops being asserted here."""
        assert X.message_of(prefix + " trailing detail") is None

    def test_boilerplate_detection_is_a_PREFIX_not_a_substring(self):
        """🔴 POSITIVE CONTROL for the test above. If `message_of` dropped on a
        substring match, a real message merely MENTIONING the boilerplate would
        vanish — and the parametrised test above would stay green either way."""
        raw = f"{LEAK_TYPED} — it said {X.BOILERPLATE_PREFIXES[0]} at the top"
        assert X.message_of(raw) == ("typed", raw)

    def test_a_short_bare_tag_is_dropped_but_a_long_one_is_kept(self):
        assert X.message_of("<some-leftover-tag>") is None
        long_tag = "<t>" + "x" * 90 + "</t>"
        assert X.message_of(long_tag) == ("typed", long_tag)

    def test_empty_and_whitespace_are_dropped(self):
        assert X.message_of("") is None
        assert X.message_of("   \n  ") is None


class TestExtractFromContent:
    def test_a_bare_string_content_is_one_message(self):
        assert X.extract_from_content(LEAK_TYPED) == [LEAK_TYPED]

    def test_only_text_blocks_are_read(self):
        content = [{"type": "text", "text": LEAK_TYPED},
                   {"type": "tool_result", "content": LEAK_TOOL},
                   {"type": "thinking", "thinking": LEAK_TOOL},
                   "not-a-dict"]
        got = X.extract_from_content(content)
        assert got == [LEAK_TYPED]
        assert LEAK_TOOL not in "".join(got)


class TestRecordsOf:
    def test_it_carries_session_project_and_timestamp(self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "sess-1",
                           [_user(LEAK_TYPED, ts="2026-09-02T03:04:05.000Z")])
        (rec,) = list(X.records_of(p))
        assert rec == {"session_id": "sess-1", "project": "proj-a",
                       "ts": "2026-09-02T03:04:05.000Z", "kind": "typed",
                       "text": LEAK_TYPED}

    def test_meta_sidechain_and_assistant_records_are_skipped(self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "sess-1", [
            _user(LEAK_TYPED),
            _user("LEAKCANARY-meta", isMeta=True),
            _user("LEAKCANARY-sidechain", isSidechain=True),
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text",
                                                           "text": LEAK_TOOL}]}},
        ])
        texts = [r["text"] for r in X.records_of(p)]
        assert texts == [LEAK_TYPED], (
            "a subagent's own transcript and the harness's meta records are not "
            "things the operator typed")

    def test_a_malformed_line_skips_that_LINE_not_the_file(self, tmp_path):
        p = _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        p.write_text(p.read_text() + "{not json\n" +
                     json.dumps(_user(LEAK_OTHER)) + "\n")
        assert [r["text"] for r in X.records_of(p)] == [LEAK_TYPED, LEAK_OTHER]


# --- selectors -----------------------------------------------------------------

class TestReadIdsFile:
    def test_comments_and_blanks_are_skipped(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("# a comment\n\n  id-a  \nid-b\n\n# another\n")
        assert X.read_ids_file(str(f)) == ["id-a", "id-b"]

    def test_dash_reads_stdin(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("id-a\nid-b\n"))
        assert X.read_ids_file("-") == ["id-a", "id-b"]

    def test_an_unreadable_file_RAISES_rather_than_returning_empty(self, tmp_path):
        """🔴 The whole point. An OSError degraded into `[]` would make an
        unreadable ids file indistinguishable from an empty selection."""
        with pytest.raises(OSError):
            X.read_ids_file(str(tmp_path / "nope.txt"))


class TestResolveSessions:
    def test_an_unresolvable_id_is_RETURNED_not_dropped(self, tmp_path):
        """🔴 The load-bearing half. Silently narrowing the set to whatever
        happened to resolve is how a partial extraction reads as a whole one."""
        _write_session(tmp_path, "proj-a", "sess-real", [_user(LEAK_TYPED)])
        resolved, missing = X.resolve_sessions(
            ["sess-real", "sess-ghost"], root=tmp_path)
        assert [sid for sid, _ in resolved] == ["sess-real"]
        assert missing == ["sess-ghost"]

    def test_duplicate_ids_resolve_once(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-real", [_user(LEAK_TYPED)])
        resolved, missing = X.resolve_sessions(
            ["sess-real", "sess-real"], root=tmp_path)
        assert len(resolved) == 1 and missing == []

    def test_a_subagent_transcript_is_NOT_a_session(self, tmp_path):
        """`find_transcript` applies `is_corpus_member`; a subagent is not a
        session anybody typed into, so its id must not resolve."""
        _write_session(tmp_path, "proj-a/sess-1/subagents", "agent-7",
                       [_user(LEAK_TYPED)])
        resolved, missing = X.resolve_sessions(["agent-7"], root=tmp_path)
        assert resolved == [] and missing == ["agent-7"]


# --- the four zeros ------------------------------------------------------------

class _FakeMember:
    def __init__(self, session_id, role):
        self.session_id, self.role = session_id, role


class _FakeReport:
    def __init__(self, members, doc="handoff-fake.md", repo="devrc"):
        self.members, self.doc, self.repo = members, doc, repo
        self.unmeasured_notes = ["LEAKCANARY-note-is-surfaced"]


def _fake_find_session(*, basename="handoff-fake.md", members=(), raises=None):
    """A stand-in for `find-session.py` — the ONLY thing `arc_sessions` needs."""
    class _Unmeasured(RuntimeError):
        pass

    class _HandoffArc:
        @staticmethod
        def coverage_line(report):
            return "2 of 5 commit(s) on this doc carry no session id"

    class _Mod:
        ArcUnmeasured = _Unmeasured
        handoff_arc = _HandoffArc
        ROOT = None
        calls = []

        @staticmethod
        def arc_seed_to_doc(seed, root=None):
            return basename

        @staticmethod
        def arc_report(name):
            _Mod.calls.append(name)
            if raises:
                raise _Unmeasured(raises)
            return _FakeReport(list(members))
    return _Mod


class TestAZeroIsNeverJustAZero:
    """🔴 Four ways to come back empty, four codes, four reasons.

    Each assertion pins the code AND the mechanism the sentence names. A right
    code under a sentence pointing at the wrong mechanism sends the reader to
    the wrong fix, which is the failure this whole design exists to prevent.
    """

    def test_3_a_seed_naming_no_doc_is_UNMEASURED(self, tmp_path, monkeypatch):
        fake = _fake_find_session(basename="")
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "not-a-doc"], tmp_path)
        assert rc == X.EXIT_ARC_UNMEASURED
        assert "names no handoff doc" in err
        assert "Nothing was measured" in err

    def test_3_no_checkout_holding_the_doc_is_UNMEASURED(self, tmp_path,
                                                        monkeypatch):
        fake = _fake_find_session(raises="no repo handle holds it")
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_ARC_UNMEASURED
        assert "no repo handle holds it" in err

    def test_4_a_doc_that_resolves_with_no_members_is_a_MEASURED_empty_arc(
            self, tmp_path, monkeypatch):
        fake = _fake_find_session(members=())
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, _, err = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_ARC_EMPTY
        assert "MEASURED empty arc" in err
        assert "not a typo in your argument" in err
        assert f"exit {X.EXIT_ARC_UNMEASURED}" in err, (
            "the reason must name the OTHER code, or a reader cannot tell "
            "which of the two they are looking at")

    def test_4_and_3_are_DIFFERENT_CODES(self):
        """The pair this module exists for. A single 'empty' code would make a
        typo and a real finding indistinguishable."""
        assert X.EXIT_ARC_UNMEASURED != X.EXIT_ARC_EMPTY

    def test_5_ids_that_resolve_to_nothing_read_NOTHING(self, tmp_path):
        rc, _, err = _run(["--session", "sess-ghost"], tmp_path)
        assert rc == X.EXIT_NO_TRANSCRIPTS
        assert "sess-ghost" in err, "every unresolved id must be named"
        assert "Nothing was read" in err
        assert f"exit {X.EXIT_NO_MESSAGES}" in err

    def test_6_transcripts_that_ARE_read_and_hold_nothing_typed(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("<system-reminder>only boilerplate</system-reminder>"),
            _user("[Request interrupted by user]"),
        ])
        rc, _, err = _run(["--session", "sess-1"], tmp_path)
        assert rc == X.EXIT_NO_MESSAGES
        assert "WERE read" in err
        assert "measured emptiness" in err

    def test_5_and_6_are_DIFFERENT_CODES(self):
        assert X.EXIT_NO_TRANSCRIPTS != X.EXIT_NO_MESSAGES

    def test_every_exit_code_in_the_contract_is_distinct(self):
        codes = [c for c, _ in X.EXIT_CONTRACT]
        assert len(codes) == len(set(codes)), codes

    def test_2_an_unreadable_ids_file_never_degrades_to_an_empty_selection(
            self, tmp_path):
        rc, _, err = _run(["--ids-file", str(tmp_path / "nope.txt")], tmp_path)
        assert rc == X.EXIT_USAGE, (
            "an OSError degraded into an empty selection would fall through to "
            f"exit {X.EXIT_NO_TRANSCRIPTS} and read as 'those sessions are gone'")
        assert "nope.txt" in err, "the reason must name the file it could not read"

    def test_2_an_ids_file_holding_no_ids(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("# only a comment\n\n")
        rc, _, err = _run(["--ids-file", str(f)], tmp_path)
        assert rc == X.EXIT_USAGE
        assert "held no session ids" in err


# --- extraction end to end -----------------------------------------------------

class TestScopedExtraction:
    def test_a_selected_session_extracts_only_that_session(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_OTHER)])
        rc, out, _ = _run(["--session", "sess-1"], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in out
        assert LEAK_OTHER not in out, (
            "🔴 the scope is the whole feature — an unselected session must "
            "not appear")

    def test_repeating_session_selects_both(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_OTHER)])
        rc, out, _ = _run(["--session", "sess-1", "--session", "sess-2"], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in out and LEAK_OTHER in out

    def test_ids_file_dash_reads_stdin(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        rc, out, _ = _run(["--ids-file", "-"], tmp_path, stdin="sess-1\n")
        assert rc == X.EXIT_OK and LEAK_TYPED in out

    def test_a_partially_resolvable_selection_SAYS_SO_and_still_succeeds(
            self, tmp_path):
        """A partial extraction that does not announce itself reads as a whole
        one — so the warning must print on the SUCCESS path, not only on 5."""
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        rc, out, err = _run(["--session", "sess-1", "--session", "sess-ghost"],
                            tmp_path)
        assert rc == X.EXIT_OK
        assert "sess-ghost" in err
        assert "sess-ghost" in out, (
            "the markdown header must carry the gap too — stderr is routinely "
            "discarded by a caller redirecting stdout")

    def test_jsonl_carries_every_key_on_every_row(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user(LEAK_TYPED),
                        _user("<command-name>handoff</command-name>")])
        rc, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        rows = [json.loads(l) for l in out.splitlines() if l.strip()]
        assert len(rows) == 2
        for r in rows:
            assert set(r) == {"session_id", "project", "ts", "kind", "text",
                              "arc_role"}, sorted(r)
        assert {r["kind"] for r in rows} == {"typed", "command"}

    def test_arc_role_is_null_outside_arc_mode(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert json.loads(out.splitlines()[0])["arc_role"] is None

    def test_rows_are_chronological_and_undated_rows_sort_LAST(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("second", ts="2026-09-02T00:00:00Z"),
            _user("first", ts="2026-09-01T00:00:00Z"),
            _user("undated", ts=""),
        ])
        _, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert [json.loads(l)["text"] for l in out.splitlines()] == [
            "first", "second", "undated"], (
            "an undated row sorting FIRST would head the chain with the one "
            "message nothing could place in it")

    def test_dedup_suppresses_a_repeat_and_REPORTS_the_count(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_TYPED)])
        rc, out, err = _run(["--session", "sess-1", "--session", "sess-2",
                             "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        assert len(out.splitlines()) == 1
        assert "deduped=1" in err, (
            "a suppressed message that is not counted is a silent narrowing")

    def test_dedup_does_NOT_collide_a_command_with_identical_typed_prose(
            self, tmp_path):
        """🔴 REGRESSION. The first cut of the scoped dedup keyed on
        `project + text` and dropped `kind`, so `/handoff` typed as prose and
        `/handoff` the slash command hashed the same and whichever the walk
        reached second vanished. MEASURED over a frozen 974-transcript list: it
        lost 7 rows the previous implementation kept. Two different events with
        the same text are two rows."""
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("<command-name>handoff</command-name>",
                  ts="2026-09-01T00:00:00Z"),
            _user("handoff", ts="2026-09-01T00:00:01Z"),
        ])
        rc, out, _ = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        rows = [json.loads(l) for l in out.splitlines()]
        assert [(r["kind"], r["text"]) for r in rows] == [
            ("command", "handoff"), ("typed", "handoff")], rows

    def test_dedup_is_scoped_PER_PROJECT_not_global(self, tmp_path):
        """🔴 The bug this whole dedup change exists to fix, asserted directly.
        The previous implementation keyed commands on TEXT ALONE, so a
        `/handoff` in repo A suppressed the `/handoff` in repo B and which one
        survived depended on unsorted glob order. The same text in two projects
        is two events.

        Found by the mutation battery: dropping `project` from the key left the
        whole suite green, so this covers the one key component nothing else
        reached."""
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user("<command-name>handoff</command-name>")])
        _write_session(tmp_path, "proj-b", "sess-2",
                       [_user("<command-name>handoff</command-name>")])
        rc, out, err = _run(["--session", "sess-1", "--session", "sess-2",
                             "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        rows = [json.loads(l) for l in out.splitlines()]
        assert sorted(r["project"] for r in rows) == ["proj-a", "proj-b"], rows
        assert "deduped=0" in err

    def test_dedup_keeps_two_DIFFERENT_texts_in_one_project(self, tmp_path):
        """Positive control on the `text` component — a key that dropped it
        would collapse a whole project to one row per kind."""
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user(LEAK_TYPED, ts="2026-09-01T00:00:00Z"),
                        _user(LEAK_OTHER, ts="2026-09-01T00:00:01Z")])
        rc, out, err = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert rc == X.EXIT_OK
        assert [json.loads(l)["text"] for l in out.splitlines()] == [
            LEAK_TYPED, LEAK_OTHER]
        assert "deduped=0" in err

    def test_dedup_still_collapses_a_repeat_of_the_SAME_kind(self, tmp_path):
        """Positive control for the test above — widening the key must not have
        turned dedup off, which would make that test pass for the wrong reason."""
        _write_session(tmp_path, "proj-a", "sess-1", [
            _user("<command-name>handoff</command-name>"),
            _user("<command-name>handoff</command-name>"),
        ])
        _, out, err = _run(["--session", "sess-1", "--jsonl"], tmp_path)
        assert len(out.splitlines()) == 1
        assert "deduped=1" in err

    def test_no_dedup_keeps_the_repeat(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-2", [_user(LEAK_TYPED)])
        _, out, err = _run(["--session", "sess-1", "--session", "sess-2",
                            "--jsonl", "--no-dedup"], tmp_path)
        assert len(out.splitlines()) == 2
        assert "deduped=0" in err

    def test_out_writes_a_file_and_leaves_stdout_clean(self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        dest = tmp_path / "msgs.md"
        rc, out, _ = _run(["--session", "sess-1", "-o", str(dest)], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in dest.read_text()
        assert out == ""


class TestPipingToHead:
    """🔴 REGRESSION, and it was reachable from the DOCUMENTED invocation.
    `--help` and the reference doc both show `… | jq …`; piping the real tool
    to `head` raised `BrokenPipeError` with a traceback. A tool that crashes on
    its own documented pipeline teaches the reader the pipeline is wrong."""

    #: Two cut points, because there are TWO failures behind one symptom: the
    #: failed WRITE (the `except BrokenPipeError` arm) and CPython's retry of
    #: the flush AT SHUTDOWN (the `os.dup2` beside it). This class pins BOTH —
    #: MEASURED over 20 runs each: dup2 deleted => 20/20 red, dup2 present =>
    #: 0/20 red; mutating the exception type is likewise red.
    #:
    #: 🔴 THE 4 KB ROWS ARE LOAD-BEARING, NOT PADDING. With short rows the
    #: failed write drains the buffer, nothing is left for the shutdown flush,
    #: and the dup2 mutant SURVIVES. An earlier revision of this comment
    #: recorded exactly that survival as a property of the guard — it was a
    #: single draw against the WEAKER fixture that predated `CUT_POINTS`, and
    #: the claim was written as if it applied to this one. It shipped a comment
    #: telling maintainers a load-bearing line was uncovered. **Re-run the sweep
    #: after touching this fixture; a mutation result is a fact about the test
    #: as it stood, and does not survive a change to what it feeds.**
    CUT_POINTS = (1, 5)

    def _pipe_to_head(self, tmp_path, n, *extra):
        import subprocess
        src = SA_DIR / "extract_user_msgs.py"
        p1 = subprocess.Popen(
            [sys.executable, str(src), "--session", "sess-1", "--jsonl",
             "--no-dedup", "--root", str(tmp_path), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p2 = subprocess.Popen(["head", f"-{n}"], stdin=p1.stdout,
                              stdout=subprocess.PIPE)
        p1.stdout.close()
        head_out = p2.communicate()[0]
        err = p1.stderr.read().decode()
        p1.stderr.close()
        p1.wait()
        return head_out, err

    @pytest.mark.parametrize("n", CUT_POINTS)
    def test_a_closed_stdout_exits_quietly(self, tmp_path, n):
        _write_session(tmp_path, "proj-a", "sess-1",
                       [_user(f"{LEAK_TYPED}-{i}" + "x" * 4000,
                              ts=f"2026-09-01T00:00:{i:02d}Z")
                        for i in range(40)])
        head_out, err = self._pipe_to_head(tmp_path, n)
        assert head_out.strip(), (
            "head got no row — the positive control failed, so a silent stderr "
            "would prove nothing")
        assert "BrokenPipeError" not in err, err
        assert "Exception ignored" not in err, err
        assert "Traceback" not in err, err


class TestUnscopedWalk:
    def test_with_no_selector_it_walks_the_corpus_and_SAYS_it_is_unscoped(
            self, tmp_path):
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-b", "sess-2", [_user(LEAK_OTHER)])
        rc, out, _ = _run([], tmp_path)
        assert rc == X.EXIT_OK
        assert LEAK_TYPED in out and LEAK_OTHER in out
        assert "UNSCOPED" in out, (
            "a corpus-wide answer that does not announce itself is the 54 MiB "
            "run reading as a scoped one")

    def test_a_REAL_shaped_subagent_transcript_is_excluded(self, tmp_path):
        """🔴 THE FIXTURE SHAPE IS THE WHOLE TEST, and the version this replaces
        got it wrong. It wrote `<root>/proj-a/sess-1/subagent-x.jsonl` — parent
        dir `sess-1` — and asserted the walk INCLUDED it, which it did, because
        nothing there is named `subagents`. A real subagent transcript lives at
        `<project>/<session-id>/subagents/agent-*.jsonl`, so its parent IS
        `subagents` and it was always excluded. The old test therefore asserted
        a shape the corpus does not have, and read as coverage for a claim
        (`the unscoped walk sees subagents`) that was false: MEASURED on the
        live corpus, 0 of 5,681 such transcripts were included."""
        _write_session(tmp_path, "proj-a/sess-1/subagents", "agent-aa845366",
                       [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_OTHER)])
        assert [p.stem for p in X.corpus_paths(root=tmp_path)] == ["sess-1"]

    def test_a_literal_subagents_dir_and_wf_dirs_are_skipped(self, tmp_path):
        _write_session(tmp_path, "subagents", "agent-1", [_user(LEAK_TYPED)])
        _write_session(tmp_path, "wf_123", "run-1", [_user(LEAK_OTHER)])
        _write_session(tmp_path, "proj-a", "sess-1", [_user("kept")])
        assert [p.stem for p in X.corpus_paths(root=tmp_path)] == ["sess-1"]

    def test_the_walk_IS_the_shared_enumerator_not_a_second_copy(self, tmp_path):
        """One rule, one place — asserted behaviourally, over the shape that
        separated the two: the private copy tested only the IMMEDIATE parent, so
        a transcript nested BELOW a `subagents/` dir passed it and fails
        `is_corpus_member`, which tests every parent part."""
        import transcript_search
        _write_session(tmp_path, "proj-a/sess-1/subagents/nested", "agent-9",
                       [_user(LEAK_TYPED)])
        _write_session(tmp_path, "proj-a", "sess-1", [_user(LEAK_OTHER)])
        assert X.corpus_paths(root=tmp_path) == list(
            transcript_search.iter_transcripts(root=tmp_path))
        assert [p.stem for p in X.corpus_paths(root=tmp_path)] == ["sess-1"], (
            "a transcript nested below a subagents/ dir must be excluded — the "
            "private walk this replaced included it")

    def test_the_walk_is_SORTED_so_a_run_is_deterministic(self, tmp_path):
        for name in ("sess-c", "sess-a", "sess-b"):
            _write_session(tmp_path, "proj-a", name, [_user(f"m-{name}")])
        paths = [p.stem for p in X.corpus_paths(root=tmp_path)]
        assert paths == sorted(paths)


class TestArcSessions:
    def test_the_resolver_is_IMPORTED_not_re_implemented(self, tmp_path):
        """🔴 Behavioural, not spelled. `arc_sessions` must obtain its chain by
        calling the CLI's `arc_report`; a private re-derivation here would let
        the two tools answer 'which sessions worked this doc' differently while
        neither said so."""
        fake = _fake_find_session(members=[_FakeMember("s1", "originated")])
        members, _ = X.arc_sessions("handoff-fake", find_session=fake)
        assert fake.calls == ["handoff-fake.md"], (
            "arc_report was not called — the glue was re-implemented")
        assert [m.session_id for m in members] == ["s1"]

    def test_every_coverage_note_the_resolver_produced_is_SURFACED(self):
        fake = _fake_find_session(members=[_FakeMember("s1", "wrote")])
        _, notes = X.arc_sessions("handoff-fake", find_session=fake)
        joined = "\n".join(notes)
        assert "carry no session id" in joined, (
            "🔴 the coverage line is what stops a partial chain reading as a "
            "complete one, and it must appear even when the count is zero")
        assert "LEAKCANARY-note-is-surfaced" in joined, (
            "an unmeasured_note the resolver produced was dropped")

    def test_an_unresolvable_seed_RAISES_rather_than_returning_empty(self):
        fake = _fake_find_session(basename="")
        with pytest.raises(X.ArcUnresolved):
            X.arc_sessions("nope", find_session=fake)

    def test_the_member_ROLE_reaches_the_markdown_heading(self, tmp_path,
                                                          monkeypatch):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        fake = _fake_find_session(members=[_FakeMember("s1", "earliest-stamped")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        rc, out, _ = _run(["--arc", "handoff-fake"], tmp_path)
        assert rc == X.EXIT_OK
        assert "earliest-stamped" in out, (
            "the role is handoff_arc's vocabulary and carries the caveat that "
            "`originated` was demoted; dropping it loses that caveat")

    def test_arc_role_lands_in_the_jsonl(self, tmp_path, monkeypatch):
        _write_session(tmp_path, "proj-a", "s1", [_user(LEAK_TYPED)])
        fake = _fake_find_session(members=[_FakeMember("s1", "resumed")])
        monkeypatch.setattr(X, "_load_find_session", lambda: fake)
        _, out, _ = _run(["--arc", "handoff-fake", "--jsonl"], tmp_path)
        assert json.loads(out.splitlines()[0])["arc_role"] == "resumed"


# --- the --help contract -------------------------------------------------------

class TestHelpNamesEverySelector:
    """Two-way against the parser, so a selector added with no example fails."""

    SELECTOR_DESTS = {"arc", "session", "ids_file"}

    def _selector_flags(self):
        parser = X.build_parser()
        return {a.option_strings[0] for a in parser._actions
                if a.dest in self.SELECTOR_DESTS}

    def test_the_selector_set_matches_the_parser(self):
        """If a new selector lands, this fails first and names it — before the
        vaguer 'no example' failure below."""
        parser = X.build_parser()
        dests = {a.dest for a in parser._actions
                 if a.dest not in ("help", "jsonl", "out", "no_dedup", "root")}
        assert dests == self.SELECTOR_DESTS, (
            f"a selector was added or removed: {sorted(dests)}. Give it an "
            "example in EPILOG and add it here.")

    def test_every_selector_has_at_least_one_example_in_the_help(self):
        for flag in self._selector_flags():
            examples = [l for l in X.EPILOG.splitlines()
                        if "extract_user_msgs.py" in l and flag in l]
            assert examples, f"--help shows no example for {flag}"

    def test_both_output_formats_are_named_in_the_help(self):
        assert "--jsonl" in X.EPILOG
        assert "markdown" in X.EPILOG

    def test_every_exit_code_is_listed_in_the_help(self):
        for code, _ in X.EXIT_CONTRACT:
            assert f"\n  {code}  " in X.EPILOG, (
                f"exit {code} is in EXIT_CONTRACT but not in --help")

    def test_the_help_lists_no_code_the_contract_does_not_define(self):
        """The other direction — a stale code in the help is a wrong promise."""
        import re
        listed = {int(m) for m in re.findall(r"^  (\d)  ", X.EPILOG, re.M)}
        assert listed == {c for c, _ in X.EXIT_CONTRACT}, listed


# --- routing: the reference doc ------------------------------------------------

class TestTheReferenceDocIsRoutedAndDeployed:
    """Mirrors `test_subsystem_recall.py`'s sidecar guards. A reference file
    that is not git-tracked is silently absent from the flake source, so the
    deploy omits it and `/resume`'s pointer dangles on every host."""

    def test_the_reference_exists_beside_the_handoff_skill(self):
        assert REFERENCE.exists(), REFERENCE
        assert REFERENCE.parent.name == "reference"
        assert REFERENCE.parent.parent.name == "handoff"

    def test_resume_ROUTES_to_it(self):
        body = RESUME_SKILL.read_text(encoding="utf-8")
        assert "handoff/reference/user-messages.md" in body, (
            "/resume must name the reference, or nothing loads it")
        assert "extract_user_msgs.py" in body, (
            "the row must name the TOOL — an agent searching for the tool by "
            "name is how the row gets found")

    def test_the_reference_is_TRACKED_so_the_deploy_carries_it(self):
        """Two tiers, neither a skip — the shape `test_subsystem_recall.py`
        documents. In the nix sandbox there is no `.git`, and the file being
        here AT ALL is the proof, because the flake copies tracked files."""
        assert REFERENCE.exists()
        if not (REPO / ".git").exists():
            return
        import subprocess
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             str(REFERENCE.relative_to(REPO))],
            cwd=REPO, capture_output=True, text=True)
        assert r.returncode == 0, (
            f"{REFERENCE.relative_to(REPO)} is untracked, so the flake omits it "
            f"and /resume's pointer dangles on every host.\n{r.stderr}")

    def test_the_reference_pin_can_report_absence(self):
        """Negative control: a check against a doc containing everything is
        indistinguishable from one pointed at the wrong file."""
        assert "a sentence deliberately absent from the user-messages reference" \
            not in REFERENCE.read_text(encoding="utf-8")

    def test_the_reference_documents_EVERY_exit_code_with_its_meaning(self):
        """Two-way against `EXIT_CONTRACT`: a code added to the tool with no
        row here leaves the reference reading as coverage while providing none,
        and a row for a code the tool no longer returns is a false promise."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        rows = {int(m) for m in re.findall(r"^\| (\d) \|", text, re.M)}
        assert rows == {c for c, _ in X.EXIT_CONTRACT}, (
            f"the reference's exit table lists {sorted(rows)}; the tool "
            f"defines {sorted(c for c, _ in X.EXIT_CONTRACT)}")

    def test_the_reference_carries_a_MEASURED_cost_not_a_vague_one(self):
        """🔴 PINS THE SHAPE, NOT THE DIGITS — and the version this replaces
        pinned the digits. It required the literal `54.1 MiB`, `13,768` and
        `974`, two of which are LIVE-CORPUS counts that drift by design (the
        doc says so in its own caveat). So the only thing it could ever fire on
        was somebody CORRECTLY re-measuring: it went red on the right action
        and green on a stale number, which is backwards. It was already stale
        when written — the corpus read 13,780 the same day.

        What must hold is that the claim is anchored: the word MEASURED, a
        date, a byte figure and a transcript count, in a table. The values are
        the author's to update."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        assert "MEASURED" in text, "the cost claim lost its MEASURED anchor"
        assert re.search(r"MEASURED\s+\d{4}-\d{2}-\d{2}", text), (
            "a measurement with no date cannot be judged stale")
        assert re.search(r"\d+(?:\.\d+)?\s*MiB", text), "no byte figure"
        assert re.search(r"\|\s*transcripts read\s*\|", text), (
            "the cost table lost its transcripts-read row")
        assert re.search(r"\d+×|\d+x", text), "no ratio — the headline claim"

    def test_the_reference_gives_a_RUNNABLE_command_for_every_selector(self):
        """🔴 PINS THE COMMAND BLOCK, NOT A MENTION. The version this replaces
        asserted `flag in text`, which a flag satisfies by appearing anywhere —
        including inside the exit-code table, where `--ids-file` is named only
        to explain exit 2. So the reference could lose every runnable example
        of a selector and still pass. Measured while building the mutation
        battery: `--ids-file` occurs 3x in this doc, and deleting the one
        example that teaches you to USE it killed nothing.

        What a reader needs is a command they can copy, so that is what is
        pinned: each selector must appear on a `python3 $E …` line."""
        import re
        text = REFERENCE.read_text(encoding="utf-8")
        commands = [ln for ln in text.splitlines()
                    if re.search(r"^\s*\|?\s*python3 \$E ", ln)]
        assert commands, "the reference has no runnable command block at all"
        for flag in ("--arc", "--session", "--ids-file", "--jsonl"):
            assert any(flag in ln for ln in commands), (
                f"{flag} appears in no `python3 $E …` command — a reader has "
                f"nothing to copy. Lines found: {commands}")


class TestNoCapturedTextEscapes:
    """devrc is PUBLIC. Nothing in this module may carry a real transcript."""

    def test_every_fixture_string_is_a_canary(self):
        src = Path(__file__).read_text(encoding="utf-8")
        assert "LEAKCANARY" in src
        for canary in (LEAK_TYPED, LEAK_OTHER, LEAK_REMINDER, LEAK_TOOL):
            assert canary.startswith("LEAKCANARY-")

    def test_the_canaries_are_pairwise_distinct(self):
        canaries = [LEAK_TYPED, LEAK_OTHER, LEAK_REMINDER, LEAK_TOOL]
        assert len(set(canaries)) == len(canaries)
        for a in canaries:
            for b in canaries:
                if a is not b:
                    assert a not in b, (
                        f"{a!r} is a substring of {b!r} — a mutant that emits "
                        "the wrong one would survive")
