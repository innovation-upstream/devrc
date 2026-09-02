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

**DONE — merged, deployed and verified at the consumer:**
- **devrc#1215 → `b59b0475`** (rank 1 of the previous list). Both gate tiers run on the
  MERGED tree, proven with `git merge-tree --write-tree` = the gated tree's own OID; the
  branch had been **6 commits behind main**, so gating it as-shipped would not have been a
  claim about the merge. Verified by content on `origin/main`.
- **devrc#1223 → `540e748d`** — the `dropped lines:` advisory in `--validate`. Verified by
  content AND behaviour: run against the real 2026-08-19 blob it reports 13 dropped lines
  and flags nuance line 11 as a lost declaration.
- **`ship.sh` rc 0, both hosts at `a6e5064`.** Verified at the CONSUMER, not the rollout:
  the new `dropped lines:` paragraph resolves live in the nix-store copy on the workbench
  AND the laptop, and the laptop carries the validator code. ⚠ ship.sh independently
  reported the operator's uncommitted `nix/programs/alacritty/default.nix` as baked into
  the generation it built, so `save_to_clipboard` is now LIVE; both dirty files were copied
  to a scratchpad first and nothing was reverted.

**IN FLIGHT:**
- **devrc#1232** `docs/ci-claim-protection-disabled` — CLAUDE.md asserted `main` was gated
  by both tiers with `enforce_admins: true`; it is protected in NAME ONLY. Green on both
  tiers (20,474 pytest / 1,449 node, 0 failed, 0 timeout panics) — but **that run is now 2
  commits behind main, so it no longer covers the merge result.** Re-gate before merging.

**NOT DONE, by explicit operator decision (not oversight):**
- `/audit-pr 1215` — skipped.
- Audit round 3 on #1223 — skipped, though round 2 returned five real findings, so the
  ladder ended by decision rather than on a clean round.
- The local-store freeze bypass — left alone; the freeze is temporary.
- `main` stays unprotected until the Tekton capacity work lands (another session owns it).

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

### Entries are still being WRITTEN to the local mirror while the pod is canonical
- **Symptom + exact repro:** post-Cairn-cutover the pod is the authority and every skill
  routes writes through `cairn append`/`cairn put`, yet the local mirror keeps changing.
  `find ~/.claude/analyze-service-index -name '*.md' ! -name README.md -newermt '-1 day'`
- **Observed (with values):** `~/.claude/analyze-service-index/devrc/tests.md` — mode
  `-r--r--r--`, mtime **2026-09-02 10:39:27**, carrying a new `- 2026-09-02:` bullet;
  autocommitted at **2026-09-02T11:04:10 `e2f21cf`**; working copy == HEAD. The
  `analyze-service-index-commit.timer` is **active** (ran 10:07, next 11:04). All **16 of
  16** scopes are still git repos with commits through `2026-09-02T03:01`. Entry-file mode
  census: **141/141 at 0444** — the single 0644 `.md` in the tree is the store-root
  `README.md`, not an entry.
- **Ruled out:** "the local mirror is frozen / inert / no longer a git repo" — the store
  ROOT has no `.git`, but all 16 scopes do, the commit timer is live, and content changed
  today.
  via: measurement
- **Ruled out:** "the 0444 freeze prevents local writes" — a file at 0444 gained a bullet
  today and is still 0444.
  via: measurement
- **Ruled out:** "it is the naive temp-file-and-rename bypass" — measured in a replica
  (0755 dir, 0444 file): rename succeeds and leaves the file **0644**. The live file is
  still 0444, so whatever wrote it preserves or restores the mode.
  via: measurement
- **Leading hypothesis:** a local writer that handles the mode deliberately — either it
  chmods around the freeze, or something syncs pod→local. Not yet identified. The
  consequence is the part that matters: two authorities are accumulating divergent content,
  which is a stronger reason not to run `seed.sh` than the staleness the previous doc
  assumed.
- **Next probe:** identify the writer, not the mechanism:
  `git -C ~/.claude/analyze-service-index/devrc show e2f21cf -- tests.md` for what landed,
  then `inotifywait -m -e close_write,moved_to ~/.claude/analyze-service-index/devrc/`
  across one write to catch the process.

## Next steps (ranked)

1. **Resolve the local-vs-pod divergence** (`~/.claude/analyze-service-index/` vs the
   `store.zacx.dev` pod). It is the only item actively accumulating: 48 entries exist only
   on the pod, 1 only locally, 153 in both, and local writes are still landing (see the
   open investigation). Decide which side is authoritative in practice, then make the other
   one stop or reconcile.
   forcing: regression — the Cairn cutover reversed the direction of truth and local
   writes did not stop; content is diverging now, unattended.

2. **Re-gate and merge devrc#1232** (`docs/ci-claim-protection-disabled`, one file:
   `CLAUDE.md`). It was green on both tiers but is now 2 commits behind main, so re-run
   `scripts/gate.sh --tier both` and the two sandbox derivations one at a time on the
   merged tree first.
   forcing: regression — until it merges, an always-loaded file tells every session the
   merge is gated by both Tekton checks when nothing gates it at all.

3. **Hard-guard `scripts/subsystem-store-api/seed.sh`** (devrc). Its header still opens
   `🔴 THE LOCAL STORE IS AUTHORITATIVE` (line 4) and its extract "adds and overwrites but
   never deletes" (lines 322-323), so running it today reverts live pod content. Measured
   blast radius: 28 cairn-attributed bullets on the pod vs 6 locally — **22 bullets written
   by real sessions destroyed**, 13,623 bytes across the 153 shared entries. Fix is an
   inversion of the `comm -23` containment set it already computes, plus the header.
   forcing: regression — the cutover inverted the authority and the script was never
   updated; a one-off footgun becomes a recurring data-loss job if anyone automates it.

4. **Fix devrc#1170's 🟡 5 and 🟡 6** (`scripts/lib/subsystem_touch.py`,
   `claude/skills/subsystem-index/SKILL.md`, `claude/skills/analyze-service/reference/index-store.md`).
   🟡5: `SKILL.md:148` says read the policy the probe named on its `policy:` line, but the
   `/analyze-service` door reaches the write half via `service_recon.py` + `--template`,
   **neither of which emits one** (verified: 0 occurrences in both) — so that caller is told
   to read a policy nobody named and forbidden from looking, while
   `index-store.md:55` tells it to read the scope README. 🟡6: `--template` over an EXISTING
   entry prints the first-ever-file template and **exits 0 with no warning** (reproduced);
   `SKILL.md` then says to write it and "run no git command", so an `OPEN:` bullet is
   destroyed silently. Fixes: emit `policy:` from the existing `governing_policy()`; add a
   create mode using `os.open(..., O_CREAT|O_EXCL, 0o444)`.
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

- 🔴 **THE PREVIOUS DOC'S PREMISE INVERTED — carried forward here because the ranked item
  that held it was replaced.** It recorded: *"`store.zacx.dev` snapshot lags the source
  (seeded 2026-08-29, 132 entry-files vs 143 local)"*. MEASURED 2026-09-02: the pod's
  `.seed-stamp` reads `2026-09-01T20:38:36Z staged_entries=49`, the pod holds **201**
  entries to the local **154**, and **48 exist only on the pod against 1 only locally**.
  The snapshot does not lag the source — **the local mirror lags the pod**, because the
  Cairn cutover made the pod authoritative. Anything reasoning from the old numbers is
  reasoning backwards, which is what made "automate the seed" look sensible.
- 🔴 **Two of this effort's own numbers were README-inclusive and wrong, and the same
  mistake recurred in a subagent's report.** "154 entry files" and "153/153 at 0444" count
  the 13 scope READMEs; `validate_scope` excludes them, so the real figure is **141**, and
  141/141 are 0444. "789 blob versions" was likewise README-inclusive AND a moving number —
  the store commits hourly, and it read 777 entry-file versions / 791 including READMEs when
  re-measured hours later. **Date any count taken from this store and say it moves.**
- 🔴 **A two-dot `git diff A..B` between a branch tip and main lists YOUR OWN changes as
  main's.** It produced a false "both incoming commits touch exactly my two files" and a
  semantic-conflict scare that did not exist. `git log --name-only <tip>..origin/main` is
  the question actually being asked.
- 🔴 **The audit's headline finding was one I could not have reached by re-reading my own
  code**: `carries_marker` was inert on all 7 historical blobs for TWO independent reasons —
  a hand-spelled marker vocabulary AND position-0 anchoring against a mid-line marker.
  Fixing only the first still read 0 on every one of them. Consolidating into
  `subsystem_resolver.line_openness` / `line_mentions_marker` is what made the disagreement
  audible.
- **Re-verify a subagent's numbers, not just its reasoning.** Both dispatched agents were
  substantially right and each carried one wrong datum: a "post-freeze locally-created entry
  at 0644" that is really the store-root README, and "the mirror is no longer a git repo"
  when all 16 scopes are and are still autocommitting.
- **The `--template`-over-existing-file loss needs no race to reproduce.** The audit framed
  🟡6 as a concurrency hazard; the single-writer variant is a two-command demonstration.
- **`_MARKER_ANYWHERE` requires the colon on purpose.** `_NEAR_MISS_MARKER`'s shouted branch
  may skip the terminator because it is ANCHORED at a bullet head; unanchored over a whole
  line that same rule fires on `OPEN SOURCE`.
- **No clawgate task recorded.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session — which cannot distinguish "touched no task" from "wrong session id", so no
  `clawgate-task:` field was written and none was created.
- ⚠ **Environment, unaddressed:** the shared `devrc` clone carries ~150 worktrees from
  finished agent runs, and its working tree holds another session's uncommitted WIP
  (`nix/programs/alacritty/default.nix`, `nix/system/apply-tmp-churn-retention.sh`,
  `output.txt`, two `scripts/diagnose-*.sh`).

## How to verify

```bash
# the two merged fixes are on main AND live at the consumer, both hosts
git -C ~/workspace/devrc show origin/main:scripts/lib/subsystem_touch.py | grep -c 'def scan_dropped_lines'   # 1
grep -c 'dropped lines:' ~/.claude/skills/subsystem-index/SKILL.md                                            # 1
ssh zach@192.168.50.155 'grep -c "dropped lines:" ~/.claude/skills/subsystem-index/SKILL.md'                  # 1

# the advisory fires on the REAL historical corruption, and ranks the lost declaration
git -C ~/.claude/analyze-service-index/datapacket-talos show 409fd27b15 > /tmp/o.md   # into a scope dir
nix develop ~/workspace/devrc -c python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py \
  --store <scratch> --validate <scratch>/datapacket-talos/orchestration.md            # 13 dropped, 1 DECLARATION

# the divergence is still accumulating (rank 1)
find ~/.claude/analyze-service-index -name '*.md' ! -name README.md -newermt '-1 day' -printf '%TF %TR %p\n'
systemctl --user list-timers 'analyze-service-index-commit*' --no-pager
```
