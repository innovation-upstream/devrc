# Handoff: test-tier-hardening — 2026-08-21

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

**Branch:** base clone on `main` @ `295753ae`, **behind origin/main by 2**. Three untracked
files, none mine (`handoff-espanso-audit-gate.md`, two `nix/system/apply-*.sh`).

**DONE — merged AND shipped, all seven verified present after the wipe+restore:**

| PR | what |
|---|---|
| #579 | `tmux-scratch-slots.sh` documented an i3 chord bound to nothing |
| #582 | `session-write` — the four tmux write verbs behind one validated wrapper |
| #601 | seven holes in #582's test battery |
| #608 | scanner skip-lists — a live red gate, 2 of 4 copies stale |
| #611 | `grep -r` honours `.gitignore` → `claude/RULES.md` |
| #620 | `ship.sh` verified artifacts RESOLVE but not that they are CURRENT (rc 13) |
| #628 | `test_liveness.py` flake — timing dependency removed |

The **original objective is complete and validated**: `scripts/session-write` is in
`origin/main`, all four verbs present, and the audited `\x0f` bypass is REFUSED on this
host while a legitimate payload is accepted (exercised on the live path, not inferred).

**IN FLIGHT / PARKED:**

- **#630** — merged at `19:28:30Z` *inside the wipe window*; the restore rolled `main` back
  past it. GitHub says MERGED, `main` does not contain it. Branch survives at `bd05708d`.
  Its enforcement fix is written but **was never pushed** — see the salvage note below.
- **#632** — `OPEN MERGEABLE`, gated green, two audit rounds clean. Parked on the freeze only.
- **#676** — `OPEN MERGEABLE`, **needs rework**, seven measured bypasses.
- **#689** — another session's runner-layer guard. Not mine. Also unmerged.

**Deploy status:** nothing shipped since the seven above. Both hosts were converged and
consumer-verified at that point.

🔴 **A FREEZE is in force**: do not run `scripts/run-tests.sh` or
`nix build .#checks.x86_64-linux.pytests` against `~/workspace/devrc` or any worktree of it.
Gate in a **standalone clone with `origin` removed** — disposable, therefore safe.

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

## Next steps (ranked)
1. **Land `d43e425a` ONLY, from `salvage/630-enforcement-2026-08-21`** — **not** `7de0e21b`,
   which #696 superseded (see the RESOLVED block above; same three files, so it conflicts).
   Re-run **both** tiers in a contained clone first. Then push to
   `fix/managed-path-wrong-writer` and **re-merge**. Re-verified against the current
   `origin/main` (`454550a`), by content and not ancestry: `scripts/tests/test_githooks_
   install.py` is **ABSENT** and `CODE_RE` still reads `^(scripts/|flake\.nix$|flake\.lock$)`
   with no `nix/` — so GitHub still claims work exists that `main` does not contain.
2. ~~Fix `main`'s hermetic red~~ — **DONE by #696 `6439921`, already on `main`.** Nothing owed.
3. **#676 rework** — 🔴-4 first (the guard is worse than no guard on that path), then 🔴-1/-2,
   then 🔴-5 by name across 181 entries, verified on both hosts.
4. **#632** — unblock when the freeze lifts. It is green and twice-audited; nothing else is owed.
5. **Decide the freeze-lift sequence.** Suggested: branch protection (done) → stop unowned tier
   runs → repair holds for a measured interval → guards. The guards are the last step, not the gate.

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

## How to verify
```bash
# the shipped objective, on the live path (not inferred from the deploy)
python3 - <<'PY'
import importlib.machinery, importlib.util, sys
p="/home/zach/workspace/devrc/scripts/session-write"
s=importlib.util.spec_from_loader("sw", importlib.machinery.SourceFileLoader("sw",p))
m=importlib.util.module_from_spec(s); sys.modules["sw"]=m; s.loader.exec_module(m)
print("ctrl-O:", "REFUSED" if m.validate_text("echo X\x0f") else "ACCEPTED")
print("legit :", "accepted" if m.validate_text("restart the poller") is None else "refused")
PY

# the salvaged #630 work is preserved
git -C ~/workspace/devrc log --oneline -3 salvage/630-enforcement-2026-08-21

# main's hermetic red — reproduce in a CONTAINED clone, never the real one
git clone --no-hardlinks -q ~/workspace/devrc /tmp/hchk && git -C /tmp/hchk remote remove origin
git -C /tmp/hchk remote -v            # MUST be empty before anything runs

# incident aftermath still clean
git -C ~/workspace/devrc config --get core.bare      # <unset>
git -C ~/workspace/devrc config --get core.hooksPath # <unset>
git -C ~/workspace/devrc reflog show main --date=iso | grep -E 'commit: (c|seed)$' | head -1
# newest must remain 2026-08-21 14:42:12 — anything later is a recurrence
```
