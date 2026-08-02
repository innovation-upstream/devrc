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
| `i3status-agent-ops` | live count of Claude-Code-in-tmux runs |
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
| `agent-ops` | tmux "mission-control" agent operations dashboard |
| `verify-agent-work` | deterministic, repo-aware post-agent verification gate |
| `find-session.py` | find past Claude Code sessions by keyword |
| `memory-audit.py` | audit a project's auto-memory index (`MEMORY.md`) |
| `resume-state.sh` | initiative-scoped live-state reconciler for `/resume` |
| `obs-read` | one-command, cluster-aware observability query tool |
| `playwright-nixos` | drive Playwright with the nixpkgs-provided Chromium |
| `dogfood-cycle` | automate the civitai App Block "dogfood test cycle" |

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
| `run-tests.sh` | **single source of truth** for running the Python suites |
| `run-node-tests.sh` | single source of truth for running the `.mjs` suites |
| `ship.sh` | converge BOTH NixOS hosts (workbench + laptop) to `origin/main`, then verify |

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
