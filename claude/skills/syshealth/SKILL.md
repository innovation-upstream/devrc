---
name: syshealth
description: "One-shot deep system sweep: zombies (traced to the parent not reaping them), CPU/mem hogs, sustained runaways, load, swap. Use for: check procs, zombie processes, what's eating my CPU/memory, why is load so high, runaway process, is the box thrashing."
argument-hint: "[--systemd] [--fds] [--json] [--cpu-threshold N] [--mem-threshold GIB]"
allowed-tools: Bash, Read
---

# /syshealth — on-demand deep system inspection

```bash
python3 ~/workspace/devrc/scripts/syshealth $ARGUMENTS
```

Report-only. It never signals a process — a test pins that (`test_the_script_has_no_kill_flag`).

**Exit codes:** `0` clean · `1` warnings · `2` critical (sustained runaway, or memory
near exhaustion) · `3` could not measure. Read the VERDICT block, which names every
reason; the code alone does not say which of six rules fired.

## Reading the output

- **ZOMBIES** are grouped by the parent that is not reaping them, because that parent
  is the fix. 🔴 A zombie whose parent had died would already have been reparented and
  reaped — so a zombie that persists for days always indicts a **live** parent. The
  classic cause here is a container whose PID 1 is `sleep infinity`: it never calls
  `wait()`, so every dead child accumulates. Measured on the workbench 2026-09-04:
  18 zombies under one such pid, up to 7 days old.
- **CPU/MEM HOGS** collapse ≥3 siblings under a shared parent into one row (eight
  pytest workers are one finding, not eight).
- **RUNAWAYS** require sustained load — a high percentage *and* a real age.
- Each runaway prints its `cwd` and `ppid` beside a paste-ready `kill` line. Check
  those first: this box runs parallel agents whose test workers look exactly like a
  runaway, and killing one reads as a code defect in the branch they were verifying.

## The one thing to know before trusting any %CPU

`ps`'s %CPU is **cpu-time ÷ elapsed, averaged over the process's whole life** — not an
instantaneous sample. For a process a few milliseconds old the divisor is ~0 and the
number is meaningless. `--min-age` (default 10s) exists for exactly this and is why
this script is not four `ps` one-liners: a manual sweep on 2026-09-03 reported a
"runaway nix store scan at 240%" that was a 20-millisecond `pgrep`, and a 1100% row
that was the `ps` producing the report. This script excludes itself, its `ps` child
and its ancestors for the same reason.

## Flags worth knowing

| flag | effect |
|---|---|
| `--json` | same verdict, machine-readable; `exit_code` is in the document |
| `--systemd` | adds failed **user** units (`standup` also covers this) |
| `--fds` | adds open-FD counts; prints `unreadable` separately — most processes are not ours, and an unmeasured process is never folded into a clean count |
| `--ignore` | comma-separated substrings never to flag; shares `cpu-monitor.sh`'s default (`anno,logd`) so one `Environment=` value can serve both |
| `--cpu-threshold` `--mem-threshold` `--load-threshold` `--runaway-pct` `--runaway-age` `--min-age` | retune per host; the values used are echoed into the report |

## Not this skill

- **Always-on alerting** → `scripts/cpu-monitor.sh` (sampling daemon, dunst toasts,
  cooldowns, daily cap). syshealth sends no notifications and writes no state.
- **Disk / nix-store / host drift** → `scripts/drift-check.sh`.
- **Cluster or service health** → `standup`, `obs-read`.
