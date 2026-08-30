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
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "lib" / "session_trailer.py"

sys.path.insert(0, str(REPO / "scripts"))

from testlib import hermetic_git  # noqa: E402


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


# ---------------------------------------------------------------------------
# 🔴 ROUND-4 GUARDS. The round-3 audit found that reverting the WHOLE of
# session_trailer.py to its round-2 version left all 137 tests across five files
# GREEN — three shipped behaviour changes and one correctness fix with nothing
# that could see them. Positive control at the time: mutating TRAILER_KEY turned
# 16 red, so the harness reached the module; the SURVIVED was real, not wiring.
# These are the discriminators.
# ---------------------------------------------------------------------------
class TestExoticLineSeparatorsAreNotRewritten:
    """🔴 `splitlines()` also breaks on \\r, \\x0b, \\x0c, \\x85, \\u2028 and
    \\u2029, so rejoining with "\\n" EDITS prose it was only meant to read.
    Measured on the round-2 code: a CRLF message lost every \\r, and a body
    containing \\x0b gained a line break the author never wrote."""

    @pytest.mark.parametrize("sep,name", [
        ("\r", "CR"), ("\x0b", "VT"), ("\x0c", "FF"),
        ("\x85", "NEL"), (" ", "LS"), (" ", "PS"),
    ])
    def test_a_body_separator_survives_a_rewrite(self, sep, name):
        msg = f"feat: x\n\nbo{sep}dy\n\nClaude-Session-Id: OLD\n"
        out = st.append_trailer(msg, "NEW")
        assert "Claude-Session-Id: NEW" in out
        assert f"bo{sep}dy" in out, (
            f"a {name} in the body was rewritten as a line break")

    def test_a_crlf_message_keeps_its_carriage_returns(self):
        msg = "feat: x\r\n\r\nClaude-Session-Id: OLD\r\nCo-Authored-By: Z <z@e>\r\n"
        out = st.append_trailer(msg, "NEW")
        assert "Claude-Session-Id: NEW" in out
        assert "Co-Authored-By: Z <z@e>\r" in out, "the CRLF body lost its \\r"


class TestSameIdDuplicatesCollapse:
    """🔴 The early return used to test only "are they all already correct",
    which is TRUE for a message carrying the same id twice — measured count 2."""

    def test_two_identical_trailers_collapse_to_one(self):
        msg = "feat: x\n\nClaude-Session-Id: A\nClaude-Session-Id: A\n"
        assert st.append_trailer(msg, "A").count("Claude-Session-Id:") == 1

    def test_a_single_correct_trailer_is_still_a_byte_identical_no_op(self):
        """Positive control: the fix did not simply stop returning early."""
        msg = "feat: x\n\nClaude-Session-Id: A\n"
        assert st.append_trailer(msg, "A") == msg

    @pytest.mark.parametrize("first,second", [("A", "B"), ("B", "A")])
    def test_collapse_keeps_the_first_position(self, first, second):
        msg = (f"feat: x\n\nClaude-Session-Id: {first}\n"
               f"Claude-Session-Id: {second}\nCo-Authored-By: Z <z@e>\n")
        out = st.append_trailer(msg, "A")
        lines = out.split("\n")
        assert out.count("Claude-Session-Id:") == 1
        assert lines.index("Claude-Session-Id: A") < lines.index("Co-Authored-By: Z <z@e>")


class TestTheStateFileModeSurvivesALeftoverTemp:
    """🔴 `os.open(..., 0o600)` applies the mode ONLY on creation, so a leftover
    `.tmp` is reused with its OLD mode and `os.replace` carries that onto the
    target. The reachable leftover is this feature's own: an earlier revision
    created it at 0644 with `open(tmp,"w")`, and it survives a kill between the
    write and the replace."""

    def test_a_stale_0644_temp_does_not_downgrade_the_state_file(self, tmp_path):
        common = str(tmp_path)
        os.makedirs(common, exist_ok=True)
        stale = st.state_file(4242, common) + ".tmp"
        Path(stale).write_text("{}")
        os.chmod(stale, 0o644)
        assert st.record("s", 4242, root=common, reader=LIVE) is True
        mode = oct(os.stat(st.state_file(4242, common)).st_mode)[-3:]
        assert mode == "600", f"state file shipped {mode}, not 600"

    def test_the_clean_path_is_also_0600(self, tmp_path):
        """Positive control — so the test above is about the LEFTOVER, not about
        record() being broken in general."""
        common = str(tmp_path)
        assert st.record("s", 4243, root=common, reader=LIVE) is True
        assert oct(os.stat(st.state_file(4243, common)).st_mode)[-3:] == "600"


# ---------------------------------------------------------------------------
# 🔴 GIT ITSELF IS THE ORACLE. Every guard above asserts that the trailer TEXT
# is somewhere in the message — which was TRUE of all three message shapes while
# git recognised the trailer in only two of them, so this whole file passed
# straight through the defect. The guards below assert the property that
# actually matters: `git interpret-trailers --parse` returns the stamp. None of
# them derives its expectation from `append_trailer`'s own formatting.
# ---------------------------------------------------------------------------
GIT_ENV = hermetic_git.hermetic_git_env()


def _git_parsed_trailers(message: str) -> str:
    """What GIT reads as this message's trailer block — never our own parser."""
    return subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=message, capture_output=True, text=True, env=GIT_ENV).stdout


class TestGitParsesTheStampedTrailer:
    """🔴 REGRESSION for the single-paragraph shape; INVARIANT GUARDS for the rest.

    MEASURED at `a5bc5df6` (2026-08-30): `append_trailer("fix: one line only\\n",
    …)` returned `'fix: one line only\\nClaude-Session-Id: …\\n'` — no blank line
    — and `git interpret-trailers --parse` returned `''`. Confirmed on a real
    commit object, whose `%(trailers:key=Claude-Session-Id,valueonly)` was EMPTY.
    The cause was a separator test that looked at the LAST LINE alone, which a
    conventional-commit SUBJECT satisfies: `fix: one line only` matches
    `^[A-Za-z][A-Za-z0-9-]*:\\s`.

    ⚠ THE SPLIT BELOW IS MEASURED, NOT ASSERTED. Each shape was run through
    `append_trailer` at `a5bc5df6` AND at HEAD, and its verdict read off `git
    interpret-trailers --parse`; `REGRESSION` is exactly the set that came back
    RED at base. Everything in `INVARIANT` was GREEN there, so it is NOT
    regression coverage — it is here because the obvious WRONG fix (always
    appending "\\n\\n") greens every RED shape while splitting an existing
    trailer block, which is the shape every agent commit in this repo has.
    """

    # RED at a5bc5df6, GREEN at HEAD. All four share one cause: the old test
    # looked at the LAST LINE, and `fix: …` / `Note: …` satisfy it wherever they
    # sit — so the stamp was glued onto a paragraph git reads as prose.
    REGRESSION = [
        ("single-line", "fix: one line only\n"),
        ("prose-then-trailerish", "fix: x\n\nSome prose here.\nNote: something\n"),
        ("trailerish-second-line", "fix: x\nNote: right after the subject\n"),
        ("subject-and-blank-only", "fix: x\n\n"),
    ]
    # GREEN at a5bc5df6 too — invariant guards, pinning what must not break.
    INVARIANT = [
        ("with-body", "fix: x\n\nbody here.\n"),
        ("existing-trailer", "fix: x\n\nbody.\n\nCo-Authored-By: A <a@b.c>\n"),
        ("agent-shape", "fix: x\n\nbody.\n\nCo-Authored-By: A <a@b.c>\n"
                        "Claude-Session: https://example.invalid/s/1\n"),
        ("subject-no-colon", "subject with no colon at all\n"),
        # 🔴 THE MUTATION-SWEEP FIXTURE. Every other shape here is decided by
        # the paragraph's FIRST line, so dropping the check that the WHOLE
        # paragraph is trailers SURVIVED a fully green run. git does not read
        # this last paragraph as a block (measured), so the stamp must be
        # separated from it or git parses nothing.
        ("trailerish-then-prose",
         "fix: x\n\nbody\n\nCo-Authored-By: A <a@b.c>\nplain prose line\n"),
        ("continuation-line", "fix: x\n\nbody\n\nCo-Authored-By: A\n    <a@b.c>\n"),
    ]

    @pytest.mark.parametrize("name,message", REGRESSION + INVARIANT)
    def test_git_reads_the_stamp_as_a_trailer(self, name, message):
        out = st.append_trailer(message, "SID-42")
        assert "Claude-Session-Id: SID-42" in _git_parsed_trailers(out), (
            f"git does not read the stamp as a trailer for the {name} shape; "
            f"the stamped message was {out!r}")

    @pytest.mark.parametrize("name,message", [
        ("existing-trailer", "fix: x\n\nbody.\n\nCo-Authored-By: A <a@b.c>\n"),
        ("continuation-line", "fix: x\n\nbody\n\nCo-Authored-By: A\n    <a@b.c>\n"),
    ])
    def test_a_sibling_trailer_keeps_its_block(self, name, message):
        """🔴 The always-"\\n\\n" fix passes the test above and fails this one: a
        blank line inserted INSIDE a trailer block leaves git parsing only the
        half after it, so `Co-Authored-By` stops being a trailer at all.

        MEASURED per shape at `a5bc5df6`: `existing-trailer` KEPT its sibling
        (invariant guard), `continuation-line` LOST it (regression) — the old
        tail test saw the indented `    <a@b.c>` as prose and split the block.
        """
        parsed = _git_parsed_trailers(st.append_trailer(message, "SID-42"))
        assert "Co-Authored-By: A <a@b.c>" in parsed, (
            f"the {name} shape lost its sibling trailer; git parsed {parsed!r}")
        assert "Claude-Session-Id: SID-42" in parsed

    def test_the_oracle_can_report_absence(self):
        """🔴 NEGATIVE CONTROL. Without it, a `--parse` wired to nothing would
        make every assertion above vacuous. The input is the exact broken output
        `a5bc5df6` produced, handed to git directly."""
        assert _git_parsed_trailers(
            "fix: one line only\nClaude-Session-Id: SID-42\n") == ""

    def test_the_oracle_can_report_a_trailer(self):
        """POSITIVE CONTROL: a hand-written, literally-correct message, so the
        expectation is not derived from `append_trailer`."""
        assert "Claude-Session-Id: SID-42" in _git_parsed_trailers(
            "fix: x\n\nbody.\n\nClaude-Session-Id: SID-42\n")


class TestTheSeparatorIsChosenFromTheLastParagraph:
    """The unit behind the property above, pinned as LITERAL expected strings so
    the shapes are legible without running git."""

    def test_a_single_paragraph_message_gains_a_blank_line(self):
        assert st.append_trailer("fix: one line only\n", "SID-42") == (
            "fix: one line only\n\nClaude-Session-Id: SID-42\n")

    def test_a_trailer_block_is_joined_with_no_blank_line(self):
        assert st.append_trailer(
            "fix: x\n\nCo-Authored-By: A <a@b.c>\n", "SID-42") == (
            "fix: x\n\nCo-Authored-By: A <a@b.c>\nClaude-Session-Id: SID-42\n")

    def test_a_trailerish_line_in_the_subject_paragraph_is_not_a_block(self):
        """git never reads the FIRST paragraph as trailers, however it looks."""
        assert st._ends_in_trailer_block("fix: x\nNote: right after") is False

    def test_a_mixed_last_paragraph_is_not_a_block(self):
        assert st._ends_in_trailer_block(
            "fix: x\n\nSome prose here.\nNote: something") is False

    def test_a_paragraph_that_only_STARTS_as_trailers_is_not_a_block(self):
        """🔴 The discriminating case: judging the paragraph by its first line
        alone SURVIVED a fully green suite until this fixture existed."""
        assert st._ends_in_trailer_block(
            "fix: x\n\nbody\n\nCo-Authored-By: A <a@b.c>\nplain prose line"
        ) is False

    def test_an_indented_continuation_stays_inside_the_block(self):
        assert st._ends_in_trailer_block(
            "fix: x\n\nbody\n\nCo-Authored-By: A\n    <a@b.c>") is True
