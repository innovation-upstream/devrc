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

- **Branch / PR:** `fix/nebula-verifier-guard-and-rc-classify` @ `9815a3cc` → **PR #1420 OPEN**, `MERGEABLE/CLEAN`, both Tekton checks SUCCESS. ⚠ **10 commits behind `origin/main`** — those checks are a claim about the *branch*, not the merged tree.
- **Worktree:** `/home/zach/workspace/devrc-nebguard` (kept deliberately; six throwaway control worktrees removed).

**DONE this session (all merged + shipped to both hosts):**
- **#1384** `fix(neovim)` — adopt `withRuby=false`/`withPython3=false`. Warning count 2→0 (negative control: unset=2, `false`=0, `true`=0). Generation closure −43,551,224 B. Verified behaviourally: `nvim --headless` reports `ruby=0 py3=0 rubyprog=UNSET` on both hosts.
- **#1398** `docs(secrets)` — `SECRETS.md` said age-keygen echoes secrets on malformed input; false since age 1.3.2. Measured **4/8 leak on 1.3.1, 0/8 on 1.3.2** (same fixtures both binaries; the 4 is the positive control).
- **#1407** `fix(nebula)` — the verifier was exec'd via its `#!/usr/bin/env bash` shebang; the nix sandbox has **no `/usr`**, so 20 tests failed there while the dev-host tier stayed green. Now `run_check() { "$BASH" "$CHECK" "$@"; }`.
- **News:** 410 unread cleared on **both** hosts, archived first to `~/.local/share/home-manager/news-archive-2026-09-08.txt` (3818 lines / 91,087 B each) so the 540 consumed ids are not lost. `read-ids` 0 → 540.
- **#1410 CLOSED, not merged** — obsolete; #1405 fixed the same `h.civit.ai` leak first, deliberately (their comment cites the same guard).

**IN FLIGHT — #1420 (`9815a3cc`), 4 files, +458/−14.** Closes both round-1 audit findings on #1407 and all round-2 findings. Adds: a behavioural spelling-independent guard, `verifier_answered` rc classification at both call sites, 6 battery mutants, 2 `test_runtime_shebangs.py` allowlist entries.

**Deploy/verify status — be precise:**
- `home-manager switch` is **clean on both hosts**, verified by re-running the operator's exact command. Only the upstream `install`→`add` line remains (home-manager's own; they fixed it in #8756 then **reverted** it in #8835 for Lix — issue #9598 open, no PR).
- Both hosts converged + switched at `03d7e0ad` via `ship.sh`.
- **#1420 is NOT merged and NOT deployed.** `nix/system/**` ships only by git checkout and is hand-run under `sudo` — nothing in the flake reads it, so no deploy is pending.

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

## Next steps (ranked)

1. **Decide #1420: merge or close.** Re-gate on the MERGED tree first — the branch is 10 behind, so its green Tekton checks say nothing about the merge result. Build the two nix checks **one at a time** (a combined invocation produces false failures). Repo: `devrc`. Files: the 4 in the PR.
   forcing: none
2. **Diagnose the battery nondeterminism** (block above). This is the highest-value item: it is a shared instrument, a false `SURVIVED` undermines every "mutation-verified" claim in the repo, and the load that triggers it is normal for this box. Repo: `devrc`. Files: `scripts/tests/mutants-nebula-relay.sh`, `scripts/tests/test_nebula_relay_apply.py`.
   forcing: none
3. **`h.civit.ai` is still in git history at `6d488a1b`** on a PUBLIC repo. Fixed forward by #1405, but all four content gates read `git ls-files` and are blind to history (`SECRETS.md` → "Dead credentials in reachable history"). Whether to rewrite is the operator's call and was never made. Repo: `devrc`.
   forcing: security — a client subdomain is published in a public repo's history; the gates that exist cannot see it
4. **`ship.sh` cannot reach the laptop.** It defaults to `zach@192.168.50.155` (LAN), which timed out; nebula `zach@10.42.0.100` works and `host-role.sh` already defines it as `LAPTOP_IP_SECONDARY` but nothing falls back to it. Worked around this session with `REMOTE_SSH=zach@10.42.0.100`. Left unfixed: a bare `ship.sh` reports the laptop unreachable and skips it — the documented silent-drift shape. Repo: `devrc`. Files: `scripts/ship.sh`, `scripts/lib/host-role.sh`.
   forcing: none
5. **Close the audit items recorded as open, not fixed** — no test reaches the post-**switch** verify (site 3; `apply_beside_sequenced_verifier` is two-stage by construction); `test_apply_declares_every_tool_it_execs` still cannot catch an undeclared tool (`head`/`id`/`rm` are exec'd and undeclared, test green — docstring corrected, body unchanged); round-1 nits (closed-set regex spellings, rc-2 ambiguity, unquoted counter path, `apply-tailscale.sh` has no equivalent guard). Repo: `devrc`.
   forcing: none
6. **Three superseded branches with no open PR**, from other sessions: `fix/opencode-engine-pin-1.18.29`, `fix/opencode-pin-1-18-29`, `fix/opencode-pin-1-18-21`. The opencode pin itself IS in `main` (1.18.29, re-derived via #1392), so they look redundant — but they are not this session's to delete. Repo: `devrc`.
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
- **Four sessions collided on one failure this session** (the age 1.3.2 breakage), none using `claim-work` until late. A second collision followed on the `h.civit.ai` leak. `claim-work` only helps whoever checks it first.

## How to verify

**The shipped work (should all pass today):**
```bash
# 1. the switch is clean on both hosts — only the upstream alias line
home-manager switch --flake ~/workspace/devrc --impure 2>&1 | grep -iE "warning|unread|default value of"
ssh zach@10.42.0.100 'home-manager switch --flake ~/workspace/devrc --impure 2>&1 | grep -iE "warning|unread"'
# expect exactly: warning: 'install' is a deprecated alias for 'add'

# 2. neovim providers are really off (ask neovim, not the file)
nvim --headless -c 'echo "ruby=" . get(g:,"loaded_ruby_provider","UNSET") . " py3=" . get(g:,"loaded_python3_provider","UNSET")' -c q
# expect: ruby=0 py3=0
```

**Before merging #1420 — on the MERGED tree, one at a time:**
```bash
R=/home/zach/workspace/devrc
git -C $R worktree add --detach /tmp/wt-1420 origin/main && cp $R/.envrc /tmp/wt-1420/
git -C /tmp/wt-1420 merge --no-edit origin/fix/nebula-verifier-guard-and-rc-classify
nix build /tmp/wt-1420#checks.x86_64-linux.pytests   --no-link   # then, SEPARATELY:
nix build /tmp/wt-1420#checks.x86_64-linux.nodetests --no-link
# read each derivation's own RESULT: line, never the piped exit code:
nix log $(nix path-info --derivation /tmp/wt-1420#checks.x86_64-linux.pytests) \
  | sed 's/\x1b\[[0-9;]*m//g' | grep -E "TOTAL collected|RESULT:"
```
Expect `failed=0` and `RESULT: PASS` on both. Compare `collected` against a base build of `origin/main` alone — a drop means tests were lost, not fixed.

**The battery (expect it to be flaky until item 2 is done):**
```bash
find /tmp -maxdepth 1 -name 'nebula-relay-pre.*' -type f -user "$(id -un)" -delete
uptime   # if load > 10, the full-run aggregate is not trustworthy
nix develop /home/zach/workspace/devrc -c bash /home/zach/workspace/devrc-nebguard/scripts/tests/mutants-nebula-relay.sh
```
A single mutant in isolation IS trustworthy: `… mutants-nebula-relay.sh M-FH-1-verifier-execed-via-shebang`.
