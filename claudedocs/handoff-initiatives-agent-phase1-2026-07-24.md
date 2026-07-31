# Handoff — Initiatives Agent, Phase 1 kickoff (build read-only on the OpenClaw+skills stack), 2026-07-24

**Next session's job:** rebuild the initiatives **assistant** (read-only Q&A + routing) as a **model-driven
OpenClaw agent with skills** — the stack we researched — replacing the brittle hand-rolled regex classifier
that shipped in Phase 1. Read-only, so none of the write/dispatch security work is needed yet.

## Why this pivot (read before building)
Phase 1 shipped a scripted assistant (`scripts/initiatives/assistant.py`) with a **regex intent-classifier +
keyword-marker tools + a synthesis prompt**. It is **brittle** — three real failures found via the audit log
in one sitting, all the same root cause (regex can't understand language):
1. `blocked_on_me` matched the marker `"for zach to"` against an initiative's *purpose* ("cards **for Zach to**
   adjudicate") → false positive (fixed PR #161 by narrowing fields/markers — a patch).
2. Compound question "whats stalled **and** waiting" → single-intent classifier collapsed to `stalled` only,
   **missed** the waiting item (spend-analytics), and phrased "None are stalled and waiting" (over-claimed).
3. Confabulated reasons when a marker hit but no real reason existed.
Patching the classifier is whack-a-mole. **The determinism is in the wrong place:** we made *intent
understanding* deterministic (regex) and left the model to only phrase — backwards. The fix is to **flip it**:
the model does intent-understanding + tool-selection (what LLMs are reliably good at), the **tools stay
deterministic** (grounded store/route queries), and **sources are computed from tool output** (no fabricated
facts). Worst case of a wrong tool pick = a less-relevant but still-grounded answer, never a made-up fact —
a graceful, improvable failure vs the regex's hard wall.

And we already **researched the right stack** for exactly this (see `claudedocs/initiatives-agent-proposal-2026-07-24.md`
+ the red-team `…-eval-2026-07-24.md`): an **OpenClaw agent** deployed by **kubeclaw**, tools exposed as
**skills** (the `civit/support-agent-*` `SKILL.md` + `query.mjs` subprocess pattern — MCP is off on the pinned
openclaw 2026.6.x), chat via the **support-agent/clawgate operator-chat**. The eval re-scoped Phase 1 to a
"scripted/**skills** assistant" — we took the *scripted* fork; the *skills* fork is the non-brittle one.

## Phase 1 plan (read-only OpenClaw + skills agent)
- **Retire** the regex intent-classifier in `assistant.py`. **REUSE** its deterministic tools — `query_initiatives`
  (store reads: blocked-on-me/active/slowing/stalled/status-of/live-sessions/by-repo), `route_signal`
  (`route.rank_matches`), `read_handoff` (guarded doc read) — as **skills** (`SKILL.md` + a query script). The
  keyword fixes from #161 carry over as skill *internals*; only the routing layer is replaced.
- **Model-driven tool-calling:** the OpenClaw agent reads the question, calls the skill tools (multiple for
  compound questions), and synthesizes from the grounded results. It must **report which tools it ran** (no
  over-claiming). Keep sources = tool output (grounded).
- **Runtime:** an OpenClaw devpod via **kubeclaw**. The eval deferred the devpod for *write/dispatch* reasons
  (voluntary gate, cross-cluster mutation) — **none apply to read-only** (no write tools = nothing to gate;
  blast radius = a wrong answer). This read-only agent is the **foundation** for the full agent (Phase 2 just
  adds write/dispatch skills behind a STRUCTURAL server-side gate + a least-privilege DB role) — not a throwaway.
- **Store reach:** use the `_db.py` **direct in-cluster mode** shipped in #156 (`MAILBOX_PG_HOST=mailbox-postgres.mailbox.svc`
  …) so the in-cluster agent connects directly — no per-query kubectl port-forward. Give the agent a
  **read-only/least-privilege DB role** (it only SELECTs `initiatives.*` + appends `assistant_log`).
- **Chat:** keep the viewer's `/api/ask` + sidebar pane as the surface, now backed by the agent (or adapt the
  support-agent operator-chat). Keep the **`initiatives.assistant_log`** audit table — it's what caught every bug.
- **Model:** OPEN DECISION — the local `vllm-recap` Qwen2.5-7B may be too weak for reliable tool-selection;
  candidates: deepseek (Zach's dispatch default, [[task-dispatch-default-deepseek]]) or a larger local model.
  **Validate tool-calling reliability early** (does it pick the right tools on 8-10 varied questions incl.
  compound?) — if the 7B under-performs, switch the model, NOT back to regex.

## Reuse / retire / defer
- REUSE: `assistant.py` tools → skills; the store (`initiatives.latest`/`recaps`/`assistant_log`); `route.py`;
  the viewer chat pane + `/api/ask`; `_db.py` direct-mode (#156); the support-agent skills pattern; clawgate/kubeclaw.
- RETIRE: the regex `classify_intent` / `_PATTERNS` / marker-scan routing.
- DEFER (Phase 2+): write/dispatch tools — behind a **structural server-side write-gate** (NOT the voluntary
  `agent_checkpoint`, per the eval) + least-privilege DB role. Dispatch (clawgate `/api/tasks`, title as
  `directory`) is structurally safe (card only). Cross-cluster/shared-`mailbox`-DB blast radius: scoped role.

## Current live state (all workbench-only, serverMode-gated)
- Store: `initiatives` schema in the homelab **mailbox** Postgres — `snapshots`, `initiative_snapshot`,
  views `latest`/`current` (DROP+CREATE on a column add + marker bump), `recaps` (identity/status), `assistant_log`.
- Sync: `initiatives-sync.timer` (15min) → `run-sync.sh` (sops-decrypts CH reader creds) → `sync.py` →
  `initiative-scan.py --json`. Recaps generated here via `vllm-recap` (homelab Qwen2.5-7B-AWQ, ns `promptver`,
  svc `vllm-recap`, served model `recap`; joycaption+comfyui scaled to 0 in git to free the shared 5080).
- Viewer: `initiatives-viewer.service` at **http://192.168.50.250:8899** (workbench eth1 — NOT .94, a homelab
  node). Flat/grouped/**recency(default, rolling)** views + live-unmatched sessions + recaps + `/api/ask` chat.
- Operate via the (to-be-written) **`initiatives` skill**. Full arc: `handoff-initiatives-consolidation-2026-07-22.md`.

## First steps next session
1. Read the proposal + eval + this doc + the `initiatives` skill. 2. Confirm the model (validate 7B tool-calling
   or pick deepseek/bigger). 3. Turn the `assistant.py` tools into skills. 4. Stand up the read-only OpenClaw
   devpod via kubeclaw with the least-privilege DB role + `_db.py` direct-mode. 5. Wire the agent to the
   `/api/ask` chat. 6. Verify with the same audit-log loop: re-ask "whats stalled and waiting" → expects BOTH
   the stalled set AND spend-analytics (waiting), correctly. Then build→verify→ship per standing autonomy.
