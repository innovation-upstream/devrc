# Handoff — Claude/opencode setup audit, 2026-08-02 → 03

Started as "audit our Claude Code setup, research opencode best practice, mine
`/activity` for issues". Ended with 12 PRs merged and deployed to both hosts.
Every number below was measured with the command named; where something is
inferred or unverified it says so.

## Outcome

| | before | after |
|---|---:|---:|
| always-on context per session | 63,954 B (~17,285 tok) | **43,420 B (~11,735 tok)** |
| opencode `AGENTS.md` (every session, incl. flash subagents) | 47,741 B | **39,280 B** |
| ClickHouse `/var/lib/clickhouse/store` | 112.4 GB | **82.1 MB** |
| `MEMORY_LIMIT_EXCEEDED` | 113,378 and climbing | **0** |
| gated tests (`nix build .#checks…pytests`) | 4,373 | **5,792** |
| typed `git stash` on both hosts | permitted | denied |
| `git stash` via `rebase.autoStash` | silent, guard-invisible | refuses |
| opencode `tool-call` telemetry | 2,736 rows reading `unknown` | real tool names |

## Merged

| PR | What |
|---|---|
| #295 | `git stash` + `git clean -f` enforced for Claude Code, not just opencode |
| #296 | opencode pinned declaratively at 1.18.4 (hosts had drifted 1.18.4 / 1.18.9) |
| #297 | PostToolUse[Bash] nudge toward Grep/Glob |
| #298 | opencode `tool-call` capture — **also caused an outage, see below** |
| #299 | `insights.py` failed silently with exit 0 on a healthy pipeline |
| #301 | ClickHouse A1 execution record + corrected runbook |
| #302 | opencode plugin declarative deploy — fixed #298's outage |
| #303 | `talosctl reset` / `mkfs` / `dd`-to-block-device enforced for Claude Code |
| #304 | RULES.md + CLAUDE.md split into always-on core + non-loaded archive |
| #305 | `~/.claude` log rotation + session-summary amplification fix |
| #306 | gate `scripts/dl-router/tests` — 989 tests no gate had ever run |
| #307 | `rebase.autoStash = false` |

Plus, outside devrc: **homelab-talos `c7b20a72`** (ClickHouse A2 — logger bounds,
`system.*` log removal + TTLs, merge pool 32→8), and cluster-side A1
(`system.trace_log` etc. truncated).

## The theme: every defect was SILENT

Not one of these announced itself. That is the single most useful thing to carry
forward.

- A guard that **structurally could not fire** (`rebase.autoStash` uses the
  shared stash *inside git*; the guard matches command text).
- A plugin that **did not load** (`emitEvent` swallows all errors by design).
- **989 + 508 tests** that were never in a runner's target list.
- `insights.py` reporting "telemetry unavailable" **with exit 0** on a healthy
  pipeline.
- Harnesses that **tested nothing** and reported green.
- ~~A runner printing `collected=0`, `RESULT: FAIL`, and **exiting 0**.~~
  **RETRACTED** — see Open. It exits 1; the real defect was a misleading
  diagnostic, not a false green. Relayed from a subagent report without being
  tested, which is the same class of error as the four corrections above — the
  difference is that this one made it into a merged doc before anyone checked.

## Corrections — things asserted then disproved

Recorded because the wrong version was believed and acted on for a while.

1. **"ClickHouse is at capacity because `activity.events`/`payload` is too big."**
   FALSE. `activity.events` is **27 MiB — 0.03%** of the instance. The cause was
   `system.trace_log` (69.4 GiB) + `system.text_log` (22.6 GiB), un-TTL'd, whose
   merges held ~1.03 GiB of the 2.5 GiB budget. Queries were **losing the
   OvercommitTracker lottery**, not being expensive.
2. **"opencode's declared model is being ignored — 74% of turns ran a model the
   config never mentions, and `nav` has zero turns."** FALSE — a measurement
   error. The config landed at 16:43 that same day; the 30-day window predated
   it by 29 days. **Zero opencode turns had occurred since the deploy.** Always
   check an artifact's age before diagnosing why it "isn't working".
3. **"The `clickup` skill is hand-placed, doesn't ship to the workbench, and
   drifts."** FALSE. `ship.sh:368` rsyncs `~/.claude/skills/` workbench→laptop,
   direction-guarded; both hosts had identical mtimes. Worse, acting on it would
   have been harmful: `accounts.json` is **mode 0600** and `/nix/store` is 0444,
   so "fixing" it would have made a credential file world-readable.
4. **A1 alone did not fix `insights.py`.** It fixed the memory ceiling and
   **unmasked** a second fault: `Code: 209 Timeout exceeded while writing to
   socket`. Control: workbench (LAN) 6/6 ok vs laptop (nebula) 3/6, same server
   same minute. Raising the client timeout made it **worse** (1/6) — it is not
   slowness. Bimodal ~400 ms or full stall. MTU blackhole is the hypothesis and
   is **NOT confirmed**.

## The outage we caused

PR #298 added `export const _internals = {...}` to `activity-plugin.js`.
**opencode's loader iterates every named export and rejects the ENTIRE module if
any is not a function** (`"Plugin export is not a function"`), so all opencode
telemetry died on both hosts for ~11 hours. Fixed in #302.

Two things worth keeping:
- **Every *function* export is also invoked as a plugin factory.** #298's
  `emitEvent` export would have emitted a junk row per launch; the crash masked it.
- It was found **by luck** — a manual verification nobody had scheduled. That is
  the entire justification for the telemetry deadman work now in flight.

Also uncovered: the laptop had **zero `kind=tool-call` rows for its entire
existence** (2026-07-29 → 2026-08-02) because `deploy-plugin.sh` was a hand-run
script executed once on the workbench and never on the laptop. `deploy-plugin.sh`
is now deleted; deployment is declarative.

## Open — nothing scheduled

- ~~`scripts/dl-router/tests/*.test.mjs` — 508 tests, ungated.~~ **DONE (#309)** —
  and it was **529**, not 508: `scripts/collector/browser-ext/tests` (21 `.mjs`)
  was ungated too, found by auditing every `*.test.mjs` rather than only the one
  directory named in the brief. The hard-coded glob was replaced with discovery
  plus a two-way pin, because adding two lines leaves the *next* suite ungated
  identically. Node gate 468 → **997**.
- ~~`run-tests.sh` can exit 0 while printing `RESULT: FAIL` / `collected=0`.~~
  **RETRACTED — this does not reproduce.** Measured on `13bc8bd`: a run without
  pytest importable prints 17 × "could not parse pytest's summary",
  `RESULT: FAIL`, and exits **1** (2 in another probe). It is structurally
  impossible: `RESULT: FAIL` prints only inside `if [ "$fail" -ne 0 ]`, and the
  next statement is `exit "$fail"`. The original report most likely read a status
  through a pipeline — the `rc=$?` trap RULES.md names. **The real defect is
  narrower and was fixed in #310**: `REQUIRED_TOOLS` checks *binaries* via
  `command -v`, but pytest is a *module*, so a missing pytest produced a
  confusing diagnostic blaming pytest's output format rather than a clean
  precondition failure. It also asserted `python3` while the runner calls
  `python`. Bad diagnostics, **not** a false green.
- ~~Telemetry deadman.~~ **DONE (#311)** — per `(host, source)` liveness measured
  in ACTIVE time (buckets where any source emitted), so away-time is not silence,
  and budgets are measured per pair rather than configured. Surfaces as a `tlm`
  pill via the existing workbench `bar-status-poll`; no new timer or secret.
  🔴 The load-bearing property, verified end-to-end against the pill's real
  renderer: deadman returns `count=0` for **both** `ok` and `unreachable`, so a
  consumer reading `count` alone would render "cannot tell" as "all healthy".
  It doesn't — healthy is invisible, sustained-unknown shows `tlm ?` (Warning),
  a dead source shows `tlm N` (Critical).
- **`kind=session-create` has never emitted a single row, ever.** **Cause found
  (#311), not fixed:** `session.created` is **not a plugin hook name** on
  opencode 1.18.4 — it is a *bus event type*. Probed directly: the named hook
  fired 0 times, the generic `event` hook fired once with
  `event.type === "session.created"`. Same for `message.updated` / `session.idle`;
  only `tool.execute.before/after` are real hooks. **Downstream cost:
  `currentSession` is only ever set by that dead handler, so 2,736 of 2,799
  `tool-call` rows carry `session=''`.** Deliberately not fixed in the same PR —
  the fix rewrites the plugin's hook surface, and that file's last edit killed 11
  hours of telemetry.
- **Nebula-path stall** — laptop `insights.py` 3/6, workbench 6/6. Deliberately
  deprioritised: workaround is to run it from the workbench.
- **`system.error_log` (2.85M rows) and `query_metric_log` are still un-TTL'd** —
  the remaining unbounded system tables after A2.
- **ClickHouse regrowth**: A2 should hold, but the ~4-week claim is untestable by
  construction. **Schedule a check** or it will be rediscovered at 112 GB.
- **RULES rule-family consolidation** (~6 KB): harness-validation /
  count-the-tests / parse-the-output are one rule split across sections; same for
  merged-tree / two-tiers, and reachable-guard / mutation-test. Needs its own
  scope audit — #304 correctly declined to do it in the same PR.
- **Layer B (`kind=session-insight`)**: 34% coverage overall, **8% on the
  laptop**, 4 days stale on the workbench. A timer can only run `prepare` — the
  architecture deliberately requires a live session for extraction, so do not
  "fix" it with `claude -p`.
- 9 `.bak` files in `~/.claude/` — deliberately excluded from rotation, delete by hand.

## Gotchas that cost real time

- 🔴 **A dev-host green means nothing.** `nix build .#checks.x86_64-linux.pytests`
  is the gate. PR #298 was green on `nix-shell` and **red in the sandbox**, and
  merged tree ≠ any individual PR.
- 🔴 **The nix build sandbox has no `/usr/bin/env`.** `patchShebangs` fixes the
  source tree but cannot touch a file a test writes at runtime. Use
  `scripts/testlib/mockbin.py`. Bit #298 and #306.
- **`sops` is not on PATH** in a non-interactive shell — `nix-shell -p sops`.
- **`TRUNCATE` on a >50 GB table is refused** (code 359,
  `max_table_size_to_drop`). Drop partitions, and for one over the limit
  `touch /var/lib/clickhouse/flags/force_drop_table` (consumed after one use).
- **ClickHouse `<ttl>` on a `*_log` renames the old table to `<name>_0`**, which
  keeps its rows with **no TTL forever**. One-time per table, lazy on first use.
  Undocumented; derived from observation.
- **zsh does not word-split an unquoted `$var`** — bit twice tonight, including a
  `set -- $pair` that silently left `$2` empty and produced a vacuous test.
- **A local worktree branch ref can resolve to `origin/main`**, making a
  `merge-tree` test merge main with main and return a confident clean. A
  positive control (diffstat must be non-empty) caught it.
- **`~/.config/git/config` is a read-only store symlink** — `git config --global`
  fails with `Read-only file system`. Git settings must go through nix.
