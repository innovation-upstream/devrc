"""Unit tests for the PURE core of scripts/initiatives/route.py.

Offline: no live DB, no live scan run. We feed `rank_matches` fixture initiative
sets + free-text signals and assert on the ranking, the exposed score components,
the confident-vs-likely-new-work classification, repo scoping, and limit.

`rank_matches` reuses the scan's tokenizers (`text_tokens` / `slug_tokens`) by
importing `initiative-scan.py` via importlib — that import runs the scan's
top-level `import chquery` (needs `requests` + `scripts/validation` on sys.path),
which is available in the hermetic pytest sandbox exactly as it is for
`test_initiative_scan.py`. No ClickHouse/git/gh is touched by the pure matcher.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import route  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture initiatives — minimal (slug/repo/title is all the matcher reads).
# --------------------------------------------------------------------------- #
def _ini(slug, title, repo="/repo/devrc"):
    return {"slug": slug, "repo": repo, "title": title}


CLAWGATE = _ini("clawgate-agent-loop", "Clawgate agent loop close")
TEKTON = _ini("tekton-pipeline", "Tekton pipeline migration")
SYSREDIS = _ini("sysredis", "Redis sentinel failover hardening")
AGENT_OPS = _ini("agent-ops-dashboard", "Agent ops dashboard")

# Sibling pair sharing a common prefix (the classic app-blocks ⊂ app-blocks-followups).
APP_BLOCKS = _ini("app-blocks", "App blocks")
APP_BLOCKS_FU = _ini("app-blocks-followups", "App blocks followups")


def _slugs(ranked):
    return [r["slug"] for r in ranked]


def _by_slug(ranked, slug):
    return next(r for r in ranked if r["slug"] == slug)


# --------------------------------------------------------------------------- #
# Exact / strong slug hit
# --------------------------------------------------------------------------- #
def test_exact_slug_hit_is_confident_and_top():
    inis = [CLAWGATE, TEKTON, SYSREDIS]
    ranked = route.rank_matches("harden the clawgate agent loop soak testing", inis)
    assert ranked[0]["slug"] == "clawgate-agent-loop"
    assert ranked[0]["confident"] is True
    assert ranked[0]["slug_overlap"] == 3
    assert route.classify(ranked) == "confident match: clawgate-agent-loop"


def test_matched_tokens_are_the_actual_overlaps():
    ranked = route.rank_matches("clawgate agent loop", [CLAWGATE])
    assert ranked[0]["matched_tokens"] == ["agent", "clawgate", "loop"]


# --------------------------------------------------------------------------- #
# Unique-single-token match (df == 1 gate)
# --------------------------------------------------------------------------- #
def test_unique_single_slug_token_is_confident():
    inis = [CLAWGATE, TEKTON, SYSREDIS]
    ranked = route.rank_matches("some tekton thing", inis)
    top = ranked[0]
    assert top["slug"] == "tekton-pipeline"
    assert top["slug_overlap"] == 1
    assert top["confident"] is True  # unique token → qualifies on a single hit


def test_single_shared_token_is_not_confident():
    # 'agent' is shared by clawgate-agent-loop AND agent-ops-dashboard (df==2), so a
    # lone 'agent' hit must NOT fire a confident match on either.
    inis = [CLAWGATE, AGENT_OPS]
    ranked = route.rank_matches("agent onboarding", inis)
    assert ranked, "both share the token 'agent' → should still be listed"
    assert all(r["confident"] is False for r in ranked)
    assert route.classify(ranked) == "no confident match — likely new work"


# --------------------------------------------------------------------------- #
# Title-only match
# --------------------------------------------------------------------------- #
def test_title_only_two_token_match_is_confident():
    # Signal shares no SLUG token with sysredis, but two TITLE tokens → confident.
    ranked = route.rank_matches("sentinel failover runbook", [SYSREDIS, TEKTON])
    top = ranked[0]
    assert top["slug"] == "sysredis"
    assert top["slug_overlap"] == 0
    assert top["title_overlap"] == 2
    assert top["confident"] is True


# --------------------------------------------------------------------------- #
# No match → likely new work
# --------------------------------------------------------------------------- #
def test_no_overlap_returns_empty_and_new_work():
    inis = [CLAWGATE, TEKTON, SYSREDIS]
    ranked = route.rank_matches("quarterly tax invoice paperwork", inis)
    assert ranked == []
    assert route.classify(ranked) == "no confident match — likely new work"


def test_weak_single_shared_token_classifies_as_new_work():
    ranked = route.rank_matches("agent onboarding", [CLAWGATE, AGENT_OPS])
    assert route.classify(ranked) == "no confident match — likely new work"


# --------------------------------------------------------------------------- #
# Sibling disambiguation
# --------------------------------------------------------------------------- #
def test_siblings_do_not_both_fire_on_one_shared_token():
    inis = [APP_BLOCKS, APP_BLOCKS_FU]
    ranked = route.rank_matches("blocks redesign work", inis)
    # both share 'blocks' so both are listed, but the df gate keeps BOTH non-confident.
    assert set(_slugs(ranked)) == {"app-blocks", "app-blocks-followups"}
    assert all(r["confident"] is False for r in ranked)


def test_unique_sibling_token_fires_only_the_specific_initiative():
    inis = [APP_BLOCKS, APP_BLOCKS_FU]
    ranked = route.rank_matches("followups cleanup", inis)
    assert _slugs(ranked) == ["app-blocks-followups"]  # app-blocks has zero overlap
    assert ranked[0]["confident"] is True


def test_full_sibling_name_prefers_the_more_specific_slug():
    inis = [APP_BLOCKS, APP_BLOCKS_FU]
    ranked = route.rank_matches("app-blocks-followups triage", inis)
    assert ranked[0]["slug"] == "app-blocks-followups"  # 3 slug hits beats 2
    assert ranked[0]["slug_overlap"] == 3
    assert _by_slug(ranked, "app-blocks")["slug_overlap"] == 2


# --------------------------------------------------------------------------- #
# Ranking order
# --------------------------------------------------------------------------- #
def test_confident_ranks_above_weak_non_confident():
    inis = [CLAWGATE, AGENT_OPS]
    ranked = route.rank_matches("clawgate agent loop", inis)
    assert _slugs(ranked) == ["clawgate-agent-loop", "agent-ops-dashboard"]
    assert ranked[0]["confident"] is True
    assert ranked[1]["confident"] is False
    assert ranked[0]["score"] > ranked[1]["score"]


# --------------------------------------------------------------------------- #
# Repo scoping
# --------------------------------------------------------------------------- #
_WIDGET_A = _ini("widget-sync", "Widget sync", repo="/repo/devrc")
_WIDGET_B = _ini("widget-sync-mirror", "Widget sync mirror", repo="/repo/other")


def test_repo_scope_by_full_path():
    inis = [_WIDGET_A, _WIDGET_B]
    ranked = route.rank_matches("widget sync", inis, repo="/repo/devrc")
    assert _slugs(ranked) == ["widget-sync"]


def test_repo_scope_by_basename():
    inis = [_WIDGET_A, _WIDGET_B]
    ranked = route.rank_matches("widget sync", inis, repo="other")
    assert _slugs(ranked) == ["widget-sync-mirror"]


def test_no_repo_scope_considers_all_repos():
    inis = [_WIDGET_A, _WIDGET_B]
    ranked = route.rank_matches("widget sync", inis)
    assert set(_slugs(ranked)) == {"widget-sync", "widget-sync-mirror"}


def test_repo_scope_narrows_df_uniqueness():
    # Both repos have a 'widget' initiative → across the full set 'widget' is df==2 and
    # a lone 'widget' hit is not unique; scoped to one repo it becomes unique (df==1).
    inis = [_WIDGET_A, _WIDGET_B]
    scoped = route.rank_matches("widget", inis, repo="/repo/devrc")
    assert scoped and scoped[0]["confident"] is True  # unique within the scoped set


# --------------------------------------------------------------------------- #
# Limit + empty store
# --------------------------------------------------------------------------- #
def test_limit_caps_the_result_list():
    inis = [APP_BLOCKS, APP_BLOCKS_FU, _ini("app-blocks-legacy", "App blocks legacy")]
    ranked = route.rank_matches("app blocks", inis, limit=2)
    assert len(ranked) == 2


def test_empty_store_returns_empty_and_new_work():
    ranked = route.rank_matches("anything at all", [])
    assert ranked == []
    assert route.classify(ranked) == "no confident match — likely new work"


def test_empty_signal_matches_nothing():
    ranked = route.rank_matches("", [CLAWGATE, TEKTON])
    assert ranked == []
