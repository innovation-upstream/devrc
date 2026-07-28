"""Unit tests for the READ-ONLY initiatives assistant (scripts/initiatives/assistant.py).

Hermetic: no live DB, no live model. The pure core (intent classification, the tool
queries, the plain renderer, the fact projection, sources) is exercised with fixture
initiative "views"; the assistant loop is exercised with an injected FAKE model client and
injected views, so nothing here opens a port-forward or hits ClickHouse/Postgres/vLLM.

`assistant` lazily loads its `route` sibling (the scan's token matcher) via importlib for
the status_of/route tools — the same import test_route.py relies on, available in the
hermetic pytest sandbox. No viewer/recap module is loaded (handoff read + model are
injected).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import assistant  # noqa: E402


@pytest.fixture(autouse=True)
def _capture_log(monkeypatch):
    """Neutralize the audit-log DB writer for EVERY test in this file: capture the rows in
    a list instead of opening a kubectl port-forward (keeps the suite hermetic + fast, and
    lets the logging tests assert on what was written). `ask()` routes to the module-level
    `_write_log_row` when no `log_writer` is injected, so patching it here covers all asks."""
    rows: list[dict] = []
    monkeypatch.setattr(assistant, "_write_log_row",
                        lambda row, **_kw: rows.append(row))
    return rows


# --------------------------------------------------------------------------- #
# Fixture initiative "views" — the shape viewer.build_model's `flat` produces.
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


CLAWGATE = _view("clawgate-agent-loop", "Clawgate agent loop close", "/repo/devrc",
                 status="awaiting Zach's go-ahead on the phase-7 cutover",
                 next_step="cut over phase 7", age="20m", minutes_ago=20)
SYSREDIS = _view("sysredis-buffer", "Redis sentinel failover hardening", "/repo/homelab",
                 momentum="stalled", next_step="resume the sentinel failover soak",
                 age="9d", minutes_ago=60 * 24 * 9)
REMIX = _view("remix-session", "Remix session platform", "/repo/remix",
              momentum="slowing", status="exploring age-gating and moderation",
              age="2d", minutes_ago=60 * 48)
TAXES = _view("taxes-archiver", "Tax invoice archiver", "/repo/devrc",
              next_step="your call on the invoice archive format", age="3h",
              minutes_ago=180)
COMFY = _view("2026-07-21", "ComfyUI realism pipeline", "/repo/civitai",
              live_tasks=["running preference round 3"], tmux_sessions=["8-1"],
              age="45m", minutes_ago=45)

VIEWS = [CLAWGATE, SYSREDIS, REMIX, TAXES, COMFY]
UNMATCHED = [{"id": "Pool2", "title": "dp-500 sweep", "repo": "/repo/datapacket",
              "repo_name": "datapacket"}]


def _slugs(items):
    return [i["slug"] for i in items]


# --------------------------------------------------------------------------- #
# Intent classification (pure, deterministic)
# --------------------------------------------------------------------------- #
def test_classify_blocked_on_me_variants():
    for q in ["what's blocked on me?", "what is waiting on me right now",
              "anything need my input?", "what needs my call"]:
        assert assistant.classify_intent(q)["intent"] == "blocked_on_me", q


def test_classify_momentum_buckets():
    assert assistant.classify_intent("what's stalled?")["intent"] == "stalled"
    assert assistant.classify_intent("anything slowing down")["intent"] == "slowing"
    assert assistant.classify_intent("what am I working on")["intent"] == "active"


def test_classify_status_of_extracts_target():
    info = assistant.classify_intent("what's the status of clawgate")
    assert info["intent"] == "status_of"
    assert info["target"] == "clawgate"


def test_classify_where_did_i_leave_extracts_target():
    info = assistant.classify_intent("where did I leave off with sysredis buffer")
    assert info["intent"] == "status_of"
    assert "sysredis" in info["target"]


def test_classify_route_extracts_signal():
    info = assistant.classify_intent("which initiative does the redis failover work belong to")
    assert info["intent"] == "route"
    assert "redis" in info["target"]


def test_classify_handoff_extracts_target():
    info = assistant.classify_intent("read the handoff for clawgate")
    assert info["intent"] == "handoff"
    assert info["target"] == "clawgate"


def test_classify_live_sessions_and_most_recent():
    assert assistant.classify_intent("what's running right now")["intent"] == "live_sessions"
    assert assistant.classify_intent("what did I touch most recently")["intent"] == "most_recent"


def test_classify_unknown_falls_back_to_overview():
    assert assistant.classify_intent("hello there friend")["intent"] == "overview"
    assert assistant.classify_intent("")["intent"] == "overview"


# --------------------------------------------------------------------------- #
# Tools (pure, over fixture views)
# --------------------------------------------------------------------------- #
def test_tool_blocked_on_me_filters_on_markers():
    res = assistant.tool_blocked_on_me(VIEWS)
    # clawgate ("awaiting …") + taxes ("your call …") are blocked; the rest are not.
    assert set(_slugs(res["initiatives"])) == {"clawgate-agent-loop", "taxes-archiver"}
    assert "awaiting" in res["markers"]["clawgate-agent-loop"]
    assert "your call" in res["markers"]["taxes-archiver"]


def test_tool_blocked_on_me_empty_when_nothing_waiting():
    calm = [_view("calm-a", "Calm A", "/repo/x", status="all merged and shipped"),
            _view("calm-b", "Calm B", "/repo/x", next_step="keep soaking")]
    assert assistant.tool_blocked_on_me(calm)["initiatives"] == []


# --- precision: blocked-scan reads ONLY status + next_step, not purpose/recall text ------
def test_blocking_hits_scans_only_status_and_next_step():
    # A marker present ONLY in identity/summary (the durable purpose / recall text) must NOT
    # flag the initiative — those describe purpose, not what's actually pending on the user.
    v = _view("purpose-only", "Purpose only", "/repo/x",
              status="fix(x): harden per audit", next_step="",
              identity="decision-ready cards for Zach to review and sign-off",
              summary="waiting on you is the whole point of this project")
    assert assistant._blocking_hits(v) == []
    assert assistant._BLOCKED_FIELDS == ("status", "next_step")


def test_blocked_markers_pruned_of_process_prose():
    # The leaky descriptive/process markers are gone; the genuine wait markers remain.
    for leaked in ("for zach to", "eyeball", "human deploys", "human verif", "you deploy"):
        assert leaked not in assistant.BLOCKED_MARKERS, leaked
    for genuine in ("awaiting", "blocked on", "your call", "your input", "your review",
                    "your sign-off", "your go-ahead", "waiting on you", "waiting for you",
                    "waiting on zach", "needs you", "needs zach", "pending your",
                    "up to zach"):
        assert genuine in assistant.BLOCKED_MARKERS, genuine


# --------------------------------------------------------------------------- #
# Severity signal — an ACTIVE, UNRESOLVED risk promotes a card to needs_you even when it
# carries no "blocked/awaiting" wait marker. Scanned over the SAME narrow status/next_step
# surface as the blocked-scan, so an infra card that merely NAMES prod can't trip it.
# --------------------------------------------------------------------------- #
def test_severity_hits_flags_active_unresolved_problems():
    # A live risk in status (a 5xx incident still happening) → flagged via the phrase marker.
    # (The bare "499s"/HTTP-code markers were dropped for precision — the phrase catches it.)
    v = _view("img-499", "Image edge 499s", "/repo/civitai",
              status="the 5xx errors are still happening after the rollout", next_step="")
    marks = assistant._severity_hits(v)
    assert "still happening" in marks and "5xx" in marks
    # A disk risk in next_step → flagged.
    v2 = _view("disk", "Disk pressure", "/repo/homelab",
               status="", next_step="node is almost out of space — disk full imminent")
    marks2 = assistant._severity_hits(v2)
    assert "out of space" in marks2 and "disk full" in marks2


def test_severity_hits_no_false_positive_on_numbers_or_versions():
    # Precision guard (audit 2026-07-28): the dropped bare numeric HTTP codes used to substring-
    # match record counts / versions / durations. None of these benign statuses may flag.
    for status in ("processed 15024 pending rows", "released v1.502.0 to prod",
                   "the job ran for 2499s", "bumped quota to 5040 users",
                   "3 unresolved review comments to address"):
        v = _view("calm", "Calm", "/repo/x", status=status, next_step="")
        assert assistant._severity_hits(v) == [], status


def test_severity_hits_does_not_flag_benign_prod_mentions():
    # The whole precision point: a card that merely deployed/mentions prod is NOT a risk.
    for status in ("deployed to prod, soaking", "shipped the prod rollout, all green",
                   "client-facing prod dashboard work", "monitoring prod, nothing on fire"):
        v = _view("calm", "Calm", "/repo/x", status=status, next_step="keep soaking")
        assert assistant._severity_hits(v) == [], status


def test_severity_hits_scans_only_status_and_next_step():
    # A severity phrase present ONLY in identity/summary must NOT flag (mirrors the blocked-scan
    # precision guard) — those are purpose/recall text, not what's actively broken.
    v = _view("purpose", "Purpose", "/repo/x", status="fix(x): tidy up", next_step="",
              identity="a project about 5xx outage triage and crashloop recovery",
              summary="disk full and data loss were the original motivation")
    assert assistant._severity_hits(v) == []


def test_severity_markers_curated_for_precision():
    # Broad topic words that would over-match every infra card are EXCLUDED; the concrete
    # active-failure signals are present. Bare "oom" (matches room/zoom) is excluded.
    # Broad topic words, bare oom, AND the FP-prone bare numeric HTTP codes + "unresolved" are OUT.
    for broad in ("prod", "regression", "growing", "client-facing", "slow", "oom",
                  "499s", "500s", "502", "503", "504", "unresolved", "not resolved"):
        assert broad not in assistant.SEVERITY_MARKERS, broad
    for genuine in ("still happening", "still failing", "out of space",
                    "disk full", "5xx", "outage", "crashloop", "data loss",
                    "flapping", "oomkill"):
        assert genuine in assistant.SEVERITY_MARKERS, genuine


def test_severity_bare_oom_word_does_not_false_positive():
    # Regression guard: "room"/"zoom" must not trip a severity hit (only oomkill/oom-kill do).
    v = _view("mtg", "Meeting notes", "/repo/x",
              status="need more room in the zoom call for the boomer cohort", next_step="")
    assert assistant._severity_hits(v) == []


# --- REGRESSION: the real logged false positive (task-spec-drafter) ----------------------
# Diagnosed from a live "whats blocked on me" ask that wrongly returned task-spec-drafter
# alongside the genuine spend-analytics. Two leaks: (1) the scan read `identity` (whose
# purpose text "…cards for Zach to adjudicate…" matched the marker "for zach to"), and
# (2) that marker was itself descriptive prose. Both are now closed.
TASK_SPEC_DRAFTER = _view(
    "task-spec-drafter", "Task-spec drafter", "/repo/devrc",
    status="fix(task-spec-drafter): harden per audit — read-only allowlist, "
           "spec-only writes, no dispatch",
    next_step="",  # no explicit next-step → nothing genuinely pending on the user
    identity="an autonomous loop that turns signals into decision-ready cards "
             "for Zach to adjudicate")
SPEND_ANALYTICS = _view(
    "spend-analytics", "Spend analytics", "/repo/devrc",
    status="blocked: awaiting the monthly $ figure from Zach for the Cloudflare line",
    next_step="awaiting the monthly $ figure from Zach", age="5h", minutes_ago=300)


def test_regression_task_spec_drafter_not_blocked_but_spend_analytics_is():
    res = assistant.tool_blocked_on_me([TASK_SPEC_DRAFTER, SPEND_ANALYTICS])
    slugs = _slugs(res["initiatives"])
    assert "task-spec-drafter" not in slugs          # the false positive is gone
    assert slugs == ["spend-analytics"]              # ONLY the genuine block remains
    assert "awaiting" in res["markers"]["spend-analytics"]


# --------------------------------------------------------------------------- #
# Change A — the board⟷chat consistency bug: `tool_blocked_on_me` must return the SAME set the
# board's `Needs you` chip counts (blocked WAITS + live-risk SEVERITY), distinguishing the two.
# A card that carries only a severity risk (no "blocked/awaiting" wait marker) used to be
# counted by the chip but MISSED by the chat — the trust bug this change closes.
# --------------------------------------------------------------------------- #
# A pure severity-risk card: an active 5xx incident in `status`, NO blocked/awaiting wait marker.
RISK_5XX = _view("img-499", "Image edge 499s", "/repo/civitai",
                 status="the 5xx errors are still happening after the rollout",
                 next_step="", age="6h", minutes_ago=360)
# A card that trips BOTH a blocked wait AND a severity marker — reason must resolve to "blocked".
BOTH = _view("both-signals", "Both signals", "/repo/devrc",
             status="awaiting your call — the outage is still happening", next_step="",
             age="2h", minutes_ago=120)


def test_tool_blocked_on_me_includes_severity_risk_not_just_blocked():
    # A blocked wait (spend-analytics) AND a pure severity risk (img-499) are BOTH returned —
    # the risk card is no longer silently dropped while the board chip counts it.
    res = assistant.tool_blocked_on_me([SPEND_ANALYTICS, RISK_5XX])
    slugs = set(_slugs(res["initiatives"]))
    assert slugs == {"spend-analytics", "img-499"}
    # The reason distinguishes them: a wait vs a live risk.
    assert res["reasons"]["spend-analytics"] == "blocked"
    assert res["reasons"]["img-499"] == "risk"
    # markers follow the reason (blocked hits for the wait, severity phrases for the risk).
    assert "awaiting" in res["markers"]["spend-analytics"]
    assert "5xx" in res["markers"]["img-499"] or "still happening" in res["markers"]["img-499"]


def test_tool_blocked_on_me_reason_blocked_wins_when_both_trip():
    # When a card trips BOTH a wait and a severity marker, "blocked" wins (the more actionable
    # framing) — mirrors viewer.derive_needs_reason. Markers are the blocked hits, not severity.
    res = assistant.tool_blocked_on_me([BOTH])
    assert res["reasons"]["both-signals"] == "blocked"
    assert "your call" in res["markers"]["both-signals"]


def test_build_facts_blocked_distinguishes_wait_vs_risk():
    facts = assistant.build_facts(
        assistant.tool_blocked_on_me([SPEND_ANALYTICS, RISK_5XX]))
    by_slug = {f["slug"]: f for f in facts["initiatives"]}
    # The wait card: attention_reason "waiting on you" + a waiting_on_you_because, NO risk field.
    wait = by_slug["spend-analytics"]
    assert wait["attention_reason"] == "waiting on you"
    assert "awaiting" in wait["waiting_on_you_because"]
    assert "live_risk_because" not in wait
    # The risk card: attention_reason "live risk" + a live_risk_because, NO wait field.
    risk = by_slug["img-499"]
    assert risk["attention_reason"] == "live risk"
    assert "live_risk_because" in risk
    assert "waiting_on_you_because" not in risk


def test_render_plain_blocked_splits_waits_and_risks():
    out = assistant.render_plain(
        "blocked_on_me", {}, assistant.tool_blocked_on_me([SPEND_ANALYTICS, RISK_5XX]))
    assert "need your attention" in out
    assert "Waiting on you (1)" in out and "spend-analytics" in out
    assert "Live risks (1)" in out and "img-499" in out


def test_render_plain_blocked_empty_still_says_nothing():
    out = assistant.render_plain("blocked_on_me", {}, assistant.tool_blocked_on_me([]))
    assert "Nothing" in out


def test_blocked_on_me_matches_board_needs_you_set():
    # THE trust bug, pinned: the chat's `tool_blocked_on_me` set MUST equal the board's
    # `Needs you` set (viewer.derive_state == "needs_you"), over a mixed fixture that includes a
    # pure severity-risk card, a wait, a both-card, and non-attention cards. Same predicate.
    import viewer  # importable in the hermetic sandbox (psycopg2/requests are lazy)
    mixed = [SPEND_ANALYTICS, RISK_5XX, BOTH, SYSREDIS, REMIX, COMFY]
    board = {v["slug"] for v in mixed if viewer.derive_state(v)[0] == "needs_you"}
    tool = set(_slugs(assistant.tool_blocked_on_me(mixed)["initiatives"]))
    assert board == tool == {"spend-analytics", "img-499", "both-signals"}


def test_regression_blocked_facts_reason_comes_from_real_text_only():
    # The model must be handed the REAL awaiting-text as grounding — never a fabricated
    # "fix the issues per the audit" cause. Assert the facts carry the initiative's own
    # status/next_step and no invented narrative.
    facts = assistant.build_facts(
        assistant.tool_blocked_on_me([TASK_SPEC_DRAFTER, SPEND_ANALYTICS]))
    assert [f["slug"] for f in facts["initiatives"]] == ["spend-analytics"]
    spend = facts["initiatives"][0]
    assert "awaiting the monthly $ figure from Zach" in spend["next_step"]
    blob = json.dumps(facts).lower()
    assert "fix the issues" not in blob and "per the audit" not in blob


def test_synth_system_forbids_inventing_a_blocking_reason():
    # The anti-confab contract for blocked_on_me lives in the synthesis system prompt.
    sysprompt = assistant._SYNTH_SYSTEM.lower()
    assert "do not invent a cause" in sysprompt or "do not invent" in sysprompt
    assert "next_step/status" in sysprompt


def test_tool_by_momentum():
    assert _slugs(assistant.tool_by_momentum(VIEWS, "stalled")["initiatives"]) == \
        ["sysredis-buffer"]
    assert _slugs(assistant.tool_by_momentum(VIEWS, "slowing")["initiatives"]) == \
        ["remix-session"]
    active = _slugs(assistant.tool_by_momentum(VIEWS, "active")["initiatives"])
    assert set(active) == {"clawgate-agent-loop", "taxes-archiver", "2026-07-21"}


def test_tool_most_recent_orders_by_last_touch():
    res = assistant.tool_most_recent(VIEWS, n=3)
    # clawgate (20m) is newest, then comfyui (45m), then taxes (3h).
    assert _slugs(res["initiatives"]) == ["clawgate-agent-loop", "2026-07-21", "taxes-archiver"]


def test_tool_status_of_resolves_named_initiative_via_matcher():
    res = assistant.tool_status_of(VIEWS, "clawgate")
    assert res["initiatives"][0]["slug"] == "clawgate-agent-loop"


def test_tool_status_of_substring_fallback_below_token_bar():
    # "taxes" isn't a slug/title token of taxes-archiver's title ("Tax invoice archiver"),
    # so the token matcher may miss — the substring fallback still resolves it by slug.
    res = assistant.tool_status_of(VIEWS, "taxes")
    assert any(v["slug"] == "taxes-archiver" for v in res["initiatives"])


def test_tool_status_of_unknown_returns_empty():
    assert assistant.tool_status_of(VIEWS, "nonexistent-xyz")["initiatives"] == []


def test_tool_route_delegates_to_route_module():
    res = assistant.tool_route(VIEWS, "harden the redis sentinel failover soak")
    assert res["ranked"][0]["slug"] == "sysredis-buffer"
    assert res["ranked"][0]["confident"] is True
    assert "sysredis-buffer" in res["verdict"]


def test_tool_route_matches_route_rank_matches_exactly():
    # route_signal must be a faithful delegate — same ranking route.rank_matches gives.
    route = assistant._route()
    signal = "harden the redis sentinel failover soak"
    assert assistant.tool_route(VIEWS, signal)["ranked"] == \
        route.rank_matches(signal, VIEWS, limit=5)


def test_tool_live_sessions_lists_tied_plus_untracked():
    res = assistant.tool_live_sessions(VIEWS, UNMATCHED)
    assert _slugs(res["initiatives"]) == ["2026-07-21"]
    assert len(res["untracked"]) == 1


def test_tool_by_repo_groups_and_scopes():
    grouped = assistant.tool_by_repo(VIEWS)
    assert set(grouped["groups"]) == {"devrc", "homelab", "remix", "civitai"}
    scoped = assistant.tool_by_repo(VIEWS, "devrc")
    assert set(_slugs(scoped["initiatives"])) == {"clawgate-agent-loop", "taxes-archiver"}


def test_tool_read_handoff_uses_injected_reader_and_resolves_name():
    captured = {}

    def reader(repo, current_doc):
        captured["repo"] = repo
        return {"summary": "the durable goal", "next_steps": ["do X", "do Y"],
                "open_investigations": []}

    res = assistant.tool_read_handoff(VIEWS, "clawgate", reader=reader)
    assert res["initiative"]["slug"] == "clawgate-agent-loop"
    assert res["detail"]["summary"] == "the durable goal"
    assert captured["repo"] == "/repo/devrc"


def test_tool_read_handoff_unknown_initiative():
    res = assistant.tool_read_handoff(VIEWS, "nope-xyz", reader=lambda r, d: None)
    assert res["initiative"] is None


# --------------------------------------------------------------------------- #
# Sources (deterministic citation — the anti-confabulation anchor)
# --------------------------------------------------------------------------- #
def test_sources_from_filter_result():
    res = assistant.tool_blocked_on_me(VIEWS)
    srcs = assistant.sources_of(res)
    assert {s["slug"] for s in srcs} == {"clawgate-agent-loop", "taxes-archiver"}
    assert {s["repo"] for s in srcs} == {"devrc"}


def test_sources_from_route_result():
    res = assistant.tool_route(VIEWS, "redis sentinel failover")
    assert any(s["slug"] == "sysredis-buffer" for s in assistant.sources_of(res))


def test_sources_from_handoff_result():
    res = assistant.tool_read_handoff(VIEWS, "clawgate", reader=lambda r, d: {})
    srcs = assistant.sources_of(res)
    assert _slugs(srcs) == ["clawgate-agent-loop"]


# --------------------------------------------------------------------------- #
# Deterministic renderer (graceful-degradation path)
# --------------------------------------------------------------------------- #
def test_render_plain_blocked_lists_initiatives():
    out = assistant.render_plain("blocked_on_me", {},
                                 assistant.tool_blocked_on_me(VIEWS))
    assert "clawgate-agent-loop" in out and "taxes-archiver" in out


def test_render_plain_blocked_empty():
    out = assistant.render_plain("blocked_on_me", {},
                                 assistant.tool_blocked_on_me([]))
    assert "Nothing" in out


def test_render_plain_status_of_not_found():
    out = assistant.render_plain("status_of", {"target": "ghost"},
                                 assistant.tool_status_of(VIEWS, "ghost"))
    assert "Couldn't find" in out and "ghost" in out


def test_render_plain_route_shows_verdict():
    out = assistant.render_plain("route", {},
                                 assistant.tool_route(VIEWS, "redis sentinel failover"))
    assert "sysredis-buffer" in out


# --------------------------------------------------------------------------- #
# build_facts — compact + only tool-derived (nothing to confabulate from)
# --------------------------------------------------------------------------- #
def test_build_facts_caps_and_projects():
    facts = assistant.build_facts(assistant.tool_overview(VIEWS))
    assert facts["count"] == len(VIEWS)
    slugs = {f["slug"] for f in facts["initiatives"]}
    assert slugs == {v["slug"] for v in VIEWS}


def test_build_facts_blocked_carries_reason():
    facts = assistant.build_facts(assistant.tool_blocked_on_me(VIEWS))
    claw = next(f for f in facts["initiatives"] if f["slug"] == "clawgate-agent-loop")
    assert "awaiting" in claw["waiting_on_you_because"]


# --------------------------------------------------------------------------- #
# The assistant loop with a FAKE model client (intent -> tool -> grounded answer)
# --------------------------------------------------------------------------- #
class _FakeClient:
    """Routes generate() by the system prompt: the classify call (JSON) vs the synthesis
    call (a phrased answer). Records the DATA it was given so a test can assert grounding."""

    def __init__(self, *, synth="here is a grounded answer", classify_json=None):
        self._synth = synth
        self._classify_json = classify_json
        self.synth_calls = 0
        self.classify_calls = 0
        self.last_data = None

    def generate(self, messages, *, max_tokens=None, temperature=None):
        sys_prompt = messages[0]["content"]
        if sys_prompt.startswith("Classify"):
            self.classify_calls += 1
            return self._classify_json or '{"intent": "overview", "target": ""}'
        self.synth_calls += 1
        # capture the DATA json handed to the model
        user = messages[1]["content"]
        self.last_data = user
        return self._synth


def test_ask_intent_to_tool_to_model_answer():
    client = _FakeClient(synth="clawgate-agent-loop and taxes-archiver await you.")
    res = assistant.ask("what's blocked on me?", views=VIEWS, unmatched=UNMATCHED,
                        client=client)
    assert res["ok"] is True
    assert res["intent"] == "blocked_on_me"
    assert res["answer"] == "clawgate-agent-loop and taxes-archiver await you."
    assert {s["slug"] for s in res["sources"]} == {"clawgate-agent-loop", "taxes-archiver"}
    assert client.synth_calls == 1
    # a confidently-classified question must NOT invoke the model classifier
    assert client.classify_calls == 0


def test_ask_anticonfab_sources_come_from_tool_not_model():
    # The model FABRICATES an initiative in its prose; sources must still be ONLY the real
    # tool output (the auditability anchor). The invented slug never enters sources.
    client = _FakeClient(synth="You should look at ghost-initiative, it is on fire!")
    res = assistant.ask("what's stalled?", views=VIEWS, client=client)
    src_slugs = {s["slug"] for s in res["sources"]}
    assert "ghost-initiative" not in src_slugs
    assert src_slugs == {"sysredis-buffer"}   # the only real stalled initiative


def test_ask_model_refines_overview_fallback():
    # A fuzzily-worded question deterministic can't parse -> overview; the model maps it to
    # blocked_on_me and the RIGHT tool runs.
    client = _FakeClient(synth="two things await you",
                         classify_json='{"intent": "blocked_on_me", "target": ""}')
    res = assistant.ask("hey any hot potatoes on my plate", views=VIEWS, client=client)
    assert res["intent"] == "blocked_on_me"
    assert client.classify_calls == 1
    assert {s["slug"] for s in res["sources"]} == {"clawgate-agent-loop", "taxes-archiver"}


def test_ask_data_handed_to_model_is_grounded():
    client = _FakeClient()
    assistant.ask("what's stalled?", views=VIEWS, client=client)
    data = json.loads(client.last_data.split("DATA (the only facts you may use):\n", 1)[1])
    # the model only ever sees the real stalled initiative — nothing else to confabulate.
    assert [f["slug"] for f in data["initiatives"]] == ["sysredis-buffer"]


# --------------------------------------------------------------------------- #
# Best-effort degradation
# --------------------------------------------------------------------------- #
def test_ask_no_model_uses_deterministic_render():
    res = assistant.ask("what's blocked on me?", views=VIEWS, client=None,
                        client_factory=lambda: None)
    assert res["ok"] is True
    assert res["intent"] == "blocked_on_me"
    # deterministic renderer output (no model) still names the real initiatives
    assert "clawgate-agent-loop" in res["answer"]
    assert {s["slug"] for s in res["sources"]} == {"clawgate-agent-loop", "taxes-archiver"}


def test_ask_model_error_degrades_to_deterministic():
    class Boom:
        def generate(self, *a, **k):
            raise RuntimeError("vllm timeout")

    res = assistant.ask("what's stalled?", views=VIEWS, client=Boom())
    assert res["ok"] is True
    assert "sysredis-buffer" in res["answer"]  # deterministic fallback ran


def test_ask_store_unreachable_is_graceful_error():
    def boom_loader():
        raise ConnectionError("port-forward failed")

    res = assistant.ask("what's active?", loader=boom_loader)
    assert res["ok"] is False
    assert res["intent"] == "error"
    assert res["sources"] == []
    assert "read-only" in res["answer"]


def test_ask_empty_question_is_handled():
    res = assistant.ask("   ", views=VIEWS)
    assert res["ok"] is False
    assert res["sources"] == []


def test_ask_client_factory_failure_degrades():
    def bad_factory():
        raise RuntimeError("cannot open port-forward")

    res = assistant.ask("what's stalled?", views=VIEWS, client_factory=bad_factory)
    assert res["ok"] is True
    assert "sysredis-buffer" in res["answer"]


# --------------------------------------------------------------------------- #
# Model-config gating (no live call)
# --------------------------------------------------------------------------- #
def test_default_client_none_when_model_unconfigured():
    assert assistant._default_client({}) is None  # no RECAP_MODEL -> no client


def test_default_client_none_on_placeholder_model():
    recap = assistant._recap()
    assert assistant._default_client({"RECAP_MODEL": recap.RECAP_MODEL}) is None


# --------------------------------------------------------------------------- #
# Audit logging — every ask writes one row, best-effort, without breaking the answer.
# --------------------------------------------------------------------------- #
def test_ask_writes_audit_log_row_with_correct_fields(_capture_log):
    client = _FakeClient(synth="clawgate-agent-loop and taxes-archiver await you.")
    res = assistant.ask("what's blocked on me?", views=VIEWS, unmatched=UNMATCHED,
                        client=client)
    assert len(_capture_log) == 1
    row = _capture_log[0]
    assert row["question"] == "what's blocked on me?"
    assert row["intent"] == "blocked_on_me"
    assert row["answer"] == res["answer"]
    assert {s["slug"] for s in row["sources"]} == {"clawgate-agent-loop", "taxes-archiver"}
    assert row["used_model"] is True            # the fake client phrased the answer
    assert row["model"] is None                 # no RECAP_MODEL configured in the sandbox
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert row["host"]                          # a host tag is always present


def test_ask_log_records_intent_target_for_status_of(_capture_log):
    client = _FakeClient(synth="clawgate is awaiting the go-ahead")
    assistant.ask("status of clawgate", views=VIEWS, client=client)
    row = _capture_log[-1]
    assert row["intent"] == "status_of"
    assert row["target"] == "clawgate"


def test_ask_log_used_model_false_on_deterministic_answer(_capture_log):
    assistant.ask("what's blocked on me?", views=VIEWS, client=None,
                  client_factory=lambda: None)
    assert _capture_log[-1]["used_model"] is False


def test_ask_empty_question_is_not_logged(_capture_log):
    assistant.ask("   ", views=VIEWS)
    assert _capture_log == []                    # a degenerate ask records nothing


def test_ask_answer_intact_when_log_write_fails(monkeypatch):
    # A DB/log failure must NEVER propagate or change the answer (best-effort, non-breaking).
    def boom(row, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(assistant, "_write_log_row", boom)
    client = _FakeClient(synth="two things await you")
    res = assistant.ask("what's blocked on me?", views=VIEWS, client=client)
    assert res["ok"] is True
    assert res["answer"] == "two things await you"
    assert {s["slug"] for s in res["sources"]} == {"clawgate-agent-loop", "taxes-archiver"}


def test_ask_uses_injected_log_writer_over_default(_capture_log):
    captured = []
    assistant.ask("what's stalled?", views=VIEWS, client=None,
                  client_factory=lambda: None, log_writer=captured.append)
    # the injected writer wins; the module default (fixture capture) is untouched
    assert len(captured) == 1 and captured[0]["intent"] == "stalled"
    assert _capture_log == []


# --------------------------------------------------------------------------- #
# The audit-log table DDL + creation (standalone, idempotent, no view bump).
# --------------------------------------------------------------------------- #
def test_assistant_log_ddl_is_idempotent():
    ddl = assistant.ASSISTANT_LOG_DDL
    assert "CREATE TABLE IF NOT EXISTS initiatives.assistant_log" in ddl
    assert "CREATE SCHEMA IF NOT EXISTS initiatives" in ddl   # self-heal without the sync
    for col in ("question", "intent", "target", "sources", "answer", "model",
                "used_model", "latency_ms", "host"):
        assert col in ddl


def test_create_assistant_log_table_executes_ddl():
    executed = []

    class _Cur:
        def execute(self, sql, params=None):
            executed.append(sql)

    assistant.create_assistant_log_table(_Cur())
    assert any("initiatives.assistant_log" in s for s in executed)


# --------------------------------------------------------------------------- #
# Reading the audit log — the review subcommand.
# --------------------------------------------------------------------------- #
_LOG_ROW = {
    "ts": datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc),
    "question": "what's blocked on me?", "intent": "blocked_on_me", "target": "",
    "sources": [{"slug": "clawgate-agent-loop", "repo": "devrc"}],
    "answer": "clawgate-agent-loop awaits you.", "model": None, "used_model": True,
    "latency_ms": 42, "host": "workbench",
}


def test_format_log_renders_rows():
    out = assistant.format_log([_LOG_ROW])
    assert "blocked_on_me" in out
    assert "what's blocked on me?" in out
    assert "clawgate-agent-loop awaits you." in out
    assert "clawgate-agent-loop" in out          # cited source slug
    assert "model=yes" in out


def test_format_log_empty():
    assert "No assistant asks" in assistant.format_log([])


def test_read_log_uses_injected_reader():
    seen = {}

    def reader(limit):
        seen["limit"] = limit
        return [_LOG_ROW]

    rows = assistant.read_log(limit=5, reader=reader)
    assert seen["limit"] == 5
    assert rows[0]["question"] == "what's blocked on me?"


def test_main_log_subcommand_reads_and_formats(monkeypatch, capsys):
    monkeypatch.setattr(assistant, "_read_log_rows", lambda limit: [_LOG_ROW])
    rc = assistant.main(["log"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "blocked_on_me" in out and "what's blocked on me?" in out


def test_main_log_subcommand_json(monkeypatch, capsys):
    monkeypatch.setattr(assistant, "_read_log_rows", lambda limit: [_LOG_ROW])
    rc = assistant.main(["log", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)[0]["intent"] == "blocked_on_me"


# --------------------------------------------------------------------------- #
# recommend_next_step — the grounded next-step chat tool (Phase-2a).
# --------------------------------------------------------------------------- #
def test_classify_recommend_next_step_phrasings():
    for q in ("what should I work on next on clawgate",
              "what do I do next for clawgate",
              "recommend a next step for clawgate",
              "suggest next step on clawgate",
              "next step for clawgate"):
        assert assistant.classify_intent(q)["intent"] == "recommend_next_step", q


def test_classify_recommend_next_step_does_not_shadow_status_of():
    # "status of X" and "where did I leave X" must STILL classify as status_of, not the new
    # recommend intent (the recommend patterns are narrower and sit just before status_of).
    assert assistant.classify_intent("status of clawgate")["intent"] == "status_of"
    assert assistant.classify_intent(
        "where did I leave off with clawgate")["intent"] == "status_of"


def test_classify_recommend_extracts_target():
    info = assistant.classify_intent("what should I work on next on the clawgate agent loop")
    assert info["intent"] == "recommend_next_step"
    assert "clawgate" in info["target"]


def test_tool_recommend_next_step_found_grounds_on_handoff():
    res = assistant.tool_recommend_next_step(VIEWS, "clawgate")
    assert res["kind"] == "recommend_next_step"
    assert res["initiative"]["slug"] == "clawgate-agent-loop"
    # CLAWGATE has a next_step → basis handoff, text is the parsed step verbatim.
    assert res["recommendation"] == {"text": "cut over phase 7", "basis": "handoff"}


def test_tool_recommend_next_step_not_found():
    res = assistant.tool_recommend_next_step(VIEWS, "nonexistent-xyz")
    assert res["kind"] == "recommend_next_step"
    assert res["initiative"] is None
    assert res["recommendation"] is None


def test_run_tool_dispatches_recommend_next_step():
    res = assistant.run_tool("recommend_next_step", {"target": "clawgate"}, VIEWS, UNMATCHED)
    assert res["kind"] == "recommend_next_step"


def test_build_facts_recommend_next_step_found():
    res = assistant.tool_recommend_next_step(VIEWS, "clawgate")
    facts = assistant.build_facts(res)
    assert facts["kind"] == "recommend_next_step"
    assert facts["found"] is True
    assert facts["recommendation"] == {"text": "cut over phase 7", "basis": "handoff"}
    assert facts["initiative"]["slug"] == "clawgate-agent-loop"
    # grounded_context carries the REAL fields the model may phrase over.
    assert facts["grounded_context"]["status"].startswith("awaiting")
    assert facts["grounded_context"]["next_step"] == "cut over phase 7"
    json.dumps(facts, default=str)  # must be serializable


def test_build_facts_recommend_next_step_not_found():
    facts = assistant.build_facts(
        assistant.tool_recommend_next_step(VIEWS, "nonexistent-xyz"))
    assert facts["found"] is False
    assert facts["recommendation"] is None


def test_sources_recommend_next_step_cites_the_one_initiative():
    res = assistant.tool_recommend_next_step(VIEWS, "clawgate")
    srcs = assistant.sources_of(res)
    assert len(srcs) == 1
    assert srcs[0]["slug"] == "clawgate-agent-loop"


def test_sources_recommend_next_step_empty_when_not_found():
    res = assistant.tool_recommend_next_step(VIEWS, "nonexistent-xyz")
    assert assistant.sources_of(res) == []


def test_render_plain_recommend_next_step_found():
    res = assistant.tool_recommend_next_step(VIEWS, "clawgate")
    out = assistant.render_plain("recommend_next_step", {"target": "clawgate"}, res)
    assert "clawgate-agent-loop" in out
    assert "cut over phase 7" in out
    assert "from your handoff" in out  # basis hint


def test_render_plain_recommend_next_step_not_found():
    res = assistant.tool_recommend_next_step(VIEWS, "ghost")
    out = assistant.render_plain("recommend_next_step", {"target": "ghost"}, res)
    assert "don't have enough" in out
    assert "ghost" in out
