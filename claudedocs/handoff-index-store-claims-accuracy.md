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

**RANK 1 (the `seed.sh` hard-guard) IS IN FLIGHT AS `devrc#1304`, NOT MERGED.**
Branch `fix/seed-refuse-pod-overwrite`, head `d7c4c266`. Two audit rounds done; **round 3
is owed** (round 2 returned findings, so the ladder cannot stop there).

- **What it does.** A PRE-FLIGHT before the tar: for exactly the paths in `$staged_list`, ask
  the pod whether it holds different bytes; refuse **exit 8, pushing nothing**, and name them.
  `--allow-overwrite` proceeds and still prints what it replaced. The header's
  `🔴 THE LOCAL STORE IS AUTHORITATIVE` — false since the cutover — is corrected.
- **Why bytes, not mtime:** two copies cannot be ordered (a local edit not yet pushed and a pod
  edit not yet pulled both read as "different"). Post-cutover the pod is the authority, so ANY
  difference means a derivative would overwrite it. No clock needed.
- **Round 1 (audit) found a 🔴 IN THE GUARD ITSELF.** The pre-flight printed its count only on
  failure and the probe emitted a line only for files the pod HAS — so "the pod holds none of
  them" and "the probe never ran" were the same observation (zero lines, rc 0). Driven
  end-to-end with a silenced probe, the push **destroyed the pod's newer bytes and printed
  `seed: OK`**. Fixed: every path is answered (hash / `ABSENT` / `UNREADABLE`), a short reply is
  exit 9, and the counts print on every path.
- **Round 2 refuted a round-1 claim of mine, in both halves.** `-I{}` does NOT disable xargs's
  input quote parsing (only `-d`/`-0` does), so a quoted path still aborted rc 1 with no
  diagnostic — byte-identical to base. And the space case stopped aborting and started LYING:
  `awk '{print $2" "$1}'` truncated the join key at the first blank, so two BYTE-IDENTICAL
  entries collapsed to one key, the join became a cross-product, and the guard refused with
  `differing=2` naming a path that does not exist. Fixed: `-d '\n'` on both sides, TAB-separated
  key, `join -t <tab>`.
- **Mutation matrix, all re-run rather than asserted:** local loses `-d` → killed; `awk`
  truncates the key → killed; `LC_ALL=C join` → `join` → killed (it SURVIVED all 40 base tests
  before round 1); drop the answered-count gate → killed.

🔴 **NOT VERIFIED, and not claimed:**
- **Neither gate tier has run on `d7c4c266`** — not `scripts/gate.sh --tier both`, not the two
  sandbox derivations, not on the merged tree.
- The full `test_subsystem_store_api.py` was last run green (**723 passed**) at the round-2 fix
  commit; since then only targeted subsets (31 passed) have run.
- **Nothing has touched the LIVE pod.** Every test drives a fake `kubectl` whose `exec` rewrites
  `/data` to a temp dir and runs the command locally — a harness that structurally cannot tell
  "works on the pod" from "works on this host". The round-1 auditor closed that gap with a
  read-only live probe (pod `/bin/sh` = dash, GNU findutils 4.10, the literal command clean over
  211 real entries); a later auditor's worktree guard refused `kubectl exec` entirely, so that
  evidence has not been refreshed.

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

### RESOLVED — "entries are still being WRITTEN to the local mirror while the pod is canonical"
- **Symptom + exact repro:** entries kept changing under `~/.claude/analyze-service-index/`
  after the Cairn cutover made the pod authoritative.
  `find ~/.claude/analyze-service-index -name '*.md' ! -name README.md -newermt '2026-09-01'`
- **Observed (with values):** two writer classes, both Claude Code sessions.
  `Edit` on a `0444` file succeeds and preserves `0444` (reproduced in a scratch replica;
  ctime−mtime 41ms, matching live `devrc/tests.md` 9ms and `civitai/blocks.md` 22ms).
  `Write` of a new entry lands `0644`. Shell `>>` on the same file returns EACCES.
- **Ruled out:** "a writer that chmods around the freeze, or a periodic re-freeze" — every
  entry's ctime equals its mtime to within ~20ms, and nothing chmod'd `tests.md` between its
  10:39 write and the next 13:54 write. The mode is set AT WRITE TIME by the tool, not after.
  via: measurement
- **Ruled out:** "something syncs pod→local" — zero of the recent local writes existed on the
  pod, and the cache `cairn sync` writes is entirely `0644` (201/201), so `0444` is not a pod
  artefact at all. via: measurement
- **Ruled out:** "the local mirror simply lags the pod" — it is BOTH ahead and behind, per
  bullet. See the next block; this is the finding that matters. via: measurement
- **Root cause, and it is PRESCRIBED, not rogue:** `claude/skills/subsystem-index/SKILL.md`
  (the write half) tells a session that a brand-new entry "exists only locally and the pod has
  never seen it", and offers `subsystem_touch.py --validate <path-you-just-wrote>`. Sessions
  are following the rules into the defect. `subsystem_touch.py:461` still has
  `DEFAULT_STORE_ROOT = ~/.claude/analyze-service-index`; devrc#1233 repointed READS only.

### 🔴 The frozen mirror is STALE-BACKWARDS as well as ahead — a wholesale merge destroys pod content
- **Symptom + exact repro:** treating "local has bullets the pod lacks" as one class and
  applying it wholesale reverts pod content. Reproduce by diffing any of the 5 named below
  between `~/.claude/analyze-service-index/<rel>` and `~/.cache/subsystem-store/<rel>`.
- **Observed (with values):** of 25 local-only bullet candidates, **5 were backwards** — the pod
  held the NEWER text and local a pre-freeze remnant. Two are `OPEN:`→`RESOLVED` closures:
  `datapacket-talos/tekton-builds.md` (pod `RESOLVED f7557727c` + ~20 lines of 09-01/09-03
  corrections; local still `OPEN:`) and `homelab-talos/tekton-ci.md` (pod `RESOLVED 841d6fc4 …
  VERIFIED LIVE 2026-09-02`; local `OPEN: … UNVERIFIED`). Also pod-newer:
  `datapacket-talos/claude-pool.md`, `devrc/subsystem-store-api.md`, `devrc/dl-router.md`
  (pod: "UPDATED 2026-09-02 — BOTH CODE BLOCKERS ARE GONE"; local: "Two blockers").
  All five were left alone.
- **Ruled out:** "similarity on the same date identifies the newer side" — it identifies the
  PAIR, never the direction. Only reading both texts does. via: measurement
- **Ruled out:** "local-only means stranded" — 5 of 25 were the opposite. via: measurement
- **Consequence for rank 2:** this is a stronger reason to guard `seed.sh` than staleness was.
  `seed.sh` pushes local→pod and "adds and overwrites but never deletes", so running it today
  reverts every one of these five by construction, silently.
- **Next probe:** none for the diagnosis. Before ANY future local→pod bulk operation, run the
  per-bullet direction check rather than a file-level containment set.

### `cairn-cutover.py` P3 is blocked by the new guard and cannot complete
- **Symptom + exact repro:** `cairn-cutover.py` P3 invokes `bash seed.sh --store <delta_dir> …`
  with **no `--allow-overwrite`** (`cairn-cutover.py:1379-1382`). Its `plan.shippable` is
  `ADD + SUPERSEDES + MERGED` (`cairn-cutover.py:516`, `:494`).
- **Observed (with values):** `SUPERSEDES` and `MERGED` are BY DEFINITION entries whose pod bytes
  differ, which is exactly what the pre-flight refuses — so P3 exits 8 and pushes nothing the
  moment anything supersedes. Measured on the real store while auditing: **52 of 157** shared
  entries differ today, so this is the normal state, not an edge.
- **Ruled out:** "the cutover tests would have caught it" — `test_cairn_cutover.py` only
  re-extracts the `find` expression from `seed.sh`'s source (`:473-475`); **nothing exercises P3
  against the real script**, so its 85 green tests say nothing about this. via: code
- **Ruled out:** "the refusal predates this PR so the guidance is fine" — the exit-8 refusal does
  predate round 2, but round 2 rewrote the message, and it now tells that caller the local tree
  is "a FROZEN pre-cutover mirror" and to "send it entry-by-entry". Both are FALSE for a curated
  delta that `_materialise` built and whose rollback set `_save_prepush` already wrote. via: code
- **Leading hypothesis:** P3 either needs to pass `--allow-overwrite` (it IS a reviewed delta
  with a rollback set already on disk) or is simply dead post-cutover and should say so. This is
  a decision about the cutover's lifecycle, not a bug fix, which is why it was not taken
  unilaterally.
- **Next probe:** `python3 scripts/cairn-cutover.py --help` and read P3's own description, then
  decide. If P3 is retained, the one-line change is `--allow-overwrite` at
  `cairn-cutover.py:1379-1382` plus a test that exercises P3 against the real `seed.sh`.

## Next steps (ranked)

1. **Round 3 delta re-audit of `ec16cce8..d7c4c266`** (devrc#1304). Round 2 returned findings, so
   the ladder cannot stop there — a clean round is the stop condition, not a verdict. Dispatch
   BLIND: give the diff and round 2's claims block, never the reasoning for why they are correct.
   forcing: gate — this repo's audit gate is the only pre-merge review, and two of two rounds so
   far each found a real defect the previous round introduced.

2. **Gate `devrc#1304` and merge.** `scripts/gate.sh --tier both` AND
   `nix build .#checks.x86_64-linux.{pytests,nodetests}` ONE AT A TIME, on the MERGED tree, with
   the base sha named in the claim. ⚠ Read the runners' `RESULT:` lines, never a piped exit
   code — that trap fired four times in this effort. ⚠ Any red measured above ~load 20 on this
   box needs a control before it means anything; three failures investigated here were other
   agents' suites.
   forcing: gate — nothing else gates a merge in this repo; `main` is protected in name only.

3. **Decide `cairn-cutover.py` P3** (see the open investigation above). Either pass
   `--allow-overwrite` or declare P3 dead post-cutover.
   forcing: regression — a shipped code path that can never complete, made worse by guidance
   that is false for its only programmatic caller.

4. **Fix the opencode blindness in `scripts/lib/clawgate_handoff.sh`.** Diagnosed and recorded
   (squash `13775144`), NOT fixed. It reads only `CLAUDE_CODE_SESSION_ID`;
   `grep -c OPENCODE_SESSION_ID` is **0**. Detached opencode ⇒ exit 3 forever; NESTED opencode
   inherits the outer Claude session's id ⇒ exit 0 with **another session's tasks**. The browser
   bridge already fails closed on exactly this (`X-Session-Origin: opencode-inherited`).
   forcing: regression — the nested path silently misattributes today.

5. **Run `scripts/ship.sh`.** Still never run in this effort. The workbench looked current; the
   **laptop is UNVERIFIED**, so a session there may still be told to write new entries into the
   dead mirror. Read every per-host line, not the final verdict.
   forcing: regression — a stale prescription on one host reintroduces the defect this effort
   closed.

6. **Decide the token allowlist for the 2 remaining local-only entries**
   (`civitai-app-requests/app-requests.md`, `civitai-developer-docs/apps.md`). `cairn create`
   answers `not-found`; `cairn doctor` confirms neither scope is in this token's allowlist.
   Widening it means editing the k8s secret and deleting the pod (the token file is read ONCE at
   startup) — an access-control change and a second outage window.
   forcing: none

7. **Fix `devrc#1170`'s 🟡5 and 🟡6.** Still never started. Re-measured 2026-09-04: **0**
   occurrences of `policy:` in `service_recon.py` on `origin/main`, so 🟡5 stands exactly as
   written. 🟡6: `--template` over an EXISTING entry prints the first-ever-file template and
   exits 0 silently, destroying an `OPEN:` bullet.
   forcing: none

8. **`main` is RED on `scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py`**
   (`test_a_body_file_written_by_a_heredoc_on_the_same_line_is_read`). PROVEN inherited: fails on
   `origin/main` with any PR diff absent, deterministically in 0.24s on a quiet box. ⚠ The
   SANDBOX tier does NOT reproduce it — only the dev-host tier — so it is invisible to the gate a
   merge is judged on. Unowned.
   forcing: regression — a red dev-host tier trains everyone to merge through it.

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

- **Carried forward from the previous `State now` (it would otherwise be dropped by this
  update):** `devrc#1223 → 540e748d`, the `dropped lines:` advisory in `--validate`, was
  verified by content AND behaviour — run against the real 2026-08-19 blob it reports **13
  dropped lines** and flags nuance line 11 as a lost declaration.
- 🔴 **`ctime` cannot distinguish "the writer set the mode" from "something chmod'd right
  after" — it only rules out a LATER re-freeze.** Both shapes leave ctime a few ms past mtime.
  What actually answered it was reproducing the `Edit` in a replica. An earlier reading of mine
  ("no post-write chmod") was stated too widely and is corrected to that narrower claim.
- 🔴 **A validator that goes red is not yet a validated instrument.** The first negative control
  went red for the WRONG reason — copying `tests.md` to `_control.md` tripped the
  filename-vs-`service:` guard, not the wrapped-`aliases:` defect being injected. Redone with a
  matching slug it gave the paired result that counts: positive rc=0 unmodified, negative rc=3
  with `aliases: must be a list, not a bare string`.
- 🔴 **A line-based bullet scan under-counts against a multi-line corpus.** `^- YYYY-MM-DD:`
  found 24; block-aware parsing found 25, and the extra one was a stranded in-place EDIT of an
  existing pod bullet — a case that must be REPLACED, never inserted, or it duplicates.
- **The `cairn` write verbs are `append` and `put` only.** `PUT` requires `If-Match` (428
  without) and explicitly REFUSES `If-Match: *`; `replace_entry` opens `path.read_bytes()`. So
  the pod structurally cannot accept a new entry, and `seed.sh` is the only path that ever
  created one. That is why item 1 is a code change, not an operation.
- **Front-matter/`## Pointers` divergence was checked and was ZERO** — all 10 shared entries
  were byte-identical above `## Nuance / work-history`, which is what made a bullet-level
  insert safe. Do not assume that holds next time; it was measured, not reasoned.
- **`main` moved twice mid-session** (`dc7345f6`, `2c6b2ac9`). `2c6b2ac9` is adjacent work —
  "the THIRD frozen read surface — the one whose output drives deletions (rank 20)" — so more
  than one session is repointing read surfaces off this mirror. Check for overlap before
  editing `subsystem_audit`/`subsystem_recall`.
- **No clawgate task recorded.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for this
  session, with its positive control confirming the board was reachable. A wrong session id
  answers 200/empty exactly like a session that touched nothing, so this is **not** a clean
  reading; no field was written and no task was created.
- ⚠ **Environment, unchanged:** the shared `devrc` clone still holds another session's
  uncommitted WIP (`nix/programs/alacritty/default.nix`, `nix/system/apply-tmp-churn-retention.sh`,
  `output.txt`, two `scripts/diagnose-*.sh`). Nothing here touched them.

- 🔴 **A COMMIT MESSAGE WRITTEN FROM MEMORY SHIPPED A FALSE CLAIM, AND THE DEFECT IT SAID WAS
  FIXED WENT WITH IT.** `3c8e37da` asserted a 🔴 fix; the pushed blob contained **none** of it
  (`grep -c OC_LOCK_PID_FILE` = 6 where it should have been 0). Cause: the red-at-base check
  restores with `git checkout HEAD -- <file>`, and it was run BEFORE committing, so `HEAD` was
  the pre-fix commit and the "restore" reverted the uncommitted work. `git add` then staged a
  file that no longer held the change. **Read the claim off the committed blob, never off what
  you remember editing** — and commit before any checkout-based experiment.
- 🔴 **A CONTROL THAT SHARES THE CONTAMINANT IS NOT A CONTROL.** A browser-bridge failure
  reproduced on `origin/main`, which read as "inherited / main is broken" and was reported that
  way. It was neither: a machine-global orphaned lock was failing both runs. The rule names this
  shape exactly, and it was still walked into. The discriminator that worked was removing the
  suspected cause and watching the test pass (165s), not a second sample.
- 🔴 **THE PIPE TRAP FIRED FOUR TIMES IN ONE SESSION** — `… | tail; echo "rc=$?"` printed
  `GATE_RC=0` over `GATE: RESULT=FAIL exit=1`, and `NIXBUILD_RC=0` over a failed derivation.
  Reading the runners' own `RESULT:` line is the only thing that caught it each time.
- 🔴 **A GUARD CAN PIN THE DEFECT.** `test_index_append_protocol.py` asserted that
  `prune-index/SKILL.md` still contained "any editor write against one fails with `EACCES`" —
  the exact falsehood the work existed to correct. Correcting the prose turned the suite red.
  The same false sentence appeared in THREE places in that file family; two conflict markers
  pointed at none of them.
- 🔴 **A TEST CAN PASS FOR THE WRONG REASON IN THE DIRECTION THAT HIDES THE BUG.** #1277's
  release test asserted `not lock.exists()` after a kill — which is also true when the run
  simply completed. It only became meaningful once the kill was gated on a marker written
  INSIDE the warm (0.03s → 3.12s), proving the lock was held at that moment.
- **The gate's own tiers disagree, and the merge is judged on one of them.** The dev-host tier
  is red on a test the sandbox tier passes. `gate.sh` never invokes `nix build`; the sandbox
  builds from a store copy with no `.git`, so the whole repo-local guard class evaluates
  differently. Run both, and name the tier in any claim.
- ⚠ **Concurrent agents corrupt each other's test results on this box.** Load hit 62 on 24
  cores; three separate failures this effort investigated were other sessions' suites, not
  code. `browser-agent`'s machine-global lock was one mechanism; raw CPU contention was
  another. Any red measured above ~load 20 needs a control before it means anything.

- **Carried forward from an earlier `State now` (a REPLACE section, so it would otherwise be
  dropped):** the ORIGINAL rank 1 is CLOSED — the writer was Claude Code sessions themselves
  using `Edit`/`Write` on `~/.claude/analyze-service-index/` (the `0444` freeze is inert against
  them: those tools rewrite-and-rename and need only the containing directory's `0755` bit), 21
  stranded bullets + 2 revisions were reconciled onto the pod and verified at the consumer, and
  the write path was closed by the CREATE verb (`devrc#1254` → `34d00d90`, live as image
  `subsystem-store-api:0.7.0`, verified with `cairn create` returning exit 9 / already-exists
  where it returned 405 read-only before).
- **Also carried forward:** `seed.sh`'s blast radius is MEASURED HIGHER than when this doc was
  first written — beyond the cairn-attributed bullets it would revert the **5 pod-newer bullets**
  found on 2026-09-02/03, two of them `OPEN:` → `RESOLVED` closures with ~20 lines of later
  corrections, and report success.

- 🔴 **`git checkout -- <file>` DESTROYED UNCOMMITTED WORK THREE TIMES IN THIS EFFORT**, twice
  after a mutation run and once after a red-at-base check. It restores from the INDEX, so
  mutating uncommitted work and "reverting" takes the work with it. The second time it also
  shipped a FALSE COMMIT MESSAGE: `3c8e37da` asserted a 🔴 fix while
  `git show 3c8e37da:…/browser-agent | grep -c OC_LOCK_PID_FILE` was **6**, and a PR comment
  repeated the claim. **Commit before every mutation run, and read the claim off the committed
  blob rather than off what you remember editing.**
- 🔴 **A GUARD'S OWN CLEAN PATH CAN BE THE SILENT ZERO.** The `seed.sh` pre-flight printed its
  count only on refusal, so a probe that never ran and a pod holding nothing were the same
  observation. The fix that looks obvious — refuse when 0 are present — is WRONG: that is the
  ordinary first-seed case and it failed **18 legitimate tests**. The answerable question was
  "did it SEE the whole list", not "did it find anything".
- 🔴 **`-I{}` DOES NOT DISABLE `xargs` QUOTE PARSING** — only `-d`/`-0` does. And rebuilding a
  line from awk FIELDS (`{print $2" "$1}`) truncates any path at its first blank, which turned a
  loud crash into a confident FALSE REFUSAL naming a nonexistent path. Both shipped as a claim
  with no test; both were caught only by an audit re-running them.
- 🔴 **A CONTROL THAT SHARES THE CONTAMINANT IS NOT A CONTROL.** A browser-bridge failure
  reproduced on `origin/main` and was reported as "inherited / main is broken". It was neither —
  a machine-global orphaned lock was failing both runs. What worked was removing the suspected
  cause and watching the test pass, not a second sample.
- 🔴 **THE PIPE TRAP FIRED FOUR TIMES** — `… | tail; echo "rc=$?"` printed `GATE_RC=0` over
  `GATE: RESULT=FAIL exit=1`, and `NIXBUILD_RC=0` over a failed derivation. The runners' own
  `RESULT:` line caught it every time.
- 🔴 **A TEST CAN BE VACUOUS IN A WAY ONLY MUTATION SHOWS.** The first `LC_ALL=C join` guard
  planted its sort-inversion on the POD — but the probe answers only STAGED paths, so a pod-only
  file never reaches the join. It passed, and the mutant survived. Both sides of the inversion
  must be staged.
- **A `-k` FILTER CAN EXCLUDE THE KILLING TEST SILENTLY.** `-k "SILENTLY_SKIPPED"` matched
  nothing against class `…SILENTLYSKIPPED` and reported `1 passed` — a green that proved nothing
  about the two tests it had quietly dropped.
- **Concurrent agents corrupt each other's results on this box.** Load hit 62 on 24 cores; three
  failures investigated in this effort were other sessions' suites rather than code. Queue behind
  them rather than killing them, and treat any red above ~load 20 as needing a control.
- **No clawgate task recorded.** `clawgate_handoff.sh resolve` exited **6** — one linked task
  (`#477`, role=`read`, "Bot-account detection agent"), NONE worked. That task was read only to
  verify another agent's claim about it and is definitively not this work, so per the flow no
  field was written and none was created.

## How to verify

```bash
# the reconciliation is live on the pod, read through the PRESCRIBED reader
cairn sync
grep -c 'A GUARD OVER TEXT, OR OVER ONE INSTANCE' ~/.cache/subsystem-store/devrc/tests.md   # 1
grep -c 'DO NOT RETRY THE NODE UNPIN' ~/.cache/subsystem-store/homelab-talos/devrc-ci.md    # 1

# the 5 pod-newer bullets were NOT reverted (each must still say RESOLVED / UPDATED)
grep -c 'RESOLVED f7557727c' ~/.cache/subsystem-store/datapacket-talos/tekton-builds.md     # 1
grep -c 'RESOLVED 841d6fc4' ~/.cache/subsystem-store/homelab-talos/tekton-ci.md             # 1

# the freeze is inert against the Edit tool (reproduce, don't trust this doc)
d=$(mktemp -d); printf 'a\nb\n' > $d/f.md; chmod 0444 $d/f.md
printf 'x\n' >> $d/f.md 2>/dev/null && echo "shell wrote it" || echo "shell EACCES (expected)"
# then Edit $d/f.md with the Edit tool -> succeeds, and `stat -c %a` still reports 444

# the 5 entries still local-only (until item 1 lands)
comm -23 <(cd ~/.claude/analyze-service-index && find . -name '*.md' ! -name README.md -printf '%P\n' | sort) \
         <(cd ~/.cache/subsystem-store   && find . -name '*.md' ! -name README.md -printf '%P\n' | sort)
```
