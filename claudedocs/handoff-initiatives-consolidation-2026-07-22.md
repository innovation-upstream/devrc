# Handoff — initiatives consolidation (live store for a viewer + router), 2026-07-22

**Goal:** consolidate the on-demand `initiative-scan.py` output into a durable, queryable
Postgres store so apps can build on live initiatives data. First two use cases: a **live
viewer** and a **router** (route an incoming signal — a new task / repo-cos proposal /
mail-action — to the right existing initiative). Router is the reason it's Postgres and not
a JSON cache: it joins against `mail_actions` / clawgate tasks, which already live in the
homelab `mailbox` Postgres.

## Status — ALL THREE PHASES + BOTH WIRINGS SHIPPED & LIVE-VERIFIED (2026-07-22)

**store → router → viewer, all reading one consolidated Postgres dataset. Fully live.**

- **Initiatives agent — Phase 1 (PR #158, merged, deployed, live-verified) + gaps (PR #156).** Researched
  containerized-agent best practice + Zach's claw stack (kubeclaw/openclaw-image/clankup/support-agent/
  clawgate/crabbox) → proposal (`claudedocs/initiatives-agent-proposal-2026-07-24.md`) → independent
  **red-team** (`…-eval-2026-07-24.md`, verdict *sound-with-caveats*): the proposed write-gate was
  **voluntary** (prompt-injection-defeatable) + cross-cluster shared-DB blast radius under-scoped + the
  devpod over-built for read-only. **Re-scoped Phase 1 = a READ-ONLY assistant** (`scripts/initiatives/
  assistant.py`): deterministic regex intent-classifier (11 intents) over the store + `route()`, the local
  `vllm-recap` model only phrases answers, sources computed from tool output (not the model). Sidebar chat
  = `POST /api/ask` + a collapsible pane in the viewer. **NO write/dispatch/devpod/clawgate/MCP** — worst
  prompt-injection case is a skewed answer, never an action. Live: "what's blocked on me" → the truly-blocked
  initiatives; routing works. **crabbox** = an openclaw execution *control plane* (leases box/rsync/run/
  release), NOT an isolation tech → wrong shape for a persistent assistant (rejected as runtime; possible
  future runner for dispatched CI/verify jobs). **Gaps fixed (#156):** clawgate task `title` was a CLIENT
  bug (clawgate `/api/tasks` has no `title` field; title renders from `directory`) → send it as `directory`;
  `_db.py` gained an additive opt-in **in-cluster direct-DB mode** (`MAILBOX_PG_HOST`/`MAILBOX_PG_DIRECT`) for
  a future in-cluster agent. **DEFERRED to Phase 2+ (needs its safety story first):** the openclaw-devpod
  runtime, dispatch (via clawgate `/api/tasks` — structurally safe: card only), and STATE MUTATION — which
  must have a **structural server-side write-gate** (not the voluntary `agent_checkpoint`) + a least-privilege
  scoped DB role before it ships.

- **Recap identity/status split (PR #154, merged, deployed, live-verified).** The single recap
  conflated *what a project is* with *what was just done in it* — `remix-session` (a video-remix
  platform) recapped as "focusing on cloudflare reliance" (a recent tangent) because it over-weighted
  `recent_messages` and barely used the handoff. Fix (user-chosen: feed the handoff, stay LOCAL, split,
  no journal): recap is now **two independently-cached fields** — **`identity`** ("what it is",
  generated from the handoff's durable head via `identity_blob` which stops at the first volatile
  heading; cache-keyed on the HANDOFF so it doesn't churn as prompts change) + **`status`** ("current",
  from recent activity; cache-keyed on activity). Two separate `vllm-recap` calls, additive columns on
  the standalone `initiatives.recaps` table (identity/identity_hash/status/status_hash — NO view
  migration), viewer renders identity primary (fallback identity→recap→summary) + status secondary.
  Live: remix identity = "explore, stash, publish… age-gating + moderation" (cloudflare GONE from
  identity). ⚠ **Known residual:** `status` still inherits the thin-context/boilerplate-prompt vagueness
  (now on the secondary line, lower harm) — fix if needed via the same substantive-prompt filter as the
  card face. dspy-eval/ (#148, report-only) still imports the retired single-recap API — harmless (not
  run by run-tests.sh), left as the point-in-time eval record.

- **Coverage fix (PRs #150 viewer + #151 matcher, merged, deployed, live-verified).** A reconciliation of
  the viewer's live-tmux overlay against RAW tmux (44 live claude panes) found the overlay was **precise
  (0 phantoms) but low-recall — showed 7 of ~44 threads**. Root: the viewer COMPUTED the `unmatched` live
  panes (`match_tmux_to_initiatives` returns them) but **discarded** the list; ~90% of live work is in the
  `datapacket-talos` client monorepo with no handoff → structurally uncoverable. Fixes: (#150) the viewer
  now renders a "**Live sessions — not tied to an initiative**" section (collapsible, by repo) — board went
  from 7 → **40** visible live threads (7 tagged + 33 unmatched); + multi-pane `live_task` shows all panes.
  (#151) date-only-slug **title-fingerprint fallback** (`2026-07-21` now matches its comfyui panes;
  strictly additive, `is_real_slug` rank guard so a fallback can't steal a pane). **Deliberately NOT fixed
  (precision > recall):** the remix sibling-dilution tiebreak was built, proven to create a `civitai`-token
  phantom (structurally indistinguishable from the good case without a RULES-forbidden keyword stoplist),
  and REVERTED with a regression test; cross-repo `civitai-cli` mis-filing deferred as a data-modeling
  issue (handoff filed under datapacket-talos, work in civitai-manager). **Strategic takeaway:** the durable
  board is handoff-anchored by design; the live-sessions catch-all is the right answer to "show everything
  running" — do NOT model the ephemeral client firehose as initiatives.

- **Momentum-accuracy fix (PR #149, merged, deployed, live-verified).** `last_touch`/momentum was
  driven by the transcript **file mtime**, which Claude Code clobbers with in-place session-file
  rewrites (title/mode metadata) → 18-day-idle sessions read as "active ~47m ago" (root-caused via
  `root-cause-analyst`; 3 confirmed false-actives, 14/15 "active" were mtime-driven). Fix: time
  `last_session` from the **last genuine USER-turn timestamp** (`_read_session_turns` already parses
  `turns[].ts`; threaded through `collect_session_records`→`build_report`→`attribute_sessions`),
  mtime only as a rare fallback — mirrors the `doc_touch_epoch` precedent. Live: clawgate-chat-polish
  + sysredis-buffer + dp-500-sweep correctly aged out of the 4-day window (real last-touch 9–18d);
  snapshot dropped 24→19 rows, active count now truthful. **DSPy recap eval (PR #148, merged,
  report-only): measured DSPy vs the hand prompt → within-noise (+0.012 ±0.011); kept the prompt.**

- **Phase B — LLM recap (SHIPPED, deployed, live-verified).** Cards now lead with a synthesized
  plain-language recap of what each initiative is + where it stands.
  - **B1 — homelab model** (homelab-infra PR #193): a vLLM serving **Qwen2.5-7B-Instruct-AWQ** as
    `svc/vllm-recap:8000` (ns `promptver`, served model `recap`), patterned on `vllm-joycaption`.
    ⚠ The GPU is a SINGLE shared 5080 (16.3GB, time-sliced ×4) — 7B not 14B, and **`vllm-joycaption`
    + `comfyui` were scaled to `replicas: 0` in git** to free VRAM (both idle; joycaption's manifest
    already documents this scale-0/1 pattern). If you need them back for a pass, scale up — but VRAM
    then contends with recap. Endpoint verified live (real completion).
  - **B2 — generator/store/viewer** (devrc PR #146 + the config-wire commit): `scripts/initiatives/recap.py`
    generates a recap per initiative **in the sync**, **best-effort** (model down → sync/store unaffected,
    card falls back to `summary`), **cached + regenerated only on input-hash change**, stored in a
    standalone `initiatives.recaps` table (NO view migration — markers stay v3). **Anti-confabulation**
    enforced structurally (model sees only the context JSON). Viewer renders `recap || summary` as the
    primary line; **the `N commits · N merged · N sess · N ev` stat strip is removed** (per Zach). Config
    lives in the `initiatives-sync` unit env (`INITIATIVES_RECAP_ENABLED=1`, `RECAP_NAMESPACE=promptver`,
    `RECAP_SERVICE=svc/vllm-recap`, `RECAP_MODEL=recap`). Live: 24/24 recaps generated (snapshot #40,
    `recap 24 new/0 cached`), legible + status-aware (e.g. spend-analytics → "awaiting Zach's monthly
    Cloudflare cost figure"), anti-confab held. Weak recaps only where input context is thin (fall back
    to honest, not wrong). The 15-min sync regenerates only changed initiatives.

- **PR #143** (merged, deployed, live-verified) — **card legibility Phase A (deterministic, no LLM)**:
  "bring the conversation onto the card". Adds per initiative — **recent sent messages** (user's own
  prompts, read from `~/.claude/projects` transcripts, attributed via the scan's existing genesis +
  branch/cwd session matching; new `recent_messages jsonb` col), **recent commit subjects** (new
  `recent_commits jsonb` col), and a render-time **live-session task** line (the matched tmux pane
  title). Card face shows summary + latest prompt + live task; expand adds the full message/commit
  lists. Migration v2→**v3** (marker bump → DROP+CREATE views; replayed on a throwaway PG 18.4 first,
  then applied live — snapshot #31, 20/24 initiatives now carry messages). Live cards are now genuinely
  legible (real prompts like "relabel the node as web", "fix bad-eyes then launch round 3").
  **Precision fix — PR #145** (merged, deployed, live-verified): message attribution is now
  **single-best-credit** (each session → the most-specific initiative via `best_matching_initiative`'s
  ranking, factored into `_specificity_key`; `sess:` counts unchanged) + the card FACE shows the most
  recent **substantive** prompt (viewer-side `_is_trivial_prompt`/`pick_face_message`, skips
  dispatch/proceed/short boilerplate; expand keeps the full verbatim list). Verified live: the genuine
  multi-credit dup is GONE (the Comfy-Cloud resume msg now on ONE card, not 3).
  ⚠ **Residual (honest, NOT the bug):** Zach's **session-ritual prompts** ("give me the kickoff message…",
  "review work done this session…") are genuinely typed across many sessions, so they legitimately appear
  on several cards (each single-credited to its own initiative) and can occupy a card FACE — the
  length/stopword filter can't catch them without brittle keyword-whacking (against RULES). **Next clean
  deterministic lever:** pick the face prompt by **token-overlap with the initiative's own slug/title**
  (structural, reuses the matcher) so the face prefers the on-topic prompt over a ritual one. If that's
  still insufficient, that's the **Phase B trigger**.
  **Phase B (LLM recap) NOT built** — homelab-served model, cached/regen-on-change, deterministic-first
  per Zach; the ritual-prompt residual above is the concrete evidence that would justify standing it up.

- **PR #142** (merged, deployed, live-verified) — **viewer v2 feedback round**: (1) **flat view default**
  + flat/grouped toggle (localStorage) + client-side search, ordered most-recently-active, repo label
  per card; (2) **realtime**: sync cadence 1h→**15min**, a debounced/single-flight **`POST /refresh`**
  button (runs `run-sync.sh` as a subprocess, killpg on timeout, 60s debounce, scrubbed stderr; LAN-open
  + rate-limited by choice — no auth), honest footer (`live ● realtime · store synced Xm ago`); (3)
  **legible cards**: a deterministic **`summary`** field parsed from each handoff (new scan→store column,
  no LLM) + **PR titles** + **click-to-expand** that live-reads the handoff doc (full next-steps +
  open-investigations; realpath-allowlisted under `<repo>/claudedocs/`, 512KB cap). ⚠ **Migration gotcha
  (audit-caught, fixed):** the new `summary` column reorders the `latest`/`current` views' columns, which
  `CREATE OR REPLACE VIEW` REJECTS (`cannot change name of view column …`) → froze the whole v1 store on
  deploy. Fixed to **DROP+CREATE on a version-marker bump** (`_ensure_view`); validated by replaying the
  real v1→v2 migration on a throwaway PG 18.4 (fixtures can't — they only assert SQL strings). Live:
  summary populated, refresh runs a real sync + debounces, detail endpoint reads handoffs, traversal
  guard returns 404. Minor open polish: `parse_summary` sometimes keeps a leading `> ` blockquote marker.

- **PR #140** (merged, deployed) — **Phase 3, the live web viewer**: `scripts/initiatives/viewer.py`,
  a stdlib-`http.server` service. Grouped by repo, momentum badges, next-step, PRs, **live-tmux
  overlay at render time** (reuses the scan's tmux funcs), auto-refresh 30s, gruvbox, LAN-only.
  Workbench `systemd --user` service (`initiatives-viewer.service`, serverMode-gated). Adds an
  **`initiatives.latest` view** (rows from `max(snapshot_id)`) to sync.py's DDL to kill the ghost
  problem — the viewer reads `latest` (inline-fallback before the first post-deploy sync). Router
  keeps reading `current` (ghosts are desirable there). **LIVE at http://192.168.50.250:8899/**
  (service active; `/healthz`→ok; renders real data + live tmux tags; `latest`=23==snapshot#12,
  `current`=25 so 2 ghosts correctly excluded).
- **PR #141** (merged, deployed) — **viewer bind-IP fix**. #140 hardcoded
  `INITIATIVES_VIEWER_HOST=192.168.50.94` → crash-loop `OSError: Cannot assign requested address`.
  **192.168.50.94 is a HOMELAB node (kube-apiserver/NodePorts/ClickHouse), NOT the workbench** —
  the workbench's own LAN IP is **192.168.50.250 (eth1)**. Caught on live deploy-verify (the
  pre-merge worktree smoke test passed only on 127.0.0.1). See [[workbench-lan-ip]].
- **PR #138** (merged) — **router → repo-cos**: tags each synthesized proposal with its related
  initiative in the digest ("↳ relates to: <slug>"), surface-only, best-effort (store outage →
  digest byte-identical). `scripts/repo-cos/routing.py`.
- **PR #139** (merged, ALTER applied) — **router → mail-actions**: tags each extracted action with
  its related initiative. **Added an additive/nullable `related_initiative text` column to the live
  `mail_actions` table** (idempotent, same pattern as `thread_key`; verified PRESENT in prod).
  Surfaced in `extract.py list`, the clawgate card `source_ref`, and a `routed` run-counter.
  Best-effort (router/DB failure → action queued untagged). `MailDB.fetch_current_initiatives()`
  reads `initiatives.current` on the already-open connection (no 2nd port-forward).

### Phase 1 (sync) + Phase 2 (router) — SHIPPED earlier same day

- **PR #137** (merged, `e2ac53e`) — **Phase 2, the router**: `scripts/initiatives/route.py`.
  `route(signal_text, repo=None, limit=5)` ranks a free-text signal (task title / repo-cos
  proposal / mail subject) against `initiatives.current`, reusing the scan's `best_title_match`
  gate verbatim (lazy importlib load — no `chquery` side-effect until a match runs). Returns a
  score + `confident` flag; `classify()` → "confident match: <slug>" vs "likely new work".
  Read-only, **not wired to any caller** (Zach's pick — ship the primitive, eyeball via CLI
  first). 18 tests. LIMITATION: word-equality, no stemming (`polish`≠`polishing`) — inherited
  from the scan; callers feeding raw prose should expect misses on morphological variants.

### Phase 1 (below) — SHIPPED + ENABLED + live-verified

- **PR #135** (merged, `209b59f`) — the sync: `scripts/initiatives/sync.py` shells out to
  `initiative-scan.py --days N --json` (no `--tmux`), transforms → rows, writes to a new
  `initiatives` schema in the `mailbox` Postgres via `mail-actions/_db.py`'s kubectl
  port-forward. Self-migrating idempotent DDL under a `pg_advisory_xact_lock`. Workbench-only
  `systemd --user` timer, hourly.
- **PR #136** (merged, `2dfb870`) — provisions the ClickHouse **reader** creds into the unit
  at runtime (sops decrypt in `run-sync.sh`, host age key, no plaintext at rest, degrades to
  telemetry-off if key/repo/sops absent) and flipped `enableInitiativesSync = true`.
- **Deployed** to workbench (`home-manager switch`); timer armed. Verified live: unit ran via
  its own cred path → `telemetry-on`, wrote snapshots #2/#3 (both telemetry_available=true,
  host=workbench). Two near-concurrent runs both succeeded → advisory-lock DDL race fix holds.
- **DSN role has `CREATE SCHEMA`** (the audit's top first-run risk) — confirmed by the live write.

### Schema (`initiatives` schema in the `mailbox` DB)
- `snapshots` — one row per run (`id, captured_at, host, days_window, telemetry_available`).
- `initiative_snapshot` — one row per initiative per run (slug/repo/title/momentum/last_touch/
  commits/merged_prs/open_prs jsonb/session_count/telem_*/current_doc/open_investigations jsonb/
  docs jsonb). Indexes: `(repo,slug,snapshot_id)`, `(snapshot_id)`, `snapshots(captured_at)`.
  FK `ON DELETE CASCADE`. 90-day retention prune each run.
- `current` view — `DISTINCT ON (repo,slug)` newest across ALL history; guarded re-create via
  a `COMMENT ON VIEW … 'initiatives-sync view v1'` version marker (bump `VIEW_VERSION` in
  sync.py to force a recreate after a hand-edit).

### Operate
- By hand: `KUBECONFIG=$KC_HOMELAB systemctl --user start initiatives-sync.service` (or run
  `scripts/initiatives/sync.py --dry-run` under `nix-shell -p "python3.withPackages(p:[p.psycopg2 p.requests])"`).
- Check it's telemetry-on: `journalctl --user -u initiatives-sync.service | grep -i telemetry`
  → expect "reader creds provisioned — telemetry-on" and "… telemetry on" (NOT OFF).
- Read-back: reuse `mail-actions/_db.py` and query `initiatives.current` / `initiatives.snapshots`.

## Open investigations

### `current` view accumulates aged-out ("ghost") initiatives
`current` is newest-per-`(repo,slug)` across *all* snapshots, so an initiative that drops out
of the scan's N-day window still appears with stale last-seen state until the 90-day retention
prunes it (live: `current`=24 while the latest snapshot had 22). Fine for the router (matching
against recently-dormant initiatives is desirable); wrong for a live viewer (shows ghosts).

## Next steps (the platform is built; these are remaining reach)
1. **Dual-host merge (deferred).** Phase 1 is workbench-only. Do NOT naively sum a laptop push:
   the rollup already embeds central-ClickHouse telemetry, so summing double-counts `ev`. Merge
   by `max(last_touch)` per `(repo,slug)`, telemetry from one host only.
2. **Viewer hardening (optional).** Bind is a hardcoded LAN IP (`192.168.50.250`); if eth1's IP
   ever changes, `Restart=on-failure` crash-loops every 10s. Consider binding `0.0.0.0` (LAN+nebula,
   no public iface on this host) or a fail-soft fallback to `127.0.0.1`. Also: public exposure via
   the homelab gateway is a deliberate un-taken choice (internal work data).
3. **Matcher limitation.** Word-equality, no stemming (`polish`≠`polishing`) — inherited from the
   scan. Both wirings feed it prose (proposal text / mail subject) so expect silent misses on
   morphological variants. If misses matter, add light stemming to `text_tokens` (touches the scan
   + its tests — keep behavior green).

## Notes / gotchas
- The transform is a pure function (`report_to_rows`) separate from I/O — unit-tested with
  fixtures (`scripts/initiatives/tests/test_sync.py`), no live infra. Registered in
  `scripts/run-tests.sh`.
- `_db.py` is loaded by **explicit importlib path** (NOT via `sys.path`) — its sibling `llm.py`
  shadows repo-cos's; do not add `mail-actions/` to `sys.path`. If Phase 2 grows more shared DB
  helpers, promote the port-forward/DSN plumbing into a proper shared module.
- CH reader endpoint `http://192.168.50.94:30123` is workbench-LAN-only → a laptop copy of the
  unit degrades to telemetry-off (intended).
