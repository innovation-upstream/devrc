# gate-speed / CI-signal — CLOSED investigation blocks

🔴 **EVICTED FROM `claudedocs/handoff-gate-speed-and-ci-signal.md` on 2026-09-13** because that doc
went over its size allowance and `test_no_handoff_doc_exceeds_its_budget` turned `main` RED. Every
block below is a question that got ANSWERED — the first remedy that test's own playbook prescribes.

⚠ **A `refs/` file is NOT indexed by `handoff_search`.** That is the trade, and it is why only
CLOSED, DATED material is here and never an open thread. Nothing below is live; each block records
how a question was settled, so a future session does not re-run the probe.

### RANK 2: #1469's audit ladder has not reached a clean round
- **Symptom + exact repro:** `scripts/main-status-watch.py` (839 lines) ships and runs every 10
  minutes on both hosts, but its audit ladder never terminated. Round 3 never ran, and a
  95-mutant enumerated sweep died mid-run with its verdict unknown.
- **Observed (with values):** the dead sweep had already surfaced one genuine survivor —
  `print_header`'s sentinel branch — which the two earlier hand-written sweeps (self-reported
  24/24 and 31/31) would never have included. Whether that survivor is fixed is **unknown as of
  this writing**; confirming it is the first step of the in-flight agent's task.
- **Ruled out:** "the hand-written sweeps were adequate, just unlucky" — falsified twice:
  independent samples found survivors after both self-reported perfect scores, and one audit
  found two survivors the author's own table did not contain. via: measurement
- **Ruled out:** "capacity/load explains the sweep dying" — not investigated, and not assumed
  either; the verdict was simply never read. via: assumed
- **Leading hypothesis:** the enumerated sweep will find further survivors in the RC-code and
  state-mutation families, because those are exactly what a hand-picked list omits.
- **Next probe:** read the in-flight agent's survivor table when it reports. If it did not
  finish, re-run the enumerated sweep under `PYTHONDONTWRITEBYTECODE=1` with a known-killed
  positive-control mutant in every batch, and report the table rather than a score.

### RANK 10: #1600's merged-tree verdict is UNREAD — the run did not finish in session
- **Symptom + exact repro:** `#1600` is green on its own branch at a base 24 commits behind `main`.
  The question is whether the merge it creates is green. Repro:
  `bash /tmp/.../scratchpad/mt1600-run.sh` — or rebuild it: worktree off `origin/main`, merge
  `origin/pr-1600`, run every test file that reads `nix/home.nix` plus the PR's own new file.
- **Observed (with values):** merge is textually clean — `Auto-merging nix/home.nix`, ort strategy,
  `+450/-0` across 2 files, merge commit `01418433` on branch `mt/1600-merged` in worktree
  `/home/zach/workspace/devrc-mt1600`. Selector found **72 test files**. Run reached **~35%** with
  **zero failures so far** before the session ended. `origin/main` did not move during the run
  (still `7e000e6b`), so the merged tree tested is the merged tree that would land.
- **Ruled out:** "the three green checks settle it" — they are a claim about the PR branch at a
  stale base; a clean textual merge is not a clean merge, and the at-risk surface here is a new
  systemd unit against unit ledgers that live in OTHER files. via: code
- **Ruled out:** "`scoped-tests.sh` can answer this" — it exits **4** on `nix/**`, a declared shared
  surface, precisely because its mapper selects only files that NAME what changed and drops every
  target reaching it through an import. via: doc
- **Leading hypothesis:** it passes. The 35% already covered includes much of `scripts/tests/`, and
  the unit is additive with its own master switch. Stated as a hypothesis because **the verdict was
  never read** — this is exactly the "deployed ≠ verified" shape one level up.
- **Next probe:** re-run the script and read `PYTEST_RC=` from
  `scratchpad/mt1600.log`. 🔴 **Read the CONTENT, not the wrapper's exit code** — see the Gotchas
  entry below; the first run of this very script reported wrapper exit 0 over `PYTEST_RC=4`.

### 🔴 RANK 10 SHIPPED TO NEITHER HOST — the workbench is blocked by ANOTHER SESSION'S LIVE WIP
- **Symptom + exact repro:** `bash ~/workspace/devrc/scripts/ship.sh` → **rc 7**. Workbench line:
  `SKIPPED — cannot fast-forward to origin/main: could not switch to the main branch`, blocking file
  `claude/skills/clawgate/SKILL.md`. Re-check with
  `systemctl --user list-timers stale-base-triage.timer --all` (workbench: 0 timers, unit absent).
- **Observed (with values):** the blocking file is **genuinely live WIP, not a stale orphan** — its
  working copy sha1 `978ad806` matches **none** of the last 8 commits of that path, and its mtime was
  **2 minutes old at the moment it was read** (`17:23:38`, read `17:25:49`). The diff is three
  security-relevant RETRACTIONS to the clawgate skill: the LAN UI is *not* open (`/tasks/1` → 303 →
  `/login`), `DELETE /api/tasks/{id}` is `requireHookToken` not unauthenticated (`server.go:699`),
  and a credential-less `POST /agents` on the LAN returns **401**. Someone is writing that file now.
- **Ruled out:** "`ship.sh` failed, so nothing deployed" — the LAPTOP leg SUCCEEDED
  (`fast-forwarded main 7e000e6b -> f99d3c1b`, `552 checked, 0 dangling`, `✅ VERIFIED … + switched`).
  Reading only the final `rc=7` would have gotten this backwards in both directions. via: command
- **Ruled out:** "the laptop got the switch, so the sweep is running there" — its timer is
  `UnitFileState=linked` with **0 timers listed** and `journalctl` `-- No entries --`, because the
  `Install` block is `serverMode`-gated and the laptop has no `~/.server-mode`. That is CORRECT
  behaviour, not a second bug. via: command
- **Ruled out:** "just `git checkout origin/main -- <file>` and re-run ship" — that is the remedy
  `ship.sh` itself prints, and here it would **destroy another session's uncommitted work while it is
  mid-edit**. `claude/RULES.md` names docs-in-a-working-tree as unsaved work and names writing into a
  tree another process is writing as its own hazard. **Deliberately NOT taken.** via: code
- **Leading hypothesis:** nothing is wrong with `#1600`. This is purely the documented
  diverged/dirty-host failure mode, and it will clear on its own once the other session commits or
  parks that file — at which point `ship.sh` fast-forwards the workbench normally.
- **Next probe:** re-check whether the file is still dirty, and only then re-ship:
  `git -C ~/workspace/devrc status --short claude/skills/clawgate/SKILL.md` → if clean,
  `bash ~/workspace/devrc/scripts/ship.sh` and **read every per-host line, not the final verdict**,
  then `journalctl --user -u stale-base-triage -n 40 --no-pager` **on the workbench**.
  🔴 If it is STILL dirty, that is the other session's to resolve — hand it over, do not clear it.

### 🔴 RANK 13: the triage bot's FIRST live sweep produced a FALSE `INHERITED`, on a structural blind spot
- **Symptom + exact repro:** `journalctl --user -u stale-base-triage --no-pager | grep -A14 'PR #1603'`
  on the workbench. It ruled `devrc#1603` **INHERITED — likely cured by rebase**. It was not: the red
  was caused by the PR's own diff, and a rebase would not have touched it.
- **Observed (with values):** the bot's stated evidence was
  `scripts/tests/test_runtime_shebangs.py` *is byte-identical at the PR head and the merge-base*
  (`blobs: merge-base 91f2054bd73b  head 91f2054bd73b  main 4e44053c20c2`) *and main has moved it in
  1 commit absent from the head* — `cfdb38997ba4`. **That commit's entire change to that file is an
  18-line ALLOWLIST ENTRY for `scripts/tests/test_nvim_octo.py`**, an unrelated file. It could not
  have cured `#1603`. The true cause was `#1603`'s own new controls in `test_census_scan.py` writing
  env-resolved shebangs; replacing them with `testlib.mockbin.write_exec` turns the guard green
  (9 passed), which is direct causal evidence rather than correlation.
- **Ruled out:** "the verdict was right and my fix was unnecessary" — the guard fails at `8a88f255`
  and passes at `323c6b6b`, with `main` held constant. The cause is in the PR. via: measurement
- **Ruled out:** "a rebase would have cured it anyway" — the only commit `main` had on that file is
  an allowlist row naming a different file; nothing in it reaches `test_census_scan.py`. via: command
- **Ruled out:** "this is a one-off / bad luck" — the mechanism is structural, see below.
  via: code
- **Leading hypothesis — and it is precise.** The bot's INHERITED test is *"the failing TEST FILE is
  unchanged in my branch, and `main` moved it in commits I lack."* That is sound for a test which
  exercises code it names, and **systematically wrong for a repo-wide CENSUS/SCANNER guard**, where
  the test file scans OTHER files and the offending change lives somewhere else entirely. For that
  whole class, "the guard file is unchanged in my branch" is the NORMAL state of a genuine breakage,
  so the heuristic fires exactly backwards. 🔴 **This repo is dense with that class** —
  `_KILL_MENTION_LEDGER`, `_OWN_BOUND_LEDGER`, `test_runtime_shebangs.py`, and the targets
  `#1603` itself was built to screen. It is the same class rank 9 records as "fixed in both known
  instances, unaddressed as a class". **So the bot's worst false-positive mode coincides with this
  repo's most common way of reddening `main`.**
- **Next probe:** hand-check the other four INHERITED verdicts from this sweep
  (`#1450 #1286 #1194 #1038`) against the same question — *is the named failing test a census/scanner
  guard over files it does not name?* That converts one confirmed false positive into a RATE, which
  is what the arming decision actually needs. ⚠ **Do not assume the other four are also false** —
  `#1450` is 174 commits behind and may well be genuine; only `#1603` has been checked.


## CLOSED dated sections, evicted 2026-09-13 (second pass)

🔴 Evicted when the budget warning shipped in `#1648` fired on its own author's write — 873 B of
headroom left — and said evicting now is cheaper than doing it under a red `main`. Both sections
below belong to ranks that are CLOSED (2 and 3). ⚠ The `#792` review section was deliberately NOT
evicted: rank 8 is LIVE and dated 2026-09-18, and that section is its arming evidence.

### 2026-09-11 — verifying rank 2, and four instrument failures in one session

- 🔴 **`git rev-parse $ref:path` in zsh returns a CONFIDENT WRONG 40-char sha.** `$r` followed by
  `:s` is eaten as a **history substitute modifier**, so the command never asks what you think.
  It printed a plausible blob id that made a file look CHANGED across three refs when
  `git ls-tree` showed one identical blob (`c342720d`). **Brace it (`${r}:path`) or use
  `git ls-tree`.** This is the documented unbraced-var trap, hit while actively reading the rule.
- 🔴 **A mutation that edits a COMMENT reports SURVIVED having never run.** `classify`'s
  fall-through was mutated with `.replace('return "error-other"', …, 1)` — and the FIRST
  occurrence in the file is inside a comment *quoting that literal as prose*. The sweep was green
  and meaningless. **Mutate by LINE NUMBER with an assert on the line's exact content.** Redone
  properly, both fall-throughs are KILLED by named tests
  (`test_an_UNRECOGNISED_state_is_never_green_end_to_end`).
- 🔴 **zsh does not word-split, so `pytest $SEL` passed 50 paths as ONE argument** — pytest errored
  `file or directory not found`, ran **`no tests ran in 0.00s`**, and the pipeline still **exited
  0**. A merged-tree gate that observed nothing and reported success. Use an array, or `${=SEL}`,
  and **assert the collected count moved** before believing a green.
- 🔴 **I destroyed my own control by removing its worktree while it was still running**
  (`FileNotFoundError: …/wt-control`, and it exited **0** anyway). The decision did not change
  because the discriminating run had already finished — but that is luck, not justification.
  **Do not tear down a tree a background job depends on.**
- **A 1-of-4049 merged-tree failure was LOAD, and the mechanism was identified rather than
  re-run-until-green.** `test_no_real_launchers.py::test_autouse_is_what_protects_a_test_that_
  never_asks` died on a 300s `TimeoutExpired` on a *nested* pytest inside a run that took 3090s;
  the same file alone on the same merged tree passes **80/80 in 254s**. ⚠ It was NOT dismissed on
  shape: that file references `main-status-watch` four times, #1469 added its `NOLAUNCH_ACK` row,
  and `ffe4e5d0` is precedent for this exact file breaking on a merge with main. The launcher
  ledger's one real citation into the rewritten test file
  (`test_the_trigger_verb_is_MUTATING_so_the_stub_fails_it_closed`) was checked and RESOLVES.
- 🔴 **A PR green on its own branch is not a merged-tree claim, and #1502 was 12 commits behind.**
  Zero file overlap and a clean textual merge — which is NOT safety — so the at-risk surfaces
  (every test reading `CLAUDE.md`, the disk-accounting ledger, `scoped-tests`) were run on the
  actual merged tree before merging.
- **`#1502`'s own ladder repeated the arc's headline lesson twice more.** Round 4 caught round 3
  **claiming a fix it had not made** — the PR body had been written from the finding list rather
  than the diff. Round 5 then found three of round 3's own measured numbers stated **wider than
  measured** ("21 mutants" → 16; "every mutant of classify's arms" → 35 of 60; "ZERO coverage" →
  10 of 11). A fix round's own prose remains the likeliest next finding.
- **A 100% kill rate is a broken harness, and was reported as such.** One verification sweep
  returned 439/439 KILLED **with a RED negative control**; the whole 457-run sweep was voided
  rather than reported. A span self-check also caught 1 mutant of 440 whose edit landed inside an
  f-string and never made the change it claimed — reported VOID, not SURVIVED.
- 🔴 **CLAUDE.md said `main-status-watch` was "NOT LIVE UNTIL A SWITCH" for a full day after
  `ship.sh` had converged both hosts to `86b1ddec`.** The sentence was written before the deploy
  and nothing re-read it. Corrected in `#1502` to a MEASURED liveness claim that names its own
  re-measurement command. **A ⏳ "not live yet" note is a claim with an expiry that nothing prints.**
- ⚠ **#792's dry-run soak cannot validate the guard its own best evidence needs.** devrc **#1500**
  merged **14 seconds after** the sweep pod started — i.e. mid-tick, exactly the race the re-read
  guard exists for. Dry-run short-circuits `cancel_all` BEFORE the re-read, so the soak shows the
  SELECTOR is right while structurally never exercising the WRITE-path guard. Weigh that when
  deciding rank 8.

### 2026-09-11 — rank 3: a ranked item whose premise was wrong in both directions

- 🔴 **"Needs a diagnosis" was FALSE — the diagnosis was already in the file, and excellent.** The
  suite's own classifier had printed `MECHANISM = SERVER_BLOCKED_IN_FSYNC … accept loop parked=True`
  on run `devrc-ci-86zxj`: `server.py:_replace_bytes` fsyncs the file and then the parent dir
  **inside the request, before the response is written**, and fsync blocks in uninterruptible sleep
  — which is exactly the captured `TimeoutError` inside `socket.recv_into` (connection ESTABLISHED,
  never answered). **Read the target file before believing a handoff's characterisation of it.**
- 🔴 **AND IT WAS ALREADY FIXED, by a PR nobody connected to it.** `#1458` (`ce9b55c3`) sited the 18
  remaining store roots — this class's among them — on **tmpfs** via `sited_root`. That removes the
  mechanism rather than widening a bound: an fsync to RAM cannot stall on a contended disk. Nothing
  recorded that the fix had LANDED, so the item stayed on the queue reading as live.
  **A fix that is not written down where the symptom is described has not finished landing.**
- **Measured, `tekton/devrc-pytests`, newest verdict per PR head, split on `ce9b55c3`:** pre-fix 125
  verdicts / 29 genuine failures / **4** this test; post-fix 45 / 6 / **0**. `failure` and `error`
  counted separately throughout — `error` is a broken gate, not a bad change.
- 🔴 **THE ZERO WAS NEVER THE PROOF, and the comment now says so in the file.** At the pre-fix
  per-verdict rate (3.2%) the expected count in 45 verdicts is ~1.4, so **P(observing 0) ≈ 0.23** —
  a one-in-four coincidence. The mechanism's removal is what carries the claim. **A before/after
  table is the easiest thing in this repo to over-read; state the power beside it or it will be
  upgraded to "proven" by the next reader.**
- 🔴 **IT WAS NEVER THE WORST FLAKE — it was the best-DOCUMENTED one.** Same pre-fix window:
  `test_every_decrypt_family_VERDICT_is_pinned_WHOLE` failed **8** times to this test's **4**, and
  is also at 0 post-fix. It had no long diagnosis attached and was never ranked. **Vividness is not
  frequency: COUNT the failures before choosing which flake to chase.** This is the same error as
  ranking by a memorable incident rather than by a census.
- **`main` is a useless population for this question and that is structural.** Of 43 post-fix
  pytests verdicts on `main`, **40 were artefacts** (~31 `superseded`, 4 `KILLED: the gate pod
  died`, 4 `NO GATE POD`, 1 pending) leaving **3** authoritative successes. Use PR heads.
- ⚠ **UNMEASURED, recorded rather than guessed:** the 760-test verification run emitted **3
  warnings**, and this suite warns on `the spawn lost the port race and retried`. Only the tail was
  captured, so whether those were port-race retries is **unknown**. If they were, the race is live
  on this host — but nothing here claims that.



## Evicted 2026-09-14 from the handoff (rank 13 is a CLOSED tombstone)

### ✅ RANK 13 PROBE RUN — the false-INHERITED rate is 2 of 4, and the predicate SEPARATES them 4/4
- **Symptom + exact repro:** the triage bot's first live sweep named 5 PRs `INHERITED — likely cured
  by rebase`. That is a directly testable claim, so it was tested rather than argued:
  `bash <scratch>/probe-inherited.sh` — for each PR, build the merged tree (PR head + current
  `origin/main`) and run **only the named failing test** there. Passes ⇒ the bot was right; fails ⇒
  the red is the PR's own and the verdict was false. A merge conflict is its own outcome, recorded,
  never folded into a pass.
- **Observed (with values), base `origin/main` = `14daa42a`:**

  | PR | named failing test | file | merged-tree result | verdict |
  |---|---|---|---|---|
  | #1450 | `test_a_partial_run_is_declared_where_gate_sh_actually_LOOKS` | `test_run_tests_targets.py` | **1 passed** | bot RIGHT |
  | #1286 | `test_agent_without_any_tab_is_untouched` | `test_browser_tab_ref.py` | **1 passed** | bot RIGHT |
  | #1603 | `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` | `test_runtime_shebangs.py` | **1 failed** | 🔴 **FALSE** |
  | #1194 | `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` | `test_runtime_shebangs.py` | **1 failed** | 🔴 **FALSE** |
  | #1038 | `test_every_historical_version_claim_still_exists` | `test_opencode_engine.py` | MERGE CONFLICT | UNTESTABLE |

  **2 of 4 testable verdicts are FALSE (50%).** Both false ones are the SAME test — a repo-wide
  census guard. Both correct ones are ordinary unit tests. 🔴 **The predicate proposed when there was
  only one data point — *is the named failing test a census/scanner guard over files it does not
  name?* — separates this sample 4 of 4.**
- 🔴 **AND THE MECHANISM IS NOW SHARPER THAN "CENSUS GUARD": IN BOTH FALSE CASES THE OFFENDER IS A
  NEW FILE THE PR ITSELF ADDS.** `#1194`'s failure names
  `scripts/tests/test_break_glass_merge.py:65: GH_STUB = r'''…` — **one of `#1194`'s own files,
  confirmed ABSENT from `main`**, so a rebase would carry the offending file along with the red.
  `#1603`'s was its own new controls in `test_census_scan.py`. The guard file is byte-identical in
  the branch *because the PR never touched the guard* — which is the normal state of this breakage,
  not evidence of innocence.
- **Ruled out:** "the single #1603 case was unrepresentative" — a second, independent instance
  (`#1194`, a different PR, a different offending file, 450 commits behind) reproduces it exactly.
  via: measurement
- **Ruled out:** "the bot is simply unreliable / every INHERITED is suspect" — #1450 and #1286 were
  both RIGHT on the merged tree, and both are ordinary unit tests whose evidence commit genuinely
  fixed them. **The failure is a specific, identifiable class, not general noise.** via: measurement
- **Ruled out:** "#1038 is a fifth data point" — its merged tree does not build (1 conflicting path
  at 601 commits behind), so its verdict is UNTESTABLE by this method and is excluded from the rate
  rather than assumed either way. via: command
- 🔴 **Leading hypothesis — now with a ready-made fix, and the oracle already exists on `main`.**
  This false-positive class is *exactly* the class `#1603` was built for: `scoped-tests.sh` maps a
  diff to tests that NAME what you changed, and **a brand-new file names nothing** — the same
  sentence appears in `ledger-check.sh`'s own header as its reason to exist. So the fix is: before
  ruling INHERITED, ask whether the named failing test is in
  **`scripts/testlib/census_scan.py::census_nodeids()`** — the AST derivation `#1603` merged as
  `14daa42a`, which computes precisely "every test whose verdict depends on the repo's FILE SET".
  If it is, the "test file unchanged in my branch" premise carries no information and the verdict
  must be demoted to NOT EXPLAINED. **The two pieces of work were built independently in one session
  and did not know about each other; the probe is what connected them.**
- **Next probe:** implement the demotion above and re-run the sweep against the same five PRs — the
  pass condition is `#1450`/`#1286` still INHERITED and `#1603`/`#1194` demoted to NOT EXPLAINED.
  That is a regression test with a known-red baseline, which this repo requires anyway. ⚠ `#1038`
  cannot serve as a fixture (its tree does not build); use it only as a reminder that a conflicted
  PR needs its own outcome rather than a verdict.
