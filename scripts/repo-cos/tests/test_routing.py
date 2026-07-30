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


# --------------------------------------------------------------------------- #
# taggable-slug DENYLIST — every case below is a REAL slug from initiatives.current
# (or from a 120-day replay of the same scan that mints it). A breadcrumb may show
# junk; a durable clawgate `initiative:` tag may not.
# --------------------------------------------------------------------------- #

# (slug, expected drop reason) — the junk the vocabulary actually contains.
JUNK_SLUGS = [
    # bare dates adopted from a dated filename
    ("2026-07-21", "no-letters"),
    ("2026-06-13", "no-letters"),
    # NOT-ALREADY-LOWERCASE. The tag grammar lowercases, so any of these would emit a tag
    # that no longer equals its ledger slug → the initiatives-side join silently misses.
    # All-caps doc filenames (HANDOFF.md, APP-DISCOVERY-DESIGN.md …) …
    ("HANDOFF", "not-lowercase"),
    ("SESSION-HANDOFF", "not-lowercase"),
    ("APP-DISCOVERY-DESIGN", "not-lowercase"),
    ("COMFYUI-INTEGRATION-DESIGN", "not-lowercase"),
    # … and the MIXED-case ones, which hit the identical join-miss. `HANDOFF-comfyui-
    # session` is also exactly the doc-filename class the rule exists to drop.
    ("HANDOFF-comfyui-session", "not-lowercase"),
    ("SECURITY-AUDIT-v0.1.64", "not-lowercase"),
    # SYNTHETIC (the one entry in this table that is not a real store slug): title-case,
    # the shape a future scan could mint from a prose heading. `remix-platform` IS real.
    ("Remix-Platform", "not-lowercase"),
    # ClickUp id salad
    ("868j34n9y-868kf6w7r-complete-mark", "opaque-id"),
    ("868f9pd14-close-issue", "opaque-id"),
    # pure document/process filler
    ("actionable-next-steps", "generic"),
    ("next-session", "generic"),
    ("past-sessions-week", "generic"),
    # degenerate input
    ("", "empty"),
    ("   ", "empty"),
    ("ab", "too-short"),
]

# Genuine (all-lowercase) initiative slugs that MUST survive — including the near-misses
# that a lazier rule (any "-handoff" suffix, any digit run) would eat.
GENUINE_SLUGS = [
    "clawgate-agent-loop-close",
    "dp-prod-latency-sweep",
    "remix-next-session-kickoff",         # contains 'next-session' but names remix
    "faro-rum-observability",
    "arr-backfill-and-face-pass",
    "standup-triage-handoff",             # '-handoff' suffix, real arc
    "clawgate-documentation-handoff",
    "spend-exporter-handoff",
    "security-audit-v0.1.64",             # the lowercase form of a real slug: KEPT
    "civitai-cli-arc-continuation-v0.1.73",
    "image-scan-ingestion-down-0313-rca",  # '0313' is a date, not an opaque id
    "sysredis-ha-cutover-complete",
    "app-blocks-w13-external-listings",
    "svi_loop_recipe",                     # underscores are legal in the tag charset
    "mail-automation",
    "espanso-typing-toil",
]

# Ambiguous by design: junk-ish, but no rule separates them from genuine short slugs
# without overfitting. PINNED as KEPT — a missing tag is cheap, a wrong tag is not.
AMBIGUOUS_KEPT = [
    "code-open",
    "dispatch-yes",
    "changes-merge-ship",
    "from-memory-previous",
    "component-file",
    "durable-new-tag",
]


def test_denylist_drops_each_junk_pattern():
    for slug, reason in JUNK_SLUGS:
        assert routing.slug_drop_reason(slug) == reason, slug
        assert routing.taggable_slug(slug) is None, slug


def test_denylist_keeps_every_genuine_slug():
    for slug in GENUINE_SLUGS:
        assert routing.slug_drop_reason(slug) is None, slug
        assert routing.taggable_slug(slug) == slug, slug


def test_denylist_keeps_ambiguous_slugs_by_design():
    for slug in AMBIGUOUS_KEPT:
        assert routing.slug_drop_reason(slug) is None, slug
        assert routing.taggable_slug(slug) == slug, slug


def test_taggable_slug_logs_what_it_dropped_and_why(capsys):
    # Silent filtering is not acceptable: a dropped slug must name itself AND its rule.
    assert routing.taggable_slug("HANDOFF") is None
    err = capsys.readouterr().err
    assert "HANDOFF" in err
    assert "not-lowercase" in err
    assert "dropped initiative slug" in err


def test_taggable_slug_logs_nothing_when_it_keeps(capsys):
    assert routing.taggable_slug("clawgate-agent-loop-close") == "clawgate-agent-loop-close"
    assert capsys.readouterr().err == ""


def test_taggable_slug_trims_and_never_raises_on_junk_types():
    assert routing.taggable_slug("  dp-prod-latency-sweep  ") == "dp-prod-latency-sweep"
    assert routing.taggable_slug(None) is None
    assert routing.taggable_slug(12345) is None      # no letters
    # A non-string that stringifies to something with letters survives the DENYLIST (it
    # is not the denylist's job to police charset) — the tag-grammar layer drops it.
    assert routing.taggable_slug(["a", "b"]) == "['a', 'b']"


def test_slug_tokens_splits_on_all_separators():
    assert routing.slug_tokens("App-Blocks_w13/x.y") == ["app", "blocks", "w13", "x", "y"]


def test_an_all_lowercase_slug_is_kept_verbatim():
    # The positive half of the not-lowercase rule: already-lowercase → kept, byte-identical.
    for slug in ("handoff-comfyui-session", "security-audit-v0.1.64", "remix-platform"):
        assert routing.slug_drop_reason(slug) is None, slug
        assert routing.taggable_slug(slug) == slug, slug


# --------------------------------------------------------------------------- #
# CORPUS PROPERTIES — asserted over a SNAPSHOT of the real `initiatives.current`
# vocabulary (tests/fixtures/initiatives_current_slugs.txt, 140 slugs, 2026-07-30).
# Hermetic: the file is a checked-in snapshot, never a live read.
#
# ⚠ WHAT THESE CAN AND CANNOT CATCH. Every number below is pinned against the FIXTURE,
# not against the live store, so they detect an unreviewed EDIT to the fixture or a
# behaviour change in the denylist — never that the live vocabulary has moved on. It
# already has once (139 → 140 between the feature landing and this follow-up) with the
# suite fully green. Refreshing the fixture is a manual step; the command is in its
# header. That trade is deliberate: a live read here would make the whole suite depend
# on cross-cluster reachability, which tests/conftest.py exists specifically to prevent.
# --------------------------------------------------------------------------- #
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _slug_corpus() -> list[str]:
    """The checked-in SNAPSHOT of `initiatives.current` — not a live read (see above)."""
    lines = FIXTURES.joinpath("initiatives_current_slugs.txt").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def test_corpus_fixture_self_check_not_a_live_drift_detector():
    """SELF-CHECK on the checked-in fixture — deliberately NOT a live-store drift detector.

    All this proves is that the fixture still holds the number of slugs the properties
    below are stated in, i.e. it fails only if someone edits the file without re-stating
    the counts. It CANNOT fail because `initiatives.current` grew, since nothing here
    reads the store. Do not read a green suite as "the snapshot is current" — refresh it
    with the command in the fixture header (and re-state these counts + README.md)."""
    assert len(_slug_corpus()) == 140


def test_every_emitted_tag_equals_its_ledger_slug_exactly(capsys):
    """🔴 THE JOIN INVARIANT, over the whole real vocabulary and through the REAL emission
    path (`clawgate.build_tags`, not just the grammar layer): every slug yields either NO
    tag or EXACTLY `initiative:<slug>` — never a lowercased/whitespace-collapsed/mangled
    variant. Enforced structurally in build_tags; the denylist's not-lowercase rule only
    keeps that from throwing away tags we could have kept."""
    import clawgate  # noqa: PLC0415 - local: keeps the routing-only tests import-light
    for slug in _slug_corpus():
        # the emission path, end to end: a slug on a proposal → the tag list posted.
        assert clawgate.build_tags({"initiative": slug}) in ([], [f"initiative:{slug}"]), slug
        keep = routing.taggable_slug(slug)
        if keep is None:
            continue
        assert keep == slug, f"denylist mutated {slug!r} -> {keep!r}"
        tags = clawgate.normalize_tags([f"initiative:{keep}"])
        # the grammar layer may still DROP (e.g. the 64-rune cap) — it must never REWRITE.
        assert tags in ([], [f"initiative:{slug}"]), (slug, tags)
    capsys.readouterr()  # swallow the per-drop log lines


def test_corpus_denylist_drop_counts_are_pinned(capsys):
    """The measurement this rule change was justified by, restated against the CURRENT
    fixture (2026-07-30, 140 slugs): 10 denylist drops (4 all-caps + 2 mixed-case + 1 bare
    date + 1 opaque id + 2 generic) → 130 pass the denylist → 3 more lost to the 64-rune
    tag cap → 127 actually taggable. These are fixture numbers, not live ones — see the
    section header. (The one slug added since the feature landed,
    `deploy-comfyui-video-generation`, is taggable, so only the totals moved.)"""
    import clawgate  # noqa: PLC0415
    from collections import Counter
    corpus = _slug_corpus()
    reasons = Counter(r for r in (routing.slug_drop_reason(s) for s in corpus) if r)
    assert reasons == {"not-lowercase": 6, "no-letters": 1, "opaque-id": 1, "generic": 2}
    kept = [s for s in corpus if routing.slug_drop_reason(s) is None]
    assert len(kept) == 130
    taggable = [s for s in kept if clawgate.normalize_tags([f"initiative:{s}"])]
    assert len(taggable) == 127
    capsys.readouterr()


def test_the_two_mixed_case_corpus_slugs_are_now_dropped(capsys):
    # The exact slugs this rule change added to the drop set — pinned by name so a
    # regression is legible, not just a count that moved.
    corpus = _slug_corpus()
    for slug in ("HANDOFF-comfyui-session", "SECURITY-AUDIT-v0.1.64"):
        assert slug in corpus, f"{slug} left the store — refresh the fixture"
        assert routing.slug_drop_reason(slug) == "not-lowercase"
        assert routing.taggable_slug(slug) is None
    capsys.readouterr()
