---
name: activity
description: Operate the personal activity-telemetry pipeline — 6 sources (zsh, tmux, X11 keylogger, browser, Claude Code sessions, i3 window/workspace focus) → per-host collector → dedicated authed homelab ClickHouse (activity.events) → Grafana "Activity & Productivity" dashboard, plus a deterministic validation harness. Status, query the data, troubleshoot a stalled source, deploy a change, run validation. Use when the user mentions activity tracking, the keylogger, "where my time goes", the activity dashboard, activity.events, the collector, or productivity mining of their own behaviour.
---

# activity-telemetry operations

A personal activity dataset for productivity mining. Six sources emit events through one per-host collector daemon into a dedicated, authenticated ClickHouse on the homelab cluster; a Grafana dashboard surfaces time/attention + focus/context-switching, and a validation harness proves capture/query correctness. Full point-in-time state lives in memory `activity-telemetry-pipeline` (read it first) + the latest `devrc/claudedocs/handoff-activity-*.md`.

**Data flow:** source → `emit` (or `spool_emit`) appends a v1 line to `~/.local/state/activity/spool/` → `collector.py` (systemd user daemon) batches → POST `JSONEachRow` to ClickHouse `activity.events` (offline-buffered, retried). The collector stamps `host` from `ACTIVITY_HOST`.

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Code | `~/workspace/devrc/scripts/collector/` (`emit`, `collector.py`, `keylog/`, `browser-ext/`, `claude/`) + `scripts/validation/` |
| Sources (6) | `zsh` (preexec/precmd, interactive-only → excludes Claude's Bash tool), `tmux` (focus hooks), `keys` (X11 XRecord keylogger, **full content**, GUI-only), `browser` (Brave MV3 ext → loopback receiver :8787; emits **nav events only** now — `text`=URL, `title`, **`scroll_pct`** (max reading depth) + **`scroll_ms`** (active-scroll time) per page view via a capture-phase content script that catches SPA inner-container scroll too; receiver labels `app` from `BROWSER_APP` env. **`active_ms` + the focus/idle events were RETIRED (PR #27, 2026-06-29)** — they were structurally wrong on i3 (`chrome.idle` is system-wide + blur unreliable → counted *other-app* time as browser-active). Browser ATTENTION is now derived downstream by intersecting i3 "Brave-focused" intervals with the active-tab domain timeline (see query patterns). **NOTE:** the URL is the **`text`** column (NOT `payload.url`); a nav event's `text`/`title` are the DESTINATION tab but its `scroll_pct`/`scroll_ms` belong to the LEAVING tab. Extract scroll with `JSONExtractInt(toString(payload),'scroll_pct')` — `payload.scroll_pct` subcolumn access is NOT available), `claude` (tails `~/.claude/projects/**/*.jsonl`, timer every 5min), `i3` (i3ipc window::focus + workspace::focus → `i3-source` daemon, GUI-only/laptop; captures attention even when NOT typing). Both `keys` and `i3` carry `app`=WM_CLASS + `payload.workspace` |
| ClickHouse | dedicated, **authed**, homelab ns `activity` — NOT the shared clickstack one. Table `activity.events`, 180d TTL, monthly partitions |
| Endpoint — workbench | `http://192.168.50.94:30123` (same LAN) |
| Endpoint — laptop | `http://10.42.0.10:30123` (**nebula** — the laptop is nebula-only; it CANNOT reach the 192.168.50.x LAN IP) |
| Endpoint — in-cluster | `clickhouse.activity.svc.cluster.local:8123` (NodePort `30123`) |
| CH users | `default`=admin (`admin-password`), `activity_writer`=INSERT+SELECT (`writer-password`, collector uses this), `activity_reader`=SELECT (`reader-password`, dashboard + harness) |
| CH creds | SOPS secret `homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml`. Decrypt: `SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' <file>` |
| Collector config | `~/.config/activity-collector/env` per host (chmod 600, NOT in git/nix store): `CLICKHOUSE_URL/USER/PASSWORD`, `ACTIVITY_HOST` (=`workbench`/`laptop`), batch/flush/buffer caps |
| Services (home-manager systemd **user**) | `activity-collector` (always), `keylog` (graphical-session.target — laptop only), `browser-activity-receiver` (:8787 loopback), `claude-activity-source` (oneshot + timer), `i3-source` (i3ipc focus daemon, graphical-session.target — laptop only) |
| Browser extension (NOT fully nix-managed) | The MV3 ext (`scripts/collector/browser-ext/`) is **hand-loaded** in Brave (the laptop's daily browser) as an unpacked extension from `~/.local/share/activity-browser-ext/` (a real-file copy, since Chromium dislikes loading the nix-store symlink dir). It persists across restarts, but a `service_worker.js`/content-script change needs: ship → `cp -fL ~/.config/activity-collector/browser-ext/*.js ~/.local/share/activity-browser-ext/` → **reload the extension in `brave://extensions`** (+ reload the page, content scripts only inject post-reload). Manifest **v1.4.0** — the ext is now **nav + scroll ONLY** (the `active_ms` accumulator + focus/idle handlers + the `idle` permission were removed in PR #27; when stripping a deleted file like the old `active_time.js`, `rm` it from `~/.local/share/...` — `cp` won't remove it). |
| Dashboard | Grafana "Activity & Productivity" (uid `activity-productivity`), datasource `activity-clickhouse` → `https://grafana.homelab.lan` |
| Cluster access | `KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig` (context `admin@zach-homelab`); manifests in `clusters/homelab/apps/activity/` + dashboard in `clusters/homelab/flux-system/charts/prom-stack/`. **From the laptop** (nebula-only, can't reach the LAN API `192.168.50.94:6443`): `KUBECONFIG=~/.kube/homelab-nebula.yaml` — routes through the `homelab-kube-tunnel` systemd user service (ssh -D SOCKS via the workbench `10.42.0.30`) |
| Schema columns | `ts DateTime64(3) (UTC), host, source, kind, project, cwd, session, app, text, duration_ms, exit_code, payload(JSON), ingested_at` |

## ⚠ Gotchas (each cost real time this build)
- **Timezone:** `ts` is stored as the **UTC instant** (tz-less DateTime64). Dashboard buckets hour-of-day / per-day with explicit `'America/Winnipeg'` (`toHour`/`toDate`); time-series stay UTC. If you add a query that groups by local hour/day, add the tz arg — but NEVER tz-shift `$__timeFilter`/range comparisons (they're UTC, aligned with `now()`).
- **`home-manager switch` now RESTARTS these services on a script-only change** (FIXED — devrc PR #16, merged + shipped to both hosts 2026-06-24). `X-Restart-Triggers` (the script store path) on all 3 long-running daemons flips the unit definition whenever the code changes, so sd-switch restarts them; verified on both hosts (collector/keylog/receiver got new MainPIDs on the activating switch). `claude-activity-source` is excluded (5-min timer oneshot re-runs fresh code anyway). So a manual `systemctl --user restart` after a code ship is **no longer needed** — before #16, switch left STALE code running and you had to restart by hand.
- **Both hosts are hostname `nixos`** → without `ACTIVITY_HOST` in the env, every row collides on `host=nixos`. Set it per host.
- **keylog + browser are GUI-only** → they only run on the laptop (X11/i3). The workbench is headless (server-mode) — no keylog there.
- **Full-content keylogging** → `activity.events` holds secrets. That is WHY the store is a dedicated authed ClickHouse, not the shared LAN-open clickstack. Treat reader/writer creds as sensitive.

## status
```bash
# services (run per host; laptop via ssh zach@10.42.0.100). i3-source+keylog laptop-only.
systemctl --user is-active activity-collector keylog browser-activity-receiver claude-activity-source i3-source
journalctl --user -u activity-collector -n 20 --no-pager        # ship failures / drops
ls -la ~/.local/state/activity/spool/                            # backlog = unsent segments (offline buffer)

# is data flowing? (reader creds via SOPS, endpoint per host)
CH=http://192.168.50.94:30123   # laptop: http://10.42.0.10:30123
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' <(git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml))
curl -s --user "activity_reader:$RPW" --data-binary "SELECT host, source, count(), max(ts) FROM activity.events GROUP BY host, source ORDER BY host, source FORMAT TSV" "$CH/"
# UTC sanity (lag should be SECONDS, not ~5h):
curl -s --user "activity_reader:$RPW" --data-binary "SELECT dateDiff('second', max(ts), now()) FROM activity.events WHERE source IN ('zsh','keys','browser') FORMAT TSV" "$CH/"
```

## validate
```bash
# invariants + cross-source reconciliation (always); --replay adds the controlled-replay assertions
CLICKHOUSE_URL=$CH CLICKHOUSE_USER=activity_reader CLICKHOUSE_PASSWORD=$RPW python3 ~/workspace/devrc/scripts/validation/validate.py
# keystroke-replay assertions REQUIRE the laptop's X session — run there, not on the headless workbench
```
Invariant changes (2026-06-29): `active_ms_capped` + `per_host_hour_active_cap` were **retired** (PR #26 — extension `active_ms` is no longer trusted/emitted) and replaced by **`derived_attention_consistent`** (per-domain i3-derived attention ≤ total Brave i3 dwell, trailing 48h); the `duration_ms` garbage bound was raised **24h → 7d** (PR #28 — multi-day interactive `claude --resume` is real, was a false positive).

## analysis / mining tooling (deterministic, on-demand)
- `~/workspace/devrc/scripts/session-analysis/activity-scan.py [--days N] [--json]` — weekly "where workflow time goes + what to automate" report over `activity.events`: automation candidates (top repeated zsh commands + binaries), bottlenecks (binaries by total wait time), signal-vs-noise (i3 switch rate, deep-work blocks, attention-by-app, browser-by-domain). Reads `CLICKHOUSE_URL/USER/PASSWORD` from env (reuses `validation/chquery.py`). Honest caveat baked in: "signal vs noise" = switch-rate/attention-split only; value judgment needs a human/LLM layer.
- `~/workspace/devrc/scripts/session-analysis/initiative-scan.py [--days N] [--json] [--repo PATH]` — cross-repo **initiative + progress ledger**: fuses handoff docs (the registry) + git (commits/PRs by slug↔branch) + `activity.events` telemetry (recency/momentum by `gitBranch`) into a ranked report — each initiative's momentum (active/slowing/stalled), last-touched, next-step. Reuses the SAME reader creds (`CLICKHOUSE_*` env, `validation/chquery.py`); **degrades to handoff+git** when telemetry is off/unreachable. Surfaced via the **`/initiatives`** command AND folded into `/standup` (the `initiatives` scope, telemetry-OFF). Honest caveat: momentum = recency of touch, NOT % done; initiative↔commit linking is heuristic slug-matching. Collapses git worktrees to their canonical repo. **Momentum now times from the last genuine USER-turn timestamp, not the transcript file mtime** (Claude Code rewrites session `.jsonl` files in place → mtime falsely read as "active"; PR #149). ➜ **This scan is now the engine of a full durable subsystem** — the `initiatives` skill (store in the homelab mailbox Postgres → 15-min sync → live viewer at `192.168.50.250:8899` → LLM recaps → router + read-only assistant). Operate that layer via the **`initiatives` skill**; don't duplicate it here.
- `~/workspace/devrc/scripts/dogfood-cycle` — collapses the manual civitai dogfood test loop (create→install→token→run→teardown→upgrade) into one command; surfaced as a top automation candidate by activity-scan. `--dry-run` + hard `rm` guards.

## troubleshoot a stalled source
1. `journalctl --user -u <service> -n 30` — `urlopen timed out` = can't reach CH (check the endpoint matches the host: laptop must use the nebula IP).
2. spool backlog growing = collector can't ship; check `CLICKHOUSE_URL`/creds in `~/.config/activity-collector/env`, then `systemctl --user restart activity-collector`.
3. browser receiver crash (`ModuleNotFoundError: spool_emit`) = a symlink-resolution regression — receiver must NOT `.resolve()` `__file__`.
4. events landing as `host=nixos` = `ACTIVITY_HOST` missing/old code; set env + restart.

## deploy a change
```bash
# 1. land the code (devrc PR → merge to main)
# 2. converge both hosts (home-manager)
~/workspace/devrc/scripts/ship.sh
# 3. (No manual restart needed since PR #16 — switch restarts them via
#    X-Restart-Triggers. Only restart by hand if you bypassed ship/switch.)
#    systemctl --user restart activity-collector keylog browser-activity-receiver
# Dashboard / CH-manifest changes live in homelab-talos (Flux: commit=deploy) — merge to trunk, then `flux reconcile kustomization charts-prom-stack` (dashboard) or `... apps` (CH).
```

## query patterns (the non-obvious ones)
The dashboard's row **"Attention (i3 focus) & Reading (scroll)"** (homelab PR #78) has: Attention-by-app, Attention-by-i3-workspace, Reading-depth-by-domain, i3-window-switches-over-time. The old "Browser active time by domain (s)" panel was **replaced** by **"Browser attention by domain (i3-derived, s)"** (homelab-infra PR #79) — since extension `active_ms` is retired, browser attention is now the i3∩domain intersection below. Key reusable SQL:
```sql
-- i3 "attention" / dwell: focus events are point-in-time (NO stored duration).
-- dwell = gap to the NEXT i3 focus event, CAPPED (30min) so an idle focus doesn't inflate.
SELECT app, round(sum(dwell_ms)/60000,1) AS dwell_min FROM (
  SELECT app, kind, least(
    leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts))
      OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
      - toUnixTimestamp64Milli(ts), 1800000) AS dwell_ms
  FROM activity.events WHERE source='i3' AND host IN ('laptop') AND ts > now()-interval 7 day)
WHERE kind='window-focus' AND app != '' GROUP BY app ORDER BY dwell_min DESC;
-- browser reading depth: scroll lives in payload; extract via toString (NO payload.scroll_pct subcol).
SELECT domain(text) d, avg(JSONExtractInt(toString(payload),'scroll_pct')) avg_depth,
       max(JSONExtractInt(toString(payload),'scroll_pct')) max_depth,
       sum(JSONExtractInt(toString(payload),'scroll_ms'))/1000 scroll_s
FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' GROUP BY d ORDER BY scroll_s DESC;
-- browser ATTENTION by domain (i3-DERIVED — replaces the retired active_ms): intersect i3
-- "Brave-focused" intervals with the active-tab domain timeline (URL = the `text` column).
WITH brave AS (SELECT bs, be FROM (SELECT toUnixTimestamp64Milli(ts) bs, app,
    least(leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)) OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING), toUnixTimestamp64Milli(ts)+1800000) be
  FROM activity.events WHERE source='i3' AND kind='window-focus' AND host='laptop' AND ts>now()-interval 7 day) WHERE app='Brave-browser'),
dom AS (SELECT d, ds, least(de, ds+1800000) de FROM (SELECT if(domain(text)!='',domain(text),netloc(text)) d, toUnixTimestamp64Milli(ts) ds,
    leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)+1800000) OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) de
  FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' AND host='laptop' AND ts>now()-interval 7 day))
SELECT d domain, round(sum(greatest(0, least(be,de)-greatest(bs,ds)))/60000,1) min FROM brave CROSS JOIN dom WHERE be>ds AND de>bs GROUP BY d HAVING min>0 ORDER BY min DESC;
```
Caveats: i3/scroll exist only for **host=laptop** (GUI). `scroll_pct/ms` are on a SUBSET of nav events (only scrolled pages; un-scrolled report 0) and belong to the LEAVING tab. `workspace` empty on a small share of window-focus events.

## the human validation only the operator can do
The harness proves the machinery is correct. Whether it's *true* — pick an hour you remember and confirm the dashboard matches what you actually did — is the operator's spot-check once a day+ of data has accrued.

## session insights (Layer B — LLM qualitative facets)

Turns settled Claude sessions into `source=claude, kind=session-insight` rows in
`activity.events`. Deterministic Python does the plumbing; THIS live session does the
extraction (no `claude -p`, no external API). Manual/on-demand only. (Layer A =
`kind=session-summary` deterministic rollups from `claude/session-tailer.py`, on the
5-min timer; the report `scripts/session-analysis/insights.py` fuses A + B + the
message stream and is the telemetry-native successor to the built-in `/insights`.)

**Layer A is EMIT-ON-SETTLE** (since 2026-07-30). It does NOT re-ship a live
session's rollup every tick any more — it emits once on first sight, at most once
per `CLAUDE_SUMMARY_INTERIM_HOURS` (default 4) while the session is still live,
and again whenever the transcript has been idle for
`CLAUDE_SUMMARY_SETTLE_MINUTES` (default 20) — so a resumed session still gets a
correct final rollup. State: `~/.local/state/activity/session-summary-state.json`
(v2: `{"sessions": {path: {sig, emitted_at}}}`; a corrupt/missing file degrades to
"never emitted", never crashes the timer). Both knobs are read from the
environment at run time; 0 disables that gate. **The `argMax(<field>,
ingested_at)` per-`session` read contract is UNCHANGED** — every emit re-reads the
whole transcript, so the newest row is still the most complete. Expect ~1–5 rows
per session; the `session_summary_rows_bounded` invariant flags >24 rows/session
ingested in 24h. Before the fix: 27,061 rows over 702 sessions (avg 38.5, worst
486, ~1,800/day), 97.4% immediately superseded. The historical duplicates are NOT
rewritten — they age out under the 180d TTL.

Prereqs: `CLICKHOUSE_URL/USER/PASSWORD` in env (reader creds via SOPS — see top of this skill;
`sops -d` on a `<(git show …)` process-substitution needs `--input-type yaml`).

**1. See what's pending (no writes):**
```bash
python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py status --json
```

**2. Prepare a batch (deterministic: select settled + un-extracted, scrub, attach ground truth):**
```bash
python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py \
    prepare --days 14 --limit 20 --json
# prints: run_id, staging dir, and the per-session input.json paths.
```

**3. Extract (THIS session does the work):** read each `input.json` and, per session,
write `results/<run-id>/<session>.result.json` conforming to the schema in the input.
- 1 session → do it inline.
- A backlog (>3) → fan out with the Agent tool (`general-purpose`), one disjoint slice of
  sessions per subagent, each writing its result.json. NO worktree isolation needed — the
  agents only READ staging inputs and WRITE result files under `~/.local/state/…`, never the repo.
- Per session it is MAP-REDUCE: note qualitative observations per `chunk`, then reduce to ONE
  result.json. Counts come from `ground_truth` — never recount.

**4. Write to ClickHouse (deterministic: validate + emit):**
```bash
python3 ~/workspace/devrc/scripts/session-analysis/session_insight/cli.py write --run-id <id> --json
```

**5. Read the report:**
```bash
CLICKHOUSE_URL=… CLICKHOUSE_USER=activity_reader CLICKHOUSE_PASSWORD=… \
    python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 30
```

### Extraction rules (the anti-confabulation contract — NON-NEGOTIABLE)
- The `ground_truth` block = DETERMINISTIC counts (tools, tokens, commits, files, lines, errors,
  interruptions, models, durations). They are FACTS. Do NOT contradict, restate-as-if-counted,
  or invent ANY count/limit/metric. There is **no "output-token maximum"** — that was a
  confabulation by the old built-in; do not reproduce it.
- Your job is ONLY the qualitative facets: underlying_goal, goal_categories, outcome,
  session_type, claude_helpfulness (1–5: 5=Claude materially drove the win … 1=mostly got in the way),
  friction_counts + friction_detail (INTERACTION friction — wrong approaches, repeated corrections
  — distinct from mechanical tool_errors), primary_success, brief_summary, and the
  automation_opportunity / recurring_toil / workflow_gap observations (these three are WHY this
  data exists — be concrete and evidence-backed).
- Use only the controlled enum values given in the input's `schema` block.
- If a (chunked) transcript is too degraded/truncated/ambiguous to judge honestly, set
  `unreadable=true` + a one-line `unreadable_reason` and leave qualitative fields empty.
  **Flag it — never fabricate.**
- `<REDACTED:…>` tokens are scrubbed secrets; treat as opaque, never guess the original.

### Operating the backlog (real-run lessons)
Lessons from an actual 8-session run:
- **Scale**: ~90+ sessions are typically pending (`status --json` → `candidates`). Extract in
  BOUNDED batches (`prepare --limit ~6`) — the real cost is THIS operating session's own tokens,
  so never fan the whole backlog at once.
- **Session sizes vary wildly** — some transcripts are 250–280 `chunks`. Give each such MONSTER
  session its OWN extraction subagent, and tell subagents to SAMPLE strategically: first ~3 chunks
  (goal), last ~3 (outcome), ~every 25th middle chunk (friction/automation/toil/gap signals).
  NEVER read all chunks — it blows context and the counts come from `ground_truth` anyway. Batch
  several SMALL sessions (<~40 chunks) per subagent.
- **`write --run-id <id>`** validates + emits + PURGES each session's staging/result on success
  (per-session); a session that's missing/conflict/rejected is RETAINED for re-run. Re-runs are
  append-only (argMax-latest wins); `--force` re-extracts.
- **What it produces** (so the value is legible): the first 8-session batch surfaced a dominant
  **config-as-code gap for the storage/CDN layer** (CORS / CF-rules / egress-IPs managed
  out-of-band, not in git — recurred ×5 across incidents) and **B2-incident tooling as repeated
  toil** (~18 throwaway probe scripts / hand-launched diagnostic Jobs across ≥3 sessions) — the
  leverage-ranked automation/toil/gap outputs.
