# Handoff: agent-setup-audit — 2026-08-03

**Canonical continuation doc.** The narrative record of what shipped and what was
disproved lives in `claudedocs/handoff-agent-setup-audit-2026-08-03.md` — read
that for history. **This file is for what is still open**, and exists so the next
session does not re-run probes that have already been run.

## Goal
Audit the Claude Code / opencode setup and mine the activity telemetry for
issues, token inefficiency and tool opportunities — then fix what was found.
17 PRs merged and deployed. What remains is listed below.

## State now

- **Branch:** `main` @ `57dde3c`, both hosts converged and switched.
- **Tree is DIRTY, and deliberately so** — pre-existing operator WIP, not mine:
  `D .npmrc`, `M .serena/project.yml`, `M nix/pkgs/tools/default.nix`
  (a commented-out `screenarc.nix` import), `?? nix/pkgs/tools/screenarc.nix`.
  Leave it. `ship.sh` reports "deployed origin/main + local WIP" because of it.
- **Gates green:** `nix build .#checks.x86_64-linux.pytests` → **5868 collected /
  5867 passed / 1 skipped / 0 failed** (floor 5600). `.#checks…nodetests` →
  **997 / 997**, 3 suites, 29 files (floor 970).
- **Deployed AND verified against the live artifacts on both hosts** (not just
  "the switch succeeded"): guard denies `git stash` / `git clean -f` /
  `talosctl reset` / `mkfs` / `dd of=/dev/*` while the allow-fence and the
  deliberate `rm -rf` exemption hold; the Grep/Glob nudge fires and stays silent
  on `git log | grep push`; `opencode --version` = 1.18.4 on both;
  deployed `RULES.md` = 33,105 B.
- **Open PRs I did NOT touch:** **#313 is yours** (`zach/rules-multiagent-lessons`,
  opened 07:07Z while this session ran). #300 and #294 predate this session.

### Numbers, start → now
| | before | after |
|---|---:|---:|
| always-on session context | 63,954 B (~17,285 tok) | 43,420 B (~11,735 tok) |
| opencode `AGENTS.md` | 47,741 B | 39,280 B |
| ClickHouse store | 112.4 GB | 82.1 MB |
| `MEMORY_LIMIT_EXCEEDED` | 113,378, climbing | 0 |
| gated tests (pytest / node) | 4,373 / 468 | 5,868 / 997 |

---

## Open investigations — live diagnosis state

### 1. `insights.py` degrades ~50-75% of runs FROM THE LAPTOP ONLY

- **Symptom + exact repro:** on the laptop (192.168.50.155, nebula endpoint),
  `set -a && . ~/.config/activity-collector/env && set +a` then
  `python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 14 --json`.
- **Observed (verbatim, re-measured 2026-08-03 after both ClickHouse fixes):**
  ```
  run1 rc=5 15683ms "status": "degraded"
  run2 rc=5 15600ms "status": "degraded"
  run3 rc=5 30604ms "status": "degraded"
  run4 rc=5 15612ms "status": "degraded"
  ```
  Earlier the same night it was 3/6 ok. Successful runs complete in **~400 ms**;
  failures stall to the full client timeout. Server-side exception, from
  `system.query_log`:
  `Code: 209. DB::Exception: Timeout exceeded while writing to socket ([::ffff:10.244.0.123]:…)`
  at `query_duration_ms` 30050 and 30101.
- **Ruled out:**
  - *ClickHouse memory* — the pre-fix error was `Code: 241 MEMORY_LIMIT_EXCEEDED`;
    it is now **`Code: 209`**, server RSS **913 MiB** against a 2.5 GiB ceiling,
    `MEMORY_LIMIT_EXCEEDED` = **0**, merges in flight = **0**.
  - *Server-side / the query itself* — **the discriminating control**: from the
    workbench (LAN `192.168.50.94:30123`), same server, same minute, same code:
    **6/6 ok, 329–401 ms, 333 sessions each.** Re-run it before assuming anything
    changed.
  - *Slowness* — raising `CLICKHOUSE_HTTP_TIMEOUT` to 90 made it **worse**
    (1/6 ok vs 3/6 at the 15 s default); failures then stalled 90 s and 180 s.
    A longer timeout buys a longer wait before the identical failure.
- **Leading hypothesis:** MTU blackhole on the nebula tunnel. Bimodal
  success-or-stall that is size-dependent (small aggregate queries pass, the
  larger response stalls) over a tunnel is the classic shape. **NOT CONFIRMED.**
- **Next probe (run verbatim, on the laptop):**
  ```
  tracepath -n 10.42.0.10
  for s in 1200 1300 1372 1400 1450; do ping -M do -s $s -c 2 -W 2 10.42.0.10 >/dev/null 2>&1 \
    && echo "$s OK" || echo "$s FAILS"; done
  ip link show nebula1 | grep -o 'mtu [0-9]*'
  ```
  A clean cliff between two sizes confirms it; if every size passes, the theory
  is dead and the next suspect is the NodePort path, not the tunnel.
- **Priority: LOW.** Workaround is real — run it from the workbench.

### 2. `session=''` on 97.7% of opencode `tool-call` rows

- **Symptom:** `2,736 of 2,799` `source='opencode', kind='tool-call'` rows carry
  an empty `session`, so tool calls cannot be grouped into sessions.
- **Observed / root cause (MEASURED, not inferred):** `session.created` is **not
  a plugin hook name** on opencode 1.18.4 — it is a **bus event type**. Probed
  with a throwaway `OPENCODE_CONFIG_DIR` + `opencode serve` + `POST /session`:
  the named `session.created` hook fired **0 times**; the generic `event` hook
  fired **once** with `event.type === "session.created"`. Same for
  `message.updated` and `session.idle`. Only `tool.execute.before/after` are real
  hooks. Corroborated: `kind='session-create'` and `kind='session-idle'` have
  **0 rows ever**; all `prompt`/`assistant-turn` rows come from `tailer.py`, not
  the plugin.
- **Ruled out:** a deployment problem — the plugin IS loaded and IS emitting
  (verified live on both hosts post-#302, real tool names, `name_captured=true`,
  exactly 1 row per call).
- **Leading hypothesis:** none needed; the cause is established. `currentSession`
  is only ever assigned by the dead handler.
- **Next probe:** none — go straight to the fix: subscribe to the generic
  `event` hook and switch on `event.type`, in
  `scripts/collector/opencode/activity-plugin.js`.
- 🔴 **Handle with care.** That file's last edit (#298, a stray
  `export const _internals`) killed ALL opencode telemetry on both hosts for
  ~11 hours, silently. opencode's loader rejects the ENTIRE module if any named
  export is not a function, AND invokes every *function* export as a plugin
  factory. **`ActivityPlugin` must remain the only export.** After any change:
  `home-manager switch`, run a real `opencode run` that makes a tool call, then
  confirm rows land — do not trust the switch.

### 3. ClickHouse regrowth — untestable now, and will therefore go unchecked

- **Observed:** A1 (truncate) + A2 (Flux `c7b20a72`: logger `trace`→`warning`,
  `text_log`/`trace_log`/`latency_log`/`processors_profile_log` removed, 7d/14d
  TTLs, merge pool 32→8 slots) took the store **112.4 GB → 82.1 MB**.
- **Why it is open:** 3.77 B `trace_log` rows accumulated in ~5 weeks. A2 should
  stop that, but "the store stays flat" needs **weeks** of observation and cannot
  be asserted now. Nobody will remember.
- **Next probe (run ~2026-09-01, or schedule it):**
  ```
  export KUBECONFIG=$KC_NEBULA
  PW=$(nix-shell -p sops --run "SOPS_AGE_KEY_FILE=$HOME/workspace/homelab-talos/.secrets/age.key sops -d --extract '[\"stringData\"][\"admin-password\"]' $HOME/workspace/homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml")
  kubectl exec -n activity deploy/clickhouse -- du -sh /var/lib/clickhouse/store
  kubectl exec -n activity deploy/clickhouse -- clickhouse-client --user default --password "$PW" \
    --query "SELECT database,name,formatReadableSize(total_bytes) FROM system.tables WHERE total_bytes>100000000 ORDER BY total_bytes DESC"
  ```
  Expect `activity.events` to be the largest table. If a `system.*` table is
  back above 1 GB, A2 did not hold.
- **Also still un-TTL'd:** `system.error_log` (2.85M rows) and
  `system.query_metric_log`. Negligible today, flat over the observation window,
  but they are the remaining unbounded tables.

### 4. Possibly-stranded branches (pre-existing, NOT from this session)

16 agent worktrees remain under `.claude/worktrees/`. **None is dirty** —
verified with `git status --porcelain` in each — so there is no uncommitted work.
But four carry commits not on `main`:

| branch | commits | PR |
|---|---:|---|
| `pr271-annotated-frame` | 3 (`--annotated` with `--frame`, ext 0.7.1) | **no PR** |
| `worktree-agent-a7046cc9fac6d40ef` | 3 (browser-agent `context` reachable) | **no PR** |
| `sandbox/opencode` | 2 (the #298 commits) | **no PR** (content merged via #298) |
| `clawgate-skill-facts` | 1 (corrects two stale skill facts) | **#211 CLOSED** |

🔴 **Do not use `git merge-base --is-ancestor` to decide whether these are
merged — a squash merge is never an ancestor**, so that test reports every
squash-merged branch as unmerged (it reported #310 as unmerged an hour after I
merged it). Check PR state instead: `gh pr list --head <branch> --state all`.
CLAUDE.md already claims the extension is 0.7.1 with `--annotated`+`--frame`
working, so `pr271-annotated-frame` may be a duplicate of merged work — **verify
by content, not by branch topology**, before deleting any worktree.

---

## Next steps (ranked)

1. **Fix `session=''`** (investigation 2). Highest value: it makes opencode tool
   telemetry groupable, and the cause is already established — no diagnosis left.
   Respect the single-export constraint and verify live.
2. **Schedule the ClickHouse regrowth check** (investigation 3). It is a
   two-command check that will simply not happen unless it is on a calendar.
   Add `system.error_log` / `query_metric_log` TTLs at the same time.
3. **Resolve the four branches** (investigation 4), then prune the 16 worktrees.
   `clawgate-skill-facts` is a doc correction on a CLOSED PR — exactly the
   "stranded lesson" pattern this repo has been bitten by repeatedly.
4. **RULES rule-family consolidation** (~6 KB). Three families are one rule split
   across sections: harness-validation / count-the-tests / parse-the-output;
   merged-tree / two-tiers (the second literally opens "extended to
   environments"); reachable-guard / mutation-test. #304 correctly declined to do
   this in the same PR because it rewrites rule statements — it needs its own
   🔴-scope audit. Note **#313 (yours) also edits RULES.md** — land that first.
5. **Layer B `prepare` timer + staging reaper + pending-count bar block.**
   Coverage 34% overall, **8% on the laptop**, 4 days stale on the workbench.
   🔴 A timer can only run `prepare` — the architecture deliberately requires a
   LIVE session for extraction. Do not "fix" it with `claude -p`; the
   anti-confabulation contract exists because the built-in `/insights`
   confabulated.
6. **`test_bash_guard.py` prints `RESULT: all good`**, colliding with the
   runner's own `RESULT: PASS`/`FAIL`. Any `grep "RESULT:"` over a gate log
   returns two subsystems interleaved, hook first. Cosmetic but it has already
   cost one agent real confusion.
7. **Nebula stall** (investigation 1) — lowest value, real workaround.

## Gotchas / decisions / dead-ends

- 🔴 **A dev-host green means nothing. The gate is `nix build
  .#checks.x86_64-linux.pytests`.** #298 was green under `nix-shell` and RED in
  the sandbox; it merged and had to be fixed. And **gate on the MERGED tree** —
  five individually-green PRs produced a 14-failure tree once.
- 🔴 **The nix build sandbox has no `/usr/bin/env`.** `patchShebangs` fixes the
  source tree but cannot touch a file a test writes at runtime. Use
  `scripts/testlib/mockbin.py`. Bit #298 and #306.
- 🔴 **`rebase.autoStash` is now `false`** (#307). A rebase/merge on a dirty tree
  will REFUSE rather than silently using the repo-global stash. **That refusal is
  the intended signal** — commit, or copy the file aside. Do not re-enable it:
  it was the one path that used the shared stash with nothing typed, so the
  PreToolUse guard structurally could not see it.
- **The guard blocks its own documentation.** Writing a commit message or PR body
  containing a banned command string trips the raw-text match. Use the Write tool
  + `git commit -F <file>` / `gh pr create --body-file <file>`.
- **`sops` is not on PATH** in a non-interactive shell — `nix-shell -p sops`.
- **`TRUNCATE` is refused above 50 GB** (code 359, `max_table_size_to_drop`).
  Drop partitions; for one over the limit,
  `touch /var/lib/clickhouse/flags/force_drop_table` (consumed after one use).
- **ClickHouse `<ttl>` on a `*_log` renames the old table to `<name>_0`**, which
  keeps its rows with **no TTL forever**. One-time per table, lazy on first use.
  Undocumented; derived from observation.
- **`~/.config/git/config` is a read-only store symlink** — `git config --global`
  fails with `Read-only file system`. Git settings go through
  `nix/programs/git/default.nix`.
- **zsh does not word-split an unquoted `$var`.** Bit twice in one session,
  including a `set -- $pair` that left `$2` empty and produced a *vacuous*
  merge test that reported clean three times.
- **A local worktree branch ref can resolve to `origin/main`**, making a
  `merge-tree` test compare main with main and return a confident clean. A
  positive control (the diffstat must be non-empty) is what caught it.
- **Deadman returns `count=0` for BOTH `ok` and `unreachable`.** Read `state`,
  never `count`. The bar already does this correctly (`tlm ?` Warning on
  sustained unknown) — do not "simplify" it to a count check.

## How to verify

```bash
# 1. Gates (the authoritative tier — NOT nix-shell)
cd ~/workspace/devrc
nix build .#checks.x86_64-linux.pytests .#checks.x86_64-linux.nodetests --no-link
#   expect: pytests 5868/5867/1 skipped/0 failed · nodetests 997/997

# 2. Guard is LIVE on this host (positive control first, else the denies are meaningless)
python3 - <<'PY'
import json,subprocess,sys,os
G=os.path.expanduser("~/.claude/hooks/bash-guard.py")
def d(c):
    p=subprocess.run([sys.executable,G],input=json.dumps({"tool_name":"Bash","tool_input":{"command":c}}),capture_output=True,text=True)
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] if p.stdout.strip() else "allow"
for c,w in [("git "+"add"+" -"+"A","deny"),("ls -la /tmp","allow"),("git "+"stash","deny"),
            ("talosctl -n 1.2.3.4 "+"reset","deny"),("rm -rf /home/zach/x","allow")]:
    g=d(c); print(("ok  " if g==w else "FAIL"),c,"->",g,"want",w)
PY

# 3. Telemetry deadman (workbench is where the poller runs)
ssh zach@10.42.0.30 'set -a; . ~/.config/activity-collector/env; set +a; \
  python3 ~/workspace/devrc/scripts/collector/deadman.py --json | head -6'
#   expect: state=ok, evaluated=17, count=0 · and ~/.cache/bar-status/telemetry.json fresh

# 4. insights.py — use the WORKBENCH; the laptop has the open nebula fault
ssh zach@10.42.0.30 'set -a; . ~/.config/activity-collector/env; set +a; \
  python3 ~/workspace/devrc/scripts/session-analysis/insights.py --days 14 --json | head -5'
#   expect: "status": "ok" — 6/6 when last measured
```
