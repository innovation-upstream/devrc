# activity-collector

First slice of a personal activity-telemetry pipeline: per-host source hooks
emit events to a local spool; a user-systemd daemon batches them and ships to the
homelab ClickHouse `activity.events` table.

```
zsh preexec/precmd ─┐
tmux focus hooks   ─┼─► emit (pure shell, hot path) ─► spool/current.log
                    ┘                                        │ rotate
                                                collector.py ─┴─► seg-*.log ─► ClickHouse
                                                              (delete on HTTP 200)
```

## Components
- `emit` — pure-shell hot-path helper. Appends ONE event to the spool with
  atomic `>>`. No interpreter startup. Free-text fields are base64-encoded so
  arbitrary content (quotes, newlines, unicode, passwords) survives intact.
- `collector.py` — daemon. Rotates `current.log` → `seg-*.log`, parses, assembles
  JSONEachRow, POSTs to ClickHouse, deletes the segment **only on HTTP 200**.
  Offline-buffered (segments accumulate when the backend is unreachable),
  lossless on transient errors, no double-ship, on-disk cap by age + size.
- `tests/` — pytest unit + round-trip coverage (mocks the HTTP endpoint).

## Spool / emit line contract (v1)
One event per line in `current.log`. TAB-separated `key=value` tokens, first
token literally `v1`:

```
v1<TAB>ts=2026-06-23 14:00:00.123<TAB>source=zsh<TAB>kind=command<TAB>b64:text=<base64><TAB>duration_ms=42<TAB>exit_code=0
```

- Keys prefixed `b64:` carry a base64-encoded value (free text). The daemon
  decodes them.
- Known columns (`host source kind project cwd session app text payload`,
  `duration_ms exit_code`, `ts`) map straight to ClickHouse columns. Any other
  key is bundled into the JSON `payload` column.
- `ts` and `host` are auto-filled by `emit` if the caller omits them.

## Config
Runtime config lives in `~/.config/activity-collector/env` (chmod 600, **not** in
the nix store, **not** committed). The nix module seeds it from
[`.env.example`](.env.example) on first switch if absent. To use an authed
ClickHouse user later, edit that file — no code change.

## Manual run / debug
```sh
# one rotate+ship pass against the live endpoint
CLICKHOUSE_URL=http://clickhouse.homelab.lan python3 collector.py --flush-once
# tail the service
systemctl --user status activity-collector
journalctl --user -u activity-collector -f
```
