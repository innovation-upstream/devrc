---
clawgate-task: 322
---
# Handoff: git repo-pointer leak + the CI gates that hid it — 2026-08-24

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ Invoke it with an ABSOLUTE `--repo` or from outside `devrc`: run as `--repo devrc` from
inside the repo it resolved `<cwd>/devrc` and died `cannot change to '…/devrc/devrc'`.

## Goal
Answer clawgate #322's probe (does `--set all` rewrite the branch it pushes?), then fix
what it actually found. It found a family: a leaked `GIT_DIR` aims git-writing programs at
a foreign repository. Three programs, three PRs, all merged.

## State now
- Branch: `main`, clean except other sessions' WIP (`scripts/run-node-tests.sh`, +1 line).
- `origin/main` moved **~15 times** during this session; every gate result below names the
  sha it was measured on.

**MERGED and verified by content**
- **#721** `081838cc` — `commit.sh` strips the git repo-pointer vars. Live on **both** hosts.
- **#767** `a8a7e94f` — `backup.py` (the one with a NETWORK) strips them via the shared
  `testlib.gitenv` ledger. Live on the workbench.
- **#801** `0f366a4e` — `dl-router` `Store._write()` retries `SQLITE_BUSY`. **NOT live** —
  see below.

**Deploy status — honest, and it differs per program**
- `commit.sh` and `backup.py` run **from the working tree** (`ExecStart=… %h/workspace/…`),
  so `readlink -f` terminates in the repo and the `git pull` made them live. Verified by
  grepping the file the unit actually executes.
- 🔴 **`dl-router` does NOT** — the unit runs
  `/nix/store/y2686x2sgh7kn2qys66db3q4fhn93vx0-dl-router/server.py`, the running copy has
  **0** occurrences of the retry, and the unit is `active` on old code. Needs
  `home-manager switch --flake ~/workspace/devrc --impure` + `systemctl --user restart dl-router`.
  Not run because a switch drops every `home.packages` binary for ~1s and **3 sibling gate
  runs were live**; that failure surfaces as a phantom defect in someone else's branch.
- **Laptop** was behind at last check and converges itself; `commit.sh`'s fix was confirmed
  live there.

**Cards filed this session:** #343 (backup.py, complete), #349 (dl-router, complete),
#348 (CI preemption, open), #355 (timeout invariant, open). Evidence added to #337.

## Open investigations — live diagnosis state

### The `pre-push` → `tests-on-push.sh` route has never been exercised end-to-end
- **Symptom + exact repro:** #322's original report — `git push -u origin <branch>` from a
  **linked worktree** hangs ~2min, then the branch HEAD is fixture commits
  (`autocommit: N change(s) in the some-scope analyze-service index`), the real commit gone,
  the index wrecked, working files surviving on disk.
- **Observed (with values):** mechanism proven at the unit level and reproduced end-to-end
  against a decoy. git 2.55.0 exports `GIT_DIR` into a pre-push hook **only from a linked
  worktree** (main checkout → `GIT_EDITOR`/`GIT_EXEC_PATH`/`GIT_PREFIX` only). With it set,
  `git -C "$scope" rev-parse --show-toplevel` returns `$scope` itself → `scope_repo_state`
  reports "its own repo" → the nesting guard is skipped. Decoy went `2320f0a → 8c952fe`,
  HEAD tree `a.md`/`alpha.md`/`beta.md`.
- **Ruled out:** *`--set all`* — REFUTED. Full battery at `8f7473b5`:
  `before == after == 8f7473b5`, tree clean, `collected=14689 passed=14687 failed=0`.
- **Leading hypothesis:** the fixes close it; the hook path is inference.
- **Next probe:** push a throwaway two-file docs commit from a linked worktree with
  `core.hooksPath` set repo-locally to `<repo>/githooks`, and diff HEAD before/after.
  🔴 Re-measure `core.hooksPath` at that moment — it flipped **three times** in one day.

### Nothing exercises the real store, MinIO, or the systemd namespace
- **Observed:** all three fixes were verified against decoys and `env -i PATH HOME`
  (the unit's environment *shape*), never under a real namespace, and `backup.py` was never
  run against the real store or allowed to upload.
- **Ruled out:** doing it casually — a real run ships client-identifying content off-box.
- **Next probe:** `systemd-run --user` with the unit's exact sandbox properties against a
  **throwaway store**, not the live one. The import-reachability half of this was already
  done that way (`IMPORT OK — ledger has 11 names`, with a negative control).

## Next steps (ranked)
1. **Run the dl-router deploy when the box is quiet** — `home-manager switch --flake
   ~/workspace/devrc --impure && systemctl --user restart dl-router`. Check for live gates
   first (`pgrep -f run-tests.sh` matches your OWN shell — resolve PIDs and read
   `/proc/<pid>/cmdline`). devrc, no files.
2. **#355 — pin the timeout invariant** (`homelab-talos`,
   `clusters/homelab/apps/tekton-pipelines/triggers/*.yaml`, `scripts/tests/`). Margins are
   3–5min and unenforced. 🔴 The defect is already FIXED in all five pipelines; the card
   guards it, do not re-fix it.
3. **#348 / #337 — CI preemption.** Both my prescriptions on #348 are RETRACTED. The
   repo's own record names the open levers: bound devrc CI concurrency, and right-size the
   remaining pipelines' requests (`gitops-validate` was done by homelab-infra #389).
4. **Converge the laptop** (`scripts/ship.sh`) once the workbench deploy lands.

## Gotchas / decisions / dead-ends
- 🔴 **Read the subsystem's decision record before proposing a fix.** I recommended four
  fixes for the CI preemption; `ci-priority-classes.yaml` had already tried and rejected
  **all four** with measurements. Separately I dispatched an agent to right-size pipeline
  requests — already merged as homelab-infra #389 an hour earlier. A
  `# 🔴 was tried and REVERTED` comment is the cheapest thing in the repo to find.
- 🔴 **`ci-bulk` at `-10000` is CORRECT, not a misconfiguration.** Measured: it is CI-only
  (301 pods, all `tekton-ci`), and the pinned node `talos-xr6-r7p` runs cert-manager, the
  Flux image controllers, external-dns and real apps at default priority. Preemption of
  devrc gates is the system working as designed. **Do not raise it.**
- 🔴 **Two tiers disagree in BOTH directions.** #721's hermetic tier was red while dev-host
  passed; #801's dev-host was red while hermetic passed. Read both, always.
- 🔴 **The pipe trap, three ways this session:** `nix build … | tail; echo $?` printed
  `NIX_BUILD_EXIT=0` directly under `RESULT: FAIL`; a wrapper's `…; echo "EXIT=$?"` always
  reports 0; and a `grep … | head -30` truncated a caller list, hiding a test and turning a
  gate round red. Capture status directly; read the `RESULT:` line's content.
- **Every audit finding across three PRs was a claim wider than the code**, never a logic
  bug: a comment calling `GIT_OBJECT_DIRECTORY` harmless when it wrote client content into a
  foreign object store (the test's fingerprint was blind to `objects/`); a docstring naming
  an `& 0xFF` mask no test pinned; a ledger guard that `INSERT OR REPLACE INTO` walked past
  because it substring-matched `INSERT INTO`; a stated 30s bound that measured 40s; and a
  comment promising `Restart=always` when `server.py` actually degrades to an in-memory
  store serving sticky 503s.
- **#349 was filed as a flaky test and was wrong.** Its own verifier showed rows genuinely
  LOST (125 then 25 of 150). The test was right; the `Store` was wrong. Implementing the
  card as filed would have deleted the coverage that found it.
- **Dead end:** reproducing the SQLite flake with CPU load — 0/6 with 20 burners on 24
  cores, 0/6 unloaded. In WAL mode a 10s lock wait means **I/O** stall. The mechanism was
  established with a `busy_timeout` sweep instead.
- **Dead end:** running two pytest roots in one invocation (`dl-router` + `browser-bridge`)
  → 8 collection errors from colliding module names. The runner runs targets separately.
  My instrument, not the tree.
- ⚠ The base clone carries other sessions' uncommitted WIP; it blocked an ff-merge once.
  **Hash a dirty file against recent commits before treating it as WIP** — both were
  genuine, neither a stale orphan. Never `git stash` here.

## How to verify
```bash
# the three fixes are present on main (content, not ancestry — squashes are never ancestors)
git -C ~/workspace/devrc show origin/main:scripts/analyze-service-index/commit.sh | grep -c DEVRC_GIT_REPO_POINTERS
git -C ~/workspace/devrc show origin/main:scripts/analyze-service-index/backup.py   | grep -c strip_repo_pointers
git -C ~/workspace/devrc show origin/main:scripts/dl-router/store.py                | grep -c _retry_busy

# is dl-router LIVE? (runs from /nix/store — a pull does NOT deploy it)
p=$(systemctl --user show dl-router.service -p ExecStart | grep -oE '/[^ ;]+server\.py'); readlink -f "$p"
grep -c _retry_busy "$(dirname "$(readlink -f "$p")")/store.py"   # want: non-zero

# the pointer family is contained (want: HEAD unmoved, 0 foreign objects, scope commits to itself)
# build a decoy repo + worktree, export each of GIT_DIR/GIT_OBJECT_DIRECTORY/GIT_INDEX_FILE/
# GIT_COMMON_DIR/GIT_CEILING_DIRECTORIES/GIT_TEMPLATE_DIR/GIT_CONFIG in turn, run commit.sh

# both tiers — read RESULT:, never an exit code, and never through a pipe
nix develop <wt> --command bash <wt>/scripts/run-tests.sh <wt> --set all
nix build <wt>#checks.x86_64-linux.pytests ; nix build <wt>#checks.x86_64-linux.nodetests
```
