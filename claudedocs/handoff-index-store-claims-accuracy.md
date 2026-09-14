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

**BOTH RANKED ITEMS THIS DOC CARRIED ARE CLOSED, and the arc is finished.** Five PRs
merged, each verified on `origin/main` BY CONTENT rather than by ancestry:

- **`#1504` → `3161851b`** — retracted *"there is no CREATE route"* from the `cairn`
  skill (false since `#1254`), swept **five** tracked files, added a two-way verb
  ledger and a repo-wide needle. Four audit rounds; see Gotchas.
- **`#1553` → `b4d472f7`** — guards `operator-surface.md`'s REFUSED/ABSENT probe
  against the code it routes off: the doc's remedy table is parsed as a routing
  object and every code it quotes is checked against `server.py`'s emissions and
  `scripts/cairn::_classify`. A SEAM guard — both sides were well tested alone.
- **`#1554` → `663bc86a`** — `#1170`'s 🟡5 (the probe now EMITS `policy:`) and 🟡6
  (`--template` refuses instead of clobbering).
- **`#1636` → `7cee3602`** — the ranked-list closeout.
- `#1508` (not ours) merged mid-arc and conflicted with `#1554`; resolved union.

**Deploy status, stated separately from the merge:** `#1504` is DEPLOYED and verified
at the consumer on BOTH hosts — same `/nix/store` path, `cmp`-identical to
`origin/main`. `#1553`/`#1554`/`#1636` are merged but **NOT deployed** (no switch since).

**Two live-state repairs, neither of them a ranked item:**
- `cli/safeterm.md` was WRITABLE in the frozen mirror. Re-frozen `0444` — but only
  after proving the POD copy was newer (9,840 vs 3,763 bytes; pod held all 4 of the
  mirror's bullets plus 5 more). Pushing the mirror would have reverted a day.
- The two stranded entries were **single-copy**: the laptop had neither, the mirror
  is not a git repo, and the backup CronJob covers the pod's `/data`, not the mirror.
  Rescue copies now on BOTH hosts at `~/rescue/subsystem-stranded-2026-09-12/`,
  md5-verified. That is what made rank 1 safe to park.

**No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks
for this session. An unknown session id answers `200 {"tasks":[]}`, not 404, so this
cannot distinguish "touched no task" from "the id is wrong". Per the flow, nothing
was written and no task was created. This is **not** a clean reading.

⚠ **This doc was cut on 2026-09-13** because it passed its size ceiling and turned `main`
RED. Nothing was deleted: every closed investigation block, the closed ranked item and ten
duplicated Gotchas bullets were moved VERBATIM to
`claudedocs/refs/index-store-claims-accuracy.md` and are pointed at from where they stood. 🔴 That file is **not** indexed by `handoff_search` — which is why only
CLOSED material went there, and why every open investigation, gotcha and ruled-out theory
below stayed.

## Open investigations — live diagnosis state

### ✅ CLOSED — why devrc-ci went red on PRs whose diff could not reach the failing suite
Both causes were specific, not systemic: the store-api reds were `_replace_bytes` fsyncing inside
the request and exceeding `HANG_TIMEOUT` under disk contention (**devrc#1211** → `1a4350f3`); the
browser-bridge reds were three flat `elapsed < 1.0` bounds against a **5.0s** timeout, i.e. load
detectors rather than timeout detectors (**devrc#1179**, which derives each as `TIMEOUT / 2`).
🔴 **If it recurs, read the `verdict` step FIRST** — `pytests exit 0` + `verdict exit 1` means a
test FAILED, and a step that emitted no `RESULT:` line was KILLED, which is a different problem.
Run ids, the per-run failure counts and the RETRACTED unsandboxed-nix theory →
`claudedocs/refs/index-store-claims-accuracy.md`.

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

### ✅ CLOSED — `cairn-cutover.py` P3 is blocked by the new guard and cannot complete
Settled the OPPOSITE way round from this block's own leading hypothesis: P3 is **RETIRED**
(**devrc#1428** → squash `13c0791a`), because `--allow-overwrite` is the DATA-LOSS path and the
exit-8 refusal was the last guard, not the bug. See the retained Gotchas bullet. Full diagnosis,
the `52 of 157` shared-entries measurement and the two ruled-out readings →
`claudedocs/refs/index-store-claims-accuracy.md`.

### 🔴 The pre-flight's join key can DIVERGE between host and pod — and the pod's `echo` is the reachable route
- **Symptom + exact repro:** a staged entry whose pod copy DIFFERS is silently treated as a pure
  addition and pushed, or an IDENTICAL entry is falsely refused naming a path that does not
  exist. Reproduce the escape half with no cluster:
  `dash -c 'echo "ABSENT  $1"' _ 'sc/tab\there.md' | cat -A`
- **Observed (with values):** `scripts/subsystem-store-api/Dockerfile:34` is
  `FROM python:3.12-slim` → Debian → `/bin/sh` is **dash**. dash's `echo` interprets
  backslash escapes; bash's does not. Same command, same input:

  | staged path | pod (dash) | tests (bash) |
  |---|---|---|
  | `sc/tab\there.md` | `ABSENT  sc/tab<REAL TAB>here.md` | `ABSENT  sc/tab\there.md` |
  | `sc/new\nline.md` | splits into **TWO** lines | one line |
  | `sc/cut\chere.md` | truncates AND **swallows the next answer** | one line |

  The `\t` case emits a real TAB on the pod side only, so the local key is
  `sc/tab\there.md` and the pod key is `sc/tab` — they do not join, the row vanishes, and a
  differing pod entry reads as a pure addition. `answered == staged` still holds, so exit 9
  does not fire. `printf 'ABSENT  %s\n' "$1"` produces the bash output under dash in all three
  cases (measured).
- **Observed (lens-1 auditor, its fuzzer validated both ways — 0 disagreements in 400 clean
  trials, and it killed a known-fatal mutant by dropping `join -t <TAB>`):** a literal TAB in a
  staged path takes base `differing=0 / rc 0` to HEAD `differing=1 / rc 8` naming `sc/a`; two
  scopes `" sc"` and `"sc"` take base `rc 0` to HEAD `differing=2 / rc 8` naming `sc/e.md`
  twice. Both are FALSE REFUSALS — fail-safe, but verbatim the shape the delta's own comment
  claims to have removed.
- **Observed:** `UNREADABLE` does not always reach the clobber set. For `sc/back\slash.md`
  differing AND unreadable on the pod, GNU `sha256sum` escapes the local line
  (`sc/back\\slash.md`) while the hand-written `echo` does not, the keys diverge, `clobber` is
  empty and `PRE-FLIGHT staged=1 answered=1 differing=0` lets the push proceed.
- **Ruled out:** "the pod's `xargs` is busybox, so `-d '\n'` fails" — the image carries GNU
  findutils 4.10.0 and GNU coreutils 9.7, so `-d` works and the HASH branch's escaping is
  symmetric with the host. The asymmetry is confined to the two hand-written `echo` branches.
  via: measurement
- **Ruled out:** "round 2's TAB key removed the truncation class" — it moved the boundary from
  `0x20` to `0x09`; it did not remove it. via: measurement
- **Ruled out:** "the existing suite would catch any of this" — the file is fully green
  (723 passed) and catches none of them, because the fake `kubectl` runs the probe under bash.
  via: measurement
- **Leading hypothesis — one root cause, not five:** the key is derived by parsing a path back
  OUT of hash-output text, on two sides that do not agree on how text is escaped. The
  consolidating fix is to stop doing that: both sides consume the SAME `$staged_list` in the
  SAME order through `xargs -I{}`, so pair by POSITION (`paste`) and look the path up from
  `$staged_list` by line number. That structurally removes the TAB case, the leading-space
  case, the `UNREADABLE` case AND the whole `sort`/`join`/`LC_ALL` ordering class. Cost: it
  deletes `TestTheSeedPreFlightJoinIsLocaleSafe`, which pins a real hazard — so removing it
  needs care, and that is why it was not done unilaterally.
- **Corroboration:** lens 2 reproduced the TAB false refusal INDEPENDENTLY at unmutated HEAD —
  two byte-identical `widget-cfg/ta<TAB>bbed.md` files give `differing=1`, exit 8, and a refusal
  naming `widget-cfg/ta`. Mechanism: `join -t TAB` splits the tab path into extra fields, so
  `awk -F'\t' '$2 != $3'` compares a filename fragment against a hash and is ALWAYS unequal.
  Backslash paths pass. Direction is fail-safe (always-refuse, never a silent clobber) for the
  literal-TAB route — the SILENT-CLOBBER route is the dash `echo` one above, which lens 2 did
  not test.
- **Next probe:** decide between the minimal fix (`echo` → `printf` on both pod branches; `awk`
  skips blank lines in `--help`) and the structural one (pair by position). Either way a ROUND 4
  delta audit is owed.

### ✅ CLOSED — `--help` still drifts, and has ZERO test coverage
Both halves closed. `test_subsystem_store_api.py` now asserts `seed.sh --help` contains
`--allow-overwrite`, and pins the blank-line injection that used to truncate it. ⚠ The standing
lesson is retained in Gotchas below — **`--help` claimed "cannot drift" TWICE and was wrong both
times**, which is why the test exists. The 61 → 31 line measurement and the two ruled-out readings
→ `claudedocs/refs/index-store-claims-accuracy.md`.

### Two of the delta's NEW guards have no test — both mutants SURVIVED
- **Symptom + exact repro:** break the guard on purpose and the suite stays green.
  `MUTANT F`: insert `awk '!index($0,"\047")' |` before the LOCAL `xargs` (drops quoted paths,
  exits 0, remote probe untouched so `answered==staged` still holds).
  `MUTANT H`: revert `seed.sh:441`'s `|| [ $? -eq 1 ]` to `|| :`.
- **Observed (with values):** both survived **all 46 seed tests** (unmutated control 46/46
  green). Under `MUTANT F` a quoted entry whose pod copy DIFFERS prints
  `PRE-FLIGHT staged=4 answered=4 present_on_pod=1 differing=0` then
  `seed: PUSHED … seed: OK all 4 staged entries are present on the pod` — a silent clobber, the
  exact defect this PR exists to remove, with the suite green. The auditor wrote the missing
  test (a quoted entry whose pod copy differs); it FAILS under `MUTANT F` and PASSES at
  unmutated HEAD, so the capability is real and only the test is absent.
- **Ruled out:** "the SPACE hazard has the same gap" — it does not. `MUTANT G` (exclude spaced
  paths from the comparison) killed the DIFFERS test while the IDENTICAL test survived, so the
  SPACE pair is a genuine two-direction control. Only the QUOTE hazard is one-directional.
  via: measurement
- **Ruled out:** "`|| [ $? -eq 1 ]` is unreachable, so the surviving mutant is harmless" — GNU
  grep does exit **2** on a write error (verified against `/dev/full`), and
  `cmd || [ $? -eq 1 ]` does abort under `set -euo pipefail` (verified). The guard works; it is
  simply unguarded. ⚠ Its abort emits **no `seed:` line** — the bare-failure-no-diagnostic shape
  the QUOTE test's own docstring calls unacceptable, one guard over. via: measurement
- **Leading hypothesis:** three tests are owed, not a code change — a quoted entry whose pod
  copy differs (kills F), a `grep` I/O-error path (kills H), and a `seed.sh --help` assertion.
- **Next probe:** add them in the same commit as the fixes above, then round 4.

### What the `fake_cluster` harness structurally CANNOT see
- **Symptom + exact repro:** read the fixture — its fake `kubectl exec` sed-rewrites
  `/data`→`$FAKE_DEST` in each argument and runs the command **locally** via `"$@"`. The "pod"
  is this host.
- **Observed (with values):** in the devshell `sh` resolves to `bash-interactive-5.3p15/bin/sh`
  — bash, the most permissive shell available — while the real pod's `/bin/sh` is dash. Also:
  `xargs -d` is a GNU findutils extension, and **no test pins the `Dockerfile`'s `FROM` line**
  (`test_the_image_copies_every_module_it_needs` pins `COPY` lines only), so a base-image swap
  to alpine would break the probe with **zero** test failures. Truncation is simulated only as
  TOTAL silence (`FAKE_PROBE_SILENT` → 0 lines); PARTIAL truncation and pod-stderr interleaving
  into probe stdout are never produced.
- **Ruled out:** "723 green tests cover the probe's behaviour on the pod" — they cover it on
  this host, under bash, with GNU tools. Every finding in the two blocks above is invisible to
  all of them. via: measurement
- **Leading hypothesis:** a cheap two-line test pinning `FROM python:3.12-slim` (so a base-image
  swap fails loudly) is worth more than widening the fake; a genuinely faithful fake needs the
  real image.
- **Next probe:** add the `FROM` pin; decide separately whether a docker-backed test is worth
  its cost.

### ✅ CLOSED — `test_git_repo_isolation.py::test_live_cotenants_sees_another_process_in_the_repo`
Diagnosed and fixed in **devrc#1453** → `eeea9025`: the intruder is
`git maintenance run --auto --quiet --detach`, which `subprocess.run` does not wait for, so the
child outlives the parent with its cwd inside the just-created repo. Fixed with
`maintenance.auto=false` in `_GIT_ENV`.
🔴 **Do NOT re-derive the `gc --auto` theory.** It was refuted twice — arithmetically (3 loose
objects against a 6700 threshold) and empirically (0/80 with `gc.auto=0` forced, behind a positive
control) — and **both refutations were CORRECT**; `maintenance run --auto` is a different code path
that `gc.auto` does not reach. `gc.auto` is deliberately NOT set in the fix. Both blocks — the
load-flaky observation and the REFUTED-theory block with its positive control — are preserved
verbatim in `claudedocs/refs/index-store-claims-accuracy.md`; the standing lessons ("ship the
diagnostic, not a guess"; "a 0/N only means something after a positive control") are retained in
Gotchas below.

## Next steps (ranked)

🔴 **BOTH RANKS THIS DOC CARRIED ARE CLOSED. It went stale a FOURTH time before
this edit** — it was still telling a reader that rank 2 had been "Never started"
**after `#1554` had merged**, and still flagging a sub-claim as UNVERIFIED after it
had been verified. The session that closed them claimed the item with *"retire the
stale ranked text"* in its own subject line and then did not do it. That is the
failure this doc has documented three times over; nothing closes a ranked item
automatically, and the next writer is the only moment anyone looks.

1. ⏸ **PARKED, deliberately — the two stranded scopes** (`civitai-app-requests`,
   `civitai-developer-docs`). ⚠ The old text asked the WRONG QUESTION ("decide the
   token allowlist") and carried a sub-claim it marked UNVERIFIED. Both settled
   2026-09-12:
   - **`cairn create` DOES create a scope's first entry, directory included**
     (`create_entry` → `path.parent.mkdir(exist_ok=True)`; pinned by
     `test_a_scopes_FIRST_entry_creates_the_directory`). **Seeding is NOT
     required** — `seed.sh` is for bulk pushes and carries the overwrite hazard.
   - **The only scope-level gate is the token's scope allowlist.** The
     `not-found` sub-claim is VERIFIED: rc 6 for a non-allowlisted scope against a
     control of rc 9 `already-exists`; both wrote nothing. ⚠ The original probe
     could not have distinguished the mechanisms — it used a scope that was absent
     AND non-allowlisted on a pod where those sets coincide.
   **Why parked, not done:** the remaining step edits a SOPS secret in
   `ZacxDev/homelab-infra` and needs a pod delete, whose failure mode is
   `EXIT_CONFIG (78)` with no second pod under `Recreate` — *the store stays down*.
   Measured against that: nothing has read either scope, neither repo is checked
   out on this host, and the entries have not moved since 2026-09-02/03.
   🔴 **The loss risk that would have justified it is CLOSED**: they were
   single-copy (laptop had neither, the mirror is not a git repo, and the backup
   CronJob covers the pod's `/data`, not the mirror). Rescue copies now exist on
   BOTH hosts at `~/rescue/subsystem-stranded-2026-09-12/`, md5-verified.
   **Closing condition:** the next time that secret is edited for ANY reason, add
   both scope names in the same commit — the pod restart is already being paid —
   then `cairn create` twice. `cairn doctor`'s `token-scopes` PROBLEM is the
   standing reminder and is an accurate report, not an alarm.
   forcing: none

2. **Two small guard gaps left open on purpose, named so they read as OPEN rather
   than absent.**
   - `scripts/tests/test_cairn_skill_verb_ledger.py` — nothing verifies that a
     `DELEGATED` ledger reason is TRUE. Measured churn of the guarded surface:
     6 verb-set-changing commits in 16 days, so the cheapest way past the ledger is
     to type `DELEGATED` and a sentence. (Round 0 of `/audit-pr` on `#1504`.)
   - `_RETRACTED_BOUNDARY` has no needle for the retracted *"widening the allowlist
     changes nothing"*. MEASURED structural, not live: the only two occurrences sit
     inside explicit retraction markers. 🔴 Any needle change there owes the round-4
     discipline — a false-positive probe over TRUE sentences **plus** a mutation
     battery, verified as a pair. A needle without a subject fires on correct
     writing; that has happened twice in this file's history.
   forcing: none

## Gotchas / decisions / dead-ends
- ✅ **THE DASH PREMISE IS TRUE ON THE DEPLOYED POD, AND NO LONGER LOAD-BEARING** —
  every pod-side emit in `seed.sh` is now `printf`, not `echo` (`grep -nE "sh -c .*echo"`
  returns nothing), so dash's escape-interpreting `echo` cannot reach the join key any
  more. 🔴 Read that beside the join-key block above, which still carries the LITERAL-TAB
  route: the premise held AND the code stopped depending on it. Measurement, bash control
  and the honest `forcing: none` outcome → `claudedocs/refs/index-store-claims-accuracy.md`.

- ✅ **THE CO-TENANT FLAKE IS DIAGNOSED AND FIXED — `devrc#1453` → `eeea9025`. It
  was `git maintenance run --auto --quiet --detach`, never `gc --auto`.**
  Committing spawns it DETACHED, so `subprocess.run` returns when the parent exits
  while that child lives on with its cwd inside the repo created microseconds
  earlier — and `live_cotenants` matches on cwd. Fixed with
  `maintenance.auto=false` via `GIT_CONFIG_*` in `_GIT_ENV`, so it covers every
  git call in the file including ones added later.
- 🔴 **THE `gc --auto` REFUTATION WAS RIGHT, AND THAT IS WHY THIS TOOK SO LONG.**
  It was killed twice — 3 loose objects against a 6700 threshold, and 0/80 with
  `gc.auto=0` forced. Both correct. `maintenance run --auto` is a DIFFERENT code
  path, reached regardless of `gc.auto`, deciding per-task only after the process
  exists. `gc.auto` is deliberately NOT set in the fix: it would read as
  belt-and-braces while quietly re-legitimising a disproved theory.
- 🔴 **SHIPPING THE DIAGNOSTIC INSTEAD OF A GUESS IS WHAT SOLVED IT.** `gc.auto=0`
  was one line and would have looked like a resolution while the real mechanism
  stayed open behind it. `#1340` instead made the assertion print the intruder's
  `cwd=`/`cmdline=`, and the first recurrence named its own cause in one line.
  **When the mechanism is unknown, ship the diagnostic.**
- 🔴 **PIN A FLAKE BY ITS SPAWN, NOT BY ITS RACE.** Waiting for recurrence is not
  a test — this went 0/80 in a deliberate loop and then fired in CI.
  `GIT_TRACE2_EVENT` records every child git spawns, so the guard is a spawn
  COUNT: deterministic, with a positive control proving the trace recorded
  anything at all.
- 🔴 **THIS RANKED LIST HAS NOW GONE STALE THREE TIMES, ONCE WITHIN A SINGLE
  SESSION.** P3 and the opencode item were closed in `#1449` while rank 1 was
  being fixed in `#1453`, so the doc shipped saying "still UNFIXED" about work
  that had merged an hour earlier. Nothing closes these automatically; the next
  writer is the only moment anyone looks. **Re-verify every ranked item against
  `origin/main` by CONTENT before acting on it or quoting it.**
- ⚠ **A failure-set comparison can be VACUOUS AND LOOK CLEAN.** Comparing which
  tests fail on two trees: the sandbox log carries no `FAILED ` summary lines
  (the runner uses `-q` without `-rf`), so a `FAILED`-based grep returned 0 for
  BOTH trees and `comm` printed an empty, reassuring "no new failures". Only a
  positive control (11 names on one side, 10 on the other) made it evidence.
  Extract from pytest's traceback headers, and never quote an empty diff without
  showing the extraction found something.

- ✅ **THE OPENCODE BLINDNESS IS FIXED — `devrc#1365` → squash `14126d94`.**
  `clawgate_resolve` reads `OPENCODE_SESSION_ID` before `CLAUDE_CODE_SESSION_ID`, and
  refuses outright when `$OPENCODE` is set with no opencode id. ⚠ **STILL OPEN, and it is
  a CAPABILITY gap rather than a correctness one:** `clawgatectl` has no opencode tier, so
  opencode sessions resolve exit 5 rather than their own tasks until that lands — a Go
  change in `homelab-talos`. Verification detail → `claudedocs/refs/index-store-claims-accuracy.md`.

- ✅ **P3 IS RETIRED — `devrc#1428` → squash `13c0791a`.** `--allow-overwrite` is the
  DATA-LOSS path, not the one-line fix the old ranked item called it: `seed.sh`'s tar
  adds and overwrites but never deletes. P3 now refuses with `RC_CUTOVER_COMPLETE (19)`,
  conditioned on state so a genuine first cutover still passes. Why the old framing was
  the wrong half of the choice → `claudedocs/refs/index-store-claims-accuracy.md`.
- 🔴 **FOUR AUDIT ROUNDS, AND THE LAST THREE FOUND MY OWN PROSE, NOT MY CODE.** The
  guard was right after round 1; rounds 2–4 each found that the *fix round* had
  written something false or harmful. Round 2: the predicate `writable == 0` failed
  in BOTH directions (a post-freeze creation disarmed it; EROFS tripped it falsely).
  Round 3: `refused > 0` then failed OPEN on the `other` bucket — I traded one
  direction for the other. Round 4: my ordering guard used `str.index`, which found
  an earlier unrelated occurrence, so the defect it existed to stop passed it.
- 🔴 **THE ADVICE WAS THE WORST DEFECT, AND IT WAS MINE.** A recovery route I
  recommended (`--freeze --apply`) writes a SECOND mode ledger recording 0444;
  `--unfreeze` takes the newest, so a 0600 entry is "restored" to 0444 and the
  rollback exits 0. Reproduced end to end by the round-4 auditor, along with the
  control proving the caveat is load-bearing. **Prose that tells an operator what to
  do is payload — audit it like code.**
- ⚠ **`cairn create` EXISTS** (`34d00d90`/#1254, `PUT` + `If-None-Match: *`), so
  retiring P3 strands nothing. Two comments in the tree still said the API had
  "no create route" — that sentence is wrong, and it was what made this look
  costly. One occurrence straddles a newline AND is uppercase, so case-sensitive
  and line-based greps both miss it.
  🔴 **THIS BULLET'S COUNT HAS NOW BEEN WRONG TWICE, WHICH IS THE POINT OF IT** —
  every wrong figure was produced by a hand sweep and every one read as precise.
  **Do not quote a count here you have not re-derived with the repo's OWN scanner**
  (`_normalise_for_scan` + `_RETRACTION_MARKERS` + `_MARKER_WINDOW`), behind a
  positive control that returns non-zero. The 2026-09-11 figures and the history of
  the wrong ones → `claudedocs/refs/index-store-claims-accuracy.md`.
  🔴 **AND "ALL SITES ARE CORRECTED" WAS ITSELF FALSE WHEN WRITTEN — round 3 found
  TWO more live, present-tense sites, one of them 115 lines below that claim in
  this very file.** Four sweeps, four spellings: mechanism → one conclusion phrase
  → two more. **A string needle cannot close this class**, because the claim has
  no canonical wording; the needles in `_RETRACTED_BOUNDARY` catch the five
  spellings seen so far and nothing guarantees a sixth. Sweep by MEANING before
  claiming this class is clean, and do not write "all sites are corrected" again
  without showing the sweep that establishes it.
- ⚠ **A mutation batch that reports a green may have applied NO mutant.** My first
  attempt at the round-4 verification had a non-matching anchor and printed "97
  passed" — indistinguishable from a survival. Only the traceback caught it.

- 🔴 **A VERSION STRING DERIVED FROM THE COMPILED SOURCE IS STILL NOT A CURRENCY SIGNAL.**
  `clawgatectl.nix` reads `version` out of the very `client.go` it compiles — the design that
  exists so a label cannot lie about its code — and the workbench STILL sat 2 commits stale at
  a label identical to current (`0.8.27` both sides), because the incoming commits changed
  `internal/ui/*` without bumping `buildVersion`. Derivation-from-source guarantees the label
  is not FABRICATED; it guarantees nothing about being CURRENT. Compare the resolved
  `/nix/store` path, or the subtree tree OID — both moved when the label did not.
- 🔴 **A PER-HOST CONDITION NAMED FOR ONE HOST IS A SCOPE CLAIM, AND IT AGED WRONG.** This
  doc's rank 2 said "the laptop"; five days later the workbench was stale too and the laptop's
  gap had grown 18 → 24. Fixing exactly what the ranked item named would have left rc 17
  firing and read as a failed fix. **Re-run the detector and fix what IT names**, not the host
  the doc remembers — the detector reports per-host and the doc does not.
- ⚠ **A merged `2>&1 >/dev/null` capture is not a stderr reading** — hit here checking for a
  version-skew notice, and JSON appeared in the "stderr" half. Each stream to its own file is
  what turned it into evidence (`stderr bytes: 0`). The rules name this exact shape; it was
  walked into anyway, which is the argument for the file-per-stream habit over care.
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
- ⚠ **RETRACTED — this bullet described the PRE-#1254 world and read as current.** It said the
  write verbs were `append` and `put` only; it said the pod *"structurally cannot accept a new
  entry"*; and it said `seed.sh` was *"the only path that ever created one"*. Each of those
  is wrong today — **devrc#1254 / `34d00d90`** added `create`, which makes the scope directory
  too. 🔴 This bullet survived a sweep that had just declared "All sites are corrected"
  — **115 lines above it** — because that sweep matched strings and these sentences spell the
  claim differently. Do not reach for `seed.sh` on the strength of a paragraph like this one.
- **Front-matter/`## Pointers` divergence was checked and was ZERO** — all 10 shared entries
  were byte-identical above `## Nuance / work-history`, which is what made a bullet-level
  insert safe. Do not assume that holds next time; it was measured, not reasoned.
- **`main` moved twice mid-session** (`dc7345f6`, `2c6b2ac9`). `2c6b2ac9` is adjacent work —
  "the THIRD frozen read surface — the one whose output drives deletions (rank 20)" — so more
  than one session is repointing read surfaces off this mirror. Check for overlap before
  editing `subsystem_audit`/`subsystem_recall`.
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
- 🔴 **A TEST CAN BE VACUOUS IN A WAY ONLY MUTATION SHOWS.** The first `LC_ALL=C join` guard
  planted its sort-inversion on the POD — but the probe answers only STAGED paths, so a pod-only
  file never reaches the join. It passed, and the mutant survived. Both sides of the inversion
  must be staged.
- **A `-k` FILTER CAN EXCLUDE THE KILLING TEST SILENTLY.** `-k "SILENTLY_SKIPPED"` matched
  nothing against class `…SILENTLYSKIPPED` and reported `1 passed` — a green that proved nothing
  about the two tests it had quietly dropped.
- 🔴 **THE TEST HARNESS RUNS THE POD'S COMMAND UNDER BASH, AND THE POD IS DASH.** Every test in
  `test_subsystem_store_api.py` drives a fake `kubectl` whose `exec` runs the command locally.
  `echo "ABSENT  $1"` therefore behaves one way in all 723 green tests and a different way on
  the pod. **Any claim about the probe's OUTPUT TEXT is unproven by that suite.** Measure the
  pod side with `dash`, or by running the `Dockerfile`'s own base image under docker — both
  cost seconds and neither needs a cluster.
- 🔴 **A DELIMITER FIX THAT CHANGES THE DELIMITER IS NOT A FIX.** Round 2 replaced a
  space-delimited key with a TAB-delimited one and declared the truncation class closed. It
  moved the boundary from `0x20` to `0x09` — the same bug, one byte lower, and the round-2
  commit's own comment describes the defect it still has. Ask instead whether the key needs to
  be parsed out of text AT ALL.
- **Two of three round-3 findings I re-measured MYSELF rather than taking the auditor's word,
  and both held exactly.** The rules require re-verifying a subagent's numbers; here they were
  right. That is worth recording precisely because the previous two rounds each carried a wrong
  datum from a subagent.
- 🔴 **THE FIRST ROUND WHOSE PREDECESSOR'S CLAIMS ALL SURVIVED — and it still found defects.**
  Rounds 1 and 2 each caught the previous round LYING about what it had fixed. Round 3 verified
  every round-2 claim as TRUE (and one as *understated*), then found four NEW gaps anyway. So a
  round is not over when the previous round's claims check out; "the claims are honest" and
  "the code is right" are different questions, and only the second ends the ladder.
- 🔴 **A SURVIVING MUTANT IS THE ONLY THING THAT FOUND THE MISSING QUOTE TEST.** The QUOTE test
  looked like a regression test, IS red at base, and still pins nothing about whether the quoted
  path was compared — because it asserts an absence (no crash) rather than a presence. The tell
  is generic: **a test whose every assertion is negative cannot distinguish "handled" from
  "skipped".** Its SPACE sibling avoids this only because a second test asserts the positive
  direction.
- **The handoff doc was 1 commit behind at session start** and `handoff_doc.py` resolves its
  base from the working tree, so the fast-forward had to happen BEFORE any draft. A stale base
  would have merged into an out-of-date document and reported success.
- **Two corrections to this doc, measured 2026-09-05 — both now ✅ CLOSED.** The live
  residue is the one sentence worth carrying: **the dev-host tier has no known inherited
  red any more, so a red there now means something** (read that beside the "INHERITED RED"
  bullet below, which is what it qualifies). The old rank 8, the `8c27c5cf` fix and the
  branch-protection reading taken at merge time →
  `claudedocs/refs/index-store-claims-accuracy.md`. ⚠ Do not quote that
  protection reading — `CLAUDE.md` owns the current state and it has moved since.

- 🔴 **A SPLICE-BASED EDIT TO A TEST FILE IS A COVERAGE-DELETING OPERATION.** Replacing one
  test by cutting between two string anchors silently removed a 4-param test that sat
  between them. The suite went 741 → 739 while the commit message said it ADDED a test, and
  the `printf`→`echo` mutant on the ABSENT arm — the actual silent-clobber route — then
  survived the whole repo. **Check the COLLECTED COUNT across a test-file edit, never just
  read the diff**; a deletion inside a large file looks like context.
- 🔴 **FOUR OF SIX ROUNDS FOUND A DEFECT THE PREVIOUS ROUND'S FIX INTRODUCED.** Each fix
  picked a delimiter and the next round's input contained it: space (0x20) → TAB (0x09) →
  newline (the list's own separator). **The escape from that regress was BOUNDING THE INPUT
  DOMAIN, not hardening the parser one more time.** When a fix is "handle this character
  too", ask whether the domain can be constrained instead — 0 of 373 live entries needed any
  of it.
- 🔴 **MY OWN MUTATION TESTING MISSED TWO OF MY DEFECTS, AND THE TELL WAS AN `or`.** The
  newline test asserted `SCOPE/na in stderr **or** "me.md" in stderr`; that `or` made
  deleting the `\.md$` anchor invisible. **An assertion with an `or` across two observations
  is one assertion weaker than it reads.**
- 🔴 **A COMMENT THAT EXPLAINS WHY A CLAUSE IS REDUNDANT IS A CLAIM — AND MINE WAS WRONG.**
  I deleted the `\.md$` anchor arguing "`find` only emits `*.md`". True of BASENAMES; the
  grep runs on LINES, and a newline split produces a line that is not a basename. Measured
  after restoring: the refusal names 2 halves instead of 1.
- 🔴 **`gh pr view` IS THE AUTHORITY ON CONFLICTS, AND MY LOCAL CHECK LIED.** A local
  test-merge came back clean because the integration branch ALREADY contained my resolution
  — I was testing the resolved tree against main, not the PR branch against main. GitHub
  said `CONFLICTING/DIRTY` and was right.
- 🔴 **`rerere` auto-applied a resolution and I verified it by hand anyway** — union of both
  sides, both justification comments present, both files parsing. main had added `tmux` to
  `REQUIRED_TOOLS`/`gateTools` for EXACTLY the reason this PR added `dash`.
- 🔴 **A TEST THAT SKIPS ITSELF IS WORSE THAN NO TEST, and 5 of mine did.** Without `dash`
  on PATH the whole dash class skipped silently. Fixed by adding it to `REQUIRED_TOOLS` +
  `gateTools` (same argument the `zsh` entry already carried, one layer out) and by making
  the helper FAIL rather than skip.
- 🔴 **THE PIPE TRAP FIRED TWICE MORE.** `gh pr merge … | tail` printed `MERGE_CMD_RC=0`
  over a refusal, and a later `gh pr merge` returned **rc 1** for a failure that was ONLY
  about deleting a local branch held by a worktree — the remote merge had succeeded.
  **Read the message, then verify the outcome by content.**
- ⚠ **A `grep` that finds nothing exits 1**, so a trailing `grep -c` in a verification
  script makes the whole run "fail". Two of this session's background jobs reported failure
  for exactly that reason while every underlying check was green.
- **`--help` claimed "cannot drift" TWICE and was wrong both times** — first a `sed` line
  range, then an `awk` stopping at the first non-comment line (which a BLANK line is: 61 →
  31 lines, `--allow-overwrite` gone). Now tested, because a claim that has been wrong twice
  with no test is a claim nobody is checking.
- **Two independent blind lenses beat one auditor run twice over.** Round 3's lenses found
  the same TAB defect by different routes without being told what the other sought; that
  agreement was the round's strongest evidence.
- 🔴 **A THEORY THAT EXPLAINS THE FAILURE IS NOT EVIDENCE FOR IT — and this one was
  arithmetically impossible the whole time.** `gc --auto` needs ~6700 loose objects;
  `_mkrepo` leaves 3. One `git config --get gc.auto` plus one `count-objects -v` would
  have killed it before any loop ran. **Cost the theory a round; cost the refutation two
  commands.** Check whether a mechanism CAN fire before measuring whether it DID.
- 🔴 **A 0/N ONLY MEANS SOMETHING AFTER A POSITIVE CONTROL.** The two-arm loop returned
  0/80 in BOTH arms — which, without proving the probe could see anything at all, is
  indistinguishable from a harness wired to nothing. The control (spawn a process in the
  repo, watch `live_cotenants` return a pid) is what made the zero a reading.
- 🔴 **A DIAGNOSTIC NOBODY TESTED IS WORTH NOTHING AT THE MOMENT IT FIRES** — it only ever
  runs inside an already-failing assertion. Two tests plus a mutation matrix (drop
  cwd+cmdline → KILLED 2; let a dead pid raise instead of degrading → KILLED 1), because a
  helper that throws inside an assertion message REPLACES the real failure with its own.
- 🔴 **A BULLET THAT NAMES ITS OWN CLOSING CONDITION STILL NEEDS SOMEBODY TO COME BACK.**
  The index held `"stays OPEN only until #1222 merges"`; #1222 merged five days earlier and
  the bullet still read OPEN. Nothing closes these automatically — the next writer is the
  only moment anyone looks, which is exactly why the protocol makes you re-check before
  appending.
- 🔴 **A MARKER WITH A PARENTHETICAL DECLARES NOTHING.** `RESOLVED be3084f0
  (homelab-infra):` fails the grammar, so a genuine closure showed no badge and read as
  unfinished. `--validate` reports these as *attempted marker did not parse* — an advisory
  that exits 0, so it is only seen by someone who reads past the verdict.
- 🔴 **`clawgate_handoff.sh resolve` EXIT 5 CANNOT DISTINGUISH "THIS SESSION TOUCHED NO
  TASK" FROM "THE SESSION ID IS WRONG"** — an unknown session returns `200 {"tasks":[]}`,
  not 404, so the empty array is the same observation for both. It answered that way on
  SIX handoffs in this effort, each behind a positive control proving the board was
  reachable, and each time nothing was written and no task created. Stated in `## Goal`
  and `## State now` above; the six merged bullets and the one `exit 6` variant →
  `claudedocs/refs/index-store-claims-accuracy.md`.

- ⚠ **CARRIED FORWARD from the old `State now` so this REPLACE cannot drop them** (the two
  the merge flagged as durable-and-orphaned): the original rank 1 closed as **devrc#1304**,
  squash **c5a445d8** — the number survives in Open investigations, the sha did not. And the
  laptop's built-source drift was measured at **24 commits behind** when it was finally fixed,
  against the **18** this doc had recorded five days earlier: 🔴 **a count in a handoff AGES —
  re-measure it before acting on it, never quote the stored number.**
- 🔴 **`handoff_doc.py` HANGS — CPU-SPINS FOREVER — ON BACKTICK-QUOTED TOKENS SEPARATED BY
  `/` INSIDE `**bold**`.** MINIMAL REPRO, measured 2026-09-10: a delta whose only content is
  ``- **`#1449`/`#1465`/`#1470`/`#1475`** → …`` never returns (killed at 540s, 0 bytes of
  output, process state `RN` with NO child processes — so it is pure-Python backtracking, not
  git, `gh`, network or stdin). The SAME line with the slashes flattened to
  `- **#1449, #1465, #1470, #1475** → …` completes in under a second. The base doc is NOT the
  trigger — a trivial delta against this same 64 KB doc returns fine — so it is the DELTA's
  text, and any session can hit it while writing an ordinary PR list. Workaround: do not put
  slash-separated backticked tokens inside bold. 🔴 It produces NO output and NO error, so it
  reads as a wedged terminal rather than a defect; bisect the delta by section, then by line.

- 🔴 **A BULLET-LEVEL SURVIVAL CHECK IS STRUCTURALLY BLIND TO A WHOLE SECTION VANISHING
  — COUNT THE HEADINGS.** Pruning this doc, the last bullet of an evicted class sat
  immediately before `## How to verify`, so discarding that block took the entire
  section with it. The survival check reported **93 → 83 bullets, 11 missing, all 11
  accounted for, 0 unexplained losses** — a clean-looking pass over a real loss, because
  it only ever compared bullets. Only an H2 COUNT caught it. Verify a prune on
  STRUCTURE (H2/H3/fence/ranked-item counts) as well as on content, and split any
  trailing section off BEFORE block-splitting the one you are cutting.
- ⚠ **`ship.sh` NO LONGER NEEDS THE `REMOTE_SSH` OVERRIDE — measured 2026-09-10.** It
  probes `192.168.50.155`, prints `did not answer — falling back to zach@10.42.0.100`,
  and converges. Earlier sessions (this one included) carried
  `REMOTE_SSH=zach@10.42.0.100` as a workaround; that is obsolete and should not be
  inherited. `605b29ac`/#1439 landed it — ⚠ and that PR merged with **10 of its own
  tests red** on the tier a merge gates on, fixed upstream later the same day.

- 🔴 **A PROBE WHOSE TWO CANDIDATE MECHANISMS COINCIDE IN THE SAMPLE CANNOT SEPARATE
  THEM — and it will confidently name the wrong one.** `cairn create` was run against a
  scope that was absent AND non-allowlisted, on a pod whose allowlist enumerated
  exactly the scopes on disk. The `rc 6` came from the allowlist check, which returns
  BEFORE the index is loaded; it was credited to an index walk, and that false
  mechanism then shipped into two skills, three `claudedocs/` and four test
  docstrings. The discriminating control was available and never run: **allowlist a
  scope, do NOT seed it, create → 201.** RULES: *an EMPTY RESULT cannot distinguish
  two mechanisms — go find the step that differs.*
- 🔴 **A CASE-SENSITIVE GREP QUOTED AS A CLEAN SWEEP.** `grep -ln 'There is no CREATE
  route\|no CREATE route'` (both spellings retracted) → **0 files**, reported as
  "only the skill had it".
  Case-insensitively: **6**. Through the repo's own normalising scanner: **10
  occurrences in 8 files**, one straddling a newline inside a docstring where no
  line-based grep can ever see it. Four successive sweeps each found a spelling the
  previous one's phrasing missed — mechanism → one conclusion phrase → two more.
  **A string needle cannot close a claim that has no canonical wording.**
- 🔴 **AN UNSUBJECTED NEEDLE FIRES ON TRUE WRITING, AND IT HAPPENED TWICE IN ONE PR.**
  `"remains an operator step"` carries no subject; the repo holds **9 true occurrences
  of "operator step" across 7 unrelated files**, each one word from turning a repo-wide
  gate red. My own probe fired **7 of 8** true sentences — including on a needle I had
  already "narrowed", because *"The first entry remains an operator step for the OSS
  multi-instance store"* is TRUE. **Bind every needle to its subject, and verify as a
  PAIR: a false-positive probe over TRUE sentences AND a mutation battery.** A gate
  that cannot go green is the mirror of one that cannot go red.
- 🔴 **`rerere` REPLAYED A RESOLUTION FROM A DIFFERENT MERGE — verify by COUNTING, not
  by reading.** It auto-applied the union resolution recorded on an integration branch
  onto the real PR merge. It was correct, established by counting test defs across all
  three trees: base 688, PR +16, `#1508` +0, merged 704. The rr-cache lives in the
  **common** git dir, so a resolution recorded in any worktree is repo-global.
- 🔴 **"INHERITED RED" IS NOT A REUSABLE VERDICT — run the control every time.** One red
  was inherited (`main` failed it too, naming a different file). The next red looked
  identical and was NOT: `main` PASSED it. The real cause was staleness — the branch sat
  **10 commits behind**, and a commit in that window added the ledger row the test
  demanded. Merging current `main` fixed it. **Read the failing test's name, ask whether
  the diff can reach it, then check how far behind the base is.**
- 🔴 **zsh ATE A GIT PATHSPEC AND PRODUCED A PLAUSIBLE NUMBER.** `$MB:scripts/...` —
  `:s` is a history modifier, so `git show` received only the sha and printed a COMMIT
  MESSAGE. The count read `base defs: 0`, and the arithmetic built on it
  ("PR added 704") looked like data rather than an error. **Brace it: `${MB}:`.**
- ⚠ **MY OWN CI WATCHER COULD NOT GO GREEN.** It required no `pending` in the status
  list, but every context posts BOTH a pending and a final row, so the condition never
  cleared and it reported "unresolved" over a resolved verdict. Validate the instrument
  before reading its verdict — including one you just wrote.
- 🔴 **"DO WE NEED IT" RETIRED THE REMAINING WORK, and the operator had to ask.** Rank 1
  was presented as a next step with a three-step runbook. Measured on the question:
  nothing has read either scope, neither repo is checked out on this host, the entries
  have not moved since 2026-09-02/03, and most references to them are this effort
  discussing itself. The cost was a SOPS edit plus a pod restart whose failure mode is
  *the store stays down*. **"It is broken" was slid into "it needs fixing" without ever
  asking whether anything depended on it.**
- ⚠ **A SUBAGENT CAUGHT A PERMANENTLY-RED GATE IN ITS OWN GUARD before shipping it** —
  its first cut classified every refusal row as the create path's 404, so a *correct*
  third row (`rc 9 already-exists`, which `_entry_exists` answers at 412) would have
  failed the guard against a doc that had just improved.

## How to verify

```bash
# every closure landed, BY CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc show origin/main:claude/skills/cairn/SKILL.md | grep -c 'cairn create'              # 4
git -C ~/workspace/devrc cat-file -e origin/main:scripts/tests/test_cairn_operator_surface_probe.py && echo ok
git -C ~/workspace/devrc show origin/main:scripts/lib/service_recon.py | grep -c governing_policy            # 5

# the mechanism itself — the control the first probe never ran
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_subsystem_store_api.py -q -p no:cacheprovider \
  -k 'FIRST_entry_creates_the_directory or OUTSIDE_the_allowlist_is_the_SAME_404'     # 2 passed

# the needles are bound to a subject: TRUE sentences must NOT fire
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_subsystem_store_api.py -q -p no:cacheprovider \
  -k TestByteIdentityVerifier                                                          # 35 passed

# the two stranded entries still have an off-machine copy (they were single-copy)
md5sum ~/rescue/subsystem-stranded-2026-09-12/*.md
ssh zach@10.42.0.100 'md5sum ~/rescue/subsystem-stranded-2026-09-12/*.md'              # must match

# the standing reminder for rank 1 — an accurate report, not an alarm
cairn doctor 2>&1 | grep -E 'token-scopes|frozen-mirror'
```
