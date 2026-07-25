#!/usr/bin/env python3
"""Proxy the initiatives OpenClaw AGENT gateway for the viewer's `POST /api/ask`.

Phase 1 of the initiatives agent (`claudedocs/handoff-initiatives-agent-phase1-2026-07-24.md`).
The read-only agent devpod (homelab ns `devpod-initiatives`, svc `initiatives-devpod:18789`,
`openclaw/initiatives`, DeepSeek V4 Pro) does the intent-understanding + tool-SELECTION and
synthesizes a grounded answer; the deterministic `skills/query.py` tools do the store/route
query. This module lets the WORKBENCH viewer reach that homelab agent — the SAME cross-cluster
route it already uses for the store: `KUBECONFIG=$KC_HOMELAB` + a `kubectl port-forward`
(the DB is homelab-only; so is the agent).

Design (mirrors recap.VllmClient's port-forward + assistant.py's read-only/degrade discipline):
  - `agent_ask(question, *, views, env)` returns a result dict shaped like `assistant.ask`'s
    (`{ok, question, intent:"agent", answer, sources, used_model, target}`) — so the viewer's
    `/api/ask` handler and its `{ok, answer, sources, intent}` response contract are UNCHANGED.
  - On ANY failure (agent disabled, port-forward/gateway/timeout error, malformed reply) it
    returns **None** → the viewer FALLS BACK to the deterministic regex assistant
    (`default_ask`). The agent is an upgrade over that path, never a hard dependency: devpod
    down ⇒ the viewer still answers deterministically.
  - **`sources` are computed DETERMINISTICALLY** by slug-matching the answer text against the
    store's known slugs (the `views` the viewer already loaded) — never trusting the model to
    emit structured citations. Same anti-confabulation anchor as assistant.py.
  - **Read-only.** No write/dispatch/tool here — just a chat POST + a grounded projection.

Auth: the gateway token = `sha256("gw-" + HOOKS_TOKEN)` (the kubeclaw contract). `HOOKS_TOKEN`
is read at call time from the in-cluster secret `initiatives-agent-secrets` via kubectl — so
NO agent secret is stored on the workbench.

Audit: every agent ask is appended to `initiatives.assistant_log` by reusing assistant.py's
write path (the viewer holds full mailbox creds; the agent's least-privilege role cannot write
the log). Labeled `intent="agent"` + the agent model id, so the audit loop covers both paths.

Config (env; all optional — absent/false ⇒ agent disabled ⇒ viewer uses the regex assistant):
    INITIATIVES_AGENT_ENABLED   master switch (truthy)
    AGENT_BASE_URL              direct gateway URL (skip port-forward; e.g. a nebula/NodePort)
    AGENT_NAMESPACE             default devpod-initiatives
    AGENT_SERVICE               default svc/initiatives-devpod
    AGENT_PORT                  default 18789
    AGENT_MODEL                 default openclaw/initiatives
    AGENT_SECRET                default initiatives-agent-secrets (holds HOOKS_TOKEN)
    AGENT_HOOKS_TOKEN           override HOOKS_TOKEN directly (skip the kubectl secret read)
    AGENT_TIMEOUT               per-request seconds (default 180 — the agentic loop is slow)
    AGENT_READY_TIMEOUT         port-forward readiness seconds (default 20)
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_ASSISTANT_PATH = Path(__file__).resolve().parent / "assistant.py"
_CHAT_PATH = "/v1/chat/completions"

# Config defaults.
_DEF_NAMESPACE = "devpod-initiatives"
_DEF_SERVICE = "svc/initiatives-devpod"
_DEF_PORT = 18789
_DEF_MODEL = "openclaw/initiatives"
_DEF_SECRET = "initiatives-agent-secrets"
_DEF_TIMEOUT = 180.0
_DEF_READY_TIMEOUT = 20.0


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _assistant():
    spec = importlib.util.spec_from_file_location("initiatives_agent_assistant", _ASSISTANT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_ASSISTANT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def agent_config(env: dict | None = None) -> dict:
    """Resolve the agent gateway config from the environment (PURE w.r.t. `env`)."""
    env = os.environ if env is None else env
    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d
    def _float(v, d):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d
    return {
        "enabled": _truthy(env.get("INITIATIVES_AGENT_ENABLED")),
        "base_url": (env.get("AGENT_BASE_URL") or "").strip(),
        "namespace": (env.get("AGENT_NAMESPACE") or _DEF_NAMESPACE).strip(),
        "service": (env.get("AGENT_SERVICE") or _DEF_SERVICE).strip(),
        "port": _int(env.get("AGENT_PORT"), _DEF_PORT),
        "model": (env.get("AGENT_MODEL") or _DEF_MODEL).strip(),
        "secret": (env.get("AGENT_SECRET") or _DEF_SECRET).strip(),
        "hooks_token": (env.get("AGENT_HOOKS_TOKEN") or "").strip(),
        "timeout": _float(env.get("AGENT_TIMEOUT"), _DEF_TIMEOUT),
        "ready_timeout": _float(env.get("AGENT_READY_TIMEOUT"), _DEF_READY_TIMEOUT),
    }


# --------------------------------------------------------------------------- #
# Gateway auth token — sha256("gw-"+HOOKS_TOKEN), the kubeclaw contract.
# --------------------------------------------------------------------------- #
def gateway_token(hooks_token: str) -> str:
    return hashlib.sha256(("gw-" + (hooks_token or "")).encode("utf-8")).hexdigest()


def _read_hooks_token(cfg: dict) -> str:
    """HOOKS_TOKEN: an explicit override, else read from the in-cluster secret via kubectl
    (the workbench viewer runs with KUBECONFIG=$KC_HOMELAB). Raises on failure — the caller
    turns that into the None (fall-back-to-regex) path."""
    if cfg.get("hooks_token"):
        return cfg["hooks_token"]
    out = subprocess.check_output(
        ["kubectl", "-n", cfg["namespace"], "get", "secret", cfg["secret"],
         "-o", "jsonpath={.data.HOOKS_TOKEN}"],
        stderr=subprocess.DEVNULL, timeout=15,
    )
    tok = base64.b64decode(out.decode().strip()).decode().strip()
    if not tok:
        raise RuntimeError("HOOKS_TOKEN empty in secret")
    return tok


# --------------------------------------------------------------------------- #
# Gateway client — port-forward (or direct base_url) + one chat POST.
# --------------------------------------------------------------------------- #
def _free_local_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class AgentGateway:
    """Context manager: reach the OpenClaw gateway and `chat()` once.

    `base_url` set → POST directly (nebula/NodePort). Otherwise `kubectl port-forward` the
    service on an ephemeral local port for the call, torn down on exit (mirrors
    recap.VllmClient / _db.py)."""

    def __init__(self, cfg: dict, token: str):
        self._cfg = cfg
        self._token = token
        self._pf: subprocess.Popen | None = None
        self._url: str | None = None

    def __enter__(self) -> "AgentGateway":
        base = self._cfg.get("base_url")
        if base:
            self._url = base.rstrip("/") + _CHAT_PATH
            return self
        local = _free_local_port()
        self._pf = subprocess.Popen(
            ["kubectl", "-n", self._cfg["namespace"], "port-forward",
             self._cfg["service"], f"{local}:{self._cfg['port']}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self._wait_for_port("127.0.0.1", local)
        self._url = f"http://127.0.0.1:{local}{_CHAT_PATH}"
        return self

    def __exit__(self, *_exc) -> None:
        if self._pf is not None:
            self._pf.terminate()
            with contextlib.suppress(Exception):
                self._pf.wait(timeout=5)

    def _wait_for_port(self, host: str, port: int) -> None:
        deadline = time.monotonic() + self._cfg["ready_timeout"]
        while time.monotonic() < deadline:
            if self._pf and self._pf.poll() is not None:
                err = self._pf.stderr.read().decode() if self._pf.stderr else ""
                raise RuntimeError(f"kubectl port-forward exited early:\n{err}")
            with contextlib.suppress(OSError):
                with socket.create_connection((host, port), timeout=1):
                    return
            time.sleep(0.25)
        raise TimeoutError(f"agent gateway port-forward to {host}:{port} not ready in time")

    def chat(self, question: str) -> str:
        """POST one user turn; return the agent's answer text (choices[0].message.content)."""
        if not self._url:
            raise RuntimeError("AgentGateway used outside its context manager")
        body = json.dumps({
            "model": self._cfg["model"],
            "messages": [{"role": "user", "content": question}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._token}"},
        )
        with urllib.request.urlopen(req, timeout=self._cfg["timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"]["content"] or "").strip()


# --------------------------------------------------------------------------- #
# Deterministic sources — slug-match the answer against the store's known slugs.
# --------------------------------------------------------------------------- #
def _slug_at(text: str, slug: str) -> int:
    """First index at which `slug` appears in `text` as a WHOLE token (bounded by
    non-`[\\w-]` on both sides), or -1. A plain substring test would let a shorter slug
    (`clawgate`) falsely match inside a longer one (`clawgate-chat`), inventing a citation
    the model never named — so the boundary is load-bearing for grounding. `-` is treated
    as an in-token char so a hyphenated slug binds tightly."""
    for m in re.finditer(r"(?<![\w-])" + re.escape(slug) + r"(?![\w-])", text):
        return m.start()
    return -1


def extract_sources(answer: str, views: list[dict]) -> list[dict]:
    """PURE: which known initiative slugs appear in the answer → [{slug, repo}] (de-duped,
    first-appearance order). The model NEVER supplies the citation; we recover it from the
    store's own slug set via a WHOLE-TOKEN match, so `sources` stays grounded (and can't gain
    an initiative the model didn't name) even if the phrasing drifts."""
    text = (answer or "").lower()
    hits: list[tuple[int, dict]] = []
    seen: set = set()
    for v in views or []:
        slug = str(v.get("slug") or "")
        if not slug or slug in seen:
            continue
        pos = _slug_at(text, slug.lower())
        if pos >= 0:
            seen.add(slug)
            repo = v.get("repo_name") or os.path.basename(str(v.get("repo") or "").rstrip("/"))
            hits.append((pos, {"slug": slug, "repo": repo}))
    # first-appearance order for a stable, readable citation list.
    hits.sort(key=lambda h: h[0])
    return [h[1] for h in hits]


# --------------------------------------------------------------------------- #
# The one callable — best-effort; returns None to signal "fall back to the regex assistant".
# --------------------------------------------------------------------------- #
def agent_ask(question: str, *, views: list[dict] | None = None, env: dict | None = None,
              gateway_factory=None, log_writer=None) -> dict | None:
    """Ask the initiatives agent; return a result dict, or None on ANY failure (caller falls
    back to `default_ask`). Audit-logs the ask to `initiatives.assistant_log` best-effort.

    `gateway_factory(cfg, token) -> context-manager` and `log_writer(row)` are injectable for
    tests (no kubectl, no DB). Production uses the defaults."""
    cfg = agent_config(env)
    if not cfg["enabled"]:
        return None
    question = (question or "").strip()
    if not question:
        return None
    started = time.monotonic()
    try:
        token = gateway_token(_read_hooks_token(cfg))
        factory = gateway_factory or (lambda c, t: AgentGateway(c, t))
        with factory(cfg, token) as gw:
            answer = gw.chat(question)
    except Exception as exc:  # noqa: BLE001 - agent unreachable/slow/malformed → regex fallback
        sys.stderr.write(f"agent_client: agent_ask failed ({type(exc).__name__}: {exc}) "
                         f"— falling back to the deterministic assistant\n")
        return None
    if not answer:
        return None
    sources = extract_sources(answer, views or [])
    result = {"ok": True, "question": question, "intent": "agent", "target": "",
              "used_model": True, "answer": answer, "sources": sources}
    _log_agent_ask(result, question=question, started=started, model=cfg["model"],
                   env=env, log_writer=log_writer)
    return result


def _log_agent_ask(result: dict, *, question: str, started: float, model: str,
                   env, log_writer) -> None:
    """Append one agent ask to `initiatives.assistant_log`, reusing assistant.py's write path
    (idempotent table self-heal + insert). Best-effort: NEVER raises, never alters `result`."""
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    try:
        A = _assistant()
        row = {"question": question, "intent": result.get("intent", "agent"), "target": "",
               "sources": result.get("sources") or [], "answer": result.get("answer") or "",
               "model": model, "used_model": True, "latency_ms": latency_ms, "host": A._host()}
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"agent_client: audit-log build failed ({type(exc).__name__})\n")
        return
    try:
        A._stderr_note(row)
    except Exception:  # noqa: BLE001
        pass
    writer = log_writer or (lambda r: _assistant()._write_log_row(r))
    try:
        writer(row)
    except Exception as exc:  # noqa: BLE001 - DB down / table missing → drop the row, log it
        sys.stderr.write(f"agent_client: audit-log write skipped ({type(exc).__name__}: {exc})\n")
