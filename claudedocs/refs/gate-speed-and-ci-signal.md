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

