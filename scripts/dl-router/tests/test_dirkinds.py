"""Directory kinds: the classification file, the draft generator, and the
auto-file gate it drives.

The library is not purely subject-keyed — some directories collect unattributed
material by category — and the two need opposite learning rules and opposite
auto-file rules. Fixtures are synthetic throughout.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dirkinds as dk  # noqa: E402
from matcher import (  # noqa: E402
    KIND_CATEGORY, KIND_PERFORMER, KIND_UNKNOWN, MatchContext, Matcher,
    norm_key,
)


def write(tmp_path, body: str) -> Path:
    path = tmp_path / "dirs.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- loading ---------------------------------------------------------------- #
def test_a_missing_file_leaves_everything_unclassified(tmp_path):
    kinds = dk.DirKinds.load(tmp_path / "nope.toml")
    assert kinds.present is False
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert kinds.error is None


def test_the_two_lists_classify(tmp_path):
    path = write(tmp_path, 'performer = ["Jane Doe"]\ncategory = ["Field Notes"]\n')
    kinds = dk.DirKinds.load(path)
    assert kinds.kind("Jane Doe") == KIND_PERFORMER
    assert kinds.kind("Field Notes") == KIND_CATEGORY
    assert kinds.kind("Someone Else") == KIND_UNKNOWN


@pytest.mark.parametrize("spelling", ["jane-doe", "JANE_DOE", "  Jane Doe  "])
def test_classification_folds_the_naming_conventions(tmp_path, spelling):
    """The same convention folding that means existing directories are never
    renamed: a dirs.toml written in a different case still classifies."""
    path = write(tmp_path, f'performer = ["{spelling}"]\ncategory = []\n')
    assert dk.DirKinds.load(path).kind("Jane Doe") == KIND_PERFORMER


def test_a_directory_in_both_lists_is_ambiguous_and_therefore_unknown(tmp_path):
    """Unknown ASKS. Resolving to whichever list happened to be read last would
    silently pick a rule for a directory the operator was unsure about."""
    path = write(tmp_path,
                 'performer = ["Jane Doe"]\ncategory = ["jane-doe"]\n')
    kinds = dk.DirKinds.load(path)
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert kinds.error and "both lists" in kinds.error


def test_a_malformed_file_degrades_instead_of_raising(tmp_path):
    """This is loaded on the /match path, which has a 400 ms budget before the
    extension gives up. A parse error there must not take matching down."""
    path = write(tmp_path, "this is not = valid toml [[[")
    kinds = dk.DirKinds.load(path)
    assert kinds.error and kinds.kind("Jane Doe") == KIND_UNKNOWN


def test_a_wrongly_typed_list_is_reported_not_obeyed(tmp_path):
    path = write(tmp_path, 'performer = "Jane Doe"\ncategory = []\n')
    kinds = dk.DirKinds.load(path)
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert kinds.error


def test_the_human_file_wins_over_the_picker_overlay(tmp_path):
    """The file is the thing the operator reviewed."""
    path = write(tmp_path, 'performer = ["Jane Doe"]\ncategory = []\n')
    kinds = dk.DirKinds.load(path, overlay={"Jane Doe": KIND_CATEGORY,
                                            "Field Notes": KIND_CATEGORY})
    assert kinds.kind("Jane Doe") == KIND_PERFORMER
    assert kinds.kind("Field Notes") == KIND_CATEGORY


def test_counts_and_unclassified(tmp_path):
    path = write(tmp_path, 'performer = ["Jane Doe"]\ncategory = ["Field Notes"]\n')
    kinds = dk.DirKinds.load(path)
    names = ["Jane Doe", "Field Notes", "Nobody Knows"]
    assert kinds.counts(names) == {KIND_PERFORMER: 1, KIND_CATEGORY: 1,
                                   KIND_UNKNOWN: 1}
    assert kinds.unclassified(names) == ["Nobody Knows"]


# --- the draft generator ---------------------------------------------------- #
DRAFT_DIRS = ["Jane Doe", "john-smith", "Field Notes", "Compilations",
              "Live Sets", "Archive Sets", "Live Archive", "Season 2024",
              "A Very Long Directory Name Indeed"]


def test_the_draft_is_valid_toml_and_covers_every_directory():
    text = dk.draft(DRAFT_DIRS)
    parsed = tomllib.loads(text)
    listed = {norm_key(n) for n in parsed["performer"] + parsed["category"]}
    assert listed == {norm_key(n) for n in DRAFT_DIRS}


def test_the_draft_splits_names_from_categories():
    parsed = tomllib.loads(dk.draft(DRAFT_DIRS))
    assert "Jane Doe" in parsed["performer"]
    assert "john-smith" in parsed["performer"]
    # single word, digits, too many words, and the shared-vocabulary rule:
    # every word of "Live Sets" / "Archive Sets" / "Live Archive" also appears
    # in another directory, which is what a taxonomy looks like and what a
    # person's name does not.
    for name in ("Compilations", "Season 2024",
                 "A Very Long Directory Name Indeed", "Live Sets",
                 "Archive Sets", "Live Archive"):
        assert name in parsed["category"], name


def test_every_draft_line_carries_the_reason_it_landed_there():
    """The review action is "does that reason hold?", not "what even is this?"."""
    text = dk.draft(DRAFT_DIRS)
    for line in text.splitlines():
        if line.strip().startswith('"'):
            assert "#" in line, line


def test_the_draft_preserves_an_existing_classification(tmp_path):
    """Re-running the generator after a partial review must not undo it."""
    path = write(tmp_path, 'performer = ["Compilations"]\ncategory = []\n')
    parsed = tomllib.loads(
        dk.draft(DRAFT_DIRS, known=dk.DirKinds.load(path)))
    assert "Compilations" in parsed["performer"]


def test_a_directory_name_with_toml_metacharacters_survives_the_round_trip():
    tricky = ['He said "hi"', "back\\slash", "tab\there"]
    parsed = tomllib.loads(dk.draft(tricky))
    assert set(parsed["performer"] + parsed["category"]) == set(tricky)


def test_an_empty_library_still_produces_a_usable_file():
    parsed = tomllib.loads(dk.draft([]))
    assert parsed == {"performer": [], "category": []}


# --- the auto-file gate ----------------------------------------------------- #
def alias_matcher(kinds):
    return Matcher(["Jane Doe", "Field Notes", "other"],
                   {("janedoe", "site.test"): "Jane Doe",
                    ("fieldnotes", "site.test"): "Field Notes"},
                   dir_kinds=kinds)


def test_a_performer_directory_auto_files():
    res = alias_matcher({"Jane Doe": KIND_PERFORMER}).match(
        MatchContext(tags=("Jane Doe",), site="site.test"))
    assert res.dir == "Jane Doe" and res.auto is True


def test_a_category_directory_always_asks_however_high_it_scores():
    """A tag legitimately identifies a category directory, but a tag is a weak
    claim about any ONE file — so it is confirmed every time."""
    res = alias_matcher({"Field Notes": KIND_CATEGORY}).match(
        MatchContext(tags=("Field Notes",), site="site.test"))
    assert res.dir == "Field Notes"
    assert res.confidence == pytest.approx(1.0)
    assert res.auto is False
    assert "category" in res.reason


def test_an_unclassified_directory_never_auto_files():
    """Absence of a classification is not permission."""
    res = alias_matcher({}).match(
        MatchContext(tags=("Jane Doe",), site="site.test"))
    assert res.dir == "Jane Doe"
    assert res.auto is False
    assert "unclassified" in res.reason
    # ...and it says what to do about it.
    assert "classify" in res.reason


def test_the_gate_cannot_be_routed_around_by_the_host_prior():
    res = alias_matcher({}).match(
        MatchContext(tags=("Jane Doe",), site="site.test"),
        host_prior="Jane Doe")
    assert res.auto is False
