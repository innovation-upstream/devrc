# Handoff: test-tier-hardening — 2026-08-21, resolved 2026-08-22

> 🔴 **READ THIS BLOCK FIRST — the guard-strategy question below is SETTLED, and several
> sections underneath it are now WRONG.** Everything from "Goal" down is preserved as the
> evidence trail that produced the decision, not as current state. Where the two disagree,
> this block wins. Measured 2026-08-22 in standalone clones with `origin` **removed**.

## RESOLUTION — 2026-08-22

### The decision: converge on the ENV-LEVER architecture, close the INTERCEPTOR family

Four guard PRs existed for one incident, in two architectures. The framing this session
inherited was "#683 vs #689"; there were actually four, and one of them was complementary
rather than competing.

| PR | architecture | outcome |
|---|---|---|
| **#683** `fix/test-fixtures-escape-ambient-repo` | strip the 11 env vars that redirect git, + fingerprint detector | 🟢 **MERGED** — `dfd2d203` on `main`, 19:05:55Z |
| **#673** `guard/nogit-test-isolation` | `GIT_CONFIG_GLOBAL` throwaway + `GIT_ALLOW_PROTOCOL=file` | 🟡 **OPEN, complementary** — cannot merge as-is, see the seam below |
| **#676** `guard/no-real-git-writes` | intercept git (`nogit.py`, 1758 lines) | 🔴 **CLOSED** |
| **#689** `fix/no-real-git-remote-in-tests` | intercept git (`norepo.py`, 1358 lines) | 🔴 **CLOSED** |

**Why the interceptor family lost, in one line:** the per-verb positional-repo problem is
not one fix. **14 of git's 181 dispatch names take a repository positionally** by synopsis
alone (`upload-pack`, `receive-pack`, `fetch-pack`, `send-pack`, `upload-archive`, `clone`,
`init`, `init-db`, `archive`, `fetch`, `pull`, `push`, `ls-remote`, `mailsplit`), and on top
sits an open-ended set of destination-bearing *options* across all 181 — #676's own audit
found `collect_extra_dests` covering 4 verbs and missing 3. Every bounded formulation fails:
enumerating verbs buys the next audit round; default-deny makes 167 verbs false-refuse
(permanently red); resolving with real git *is* the measured 8.85× overhead and still cannot
see `git-upload-pack <dir>`. Full reasoning is on the closed PRs, not repeated here.

🔴 **This SUPERSEDES the "Recommendation on record, and it is SPLIT" paragraph further down**,
which said #683's detection half was not stronger than #689's and that #689 should continue
if someone took the positional problem knowingly. Nobody should: the positional problem is
the *cheapest* part of that surface, and it was hiding the rest.

### The three-way gate, base `a34d695d`, standalone clones with `origin` removed

```
control  pristine main            pytest 14689 collected /  0 failed   scripts/tests 687.7s   GATE PASS
main + #683                       pytest 14721 collected /  0 failed   scripts/tests 699.6s   GATE PASS
main + #683 + #673 (hand-merged)  pytest 14766 collected / 39 failed                          GATE FAIL
```

`main + #683` node tier: 1119/1119. #683's positive-control marker printed from **26 of 26**
pytest targets, so it is loaded rather than silently absent. Cost: **+1.7%** wall clock.

🔴 **`main` is GREEN.** The claim in this doc's older sections — and in open PRs #708/#710/#713
— that `main` is red on the hermetic tier or a signal floor is **STALE**. #708 fixed it;
measured `GATE: RESULT=PASS` on both tiers at `a34d695d`.

### 🔴 THE FINDING: #683 and #673 are each correct and BREAK TOGETHER

This is the most valuable thing the session produced, and it is a textbook instance of
`claude/RULES.md` → *"Verified in isolation is the new vacuous green — the defect lives in
the SEAM nobody owns."* Both guards were mutation-tested and audit-clean **alone**.

Stacked, every one of the 26 targets produced **exactly one error**, always at the teardown
of whichever test ran first in that target (`.E...`), always identical:

```
DEVRC-GITENV-VIOLATION: test `…::<first test>` MUTATED a git repository that is not its own tmpdir.
What moved:
  CHANGED  /tmp/nix-shell.<x>/tmp.<y>/gitconfig   c2d6f3ff28f7 -> abf7b267f2bd
```

**Mechanism:** #683's detector fingerprints the user-level git config, and its
`global_config_paths()` resolves that by reading `GIT_CONFIG_GLOBAL` — which **#673 sets**,
to its throwaway file. #673's own positive control then performs a real `git config --global`
write into that file *by design*. #683 sees a file it was told to protect change, and fires
correctly. Neither guard is wrong; **#683 is watching a file #673 legitimately writes.**

The other 13 failures are the two PRs' pinning tests: each pins the exact
`python -m pytest …` line in `run-tests.sh` and the conftest import block.

🔴 **Do NOT fix this by dropping `global_config_paths()` from #683's fingerprint.** A
`--global` write leaves the repo untouched, so it is the one damage class the ref fingerprint
cannot see — and the incident produced exactly that (`core.hooksPath` in `~/.gitconfig`).
Fix it on #673's side: put the throwaway somewhere #683 is not watching, or re-baseline
#683's detector around #673's deliberate control write.

### Why #673 is worth finishing (it is NOT redundant with #683)

They close different levers, and together they cover **both phases** of the incident:

| | #683 (merged) | #673 (open) |
|---|---|---|
| which repo git lands in | `GIT_DIR` + 10 siblings, stripped | — |
| which config git writes | — | `GIT_CONFIG_GLOBAL` → throwaway |
| whether git can reach the network | — | `GIT_ALLOW_PROTOCOL=file`, refused **by git**, exit 128 |

Local corruption 19:21 = #683's strip. Remote push storm 19:28 = **#673's `GIT_ALLOW_PROTOCOL`**.
**`main` today does not close the push half.**

#673's remaining work: (1) the seam above; (2) **both PRs call themselves GUARD 9** — the
stack's runner banner prints the collision verbatim, so one must become GUARD 10 with its
pinning tests updated; (3) rebase onto `dfd2d203` (its base is `3116225d`, ~20 commits back
and pre-#683); (4) put a cost number in the body — the stack was slower on `scripts/tests`
than #683 alone, and #673's per-target live controls are the plausible driver.

### Salvaged from the closed PRs

- **#716 OPEN** — `fix/repo-path-set-but-empty`. The six `${VAR:-default}` sites in
  `ship.sh`, `drift-check.sh` and `analyze-service-index/commit.sh` where a SET-BUT-EMPTY
  override silently resolves to the operator's real clone or real index store. Extracted
  from #689 with its two-way ledger and the behavioural test that asserts the exact `exit 2`.
  Red/green re-measured: **7 failed at base → 9 passed**. Independent of the guard
  architecture; it would have died with #689.
- **Recorded as patterns, not instances:** key a `git-*` exec-path farm on the DISPATCH NAME
  never the inode (181 names vs 146 inode matches, `PATH`-dependent within one machine);
  audit every `eval` of an argv-derived value. Both already in the analyze-service index
  under `devrc/tests`.
- 🔴 **#689 RETRACTED #676's claim** that the exec-path farm closed the alias/hook residual —
  the farm substituted only the literal name `git` while git dispatches on `argv[0]`. If
  anyone revives that approach, start from the retraction.

### Housekeeping done 2026-08-22

- **A Tekton pre-merge gate now exists on devrc PRs** (`tekton/devrc-pytests`,
  `tekton/devrc-nodetests`). The four guard PRs showed 0 checks only because they predate it.
  🔴 **`CLAUDE.md`'s `<!-- merge-gate: … -->` marker was STALE at `none`** — per the marker's
  own rules it must read **`other`** (Tekton is not a GitHub Actions `pull_request` workflow).
  ✅ **Changed by #723**, `f3244aa8`. This paragraph said "still owed" and was itself stale
  within the hour; the Gotchas section below has always carried the correct version.
- The base clone's `CLAUDE.md` was **staged and byte-identical to `86e5311a`** — a stale
  orphan that would have silently reverted #702. Restored.
- The base clone was on a **detached HEAD** at `2d1ba0d7` (0 unique commits, verified an
  ancestor of `origin/main`). Reattached to `main` and fast-forwarded.
- 🔴 **The freeze can be reconsidered but is NOT lifted here.** #683 closes the measured
  mechanism on `main`; the residual is a test that writes to some *other* repo by absolute
  path, which #683 detects only for the repo the suite runs from. Note also that the base
  clone has **concurrent sessions writing to it** (one landed #715 mid-session), and #683's
  detector will attribute a concurrent session's ref write to whatever test is running — a
  real false-positive mode for a bare `pytest` in the live clone, harmless in a contained one.

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** (nothing
resolved). An unknown session id answers `200` with an empty array, so that cannot
distinguish "this session touched no task" from "the id is wrong". Not a clean bill of
health; no task was created to fill it.

## Goal
The session started on agent-attention-tooling (write verbs on `session-resolve`) and
finished it. It then became something else: the devrc **pytest tier can write to the
operator's real clone**, and did — destroying `main` on GitHub. Everything live below is
that incident, the two guards written against it, and two PRs parked by the freeze.

## State now

**Branch:** base clone on `main` @ `a0fb39c7`, clean except one other session's
`M scripts/session-analysis/initiative-scan.py` and five untracked files (not mine).

🔴 **THE GUARD-STRATEGY QUESTION IS CLOSED. Both surviving guards are ON `main`.**

| PR | verdict | landed |
|---|---|---|
| **#683** GUARD 9 — strip the 11 repo-pointer env vars + fingerprint detector | ✅ MERGED | `dfd2d203` |
| **#673** GUARD 10 — `GIT_CONFIG_GLOBAL` throwaway + `GIT_ALLOW_PROTOCOL=file` | ✅ MERGED | `4dd14e68` |
| **#720** audit follow-ups on #683 | ✅ MERGED | closes the seam below |
| **#676** interceptor (`nogit.py`, 1758 lines) | 🔴 CLOSED | superseded |
| **#689** interceptor (`norepo.py`, 1358 lines) | 🔴 CLOSED | superseded |
| **#716** the `${VAR:-default}` guards salvaged out of #689 | ✅ MERGED | `3be3d048` |
| **#718** the CI-claim guard's own docstring had gone stale | ✅ MERGED | `b0afd63f` |

**The decision, and it is not "one guard won":** converge on the ENV-LEVER architecture,
close the INTERCEPTOR family. #683 and #673 are complementary and both shipped — #683 owns
*which repo git lands in*, #673 owns *which config git writes* and *whether git can reach
the network*. Those are the two phases of the 2026-08-21 incident (local corruption 19:21,
remote push storm 19:28). **`main` now closes both.**

**Why the interceptors lost — an UNBOUNDED surface, not effort.** Measured: **14 of git's
181 dispatch names take a repository POSITIONALLY** by synopsis alone (`upload-pack`,
`receive-pack`, `fetch-pack`, `send-pack`, `upload-archive`, `clone`, `init`, `init-db`,
`archive`, `fetch`, `pull`, `push`, `ls-remote`, `mailsplit`), plus an open-ended set of
destination-bearing *options* across all 181 — #676's `collect_extra_dests` covered 4 verbs
and missed 3. Every bounded formulation fails: enumerating verbs buys the next audit round;
default-deny false-refuses 167 verbs (permanently red); resolving with real git *is* the
measured 8.85× overhead and still cannot see `git-upload-pack <dir>`. 🔴 **This SUPERSEDES
this doc's earlier "SPLIT recommendation" that #689 continue if someone took the positional
problem knowingly** — the positional problem was the cheapest part of that surface.

**Gate evidence, standalone clones with `origin` removed:**

```
control  pristine main a34d695d       14689 collected /  0 failed   scripts/tests 687.7s  PASS
main + #683                           14721 collected /  0 failed   scripts/tests 699.6s  PASS   (+1.7%)
main + #683 + #673 (hand-merged)      14766 collected / 39 failed                        FAIL   <- the seam
main f3244aa8 + #716 + #718           15080 collected /  0 failed                        PASS
```

🔴 **`main` is GREEN.** Every "main is RED" claim in this doc's earlier sections, and in
PRs #708/#710/#713, is STALE — #708 fixed the signal floor and #722 fixed the CI red.

**Deploy status: ✅ SHIPPED 2026-08-22.** `scripts/ship.sh` converged **both** hosts from
`b8f1e996` to `ec4fc008`, rc 0. Per-host lines read individually, not the final verdict:

```
workbench  fast-forwarded · 526 artifacts checked / 0 dangling / 0 absent · 373 examined / 0 stale · VERIFIED + switched
           NOTE: tree DIRTY — what was built is origin/main + another session's local WIP
laptop     fast-forwarded · 487 artifacts checked / 0 dangling / 0 absent · 358 examined / 0 stale · VERIFIED + switched (clean tree)
```

Payload confirmed present on both machines by content, not by the rc: `scripts/testlib/gitenv.py`
(GUARD 9), `nogit_plugin.py` (GUARD 10), and #716's two `SHIP_REPO+set` guards in `ship.sh`.
🔴 The **workbench** built from a dirty tree, so only the **laptop** is an honest witness that
`origin/main` alone builds. Incident aftermath re-checked at deploy time and still clean:
`core.bare` unset, `core.hooksPath` unset both `--local` and effective (**re-measured then, not
carried from earlier in the session — that value is volatile**), newest fixture-shaped reflog
entry on `main` still `2026-08-21 14:42:12 commit: seed`.

🔴 **And already 1 behind again.** `origin/main` moved to `a689441f` (#731, #733) within the
hour, from other sessions. The hosts are at `ec4fc008`. That is not a deploy failure — it is
what "merged ≠ deployed" costs in a repo this concurrent, and it is why the passive deadman
(`scripts/drift-check.sh`, the `drift-check` timer) exists rather than a one-shot claim here.

## Open investigations — live diagnosis state

### 🔴 #630's enforcement fix exists ONLY as a salvaged local branch
- **Symptom:** the agent committed `d43e425a` + `7de0e21b` on branch `integ` inside its
  `/tmp` sandbox and stopped before pushing. `/tmp` is not durable.
- **Observed:** both shas were absent from the base clone and from origin. Preserved with
  `git -C <repo> fetch <sandbox> integ:refs/heads/salvage/630-enforcement-2026-08-21`
  (verified present afterwards; bogus-sha negative control passed; base clone untouched).
- **What those commits contain:** `d43e425a` = the activation-order guard ran in **no**
  automated tier + the hook it relied on could not be installed. `7de0e21b` = the two
  hermetic failures below, in a **separate commit** as agreed.
- **Ruled out:** that the work was pushed (`origin/fix/managed-path-wrong-writer` is still
  `bd05708d`); that the sandbox was dirty (0 files).
- **Next probe:** decide whether the last verification actually completed. The dev-host gate
  was green after its fixes; the hermetic tier's **re-run after `7de0e21b` was never
  reported**. Re-run both from the salvage branch in a contained clone before pushing.

### ✅ RESOLVED BY SOMEONE ELSE — `main`'s HERMETIC tier was red
🔴 **Do NOT re-land `7de0e21b`.** While this doc was being written, **#696 `6439921`**
(*"fix(gate): two guards were RED only in the sandbox — the tier that gates merges"*) landed
on `main` and fixes **both** failures below, independently. Verified in a fresh clone:
`merge-base --is-ancestor 6439921 origin/main` → **YES** (bogus-sha negative control
rejected). It touches **exactly the same three files** as `7de0e21b`
(`scripts/lib/subsystem_touch.py`, `scripts/tests/test_no_conflict_markers.py`,
`scripts/tests/test_subsystem_touch.py`), so re-landing the salvaged commit conflicts rather
than merges. Its message also records a **third** cause this session never saw — an unpinned
`SIGNAL_PG_DSN` skip, fixed on `main` concurrently.
**Push only `d43e425a` from the salvage branch** — its file set is disjoint from #696's.
The diagnosis below is kept because it is the evidence for *why* the fix is shaped as it is.

- **Symptom + repro:** `nix build .#checks.x86_64-linux.pytests` on pristine `origin/main`.
- **Observed, control-measured** (pristine `main` `295753ae`, origin removed, vs branch
  `d43e425a`):
  ```
  main   295753ae   collected=14378  failed=2
  branch d43e425a   collected=14479  failed=2     ← same two test names
  ```
  The two failures:
  ```
  E  subprocess.CalledProcessError: Command '['git','-C','/build/src','ls-files','-z']'
     returned non-zero exit status 128
  E  assert 3 == 0
  ```
- **Root cause (one, two paths):** `/build/src` in the nix sandbox has **no `.git`**.
  `test_no_conflict_markers.py` shells `git ls-files`; `subsystem_touch.py` resolved `scope`
  **eagerly**, so the single-file `--validate` form demanded a git repo it never consults.
- **Ruled out:** that #630 caused them — neither file is touched by that branch
  (`git diff --name-only 295753ae d43e425a -- <both>` empty; last changed by `9d21a735`/#653
  and `8c940508`/#647). Also ruled out: a third failure — `browser-bridge/SKILL.md` is
  **11,961 B** against the 12,038 budget, pruned by `ed40ab0c`/#679.
- **Fix, written but unpushed** (in `7de0e21b` on the salvage branch): reuse
  `testlib.public_ip_scan.repo_files` rather than a third copy of the git-vs-filesystem rule;
  make `scope` resolution lazy. Verified in a `.git`-removed copy: **3 failed before,
  17 passed after**.
- **Why it read as two unrelated bugs:** the sibling test passes `--scope`, short-circuiting
  the same call — one guard green and one red **on the same defect** — and the cause went to
  **stderr** while the test captured stdout, leaving a bare `assert 3 == 0`.
- **Next probe:** none needed to diagnose. Land the fix; every branch's gate currently shows
  these two and its author will misattribute them (this session did, to its own agent).

### 🔴 #676 needs rework — seven measured bypasses
Guard = `scripts/testlib/nogit.py` + `nogit_plugin.py`, registered from
`scripts/tests/conftest.py`. Branch `guard/no-real-git-writes` @ `057aaa9d`.
Each below measured three-way (noop stable / LIVE unguarded / reached the victim guarded);
four reproduced **inside a real guarded pytest session against the genuine denied repo root**.

| | bypass | root cause |
|---|---|---|
| 🔴-1 | `diff\|log\|show\|diff-tree\|diff-index\|whatchanged --output=` | read fast path does **no** destination check (`nogit.py:1265`) |
| 🔴-2 | `grep -O<cmd>` executes an arbitrary command | same |
| 🔴-3 | symlink-leaf destinations, 6 spellings | `canon_of` resolves the **parent** (`nogit.py:1022`) |
| 🔴-4 | `--config-env=k='V:-$(cmd)'` | the shim's own `eval` (`nogit.py:1200`) |
| 🔴-5 | `git-commit` / `git-config` from an alias or hook | farm skips only the literal `git` (`nogit.py:1580`) |
| 🔴-6 | `checkout-index --prefix`, `pack-objects`, `diagnose -o` | `collect_extra_dests` covers 4 verbs (`nogit.py:899`) |
| 🔴-7 | `branch --set-upstream-to=<ref>` | `case` matches only the bare form (`nogit.py:853`) |

- 🔴 **🔴-4 is the one to fix first regardless of merge timing.** `git
  --config-env=core.pager='V:-$(touch X)' --version` runs the `touch` **inside the shim,
  before any check, on a pure read**. Real git exits 128 (`fatal: missing environment
  variable`) and runs nothing. **On that path the guard is strictly worse than no guard.**
  Fix at the family level, not the instance: validate the name as a shell identifier and read
  with `printenv`, plus a test that fails if a new `eval` appears **or the count changes**.
- 🔴 **🔴-5's fix property is NAMING, not inodes — this bit two sessions.** I reported
  "145 of 188 are hardlinks to the git binary". Reproducible but the wrong property:
  ```
  reference binary                    inode      same-inode entries in `git --exec-path`
  ~/.nix-profile/bin/git              53015695   146     ← git 2.55.0 (what `git` resolves to)
  /run/current-system/sw/bin/git      62031535     0     ← git 2.52.0 (system)
  entries whose NAME git dispatches on (git-*):  181     ← stable, packaging-independent
  ```
  An inode-keyed fix is **PATH-dependent within one machine**. Route all **181** `git-*` by
  name; shim reads `argv[0]` and reconstructs `git <verb>`; verify `git-status` still returns 0,
  **on both hosts**.
- **Ruled out:** that the rebuilt env battery is vacuous — its unguarded arm unwinds all three
  layers (PATH, `GIT_EXEC_PATH`, L2 via MRO walk) **and asserts it did**; 7/7 LIVE confirmed
  independently. Also ruled out: that overhead is "coverage, not waste" — with `maintenance`
  disabled the ratio is **unchanged** (8.85×) or worse (`tag` 12.93×); the driver is **three
  real-git processes plus ~10-18 forks per guarded write**. `nogit.py:1398-1405` documents why
  the two `rev-parse` calls cannot simply merge (bare repos exit 128 on `--show-toplevel`);
  the available win is combined-first with a fallback to the split pair on non-zero exit.
- **Held up:** all 21 previously-pinned bypasses stay blocked (17 re-fired independently);
  **zero** new false refusals across 31 legitimate-use cases; 197 guard tests; `scripts/tests`
  7052 passed / 0 failed with the full toolchain.
- **Next probe:** fix 🔴-4 first, then 🔴-1/🔴-2 by moving destination and command-bearing
  option checks **above** the fast path, then 🔴-5 by name.

### The leak class that caused the recurrence is covered by NEITHER guard
- **Observed:** `git config` run **from inside a worktree**, without `--global`, writes the
  **base clone's** `.git/config` — worktrees share the common git dir. Measured with a control:
  the correctly-scoped `--worktree` form **fails by default**
  (`fatal: … unless extensions.worktreeConfig is enabled`), so the shared-write behaviour is
  the only thing that works without prior setup.
- **This is what re-armed `core.hooksPath` and set `core.bare=true` at 18:08**, from agents in
  worktrees — not from the test tier either guard covers. #689's GUARD 9 refuses the *tier's*
  route into the class and says so honestly; the class itself is open.
- **Next probe:** decide whether this wants a guard at all, or a rule. It is an agent-behaviour
  hazard, not a test-suite one.

### Incident aftermath — contained, verified at close
```
core.bare  <unset>   hooksPath  <unset>   origin  git@github.com:innovation-upstream/devrc.git
main branch-protected on the API: yes
incident/fixture-trunk-2026-08-21     5d91acdd   (the fixture `seed` lineage)
incident/main-fixture-wipe-2026-08-21 14200130   (the push-storm lineage — DIFFERENT lineage)
newest fixture-shaped reflog entry: 14:42:12 "commit: seed"   ← no recurrence since
```
- **Culprit identified:** `scripts/repo-cos/tests/test_prescan.py::_init_clone` (lines 38–56),
  mapping one-for-one onto the reflog: `config user.email t@t` (:48) → the `t <t@t>` author;
  `commit -m seed` (:51); `branch -M trunk` (:54); `push origin HEAD:trunk` (:53).
  Committed on `main` — not from anyone's worktree.
- 🔴 **`git -C <path>` is NOT protective.** `GIT_DIR=<victim>/.git git -C <fixture> commit`
  mutates the victim while `rev-parse --show-toplevel` truthfully reports the fixture. Every
  "absolute `-C`, therefore safe" audit measured the wrong property.
- **Ordering that pins the mechanism:** local corruption **19:21:35Z**, remote push storm
  **19:28:14Z** — ~7 minutes apart. A fixture-scoped `git -C <tmpdir>` cannot rename a branch
  in `~/workspace/devrc`, so the write came from something resolving that path for itself.
- **107 of 107 per-worktree reflogs** showed **zero** in-window entries (positive control: 107
  worktrees, 107 readable) — rules out every worktree-escape story.

### 🔴 #689 (the runner-layer guard) is GATE RED — and the cause is its own fix

- **State:** `fix/no-real-git-remote-in-tests` @ `1ac08879`, PR #689 OPEN, **not merged**, 9 commits behind `origin/main`. Force-updated from `36ff9e3c` (that head carried an RCE — see Gotchas). Body rewritten with the retraction, the RCE disclosure, and the name-not-inode rationale; a follow-up comment carries the late findings.
- **Observed (verbatim):**
  ```
  RESULT: FAIL (exit=1)
  TOTAL collected=14505  passed=14281  skipped=2  failed=222  (floor 13324; no floor drifted)
  3 targets red: scripts/tests (37 failed/177 errors), scripts/repo-cos/tests (7), scripts/task-spec-drafter/tests (1)
  ERROR: 3 GUARD 9 problem(s)
  ```
  ~221 of 222 failures are one shape:
  ```
  git(refused)  would mutate the PROTECTED repository <sandbox>/.git
                git upload-pack <tmp_path>/…/origin.git
  ```
- **Mechanism (isolated, not inferred):** a **local** `git clone <fixture>` makes git spawn `git-upload-pack <dir>`. Routing all `git-*` libexec names through the shim means that child *is* the shim — and the repo arrives as a **bare positional**, invisible to a global-option parser that reads `-C`/`--git-dir`. Target falls back to `cwd` = the protected repo ⇒ refusal. Three-arm control, cwd held constant, one variable moved:

  | arm | rc |
  |---|---|
  | 1 · no farm | 0 |
  | 2 · farm as shipped | **128, GUARD 9 REFUSED** |
  | 3 · farm, only `git-upload-pack` pointed back at the real binary | 0 |

  Arm 3 = 0 is what separates *"the guard is wrong"* from *"the guard is right and target resolution is wrong"*. It is the latter. **The refusals are FALSE** — no test reaches a real repo — but the effect is a permanently-red tier (the **third** this PR has produced).
- **Ruled out:** not a floor drift (all 25 per-target floors cleared); not exit 90 (runner verdict and gate agree); not the `SKILL.md` headroom test (fixed on main by `ed40ab0c`/#679).
- **🔴 The 222 is NOT attributable arithmetic.** The branch predates **#696 (`6439921`)**, now an ancestor of `origin/main` (verified, with a bogus-sha negative control), which fixed two hermetic failures on `main` itself plus a third concurrent cause. **2–3 failures are inherited.** Rebase before subtracting.
- **The design cost, which is the real finding:** the positional-repo shape is **not one special case**. It covers at least `git-upload-pack`, `git-receive-pack`, `git-upload-archive`, `git-init-db`, and `--git-dir`-less forms of other helpers. **Any farm that routes by name inherits a per-verb argument-shape problem** — each verb needs its own parse rule, hit once per verb, forever. The remaining 3 failures are a second class: `-c core.fsmonitor=` is not on `INJECTABLE_KEYS`.
- **Next probe (verbatim):**
  ```bash
  # rebase onto current main, re-gate in a CONTAINED clone, then attribute
  git -C <standalone-clone> rev-parse --path-format=absolute --git-common-dir   # MUST be inside the sandbox
  nix develop . --command bash scripts/gate.sh --tier pytest
  ```
- **Recommendation on record, and it is SPLIT — do not read it as "merge #683":** #683's **prevention** half is correct and independently verified (guard at `run-tests.sh:235` / `run-node-tests.sh:142` / `gate.sh:120`, each immediately above its own `ROOT=`, nothing git-related executing before it). That half stops the measured mechanism and is worth landing. Its **detection** half is *not* stronger than #689 on the axis that produced #689's fourteen bypasses — two measured defects below. Continue #689 only if someone takes the per-verb positional-repo problem knowingly. Salvage from #689 regardless: the armed-vector battery with **live-axis arms**, the per-target `control=plugin:N inherited:N` accounting, and the fail-open checks.

### ✅ THE ROOT CAUSE OF THE RECURRENCE IS IDENTIFIED (2026-08-25) — this heading used to say it was not

🔴 **The retraction below is itself now partly retracted, and this is the fifth
place the same false generalisation was found. A `RETRACTED:` banner carries
extra authority, which is exactly why a wrong one is expensive.**

- **What was measured, and is still true:** on git 2.55.0, a `git push` **from a
  MAIN CHECKOUT** exports `GIT_EXEC_PATH`, `GIT_PREFIX` and `GIT_EDITOR` to
  `pre-push` — not `GIT_DIR`. The rename in #683 is a route only if an outer
  caller had already exported the name. That much stands.
- 🔴 **What was WRONG: the generalisation to every push.** Both points were
  measured on the same side of the dimension that decides the answer — which
  checkout the push came from. Measured 2026-08-25, git 2.55.0, parent scrubbed
  of every `GIT_*` name, reproduced independently on a second rig:

  | push origin | `GIT_DIR` in `pre-push` |
  |---|---|
  | main checkout | *none* |
  | **linked worktree** | `<repo>/.git/worktrees/<name>` |
  | `--separate-git-dir` | `<separate gitdir>` |
  | submodule | `<super>/.git/modules/<sub>` |
  | bare repo | `.` *(relative!)* |

  **git itself exports it. No outer caller is required** — and the incident was a
  `git push -u origin <branch>` **from a linked worktree**. That is the answer to
  "why pushing appeared to trigger corruption".
- **The live scan is not counter-evidence.** "46 processes carry some `GIT_*`,
  0 carry `GIT_DIR`" is a fact about ambient processes; git sets this variable
  *for the duration of the hook*, where no scan would ever see it.
- Pinned by `scripts/tests/test_git_repo_isolation.py::test_git_exports_GIT_DIR_to_pre_push_from_a_worktree_but_not_a_main_checkout`.
  Since worktree isolation is the standing default for file-modifying agents,
  this is the ORDINARY path — so the `REPO_POINTER_VARS` strip is load-bearing,
  not belt-and-braces. Do not weaken it on the strength of the first bullet.
- ⚠ **Still open:** this identifies what SETS `GIT_DIR` on a push. It does not
  complete the end-to-end walk `pre-push` → `tests-on-push.sh` → the suite for
  the incident itself, which `claudedocs/handoff-gitdir-leak-and-ci-gates.md`
  still lists as never exercised. And nothing here protects `homelab-talos` or
  any other repo's tooling.

### 🔴 #683's DETECTION half — two measured defects (its prevention half is sound)

- **The detector cannot distinguish a fixture escape from ANY other writer to that clone, and asserts the former at maximum confidence.** An innocent nested test plus a background `git branch` yields: *"test 'test_d.py::test_innocent_one' MUTATED a git repository that is not its own tmpdir … This is the 2026-08-21 incident's shape."* Measured live: during a 40-minute audit the operator's clone gained `int/gate-665` and `integ/git-isolation-trio`, deleted a branch and fast-forwarded `main` twice — all from other sessions. Two amplifiers: `common_dir_of` makes a worktree-hosted run watch the **shared** common dir (so sibling agents count), and `drift-check.sh` runs `git fetch origin` on a 6-hourly timer whose own comment notes `fetch` triggers `gc --auto` — a repack would emit **hundreds** of `DELETED refs/...` lines under a banner claiming the incident recurred.
- 🔴 **`DEVRC_GITENV_PROTECT` is a one-variable defeat of the whole detection half, and no runner unsets it — it is not in `REPO_POINTER_VARS`.** Measured triple, same escaping test: correct ⇒ `protected-git-dirs=1`, RED with the guard's own token ✅ · `":"` ⇒ `protected-git-dirs=0`, **GREEN**, branch actually created · `/nonexistent/x` ⇒ **`protected-git-dirs=1` AND GREEN** while the repo was mutated — **the marker line actively lies**. That reintroduces "one inherited env var defeats every layer" *inside the fix for it*.
- **Also unswept:** the second entry point covers **1 of 7 conftests**, and not `scripts/claude-hooks/tests/conftest.py` — where the plugin's own rationale says import-time arming matters. Five semantic mutants of the fingerprint's *content* SURVIVE a green suite (dropping `HEAD`, `packed-refs`, `ORIG_HEAD`+`logs/HEAD`; either `starts` reduction); its docstring claims a HEAD-move row that does not exist, and `packed-refs` never exists in any fixture — while the incident's `DELETED refs/heads/main` on a **packed** ref is exactly that case. "12/12 mutants killed" is true of the plumbing mutants chosen and says nothing about the detector's content.

### ✅ RESOLVED — the #683/#673 seam (kept because the mechanism is the lesson)
- **Symptom:** stacking #673 onto #683 produced **exactly one error in every one of the 26
  pytest targets**, always at the teardown of whichever test ran FIRST (`.E...`), always
  `DEVRC-GITENV-VIOLATION … CHANGED <tmp>/gitconfig`.
- **Mechanism:** #683's detector fingerprints the user-level git config via
  `global_config_paths()`, which reads `GIT_CONFIG_GLOBAL` — the variable **#673 sets** to
  its throwaway and then **writes to from its own positive control**. Neither guard was
  wrong; **#683 was watching a file #673 legitimately writes.**
- **Both were mutation-tested and audit-clean ALONE.** Textbook `claude/RULES.md` →
  *"verified in isolation is the new vacuous green — the defect lives in the SEAM nobody owns."*
- **Fixed on `main` by #720**, on the watcher's side and in a STRICTLY STRONGER form than
  recommended: `gitenv.py` now skips a *scratch redirect* while still watching the
  operator's real `~/.gitconfig`. The old code returned early on ANY override, so once
  something redirected the variable a direct write to `~/.gitconfig` went unwatched.
  See the comment at `scripts/testlib/gitenv.py` (`A REDIRECT BY THE HARNESS IS NOT A
  CONFIG TO PROTECT`). #673 was renumbered to **GUARD 10**, as recommended.

### 🟢 MEASURED 2026-08-22 — #720 closes the concurrent-session case, with ONE residual

The probe below was run. The answer is not "closed" or "open" — it is a boundary, and the
boundary needs **two** conditions to fail. Five arms in a contained clone at `a689441f`,
one variable moved per arm, each with a positive control proving the writer really wrote.

| arm | who writes | writer's cwd | still writing at teardown | GUARD 9 verdict |
|---|---|---|---|---|
| A | nobody | — | — | green · `mode=enforce(auto)` · `unattributable=0` |
| D | **the test itself** (a real escape) | — | — | 🔴 `DEVRC-GITENV-VIOLATION` · attributed=1 — **correct** |
| B | another process | **inside** the tree | no | downgrade → `report(auto)` · `gitenv(observed)` · test PASSES — **correct** |
| E | another process, `git -C <repo>` | **outside** | no | 🔴 `DEVRC-GITENV-VIOLATION` on an innocent sleeping test — **FALSE** |
| F | another process, `git -C <repo>` | outside | **yes** | `gitenv(foreign-writer)` via the 0.25s settle · test PASSES — **correct** |

- **Arm D is the reachability control and it is not optional.** Without it a green in B or F
  is indistinguishable from a disarmed guard. It fires with the guard's own `VIOLATION_TOKEN`,
  so B/F's greens are about the guard's judgement rather than its absence.
- **The mechanism, from the source rather than inferred:** #720 downgrades on *proven*
  evidence of another writer, from two independent probes — `live_cotenants` (processes whose
  **CWD** is inside a protected work tree and are not our ancestors) and a **settle re-read**
  when a delta appears. Arm B trips the first; arm F trips the second. **Arm E trips neither**,
  and enforce mode then asserts at full confidence against whatever test happened to be running.
- 🔴 **The residual is narrow but it is the HOUSE STYLE.** It needs cwd-outside **and**
  quiescent-by-teardown together — but that is the exact shape of an ordinary agent write, and
  `CLAUDE.md` *mandates* the half that defeats the cwd probe: *"Use `git -C <path>` and absolute
  paths — never `cd <repo> && …`"*. A `git -C <repo> commit` from a shell sitting elsewhere
  finishes in well under a second. So the probe designed to prove another writer exists is
  blind to the dominant way writers in this repo actually write.
- **What this does NOT affect:** the pointer strip (prevention) is independent of mode, and the
  gate runs in a contained clone where no co-tenant exists. This is a false-positive class for
  a bare `pytest` in the live clone, not a hole in the guard.
- **Also corrected while measuring:** the earlier text here blamed `logs/HEAD` being
  fingerprinted. #720 **removed** `logs/HEAD` from the fingerprint outright (a pure derivative
  that `gc`'s reflog expiry rewrites on its own) and replaced `packed-refs`-as-a-file with a
  parsed name→object-id map. That specific mechanism is gone; the class survived it by a
  different route.
- **Next probe, if someone takes the residual:** widen `live_cotenants` from CWD to also match
  a process whose **argv** names a protected repo path (`git -C <repo> …`, `--git-dir=<repo>`),
  and re-run arm E — it must move from `enforce` to `report`. Keep arm D in the same battery:
  a widening that silences arm D has removed the guard, not the false positive.

### 🟢 DONE 2026-08-22 — #493's `nix-shell.*` glob, plus the reason it could not have shipped
- **Rebased** onto current `main` (it was **227 commits behind**; original blob OID preserved,
  force-pushed with `--force-with-lease` pinned to `08849bd3`) and the glob added.
- **Re-measured live** rather than carried from the numbers below: `nix-shell.*` = **3340**,
  `nix-shell-*` = **1017** — a 3.3:1 ratio, against a positive control of **119,066** total
  top-level `/tmp` entries, so the zeros are real zeros and not a walk that never ran.
- 🔴 **The fix could not have reached an already-applied host.** The script gated its entire
  edit on `grep -qF "$MARKER"` — the presence of a *comment* — so a host that had run it
  printed `already present — skipping edit` and exited **0** over a config missing every rule
  added since. The skip is now per-RULE, off a single ledger, and the script **re-reads the
  file** afterwards to prove each rule landed. Applied state, measured: workbench **0**
  (unapplied, positive control confirms the anchor greps 1); laptop **UNMEASURED** —
  `Permission denied`, *not* zero. That laptop row is why the idempotency half is in the same PR.
- **Coverage:** `scripts/tests/test_tmp_churn_retention.py` extracts the inserter heredoc
  verbatim from the shipped script, so there is no second copy of the rules to drift. 11 tests,
  **watched to fail**: inert reword → 11 passed (control) · marker-only skip restored → **1
  failed, the regression test by name** · late rule deleted → 4 failed · glob broadened to
  `/tmp/*` → 4 failed · restored byte-identical → 11 passed.
- 🔴 **Still staged, not applied** — `sudo bash nix/system/apply-tmp-churn-retention.sh`.
- **Deliberately NOT taken**, each larger than everything this PR reaps put together and none
  checked against live work: `cgparent-*` 17064 · `fx-excerpt-*` 14012 · `cbf-*` 5268 ·
  `tmp.*` 4227. `tmp.*` is bare `mktemp`'s default and needs its own audit first.

### 🟡 SUPERSEDED — the original #493 measurement
- **Observed:** #493's globs cover **6,775** live entries. `nix-shell-*` requires a literal
  hyphen and therefore **cannot match `nix-shell.<mktemp>`** — which is **3,178** entries,
  outnumbering the hyphen form 3:1, and is the form `gate.sh` produces on every agent run.
  Uncovered besides: `cgparent-*` 17,064 · `fx-excerpt-*` 14,012 · `cbf-*` 5,268 ·
  `tmp.*` 4,227 · `refresh-cli-snapshot-*` 3,370 · `bap-*` 2,634 · `cb-step-*` 2,495 ·
  `ab-redir-*` 2,140. ≈54,000 uncovered vs 6,775 covered.
- **Ruled out:** that this was the disk problem — see the incident block below.
- **Next probe:** add `"e /tmp/nix-shell.* - - - m:7d"` to
  `nix/system/apply-tmp-churn-retention.sh`. Posted as a comment on #493. 🔴 **It is a
  `nix/system/apply-*.sh` — Claude CANNOT apply it; it is staged for `sudo`.**

### ✅ RESOLVED — the disk hit 99% and turned the merge gate RED
- **Symptom:** `OSError: [Errno 28] No space left on device` in
  `test_agent_ledger_hook.py` during a gate run. **Not a code defect.**
- **Root cause:** `/tmp` was **766.7 GiB**, of which `/tmp/mutate` was **587.9 GiB** —
  `/tmp/mutate/work/clusters-app/proc` alone was **582 GiB**, a recursive copy of a rootfs
  that included `/proc`, so three `pagemap` pseudo-files materialised at 257 G + 257 G + 70 G.
- 🔴 **Verified genuinely allocated, not sparse**, before acting: `find -printf %s` reports
  APPARENT size and `/proc/*/pagemap` is a pseudo-file, so the obvious reading is wrong.
  536,879,872 × 512-byte blocks, and `du` real usage agrees. **Always check allocated
  blocks before quoting a `/proc`-derived size.**
- **Deleted with the operator's approval; 97% → 63%, 581 GiB freed.** `var/` (6.6 G, k3s
  state + coredumps) deliberately kept. A directory literally named `&&` survives in that
  tree — a shell-quoting bug in whatever ran the copy.
- 🔴 **RETRACTED, mine:** I estimated the stale `nix-shell.*` dirs at ~52 GiB by multiplying
  a sample MEAN over a heavy-tailed distribution. Reaping all 1,305 of them freed **1.4 GiB**.
  The large dirs are the RECENT, in-use ones; the old ones are ~1 MB. The median (996 KiB)
  was in front of me and I extrapolated from the mean anyway.

### 🔴 THE CI TIER HAS THREE INDEPENDENT FLAKES, AND THEY SHARE ONE SHAPE

Found in a single evening, only one of them on anybody's list. Each was a red
check on a PR whose diff could not have caused it, and each was diagnosed by
reading the STEP LOG rather than the verdict — twice the totals coincided with a
different failure, which is `handoff-guards-and-gates.md`'s recorded trap firing
again.

| | flake | evidence | owner |
|---|---|---|---|
| 1 | the audit line does not exist yet when the client's response arrives | 9 fails / 43 runs under load, all the same `IndexError`; `devrc-ci-jxf5j` FAILED vs `devrc-ci-vl88r` SUCCEEDED on the **identical** revision `ba97a9d7` | **#740** |
| 2 | a gate STEP is killed with exit 255 — no verdict is produced | **2 of 54** gate taskruns (4%) in an unperturbed window; check reads `fail` with `NOT RUN: the gate stopped before this leg reported` | 🟡 **UNOWNED, and smaller than first reported** |
| 3 | `tree_hash` reads `.git/objects/maintenance.lock` after git deletes it | `devrc-ci-x9zkh`, on a PR touching neither the test nor the tool | **#743** |

🔴 **They are one shape, not three accidents: a test observing a system that is
still moving.** A response does not imply the log line. A `rglob` listing does not
imply the file is still there to read. A clone step reporting does not imply a
checkout. In every case the test took an observation as a proof of a LATER state,
which is the same error as `claude/RULES.md` → *"a control that SHARES the step
you doubt"*. Worth one deliberate sweep of the suite for that shape rather than
finding them one CI run at a time; **not done**, and nobody should read this table
as the closed set.

🔴 **RETRACTED, AND THE RETRACTION IS THE USEFUL PART. #2's rate was first written
here as "4 of 75 (5.3%)". That number was measured over a window this session was
itself saturating, and MOST of those failures were self-inflicted.**

```
                        gate runs   steps killed with exit 255
before this session's
  push burst (03:20Z)       54          2   (4%)   <- the real background rate
during / after it           34          8   (24%)  <- congestion, caused here
```

**Mechanism, measured not guessed:** every `devrc-ci` gate pod is pinned to ONE
node by Tekton's affinity assistant (a shared workspace PVC). Five branches pushed
in quick succession put five full pipeline runs on that one node — at 77% CPU
requests / 237% limits, with 424 Completed and 97 Error pods resident — and the
cluster began emitting `ExceededNodeResources: Insufficient resources to schedule
pod`. Steps were then killed with exit 255, in `step-clone` AND `step-pytests`
alike, which is why one signature appeared in two different places and read as two
bugs. The queue drained; #632 immediately went green on both tiers. That recovery
is itself the control: a code cause would not heal when the node emptied.

🔴 **Two lessons, and the second is the transferable one.**
  * *A rate measured over a window you are perturbing is a fact about you, not the
    system* — `claude/RULES.md` → "a control that SHARES the step you doubt". The
    load was mine and I reported it as a property of the CI tier.
  * *Pushing N branches at once is not N independent actions here.* They serialise
    onto one node, so a burst is a blast-radius action like `pkill -f`: **anyone
    else's PR checks in that window were killed too.** Push, wait, push.

**What survives:** a real background rate of ~4%, unowned, cause unknown — its pod
logs are garbage-collected before anyone looks, so there is no evidence left to
diagnose. A Tekton retry would hide it with the cause still unknown; named as a
workaround, not recommended.

🟡 **What this does to the "make the checks REQUIRED" step.** The earlier version of
this paragraph argued against arming them on the strength of the inflated 5.3%.
**That argument is withdrawn.** At ~4% background — roughly 1 PR in 25 needing a
re-run — the case for arming the checks is materially stronger than this doc first
claimed. It remains true that "red" will not mean "broken code" until #740 and
#743 land, and that whoever arms them wants a re-run affordance on day one; it is
no longer true that the flake rate is a reason to wait.

## Next steps (ranked) — rewritten 2026-08-22 after the four above were worked

0. ✅ **DONE — main's red is closed.** #732 merged (`5a2a7b21`, by a concurrent session).
   Proved live rather than by the merge notification: `origin/main` extracted into a
   `.git`-less tree runs `test_the_module_root_is_load_bearing` → **1 passed**, where the
   same extraction of the pre-merge `main` gave the verbatim CI assertion. The item below
   is kept because its evidence is the reason the three-flake table above exists.
1. ~~🔴 `tekton/devrc-pytests` is RED on EVERY open PR, and the cause is on `main`.~~
   `test_the_module_root_is_load_bearing` asserts `gitenv.py` sits inside a git checkout;
   the authoritative runner's source is a `/nix/store` path with **no `.git`**, so it dies on
   its own precondition. It passes on a dev host and fails in the one environment that would
   gate a merge — the two-tier blindness again, from GUARD 9's own test file. **#732 fixes it**,
   verified independently here, red-at-base / green-at-HEAD in a `.git`-less tree:
   ```
   main          (no .git)  ->  1 failed   AssertionError: gitenv.py is not inside a git checkout
   #732 ba97a9d7 (no .git)  ->  1 passed
   ```
   🔴 **#732's OWN Tekton check still reads FAILURE on that head sha, with totals byte-identical
   to its pre-fix measurement** (`collected=15152 failed=1`). That is either a stale run or a
   DIFFERENT test — precisely the coincidence `handoff-guards-and-gates.md` records. Read the
   step log or re-run it; do not merge on the local matrix alone.
2. **Make the two Tekton checks REQUIRED** — the highest-value thing left, and blocked on a
   sudo-mode GitHub action Claude cannot perform. `required_status_checks` is **null**, there
   are **0 rulesets**, and `enforce_admins: true` is enforcing an empty set. 🔴 **Do it only
   after step 1 lands and pytests has gone green once**: arming a check that cannot pass makes
   every PR unmergeable, and a permanently-red gate is worse than no gate.
3. ~~#632 does not merge~~ — ✅ **RESOLVED AND RE-GATED 2026-08-22.** The conflict was
   semantic, not textual: `main` (#716, shipped to both hosts) uses **rc 2** for *`SHIP_REPO`
   set-but-empty* — two `exit 2` sites — while #632 used **rc 2** for *usage error*.
   **Operator's decision: the usage error moves to 21**, the code #632's own reservation
   ledger already pointed at. Both `exit 2` usage sites became `exit 21`; the two
   `SHIP_REPO` guards keep rc 2; the rc legend prints both.
   🔴 **The ledger values were not hand-computed.**
   `test_the_two_rc_ladders_reserve_each_others_codes` DERIVES them from the two measured
   code sets and prints the answer on failure — it said *"drift-check.sh reserves
   [5, 7, 9, 11, 19, 20] to ship.sh, but ship.sh alone can return [5, 7, 9, 11, 19, 20, 21]"*
   and that is what was written. Next-free is now 22 on both sides. rc 2 is returned by
   BOTH scripts, so it correctly appears on neither reservation line.
   **Verified behaviourally, because a clean git merge is not a clean merge** — both sides
   edited the same ladder, so the result was exercised against a non-existent `SHIP_REPO`
   (no host touched): unknown arg → **21**, `--no-local --no-remote` → **21**, `SHIP_REPO`
   set-but-empty → **2**, and the same call on `origin/main` → **2**, unchanged.
   Re-gate on the resolved merged tree, contained clone, origin removed:
   `pytests 15184 collected / 0 failed` · `nodetests 1149/1149` — **both PASS**.
   🔴 **A consequence of the decision, stated rather than buried:** rc 2 now means different
   things in `ship.sh` and `drift-check.sh`. The ledger's machine-checked property is intact
   (it governs only codes one script can return and the other cannot), but the headers' prose
   intent *"a number must not mean two things across the pair"* is now aspirational for rc 2.
   That was already true on `main` before #632 was touched; this merge only makes it visible.
   `test_a_run_with_no_host_in_scope_is_a_usage_error`'s docstring claimed the two ladders
   were "deliberately aligned" on rc 2 — true when written, false now, and rewritten rather
   than left as a comment asserting coverage it no longer has.
4. ~~Verify the concurrent-session detector case~~ — **done**, see the five-arm matrix above.
   #720 closes it except when the other writer's cwd is outside the tree **and** it is quiescent
   by teardown. Residual documented with its next probe; not fixed.
5. ~~#493~~ — **done**, rebased and extended; still needs the operator's `sudo`.
6. **#701 (this doc)** — being landed with these updates.

## Gotchas / decisions / dead-ends

- 🔴 **A worktree is NOT isolation.** It shares the common git dir — `refs/`, index, reflog,
  config, remotes. Develop against the tier in a **standalone clone with `origin` removed**;
  note a clone of a local repo gets `origin` pointing at the **real clone**, so removing it is
  the containment, not tidiness.
- 🔴 **`grep -r` here is a shell function wrapping ugrep and honours `.gitignore`** — blind to
  generated/ignored paths, and its BRE bracket parsing has returned `0` for present patterns.
  Use `grep -F`/`grep -P`, `find … -print0 | xargs -0 grep`, and **never pipe `find` into
  `head`** (early pipe close truncates the walk and fabricates a zero).
- 🔴 **`gate.sh` writes its log under the nix-shell `TMPDIR`** (`/tmp/nix-shell.*/devrc-gate-*/`),
  **not** `/tmp/devrc-gate-*`. A glob on the latter structurally cannot find it — this session
  read that absence as a timeout kill and was wrong.
- 🔴 **A cache-hit `nix build` exits 0 with no output**; `pytest -q -q` suppresses the summary
  entirely. Read the derivation log, count progress characters, never trust an exit code.
- **Wrap long gates so an absent verdict is distinguishable from a kill**: `run_bounded.sh`
  writes `EXIT=<rc> KILLED=<0|1>` to a status file. 3600s is too short — `scripts/tests` alone
  is ~700–815s and the full tier is longer.
- **The nix sandbox does NOT have `nix-command` disabled** — a long-standing repo claim,
  measured false (`nix eval --expr '1+1'` → rc 0 inside a real build sandbox). What is missing
  is the flake's **inputs**. Corrected in six places.
- **Mutation batteries lie in specific ways**: a malformed `-k` running zero tests scores
  KILLED; `-k` against parametrised ids fails because those ids contain **spaces**; a mutant
  that lands on its own boundary never builds the state it claims (an `M21` here picked a
  target already in `HERMETIC_TARGETS`, so it stayed summed and SURVIVED against a working
  guard). Validate the battery before scoring, and keep a **genuinely inert** negative control.
- **A killed battery can poison the next run** — it leaves a file mutated and the next run backs
  the mutated file up as "pristine". Guard validated three ways here: clean → PASS, poisoned →
  RAISED and names the file, restored → PASS.
- **Two tiers must both be green.** The dev-host gate and the nix sandbox disagree structurally;
  `/build/src` has no `.git`. `_repo_files()` in `test_claude_sessions.py` already documents
  this and the new code repeated it anyway.
- **Decisions:** #676 deliberately does **not** close B1 (per-target coverage) — that is #689's
  runner layer, and duplicate enforcement is the `Popen` collision this session measured. Both
  guards state their residuals in-file rather than implying coverage.

- 🔴 **A guard can be WORSE than no guard, and #689 shipped that for hours.** `eval "_cv=\${$_ev:-}"` with the value from `--config-env=key=VAR` meant:
  ```
  git --config-env=core.pager='V:-$(touch X)' --version
    shim     -> X CREATED, then exit 99      <- executed
    real git -> exit 128, "fatal: missing environment variable", nothing run
  ```
  The mitigation introduced execution the unguarded system **refuses**. Fixed by validating the name as a shell identifier + `printenv`. All four `eval`s audited; only that one was argv-derived; a test now fails if a new `eval` appears **or the count changes** — the pattern, not the instance.
- 🔴 **RETRACTED: the `GIT_EXEC_PATH` symlink farm does NOT close the alias/hook residual.** It was reported closed (by a peer, ported and credited by me, relayed to two reviewers). The farm skipped only the literal name `git`; **181 `git-*` entries reached the real binary**, and `<farm>/git-config -f <victim>/.git/config core.bare true` landed the incident's own signature at rc 0. Residual is **narrowed to two derived escapes**, not closed.
- 🔴 **Key on the DISPATCH NAME, never the inode.** This host has **two git binaries from unrelated nix closures** (`~/.nix-profile/bin/git` inode 53015695 = 2.55.0, which `git` resolves to and whose libexec `--exec-path` reports; `/run/current-system/sw/bin/git` inode 62031535 = 2.52.0). Same-inode counts *within the `--exec-path` dir*: **146** vs **0**; the system git's *own* libexec gives **143**. So an inode-keyed enumeration is **`PATH`-dependent within one machine** and audits green over a live hole. Stronger form: that dir holds **181 dispatch names but only 146 resolve to git's inode** — an inode enumeration misses **35 even on the right binary**. 181 is identical on both hosts.
- **A ledger can fail in four escalating ways, all seen here:** rotted (upgrade adds a helper) · **born incomplete** (145 aliases enumerated then skipped by name) · **host-dependent** (packaging differs) · **`PATH`-dependent within one host** (which `git` you resolve). All four are the same defect: the ledger described the wrong attribute.
- 🔴 **A vacuity probe can itself be vacuous.** A peer's second guard layer silently **re-guarded 13 "unguarded" controls** and the harness printed `VACUOUS` — which does not merely mislead, it **shrinks the armed set while reading as rigour**. Ours was checked and held, but only via three integrity checks per arm: structural, a **negative canary** (a call the guard *would* refuse isn't), and a **positive canary** (an ordinary mutating op still lands — catches a PATH so mangled that *nothing* runs, which also produces "nothing moved"). That triple is the mechanical form of *a control must detect its own absence*.
- **Instrument failures that produced confident wrong answers, all measured today:**
  - `pgrep -f <pattern>` — **the sweep is itself a process containing the pattern.** Produced a false *confession* (a tier run in the base clone that was my own measuring shell, gone by the next command).
  - **Kinship tests are vacuous on this box** — every process descends from one tmux (`4025325`), so "shares an ancestor with me" returns MINE for all. The owning `claude` pid is the only real boundary.
  - **A control built from an input broken for an unrelated reason is not a control** — an `unreachable.invalid` host can only ever fail, so it manufactured a clean-looking discriminator. Against a *reachable* remote the same push **authenticates over ssh** and fails on ref rejection, so `rc != 0` holds in both arms. **Pin the literal `fatal: transport '<proto>' not allowed`, never the exit code.**
  - **A stale tree answers confidently** — an audit tool was correct and its *input* was 11 commits behind, producing a live-looking defect. Ask *which tree did this read?*
  - **BRE `\t` is a literal `t`** — `grep -c '^git(control)\tvia=plugin'` matched nothing and reported `control=0` for every target while the guard was healthy.
  - **`git rev-parse --git-common-dir` returns a REPO-relative path** (`.git`) for a healthy clone and an absolute one for a worktree. A prefix match on the relative form calls a good sandbox unsafe; joining it to your own cwd inside a repo resolves to *that* repo. **Always `--path-format=absolute`.**
- **Relay discipline:** two claims were relayed today after being *reproduced* rather than *verified* (the transport control, the farm). **A claim's authority does not decay as it is passed along, but its evidence does.** What worked was the correction arriving in the same channel as the overstatement, fast, from whoever measured it.
- **A test's structural check matched its own explanatory COMMENT three separate times** in this work — a guard reading the documentation and reporting on the code. Each is now scoped to code.
- **`--force-with-lease=<ref>:<sha>`, never bare `--force`** — and verify the discarded head carried no unique work first (here: `36ff9e3` and `ae7ae82` touched identical files; the only delta was 12 lines absorbed from main's #687).

- 🔴 **`os.kill(pid, 0)` SUCCEEDS on a zombie** — not a liveness check, and which way it is
  wrong is decided by whatever PID 1 is. `devrc-ci` was red on its first **5 of 5** runs on
  this alone (#722). A dev-host green was never evidence about CI.
- 🔴 **A check that RUNS is not a check that GATES.** Tekton now posts
  `tekton/devrc-pytests`/`-nodetests` on devrc PRs, and `main` DOES carry branch protection
  — but with **no `required_status_checks`**, so nothing blocks. Proved behaviourally: #707
  merged **28 minutes** after its check went RED, #711 two minutes after. The marker is
  `other` (#723). 🔴 **My call of `none` was overruled and `other` is right** — the marker's
  own docs name Tekton as an `other` trigger.
- 🔴 **A mutant that SURVIVES may be a broken MUTANT, not a sound guard.** One of mine
  scored SURVIVED because it inserted the unrelated `exit` INSIDE the guard rather than
  after the `fi`, so it was never the case being tested. Rebuilt against the verbatim guard
  text, it died. **Validate the battery before scoring it** — I nearly reported a hole that
  did not exist.
- 🔴 **I fixed a SPELLED guard by writing a spelled guard.** #716's terminator assertion did
  `window.rsplit("if ", 1)[-1]` — splitting on the last literal `"if "` anywhere in the
  window, prose included, then running past the guard's own `fi`. Three mutants walked it,
  and the condition half was satisfied by a guard existing only as a COMMENT. Now
  comment-stripped and bounded at the matching `fi` by depth-counting.
- 🔴 **A mis-classification in a waiver list is worse than an omission** — it reads as
  "considered and cleared". I filed `run-sync.sh`'s `KUBECONFIG` as read-only because
  "kubectl reads"; `sync.py` runs `CREATE TABLE`/`DROP VIEW`/`DELETE`/`INSERT` over it.
  Removed the mechanism rather than patching the entry.
- 🔴 **zsh, twice, both silent:** `mapfile` does not exist, so a liveness scan returned an
  empty set and printed a confident "no live process references it" — caught ONLY by a
  positive control that demanded my own scratchpad appear. And **backticks inside a
  double-quoted string are command-substituted**, which ate several terms from a commit
  message. Use `-F <file>` / `--body-file`.
- 🔴 **A squash merge makes the branch head permanently a NON-ancestor.** Verify a landing
  by CONTENT (`git cat-file -e origin/main:<path>`, grep the guard), never by
  `merge-base --is-ancestor`.
- **`grep` cannot tell a QUOTED claim from an ASSERTED one.** My check for "did the stale
  sentence survive?" found it and was wrong — it survives only inside `It used to
  continue: "…"`, immediately followed by the correction. Read the context.
- **The base clone is genuinely shared** — during this session other sessions landed #715,
  #722, #723, #724, #720, #673, #714, switched its branch to `rules/proactivity-gate`
  mid-operation, and left uncommitted edits to `claude/RULES.md`. **Every commit here was
  made in a contained `/tmp` clone and every push was ref-explicit** (`git push origin
  <local>:<remote>`), which is the only reason none of that mattered.
- **`/tmp/claude-1000` is 52.7 GiB** of agent scratchpads across sessions, and several
  `/tmp/wt-*` worktrees persist. Untouched — other sessions' state.

## How to verify

```bash
# both guards are on main, by CONTENT (a squash merge breaks ancestry forever)
git -C ~/workspace/devrc cat-file -e origin/main:scripts/testlib/gitenv.py      && echo "GUARD 9  present"
git -C ~/workspace/devrc cat-file -e origin/main:scripts/testlib/nogit_plugin.py && echo "GUARD 10 present"
git -C ~/workspace/devrc show origin/main:scripts/run-tests.sh | grep -c 'gitenv_plugin\|nogit_plugin'   # expect 2+

# the seam is closed on the merged tree, not just in one PR
git -C ~/workspace/devrc show origin/main:scripts/testlib/gitenv.py | grep -A2 'REDIRECT BY THE HARNESS'

# #716's six guards
git -C ~/workspace/devrc show origin/main:scripts/ship.sh        | grep -c 'SHIP_REPO+set'    # 2
git -C ~/workspace/devrc show origin/main:scripts/drift-check.sh | grep -c 'DRIFT_REPO+set'   # 3

# gate — ALWAYS in a standalone clone with origin REMOVED (the freeze still applies)
git clone --no-hardlinks -q ~/workspace/devrc /tmp/hchk && git -C /tmp/hchk remote remove origin
git -C /tmp/hchk remote -v          # MUST print nothing before anything runs
nix develop /tmp/hchk --command bash /tmp/hchk/scripts/gate.sh --tier both --set hermetic

# incident aftermath still clean
git -C ~/workspace/devrc config --get core.bare       # <unset>
git -C ~/workspace/devrc config --get core.hooksPath  # <unset>
git -C ~/workspace/devrc reflog show main --date=iso | grep -E 'commit: (c|seed)$' | head -1
# newest must remain 2026-08-21 14:42:12 — anything later is a recurrence
```
