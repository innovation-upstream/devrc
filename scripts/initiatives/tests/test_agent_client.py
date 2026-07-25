"""Unit tests for the initiatives AGENT client (scripts/initiatives/agent_client.py) and
the viewer's `build_asker` agent/fallback wiring (scripts/initiatives/viewer.py).

Hermetic: NO kubectl, NO live DB, NO network, NO subprocess. `agent_ask` is designed for
injection — `gateway_factory(cfg, token)` returns a fake context manager, `log_writer(row)`
captures the audit row in a list — so the real port-forward + DB write paths are never
touched. The pure pieces (`gateway_token`, `agent_config`, `extract_sources`) are exercised
directly over fixtures; `build_asker` is exercised with a minimal fake provider.

Loads the module-under-test by explicit path (mirrors test_query.py / test_assistant.py's
sys.path-insert convention), so nothing here imports psycopg2/requests transitively.

NOTE on `extract_sources` (see test_extract_sources_substring_*): a slug is cited only when
it appears in the answer as a WHOLE token (bounded by non-`[\\w-]`), so a short slug that is a
substring of a longer slug present in the answer is NOT falsely emitted (answer "clawgate-chat"
→ "clawgate-chat" only, never "clawgate"). This keeps the grounded citation set free of an
initiative the model never named.
"""
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_INIT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_INIT_DIR))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _INIT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agent_client = _load("initiatives_agent_client_under_test", "agent_client.py")


# --------------------------------------------------------------------------- #
# Fixtures — fake gateway + fixture views.
# --------------------------------------------------------------------------- #
VIEWS = [
    {"slug": "cutover-gate", "repo": "/home/zach/workspace/devrc"},
    {"slug": "sentinel-soak", "repo_name": "homelab"},
    {"slug": "quantum-sched", "repo": "/repo/devrc/"},
]


class FakeGateway:
    """Injectable stand-in for AgentGateway: a context manager whose `.chat()` returns a
    canned answer (or raises). Records enter/exit + the question so tests can assert the
    context-manager discipline (torn down even on the happy path)."""

    def __init__(self, *, answer=None, chat_raises=None, enter_raises=None):
        self._answer = answer
        self._chat_raises = chat_raises
        self._enter_raises = enter_raises
        self.entered = False
        self.exited = False
        self.asked = None

    def __enter__(self):
        if self._enter_raises is not None:
            raise self._enter_raises
        self.entered = True
        return self

    def __exit__(self, *_exc):
        self.exited = True
        return False

    def chat(self, question):
        self.asked = question
        if self._chat_raises is not None:
            raise self._chat_raises
        return self._answer


def _enabled_env(**over):
    """Env with the agent enabled and the kubectl secret read short-circuited via
    AGENT_HOOKS_TOKEN (so no subprocess is ever spawned)."""
    env = {"INITIATIVES_AGENT_ENABLED": "1", "AGENT_HOOKS_TOKEN": "t"}
    env.update(over)
    return env


# --------------------------------------------------------------------------- #
# 1. gateway_token — sha256("gw-"+token), the kubeclaw contract.
# --------------------------------------------------------------------------- #
def test_gateway_token_matches_independent_sha256():
    expected = hashlib.sha256(b"gw-abc").hexdigest()
    assert agent_client.gateway_token("abc") == expected


def test_gateway_token_handles_empty_and_none():
    empty = hashlib.sha256(b"gw-").hexdigest()
    assert agent_client.gateway_token("") == empty
    assert agent_client.gateway_token(None) == empty
    # deterministic + hex
    assert len(agent_client.gateway_token("abc")) == 64
    int(agent_client.gateway_token("abc"), 16)


# --------------------------------------------------------------------------- #
# 2. agent_config — env-driven, module-constant defaults, pure w.r.t. `env`.
# --------------------------------------------------------------------------- #
def test_agent_config_disabled_by_default():
    assert agent_client.agent_config({})["enabled"] is False
    for falsey in ("", "0", "false", "no", "off", "nope"):
        assert agent_client.agent_config(
            {"INITIATIVES_AGENT_ENABLED": falsey})["enabled"] is False


def test_agent_config_enabled_for_truthy_values():
    for truthy in ("1", "true", "TRUE", "yes", "on", " on "):
        cfg = agent_client.agent_config({"INITIATIVES_AGENT_ENABLED": truthy})
        assert cfg["enabled"] is True, truthy


def test_agent_config_defaults_match_module_constants():
    cfg = agent_client.agent_config({})
    assert cfg["namespace"] == agent_client._DEF_NAMESPACE
    assert cfg["service"] == agent_client._DEF_SERVICE
    assert cfg["port"] == agent_client._DEF_PORT
    assert cfg["model"] == agent_client._DEF_MODEL
    assert cfg["secret"] == agent_client._DEF_SECRET
    assert cfg["timeout"] == agent_client._DEF_TIMEOUT
    assert cfg["ready_timeout"] == agent_client._DEF_READY_TIMEOUT
    assert cfg["base_url"] == ""
    assert cfg["hooks_token"] == ""


def test_agent_config_reads_overrides_and_coerces_numbers():
    env = {
        "AGENT_BASE_URL": "http://gw:9/", "AGENT_NAMESPACE": "ns",
        "AGENT_SERVICE": "svc/foo", "AGENT_PORT": "1234", "AGENT_MODEL": "m/x",
        "AGENT_SECRET": "sec", "AGENT_HOOKS_TOKEN": "tok",
        "AGENT_TIMEOUT": "12.5", "AGENT_READY_TIMEOUT": "3",
    }
    cfg = agent_client.agent_config(env)
    assert cfg["base_url"] == "http://gw:9/"
    assert cfg["namespace"] == "ns"
    assert cfg["service"] == "svc/foo"
    assert cfg["port"] == 1234
    assert cfg["model"] == "m/x"
    assert cfg["secret"] == "sec"
    assert cfg["hooks_token"] == "tok"
    assert cfg["timeout"] == 12.5
    assert cfg["ready_timeout"] == 3.0


def test_agent_config_bad_numbers_fall_back_to_defaults():
    cfg = agent_client.agent_config({"AGENT_PORT": "notint", "AGENT_TIMEOUT": "x"})
    assert cfg["port"] == agent_client._DEF_PORT
    assert cfg["timeout"] == agent_client._DEF_TIMEOUT


# --------------------------------------------------------------------------- #
# 3. extract_sources — PURE, the anti-confabulation anchor.
# --------------------------------------------------------------------------- #
def test_extract_sources_returns_only_slugs_present_in_the_answer():
    out = agent_client.extract_sources(
        "we resumed sentinel-soak today; nothing else moved", VIEWS)
    assert out == [{"slug": "sentinel-soak", "repo": "homelab"}]


def test_extract_sources_repo_resolution():
    # repo_name wins verbatim; otherwise basename of `repo` (trailing slash stripped).
    out = agent_client.extract_sources("cutover-gate and quantum-sched", VIEWS)
    by_slug = {s["slug"]: s["repo"] for s in out}
    assert by_slug["cutover-gate"] == "devrc"      # basename of /home/zach/workspace/devrc
    assert by_slug["quantum-sched"] == "devrc"     # basename after rstrip("/")


def test_extract_sources_dedupes_repeated_mentions():
    out = agent_client.extract_sources(
        "cutover-gate cutover-gate cutover-gate",
        [{"slug": "cutover-gate", "repo": "/r/devrc"}])
    assert out == [{"slug": "cutover-gate", "repo": "devrc"}]


def test_extract_sources_preserves_first_appearance_order():
    # answer mentions quantum-sched before cutover-gate → that order is preserved.
    out = agent_client.extract_sources(
        "first quantum-sched, later cutover-gate", VIEWS)
    assert [s["slug"] for s in out] == ["quantum-sched", "cutover-gate"]


def test_extract_sources_empty_answer_or_views_yields_empty():
    assert agent_client.extract_sources("", VIEWS) == []
    assert agent_client.extract_sources(None, VIEWS) == []
    assert agent_client.extract_sources("cutover-gate", []) == []
    assert agent_client.extract_sources("cutover-gate", None) == []


def test_extract_sources_case_insensitive():
    out = agent_client.extract_sources("SENTINEL-SOAK is back", VIEWS)
    assert [s["slug"] for s in out] == ["sentinel-soak"]


def test_extract_sources_substring_only_longer_slug_present():
    """GROUNDING: when only the LONGER slug ('clawgate-chat') appears, the shorter slug
    ('clawgate') must NOT be emitted — the whole-token match prevents a substring
    false-positive from inventing a citation the model never named."""
    views = [
        {"slug": "clawgate", "repo": "/r/devrc"},
        {"slug": "clawgate-chat", "repo": "/r/devrc"},
    ]
    out = agent_client.extract_sources("we shipped clawgate-chat today", views)
    slugs = [s["slug"] for s in out]
    assert slugs == ["clawgate-chat"]  # only the slug actually named; no substring bleed


def test_extract_sources_substring_only_shorter_slug_present():
    # Complement: when only the SHORTER slug appears, the longer one is (correctly) absent.
    views = [
        {"slug": "clawgate", "repo": "/r/devrc"},
        {"slug": "clawgate-chat", "repo": "/r/devrc"},
    ]
    out = agent_client.extract_sources("clawgate is live", views)
    assert [s["slug"] for s in out] == ["clawgate"]


# --------------------------------------------------------------------------- #
# 4. agent_ask — success path (injected gateway + log_writer).
# --------------------------------------------------------------------------- #
def test_agent_ask_success_returns_grounded_result_and_logs_once():
    captured = []
    gw = FakeGateway(answer="Resolved by resuming sentinel-soak; quantum-sched is next.")
    result = agent_client.agent_ask(
        "what's blocked?", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)

    assert result is not None
    # Result contract (shaped like assistant.ask's, intent="agent").
    assert result["ok"] is True
    assert result["intent"] == "agent"
    assert result["used_model"] is True
    assert result["target"] == ""
    assert result["question"] == "what's blocked?"
    assert result["answer"] == "Resolved by resuming sentinel-soak; quantum-sched is next."
    assert set(result) == {"ok", "question", "intent", "target",
                           "used_model", "answer", "sources"}

    # sources were extracted DETERMINISTICALLY from the answer via the views.
    assert result["sources"] == agent_client.extract_sources(result["answer"], VIEWS)
    assert {s["slug"] for s in result["sources"]} == {"sentinel-soak", "quantum-sched"}

    # the fake gateway was driven as a context manager (entered, asked, torn down).
    assert gw.entered and gw.exited
    assert gw.asked == "what's blocked?"

    # exactly ONE audit row was captured.
    assert len(captured) == 1
    row = captured[0]
    assert row["intent"] == "agent"
    assert row["model"] == agent_client._DEF_MODEL
    assert row["used_model"] is True
    assert row["question"] == "what's blocked?"
    assert row["answer"] == result["answer"]
    assert row["sources"] == result["sources"]
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert row["target"] == ""
    assert isinstance(row["host"], str) and row["host"]


def test_agent_ask_uses_configured_model_in_result_and_log():
    captured = []
    gw = FakeGateway(answer="quantum-sched moving")
    result = agent_client.agent_ask(
        "status?", views=VIEWS, env=_enabled_env(AGENT_MODEL="openclaw/custom"),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    assert result is not None
    assert captured[0]["model"] == "openclaw/custom"


def test_agent_ask_passes_gateway_token_to_factory():
    captured_token = {}

    def factory(cfg, token):
        captured_token["token"] = token
        return FakeGateway(answer="quantum-sched")

    agent_client.agent_ask(
        "q", views=VIEWS, env=_enabled_env(AGENT_HOOKS_TOKEN="secret"),
        gateway_factory=factory, log_writer=[].append)
    assert captured_token["token"] == agent_client.gateway_token("secret")


# --------------------------------------------------------------------------- #
# 5. agent_ask — failure / fallback signals → return None (viewer falls back).
# --------------------------------------------------------------------------- #
def test_agent_ask_disabled_returns_none_without_logging():
    captured = []
    gw = FakeGateway(answer="cutover-gate")
    result = agent_client.agent_ask(
        "q", views=VIEWS, env={"INITIATIVES_AGENT_ENABLED": "0"},
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    assert result is None
    assert captured == []          # no audit row when the agent never ran
    assert gw.entered is False     # gateway never constructed/entered


def test_agent_ask_gateway_factory_raising_returns_none_no_log():
    captured = []

    def boom_factory(cfg, token):
        raise ConnectionError("port-forward failed")

    result = agent_client.agent_ask(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=boom_factory, log_writer=captured.append)
    assert result is None
    assert captured == []


def test_agent_ask_chat_raising_returns_none_no_log():
    captured = []
    gw = FakeGateway(chat_raises=TimeoutError("agent slow"))
    result = agent_client.agent_ask(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    assert result is None
    assert captured == []
    assert gw.exited is True        # context manager still torn down on chat error


def test_agent_ask_empty_answer_returns_none_no_log():
    captured = []
    gw = FakeGateway(answer="")
    result = agent_client.agent_ask(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    assert result is None
    assert captured == []


@pytest.mark.parametrize("question", ["", "   ", "\n\t", None])
def test_agent_ask_blank_question_returns_none_no_gateway(question):
    captured = []
    gw = FakeGateway(answer="cutover-gate")
    result = agent_client.agent_ask(
        question, views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    assert result is None
    assert captured == []
    assert gw.entered is False


def test_agent_ask_no_exception_propagates_on_any_failure():
    # Belt-and-suspenders: none of the failure signals should raise out of agent_ask.
    for gw in (FakeGateway(enter_raises=RuntimeError("x")),
               FakeGateway(chat_raises=ValueError("y")),
               FakeGateway(answer="")):
        assert agent_client.agent_ask(
            "q", views=VIEWS, env=_enabled_env(),
            gateway_factory=lambda cfg, token: gw, log_writer=[].append) is None


# --------------------------------------------------------------------------- #
# 6. Audit-log is best-effort — a raising log_writer must NOT break agent_ask.
# --------------------------------------------------------------------------- #
def test_agent_ask_log_writer_raising_does_not_break_result():
    def bad_writer(row):
        raise RuntimeError("DB down")

    result = agent_client.agent_ask(
        "what's next?", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: FakeGateway(answer="quantum-sched next"),
        log_writer=bad_writer)
    assert result is not None
    assert result["ok"] is True
    assert result["answer"] == "quantum-sched next"


# --------------------------------------------------------------------------- #
# 7. viewer.build_asker — agent-disabled → None; agent-None → default_ask fallback.
# --------------------------------------------------------------------------- #
viewer = _load("initiatives_viewer_under_test", "viewer.py")


class _FakeProvider:
    """Minimal provider: snapshot() -> (model, error). model.flat are the views the asker
    hands to the agent for grounding."""

    def __init__(self, flat=None):
        self._flat = flat if flat is not None else [{"slug": "cutover-gate",
                                                     "repo": "/r/devrc"}]

    def snapshot(self):
        return {"flat": self._flat}, None


def test_build_asker_returns_none_when_agent_disabled():
    # Agent disabled → build_asker returns None so make_handler uses default_ask.
    asker = viewer.build_asker(_FakeProvider(), env={"INITIATIVES_AGENT_ENABLED": "0"})
    assert asker is None


def test_build_asker_falls_back_to_default_ask_when_agent_returns_none(monkeypatch):
    # Agent ENABLED but the agent path returns None (devpod down) → the asker must fall
    # back to default_ask. We patch the LOADED agent module's agent_ask to return None and
    # viewer.default_ask to a sentinel, then assert the sentinel is called with the question.
    prov = _FakeProvider(flat=[{"slug": "quantum-sched", "repo": "/r/devrc"}])

    class _StubAgent:
        @staticmethod
        def agent_config(env):
            return {"enabled": True}

        @staticmethod
        def agent_ask(question, *, views=None, env=None):
            return None  # agent unreachable → signal fallback

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())

    called = {}

    def _sentinel_default_ask(question, provider):
        called["question"] = question
        called["provider"] = provider
        return {"ok": True, "answer": "fallback", "sources": [], "intent": "overview"}

    monkeypatch.setattr(viewer, "default_ask", _sentinel_default_ask)

    asker = viewer.build_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    assert asker is not None
    res = asker("what's next?")
    assert res["answer"] == "fallback"
    assert called["question"] == "what's next?"
    assert called["provider"] is prov


def test_build_asker_returns_agent_result_when_agent_answers(monkeypatch):
    # When the agent DOES answer, the asker returns the agent result and does NOT fall back.
    prov = _FakeProvider(flat=[{"slug": "quantum-sched", "repo": "/r/devrc"}])
    seen = {}

    class _StubAgent:
        @staticmethod
        def agent_config(env):
            return {"enabled": True}

        @staticmethod
        def agent_ask(question, *, views=None, env=None):
            seen["views"] = views
            return {"ok": True, "answer": "agent answer", "sources": [], "intent": "agent"}

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())
    monkeypatch.setattr(viewer, "default_ask",
                        lambda *a, **k: pytest.fail("default_ask must not be called"))

    asker = viewer.build_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    res = asker("q")
    assert res["intent"] == "agent" and res["answer"] == "agent answer"
    # the asker threaded the provider's cached flat views into the agent for grounding.
    assert [v["slug"] for v in seen["views"]] == ["quantum-sched"]


def test_build_asker_falls_back_when_agent_ask_raises(monkeypatch):
    # A raising agent_ask must never break /api/ask — the asker degrades to default_ask.
    prov = _FakeProvider()

    class _StubAgent:
        @staticmethod
        def agent_config(env):
            return {"enabled": True}

        @staticmethod
        def agent_ask(question, *, views=None, env=None):
            raise RuntimeError("agent blew up")

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())
    monkeypatch.setattr(viewer, "default_ask",
                        lambda q, p: {"ok": True, "answer": "fallback", "intent": "overview",
                                      "sources": []})

    asker = viewer.build_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    assert asker("q")["answer"] == "fallback"
