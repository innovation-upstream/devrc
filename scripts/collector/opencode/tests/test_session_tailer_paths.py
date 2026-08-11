"""GAP B — the file/git statistics OpenCode used to pin at zero, plus the
changed-path set.

Run: python -m pytest scripts/collector/opencode/tests/test_session_tailer_paths.py -v

See `session_tailer.tool_input()` for the measured store shape and
`build_rollup()`'s docstring for the absent-vs-zero contract.

No path in this file is real; every fixture uses invented repo names.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COLLECTOR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(COLLECTOR))
sys.path.insert(0, str(COLLECTOR / "keylog"))
sys.path.insert(0, str(COLLECTOR / "opencode"))

import session_tailer as T  # noqa: E402


REPO = "/srv/checkouts/widget-repo"
SESSION = {"id": "ses_x", "cost": 0.0, "tokens": {},
           "time_created": 1700000000000, "time_updated": 1700000060000}
MSGS = [{"role": "user"}, {"role": "assistant"}]


def _part(tool, inp, *, status="completed", error=None):
    """A part dict shaped exactly as `_shared.iter_parts` yields one.

    `state.input` is where OpenCode really puts tool arguments — the whole of
    gap B was reading them from the part's top level instead.
    """
    state = {"status": status}
    if inp is not None:
        state["input"] = inp
    if error is not None:
        state["error"] = error
    data = {"type": "tool", "tool": tool, "callID": "call_x", "state": state}
    return {"id": "p", "message_id": "m", "session_id": "s",
            "type": "tool", "tool": tool, "state": state, "_data": data}


def _legacy_part(tool, **top_level):
    """A part in the shape the OLD fixtures used — arguments at the TOP level.

    OpenCode has never emitted this. It exists so a test can assert the
    extractor does NOT silently accept it: accepting both shapes would restore
    the exact blindness this PR removes.
    """
    data = {"type": "tool", "tool": tool, "state": {"status": "completed"},
            **top_level}
    return {"id": "p", "message_id": "m", "session_id": "s",
            "type": "tool", "tool": tool, "state": data["state"], "_data": data}


# --------------------------------------------------------------------------- #
# The statistics themselves
# --------------------------------------------------------------------------- #
class TestOpenCodeFileAndGitStats:
    """POSITIVE CONTROL, STATED AS A PAIR. The pre-change extractor produced a
    zero for every one of these fields on every session, so a test asserting
    only the new non-zero would be indistinguishable from a test asserting a
    constant. `test_legacy_top_level_shape_yields_nothing_and_says_so` is the
    other arm: the shape the old code expected must NOT produce a number."""

    def test_edit_parts_populate_files_modified(self):
        parts = [
            _part("edit", {"filePath": f"{REPO}/src/a.py",
                           "oldString": "x", "newString": "y"}),
            _part("edit", {"filePath": f"{REPO}/src/b.py",
                           "oldString": "x", "newString": "y"}),
        ]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] == 2

    def test_legacy_top_level_shape_yields_nothing_and_says_so(self):
        parts = [_legacy_part("edit", file_path=f"{REPO}/src/a.py"),
                 _legacy_part("bash", command="git commit -m x")]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] is None
        assert r["git_commits"] is None
        assert sorted(r["stats_unavailable"]) == ["files", "git"]

    def test_write_parts_count_and_add_lines(self):
        parts = [_part("write", {"filePath": f"{REPO}/README.md",
                                 "content": "one\ntwo\nthree"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] == 1
        assert r["lines_added"] == 3
        assert r["lines_removed"] == 0

    def test_edit_churn_matches_the_claude_summariser_semantics(self):
        # Claude counts lines(new_string) added and lines(old_string) removed;
        # opencode's keys are camelCase but the MEASURE must be identical, or
        # the two sources' columns are not comparable.
        parts = [_part("edit", {"filePath": f"{REPO}/src/a.py",
                                "oldString": "a\nb",
                                "newString": "a\nb\nc\nd"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["lines_added"] == 4
        assert r["lines_removed"] == 2

    def test_churn_helper_line_counts(self):
        assert T.churn("edit", {"newString": "a\nb\nc", "oldString": "z"}) == (3, 1)
        assert T.churn("write", {"content": "a\nb"}) == (2, 0)
        assert T.churn("read", {"filePath": "x"}) == (0, 0)
        assert T.count_lines("") == 0 and T.count_lines(None) == 0
        assert T.count_lines("a") == 1 and T.count_lines("a\nb") == 2

    def test_git_commit_and_push_come_from_the_nested_command(self):
        parts = [_part("bash", {"command": "git commit -m 'x'"}),
                 _part("bash", {"command": "git push origin main"}),
                 _part("bash", {"command": "ls -la"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["git_commits"] == 1
        assert r["git_pushes"] == 1
        assert r["stats_unavailable"] == []

    def test_languages_are_derived_from_the_nested_filePath(self):
        parts = [_part("edit", {"filePath": f"{REPO}/a.py", "newString": "x"}),
                 _part("write", {"filePath": f"{REPO}/b.ts", "content": "y"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["languages"] == {"Python": 1, "TypeScript": 1}

    def test_a_path_only_key_is_not_accepted_for_file_tools(self):
        """`path` is grep/glob's input key. Accepting it for file tools would
        blunt the drift detector — a `filePath` rename would keep resolving
        through the alternate key and go back to being invisible."""
        parts = [_part("edit", {"path": f"{REPO}/src/a.py"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] is None
        assert "files" in r["stats_unavailable"]

    def test_an_errored_part_still_contributes_its_command(self):
        # MEASURED: all 152 errored tool parts in the live store carry `input`.
        parts = [_part("bash", {"command": "git push origin main"},
                       status="error", error="permission denied")]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["git_pushes"] == 1
        assert r["tool_errors"] == 1


# --------------------------------------------------------------------------- #
# Absent vs zero
# --------------------------------------------------------------------------- #
class TestOpenCodeAbsentVersusZero:
    """A zero that means 'cannot report' is indistinguishable from a real zero.
    These pin the difference in BOTH directions — a test that only covered the
    absent case would stay green if every zero became absent."""

    def test_a_session_that_ran_no_file_tools_reports_a_REAL_zero(self):
        parts = [_part("read", {"filePath": f"{REPO}/src/a.py"}),
                 _part("bash", {"command": "ls"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] == 0
        assert r["lines_added"] == 0
        assert r["git_commits"] == 0
        assert r["changed_paths"] == []
        assert r["stats_unavailable"] == []

    def test_file_tools_present_but_no_readable_path_is_ABSENT_not_zero(self):
        parts = [_part("edit", None), _part("write", None)]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] is None
        assert r["lines_added"] is None
        assert r["lines_removed"] is None
        assert r["changed_paths"] is None
        assert r["stats_unavailable"] == ["files"]

    def test_bash_present_but_no_readable_command_is_ABSENT_not_zero(self):
        parts = [_part("bash", None)]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["git_commits"] is None
        assert r["git_pushes"] is None
        assert r["stats_unavailable"] == ["git"]

    def test_the_two_groups_are_INDEPENDENT(self):
        """A readable bash part beside an unreadable edit part must leave git
        reportable — collapsing them into one verdict would hide a real number
        behind an unrelated failure."""
        parts = [_part("edit", None), _part("bash", {"command": "git commit -m x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["stats_unavailable"] == ["files"]
        assert r["files_modified"] is None
        assert r["git_commits"] == 1

    def test_ONE_readable_part_among_many_unreadable_keeps_the_group_reportable(self):
        """The verdict is 'not ONE part was readable', not 'some were not'. A
        stricter rule would null a genuine reading whenever a single tool call
        happened to be mid-flight when the tailer ran."""
        parts = [_part("edit", None), _part("edit", None),
                 _part("edit", {"filePath": f"{REPO}/a.py", "newString": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["files_modified"] == 1
        assert r["stats_unavailable"] == []

    def test_a_store_read_that_yields_no_parts_at_all_is_ABSENT(self):
        """`_shared.iter_parts` swallows sqlite3.OperationalError and yields
        nothing, so a `part`-table schema change looks exactly like a quiet
        session at the call site. MEASURED: 233 of 233 live sessions with
        assistant messages have >= 1 part, so this fires on drift only."""
        r = T.build_rollup(SESSION, MSGS, [], directory=REPO)
        assert r["changed_paths"] is None
        assert r["files_modified"] is None
        assert r["git_commits"] is None
        assert sorted(r["stats_unavailable"]) == ["files", "git"]

    def test_a_session_with_NO_assistant_messages_and_no_parts_is_a_real_zero(self):
        """The store-unreadable rule is deliberately conditioned on assistant
        messages: a session created and never used genuinely has no parts, and
        calling that unobservable would cry wolf on every one of them.
        (MEASURED: per-MESSAGE the rule would be wrong — 10 of 5,576 assistant
        messages legitimately have zero parts — which is why it is per-SESSION.)"""
        r = T.build_rollup(SESSION, [{"role": "user"}], [], directory=REPO)
        assert r["changed_paths"] == []
        assert r["files_modified"] == 0
        assert r["stats_unavailable"] == []

    def test_stats_unavailable_ships_on_every_rollup(self):
        """An intermittently-absent key is indistinguishable from a consumer
        reading the wrong name."""
        parts = [_part("edit", {"filePath": f"{REPO}/a.py", "newString": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert "stats_unavailable" in r and r["stats_unavailable"] == []


# --------------------------------------------------------------------------- #
# The changed-path set
# --------------------------------------------------------------------------- #
class TestOpenCodeChangedPaths:
    def test_paths_are_repo_relative_deduped_and_sorted(self):
        parts = [_part("edit", {"filePath": f"{REPO}/src/z.py", "newString": "x"}),
                 _part("edit", {"filePath": f"{REPO}/src/a.py", "newString": "x"}),
                 _part("write", {"filePath": f"{REPO}/src/a.py", "content": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["changed_paths"] == ["src/a.py", "src/z.py"]
        assert r["changed_paths_total"] == 2

    def test_paths_outside_the_session_directory_are_counted_not_emitted(self):
        parts = [_part("write", {"filePath": f"{REPO}/a.py", "content": "x"}),
                 _part("write", {"filePath": "/var/tmp/scratch/n.md", "content": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["changed_paths"] == ["a.py"]
        assert r["changed_paths_outside_cwd"] == 1

    def test_the_accounting_invariant_holds(self):
        parts = [_part("write", {"filePath": f"{REPO}/a.py", "content": "x"}),
                 _part("write", {"filePath": "/var/tmp/x.md", "content": "x"}),
                 _part("write", {"filePath": "/var/tmp/y.md", "content": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert (r["changed_paths_total"] + r["changed_paths_outside_cwd"]
                == r["files_modified"])

    def test_truncation_is_reported_end_to_end(self):
        cap = T.CP.CHANGED_PATHS_CAP
        parts = [_part("write", {"filePath": f"{REPO}/f{i:05d}.py", "content": "x"})
                 for i in range(cap + 3)]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["changed_paths_truncated"] is True
        assert len(r["changed_paths"]) == cap
        # The TRUE count survives the truncation — that is what stops a
        # consumer reading the short list as complete.
        assert r["changed_paths_total"] == cap + 3
        assert r["files_modified"] == cap + 3

    def test_just_under_the_cap_is_not_truncated(self):
        cap = T.CP.CHANGED_PATHS_CAP
        parts = [_part("write", {"filePath": f"{REPO}/f{i:05d}.py", "content": "x"})
                 for i in range(cap)]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        assert r["changed_paths_truncated"] is False
        assert len(r["changed_paths"]) == cap

    def test_emitted_paths_satisfy_the_downstream_resolver_contract(self):
        """subsystem_resolver._validate_path RAISES on an absolute path or a
        `..` segment, so an emitter producing one would blow P1 up rather than
        merely degrade it."""
        parts = [_part("write", {"filePath": f"{REPO}/a/b.py", "content": "x"}),
                 _part("write", {"filePath": "/elsewhere/c.py", "content": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        for p in r["changed_paths"]:
            assert not p.startswith("/") and ".." not in p.split("/")

    def test_an_empty_session_directory_makes_absolute_paths_unrelativizable(self):
        parts = [_part("write", {"filePath": f"{REPO}/a.py", "content": "x"})]
        r = T.build_rollup(SESSION, MSGS, parts, directory="")
        assert r["changed_paths"] == []
        assert r["changed_paths_outside_cwd"] == 1
        assert r["files_modified"] == 1


# --------------------------------------------------------------------------- #
# tool_input — the extractor at the heart of gap B
# --------------------------------------------------------------------------- #
class TestOpenCodeToolInput:
    def test_reads_the_nested_input(self):
        assert T.tool_input({"state": {"input": {"a": 1}}}) == {"a": 1}

    def test_missing_state_is_None_not_empty_dict(self):
        # None vs {} is the difference between "could not read" and "read, empty".
        assert T.tool_input({"tool": "edit"}) is None

    def test_non_dict_state_is_None(self):
        assert T.tool_input({"state": "completed"}) is None

    def test_missing_input_is_None(self):
        assert T.tool_input({"state": {"status": "completed"}}) is None

    def test_non_dict_input_is_None(self):
        assert T.tool_input({"state": {"input": "git commit"}}) is None

    def test_top_level_keys_are_NOT_consulted(self):
        assert T.tool_input({"command": "git commit", "filePath": "/a"}) is None


class TestOpenCodeRollupIsSerializable:
    def test_the_whole_rollup_round_trips_through_json(self):
        """The payload is JSON-encoded into the spool line, so a None must
        survive as `null` — readable by a consumer — rather than crash the
        emit."""
        parts = [_part("edit", None)]
        r = T.build_rollup(SESSION, MSGS, parts, directory=REPO)
        back = json.loads(json.dumps(r))
        assert back["files_modified"] is None
        assert back["changed_paths"] is None
        assert back["stats_unavailable"] == ["files"]
