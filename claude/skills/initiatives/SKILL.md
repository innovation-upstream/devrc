---
name: initiatives
description: "Operate the DURABLE initiatives board — the Postgres store, the 15-min sync, LLM recaps, the workbench viewer at 192.168.50.250:8899, the signal->initiative router and the read-only /api/ask assistant. Use for: the initiatives viewer/board, initiatives.current/latest, the initiative store/sync, initiative recaps, the vllm-recap model, the initiatives assistant/chat, \"what's on my board\" as a durable page. The one-off scan is `initiative-scan`."
---

# initiatives subsystem operations

The **durable, queryable counterpart** to the on-demand `/initiative-scan` scan and the ephemeral
agent-ops TUI. Four layers **store → sync → viewer → recaps** + a **router** (signal→initiative)
+ a **read-only assistant**. Code: `~/workspace/devrc/scripts/initiatives/` (sync.py, viewer.py,
route.py, assistant.py, recap.py, run-sync.sh, run-viewer.sh); engine =
`scripts/session-analysis/initiative-scan.py`. Everything is **workbench-only,
serverMode-gated** — the laptop (no `~/.server-mode`) correctly runs none of it.
Arc: `devrc/claudedocs/handoff-initiatives-nextstep-dispatch-shipped-2026-07-26.md` +
`handoff-initiatives-consolidation-2026-07-22.md` (read first).

**Reference files** (`~/.claude/skills/initiatives/reference/`, source
`~/workspace/devrc/claude/skills/initiatives/reference/`) — read on demand:
- `viewer-board.md` — board layout, states/chips/search, per-card actions, archive lifecycle. Read when changing the viewer UI or debugging a card's lane/state.
- `clawgate-tasks.md` — the `initiative:<slug>` tag join, dispatch guard, fetch shape + the fetch worker's termination bounds (body phase fixed 2026-08-11; connect/headers still unbounded, and the single-flight guard does NOT hold it to one thread). Read when a dispatched task doesn't link, when touching tagging, or before claiming that fetch cannot leak threads.
- `agent-devpod.md` — devpod deploy, kubeclaw 0.7.x hardening values, image `2026.6.11-py` + web-search-off, identity pin, least-priv DB role. Read when deploying/bumping the agent pod or debugging a crash-loop.

## How a card comes to exist (discovery model)
- **SESSION/ACTIVITY-FIRST.** Initiatives are `(repo, topic)` groups mined from Claude SESSIONS
  + activity telemetry. topic = the transcript `ai-title` (`~/.claude/projects/*/<sid>.jsonl`,
  `{"type":"ai-title"}`); recency = last user-turn ts. Noise floor `MIN_SESSION_TURNS=1`,
  `MIN_TOPIC_TOKENS=2` (constants in initiative-scan.py).
- **SESSIONS-ONLY.** A card is ALWAYS session-backed (`source ∈ {session, both}`). A handoff doc
  of ANY filename can ANCHOR a session group (`best_title_match` → `source=both`, adopting its
  slug/summary/next_step/investigations for ENRICHMENT) but NEVER creates a standalone card
  (pure `source=doc` is DROPPED in `combine_docs_and_groups`).
- **Timing is session-only** — `last_touch`/momentum come from the session last-user-turn
  (+ git/telemetry/live signals); `doc_touch_epoch` does NOT feed `last_touch`.
  `undocumented=true` tags a session-only (no doc-anchor) card.
- **Title-drift MERGE is OFF** (`ENABLE_TITLE_DRIFT_MERGE=False`): only Pass-1 EXACT-title
  grouping, so distinct sessions don't over-merge into token-salad slugs. Recognizability comes
  from the card's `start ›`/`you ›` lines + the client-side fuzzy `matchQ` search.
- Store is **v6**: `source`/`undocumented` @v4; `opening_message` genesis prompt @v5;
  `search_text` (full per-session user-turn text, capped 6000, search-only-never-rendered) @v6.
- Fusion (git + telemetry + live tmux → momentum/last-touch/next-step) persists to Postgres.

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Code | `~/workspace/devrc/scripts/initiatives/` + the engine `scripts/session-analysis/initiative-scan.py` |
| Store | **`initiatives` schema in the homelab `mailbox` Postgres** (SAME DB as `mail_actions`; ns `mailbox`, `mailbox-postgres-0`). `KUBECONFIG=$KC_HOMELAB` |
| Store reach | via **`scripts/mail-actions/_db.py`** (`MailDB`), loaded by **explicit importlib path — NOT `sys.path`** (its sibling `llm.py` shadows repo-cos's). Default = kubectl port-forward; **direct in-cluster mode** (PR #156) via `MAILBOX_PG_HOST` (+`MAILBOX_PG_PORT`) or `MAILBOX_PG_DIRECT=1` |
| Tables | `snapshots` (one row/run: `id,captured_at,host,days_window,telemetry_available`); `initiative_snapshot` (**append-only**, one row/initiative/run: slug/repo/title/momentum/last_touch/commits/merged_prs/open_prs/session_count/telem_*/summary/current_doc/open_investigations/docs + recent_messages/recent_commits jsonb); `recaps` (standalone: `repo,slug` + `identity`/`identity_hash` + `status`/`status_hash` + legacy `recap`); `assistant_log` (standalone audit); **`archived`** (standalone: `repo,slug` PK + `title`/`reason`/`archived_at`; in NO view → no VIEW_VERSION bump) |
| Views | **`latest`** = rows from `max(snapshot_id)` (ghost-free; the **viewer** reads this). **`current`** = `DISTINCT ON (repo,slug)` newest across ALL history → includes aged-out "**ghosts**" (the **router** reads this — matching dormant initiatives is desirable). Both DROP+CREATE-guarded by a `COMMENT ON VIEW` version marker (`VIEW_VERSION`/`LATEST_VIEW_VERSION`, both `v6`) |
| Sync | `initiatives-sync.timer` (`systemd --user`, **workbench**, `OnUnitActiveSec=15min`) → `run-sync.sh` → `sync.py --days 5` → `initiative-scan.py --json`. **Lookback = 5d** (Zach's call — a tight recent board; 14d was too cluttered; override via `INITIATIVES_SYNC_DAYS`). Momentum buckets are absolute last-touch ages (<2d active / 2-7d slowing / ≥7d stalled), so **at 5d "stalled" (≥7d) is structurally EMPTY and "cooling"/slowing (2-5d) is the going-quiet triage signal**; widen the window if deep-stalled work is wanted. Double-gated `serverMode && enableInitiativesSync`. **Timeout 600s** (headroom for a cold-recap batch; warm runs ~6-15s). 90-day retention prune each run |
| Sync creds | `run-sync.sh` **runtime sops-decrypts** the ClickHouse **reader** password from homelab-talos trunk (`clusters/homelab/apps/activity/secrets.enc.yaml`, host age key) → `CLICKHOUSE_*` exported → scan runs **telemetry-on**. Every step guarded → missing key/repo/sops ⇒ telemetry-off (still writes a useful handoff+git snapshot). `--input-type yaml` is REQUIRED (mktemp has no `.yaml` ext) |
| Viewer | `initiatives-viewer.service` (`systemd --user`, **workbench**, serverMode-gated, `Restart=on-failure`) → `run-viewer.sh` → `viewer.py` (stdlib `http.server`). **LIVE at http://192.168.50.250:8899/** |
| Viewer bind | ⚠ binds the workbench **eth1 LAN IP `192.168.50.250`** — **NOT `192.168.50.94`, which is a HOMELAB node** (kube-apiserver/NodePorts/ClickHouse); binding `.94` → `OSError: Cannot assign requested address` crash-loop (bit us in #140→#141), and **a `127.0.0.1` smoke test will NOT catch it**. See `[[workbench-lan-ip]]`. Internal work data — deliberately NOT wired to the public gateway |
| Viewer board | Grouped-by-repo triage board, two-line collapsed cards, 4 sections, state chips + fuzzy search, state-driven per-card actions, archive lifecycle. **Full detail → `reference/viewer-board.md`** |
| Recap model | **`vllm-recap`** — homelab vLLM, **ns `promptver`, svc `vllm-recap:8000`, served model `recap` = Qwen2.5-7B-Instruct-AWQ**. Wired via the unit env (`INITIATIVES_RECAP_ENABLED=1`, `RECAP_NAMESPACE=promptver`, `RECAP_SERVICE=svc/vllm-recap`, `RECAP_SERVICE_PORT=8000`, `RECAP_MODEL=recap`) — recap.py's in-code defaults are PLACEHOLDERs |
| Router | `route.py` — `route(signal,repo,limit)` / `rank_matches()` / `classify()`. Reads `initiatives.current`; scoring single-sourced from the scan's `best_title_match` (word-equality, no stemming). Read-only, suggests-never-acts. Wired into repo-cos digests (#138) + mail-actions (#139, adds `related_initiative` col) |
| Assistant (Phase 1 agent) | **PRIMARY `/api/ask` = a model-driven OpenClaw devpod** (ns `devpod-initiatives`, svc `initiatives-devpod:18789`, `openclaw/initiatives`, **DeepSeek V4 Pro via OpenRouter**). The MODEL selects which deterministic skill-tool(s) to run (incl. MULTIPLE for compound Qs) — the tools are `scripts/initiatives/skills/query.py` (reuses assistant.py's `run_tool`/`build_facts`/`sources_of`), reached via `_db.py` **direct in-cluster mode** (#156) with a **least-priv `initiatives_agent` PG role** (SELECT-only on `initiatives.*`). Viewer's `agent_client.py` proxies via kubectl port-forward + gateway token `sha256("gw-"+HOOKS_TOKEN)`, **streams** the answer (SSE `/api/ask/stream`) and **renders markdown**; **graceful FALLBACK** to the deterministic regex `assistant.py` if the devpod is down. Every ask audit-logged to `initiatives.assistant_log` (`intent=agent`). Phase 2 (write/dispatch) stays deferred behind a structural server-side gate. **Deploy/hardening → `reference/agent-devpod.md`** |

## status
```bash
export KUBECONFIG=$KC_HOMELAB   # the store is only reachable via port-forward here
systemctl --user status initiatives-sync.timer initiatives-viewer.service --no-pager | head -30
systemctl --user list-timers | grep initiatives
journalctl --user -u initiatives-sync.service -n 30 --no-pager      # grep -i telemetry for on/off
curl -sf http://192.168.50.250:8899/healthz; echo                   # viewer health
PSQL='kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c'
$PSQL "select id,captured_at,host,days_window,telemetry_available from initiatives.snapshots order by id desc limit 5;"
$PSQL "select count(*) from initiatives.latest;"                    # viewer's set (ghost-free)
$PSQL "select count(*) from initiatives.current;"                   # router's set (latest + N ghosts)
$PSQL "select repo,slug,momentum,last_touch from initiatives.latest order by last_touch desc limit 15;"
$PSQL "select repo,slug,left(identity,60),left(status,50) from initiatives.recaps order by slug limit 10;"
```

## operate

```bash
# run a sync NOW (both routes go through run-sync.sh → telemetry-on):
KUBECONFIG=$KC_HOMELAB systemctl --user start initiatives-sync.service
#   confirm: journalctl --user -u initiatives-sync.service | grep -i "telemetry\|recap"
#   expect "reader creds provisioned — telemetry-on" and a "recap N new/M cached" line.
# dry-run the transform without writing (needs the DB port-forward for the read only):
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python ~/workspace/devrc/scripts/initiatives/sync.py --dry-run --days 4'

# router — which initiative does a free-text signal belong to (read-only, suggests):
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python ~/workspace/devrc/scripts/initiatives/route.py "polish the clawgate chat scroll"'

# assistant PRIMARY path = the OpenClaw agent devpod (ask via the viewer):
curl -s -X POST http://192.168.50.250:8899/api/ask -H 'Content-Type: application/json' \
  -d '{"question":"whats stalled and waiting on me"}' | python3 -m json.tool   # intent=agent when the devpod answered
# agent devpod status/logs (homelab):
KUBECONFIG=$KC_HOMELAB kubectl -n devpod-initiatives get pods,helmrelease -A 2>/dev/null; \
  KUBECONFIG=$KC_HOMELAB kubectl -n flux-system get helmrelease initiatives-agent
POD=$(KUBECONFIG=$KC_HOMELAB kubectl -n devpod-initiatives get pods -l app.kubernetes.io/instance=initiatives -o name | tail -1)
KUBECONFIG=$KC_HOMELAB kubectl -n devpod-initiatives logs $POD -c agent --tail=50   # agent loop
# run a skill-tool IN-POD (grounded JSON; the agent calls these):
KUBECONFIG=$KC_HOMELAB kubectl -n devpod-initiatives exec $POD -c agent -- \
  python3 /data/repos/devrc/scripts/initiatives/skills/query.py blocked_on_me
# hit the gateway directly (bypass the viewer): token = sha256("gw-"+HOOKS_TOKEN), model openclaw/initiatives
# audit log (BOTH paths; intent=agent vs the regex intents):
KUBECONFIG=$KC_HOMELAB kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c \
  "select id,intent,model,jsonb_array_length(sources) nsrc,latency_ms,left(question,40) from initiatives.assistant_log order by id desc limit 8;"
# FALLBACK deterministic assistant (used when the devpod is down; also the CLI):
nix-shell -p 'python3.withPackages(p:[p.psycopg2 p.requests])' --run \
  'python ~/workspace/devrc/scripts/initiatives/assistant.py "whats stalled and waiting on me"'

# force an out-of-band viewer refresh (same as the ↻ button):
curl -sf -X POST http://192.168.50.250:8899/refresh; echo

# ONE-TAP DISPATCH → a clawgate Task card (Phase 2a, origin/main 835fe0c). The VIEWER holds
#   ~/.claude/clawgate.env; the in-cluster devpod does NOT. Two human gates: this creates a
#   card; you still tap Dispatch in clawgate.
curl -s -X POST http://192.168.50.250:8899/api/dispatch -H 'Content-Type: application/json' \
  -d '{"repo":"<repo full path OR repo_name>","slug":"<slug>"}'   # 200 {ok,task_id} / 400 / 404 / 502
# dispatch.py mirrors repo-cos/clawgate.py: POST clawgate /api/tasks {directory(=label),body,repo?,tags} —
#   model OMITTED → clawgate default deepseek. Confirm the card: GET {CLAWGATE_API_URL}/api/tasks (Bearer).
# Linked-task join, dispatch guard, fetch shape + what still leaks → reference/clawgate-tasks.md
```

**Grounded next step (nextstep.py, read-only):** every card carries one — documented cards
show their parsed `next_step`; EMERGING cards show a distinct `next (suggested) ›` line
inferred from a real field (open-PR/investigation/last-prompt/status/stalled) — **NEVER
invented**. Chat: *"what should I do next on \<initiative\>"* → the `recommend_next_step` tool
(agent + regex fallback).

## deploy a change
```bash
# scripts (sync.py/route.py/assistant.py/recap.py + wrappers) ship via home-manager:
home-manager switch --flake ~/workspace/devrc --impure          # workbench; or scripts/ship.sh after merge
# after a viewer.py change (X-Restart-Triggers covers viewer.py+run-viewer.sh on switch;
# restart explicitly if you edited out of band):
systemctl --user restart initiatives-viewer.service
# regenerate the store immediately (e.g. after a sync.py or recap change):
KUBECONFIG=$KC_HOMELAB systemctl --user start initiatives-sync.service
# tests: scripts/initiatives/tests/ via nix-shell pytest (pure fixtures; query/agent_client/assistant/sync)
# agent devpod deploy → reference/agent-devpod.md
```

## ⚠ gotchas (each cost real time)
- **🔴 X-Restart-Triggers does NOT cover every long-lived sibling — a "shipped" change can be silently INERT.**
  The viewer importlib-caches its siblings at startup and `nix/home.nix` only triggers on
  `viewer.py`+`run-viewer.sh`. So a change to **`initiative-scan.py`** (the viewer's `attach_tmux`
  reuses `scan.match_tmux_to_initiatives` verbatim for the live overlay) or **`assistant.py`** switches
  cleanly and does **nothing** until you `systemctl --user restart initiatives-viewer.service`.
  A fresh sync alone won't do it — the live badge is a render-time overlay, not stored.
- **Running `run-viewer.sh` from an INTERACTIVE shell needs `env -u shellHook`.** `homelab-talos/.envrc`
  is `use flake`, and that flake's `shellHook` exports a **relative** `KUBECONFIG=homelab-kubeconfig`.
  The wrapper only defaults (`${KUBECONFIG:-<absolute>}`) so it defers to the relative one, and the
  inner `nix-shell -p …` re-executes the **inherited `$shellHook`**, re-exporting it even if you fixed
  `KUBECONFIG` first — the store read then fails silently from any cwd that isn't the repo root.
  Use `env -u shellHook ./run-viewer.sh`. **Systemd is unaffected** (minimal env, no direnv).
- **Don't pin absolute slug counts in docs or tests.** The live store drifted **139→144 over ~3 days**;
  every doc or assertion carrying a total goes stale within days. Assert/state **properties** (floors,
  set-equality, ratios) instead — this already rotted repo-cos's corpus tests three days running.
- **Schema migration = DROP+CREATE the views, never CREATE OR REPLACE.** Adding a column to
  `initiative_snapshot` reorders the `latest`/`current` views' columns; `CREATE OR REPLACE VIEW`
  REJECTS a column-name/order change (`cannot change name of view column …`) → froze the whole store
  on the v1→v2 deploy. Fix: **bump the `VIEW_VERSION`/`LATEST_VIEW_VERSION` marker** so `_ensure_view`
  does DROP VIEW IF EXISTS + CREATE (ACCESS EXCLUSIVE, momentary). `recaps` + `assistant_log` are
  **standalone tables** (in no view) → adding columns there needs NO view-marker bump. **Validate a
  migration by replaying it on a throwaway Postgres first** — the unit fixtures only assert SQL
  strings, they can't catch this.
- **Repo-name tokens are STRIPPED before a live tmux pane → initiative match** (2026-07-28).
  `best_title_match`'s `title_overlap >= 2` gate was clearing on the REPO-NAME tokens alone
  (civitai-manager → {civitai, manager}) — which every session AND initiative in the repo shares —
  so a generic pane ("Continue civitai-manager development work") got badged `● live` on an unrelated
  card while that session did other work. Fix in `match_tmux_to_initiatives`:
  `ptoks -= set(text_tokens(os.path.basename(repo)))` so a pane must share a DISTINGUISHING word;
  a pane with none falls to `live_unmatched` (an honest miss — "a wrong tag costs more than a miss").
- **Momentum times from the last genuine USER-turn timestamp, NOT the transcript file mtime** (PR #149).
  Claude Code rewrites session `.jsonl` files in place (title/mode metadata) → mtime-driven momentum
  read 18-day-idle sessions as "active ~47m ago". `initiative-scan.py` parses `turns[].ts` for the
  last user turn; mtime is a rare fallback only (mirrors `doc_touch_epoch`).
- **Recap VRAM contention** — `vllm-recap` shares a single 5080 (16.3GB, time-sliced ×4); to free VRAM,
  **`vllm-joycaption` + `comfyui` are scaled to `replicas: 0` in homelab git**. Scale them back for a
  captioning/comfy pass — but VRAM then contends with recap. Recap is **best-effort**: model down → the
  sync/store are unaffected and the card falls back to the deterministic `summary`.
- **Recap identity vs status** (PR #154): two independently-cached fields — `identity` ("what it is",
  from the handoff's durable head, hash-keyed on the HANDOFF so it's stable across prompt churn) +
  `status` ("current", hash-keyed on recent activity). Two `vllm-recap` calls. Viewer renders identity
  primary (fallback identity→recap→summary), status secondary.
- **`_db.py` importlib load** — never add `mail-actions/` to `sys.path` (its `llm.py` shadows
  repo-cos's and breaks synthesis). route/assistant/sync all load it by explicit path.
- **Assistant is read-only by design** — no write/dispatch/devpod/MCP; worst prompt-injection case is a
  skewed but grounded answer, never an action. The Phase-2 write/dispatch path is DEFERRED behind a
  **structural** server-side write-gate (NOT the voluntary `agent_checkpoint`) + a least-privilege DB role.
