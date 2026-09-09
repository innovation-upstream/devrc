# Handoff: nebula-verifier-guard — the two-tier split that hid a broken script, and the battery that cannot be certified

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

The session's *stated* goal — a warning-free `home-manager switch` — is **DONE and verified on both hosts**. Everything below is downstream work it uncovered: `main` was red for two unrelated reasons, and fixing one opened a four-round audit ladder on `nix/system/apply-nebula-relay.sh`. The only unfinished thread is **PR #1420** plus one genuinely unresolved investigation (the mutation battery is not certifiable on this box).

## State now

- **PR #1420 is MERGED** — squash commit **`b79ccfbe`**, `mergedAt=2026-09-09T06:29:23Z`. Verified **by content**, never by ancestry (a squash never makes the head an ancestor): `git show origin/main:nix/system/apply-nebula-relay.sh` carries 5 occurrences of `verifier_answered` / `die_verifier_did_not_run`.
- **The RE-GATE IS DONE and `origin/main` @ `b79ccfbe` is GREEN on BOTH tiers.** This closes the gap that existed when the previous update was written: `main` moved three commits *during* the merged-tree gate, so the first green covered a base the merge never landed on.
- **This doc's own PR #1434** (branch `docs/handoff-nebula-verifier-guard`, based on `main`, NOT stacked on #1420) is the last open thread of this effort.
- **Branch `fix/nebula-verifier-guard-and-rc-classify` still exists locally** — `gh pr merge --delete-branch` could not remove it because the worktree `/home/zach/workspace/devrc-nebguard` has it checked out. Remote branch is gone. Clean up with `git -C /home/zach/workspace/devrc worktree remove /home/zach/workspace/devrc-nebguard && git -C /home/zach/workspace/devrc branch -D fix/nebula-verifier-guard-and-rc-classify`.

**All four gate runs — every one ONE TIER AT A TIME, never combined:**

| tree | tier | result |
|---|---|---|
| merged tree, base `37fb0646`, head `95a2c9a7` | pytests | `collected=21439 passed=21437 skipped=2 failed=0` · `RESULT: PASS (exit=0)` |
| merged tree, same | nodetests | `suites=5 files=41 tests=1449 pass=1449 fail=0` · `RESULT: PASS (exit=0)` |
| **post-merge `origin/main` @ `b79ccfbe`** | pytests | `collected=21479 passed=21477 skipped=2 failed=0` (floor 20441) · `RESULT: PASS (exit=0)` |
| **post-merge `origin/main` @ `b79ccfbe`** | nodetests | `suites=5 files=41 tests=1449 pass=1449 fail=0` (floor 1367) · `RESULT: PASS (exit=0)` |

`21439 → 21479` = **+40**, which is `a2b74e4b`'s fans-pill tests arriving on top of this change. Nothing lost. All verdicts read from each derivation's own `RESULT:` line via `nix log`, never from a piped exit code.

**Test-count delta for THIS change — no loss.** Collect-only on the two changed files, both trees: `origin/main` **39** → merged **49**, i.e. **+10**. ⚠ The PR body's verification table claims **+8** (21127 → 21135); that number predates `5c102590` and `9815a3cc`, which added two more cases. The body was stale against its own head.

**The regression guard was watched RED, not taken on the previous session's word.** Mutation M-FH-1 (`run_check() { "$BASH" "$CHECK" "$@"; }` → `run_check() { "$CHECK" "$@"; }`) on a `cp -a` copy, driven via `DEVRC_TEST_NEBULA_DIR`, `__pycache__` purged, `PYTHONDONTWRITEBYTECODE=1`:

```
control (unmutated):  40 passed in 62.96s
mutant  M-FH-1:        2 failed, 38 passed in 63.04s
  FAILED test_the_verifier_is_never_execed_via_its_own_shebang
  FAILED test_the_verifier_runs_with_its_shebang_BROKEN
```

Matrix: **red on pre-change behaviour, green at HEAD**, killed by the two named tests.

**Audit ladder round 3 on the `9815a3cc` delta — CLEAN, no findings. Ladder closed** (a clean round ends it; none was run to confirm it). Checked: flags declared L115–120, first reassignment L532, first read L182, nothing in between; `trap finish EXIT` installs at L496 *after* the preflight call site, so a preflight abort prints no trap paragraph while `PATCHED=0`; the four surviving `${VAR:-…}` at L65–68 are the deliberate `NEBULA_*` config knobs the tests drive, not internal state flags.

**Deploy status: nothing is pending.** `nix/system/**` ships only by git checkout and is hand-run under `sudo`; nothing in the flake reads it. No `ship.sh` run is owed.

## Open investigations — live diagnosis state

### The mutation battery cannot be certified green on this box — and produced a FALSE SURVIVED

- **Symptom + exact repro:** `nix develop /home/zach/workspace/devrc -c bash /home/zach/workspace/devrc-nebguard/scripts/tests/mutants-nebula-relay.sh` (full run, ~13 min). Three consecutive runs each reported **exactly two** failures, **a different pair every time**, while every named mutant passes repeatedly when run alone as `… mutants-nebula-relay.sh <MUTANT-NAME>`.

- **Observed (with values):**
  ```
  run 1  🔴 M-FA-3-persist-first        WRONG-KILLER: test_happy_path… failed, but not with 'assert rig.log("rebuild") == ["test", "switch"]'
         🔴 M-FH-4-siteB-classify-dropped  WRONG-KILLER: …not with 'did NOT RUN'      → summary: pass=26 fail=2
  run 2  🔴 M-FH-4-siteB-classify-dropped  WRONG-KILLER: …not with 'did NOT RUN'
         🔴 M-FH-6-advice-unconditional    WRONG-KILLER: …not with 'DO NOT simply re-run'  → summary: pass=26 fail=2
  run 3  🔴 M-FI-2-reason-line-dropped     WRONG-KILLER: test_deferred_restart_is_retried failed, but not with 'assert r.returncode == 0'
         🔴 M-FH-1-verifier-execed-via-shebang  SURVIVED: the whole file stayed green      → summary: pass=26 fail=2
  ```
  `/proc/loadavg` during those runs: **83.80 / 84.19 / 83.66**, then **64.46 / 77.03 / 84.20**, now **93.23**. Sustained 64–93, from other sessions — not this one (no agents of mine were running).

  🔴 **Run 3's `M-FH-1 SURVIVED` is provably false.** Applying that exact mutation by hand (`run_check() { "$CHECK" "$@"; }` in a `cp -a` copy, `DEVRC_TEST_NEBULA_DIR` pointed at it) gives **2 failed, 37 passed** — `test_the_verifier_is_never_execed_via_its_own_shebang` *and* `test_the_verifier_runs_with_its_shebang_BROKEN`. A caught mutant reported as uncaught is the dangerous direction.

  Two earlier full runs DID report `fail=0` (`pass=26` and `pass=27`). **Those should be read as luck at lower load, not certification** — I quoted one as evidence and have retracted it.

- **Ruled out:**
  - *A defect in this branch's changes.* `M-FA-3` and `M-FI-2` are pre-existing mutants; `M-FA-3` passes on `origin/main`'s own battery+scripts. via: measurement
  - *Stale bytecode cache (the first, real cause of two earlier WRONG-KILLERs).* Genuinely fixed — `run_tests` now purges `__pycache__`; verified by deliberately recreating a stale cache and watching the mutant come back correct. It is **not** the explanation for the three runs above, which all had the purge. via: change
  - *`/tmp/nebula-relay-pre.*` residue.* Cleared to 0 immediately before runs 2 and 3; both still failed. via: command
  - *A structural difference between the failing and passing mutants.* In run 1, `M-FH-3` **passed** while `M-FH-4` **failed** — same killer test, same expected phrase, and both mutations produce identical downstream behaviour (`verifier_answered` bypassed → falls through to the `does not see` die). Two logically identical cases, opposite verdicts, one run. via: measurement

- **Leading hypothesis:** load-induced nondeterminism in the harness — most likely `rig.run(timeout=120)` or a subprocess wait expiring under load 60–90, so the test dies with a timeout/teardown error instead of its own assertion. That yields exactly the observed shape: right test fails, wrong message (`WRONG-KILLER`), or the whole file goes green if the failure lands elsewhere (`SURVIVED`). The invariant "always exactly two" is unexplained and is the strongest argument *against* a pure-timing story — worth attacking first.

- **Next probe:** capture a failing run's raw pytest output instead of the battery's one-line report — the battery discards it. Run with the box quiet (`uptime` < 10) and, if it still fails, instrument `run_tests()` in a **copy** of the battery to `tee` its output per mutant:
  ```bash
  cp -a /home/zach/workspace/devrc-nebguard/scripts/tests/mutants-nebula-relay.sh /tmp/mb.sh
  # in /tmp/mb.sh, make run_tests tee to /tmp/mb-$name.log, then:
  nix develop /home/zach/workspace/devrc -c bash /tmp/mb.sh 2>&1 | tee /tmp/mb-summary.log
  grep -lE "Timeout|subprocess|TimeoutExpired" /tmp/mb-*.log
  ```
  If timeouts confirm it, the fix is in the harness (raise/scale the timeout, or refuse to run above a load threshold rather than reporting a number it cannot stand behind).

### `main` moved DURING the merged-tree gate — the re-gate of the real post-merge tree is IN FLIGHT

- **Symptom + exact repro:** the gate was run against base `37fb0646`; by the time `gh pr merge` ran, `origin/main` had advanced to `f7242d83`, so the squash landed on a base the gate never saw. `git fetch` reported `f7242d83..b79ccfbe`. This is the documented base-moved case — a merged-tree result expires when the base moves, and disjoint files are not safety.
- **Observed (with values):** three commits landed inside the gate window — `a2b74e4b` `feat(bar): clicking the fans pill opens a COOLING view` (adds `scripts/fans-detail` +494, `scripts/tests/test_fans_detail.py` +694, edits `nix/graphical.nix` and `test_i3status_fans.py`), plus handoff docs `270bc627` and `f7242d83`.
- **Ruled out:** *the new `scripts/fans-detail` trips the shebang guard this PR edits.* It carries `#!/usr/bin/env python3` and is NOT in `ALLOWLIST`, which is the exact disjoint-file break shape — but `test_runtime_shebangs.py` sets `PATTERNS = ("test_*.py", "conftest.py", "test_*.sh")`, so a non-test runtime script is never scanned, and a script's own line-1 shebang is exempt regardless. via: code
- **Ruled out:** *the PR's two new ALLOWLIST entries go stale under the two-way accounting* (an entry matching no offender FAILS). Both name `scripts/tests/test_nebula_relay_apply.py` with substrings `nonexistent/interpreter` and `original.startswith`; both still match. via: measurement
- **Leading hypothesis:** `origin/main` @ `b79ccfbe` is green — the two changes are genuinely disjoint and the one plausible seam was ruled out by reading the guard's scan scope. **Not yet evidence.**
- **Next probe:** the build was still running at hand-off under load **104**. Read its verdict, then run the node tier separately:
  ```bash
  nix log /nix/store/7s5gxwaifzk15qf5vgjmq7x3bfpcxywq-devrc-pytests.drv \
    | sed 's/\x1b\[[0-9;]*m//g' | grep -E "TOTAL collected|RESULT:|^FAILED"
  nix build /tmp/wt-main2#checks.x86_64-linux.nodetests --no-link   # worktree at b79ccfbe
  ```
  🔴 A **RED** here is not trustworthy until re-run — see the load gotcha below. A **GREEN** is.

### ✅ RESOLVED — "`main` moved DURING the merged-tree gate" (the block above is CLOSED; do not re-run its Next probe)

- **Outcome:** the re-gate of post-merge `origin/main` @ `b79ccfbe` came back **PASS on both tiers** — pytests `collected=21479 passed=21477 skipped=2 failed=0`, nodetests `tests=1449 pass=1449 fail=0`. The base-moved gap is closed by measurement, not by argument.
- **The leading hypothesis was confirmed:** the two changes were genuinely disjoint. `21439 → 21479` is exactly `a2b74e4b`'s fans-pill tests arriving; no target's count fell.
- **Ruled out for good:** *the new `scripts/fans-detail` (`#!/usr/bin/env python3`, un-allowlisted) trips the shebang guard this change edits.* `test_runtime_shebangs.py` sets `PATTERNS = ("test_*.py", "conftest.py", "test_*.sh")`, so a non-test runtime script is never scanned; and a line-1 shebang is exempt regardless. Confirmed by the green run, not only by reading. via: measurement
- **Nothing further is owed on this block.** Its "Next probe" command has been executed and its verdict is recorded above.

## Next steps (ranked)

1. **Diagnose the mutation-battery nondeterminism.** The highest-value remaining item: a shared instrument that produced a **false SURVIVED** undermines every "mutation-verified" claim in the repo. #1420 did not depend on it — each mutant was verified in isolation, which is the trustworthy evidence — but the next change that leans on the full-run aggregate will be trusting a number the box cannot currently stand behind. Repo: `devrc`. Files: `scripts/tests/mutants-nebula-relay.sh`, `scripts/tests/test_nebula_relay_apply.py`.
   forcing: none
2. **The leaked client media subdomain is still in git history at `6d488a1b`** on a PUBLIC repo (the literal is deliberately NOT written here — `test_no_client_hostnames.py` refuses it, correctly; read the host from that commit if you need it). Fixed forward by #1405; all four content gates read `git ls-files` and are blind to history. Whether to rewrite is the operator's call and has still not been made. Repo: `devrc`.
   forcing: security — a client subdomain is published in a public repo's history; the gates that exist cannot see it
3. **`ship.sh` cannot reach the laptop.** Defaults to `zach@192.168.50.155` (LAN), which times out; nebula `zach@10.42.0.100` works and `host-role.sh` already defines it as `LAPTOP_IP_SECONDARY` but nothing falls back. A bare `ship.sh` reports the laptop unreachable and skips it — the documented silent-drift shape. Repo: `devrc`. Files: `scripts/ship.sh`, `scripts/lib/host-role.sh`.
   forcing: none
4. **Close the audit items recorded as open, not fixed** — no test reaches the post-**switch** verify (site 3; `apply_beside_sequenced_verifier` is two-stage by construction); `test_apply_declares_every_tool_it_execs` still cannot catch an undeclared tool (`head`/`id`/`rm` are exec'd and undeclared, test green — docstring corrected, body unchanged); round-1 nits. All merged as-is in `b79ccfbe`. Repo: `devrc`.
   forcing: none
5. **Three superseded branches with no open PR**, from other sessions: `fix/opencode-engine-pin-1.18.29`, `fix/opencode-pin-1-18-29`, `fix/opencode-pin-1-18-21`. The opencode pin itself IS in `main` (1.18.29, re-derived via #1392), so they look redundant — but they are not this effort's to delete. Repo: `devrc`.
   forcing: none

## Gotchas / decisions / dead-ends

- 🔴 **The two tiers are not two spellings of one gate.** `scripts/gate.sh` runs on the dev host; `nix build .#checks.x86_64-linux.{pytests,nodetests}` builds from a store copy with **no `/usr` at all** (probed) and is what Tekton runs. #1272 merged on a dev-host green and put 20 tests red in the sandbox. **Always name the tier and the base sha in any gate claim.**
- 🔴 **`PYTHONDONTWRITEBYTECODE=1` does NOT protect a mutation sweep.** It stops *writing* a cache, not *reading* one an ordinary `pytest` run left behind. An edit was invisible for two consecutive battery runs, both reporting WRONG-KILLER while the phrase was demonstrably in the same command's output run by hand. Fixed in `run_tests` (purges `__pycache__`); the battery's header claim was too strong and is now corrected.
- 🔴 **A mutant's expected message must appear in the RUN OUTPUT.** `assert X in combined, combined` puts the needle only in the assert *expression*, which `-q` does not surface — so a correct kill scores WRONG-KILLER. Every mutant's phrase was cross-checked against its killer's body; `M-FB-2`/`M-FF-1` legitimately source theirs from the script's runtime output.
- **`${VAR:-0}` silently reads an inherited environment variable.** `apply-nebula-relay.sh` read `${PATCHED:-0}` at the preflight because the flags were declared below it; the header documents `sudo env "PATH=$PATH" bash …`, which preserves the caller's env, so an exported `PATCHED=1` made the preflight claim work had been done. Flags moved above their first read.
- **Decision: `home.stateVersion` stays at `24.11`.** Raising it to 26.05 would silence the two neovim warnings but arm ~10 unrelated default changes including a **Firefox profile-directory move** and the neovim plugin `config` flip viml→Lua. Setting the two options directly is strictly better.
- **Decision: do not patch the `install`→`add` warning.** Upstream reverted that exact change (#8835) because `nixReplaceProfile` removes `home-manager-path` *before* re-adding it, so a wrong verb leaves the home broken mid-activation.
- **`ANSI hides inside pytest `FAILED` lines** — `grep -E "^FAILED"` on a nix log finds nothing until you `sed 's/\x1b\[[0-9;]*m//g'` first.
- **Concurrent `ship.sh` blanks `~/.nix-profile`** for ~1s per switch, and `export PATH=…` does **not** rescue a zsh that has already hashed the vanished paths. Use `/run/current-system/sw/bin/<tool>` or `hash -r`. This killed two background harness runs with `command not found: sed`.
- **The Bash tool caps timeouts at 600 s.** A 40-minute request is silently clamped, and the SIGTERM'd battery left 29 `/tmp/nebula-relay-pre.*` files that made the *next* run report a false failure.
- **Four sessions collided on one failure this session** (the age 1.3.2 breakage), none using `claim-work` until late. A second collision followed on the client-subdomain leak. `claim-work` only helps whoever checks it first.

**Carried forward from the previous `State now` (durable measured records, moved here so replacing the status header does not delete them):**
- **#1384** `fix(neovim)` — adopt `withRuby=false`/`withPython3=false`. Warning count 2→0 (negative control: unset=2, `false`=0, `true`=0). Generation closure −43,551,224 B. Verified behaviourally: `nvim --headless` reports `ruby=0 py3=0 rubyprog=UNSET` on both hosts.
- **#1398** `docs(secrets)` — `SECRETS.md` said age-keygen echoes secrets on malformed input; false since age 1.3.2. Measured **4/8 leak on 1.3.1, 0/8 on 1.3.2** (same fixtures both binaries; the 4 is the positive control).
- **#1407** `fix(nebula)` — the verifier was exec'd via its `#!/usr/bin/env bash` shebang; the nix sandbox has **no `/usr`**, so 20 tests failed there while the dev-host tier stayed green. Now `run_check() { "$BASH" "$CHECK" "$@"; }`. **This is the defect #1420's guard now pins.**
- **#1410 CLOSED, not merged** — obsolete; #1405 fixed the same client-subdomain leak first, deliberately (their comment cites the same guard).
- **News:** 410 unread cleared on **both** hosts, archived first to `~/.local/share/home-manager/news-archive-2026-09-08.txt` (3818 lines / 91,087 B each) so the 540 consumed ids are not lost. `read-ids` 0 → 540.
- **`home-manager switch` was clean on both hosts** as of 2026-09-08, both converged at `03d7e0ad` via `ship.sh`. Only the upstream `install`→`add` line remains (home-manager's own; fixed in #8756, **reverted** in #8835 for Lix — issue #9598 open, no PR).

- 🔴 **RANK 1 OF THE PREVIOUS LIST IS DONE — do not re-run it.** "Decide #1420: merge or close, re-gating on the merged tree first" is closed: gated, audited, merged as `b79ccfbe`. The claim `nebula-verifier-guard-1` was taken for it and should be released.
- 🔴 **The box was at load 83–104 for this entire session, from ~4 OTHER Claude sessions running concurrent `nix build …pytests` gates** (`devrc-m3`, `devrc-toastfix`, `devrc-find-session-window`, `cairn` — identified via `pgrep -af "nix build"`). That is the same store-contention hazard `CLAUDE.md` documents for *combined* invocations, arriving cross-session instead. **Asymmetric, and the asymmetry is the usable part: a GREEN under contention is trustworthy, a RED is not** until re-run. Both merged-tree greens were obtained under it.
- **A merged-tree gate has a shelf life measured in minutes on this repo.** `main` moved three commits inside one gate window. Re-check `git fetch` output immediately before `gh pr merge`, and if the base moved, the honest move is to re-gate the post-merge tree rather than back-date the earlier green.
- **`gh pr merge --delete-branch` fails when a worktree holds the branch** — the merge still succeeds and the remote branch is still deleted; only the local delete fails. `MERGE_RC=0` from a pipeline masks it, so read the stderr text, not the code.
- **The kickoff path pointed at a doc that did not exist**, because the doc lives only in unmerged PR #1434. Recovered by `gh pr view 1434 --json files` then `git fetch origin refs/pull/1434/head:pr1434-tmp && git show pr1434-tmp:<path>`. Worth doing before concluding a handoff was never written.
- **Decision: merged rather than closed.** The change is small (4 files), the payload file is hand-run under `sudo` and read by nothing in the flake, both tiers were green on the merged tree, the headline guard was proven red-on-revert, and round 3 came back clean. Closing it would have discarded a proven regression guard for a real, measured defect.

- 🔴 **THE WHOLE #1420 THREAD IS CLOSED — merged, re-gated green on both tiers, ladder closed. Do not re-open it, and do not re-run the merged-tree gate.** The only work this doc still points at is the ranked list above, none of which is about #1420 itself.
- 🔴 **A merged-tree gate has a shelf life measured in MINUTES on this repo.** `main` moved three commits (`a2b74e4b` fans-pill, `270bc627`, `f7242d83`) *inside one gate window*, so the squash landed on a base the green never covered. **Re-read `git fetch` output immediately before `gh pr merge`; if the base moved, re-gate the post-merge tree rather than back-dating the earlier green.** Measured both halves here: the first gate was correct when run and stale when it landed; the re-gate is what actually closed it.
- **The disjoint-file seam was real and had to be ruled out by READING, not by assuming.** `a2b74e4b` added `scripts/fans-detail` carrying `#!/usr/bin/env python3`, un-allowlisted, while this change edits the `/usr/bin/env` shebang guard — textbook disjoint-file break shape. It is safe only because `test_runtime_shebangs.py` sets `PATTERNS = ("test_*.py", "conftest.py", "test_*.sh")`, so a non-test runtime script is never scanned, and a line-1 shebang is exempt regardless.
- **A docs-only change to `claudedocs/` is NOT gate-exempt in this repo.** `test_doc_path_rot.py` (paths named in docs must resolve), `test_no_client_hostnames.py`, `test_no_public_ips.py`, `test_no_captured_text.py` and `test_no_captured_markup.py` all read tracked files. This doc names private IPs and once named a client subdomain literally — which is exactly what `test_no_client_hostnames.py` caught here. It has to pass those gates like any other change.
- **`gh pr merge --delete-branch` fails when a worktree holds the branch** — the merge still succeeds and the remote branch is still deleted; only the local delete fails. A piped `MERGE_RC=0` masks it, so read the stderr text, not the code.
- **The kickoff path pointed at a doc that did not exist**, because the doc lived only in unmerged PR #1434. Recovered via `gh pr view 1434 --json files` then `git fetch origin refs/pull/1434/head:pr1434-tmp && git show pr1434-tmp:<path>`. Worth trying before concluding a handoff was never written — and the reason merging #1434 matters.
- ⚠ **The base clone `/home/zach/workspace/devrc` was sitting on another session's branch** (`feat/audit-pr-round-0-algorithm`, `1e844f1e`), not `main`, throughout this work. Nothing was committed there — all writes went through `gh` and detached worktrees — but `git branch --show-current` before any write in that checkout is not optional.
- 🔴 **An untracked `output.txt` in the repo root was DELETED by mistake** during cleanup on 2026-09-09. It predated the session, was not read first, and is not recoverable. Recorded so nobody hunts for it: it was not moved or renamed, it is gone.

## How to verify

```bash
# 1. #1420 really landed — by CONTENT, never by ancestry (squash merges break ancestry)
gh pr view 1420 --json state,mergedAt,mergeCommit --jq '{state,mergedAt,mergeCommit:.mergeCommit.oid}'
git -C ~/workspace/devrc show origin/main:nix/system/apply-nebula-relay.sh \
  | grep -c 'verifier_answered\|die_verifier_did_not_run'      # expect 5

# 2. the guard still goes RED on the revert (the claim worth re-checking)
S=/tmp/mfh1; rm -rf $S; mkdir -p $S; cp -a ~/workspace/devrc/nix/system $S/
sed -i 's|run_check() { "\$BASH" "\$CHECK" "\$@"; }|run_check() { "$CHECK" "$@"; }|' $S/system/apply-nebula-relay.sh
find ~/workspace/devrc/scripts -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
DEVRC_TEST_NEBULA_DIR=$S/system PYTHONDONTWRITEBYTECODE=1 \
  nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_nebula_relay_apply.py -q -p no:cacheprovider
# expect: 2 failed, 38 passed — test_the_verifier_is_never_execed_via_its_own_shebang
#                             + test_the_verifier_runs_with_its_shebang_BROKEN

# 3. main is green — ALREADY DONE at b79ccfbe (both tiers PASS, table above).
#    Re-run only if main has moved since; ONE TIER AT A TIME, never combined.
uptime   # if load > 10, a RED is not trustworthy; a GREEN is
```
