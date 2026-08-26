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

    @pytest.mark.parametrize("falsy", [[], "", 0])
    def test_a_FALSY_non_dict_input_is_still_counted(self, falsy):
        """The branch `or {}` cannot see. A falsy non-dict `input` ([] / "" / 0)
        is malformed exactly like a truthy one, but `block.get("input") or {}`
        silently converts it to an empty dict and counts nothing — a surviving
        mutant found by review, on the diagnostic itself rather than on
        fatality (neither spelling raises)."""
        r = S.build_rollup([_user(), _assistant([("Write", falsy)])])
        assert r["unusable_file_paths"] == 1
        assert r["changed_paths"] == []

    def test_an_ABSENT_input_is_not_counted(self):
        """The other arm, and the reason the guard tests `is not None` rather
        than truthiness: a tool_use block with no `input` key at all is absent,
        not malformed."""
        objs = [_user(), {"type": "assistant", "timestamp": "2026-07-11T10:01:00.000Z",
                          "cwd": REPO,
                          "message": {"role": "assistant", "model": "m", "usage": {},
                                      "content": [{"type": "tool_use", "name": "Write"}]}}]
        r = S.build_rollup(objs)
        assert r["unusable_file_paths"] == 0

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


# --------------------------------------------------------------------------- #
# The ABSOLUTE window — the opt-in second frame
# --------------------------------------------------------------------------- #
#
# 🔴 WHY IT EXISTS. `changed_paths` reads every entry against the SESSION's cwd,
# and 86.1% of entries are not under it (3,913 distinct file-tool paths over the
# 636 transcripts the tailer walks, 543 under cwd — 13.9%, reproducing the
# extractor's own 14.3% figure). Some of the remainder are absolute paths into
# another real tree: attributable to it with no inference whatever, and
# discarded because the only frame on offer was the session's. This block is
# that second frame, and NOTHING ELSE — an entry it reports is one the
# transcript already spelled out in full. (For the COUNT of the recoverable
# remainder, and why the 112 that motivated this is retracted, see
# `subsystem_touch.collect_session_paths`.)
OTHER = "/srv/checkouts/other-repo"


class TestAbsoluteWindowPositiveControl:
    """The pair. A zero here is indistinguishable from a frame wired to nothing,
    so both arms run on the same code path with the same fixture shape."""

    def test_THE_PAIR_absolute_under_the_root_yields_relative_under_the_control(self):
        under = [f"{OTHER}/src/a.py", f"{OTHER}/src/b.py"]
        elsewhere = [f"{REPO}/src/a.py", "/var/tmp/scratch/c.py"]
        blocks = [("Edit", {"file_path": p}) for p in under + elsewhere]
        r = S.build_rollup([_user(), _assistant(blocks)], absolute_root=OTHER)
        assert r["changed_paths_absolute"] == ["src/a.py", "src/b.py"], (
            "the absolute frame yielded nothing — it is wired to nothing"
        )
        assert r["changed_paths_absolute_total"] == 2
        assert r["changed_paths_absolute_truncated"] is False
        # …and the CONTROL: the same call, rooted at the session's own repo, sees
        # a different set. One root cannot answer for both, which is the whole
        # reason this is a parameter and not a constant.
        c = S.build_rollup([_user(), _assistant(blocks)], absolute_root=REPO)
        assert c["changed_paths_absolute"] == ["src/a.py"]

    def test_the_DEFAULT_window_is_untouched_by_the_second_frame(self):
        """Two frames on one rollup, and neither may bend the other. The session
        window still answers about the session's cwd."""
        r = S.build_rollup(
            [_user(), _assistant([("Edit", {"file_path": f"{REPO}/src/a.py"}),
                                  ("Edit", {"file_path": f"{OTHER}/src/b.py"})])],
            absolute_root=OTHER,
        )
        assert r["changed_paths"] == ["src/a.py"]
        assert r["changed_paths_outside_cwd"] == 1
        assert r["changed_paths_absolute"] == ["src/b.py"]


class TestAbsoluteWindowNeverInfers:
    """🔴 THE SAFETY PROPERTY, which is the reason the guard downstream exists at
    all. A RELATIVE entry names no tree. Re-anchoring one against a root it was
    never measured against is how another repo's work gets filed here — and it
    is invisible, because the manufactured path resolves perfectly."""

    def test_a_RELATIVE_entry_is_excluded_however_plausible_it_looks(self):
        r = S.build_rollup(
            [_user(cwd=REPO),
             _assistant([("Edit", {"file_path": "src/a.py"}),
                         ("Write", {"file_path": "docs/b.md"})], cwd=REPO)],
            absolute_root=OTHER,
        )
        assert r["changed_paths_absolute"] == []
        # The negative control on that zero: the SAME two paths, spelled
        # absolutely under the same root, ARE reported. So the exclusion above is
        # the relativeness, not the fixture failing to reach the code.
        ok = S.build_rollup(
            [_user(cwd=REPO),
             _assistant([("Edit", {"file_path": f"{OTHER}/src/a.py"}),
                         ("Write", {"file_path": f"{OTHER}/docs/b.md"})], cwd=REPO)],
            absolute_root=OTHER,
        )
        assert ok["changed_paths_absolute"] == ["docs/b.md", "src/a.py"]

    def test_a_SIBLING_directory_sharing_a_name_prefix_is_not_under_the_root(self):
        """`…/other-repo-2/a.py` against `…/other-repo` must not become
        `-2/a.py`. The separator that prevents it lives in `to_repo_relative`,
        which is why this function reuses it rather than restating the prefix
        test."""
        assert CP.absolute_under([f"{OTHER}-2/a.py"], OTHER)["changed_paths_absolute"] == []
        assert CP.absolute_under([f"{OTHER}/a.py"], OTHER)["changed_paths_absolute"] == ["a.py"]

    def test_the_root_DIRECTORY_itself_is_not_a_changed_file(self):
        assert CP.absolute_under([OTHER], OTHER)["changed_paths_absolute"] == []

    def test_a_root_that_is_not_absolute_yields_nothing_rather_than_guessing(self):
        for bad in ("", "relative/root", "."):
            assert CP.absolute_under([f"{OTHER}/a.py"], bad)["changed_paths_absolute"] == []

    def test_an_escaping_absolute_path_is_normalized_before_the_prefix_test(self):
        assert CP.absolute_under(
            [f"{OTHER}/x/../a.py"], OTHER)["changed_paths_absolute"] == ["a.py"]
        assert CP.absolute_under([f"{OTHER}/../a.py"], OTHER)["changed_paths_absolute"] == []


class TestAbsoluteWindowBounds:
    def test_it_is_capped_and_SAYS_SO(self):
        """Same cap, same truncation contract as the default window: the list is
        a lexicographic PREFIX, so a consumer that cannot see it was cut reads a
        late-sorting subtree as absent."""
        paths = [f"{OTHER}/f{i:04d}.py" for i in range(CP.CHANGED_PATHS_CAP + 3)]
        r = CP.absolute_under(paths, OTHER)
        assert len(r["changed_paths_absolute"]) == CP.CHANGED_PATHS_CAP
        assert r["changed_paths_absolute_total"] == CP.CHANGED_PATHS_CAP + 3
        assert r["changed_paths_absolute_truncated"] is True

    def test_EXACTLY_at_the_cap_is_NOT_truncated(self):
        """🔴 THE BOUNDARY, and it was missing: `total > cap` mutated to
        `total >= cap` SURVIVED the whole suite, because the only truncation case
        probed was `cap + 3`. At exactly the cap the list IS complete, so the flag
        would be a lie — and this module's contract is that a truncated list is
        readable as truncated from the numbers alone, which requires the
        un-truncated one to be readable as complete.

        The peer function has had this test since it was written
        (`scripts/collector/tests/test_changed_paths.py::TestTruncation`); the
        copy dropped it. Same `cap=5` shape, deliberately, so the two read as one
        contract rather than two.
        """
        paths = [f"{OTHER}/f{i:04d}.py" for i in range(5)]
        out = CP.absolute_under(paths, OTHER, cap=5)
        assert out["changed_paths_absolute_truncated"] is False
        assert len(out["changed_paths_absolute"]) == 5
        assert out["changed_paths_absolute_total"] == 5

    def test_one_over_the_cap_IS_truncated(self):
        """The other arm at the same boundary. Without it, a mutant pinning the
        flag to a constant False would pass the test above."""
        paths = [f"{OTHER}/f{i:04d}.py" for i in range(6)]
        out = CP.absolute_under(paths, OTHER, cap=5)
        assert out["changed_paths_absolute_truncated"] is True
        assert len(out["changed_paths_absolute"]) == 5
        # 🔴 The TRUE count survives the truncation — that is what makes the
        # short list readable as short.
        assert out["changed_paths_absolute_total"] == 6

    def test_one_UNDER_the_cap_is_not_truncated(self):
        """The third point. Two points cannot distinguish `>` from `>=` from a
        constant; three can."""
        out = CP.absolute_under([f"{OTHER}/f{i}.py" for i in range(4)], OTHER, cap=5)
        assert out["changed_paths_absolute_truncated"] is False
        assert out["changed_paths_absolute_total"] == 4

    def test_it_dedupes_on_the_ROOT_RELATIVE_form(self):
        r = CP.absolute_under([f"{OTHER}/a.py", f"{OTHER}//a.py", f"{OTHER}/./a.py"], OTHER)
        assert r["changed_paths_absolute"] == ["a.py"]
        assert r["changed_paths_absolute_total"] == 1

    def test_a_malformed_entry_RAISES_whether_or_not_the_root_is_usable(self):
        """The same corpus must not raise or not depending on an unrelated
        argument — that would make the validation a property of the caller."""
        for root in (OTHER, ""):
            with pytest.raises(ValueError, match="not a usable path"):
                CP.absolute_under([f"{OTHER}/a.py", "  "], root)

    def test_a_non_string_root_is_named_rather_than_coerced(self):
        with pytest.raises(ValueError, match="root must be a string"):
            CP.absolute_under([f"{OTHER}/a.py"], None)


class TestAbsoluteWindowAbsentVsEmpty:
    """The discriminator the whole module is built on, applied to the new block:
    [] means "checked, nothing resolved"; None means "we never had a file set"."""

    def test_an_unreadable_rollup_reports_ABSENT(self):
        r = S.build_rollup([], absolute_root=OTHER)
        assert r["changed_paths_absolute"] is None
        assert r["changed_paths_absolute_total"] is None
        assert r["changed_paths_absolute_truncated"] is None

    def test_an_unopenable_file_reports_ABSENT(self, tmp_path):
        r = S.summarize_transcript(str(tmp_path / "nope.jsonl"), absolute_root=OTHER)
        assert r["unreadable"] is True
        assert r["changed_paths_absolute"] is None

    def test_a_garbage_file_reports_ABSENT(self, tmp_path):
        p = tmp_path / "junk.jsonl"
        p.write_text("not json at all\n{{{\n", encoding="utf-8")
        r = S.summarize_transcript(str(p), absolute_root=OTHER)
        assert r["unreadable"] is True
        assert r["changed_paths_absolute"] is None

    def test_a_READABLE_session_with_nothing_under_the_root_reports_EMPTY(self, tmp_path):
        """The other arm. Without it, a change making EVERY rollup report None
        would still pass the three above."""
        p = tmp_path / "s.jsonl"
        p.write_text(
            "\n".join(json.dumps(o) for o in
                      [_user(), _assistant([("Edit", {"file_path": f"{REPO}/a.py"})])]),
            encoding="utf-8")
        r = S.summarize_transcript(str(p), absolute_root=OTHER)
        assert r["unreadable"] is False
        assert r["changed_paths_absolute"] == []


class TestAbsoluteWindowNeverReachesTheEVENT:
    """🔴 THE EMIT PATH IS THE BLAST RADIUS. `build_event` json-dumps the WHOLE
    rollup as the payload, so any key placed on it unconditionally would ship a
    second path list — dominated by agent scratchpad and temp-worktree paths — to
    ClickHouse for every session on both hosts. The block is opt-in for that
    reason, and `run()` never opts in."""

    def test_the_keys_are_ABSENT_when_no_root_is_asked_for(self):
        r = _rollup([("Edit", {"file_path": f"{REPO}/a.py"})])
        for key in CP.ABSOLUTE_KEYS:
            assert key not in r, f"{key} appeared without a caller asking for it"

    def test_the_emitted_payload_carries_none_of_them(self):
        ev = S.build_event("sid", _rollup([("Edit", {"file_path": f"{REPO}/a.py"})]))
        for key in CP.ABSOLUTE_KEYS:
            assert key not in ev["payload"]

    def test_the_block_is_NOT_part_of_the_payload_contract(self):
        """Structural, not a spelling: `PAYLOAD_KEYS` is the set both tailers
        promise to place ALWAYS. An overlap here would make that promise false
        for the opencode side, which has no absolute window at all."""
        assert set(CP.ABSOLUTE_KEYS).isdisjoint(CP.PAYLOAD_KEYS)

    def test_the_tailers_RUN_LOOP_does_not_pass_a_root(self):
        """The pin that makes the two tests above more than a statement about
        today's default. A future `run()` that started passing one would ship the
        paths regardless of the parameter's default.

        ⚠ INVARIANT GUARD, not regression coverage: it is green on the pre-change
        code too, by construction — the parameter did not exist there. It pins a
        property nothing has violated yet, which is the point of it."""
        src = (_CLAUDE_DIR / "session-tailer.py").read_text(encoding="utf-8")
        body = src[src.index("def run("):]
        assert "absolute_root" not in body, (
            "the tailer's run loop now passes absolute_root — that puts a second "
            "path list into every emitted session-summary payload"
        )
        # 🔴 THE LEDGER MUST GROW WITH THE PARAMETER LIST, or this guard's
        # DESCRIPTION ("does not pass a root") stays true of the sentence and
        # false of the code the moment a second root parameter is added — which
        # is exactly what happened when `absolute_extra_roots` arrived.
        assert "absolute_extra_roots" not in body, (
            "the tailer's run loop now passes absolute_extra_roots — same defect, "
            "second parameter"
        )


# --------------------------------------------------------------------------- #
# The MULTI-ROOT absolute window — a repo and its worktrees are ONE namespace
# --------------------------------------------------------------------------- #
#
# 🔴 THE UNION IS SOUND ONLY BECAUSE THE ROOTS SHARE A PATH NAMESPACE. Every
# worktree of one repository has the same tracked layout by construction, so
# `src/a.py` under a worktree and `src/a.py` under the base clone are the SAME
# repo-relative path and deduplicating them is correct. Handed two genuinely
# different projects this would conflate them — which is why the caller derives
# its roots from `git worktree list --porcelain` and never from a naming
# convention. See `subsystem_touch.worktree_roots`.

WT_A = "/srv/checkouts/other-repo-wt-a"
WT_B = "/tmp/wt-topic"


class TestAbsoluteUnderAny:
    def test_THE_PAIR_every_root_contributes_and_a_stranger_root_does_not(self):
        paths = [f"{OTHER}/src/a.py", f"{WT_A}/src/b.py", f"{REPO}/src/never.py"]
        r = CP.absolute_under_any(paths, [OTHER, WT_A])
        assert r["changed_paths_absolute"] == ["src/a.py", "src/b.py"], (
            "a root contributed nothing — the union is wired to one root only"
        )
        # CONTROL: the SAME corpus against roots that hold none of it. A non-zero
        # above is only readable as a measurement next to a zero that is one too.
        c = CP.absolute_under_any(paths, [WT_B])
        assert c["changed_paths_absolute"] == []
        assert c["changed_paths_absolute_total"] == 0

    def test_it_dedupes_the_SAME_repo_relative_path_across_two_roots(self):
        """🔴 THE PROPERTY THAT MAKES THE COUNT NON-ADDITIVE, and the caller's
        reconciliation depends on knowing it: two roots holding the same
        repo-relative path consume TWO entries from the outside-cwd population
        and yield ONE path here."""
        r = CP.absolute_under_any([f"{OTHER}/src/a.py", f"{WT_A}/src/a.py"], [OTHER, WT_A])
        assert r["changed_paths_absolute"] == ["src/a.py"]
        assert r["changed_paths_absolute_total"] == 1

    def test_a_RELATIVE_entry_is_excluded_however_many_roots_are_offered(self):
        """The safety property of the single-root function, held under the
        union: more roots must not become more licence to re-anchor."""
        r = CP.absolute_under_any(["src/a.py", "./b.py"], [OTHER, WT_A, WT_B])
        assert r["changed_paths_absolute"] == []

    def test_no_roots_yields_a_MEASURED_empty_not_None(self):
        """`[]`/`0`/`False` is "checked, found nothing"; None is reserved for
        "the file set was never observed". Spelling them the same way is the
        defect `absolute_unobservable` exists to prevent."""
        r = CP.absolute_under_any([f"{OTHER}/a.py"], [])
        assert r["changed_paths_absolute"] == []
        assert r["changed_paths_absolute_total"] == 0
        assert r["changed_paths_absolute_truncated"] is False

    def test_a_root_whose_OWN_subtree_overflows_the_cap_does_not_truncate_the_union(self):
        """🔴 THE BUG A NAIVE UNION SHIPS. Unioning each root's CAPPED list would
        let a big root's lexicographic prefix silently drop members that the
        union has room for — and the union's own `truncated` flag would read
        False while paths were already lost. Root A alone overflows `cap=5`;
        root B's single path sorts LAST, so a prefix-truncated union loses it."""
        paths = [f"{OTHER}/a{i:04d}.py" for i in range(7)] + [f"{WT_A}/zzz.py"]
        r = CP.absolute_under_any(paths, [OTHER, WT_A], cap=5)
        assert r["changed_paths_absolute_total"] == 8, (
            "the union counted a capped prefix rather than the full set"
        )
        assert r["changed_paths_absolute_truncated"] is True
        assert len(r["changed_paths_absolute"]) == 5

    def test_a_malformed_entry_RAISES_here_too(self):
        with pytest.raises(ValueError, match="not a usable path"):
            CP.absolute_under_any([f"{OTHER}/a.py", "  "], [OTHER])


class TestExtraRootsPlumbing:
    """The parameter has to reach the extractor through BOTH rollup branches, or
    a caller that asked for the block gets it in one outcome and not the other —
    the absent-key/None-key divergence `summarize_transcript` already documents
    for its OSError path."""

    def test_extra_roots_alone_produce_the_block(self):
        blocks = [("Edit", {"file_path": f"{WT_A}/src/a.py"})]
        r = S.build_rollup([_user(), _assistant(blocks)], absolute_extra_roots=(WT_A,))
        assert r["changed_paths_absolute"] == ["src/a.py"]

    def test_the_root_and_the_extra_roots_are_UNIONED_not_overridden(self):
        blocks = [("Edit", {"file_path": p})
                  for p in (f"{OTHER}/src/a.py", f"{WT_A}/src/b.py")]
        r = S.build_rollup(
            [_user(), _assistant(blocks)], absolute_root=OTHER, absolute_extra_roots=(WT_A,)
        )
        assert r["changed_paths_absolute"] == ["src/a.py", "src/b.py"]

    def test_an_UNREADABLE_transcript_returns_None_for_extra_roots_too(self, tmp_path):
        """A `[]` here would say "the roots were checked and nothing resolved" —
        a measurement — about a session whose file set was never read."""
        p = tmp_path / "t.jsonl"
        p.write_text("not json at all\n", encoding="utf-8")
        r = S.summarize_transcript(str(p), absolute_extra_roots=(WT_A,))
        assert r["unreadable"] is True
        for key in CP.ABSOLUTE_KEYS:
            assert r[key] is None, f"{key} was a measurement about an unread session"

    def test_the_DEFAULT_still_emits_no_absolute_block_at_all(self):
        """The parameter is opt-in on BOTH names. A default that started
        emitting would ship a second path list into every telemetry payload.

        ⚠ INVARIANT GUARD, not regression coverage: green on the pre-change code
        too, by construction — the second parameter did not exist there."""
        r = S.build_rollup([_user(), _assistant([("Edit", {"file_path": f"{REPO}/a.py"})])])
        for key in CP.ABSOLUTE_KEYS:
            assert key not in r
