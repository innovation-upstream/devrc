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

🔴 WHY `has_trailer` DOES NOT USE GIT'S OWN PARSER. Measured on origin/main
2026-08-30: `git log --format='%(trailers:key=Claude-Session,valueonly)'` reports
a value for 9 commits where a plain content search finds 66, because git only
recognises a contiguous trailer block at the very END of the message and this
repo's messages carry a "Generated with" line after it. A test that used git's
parser as its oracle would enshrine that undercount.
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
def tree(mapping):
    """mapping: {pid: (comm, ppid)} -> a reader compatible with read_proc."""
    def reader(pid):
        got = mapping.get(pid)
        if not got:
            return None
        return {"comm": got[0], "ppid": got[1]}
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
        st.record(common, "session-AAA", 111)
        st.record(common, "session-BBB", 222)

        # A commit whose ancestry reaches pid 222 must see BBB, never AAA.
        r = tree({900: ("git", 222), 222: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(common, start_pid=900, reader=r) == "session-BBB"

        r2 = tree({901: ("git", 111), 111: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(common, start_pid=901, reader=r2) == "session-AAA"

    def test_lookup_is_none_when_no_state_was_recorded_for_this_session(self, tmp_path):
        common = str(tmp_path)
        st.record(common, "session-AAA", 111)
        r = tree({900: ("git", 333), 333: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(common, start_pid=900, reader=r) is None

    def test_lookup_is_none_outside_a_claude_session(self, tmp_path):
        common = str(tmp_path)
        st.record(common, "session-AAA", 111)
        r = tree({900: ("git", 800), 800: ("zsh", 1), 1: ("init", 0)})
        assert st.lookup(common, start_pid=900, reader=r) is None

    def test_a_corrupt_state_file_yields_none_rather_than_raising(self, tmp_path):
        common = str(tmp_path)
        os.makedirs(st.state_dir(common), exist_ok=True)
        Path(st.state_file(common, 111)).write_text("{not json")
        r = tree({900: ("git", 111), 111: ("claude", 1), 1: ("init", 0)})
        assert st.lookup(common, start_pid=900, reader=r) is None

    def test_record_round_trips_the_id_verbatim(self, tmp_path):
        """Opaque-string discipline: a non-uuid id survives unchanged."""
        common = str(tmp_path)
        st.record(common, "ses_A1b2C3", 111)
        data = json.loads(Path(st.state_file(common, 111)).read_text())
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
        st.record(common, "live", 111)
        st.record(common, "dead", 222)
        removed = st.prune(common, alive=lambda pid: pid == 111)
        assert removed == 1
        assert os.path.exists(st.state_file(common, 111))
        assert not os.path.exists(st.state_file(common, 222))

    def test_prune_on_a_missing_directory_is_a_no_op_not_an_error(self, tmp_path):
        assert st.prune(str(tmp_path / "nope")) == 0

    def test_a_non_pid_filename_is_ignored_rather_than_crashing(self, tmp_path):
        common = str(tmp_path)
        os.makedirs(st.state_dir(common), exist_ok=True)
        Path(os.path.join(st.state_dir(common), "notapid.json")).write_text("{}")
        assert st.prune(common, alive=lambda pid: False) == 0


# ---------------------------------------------------------------------------
# 🔴 THE ON-DISK ARTIFACT NAMES, pinned as WHOLE relative paths.
#
# `scripts/claude-hooks/tests/test_on_disk_artifact_names.py` classifies
# `session-stamp.py` as delegating every path to this module, and its entry
# NAMES the test below as where those paths are pinned. That cross-reference is
# a claim: if this test does not exist, or stops pinning whole paths, the ledger
# entry reads as coverage while providing none.
#
# 🔴 The corroboration scan in that file CANNOT see these names — it greps for
# `.cache` / `.local/share` / XDG_CACHE_HOME literals, and this state lives in
# the repo's own <git-common-dir>, not under $HOME at all. So the enumeration
# plus this pin are the whole of the coverage; there is no second net.
#
# Whole paths, not fragments: a pin on the leaf alone stays green when the
# parent directory is renamed, which is exactly the rename that would strand
# every existing session's state.
# ---------------------------------------------------------------------------
def test_the_on_disk_artifact_names_are_pinned_as_whole_paths():
    assert st.STATE_DIRNAME == "claude-session"
    assert st.state_dir("/COMMON") == "/COMMON/claude-session"
    assert st.state_file("/COMMON", 4242) == "/COMMON/claude-session/4242.json"


def test_the_trailer_keys_are_pinned():
    """A renamed key silently stops matching every trailer already in history —
    `has_trailer` would stop seeing them and every amend would add a duplicate."""
    assert st.TRAILER_KEY == "Claude-Session-Id"
    assert st.LEGACY_TRAILER_KEY == "Claude-Session"
