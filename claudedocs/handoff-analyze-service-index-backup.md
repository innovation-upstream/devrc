# Handoff: analyze-service index — A/B evaluation, prune surface, offsite backup — 2026-08-22

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
Evaluate whether the `/analyze-service` index store earns its upkeep, act on the findings,
and close the one failure mode in it that is unrecoverable (no off-machine backup).

## State now

**Merged and shipped to both hosts:**
- `#637` — every entry says what it is; **no briefing path printed it** (`--search` always did).
- `#650` — two surviving mutants + a false claim; also re-anchored `test_the_recall_step_comes_AFTER_the_handoff_is_read`, which had left `main` RED since #643.
- `#653` — a `<<<<<<< HEAD` marker that lived in `main` **inside a docstring** (450 tests passed with it present), plus `scripts/tests/test_no_conflict_markers.py` to stop a recurrence.
- `#668` — `prune-index` skill + `scripts/subsystem-audit.py` + the `write-back.md` lifecycle fix.

**In flight:**
- `#703` — encrypted offsite backup. **A full gate is running right now** on the merged tree in a standalone clone at `/tmp/claude-1000/-home-zach-workspace-devrc/4eaccec0-.../scratchpad/gate703`, output to `.../scratchpad/gate703.out`. Merge if `GATE: RESULT=PASS`.
- `#681` — `skill-audit.py` headroom blindness. Ready, never gated. **Still needed**: `main` has 0 headroom-aware lines, so `prune-skill` runs still get `✓ no prune needed` about files the gate rejects.
- `#673` — superseded, deliberately unmerged, kept as the record. Carries **four** correction comments.

**Deploy status:** #637/#650/#653/#668 merged AND shipped (`ship.sh`, both hosts, verified by content). #703/#681 not merged, not deployed.

## The A/B result — what the index is actually worth

Controlled A/B on `datapacket-talos/storage-resolver`, pre-registered 10-question answer key, both arms on a clean `origin/trunk` worktree.

- **~90% of the entry is recoverable from the repo itself.** Sampled 10 load-bearing facts; only `kubectl wait -l app=storage-resolver` hangs was index-only.
- The control arm **matched or beat** the index arm on 6 of 10 questions.
- **The index's one clean win was recency-ordered SELECTION**: the control confidently asserted the pre-2026-08-20 auth control (`401`) because it read the 08-19 docs and stopped. The correction (`403 SignatureDoesNotMatch`) is in the repo — in two docs dated 08-20 it never opened.
- Cost: 25 vs 26 tool calls, 123k vs 148k tokens (~17% saved). Not a step change.
- 🔴 **n=1.** One subsystem in an unusually doc-rich repo. A second A/B against a doc-poor repo is the outstanding measurement.

**My own answer key was wrong in two places** (one image-pin site vs three; it repeated the entry's incomplete `CLEANED=` advice). The artifact under test corrupted the instrument measuring it.

## Open investigations — live diagnosis state

### The fixture-wipe incident — diagnosed, repaired, NOT closed
- **Symptom:** `origin/main` tree became a single file `f`; 63 fixture commits pushed over it in a 26-second burst; the operator's base clone got `core.bare=true`, `core.hooksPath` re-armed, `main`→`trunk` renamed, identity rewritten to `T <t@example.com>`.
- **Observed (mechanism, reproduced by me on git 2.55.0):**
  ```
  GIT_DIR=<victim>/.git git -C <innocent> branch -m PWNED
    innocent: unchanged        victim: branch -> PWNED
  ```
  **`GIT_DIR` silently overrides an explicit `git -C`.** Every fixture in the tree binds `-C` or `cwd=` correctly — audited mechanically, twice — and that property confers no safety.
- **Second half:** `git rev-parse --git-common-dir` from a **linked worktree** resolves to the real clone's `.git`. **A worktree is not containment.** Every session that "isolated" itself in a worktree was writing to the operator's clone.
- **Ruled out:** `-C` discipline (audited clean, irrelevant); a repo-local `core.hooksPath` as the cause (it was `.git/hooks`, sample-only, benign).
- **Repaired by me, all five fields measured together:** `core.bare` unset, `core.hooksPath` unset (local+global), `remote.origin.url` intact, identity restored to `Zach Lowden <zachlowden1@gmail.com>` (evidence: `86405705`, a `commit:` entry in this clone's OWN reflog), HEAD reattached. Evidence preserved at `scratchpad/incident-evidence/`.
- **Still open:** sessions keep starting tier runs in trees that share the base clone. HEAD has been re-detached since. `main` is periodically pinned by other sessions' worktrees.
- **Next probe:** `for p in $(pgrep -f 'run-tests.sh|gate.sh'); do git -C "$(readlink /proc/$p/cwd)" rev-parse --path-format=absolute --git-common-dir; done` — anything equal to `~/workspace/devrc/.git` is the hazard.

### The index store has no backup — the reason #703 exists
- **Observed:** 10 scopes, all git repos, **`remote = none` on every one**, no off-machine copy, `ship.sh` rsyncs only `~/.claude/skills/`. The laptop's store is divergent content, not a backup.
- **Consequence:** local git history covers an agent clobbering a file and nothing else. Disk failure or `rm -rf` of the store root loses it permanently, and the content is **not re-derivable**.
- 🔴 **Unresolved and NOT a code problem: the age key has no escrow.** `~/workspace/homelab-talos/.secrets/age.key` is gitignored and untracked; it exists only on the two machines being backed up. Lose the disk and you lose the store *and* the key. Encryption is what makes the backup safe to store off-box and simultaneously a single point of failure. **Operator decision: where a second copy of that key lives.**

## Next steps (ranked)
1. **Read `scratchpad/gate703.out`.** If `GATE: RESULT=PASS exit=0` → merge #703, then `ship.sh`. If not, read the log; do not merge on a summary line.
2. **Gate and merge #681** the same way — standalone clone, merged tree.
3. **Decide the age-key escrow.** #703 is not really done until a second copy of that key exists somewhere the disk failing doesn't reach.
4. **Run `prune-index` against the store.** Built, shipped, validated on synthetic and live input, and **never used for its purpose**. Latest verdict: 5 entries over the 12,288 B hard cap, 11 over target, 32 evictable `RESOLVED`, 1 `NO HOME`, 22 broken pointers, 5 scopes with no README, 31 OPEN bullets protected.
5. Second A/B against a doc-poor repo (tests whether "selection, not knowledge" generalises past n=1).
6. Backlog: the 08-19 "two defects" bullet needs a durable record before it can be pruned; `created_by` is 48 `handoff` : 3 `analyze-service` and `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` is stale against it.

## Gotchas / decisions / dead-ends
- 🔴 **`git bundle verify` does NOT detect corruption.** Measured: one byte flipped mid-packfile → `rc=0`, *"The bundle records a complete history."* A clone of the same bundle dies `index-pack died`. **Verify a bundle by restoring from it**, never by asking git whether it would work. I specified `verify` in the #703 brief; it was wrong.
- 🔴 **`ProtectHome = "read-only"` makes `$HOME` READABLE**, not inaccessible. #703 shipped it on a *networked* unit whose comment claimed `.secrets/` was hidden. Fixed to `tmpfs`; measured: read-only → `~/.ssh/id_ed25519` and `~/.kube/config` READABLE; tmpfs → ABSENT.
- **Isolation means a standalone clone with `origin` removed** — not a worktree. This is the single most useful sentence from the whole incident.
- **The "freeze"** on running the tier was an inter-session agreement, not an operator instruction. It is over-broad: the hazard is *where* you run, not *whether*. Standalone clones are provably safe and were used all session with zero damage.
- **Three of my own controls were vacuous this session** and I caught each only by checking the input: a `GIT_ALLOW_PROTOCOL` control against an unreachable host (could only ever fail); a `git init` default-branch fixture already at the asserted end state; a `git show | grep` returning 0 because the pipeline was empty, not the file.
- **`skill-audit.py` says `✓ no prune needed` for files the gate rejects** — it checks the hard ceiling and is blind to the 250 B headroom floor. #681 fixes it; until that merges, do not trust its ✓.
- Peer sessions carry the guard work: `#689`/`#676`/`#683`. My `nogit_plugin` (#673) was open on `GIT_WORK_TREE`, `GIT_CONFIG_COUNT`, and its own claim was wider than its reach. Superseded on purpose.

## How to verify
```bash
# the backup round-trip, end to end (what #703 actually promises)
nix-shell -p age --run 'age -d -i ~/workspace/homelab-talos/.secrets/age.key -o /tmp/rt.bundle <artifact>.age'
git clone /tmp/rt.bundle /tmp/rt && git -C /tmp/rt rev-list --all | sort | sha256sum   # must equal the source

# the index store's own verdict
python3 ~/workspace/devrc/scripts/subsystem-audit.py

# nobody is running the tier against the real clone
for p in $(pgrep -f 'run-tests.sh|gate.sh'); do
  git -C "$(readlink /proc/$p/cwd)" rev-parse --path-format=absolute --git-common-dir
done   # none may equal ~/workspace/devrc/.git
```
