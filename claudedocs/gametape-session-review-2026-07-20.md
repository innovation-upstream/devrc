# Gametape — Claude Code session review (2026-07-20)

**Ask:** read past Claude Code sessions on both hosts, extract my inputs + the agent's
delivery, then identify where to skip steps / simplify / speed up / automate.

## Method (so you can trust or discount the numbers)
- **Corpus:** 57 settled, un-extracted sessions from the **last 10 days**, both hosts
  (53 workbench + 4 laptop), via the Layer-B `session-insight` pipeline → `activity.events`.
  Report aggregates over 65 deduped extracted sessions (trailing 30d insight window).
- **Sampling:** 32 giant sessions (up to 635 chunks) were deterministically capped to a
  42-chunk head+middle+tail sample so they didn't dominate the read. Facets for those judge
  the session *arc*, not every turn.
- **Extraction:** 9 subagents, anti-confabulation contract (counts are deterministic ground
  truth; the model only writes qualitative facets). 10 empty-stub sessions correctly flagged
  unreadable and excluded.
- **Honest caveats:** leverage tags are model-surfaced, **not measured savings**. The
  theme-recurrence counts below are heuristic keyword clustering — directional, not exact.
  Corpus skews to **datapacket-talos** (client infra, 74 sessions) so themes lean that way.

## Scorecard — what the tape shows
- **Outcomes (n=55):** 51% fully / 45% mostly / 4% partially achieved. **0 not-achieved.**
- **Mean Claude-helpfulness: 4.85/5** (47×5, 8×4, none below 4).
- **Session shape:** feature_build (22) + investigation (17) dominate; goal mix ops/feature/bugfix/deploy/infra.
- **Interaction friction (qualitative):** wrong_approach 41, env_breakage 23, tool_error 16, slow_feedback 15.
- **Positive signal worth keeping:** repeated, correct **"flag before acting"** (refused a
  value-inverted spec, stopped before pushing release commits), **"deployed ≠ verified"**
  discipline, and honest self-correction against live metrics (refuted a false handoff claim
  about an orphan pool net-growing; corrected a wrong "CF-cache is the dominant lever"
  diagnosis when challenged). The tape is *not* a story of a struggling agent — it's a
  high-functioning loop with specific, repeated toil sinks.

## The opportunities — ranked by recurrence × leverage

Recurrence = distinct sessions the theme appeared in (heuristic).

| # | Pattern | Sessions | Move | Fix |
|---|---------|:---:|------|-----|
| 1 | **Hand-rolled observability queries** — port-forward → PromQL/LogQL/Pyroscope → python-parse → re-aggregate, rebuilt per read; a wrong service label silently returns zero | 12 | automate / speed-up | A standing `obs-read <cluster> <preset>` tool/skill that owns the port-forward lifecycle + a library of *validated* presets (503-by-service, single-pod CPU saturation, B2 Class-B breakdown) returning parsed tables. Kills the silent-zero footgun. |
| 2 | **Silent cross-surface drift, no detector** — a change lands on one surface, the co-requisite surface rots: stale DB hosts post-migration, `pr-deploy` auth drift, `schema.prisma` vs live indexes, cross-repo manifest mirroring, out-of-band Cloudflare/B2 config not in git | 12 | automate | Extend repo-cos into a weekly **drift detector**: DB conn-strings resolve? prisma-schema vs live indexes? cross-repo manifest parity? committed assert-scripts for out-of-band CF/B2 config. Emit to bar/mail. |
| 3 | **Proactive-alerting blind spots** — Pyroscope backend NotReady ~2d (no liveness probe), face-pass had no restart alert, HPA scales on fleet-avg CPU (blind to single-pod core saturation); several caught *only* because a human noticed Discord noise | 10 | automate | Alertmanager coverage audit: liveness probes present, single-pod-saturation rule, NotReady>N-min. |
| 4 | **"Merged ≠ live" verification** — hand-driving PR→release-cut→image-build→canary→live-artifact + multi-DB "did the prod write land" diffs against hand-captured baselines | 9 | simplify | A **`/verify-deploy` skill already exists but went un-invoked** ("a real efficiency miss", in-session). Surface it in kickoff/resume; extend it to the multi-DB last-mile so "is it live + did it write" is one command. |
| 5 | **Trust-but-verify subagent claims** — agents claim "build green"/"committed" but stop early or leave stale worktree state (`node_modules` symlink removed → false LSP flood; `vite build` doesn't typecheck) → Claude re-runs the authoritative gate every time | 8 | automate | A deterministic **post-agent verification gate** (audit-pr shape): re-run authoritative build/test/-race + assert the agent actually finished. Fix gopls workspace to include per-task worktrees. **Highest leverage given you work entirely via agents.** |
| 6 | **Browser automation broken on NixOS** — Playwright Chromium `GLIBC_ABI_GNU2_TLS not found` → every click-path / OAuth e2e is undrivable, forcing DB-baseline *proxies* for verification | 6 | fix-env (skips a whole workaround class) | Get Playwright working (nix `playwright-driver`, working chromium, or a container). Directly shrinks #4's toil and unblocks real e2e. |
| 7 | **No cheap "are my agents done?" signal** — sessions burned ~22 `ScheduleWakeup` polls ("agents still working") because the only done-signal is dumping full transcripts | 6 | automate | A completion webhook / clawgate task-status the loop can cheaply poll. (agent-ops dashboard partially covers the *human* view; the *agent* still polls.) |
| 8 | **Regenerable-cache / disk GC unautomated** — disk left to hit 82% before a manual scan; the green-tier reclaim (~40–751G) is deterministic | 6 | automate | Scheduled green-tier reclaim `systemd --user` timer (the scan is already written). |
| 9 | **Existing skill/knowledge un-invoked or no durable home** — hand-rolled sequences that a skill already covers; new platforms (remix, bar) had no skill until session close; MEMORY.md manual byte-cap compaction | 4 | simplify | Surface relevant skills at session start; auto-flag when a hand-rolled sequence matches an existing skill. |
| 10 | **"Where can I see the latest runs?"** asked 3× in one session — no surfaced run index | 3 | simplify | A "latest runs" index/link surfaced by the pipeline that produces them. |
| 11 | **Stale local build artifacts break pre-push/CI** — `node_modules` behind a pinned dep, ungenerated Prisma model, dirty tracked `output.css`/`go.sum` | 2+ | automate | Auto-sync (`pnpm install` + `prisma generate`) in the pre-push hook before the typecheck. |
| 12 | **Destructive/inline-shell footguns** — `git worktree remove --force` + a shell glob nuked unrelated worktrees' uncommitted work; inline-python-in-bash quoting failed repeatedly until rewritten as files | 2+ | guardrail | Extend the bash-guard hook: block glob-driven `worktree remove --force`; nudge inline-python → file. |

## Cut list — quick wins vs bigger builds
- **Quick wins (cheap, high-frequency):** #11 pre-push auto-sync · #8 disk-GC timer · #5b gopls-worktree config · #6 fix Playwright on NixOS · #4b surface `/verify-deploy` at kickoff.
- **Bigger builds (worth it — top recurrence):** #1 `obs-read` tool · #2 cross-surface drift detector · #5 post-agent verification gate.

## What's already being attacked (don't rebuild)
- Context re-gathering at session start → **resume-state digest** (`/resume` step 3) already targets this; extend it with the obs presets (#1) and skill-surfacing (#9).
- Human "what are my agents doing / blocked on me" → **agent-ops dashboard** exists; the gap is the *agent-facing* done-signal (#7), not the human view.
- Cross-repo improvement signals → **repo-cos** exists; the drift detector (#2) is a natural new signal class for it.

## Single highest-leverage pick
**#5 — the post-agent verification gate.** You work entirely through agents, "trust-but-verify
subagent claims" recurs in 8 sessions, and it compounds with #6 (fixing Playwright lets that
gate include real e2e). It converts a per-PR manual re-verification ritual into one deterministic
step and closes the "agent claimed green but stopped early" failure mode that shipped at least
one real money-path 500 regression (self-caught four PRs later).
