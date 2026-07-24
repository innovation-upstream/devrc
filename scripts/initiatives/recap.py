#!/usr/bin/env python3
"""LLM recap generation for the initiatives store (Phase B2).

The deterministic `summary` line parsed from each handoff is often still not legible
enough, and a SINGLE synthesized recap over-weighted the user's *recent prompts* — so a
recent, tangential workstream (e.g. "cloudflare reliance") could get mistaken for the
project's fundamental purpose. Phase B therefore splits the recap into two independently
sourced + independently cached fields:

  * IDENTITY — "what this project fundamentally IS" (STABLE). Sourced from the handoff
    doc itself (its title + opening description + first durable-description section — the
    "identity blob"), NOT from recent prompts. Cache key = hash of the identity blob (+
    the identity-prompt fingerprint + model), so it regenerates ONLY when the handoff's
    description changes — it does NOT churn as the user prompts. This is the core fix.
  * STATUS — "what's happening now" (VOLATILE). Sourced from recent activity
    (recent_messages / recent_commits / next_step / momentum / open_prs /
    open_investigations), NOT the handoff blob. Cache key = hash of those activity fields
    (+ the status-prompt fingerprint + model), so it tracks activity change.

Two separate model calls, two separate cache keys: identity stays cached across prompt
churn while status tracks activity, and vice-versa.

Division of responsibility (mirrors sync.py / viewer.py):
  * PURE logic — `identity_blob` (durable-head extraction from a handoff), the two
    contexts (`identity_context` / `status_context`), the two hashes (`identity_hash` /
    `status_hash`, stable + order-independent), and the two prompt builders. No I/O;
    unit-tested directly with fixtures.
  * I/O — `read_identity_blob` (the size-capped, traversal-guarded on-box handoff read),
    `VllmClient` (the OpenAI-compatible HTTP call over a kubectl port-forward), the
    `initiatives.recaps` table DDL + `fetch_recaps` / `upsert_recap`, and the best-effort
    orchestration (`sync_recaps` / `maybe_sync_recaps`).

The "cached, regenerate-on-change" contract (PER FIELD): for each initiative we hash the
identity blob and (separately) the activity fields. Each field is regenerated ONLY when
its hash differs from the stored one (or it's missing). A field whose hash matches is
REUSED with no model call, so identity survives prompt churn and status survives handoff
edits. This bounds cost to actual change and keeps the two independent.

Best-effort / non-breaking (CRITICAL): the vLLM endpoint being down / slow / erroring, or
a handoff read failing, must NEVER break the sync or the snapshot write. Every generation
and read is wrapped in try/except + a timeout; on failure we keep the existing cached
value (last-good) — or, for identity with no cache, the viewer falls back to the
deterministic `summary` — and continue. `maybe_sync_recaps` rolls the connection back so
the UNCONDITIONAL `write_snapshot` that follows is unaffected.

Configuration (endpoint / model / namespace / service) is all env-overridable with
PLACEHOLDER defaults. Recap generation is OFF until `INITIATIVES_RECAP_ENABLED` is truthy,
so the sync stays a no-op (beyond ensuring the recaps table exists) until the model
service is real. See `recap_config()` for the full list.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuration — env-overridable with PLACEHOLDER defaults.
# --------------------------------------------------------------------------- #
#   INITIATIVES_RECAP_ENABLED  — master switch (truthy → generate). Default OFF.
#   RECAP_BASE_URL             — if set (e.g. a NodePort/nebula URL like the ClickHouse
#                                reader), call it DIRECTLY and skip the port-forward.
#   RECAP_NAMESPACE            — k8s namespace of the vLLM service.
#   RECAP_SERVICE              — k8s service ref for kubectl port-forward.
#   RECAP_SERVICE_PORT         — the service's OpenAI-API port.
#   RECAP_MODEL                — the served model id (OpenAI `model`).
#   RECAP_TIMEOUT              — per-call HTTP timeout (seconds).
RECAP_NAMESPACE = "vllm"                 # PLACEHOLDER — set to the real namespace
RECAP_SERVICE = "svc/vllm"               # PLACEHOLDER — set to the real service ref
RECAP_SERVICE_PORT = 8000                # vLLM OpenAI server default; override if B1 differs
RECAP_MODEL = "PLACEHOLDER-MODEL-ID"     # PLACEHOLDER — set to the served model id
RECAP_TIMEOUT = 30.0
RECAP_MAX_TOKENS = 160                   # a 1-2 sentence recap is well under this
RECAP_TEMPERATURE = 0.2                  # low — we want faithful, not creative
# The OpenAI-compatible chat-completions path (stable across vLLM / OpenAI).
RECAP_CHAT_PATH = "/v1/chat/completions"

# Only the N most-recent prompts / commits feed the status hash + prompt (older ones don't
# change "what's happening now" and would churn the hash needlessly).
RECAP_MAX_MESSAGES = 6
RECAP_MAX_COMMITS = 6

# The identity blob is the handoff HEAD (title + opening description + first durable
# section). Cap it so the local model's context isn't blown; a couple thousand chars is
# plenty of "what this is". The on-box read is separately byte-bounded so a pathological
# file can't spike memory before we even trim.
IDENTITY_MAX_CHARS = 2400
IDENTITY_MAX_DOC_BYTES = 512 * 1024


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def recap_config(env: dict | None = None) -> dict:
    """Resolve the recap config from the environment (PLACEHOLDER defaults otherwise).

    PURE w.r.t. `env` (defaults to os.environ). `enabled` is the master switch; when
    False the sync only ensures the recaps table exists and does zero model work."""
    env = os.environ if env is None else env
    return {
        "enabled": _truthy(env.get("INITIATIVES_RECAP_ENABLED")),
        "base_url": (env.get("RECAP_BASE_URL") or "").strip(),
        "namespace": (env.get("RECAP_NAMESPACE") or RECAP_NAMESPACE).strip(),
        "service": (env.get("RECAP_SERVICE") or RECAP_SERVICE).strip(),
        "service_port": _int(env.get("RECAP_SERVICE_PORT"), RECAP_SERVICE_PORT),
        "model": (env.get("RECAP_MODEL") or RECAP_MODEL).strip(),
        "timeout": _float(env.get("RECAP_TIMEOUT"), RECAP_TIMEOUT),
    }


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# The prompts — anti-confabulation discipline (mirrors session_insight/schema.py).
# --------------------------------------------------------------------------- #
# The model works from the PROVIDED context ONLY and may not invent counts, dates, PR
# numbers, or a status the inputs don't support. The output is the substance itself
# (no "this initiative…" meta), present tense, terse.
ANTI_CONFABULATION_CONTRACT = (
    "ANTI-CONFABULATION CONTRACT. Write ONLY from the context provided below. "
    "You MUST NOT invent or guess any count, date, PR number, name, or status that is "
    "not present in that context (if the number of commits/PRs/sessions isn't given, "
    "do not state one — there is no metric to cite). Do NOT restate raw counts as if "
    "you tallied them. If the context is too thin to say anything, write a single honest "
    "clause from whatever IS present rather than fabricating detail."
)

# Shared style/anti-doc-meta contract. Especially important for IDENTITY, which is fed the
# handoff doc verbatim — the recap is about the feature/system, NEVER about the document.
_STYLE_CONTRACT = (
    "Describe the WORK ITSELF, never the paperwork about it. You MUST NEVER mention or "
    "describe a handoff doc, a resume/canonical doc, a markdown or notes file, any "
    "filename, the word \"supersedes\", or the existence of documentation — the recap is "
    "about the feature / system, NOT about any document that tracks it. "
    "Requirements: present tense; terse and concrete; describe the substance directly — "
    "do NOT open with meta like \"This initiative…\" / \"The goal is…\"; no preamble, no "
    "bullet points, no markdown, no quotes around the output. Return ONLY the text."
)

# IDENTITY — "what this project fundamentally IS" (from the durable handoff description).
IDENTITY_INSTRUCTIONS = (
    "You describe what one software project/initiative fundamentally IS for a status "
    "board. From the provided handoff description, state in ONE to TWO sentences what "
    "this project/initiative fundamentally is — its purpose and what it does. "
    "Do NOT describe a recent, temporary, or tangential workstream as if it were the "
    "project's purpose; capture the enduring purpose of the system, not this week's task. "
)
IDENTITY_SYSTEM_PROMPT = (
    IDENTITY_INSTRUCTIONS + _STYLE_CONTRACT + "\n\n" + ANTI_CONFABULATION_CONTRACT
)

# STATUS — "what's happening now" (from recent activity only).
STATUS_INSTRUCTIONS = (
    "You describe the CURRENT status of one software initiative for a status board. "
    "From the provided recent activity, state in ONE sentence the current status of the "
    "work — what is in progress, recently done, or blocked. "
    "Do NOT restate what the project is or its overall purpose (that is covered "
    "separately); describe only the current state of the work. "
)
STATUS_SYSTEM_PROMPT = (
    STATUS_INSTRUCTIONS + _STYLE_CONTRACT + "\n\n" + ANTI_CONFABULATION_CONTRACT
)


def _fingerprint(text: str) -> str:
    """A short, deterministic sha256 prefix of a prompt — folded into the field's hash so
    ANY edit to the prompt busts that field's cache and the next sync regenerates it under
    the new prompt (no manual version-bump to remember). A prefix is fine for a cache key
    — full 256-bit collision resistance is irrelevant."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_IDENTITY_PROMPT_FINGERPRINT = _fingerprint(IDENTITY_SYSTEM_PROMPT)
_STATUS_PROMPT_FINGERPRINT = _fingerprint(STATUS_SYSTEM_PROMPT)


# --------------------------------------------------------------------------- #
# PURE — the identity blob (durable "what this IS" head of a handoff doc).
# --------------------------------------------------------------------------- #
# Volatile section headings we STOP at: everything from these down is "what's happening
# now" (Status / Next steps / Open investigations / …), which belongs to STATUS, not
# identity. Durable headings (## Goal / ## Overview / ## Current live state / ## Background
# / ## Architecture …) do NOT match, so their bodies are kept in the blob.
_VOLATILE_HEADING_RE = re.compile(
    r"^\s*#{2,6}\s+("
    r"status|next\s+steps?|open\s+investigation|progress|changelog|recent\b|todo|"
    r"gotcha|notes?\b|history|log\b|timeline|updates?\b|open\s+questions?"
    r")",
    re.IGNORECASE,
)


def identity_blob(text: str, max_chars: int = IDENTITY_MAX_CHARS) -> str:
    """PURE: a handoff doc's text -> the durable "what this IS" blob.

    Keeps the document HEAD — the `# ` title, the opening description (e.g. a `**Goal:**`
    line or the first prose paragraph), and any durable-description sections — UP TO the
    first VOLATILE status-type heading (`## Status` / `## Next steps` / `## Open
    investigations` …), then caps at `max_chars`. This is deliberately independent of the
    user's recent prompts, so identity does NOT churn as they work — only a genuine edit
    to the handoff's description changes it. Never raises; "" for empty/whitespace text."""
    if not text or not text.strip():
        return ""
    kept: list[str] = []
    seen_content = False
    for line in text.splitlines():
        if _VOLATILE_HEADING_RE.match(line):
            # Stop at the first volatile section — but only once we've captured some
            # durable content (guards a doc that opens straight into a status heading:
            # keep scanning for the real description instead of returning nothing).
            if seen_content:
                break
            continue
        kept.append(line)
        if line.strip():
            seen_content = True
    blob = "\n".join(kept).strip()
    blob = re.sub(r"\n{3,}", "\n\n", blob)  # collapse runs of blank lines
    return blob[:max_chars].strip()


# --------------------------------------------------------------------------- #
# PURE — contexts + hashes for the two fields (kept independent by construction).
# --------------------------------------------------------------------------- #
def identity_context(blob: str) -> dict:
    """PURE: the identity blob -> the identity context (the ONLY thing identity sees /
    hashes). Deliberately excludes recent_messages so prompt churn can't move identity."""
    return {"handoff": (blob or "").strip()}


def status_context(ini: dict) -> dict:
    """PURE: one store/insert row -> the STATUS context: the recent-activity fields that
    define "what's happening now". Deliberately EXCLUDES the handoff identity blob and the
    deterministic `summary` (those are identity). Empty/missing fields normalize to ""/[]."""
    return {
        "momentum": (ini.get("momentum") or "").strip(),
        "next_step": (ini.get("next_step") or "").strip(),
        "open_investigations": [
            s for s in (str(x).strip() for x in (ini.get("open_investigations") or [])) if s
        ],
        "recent_messages": [
            t for t in (_msg_text(m) for m in (ini.get("recent_messages") or []))
            if t
        ][:RECAP_MAX_MESSAGES],
        "recent_commits": [
            s for s in (str(x).strip() for x in (ini.get("recent_commits") or [])) if s
        ][:RECAP_MAX_COMMITS],
        "open_prs": [s for s in (_pr_text(p) for p in (ini.get("open_prs") or [])) if s],
    }


def _msg_text(m) -> str:
    if isinstance(m, dict):
        return str(m.get("text") or "").strip()
    return str(m or "").strip()


def _pr_text(p) -> str:
    if isinstance(p, dict):
        num = p.get("number")
        title = str(p.get("title") or "").strip()
        return (f"#{num} " if num is not None else "") + title
    return str(p or "").strip()


def _hash(canonical: dict, fingerprint: str, model: str) -> str:
    """PURE + STABLE: a sha256 over a canonical field-set PLUS the prompt fingerprint and
    the served `model`. `sort_keys` makes it dict-order-independent; the caller pre-sorts
    any set-like lists. A prompt edit (fingerprint) or model swap busts the cache."""
    payload = dict(canonical)
    payload["prompt"] = fingerprint
    payload["model"] = (model or "").strip()
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def identity_hash(ctx: dict, model: str = "") -> str:
    """PURE: the identity cache key — hash of ONLY the handoff identity blob (+ the
    identity-prompt fingerprint + model). Independent of any activity field, so a new/edited
    recent message NEVER changes it → identity stays cached across prompt churn. Regenerates
    only when the handoff description (the blob) itself changes."""
    return _hash({"handoff": ctx.get("handoff", "")},
                 _IDENTITY_PROMPT_FINGERPRINT, model)


def status_hash(ctx: dict, model: str = "") -> str:
    """PURE: the status cache key — hash of the activity fields (+ the status-prompt
    fingerprint + model). Order-independent for the set-like fields (open_investigations /
    open_prs sorted); recent_messages / recent_commits keep newest-first order (a reorder
    there implies added/removed content, which is a change we want to re-recap). Independent
    of the identity blob, so a handoff edit NEVER changes it → status stays cached across
    handoff edits."""
    canonical = {
        "momentum": ctx.get("momentum", ""),
        "next_step": ctx.get("next_step", ""),
        "open_investigations": sorted(ctx.get("open_investigations") or []),
        "open_prs": sorted(ctx.get("open_prs") or []),
        "recent_messages": list(ctx.get("recent_messages") or []),
        "recent_commits": list(ctx.get("recent_commits") or []),
    }
    return _hash(canonical, _STATUS_PROMPT_FINGERPRINT, model)


def build_identity_messages(ctx: dict) -> list[dict]:
    """PURE: identity context -> OpenAI chat `messages`. The system message carries the
    identity instructions + anti-confabulation contract; the user message is the handoff
    identity blob — the model sees ONLY the durable description, never recent prompts."""
    user = (
        "Describe what this project/initiative fundamentally IS, from the following "
        "handoff description.\n\n" + ctx.get("handoff", "")
    )
    return [
        {"role": "system", "content": IDENTITY_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_status_messages(ctx: dict) -> list[dict]:
    """PURE: status context -> OpenAI chat `messages`. The user message is the recent
    activity as JSON — the model sees ONLY these fields (no handoff description)."""
    user = (
        "State the CURRENT status of work from the following recent activity (JSON). Use "
        "only these fields.\n\n" + json.dumps(ctx, ensure_ascii=False, indent=2,
                                              sort_keys=True)
    )
    return [
        {"role": "system", "content": STATUS_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# --------------------------------------------------------------------------- #
# I/O — the on-box handoff read (size-capped, traversal-guarded) for identity.
# --------------------------------------------------------------------------- #
def _safe_read_doc(repo, current_doc, max_bytes: int = IDENTITY_MAX_DOC_BYTES):
    """Read `current_doc` off disk ONLY if it is safe: contained under `<repo>/claudedocs/`
    (realpath-resolved so `..`/symlink escapes are rejected) and an existing file. The
    read is bounded to `max_bytes`. Returns the text or None. Both `repo` and `current_doc`
    come from the STORE (not user query input); this is defense-in-depth against a traversal
    via a poisoned stored path — mirrors the viewer's `safe_doc_path`/`read_doc_detail_live`."""
    if not repo or not current_doc:
        return None
    try:
        repo_real = Path(repo).resolve()
        doc = Path(current_doc).resolve()
    except Exception:  # noqa: BLE001
        return None
    claudedocs = (repo_real / "claudedocs").resolve()
    if claudedocs not in doc.parents:  # must live under <repo>/claudedocs/
        return None
    if not doc.is_file():
        return None
    try:
        with doc.open("r", errors="replace") as f:
            return f.read(max_bytes)
    except OSError:
        return None


def read_identity_blob(repo, current_doc) -> str:
    """I/O: safe-read the handoff at `current_doc` and extract its durable identity blob.
    "" on a failed guard / missing file / read error (→ identity has no source this run, so
    the viewer falls back to the deterministic `summary`). Best-effort — never raises."""
    text = _safe_read_doc(repo, current_doc)
    if not text:
        return ""
    return identity_blob(text)


# --------------------------------------------------------------------------- #
# Store — a STANDALONE recaps table (a true cache; NOT in the snapshot/views).
# --------------------------------------------------------------------------- #
# Deliberately separate from `initiative_snapshot` and the `latest`/`current` views:
#   * it persists ACROSS snapshots (the cache survives every sync), and
#   * keeping it out of the views means NO view-version bump / view migration (a recap
#     column would reorder the frozen `SELECT i.*, s.captured_at` and force yet another
#     view migration). The viewer LEFT-JOINs this table by (repo, slug) instead.
# The identity/identity_hash/status/status_hash columns are added ADDITIVELY (ADD COLUMN
# IF NOT EXISTS) alongside the original recap/input_hash columns, which are KEPT for
# back-compat (a viewer reading `recap` still works during rollout — the new upsert mirrors
# the identity text into `recap`/`input_hash`). Created idempotently inside
# sync.ensure_schema, under the SAME advisory lock as the rest of the schema.
RECAPS_DDL = """
CREATE TABLE IF NOT EXISTS initiatives.recaps (
    repo          text NOT NULL,
    slug          text NOT NULL,
    recap         text,
    input_hash    text,
    identity      text,
    identity_hash text,
    status        text,
    status_hash   text,
    model         text,
    generated_at  timestamptz DEFAULT now(),
    PRIMARY KEY (repo, slug)
);

-- Additive migration for pre-existing installs (the table predates the identity/status
-- split): CREATE TABLE IF NOT EXISTS won't add a column to an existing table, so bring
-- the four new nullable columns in explicitly. Idempotent — a no-op once each exists.
-- This is a STANDALONE table (NOT in the latest/current views), so there is NO view
-- migration / version bump here — just these column ADDs under the existing advisory lock.
ALTER TABLE initiatives.recaps ADD COLUMN IF NOT EXISTS identity      text;
ALTER TABLE initiatives.recaps ADD COLUMN IF NOT EXISTS identity_hash text;
ALTER TABLE initiatives.recaps ADD COLUMN IF NOT EXISTS status        text;
ALTER TABLE initiatives.recaps ADD COLUMN IF NOT EXISTS status_hash   text;
"""


def create_recaps_table(cur) -> None:
    """Execute the idempotent recaps-table DDL + additive ALTERs on an OPEN cursor (called
    from sync.ensure_schema, inside its advisory-locked transaction — no commit here)."""
    cur.execute(RECAPS_DDL)


def fetch_recaps(conn) -> dict:
    """Read the cached recaps -> {(repo, slug): {"identity","identity_hash","status",
    "status_hash"}}. The hashes are what `sync_recaps` matches each initiative's
    freshly-computed identity/status hash against to decide cache-hit vs regenerate."""
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("SELECT repo, slug, identity, identity_hash, status, status_hash "
                    "FROM initiatives.recaps")
        for repo, slug, identity, id_hash, status, st_hash in cur.fetchall():
            out[(repo, slug)] = {
                "identity": identity, "identity_hash": id_hash,
                "status": status, "status_hash": st_hash,
            }
    return out


def upsert_recap(cur, repo: str, slug: str, *, identity, identity_hash,
                 status, status_hash, model: str) -> None:
    """Upsert one (repo, slug) recap row (INSERT … ON CONFLICT DO UPDATE). Writes the
    identity/status fields + their hashes, and MIRRORS the identity into the legacy
    `recap`/`input_hash` columns so an un-migrated reader of `recap` still shows the
    primary "what this is" line. `generated_at` is stamped now() on insert and update."""
    cur.execute(
        """
        INSERT INTO initiatives.recaps
            (repo, slug, recap, input_hash, identity, identity_hash,
             status, status_hash, model, generated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (repo, slug) DO UPDATE
           SET recap         = EXCLUDED.recap,
               input_hash    = EXCLUDED.input_hash,
               identity      = EXCLUDED.identity,
               identity_hash = EXCLUDED.identity_hash,
               status        = EXCLUDED.status,
               status_hash   = EXCLUDED.status_hash,
               model         = EXCLUDED.model,
               generated_at  = now()
        """,
        # recap/input_hash mirror identity/identity_hash for legacy back-compat.
        (repo, slug, identity, identity_hash, identity, identity_hash,
         status, status_hash, model),
    )


# --------------------------------------------------------------------------- #
# I/O — the vLLM client (OpenAI-compatible chat, over a kubectl port-forward).
# --------------------------------------------------------------------------- #
def _free_local_port() -> int:
    """Ask the OS for a free TCP port (bind to 0, read it back, release). Same trick
    as mail-actions/_db.py's port-forward bridge."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class VllmClient:
    """Context manager: an OpenAI-compatible chat client for the homelab vLLM service.

    Two reach modes (config-driven):
      * `base_url` set  -> POST directly to `<base_url>/v1/chat/completions` (e.g. a
        NodePort/nebula URL, like the ClickHouse reader endpoint). No port-forward.
      * otherwise       -> `kubectl -n <ns> port-forward <service> <local>:<svc_port>`
        on an ephemeral local port for the batch, torn down on exit (mirrors _db.py).

    Uses stdlib urllib (no `requests` dependency). `generate(messages)` returns the
    completion text or raises — callers wrap it best-effort so a model outage is inert.
    """

    def __init__(self, cfg: dict, ready_timeout: float = 20.0):
        self._cfg = cfg
        self._ready_timeout = ready_timeout
        self._pf: subprocess.Popen | None = None
        self._url: str | None = None

    def __enter__(self) -> "VllmClient":
        base = self._cfg.get("base_url")
        if base:
            self._url = base.rstrip("/") + RECAP_CHAT_PATH
            return self
        local_port = _free_local_port()
        self._pf = subprocess.Popen(
            [
                "kubectl", "-n", self._cfg["namespace"], "port-forward",
                self._cfg["service"], f"{local_port}:{self._cfg['service_port']}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._wait_for_port("127.0.0.1", local_port)
        self._url = f"http://127.0.0.1:{local_port}{RECAP_CHAT_PATH}"
        return self

    def __exit__(self, *_exc) -> None:
        if self._pf is not None:
            self._pf.terminate()
            with contextlib.suppress(Exception):
                self._pf.wait(timeout=5)

    def _wait_for_port(self, host: str, port: int) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self._pf and self._pf.poll() is not None:
                err = self._pf.stderr.read().decode() if self._pf.stderr else ""
                raise RuntimeError(f"kubectl port-forward exited early:\n{err}")
            with contextlib.suppress(OSError):
                with socket.create_connection((host, port), timeout=1):
                    return
            time.sleep(0.25)
        raise TimeoutError(f"vLLM port-forward to {host}:{port} not ready in time")

    def generate(self, messages: list[dict]) -> str:
        """POST an OpenAI chat-completions request; return choices[0].message.content."""
        if not self._url:
            raise RuntimeError("VllmClient used outside its context manager")
        body = json.dumps({
            "model": self._cfg["model"],
            "messages": messages,
            "temperature": RECAP_TEMPERATURE,
            "max_tokens": RECAP_MAX_TOKENS,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._cfg["timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# Orchestration — cached, regenerate-on-change PER FIELD, best-effort per initiative.
# --------------------------------------------------------------------------- #
def sync_recaps(conn, rows: list[dict], *, client, model: str,
                blob_reader=None) -> dict:
    """For each row, resolve IDENTITY and STATUS independently: each is regenerated ONLY
    when its own hash differs from the cached one (or it's missing) — a matching hash is a
    cache hit that skips the model. Identity is sourced from the handoff blob (read via
    `blob_reader`, injectable for tests); status from the recent-activity fields. Each
    generation/read is best-effort — a single failure leaves that field's cached (last-good)
    value UNTOUCHED and moves on (for identity with no cache, the viewer falls back to
    `summary`). Upserts once at the end whenever EITHER field regenerated. Returns a stats
    dict. `conn`/`client`/`blob_reader` I/O is the only impurity; the decision logic is
    deterministic."""
    # Resolve the reader at call time (not as a def-time default) so a monkeypatch of the
    # module-level `read_identity_blob` takes effect and tests can inject a fake cleanly.
    if blob_reader is None:
        blob_reader = read_identity_blob
    stats = {"total": len(rows), "identity_new": 0, "status_new": 0,
             "cached": 0, "failed": 0, "skipped": 0}
    cached = fetch_recaps(conn)
    with conn.cursor() as cur:
        for r in rows:
            repo, slug = r.get("repo"), r.get("slug")
            if not repo or not slug:
                stats["skipped"] += 1
                continue
            prev = cached.get((repo, slug)) or {}

            # ---- identity (from the durable handoff blob) --------------------
            try:
                blob = blob_reader(repo, r.get("current_doc")) or ""
            except Exception:  # noqa: BLE001 - a read hiccup must not break the sync
                blob = ""
            id_ctx = identity_context(blob)
            want_id_hash = identity_hash(id_ctx, model) if blob else None
            id_hit = bool(blob and prev.get("identity")
                          and prev.get("identity_hash") == want_id_hash)

            # ---- status (from recent activity) -------------------------------
            st_ctx = status_context(r)
            want_st_hash = status_hash(st_ctx, model)
            st_hit = bool(prev.get("status") and prev.get("status_hash") == want_st_hash)

            if id_hit and st_hit:
                stats["cached"] += 1        # both unchanged → reuse, no model call
                continue

            # Start from the previous (last-good) values; regenerate only the misses.
            id_text, id_h = prev.get("identity"), prev.get("identity_hash")
            st_text, st_h = prev.get("status"), prev.get("status_hash")
            id_regen = st_regen = False

            if not id_hit and blob:
                try:
                    t = (client.generate(build_identity_messages(id_ctx)) or "").strip()
                except Exception:  # noqa: BLE001 - a model outage must not break the sync
                    t = ""
                if t:
                    id_text, id_h, id_regen = t, want_id_hash, True
                else:
                    stats["failed"] += 1    # keep last-good identity (or none → summary)

            if not st_hit:
                try:
                    t = (client.generate(build_status_messages(st_ctx)) or "").strip()
                except Exception:  # noqa: BLE001
                    t = ""
                if t:
                    st_text, st_h, st_regen = t, want_st_hash, True
                else:
                    stats["failed"] += 1    # keep last-good status (or none)

            if id_regen or st_regen:
                upsert_recap(cur, repo, slug, identity=id_text, identity_hash=id_h,
                             status=st_text, status_hash=st_h, model=model)
                if id_regen:
                    stats["identity_new"] += 1
                if st_regen:
                    stats["status_new"] += 1
    conn.commit()
    return stats


def maybe_sync_recaps(conn, rows: list[dict], env: dict | None = None,
                      client_factory=None) -> dict:
    """Best-effort entry point called from the sync BEFORE the (unconditional) snapshot
    write. Reads config; if disabled, returns immediately (the recaps table still exists,
    created in ensure_schema). Otherwise opens ONE vLLM client for the whole batch and
    runs `sync_recaps`. ANY failure (config, port-forward, DB) is caught and the connection
    is ROLLED BACK so the following `write_snapshot` is never affected.

    `client_factory(cfg) -> context-manager client` is injectable for tests (defaults to
    `VllmClient`). Returns a stats dict with a `status` key for the sync's log line."""
    cfg = recap_config(env)
    if not cfg["enabled"]:
        return {"status": "disabled",
                "message": "recap generation disabled (INITIATIVES_RECAP_ENABLED unset)"}
    factory = client_factory or VllmClient
    try:
        with factory(cfg) as client:
            stats = sync_recaps(conn, rows, client=client, model=cfg["model"])
        stats["status"] = "ok"
        return stats
    except Exception as exc:  # noqa: BLE001 - recap is best-effort; never break the sync
        with contextlib.suppress(Exception):
            conn.rollback()  # clear any aborted-transaction state before write_snapshot
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def format_recap_note(stats: dict) -> str:
    """A compact one-liner for the sync's stdout summary (e.g. ', recap 2 id/3 st new,
    9 cached')."""
    status = stats.get("status")
    if status == "disabled":
        return ", recap off"
    if status == "error":
        return ", recap error (best-effort, skipped)"
    if status == "ok":
        note = (f", recap {stats.get('identity_new', 0)} id/"
                f"{stats.get('status_new', 0)} st new, {stats.get('cached', 0)} cached")
        if stats.get("failed"):
            note += f", {stats['failed']} failed"
        return note
    return ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
