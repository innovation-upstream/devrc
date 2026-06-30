# Handoff: productivity instrumentation (initiative tracker + autonomous audit trigger) — 2026-06-30

## Goal
"Use the activity telemetry to track ongoing initiatives + their progress" — built that instrument, plus the structural surfaces to make it (and the audit ritual) reflexive rather than opt-in. **Everything below is merged + live + verified on BOTH hosts.** The honest meta-note: this session was mostly TOOLING; the next move is to USE what it surfaces, not extend it.

## State now (all shipped, nothing in flight)
**Branch:** `main` @ `46cd613` (both hosts converged via `ship.sh`). Commits this session: `5dd2a64` (commands→home-manager + `/handoff` upgrade), `b7e7773` (archiver host-gate + audit-nudge hook), `18a450c` (hook false-positive fix), PR **#36** (initiative-scan), `7503ff4` (`/initiatives`), `bfbd186` (espanso `:eos`), PR **#40** (worktree-dedup) → `46cd613`.

- **`initiative-scan.py`** (`scripts/session-analysis/`, PRs #36+#40) — cross-repo initiative+progress ledger: handoff docs (registry) + git (commits/PRs by slug↔branch) + `activity.events` telemetry (momentum by `gitBranch`) → ranked report (momentum active/slowing/stalled · last-touched · next-step · owed/held). 56 tests. Telemetry-optional (degrades to handoff+git). **#36 was dogfood-audited via `/audit-pr` → caught a 🔴 UTC bug pre-merge** (parsed UTC `ts` as local; fixed). #40 collapses git worktrees to canonical repo (`git --git-common-dir`) — fixed the dup `datapacket-talos-{review-sandbox,flagger-autoscaler}` sections (56→35 initiatives live).
- **`/initiatives`** command (`claude/commands/initiatives.md`) — in-session surface; wraps SOPS creds + nix-shell + scan. *Weak opt-in surface by itself.*
- **`/standup` `initiatives` scope** (`~/.claude/skills/standup/standup.sh` + SKILL.md) — **the durable routed surface**: momentum counts, owed/held → `ACTIONS`, initiative-tied open PRs, most-stalled, `Initiatives Na/Ms/Kst` on `STATUS`. Runs scan telemetry-OFF (fast, no creds). Global skill → synced to laptop by `ship.sh`.
- **`audit-pr-nudge` hook** (`scripts/claude-hooks/audit-pr-nudge.py`, home-manager symlink; registered per-host in `settings.json` via `register-nudge-hook.py`) — fires on real `gh pr create` → Claude reflexively offers `/audit-pr`. The autonomous-trigger answer to the measured "audit hand-typed ~51×/7d, opt-in `/audit-pr` ignored".
- **Structural:** ALL slash-commands now in `claude/commands/` under home-manager (recursive symlink, both hosts lockstep). `/handoff` upgraded to capture live-diagnosis-state. `mail-actions-archive` timer host-gated to workbench (`serverMode`). espanso `:eos` = end-of-session ritual.
- **Docs/memory updated:** `CLAUDE.md` (initiative-scan + commands-under-hm), `activity` SKILL.md (initiative-scan consumer), close-the-loop `STATE.md` (top thread), memories `productivity-command-suite` (updated) + `activity-domain-classification` (new: civitai/discord = client work = signal).

## Next steps (ranked)
1. **USE the instrument — do NOT build more tooling.** `/initiatives` surfaced real owed work: **sysredis fal.ai 403** (the ONLY active-user-impact item — owed to the civitai-orchestration owner), App Blocks **#2820** functional-verify (OWED, human), get-started **flag flip** (HELD), safe merges **#226** (docs) / **#2629** (+migration).
2. **Next close-the-loop run picks a genuine VALUE thread** (run `/close-the-loop`) — the productivity-instrument loop is built; point it at value.
3. **Still-pending verifier from the QA-loops thread:** `activity-scan --days 7` (did manual vetr/naida QA browser-time drop).
4. **De-prioritized (explicitly more-harness):** initiative-scan B (cheap LLM per-initiative state), C (Grafana trend panel), the #40 retro-audit, a `/recheck` command.

## Gotchas / decisions / dead-ends
- **Opt-in commands DON'T stick (measured ~51×/7d hand-typed audit).** → autonomous trigger (the hook) + routed surface (standup fold) beat the opt-in `/initiatives`. This shaped every surfacing decision.
- **audit-pr-nudge gates on a `/pull/<digits>` URL in the command OUTPUT**, not the phrase `gh pr create` (which it once matched inside a commit *message* and misfired). Precision over recall — a noisy nudge gets tuned out.
- **initiative-scan honesty:** momentum = recency-of-touch, NOT % done; initiative↔commit linking is heuristic slug-matching (longest-slug-wins so siblings don't share counts); telemetry `ts` is UTC (the bug). The `(unknown repo)` telemetry bucket (~840 ev) = `scratch/naida-ai` + `scratch/vetr` (no handoff docs → never discovered) — out of scope, not a bug.
- **Commands are now read-only nix-store symlinks:** edit `devrc/claude/commands/*.md` + `home-manager switch`/`ship.sh`, NOT `~/.claude/commands/`. A NEW command file must be `git add`ed before it switches (flakes only see tracked files — hit this live).
- **`/standup` + `activity`/`close-the-loop` are GLOBAL skills** (`~/.claude/skills/`), synced workbench→laptop by `ship.sh`'s skills step — not in devrc git.
- **I merged #40 without a full `/audit-pr`** (small, additive, 56 tests, file already deeply audited on #36) — judgment call, flagged honestly; a retro-audit is item 4.4 if wanted.

## How to verify
```bash
# the instrument (full telemetry view):
/initiatives                 # or: nix-shell -p 'python3.withPackages(p:[p.requests])' --run \
                             #   'python ~/workspace/devrc/scripts/session-analysis/initiative-scan.py --days 14'
# the durable routed surface (telemetry-OFF, no creds):
bash ~/.claude/skills/standup/standup.sh initiatives    # → # initiatives section + owed/held in ACTIONS
# the autonomous audit trigger: open any PR → the nudge offers /audit-pr <n>
#   (test the hook logic: echo a gh-pr-create payload w/ a /pull/N URL into audit-pr-nudge.py)
# tests:
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests])' --run \
  'python -m pytest ~/workspace/devrc/scripts/session-analysis/tests -q'   # 56 pass
# both hosts converged:  cd ~/workspace/devrc && bash scripts/ship.sh   # → VERIFIED at origin/main
```
