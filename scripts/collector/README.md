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
- `claude/tailer.py` — the Claude Code **message stream** source: tails
  `~/.claude/projects/**/*.jsonl` and emits one `source=claude, kind=prompt|command`
  event per genuine user turn / slash-command.
- `claude/session-tailer.py` — the Claude Code **Layer A** source: emits one
  `source=claude, kind=session-summary` event per session, whose `payload` is a
  deterministic whole-transcript rollup (tool counts, tokens, languages, git
  commits/pushes, churn, durations, interruptions, tool errors, models, …). This
  is the telemetry-native, durable, versioned successor to the built-in `/insights`
  `~/.claude/usage-data/session-meta` cache — **no LLM**. Idempotent +
  **emit-on-settle**: a per-transcript signature (mtime-ns + size) *and* the last
  emit time live in `session-summary-state.json`, and a summary is emitted when the
  session is first seen, when it has been idle for `CLAUDE_SUMMARY_SETTLE_MINUTES`
  (default 20 — the authoritative final rollup, re-fired after a `claude --resume`),
  or at most once per `CLAUDE_SUMMARY_INTERIM_HOURS` (default 4) while it is still
  live. It does NOT re-ship on every 5-min tick (that produced 27k rows over 702
  sessions, 97.4% immediately superseded). `activity.events` is append-only, so a
  session still accumulates a handful of summary rows; **consumers dedupe on read
  with `argMax(<field>, ingested_at)` grouped by `session`** (unchanged — every emit
  re-reads the whole transcript, so newest = most complete). See
  `scripts/session-analysis/insights.py`. A transcript
  that can't be parsed is emitted with `unreadable: true` — never fabricated.
  Both tailers share `claude/_shared.py` (ts/project/emit/root/iter helpers) and run
  on the same 5-min `claude-activity-source` timer (both hosts).
  Layer B (`kind=session-insight`, qualitative goal/outcome/friction) is a later PR.
- **External emitters** (write to the same spool, live outside this dir):
  `browser-ext/receiver.py` (`source=browser, kind=nav` — page nav/scroll from the
  collector's MV3 extension) and the **browser-bridge server**
  (`scripts/browser-bridge/server.py`, `source=browser-bridge, kind=cmd` — one
  best-effort, metadata-only event per Claude-driven browser command:
  op/instance-key/outcome/latency + bare domain, **never** page content). Both
  reuse `keylog/spool_emit.py` for the v1 line format.
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
