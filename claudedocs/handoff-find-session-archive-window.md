# Handoff: find-session-archive-window — 2026-09-08

## Run this first — the index, one command
```bash
cairn recall --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Give `find-session.py`'s archive leg a **12-day default window** that never reads as a
corpus-wide search, and fix the two defects a live trace of the tool turned up. Sibling
efforts, different docs: `handoff-find-session-live-first.md` (the `--live` inversion),
`handoff-find-session-opencode.md` (the second corpus).

## State now
🔴 **CLOSED. Nothing in this effort is open, in flight, or waiting on a human.** The three
items the previous revision listed as outstanding are all done:

| was outstanding | now |
|---|---|
| this doc (`#1418`) open, and its content stale | **MERGED** `3a4a057d` — and its three false claims were corrected *before* the merge, not after |
| the tmux gate flake, unfiled and tagged `forcing: none` | **filed as issue `#1473`**, re-tagged `forcing: gate` |
| two orphaned worktrees | **removed**, with their three local branches |

Shipped state, unchanged and re-verified: **`#1388`** (the window + audit rounds 1–5, merged
2026-09-09) and **`#1438`** (round 6, squash `f241d7f7`); both hosts converged and
verified at the CONSUMER (`ship.sh` rc 0, `2 hosts compared`), deployed `SKILL.md` resolving
to the **same store hash on both**. `#1439` (ship.sh's nebula fallback, another session's)
merged and verified live here with a bare `ship.sh`.

⚠ **Worktree hygiene, verified by CONTENT not ancestry.** All three worktrees showed 7/1/2
"commits not in main" — expected and meaningless, because **a squash merge never makes the
branch head an ancestor**. The real check was `git diff origin/main <tip> -- <its own files>`:
**0 lines** for each. Only then were they removed.

⚠ **No `clawgate-task:` field**, again: `clawgate_handoff.sh resolve` exited **5**. Its
positive control confirms the board is reachable, but an unknown session id also answers 200
with an empty array — so that 0 is not a clean bill of health, and none is recorded.

## Open investigations — live diagnosis state

### Audit round 5 — is the new structural gate's completeness claim true?
- **Symptom + exact repro:** `993a7564` added `test_the_module_EXITS_ONLY_BY_RETURNING_from_main`
  (`scripts/tests/test_find_session_skill_contract.py`), which forbids `sys.exit` /
  `raise SystemExit` / bare `exit` outside the `__main__` guard. That gate is what lets the
  exit-2 *enumeration* claim completeness. If a non-`return` exit route survives, the claim is
  too strong — the shape this ladder produced four times running.
- **Observed (with values):** `_exit_usage_sites` recognises four spellings. `EXIT_USAGE_SITE_COUNT
  = 10`. The module uses `sys.exit` at exactly one line — `sys.exit(main())` in the `__main__`
  guard — so the gate passed on the tree that introduced it, and three mutants were watched to go
  KILLED against an unmutated control of 185 passed.
- **Ruled out:** "a wider enumeration will do" — two rounds running, a spelling nobody had thought
  of walked through (`sys.exit(EXIT_USAGE)`, then `raise SystemExit(EXIT_USAGE)`), each with the
  whole suite green. `via: measurement`
- **Leading hypothesis:** the open route is **argparse's own `parser.error()`**, which exits 2 from
  inside argparse rather than from this module — an unknown flag produces rc 2 whose cause the
  exit-2 sentence does not name, and no gate covers it.
- **Next probe:** `python3 scripts/find-session.py --nosuchflag; echo "rc=$?"` — if that is 2, decide
  whether the sentence must name it, or whether the sentence's scope is "causes THIS module
  produces" and should say so.

### `test_a_launched_pane_gets_a_PATH_THAT_CAN_FIND_claude` is a load flake
- **Symptom + exact repro:** fails inside a full `gate.sh --tier both` run under high load with
  `open_window failed: tmux did not report the new window's directory`
  (`scripts/tests/test_tmux_reply_agent.py:2264`).
- **Observed (with values):** failed once at load average **65**, in a run whose wall time inflated
  to **26m30s** against ~17m earlier; passes **3/3** in isolation; absent from the fully green
  28m32s run. Spawns a real tmux server with 30s subprocess timeouts.
- **Ruled out:** "PR #1388 caused it" — the file is outside this PR's six-file diff and `main` has
  not touched `scripts/tmux-reply-agent` since the base. `via: command`
- **Ruled out:** "leaked load generators from the audit rounds" — only `tmux: server` and
  `k3s-server` are reparented to init; the load was live, parented work from other sessions.
  `via: measurement`
- **Leading hypothesis:** a genuine wall-clock timing dependency in the test, exposed by
  contention rather than by any code change.
- **Next probe:** run it under artificial load and read which tmux call times out; the fix is to
  remove the timing dependency, not to re-run. **Not this PR's file — do not fix it here.**

### RESOLVED — argparse and the structural exit gate (was: "is the completeness claim true?")
🔴 **CLOSED. Do not re-run the probe the earlier revision of this section left here.** The
answer was NO, four times over, and it is fixed.
- **Answer:** `parser.error()` was only one of SEVEN routes. Measured: `_bye = sys.exit;
  _bye(…)`, `_e = SystemExit(…); raise _e`, `os._exit(…)`, `build_parser().error(…)`,
  `build_parser().exit(…)`, `return X if … else …`, plus `quit(…)` and
  `raise <recv>.SystemExit(…)` found in round 6. And argparse exits 2 with **no mutation at
  all** — `--nope`, `--limit abc`, `--tail x`.
- **Fixed by:** naming argparse's rejection in `EXIT_CONTRACT` / `EXIT_2_CAUSES` / the shipped
  doc; widening the detector to any receiver; and — the load-bearing part — **deleting the
  completeness claim rather than replacing it**. Four consecutive rounds wrote one and all
  four were false; the docstring now records the measurement and instructs successors not to
  assert a closed set.
- **Ruled out:** "a wider enumeration will eventually be complete" — two rounds running, a
  spelling nobody had thought of walked through, each with the whole suite green.
  `via: measurement`

### The tmux load flake — unchanged, still real, now correctly ranked
- **Symptom + repro:** inside a full `gate.sh --tier both` under load, `open_window failed:
  tmux did not report the new window's directory` (`test_tmux_reply_agent.py:2264`).
- **Observed:** failed at load average 65 and again at 78–87; run wall time inflated to
  26m30s and 48m53s against ~17–22m nominal; passes **3/3** in isolation; spawns a real tmux
  server with 30s subprocess timeouts.
- **Ruled out:** "PR #1388 caused it" — outside the six-file diff, and `main` has not touched
  `scripts/tmux-reply-agent` since the base. `via: command`
- **Ruled out:** "leaked load generators from the audit rounds" — only `tmux: server` and
  `k3s-server` were reparented to init; the load was live, parented work from other sessions.
  `via: measurement`
- **Next probe:** run it under artificial load and read which tmux call times out. **Not this
  effort's file** — fix it in its own PR.

## Next steps (ranked)
1. **Nothing remains in this effort.** Its one descendant is tracked elsewhere:
   **`IN FLIGHT: innovation-upstream/devrc#1473`** — the tmux gate flake
   (`test_a_launched_pane_gets_a_PATH_THAT_CAN_FIND_claude`). It carries its own measured
   evidence and closing condition; do not re-derive them from this doc.
   forcing: gate — it truncated two full `gate.sh` runs at the 3600s cap. 🔴 Work it **on the
   issue**, not from this queue: a second entry point is how two sessions end up fixing one
   flake.

## Gotchas / decisions / dead-ends
- 🔴 **The base moved twice mid-ladder and both rebases mattered.** `main` fixed the **7 inherited
  gate failures** in #1392 (nixpkgs drift: opencode pin + age 1.3.2 tamper verdict) — every earlier
  PR comment reporting "7 failed" was correct when written and is now superseded. A run at
  `bd796162` then failed `test_no_client_subdomain_literal_is_committed` on a **client subdomain
  committed to this PUBLIC repo** in `claudedocs/handoff-civitai-app-fleet.md`; introduced upstream,
  already removed by a later commit on `main`, cleared by rebasing forward.
- 🔴 **`git show $r:path` UNBRACED hits the zsh history-modifier trap** — it reported `0 matches` for
  every ref while the literal was plainly in the file; `${r}` returned 1. Cost a near-miss report
  that a secret was live on `main`.
- 🔴 **`merge-base --is-ancestor <commit-that-introduced-X>` does not answer whether X survives.**
  A later commit may have removed it. Check content, not ancestry.
- **The 30-day no-op is structural, not this week's luck:** Claude Code prunes `~/.claude/projects`
  on a 30-day retention with no `cleanupPeriodDays` override, so that corpus can never be deeper and
  any default at or above 30 days is inert by construction. **Not** true of the opencode store,
  which has no pruning — which is why its larger cut (487 of 707, 69%) must be named as unmeasured.
- **Corpus counts are dated snapshots.** The walked set was 924 on 2026-09-08 and passed 940 within
  a day. `find ~/.claude/projects -name '*.jsonl'` reports ~5,954 but ~5,030 are
  `subagents/agent-*.jsonl`, which `iter_transcripts` excludes by name. Quote ratios, not totals.
- **`--skill` is exempt from the default window on purpose** — `adoption-scan` routes "has skill X
  ever been used" here by name, and the `skills_used` rollup is forward-only from 2026-08-29.
- **Deliberately NOT fixed:** round 3's "cannot drift" sentence about `unmeasured_legs` vs
  `archive_search`. Round 4's audit found it imprecise but not load-bearing (a pre-existing test
  catches behavioural drift, verified by mutation). Rewriting it a third time is what the
  "stop reaching for a better rationale" rule forbids.
- **An opencode session (`ses_f810ec0e8ffe5ou7j6JVe5fWvE`, tmux `scratch8:3`) held a plan for
  almost exactly this change**, parked at "Want me to proceed?". Its proposed 30-day default is
  measured as a **no-op** (skips 0 of 924). Its `--until` idea is the salvageable part.
- **No `clawgate-task:` field is recorded**: `clawgate_handoff.sh resolve` exited **5** (nothing
  resolved) with its positive control confirming the board is reachable. Per the skill, an unknown
  id also answers 200 with an empty array, so that 0 is not a clean bill of health.

- 🔴 **The `gh pr list` sweep is what stopped me duplicating a whole feature.** Asked to add
  the nebula fallback, I found `fix/ship-nebula-fallback` already existed and `#1439` was
  already at audit round 1. Its design was the one the constraints force (`remote_ssh_of`
  kept pure, a separate `remote_ssh_candidates_of` + `first_reachable_ssh`) and it also fixed
  `drift-check.sh`, which my sketch would have missed. **Two competing implementations of the
  converger racing into `main` is the worst version of that mistake** — if they disagree, the
  failure is invisible until a host silently stops converging.
- 🔴 **`ship.sh` does `git checkout main` on the local host** (`ship.sh:1156`). When the
  shared base clone is on another session's branch, that switches their tree under them. I
  refused to run the local leg for that reason and shipped `--no-local` first; the blocker
  cleared on its own when that session returned the checkout to `main`. **Re-check the branch
  at the moment of acting, not from an earlier survey** — mine was 20 minutes stale and the
  answer had already changed.
- 🔴 **`gate.sh` has its OWN internal 3600s cap.** Under sustained load it is SIGTERMed
  (`exit=124` → `RESULT: FAIL (exit=143)`), which is a **could-not-run, not a verdict** — one
  such run completed 20 of 28 targets with **zero failures**. Recover by running the
  unreached targets directly rather than re-running the whole sweep; say plainly when a
  verdict is assembled from several runs.
- **`run-tests.sh --targets` takes a SPACE-separated list**, not comma-separated — a comma
  list is rejected as "not a target in the hermetic set", which reads like a missing target
  rather than a syntax error. `scripts/devhost-tests` is in `DEVHOST_TARGETS` and needs
  `--set all`.
- ⚠ **`ship.sh`'s fallback prints its "did not answer" line twice** (once bare, once with the
  "falling back" clause). Cosmetic; not worth its own PR.
- **The duplicate-plan opencode session** (`scratch11:7`) is alive and still labelled
  "Identify find-session command". Its plan is obsolete — its proposed 30-day default is
  measured as a **no-op** (skips 0 of 924 files, because Claude Code prunes
  `~/.claude/projects` on a 30-day retention). Its `--until` idea is the salvageable part.

- 🔴 **THE LADDER'S FINAL TALLY — carried here from `State now` on purpose**, because that
  heading is REPLACED on every update and this is the arc's most durable fact. Six rounds:
  **0 🔴, 23 🟡, ~16 🟢.** Payload lines changed per round **155 → 90 → 33 → 12 → 8 → 2**.
  Exactly ONE substantive correctness bug in the whole run (`--claude-only --opencode-only`
  searching no corpus and exiting 0) — and it **predated the PR**. Every finding from round 3
  onward was a defect in a guard the ladder itself had written. 🔴 **The severity never
  dropped while the payload did** — 🟡 counts stayed 3–5 every round — which is the signature
  of a ladder that has left the PR and is auditing its own scaffolding. That, not the round
  count, is what said stop.
- 🔴 **A squash merge makes every worktree look like it holds unsaved work.** All three of
  this effort's worktrees reported commits "not in main" (7, 1, 2) *after* their PRs had
  merged. Ancestry is the wrong instrument by construction; `git diff origin/main <tip> --
  <files it touched>` returning 0 lines is the right one. Checking ancestry here would have
  either stranded three worktrees indefinitely or — worse — invited a `--force` removal on a
  reading that was never evidence of anything.
- 🔴 **The handoff doc was WRONG in the direction that costs the most, and only a grep caught
  it.** Before this close-out it still asserted `#1388 … OPEN`, "the sandbox tier … not
  re-run since the rebase" and "audit round 5 is running" — all false, in the one artifact a
  `/resume` reads first. A stale handoff does not fail loudly; it silently buys a re-run of a
  finished ladder. **Grep your own doc's claims against live state before merging it**, the
  same way you would any other assertion.
- **`forcing: none` is not a neutral tag — it is a decision that the item will never be
  worked.** The tmux flake sat under it because it felt like a nit. It had truncated two full
  gate runs, which is `forcing: gate`. If an item has a real external signal, mis-tagging it
  `none` is how it disappears.
- ⚠ The base clone `~/workspace/devrc` is 2 commits behind `origin/main` and is being moved
  by another session; this close-out was written from a dedicated worktree instead. That is
  the normal state on this box, not drift to fix.

## How to verify
```bash
# 1. the change is IN main (content, never ancestry — squash merges break ancestry)
git -C ~/workspace/devrc show origin/main:scripts/find-session.py | grep -c 'DEFAULT_SINCE_DAYS = 12'
git -C ~/workspace/devrc show origin/main:scripts/tests/test_find_session_skill_contract.py \
  | grep -c '"exit", "quit"'                       # round 6's guard

# 2. both hosts SERVE it (identical store hash = a two-host claim)
readlink -f ~/.claude/skills/find-session/SKILL.md
ssh zach@10.42.0.100 'readlink -f ~/.claude/skills/find-session/SKILL.md'

# 3. the shipped behaviour, streams SEPARATE (zsh MULTIOS eats stderr otherwise)
python3 ~/workspace/devrc/scripts/find-session.py zzzznomatch --claude-only >/tmp/o 2>/tmp/e
grep -c 'ARCHIVE window' /tmp/o                    # 1 — human path discloses on STDOUT
python3 ~/workspace/devrc/scripts/find-session.py x --claude-only --opencode-only; echo $?  # 2
python3 ~/workspace/devrc/scripts/find-session.py x --live --tail 0; echo $?                # 2

# 4. ship.sh's nebula fallback, off-LAN, with NO override
bash ~/workspace/devrc/scripts/ship.sh          # expect: "falling back to zach@10.42.0.100"
```
