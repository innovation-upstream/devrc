---
name: standup
description: One-shot fleet-wide status sweep across all of Zach's active repos and clusters — open PRs (your approved-mergeable/red/conflicting), in-flight deploys/rollouts (canaries, not-ready deployments), and firing alerts split per cluster — as an action-first roll-up. Use for "standup", "fleet status", "what's in flight", "check everything", "what's the state of everything", or a cross-repo "merged, what's next".
argument-hint: [all|repos|deploys|alerts|state|initiatives]
allowed-tools: Bash, Read
---

# standup — fleet status roll-up

**Run the deterministic sweep — do NOT hand-roll the queries:**

```
bash ~/.claude/skills/standup/standup.sh [all|repos|deploys|alerts|state|initiatives]   # default: all (~20–40s)
```

All logic lives in the script (so every run is identical, token-efficient, and correctly attributed). It sweeps:
- **repos** → open PRs; flags only **your** (`ME`) approved-mergeable / red-CI / conflicting ones.
- **deploys** → Flagger canaries mid-wave + deployments not fully ready, per cluster.
- **alerts** → firing alerts **split by `cluster` label** (so the dp-1 multi-cluster fan-in is never misattributed — submodel-GPU alerts don't get blamed on dp-1 prod), with known-noise filtered.
- **state** → per-repo *working state* (the "where was I" view, distinct from the action queue above): branch · ahead/behind origin · dirty-file count · last-commit age+subject · `⚠unpushed`/`⚠wip` flags · a pointer to each repo's `HANDOFF.md`/`STATE.md` if present. **Cross-host:** workbench `REPOS` are read locally and the laptop's `LAPTOP_REPOS` (vetr, naida — tagged `(lap)`) over `ssh`; host-aware so it works run from either host. This is informational (not folded into `ACTIONS`).
- **initiatives** → the cross-repo, cross-session *initiative ledger* (durable counterpart to the live agent-ops dashboard — `$mod+i`/`prefix+A`; the old Alt+i HUD was removed 2026-07): momentum counts (active/slowing/stalled), **owed/held** next-steps (folded into `ACTIONS` — they need you), initiative-tied open PRs, and the most-stalled. Runs `initiative-scan.py --json` **telemetry-OFF** (fast, no creds); the full telemetry view is the **`/initiatives`** command. Skips gracefully if the script/`nix-shell`/`jq` are absent. Adds an `Initiatives Na/Ms/Kst` field to `STATUS`.

Output is **action-first**: a `STATUS` counts line, an `ACTIONS` block (only items needing you, each naming a drill-down skill), and a `Filtered:` transparency line. Only that digest reaches context — never the raw JSON.

## Acting on it
- Drill down per flagged item: app health `/check-app` · cluster `/check-cluster` · triage `/triage-dp` · deploy chain `/verify-deploy` · alerts `/manage-alerts` `/observability` · PR audit `/audit-pr`.
- A "deploy not ready" is often a **stuck new rollout** (new ReplicaSet crashlooping while the old one still serves) — confirm with `/verify-deploy` before alarm; prod may be fine on the old pods.
- `dp-1 ?f` = its Alertmanager was unreachable this run → `/manage-alerts`.

## Maintaining it
- Inventory + tunables are the first ~30 lines of `standup.sh`: `REPOS`, `LAP`/`LAPTOP_REPOS` (cross-host state), `CL_NAMES`/`CL_KC`, `ME=ZacxDev`, `HL_PROM`, `NOISE_RE`. Add a repo/cluster there.
- Runs under **bash** (sidesteps the zsh gotchas), uses `kubectl --request-timeout` / `curl --max-time` (no external `timeout`/`head`), port-forwards the dp-1 ClusterIP Alertmanager briefly. Missing repos/clusters skip gracefully.
- If a query shape changes, **fix the script and re-run to verify** (it's deterministic) — don't paper over it with prose here.
