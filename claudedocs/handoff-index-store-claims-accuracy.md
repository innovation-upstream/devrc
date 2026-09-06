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

**RANK 1 REMAINS CLOSED** (`devrc#1304` → squash `c5a445d8`). Since the last update this
session closed the three items that were outstanding from it, and **refuted its own
leading hypothesis about the flake**.

- 🔴 **`devrc#1340` → squash `6ae9fef9`** — the co-tenant precondition now NAMES the
  intruder (cwd + cmdline) when it fails. **This is a diagnostic, NOT a fix**: the
  `gc --auto` theory this doc carried is REFUTED (see the block below), so no remedy was
  shipped for a mechanism that does not exist. Gated both tiers on the merged tree
  (`fe050a11`, base `88f1bda4`): dev-host 21876/21873/**0 failed** + node 1449/1449;
  sandbox `pytests` 21869/21866/**0 failed** + `nodetests` 1449/1449. Verified on
  `origin/main` by content. 116 tests in that file, was 114.
- **`scripts/ship.sh` RUN — both hosts converged and VERIFIED at `88f1bda4`** (workbench
  from `c5a445d8`, laptop from `3f4d9c0f`, closing its 5-commit gap). Read per-host, not
  the verdict: no skips; artefacts 593/535 resolve with **0 dangling**, 409/394
  repo-sourced with **0 stale**. This was the doc's old rank 4 and it is done.
- **The subsystem index was written** — the `/handoff` step 4 that two earlier runs in this
  session skipped. It also repaid the detour: it closed the PREVIOUS session's `OPEN:` for
  #1304 (`RESOLVED c5a445d8`), closed one whose own stated condition — *"stays OPEN only
  until #1222 merges"* — had been met **five days** earlier (`RESOLVED 7d9da8f5`, verified
  by content on `main`, not by the merge alone), and repaired a bullet whose
  `RESOLVED be3084f0 (homelab-infra):` marker carried a parenthetical that breaks the
  grammar, so a real closure was declaring nothing and showing no badge.
  `cairn validate --scope devrc`: **30 of 30 parse, 0 malformed**.

🔴 **NEW, and `ship.sh` structurally CANNOT fix it — `drift-check.sh` rc 17.** The laptop's
`homelab-talos/containers/clawgate` **built-source subtree is 18 commits behind** its own
upstream (repo-wide 223 behind). `nix/pkgs` builds `clawgatectl` from that TREE, so the
laptop's binary is stale code whatever version string it reports. `ship.sh` is scoped to
`~/workspace/devrc` and will never touch it. Now rank 2.

🔴 **NOT VERIFIED, and not claimed:**
- **The flake is NOT fixed.** #1340 makes the next occurrence diagnosable; it did not
  recur on the one sandbox run after the change, and **one green run of a
  non-deterministic failure is not evidence of a fix.**
- **Nothing has touched the LIVE pod** across all six audit rounds and this follow-up. The
  `/bin/sh = dash` premise the whole `seed.sh` guard rests on is measured against the
  `Dockerfile`'s own base image under local docker, never the deployed one.
- **`main` moved between gate and merge, twice.** #1304 gated at `f0b9c474`/merged at
  `eb68d7c1`; #1340 gated at `88f1bda4`/merged at `e8143452`. Intervening commits touched
  none of the changed files — but that is REASONED, not measured, and with `strict: false`
  and `main` moving every few minutes it is not reachable to close.

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

### `test_git_repo_isolation.py::test_live_cotenants_sees_another_process_in_the_repo` is LOAD-FLAKY on the sandbox tier
- **Symptom + exact repro:** the sandbox `pytests` derivation goes red with `failed=1` while
  the dev-host tier on the same tree is green. Re-running the identical derivation passes.
  `nix build <repo>#checks.x86_64-linux.pytests --no-link --print-build-logs`
- **Observed (with values):** `scripts/tests/test_git_repo_isolation.py:1496` —
  `assert live_cotenants([git_dir]) == []` returned `['126220:git']` on a **brand-new tmp
  repo**, i.e. a live `git` process whose cwd is inside a repo created microseconds earlier.
  Run 1: `SANDBOX_PYTESTS_NIX_RC=1`, `RESULT: FAIL (exit=1)`, 21867 collected / 21863 passed
  / **1 failed**. Run 2, byte-identical derivation: `RC=0`, `RESULT: PASS`, 21867 / 21864 /
  **0 failed**.
- **Ruled out:** "devrc#1304 caused it" — the file is **byte-identical to `origin/main`**
  (`git diff --stat origin/main HEAD -- <file>` empty) and the PR touches four files, none
  of them this one. via: command
- **Ruled out:** "it is deterministic" — same derivation, 1 red / 1 green. via: measurement
- **Ruled out:** "it reproduces on the dev host" — 10 consecutive runs of that single test,
  unloaded, all passed in 0.63–1.34s. Absence at low load is NOT evidence of absence.
  via: measurement
- **Leading hypothesis, NOT confirmed:** `_mkrepo` (`:250-258`) runs `git init` / `add` /
  `commit` via `subprocess.run`, which waits only for the PARENT. `git commit` can fork a
  detached `git gc --auto` whose cwd is the new repo; the co-tenant scan then sees it. Load
  widens the window, which would explain dev-host-green / sandbox-red. A theory that
  explains the failure is not evidence for it — this was never reproduced.
- **Next probe:** the discriminating one is to make `_mkrepo` deterministic rather than to
  re-run: `git -c gc.auto=0 …` on all three commands (or `git init` with
  `core.logAllRefUpdates=false` + an explicit `gc.auto=0` in `_env()`), then re-run the
  sandbox tier under deliberate load. If the flake survives that, the gc theory is wrong and
  the next suspect is the fsmonitor/credential helper. **Do not "fix" it by re-running** —
  a flaky gate trains everyone to click through.

### 🔴 REFUTED — the `gc --auto` theory for the co-tenant flake (supersedes the block above)
- **Symptom + exact repro:** unchanged — the sandbox `pytests` derivation reds with
  `live_cotenants(...)` returning a `git` process on a brand-new tmp repo, and a re-run of
  the byte-identical derivation passes.
- **Observed (with values):** `gc.auto` is unset, i.e. the default **6700** loose objects;
  `_mkrepo` leaves **3** (`git count-objects -v` → `count: 3 size: 12 in-pack: 0`). The
  threshold is unreachable by construction, so the commit CANNOT fork a `gc --auto`.
  Two-arm loop, 80 iterations each at load 31.6 — as-shipped and with `gc.auto=0` forced:
  **0 hits in both arms**.
- **Ruled out:** the `gc --auto` mechanism this doc previously named as the leading
  hypothesis. Refuted twice over: arithmetically (3 objects vs a 6700 threshold) and
  empirically (0/80 in the arm that should show it). via: measurement
- **Ruled out:** "the 0/80 might be a probe wired to nothing" — a positive control ran
  FIRST: spawning a process with cwd in the repo made `live_cotenants` return
  `['<pid>:python3.12']`, and the same harness returned `[]` before the spawn. The probe
  demonstrably sees a co-tenant, so the zero is a real reading. via: measurement
- **Leading hypothesis:** NONE — and that is the honest state. The failure was observed
  once, in the sandbox tier, under `pytest -n 4 --dist loadfile`. `live_cotenants` matches
  on a process whose **cwd** is inside the work tree, and nothing yet explains how a `git`
  process acquired a cwd inside a just-created per-test tmp repo.
- **Next probe:** do NOT re-derive `gc --auto`. Wait for the next red and read the message
  #1340 added — it prints the intruder's `cwd=` and `cmdline=`, which is what separates an
  xdist sibling from a stray host process from one of our own children. `/proc` for a
  transient process is gone by the time anyone reads the log, which is why the capture had
  to move into the assertion.

## Next steps (ranked)

🔴 **Renumbered.** Old rank 4 (`ship.sh`) is DONE and old rank 8 was already closed, so the
list is re-based. No `claim-work` claim was live against this doc when it was rewritten
(`claim-work --list` showed none for this slug), so no claim was re-pointed.

1. **The co-tenant flake is still UNFIXED — diagnosable, not diagnosed.** `devrc`,
   `scripts/tests/test_git_repo_isolation.py`. Do not close it by re-running; do not
   re-derive `gc --auto`. Wait for the next sandbox red and read the `cwd=`/`cmdline=` the
   assertion now prints.
   forcing: gate — it reds the sandbox tier non-deterministically, and the only reason
   #1304 merged through it was a human re-running and reading both results.

2. **The laptop's `clawgatectl` is built from source 18 commits stale** (`drift-check.sh`
   rc 17). Fix on that host: `git -C ~/workspace/homelab-talos pull --ff-only` then a
   home-manager switch. 🔴 Not a devrc change and `ship.sh` will never do it.
   forcing: regression — a deployed binary whose code is not the code its version string
   implies, on a host that looks converged by every other measure.

3. **Decide `cairn-cutover.py` P3.** It invokes `seed.sh` WITHOUT `--allow-overwrite`
   (`cairn-cutover.py:1379-1382`) over an ADD + SUPERSEDES + MERGED set, where
   SUPERSEDES/MERGED are BY DEFINITION entries whose pod bytes differ. 🔴 #1304 made this
   WORSE: the new NAME-CHECK is a SECOND refusal P3 can hit. Either pass the flag or
   declare P3 dead post-cutover.
   forcing: regression — a shipped code path that can never complete.

4. **Fix the opencode blindness in `scripts/lib/clawgate_handoff.sh`.** Diagnosed (squash
   `13775144`), NOT fixed. It reads only `CLAUDE_CODE_SESSION_ID`;
   `grep -c OPENCODE_SESSION_ID` is **0**. Detached opencode ⇒ exit 3 forever; NESTED
   opencode inherits the outer Claude session's id ⇒ exit 0 with **another session's
   tasks**.
   forcing: regression — the nested path silently misattributes today.

5. **Verify the dash premise against the DEPLOYED pod image**, read-only. The whole
   `seed.sh` guard rests on `/bin/sh` being dash there; that was measured against the
   `Dockerfile`'s `FROM`, never the running pod.
   forcing: none

6. **Decide the token allowlist for the 2 remaining local-only entries**
   (`civitai-app-requests`, `civitai-developer-docs`). `cairn create` answers `not-found`;
   neither scope is in this token's allowlist. Widening it edits the k8s secret and needs a
   pod delete (the token file is read ONCE at startup).
   forcing: none

7. **Fix `devrc#1170`'s 🟡5 and 🟡6.** Still never started. 🟡5: re-measured 2026-09-04,
   **0** occurrences of `policy:` in `service_recon.py` on `origin/main`. 🟡6: `--template`
   over an EXISTING entry prints the first-ever-file template and exits 0 silently,
   destroying an `OPEN:` bullet.
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

- **Two corrections to this doc, measured 2026-09-05, recorded HERE so a future `State now`
  replace cannot drop them.** (1) The old rank 8 — *"`main` is RED on
  `test_clawgate_task_interview_guard.py`"* — is **CLOSED**: it passes on `origin/main`
  (`1 passed in 0.29s`), fixed by `8c27c5cf` (#1303), "a stale file at the `--body-file`
  path shadowed the heredoc about to overwrite it — the verdict was a property of the HOST".
  The dev-host tier has no known inherited red any more, so a red there now means something.
  (2) Tekton posts on a PR head but `required_status_checks` is **null** and
  `enforce_admins` **false** — re-measured at merge time. Nothing gates; the two-tier local
  run IS the gate.

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
- **No clawgate task recorded.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session, positive control confirming the board was reachable (2 links for a different
  session). A wrong session id answers 200/empty exactly like a session that touched
  nothing, so this is not a clean reading; no field written, none created.
- ⚠ **Environment, unchanged:** the shared `devrc` clone still holds another session's
  uncommitted WIP (`output.txt`, `nix/system/apply-nebula-relay.sh`,
  `nix/system/check-nebula-relays.sh`, `scripts/diagnose-nix-disk.sh`). Untouched. All work
  here was done in worktrees; both have been removed and the base clone fast-forwarded.

- 🔴 **A THEORY THAT EXPLAINS THE FAILURE IS NOT EVIDENCE FOR IT — and this one was
  arithmetically impossible the whole time.** `gc --auto` needs ~6700 loose objects;
  `_mkrepo` leaves 3. One `git config --get gc.auto` plus one `count-objects -v` would
  have killed it before any loop ran. **Cost the theory a round; cost the refutation two
  commands.** Check whether a mechanism CAN fire before measuring whether it DID.
- 🔴 **A 0/N ONLY MEANS SOMETHING AFTER A POSITIVE CONTROL.** The two-arm loop returned
  0/80 in BOTH arms — which, without proving the probe could see anything at all, is
  indistinguishable from a harness wired to nothing. The control (spawn a process in the
  repo, watch `live_cotenants` return a pid) is what made the zero a reading.
- 🔴 **WHEN THE MECHANISM IS UNKNOWN, SHIP THE DIAGNOSTIC, NOT A GUESS.** `gc.auto=0` was
  one line and would have looked like a resolution while the real cause stayed open —
  strictly worse than nothing, because it would have stopped anyone looking. Making the
  failure self-describing is the honest move when you cannot name the cause.
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
- **`gh pr merge` rc is not the merge's verdict.** It returned **1** for a failure that was
  only about deleting a LOCAL branch still held by a worktree — the remote merge had
  already succeeded. Remove the worktree first, and verify by content either way.
- ⚠ **Environment, unchanged:** the shared clone still holds another session's untracked
  WIP (`output.txt`, `nix/system/apply-nebula-relay.sh`, `nix/system/check-nebula-relays.sh`,
  `scripts/diagnose-nix-disk.sh`). Untouched. `drift-check` classifies all 4 as read by no
  nix path, so what was deployed IS `origin/main`.
- **No clawgate task recorded, third time.** `clawgate_handoff.sh resolve` exited **5** — 0
  tasks for this session, positive control confirming the board was reachable (11 links for
  a different session). A wrong session id answers 200/empty exactly like a session that
  touched nothing, so this is not a clean reading; no field written, none created.

## How to verify

```bash
# both merges landed, by CONTENT (a squash is never an ancestor)
git -C ~/workspace/devrc show origin/main:scripts/subsystem-store-api/seed.sh | grep -c 'NAME-CHECK'          # 2
git -C ~/workspace/devrc show origin/main:scripts/tests/test_git_repo_isolation.py | grep -c '_describe_cotenants'  # 7

# the refutation is recorded in the test file itself, not only in a commit message
git -C ~/workspace/devrc show origin/main:scripts/tests/test_git_repo_isolation.py \
  | grep -c 'defaults to 6700 loose objects'                                        # 1

# the arithmetic that refuted it — two commands, no loop needed
d=$(mktemp -d); git init -q "$d" && (cd "$d" && touch f && git add f && git -c user.email=a@b -c user.name=a commit -qm x)
git -C "$d" config --get gc.auto || echo "(unset -> default 6700)"
git -C "$d" count-objects -v | head -2                                              # count: 3

# both hosts converged, and AGREEING on one sha is the claim that matters
bash ~/workspace/devrc/scripts/drift-check.sh 2>&1 | grep -E '^\[(workbench|laptop)\].*(BEHIND|VERIFIED|DRIFT)'

# the index is well-formed after this session's writes
cairn sync && cairn validate --scope devrc 2>&1 | grep -E '^OK|malformed'           # OK — N of N parse
```
