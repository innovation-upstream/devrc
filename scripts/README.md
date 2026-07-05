# Scripts

Utility scripts for the devrc repo — automation, tmux tooling, system
monitoring, and development helpers.

## Top-level scripts

| Script | Description |
|---|---|
| `cpu-monitor.sh` | Desktop-notification daemon for sustained high CPU load, runaway processes, and high package temperature. |
| `dictate` | Wrapper that launches `dictate.py` from its venv; intended as an i3 keybinding target. |
| `dictate.py` | Voice dictation daemon using faster-whisper, with socket-based start/stop control. |
| `dogfood-cycle` | Automate the civitai App Block "dogfood test cycle": create, install, run, tear down, and upgrade. |
| `i3blocks-cpu` | i3blocks blocklet: per-second CPU utilization with warning/critical colour thresholds. |
| `i3blocks-disk` | i3blocks blocklet: disk usage for a mount point with colour thresholds. |
| `i3blocks-memory` | i3blocks blocklet: memory usage with colour thresholds. |
| `i3blocks-temp` | i3blocks blocklet: CPU package temperature (AMD k10temp / Intel coretemp). |
| `i3blocks-vpn` | i3blocks blocklet: Mullvad VPN status with left-click rofi menu and right-click details terminal. |
| `i3blocks-vpn-detail` | Script launched by `i3blocks-vpn` on right-click: shows full Mullvad status in a floating terminal. |
| `i3blocks-vpn-sudo` | Privileged helper (NOPASSWD sudo) for `i3blocks-vpn`: switch server, bring VPN up/down. |
| `setup-dictation.sh` | One-time setup: creates a Python venv and installs faster-whisper for dictation. |
| `ship.sh` | Converge both NixOS hosts (workbench + laptop) to `origin/main` and run `home-manager switch`. |
| `tmux-activity-emit.sh` | Focus-change hook that ships window/session telemetry to the activity-collector spool. |
| `tmux-activity-receiver.sh` | Receives piped pane output and updates per-window idle-timestamp files. |
| `tmux-claude-counters.sh` | Status-right widget: counts running/paused/waiting Claude windows across all tmux sessions. |
| `tmux-idle-update.sh` | Batch-updates tmux window tab colours based on idle time, called each status-interval. |
| `tmux-initiatives.sh` | Fzf dashboard (Alt+i) aggregating Claude sessions grouped by tmux session with project info. |
| `tmux-pipe-activity.sh` | Manages `pipe-pane` lifecycle (start/stop/switch) for background activity tracking. |
| `tmux-scratch-monitor.sh` | Live HUD popup (Alt+m) showing output from all 12 scratch sessions. |
| `tmux-scratch-picker.sh` | Scratch-session picker (Alt+Shift+G): list, toggle, or create scratchpads via fzf. |
| `tmux-scratch-status.sh` | Status-left widget: renders 12 scratch-slot hotkey letters coloured to match popup borders. |
| `tmux-task-hook.sh` | Claude Code Stop hook — persists task status to `~/.tmux/tasks/` (wraps `fuzzyclaw`). |
| `tmux-task-resume.sh` | PreToolUse hook — marks a paused task as running when Claude resumes work. |

## Subdirectories

| Directory | Description |
|---|---|
| `claude-hooks/` | Claude Code hook scripts for PR audit nudges, shell environment checks, and hook registration. |
| `collector/` | Activity-collector daemon: ships telemetry (tmux, i3, keylog, browser) to ClickHouse. |
| `mail-actions/` | Email processing pipeline: fetch, extract, filter, LLM-classify, archive, and notify via Clawgate. |
| `repo-cos/` | Weekly repo-consistency scanner: fuzzy-matches config, generates digests, and emails reports. |
| `session-analysis/` | Scripts for analysing Claude session logs and espanso usage patterns. |
| `task-spec-drafter/` | Agent that drafts task specifications from conversations using structured prompts. |
| `tests/` | Test scripts (e.g. `ship-converge.test.sh`). |
| `validation/` | Config-invariant validators, query assertions, and replay-based regression checkers. |