# keylog — X11 full-content keystroke collector

Captures ALL keystrokes globally via the X11 **RECORD** extension (python-xlib),
maps keycode → keysym → character honoring modifier state (Shift / CapsLock /
AltGr), buffers them into typing units, annotates each with active-window
context, and emits them into the activity-collector spool in the v1 contract so
the existing daemon ships them unchanged.

**Full content, NO redaction** — an explicit self-instrumentation choice by the
machine owner. Records stay in the LOCAL spool until an authenticated ClickHouse
is in place; nothing here ships them off-host.

## How it captures without root / `input` group
XRecord operates at the X-protocol level: as the logged-in user owning the X
session, the daemon asks the X server to stream `KeyPress`/`KeyRelease` events
for `AllClients`. No `/dev/input` access, no `input` group, no setuid. It uses
two display connections — one blocked inside `record_enable_context`, one for
keymap + window-context queries (you cannot issue normal requests on the record
connection while it records).

## Files
- `keymap.py` — pure keysym→char mapping (Latin-1 + Unicode keysyms + named keys),
  modifier resolution (`resolve_char`, `group_index`, `shift_active`). Unit-tested.
- `chunker.py` — buffers keystrokes into typing units. Flush boundaries:
  **focus change**, **Enter** (`\n`), **idle timeout** (default 2 s), **max length**
  (default 4096). BackSpace edits the standing buffer. Pure + clock-injectable.
- `winctx.py` — active-window context via Xlib: WM_CLASS (app), `_NET_WM_NAME`
  (title), active window id (session key), i3/EWMH workspace.
- `spool_emit.py` — builds + appends a v1 spool line byte-compatible with the
  `emit` bash helper (free text base64-encoded; `ts`/`host` auto-filled). Shared
  with the browser receiver.
- `keylog.py` — the daemon: wires XRecord → keymap → chunker → winctx → spool.

## Chunking choice (why not one event per keystroke)
One spool row per keystroke would ship thousands of useless one-char rows. The
chunker accumulates characters and flushes ONE `kind=typing` event at a natural
boundary — focus change (typing moved windows), Enter (a line/command/message was
submitted), an idle pause (default 2 s of no typing = a unit), or a 4096-char
guard. BackSpace pops the last buffered char so the captured `text` reflects what
actually stood, not raw keystrokes. Emitted record:

```
source=keys  kind=typing  text=<captured chunk>  app=<wm_class>  project=""
session=<active window id>  payload={"title":…,"workspace":…,"flush":<reason>}
```
(`project` is empty — a CWD is not knowable from an X key event.)

## Config (env)
- `ACTIVITY_SPOOL_DIR`  — spool dir (default `~/.local/state/activity/spool`).
- `KEYLOG_IDLE_SECONDS` — idle gap that closes a unit (default `2.0`).
- `KEYLOG_MAX_CHARS`    — hard cap per unit (default `4096`).
- `KEYLOG_POLL_SECONDS` — idle-timer poll interval (default `0.5`).

## Run / debug
```sh
# Needs an X session (DISPLAY). Point at a TEST spool while validating:
nix-shell -p 'python3.withPackages(ps: [ps.xlib])' --run \
  "ACTIVITY_SPOOL_DIR=/tmp/activity-test-spool DISPLAY=:0 python3 scripts/collector/keylog/keylog.py"
tail -f /tmp/activity-test-spool/current.log
```
The staged `keylog` user service (After/WantedBy `graphical-session.target`)
runs it against the real spool once enabled via a converge step.

## Tests
```sh
nix-shell -p python312Packages.pytest --run "pytest scripts/collector/keylog/tests"
```
Cover keymap/modifier→char, chunking boundaries, and v1 emit-format correctness
(round-trips through the existing `collector.parse_line`).
