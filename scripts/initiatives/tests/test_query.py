"""Unit tests for the READ-ONLY initiatives query tool surface
(scripts/initiatives/skills/query.py).

Hermetic: no live DB, no live model, no cluster. `query.run_query` takes an INJECTABLE
`loader()` -> (views, unmatched); every test feeds it the same fixture "views" the sibling
`test_assistant.py` uses (the shape `viewer.build_model`'s `flat` produces), so nothing here
opens a port-forward or touches ClickHouse/Postgres. `main()`'s live-store path is exercised
by monkeypatching `query._assistant` (query re-execs assistant.py fresh per call via
importlib, so we stub the loaded module rather than a cached attribute).

query.py REUSES assistant.py's pure functions verbatim (`run_tool`/`build_facts`/
`sources_of`/`load_initiatives`); these tests assert the projection + the agent-facing
contract (catalog integrity, grounded {ok,tool,target,facts,sources} shape, the ok:false
error contract + exit codes) — NOT re-testing assistant's tool internals.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_INIT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_INIT_DIR))
sys.path.insert(0, str(_INIT_DIR / "skills"))

import assistant  # noqa: E402  (for building expectations / wrapping in the main() stubs)
import query  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture initiative "views" — same shape test_assistant.py exercises.
# --------------------------------------------------------------------------- #
NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _view(slug, title, repo, *, momentum="active", status="", next_step="",
          summary="", identity="", age="1h", minutes_ago=60,
          live_tasks=None, tmux_sessions=None, current_doc=""):
    return {
        "slug": slug, "title": title, "repo": repo, "repo_name": repo.rsplit("/", 1)[-1],
        "momentum": momentum, "status": status, "next_step": next_step,
        "summary": summary, "identity": identity, "age": age,
        "last_touch": NOW - timedelta(minutes=minutes_ago),
        "open_prs": [], "live_tasks": live_tasks or [], "live_task": (live_tasks or [""])[0],
        "tmux_sessions": tmux_sessions or [], "current_doc": current_doc,
    }


# One genuinely BLOCKED (a marker in status/next_step), one STALLED, one ACTIVE. The blocked
# one is `momentum="slowing"` so the momentum buckets stay disjoint from the block filter.
BLOCKED = _view("cutover-gate", "Phase 7 cutover", "/repo/devrc",
                momentum="slowing",
                status="awaiting Zach's go-ahead on the phase-7 cutover",
                next_step="cut over phase 7", age="20m", minutes_ago=20)
STALLED = _view("sentinel-soak", "Redis sentinel soak", "/repo/homelab",
                momentum="stalled", next_step="resume the sentinel failover soak",
                age="9d", minutes_ago=60 * 24 * 9)
ACTIVE = _view("quantum-sched", "Quantum scheduler", "/repo/devrc",
               momentum="active", next_step="wire the second stage", age="45m",
               minutes_ago=45)

VIEWS = [BLOCKED, STALLED, ACTIVE]
UNMATCHED = [{"id": "Pool2", "title": "dp-500 sweep", "repo": "/repo/datapacket",
              "repo_name": "datapacket"}]


def _loader():
    """The injectable store read — fixtures only, deterministic."""
    return (VIEWS, UNMATCHED)


def _boom_loader():
    raise ConnectionError("port-forward failed")


def _run(tool, target=""):
    return query.run_query(tool, target, loader=_loader)


def _slugs(sources):
    return {s["slug"] for s in sources}


# --------------------------------------------------------------------------- #
# 1. Catalog integrity (guard against agent-catalog ↔ assistant-intent drift).
# --------------------------------------------------------------------------- #
def test_every_tool_maps_to_an_intent():
    # Bijection: no tool without an intent, no intent mapping without a catalogued tool.
    assert set(query._TOOLS) == set(query._TOOL_TO_INTENT)


def test_every_mapped_intent_is_actually_dispatched_by_run_tool():
    # DRIFT GUARD: run each mapped intent through assistant.run_tool over the fixtures; a
    # non-"overview" intent MUST yield a non-"overview" result kind — proving run_tool
    # explicitly dispatches it and did not silently fall through to the overview fallback.
    for tool, intent in query._TOOL_TO_INTENT.items():
        # A target that resolves to nothing keeps status_of/route/handoff/by_repo on their
        # own code path (handoff with no match returns before touching the viewer reader).
        info = {"intent": intent, "target": "zzz-no-such-initiative"}
        result = assistant.run_tool(intent, info, VIEWS, UNMATCHED)
        kind = result.get("kind")
        assert kind, f"{tool}->{intent} returned no kind"
        if intent != "overview":
            assert kind != "overview", (
                f"{tool}->{intent} fell through to the overview fallback (undispatched)")


def test_catalog_lists_every_tool_with_its_takes_target_flag():
    cat = query._catalog()
    entries = cat["tools"]
    assert len(entries) == len(query._TOOLS)
    assert {e["name"] for e in entries} == set(query._TOOLS)
    for e in entries:
        takes_target, description = query._TOOLS[e["name"]]
        assert e["takes_target"] is takes_target
        assert e["description"] == description
    # sorted + JSON-serializable (the machine-readable contract the SKILL.md documents).
    assert [e["name"] for e in entries] == sorted(query._TOOLS)
    json.dumps(cat)


# --------------------------------------------------------------------------- #
# 2. run_query grounding — over the injected fixture loader.
# --------------------------------------------------------------------------- #
def test_result_shape_is_always_the_contract_and_json_serializable():
    for tool in query._TOOLS:
        target = "quantum" if query._TOOLS[tool][0] else ""
        out = _run(tool, target)
        assert set(out) == {"ok", "tool", "target", "facts", "sources"}
        assert out["ok"] is True
        assert out["tool"] == tool
        assert out["target"] == target
        assert isinstance(out["facts"], dict)
        assert isinstance(out["sources"], list)
        # must never raise — the CLI dumps with default=str.
        json.dumps(out, default=str)


def test_blocked_on_me_returns_only_the_blocked_initiative():
    out = _run("blocked_on_me")
    assert out["ok"] is True
    assert _slugs(out["sources"]) == {"cutover-gate"}
    fact_slugs = {f["slug"] for f in out["facts"]["initiatives"]}
    assert fact_slugs == {"cutover-gate"}
    # the grounded block reason is the initiative's OWN awaiting-text, not an invention.
    reason = out["facts"]["initiatives"][0]["waiting_on_you_because"]
    assert "awaiting" in reason


def test_stalled_returns_only_the_stalled_initiative():
    out = _run("stalled")
    assert _slugs(out["sources"]) == {"sentinel-soak"}
    assert {f["slug"] for f in out["facts"]["initiatives"]} == {"sentinel-soak"}


def test_active_returns_only_the_active_initiative():
    out = _run("active")
    assert _slugs(out["sources"]) == {"quantum-sched"}
    assert {f["slug"] for f in out["facts"]["initiatives"]} == {"quantum-sched"}


def test_status_of_resolves_the_named_initiative():
    out = _run("status_of", "quantum")
    assert out["target"] == "quantum"
    assert _slugs(out["sources"]) == {"quantum-sched"}


def test_route_returns_a_route_kind_facts_dict_with_a_verdict():
    out = _run("route", "harden the redis sentinel failover soak")
    facts = out["facts"]
    assert facts["kind"] == "route"
    assert facts.get("verdict")               # a verdict string is always present
    assert facts["signal"] == "harden the redis sentinel failover soak"
    # the router grounds onto the matching initiative (single-sourced from route.rank_matches)
    assert any(s["slug"] == "sentinel-soak" for s in out["sources"])


def test_overview_counts_every_fixture():
    out = _run("overview")
    assert out["facts"]["count"] == len(VIEWS)
    assert {f["slug"] for f in out["facts"]["initiatives"]} == {v["slug"] for v in VIEWS}


# --------------------------------------------------------------------------- #
# 4. Sources computed deterministically — present for initiative-returning tools,
#    empty for an empty result.
# --------------------------------------------------------------------------- #
def test_sources_present_for_initiative_returning_tool():
    out = _run("overview")
    assert _slugs(out["sources"]) == {v["slug"] for v in VIEWS}
    # each source is the deterministic {slug, repo} citation shape.
    for s in out["sources"]:
        assert set(s) >= {"slug", "repo"}


def test_sources_empty_for_an_empty_result():
    out = _run("status_of", "nonexistent-xyz-initiative")
    assert out["ok"] is True
    assert out["sources"] == []
    assert out["facts"]["initiatives"] == []


# --------------------------------------------------------------------------- #
# 3. Error contract + main() exit codes.
# --------------------------------------------------------------------------- #
def _stub_assistant(loader):
    """A proxy over the REAL assistant module that overrides only `load_initiatives`, so
    main()'s no-loader `run_query` path exercises the injected store read (fixtures or a
    raise) while run_tool/build_facts/sources_of stay verbatim. query re-execs assistant per
    call, so we patch `query._assistant` to hand back this proxy."""
    real = assistant

    class _Proxy:
        load_initiatives = staticmethod(loader)

        def __getattr__(self, name):
            return getattr(real, name)

    return _Proxy()


def test_main_valid_tool_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(query, "_assistant", lambda: _stub_assistant(_loader))
    rc = query.main(["active"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["tool"] == "active"
    assert {s["slug"] for s in out["sources"]} == {"quantum-sched"}


def test_main_list_returns_zero(capsys):
    rc = query.main(["--list"])
    assert rc == 0
    cat = json.loads(capsys.readouterr().out)
    assert {e["name"] for e in cat["tools"]} == set(query._TOOLS)


def test_main_missing_tool_returns_two(capsys):
    rc = query.main([])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert err["ok"] is False
    assert "tool" in err["error"]


def test_main_store_read_failure_returns_two_and_ok_false(monkeypatch, capsys):
    monkeypatch.setattr(query, "_assistant", lambda: _stub_assistant(_boom_loader))
    rc = query.main(["blocked_on_me"])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)
    assert err["ok"] is False
    assert err["tool"] == "blocked_on_me"
    assert "ConnectionError" in err["error"]
    # the error contract never leaks facts/sources — the agent must report the failure.
    assert "facts" not in err and "sources" not in err


def test_run_query_propagates_loader_error_for_main_to_wrap():
    # run_query itself raises (main() is what converts it to the ok:false contract).
    with pytest.raises(ConnectionError):
        query.run_query("active", "", loader=_boom_loader)
