"""Tests for scripts/collector/changed_paths.py — the shared `changed_paths*` block.

Run: python -m pytest scripts/collector/tests/test_changed_paths.py -v

🔴 WHAT THESE TESTS ARE FOR. The defect this module exists to prevent is a
SILENT SHORT LIST: a `changed_paths` that a consumer reads as complete when it
is truncated, or as "nothing was touched" when it is really "we could not look".
So the assertions here are about the DISCRIMINATORS (`changed_paths_total`,
`changed_paths_truncated`, `changed_paths_outside_cwd`, None-vs-[]) at least as
much as about the list itself — an assertion on the list alone would pass in
every one of the failure modes.

No path in this file is real. Every fixture uses invented repo/dir names.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import changed_paths as CP  # noqa: E402


CWD = "/srv/checkouts/widget-repo"


# --------------------------------------------------------------------------- #
# to_repo_relative
# --------------------------------------------------------------------------- #
class TestToRepoRelative:
    def test_absolute_under_cwd_is_stripped(self):
        assert CP.to_repo_relative(f"{CWD}/src/app.py", CWD) == "src/app.py"

    def test_nested_absolute_under_cwd(self):
        assert CP.to_repo_relative(f"{CWD}/a/b/c/d.txt", CWD) == "a/b/c/d.txt"

    def test_trailing_slash_on_cwd_does_not_break_the_prefix(self):
        assert CP.to_repo_relative(f"{CWD}/src/app.py", CWD + "/") == "src/app.py"

    def test_redundant_separators_are_normalized(self):
        assert CP.to_repo_relative(f"{CWD}//src/./app.py", CWD) == "src/app.py"

    def test_absolute_outside_cwd_has_no_repo_relative_form(self):
        assert CP.to_repo_relative("/var/tmp/scratch/notes.md", CWD) is None

    def test_sibling_directory_sharing_a_prefix_is_not_under_cwd(self):
        # `/srv/checkouts/widget-repo-2` starts with the cwd STRING but is a
        # different directory. A naive `startswith(cwd)` would relativize it to
        # "-2/x.py" — a component that matches nothing and a path that lies.
        assert CP.to_repo_relative("/srv/checkouts/widget-repo-2/x.py", CWD) is None

    def test_the_cwd_itself_is_not_a_changed_file(self):
        assert CP.to_repo_relative(CWD, CWD) is None

    def test_absolute_path_with_empty_cwd_has_no_repo_relative_form(self):
        assert CP.to_repo_relative("/srv/checkouts/widget-repo/a.py", "") is None

    def test_already_relative_path_is_kept(self):
        assert CP.to_repo_relative("src/app.py", CWD) == "src/app.py"

    def test_relative_path_is_kept_even_with_no_cwd(self):
        assert CP.to_repo_relative("src/app.py", "") == "src/app.py"

    def test_interior_dotdot_is_collapsed_not_rejected(self):
        assert CP.to_repo_relative("src/x/../app.py", CWD) == "src/app.py"

    def test_leading_dotdot_escapes_the_root_and_is_rejected(self):
        # subsystem_resolver.InvalidPathError rejects `..`; emitting one would
        # make the whole association call raise.
        assert CP.to_repo_relative("../other/app.py", CWD) is None

    def test_dotdot_that_only_escapes_after_collapsing_is_rejected(self):
        assert CP.to_repo_relative("a/../../app.py", CWD) is None

    def test_bare_dot_is_not_a_path(self):
        assert CP.to_repo_relative(".", CWD) is None


# --------------------------------------------------------------------------- #
# Negative controls — each guard has its OWN sentinel and its OWN reachable input
# --------------------------------------------------------------------------- #
class TestGuards:
    """Each guard is reached by an input NO EARLIER GUARD rejects, and each is
    asserted on its own sentinel phrase — a test that only asserted "something
    raised" would stay green when a neighbouring guard fires instead."""

    def test_cap_zero_is_rejected_by_the_cap_guard(self):
        with pytest.raises(ValueError, match="changed-paths cap must be an int >= 1"):
            CP.summarize([f"{CWD}/a.py"], CWD, cap=0)

    def test_negative_cap_is_rejected_by_the_cap_guard(self):
        with pytest.raises(ValueError, match="changed-paths cap must be an int >= 1"):
            CP.summarize([], CWD, cap=-3)

    def test_bool_cap_is_rejected_although_bool_is_an_int(self):
        # `True` is an int and `True >= 1`, so a naive check accepts it and caps
        # the list at ONE path — a silent short list, which is the exact defect.
        with pytest.raises(ValueError, match="changed-paths cap must be an int >= 1"):
            CP.summarize([f"{CWD}/a.py", f"{CWD}/b.py"], CWD, cap=True)

    def test_non_int_cap_is_rejected_by_the_cap_guard(self):
        with pytest.raises(ValueError, match="changed-paths cap must be an int >= 1"):
            CP.summarize([], CWD, cap="256")

    def test_unobservable_also_validates_its_cap(self):
        with pytest.raises(ValueError, match="changed-paths cap must be an int >= 1"):
            CP.unobservable(cap=0)

    def test_non_string_cwd_is_rejected_by_the_cwd_guard(self):
        # Reachable: the cap is valid, so the cap guard cannot fire here.
        with pytest.raises(ValueError, match="changed-paths cwd must be a string"):
            CP.summarize([f"{CWD}/a.py"], None)

    def test_path_object_cwd_is_rejected_by_the_cwd_guard(self):
        with pytest.raises(ValueError, match="changed-paths cwd must be a string"):
            CP.summarize([], Path(CWD))

    def test_none_path_is_rejected_by_the_path_guard(self):
        # Reachable: cap and cwd are both valid, so neither earlier guard fires.
        with pytest.raises(ValueError, match="changed-paths entry is not a usable path"):
            CP.summarize([f"{CWD}/a.py", None], CWD)

    def test_empty_string_path_is_rejected_by_the_path_guard(self):
        with pytest.raises(ValueError, match="changed-paths entry is not a usable path"):
            CP.summarize([""], CWD)

    def test_whitespace_only_path_is_rejected_by_the_path_guard(self):
        with pytest.raises(ValueError, match="changed-paths entry is not a usable path"):
            CP.summarize(["   "], CWD)

    def test_the_three_guards_have_three_DISTINCT_sentinels(self):
        """A shared message would make every mutation test above green for the
        wrong reason: any guard firing would satisfy any other guard's match."""
        msgs = []
        for call in (
            lambda: CP.summarize([], CWD, cap=0),
            lambda: CP.summarize([], None),
            lambda: CP.summarize([None], CWD),
        ):
            with pytest.raises(ValueError) as exc:
                call()
            msgs.append(str(exc.value))
        assert len(set(msgs)) == 3, msgs


# --------------------------------------------------------------------------- #
# summarize — the ordinary path
# --------------------------------------------------------------------------- #
class TestSummarize:
    def test_positive_control_a_non_empty_set_produces_a_non_empty_list(self):
        """POSITIVE CONTROL. A reassuring `[]` is indistinguishable from an
        extractor wired to nothing, so the count must be shown to MOVE."""
        empty = CP.summarize([], CWD)
        assert empty["changed_paths"] == [] and empty["changed_paths_total"] == 0
        loaded = CP.summarize([f"{CWD}/src/app.py", f"{CWD}/README.md"], CWD)
        assert loaded["changed_paths_total"] == 2
        assert loaded["changed_paths"] == ["README.md", "src/app.py"]

    def test_empty_input_is_an_empty_list_not_None(self):
        """[] and None mean opposite things downstream — see the module docstring."""
        out = CP.summarize([], CWD)
        assert out["changed_paths"] == []
        assert out["changed_paths"] is not None
        assert out["changed_paths_truncated"] is False
        assert out["changed_paths_outside_cwd"] == 0

    def test_duplicates_collapse_on_the_RELATIVE_form(self):
        out = CP.summarize(
            [f"{CWD}/src/app.py", f"{CWD}//src/./app.py", "src/app.py"], CWD)
        assert out["changed_paths"] == ["src/app.py"]
        assert out["changed_paths_total"] == 1

    def test_ordering_is_deterministic_regardless_of_input_order(self):
        a = CP.summarize([f"{CWD}/z.py", f"{CWD}/a.py", f"{CWD}/m.py"], CWD)
        b = CP.summarize([f"{CWD}/m.py", f"{CWD}/z.py", f"{CWD}/a.py"], CWD)
        assert a["changed_paths"] == b["changed_paths"] == ["a.py", "m.py", "z.py"]

    def test_outside_cwd_paths_are_counted_not_dropped(self):
        out = CP.summarize(
            [f"{CWD}/src/app.py", "/var/tmp/scratch/a.md", "/var/tmp/scratch/b.md"],
            CWD)
        assert out["changed_paths"] == ["src/app.py"]
        assert out["changed_paths_total"] == 1
        assert out["changed_paths_outside_cwd"] == 2

    def test_outside_cwd_count_dedupes_too(self):
        out = CP.summarize(["/var/tmp/x.md", "/var/tmp/x.md"], CWD)
        assert out["changed_paths_outside_cwd"] == 1

    def test_no_emitted_path_is_absolute_or_escaping(self):
        """The consumer (subsystem_resolver._validate_path) RAISES on either, so
        this is a contract check, not a style check."""
        out = CP.summarize(
            [f"{CWD}/a.py", "/elsewhere/b.py", "../c.py", "d/../e.py"], CWD)
        for p in out["changed_paths"]:
            assert not p.startswith("/")
            assert ".." not in p.split("/")

    def test_cap_is_reported_so_a_reader_knows_the_bound(self):
        assert CP.summarize([], CWD)["changed_paths_cap"] == CP.CHANGED_PATHS_CAP

    def test_every_payload_key_is_always_present(self):
        for block in (CP.summarize([f"{CWD}/a.py"], CWD), CP.unobservable()):
            assert set(CP.PAYLOAD_KEYS) <= set(block)


# --------------------------------------------------------------------------- #
# The truncation branch
# --------------------------------------------------------------------------- #
class TestTruncation:
    def test_exactly_at_the_cap_is_NOT_truncated(self):
        paths = [f"{CWD}/f{i:04d}.py" for i in range(5)]
        out = CP.summarize(paths, CWD, cap=5)
        assert out["changed_paths_truncated"] is False
        assert len(out["changed_paths"]) == 5
        assert out["changed_paths_total"] == 5

    def test_one_over_the_cap_IS_truncated_and_says_so(self):
        paths = [f"{CWD}/f{i:04d}.py" for i in range(6)]
        out = CP.summarize(paths, CWD, cap=5)
        assert out["changed_paths_truncated"] is True
        assert len(out["changed_paths"]) == 5
        # 🔴 The whole point: the TRUE count survives the truncation, so a
        # consumer cannot read the short list as complete.
        assert out["changed_paths_total"] == 6

    def test_truncation_keeps_a_deterministic_prefix(self):
        paths = [f"{CWD}/f{i:04d}.py" for i in range(20)]
        out_a = CP.summarize(paths, CWD, cap=3)
        out_b = CP.summarize(list(reversed(paths)), CWD, cap=3)
        assert out_a["changed_paths"] == out_b["changed_paths"]
        assert out_a["changed_paths"] == ["f0000.py", "f0001.py", "f0002.py"]

    def test_truncation_counts_the_distinct_set_not_the_input_length(self):
        paths = [f"{CWD}/a.py"] * 50
        out = CP.summarize(paths, CWD, cap=5)
        assert out["changed_paths_total"] == 1
        assert out["changed_paths_truncated"] is False

    def test_outside_cwd_paths_do_not_count_toward_the_cap(self):
        paths = [f"{CWD}/a.py"] + [f"/var/tmp/x{i}.py" for i in range(50)]
        out = CP.summarize(paths, CWD, cap=2)
        assert out["changed_paths_truncated"] is False
        assert out["changed_paths_total"] == 1
        assert out["changed_paths_outside_cwd"] == 50

    def test_default_cap_clears_the_measured_corpus_maximum(self):
        """The cap is a BACKSTOP, not a routine truncation. The largest session
        in the live store (all-time, deduped per session) modified 93 distinct
        files; the opencode store's largest was 46. A cap at or below either
        would truncate real sessions rather than pathological ones."""
        assert CP.CHANGED_PATHS_CAP >= 2 * 93


# --------------------------------------------------------------------------- #
# unobservable
# --------------------------------------------------------------------------- #
class TestUnobservable:
    def test_every_data_field_is_None(self):
        out = CP.unobservable()
        assert out["changed_paths"] is None
        assert out["changed_paths_total"] is None
        assert out["changed_paths_truncated"] is None
        assert out["changed_paths_outside_cwd"] is None

    def test_the_cap_is_still_reported(self):
        # The cap is a fact about the CODE, not a reading of the session, so it
        # survives an unobservable verdict.
        assert CP.unobservable()["changed_paths_cap"] == CP.CHANGED_PATHS_CAP

    def test_unobservable_is_distinguishable_from_an_empty_reading(self):
        """The one assertion the whole design rests on: `if not paths:` must NOT
        be able to conflate them."""
        assert CP.unobservable()["changed_paths"] is None
        assert CP.summarize([], CWD)["changed_paths"] == []
        assert CP.unobservable()["changed_paths"] != CP.summarize([], CWD)["changed_paths"]
