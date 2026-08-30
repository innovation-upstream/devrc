#!/usr/bin/env python3
"""`scripts/lib/session_trailer.py` — the session-id commit trailer.

🔴 WHAT THIS FILE IS ACTUALLY GUARDING. Three properties, each of which fails
SILENTLY and each of which produces a *plausible-looking* commit:

  1. A commit must never be stamped with ANOTHER session's id. Two sessions
     committing in one repo is the normal state on this box (~117 worktrees share
     one common git dir). A wrong id is worse than none — it sends a future reader
     to a session that never touched the line — so the state is pid-keyed and
     `lookup()` resolves the pid from the CALLER's own ancestry.
  2. The id is an OPAQUE STRING. `cairn_who.py` records 2 of 41 windows carrying a
     `ses_…` token rather than a uuid, and that a join assuming uuid shape
     "silently matches nothing". Any shape-check here would reintroduce that bug.
  3. Stamping is IDEMPOTENT. `prepare-commit-msg` runs again on `--amend` and on
     every re-edit, so a non-idempotent append accretes one trailer per edit and
     nobody notices until a rebased commit has six.

🔴 WHY `has_trailer` DOES NOT USE GIT'S OWN PARSER. Measured on `origin/main` at
`3b1a0477` (2026-08-30): `git log --format='%(trailers:key=Claude-Session,valueonly)'`
reports a value for **9** commits where a per-commit content search finds **67**
in the same 200, because git only recognises a contiguous trailer block at the
very END and this repo's messages carry a "Generated with" line after it. A test
using git's parser as its oracle would enshrine that undercount.

⚠ THREE WRONG COUNTS PRECEDED THAT 67, and they are worth more than the number:
`grep -c` counts LINES not commits (each such commit repeats the token ~3 times);
"55" and "41%/27%" came from adopting an auditor's figure WITHOUT re-measuring,
which overwrote an earlier figure of mine that had been right. Anchoring is a red
herring — anchored and unanchored give the SAME per-commit count (67 either way).
Count commits, and re-measure rather than quoting any number here.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "lib" / "session_trailer.py"


def _load():
    spec = importlib.util.spec_from_file_location("session_trailer", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


st = _load()


# --------------------------------------------------------------------------
# A fake process tree. Ancestry is the whole correctness argument, so it is
# injected rather than sampled from the live box — a test that depended on the
# real /proc would pass or fail according to what else is running.
# --------------------------------------------------------------------------
def _starttime(pid: int) -> int:
    """A stable synthetic /proc field-22 value, distinct per pid.

    Distinct per pid on purpose: `lookup()` and `prune()` compare the pinned
    start time against the live one, so a fixture where every pid shared a start
    time could not tell a recycled pid from the original and the recycle guard
    would be untestable.
    """
    return 1000 + pid


def tree(mapping):
    """mapping: {pid: (comm, ppid)} -> a reader compatible with read_proc."""
    def reader(pid):
        got = mapping.get(pid)
        if not got:
            return None
        return {"comm": got[0], "ppid": got[1], "starttime": _starttime(pid)}
    return reader


def LIVE(pid):
    """A reader for which every pid exists — used when recording."""
    return {"comm": "claude", "ppid": 1, "starttime": _starttime(pid)}


def only(*alive):
    """A reader where only `alive` pids exist."""
    def reader(pid):
        return LIVE(pid) if pid in alive else None
    return reader


def recycled(pid):
    """A reader where `pid` exists but is a DIFFERENT process than when recorded."""
    def reader(p):
        if p != pid:
            return None
        return {"comm": "claude", "ppid": 1, "starttime": _starttime(p) + 999}
    return reader


class TestAncestry:
    def test_finds_the_claude_ancestor_through_intermediate_shells(self):
        r = tree({100: ("git", 90), 90: ("zsh", 80),
                  80: (".claude-wrapped", 1), 1: ("init", 0)})
        assert st.claude_ancestor_pid(start_pid=100, reader=r) == 80

    def test_returns_none_for_a_human_shell_with_no_claude_above_it(self):
        """A human's `git commit` must come out byte-identical."""
        r = tree({100: ("git", 90), 90: ("zsh", 1), 1: ("init", 0)})
        assert st.claude_ancestor_pid(start_pid=100, reader=r) is None

    def test_the_NEAREST_claude_wins_when_sessions_are_nested(self):
        """A subagent under a session must not be attributed to the parent."""
        r = tree({100: ("git", 95), 95: ("claude", 90),
                  90: ("zsh", 80), 80: ("claude", 1), 1: ("init", 0)})
        assert st.claude_ancestor_pid(start_pid=100, reader=r) == 95

    def test_a_cycle_terminates_instead_of_hanging(self):
        r = tree({5: ("a", 6), 6: ("b", 5)})
        assert st.claude_ancestor_pid(start_pid=5, reader=r) is None

    def test_a_truncated_comm_still_matches(self):
        """/proc truncates comm to 15 chars; `.claude-wrapped` is exactly 15."""
        r = tree({10: ("git", 20), 20: (".claude-wrapped", 1), 1: ("init", 0)})
        assert st.claude_ancestor_pid(start_pid=10, reader=r) == 20


class TestRecordAndLookup:
    def test_a_concurrent_session_state_is_never_read(self, tmp_path):
        """🔴 THE CORE SAFETY PROPERTY. Two sessions, one repo, one state dir."""
        common = str(tmp_path)
        st.record("session-AAA", 111, root=common, reader=LIVE)
        st.record("session-BBB", 222, root=common, reader=LIVE)

        # A commit whose ancestry reaches pid 222 must see BBB, never AAA.
        r = tree({900: ("git", 222), 222: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(start_pid=900, root=common, reader=r) == "session-BBB"

        r2 = tree({901: ("git", 111), 111: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(start_pid=901, root=common, reader=r2) == "session-AAA"

    def test_lookup_is_none_when_no_state_was_recorded_for_this_session(self, tmp_path):
        common = str(tmp_path)
        st.record("session-AAA", 111, root=common, reader=LIVE)
        r = tree({900: ("git", 333), 333: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(start_pid=900, root=common, reader=r) is None

    def test_lookup_is_none_outside_a_claude_session(self, tmp_path):
        common = str(tmp_path)
        st.record("session-AAA", 111, root=common, reader=LIVE)
        r = tree({900: ("git", 800), 800: ("zsh", 1), 1: ("init", 0)})
        assert st.lookup(start_pid=900, root=common, reader=r) is None

    def test_a_corrupt_state_file_yields_none_rather_than_raising(self, tmp_path):
        common = str(tmp_path)
        os.makedirs(common, exist_ok=True)
        Path(st.state_file(111, common)).write_text("{not json")
        r = tree({900: ("git", 111), 111: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(start_pid=900, root=common, reader=r) is None

    def test_record_round_trips_the_id_verbatim(self, tmp_path):
        """Opaque-string discipline: a non-uuid id survives unchanged."""
        common = str(tmp_path)
        st.record("ses_A1b2C3", 111, root=common, reader=LIVE)
        data = json.loads(Path(st.state_file(111, common)).read_text())
        assert data["session_id"] == "ses_A1b2C3"


class TestIdValidation:
    @pytest.mark.parametrize("value", [
        "d8c216f2-b51d-4c2c-a559-5a5ab4163848",   # uuid, the common case
        "ses_01ABCdef",                            # the OTHER runtime's shape
        "x",                                       # short but real
    ])
    def test_opaque_ids_of_any_shape_are_accepted(self, value):
        assert st.valid_id(value) is True

    @pytest.mark.parametrize("value", [
        "has\nnewline",      # could forge a second trailer line
        "has\rcarriage",
        "",
        "   ",
        None,
        12345,
    ])
    def test_values_that_could_corrupt_a_commit_message_are_refused(self, value):
        assert st.valid_id(value) is False

    def test_an_overlong_id_is_refused(self):
        assert st.valid_id("a" * (st._MAX_ID_LEN + 1)) is False

    def test_the_length_bound_is_pinned_as_its_own_assertion(self):
        """🔴 Pinned as a LITERAL, not derived from the constant.

        RULES.md: a fixture built by arithmetic from the constant under test
        cannot see that constant change. The boundary cases above use the
        constant deliberately; this line is what notices it moving.
        """
        assert st._MAX_ID_LEN == 256


class TestAppendTrailer:
    def test_it_appends_the_resumable_id(self):
        out = st.append_trailer("fix: a thing\n", "abc-123")
        assert "Claude-Session-Id: abc-123" in out

    def test_it_is_idempotent_across_repeated_amends(self):
        """🔴 prepare-commit-msg re-runs on every --amend."""
        msg = "fix: a thing\n"
        once = st.append_trailer(msg, "abc-123")
        twice = st.append_trailer(once, "abc-123")
        thrice = st.append_trailer(twice, "abc-123")
        assert once == twice == thrice
        assert once.count("Claude-Session-Id:") == 1

    def test_an_invalid_id_leaves_the_message_byte_identical(self):
        msg = "fix: a thing\n"
        assert st.append_trailer(msg, "bad\nid") == msg

    def test_the_original_body_is_never_mangled(self):
        msg = "feat: x\n\nA body paragraph.\n\nAnother one.\n"
        out = st.append_trailer(msg, "abc-123")
        assert out.startswith("feat: x\n\nA body paragraph.\n\nAnother one.")

    def test_it_joins_an_existing_trailer_block_without_a_blank_line(self):
        msg = "feat: x\n\nCo-Authored-By: Someone <a@b.c>\n"
        out = st.append_trailer(msg, "abc-123")
        assert "Co-Authored-By: Someone <a@b.c>\nClaude-Session-Id: abc-123" in out

    def test_it_separates_from_prose_with_a_blank_line(self):
        msg = "feat: x\n\nJust prose, no trailers.\n"
        out = st.append_trailer(msg, "abc-123")
        assert "Just prose, no trailers.\n\nClaude-Session-Id: abc-123" in out


class TestHasTrailer:
    def test_it_sees_a_trailer_that_git_s_own_parser_misses(self):
        """🔴 THE MEASURED CASE. Content AFTER the trailer block hides it from
        `%(trailers:)`; it must not hide it from us, or every such commit gets a
        duplicate stamp on the next amend."""
        msg = (
            "feat: x\n\n"
            "Claude-Session-Id: abc-123\n\n"
            "🤖 Generated with Claude Code\n\n"
            "https://claude.ai/code/session_01ABC\n"
        )
        assert st.has_trailer(msg, st.TRAILER_KEY) is True
        assert st.append_trailer(msg, "abc-123") == msg

    def test_it_does_not_match_the_key_mentioned_mid_sentence(self):
        msg = "feat: mention Claude-Session-Id: in prose but not as a trailer line\n"
        # The key appears, but only inside a longer first line — not on its own.
        assert st.has_trailer(msg, st.TRAILER_KEY) is False


class TestPrune:
    def test_dead_sessions_are_removed_and_live_ones_kept(self, tmp_path):
        common = str(tmp_path)
        st.record("live", 111, root=common, reader=LIVE)
        st.record("dead", 222, root=common, reader=LIVE)
        removed = st.prune(root=common, reader=only(111))
        assert removed == 1
        assert os.path.exists(st.state_file(111, common))
        assert not os.path.exists(st.state_file(222, common))

    def test_prune_on_a_missing_directory_is_a_no_op_not_an_error(self, tmp_path):
        assert st.prune(root=str(tmp_path / "nope")) == 0

    def test_a_non_pid_filename_is_ignored_rather_than_crashing(self, tmp_path):
        common = str(tmp_path)
        os.makedirs(common, exist_ok=True)
        Path(os.path.join(common, "notapid.json")).write_text("{}")
        assert st.prune(root=common, reader=lambda pid: None) == 0


# ---------------------------------------------------------------------------
# 🔴 THE ON-DISK ARTIFACT NAMES, pinned as WHOLE relative paths.
#
# `scripts/claude-hooks/tests/test_on_disk_artifact_names.py` classifies
# `session-stamp.py` as delegating every path to this module, and its entry
# NAMES the test below as where those paths are pinned. That cross-reference is
# a claim: if this test does not exist, or stops pinning whole paths, the ledger
# entry reads as coverage while providing none.
#
# 🔴 The corroboration scan in that file CANNOT see these names, but NOT because
# the state is outside $HOME — an earlier revision of this comment said that, and
# it was the OPPOSITE of the fix in the same PR, which moved the state to
# $XDG_CACHE_HOME|~/.cache/claude-session-trailer. The scan is blind because it
# only reads files under scripts/claude-hooks/, and the `.cache` literal lives
# here in scripts/lib/. So the enumeration plus this pin are the whole of the
# coverage; there is no second net.
#
# Whole paths, not fragments: a pin on the leaf alone stays green when the
# parent directory is renamed, which is exactly the rename that would strand
# every existing session's state.
# ---------------------------------------------------------------------------
def test_the_on_disk_artifact_names_are_pinned_as_whole_paths():
    assert st.STATE_DIRNAME == "claude-session-trailer"
    assert st.state_file(4242, "/ROOT") == "/ROOT/4242.json"


def test_the_state_root_honours_XDG_and_falls_back_under_HOME(monkeypatch):
    monkeypatch.delenv("DEVRC_SESSION_TRAILER_ROOT", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")
    assert st.state_root() == "/xdg/claude-session-trailer"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", "/home/someone")
    assert st.state_root() == "/home/someone/.cache/claude-session-trailer"


def test_the_trailer_key_is_pinned():
    """A renamed key silently stops matching every trailer already in history —
    `has_trailer` goes blind and every amend appends a duplicate."""
    assert st.TRAILER_KEY == "Claude-Session-Id"


# ---------------------------------------------------------------------------
# Round-2 guards. Each pins a defect the round-1 audit MEASURED, not a defect
# imagined while writing the fix.
# ---------------------------------------------------------------------------
class TestPidRecycling:
    """🔴 `kernel.pid_max` here is 4194304 and live pids were measured spanning
    114904–4193245 — the pid space has already wrapped, so recycling is routine.
    Without a start-time pin, a recycled pid inherits a dead session's id and a
    commit is stamped with a session that never touched the line."""

    def test_a_recycled_pid_is_a_MISS_not_a_wrong_answer(self, tmp_path):
        common = str(tmp_path)
        st.record("dead-session", 4242, root=common, reader=LIVE)
        # Same pid, different process (different starttime).
        assert st.lookup(pid=4242, root=common, reader=recycled(4242)) is None

    def test_the_same_process_still_resolves(self, tmp_path):
        """Positive control: the guard rejects a recycled pid, not every pid."""
        common = str(tmp_path)
        st.record("live-session", 4242, root=common, reader=LIVE)
        assert st.lookup(pid=4242, root=common, reader=LIVE) == "live-session"

    def test_prune_drops_a_recycled_pid_even_though_it_is_alive(self, tmp_path):
        """The earlier prune kept any file whose pid merely EXISTED — measured
        keeping a record for pid 1."""
        common = str(tmp_path)
        st.record("dead-session", 4242, root=common, reader=LIVE)
        assert st.prune(root=common, reader=recycled(4242)) == 1
        assert not os.path.exists(st.state_file(4242, common))

    def test_prune_keeps_the_same_process(self, tmp_path):
        common = str(tmp_path)
        st.record("live-session", 4242, root=common, reader=LIVE)
        assert st.prune(root=common, reader=LIVE) == 0
        assert os.path.exists(st.state_file(4242, common))


class TestAmendByADifferentSession:
    """🔴 Idempotence keyed on the KEY alone meant session B amending session A's
    commit kept A's id — B's rewrite attributed to a session that never made it.
    Reached with no race and no pid reuse, so the pid keying could not help."""

    def test_a_different_session_rewrites_the_trailer(self):
        msg = st.append_trailer("feat: x\n", "SESS-A")
        out = st.append_trailer(msg, "SESS-B")
        assert "Claude-Session-Id: SESS-B" in out
        assert "SESS-A" not in out
        assert out.count("Claude-Session-Id:") == 1

    def test_the_same_session_is_still_a_byte_identical_no_op(self):
        msg = st.append_trailer("feat: x\n", "SESS-A")
        assert st.append_trailer(msg, "SESS-A") == msg

    def test_a_rewrite_keeps_the_trailer_in_place(self):
        """Position matters: appending a second block after later prose would
        put the trailer somewhere git no longer reads as one."""
        msg = "feat: x\n\nClaude-Session-Id: SESS-A\nCo-Authored-By: Z <z@e>\n"
        out = st.append_trailer(msg, "SESS-B")
        lines = out.splitlines()
        assert lines.index("Claude-Session-Id: SESS-B") < lines.index("Co-Authored-By: Z <z@e>")

    def test_duplicates_already_in_a_message_collapse_to_one(self):
        msg = "feat: x\n\nClaude-Session-Id: A\nClaude-Session-Id: A\n"
        out = st.append_trailer(msg, "B")
        assert out.count("Claude-Session-Id:") == 1


class TestQuotedTrailerInProse:
    """🔴 `has_trailer` used `.strip()`, so an INDENTED example inside a message
    body counted as a real trailer and that commit silently got no stamp. That
    is exactly the shape of this feature's own documentation commits."""

    def test_an_indented_example_is_not_a_trailer(self):
        msg = ("docs: explain the trailer\n\n"
               "The hook adds a line like:\n\n"
               "    Claude-Session-Id: some-example-id\n\n"
               "…at the end of the message.\n")
        assert st.has_trailer(msg) is False
        out = st.append_trailer(msg, "REAL-ID")
        assert "Claude-Session-Id: REAL-ID" in out
        # the prose example survives untouched
        assert "    Claude-Session-Id: some-example-id" in out

    def test_a_column_zero_trailer_is_still_seen(self):
        """Positive control, so the fix is not simply 'never matches'."""
        msg = "feat: x\n\nClaude-Session-Id: real\n"
        assert st.has_trailer(msg) is True
