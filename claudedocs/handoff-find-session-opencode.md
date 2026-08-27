---
# No clawgate task — $CLAUDE_CODE_SESSION_ID was unset (exit 3)
---
# Handoff: find-session-opencode — 2026-08-26, closed out 2026-08-27

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
Extend `find-session.py` to search opencode sessions (SQLite DB) in addition to Claude Code
transcripts (JSONL), so `/find-session` covers both agent runtimes on both hosts.

## State now — SHIPPED
- PR #910 **MERGED** (squash `67f741cd`), **deployed to both hosts** via `scripts/ship.sh`,
  and **verified against the deployed artifact on both hosts**. Nothing outstanding on it.
- This doc's own PR is #911.

## 🔴 The verification found the feature was HALF BROKEN — read this before trusting the doc it replaced

The original commit (`cf4abde9`) worked **from the laptop only**. `REMOTE_HOST` was a single
hardcoded `zach@10.42.0.30` — the workbench itself. Run from the workbench that is a
self-SSH, it fails `Permission denied`, and `except Exception: pass` swallowed it. So the
laptop's 377 opencode sessions were **permanently invisible from the workbench**, and the
search reported a clean zero while doing it.

MEASURED on both machines, with terms exclusive to each host's DB:

| term | laptop DB | workbench DB | found FROM laptop | FROM workbench |
|---|---|---|---|---|
| `sensei` | 0 | 46 | 46 ✅ | 46 ✅ |
| `verify-117` | 3 | 0 | 3 ✅ | **0 ❌ the bug** |

After the fix (`c2612ec2`), re-measured on both hosts against checksum-identical bytes, and
again post-deploy against `~/workspace/devrc/scripts/find-session.py`:

| term | FROM laptop | FROM workbench |
|---|---|---|
| `sensei` | 46 `opencode:workbench` | 46 `opencode:workbench` |
| `verify-117` | 3 `opencode:laptop` | 3 `opencode:laptop` |
| `wizard` (on both) | 18 = 14+4, 18 unique ids | identical |

🔴 **The lesson worth keeping: the old "How to verify" section in this doc ran three
commands ON ONE HOST and read as proof the feature worked on both.** A single-host
measurement cannot see a per-host bug. Any claim of the form "works on both hosts" needs a
term that exists on ONE host and is absent on the other, run from BOTH.

## Second defect, found by the gate — the suite was not hermetic
`find-session.py` searched the LIVE opencode DBs during the test suite, so three
`test_transcript_search.py` fixtures asserting "exactly this one session" were silently
collecting real sessions. Confirmed pre-existing at `cf4abde9` (same 3 failures there).

🔴 The fix is injectability (`DEVRC_OPENCODE_PEERS` / `DEVRC_OPENCODE_DB`), **not** relying
on the DB being absent — it IS absent in the nix sandbox tier and present on the dev host,
so that test would have passed in the tier the merge gates on and failed on the dev host.
Those tests now run in 13.6s instead of 205s (they no longer SSH-scan a 1.5GB DB).

## Open investigations — live diagnosis state
(Nothing mid-diagnosis.)

## Next steps (ranked)
1. **`scripts/discord-embed-ext/extension/embed_enlarge.js` is dirty on the WORKBENCH and
   nix reads it** — `ship.sh` warned that the generation it built is `origin/main` PLUS
   that uncommitted file, so the workbench's deployed extension differs from `main`. Not
   this session's work; left untouched. Closes when it is committed or reverted and
   `ship.sh` reports no dirty nix-read path on either host.
2. **`test_bash_guard.py`'s `no catastrophic backtracking` check asserts on wall-clock**
   (~2.16s) and flakes under load — it failed one gate run at load 25.7 and passes
   standalone. Closes when the timing dependency is removed and it survives an induced-load
   run.

## Gotchas / decisions / dead-ends
- 🔴 **There is no "the remote host".** `PEERS` is a table of BOTH hosts; whichever one IS
  this machine is read off local disk and the rest go over SSH, decided from live interface
  addresses. Re-introducing a single `REMOTE_HOST` re-introduces the bug above.
- 🔴 **An unreachable peer must never look like an honest zero.** Every failure path warns
  on stderr naming the peer and saying the results are incomplete. Verified live: a bogus
  peer prints `peer ghost (zach@10.42.0.199) unreachable — its sessions are NOT in these
  results`. Surface that line to the user; the remaining hits otherwise read as complete.
- Workbench opencode DB is 1.5GB — SCP times out. The query runs over SSH instead. The
  remote timeout was raised 30s → 120s for this reason.
- The remote script is written to `/tmp/_oc_search_<label>.py` on the peer (per-label, so
  two concurrent peer queries cannot clobber each other) and executed there.
- `opencode session list` is project-scoped — from `~` it only shows "global" project
  sessions. That is the root cause of the ORIGINAL gap this feature closed.
- Nebula IPs are used for peer SSH (`10.42.0.30` workbench, `10.42.0.100` laptop), not the
  LAN addresses.
- The workbench **cannot SSH to itself** (`Permission denied`) — that is what made the
  original self-SSH fail silently rather than loop or duplicate.

## How to verify
🔴 Run BOTH blocks — one host cannot demonstrate a two-host claim. Substitute terms that are
actually exclusive to each host today; the two below were chosen on 2026-08-26 and the
corpus moves.

```bash
# FROM THE LAPTOP
python3 ~/workspace/devrc/scripts/find-session.py sensei     --opencode-only --limit 5
python3 ~/workspace/devrc/scripts/find-session.py verify-117 --opencode-only --limit 5

# FROM THE WORKBENCH — must return the SAME counts, tagged with the OWNING host
ssh zach@10.42.0.30 'python3 ~/workspace/devrc/scripts/find-session.py sensei     --opencode-only --limit 5'
ssh zach@10.42.0.30 'python3 ~/workspace/devrc/scripts/find-session.py verify-117 --opencode-only --limit 5'
```
Each row's `file:` is `opencode:<host>` — that is the attribution to check, and a row from
the OTHER host appearing is the whole point. Silence on stderr means both peers answered;
a warning line means the result set is incomplete.

```bash
# corpus partitioning still holds
python3 ~/workspace/devrc/scripts/find-session.py clawgate --claude-only   --since 2026-08-20
python3 ~/workspace/devrc/scripts/find-session.py clawgate --opencode-only --since 2026-08-20
```

## Evidence trail
- `scripts/tests/test_opencode_search.py` — red at `cf4abde9` (8 failed), green at HEAD
  (9 passed). Several fail at base with `AttributeError` because the old module had no peer
  concept; the behavioural proof of the bug is the measured table above, not those.
- Two positive controls in `test_transcript_search.py` prove the opencode leg is still wired
  in despite the test muting; both mutation-tested (stub the leg → both fail on their own
  assertions).
- Gate green in BOTH tiers on the branch (17214 pytest / 1292 node) and on the MERGED tree
  built off current `main` (17632 pytest / 1292 node), plus both Tekton legs on `c2612ec2`.
