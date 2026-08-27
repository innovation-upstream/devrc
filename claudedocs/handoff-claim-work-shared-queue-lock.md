---
clawgate-task: 357
---
# Handoff: claim-work-shared-queue-lock — 2026-08-27

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

Close clawgate task **357**: the handoff `## Next steps (ranked)` list is a work queue
with no claim step, so two sessions draw the same item and build it twice. Four measured
instances; one cost an entire PR (homelab-infra #388, closed after two adversarial audit
rounds). The prose fix (`IN FLIGHT` marker, devrc#793) was live **6 minutes** before the
next collision, which is why a structural mechanism was required.

## State now

- **Branch / PR:** nothing in flight. All work merged to `main` and shipped to both hosts.
  - **#847** `200e6383` — the mechanism (`scripts/claim-work.sh`), merged + shipped.
  - **#881** `2770db2d` — rounds 2-5, the audit-driven repairs, merged + shipped.
  - **#934** `29394102` — round 6, the removed-worktree recovery, merged + shipped.
- **Task 357 is `complete`** — operator signed off criteria 2 (first-mover) and 3
  (cross-runtime) on 2026-08-27; the card's own Verifier reserved those two for a human
  read, so the sign-off is recorded in a comment rather than self-certified.
- **Deployed and verified against the DEPLOYED copy**, not a worktree: both hosts carry
  the fix (7 `clone-id` mentions each). Owning worktree live → sibling `--release` rc 10;
  after `git worktree remove` → `--check` still rc 10 (the verdict did NOT widen) and
  clone-root `--release` rc 0 (recovery fires).
- **In real use by other sessions.** The live claim-ref count has moved 3 → 2 → 6 → 11 → 8
  across this session as other sessions claimed and released. Do not trust any count
  written down; re-derive it.
- **Three follow-up cards filed and open:** **381** (a hostile `init.templateDir` can still
  rename the ownership trailer), **382** (nothing prunes `refs/heads/claim/*`), **383** (the
  lock is an exact slug match and `--slug-for` is already being bypassed).

## What the mechanism is, in three sentences

`claim-work <slug>` publishes an **orphan commit** to `refs/heads/claim/<slug>` on one
**canonical** remote (resolved from the script's own realpath, never the caller's cwd).
Because each claim is an unrelated root, a second push to a claimed ref is refused by the
receiving git's **ref-transaction compare-and-swap** on `must-not-exist` — not a
check-then-act, so there is no TOCTOU window and the **first mover** is covered by
construction. It **fails open**: no origin/network/auth ⇒ loud stderr, exit 0, degrade to
today's behaviour, so it can never block a `/resume`.

## Open investigations — live diagnosis state

### Legacy `cwd:` refs let one host read the other's claim as its own

- **Symptom + exact repro:** on the laptop, `cd ~/workspace/devrc && claim-work --check
  devrc-nix-read-path-dirt` returned **rc 12 "✅ THIS IS YOURS — carry on"** for a claim
  made on the workbench. Unsafe direction: a resuming session is told to proceed with a
  peer's live item.
- **Observed (with values):** `uname -n` is `nixos` on **both** hosts, and
  `/home/zach/workspace/devrc` exists on both, so the legacy tier's host+cwd comparison
  matches across machines. New-format refs are correct — same probe on a `owner-id:`-format
  claim returned **rc 10** from the laptop.
- **Ruled out:** not a defect in the new token. `/etc/machine-id` genuinely differs
  (`d48f5d71…` workbench / `8d9fd8d4…` laptop) and separates the hosts for every ref
  created after 2026-08-26.
- **Leading hypothesis:** this is the documented transitional residual, not a regression.
  Narrowing the legacy tier would make already-published refs unreleasable by anyone,
  which is worse.
- **Next probe:** none needed — it self-heals as legacy refs are released or hit the 7-day
  TTL. To confirm it has cleared:
  ```bash
  for s in $(git -C /home/zach/workspace/devrc ls-remote --heads origin 'refs/heads/claim/*' | sed 's|.*refs/heads/claim/||'); do
    printf '%s ' "$s"; claim-work --check "$s" >/dev/null 2>&1; echo "rc=$?"
  done
  ```
  Every ref answering with a `clone-id:`/`owner-id:` body means the residual is gone.

### F4 is closed going FORWARD only

- **Symptom + exact repro:** a claim made in a worktree that is later `git worktree
  remove`d used to be unreleasable by anyone without `--force` — rc 10 from the clone root
  and from every sibling, stuck for the full TTL, with the refusal calling the owner's own
  lock somebody else's.
- **Observed (with values):** fixed in #934 via a published `clone-id:` trailer plus a
  registered-worktree scan. But **10 of the 11 live refs at merge time predate it** and
  carry no `clone-id:`, so they still need `--force`, the TTL, or recreating the worktree.
- **Ruled out:** a retroactive migration — it would rewrite other sessions' in-flight
  claims.
- **Leading hypothesis:** for roughly a week the fix will look like it is not working. It is.
- **Next probe:** `claim-work --check <slug>`; a pre-round-6 body has no `clone-id:` line.

## Next steps (ranked)

1. **Watch for adoption drift on `--slug-for`** — repo `devrc`, card **383**. Measured:
   within a day of deploy, every live claim used a hand-authored slug with **no rank
   suffix**, i.e. the canonical derivation the design calls "the crux" is being bypassed.
   That is exactly the failure the exact-match lock cannot detect. Re-derive before acting:
   `git -C /home/zach/workspace/devrc ls-remote --heads origin 'refs/heads/claim/*'`.
2. **Give claim refs a cleanup story** — repo `devrc`, card **382**, touches
   `scripts/claim-work.sh`. Nothing prunes `refs/heads/claim/*`; a stale claim reports rc 11
   but the ref lives forever. 🔴 Non-goal: a destructive timer — clawgate retired that exact
   shape in 0.7.96.
3. **Close the `init.templateDir` trailer hole** — repo `devrc`, card **381**, touches
   `scripts/claim-work.sh`. The fix is known and measured (`git init --bare --template=`);
   it was left out of round 5 only because it changes how the scratch repo is created.
4. **Add the two 🟢 coverage rows the final audit named** — repo `devrc`, touches
   `scripts/claim-work.sh` + `scripts/tests/mutants-claim-work.sh`. Three fail-closed belts
   in the recovery function survive mutation with no `SURVIVES_BY_DESIGN` row, and there is
   a new cross-host residual in the machine-id-absent degrade (both hosts fall back to
   `uname -n` = `nixos` and share a devrc path, so clone-ids would collide). Not live.

🔴 **This list is a WORK QUEUE, and `claim-work` is its LOCK** — every
`/resume` session draws from it, so a *better* ranked list produces *more*
duplicate work, not less. **NUMBER the items and keep the numbering stable:
the rank is half a claim's identity** (`claim-work --slug-for <this doc>
<rank>`), and re-ranking silently re-points every live claim. Make each item
cheap to check — name the repo and the files it will touch, and mark anything
in flight `IN FLIGHT: <repo>#<pr>`; that marker is the SOFT half, the lock is
the command `/resume` step 6 runs before touching an item. Worktrees do NOT
prevent this. 📖 `~/.claude/skills/handoff/reference/shared-queue.md`.

## Gotchas / decisions / dead-ends

- 🔴 **Six fix rounds, five blind audits, a finding in every round but the last — and the
  gate was green the entire time.** Every defect needed someone to run the failing path. If
  you touch this file, budget for the same.
- 🔴 **Three of the six regressions were the same mistake: widening an ownership predicate
  in a way that leaked into the claim/check verdict.** `claim_is_mine` has exactly two
  callers — `report_existing` (the claim/`--check` verdict) and `require_ownership_or_force`
  (the destructive verbs). **Only the second may ever become more permissive.** Verify the
  `--check` cell explicitly before believing a change is safe.
- 🔴 **Blind audits found what framed ones would have confirmed away.** Handing an auditor
  the prior conclusions produced agreement; withholding them produced findings. The
  cross-repo 🔴 — the mechanism being inert in the exact shape of the incident that
  motivated it — was found by the first blind pass.
- **Worktree isolation is REFUTED as a fix and must not be re-proposed.** Every colliding
  session was already in its own worktree; this is a task-allocation collision and
  isolation is what *hides* it.
- **A pre-flight `gh pr list` check alone cannot work** — whoever moves first cannot see
  the second session. That sweep is still in `/resume` step 6 on the **unconditional** path,
  because it is the only thing that sees a duplicate that was never claimed.
- **Deploy note:** `claim-work` is an out-of-store symlink (`nix/home.nix`
  `mkOutOfStoreSymlink`), so it goes live on a plain `git pull` — no `home-manager switch`
  needed. But **both hosts must pull**, or one writes `clone-id` claims and the other does
  not. `ship.sh` covers both.
- **Ownership is a discriminator, not a signature.** A hand-crafted claim commit can name
  any `owner-id`, and `--force` bypasses the gate by design. Documented, not a defect.
- ⚠ **The workbench working tree was dirty in a way that reached the artifact** at ship
  time: `scripts/discord-embed-ext/extension/embed_enlarge.js` is tracked, modified, and read
  by nix at build time, so that host's generation is `origin/main` **plus** it. Not this
  session's change. The two hosts agree on `main` but not on what they built.

## How to verify

```bash
# 1. the mechanism refuses a sibling worktree and recovers a removed one
#    (run from a throwaway clone + linked worktrees against a LOCAL bare repo —
#     never write probe refs to the public canonical origin)
export DEVRC_CLAIM_REMOTE=/tmp/probe-origin.git

# 2. the suite
nix develop /home/zach/workspace/devrc --command \
  python3 -m pytest /home/zach/workspace/devrc/scripts/tests/test_claim_work.py -q
#   expect: 106 passed

# 3. the mutation battery, both controls in one run
bash /home/zach/workspace/devrc/scripts/tests/mutants-claim-work.sh
#   expect: ALL OK (40 rows), baseline clean, comment-only-edit SURVIVED as required

# 4. both gate tiers — the sandbox tier is the one Tekton gates on, and it builds
#    from a cp -r store copy with NO .git, so run BOTH
nix develop /home/zach/workspace/devrc --command \
  bash /home/zach/workspace/devrc/scripts/gate.sh --tier both --set hermetic
nix build /home/zach/workspace/devrc#checks.x86_64-linux.pytests --no-link
nix build /home/zach/workspace/devrc#checks.x86_64-linux.nodetests --no-link
```
🔴 `$?` after a pipe is the LAST command's status — capture `out=$(cmd); rc=$?`. Misreading
rc 10 as rc 0 happened twice in the session that wrote this.
