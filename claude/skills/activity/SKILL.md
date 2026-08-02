---
name: activity
description: Operate the personal activity-telemetry pipeline — 6 sources (zsh, tmux, X11 keylogger, browser, Claude Code sessions, i3 window/workspace focus) → per-host collector → homelab ClickHouse (activity.events) → Grafana "Activity & Productivity" dashboard + validation harness. Status, query the data, troubleshoot a stalled source, deploy a change, run validation. Use when the user mentions activity tracking, the keylogger, "where my time goes", the activity dashboard, activity.events, the collector, or productivity mining of their own behaviour.
---

# activity-telemetry operations

Six sources emit through one per-host collector daemon into a dedicated, authenticated
ClickHouse on the homelab cluster; a Grafana dashboard surfaces time/attention +
focus/context-switching, and a validation harness proves capture/query correctness.
Point-in-time state: memory `activity-telemetry-pipeline` (read it first) + the latest
`devrc/claudedocs/handoff-activity-*.md`.

**Data flow:** source → `emit` (or `spool_emit`) appends a v1 line to
`~/.local/state/activity/spool/` → `collector.py` (systemd user daemon) batches → POST
`JSONEachRow` to ClickHouse `activity.events` (offline-buffered, retried). The collector
stamps `host` from `ACTIVITY_HOST`.

**Reference files** (repo-absolute; read on demand):
- `~/workspace/devrc/claude/skills/activity/reference/queries.md` — the non-obvious SQL
  (i3 dwell, reading depth, i3-derived browser attention) + the column/JSON gotchas that
  make it non-obvious. Read before writing a NEW query or touching the dashboard panels.
- `~/workspace/devrc/claude/skills/activity/reference/session-insights.md` — the Layer B
  anti-confabulation extraction contract + backlog-run lessons. Read before an extraction.

## Key facts (verify against live state before asserting)

| Thing | Value |
|---|---|
| Code | `~/workspace/devrc/scripts/collector/` (`emit`, `collector.py`, `keylog/`, `browser-ext/`, `claude/`) + `scripts/validation/` |
| ClickHouse | dedicated, **authed**, homelab ns `activity` — NOT the shared clickstack one. Table `activity.events`, 180d TTL, monthly partitions |
| Endpoint — workbench | `http://192.168.50.94:30123` (same LAN) |
| Endpoint — laptop | `http://10.42.0.10:30123` (**nebula** — the laptop is nebula-only; it CANNOT reach the 192.168.50.x LAN IP) |
| Endpoint — in-cluster | `clickhouse.activity.svc.cluster.local:8123` (NodePort `30123`) |
| CH users | `default`=admin (`admin-password`), `activity_writer`=INSERT+SELECT (`writer-password`, collector uses this), `activity_reader`=SELECT (`reader-password`, dashboard + harness) |
| CH creds | SOPS secret `homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml`. Decrypt: `SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' <file>` (on a `<(git show …)` process substitution, add `--input-type yaml`) |
| Collector config | `~/.config/activity-collector/env` per host (chmod 600, NOT in git/nix store): `CLICKHOUSE_URL/USER/PASSWORD`, `ACTIVITY_HOST` (=`workbench`/`laptop`), batch/flush/buffer caps |
| Services (home-manager systemd **user**) | `activity-collector` (always), `keylog` (graphical-session.target — laptop only), `browser-activity-receiver` (:8787 loopback), `claude-activity-source` (oneshot + 5-min timer), `i3-source` (i3ipc focus daemon, graphical-session.target — laptop only) |
| Dashboard | Grafana "Activity & Productivity" (uid `activity-productivity`), datasource `activity-clickhouse` → `https://grafana.homelab.lan` |
| Cluster access | `KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig` (context `admin@zach-homelab`); manifests in `clusters/homelab/apps/activity/` + dashboard in `clusters/homelab/flux-system/charts/prom-stack/`. **From the laptop** (nebula-only, can't reach the LAN API `192.168.50.94:6443`): `KUBECONFIG=~/.kube/homelab-nebula.yaml` — routes through the `homelab-kube-tunnel` systemd user service (ssh -D SOCKS via the workbench `10.42.0.30`) |
| Schema columns | `ts DateTime64(3) (UTC), host, source, kind, project, cwd, session, app, text, duration_ms, exit_code, payload(JSON), ingested_at` |

### The six sources
| source | what |
|---|---|
| `zsh` | preexec/precmd, interactive-only → excludes Claude's Bash tool |
| `tmux` | focus hooks |
| `keys` | X11 XRecord keylogger, **full content**, GUI-only. Carries `app`=WM_CLASS + `payload.workspace` |
| `browser` | Brave MV3 ext → loopback receiver :8787. **nav events only**: `text`=URL, `title`, `scroll_pct` (max reading depth), `scroll_ms` (active-scroll time) per page view. Receiver labels `app` from `BROWSER_APP` env. `active_ms` + focus/idle events were **RETIRED** (PR #27) — structurally wrong on i3 (`chrome.idle` is system-wide, blur unreliable → counted *other-app* time as browser-active). Browser attention is now derived downstream (i3 ∩ domain — see `reference/queries.md`) |
| `claude` | tails `~/.claude/projects/**/*.jsonl`, 5-min timer. Three kinds: `prompt`/`command` (message stream), `session-summary` (Layer A rollups), `session-insight` (Layer B facets) |
| `i3` | i3ipc `window::focus` + `workspace::focus` → `i3-source` daemon, GUI-only/laptop; captures attention even when NOT typing. Carries `app`=WM_CLASS + `payload.workspace` |

**Browser extension is NOT fully nix-managed.** Hand-loaded unpacked in Brave (the laptop's
daily browser) from `~/.local/share/activity-browser-ext/` — a real-file copy, since
Chromium dislikes loading a nix-store symlink dir. It persists across restarts, but a
`service_worker.js`/content-script change needs: ship →
`cp -fL ~/.config/activity-collector/browser-ext/*.js ~/.local/share/activity-browser-ext/`
→ **reload the extension in `brave://extensions`** (+ reload the page — content scripts only
inject post-reload). Manifest **v1.4.0**. When a file is DELETED upstream (e.g. the old
`active_time.js`), `rm` it from `~/.local/share/…` by hand — `cp` won't remove it.

## ⚠ Gotchas (each cost real time)
- **Timezone:** `ts` is the **UTC instant** (tz-less DateTime64). Dashboard buckets
  hour-of-day / per-day with explicit `'America/Winnipeg'` (`toHour`/`toDate`); time-series
  stay UTC. Add the tz arg when grouping by local hour/day — but NEVER tz-shift
  `$__timeFilter` / range comparisons (they're UTC, aligned with `now()`).
- **Both hosts are hostname `nixos`** → without `ACTIVITY_HOST` in the env, every row
  collides on `host=nixos`. Set it per host.
- **keylog + browser + i3 are GUI-only** → laptop only (X11/i3). The workbench is headless
  (server-mode).
- **Full-content keylogging** → `activity.events` holds secrets. That is WHY the store is a
  dedicated authed ClickHouse, not the shared LAN-open clickstack. Treat reader/writer creds
  as sensitive.
- **`home-manager switch` RESTARTS these daemons on a script-only change** (devrc PR #16) —
  `X-Restart-Triggers` flips the unit definition when the code changes. No manual
  `systemctl --user restart` after a ship. (`claude-activity-source` is excluded — its 5-min
  timer oneshot re-runs fresh code anyway.)
- **`session-summary` / `session-insight` rows are APPEND-ONLY** — dedupe on read with
  `argMax(<field>, ingested_at)` grouped by `session`.

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
Current invariant set: `active_ms_capped` + `per_host_hour_active_cap` are **retired**
(extension `active_ms` no longer emitted), replaced by **`derived_attention_consistent`**
(per-domain i3-derived attention ≤ total Brave i3 dwell, trailing 48h). The `duration_ms`
garbage bound is **7d**, not 24h — a multi-day interactive `claude --resume` is real.
`session_summary_rows_bounded` flags >24 rows/session ingested in 24h.

## troubleshoot a stalled source
1. `journalctl --user -u <service> -n 30` — `urlopen timed out` = can't reach CH (check the
   endpoint matches the host: laptop must use the nebula IP).
2. spool backlog growing = collector can't ship; check `CLICKHOUSE_URL`/creds in
   `~/.config/activity-collector/env`, then `systemctl --user restart activity-collector`.
3. browser receiver crash (`ModuleNotFoundError: spool_emit`) = a symlink-resolution
   regression — the receiver must NOT `.resolve()` `__file__`.
4. events landing as `host=nixos` = `ACTIVITY_HOST` missing/old code; set env + restart.

## deploy a change
```bash
# 1. land the code (devrc PR → merge to main)
# 2. converge both hosts (home-manager) — this also restarts the daemons (see gotchas)
~/workspace/devrc/scripts/ship.sh
# Dashboard / CH-manifest changes live in homelab-talos (Flux: commit=deploy) — merge to
# trunk, then `flux reconcile kustomization charts-prom-stack` (dashboard) or `... apps` (CH).
```

## analysis / mining tooling (deterministic, on-demand)
All three read `CLICKHOUSE_URL/USER/PASSWORD` from env (via `validation/chquery.py`).

- `~/workspace/devrc/scripts/session-analysis/activity-scan.py [--days N] [--json]` — "where
  workflow time goes + what to automate": automation candidates (top repeated zsh commands +
  binaries), bottlenecks (binaries by total wait time), signal-vs-noise (i3 switch rate,
  deep-work blocks, attention-by-app, browser-by-domain). Caveat: "signal vs noise" =
  switch-rate / attention-split only; value judgment needs a human/LLM layer.
- `~/workspace/devrc/scripts/session-analysis/initiative-scan.py [--days N] [--json] [--repo PATH]`
  — cross-repo initiative + progress ledger (handoff docs + git + telemetry recency by
  `gitBranch` → momentum `active`/`slowing`/`stalled`, last-touched, next-step).
  **Degrades to handoff+git** when telemetry is off/unreachable.
  Surfaced via `/initiatives` and `/standup`. Caveats: momentum = recency of touch, NOT %
  done; initiative↔commit linking is heuristic slug-matching; git worktrees collapse to
  their canonical repo. Momentum times from the last genuine USER-turn timestamp, **not the
  transcript file mtime** (Claude Code rewrites `.jsonl` in place → mtime falsely reads as
  "active"). ➜ The durable subsystem built on this scan (store → 15-min sync → viewer at
  `192.168.50.250:8899` → recaps → router + assistant) is the **`initiatives` skill** — don't
  duplicate it here.
- `~/workspace/devrc/scripts/session-analysis/insights.py [--days 14] [--insight-days 30] [--json] [--host H] [--html PATH]`
  — the report over Layer A + the message stream + Layer B. Telemetry-native successor to
  the built-in `/insights`; the built-in's numbers are NOT trusted (it confabulated).
- `~/workspace/devrc/scripts/dogfood-cycle` — collapses the manual civitai dogfood loop
  (create→install→token→run→teardown→upgrade) into one command. `--dry-run` + hard `rm` guards.

## the human validation only the operator can do
The harness proves the machinery is correct. Whether it's *true* — pick an hour you remember
and confirm the dashboard matches what you actually did — is the operator's spot-check.

## session insights (Layer B — LLM qualitative facets)

Turns settled Claude sessions into `source=claude, kind=session-insight` rows. Deterministic
Python does the plumbing; **THIS live session does the extraction** (no `claude -p`, no
external API). Manual/on-demand only, no timer. Prereqs: `CLICKHOUSE_URL/USER/PASSWORD` in
env (reader creds via SOPS — see Key facts).

```bash
CLI=~/workspace/devrc/scripts/session-analysis/session_insight/cli.py
python3 $CLI status --json                          # 1. what's pending (no writes)
python3 $CLI prepare --days 14 --limit 6 --json     # 2. select settled+un-extracted, scrub,
                                                    #    attach Layer A ground truth
                                                    #    → prints run_id, staging dir, input.json paths
# 3. EXTRACT — read each input.json, write results/<run-id>/<session>.result.json per the
#    schema in the input. 🔴 FIRST read reference/session-insights.md (the anti-confabulation
#    contract + how to batch/fan out) — do not extract from memory of these rules.
python3 $CLI write --run-id <id> --json             # 4. validate + emit to ClickHouse
python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 30   # 5. read the report
```
