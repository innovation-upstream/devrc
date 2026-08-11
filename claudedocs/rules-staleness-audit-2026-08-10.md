# `claude/RULES.md` — model-generation staleness audit

**Date:** 2026-08-10 (verified from session `<env>`)
**Auditor:** dispatched subagent, worktree `agent-aadfcdb3fd199de17`
**File under audit:** `claude/RULES.md` @ `e08dfdb`, 33,561 B, 39 B of slack under
`scripts/tests/test_rules_size.py`'s working-headroom floor.

---

## 0. The premise, and what it does and does not license

Anthropic removed >80% of Claude Code's own system prompt for the Claude 5 generation
"with no measurable loss on our coding evaluations", diagnosing the removed content as
"over-specified rules that were wrong in edge cases, and context that crowded out the
model's own judgment."

That supports **"those particular instructions were doing nothing."** It does **not**
support "shorter is better." Two 2026 studies (arXiv 2605.10039; arXiv 2602.11988) found
**no** effect of instruction-file size on adherence up to ~500 lines. **No cut in this
audit is justified on length.** Every applied cut rests on one of:

- the rule's factual premise is measurably **false** in the live environment (STALE);
- the behaviour happens **without** the rule, measured by paired ablation (NOW-NATIVE);
- a **deterministic guard** already enforces it, and carries the rationale (DUPLICATE).

---

## 1. Method

### 1.1 The apparatus

Revert-and-rerun with a genuinely isolated control. Each run:

```bash
CLAUDE_CONFIG_DIR=<scratch cfg>  claude -p "$(cat <prompt>)" \
    --model opus --output-format stream-json --verbose \
    --dangerously-skip-permissions
```

where `<scratch cfg>/CLAUDE.md` = `claude/PRINCIPLES.md` + **the RULES variant under
test**, and `<scratch cfg>/.credentials.json` is a symlink to the real one. `cwd` is a
freshly-copied fixture tree with no `CLAUDE.md` of its own, so the ONLY instruction
difference between the two arms is the ablated block.

- **Control arm ("with")** — full 33,561 B `RULES.md`.
- **Treatment arm ("without")** — byte-identical except the candidate block is deleted.

Ablations were produced by a script keyed on anchor strings, which **asserts the start
anchor matches exactly one line** and prints the removed text and byte delta, so a cut
cannot silently land on the wrong occurrence.

The deployed `~/.claude/RULES.md` was never touched and `home-manager switch` was never
run.

### 1.2 Instrument validation

The harness was verified before any verdict was read from it:

- **Positive control** — a scratch `CLAUDE.md` containing only *"end your reply with the
  exact token ZEBRAQ7"*. Run output ended `ZEBRAQ7`. The injected file **is** reaching
  the model; a null result is therefore about the rule, not about broken plumbing.
- **The size gate's own negative control** — see §5.
- **The bash-guard probe's controls** — see §3.4.

One scorer defect was found and corrected mid-audit, and is reported rather than hidden:
the first C3 scorer used a **spelled** pattern (`don'?t ship`) and marked a run as
missing its not-ready verdict; the transcript actually said *"I don't think it's ready to
ship as written"*. Every marker was re-checked against transcript text afterwards. This
is the core's own "a guard can be SPELLED rather than STRUCTURAL", hit while auditing
that rule's file.

### 1.3 What this method structurally cannot see

Stated up front because it bounds every NOW-NATIVE verdict below:

- Runs are **single-task, single-turn to a few turns**. Nothing here measures drift over
  a long autonomous session, which is where several rules claim their value.
- One model (`opus`) on one harness version (Claude Code 2.1.220). The one exception is
  the temporal probe, which was additionally run on `opencode` with a non-Claude model.
- The scratch config has **no hooks**, so hook-enforced hazards were probed separately
  against the live hook rather than in-session.
- **n=2 paired runs** per applied section cut (n=1 for the two candidates NOT cut). Two
  points is enough to catch a coin-flip, not enough to bound a rare failure.

---

## 2. Phase 1 — full audit

Byte costs are per-block including the trailing newline, measured by script. Priority is
the tag carried in the file.

### 2.1 KEEP-HOT — recent, load-bearing, no deterministic equivalent

| B | pri | rule | archive incident (date) |
|---|---|---|---|
| 1952 | 🔴 | Validate the INSTRUMENT before you read its verdict | nine-broken-harnesses (2026-08-01), positive-control, parsing-tool-output (2026-08-02), count-not-exit-code |
| 1145 | 🔴 | Mutation-test a guard; prove it REACHABLE | unreachable-guards, mutation-sweep-blind-spots |
| 995 | 🔴 | zsh's unbraced `$var` — three traps | zsh-unbraced-var (2026-08-03/05) — **re-verified live, see §3.5** |
| 939+91+325 | 🔴 | `git stash` is repo-GLOBAL | stash-incidents (2026-07-25, broadened 2026-08-01) |
| 908 | 🔴 | Green run covers only its tree + environment (merged tree; both tiers) | merged-tree, two-tiers (2026-08-02) |
| 820 | 🔴 | Live probe against a DIRTY tree | dirty-tree-probe (2026-08-01) |
| 767 | — | Re-sync the base clone after worktree work | base-clone-drift (2026-07-30, 2026-08-05) |
| 748 | 🔴 | Re-check WHICH branch before ANY write | wrong-branch-writes |
| 737 | 🔴 | Fresh worktree does not inherit `.envrc` | worktree-envrc (2026-07-31) |
| 630 | 🔴 | Deploy success ≠ consumer success | deploy-vs-consumer (2026-08-02) |
| 612 | — | Worktree isolation is the default for file-modifying subagents | (stash-incidents) |
| 605 | 🔴 | "Remembered" includes what YOU observed this session | stale-observation (2026-08-01) |
| 598 | — | Run the cheap discriminating control BEFORE the theory | — |
| 593 | 🔴 | "Verified in isolation" is the new vacuous green | isolation-seam |
| 591 | 🔴 | A SQUASH merge never makes head an ancestor | — |
| 542 | 🔴 | An EMPTY RESULT cannot distinguish two mechanisms | empty-result (2026-08-01) |
| 518 | — | One rule, one place | consolidation-finds-bugs |
| 515 | 🔴 | Config-pinned suite is structurally blind | config-blind-suite (2026-08-05) |
| 504 | — | `gh pr view` is the only authority on conflicts | — |
| 496 | — | An audit/review fix RESETS the verification gate | audit-fix-resets-gate |
| 479 | — | `readlink -f` is the arbiter for HM-managed dotfiles | readlink-arbiter |
| 473 | 🔴 | Load flake vs failed assertion, by WALL TIME | flake-vs-assertion (2026-08-05) |
| 442 | — | Feature branches only (+ the homelab-talos exception) | — |
| 431 | — | A field in a DTO is not a guard | — |
| 424 | — | `pgrep -f`/`pkill -f` match your own shell | — |
| 420 | — | One measurement is not a general claim | — |
| 415 | 🟡 | Docs in a working tree are UNSAVED WORK | stranded-docs |
| 384 | 🔴 | Never `--delete-branch` a stacked parent | — |
| 381 | 🔴 | A comment is a claim too | — |
| 378 | — | Two changes one file: TEST-MERGE, and a clean merge is not clean | — |
| 372 | — | Reproduce the original symptom | green-and-audited-but-broken |
| 360 | 🔴 | A guard can be SPELLED rather than STRUCTURAL | spelled-guards |
| 334 | 🔴 | Declarations are not instances | declarations-vs-instances (2026-08-02) |
| 325 | 🔴 | Find the WRITER before blaming the VCS op | sops-retraction (RETRACTED theory) |
| 296 | — | Flag BEFORE acting | — |
| 289 | — | Regression test must be shown red at base | vacuous-guards |
| 254 | — | `grep` can render a character invisible | — |
| 247 | — | Sync a branch with a clean worktree | — |
| 239 | — | Prefer deterministic/structural fixes | — |
| 239 | — | GitHub sudo-mode cannot be automated | — |
| 231 | — | `count=1` replace on a repeated pattern | — |
| 224 | — | Write the claim AFTER the code | — |
| 220 | — | Place files by purpose (`claudedocs/`, …) | — |
| 216 | — | Never `git reset --hard` | — |
| 205 | — | `gh secret set` has no `--body-file` | **re-verified live, §3.5** |
| 194 | 🔴 | A permanently-red gate is worse than no gate | — |
| 193 | — | User-facing micro-decisions: ask first | — |
| 182 | — | NixOS: no apt/dnf | — |
| 182 | — | Don't re-emit git orientation | — |
| 181 | — | Never derive a test's expectation from the implementation | — |
| 181 | — | Never `git add -A` | — |
| 175 | — | Re-verify before acting on a remembered fact | — |
| 160 | — | Don't defend your position against repeated failure reports | — |
| 152×2 | — | Memory Hygiene bullets (see §3.3 — **measured load-bearing**) | — |
| 136 | — | UI bugs: reproduce the click path | — |
| 116 | — | Root cause, not symptom | — |
| 110 | — | Take another ref's file with `git checkout <ref> --` | — |
| 109 | — | Never skip tests/validation | — |
| 103 | — | Debug systematically | — |
| 101 | — | Review before commit | — |
| 100 | — | zsh reserves `status` | **re-verified live, §3.5** |
| 93 | — | When you can't verify, say so | — |
| 64 | — | Status first | — |
| 35 | — | Commit/push only when asked | — |
| 569 | 🔴 | Preamble: read every rule at its WIDEST reading | stash-incidents |
| 464 | 📁 | Preamble: this is the CORE | — |

### 2.2 NOW-NATIVE / DUPLICATE / STALE candidates

| B | pri | rule | class | tested? | verdict |
|---|---|---|---|---|---|
| 1542 | 🟡 | **Memory Hygiene** (whole section) | looked DUPLICATE of the `prune-memory` skill | ✅ C6, n=1 | **KEEP — ablation was WORSE** |
| 1355 | 🔴 | **`git stash` block** | DUPLICATE of `check_git_stash` DENY | ✅ C8, n=1 | **KEEP — ablation confounded** |
| 771 | 🟡 | **Token & Tool Hygiene** (section) | DUPLICATE + NOW-NATIVE | ✅ C2, n=2 | **CUT** |
| 495 | 🟡 | **Professional Honesty** (section) | NOW-NATIVE | ✅ C3, n=2 | **CUT** |
| 382 | 🟡 | **Scope & Completeness** (section) | NOW-NATIVE | ✅ C4, n=2 | **CUT** |
| 381 | 🟢 | **Tool Optimization** (section) | DUPLICATE of `search-tool-nudge.py` | ✅ C1, n=2 | **CUT** |
| 266 | 🔴 | **Temporal Awareness** (section) | DUPLICATE of harness date injection | ✅ C5, n=2 + opencode | **CUT** |
| 181 | — | Never `git add -A` | DUPLICATE of `check_git_add_all` DENY | ✅ C9, n=1 | **KEEP — see §3.4** |
| 155 | — | `sleep N && <cmd>` is blocked | STALE (premise false) | ✅ direct | **CUT** |
| 152 | — | Quote globs meant literally | STALE (`unsetopt nomatch`) | ✅ direct | **CUT** |
| 216 | — | Never `git reset --hard` | DUPLICATE of two DENY checks | ❌ UNTESTABLE | KEEP |
| 205 | — | `gh secret set` has no `--body-file` | suspected STALE | ✅ direct | **KEEP — still true** |
| 231 | — | `count=1` replace hazard | partly DUPLICATE (Edit requires a unique match) | ❌ not run | KEEP, see §6 |
| 360 | — | Git Workflow section preamble | maintainer note, not agent behaviour | ❌ unfalsifiable | KEEP, see §6 |
| 64 / 182 | — | "Status first" vs "Don't re-emit git orientation" | mutual tension | ❌ not run | KEEP, see §6 |

### 2.3 UNFALSIFIABLE

Not cut, and not proposed for cutting — they read as values rather than testable
behaviours, so an ablation cannot produce evidence either way: "Root cause, not symptom";
"Debug systematically"; the "Deterministic Over Prose" preference itself; "Read every rule
at its WIDEST reading" (a meta-rule about interpretation, and the one whose narrowing has
a documented cost).

---

## 3. Phase 2 — the measurements

**Tested: 10 candidates.** 8 by paired revert-and-rerun (26 headless sessions), 2 by
direct measurement of the live environment. **Not tested: 5** (§4).

### 3.1 Cut — paired ablation showed no behavioural difference

Full per-run marker tables live in `claude/RULES-ARCHIVE.md` under each
`retired-*` anchor. Summary:

| cand | section | runs | result |
|---|---|---|---|
| C1 | Tool Optimization | 4 | Grep tool used **0/4**; bash grep used **4/4**; max tools per message **1** in all 4. Identical both arms. The control arm **violated its own rule in 4 of 4 runs.** |
| C2 | Token & Tool Hygiene | 4 | All five markers identical across all four runs (Write not heredoc ✓, surgical read of a 74,945 B file ✓, binary not Read ✓). |
| C3 | Professional Honesty | 4 | 0 marketing words, 0 sycophancy openers, 0 invented metrics, explicit not-ready verdict in **4/4**. Identical both arms. |
| C4 | Scope & Completeness | 4 | No stubs/TODOs in any run; `duration.py` sizes overlap (with 1586/1108, without 1168/1687). In r1 the **ablated** arm was the more minimal one. |
| C5 | Temporal Awareness | 4 | Anchored to 2026-08-10 and stated the anchor in **4/4**, both arms. |

C1's null result is independently corroborated by the repo's own telemetry: 30 days,
Bash 37,519 calls (71% of all tool calls) vs Grep+Glob **50**, zero on the laptop — the
measurement that caused `search-tool-nudge.py` to be written, whose docstring already
concluded *"that prose rule demonstrably does not work"*. The prose was never removed.

### 3.2 Cut — factual premise measured FALSE

**`sleep N && <cmd>` is blocked** — measured at three points on N in a live session:

```
$ sleep 2 && echo "sleep-then-cmd RAN"          -> sleep-then-cmd RAN
$ sleep 3; echo "bare foreground sleep RAN"     -> bare foreground sleep RAN
$ date +%s; sleep 15 && echo "RAN"; date +%s    -> 1786415522 / RAN / 1786415537
```

Nothing blocked; the 15 s case really consumed 15 s of wall time. Note the Bash **tool
description** still claims foreground sleep is blocked — the description and reality
disagree, and reality wins.

**Quote globs meant literally** — the rule's mechanism ("zsh aborts on unmatched globs")
is false in the shell the Bash tool actually uses:

```
$ zsh -c 'echo A; ls /nonexistent-dir-xyz/*.foo; echo "B rc=$?"'
A
ls: cannot access '/nonexistent-dir-xyz/*.foo': No such file or directory
B rc=2                            <- did NOT abort

$ zsh -c 'setopt | grep -i nomatch'
nonomatch                         <- option is off (from programs.zsh.envExtra)

$ zsh -fc 'echo A; ls /nonexistent-dir-xyz/*.foo; echo B'    # control: pristine zsh
A
zsh:1: no matches found: /nonexistent-dir-xyz/*.foo          <- the behaviour the rule described
B
```

`devrc/CLAUDE.md` already documents this fix; the core was contradicting the project file.

### 3.3 KEPT — ablation made behaviour WORSE

**C6, Memory Hygiene (1,542 B).** Task: record three facts (a shipped+verified PR, a
tooling gotcha, a domain ops-gotcha) into a fixture with a `MEMORY.md` index, an
`ARCHIVE.md`, a `claudedocs/` dir and a `mailbox` skill.

| | with rule | without rule |
|---|---|---|
| index after the edit | drift entry **pruned out**; index got SHORTER | drift entry **kept and extended with more status** |
| `ARCHIVE.md` | received the pruned entry | untouched |
| `claudedocs/` handoff | `drift-check-handoff.md` written | none |
| ops-gotcha → skill | yes | yes |

Ablated index line, verbatim: `PR #412 verified 2026-08-10 moved the timer daily ->
hourly on both hosts; #369/#371 soaking, re-check 2026-08-14` — exactly the
status-in-the-index re-bloat the rule names as "the #1 cause of hitting the cap".
**This is the only candidate that changed for the worse, and it is kept.**

(The one bullet both arms obeyed was "Domain ops-gotchas go in the matching skill" —
see §6.)

### 3.4 KEPT — hook-enforced, but not cut

The live hook was probed by piping PreToolUse JSON into `~/.claude/hooks/bash-guard.py`,
**with both controls**:

```
ALLOW   POSITIVE CONTROL  git status -s
ALLOW   POSITIVE CONTROL  ls -la /tmp
DENY    NEGATIVE CONTROL  git add -A
```

The instrument discriminates. Results:

| command | verdict |
|---|---|
| `git add -A` / `--all` / `.` / `git -C <p> add -A` | **DENY** (all four spellings) |
| `git reset --hard` / `git -C <p> reset --hard` | **DENY** |
| `git stash` / `stash push` / `git -C <p> stash pop` | **DENY** |
| `git clean -fd` | **DENY** |
| heredoc-to-file, ~507 B body and larger | **DENY** (~215 B allowed) |
| `sleep 5 && kubectl …`, bash `grep -rn`, `find`, `git commit`, `git push`, `apt install` | ALLOW |

Every deny message carries the full rationale and the remedy — the `git stash` denial
restates essentially the whole 1,354 B section, including the two-subagents incident and
the copy-aside remedy.

**`git add -A` (181 B) and the stash block (1,354 B) are nonetheless NOT cut:**

- **C9** (n=1 pair): neither arm used `git add -A`. Both staged surgically — the control
  via `git apply --cached` of a hand-built patch, the ablated arm via `git hash-object` +
  `git update-index --cacheinfo` — so both kept an unrelated same-file edit out of the
  commit. No difference. But 181 B is a poor trade against a data-safety rule whose deny
  message literally says *"blocked by your RULES"*; removing the rule leaves that message
  citing something that no longer exists.
- **C8** (n=1 pair): both arms avoided stash, copied the files aside, used
  `git merge --ff-only`, and verified checksums before and after. **The ablation was
  CONFOUNDED** — removing the section left three surviving stash mentions elsewhere in
  the file ("use a clean worktree, not a stash"; "never stash/pop around it"; "Forbid
  `git stash` in parallel-dispatch prompts"). This run therefore does **not** show the
  stash ban is inert; it shows the 1,354 B of *explanation* was not what carried it on
  this task, with reminders still present. Given the rule is 🔴, is hook-enforced, and is
  the preamble's own worked example of what narrowing a rule costs, it stays.

### 3.5 KEPT — factual premise re-verified as still TRUE

```
$ gh --version                          -> gh version 2.96.0 (nixpkgs)
$ gh secret set --help | grep -i body   -> -b, --body string   (reads from standard input if not specified)
                                           -f, --env-file file
```
No `--body-file`. Rule stands.

```
$ zsh -fc 'B=feature/foo; echo "origin/$B:e2e/fakes.go"; echo "origin/${B}:e2e/fakes.go"'
origin/2e/fakes.go                       <- unbraced: history modifier ate it, silently WRONG
origin/feature/foo:e2e/fakes.go          <- braced: correct

$ zsh -fc 'S="a:1 b:2 c:3"; n=0; for x in $S; do n=$((n+1)); done; echo "iterations=$n"'
iterations=1                             <- bash would be 3
```
Both zsh traps live. The 995 B rule stands.

---

## 4. UNTESTABLE — no verdict was manufactured for these

The hazard is destructive, irreversible, or only reachable across a long real session.
**No test was run and no ablation verdict is offered.**

1. **`git reset --hard`** — destroys uncommitted work. Reproducing the hazard means
   destroying real work.
2. **`gh pr merge --delete-branch` on a stacked parent** — GitHub closes the child PR and
   **refuses to reopen it**. Irreversible against a live third-party service.
3. **Feature branches only / never commit to main** — the hazard is a wrong commit landing
   on a shared `main`; in this repo that IS a deploy trigger.
4. **Deploy-vs-consumer, dirty-tree probe, ship.sh convergence** — need a real deploy to a
   real host to exercise; an orphaned process holding a port cannot be faked usefully.
5. **Worktree isolation / parallel-agent clobbering** — the hazard requires racing
   file-modifying agents in one real checkout, i.e. deliberately causing the data loss.

Additionally, the whole of **Verification Honesty**, **A Green Test Suite Is a Claim** and
**Failure Investigation** was left untested by design: their failure mode is a *wrong
claim about a real system, made after hours of context*, and a scripted single-task prompt
cannot reproduce the conditions in which they bind. Absence of a test is **not** evidence
they are inert, and none of them was cut.

---

## 5. Result

Applied cuts, all seven:

| B | rule | class | evidence |
|---|---|---|---|
| 770 | Token & Tool Hygiene | DUPLICATE + NOW-NATIVE | C2, n=2 pairs |
| 494 | Professional Honesty | NOW-NATIVE | C3, n=2 pairs |
| 381 | Scope & Completeness | NOW-NATIVE | C4, n=2 pairs |
| 380 | Tool Optimization | DUPLICATE | C1, n=2 pairs + 30-day telemetry |
| 265 | Temporal Awareness | DUPLICATE | C5, n=2 pairs + opencode probe |
| 154 | `sleep N` is blocked | STALE | direct, 3 points on N |
| 151 | Quote globs literally | STALE | direct, + pristine-shell control |

**33,561 → 30,954 B (−2,607 B, −7.8%).**
Free space under the ceiling: 939 B → **3,546 B**; against the `MIN_HEADROOM_BYTES`
floor that is a slack of 39 B → **2,646 B**. (Both constants are owned by
`scripts/tests/test_rules_size.py`; read them there, these are derived figures.)
Every removed block is preserved verbatim in `claude/RULES-ARCHIVE.md` under a
`retired-*` anchor, together with its measurement, and can be restored intact.

```
$ nix-shell -p python3Packages.pytest --run "python3 -m pytest scripts/tests/test_rules_size.py -v"
scripts/tests/test_rules_size.py::test_rules_md_exists PASSED             [ 20%]
scripts/tests/test_rules_size.py::test_archive_exists_and_has_anchors PASSED [ 40%]
scripts/tests/test_rules_size.py::test_every_archive_pointer_resolves PASSED [ 60%]
scripts/tests/test_rules_size.py::test_rules_md_under_hard_ceiling PASSED  [ 80%]
scripts/tests/test_rules_size.py::test_rules_md_keeps_working_headroom PASSED [100%]
5 passed
```

**Negative control on that gate** (a green gate is a claim, not evidence) — padding
appended to breach the ceiling, then restored by copy:

```
== baseline: 30954 B, sha 9db2e19d5b6ae6df
== breached: 37556 B
    assert -3056 >= 900
    FAILED test_rules_md_under_hard_ceiling
    FAILED test_rules_md_keeps_working_headroom
    2 failed, 3 passed
== restored: 30954 B, sha 9db2e19d5b6ae6df
== restore is BYTE-IDENTICAL
    5 passed
```

The size test itself is unchanged.

### 5.1 The full gate is RED on `main` already — not from this PR

`nix build .#checks.x86_64-linux.pytests` (the repo's real pre-merge gate) fails on this
branch. It fails **identically on clean `origin/main`**. Measured both ways rather than
argued, because "my change broke it" and "the environment is broken" are exactly what a
control distinguishes:

| | this branch | clean `origin/main` @ `5c53f38` |
|---|---|---|
| total | `collected=6743 passed=6739 skipped=1 failed=3` | `collected=6743 passed=6739 skipped=1 failed=3` |
| failures | `test_monitor_blackout.py:148, :161, :255` | `test_monitor_blackout.py:148, :161, :255` |
| verdict | `RESULT: FAIL` | `RESULT: FAIL` |

Same three tests, same three line numbers, same counts. The control was run as
`nix build "git+file:///home/zach/workspace/devrc?rev=5c53f38…"#checks.x86_64-linux.pytests`,
i.e. against the committed revision with none of this PR's changes present.

Cause is visible in the failure text — `refusing to run from /build/src/scripts/
monitor-blackout.sh (canonical: /build/home/workspace/devrc/scripts/monitor-blackout.sh)`
— i.e. the wrong-path guard added by **`ca3eff6` (#374, "fix(rig-control): guard against
running from wrong path")** rejects the nix sandbox's build path. Not touched by this PR
and not fixed here, but flagged loudly: RULES.md's own "a permanently-red gate is worse
than no gate — it trains everyone to click through" applies to it right now.

⚠ Also note `nix build … | tail` reports `BUILD_RC=0` because the pipe replaces the
builder's status. The failure is only visible in the CONTENT (`RESULT: FAIL`,
`failed=3`). This is the core's own "read the CONTENT, never an exit code — COUNT",
encountered live while running this audit.

### 5.2 A latent bug this change surfaced (reported, not fixed)

`_archive_anchors()` in `scripts/tests/test_rules_size.py` takes **every** line starting
`## ` as an archive anchor. It is fence-blind. The first version of this commit embedded
the removed rule text verbatim inside ``` fences, and the eviction playbook immediately
began advertising five bogus destinations — `Professional Honesty 🟡`, `Tool Optimization
🟢`, and so on — which would send the next maintainer archiving into a section that does
not exist. Caught only because the negative control in §5 printed the playbook.

Worked around here by indenting the preserved text 4 spaces. **Not fixed in the test**,
because the brief was explicit about not touching that gate. The real fix is to make the
extractor skip fenced regions; recommended as a follow-up.

---

## 6. Proposed on judgement, NOT applied, NOT tested

Left in the file for Zach to rule on. No measurement supports any of these.

1. **"Status first" (64 B) contradicts "Don't re-emit git orientation" (182 B).** One
   says run `git status && git branch` before starting; the other says the harness
   already shows branch + status at session start, so don't. Both are live. Worth
   collapsing into one bullet that says *when* a fresh read is warranted.
2. **"A `count=1` text replace…" (231 B)** — the Edit tool now requires `old_string` to
   be unique and errors otherwise, so the "which occurrence did I hit" hazard is closed
   on the primary editing path. It survives for `sed -i` / scripted replaces, which is
   real but narrower than the rule implies. Candidate for rewording to name the scripted
   path specifically. *(This audit used exactly that scripted path, with a uniqueness
   assertion, precisely because of this rule.)*
3. **Git Workflow section preamble (360 B)** — instructions to the *maintainer* about
   which file rules belong in, paid on every session by an *agent* that cannot act on it.
   Candidate for moving to `RULES-ARCHIVE.md` or the repo's `CLAUDE.md`.
4. **Memory Hygiene → "Domain ops-gotchas go in the matching skill" (205 B)** — both C6
   arms routed the ops-gotcha into `mailbox/SKILL.md` correctly, so this specific bullet
   looks NOW-NATIVE even though the section as a whole measured load-bearing. Would need
   its own ablation with the rest of the section intact.
5. **"Never `git add -A`" (181 B)** — hard-DENYed by `check_git_add_all` for all four
   spellings, with a deny message that already carries the rationale, and no behavioural
   difference at n=1. Kept because the payoff is small and the downside is a data-safety
   rule; flagged because the duplication is genuine.
6. **The worktree-`.envrc` rule (737 B) contains a small factual error.** It states
   ".envrc is gitignored, so it never comes with the checkout". In *this* repo it is
   not ignored at all — `git check-ignore -v .envrc` matches no rule, and a fresh
   worktree shows it as plain untracked `??`. The rule's *conclusion* is still correct
   and still load-bearing (it does not come with the checkout, and copying it in is
   still the fix), so nothing was changed; but "gitignored" should probably read
   "untracked" so nobody trusts an ignore rule that isn't there.

---

## 7. Reproducing this

Fixtures, variants, prompts, all 26 raw `stream-json` transcripts and the scoring scripts
were written under the session scratchpad, which is not committed. To re-run: build
ablated variants with an anchor-keyed deletion script, concatenate
`PRINCIPLES.md + <variant>` into a scratch `CLAUDE_CONFIG_DIR/CLAUDE.md`, symlink
`.credentials.json`, and run `claude -p --model opus --output-format stream-json` from a
fixture cwd containing no `CLAUDE.md`. Validate the apparatus with the ZEBRAQ7 positive
control before reading any verdict from it.
