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

**RANK 1 (the `seed.sh` hard-guard) IS STILL IN FLIGHT AS `devrc#1304`, NOT MERGED.**
Branch `fix/seed-refuse-pod-overwrite`, head `d7c4c266`. **Round 3 ran and was NOT CLEAN, so
the ladder continues and a ROUND 4 delta audit is owed after the fixes below.** No gate tier
has been run on this branch and no merge was attempted.

- **What it does** (unchanged). A PRE-FLIGHT before the tar: for exactly the paths in
  `$staged_list`, ask the pod whether it holds different bytes; refuse **exit 8, pushing
  nothing**, and name them. `--allow-overwrite` proceeds and still prints what it replaced.
  The header's `🔴 THE LOCAL STORE IS AUTHORITATIVE` — false since the cutover — is corrected.
- **Why bytes, not mtime** (unchanged): two copies cannot be ordered, so ANY difference means a
  derivative would overwrite the authority. No clock needed.
- **Round 1 found a 🔴 in the guard itself** — the probe emitted a line only for files the pod
  HAS, so "the pod holds none of them" and "the probe never ran" were one observation. Fixed:
  every path is answered (hash / `ABSENT` / `UNREADABLE`), a short reply is exit 9.
- **Round 2 refuted a round-1 claim in both halves** — `-I{}` does NOT disable xargs's input
  quote parsing, and `awk '{print $2" "$1}'` truncated the join key at the first blank, turning
  the guard into a confident FALSE REFUSAL. Fixed: `-d '\n'` both sides, TAB-separated key,
  `join -t <tab>`.
- 🔴 **ROUND 3 (this session) IS NOT CLEAN — both lenses reported, both returned findings.**
  Dispatched blind against `ec16cce8..d7c4c266`. Full evidence in the Open-investigations
  blocks below.
- **Lens 1 (shell semantics) — five findings.** Headline: the TAB key round 2 introduced is
  itself undelimited, and the pod's `/bin/sh` is **dash**, whose `echo` interprets `\t`/`\n`/`\c`
  while every test runs under bash.
- 🔴 **Lens 2 (are the new tests real?) — round 2's own claims all held.** All four verified
  TRUE and re-run, not asserted: all **three** new tests are RED at base (claim 1 *understated*
  it — it called the DIFFERS test an anti-blindness assertion, and it is also a genuine
  regression test); the mutation matrix (a)–(d) all **KILLED, each by the named guard's own
  message**; **723 collected / 723 passed** across two independent runs (357s, 385s);
  pre-existing coverage intact (7 tests, all four behaviours green). This is the first round
  whose predecessor's claims survived scrutiny.
- 🔴 **But lens 2 found TWO SURVIVING MUTANTS — new guards with no test.** `MUTANT F` (make the
  local hash side silently drop quoted paths while still exiting 0, leaving `answered==staged`)
  **survived all 46 seed tests**; the QUOTE test asserts only that the run did not crash, so
  nothing pins that the quoted path was ever in the compared population. `MUTANT H` (revert this
  delta's `|| [ $? -eq 1 ]` to the exact `|| :` its own comment condemns) **survived all 46**.
- **Claims measured FALSE this round:** the commit's claim that the key survives any path
  (false for TAB and for a leading space — reproduced INDEPENDENTLY by both lenses), that
  `UNREADABLE` always lands in the clobber set (false), and that the `--help` `awk` "cannot
  drift as the header changes" (false — one blank line truncates it).

**Corrections to this doc, both measured 2026-09-05:**
- 🔴 **Rank 8 is CLOSED, not open.** `test_a_body_file_written_by_a_heredoc_on_the_same_line_is_read`
  **passes** on `origin/main` (`1 passed in 0.29s`). `8c27c5cf` (#1303) fixed it — "a stale file
  at the `--body-file` path shadowed the heredoc about to overwrite it — the verdict was a
  property of the HOST." The dev-host tier's known-red is gone; it is no longer a reason to
  discount a red there.
- **Both Tekton checks are GREEN on `d7c4c266`** — `/repos/.../commits/<sha>/status` reports
  `state: success` for `tekton/devrc-pytests` and `tekton/devrc-nodetests`. ⚠ `strict` is false,
  so that is a claim about the PR BRANCH and says nothing about the merged tree.
- The base clone was **1 commit behind** `origin/main` at session start; fast-forwarded to
  `f887e958` before any write. `main` has moved 2 commits past the PR's branch point
  (`d9f0836c` tmux-restore, `f887e958` this doc), both touching files disjoint from the PR —
  but `d9f0836c` adds a new test file, so the merged tree's per-target floors will move.

🔴 **NOT VERIFIED, and not claimed:**
- **Neither gate tier has run on `d7c4c266`** — not `scripts/gate.sh --tier both`, not the two
  sandbox derivations, not on the merged tree.
- **Nothing has touched the LIVE pod.** Every test drives a fake `kubectl` whose `exec` rewrites
  `/data` to a temp dir and runs the command locally under **bash** — a harness that
  structurally cannot tell "works on the pod" from "works on this host", which is exactly the
  gap round 3 fell into.
- The round-3 lens-1 auditor measured the pod's toolchain by running `python:3.12-slim` (the
  `Dockerfile`'s own `FROM`) under local docker. **It did NOT verify the deployed pod's image
  matches that Dockerfile**; if the tag drifted, the `/bin/sh = dash` finding does not transfer.

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

### `--help` still drifts, and has ZERO test coverage
- **Symptom + exact repro:** insert one blank line into `seed.sh`'s leading comment header and
  run `bash seed.sh --help`.
- **Observed (with values):** unmodified → **61 lines**, ends on a complete paragraph, contains
  `allow-overwrite`. One blank line inserted at line 33 → **31 lines**, `allow-overwrite`
  occurrences **0**. The `awk`'s `{exit}` fires on the first non-`#` line, and a blank line is
  one. That is verbatim the defect `d7c4c266` exists to fix, silently reintroducible.
- **Ruled out:** "the line-range rot was the whole bug and the `awk` closes it" — the `awk`
  closes the GROWTH shape and leaves the BLANK-LINE shape wide open. via: measurement
- **Ruled out:** "a test guards the fix" — `grep` over `test_subsystem_store_api.py` finds no
  `seed.sh --help` assertion at all; the only `--help` test belongs to
  `verify-byte-identity.sh`. The commit's headline claim is unguarded. via: measurement
- **Leading hypothesis:** `/^#/{...} /^$/{next} {exit}` (or match `^#|^$` and stop only on a
  real non-comment line), plus the missing test asserting `--help` contains `allow-overwrite`
  and ends on a terminator.
- **Next probe:** fold into the same fix commit as the block above, then round 4.

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

## Next steps (ranked)

1. **Fix round 3's findings and run ROUND 4 on `devrc#1304`.** Both lenses reported; both
   returned findings (blocks above). Round 2's own claims all HELD — the new work is (i) the
   host/pod escaping divergence, (ii) the TAB/leading-space key, (iii) `--help` blank-line
   drift, (iv) three missing tests for guards whose mutants survived. Fix once, as one batch,
   then re-audit the DELTA — a round returning findings cannot end the ladder. Decide
   minimal-vs-structural using the Leading hypothesis in the first block.
   forcing: gate — this repo's audit gate is the only pre-merge review, and three of three
   rounds have each found real defects.

2. **Gate `devrc#1304` and merge.** `scripts/gate.sh --tier both` AND
   `nix build .#checks.x86_64-linux.{pytests,nodetests}` ONE AT A TIME, on the MERGED tree, with
   the base sha named in the claim. ⚠ Read the runners' `RESULT:` lines, never a piped exit
   code. ⚠ Any red measured above ~load 20 on this box needs a control first. Note the
   dev-host tier's known-red is GONE (#1303), so a red there now means something.
   forcing: gate — nothing else gates a merge in this repo; `main` is protected in name only.

3. **Decide `cairn-cutover.py` P3.** Either pass `--allow-overwrite` at
   `cairn-cutover.py:1379-1382` or declare P3 dead post-cutover. Its shippable set is
   ADD + SUPERSEDES + MERGED, and SUPERSEDES/MERGED are by definition entries whose pod bytes
   differ — so the pre-flight refuses and P3 cannot complete.
   forcing: regression — a shipped code path that can never complete, made worse by guidance
   that is false for its only programmatic caller.

4. **Fix the opencode blindness in `scripts/lib/clawgate_handoff.sh`.** Diagnosed and recorded
   (squash `13775144`), NOT fixed. It reads only `CLAUDE_CODE_SESSION_ID`;
   `grep -c OPENCODE_SESSION_ID` is **0**. Detached opencode ⇒ exit 3 forever; NESTED opencode
   inherits the outer Claude session's id ⇒ exit 0 with **another session's tasks**.
   forcing: regression — the nested path silently misattributes today.

5. **Run `scripts/ship.sh`.** Still never run in this effort. The **laptop is UNVERIFIED**, so a
   session there may still be told to write new entries into the dead mirror. Read every
   per-host line, not the final verdict.
   forcing: regression — a stale prescription on one host reintroduces the defect this effort
   closed.

6. **Decide the token allowlist for the 2 remaining local-only entries**
   (`civitai-app-requests/app-requests.md`, `civitai-developer-docs/apps.md`). Widening it means
   editing the k8s secret and deleting the pod — an access-control change and an outage window.
   forcing: none

7. **Fix `devrc#1170`'s 🟡5 and 🟡6.** Still never started. 🟡5: **re-measured 2026-09-04, 0**
   occurrences of `policy:` in `service_recon.py` on `origin/main`, so it stands exactly as
   written — `subsystem-index/SKILL.md:148` tells the caller to read a policy nobody names.
   🟡6: `--template` over an EXISTING entry prints the first-ever-file template and exits 0
   silently, destroying an `OPEN:` bullet.
   forcing: none

8. **~~`main` is RED on `test_clawgate_task_interview_guard.py`~~ — CLOSED, do not re-open.**
   Re-measured 2026-09-05: it PASSES on `origin/main` (`1 passed in 0.29s`). `8c27c5cf` (#1303)
   fixed it. Kept as a numbered entry only so the ranks above keep their identity for
   `claim-work --slug-for`.
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
- 🔴 **`{exit}` on "the first non-comment line" treats a BLANK line as code.** The `--help`
  rewrite traded a rotting line-range for a rule that a single blank line in the header
  silently truncates — measured 61 → 31 lines with `allow-overwrite` gone. A rule that "cannot
  drift" should be tested; there is no `seed.sh --help` test at all.
- **Two of three round-3 findings I re-measured MYSELF rather than taking the auditor's word,
  and both held exactly.** The rules require re-verifying a subagent's numbers; here they were
  right. That is worth recording precisely because the previous two rounds each carried a wrong
  datum from a subagent.
- 🔴 **TWO INDEPENDENT LENSES FOUND THE SAME TAB DEFECT BY DIFFERENT ROUTES** — lens 1 by
  fuzzing the real script, lens 2 by hand at unmutated HEAD. Neither was told what the other was
  looking for. That agreement is the strongest evidence in this round, and it is also the
  argument for splitting a round into lenses rather than running one auditor twice.
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
- **No clawgate task recorded, again.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session, positive control confirming the board was reachable (2 links for another
  session). A wrong session id answers 200/empty exactly like a session that touched nothing,
  so this is **not** a clean reading; no field was written and none was created.
- ⚠ **Environment, unchanged:** the shared `devrc` clone still holds another session's
  uncommitted WIP (`nix/programs/alacritty/default.nix`, `output.txt`,
  `nix/system/apply-nebula-relay.sh`, `nix/system/check-nebula-relays.sh`,
  `scripts/diagnose-nix-disk.sh`). Nothing here touched them. This session worked on `main` for
  reads only and did every write in the worktree `~/workspace/devrc-ho-r3`.

## How to verify

```bash
# the pod is dash and its `echo` eats escapes — no cluster needed
grep -n '^FROM' ~/workspace/devrc/scripts/subsystem-store-api/Dockerfile      # python:3.12-slim
dash -c 'echo "ABSENT  $1"' _ 'sc/tab\there.md' | cat -A                      # real TAB
bash -c 'echo "ABSENT  $1"' _ 'sc/tab\there.md' | cat -A                      # literal \t
dash -c 'printf "ABSENT  %s\n" "$1"' _ 'sc/tab\there.md' | cat -A             # the fix

# --help drifts on a blank line
S=$(mktemp); git -C ~/workspace/devrc show d7c4c266:scripts/subsystem-store-api/seed.sh > $S
bash $S --help | wc -l                                     # 61
awk 'NR==33{print ""} {print}' $S > $S.b
bash $S.b --help | wc -l                                   # 31
bash $S.b --help | grep -c 'allow-overwrite'               # 0

# rank 8 is closed, not open
nix develop ~/workspace/devrc -c python3 -m pytest \
  scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py -q \
  -k test_a_body_file_written_by_a_heredoc_on_the_same_line_is_read   # 1 passed

# the PR head's checks, per-sha (never `gh pr checks`)
gh api /repos/innovation-upstream/devrc/commits/d7c4c266a20a99ba5e33df08f1fcd34b32122874/status \
  --jq '.state, [.statuses[].context]'
```
