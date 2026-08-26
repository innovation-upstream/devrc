# `scripts/`

Operational scripts for the devrc environment. **435 tracked files**: 50 top-level entrypoints (plus
this README) and 13 subsystem directories that are small applications in their own right.

This index covers the 50 top-level entrypoints individually and the subsystems one line each. It
deliberately does not enumerate every file — most of the tree is `test_*.py`, and a flat listing
would be noise that goes stale on the next commit.

Descriptions come from each script's own header comment. Most are self-documenting: run with
`--help` where supported, or read the header.

## i3 status bar

Custom blocks for i3status-rust — each prints one block's state; the bar polls them.

| script | purpose |
|---|---|
| `i3status-claude-runs` | live count of Claude-Code-in-tmux runs (bar pill) |
| `i3status-airvpn` | host AirVPN WireGuard tunnel state |
| `i3status-alerts` | firing cluster-alert count |
| `i3status-civitai` | firing civitai-prod alert count (client cluster) |
| `i3status-clawgate` | clawgate operator-pending task count |
| `i3status-mail` | open mail-actions count |
| `i3status-media` | qBittorrent (behind the gluetun AirVPN sidecar) |
| `i3status-notifs` | merged notifications bell + DND indicator |
| `bar-status-poll` | decoupled status poller for the bar (workbench only) |

Click actions for those blocks:

| script | purpose |
|---|---|
| `airvpn-menu` / `airvpn-detail` | rofi action menu (left-click) / detail popup (right-click) |
| `media-menu` / `media-detail` | rofi action menu / detail popup for the media block |
| `disk-detail` / `disk-explore` | all-disks view / `ncdu` on the fullest real filesystem |
| `notif-center` | notification center for the notifications pill |
| `mail-triage` | interactive triage view for the mail block |

## Desktop / window management

| script | purpose |
|---|---|
| `i3-grid` | arrange all windows on a workspace into a grid |
| `rig-control.sh` | yad control panel (and CLI) for two workbench toggles |
| `i3blocks-rigcontrol` | launcher block for the rig-control panel |
| `monitor-blackout.sh` | black out the external monitor without powering it off |

## tmux

| script | purpose |
|---|---|
| `tmux-scratch-slots.sh` | canonical scratchpad slot table — the single source of truth |
| `tmux-scratch-picker.sh` | list/toggle/create `scratch-*` sessions |
| `tmux-scratch-monitor.sh` | live HUD of the last N lines across all scratch sessions |
| `tmux-scratch-status.sh` | scratch slot indicator for `status-left` |
| `tmux-claude-counters.sh` | aggregate Claude-window counters for `status-right` |
| `tmux-idle-update.sh` | batch-update window tab colours by idle time |
| `tmux-pipe-activity.sh` | manage `pipe-pane` for background activity tracking |
| `tmux-activity-emit.sh` | activity-telemetry shipper for tmux |
| `tmux-activity-receiver.sh` | receive piped pane output, update timestamps |
| `tmux-session-restore.py` | snapshot the live claude/tmux workspace, resume it post-reboot |
| `tmux-task-hook.sh` | Claude Code `Stop` hook (thin wrapper for fuzzyclaw) |
| `tmux-task-resume.sh` | `PreToolUse` hook — mark a window active |

## Agent & development tooling

| script | purpose |
|---|---|
| `verify-agent-work` | deterministic, repo-aware post-agent verification gate |
| `find-session.py` | find past Claude Code sessions by keyword |
| `memory-audit.py` | audit a project's auto-memory index (`MEMORY.md`) |
| `skill-audit.py` | audit `SKILL.md` byte budgets + reference routing (`/prune-skill`) |
| `resume-state.sh` | initiative-scoped live-state reconciler for `/resume` |
| `obs-read` | one-command, cluster-aware observability query tool |
| `playwright-nixos` | drive Playwright with the nixpkgs Chromium matching *this project's* pin (`--list` / `--select`) |
| `dogfood-cycle` | automate the civitai App Block "dogfood test cycle" |
| `run3` | run a command with stdout and stderr captured to **separate** files (never merged), reporting each stream's byte count, path and the command's own exit code. Use it whenever the question is *which stream produced this* — in zsh, `cmd 2>&1 >/dev/null \| consumer` hands the consumer **stdout**, so redirection order cannot answer it |
| `worktree-prune` | classify git worktrees across many repos as `dead` / `orphan` / `live` / `cannot-tell`, recording the EVIDENCE each verdict rests on. **Dry-run by default**; `--execute` additionally requires `--confirm N` matching the exact removal count, only ever touches `dead` rows, re-checks dirtiness immediately before each removal and never passes `--force`. 🔴 It does **not** decide "did this land" by ancestry: `git merge-base --is-ancestor` is false for every **squash**-merged branch forever, so containment is answered by four independent signals (`ancestor`, `pr-merged` via `gh`, `content-identical` over the branch's changed paths, `patch-equivalent` via `git cherry`) and anything none of them can answer is a loud `cannot-tell`, never a `dead` |

## Network / VPN

| script | purpose |
|---|---|
| `airvpn-updown` | tunnel PostUp/PostDown helper — the killswitch + split-tunnel |
| `airvpn-sudo` | privileged helper for the AirVPN block (via a NOPASSWD sudo rule) |

## Media

| script | purpose |
|---|---|
| `deep-search` | search all Prowlarr indexers, grab a release into qBittorrent |

## System

| script | purpose |
|---|---|
| `cpu-monitor.sh` | CPU desktop notifications on two independent conditions |
| `notify-failure.sh` | systemd `OnFailure` toast handler |
| `keylog-spin-capture.sh` | catch the `keylog.service` CPU spin in the act |

## Test runners & deploy

| script | purpose |
|---|---|
| `gate.sh` | **run the gate through this.** Wraps both runners, sends their full output to a LOG FILE (not a pipe — the pipe is what destroyed the status for four agents in one day) and prints a bounded summary, so **its exit code is authoritative**. Cross-checks that status against the runner's own `RESULT:` line and exits **90 = could-not-vouch** on a disagreement, a missing verdict, or a `panic: test timed out` — a different finding from "the tests failed" |
| `run-tests.sh` | **single source of truth** for running the Python suites. Per-target collected-test floors (`TARGET_FLOORS`, pinned two-way against the target list); the global floor is their SUM, never hand-written. `--check-targets` / `--check-floors` validate the two tables in milliseconds |
| `run-node-tests.sh` | single source of truth for running the `.mjs` suites. Per-suite floors + a two-way discovery pin; global floor derived as their sum |
| `ship.sh` | converge BOTH NixOS hosts (workbench + laptop) to `origin/main`, then verify — in **three independent halves**, because each is blind to the next: git/deploy state (rc 11), every managed path RESOLVES (rc 12), and every managed path serves the content the repo is at (rc 13). rc 12 reads its manifest out of the host's OWN active generation, so an old generation is self-consistent and passes it green — measured 2026-08-19, the workbench served the pre-#611 `~/.claude/RULES.md` under "488 checked, 0 dangling, 0 absent". rc 13 compares by CONTENT with git as the oracle (blob in the working tree = current, blob elsewhere in the object store = a HISTORICAL version = stale, blob git has never seen = rendered, not copied, so excluded), which needs no manifest→repo path table to rot. `mkOutOfStoreSymlink` targets resolve back into the repo and so can never be stale: they are counted separately, never as evidence, and the EXAMINED count printed is repo-sourced only — 0 of those is RED |
| `drift-check.sh` | **passive deadman** — is either host silently no longer receiving changes? READ-ONLY: fetches and reports, never fixes. Distinct rc per condition (8 un-pushed/diverged, 10 behind, 12 not-on-main, 3/4 cannot-evaluate, **2 checked-no-host**, **13 remote unreachable for `DRIFT_UNREACHABLE_ESCALATE` CONSECUTIVE runs**), aligned with `ship.sh`'s legend. Runs unattended as the `drift-check` systemd-user timer; drift ⇒ non-zero ⇒ the existing `notify-failure@` dunst toast. **Two rc's need their policy read, not just their name:** rc 13 is *not* "the laptop was unreachable this run" — a single miss is reported loudly and contributes **nothing** to the exit code, because the timer is workbench-only and its remote leg is a laptop that is routinely shut; it escalates only after N consecutive misses (default 4 ≈ 24h at the 6h cadence), a streak persisted under `$DRIFT_STATE_DIR` and reset the moment the host answers — *or immediately* if that streak cannot be persisted, since "how long" is then unknowable. rc 2 is usage **plus** any run that ended up observing no host at all (`--no-local --no-remote`, or `--no-local` with the remote unreachable below threshold) — a 0 there would be a green from a checker wired to nothing. Below-threshold softening applies to the remote leg only: a local rc 8 with the laptop shut still exits 8 |
| `lib/host-role.sh` | the ONE host-identity predicate (both hosts report hostname `nixos`, so identity comes from local IPv4s). Sourced by `ship.sh` **and** `drift-check.sh` — never copied |
| `lib/claude_sessions.py` | the ONE Claude-Code-in-tmux detector: pane list + `/proc` tree walk → live sessions, each with its task, busy flag and per-window activity age. Loaded by explicit path (never `sys.path`) from `i3status-claude-runs`, and **symlinked beside it** by `nix/graphical.nix` so the deployed pill can find it. It is strictly deeper than `session-manager`'s `pane_current_command =~ /claude/`, which cannot see a `claude` under a wrapper shell — the two are different rules on purpose, because a `/proc` walk is not reachable over SSH. Extracted from the retired `agent-ops` TUI; the importer set is pinned two-way by `tests/test_claude_sessions.py` |
| `lib/transcript_search.py` | the ONE search over the Claude Code transcript corpus (`~/.claude/projects/**/*.jsonl`): enumeration, JSONL parsing, ranking, snippets, `--since`, `--project`. Used by `find-session.py` and by `check-clickup-addressed/{search-sessions,check-completion}.py`, which each carried their own copy until 2026-08-24 — the two disagreed in **seven** ways, all of them silent (see the module docstring and the two `RED_AT_BASE` ledgers). 🔴 The corpus is NOT flat: 797 session transcripts sit at `<project>/<id>.jsonl` and **4,795 more** at `<project>/<id>/subagents/agent-*.jsonl` (2026-08-25; these drift); a subagent transcript is not a resumable session and is excluded by name. 🔴 **It is NOT the only `*.jsonl` walk in the repo, and this line said it was.** These other production files keep their own, each wanting a different unit: `scripts/collector/claude/_shared.py`, `scripts/collector/claude/tailer.py` (deployed as a standalone copy with no `scripts/lib` beside it), `scripts/session-analysis/extract_genesis.py`, `scripts/session-analysis/extract_user_msgs.py`, `scripts/session-analysis/initiative-scan.py`, `scripts/session-analysis/recon_cost.py`, `scripts/validation/reconcile.py`, `scripts/tmux-session-restore.py`. **There is no count in front of that list on purpose** — this line used to say "Six" while listing six and omitting `tmux-session-restore.py`, which the module docstring named and the ledger carried; the LIST is now pinned two-way against the ledger from both places by `test_the_prose_names_every_other_production_walk`. What IS pinned, two-way and repo-wide, is `tests/test_transcript_search.py`: those two CLIs reach the corpus only through this module, and **every** walk site in a git-tracked Python file — glob, `os.walk`, or a bare `iterdir`/`listdir`/`scandir` listing — is enumerated with its reason **and its count**, so a walk added to one fails the suite. 🔴 Read that test's docstring for the exact spellings it does and does not see before quoting it wider: a non-Python walk, a concatenated `".json" + "l"`, and an untracked file are all outside it. The narrower earlier claims were inert twice — a fifth walker planted at `scripts/fifth_walker.py` passed the file-list version, and a sixth at `scripts/sixth_walker.py` doing `iterdir` + `listdir` + `endswith(".jsonl")` passed the glob-only detector |
| `sync-claude-permissions.py` | apply the **reviewed baseline** `permissions.allow` to a host's `~/.claude/settings.json`. Idempotent and **strictly additive** — never removes, reorders or rewrites an existing entry, backs up before writing, refuses (distinct rc) on a missing or malformed file. Exists because that file is per-host/unmanaged, so nix cannot ship it; the laptop had **no `permissions` key at all** and prompted for operations the workbench allowed. The list is **curated, not copied** — #380 measured 38 junk entries in the workbench's 248, so a wholesale copy would replicate the accretion — and it is checked against #380's own detector before anything is written. `--dry-run` to preview |

**rc 15 and the per-host allowlist.** `drift-check.sh` also reports HOST parity: **dangling managed symlinks** (rc 14) and **`settings.json` top-level key-name / `enabledPlugins` divergence** (rc 15). `settings.json` is per-host and unmanaged by design, so three keys — `theme`, `voice`, `effortLevel` — are **explicitly enumerated** as legitimately per-host, each with its reason in the source, and are reported as `IGNORED` rather than counted as drift. It is an enumeration and not a pattern **on purpose**: an unknown key is drift by default, the list is not env-overridable, and `permissions` is deliberately NOT on it (see `sync-claude-permissions.py`).

**rc 17 and rc 18 — SOURCE parity, the third kind.** Some `nix/pkgs/**` derivations build from a local working tree of ANOTHER repo (`${workspace}/…`), and nothing converges those: a host can be byte-identical to `origin/main` and still compile months-old code. rc 17 fires when a package's own `srcDir` **subtree** is behind/ahead its branch's own upstream (the repo-wide numbers are printed beside it as information only — scoping is what keeps this from being a permanently-red gate). rc 18 is its ladder: a scope that could not be evaluated at all is reported every run and escalates only after N **consecutive** runs, per (host, scope), reset the moment it measures — `no upstream` on the structural ladder (`DRIFT_UNMEASURED_ESCALATE`, default 4), a failed `fetch` on a longer one (`DRIFT_UNMEASURED_FETCH_ESCALATE`, default 12), and `repo ABSENT` on **neither**, at any count. Without it a scope stayed unmeasured forever while the run read as clean, which is the same "did not look" green rc 17 exists to refuse.

## Subsystems

Each is a self-contained application with its own tests. Counts are tracked files.

| directory | files | what it is |
|---|---|---|
| `dl-router/` | 75 | download-routing sidecar — files downloads by page context, not filename |
| `browser-bridge/` | 66 | loopback rendezvous server + MV3 extension for driving live Brave |
| `collector/` | 59 | per-host activity-telemetry daemon and its sources (keylog, i3, tmux, Claude, OpenCode) |
| `initiatives/` | 41 | durable cross-repo initiative ledger — sync, web viewer, Q&A assistant |
| `session-analysis/` | 28 | scans over Claude transcripts — activity, adoption, initiatives, insights |
| `repo-cos/` | 22 | weekly "repo chief-of-staff" — scan repos, synthesize proposals, email them |
| `mail-actions/` | 21 | action-required extraction over the self-hosted inbox + invoice archiver |
| `validation/` | 19 | activity-telemetry validation harness (replay, invariants, reconciliation) |
| `tests/` | 19 | cross-cutting tests for the top-level scripts above |
| `task-spec-drafter/` | 15 | continuous deep-context task-spec drafter + digest email |
| `claude-hooks/` | 11 | Claude Code hooks — bash guard, nudges, notifier, hook registrant |
| `opencode/` | 6 | OpenCode activity source (tailer, backfill, schema) |
| `data/` | 2 | static data files |

## Conventions

- Shell scripts use `#!/usr/bin/env bash`; Python uses `#!/usr/bin/env python3`.
- NixOS hosts: ad-hoc dependencies come via `nix-shell -p <pkg>`, never a system package manager.
- Indentation follows the repo-root `.editorconfig` (2-space default, 4 for Python).
- 🔴 Many of these are deployed by home-manager rather than run from this directory, so **a `git pull`
  changes nothing that nix manages.** An edit here takes effect only after
  `home-manager switch --flake ~/workspace/devrc --impure`. Whether a given deployed path is live or
  a store copy is answered by `readlink -f`, never by diffing it against this repo.
