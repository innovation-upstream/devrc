# Handoff: activity-telemetry pipeline — 2026-06-24

## Goal
A personal activity dataset for productivity mining: capture what Zach actually does across hosts (shell, terminal, keystrokes, browser, Claude sessions) into a queryable store, and surface time/attention + focus/context-switching. Built this session from the "mine Claude Code sessions" thread, extended to full system activity.

**Operate it via the `activity` skill** (authoritative ops doc). Cross-session state: memory `activity-telemetry-pipeline`.

## State now — LIVE + verified end-to-end
- **devrc** `main` @ `5bb6a3d`; **all PRs merged** (devrc #7,#9,#10,#11,#12,#13,#14; homelab #67,#68,#69). Open homelab PRs are unrelated renovate bots.
- **5 sources flowing**, per-host attributed, UTC-stamped: `zsh`, `tmux`, `keys` (X11 keylogger, full content, laptop only), `browser` (Chrome MV3 + receiver :8787), `claude` (transcript tailer, timer 5min).
- **Sink:** dedicated authed ClickHouse, homelab ns `activity`, table `activity.events` (180d TTL). NOT the shared clickstack. Endpoints: workbench `http://192.168.50.94:30123`, laptop `http://10.42.0.10:30123` (nebula).
- **Dashboard:** Grafana "Activity & Productivity" (uid `activity-productivity`), datasource `activity-clickhouse` → `https://grafana.homelab.lan`. Deployed + datasource health OK.
- **Validation harness** (`devrc/scripts/collector`… no — `devrc/scripts/validation/`): **8/8 invariants PASS**, controlled-replay assertions pass.
- **Both hosts shipped + services restarted** on the latest code. Claude backfill was in progress (~7159 msgs/host; trickles via the 5-min timer thereafter).
- **TZ FIX verified:** `ts↔now()` lag = **3s** (was the ~5–6h local/UTC offset). Table was truncated for a clean UTC start (data prior to 2026-06-24 ~13:30 UTC is gone — it was <1 day of thin local-ts data).

### What's IN FLIGHT / not yet done
- **Panel CONTENT unverified** — dashboard panels are structurally correct + query-valid, but won't show meaningful patterns until a day+ of data accrues. The human spot-check (below) is the remaining validation.
- **Keystroke-replay assertion** in the harness only runs on the **laptop** (needs X); skipped on the headless workbench.

## Next steps (ranked)
1. **Let data accrue ~a day, then do the human ground-truth check** (the one validation no harness can do): open the dashboard, pick an hour you remember, confirm it matches reality. This is the real "is it true" test.
2. **Ops fix — `X-Restart-Triggers`** on the collector/keylog/receiver systemd user services so `home-manager switch` restarts them on a script-only change. This bit us twice this session (switch leaves stale code running; I worked around it with manual `systemctl --user restart`). Small devrc PR.
3. **Run the keystroke-replay assertion on the laptop** to close the one validation gap that the headless workbench couldn't exercise: `ssh zach@10.42.0.100`, then `... validate.py --replay` with reader creds (DISPLAY=:0 present).
4. **Optional next layer:** an LLM-summarized daily digest (task-drafter pattern) on top of the deterministic dashboard. CAVEAT (carried from the session): this is a measurement layer — mining describes the present, it doesn't pick the next altitude. Don't let it become a well-instrumented saw-sharpen.
5. **Housekeeping:** workbench is intentionally NOT on a clean branch (it's on `feat/activity-collector-slice` fast-forwarded to main, with unrelated WIP) — fine, collector works; converge later if desired. The homelab-talos local `.sops.yaml` has pre-existing drift — resolve before committing there.

## Gotchas / decisions / dead-ends
- **`ts` is the UTC instant** (tz-less DateTime64). Dashboard buckets hour-of-day/per-day with explicit `'America/Winnipeg'` (`toHour`/`toDate`); time-series stay UTC. NEVER tz-shift `$__timeFilter`/`now()` comparisons.
- **`home-manager switch` does NOT restart these services** on a script-only change → manual `systemctl --user restart activity-collector keylog browser-activity-receiver` per host after a code ship. (Next-steps #2 fixes this.)
- **Laptop is nebula-only** — it cannot reach the `192.168.50.x` LAN IP; its collector env uses `http://10.42.0.10:30123`. Workbench uses the LAN `.94`.
- **Both hosts are hostname `nixos`** → `ACTIVITY_HOST` (=`workbench`/`laptop`) in each `~/.config/activity-collector/env` disambiguates; without it everything collides on `host=nixos` (fixed PR #11).
- **keylog + browser are GUI-only** → laptop only (workbench is headless/server-mode).
- **Full-content keystrokes land in CH** → that's WHY it's a dedicated authed store (default user password-protected; reader/writer creds in SOPS). Treat as sensitive.
- **SOPS decrypt:** write the encrypted file to a temp path first — `sops -d` on a `<(...)` process-sub `/dev/fd` path fails to sniff the format (cost a failed snapshot this session). Pattern: `git show origin/trunk:…secrets.enc.yaml > /tmp/s.yaml; sops -d --extract '["stringData"]["reader-password"]' /tmp/s.yaml; rm /tmp/s.yaml`.
- **Bug history (all fixed under verification this session):** receiver `ModuleNotFoundError: spool_emit` (don't `.resolve()` `__file__` — home-manager flattens the symlink); laptop LAN-vs-nebula endpoint; `host=nixos` collision; stale-code-after-switch; the auth/exposure redesign onto a dedicated authed store; the UTC tz bug.
- **Decision: deterministic dashboard over LLM digest** as the first insight layer (no per-run cost, directly serves the lead insights). Full content + dedicated authed ClickHouse were Zach's explicit choices.

## How to verify
```bash
# creds (write to temp file — NOT process-sub)
git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/s.yaml
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' /tmp/s.yaml); rm -f /tmp/s.yaml
CH=http://192.168.50.94:30123   # on the laptop: http://10.42.0.10:30123

# data flowing, per-host, all sources:
curl -s --user "activity_reader:$RPW" --data-binary "SELECT host, source, count(), max(ts) FROM activity.events GROUP BY host, source ORDER BY host, source FORMAT TSV" "$CH/"
# UTC sanity — lag should be SECONDS, not hours:
curl -s --user "activity_reader:$RPW" --data-binary "SELECT dateDiff('second', max(ts), now()) FROM activity.events WHERE source IN ('zsh','keys','browser') FORMAT TSV" "$CH/"
# full harness (invariants + reconcile):
CLICKHOUSE_URL=$CH CLICKHOUSE_USER=activity_reader CLICKHOUSE_PASSWORD=$RPW python3 ~/workspace/devrc/scripts/validation/validate.py
# dashboard: https://grafana.homelab.lan → "Activity & Productivity"
# cluster: KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig kubectl -n activity get pods,svc
```
