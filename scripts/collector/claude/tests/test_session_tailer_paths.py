"""The `changed_paths*` block on the Claude Layer-A session-summary rollup.

Run: python -m pytest scripts/collector/claude/tests/test_session_tailer_paths.py -v

`files_modified` was only ever a COUNT; the downstream consumer
(`scripts/lib/subsystem_resolver.py`) needs the paths. These tests pin the block
that carries them, and — more importantly — the three discriminators that keep
its short/empty/absent cases apart. An assertion on the list alone would pass in
every one of the failure modes this block exists to prevent.

No path in this file is real; every fixture uses invented repo names.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_CLAUDE_DIR = Path(__file__).resolve().parent.parent
_COLLECTOR_DIR = _CLAUDE_DIR.parent
sys.path.insert(0, str(_CLAUDE_DIR))
sys.path.insert(0, str(_COLLECTOR_DIR))

_spec = importlib.util.spec_from_file_location(
    "session_tailer_paths_under_test", _CLAUDE_DIR / "session-tailer.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

import changed_paths as CP  # noqa: E402


REPO = "/srv/checkouts/widget-repo"


def _assistant(tool_uses, *, cwd=REPO, ts="2026-07-11T10:01:00.000Z"):
    return {"type": "assistant", "timestamp": ts, "cwd": cwd,
            "message": {"role": "assistant", "model": "m",
                        "content": [{"type": "tool_use", "name": n, "input": i}
                                    for n, i in tool_uses],
                        "usage": {}}}


def _user(text="hi", *, cwd=REPO, ts="2026-07-11T10:00:00.000Z", isSidechain=False):
    return {"type": "user", "timestamp": ts, "cwd": cwd,
            "isSidechain": isSidechain,
            "message": {"role": "user", "content": text}}


def _rollup(tool_uses, *, cwd=REPO):
    return S.build_rollup([_user(cwd=cwd), _assistant(tool_uses, cwd=cwd)])


# --------------------------------------------------------------------------- #
# Positive control
# --------------------------------------------------------------------------- #
class TestClaudeChangedPathsPositiveControl:
    """A reassuring empty list is indistinguishable from an extractor wired to
    nothing — the exact defect on the opencode side of this PR. So the count is
    shown to MOVE between two rollups from the same code path."""

    def test_a_session_with_no_file_tools_yields_an_empty_list(self):
        r = _rollup([("Bash", {"command": "ls"}), ("Read", {"file_path": "x"})])
        assert r["changed_paths"] == []
        assert r["changed_paths_total"] == 0
        assert r["files_modified"] == 0

    def test_a_session_with_file_tools_yields_those_paths(self):
        r = _rollup([("Edit", {"file_path": f"{REPO}/src/app.py",
                               "old_string": "a", "new_string": "b"}),
                     ("Write", {"file_path": f"{REPO}/README.md",
                                "content": "hello"})])
        assert r["changed_paths"] == ["README.md", "src/app.py"]
        assert r["changed_paths_total"] == 2
        assert r["files_modified"] == 2

    def test_every_file_tool_contributes_its_path(self):
        r = _rollup([
            ("Edit", {"file_path": f"{REPO}/a.py", "new_string": "x"}),
            ("Write", {"file_path": f"{REPO}/b.py", "content": "x"}),
            ("MultiEdit", {"file_path": f"{REPO}/c.py",
                           "edits": [{"old_string": "1", "new_string": "2"}]}),
            ("NotebookEdit", {"notebook_path": f"{REPO}/d.ipynb",
                              "new_source": "x"}),
        ])
        assert r["changed_paths"] == ["a.py", "b.py", "c.py", "d.ipynb"]

    def test_a_read_only_tool_does_not_contribute_a_path(self):
        r = _rollup([("Read", {"file_path": f"{REPO}/secret.py"}),
                     ("Grep", {"path": f"{REPO}/src"})])
        assert r["changed_paths"] == []


# --------------------------------------------------------------------------- #
# The discriminators
# --------------------------------------------------------------------------- #
class TestClaudeChangedPathsDiscriminators:
    def test_paths_are_repo_relative_not_absolute(self):
        r = _rollup([("Write", {"file_path": f"{REPO}/src/app.py", "content": "x"})])
        assert r["changed_paths"] == ["src/app.py"]

    def test_paths_outside_the_session_cwd_are_counted_not_emitted(self):
        r = _rollup([("Write", {"file_path": f"{REPO}/a.py", "content": "x"}),
                     ("Write", {"file_path": "/var/tmp/scratch/n.md", "content": "x"})])
        assert r["changed_paths"] == ["a.py"]
        assert r["changed_paths_outside_cwd"] == 1

    def test_the_accounting_invariant_holds(self):
        """`total + outside_cwd == files_modified` — so a consumer can see how
        much of the session it is NOT being told about, rather than inferring
        it from a list that merely looks short."""
        r = _rollup([("Write", {"file_path": f"{REPO}/a.py", "content": "x"}),
                     ("Write", {"file_path": "/var/tmp/x.md", "content": "x"}),
                     ("Edit", {"file_path": "/var/tmp/y.md", "new_string": "x"})])
        assert (r["changed_paths_total"] + r["changed_paths_outside_cwd"]
                == r["files_modified"])

    def test_duplicates_across_turns_collapse(self):
        r = _rollup([("Edit", {"file_path": f"{REPO}/a.py", "new_string": "1"}),
                     ("Edit", {"file_path": f"{REPO}/a.py", "new_string": "2"}),
                     ("Write", {"file_path": f"{REPO}/a.py", "content": "3"})])
        assert r["changed_paths"] == ["a.py"]
        assert r["changed_paths_total"] == 1
        assert r["files_modified"] == 1

    def test_ordering_is_deterministic_regardless_of_edit_order(self):
        forward = _rollup([("Write", {"file_path": f"{REPO}/{n}", "content": "x"})
                           for n in ("z.py", "a.py", "m.py")])
        reverse = _rollup([("Write", {"file_path": f"{REPO}/{n}", "content": "x"})
                           for n in ("m.py", "z.py", "a.py")])
        assert forward["changed_paths"] == reverse["changed_paths"]
        assert forward["changed_paths"] == ["a.py", "m.py", "z.py"]

    def test_truncation_is_reported_end_to_end(self):
        cap = CP.CHANGED_PATHS_CAP
        r = _rollup([("Write", {"file_path": f"{REPO}/f{i:05d}.py", "content": "x"})
                     for i in range(cap + 5)])
        assert r["changed_paths_truncated"] is True
        assert len(r["changed_paths"]) == cap
        assert r["changed_paths_total"] == cap + 5
        assert r["files_modified"] == cap + 5

    def test_exactly_at_the_cap_is_not_truncated(self):
        cap = CP.CHANGED_PATHS_CAP
        r = _rollup([("Write", {"file_path": f"{REPO}/f{i:05d}.py", "content": "x"})
                     for i in range(cap)])
        assert r["changed_paths_truncated"] is False
        assert len(r["changed_paths"]) == cap

    def test_emitted_paths_satisfy_the_downstream_resolver_contract(self):
        """`subsystem_resolver._validate_path` RAISES on an absolute path or a
        `..` segment; an emitter producing one would blow up P1."""
        r = _rollup([("Write", {"file_path": f"{REPO}/a/b.py", "content": "x"}),
                     ("Write", {"file_path": "/elsewhere/c.py", "content": "x"})])
        for p in r["changed_paths"]:
            assert not p.startswith("/") and ".." not in p.split("/")

    def test_the_cap_is_reported_on_the_payload(self):
        assert _rollup([])["changed_paths_cap"] == CP.CHANGED_PATHS_CAP


# --------------------------------------------------------------------------- #
# Absent vs empty
# --------------------------------------------------------------------------- #
class TestClaudeUnobservable:
    def test_an_unreadable_transcript_reports_ABSENT_not_empty(self):
        """`unreadable` means we never saw a single message — so the file set is
        UNKNOWN. Emitting [] here would recreate gap B one source over."""
        r = S.build_rollup([])
        assert r["unreadable"] is True
        assert r["changed_paths"] is None
        assert r["changed_paths_total"] is None
        assert r["changed_paths_truncated"] is None
        assert r["changed_paths_outside_cwd"] is None
        assert r["stats_unavailable"] == ["files", "git"]

    def test_an_unopenable_file_reports_ABSENT(self, tmp_path):
        r = S.summarize_transcript(str(tmp_path / "does-not-exist.jsonl"))
        assert r["unreadable"] is True
        assert r["changed_paths"] is None
        assert r["stats_unavailable"] == ["files", "git"]

    def test_a_garbage_file_reports_ABSENT(self, tmp_path):
        p = tmp_path / "junk.jsonl"
        p.write_text("not json at all\n{{{\n", encoding="utf-8")
        r = S.summarize_transcript(str(p))
        assert r["unreadable"] is True
        assert r["changed_paths"] is None

    def test_a_READABLE_session_that_changed_nothing_reports_EMPTY(self, tmp_path):
        """The other arm. Without it, a change making EVERY session report None
        would still pass the tests above."""
        p = tmp_path / "s.jsonl"
        p.write_text("\n".join(json.dumps(o) for o in
                               [_user(), _assistant([("Bash", {"command": "ls"})])]),
                     encoding="utf-8")
        r = S.summarize_transcript(str(p))
        assert r["unreadable"] is False
        assert r["changed_paths"] == []
        assert r["stats_unavailable"] == []

    def test_empty_and_absent_are_not_equal(self):
        assert S.build_rollup([])["changed_paths"] is not _rollup([])["changed_paths"]
        assert S.build_rollup([])["changed_paths"] != _rollup([])["changed_paths"]


# --------------------------------------------------------------------------- #
# Additive-only: the existing contract must be untouched
# --------------------------------------------------------------------------- #
class TestClaudeRollupStaysAdditive:
    """`session-summary` is consumed live by session-analysis/insights.py and
    pinned by validation/invariants.py. Adding a key is safe; renaming one or
    changing its TYPE is not."""

    def test_every_previously_required_key_survives_with_its_type(self):
        # ⚠ INVARIANT GUARD, not regression coverage: it is green on the
        # pre-change code too, by construction — that is the property it pins.
        r = _rollup([("Edit", {"file_path": f"{REPO}/a.py", "new_string": "x"}),
                     ("Bash", {"command": "git commit -m x"})])
        for key in ("tool_counts", "user_message_count", "assistant_message_count",
                    "input_tokens", "output_tokens", "unreadable"):
            assert key in r
        assert isinstance(r["tool_counts"], dict)
        assert isinstance(r["unreadable"], bool)

    def test_the_legacy_integer_counters_stay_INTEGERS_even_when_unobservable(self):
        """Deliberate asymmetry with the opencode summariser, which nulls them:
        these five are read live by insights.py's aggregation, so nulling them
        would be a TYPE change on a consumed field. `stats_unavailable` and
        `changed_paths` carry the distinction instead."""
        r = S.build_rollup([])
        for key in ("files_modified", "lines_added", "lines_removed",
                    "git_commits", "git_pushes"):
            assert isinstance(r[key], int), key
        assert r["stats_unavailable"] == ["files", "git"]

    def test_the_payload_round_trips_through_json(self):
        r = _rollup([("Write", {"file_path": f"{REPO}/a.py", "content": "x"})])
        back = json.loads(json.dumps(r))
        assert back["changed_paths"] == ["a.py"]

    def test_no_changed_path_leaks_free_text(self):
        """The block carries PATHS only — never a command, a prompt or file
        content, which would put transcript free-text on a field consumers
        render."""
        r = _rollup([("Write", {"file_path": f"{REPO}/a.py",
                                "content": "SUPERSECRET"}),
                     ("Bash", {"command": "echo ALSOSECRET"})])
        block = {k: v for k, v in r.items() if k.startswith("changed_paths")}
        # 🔴 POSITIVE CONTROL FIRST. Without it this test passes vacuously on
        # any tree where the block does not exist — an empty blob contains no
        # secret, which is a fact about the blob, not about the emitter.
        assert set(block) == set(CP.PAYLOAD_KEYS), block
        assert block["changed_paths"] == ["a.py"]
        blob = json.dumps(block)
        assert "SUPERSECRET" not in blob and "ALSOSECRET" not in blob


# --------------------------------------------------------------------------- #
# Interaction with the existing rollup rules
# --------------------------------------------------------------------------- #
class TestClaudeChangedPathsInteractions:
    def test_sidechain_turns_do_not_contribute_paths(self):
        """Subagent turns are excluded from every other rollup field (so their
        timestamps cannot inflate duration); the path set must agree, or one
        field would describe a different session from the rest."""
        objs = [_user(),
                _assistant([("Write", {"file_path": f"{REPO}/main.py",
                                       "content": "x"})]),
                dict(_assistant([("Write", {"file_path": f"{REPO}/sub.py",
                                            "content": "x"})]),
                     isSidechain=True)]
        r = S.build_rollup(objs)
        assert r["changed_paths"] == ["main.py"]

    def test_the_cwd_used_is_the_transcript_cwd(self):
        other = "/srv/checkouts/other-repo"
        r = _rollup([("Write", {"file_path": f"{other}/a.py", "content": "x"})],
                    cwd=REPO)
        assert r["changed_paths"] == []
        assert r["changed_paths_outside_cwd"] == 1
        assert r["cwd"] == REPO

    def test_a_relative_file_path_is_kept_as_written(self):
        r = _rollup([("Write", {"file_path": "src/app.py", "content": "x"})])
        assert r["changed_paths"] == ["src/app.py"]


# --------------------------------------------------------------------------- #
# A malformed file-path value is COUNTED, not fatal and not invented
# --------------------------------------------------------------------------- #
class TestClaudeUnusableFilePaths:
    """🔴 NEGATIVE CONTROL FIRST — every case below RAISED before this guard,
    and `run()` had no per-session try, so ONE of them anywhere under
    `~/.claude/projects` aborted the pass and stopped ALL claude
    session-summary emission. Measured on the pre-change tree, the six shapes
    that did it were: a blank `file_path` (ValueError from
    `changed_paths.to_repo_relative`), a list or dict one (TypeError,
    unhashable, before summarize() was ever reached), an int one (TypeError in
    `lang_for_path`), a `tool_use.input` that is not a dict (AttributeError),
    and a non-string `timestamp` (TypeError under `<`). The last is deliberately
    NOT closed here — it is what keeps run()'s wrapper reachable rather than
    dead code; see test_session_tailer.py.

    The value is never coerced into a path: `str(["a.py"])` is `"['a.py']"`,
    which `to_repo_relative` would happily accept as a relative path and EMIT.
    """

    def test_the_counter_is_zero_when_every_path_is_usable(self):
        """POSITIVE CONTROL. Without this arm, a guard that counted EVERY path
        would satisfy every assertion below."""
        r = _rollup([("Write", {"file_path": f"{REPO}/a.py", "content": "x"})])
        assert r["unusable_file_paths"] == 0
        assert r["changed_paths"] == ["a.py"]

    def test_a_blank_file_path_is_counted_not_fatal(self):
        r = _rollup([("Write", {"file_path": "   ", "content": "x"})])
        assert r["unusable_file_paths"] == 1
        assert r["changed_paths"] == []
        assert r["files_modified"] == 0

    @pytest.mark.parametrize("bad", [["a.py"], {"p": "a.py"}, 7, 3.5, True])
    def test_a_non_string_file_path_is_counted_not_emitted(self, bad):
        r = _rollup([("Write", {"file_path": bad, "content": "x"})])
        assert r["unusable_file_paths"] == 1
        assert r["changed_paths"] == []

    def test_a_non_dict_tool_input_is_counted_not_fatal(self):
        """`inp = block.get("input") or {}` kept a truthy LIST, and the next
        `.get` raised. This one is not path-specific — it killed the pass for a
        Bash block too."""
        r = S.build_rollup([_user(), _assistant([("Write", ["file_path", "a.py"])])])
        assert r["unusable_file_paths"] == 1
        assert r["changed_paths"] == []
        # the tool was still counted — the block's shape is malformed, its
        # existence is not in doubt
        assert r["tool_counts"]["Write"] == 1

    def test_a_missing_file_path_is_not_counted_as_unusable(self):
        """Absent is not malformed. A file tool with no path argument at all
        contributes nothing and must not inflate the diagnostic."""
        r = _rollup([("Write", {"content": "x"}),
                     ("Edit", {"file_path": "", "new_string": "x"})])
        assert r["unusable_file_paths"] == 0
        assert r["changed_paths"] == []

    def test_a_good_path_beside_a_bad_one_still_lands(self):
        r = _rollup([("Write", {"file_path": f"{REPO}/good.py", "content": "x"}),
                     ("Write", {"file_path": ["bad.py"], "content": "x"})])
        assert r["changed_paths"] == ["good.py"]
        assert r["unusable_file_paths"] == 1

    def test_the_accounting_invariant_survives_a_malformed_entry(self):
        """`total + outside_cwd == files_modified` is the field consumers read
        to size what they are NOT being told. An entry excluded from
        `files_modified` must be excluded from both sides, or the invariant
        turns into a silent off-by-N."""
        r = _rollup([("Write", {"file_path": f"{REPO}/a.py", "content": "x"}),
                     ("Write", {"file_path": "/var/tmp/b.py", "content": "x"}),
                     ("Write", {"file_path": "  ", "content": "x"})])
        assert (r["changed_paths_total"] + r["changed_paths_outside_cwd"]
                == r["files_modified"])
        assert r["unusable_file_paths"] == 1

    def test_an_unusable_value_never_reaches_the_payload(self):
        """It is COUNTED, never carried. A malformed `file_path` is attacker- or
        bug-supplied free text, and the block is documented as paths only."""
        r = _rollup([("Write", {"file_path": {"leak": "SUPERSECRET"},
                                "content": "x"})])
        assert r["unusable_file_paths"] == 1
        assert "SUPERSECRET" not in json.dumps(r)

    def test_the_counter_is_present_on_an_unobservable_rollup(self):
        """A key that is sometimes absent is indistinguishable from a consumer
        reading the wrong name — the rule the whole payload block is built on."""
        assert S.build_rollup([])["unusable_file_paths"] == 0
