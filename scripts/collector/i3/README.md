# i3source — i3 window/workspace focus collector

Subscribes to i3's **IPC event stream** (`i3ipc`) and emits ONE `source=i3`
record on EVERY window-focus and workspace-focus change — **independent of
typing**. The keylogger only captures focus context *when the user types*; time
spent **reading** a window without typing was invisible. This daemon fixes that
so per-app / per-workspace attention and context-switching become accurate.

GUI / **laptop-only** — needs a live i3 IPC socket (`I3SOCK`). It writes into the
same spool the activity-collector ships, reusing the keylogger's `spool_emit`
(single source of truth for the v1 line format).

## What it captures
- **window::focus** — the window the user switched focus to:
  ```
  source=i3  kind=window-focus  text=<title>  app=<WM_CLASS>
  payload={"title":<title>,"workspace":<workspace name>}
  ```
- **workspace::focus** — the workspace the user switched to:
  ```
  source=i3  kind=workspace-focus  text=<workspace name>
  payload={"workspace":<workspace name>}
  ```

Field names mirror the keylogger's `winctx` (`app`=WM_CLASS, payload
`title`/`workspace`) so `source=i3` and `source=keys` rows **union cleanly** in
queries. WM_CLASS / title come straight from the i3ipc container — **no Xlib
round-trip**.

## No dwell field (by design)
The daemon does **NOT** store an `active_ms`/dwell value. Dwell is computed
downstream from the **gap between consecutive focus events** (the dashboard's
deep-work / context-switch panels already work off timestamps). Storing a raw
dwell would re-introduce the "walked-away-for-hours" inflation that was just
fixed — so we emit bare focus-change events with an accurate `ts`.

## Robustness
- No reachable i3 IPC socket → log and exit non-zero; systemd (`Restart=always`,
  `RestartSec=10`) retries without a tight crash-loop.
- When i3 restarts, `i3.main()` returns/raises → we exit so systemd brings us
  back and we **re-subscribe** against the fresh i3.
- A `spool_emit` failure is swallowed (best-effort telemetry never kills the
  daemon).

## Files
- `i3source.py` — the daemon. `build_record(event)` is a **pure** function
  (i3ipc-event-like → v1 spool fields) so it is unit-tested with synthetic
  objects, NO live i3/X; `main()` wires `i3ipc` → `build_record` → `spool_emit`.
  The `import i3ipc` is guarded inside `main()` so the pure function is testable
  without the package installed.

## Dependency
`i3ipc` (nixpkgs `python312Packages.i3ipc`). The systemd `i3-source` user
service builds its python env with it.

## Run / debug
```sh
# Needs a live i3 (I3SOCK). Point at a TEST spool while validating:
nix-shell -p 'python3.withPackages(ps: [ps.i3ipc])' --run \
  "ACTIVITY_SPOOL_DIR=/tmp/activity-test-spool python3 scripts/collector/i3/i3source.py"
tail -f /tmp/activity-test-spool/current.log
```
The `i3-source` user service (After/WantedBy `graphical-session.target`) runs it
against the real spool once enabled via a converge step.

## Tests
```sh
nix-shell -p python312Packages.pytest --run "pytest scripts/collector/i3/tests"
```
Cover window-focus (normal + missing WM_CLASS/title) and workspace-focus →
correct `source`/`kind`/`app`/`text`/payload, pure (no network, no X, no i3).
