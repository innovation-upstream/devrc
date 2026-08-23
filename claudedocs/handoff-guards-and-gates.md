# Handoff: guards-and-gates — 2026-08-22

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

> No `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** (nothing resolved).
> An unknown session id answers 200 with an empty array, so that cannot distinguish
> "touched no task" from "wrong id". No task was created.

## Goal

Trace and evaluate devrc's guards-and-gates system, then act on what the trace found. It
found that the merge-gating tier was **red on `main`** and that nothing ran it.

## State now

- Branch `main`, at `f3244aa8` when this was written (`dc25debf` #724 landed after, from
  another session). Working tree carries **other sessions'** edits to `claude/RULES.md`,
  `RULES-ARCHIVE.md`, `clawgate/reference/hooks.md`, `initiative-scan.py`,
  `test_rules_size.py` — not mine, left alone.
- **`main` is GREEN on both tiers**, verified on the merged tree, and `devrc-ci` passes.

**Merged this session**

| PR | squash | what |
|---|---|---|
| #696 | `64399219` | two guards that only worked inside a git checkout — the hermetic tier was red |
| #683 | `dfd2d203` | git fixtures escaping into the repo the suite runs from (GUARD 9) |
| #673 | `4dd14e68` | operator's global git config + 63 fixture commits to the real remote (GUARD 10), reconciled with #683 |
| #722 | `6546acdc` | the reap check called a zombie a live process — `devrc-ci` had never been green |
| #723 | `f3244aa8` | `merge-gate` marker `none` → `other` |

**#676 CLOSED, not merged.** Its reconciliation is preserved on `origin/integ/step3-676`
(`6d8e5db3`) with the diagnosis on the PR. See *Open investigations*.

**Not mine but load-bearing here:** #708 fixed the signal drift ceiling (a *third* session
hit that same red independently); #713 was a duplicate and is closed.

## Open investigations — live diagnosis state

### #676 cannot land: two guards both require `PATH[0]`

- **Symptom + exact repro:** merge `origin/guard/no-real-git-writes` onto `main`, run
  `nix build .#checks.x86_64-linux.pytests` → `collected=7384 passed=7344 failed=40`.
- **Observed (with values):**
  ```
  AssertionError: the stub dir must be the FIRST PATH entry; PATH starts with
    ['/build/pytest-of-nixbld/pytest-0/gitwrite0', '/build/tmp.4Jsl9x0clD', ...]
  scripts/tests/test_no_real_launchers.py:311
  ```
  GUARD 7 (`nolaunch`) asserts its stub dir is `PATH[0]`; #676's shim prepends its own.
  7 of the 40 failures name `gitwrite0` directly; the rest are the end-to-end suites that
  spawn the runner through the patched `subprocess`.
- **Ruled out:** *a naming collision* — renaming the module to `gitwrite_plugin.py` /
  `gitwrite.py` and moving its `SESSION_MARKER` off `nogit(session)` fixed the collisions
  and its own suite went **197 passed**; the 40 remained. *An ordering accident on its own
  branch* — it passed there because GUARD 9 and GUARD 10 were not yet registered in that
  conftest.
- **Leading hypothesis:** structural, not incidental. Two guards want to own `PATH[0]`.
- **Next probe:** none — this is a design decision for #676's author. Two options, on the
  PR: put the `git` shim **inside GUARD 7's existing stub dir** (one PATH entry, no
  ordering contract — my preference), or relax GUARD 7 from `PATH[0]` to "before any real
  launcher", which weakens a guard whose docstring argues specifically for `FIRST, not
  merely present`.

### One `devrc-ci` run's failure cause is UNMEASURED

- **Observed:** after #722, three runs were still red. Two (`xwljz` `028c1a0b`, `vbs8b`
  `b8401de6`) confirmed as the same zombie test and predate the fix. The third
  (`hmbbq`, `0696ac55`) — its pod log was already gone, `kubectl logs` returned nothing.
- **Ruled out:** nothing. It is unread, not diagnosed.
- **Next probe:** if that revision's PR is still open, push a rebase onto `main` and read
  the fresh run. Do not assume it was the zombie test.

## Next steps (ranked)

1. **Make `tekton/devrc-pytests` + `tekton/devrc-nodetests` REQUIRED.** This is the whole
   point of everything above: `main` went red **three separate times on 2026-08-21**, every
   one bookkeeping rather than a defect, and each was found only because a human happened to
   run the suite. `required_status_checks` on `main` is **null** and there are **0 rulesets**
   today, so the green ticks are a courtesy. Needs a GitHub settings action (sudo-mode class,
   an agent cannot do it):
   ```bash
   gh api -X PATCH repos/innovation-upstream/devrc/branches/main/protection ...
   ```
   or Settings → Branches → `main` → require status checks → add those two.
2. **#676** — hand the `PATH[0]` decision to its author; branch preserved.
3. **Read `hmbbq`'s cause** rather than assuming it (above).
4. **Stranded, unowned, untracked in the tree:** `nix/system/apply-nvidia-kernel-7.2.sh`
   (Aug 19, a staged `sudo` fix for nvidia-beta failing against kernel 7.2 — it is
   `chmod 644`, so it cannot be run as written) and
   `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`.

## Gotchas / decisions / dead-ends

🔴 **A green dev-host run is not evidence about CI, and vice versa — the two tiers are
blind in opposite directions.** Every defect this session was visible in exactly one:
`test_no_conflict_markers` and `subsystem_touch --validate` failed **only** in the nix
sandbox (`/build/src` is a copy, not a clone, so `git ls-files` exits 128 and a
cwd-derived `scope_for_repo` raises); the zombie reap failed **only** where PID 1 does not
reap. Run both before believing either.

🔴 **A brand-new check is not an instrument until it has passed ONCE.** `devrc-ci` was red
on its first **5 of 5** runs, all on the same test in a file none of those changes touched.
Its verdict carried no information about any of them.

🔴 **Read a red check's STEP LOG, not its verdict line.** Tekton's summary read
`passed=14843 failed=1`; my local run read `14844`/`0`. That looked like CI reproducing my
one known local failure. It was a *different* test and the totals coincided.

🔴 **`os.kill(pid, 0)` SUCCEEDS on a zombie** — it is not a liveness check. A non-reaping
PID 1 is reproducible locally, which is how #722 was verified against the real failing path
rather than a proxy:
```bash
unshare --user --map-root-user --pid --fork --mount-proc python -m pytest …
```

🔴 **A guard can be walked by a SPELLING collision, not just a logic hole.** #673 and #683
both emitted `GUARD 9` strings into one output while each suite asserted on them, so one
suite's assertion could match the other's output and pass for the wrong reason. Both also
defined `SESSION_MARKER = "nogit(session)"`, and GUARD 10's accounting fails a target
emitting other than exactly one marker — renaming only the *file* would have left that live.

🔴 **The integration defect neither branch could see:** GUARD 9 fingerprints whatever
`GIT_CONFIG_GLOBAL` names; GUARD 10 *points* it at a scratch file that exists in order to
be written. GUARD 9 called that a violation in every target (`errors=1` × 25, 39 failures).
The fix in `gitenv.py` is strictly stronger than what it replaced — the old code returned
early on *any* override, leaving a direct `~/.gitconfig` write unwatched.

🔴 **`core.hooksPath` is VOLATILE within a single session.** Measured `githooks/` early,
`NONE` hours later, with no action by anyone here. Re-measure immediately before you act on
it, never earlier in the session.

🔴 **The pre-push tier is the one that corrupts the tree it is pushing.** #696 was pushed
with `DEVRC_SKIP_TESTS=1` on purpose: that tier runs `--set all` against the real
filesystem, which is what wrote fixture commits onto `refs/heads/main` and overwrote
`remote.origin.url` earlier the same day. The hermetic gate had already passed.

🔴 **Concurrency here is extreme and duplicated work is the norm.** Three sessions
independently fixed the same signal floor; `main` moved five times mid-session; the branch
namespace holds **409** local branches (131 `worktree-agent-*`). Re-fetch before every
decision, and check whether someone has already opened your PR.

**Dead ends:** merging the isolation trio as one combined branch (they collide on
`conftest.py` three ways — sequential landing is what attributed each failure); using
`git branch --list` to inspect state (409 entries, unreadable); trusting `gh pr view
--json mergeable` immediately after a push (returns `UNKNOWN` until GitHub recomputes).

**zsh:** `origin/$b:path` ate `:s` as a history modifier and produced `bad substitution` —
brace it, `origin/${b}:path`.

## How to verify

```bash
# the authoritative gate, on the MERGED tree — both tiers
nix build .#checks.x86_64-linux.pytests   --no-link   # RESULT: PASS
nix build .#checks.x86_64-linux.nodetests --no-link   # RESULT: PASS

# the marker claim is machine-checked
python3 -m pytest scripts/tests/test_ci_claim_matches_reality.py -q

# is anything actually REQUIRED yet?  (empty output = still nothing blocks)
gh api repos/innovation-upstream/devrc/branches/main/protection --jq '.required_status_checks'

# the zombie fix, against the real failing path (not the dev host)
unshare --user --map-root-user --pid --fork --mount-proc \
  python -m pytest scripts/browser-bridge/tests/test_browser_agent.py \
  -k 'test_timeout_reaps_the_whole_process_group or zombie' -q
```
