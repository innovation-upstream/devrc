---
name: activity
description: Operate the personal activity-telemetry pipeline — 9 sources (zsh, tmux, X11 keylogger, browser, Claude Code sessions, i3 window/workspace focus, opencode, browser-bridge, tool invocations) → per-host collector → homelab ClickHouse (activity.events) → Grafana "Activity & Productivity" dashboard + validation harness + a per-host/per-source deadman check. Status, query the data, troubleshoot a stalled source, find out whether a source has silently DIED, deploy a change, run validation. Use when the user mentions activity tracking, the keylogger, "where my time goes", the activity dashboard, activity.events, the collector, a dead/stale telemetry source, the `tlm` bar pill, or productivity mining of their own behaviour.
---

# activity-telemetry operations

Nine sources emit through one per-host collector daemon into a dedicated, authenticated
ClickHouse on the homelab cluster; a Grafana dashboard surfaces time/attention +
focus/context-switching, a validation harness proves capture/query correctness, and a
**deadman check** (`scripts/collector/deadman.py`, surfaced as the `tlm` bar pill) catches a
source that has silently STOPPED emitting.
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
| Services (home-manager systemd **user**) | `activity-collector` (always), `keylog` + `i3-source` (graphical-session.target — **BOTH hosts run these; the workbench has a real X/i3 session**), `browser-activity-receiver` (:8787 loopback), `claude-activity-source` (oneshot + 5-min timer) |
| Dashboard | Grafana "Activity & Productivity" (uid `activity-productivity`), datasource `activity-clickhouse` → `https://grafana.homelab.lan` |
| Cluster access | `KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig` (context `admin@zach-homelab`); manifests in `clusters/homelab/apps/activity/` + dashboard in `clusters/homelab/flux-system/charts/prom-stack/`. **From the laptop** (nebula-only, can't reach the LAN API `192.168.50.94:6443`): `KUBECONFIG=~/.kube/homelab-nebula.yaml` — routes through the `homelab-kube-tunnel` systemd user service (ssh -D SOCKS via the workbench `10.42.0.30`) |
| Schema columns | `ts DateTime64(3) (UTC), host, source, kind, project, cwd, session, app, text, duration_ms, exit_code, payload(JSON), ingested_at` |

### The sources — MEASURED, not declared
🔴 This table said "six sources" and got two things wrong; both were measured on
2026-08-03 against `activity.events` and corrected. **Do not re-derive the
expected-present set from prose — run `python3 ~/workspace/devrc/scripts/collector/deadman.py`,
which derives it from the table.** Live pairs: **9 sources on the laptop, 8 on the
workbench, 17 pairs total.**

| source | what | laptop | workbench |
|---|---|---|---|
| `zsh` | preexec/precmd, interactive-only → excludes Claude's Bash tool | ✅ | ✅ |
| `tmux` | focus hooks | ✅ | ✅ |
| `keys` | X11 XRecord keylogger, **full content**. Carries `app`=WM_CLASS + `payload.workspace` | ✅ | ✅ **(the doc said laptop-only — wrong)** |
| `browser` | Brave MV3 ext → loopback receiver :8787 | ✅ | ❌ **0 rows, correctly — the ext is laptop-only. This is the ONLY genuinely laptop-only source.** |
| `claude` | Claude Code transcript tailer, 5-min timer | ✅ | ✅ |
| `i3` | i3ipc focus daemon | ✅ | ✅ **(the doc said laptop-only — wrong)** |
| `opencode` | opencode plugin + tailers (`prompt`/`assistant-turn`/`tool-call`/`session-summary`) | ✅ | ✅ |
| `browser-bridge` | metadata-only telemetry from the agent browser-bridge | ✅ | ✅ |
| `tool` | on-demand tool-invocation events (`scripts/collector/invocation.py`) | ✅ | ✅ |

Per-source detail that matters when querying:
- `browser` — **nav events only**: `text`=URL, `title`, `scroll_pct` (max reading depth),
  `scroll_ms` (active-scroll time) per page view. Receiver labels `app` from `BROWSER_APP`.
  `active_ms` + focus/idle were **RETIRED** (PR #27) — structurally wrong on i3
  (`chrome.idle` is system-wide, blur unreliable → counted *other-app* time as
  browser-active). Browser attention is derived downstream (i3 ∩ domain — see
  `reference/queries.md`).
- `claude` — tails `~/.claude/projects/**/*.jsonl`. Kinds: `prompt`/`command` (message
  stream), `session-summary` (Layer A rollups), `session-insight` (Layer B facets).
- `i3` — `window::focus` + `workspace::focus` → `i3-source`; captures attention even when
  NOT typing. Carries `app`=WM_CLASS + `payload.workspace`.

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
- 🔴 **CORRECTED 2026-08-03 — "keylog + browser + i3 are GUI-only → laptop only; the
  workbench is headless" was FALSE and had been for a long time.** Measured against
  `activity.events`: the workbench emits **i3 (41,001 rows) and keys (37,376 rows)**, both
  fresh. The workbench runs a real X/i3 session; only the **Brave activity extension** is
  laptop-only, so `browser` is the single source with 0 workbench rows. Anything that
  decides "is this source expected here?" must read the TABLE, not this file — which is why
  `deadman.py` derives the expected set from measured baselines instead of a hand-kept list.
- **Full-content keylogging** → `activity.events` holds secrets. That is WHY the store is a
  dedicated authed ClickHouse, not the shared LAN-open clickstack. Treat reader/writer creds
  as sensitive.
- **`home-manager switch` RESTARTS these daemons on a script-only change** (devrc PR #16) —
  `X-Restart-Triggers` flips the unit definition when the code changes. No manual
  `systemctl --user restart` after a ship. (`claude-activity-source` is excluded — its 5-min
  timer oneshot re-runs fresh code anyway.)
- 🔴 **opencode plugin: only `tool.execute.before` / `tool.execute.after` are real plugin
  HOOK names. `session.created`, `message.updated` and `session.idle` are BUS EVENT TYPES,
  not hooks — `activity-plugin.js` registers all three and none has ever fired.** MEASURED
  2026-08-03 on opencode 1.18.4 with a probe plugin registering all 14 candidate names
  against a throwaway `OPENCODE_CONFIG_DIR` + `opencode serve` + `POST /session`: the session
  was created, the named `session.created` hook fired **0 times**, and the generic `event`
  hook fired **once** with `event.type == "session.created"`. Corroborated in the data:
  `kind=session-create` and `kind=session-idle` have **0 rows, ever**; every `prompt`/
  `assistant-turn` row comes from `tailer.py`, not the plugin. Downstream consequence —
  `currentSession` is only ever set by the dead `session.created` handler, so **2,736 of
  2,799 `kind=tool-call` rows carry `session=''`** and cannot be attributed to a session.
  The fix is to route these through a single `event` hook that switches on `event.type`;
  🔴 do it in a dedicated PR with a live post-deploy check — this is the exact file whose
  last edit (#298) killed ALL opencode telemetry on both hosts for ~11 hours.
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

## deadman — "has a source silently DIED?"
`scripts/collector/deadman.py`. **Read its module docstring before changing any tunable** —
every default in it is derived from a measurement that is named inline.

```bash
python3 ~/workspace/devrc/scripts/collector/deadman.py           # the per-pair table
python3 ~/workspace/devrc/scripts/collector/deadman.py --json    # machine-readable
# exit 0 = measured & clean · 1 = something is DEAD · 2 = CANNOT TELL (never "healthy")
```
Creds come from `~/.config/activity-collector/env` (the collector's own file) unless
`CLICKHOUSE_URL/USER/PASSWORD` are already in the environment.

- **Silence is counted in ACTIVE time, not wall time.** A host's *active* 5-min buckets are
  the ones where any of its sources emitted; overnight/away time is simply not in the set.
  This is what makes a per-source budget work for `keys` (continuous) and `tool` (on-demand)
  at the same time.
- **The budget is MEASURED per (host, source)**: `clamp(2 × p99 active-gap, 2h, 48h)`.
  Measured 2026-08-03 — `keys`/`i3`/`tmux` land on the 2h floor; `workbench/opencode` 11.5h;
  `workbench/tool` 31.1h. Nothing is hand-tuned and nothing is hand-listed.
- **Expected-present is measured too**: a pair is judged only if it cleared a baseline in the
  14-day window, so `workbench/browser` (0 rows, correctly absent) can never alarm, while a
  source that *was* emitting and stopped keeps its baseline and does.
- 🔴 **"Cannot tell" ≠ "healthy".** `not-configured` / `unreachable` / `query-failed` /
  `no-data` are each their own state; `ok` is unreachable unless rows came back AND at least
  one pair was actually measured (`evaluated > 0` is the verdict's own positive control).
- 🔴 **Blind spot, by design:** this is a RELATIVE check. A simultaneous outage of every
  source on every host produces zero active buckets and reads as "0 dead" — indistinguishable
  from the operator being away. `newest_event_age_minutes` is reported for the human;
  it is deliberately not an alarm.
- **Surface:** the workbench `bar-status-poll` (~45s) runs it as its `telemetry` source →
  `~/.cache/bar-status/telemetry.json` → the `tlm` pill (signal 17) + a rising-edge dunst
  toast. One workbench runner covers BOTH hosts because it reads the shared table. See the
  `bar` skill.

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
