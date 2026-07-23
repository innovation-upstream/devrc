"""Tests for the SURFACE-ONLY initiative tagging (scripts/repo-cos/routing.py) and
its digest rendering.

Offline: no live DB, no live scan. `routing.tag_proposals` is PURE — we feed it
fixture initiative sets + Proposal objects and assert the tag. The best-effort
`related_for` is exercised by injecting `route.load_current` (the ONLY I/O) so we
never touch Postgres; a store/tagging failure is asserted to be swallowed.

`routing` loads the router (`scripts/initiatives/route.py`) by importlib, whose pure
matcher reuses the scan's tokenizers — the same import path proven hermetic by
`scripts/initiatives/tests/test_route.py` and `test_initiative_scan.py`.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import digest  # noqa: E402
import llm  # noqa: E402
import routing  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _prop(title="Fix", *, repo="devrc", why="better CI", approach="do it", ci=True):
    return llm.Proposal(
        title=title, repo=repo, evidence=[f"{repo}/a.py:1"], why=why,
        effort="S", approach=approach, ci_verifiable=ci,
    )


def _ini(slug, title, repo="/home/zach/workspace/devrc"):
    return {"slug": slug, "repo": repo, "title": title}


CLAWGATE = _ini("clawgate-agent-loop", "Clawgate agent loop close")
TEKTON = _ini("tekton-pipeline", "Tekton pipeline migration")
AGENT_OPS = _ini("agent-ops-dashboard", "Agent ops dashboard")
APP_BLOCKS = _ini("app-blocks", "App blocks")
APP_BLOCKS_FU = _ini("app-blocks-followups", "App blocks followups")


# --------------------------------------------------------------------------- #
# signal_text
# --------------------------------------------------------------------------- #
def test_signal_text_combines_title_why_approach():
    p = _prop(title="Harden hook", why="prevents drift", approach="add a guard")
    text = routing.signal_text(p)
    assert "Harden hook" in text
    assert "prevents drift" in text
    assert "add a guard" in text


def test_signal_text_tolerates_dict_and_missing_fields():
    assert routing.signal_text({"title": "Just a title"}) == "Just a title"
    assert routing.signal_text({}) == ""


# --------------------------------------------------------------------------- #
# tag_proposals — PURE
# --------------------------------------------------------------------------- #
def test_confident_match_is_tagged():
    props = [_prop(title="harden the clawgate agent loop soak testing", why="", approach="")]
    related = routing.tag_proposals(props, [CLAWGATE, TEKTON])
    assert related == ["clawgate-agent-loop"]


def test_non_confident_match_is_not_tagged():
    # 'agent' alone is shared by clawgate-agent-loop AND agent-ops-dashboard (df==2) →
    # not a confident match on either → no tag.
    props = [_prop(title="agent onboarding docs", why="", approach="")]
    related = routing.tag_proposals(props, [CLAWGATE, AGENT_OPS])
    assert related == [None]


def test_no_overlap_is_not_tagged():
    props = [_prop(title="quarterly tax invoice paperwork", why="", approach="")]
    assert routing.tag_proposals(props, [CLAWGATE, TEKTON]) == [None]


def test_empty_store_yields_no_tags():
    props = [_prop(title="harden the clawgate agent loop"), _prop(title="tekton pipeline")]
    assert routing.tag_proposals(props, []) == [None, None]


def test_tags_are_index_aligned_per_proposal():
    props = [
        _prop(title="harden the clawgate agent loop", why="", approach=""),
        _prop(title="quarterly tax invoice paperwork", why="", approach=""),
        _prop(title="migrate the tekton pipeline", why="", approach=""),
    ]
    related = routing.tag_proposals(props, [CLAWGATE, TEKTON])
    assert related == ["clawgate-agent-loop", None, "tekton-pipeline"]


def test_only_the_single_top_row_is_taken_when_siblings_are_confident():
    # 'app-blocks-followups' hits 3 slug tokens on the FU sibling and 2 on app-blocks —
    # both can qualify, but tagging takes ONLY the top row (the more specific slug).
    props = [_prop(title="app-blocks-followups triage", why="", approach="")]
    related = routing.tag_proposals(props, [APP_BLOCKS, APP_BLOCKS_FU])
    assert related == ["app-blocks-followups"]


def test_repo_scope_limits_the_candidate_initiatives():
    a = _ini("widget-sync", "Widget sync", repo="/home/zach/workspace/devrc")
    b = _ini("widget-sync-mirror", "Widget sync mirror", repo="/home/zach/workspace/other")
    props = [_prop(title="widget sync fixes", repo="other", why="", approach="")]
    related = routing.tag_proposals(props, [a, b])
    assert related == ["widget-sync-mirror"]


# --------------------------------------------------------------------------- #
# related_for — BEST-EFFORT wrapper (inject route.load_current; no real DB)
# --------------------------------------------------------------------------- #
def test_related_for_tags_from_injected_store(monkeypatch):
    route = routing._route()
    monkeypatch.setattr(route, "load_current", lambda: [CLAWGATE, TEKTON])
    props = [_prop(title="harden the clawgate agent loop", why="", approach="")]
    assert routing.related_for(props) == ["clawgate-agent-loop"]


def test_related_for_empty_store_no_tags(monkeypatch):
    route = routing._route()
    monkeypatch.setattr(route, "load_current", lambda: [])
    props = [_prop(title="harden the clawgate agent loop"), _prop(title="tekton")]
    assert routing.related_for(props) == [None, None]


def test_related_for_empty_proposals_returns_empty():
    assert routing.related_for([]) == []


def test_related_for_swallows_store_failure(monkeypatch):
    # Store unreachable → load_current raises → NO tags, pipeline continues (never raises).
    route = routing._route()

    def boom():
        raise RuntimeError("store down / no kubeconfig")

    monkeypatch.setattr(route, "load_current", boom)
    props = [_prop("A"), _prop("B")]
    assert routing.related_for(props) == [None, None]


def test_related_for_swallows_tagging_failure(monkeypatch):
    # Store loads, but the matcher blows up mid-tag → still swallowed → all None.
    route = routing._route()
    monkeypatch.setattr(route, "load_current", lambda: [CLAWGATE])

    def boom(*a, **k):
        raise RuntimeError("matcher exploded")

    monkeypatch.setattr(route, "rank_matches", boom)
    props = [_prop("A"), _prop("B")]
    assert routing.related_for(props) == [None, None]


def test_related_for_swallows_router_import_failure(monkeypatch):
    # Router import itself fails → NO tags, no crash.
    def boom():
        raise ImportError("cannot load route.py")

    monkeypatch.setattr(routing, "_route", boom)
    props = [_prop("A")]
    assert routing.related_for(props) == [None]


# --------------------------------------------------------------------------- #
# digest rendering of the ↳ relates-to breadcrumb
# --------------------------------------------------------------------------- #
def test_render_shows_relates_to_only_where_tagged():
    props = [_prop("A"), _prop("B")]
    body = digest.render(props, today=date(2026, 7, 1),
                         related=["clawgate-chat-polish", None])
    assert "↳ relates to: clawgate-chat-polish" in body
    assert body.count("relates to:") == 1  # only the tagged proposal shows it


def test_render_without_related_has_no_breadcrumb():
    body = digest.render([_prop("A")], today=date(2026, 7, 1))
    assert "relates to" not in body


def test_render_tolerates_short_related_list():
    # A related list shorter than proposals must not IndexError — extra proposals untagged.
    body = digest.render([_prop("A"), _prop("B")], today=date(2026, 7, 1),
                         related=["only-first"])
    assert "↳ relates to: only-first" in body
    assert body.count("relates to:") == 1
