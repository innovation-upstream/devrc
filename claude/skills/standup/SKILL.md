---
name: standup
description: "One-shot fleet-wide status sweep across every active repo and cluster — your approved-mergeable/red/conflicting PRs, in-flight deploys and rollouts, and firing alerts split per cluster, as an action-first roll-up. Use for \"standup\", \"fleet status\", \"what's in flight\", \"check everything\", \"what's the state of everything\", or \"merged, what's next\"."
argument-hint: [all|repos|deploys|alerts|state|local|initiatives]
allowed-tools: Bash, Read
---

# standup — fleet status roll-up

**Run the deterministic sweep — do NOT hand-roll the queries:**

```
bash ~/.claude/skills/standup/standup.sh [all|repos|deploys|alerts|state|local|initiatives]   # default: all (~20–40s)
```

All logic lives in the script (every run identical, token-efficient, correctly attributed). Scopes:
- **repos** → open PRs **fleet-wide**; flags only **your** (`ME`) approved-mergeable / red-CI / conflicting ones. The repo set is DISCOVERED (`gh search prs --author=@me`) and unioned with the local checkouts, not hard-coded — `STATUS` names the number of repos it swept, and that count is the scope of the verdict. Read it: `across 28 repos` is a fleet claim, `across 9 LOCAL repos only — fleet discovery FAILED` is not, and a degraded run says `Nothing flagged IN WHAT WAS SCANNED` instead of `All clear`.
- **deploys** → Flagger canaries mid-wave + deployments not fully ready, per cluster.
- **alerts** → firing alerts **split by `cluster` label** (so the dp-1 multi-cluster fan-in is never misattributed — submodel-GPU alerts don't get blamed on dp-1 prod), with known-noise filtered.
- **state** → per-repo *working state* (the "where was I" view, distinct from the action queue): branch · ahead/behind origin · dirty-file count · last-commit age+subject · `⚠unpushed`/`⚠wip` flags · a pointer to each repo's `HANDOFF.md`/`STATE.md` if present. **Cross-host:** workbench `REPOS` read locally, the laptop's `LAPTOP_REPOS` (vetr, naida — tagged `(lap)`) over `ssh`; host-aware, works from either host. Informational — not folded into `ACTIONS`.
- **local** → **this host's** health, folded in from the retired agent-ops dashboard (the one panel with no other owner): failed **user** units, plus the last-run result and age of the five timer-backed services (`repo-cos`, `mail-archive`, `bar-poll`, `claude-src`, `collector`). Three distinctions it keeps, all of which used to be lies: *absent* (not installed here — normal for the workbench-only units on the laptop) is not unhealthy; *never run* is not `0s ago`; a non-success `Result` is unhealthy even while `ActiveState` reads inactive. Adds `Local N failed/M unhealthy` to `STATUS`. 🔴 **`n/a` there is not `0`** — `systemctl` present but the **user manager unreachable** (ssh non-login shell, a system unit, a container: no `XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS`) renders `— systemctl n/a`, `Local n/a failed/n/a unhealthy` and `coverage was degraded`, never an all-clear.
- **initiatives** → the durable cross-repo, cross-session *initiative ledger* (it absorbed the momentum panel of the retired agent-ops dashboard): momentum counts (active/slowing/stalled), **owed/held** next-steps (folded into `ACTIONS` — they need you), initiative-tied open PRs, and the most-stalled. Runs `initiative-scan.py --json` **telemetry-OFF** (fast, no creds); the full telemetry view is the **`/initiative-scan`** command. Skips gracefully if the script/`nix-shell`/`jq` are absent. Adds an `Initiatives Na/Ms/Kst` field to `STATUS`.

Output is **action-first**: a `STATUS` counts line, an `ACTIONS` block (only items needing you, each naming a drill-down skill), and a `Filtered:` transparency line. Only that digest reaches context — never the raw JSON.

## Acting on it
- Drill down per flagged item: app health `/check-app` · cluster `/check-cluster` · triage `/triage-dp` · deploy chain `/verify-deploy` · alerts `/manage-alerts` `/observability` · PR audit `/audit-pr`.
- A "deploy not ready" is often a **stuck new rollout** (new ReplicaSet crashlooping while the old one still serves) — confirm with `/verify-deploy` before alarm; prod may be fine on the old pods.
- `dp-1 ?f` = its Alertmanager was unreachable this run → `/manage-alerts`.

## Reading the PR counts honestly
- `ready`/`red`/`conflicting` count **your** PRs; `N open` counts everyone's non-draft open PRs in the swept repos. The line says `— yours` for exactly that reason.
- `≥N repos … counts are a floor` means `gh search prs` hit its cap (`PR_SEARCH_LIMIT`, default 300). Report it as a floor, never as a total.
- `X unreadable` means that many repos returned a `gh` error. Those repos contributed **zero** to every count — a low number may be the error, not the fleet.
- GitHub computes `mergeable` lazily: the first read of a stale PR returns `UNKNOWN`, which is neither mergeable nor conflicting, so the `conflicting` count can rise on a second run minutes later. It is a floor too.
- `ZacxDev/homebrew-tap` is excluded as release-bot noise (31 of 100 hits in one measured run). The exclusion is an enumerated list in `PR_REPO_EXCLUDE`, printed on the `Filtered:` line — an unknown repo is in scope by default.
- Cost: one `gh search` call plus one `gh pr list` per repo, run `PR_JOBS`-wide (default 6). Measured 2026-08-12: **9–12s for 28 repos**, against 14.5s for the old 9-repo serial scan.

## Maintaining it
- Inventory + tunables are the first ~60 lines of `standup.sh`: `REPOS` (a **seed** for discovery, not the scope), `LAP`/`LAPTOP_REPOS` (cross-host state), `CL_NAMES`/`CL_KC`, `ME=ZacxDev`, `HL_PROM`, `NOISE_RE`, `PR_SEARCH_LIMIT`/`PR_JOBS`/`PR_REPO_EXCLUDE`. Add a cluster there; repos with an open PR of yours need no entry.
- 🔴 **Two defects made this print `0 ready, 0 red` while one in-scope repo held 8 flagged PRs — both are pinned by `scripts/tests/test_standup_pr_sweep.py`, so read that before touching the parse.** (1) Flagged rows are separated by **US (0x1f), never tab** — tab is IFS whitespace, so a run of tabs collapses and an empty `reviewDecision` shifted `author` off the end, dropping every unreviewed PR. (2) `statusCheckRollup` mixes `CheckRun.conclusion` with `StatusContext.state`; reading only `.conclusion` made every failing StatusContext look pending.
- `gh search prs --checks failure` is a useful independent cross-check but is **not** a superset: measured 2026-08-12 it returned 11 where the sweep found 14, missing four PRs whose only failure was a CheckRun. Treat a disagreement as something to look at, not as the sweep being wrong.
- Runs under **bash** (sidesteps the zsh gotchas), uses `kubectl --request-timeout` / `curl --max-time` (no external `timeout`/`head`), port-forwards the dp-1 ClusterIP Alertmanager briefly. Missing repos/clusters skip gracefully.
- If a query shape changes, **fix the script and re-run to verify** (it's deterministic) — don't paper over it with prose here.
