# devrc gate / tier / battery inventory — what has actually caught something

**Scan date:** 2026-09-07 · **Base sha:** `112a5225` · **Host:** workbench · clawgate task #525

Every gate, tier, battery and hook devrc runs, with **named evidence of a real catch** or the
explicit string `no evidence found`, and a `KEEP` / `DROP` / `TIER` verdict per row.

> 🔴 **Nothing here is a change.** The verdict column is a recommendation to the operator. No
> gate, target, battery or hook was dropped, disabled or made conditional by the PR that added
> this document, and no per-path selection rule was implemented. A dropped gate fails silently
> and is invisible until the day it would have fired — that step stays with a human.

---

## 1. How the enumeration was derived

Enumerated from the repo at scan time, not from the task description. The commands, so the
counts can be re-derived and disagreed with:

| family | command | count |
|---|---|---|
| pytest targets | `awk 'f&&/^\)$/{exit} f&&!/^[[:space:]]*#/&&NF' scripts/run-tests.sh` over `TARGET_FLOORS=(` | **31** |
| node suites | same shape over `SUITES=(` in `scripts/run-node-tests.sh` | **5** |
| nix check derivations | `grep -E '^        [a-zA-Z][a-zA-Z0-9_-]*[ ]*=' flake.nix` inside `checks.${system}` | **2** |
| mutation batteries | `ls scripts/tests/mutants-*` | **10** |
| Claude hooks | `ls scripts/claude-hooks/*.py` | **16** |
| drift-check arms | rc legend in `scripts/drift-check.sh` header (drift/actionable codes only) | **13** |
| pre-push gate | `ls githooks/` | **1** |
| Tekton checks | `gh pr view <n> --json statusCheckRollup` | **2** |

**81 rows total.** Derived figures: pytest floor-sum **21,202**; node floor-sum **1,367**.

Two counts differ from the task's own list, and the difference is the finding:

- The task said "~30 pytest targets". There are **31** in `TARGET_FLOORS` but only **30** in
  `HERMETIC_TARGETS`. The one in the first and not the second is `scripts/devhost-tests` — see
  its row.
- "16 Claude hooks" counts `*.py` files. Two of the sixteen are **not hooks**: `guard_core.py`
  is the shared predicate library `bash-guard` imports, and `register-nudge-hook.py` is the
  per-host registrar that writes the others into `settings.json`. Rows are kept for both,
  labelled, because the glob is what the criterion named.

---

## 2. Method, and what each instrument can and cannot see

Four scanners over the session-transcript corpus (**5,836** `.jsonl` files, 6.8 GB under
`~/.claude/projects/**`), plus `git log`, plus `gh`. This session's own transcript is
**excluded from every count** — it necessarily contains every search string.

🔴 **Every scanner carries a negative and a positive control, and the controls are reported.**
A table of zeros from a scanner wired to nothing is indistinguishable from a table of zeros
from gates that never fired, so the zero alone is not reportable.

| # | instrument | what it keys on | negative control | positive control |
|---|---|---|---|---|
| 1 | `scan_denials.py` | guard message **AND** `toolDenialKind` on the same transcript line | fabricated arm name → **0** ✅ | 14 arms non-zero ✅ |
| 2 | `scan_stop_hooks.py` | each hook's own emitted prefix, **no** `toolDenialKind` requirement | fabricated arm name → **0** ✅ | blind-stage arm → 97 sessions ✅ |
| 3 | `scan_targets.py` | `run-tests.sh`'s own `FAIL <target> (` / `PASS <target> (` verdict lines | non-existent target → **0/0** ✅ | 31 real targets all PASS>0 ✅ |
| 4 | `scan_drift.py` | `drift-check: <verdict> (rc=N)` — the script's own machine-emitted line | `rc=99`, a code it cannot emit → **0** ✅ | see below ⚠ |

⚠ **Scanner 4's first positive control FAILED and the table was not trusted until it was
explained.** `rc=0` returned 0 hits. Cause, read out of the source rather than assumed:
`scripts/drift-check.sh:3554` guards the `(rc=$rc)` line behind `if [ "$rc" != 0 ]`, so a clean
run is **structurally incapable** of emitting that format — it prints
`drift-check: no drift on the host(s) CHECKED` instead. Re-run against that sentence: **34
sessions**. Control passes; the arm table below stands.

**What instrument 1 buys over instrument 2.** On the same string, instrument 2 (text only) finds
97 sessions and instrument 1 (text + `toolDenialKind`) finds 75. The 22-session gap is sessions
that *discussed* the guard without being blocked by it. The co-requirement is doing real work,
which is why hook rows cite the denial-confirmed number where one exists.

### 🔴 Honest limits of this method

- **A red is not a save.** A `FAIL` line proves a target went red in a real session. It does
  **not** prove it stopped a defect reaching `main` — the red could be a test being written, or
  a broken test. Rows report this as **RED-IN-SESSION**, which is weaker than a confirmed save
  and stronger than a run-count. A run-count cannot go red at all.
- **Absence of evidence is not a DROP argument.** A guard can be correct and never yet fire.
  Rows reading `no evidence found` are called out as such and are mostly `KEEP`.
- **The corpus is not the whole history.** It covers sessions on these two hosts whose
  transcripts still exist. A gate that fired before the corpus starts, or on a host whose
  transcripts were pruned, is invisible here.
- **`git log` evidence is WEAK** and is labelled `(weak)` per the task's own assumption.
- **This document quotes no transcript content.** The repo is public; sessions are cited by
  count, and by ID only where an ID is itself the evidence.
- **Phase 0's `/activity` finding is taken as measured, not re-derived.** `activity.events`
  records no gate outcome, so it can yield a run-count and never a catch. No run-count in this
  document is presented as value.

---

## 3. Pytest targets — `scripts/run-tests.sh` `TARGET_FLOORS` (31 rows)

`F-sess` = distinct sessions where the gate printed `FAIL <target>`; `P-sess` = sessions where it
printed `PASS <target>`. Evidence via instrument 3.

Evidence cells name **one example session id** in which that target's `FAIL` line was emitted,
alongside the total. The id is the named evidence criterion 3 asks for; the count says how
typical it is. Ids beginning `agent-` are subagent transcripts, which is why some repeat.

| # | target | floor | F-sess | P-sess | evidence (example session id ×total) | verdict | reason |
|---|---|---:|---:|---:|---|---|---|
| A1 | `scripts/tests` | 12793 | 224 | 410 | `bfb2ae02-9576-4cb4-bdc3-5bc01b9504bc` ×224 | **KEEP** | Holds the repo-wide content/claim gates (captured-text, rules-size, ci-claim, skill-tiers); a diff cannot be shown not to reach it |
| A2 | `scripts/collector/tests` | 260 | 2 | 322 | `beb749f5-fa85-4221-a2b0-210778db964d` ×2 | **TIER** | Subsystem-local; two reds in the whole corpus |
| A3 | `scripts/collector/keylog/tests` | 79 | 31 | 322 | `1f045584-5d0e-4836-8652-28a51c19037b` ×31 | **TIER** | Earns its keep but only on collector changes |
| A4 | `scripts/collector/claude/tests` | 219 | 4 | 337 | `dced6c6b-c435-4906-876d-de3f6ccb9ca6` ×4 | **TIER** | Subsystem-local |
| A5 | `scripts/collector/i3/tests` | 12 | 3 | 341 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Subsystem-local, 12 tests |
| A6 | `scripts/collector/browser-ext/tests` | 12 | 3 | 590 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Subsystem-local, 12 tests |
| A7 | `scripts/collector/opencode/tests` | 224 | 5 | 360 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A8 | `scripts/dl-router/tests` | 942 | 28 | 589 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×28 | **TIER** | Strong catch record, entirely within one subsystem |
| A9 | `scripts/browser-bridge/tests` | 867 | 75 | 584 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×75 | **TIER** | Third-highest catch count; still subsystem-local |
| A10 | `scripts/validation/tests` | 97 | 3 | 366 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Subsystem-local |
| A11 | `scripts/session-analysis/tests` | 440 | 5 | 371 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A12 | `scripts/session-analysis/session_insight/tests` | 55 | 2 | 379 | `agent-abd8fca1ce83eef68` ×2 | **TIER** | Subsystem-local |
| A13 | `scripts/mail-actions/tests` | 129 | 26 | 384 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×26 | **TIER** | Subsystem-local |
| A14 | `scripts/signal/tests` | 920 | 48 | 290 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×48 | **TIER** | Second-highest catch count; subsystem-local |
| A15 | `scripts/initiatives/tests` | 745 | 28 | 390 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×28 | **TIER** | Subsystem-local |
| A16 | `scripts/repo-cos/tests` | 315 | 5 | 400 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A17 | `scripts/task-spec-drafter/tests` | 135 | 5 | 398 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A18 | `scripts/check-clickup-addressed/tests` | 232 | 2 | 212 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×2 | **TIER** | Subsystem-local |
| A19 | `.../test_guard_core.py` | 1260 | 5 | 414 | `agent-abd8fca1ce83eef68` ×5 | **KEEP** | 1,260 tests over the predicate library behind `bash-guard`, the one hook that denies destructive commands |
| A20 | `.../test_next_step_nudge.py` | 124 | 3 | 368 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Hook-local |
| A21 | `.../test_registrar_activation.py` | 16 | 4 | 351 | `agent-abd8fca1ce83eef68` ×4 | **TIER** | Hook-local |
| A22 | `.../test_agent_ledger_hook.py` | 38 | 4 | 348 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×4 | **TIER** | Hook-local |
| A23 | `.../test_clawgate_writeback_guard.py` | 282 | 2 | 350 | `agent-abd8fca1ce83eef68` ×2 | **TIER** | Hook-local |
| A24 | `.../test_clawgate_task_interview_guard.py` | 285 | 35 | 277 | `e582aa63-1d3d-443d-acda-d332d73055c9` ×35 | **TIER** | Hook-local, strong catch record |
| A25 | `.../test_gh_issue_closing_condition_guard.py` | 451 | 11 | 209 | `f23b37ec-68c0-4ad5-8ecd-659f21a88ae9` ×11 | **TIER** | Hook-local |
| A26 | `.../test_on_disk_artifact_names.py` | 15 | 3 | 343 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Hook-local |
| A27 | `.../test_handoff_write_guard.py` | 66 | **0** | 92 | `no evidence found` — instrument 3, `FAIL scripts/claude-hooks/tests/test_handoff_write_guard.py (`, 0 hits in 5,836 transcripts | **KEEP** | Never red, but it covers a Stop guard confirmed to have blocked in **139** sessions (row E11). Untested-and-load-bearing is the wrong thing to drop |
| A28 | `.../test_bg_command_capture.py` | 76 | 1 | 282 | `62565689-1242-4257-99f2-813294656554` ×1 | **TIER** | Hook-local |
| A29 | `.../test_git_add_provenance_nudge.py` | 21 | **0** | 87 | `no evidence found` — instrument 3, `FAIL .../test_git_add_provenance_nudge.py (`, 0 hits | **TIER** | Never red; the nudge it covers has fired in 81 sessions, so keep it running on hook changes |
| A30 | `scripts/opencode/tests` | 86 | 7 | 400 | `agent-abd8fca1ce83eef68` ×7 | **TIER** | Subsystem-local |
| A31 | `scripts/devhost-tests` | 6 | **0** | **4** | `no evidence found` — instrument 3, `FAIL scripts/devhost-tests (`, 0 hits; and only **4** PASS sessions against 200–590 for every other target | **KEEP (fix first)** | 🔴 See below — this row is a finding, not a recommendation to drop |

### 🔴 A31 is the one structural finding in this family

`scripts/devhost-tests` is in `TARGET_FLOORS` (31 entries) and **not** in `HERMETIC_TARGETS` (30
entries). It therefore only runs under `--set all`, which is why it appears in 4 sessions while
its neighbours appear in 200–590. Its floor of 6 is counted into the derived global floor
regardless.

This is already the failure mode the task's non-goals warn about — a target that has silently
stopped running on most invocations — except it arrived by omission rather than by anyone
choosing it. **Recommendation: decide it explicitly.** Either add it to `HERMETIC_TARGETS` so it
runs like the others, or state in the source why it is `all`-only. Do not leave it implicit.

---

## 4. Node suites — `scripts/run-node-tests.sh` `SUITES` (5 rows)

| # | suite | files\|floor | F-sess | P-sess | evidence (example session id ×total) | verdict | reason |
|---|---|---|---:|---:|---|---|---|
| B1 | `scripts/browser-bridge/tests` | 18\|540 | 76 | 589 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×76 | **TIER** | Highest node catch count; subsystem-local |
| B2 | `scripts/dl-router/tests` | 13\|500 | 29 | 597 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×29 | **TIER** | Subsystem-local |
| B3 | `scripts/collector/browser-ext/tests` | 2\|20 | 3 | 595 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Subsystem-local |
| B4 | `claude/skills/clickup/test` | 6\|179 | 4 | 407 | `71983f6d-4a60-4f51-b6c0-2a9dda7e0abb` ×4 | **TIER** | Skill-local |
| B5 | `scripts/discord-embed-ext/tests` | 2\|128 | **0** | 280 | `no evidence found` — instrument 3, `FAIL scripts/discord-embed-ext/tests`, 0 hits in 5,836 transcripts; the example-id scanner independently returned NONE for this suite, which is the two scanners agreeing | **TIER** | Never red, but runs; extension-local so tiering costs nothing |

---

## 5. Tiers and runners (3 rows)

| # | thing | evidence | verdict | reason |
|---|---|---|---|---|
| C1 | `nix build .#checks.x86_64-linux.pytests` (Tekton `devrc-pytests`) | **CONFIRMED CATCH, live at scan time**: open PRs **#1370** and **#1366** are `FAILURE` on `tekton/devrc-pytests` right now. Named in `CLAUDE.md`: PR **#773** passed `gate.sh` four consecutive times and then went red on this tier — a defect only this tier could see | **KEEP** | The only tier that builds from a `.git`-less store copy; documented to see defects the dev-host tier structurally cannot |
| C2 | `nix build .#checks.x86_64-linux.nodetests` (Tekton `devrc-nodetests`) | `SUCCESS` on all 6 open PRs sampled; no red observed at scan time. Per-suite reds exist in the dev-host tier (rows B1–B4) but that is the other tier. For this derivation specifically: `no evidence found` — search: `gh pr view <n> --json statusCheckRollup` over the 6 most recent open PRs | **KEEP** | Cheap relative to pytests, and it is half the sandbox-tier claim; dropping it would leave the node layer with no store-copy tier at all |
| C3 | `scripts/gate.sh` (dev-host tier, rc 90 = could-not-vouch) | **CONFIRMED CATCH**: `6c8a7662-7de7-4001-94ab-32a6ec272094`; `GATE: RESULT=FAIL` in **288** sessions, `could-not-vouch` in **241**, `panic: test timed out` in **787**. The rc-90 arm exists because a wrapper's exit status disagreed with the runners' own `RESULT:` line — `CLAUDE.md` records four agents reporting `exit 0` over `RESULT: FAIL` on 2026-08-11 | **KEEP** | The 90 arm catches the instrument lying, which is a different class from a failing test |

---

## 6. Mutation batteries — `scripts/tests/mutants-*` (10 rows)

🔴 **Structural finding first: none of these is wired into any gate.**
`grep -rn "mutants-" scripts/gate.sh scripts/run-tests.sh scripts/run-node-tests.sh flake.nix githooks/` returns **nothing**. They are manual, one-shot verification instruments run when their
subject changes. That is a defensible design — but it means "battery" and "gate" are different
things, and no battery is on the critical path of any change.

Evidence per row is the introducing commit's own account of what the battery found. This is
commit-message evidence, which is stronger than `git log` alone (it names the survivors) but is
still self-reported by the authoring session.

| # | battery | added | evidence | verdict | reason |
|---|---|---|---|---|---|
| D1 | `mutants-audit-dispatch.py` | `1b2117b6` 08-28 | 16 mutants, each naming its exact killer set, all killed; R1 is the reachability control (PR #958) | **KEEP** | Committed negative-control battery for the auditor brief |
| D2 | `mutants-audit-ladder.sh` | `88ede2c0` 08-27 | **CONFIRMED CATCH**: PR #933 states one of PR #900's published numbers was the wrong metric, found by checking in the instruments #900 had only run in a scratchpad. 13 mutants + 2 controls | **KEEP** | Caught a wrong published figure |
| D3 | `mutants-base-clone.sh` | `10ad288a` 08-24 | **CONFIRMED CATCH**: PR #809 — a path deleted upstream was reported FAILED forever and never removed; regression coverage shown red on pre-change code | **KEEP** | Real defect, with a watched-fail matrix |
| D4 | `mutants-claim-work.sh` | `2770db2d` 08-26 | **CONFIRMED CATCH**: PR #881 — the claim namespace was per-origin so the lock was inert cross-repo, and a claim that landed reported UNCLAIMED. **9 surviving mutants**, each now with a test that goes red | **KEEP** | Highest-value battery in the set: 9 survivors = 9 real coverage gaps |
| D5 | `mutants-dead-guard-exclude.sh` | `a5aa037b` 08-26 | **CONFIRMED CATCH**: PR #888 — a registry glob swept up a non-pytest file and published 44 flags off a run that traced nothing; a raises-only assertion let a mutant survive | **KEEP** | Caught a 44-flag false-positive publication |
| D6 | `mutants-dead-guard.sh` | `ad8259cf` 08-25 | PR #842 — states the ~180 guards the scan cannot see, in the artifact; identifies dead recognition code and reporting branches with no instances | **KEEP** | Its value is the stated blind spot as much as the finding |
| D7 | `mutants-handoff-cap.sh` | `5348a9a1` 08-30 | PR #962 — mutation-verified the handoff write cap; honest limits stated in the module and skill | **KEEP** | Guards a cap that is otherwise prose |
| D8 | `mutants-install-sh.sh` | `648f08c2` 08-27 | **CONFIRMED CATCH ×2**: PR #905/#908 — `install.sh` could not install at all on a home-manager host (`git config --global` targets the read-only nix store), **and** fixing it exposed a worse harness bug the audit had not seen (detector matched the first assertion's message while the mutant was killed on the second) | **KEEP** | Caught both a real defect and a defect in the instrument |
| D9 | `mutants-shared-clone-sync.sh` | `180ac5c2` 08-26 | **CONFIRMED CATCH**: PR #869 — 20 mutants, positive control 49 passed/0 failed; **two mutants survived the first pass** (`head-did-not-move-check-gone` and one other) | **KEEP** | Survivors are the evidence; a fully-green first pass would have proved less |
| D10 | `mutants-worktree-prune.sh` | `4c1d6da9` 08-27 | **CONFIRMED CATCH**: PR #895 — a round-2 safety flag that blocked itself; **three test gaps** the sweep exposed, all now killed | **KEEP** | Caught a self-blocking safety flag |

**All 10 have a named catch.** No `DROP` in this family. The recommendation that follows is not a
drop but a **wiring** one — see §10.

---

## 7. Claude hooks — `scripts/claude-hooks/*.py` (16 rows)

`sessions` = distinct sessions in which the hook's own emitted text appears. For `bash-guard`
arms the figure is **denial-confirmed** (instrument 1: text **and** `toolDenialKind`). For Stop
guards and PostToolUse nudges it is instrument 2 (text only), because a Stop block does not
produce `toolDenialKind` — this is instrument blindness, stated per row, not a weaker gate.

`bash-guard.py` is one file with 14 independently-reachable arms. Criterion 2 asks for one row
per hook, so it is one row (E3) with its arms broken out beneath — a single `KEEP`/`DROP` over 14
arms would hide that three of them have never fired.

| # | hook | event | sessions | evidence | verdict | reason |
|---|---|---|---:|---|---|---|
| E1 | `agent-ledger-hook.py` | PostToolUse, SessionStart, Stop, UserPromptSubmit | n/a | `no evidence found` — search: instrument 2 for its emitted `write: … measured: … records: …` prefix, 0 hits. **Not a gate**: it writes a ledger and blocks nothing, so it has no catch to find | **KEEP** | Telemetry, not verification; excluded from the value question by kind |
| E2 | `audit-pr-nudge.py` | PostToolUse | **508** | `b91e2065-9da3-42f1-9522-b41d3cb067ac` ×508 sessions | **KEEP** | Advisory; highest-reach nudge in the set |
| E3 | `bash-guard.py` | PreToolUse | **see arms** | **CONFIRMED CATCH, 11 of 14 arms** — denial-confirmed, ids per arm below. Also blocked one of *this* session's own searches mid-inventory | **KEEP** | The only hook that denies destructive commands; strongest evidence in the repo |
| E4 | `bg-command-capture.py` | PreToolUse, PostToolUse | n/a | `no evidence found` — search: instrument 2; the module emits no distinctive user-visible string to key on, so this is an instrument limit, not a measured zero. **Not a gate**: capture only | **KEEP** | Telemetry, not verification |
| E5 | `claude-notify.py` | Stop, SubagentStop, UserPromptSubmit | n/a | `no evidence found` — search: instrument 2 on its `coalesce gate error` string, 0 hits. **Not a gate**: emits toasts | **KEEP** | Notification, not verification |
| E6 | `clawgate-task-interview-guard.py` | PreToolUse | **67** (denial-confirmed) / 60 (text) | **CONFIRMED CATCH**: `8d97d643-d204-45f6-8239-2d6d24a2533c`, 93 denial-confirmed blocks across 67 sessions | **KEEP** | Enforces the `## Acceptance criteria` contract that unlocks agent self-completion |
| E7 | `clawgate-writeback-guard.py` | PostToolUse, Stop | **91** | **CONFIRMED CATCH**: `8d97d643-d204-45f6-8239-2d6d24a2533c`, 443 blocks across 91 sessions. Its own message cites tasks **#193** and **#194**, dispatched and paid for twice before it existed | **KEEP** | Named, costed defect that recurred until this shipped |
| E8 | `gh-issue-closing-condition-guard.py` | PreToolUse | **24** (denial-confirmed) | **CONFIRMED CATCH**: `eb0f4fe7-5c3e-4d25-9b74-3ce9c46027e5`, 32 denial-confirmed blocks across 24 sessions | **KEEP** | Enforces the closing-condition rule that stops un-closable work items |
| E9 | `git-add-provenance-nudge.py` | PostToolUse | **81** | `eb0f4fe7-5c3e-4d25-9b74-3ce9c46027e5` ×81 sessions | **KEEP** | Complements E3's blind spot: E3 blocks blind-staging, this catches staging someone else's file by path |
| E10 | `guard_core.py` | — (library) | via E3 | **Not a hook**, so it has no catch of its own: `no evidence found` for `guard_core` as an independent actor. Its catches are E3's 11 confirmed arms, which import from it; its 1,260 tests are row A19 | **KEEP** | One-rule-one-place: the arms live here rather than at each call site |
| E11 | `handoff-write-guard.py` | PostToolUse, Stop | **139** | **CONFIRMED CATCH**: `53d533ef-b73c-4f58-8798-b2964d538913`, 473 blocks across 139 sessions. Separately 1 session hit the cannot-measure NOTICE path — correct behaviour, not a block | **KEEP** | Its own message cites 22 of 253 `/resume` sessions (8.7%) that never recorded their work |
| E12 | `next-step-nudge.py` | Stop | **482** | `b91e2065-9da3-42f1-9522-b41d3cb067ac` ×482 sessions | **KEEP** | Advisory; wide reach |
| E13 | `register-nudge-hook.py` | — (registrar) | n/a | `no evidence found` — **not a hook**, so it emits no block or nudge to search for; it writes the other hooks into the unmanaged per-host `settings.json`. Its coverage is row A21 (16 tests) | **KEEP** | Without it the hooks are symlinked but unregistered on a new host |
| E14 | `search-tool-nudge.py` | PostToolUse | **294** | `e36f9cdb-d845-46ac-8471-26fa2c129337` ×294 sessions — and it fired **in this session**, on the `.gitignore`-blind recursive search during this inventory | **KEEP** | Caught a real instance during the work that produced this document |
| E15 | `session-stamp.py` | PreToolUse | n/a | `no evidence found` — search: instrument 2; emits no distinctive string, so this is an instrument limit, not a measured zero. **Not a gate**: stamps session identity | **KEEP** | Infrastructure other hooks key on |
| E16 | `shell-env-nudge.py` | PostToolUse | **540** | `a8f13c58-3ccc-485d-adc7-f8a06f606075` ×540 sessions — the widest-reach hook measured | **KEEP** | Memory `agent-shell-env-handles` records this moving adoption 0 → 50% |

### E3 arm breakdown — denial-confirmed (instrument 1)

| arm | blocks | sessions | example session id | verdict |
|---|---:|---:|---|---|
| `cd <path> && git …` → `git -C` | 4993 | **3105** | `agent-a2a677167f6124c33` | **KEEP** — by far the most-fired guard in the repo |
| large heredoc → use Write | 1139 | **1094** | `b91e2065-9da3-42f1-9522-b41d3cb067ac` | **KEEP** |
| `pkill -f <pattern>` | 130 | **129** | `646b087e-fbf1-4eac-9520-975bdd98f625` | **KEEP** — the sibling-agent-kill class |
| blind-stage (`git add -A`/`--all`/`.`) | 77 | **75** | `62ad62c9-2c4d-4f5d-9c00-fb12514bc406` | **KEEP** |
| `git reset --hard` | 26 | **26** | `87a5b52a-e757-45e1-826d-9afd81515f50` | **KEEP** |
| public IP + repo publish | 23 | **18** | `eb0f4fe7-5c3e-4d25-9b74-3ce9c46027e5` | **KEEP** — public-repo leak class |
| `git clean -f` | 10 | **10** | `f80cbf52-2d4e-4fda-a1e5-4665c56cee7a` | **KEEP** |
| `git stash` | 9 | **9** | `6c8a7662-7de7-4001-94ab-32a6ec272094` | **KEEP** — the repo-global stash hazard |
| private key in command | 6 | **6** | `a5069618-8629-4e0e-8af7-f9ecb5606670` | **KEEP** — secret-leak class |
| secret-like + repo publish | 2 | **1** | `agent-a1e07132635e7d4f8` | **KEEP** |
| `talosctl reset` | 1 | **1** | `c7555365-25c5-4932-a845-728cf9626667` | **KEEP** — one block; the thing it prevents is wiping a cluster node |
| `mkfs` | **0** | **0** | — | **KEEP** — `no evidence found`; search: instrument 1, `is blocked — it FORMATS a filesystem` |
| `dd` to block device | **0** | **0** | — | **KEEP** — `no evidence found`; search: instrument 1, `writing to a block device overwrites` |
| `rm -r $HOME` | **0** | **0** | — | **KEEP** — `no evidence found`; search: instrument 1, `is blocked — that is your home directory` |

🔴 **The three zero-firing arms are the clearest case in this document for KEEP-despite-no-evidence.**
Their run cost is a regex on a string already being matched for eleven other arms — effectively
zero — and the loss they prevent is total and unrecoverable (a formatted disk, an overwritten
block device, a deleted `$HOME`). Expected-value reasoning gives the same answer a run-count
never could: **frequency is the wrong axis for a guard whose downside is unbounded.** The
`talosctl reset` arm at exactly one firing is the same argument with one data point.

---

## 8. `scripts/drift-check.sh` arms (13 rows)

Evidence via instrument 4 — the script's **own** machine-emitted verdict line, so an observed
`(rc=N)` is a direct record of arm N firing, not prose about it. `sessions` = distinct sessions.

| # | rc | arm | sessions | evidence | verdict | reason |
|---|---:|---|---:|---|---|---|
| F1 | 8 | `main` DIVERGED / AHEAD (un-pushed commits) | **6** | **CONFIRMED CATCH ×6**, e.g. `agent-abad65297ffbc6474`. `CLAUDE.md` records 2026-08-06 (two un-pushed workbench commits blocked `ship.sh` for hours, with a regrowth timer due to fire on the very bug the undelivered commit fixed) and a 2026-08-09 recurrence rescued as **#366** | **KEEP** | The most consequential arm: a host in this state silently stops receiving every future change |
| F2 | 10 | `main` BEHIND origin/main | **12** | **CONFIRMED CATCH ×12** — most-fired arm; `962b6c35-ece3-4a9a-a673-b2a0d3968f46` | **KEEP** | Cheap, high-frequency, directly actionable |
| F3 | 12 | checkout not on branch `main` | **7** | **CONFIRMED CATCH ×7**; `67a8939c-6ce5-44db-95c1-709064834f50` | **KEEP** | Cheap; catches a checkout that will silently stop converging |
| F4 | 13 | remote unreachable N consecutive runs | **0** | `no evidence found` — search: instrument 4, `(rc=13)`, 0 of 5,836 transcripts | **KEEP** | An escalation ladder; zero firings means hosts have stayed reachable, which is the arm working as designed, not idle |
| F5 | 14 | managed symlinks resolving to nothing | **1** | **CONFIRMED CATCH**: `agent-a6069f54fbee9b955`. `CLAUDE.md`: 2026-08-11, **46 of 139** managed links on the laptop dangled into a GC'd `/nix/store` path while the checkout was byte-identical to `origin/main` | **KEEP** | The failure git is structurally blind to; one catch, and it was total |
| F6 | 15 | host parity — `settings.json` keys / `enabledPlugins` | **6** | **CONFIRMED CATCH ×6**; `971208ab-013b-4b01-8e27-6f86fd832f3e` | **KEEP** | `settings.json` is per-host and unmanaged; nothing else compares it |
| F7 | 16 | fuzzyclaw phase-2 gate opened (ACTIONABLE, not drift) | **2** | **CONFIRMED ×2**; `agent-acca1ea01eb070f0f` | **KEEP** | Least-severe code, `SuccessExitStatus=16`; it reports a safe deletion, not drift |
| F8 | 17 | built-source subtree not current | **16** | **CONFIRMED CATCH ×16 — most-fired drift arm**; `bd508253-0f12-49bd-9afb-95d6be09e733`. `CLAUDE.md`: 2026-08-14, the laptop's `homelab-talos` was 24 commits behind and shipped a `clawgatectl` with no `task status` under a `0.7.95` label; the command printed help and **exited 0** while drift-check was green | **KEEP** | Git parity is not source parity; this is the only detector |
| F9 | 18 | built-source scope UNMEASURABLE N runs | **3** | **CONFIRMED CATCH ×3**: `agent-a5558f4e0ce724da4`. `CLAUDE.md`: measured 2026-08-18, `tmux-fuzzyclaw` on a local branch with no upstream, `unmeasured=1`, rc 0 — concealing a genuinely divergent build between the hosts | **KEEP** | Closes the "we could not look, so we escalated never" gap rc 17 left |
| F10 | 22 | deployed `skillOverrides` vs `skill-tiers.json` | **0** | `no evidence found` — search: instrument 4, `(rc=22)`, 0 hits. **Expected**: the arm is adopted-then-drifted only, and `CLAUDE.md` states the ledger is applied to **no** host by default | **KEEP** | Cannot fire until a host adopts; a zero here is the documented design, not silence |
| F11 | 23 | untracked file in a nix-read path, N runs | **2** | **CONFIRMED CATCH ×2**: `agent-aeaab911fc0e2fbad`. `CLAUDE.md`: measured 2026-08-25, one such file had sat on the workbench ~3 weeks with every check green | **KEEP** | Catches deployed-but-unbacked files |
| F12 | 24 | `main` has no required checks while declared on | **8** | **CONFIRMED CATCH ×8**; `a76d7e7a-aa4d-4c43-a7c9-2540b28345c5` | **KEEP** | The fourth kind of parity: whether `origin/main` is still worth matching |
| F13 | 25 | the declaration itself is stale | **3** | **CONFIRMED CATCH ×3**: `agent-a7d557043e832ae21` — and it is the detector for rc 24 being disarmed | **KEEP** | Guards the guard; three firings in the ~4 days since `77dc3642` (2026-09-03) |

**12 of 13 arms have fired.** The two zero rows (F4, F10) each have a structural reason
recorded in the source, and neither is an idleness argument.

---

## 9. Pre-push and Tekton (3 rows)

| # | thing | evidence | verdict | reason |
|---|---|---|---|---|
| G1 | `githooks/` pre-push gate | **CONFIRMED CATCH ×3, all three about the gate itself**: `648f08c2` (#905/#908) — `install.sh` could not install **at all** on a home-manager host; `db896dae` (#858) — the push audit was grading pytest fixture repos, 4 runs and 167,977 output tokens, **none about real code**; `3ad7f66a` (#830) — root cause was git exporting `GIT_DIR` from a linked worktree. `tests-on-push` appears in 240 sessions | **KEEP (with the standing warning)** | 🔴 Its installed state is **volatile per clone** and `core.hooksPath` is the only answer. `CLAUDE.md` records incident **#322**: a push from a worktree returned with the branch REWRITTEN. Do not treat this as a reliable gate |
| G2 | `tekton/devrc-pytests` | **CONFIRMED CATCH, live**: `FAILURE` on open PRs **#1370** and **#1366** at scan time. Referenced in 553 sessions | **KEEP** | Currently the only automated red anyone sees; note it RUNS but does not BLOCK — branch protection is declared off |
| G3 | `tekton/devrc-nodetests` | `SUCCESS` on all sampled open PRs; `no evidence found` of a red — search: `gh pr view <n> --json statusCheckRollup` over 6 open PRs. Referenced in 512 sessions | **KEEP** | See C2 |

---

## 10. Proposed selection rules for every `TIER` verdict

🔴 **Proposed only. None of this is implemented, and implementing it is explicitly out of this
task's scope.** Tests that silently stop running on some paths is the failure mode being guarded
against, so read §11 before landing any of it.

The rule shape: run a target when the diff touches its own subtree **or** any of the shared
surfaces that can reach it.

**Always-run set (never tiered), because a diff cannot be shown not to reach them:**

```
scripts/tests                                     # repo-wide content/claim gates (A1)
scripts/claude-hooks/tests/test_guard_core.py     # the destructive-command predicate library (A19)
```

**Always-run TRIGGERS — any diff touching these runs the FULL suite, tiering disabled:**

```
flake.nix  flake.lock  nix/**                     # changes the build for every target
scripts/lib/**                                    # shared library imported across subsystems
scripts/run-tests.sh  scripts/run-node-tests.sh  scripts/gate.sh   # the runners themselves
conftest.py  **/conftest.py  pytest.ini  pyproject.toml
```

**Per-target rules:**

| rows | target(s) | run when the diff touches |
|---|---|---|
| A2–A7 | `scripts/collector/**` targets | its own subtree, **or** `scripts/collector/` shared files |
| A8, B2 | dl-router | `scripts/dl-router/**` |
| A9, B1 | browser-bridge | `scripts/browser-bridge/**` |
| A10 | validation | `scripts/validation/**` |
| A11–A12 | session-analysis | `scripts/session-analysis/**` |
| A13 | mail-actions | `scripts/mail-actions/**` |
| A14 | signal | `scripts/signal/**` |
| A15 | initiatives | `scripts/initiatives/**` |
| A16 | repo-cos | `scripts/repo-cos/**` |
| A17 | task-spec-drafter | `scripts/task-spec-drafter/**` |
| A18 | check-clickup-addressed | `scripts/check-clickup-addressed/**` |
| A20–A26, A28–A29 | per-hook suites | `scripts/claude-hooks/<that-hook>.py`, **or** any `scripts/claude-hooks/*.py` (they share `guard_core`) |
| A30 | opencode | `scripts/opencode/**` |
| B3 | collector browser-ext | `scripts/collector/browser-ext/**` |
| B4 | clickup skill | `claude/skills/clickup/**` |
| B5 | discord-embed-ext | `scripts/discord-embed-ext/**` |

**Estimated saving, stated as arithmetic on floors and not as a measurement:** the always-run
set is 14,053 of the 21,202 pytest floor-sum (66%). Tiering therefore cannot save more than
~34% of the pytest leg on a single-subsystem diff, and saves **nothing** on a diff touching
`nix/**`, a runner, or `scripts/lib/**`. 🔴 **Floors are not wall-clock.** The task's own ~19-min
figure is not apportioned per target anywhere, so the real saving is unmeasured. **Measure
per-target wall-clock before deciding this is worth building** — a 34% test-count reduction on
one-third of diffs may not repay the risk in §11.

---

## 11. 🔴 Two risks to weigh before implementing any of §10

1. **A path rule is a guard, and a guard can be wrong silently.** A target that stops running
   because a rule missed a path looks exactly like a target that passed. Any implementation
   needs the positive control this document's scanners used: a case that MUST select the target,
   asserted in a test, so "0 targets selected" is distinguishable from "selection is broken".
   Row A31 is what this failure already looks like in the repo today, arrived at by omission.
2. **Tiering interacts badly with the derived global floor.** `run-tests.sh` derives the global
   floor as the sum of the selected targets' floors. If selection becomes conditional, the floor
   must be recomputed per run from the selected set — otherwise a tiered run either fails
   spuriously against the full-suite floor, or the floor is lowered to accommodate it, which is
   the exact move `run-node-tests.sh:186` calls out: *"NEVER lower one to get green."*

---

## 12. Summary

| verdict | count | which |
|---|---:|---|
| **KEEP** | **49** | A1, A19, A27, A31 · C1–C3 · D1–D10 · E1–E16 · F1–F13 · G1–G3 |
| **TIER** | **32** | A2–A18, A20–A26, A28–A30 · B1–B5 |
| **DROP** | **0** | — |

(49 + 32 = 81, matching §1's row count. The A family splits 4 KEEP / 27 TIER.)

**No row earned a DROP, and that is the finding, not a failure to decide.** Of 81 rows:

- **67 carry a named catch** — a session id, a CI run, or a commit sha.
  Every mutation battery (10/10), 11 of 14 `bash-guard` arms, 11 of 13 drift-check arms,
  28 of 31 pytest targets red at least once, 4 of 5 node suites, and 8 of 8 hooks that are
  actually guards.
- **14 read `no evidence found`**, each with the search recorded:
  A27, A29, A31, B5, C2, E1, E4, E5, E10, E13, E15, F4, F10, G3. Every one has a stated
  reason that is not idleness — six are modules that are **not gates** and emit nothing to
  find (E1, E4, E5, E10, E13, E15); two are drift-check arms structurally unable to fire yet
  (F4, F10); three are never-red test targets covering guards that *have* fired (A27, A29, A31);
  and three are rows where a sibling tier carries the evidence (B5, C2, G3).
- The three zero-firing `bash-guard` arms (`mkfs`, `dd`, `rm -r $HOME`) are sub-rows of E3, not
  top-level rows, so they are not in the 14 — but they are the sharpest instance of the same
  point; see §7.

**The three things worth acting on are not drops:**

1. 🔴 **Row A31** — `scripts/devhost-tests` is in `TARGET_FLOORS` and not in `HERMETIC_TARGETS`,
   so it runs in ~1% of the sessions its neighbours do while its floor counts toward the global
   one. Decide it explicitly, either way.
2. 🔴 **§6** — no mutation battery is wired into any gate. Defensible, but currently undocumented;
   say so in `scripts/tests/README` or in each battery's header, so the next reader does not
   assume they run.
3. 🔴 **§10's saving is unmeasured.** Measure per-target wall-clock before building the selection
   layer. 66% of the pytest floor-sum is in the always-run set, which caps the benefit well below
   what "run less" intuitively suggests.

**Follow-on, satisfying criterion 6:** clawgate task **#528** — add `emit_invocation` to
`gate.sh`, `run-tests.sh`, `run-node-tests.sh`, `drift-check.sh` and the five guard hooks that
can block, recording the **outcome** (pass / fail / could-not-vouch / block) and, for
`bash-guard`, **which arm** fired — not merely the invocation. An event that records only "the
gate ran" would reproduce exactly the gap this document had to work around, so #528 makes the
outcome field a criterion and requires the emitter to be fail-open and demonstrated so.

---

## Appendix — reproducing this

The four scanners are not committed; they are ~60 lines each and are fully specified by §2
(corpus root, needle, control pair, and the co-requirement for instrument 1). Re-derive the
enumeration counts with the commands in §1 — the verifier for criterion 2 is that they still
agree with the numbers stated here at the PR's base sha, `112a5225`. They will drift as `main`
moves; that is expected and is why the sha is recorded.
