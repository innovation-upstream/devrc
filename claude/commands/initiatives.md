---
name: initiatives
description: "Show the cross-repo initiative + progress ledger (initiative-scan): every ongoing initiative, its momentum (●active / ◐slowing / ○stalled), last-touched, commits/PRs, and next-step — fused deterministically from handoff docs + git + activity telemetry. Use for 'what am I working on', 'what's in flight across my projects', 'what's stalled', 'where did I leave X'."
argument-hint: "[--days N] [--repo PATH] [--json] — optional; defaults to --days 14"
allowed-tools: Bash
---

# /initiatives — durable initiative + progress ledger

Runs the read-only `initiative-scan` report and presents it. This is the durable, cross-session counterpart to the live Alt+i tmux view (which only shows sessions open right now). Args: `$ARGUMENTS` (passed through to the script; default `--days 14`).

## Steps

1. **Load the ClickHouse read-only reader creds** (for the telemetry/momentum columns). The script **degrades gracefully** without them (handoff + git only), so if this fails, still proceed.
   ```bash
   git -C ~/workspace/homelab-talos fetch origin trunk -q 2>/dev/null
   git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/ch.yaml 2>/dev/null
   export CLICKHOUSE_URL=http://192.168.50.94:30123 CLICKHOUSE_USER=activity_reader
   export CLICKHOUSE_PASSWORD=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
       sops -d --extract '["stringData"]["reader-password"]' /tmp/ch.yaml 2>/dev/null); rm -f /tmp/ch.yaml
   ```
   - **Host note:** `192.168.50.94:30123` is the workbench LAN endpoint. On the **laptop** (no `~/.server-mode` marker, nebula-only) that's unreachable → the report runs **telemetry-OFF** (still useful from handoff + git). To get telemetry there, point `CLICKHOUSE_URL` at the laptop's nebula CH endpoint — see the `activity` skill.

2. **Run the scan** (substitute `$ARGUMENTS`, or `--days 14` if none):
   ```bash
   nix-shell -p 'python3.withPackages(p:[p.requests])' --run \
     'python ~/workspace/devrc/scripts/session-analysis/initiative-scan.py --days 14'
   ```

3. **Present the output as-is** — it's already a ranked, skimmable report grouped by repo. Optionally lead with a one-line read: which initiatives are ●ACTIVE vs the most notable ○stalled one, and any next-step that looks owed. **Do not editorialize beyond the data** — momentum is *recency of touch, NOT % done*, and initiative↔commit linking is heuristic (see the script's honesty note).
