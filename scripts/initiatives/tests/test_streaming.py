"""Unit tests for the STREAMING Q&A path — `agent_client.chat_stream` / `agent_client.agent_stream`
and the viewer's `build_stream_asker` wiring + the `mdToHtml` markdown renderer.

Hermetic: NO kubectl, NO live DB, NO network, NO subprocess (except the optional node eval of
the pure `mdToHtml` snippet, which SKIPS when node is absent). `chat_stream` is exercised by
monkeypatching `urllib.request.urlopen` to a fake iterable SSE response; `agent_stream` is driven
with an injected `gateway_factory` whose fake context-manager `chat_stream()` yields pieces and a
`log_writer` list captures the single audit row. `build_stream_asker` is exercised with a minimal
fake provider + a stubbed loaded agent module (mirrors test_agent_client.py's build_asker tests).
"""
import importlib.util
import json
import shutil
import subprocess
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


agent_client = _load("initiatives_agent_client_stream_test", "agent_client.py")
viewer = _load("initiatives_viewer_stream_test", "viewer.py")


VIEWS = [
    {"slug": "sentinel-soak", "repo_name": "homelab"},
    {"slug": "quantum-sched", "repo": "/repo/devrc/"},
]


def _enabled_env(**over):
    env = {"INITIATIVES_AGENT_ENABLED": "1", "AGENT_HOOKS_TOKEN": "t"}
    env.update(over)
    return env


# --------------------------------------------------------------------------- #
# 1. AgentGateway.chat_stream — parse an OpenAI SSE line stream into delta pieces.
# --------------------------------------------------------------------------- #
class _FakeResp:
    """Stand-in for the urlopen response: a context manager that iterates SSE byte-lines."""

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def _gw_with_url(url="http://127.0.0.1:9/v1/chat/completions"):
    gw = agent_client.AgentGateway({"model": "m", "timeout": 5.0}, "tok")
    gw._url = url
    return gw


def test_chat_stream_yields_delta_pieces_and_stops_on_done(monkeypatch):
    lines = [
        b'data: {"choices":[{"delta":{"content":"He"}}]}\n',
        b"\n",                                   # blank keep-alive -> skipped
        b": comment heartbeat\n",                # SSE comment (no data:) -> skipped
        b"garbage not-a-data-line\n",            # unparseable -> skipped
        b'data: {"choices":[{"delta":{}}]}\n',   # empty delta -> skipped
        b'data: {"choices":[{"delta":{"content":"llo"}}]}\n',
        b'data: {bad json\n',                    # malformed json -> skipped, stream continues
        b"data: [DONE]\n",                       # terminates
        b'data: {"choices":[{"delta":{"content":"AFTER"}}]}\n',  # never reached
    ]
    monkeypatch.setattr(agent_client.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(lines))
    pieces = list(_gw_with_url().chat_stream("hi"))
    assert pieces == ["He", "llo"]


def test_chat_stream_posts_stream_true_body(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp([b"data: [DONE]\n"])

    monkeypatch.setattr(agent_client.urllib.request, "urlopen", fake_urlopen)
    list(_gw_with_url().chat_stream("what's up"))
    assert captured["body"]["stream"] is True
    assert captured["body"]["messages"] == [{"role": "user", "content": "what's up"}]
    assert captured["auth"] == "Bearer tok"


def test_chat_stream_outside_context_raises():
    gw = agent_client.AgentGateway({"model": "m", "timeout": 5.0}, "tok")
    with pytest.raises(RuntimeError):
        list(gw.chat_stream("q"))


# --------------------------------------------------------------------------- #
# 2. agent_stream — injected fake gateway; delta frames then a grounded done frame.
# --------------------------------------------------------------------------- #
class FakeStreamGateway:
    """Injectable stand-in for AgentGateway used as a streaming context manager. Records
    enter/exit + the question so tests can assert the lifecycle is closed even on early abandon."""

    def __init__(self, *, pieces=None, enter_raises=None, chat_raises=None):
        self._pieces = pieces if pieces is not None else []
        self._enter_raises = enter_raises
        self._chat_raises = chat_raises
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

    def chat_stream(self, question):
        self.asked = question
        if self._chat_raises is not None:
            raise self._chat_raises
        for p in self._pieces:
            yield p


def test_agent_stream_yields_deltas_then_grounded_done_and_logs_once():
    captured = []
    gw = FakeStreamGateway(pieces=["sentinel-soak resumed; ", "quantum-sched next"])
    gen = agent_client.agent_stream(
        "what's blocked?", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    assert gen is not None
    frames = list(gen)

    # delta frames first, in order, then exactly one done frame.
    assert frames[:-1] == [{"delta": "sentinel-soak resumed; "}, {"delta": "quantum-sched next"}]
    done = frames[-1]
    assert done["done"] is True
    assert done["answer"] == "sentinel-soak resumed; quantum-sched next"
    # sources are extracted DETERMINISTICALLY from the concatenated answer via the views.
    assert done["sources"] == agent_client.extract_sources(done["answer"], VIEWS)
    assert {s["slug"] for s in done["sources"]} == {"sentinel-soak", "quantum-sched"}

    # the gateway was entered (eagerly) and torn down (in the generator's finally).
    assert gw.entered and gw.exited
    assert gw.asked == "what's blocked?"

    # exactly ONE audit row, shaped like agent_ask's.
    assert len(captured) == 1
    row = captured[0]
    assert row["intent"] == "agent" and row["used_model"] is True
    assert row["question"] == "what's blocked?"
    assert row["answer"] == done["answer"]
    assert row["sources"] == done["sources"]
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0


def test_agent_stream_closes_gateway_when_consumer_abandons_early():
    gw = FakeStreamGateway(pieces=["a", "b", "c"])
    gen = agent_client.agent_stream(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=[].append)
    assert next(gen) == {"delta": "a"}   # consume one, then abandon
    gen.close()                          # GeneratorExit → finally must still run
    assert gw.exited is True


def test_agent_stream_empty_answer_yields_done_without_logging():
    captured = []
    gw = FakeStreamGateway(pieces=[])    # no content at all
    gen = agent_client.agent_stream(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append)
    frames = list(gen)
    assert frames == [{"done": True, "sources": [], "answer": ""}]
    assert captured == []                # empty stream is NOT audit-logged
    assert gw.exited is True


def test_agent_stream_disabled_returns_none():
    gw = FakeStreamGateway(pieces=["x"])
    assert agent_client.agent_stream(
        "q", views=VIEWS, env={"INITIATIVES_AGENT_ENABLED": "0"},
        gateway_factory=lambda cfg, token: gw, log_writer=[].append) is None
    assert gw.entered is False


@pytest.mark.parametrize("question", ["", "   ", "\n\t", None])
def test_agent_stream_blank_question_returns_none(question):
    gw = FakeStreamGateway(pieces=["x"])
    assert agent_client.agent_stream(
        question, views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=[].append) is None
    assert gw.entered is False


def test_agent_stream_setup_failure_returns_none():
    # factory raising (port-forward down) → None (not a mid-stream crash).
    def boom(cfg, token):
        raise ConnectionError("port-forward failed")

    assert agent_client.agent_stream(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=boom, log_writer=[].append) is None


def test_agent_stream_enter_raising_returns_none():
    gw = FakeStreamGateway(enter_raises=RuntimeError("enter blew up"))
    assert agent_client.agent_stream(
        "q", views=VIEWS, env=_enabled_env(),
        gateway_factory=lambda cfg, token: gw, log_writer=[].append) is None


def test_agent_stream_uses_configured_model_in_log():
    captured = []
    gw = FakeStreamGateway(pieces=["quantum-sched moving"])
    list(agent_client.agent_stream(
        "status?", views=VIEWS, env=_enabled_env(AGENT_MODEL="openclaw/custom"),
        gateway_factory=lambda cfg, token: gw, log_writer=captured.append))
    assert captured[0]["model"] == "openclaw/custom"


# --------------------------------------------------------------------------- #
# 3. viewer.build_stream_asker — agent path, fallback path, empty-agent path.
# --------------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self, flat=None):
        self._flat = flat if flat is not None else [{"slug": "quantum-sched", "repo": "/r/devrc"}]

    def snapshot(self):
        return {"flat": self._flat}, None


def test_build_stream_asker_returns_asker_even_when_agent_disabled():
    # Unlike build_asker, this is returned UNCONDITIONALLY — the fallback always answers.
    sa = viewer.build_stream_asker(_FakeProvider(), env={"INITIATIVES_AGENT_ENABLED": "0"})
    assert sa is not None


def test_build_stream_asker_streams_agent_frames(monkeypatch):
    prov = _FakeProvider(flat=[{"slug": "quantum-sched", "repo": "/r/devrc"}])
    seen = {}

    class _StubAgent:
        @staticmethod
        def agent_stream(question, *, views=None, env=None):
            seen["views"] = views
            def _gen():
                yield {"delta": "quantum-"}
                yield {"delta": "sched"}
                yield {"done": True, "sources": [{"slug": "quantum-sched", "repo": "devrc"}],
                       "answer": "quantum-sched"}
            return _gen()

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())
    monkeypatch.setattr(viewer, "default_ask",
                        lambda *a, **k: pytest.fail("default_ask must not be called"))

    sa = viewer.build_stream_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    frames = list(sa("q"))
    assert frames == [
        {"delta": "quantum-"},
        {"delta": "sched"},
        {"done": True, "sources": [{"slug": "quantum-sched", "repo": "devrc"}],
         "answer": "quantum-sched"},
    ]
    # the provider's cached flat views were threaded into the agent for grounding.
    assert [v["slug"] for v in seen["views"]] == ["quantum-sched"]


def test_build_stream_asker_falls_back_when_agent_stream_none(monkeypatch):
    prov = _FakeProvider()

    class _StubAgent:
        @staticmethod
        def agent_stream(question, *, views=None, env=None):
            return None   # agent disabled / unreachable

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())
    monkeypatch.setattr(viewer, "default_ask",
                        lambda q, p: {"ok": True, "answer": "fallback answer",
                                      "sources": [{"slug": "x"}], "intent": "overview"})

    sa = viewer.build_stream_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    frames = list(sa("q"))
    assert frames == [
        {"delta": "fallback answer"},
        {"done": True, "sources": [{"slug": "x"}], "answer": "fallback answer",
         "intent": "overview"},
    ]


def test_build_stream_asker_falls_back_on_empty_agent_answer(monkeypatch):
    prov = _FakeProvider()

    class _StubAgent:
        @staticmethod
        def agent_stream(question, *, views=None, env=None):
            def _gen():
                yield {"done": True, "sources": [], "answer": ""}   # empty agent answer
            return _gen()

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())
    monkeypatch.setattr(viewer, "default_ask",
                        lambda q, p: {"ok": True, "answer": "deterministic",
                                      "sources": [], "intent": "overview"})

    sa = viewer.build_stream_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    frames = list(sa("q"))
    assert frames == [
        {"delta": "deterministic"},
        {"done": True, "sources": [], "answer": "deterministic", "intent": "overview"},
    ]


def test_build_stream_asker_falls_back_when_agent_stream_raises(monkeypatch):
    prov = _FakeProvider()

    class _StubAgent:
        @staticmethod
        def agent_stream(question, *, views=None, env=None):
            raise RuntimeError("agent blew up")

    monkeypatch.setattr(viewer, "_agent_client", lambda: _StubAgent())
    monkeypatch.setattr(viewer, "default_ask",
                        lambda q, p: {"ok": True, "answer": "safe", "sources": [],
                                      "intent": "overview"})

    sa = viewer.build_stream_asker(prov, env={"INITIATIVES_AGENT_ENABLED": "1"})
    frames = list(sa("q"))
    assert frames[0] == {"delta": "safe"}
    assert frames[-1]["done"] is True and frames[-1]["answer"] == "safe"


# --------------------------------------------------------------------------- #
# 4. mdToHtml — the XSS-safe markdown renderer (node eval of the pure snippet).
# --------------------------------------------------------------------------- #
def _node_md(src):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH — mdToHtml JS untested this run")
    body = "console.log(mdToHtml(%s));" % json.dumps(src)
    out = subprocess.run([node, "-e", viewer._MARKDOWN_JS + "\n" + body],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_mdtohtml_bold_and_inline_code():
    assert "<strong>x</strong>" in _node_md("**x**")
    assert "<code>x</code>" in _node_md("`x`")


def test_mdtohtml_escapes_html_first_no_raw_tag():
    out = _node_md("<script>alert(1)</script>")
    assert "<script>" not in out              # the raw tag never survives
    assert "&lt;script&gt;" in out


def test_mdtohtml_only_http_links_become_anchors():
    # a javascript: URL is NOT turned into an anchor — stays inert (escaped) text.
    js = _node_md("[l](javascript:alert(1))")
    assert "<a " not in js
    http = _node_md("[ok](https://example.com)")
    assert '<a href="https://example.com" target="_blank" rel="noopener">ok</a>' in http


def test_mdtohtml_lists_and_headings():
    out = _node_md("- a\n- b")
    assert "<ul>" in out and "<li>a</li>" in out and "<li>b</li>" in out
    assert "<h4>Head</h4>" in _node_md("# Head")
    ol = _node_md("1. one\n2. two")
    assert "<ol>" in ol and "<li>one</li>" in ol


def test_mdtohtml_fenced_code_content_is_escaped():
    out = _node_md("```\ncode<>&\n```")
    assert "<pre><code>" in out
    assert "code&lt;&gt;&amp;" in out         # content inside the code block is escaped too


def test_markdown_js_substituted_into_page():
    assert "__MARKDOWN_JS__" not in viewer._JS
    assert "function mdToHtml(" in viewer._JS
