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
  (`scripts/browser-bridge/server.py`), which emits TWO kinds:
  `kind=cmd` — one best-effort, metadata-only event per Claude-driven browser
  command (op/instance-key/outcome/latency + bare domain, **never** page content);
  this is the **usage** signal `session-analysis/adoption-scan.py` counts, so
  filter on it in any adoption query.
  `kind=heartbeat` — machine-generated every 900s by the bridge daemon
  (`payload.connected`), so the source has a cadence the deadman can measure.
  🔴 It cannot mark a bucket ACTIVE, because `deadman.py` defines active time from
  an ALLOWLIST of human-presence sources (`PRESENCE_SOURCES` = `keys` `i3` `tmux`
  `zsh`) and `browser-bridge` is not one of them. Anything machine- or
  agent-driven counted as operator activity makes every idle source on the host
  read DEAD overnight. Note `browser` is not in that set either — the bridge drives
  the same Brave profile the activity extension instruments, so agent navigation
  emits `browser` rows. `MACHINE_CADENCE` still exists in that module, but only for
  `scripts/agent-ops`' freshness panel — it no longer touches active time.
  The deadman's query is `FORMAT TSVWithNames`: the header is what stops a capture
  from the previous (also 5-column, `n_op`) format replaying as a wrong answer.
  Both reuse `keylog/spool_emit.py` for the v1 line format.
- `changed_paths.py` — **shared by BOTH session summarisers** (`claude/session-tailer.py`
  and `opencode/session_tailer.py`), and the one definition of the `changed_paths*`
  payload block. Lives at the collector ROOT, so each tailer puts its own grandparent
  dir on `sys.path` to import it; that resolves identically in the repo and at
  `~/.config/activity-collector/`. 🔴 It needs its OWN `home.file` entry in
  `nix/home.nix` — `claude/` and `opencode/` are `recursive = true` dir sources, so a
  file added *inside* either ships automatically and a file added *beside* them does
  not. `scripts/tests/test_collector_deploy_declares.py` pins that.
- `tests/` — pytest unit + round-trip coverage (mocks the HTTP endpoint).

## `session-summary` payload — changed paths, and absent vs zero
Both summarisers emit the same additive block. It exists because
`files_modified` is only a COUNT, and the downstream consumer
(`scripts/lib/subsystem_resolver.py`, which maps a session onto the subsystems it
touched) needs the PATHS.

| field | meaning |
|---|---|
| `changed_paths` | repo-relative, deduped, sorted, capped. **`null` = we could not observe the file set**; `[]` = we looked and nothing changed. Those mean opposite things downstream. |
| `changed_paths_total` | distinct count **before** the cap — the truncation discriminator |
| `changed_paths_truncated` | the list is a prefix, not the whole story |
| `changed_paths_outside_cwd` | distinct changed paths with no repo-relative form (scratchpads, other repos, temp worktrees). `total + outside_cwd == files_modified`. |
| `changed_paths_cap` | the bound this emitter was built with (256 — see the module docstring for the measurement) |
| `stats_unavailable` | `[]`, or the groups that could not be observed. `"files"` covers `changed_paths*` + (on opencode) `files_modified` / `lines_added` / `lines_removed` / `languages`; `"git"` covers `git_commits` / `git_pushes`. |

🔴 **A zero that means "cannot report" is indistinguishable from a real zero**, and
that ambiguity is what this block removes. OpenCode emitted `files_modified=0`,
`lines_added=0` and `git_commits=0` for **every** session for months because its
extractor read the tool arguments from the wrong place — and nothing could tell
that apart from a quiet week. The summariser now counts what it COULD read beside
what it saw, and reports the group as absent when the two disagree.

The two sources differ in ONE respect, deliberately: opencode emits `null` for the
unavailable integer counters, while claude keeps them ints. `session-summary` is
consumed live for `source='claude'` (`session-analysis/insights.py`,
`validation/invariants.py`), where changing a field's TYPE is not an additive change;
nothing reads opencode's, and its values were a constant 0 that was never true.
`stats_unavailable` is the uniform sentinel for both.

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
