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
- **Branch:** `fix/find-session-window-and-guards`, worktree `~/workspace/devrc-find-session-window`.
- **PR:** innovation-upstream/devrc **#1388** — OPEN, `mergeable=MERGEABLE`, `UNSTABLE`.
- **Base:** `5b564844` (rebased twice this session; see Gotchas).
- **Gate: `GATE: RESULT=PASS exit=0`** — pytest **13,139 passed / 0 failed** (floor 12,927),
  node **PASS 1449/1449**, dev-host tier, on `993a7564`. First fully green run of this branch.
  ⚠ The **sandbox tier** (`nix build .#checks.x86_64-linux.{pytests,nodetests}`) was last run
  at `0d44c562` (7 inherited failures, since fixed upstream) — **not re-run since the rebase.**

DONE — five commits, each a round of the audit ladder:

| sha | what |
|---|---|
| `3c38514e` | the window itself + the `live_scan` `hosts`-shape crash + `--tail < 1` |
| `8d3f0cfa` | exit-2 table did not enumerate its own codes; cap size measured half of one leg |
| `50404954` | round-1 guard walkable three ways; five fixes had no test |
| `0e4876d0` | `--claude-only --opencode-only` searched nothing; three narrow guards |
| `993a7564` | replaced a false "backstop" claim with a structural gate; pinned the seam |

Shipped behaviour: `DEFAULT_SINCE_DAYS = 12`; `--all-time` lifts it; `--since` + `--all-time`
refused; `--claude-only` + `--opencode-only` refused; `--skill` exempt from the default;
window announced on stdout (human) / stderr (`--json`) / `archive.window` (`--live --json`),
carrying the count of transcripts skipped unopened.

IN FLIGHT — **audit round 5 is running** (delta, blind, `0e4876d0..993a7564`). Rounds 1–4 each
returned findings; payload lines per round **155 → 90 → 33 → 12**. No clean round yet.

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

## Next steps (ranked)
1. **Read round 5's report and act on it.** If it returns findings, fix + re-gate + post the
   claims block + dispatch round 6; if it is clean, the ladder ENDS — do not run another round to
   confirm a clean one. `devrc`, files: the same six.
   forcing: gate — the audit ladder is the only pre-merge gate this repo has; branch protection is
   declared off (`CLAUDE.md`, marker `merge-gate: other`).
2. **Re-run the SANDBOX tier before merging**, one derivation at a time:
   `nix build ~/workspace/devrc-find-session-window#checks.x86_64-linux.pytests --no-link -L`
   then `.nodetests`. It has not been run since the rebase onto `5b564844`.
   forcing: gate — that tier is the one a merge is gated on when protection is restored, and it is
   structurally blind to different things than the dev-host tier.
3. **Merge #1388 and `scripts/ship.sh`.** The tool is `mkOutOfStoreSymlink`-live but
   `~/.claude/skills/find-session/SKILL.md` resolves into `/nix/store`, so merge+pull leaves agents
   reading the OLD doc against the NEW windowed tool until a switch runs.
   forcing: regression — an agent following the stale doc will report a windowed empty result as a
   corpus-wide absence, which is the exact failure this change exists to prevent.
4. **File the tmux flake separately** (`scripts/tests/test_tmux_reply_agent.py`), closing condition:
   the test no longer depends on wall-clock tmux responses and survives a full gate under load.
   forcing: none

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

## How to verify
```bash
W=~/workspace/devrc-find-session-window
# the four suites this PR touches
nix develop ~/workspace/devrc -c python3 -m pytest \
  $W/scripts/tests/test_find_session_live.py \
  $W/scripts/tests/test_find_session_skill_contract.py \
  $W/scripts/tests/test_find_session_skill_cli.py \
  $W/scripts/tests/test_transcript_search.py -q          # expect 244 passed

# the shipped behaviour, streams kept SEPARATE (MULTIOS eats stderr otherwise)
python3 $W/scripts/find-session.py zzzznomatch --claude-only >/tmp/o 2>/tmp/e
command grep -c "ARCHIVE window" /tmp/o    # 1  — human path discloses on STDOUT
python3 $W/scripts/find-session.py zzzznomatch --claude-only --json >/tmp/o 2>/tmp/e
command grep -c "ARCHIVE window" /tmp/e    # 1  — --json discloses on STDERR
python3 -c "import json;json.load(open('/tmp/o'))"   # stdout still parses

python3 $W/scripts/find-session.py x --claude-only --opencode-only; echo "rc=$?"   # 2
python3 $W/scripts/find-session.py x --since 2026-01-01 --all-time; echo "rc=$?"   # 2
python3 $W/scripts/find-session.py x --live --tail 0; echo "rc=$?"                 # 2
python3 $W/scripts/find-session.py --skill find-session --limit 1 2>&1 >/dev/null \
  | command grep "WHOLE corpus"            # --skill is unwindowed
```
