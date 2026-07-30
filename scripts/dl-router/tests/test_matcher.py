"""Matcher: every scoring rule, normalisation across all three naming
conventions, threshold boundaries, tie handling, and the short-token guard.

Fixtures are synthetic throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import SAMPLE_DIRS  # noqa: E402
from matcher import (  # noqa: E402
    HOST_PRIOR_BONUS, SCORE_ALIAS_GLOBAL, SCORE_ALIAS_SITE, SCORE_CONTAIN_MAX,
    SCORE_CONTAIN_MIN, SCORE_FILENAME_MAX, SCORE_TAG_EXACT, DirEntry,
    MatchContext, Matcher, content_tokens, find_duplicate, norm_key,
    passes_fuzzy_guard, title_case, tokens,
)


def make(dirs=None, aliases=None, **kw) -> Matcher:
    return Matcher(dirs if dirs is not None else SAMPLE_DIRS, aliases or {}, **kw)


# --- normalisation --------------------------------------------------------- #
@pytest.mark.parametrize("variant", [
    "Jane Doe", "jane-doe", "Jane_Doe", "jane.doe", "JANE  DOE", "  Jane Doe  ",
    "jane–doe",          # en dash
])
def test_norm_key_folds_all_naming_conventions(variant):
    assert norm_key(variant) == "janedoe"


def test_norm_key_strips_diacritics_and_compatibility_forms():
    assert norm_key("José") == norm_key("Jose") == "jose"
    assert norm_key("José") == "jose"          # NFD input
    assert norm_key("Ｊａｎｅ") == "jane"   # fullwidth


def test_norm_key_rejects_non_strings():
    assert norm_key(None) == ""
    assert norm_key(42) == ""


def test_tokens_and_content_tokens():
    assert tokens("Jane_Doe - 1080p") == ("jane", "doe", "1080p")
    # content_tokens drops resolution noise and stopwords
    assert content_tokens("Jane_Doe - 1080p video") == ("jane", "doe")


def test_title_case_only_touches_the_first_letter():
    assert title_case("jane doe") == "Jane Doe"
    assert title_case("McBride  vale") == "McBride Vale"
    assert title_case("o'neal") == "O'neal"


# --- individual scoring rules ---------------------------------------------- #
def test_site_scoped_alias_scores_highest():
    m = make(aliases={(norm_key("JD"), "example-site.test"): "Jane Doe"})
    ctx = MatchContext(tags=("JD",), site="example-site.test")
    res = m.match(ctx)
    assert res.dir == "Jane Doe"
    assert res.confidence == pytest.approx(SCORE_ALIAS_SITE)
    assert res.auto is True
    assert "alias(site:example-site.test)" in res.reason


def test_global_alias_scores_below_site_alias():
    m = make(aliases={(norm_key("JD"), ""): "Jane Doe"})
    res = m.match(MatchContext(tags=("JD",), site="other-site.test"))
    assert res.dir == "Jane Doe"
    assert res.confidence == pytest.approx(SCORE_ALIAS_GLOBAL)


def test_site_alias_wins_over_global_for_the_same_key():
    key = norm_key("JD")
    m = make(aliases={(key, ""): "john-smith",
                      (key, "example-site.test"): "Jane Doe"})
    res = m.match(MatchContext(tags=("JD",), site="example-site.test"))
    assert res.dir == "Jane Doe"


def test_alias_pointing_at_a_missing_directory_is_ignored():
    m = make(aliases={(norm_key("ghost"), ""): "No Such Dir"})
    res = m.match(MatchContext(tags=("ghost",)))
    assert res.dir == "other"
    assert res.candidates == []


@pytest.mark.parametrize("tag,expected", [
    ("Jane Doe", "Jane Doe"),
    ("jane_doe", "Jane Doe"),
    ("JOHN SMITH", "john-smith"),
    ("mary major", "Mary_Major"),
])
def test_exact_tag_match_across_conventions(tag, expected):
    res = make().match(MatchContext(tags=(tag,)))
    assert res.dir == expected
    assert res.confidence == pytest.approx(SCORE_TAG_EXACT)
    assert res.auto is True


def test_token_containment_scales_with_coverage():
    m = make()
    # dir tokens (jane, doe) inside a 4-content-token title ("and" is a
    # stopword) -> coverage 2/4 = 0.5
    res = m.match(MatchContext(title="Jane Doe and Aster Vale"))
    expected = SCORE_CONTAIN_MIN + (SCORE_CONTAIN_MAX - SCORE_CONTAIN_MIN) * 0.5
    top = [c for c in res.candidates if c.dir == "Jane Doe"][0]
    assert top.base == pytest.approx(expected)
    assert SCORE_CONTAIN_MIN <= top.base <= SCORE_CONTAIN_MAX


def test_containment_full_coverage_reaches_the_upper_bound():
    m = make()
    res = m.match(MatchContext(link_text="jane doe"))
    top = [c for c in res.candidates if c.dir == "Jane Doe"][0]
    # A phrase identical to the dir also hits the exact rule, which is higher.
    assert top.base == pytest.approx(SCORE_TAG_EXACT)


def test_filename_rule_is_capped_and_never_auto_files_alone():
    m = make(threshold=0.75)
    res = m.match(MatchContext(filename="jane_doe_scene_02.mp4"))
    assert res.dir == "Jane Doe"
    assert res.confidence <= SCORE_FILENAME_MAX
    assert res.auto is False, "filename evidence alone must go to the picker"


def test_opaque_filename_yields_nothing():
    res = make().match(MatchContext(filename="0hv9783sdgne5ur3xh53n_source.mp4"))
    assert res.dir == "other"
    assert res.confidence == 0.0
    assert res.candidates == []


# --- host prior ------------------------------------------------------------ #
def test_host_prior_adds_a_bonus_to_an_existing_candidate():
    m = make()
    res = m.match(MatchContext(filename="jane_doe.mp4"), host_prior="Jane Doe")
    top = res.candidates[0]
    assert top.dir == "Jane Doe"
    assert top.score == pytest.approx(top.base + HOST_PRIOR_BONUS)
    assert "+host-prior" in top.reason


def test_host_prior_never_invents_a_candidate():
    res = make().match(MatchContext(filename="opaque0987.mp4"),
                       host_prior="Jane Doe")
    assert res.candidates == []
    assert res.dir == "other"


def test_host_prior_is_never_decisive_for_auto_filing():
    """A base score just under the threshold must not be pushed over it."""
    m = make(threshold=0.75)
    # containment with coverage 2/3 -> 0.60 + 0.20*0.667 = 0.7333 (< 0.75)
    ctx = MatchContext(title="Jane Doe tonight")
    res = m.match(ctx, host_prior="Jane Doe")
    top = res.candidates[0]
    assert top.base < 0.75 <= top.base + HOST_PRIOR_BONUS
    assert res.auto is False


# --- guards, thresholds, ties ---------------------------------------------- #
def test_fuzzy_guard_rejects_a_single_short_token():
    assert passes_fuzzy_guard(("abc",)) is False
    assert passes_fuzzy_guard(("abcd",)) is True
    assert passes_fuzzy_guard(("ab", "cd")) is True
    assert passes_fuzzy_guard(()) is False


def test_short_directory_name_does_not_match_random_prose():
    m = make(dirs=["Cat", "Jane Doe", "other"])
    res = m.match(MatchContext(title="the cat sat on the mat in a video"))
    assert res.dir == "other", res.candidates


def test_four_char_directory_name_may_match_on_its_own():
    m = make(dirs=["Vale", "other"])
    res = m.match(MatchContext(title="an evening with Vale downtown"))
    assert res.dir == "Vale"


@pytest.mark.parametrize("threshold,auto", [(0.84, True), (0.85, True),
                                            (0.86, False)])
def test_threshold_boundary_is_inclusive(threshold, auto):
    m = make(threshold=threshold)
    res = m.match(MatchContext(tags=("Jane Doe",)))
    assert res.auto is auto


def test_tie_within_margin_falls_back_to_the_picker():
    # Two directories that fold to different keys but both match the title with
    # identical coverage.
    m = make(dirs=["Jane Doe", "Aster Vale", "other"], threshold=0.5,
             tie_margin=0.05)
    res = m.match(MatchContext(title="Jane Doe and Aster Vale"))
    assert res.auto is False
    assert res.reason.startswith("tie:")
    assert len(res.candidates) >= 2


def test_clear_winner_outside_the_tie_margin_auto_files():
    m = make(aliases={(norm_key("Jane Doe"), ""): "Jane Doe"}, threshold=0.5)
    res = m.match(MatchContext(tags=("Jane Doe",), title="Jane Doe"))
    assert res.auto is True
    assert res.dir == "Jane Doe"


def test_reason_is_always_populated():
    for ctx in [MatchContext(tags=("Jane Doe",)),
                MatchContext(filename="whatever.mp4"),
                MatchContext()]:
        assert make().match(ctx).reason


def test_candidate_list_is_bounded_and_ordered():
    dirs = [f"Subject {i:02d}" for i in range(30)] + ["other"]
    m = make(dirs=dirs)
    res = m.match(MatchContext(title=" ".join(dirs[:12])))
    assert len(res.candidates) <= 8
    scores = [c.score for c in res.candidates]
    assert scores == sorted(scores, reverse=True)


# --- new-directory proposal ------------------------------------------------ #
def test_proposes_a_title_case_new_directory_for_an_unknown_subject():
    res = make().match(MatchContext(tags=("aster nightingale",)))
    assert res.suggest_new == "Aster Nightingale"


def test_no_proposal_when_the_subject_already_exists():
    res = make().match(MatchContext(tags=("jane-doe",)))
    assert res.suggest_new is None


def test_no_proposal_for_a_short_or_unsafe_phrase():
    assert make().match(MatchContext(tags=("ab",))).suggest_new is None
    assert make().match(MatchContext(tags=("a/b",))).suggest_new is None


# --- context assembly ------------------------------------------------------ #
def test_from_payload_extracts_site_from_the_page_url():
    ctx = MatchContext.from_payload({
        "filename": "x.mp4",
        "page": {"url": "https://example-site.test/v/1", "tags": ["Jane Doe"]},
    })
    assert ctx.site == "example-site.test"
    assert ctx.tags == ("Jane Doe",)


def test_from_payload_tolerates_hostile_shapes():
    ctx = MatchContext.from_payload({
        "page": {"tags": "not-a-list", "og": ["not", "a", "dict"], "title": 42},
        "size": "nope",
    })
    assert ctx.tags == ()
    assert ctx.og == {}
    assert ctx.size == 0


def test_subject_phrases_are_deduplicated_by_key_and_ordered():
    ctx = MatchContext(tags=("Jane Doe", "jane-doe"), title="Jane Doe",
                       link_text="Aster Vale")
    phrases = ctx.subject_phrases()
    assert phrases[0] == "Jane Doe"
    assert "Aster Vale" in phrases
    assert sum(1 for p in phrases if norm_key(p) == "janedoe") == 1


# --- dedupe ---------------------------------------------------------------- #
class _StubIndex:
    def __init__(self, mapping):
        self.mapping = mapping

    def by_name_key(self, key):
        return self.mapping.get(key, [])


def test_find_duplicate_prefers_a_hit_inside_the_target_directory():
    idx = _StubIndex({norm_key("clip01"): [("john-smith/clip01.mp4", 10),
                                           ("Jane Doe/clip01.mp4", 20)]})
    dup = find_duplicate(idx, "Jane Doe", "clip01.mp4", size=20)
    assert dup["where"] == "target-dir"
    assert dup["relpath"] == "Jane Doe/clip01.mp4"
    assert dup["kind"] == "name+size"


def test_find_duplicate_reports_a_library_wide_hit():
    idx = _StubIndex({norm_key("clip01"): [("john-smith/clip01.mp4", 10)]})
    dup = find_duplicate(idx, "Jane Doe", "clip01.mp4", size=99)
    assert dup["where"] == "library"
    assert dup["kind"] == "name"


def test_find_duplicate_returns_none_without_an_index_or_a_hit():
    assert find_duplicate(None, "Jane Doe", "x.mp4") is None
    assert find_duplicate(_StubIndex({}), "Jane Doe", "x.mp4") is None


# --- misc ------------------------------------------------------------------ #
def test_dir_entry_precomputes_key_and_tokens():
    entry = DirEntry.of("Mary_Major")
    assert entry.key == "marymajor"
    assert entry.tokens == ("mary", "major")


def test_duplicate_keys_resolve_deterministically():
    m = make(dirs=["Jane Doe", "jane-doe", "other"])
    first = m.match(MatchContext(tags=("jane doe",))).dir
    for _ in range(5):
        assert make(dirs=["Jane Doe", "jane-doe", "other"]).match(
            MatchContext(tags=("jane doe",))).dir == first


def test_as_dict_shape_matches_the_sidecar_contract():
    out = make().match(MatchContext(tags=("Jane Doe",))).as_dict(ttl_ms=5000)
    assert set(out) == {"dir", "confidence", "reason", "auto", "candidates",
                        "suggestNew", "dup", "ttlMs"}
    assert out["ttlMs"] == 5000
