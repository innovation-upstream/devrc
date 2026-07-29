---
name: initiatives
description: Operate the initiatives subsystem — a durable, cross-repo initiative ledger built on session-analysis/initiative-scan.py, with a live web viewer, a signal→initiative router, and a read-only Q&A assistant. Store is the `initiatives` schema in the homelab mailbox Postgres; a 15-min sync writes snapshots + LLM recaps; a workbench viewer serves it at 192.168.50.250:8899 with an /api/ask chat sidebar. Status, query the store, run/trigger the sync, restart/debug the viewer, tune recaps, operate the router/assistant, do a schema migration. Use when the user mentions the initiatives viewer/board, initiatives.current/latest, the initiative store/sync, initiative recaps, the vllm-recap model, the routing of a signal to an initiative, the initiatives assistant/chat, or "what's on my board / what's blocked on me" as a durable page (vs the ephemeral /initiatives scan or agent-ops TUI).
---

# initiatives subsystem operations

The **durable, queryable counterpart** to the on-demand `/initiatives` scan and the ephemeral
agent-ops TUI. `session-analysis/initiative-scan.py` is the deterministic engine. **Discovery is
SESSION/ACTIVITY-FIRST:** initiatives are `(repo, topic)` groups mined from Claude SESSIONS +
activity telemetry — topic = the transcript `ai-title` (`~/.claude/projects/*/<sid>.jsonl`
`{"type":"ai-title"}`), recency = last user-turn ts — behind a noise floor (`MIN_SESSION_TURNS=1`,
`MIN_TOPIC_TOKENS=2`; constants in initiative-scan.py). **SESSIONS-ONLY:** a card is ALWAYS
session-backed (`source ∈ {session, both}`). A handoff doc of ANY filename can ANCHOR a session
group (`best_title_match` → `source=both`, adopting its slug/summary/next_step/investigations for
ENRICHMENT) but NEVER creates or times a standalone card (pure `source=doc` is DROPPED in
`combine_docs_and_groups`). **Timing is session-only** — `last_touch`/momentum come from the session
last-user-turn (+ git/telemetry/live signals); `doc_touch_epoch` does NOT feed `last_touch`.
`undocumented=true` tags a session-only (no doc-anchor) card. Store is **v6** (`source`/`undocumented`
@v4; `opening_message` genesis prompt @v5; `search_text` — full per-session user-turn text, capped
6000, search-only-never-rendered — @v6). **Title-drift MERGE is OFF** (`ENABLE_TITLE_DRIFT_MERGE=False`):
only Pass-1 EXACT-title grouping, so distinct sessions don't over-merge into token-salad slugs.
Recognizability comes from the card's `start ›`/`you ›` lines + a client-side **FUZZY** `matchQ`
SEARCH (substring + bounded-Levenshtein ≥4-char + ordered-subsequence ≥5-char, per-token AND) over
title/summary/status/opening/latest/`search_text`/next_step/slug/repo (a match in the collapsed
Emerging lane auto-expands it). Fusion (git + telemetry + live tmux → momentum/last-touch/next-step)
persists to Postgres. Four layers **store → sync → viewer → recaps** + a **router** (signal→initiative)
+ a **read-only assistant** (Q&A over the store). Code: `~/workspace/devrc/scripts/initiatives/`
(sync.py, viewer.py, route.py, assistant.py, recap.py, run-sync.sh, run-viewer.sh). Arc:
`devrc/claudedocs/handoff-initiatives-nextstep-dispatch-shipped-2026-07-26.md` +
`handoff-initiatives-consolidation-2026-07-22.md` (read first). Everything is
**workbench-only, serverMode-gated** — the laptop (no `~/.server-mode`) correctly runs none of it.

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Code | `~/workspace/devrc/scripts/initiatives/` + the engine `scripts/session-analysis/initiative-scan.py` |
| Store | **`initiatives` schema in the homelab `mailbox` Postgres** (SAME DB as `mail_actions`; ns `mailbox`, `mailbox-postgres-0`). `KUBECONFIG=$KC_HOMELAB` |
| Store reach | via **`scripts/mail-actions/_db.py`** (`MailDB`), loaded by **explicit importlib path — NOT `sys.path`** (its sibling `llm.py` shadows repo-cos's). Default = kubectl port-forward; **direct in-cluster mode** (PR #156) via `MAILBOX_PG_HOST` (+`MAILBOX_PG_PORT`) or `MAILBOX_PG_DIRECT=1` — for a future in-cluster agent |
| Tables | `snapshots` (one row/run: `id,captured_at,host,days_window,telemetry_available`); `initiative_snapshot` (**append-only**, one row/initiative/run: slug/repo/title/momentum/last_touch/commits/merged_prs/open_prs/session_count/telem_*/summary/current_doc/open_investigations/docs + recent_messages/recent_commits jsonb); `recaps` (standalone: `repo,slug` + `identity`/`identity_hash` + `status`/`status_hash` + legacy `recap`); `assistant_log` (standalone audit); **`archived`** (standalone: `repo,slug` PK + `title`/`reason`/`archived_at` — the Phase-2 done/archive set; in NO view → no VIEW_VERSION bump) |
| Views | **`latest`** = rows from `max(snapshot_id)` (ghost-free; the **viewer** reads this). **`current`** = `DISTINCT ON (repo,slug)` newest across ALL history → includes aged-out "**ghosts**" (the **router** reads this — matching dormant initiatives is desirable). Both DROP+CREATE-guarded by a `COMMENT ON VIEW` version marker (`VIEW_VERSION`/`LATEST_VIEW_VERSION`, both `v6`) |
| Sync | `initiatives-sync.timer` (`systemd --user`, **workbench**, ~15min, `OnUnitActiveSec=15min`) → `run-sync.sh` → `sync.py --days 5` → `initiative-scan.py --json`. **Lookback = 5d** (2026-07-27, Zach's call — a tight recent board; 14d was too cluttered at ~105 cards, 5d ≈ 48; override via `INITIATIVES_SYNC_DAYS`). Momentum buckets are absolute last-touch ages (<2d active / 2-7d slowing / ≥7d stalled), so **at 5d "stalled" (≥7d) is structurally EMPTY and "cooling"/slowing (2-5d) is the going-quiet triage signal**; widen the window if deep-stalled work is wanted. Double-gated `serverMode && enableInitiativesSync`. **Timeout 600s** (headroom for a cold-recap batch on a widened window; warm runs ~6-15s). 90-day retention prune each run |
| Sync creds | `run-sync.sh` **runtime sops-decrypts** the ClickHouse **reader** password from homelab-talos trunk (`clusters/homelab/apps/activity/secrets.enc.yaml`, host age key) → `CLICKHOUSE_*` exported → scan runs **telemetry-on**. Every step guarded → missing key/repo/sops ⇒ telemetry-off (still writes a useful handoff+git snapshot). `--input-type yaml` is REQUIRED (mktemp has no `.yaml` ext) |
| Viewer | `initiatives-viewer.service` (`systemd --user`, **workbench**, serverMode-gated, `Restart=on-failure`) → `run-viewer.sh` → `viewer.py` (stdlib `http.server`). **LIVE at http://192.168.50.250:8899/** |
| Viewer bind | ⚠ binds the workbench **eth1 LAN IP `192.168.50.250`** — **NOT `192.168.50.94`, which is a HOMELAB node** (kube-apiserver/NodePorts/ClickHouse); binding `.94` → `OSError: Cannot assign requested address` crash-loop (bit us in #140→#141). See `[[workbench-lan-ip]]`. Internal work data — deliberately NOT wired to the public gateway |
| Viewer board ("triage" board) | **Default view GROUPED by repo** (collapsible; `VIEW_KEY` v3; flat/recency in the toggle). Cards are **two-line collapsed** (state glyph + slug + title + age; one actionable `line2`) → **click to expand** the full current/start/you/live/PRs/investigations detail. **`derive_state`** per card (precedence `needs_you > stalled > slowing > active`; ⚠ needs_you=orange, ◑ stalled=gray ≥7d, ~ cooling=yellow 2-7d, → active=blue). **`live` is a separate OVERLAY BADGE** (● green, render-time tmux overlay via `buildLiveNow`), NOT a state. **Layout = 4 sections:** §3 `⚠ Needs you` **PINNED top** (rendered once, excluded from groups) → §4 **`● N live · newest:<task>` ONE-LINE strip** (`LIVENOW_OPEN_KEY='-v2'`, click→top-6 `＋more` activity-sorted; rows clickable→`focusCard`; scoped by the active filter) → §5 active cards grouped by repo (**live-badged float to top**, stable `vis.sort`) → §6 `~ Cooling` collapsed fold (slowing+stalled). **State chips** `[⚠ Needs you][◑ Stalled][~ Cooling][→ Active][All]` (filter, compose AND with search; `⚠ Needs you` pulses when >0) + a **SEPARATE `[✓ Archived]`** view toggle. **Search** (`matchQ`) AND-composes with the chip, scopes the Live-now rows too, and **auto-widens to All** when a filter hides every hit (`shouldWidenFilter`, sticky); the active chip stays visible+highlighted at 0; matches show a `match: …snippet…` reason. **Per-card actions state-driven** (`cardActions`): needs_you `[resolve][⤴ dispatch?][⤓ archive]` (`[resolve]` prefills+submits the `/api/ask/stream` sidebar), stalled/cooling `[⤴ resume?][drop][⤓ archive]` (`resume`=`/api/dispatch` with a RESUME-framed body via `dispatch.py _task_lead`), active `[⤴ dispatch?][⤓ archive]` (`?`=only when a grounded rec exists); `drop`/`⤓ archive` are **two-tap** (`armConfirm`). **`needs_you` is SEVERITY-aware** — `assistant.SEVERITY_MARKERS` over `status`+`next_step` promote an active RISK card with a `⚠ risk` cue (single-sourced from `assistant._severity_hits`; `tool_blocked_on_me` = `_blocking_hits OR _severity_hits`, so `/api/ask` "blocked/at-risk" has PARITY with the chip). **Emerging/undocumented = an inline `emerging` badge**; the glyph **legend is behind a `?`** toggle (`.legend-toggle`, hidden by default). **ARCHIVE lifecycle:** `POST /api/archive {repo,slug,reason?}` (viewer-side, never-500) hides the card + persists to the standalone `initiatives.archived` table (`archive.py`: `archive`/`unarchive`/`read_archived`/`list_archived`); **suppress IFF archived AND `last_touch <= archived_at`** → **auto-resurfaces on new activity**; `[✓ Archived]` opens the archived view (`POST /api/unarchive` restores). ⚠ `load_latest()` returns **`(rows, archived)`** — 2 callers (`DataProvider.snapshot`, `assistant.load_initiatives`) handle the tuple. Plus recaps (identity primary / status secondary), a `POST /refresh` ↻, `POST /api/dispatch`, the `POST /api/ask` sidebar. Full dogfood-evolution history: `devrc/claudedocs/handoff-initiatives-nextstep-dispatch-shipped-2026-07-26.md`. |
| Recap model | **`vllm-recap`** — homelab vLLM, **ns `promptver`, svc `vllm-recap:8000`, served model `recap` = Qwen2.5-7B-Instruct-AWQ**. Wired via the unit env (`INITIATIVES_RECAP_ENABLED=1`, `RECAP_NAMESPACE=promptver`, `RECAP_SERVICE=svc/vllm-recap`, `RECAP_SERVICE_PORT=8000`, `RECAP_MODEL=recap`) — recap.py's in-code defaults are PLACEHOLDERs |
| Router | `route.py` — `route(signal,repo,limit)` / `rank_matches()` / `classify()`. Reads `initiatives.current`; scoring single-sourced from the scan's `best_title_match` (word-equality, no stemming). Read-only, suggests-never-acts. Wired into repo-cos digests (#138) + mail-actions (#139, adds `related_initiative` col) |
| Assistant (Phase 1 agent) | **PRIMARY `/api/ask` = a model-driven OpenClaw devpod** (homelab ns `devpod-initiatives`, svc `initiatives-devpod:18789`, `openclaw/initiatives`, **DeepSeek V4 Pro via OpenRouter**). The MODEL selects which deterministic skill-tool(s) to run (incl. MULTIPLE for compound Qs) — the tools are `scripts/initiatives/skills/query.py` (reuses assistant.py's `run_tool`/`build_facts`/`sources_of`), reached via `_db.py` **direct in-cluster mode** (#156) with a **least-priv `initiatives_agent` PG role** (SELECT-only on `initiatives.*`). Viewer's `agent_client.py` proxies via kubectl port-forward + gateway token `sha256("gw-"+HOOKS_TOKEN)`, **streams** the answer (SSE `/api/ask/stream`) and **renders markdown**; **graceful FALLBACK** to the deterministic regex `assistant.py` if the devpod is down. Every ask audit-logged to `initiatives.assistant_log` (`intent=agent`). Manifests: `homelab-talos clusters/homelab/apps/agent-pods/initiatives/`. This RETIRED the brittle regex classifier from the routing role (kept only as fallback). Phase 2 (write/dispatch) stays deferred behind a structural server-side gate. Full arc: `handoff-initiatives-agent-phase1-2026-07-24.md` |

## status
```bash
export KUBECONFIG=$KC_HOMELAB   # homelab-kubeconfig; the store is only reachable via port-forward here
# units (workbench):
systemctl --user status initiatives-sync.timer initiatives-viewer.service --no-pager | head -30
systemctl --user list-timers | grep initiatives
journalctl --user -u initiatives-sync.service -n 30 --no-pager      # last sync; grep -i telemetry for on/off
curl -sf http://192.168.50.250:8899/healthz; echo                   # viewer health
# is the store fresh? (read via _db.py port-forward)
PSQL='kubectl -n mailbox exec mailbox-postgres-0 -- psql -U mailbox -d mailbox -c'
$PSQL "select id,captured_at,host,days_window,telemetry_available from initiatives.snapshots order by id desc limit 5;"
$PSQL "select count(*) from initiatives.latest;"                    # viewer's set (ghost-free)
$PSQL "select count(*) from initiatives.current;"                   # router's set (may exceed latest by N ghosts)
$PSQL "select repo,slug,momentum,last_touch from initiatives.latest order by last_touch desc limit 15;"
$PSQL "select repo,slug,left(identity,60),left(status,50) from initiatives.recaps order by slug limit 10;"
```

## operate

```bash
# run a sync NOW (both routes go through run-sync.sh → telemetry-on):
KUBECONFIG=$KC_HOMELAB systemctl --user start initiatives-sync.service
#   confirm telemetry-on: journalctl --user -u initiatives-sync.service | grep -i "telemetry\|recap"
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
# agent devpod status/logs/gateway (homelab):
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

# --- NEXT-STEP RECOMMENDATION + DISPATCH (Phase 2a, shipped 2026-07-26, origin/main 835fe0c) ---
# Every card carries a GROUNDED recommended next step (nextstep.py, read-only): documented cards show
# their parsed `next_step`; EMERGING cards show a distinct `next (suggested) ›` line inferred from a real
# field (open-PR/investigation/last-prompt/status/stalled) — NEVER invented. Chat: "what should I do next
# on <initiative>" → the `recommend_next_step` tool (agent + regex fallback).
# ONE-TAP DISPATCH → a clawgate Task card (viewer-side; the VIEWER holds ~/.claude/clawgate.env, the
#   in-cluster devpod does NOT). Two human gates: this creates a card; you still tap Dispatch in clawgate.
curl -s -X POST http://192.168.50.250:8899/api/dispatch -H 'Content-Type: application/json' \
  -d '{"repo":"<repo full path OR repo_name>","slug":"<slug>"}'   # 200 {ok,task_id} / 400 / 404 / 502
# dispatch.py mirrors repo-cos/clawgate.py: POST clawgate /api/tasks {directory(=label),body,repo?} —
#   model OMITTED → clawgate default deepseek. Confirm the card: GET {CLAWGATE_API_URL}/api/tasks (Bearer).
# Full arc: devrc/claudedocs/handoff-initiatives-nextstep-dispatch-shipped-2026-07-26.md
```

## deploy a change
```bash
# scripts (sync.py/route.py/assistant.py/recap.py + wrappers) ship via home-manager:
home-manager switch --flake ~/workspace/devrc --impure          # workbench; or scripts/ship.sh after merge
# after a viewer.py change, restart the long-running service (X-Restart-Triggers covers viewer.py+run-viewer.sh
# on switch, but restart explicitly if you edited out of band):
systemctl --user restart initiatives-viewer.service
# to regenerate the store immediately (e.g. after a sync.py or recap change):
KUBECONFIG=$KC_HOMELAB systemctl --user start initiatives-sync.service
# tests: scripts/initiatives/tests/ via nix-shell pytest (pure fixtures; query/agent_client/assistant/sync)

# --- AGENT DEVPOD deploy (homelab) ---
# GitOps record is on homelab-infra `origin/trunk` (verified: the 0.7.x-values hardening + 2026.6.11-py
#   initiatives helmrelease + task-drafter hardening are all ancestors of origin/trunk). ⚠ the LOCAL
#   ~/workspace/homelab-talos checkout may be STALE/dirty (another session left uncommitted files + a
#   behind `trunk`) — always base edits on `origin/trunk` (fetch first), never the local working copy.
#   agent-pods flux is suspend=true, so trunk changes are applied SURGICALLY by hand (below).
# query.py/skill ship in devrc → the devpod autoPulls `main` (~5min) — merge to devrc main, done.
# HelmRelease / secret / identity-pin (extraInitCommands) changes: edit the manifest, then apply
# SURGICALLY (agent-pods flux Kustomization is suspend=true → git won't auto-apply):
KUBECONFIG=$KC_HOMELAB kubectl apply -f ~/workspace/homelab-talos/clusters/homelab/apps/agent-pods/initiatives/helmrelease.yaml
KUBECONFIG=$KC_HOMELAB kubectl -n flux-system annotate helmrelease initiatives-agent reconcile.fluxcd.io/requestedAt="$(date +%s)" --overwrite
# secret is sops-encrypted in git BUT the workbench lacks the homelab age PRIVATE key → apply the
#   live secret from plaintext via kubectl (git .enc.yaml is the GitOps record); encrypt with
#   `sops --encrypt --age <homelab-recipient> --config /dev/null` (no agent-pods creation_rule).
```

## ⚠ gotchas (each cost real time)
- **Hardening = first-class kubeclaw 0.7.x chart VALUES (postRenderers + hand-written CNP RETIRED).**
  The chart (kubeclaw ≥0.7.0) exposes the three knobs the initiatives agent uses directly in `values`:
  **`securityContext`** (toYaml full-replace → `capabilities.drop:[ALL]` + `allowPrivilegeEscalation:false`
  + seccomp `RuntimeDefault`; CapEff=0 verified), **`tls.verify:true`** (→ `NODE_TLS_REJECT_UNAUTHORIZED=1`,
  overriding the chart's historical hardcoded `0`), and **`networkPolicy`** (Cilium egress default-deny +
  per-agent FQDN allowlist: kube-dns[+rules.dns]/apiserver/mailbox-postgres:5432/openrouter.ai/github.com/
  api.github.com/codeload/*.githubusercontent/*.debian.org on 443+80). Verify in-pod: `curl openrouter.ai`→200,
  `curl example.com`→blocked. This REPLACED the old `spec.postRenderers` strategic-merge (needed because
  `extraEnv` duplicate-keys made SSA reject the Deployment) + the standalone `network-policy.yaml` — both
  DELETED (functional no-op: identically hardened, cleaner). ⚠ `networkPolicy.enabled` with an EMPTY
  allowlist silently bricks egress to DNS+API only — always set the full `egress.fqdns`/`endpoints`
  (kubeclaw 0.7.1 adds a fail-loud guard for this; 0.7.0 does not). `cap-drop:[ALL]` is safe ONLY because
  deps are baked (no dpkg at runtime); **non-root is DEFERRED** — the chart hardcodes `/root/.openclaw|.ssh|
  .kube` writes with no runAsUser knob (needs a chart change).
- **Image `2026.6.11-py` + web-search OFF (the two are coupled).** query.py's deps (psycopg2/requests) are
  BAKED into the derived image (`initiatives/image/Dockerfile`, base `ghcr.io/zacxdev/openclaw-image`) so
  there is NO apt-at-init to race the Cilium FQDN/DNS warmup — this is why `cap-drop:[ALL]` is safe.
  **`tools.web.search.enabled:false`** is load-bearing for booting 2026.6.11 under the locked egress: with
  search on, `openclaw doctor --fix` (pre-gateway) auto-enables a brave/perplexity plugin and does a blocking
  `npm view @openclaw/<plugin>` fetch to registry.npmjs.org (NOT allowlisted) → doctor hangs → gateway never
  binds :18789 → crash-loop. Off = no plugin auto-fetch → doctor completes offline → gateway binds. The
  read-only Q&A agent never needs web search anyway; egress stays locked (no npm allowlist added).
  `config.updateCheckOnStart:false` is set belt-and-suspenders. Rollback tag `2026.6.1-py` (a first 2026.6.11
  attempt rolled back before web-search-off was found). The shared `openclaw-image` base rebuilds via
  `--legacy-peer-deps` (npm arborist `edgesOut` bug on node:22-slim, openclaw-image #3). To bump OpenClaw
  further: keep web-search off + egress locked, verify the gateway binds. (Historical: if you ever reintroduce
  apt-at-init it RACES the Cilium policy — baking deps mooted this.)
- **Agent identity must be PINNED or first-person questions confabulate.** OpenClaw ships a generic
  "figure out who you are" onboarding (`BOOTSTRAP.md` + empty `IDENTITY.md` in `/data/workspace`). Without
  a pin, "what am **I** working on" made the agent read BOOTSTRAP and answer about ITSELF ("fresh start, who
  are you?"). Fix (in `extraInitCommands`): `rm BOOTSTRAP.md` + overwrite `AGENTS.md`/`IDENTITY.md` so the
  agent is "already born" as the read-only initiatives assistant and "I/me/my/you" = **Zach**. Clearly-matching
  Qs ("blocked on me", "whats stalled") routed to the skill fine; ambiguous first-person ones did not.
- **Agent DB reach = least-priv SELECT-only role → the audit log write stays VIEWER-side.** The
  `initiatives_agent` PG role is SELECT-only; `CREATE TABLE IF NOT EXISTS` (assistant.py's log self-heal)
  needs CREATE on the schema **even when the table exists** (it errors before the existence check), so the
  agent can't write `assistant_log`. `agent_client._log_agent_ask` writes it from the viewer (full mailbox
  creds). Don't grant the agent role INSERT to "fix" a missing audit row — check the viewer path.
- **Repo-name tokens are STRIPPED before a live tmux pane → initiative match** (2026-07-28). `best_title_match`'s `title_overlap >= 2` gate was clearing on the REPO-NAME tokens alone (civitai-manager → {civitai, manager}) — which every session AND initiative in the repo shares — so a generic pane ("Continue civitai-manager development work") got badged `● live` on an unrelated card (the SECURITY-AUDIT one) while that session did other work. Fix in `match_tmux_to_initiatives`: `ptoks -= set(text_tokens(os.path.basename(repo)))` so a pane must share a DISTINGUISHING word to attach; a pane with none falls to `live_unmatched` (honest "live but untied" miss, per the scan's "a wrong tag costs more than a miss"). ⚠ **the VIEWER caches the scan module in memory** (`attach_tmux` reuses `scan.match_tmux_to_initiatives` verbatim; importlib-loaded once at startup) — a scan-only change ships via `home-manager switch` but the viewer's X-Restart-Triggers only cover `viewer.py`+`run-viewer.sh`, NOT `initiative-scan.py`, so you MUST `systemctl --user restart initiatives-viewer.service` for a scan/matching fix to take effect in the live overlay (a fresh sync alone won't do it — the live badge is a render-time overlay, not stored).
- **Momentum times from the last genuine USER-turn timestamp, NOT the transcript file mtime** (PR #149).
  Claude Code rewrites session `.jsonl` files in place (title/mode metadata) → mtime-driven momentum read
  18-day-idle sessions as "active ~47m ago". `initiative-scan.py` parses `turns[].ts` for the last user turn;
  mtime is a rare fallback only (mirrors `doc_touch_epoch`). Buckets: **<2d active / 2–7d slowing / ≥7d
  stalled**; the scan's ~4-day window filters older initiatives out of the view.
- **Schema migration = DROP+CREATE the views, never CREATE OR REPLACE.** Adding a column to
  `initiative_snapshot` reorders the `latest`/`current` views' columns; `CREATE OR REPLACE VIEW` REJECTS a
  column-name/order change (`cannot change name of view column …`) → froze the whole store on the v1→v2
  deploy. Fix: **bump the `VIEW_VERSION`/`LATEST_VIEW_VERSION` marker** so `_ensure_view` does DROP VIEW IF
  EXISTS + CREATE (ACCESS EXCLUSIVE, momentary). `recaps` + `assistant_log` are **standalone tables** (in no
  view) → adding columns there needs NO view-marker bump. **Validate a migration by replaying it on a
  throwaway Postgres first** — the unit fixtures only assert SQL strings, they can't catch this.
- **Viewer bind IP** — `192.168.50.250` (workbench eth1), **NOT `192.168.50.94`** (a homelab node). See the
  Key-facts row; crash-loops if wrong and a 127.0.0.1 smoke test won't catch it.
- **Recap VRAM contention** — `vllm-recap` shares a single 5080 (16.3GB, time-sliced ×4); to free VRAM,
  **`vllm-joycaption` + `comfyui` are scaled to `replicas: 0` in homelab git**. Scale them back for a
  captioning/comfy pass — but VRAM then contends with recap. Recap is **best-effort**: model down → the
  sync/store are unaffected and the card falls back to the deterministic `summary`.
- **Recap identity vs status** (PR #154): the recap is **two independently-cached fields** — `identity`
  ("what it is", from the handoff's durable head, hash-keyed on the HANDOFF so it's stable across prompt
  churn) + `status` ("current", hash-keyed on recent activity). Two `vllm-recap` calls. Viewer renders
  identity primary (fallback identity→recap→summary), status secondary.
- **`_db.py` importlib load** — never add `mail-actions/` to `sys.path` (its `llm.py` shadows repo-cos's and
  breaks synthesis). route/assistant/sync all load it by explicit path.
- **Assistant is read-only by design** — no write/dispatch/devpod/MCP; worst prompt-injection case is a
  skewed but grounded answer, never an action. The Phase-2 write/dispatch path is DEFERRED behind a
  **structural** server-side write-gate (NOT the voluntary `agent_checkpoint`) + a least-privilege DB role.
```
