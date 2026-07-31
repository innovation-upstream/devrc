"""Directory kinds: the classification file, the draft generator, and the
auto-file gate it drives.

The library is not purely subject-keyed — some directories collect unattributed
material by category — and the two need opposite learning rules and opposite
auto-file rules. Fixtures are synthetic throughout.
"""
from __future__ import annotations

import pathlib
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
    assert kinds.error and "more than one list" in kinds.error


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


def all_listed(parsed):
    return parsed["performer"] + parsed["category"] + parsed[dk.ASK_LIST]


def test_the_draft_is_valid_toml_and_covers_every_directory():
    parsed = tomllib.loads(dk.draft(DRAFT_DIRS))
    assert {norm_key(n) for n in all_listed(parsed)} \
        == {norm_key(n) for n in DRAFT_DIRS}


def test_the_draft_never_guesses_onto_the_auto_filing_side():
    """The draft must never silently authorise auto-filing.

    Nothing available to a generator distinguishes "Ada Lovelace" from "Field
    Recordings" -- two capitalised words either way -- so resolving that
    ambiguity towards `performer` meant a skimmed review turned category
    directories into auto-filers. (The module's own docstring example, "Field
    Recordings", drafted as a performer.)
    """
    parsed = tomllib.loads(dk.draft(DRAFT_DIRS))
    assert parsed["performer"] == []


def test_the_ambiguous_ones_go_to_a_list_that_is_NOT_a_kind():
    """`ask` is ignored by the loader, so everything in it is unclassified and
    therefore asks. Folding them into `category` instead was safe, but it threw
    away the labour saving for a library that is mostly people."""
    parsed = tomllib.loads(dk.draft(DRAFT_DIRS))
    assert "Jane Doe" in parsed[dk.ASK_LIST]
    assert "john-smith" in parsed[dk.ASK_LIST]
    # ...and it is not a kind at all.
    assert dk.ASK_LIST not in dk.KINDS


def test_the_ask_list_really_does_read_as_unclassified(tmp_path):
    path = write(tmp_path, 'performer = []\ncategory = []\nask = ["Jane Doe"]\n')
    kinds = dk.DirKinds.load(path)
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert not kinds.error


def test_an_unknown_list_name_is_reported(tmp_path):
    path = write(tmp_path, 'performer = []\ncategory = []\nperfomer = []\n')
    assert "unknown list" in (dk.DirKinds.load(path).error or "")


def test_the_draft_still_sorts_what_it_can_prove():
    parsed = tomllib.loads(dk.draft(DRAFT_DIRS))
    for name in ("Compilations", "Season 2024",
                 "A Very Long Directory Name Indeed", "Live Sets",
                 "Archive Sets", "Live Archive"):
        assert name in parsed["category"], name


def test_the_draft_still_explains_the_unambiguous_ones(): 
    text = dk.draft(DRAFT_DIRS)
    reasons = {l.split("#", 1)[1].strip() for l in text.splitlines()
               if l.strip().startswith('"')}
    assert "a single word" in reasons
    assert "contains digits" in reasons
    assert "every word is shared with other directories" in reasons


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
    assert set(all_listed(tomllib.loads(dk.draft(tricky)))) == set(tricky)


def test_an_empty_library_still_produces_a_usable_file():
    assert tomllib.loads(dk.draft([])) == {"performer": [], "category": [],
                                           dk.ASK_LIST: []}


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


def test_a_malformed_file_drops_the_picker_overlay_too(tmp_path):
    """FAIL CLOSED, completely. `merged` used to be seeded from the SQLite
    picker overlay BEFORE parsing, so picker-assigned `performer` kinds
    survived a broken dirs.toml and kept auto-filing while the operator
    believed their edit had disabled it. The overlay is machine state and
    syntactically fine, but the file is the authority over it, and "I cannot
    read the authority" is not a state in which to keep auto-filing."""
    path = write(tmp_path, "this is not = valid toml [[[")
    kinds = dk.DirKinds.load(path, overlay={"Jane Doe": KIND_PERFORMER})
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert kinds.error


def test_a_name_in_both_ask_and_performer_is_ambiguous(tmp_path):
    """A half-finished review — the line copied to `ask` but the original left
    in `performer` — used to resolve to `performer` with no error, so the very
    directory the operator had just parked as undecided carried on auto-filing.
    Ambiguity resolves to unknown, which is this module's rule everywhere else.
    """
    path = write(tmp_path,
                 'performer = ["Jane Doe"]\ncategory = []\nask = ["Jane Doe"]\n')
    kinds = dk.DirKinds.load(path)
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert kinds.error


def test_a_clean_ask_entry_is_not_an_error(tmp_path):
    path = write(tmp_path, 'performer = []\ncategory = []\nask = ["Jane Doe"]\n')
    kinds = dk.DirKinds.load(path)
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert not kinds.error


def test_an_ask_entry_overrides_a_picker_assigned_kind(tmp_path):
    """The reviewed file is the authority over the machine overlay, and `ask`
    is a decision too: parking a directory there has to be able to UNDO a kind
    the picker assigned, or a reclassification could never take an auto-filing
    directory back out of service."""
    path = write(tmp_path, 'performer = []\ncategory = []\nask = ["Jane Doe"]\n')
    kinds = dk.DirKinds.load(path, overlay={"Jane Doe": KIND_PERFORMER})
    assert kinds.kind("Jane Doe") == KIND_UNKNOWN
    assert not kinds.error
