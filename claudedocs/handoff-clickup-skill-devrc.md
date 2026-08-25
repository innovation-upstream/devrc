# Handoff: clickup-skill-devrc — 2026-08-14

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Bring the `clickup` skill under devrc management — version-controlled, deployed by
home-manager to both hosts, gated by CI — starting from an uncommitted directory at
`~/.claude/skills/clickup/` with **zero commits, no remote**, and a divergent hand copy
on the laptop.

## State now
**Done, merged, deployed, and verified. Nothing is in flight.** No action required.

| PR | commit | what |
|---|---|---|
| #438 | `9004af8` | skill → `claude/skills/clickup/`, node_modules via nix, 27 tests gated |
| #450 | `489a022` | catch-up authentication actually exercised; 154 tests |
| #462 | `ed35850` | guard cut 8,846 → 3,996 B (another session merged my commit — see Gotchas) |
| #463 | `8fe1654` | audit follow-ups on that cut; enforced byte ceiling |
| #455 | — | CLOSED unmerged, deliberately. Reasoning is on the PR. |

**🔴 Two later sessions have already superseded parts of this.** Verify before acting on
anything below:

- **#474 (`afb6fe0`) deleted the webhook listener entirely** — "shipped an
  unauthenticated server to both hosts and has never once run". Gone: `listen.mjs`,
  `lib/catchup.mjs`, `lib/webhook-site.mjs`, `api/webhooks.mjs`, and three test files.
  **The whole of #450's listener-hardening round no longer exists.** The `watch-*` /
  `watchers` / `webhooks` commands were removed from `query.mjs` too — checked, nothing
  stranded.
- **#479 (`157b082`) fixed the emoji desync** (issue #456, now CLOSED).

Current live state, measured 2026-08-14:
- `claude/skills/clickup/` = **32 files**; suite `files=3 tests=92 floor=88`, full node
  gate `TOTAL tests=1116 fail=0`.
- Deployed on both hosts as read-only store symlinks; `query.mjs accounts` and
  `markdownToClickUp()` both work from the store path.
- State (credentials, cache) lives in `$XDG_STATE_HOME/clickup`, **not** the skill dir.

## Corrections to my own reporting — read before trusting issue #456
I overstated one number and a later session measured it properly:
- **"7 of 26 modules affected" by the emoji desync was wrong — it is 4 of 26.**
- **`lib/markdown.mjs` "743 chars (6.8%)" does not reproduce.** That file has had zero
  astral characters at every commit that touched it. The figure came from a delta
  auditor's report that I relayed into the issue without re-deriving it.
- All four genuinely-affected modules were the listener, now deleted — so "0 affected
  today" is **dormancy, not safety**. The bug was a property of which files happened to
  carry emoji, not of the code.

## Next steps (ranked)
1. **Nothing required.** The migration is complete and both follow-up issues are closed.
2. If the ClickUp webhook feature is ever wanted again, start from #474's reasoning
   (it was deleted for never having run), not from #450's hardening — that code is gone.
3. `test/smoke-test.mjs` has **never been run** in any of this work. It does live writes
   against the real workspace. It now skips its list/space/doc/mention tests unless
   `CLICKUP_SMOKE_*` env vars or `smoke*` account keys are configured — neither host has
   them set, so a run today would skip most write tests.

## Gotchas / decisions / dead-ends
- 🔴 **`gh`'s `MERGEABLE/CLEAN` is not a merged-tree gate.** It missed two real problems
  here. (a) PR #440 landed a **fourth** open-coded copy of an assertion this work had
  consolidated — different lines of the same file, so no textual conflict, but the merged
  tree failed. (b) By the time #463 was ready, **#462 had already merged the same commit**,
  so merging as-is would have duplicated work. Both were caught only by building an
  integration branch off current `origin/main` and running the full suite there.
- 🔴 **Concurrent sessions moved state under an in-flight branch, twice.** A branch ref
  was deleted mid-PR (commit survived; recovered via
  `git push origin <sha>:refs/heads/<branch>`), and another session merged my commit as
  #462. Do not cache repo state across a long task — re-read it.
- 🔴 **A finding's stated MECHANISM was wrong three times while the defect was real.**
  Clearest: an audit diagnosed a 160 KB failure message as `ENAMETOOLONG` raising out of
  `Path.is_file()`. The `try/except OSError` written to match that diagnosis **did not
  fix it** — on this filesystem `is_file()` returns `False` instead of raising and the
  echo happens anyway. Assert the *observable* (message length ≤ 2 KB), not the mechanism.
- **Deciding to shrink a guard rather than fix it.** `skills_mapping.py` had never caught
  a real breakage — its one firing was a false positive — and making it correct grew it to
  29,920 B while still admitting six false all-clears. Cut to ~5.5 KB under a **test-enforced
  ceiling**, with source-resolution dropped because `ship.sh` already verifies it against
  the real filesystem on both hosts (`managed artifacts resolve — N checked, 0 dangling`).
  🔴 The kept half is **FILE-SCOPED, not config-scoped**: a sibling module doing
  `enable = lib.mkForce false` still reads clean, and `ship.sh` does not backstop that
  case (a disabled mapping produces no link, so nothing dangles).
- **`home.file` for `node_modules` deploys correctly and does not work.** Node resolves
  from the **realpath**, so `lib/markdown.mjs` starts in `/nix/store/…` and never sees the
  deployed path. Fix was a `claudeSkills` derivation injecting `node_modules` into the same
  store tree. Verified by `ERR_MODULE_NOT_FOUND` *after* a green switch.
- **The laptop needed manual prep**: its 825-file legacy `node_modules` directory would
  have made `ln -T` fail and `ship.sh` skip that host silently. Removed after gating on
  migrated credentials being present and byte-identical.

## How to verify
```bash
# gates (authoritative; read the RESULT line, not an exit code)
bash ~/workspace/devrc/scripts/run-node-tests.sh ~/workspace/devrc   # clickup files=3 tests=92 floor=88
nix build .#checks.x86_64-linux.nodetests .#checks.x86_64-linux.pytests

# the deployed artifact — the combination that matters (store path + node_modules + XDG state)
readlink -f ~/.claude/skills/clickup/SKILL.md      # must terminate in /nix/store
node ~/.claude/skills/clickup/query.mjs accounts   # must print the account
node -e "import(process.env.HOME+'/.claude/skills/clickup/lib/markdown.mjs')\
.then(m=>console.log(m.markdownToClickUp('**x**').length))"   # proves remark/unified resolve
```
🔴 Do **not** run `claude/skills/clickup/test/smoke-test.mjs` casually — live writes to
the real ClickUp workspace.
