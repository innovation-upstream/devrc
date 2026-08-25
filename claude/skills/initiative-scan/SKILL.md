---
name: initiative-scan
description: "Run the on-demand cross-repo initiative scan — every initiative with its momentum (active/slowing/stalled), last-touched, commits/PRs, next-step and the live tmux session hosting it; also snapshots/restores the tmux workspace across a reboot. Use for \"what am I working on\", \"what's in flight\", \"what's stalled\", \"where did I leave X\", \"which session is X in\". The durable board is `initiatives`."
argument-hint: "[--days N] [--repo PATH] [--json] [--tmux] [--exclude-slugs A,B] | snapshot | restore [--dry-run] | show — runs scripts/session-analysis/initiative-scan.py; defaults to --days 4 --tmux"
allowed-tools: Bash
---

# initiative-scan — on-demand initiative + progress ledger

🔴 **There is no `initiative-scan` binary — `which initiative-scan` finds nothing.** It is a
python script, and it does **not** live under `scripts/initiatives/` (that path is the
*durable board*, a different subsystem with its own `initiatives` skill). The one file is:

```bash
python3 ~/workspace/devrc/scripts/session-analysis/initiative-scan.py --days 4 --tmux
```

Plain `python3` is correct and sufficient — `requests` is only needed with telemetry ON (step
1), and `nix-shell` wrapping is what makes a repo `flake.nix` shellHook print a greeting in
front of the JSON. Use `--json` for machine-readable output.

Runs the read-only report and presents it. This is the durable, cross-session view; for what is running *right now* use **`session-manager`**. (Two predecessors were removed rather than kept: the `tmux-initiatives.sh` Alt+i HUD in 2026-07, and the **agent-ops** mission-control TUI — whose momentum panel this absorbed — with all three of its launchers.) Args: `$ARGUMENTS` (passed through to the script; default `--days 4`).

## 🔴 What the numbers mean before you repeat any of them
- **`momentum: active` means the initiative's handoff doc was TOUCHED recently — not that work is moving.** It is recency of touch, never % done. An initiative can read `active` with `commits: 0` and `open_prs: []` and have had nothing happen for a week.
- **Three flags in the report say which zeroes are measurements and which are "not wired up here". Read them before quoting a zero:**
  - `telemetry_available: false` → ClickHouse is unset/unreachable; momentum came from handoff + git + sessions only.
  - `gh_available: false` → **every `open_prs: []` and `merged_prs: 0` is UNMEASURED, not zero.** The PR fetch returns `[]` on any failure, so without this flag the two are identical.
  - `tmux_enabled: false` → no `--tmux`, or no tmux server; the session-linking column is absent, not empty.
  - `commits_unknown: true` on an initiative → its branch refs were unresolvable, so `commits: 0` there means "could not count", not "no commits".

## `--exclude-slugs A,B` — the ONLY way a row is hidden
Suppresses named initiatives: `--exclude-slugs observability-gaps-audit,repo-cos-precision`.
**Explicit list only — the scan never infers that an initiative is finished**, so nothing
disappears unless you name it. (A rejected WIP inferred "resolved" from `DONE`/`CLOSED`
markers in the handoff text and, measured over the real corpus, flagged 11 of 62 docs — 7 of
them on section headings like `### DONE this session` inside handoffs carrying live
next-steps. See devrc#824 / #778. Do not re-add that.)

Three things to know before quoting a suppressed run:
- **The header says what it hid**: `--exclude-slugs SUPPRESSED N initiative(s); asked for: …`.
  `SUPPRESSED 0` with a slug named means you misspelled it — matching is exact and
  **case-sensitive**, and a typo errors nothing.
- **A slug is NOT repo-unique.** Doc-anchored rows take it from the handoff filename and two
  repos can hold the same one; session-only rows derive it from the session title, so a
  suppressible slug need not match any file on disk. One name hides the row in **every** repo
  — `N` larger than the number of slugs you passed is the tell.
- **A suppressed initiative takes its live tmux session with it** and does *not* reappear
  under "no matched initiative". The header appends `N with a LIVE session` when that
  happens, so re-read it before concluding nothing is running.
- The text renderer appends an explicit NOTE for the telemetry-off and gh-unavailable cases; the `--json` output carries the raw booleans.

For the **durable, queryable** version of this data — the Postgres store, the 15-min sync, the
LLM recaps, the workbench viewer/board and its `/api/ask` assistant — use the **`initiatives`**
skill instead. This skill is the ephemeral scan that feeds it.

## Session snapshot / restore (survive a reboot)

If `$ARGUMENTS` is **`snapshot`** (or `save`), **`restore`** (optionally `restore --dry-run`), or **`show`**, run the workspace snapshot helper instead of the scan — it binds each live claude tmux window to its exact session id (by matching pane content) so you can bring the whole workspace back after a reboot. tmux-continuum already restores the sessions/windows/cwds; this relaunches the right `claude --resume <id>` in each.

```bash
python3 ~/workspace/devrc/scripts/tmux-session-restore.py <snapshot|restore|show> [--dry-run]
```
- **`snapshot`** — run BEFORE rebooting: writes `~/.config/initiatives/restore-plan.json` + a readable `restore-cheatsheet.md` (survives reboot). Present the cheat-sheet.
- **`restore`** — run AFTER reboot (once tmux-continuum has restored the shells): relaunches `claude --resume <id>` in each window; windows already running claude are skipped, and windows with no certain match fall back to the interactive picker. Use `--dry-run` first to preview.
- **⚠ host-local:** run it on the host you're rebooting — it reads that host's live tmux + `~/.claude/projects`. The plan is per-host.

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

2. **Run the scan** (substitute `$ARGUMENTS`, or `--days 4 --tmux` if none). With step 1's
   creds exported you need `requests`, so wrap it; **without** them run the plain `python3`
   form at the top of this file instead — it is faster and avoids the shellHook-chatter trap.
   ```bash
   nix-shell -p 'python3.withPackages(p:[p.requests])' --run \
     'python ~/workspace/devrc/scripts/session-analysis/initiative-scan.py --days 4 --tmux'
   ```
   - **`--tmux`** links each initiative to the live tmux session(s) hosting it — `[tmux:8,scratch7]` vs `[no session]` — by matching the claude pane's title (its session summary) against the initiative slug/title, scoped by the pane's cwd→repo. It also lists **live claude sessions with no matched initiative** (open work the ledger doesn't cover). Best-effort: on a host with no tmux server the column is silently omitted. This is the durable ledger fused with a live-session view. Drop `--tmux` if `$ARGUMENTS` explicitly overrides.

3. **Cross-check against live state before answering "what am I working on" queries.** The initiative scan measures *historical threads* (handoff docs + git), not what's happening now. A stale answer is worse than no answer. Before presenting, merge three signals in order of freshness:
   - **tmux sessions** (real-time) — what's running right now, which repo, what it's doing. The `--tmux` column covers matched sessions; also read the **unmatched sessions** section at the bottom — those are active work the ledger doesn't cover.
   - **Open PRs** (real-time) — work in flight awaiting review/merge. Run `gh pr list --state open --json number,title,updatedAt -L 10` per active repo. These don't appear in the initiative scan unless an initiative's handoff doc mentions them.
   - **Initiative scan** (lagging) — the historical thread + next-steps + momentum trend.
   
   If the tmux/PR picture disagrees with the scan (e.g. tmux shows heavy work in a repo the scan barely surfaces, or open PRs exist that no initiative claims), lead with the live signals and use the scan for context only.

4. **Present the output** — lead with a one-line read of what's actively being worked on (from tmux + PRs), then the broader initiative landscape. Optionally note which initiatives are ●ACTIVE vs the most notable ○stalled one. **Do not editorialize beyond the data** — momentum is *recency of touch, NOT % done* (see the section above), and both initiative↔commit and initiative↔tmux-session linking are heuristic (see the script's honesty notes; a multi-topic pane title may attach to one of several co-hosted initiatives). If you quote a count, carry the flag that scopes it: "0 open PRs (gh available)" is a measurement, "0 open PRs" off a `gh_available: false` run is not.
