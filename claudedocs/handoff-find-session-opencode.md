---
# No clawgate task — $CLAUDE_CODE_SESSION_ID was unset (exit 3)
---
# Handoff: find-session-opencode — 2026-08-26

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Extend `find-session.py` to search opencode sessions (SQLite DB) in addition to Claude Code transcripts (JSONL), so `/find-session` covers both agent runtimes on both hosts.

## State now
- Branch / PR: `feat/find-session-opencode` → PR https://github.com/innovation-upstream/devrc/pull/910 (OPEN)
- What's DONE this session:
  - Created `scripts/lib/opencode_search.py` — queries local DB directly, workbench via SSH+python (1.5GB DB, SCP too slow)
  - Updated `scripts/find-session.py` — merges both sources by default, `--claude-only` / `--opencode-only` flags, `[opencode]` tag in output, resume via `opencode --session`
  - Remote host: `zach@10.42.0.30` (Nebula)
  - Committed: `cf4abde9` on `feat/find-session-opencode`
- What's IN FLIGHT: PR #910 needs merge
- Deploy/verify status: not deployed (PR not merged). After merge, run `scripts/ship.sh` to deploy to both hosts.

## Open investigations — live diagnosis state
(Nothing mid-diagnosis — this was a feature implementation, not a bug investigation.)

## Next steps (ranked)
1. Merge PR #910 (`gh pr merge 910 --squash` or let CI gate run)
2. Deploy to both hosts via `scripts/ship.sh`
3. Verify: `python3 scripts/find-session.py clawgate --since 2026-08-26` should show opencode results tagged `[opencode]`

## Gotchas / decisions / dead-ends
- Workbench opencode DB is 1.5GB — SCP times out. Solution: run python query over SSH (`ssh zach@10.42.0.30 "python3 /tmp/_oc_search.py"`), writing the script to `/tmp` on each call.
- `opencode session list` is project-scoped — running from `~` only shows "global" project sessions (stale since 8/23). This is the root cause of the original gap.
- The remote search script is written to `/tmp/_oc_search.py` on the workbench via SSH heredoc, then executed. Cleanup is the host's responsibility.
- Nebula IP `10.42.0.30` used for workbench SSH (not LAN `192.168.50.250`).

## How to verify
```bash
# Should show opencode results from workbench tagged [opencode]
python3 scripts/find-session.py clawgate --since 2026-08-26 --limit 5

# Opencode-only search
python3 scripts/find-session.py extensions --opencode-only --limit 5

# Claude-only (backward compat)
python3 scripts/find-session.py clawgate --claude-only --since 2026-08-26
```
