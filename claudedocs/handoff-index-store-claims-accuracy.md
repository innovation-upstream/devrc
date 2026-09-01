# Handoff: index-store claims accuracy — 2026-09-01

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
The `/analyze-service` index store's own documentation contradicted the store's reality in
three independent ways. Correct each, and leave a guard behind so the class cannot drift
back silently.

⚠ No `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** — 0 tasks for this
session, with its positive control confirming the board was reachable. A wrong session id
answers 200/empty exactly like a session that touched nothing, so this is **not** a clean
reading, and per the protocol no field was written and no task was created.

## State now

**DONE — merged and deployed:**
- **devrc#1132 → `f9c86a8b`** (squash). The store's docs claimed **no off-machine backup**
  in what turned out to be **34 sites**, nine days after `analyze-service-index-backup.service`
  started running daily. Shipped to both hosts (`ship.sh`, both at `f9c86a8b`), and verified
  at the CONSUMER, not the rollout: `readlink -f ~/.claude/skills/analyze-service/reference/index-store.md`
  resolves into `/nix/store/…-devrc-claude-skills/`, corrected claim present on workbench and
  laptop, stale claim 0 occurrences.
- **New guard `scripts/tests/test_index_store_backup_claim.py`** — pins the doc claim against
  the nix wiring (`A == B`), not against a phrase. Plus `scripts/testlib/nix_units.py` and
  `scripts/tests/test_nix_units.py` (the shared nix-source reader had **no tests at all**).
- **devrc#1170 → `50bfd91f`** (operator-merged) and **devrc#1186** closed the two-protocol fork.

**IN FLIGHT:**
- **devrc#1215** `fix/stale-confirm-gated` — both proposal headers in `subsystem_touch.py`
  still printed `confirm-gated` after the y/N was retired twice. Guard added on the RENDERED
  OUTPUT. Red-at-base `76bb7507` confirmed, 1553 tests green on the affected suites.
  **Full `gate.sh` and the sandbox tier NOT yet run on that branch.**

## Open investigations — live diagnosis state

### Why devrc-ci went red on PRs whose diff could not reach the failing suite
- **Symptom + exact repro:** `tekton/devrc-pytests` FAILURE on `228b8cea` and `8f1d4531`
  while the same shas were green locally on both tiers.
- **Observed (with values):** run 1 `devrc-ci-5hsmf` — `pytests` exit 0, `nodetests` exit 0,
  `verdict` exit 1, `failed=1` of 19958, in `scripts/tests/test_subsystem_store_api.py`.
  Run 2 `devrc-ci-tfrr6` — `failed=3` in `scripts/browser-bridge/tests/test_browser_agent.py`,
  message: *"the wrapper did not exit within 60s. Spawning 10 trivial processes on this machine
  just now took 0.16s (idle reference 0.10s; stall threshold 0.80s), so the MACHINE is not the
  explanation."* Four other runs on four non-mine shas failed on different tests in the
  store-api file. Locally: 641 passed × 3 standalone, sandbox tier `failed=0`.
- **Ruled out:** CPU/node load — the failing test's OWN control measured process-spawn latency
  at failure time and found the node healthy. via: measurement
- **Ruled out:** a defect in devrc#1132 — the failing test and the server it exercises are
  byte-identical between `3f9c8144` (CI green) and `228b8cea` (CI red). via: command
- **Ruled out (RETRACTED, mine):** "concurrent unsandboxed nix builds share /tmp and the network
  namespace". The unsandboxed observation is REAL (`/build` absent in a live gate pod while
  `nix config show` reports `sandbox = true`) but it was NOT the mechanism for either failure.
  via: doc
- **Leading hypothesis — now RESOLVED by others, and both causes were specific, not systemic:**
  the store-api failures were `_replace_bytes` fsyncing inside the request, exceeding
  `HANG_TIMEOUT` under disk contention — **devrc#1211 (`1a4350f3`)** moved the test store off the
  contended disk. The browser-bridge failures were three flat `elapsed < 1.0` bounds against a
  **5.0s** timeout, i.e. load detectors rather than timeout detectors — **devrc#1179** derives
  each as `TIMEOUT / 2`. With both on `main`, `7d3b6d2a` went green first try.
- **Next probe:** none needed for this thread. If it recurs, read the `verdict` step first —
  `pytests exit 0` + `verdict exit 1` means a test FAILED; a step that emitted no `RESULT:` line
  was KILLED, which is a different problem.

## Next steps (ranked)
1. **Run both gate tiers on `fix/stale-confirm-gated` and merge devrc#1215.** `gate.sh` and
   `nix build .#checks.x86_64-linux.{pytests,nodetests}` one at a time, clean tree asserted.
   forcing: gate
2. **`/audit-pr 1215`** before merging it — never run on that branch.
   forcing: none
3. **Verify devrc#1170's 🟡 5 and 🟡 6.** Its blind audit reported seven findings; 🔴 1, 🔴 2,
   🟡 3, 🟡 4 and 🟡 7 are dispositioned (see Gotchas). Five and six were never checked.
   forcing: none
4. **Measure whether anything reads the hosted store before automating its seed.**
   `store.zacx.dev` snapshot lags the source (seeded 2026-08-29, 132 entry-files vs 143 local)
   and `seed.sh` has no timer. `cairn` prints its own freshness banner, so it degrades honestly;
   its docstring records *309 requests, all from the session that built it*. Measure request
   volume first — a seed timer with no consumer is harness.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **The sweep needed THREE widenings and each read as complete.** `no off-machine backup` → 12;
  `unbacked-up` → 19 more, **10 in files the first pass had already edited** (incl. a section
  HEADING 32 lines below a bullet it had just corrected, and a live `RuntimeError` string);
  `nothing leaves the machine` / `only copy` → 3 more, one a CONFIDENTIALITY claim false in the
  OPPOSITE direction. **4 sites straddled a newline**, invisible to line-based `git grep` — sweep
  on a whitespace-normalised multi-line window.
- 🔴 **"only copy" is NOT in the class.** A bullet's content really is its only copy; 14 such
  sites are correct. #1170's audit called three of them contradictions and was wrong — checked
  individually rather than actioned.
- 🔴 **A prose guard mutates faster than the prose — 7 audit rounds, each fix opening the next
  hole.** tokens → meaning-reversed section passes; whole normalised string → quoted retraction
  passes; delete-the-retraction-line → WEDGED marker passes; require a `-` list item → broke the
  file's own purpose (the failure message hands over a PARAGRAPH to paste) and silently defanged
  three sibling assertions. **What survived:** match blocks, disqualify one containing a marker,
  and PIN the residual in a test that fails if a listed shape becomes caught.
- 🔴 **Two of my own fixtures were vacuous**, both caught by positive controls: one ITERATED the
  tuple it was testing (so a dropped element dropped its own case); one built a mutant by
  replacing comment-stripped text inside raw source, so `str.replace` matched nothing and a
  byte-identical "mutant" scored SURVIVED.
- 🔴 **A live probe against a DIRTY tree is evidence about no commit.** Hit TWICE: a sandbox build
  launched clean then waited 795s while fixes were edited into the same worktree; then the same
  again on the dev-host tier, because the guard written for the first was never applied to the
  second. Both tiers now run from one script asserting a clean tree at start, after the wait, and
  re-reading HEAD at the end.
- **`gh pr checks` rolled up a verdict that did not belong to the head sha** and reported `fail`
  while `/repos/…/commits/<sha>/status` said `pending`. Use the per-sha status API.
- **prune-index deliberately keeps its y/N** — a cut is a DELETION; the evidence that retired the
  append prompt was measured on an APPEND. Six mentions there are accurate and must stay.
- **The ladder stopped on the payload-attribution gate, not on a clean round** — rounds 6 and 7
  both changed zero payload lines.

## How to verify
```bash
# the correction is in main AND live at the consumer, both hosts
git -C ~/workspace/devrc show origin/main:claude/skills/analyze-service/reference/index-store.md \
  | grep -c 'bundles every scope'                      # 1
readlink -f ~/.claude/skills/analyze-service/reference/index-store.md   # -> /nix/store/...
grep -c 'no off-machine backup\. Inside any scope' ~/.claude/skills/analyze-service/reference/index-store.md  # 0

# the guard is real: red at base, green at head
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_index_store_backup_claim.py \
  ~/workspace/devrc/scripts/tests/test_nix_units.py -q
```
