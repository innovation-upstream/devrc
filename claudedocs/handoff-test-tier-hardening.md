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
  🔴 **`CLAUDE.md`'s `<!-- merge-gate: none -->` marker is therefore STALE** — per the
  marker's own rules it should read **`other`** (Tekton is not a GitHub Actions
  `pull_request` workflow). Not yet changed; one-line PR, still owed.
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

**Deploy status:** nothing shipped to either host this session. All of the above is merged
to `origin/main` and NOT deployed — `ship.sh` has not run. Merged ≠ deployed.

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

### 🔴 THE ROOT CAUSE OF THE RECURRENCE IS STILL UNIDENTIFIED — do not close this

- **RETRACTED:** *"`githooks/pre-push` assigns to the exported name `GIT_DIR`, so it hands the base clone's git dir down to `run-tests.sh` → pytest"* was relayed to me as established and is **wrong as stated**. Measured on git 2.55.0: **`git push` exports `GIT_EXEC_PATH`, `GIT_PREFIX=""` and `GIT_EDITOR` to `pre-push` — NOT `GIT_DIR`.** The rename in #683 is a route **only if an outer caller had already exported `GIT_DIR`**; `githooks/pre-push:44-48` states that precondition, and the PR body/commit message drop it. Good hygiene, not the explanation.
- **Live scan, reported as a pair:** **46 processes carry some `GIT_*` var, 0 carry `GIT_DIR`** (13 unreadable ⇒ unmeasured, not zero). No tracked file assigns one.
- So *why pushing appeared to trigger corruption* is open. Nothing found so far protects `homelab-talos` or any other repo's tooling either.

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

### 🟡 OPEN — #683's detector attributes a CONCURRENT SESSION's write to a test
- **Observed, in practice:** a bare `pytest` whose **cwd is the live clone** went red with
  `DEVRC-GITENV-VIOLATION` because another session ran a `git checkout` mid-run —
  `logs/HEAD` is fingerprinted. Re-running passed.
- **Ruled out:** that it was my change (re-ran clean); that it is a gate problem (the gate
  runs in a contained clone, where it cannot happen).
- **Leading hypothesis:** #720's title says the detector "blamed TESTS for a repository with
  30 other writers", so this class is *known* upstream — but whether #720 fully closes the
  concurrent-session case, as opposed to the writer-attribution case, is **NOT verified here**.
- **Next probe:** `git -C <clone> log -1 --format=%H origin/main` then run a bare
  `pytest scripts/tests/test_skill_descriptions.py` with cwd = the live base clone while a
  second session commits; see whether GUARD 9 still fails the running test.

### 🟡 OPEN — #493 reaps ~11% of `/tmp`'s machine-generated entries
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

## Next steps (ranked)

1. **Deploy.** Everything above is on `origin/main` and on NEITHER host. `scripts/ship.sh`.
   🔴 Read every per-host line, not the final verdict.
2. **#493 — add the `nix-shell.*` glob**, then hand the operator
   `sudo bash nix/system/apply-tmp-churn-retention.sh`. Claude cannot apply it.
3. **#632** — `OPEN`, gated green and twice-audited when written, **now ~90 commits behind**.
   Re-gate on the merged tree before believing that.
4. **Verify the concurrent-session detector case** (probe above) — decide whether #720
   closed it or only the writer-attribution half.
5. **#701 (this doc)** — still OPEN. Land it.

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
