# Handoff: nix-read-verdicts-and-stale-claims — 2026-08-27

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
Make the repo's claims about its own deploy state verifiable **at the moment of acting**,
rather than at the moment they were written. Started from "the workbench tree moves fast —
how do we get resilient to that"; the answer turned out not to be worktrees.

## State now
- Branch / PR: **all four PRs MERGED and SHIPPED.** No work in flight from this session.
  - devrc **#854** `1ef86e06` — `resume-state.sh` reports when the SKILL being executed is
    behind `origin/main` (the *deployed* copy, resolved by `readlink -f`).
  - devrc **#857** `8d196495` — `scripts/lib/nix_read_paths.sh` + both consumers.
  - devrc **#865** `55b5b84a` — parametrize VALUES made deterministic (`#855` pinned only ids).
  - devrc **#899** `8a689a10` — the load/flake harness, fixed and generalised.
- **Deployed and verified**: `ship.sh` rc 0, both hosts at `28035f99`, cross-host agreement
  COMPARED (not a one-host `NOT COMPARED`). Managed artifacts 555/505 checked, 0 dangling,
  0 stale. `#857` is live and reporting on real data: `nix-read-paths=304 hits=0`.
- 🔴 **The base clone `~/workspace/devrc` is NOT on `main`** — it sits on another session's
  `fix/discord-embed-css-and-espanso-alo` at `5dd106a0`. This doc was therefore landed from a
  worktree off `origin/main`, not from the base clone. **Check `git branch --show-current`
  before any write there.**
- Untracked on the workbench since 2026-08-02: `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`.
  In no commit and no backup. Correctly classified NOT nix-read (`nix/system/` is the
  deliberate exclusion), so it will never trigger rc 23 — it is just unsaved work.

## Open investigations — live diagnosis state

### The flake cluster — three independent random-red sources inside REQUIRED checks
- **Symptom + exact repro:** `tekton/devrc-pytests` and `-nodetests` are both required with
  `enforce_admins: true`, so any of these reds a PR at random with no override. Reproduce the
  first with `scripts/dl-router/tests/load_test_store.sh` (landed in #899).
- **Observed (with values):**
  | condition | red |
  |---|---|
  | the named test alone, 12 burners, no xdist | **0/20** |
  | whole `test_subsystem_store_api.py`, `-n 4`, no load | **0/10** |
  | whole `scripts/tests`, 6 burners, `-n 4 --dist loadfile` | **3/10** |
  Baseline separately measured at **2 red in 10** under `-n 4` on pristine `origin/main`.
  🔴 The three reds were **not** the test originally named — they were
  `TestLockoutOverHTTP::test_five_failures_lock_out_a_VALID_token_from_the_same_client`
  (runs 3, 7) and `::test_a_SUCCESS_does_NOT_buy_more_GUESSES` (run 5). Same mechanism:
  `assert "status=lockout-triggered" in audit[N]` where the record at that index is an earlier
  one — assertions index an audit log written asynchronously by a server subprocess and assume
  arrival *and* ordering within a fixed wait.
  The other two: `test_git_repo_isolation.py::test_live_cotenants_sees_another_process_in_the_repo`
  (1 unreproduced red, 0/40 serial, `assert ['69561:?'] == []`); and
  `test_subsystem_touch.py::TestCommitMutationKillMatrix::test_kills_the_LENGTH_guard`, which
  truncates a sha to **3 hex chars** and collides when the fixture repo holds two objects with
  that prefix — non-deterministic *by construction*.
- **Ruled out:** CPU load alone (0/20); single-file xdist alone (0/10). Only the gate's own
  shape reproduces it.
- **Leading hypothesis:** the defect is the shared `audit[N]` assumption across the file, not
  any one test.
- **Next probe:** a `BURNERS=0` run of condition D — it varied load *and* xdist together, so
  which one drives it is still unseparated. Both measurements are already posted as comments on
  devrc **#863**, which owns that file; do not open a competing PR against it.

### `core.hooksPath` writer — still unidentified (carried forward, not re-investigated)
- `hookspath-watch.service` (systemd --user, hand-placed, NOT home-manager) is `active`; log
  `~/.cache/hookspath-watch/events.log` holds only its `WATCH START` line — **0 catches**.
  `core.hooksPath` was unset at both `--local` and `--global` on every check this session.
  Delete the unit once it catches one.

## Next steps (ranked)
1. **Clear the stale agent worktrees** (devrc, no files in `main`). `git worktree list` showed
   **44** `agent-*` entries, ~10 added by this session; one was orphaned (directory present,
   git no longer tracking it) and blocked resuming its agent. `scripts/worktree-prune` shipped
   in #870 for exactly this. Cheapest item, no code risk.
2. **The flake cluster** — see the investigation block. Owned by devrc **#863** for the
   `test_subsystem_store_api.py` family; the other two are unowned. `IN FLIGHT: devrc#863`.
3. **`nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`** — decide: commit, delete or
   gitignore. Unsaved work since 08-02, in no commit and no backup.
4. **`ship.sh`'s verdict block has never been reached on a live host** — a real run exits rc 13
   earlier at `verify_managed_currency`. The classification half is verified against the real
   repo; the ship half is hermetic-only, and the laptop leg is stub-tested (no real ssh).
   Watch the first ship that reaches it.

🔴 **This list is a WORK QUEUE, and `claim-work` is its LOCK** — every `/resume` session draws
from it, so a *better* ranked list produces *more* duplicate work, not less. **Keep the
numbering stable: the rank is half a claim's identity** (`claim-work --slug-for <this doc>
<rank>`). Worktrees do NOT prevent this.

## Gotchas / decisions / dead-ends
- 🔴 **Worktrees are NOT the answer to a fast-moving shared tree, and the repo already knew
  it.** `scripts/claim-work.sh`'s header: *"WORKTREE ISOLATION IS NOT AN ALTERNATIVE TO THIS AND
  NEVER WAS… isolation is what HIDES it."* A worktree prevents a FILESYSTEM collision; task
  allocation is a different problem, and a worktree does not protect the base clone, which is
  the deploy source.
- 🔴 **`claim-work`'s lock is per-SLUG, so two sessions naming the same work differently never
  collide.** Measured: I claimed the gate fix under a free-form slug; another session shipped
  it as `#855` from a differently-named branch ~40 min earlier. The canonical-slug guarantee
  only holds via `--slug-for <handoff-doc> <rank>`. **Check by FILE touched, not by title** —
  that is what caught `#863` already owning `test_subsystem_store_api.py`.
- 🔴 **A required check went red because two PRs were each green ALONE and never gated
  together** — `#821` (a test file) merged 21:53Z, `#841` (4-way xdist) merged 00:31Z; the
  module-scope `mkdtemp` in parametrize ids only collides under xdist. `strict: false` means
  GitHub will never gate the merged tree for you.
- 🔴 **`nix build .#checks…` can return exit 0 with an EMPTY log** — the derivation was already
  in the store: a cached zero, not a run. Recipe: plain `nix build` → `grep -c "building '"`
  (1 = ran, 0 = cached) → **then** `--rebuild`. Do NOT reach for `--rebuild` first: it means
  "build again and compare" and **fails on a tree never built** (`some outputs … are not
  valid`), which reads exactly like a test failure.
- 🔴 **The artifact boundary is INDEX MEMBERSHIP, not commitment.** Established by controlled
  four-state build: committed → yes; committed+modified → yes, with the *working-tree* content;
  **`git add`ed but never committed → YES**; untracked → no, at every depth. `mkOutOfStoreSymlink`
  (LIVE) paths bypass the flake source entirely, so untracked genuinely *is* served there.
- 🔴 **A guard that asserts a SPELLING is walkable; assert the VALUE.** Two instances in one
  session: `#854`'s cap accepted `00`/`007` where it rejected `0`, and `#857`'s
  `require_positive_int` did the same. **And the obvious fix is wrong in the same direction:**
  `[ 99999999999999999999 -gt 100000 ]` does not answer "yes" — it **errors and evaluates
  FALSE**, so "reject when too big" waves the too-big value through. Require `-ge 1` **and**
  `-le CEILING` to both *succeed*, so a value bash cannot compare cannot prove itself. Same
  hazard reaches the consecutive-run ladders (`[ "$STK" -ge "$THR" ]` with a >2⁶³ threshold
  goes quiet forever).
- 🔴 **Fixing the ssh transport silently invalidated the line that reads its result.**
  `remrc=${PIPESTATUS[0]}` was correct for `ssh | tee`; after `printf | ssh | tee` index 0 is
  `printf`, which always succeeds — **every remote converge would have reported rc 0**,
  including a host skipped for un-pushed commits. Caught pre-merge; index is now `[1]`.
- 🔴 **Seven separate instruments were wrong about themselves this session**, each caught by a
  control rather than by inspection: a mutation battery counting only `N failed` so a *hanging*
  mutant (reported `1 error`) scored SURVIVED; a right-reason detector matching `--tb=short`
  source-line echo; a ceiling-guard regex `[A-Z_]+` silently dropping the one default with a
  digit in its name; an `ssh_shim` leaking the parent environment so the forwarding under test
  was supplied by the fixture; two refusal tests each omitting both fields so the sibling arm
  did the failing; a load harness whose split nodeid made every run fail; and my own probe that
  reported "accepted" for seven values because the script exited at a script-relative `source`
  before validation ran. **Build the negative and positive control first.**
- **A comment is a claim too** — three comments naming tests that do not exist were found and
  fixed across `#854`/`#857`. `git grep` each `test_*` cited in a comment; it resolves or it does not.
- **`--exclude-slugs`, `require_int` vs `require_positive_int`**: `DRIFT_PHASE2_TIMEOUT` and
  `DRIFT_SRC_FETCH_TIMEOUT` legitimately accept `0` (GNU `timeout` reads it as "no timeout").
  Do **not** blanket-swap the validators.
- **rc 17 resolved itself between measurement and action.** Reported workbench/laptop building
  different clawgate source; ~30 min later all three subtree OIDs agreed at `41d5ef52` because
  both repos had moved. Compare subtree OIDs directly rather than reasoning from commit counts.

## How to verify
```bash
# 1. #854 — the deployed skill copy is what gets compared, not the working tree
bash ~/workspace/devrc/scripts/resume-state.sh   # expect a `skill-read:` line in the digest

# 2. #857 — the classification is live and derives a non-empty set on BOTH hosts
bash ~/workspace/devrc/scripts/drift-check.sh 2>&1 | grep -E '^\[nixdirt\]|^\[srcrepo\]'
#    expect `nix-read-paths=<N>` with N in the hundreds; a 0 there is COULD NOT MEASURE, not a pass

# 3. #857's value guard rejects the spellings AND the uncomparable value
#    (run from a checkout; the script needs its sibling scripts/lib/host-role.sh)
for v in 10 1 0 00 000 007 99999999999999999999; do
  DRIFT_NIXDIRT_MAX=$v bash ~/workspace/devrc/scripts/drift-check.sh 2>&1 \
    | grep -m1 'DRIFT_NIXDIRT_MAX must' || echo "$v ACCEPTED"
done
#    expect 10 and 1 ACCEPTED; 0, 00, 000, 007 and the >2^63 value each rejected with a reason

# 4. #899 — the harness refuses rather than reporting a failure count it cannot vouch for
bash ~/workspace/devrc/scripts/dl-router/tests/load_test_store.sh 1 <<'X'
X
#    a nonexistent nodeid must print COULD NOT RUN and exit 91 with NO burners spawned

# 5. both hosts converged (read the PER-HOST lines, never the final verdict)
bash ~/workspace/devrc/scripts/ship.sh
```
