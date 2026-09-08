# devrc gate / tier / battery inventory — what has actually caught something

**Scan date:** 2026-09-08 · **Base sha:** `01956bf0` · **Host:** workbench · clawgate task #525

Every gate, tier, battery and hook devrc runs, with **named evidence of a real catch** or the
explicit string `no evidence found`, and a `KEEP` / `DROP` / `TIER` verdict per row.

> 🔴 **Nothing here is a change.** The verdict column is a recommendation to the operator. No
> gate, target, battery or hook was dropped, disabled or made conditional by the PR that added
> this document, and no per-path selection rule was implemented. A dropped gate fails silently
> and is invisible until the day it would have fired — that step stays with a human.

> 🔴 **This document was re-anchored from `112a5225` to `01956bf0` after an adversarial audit.**
> The audit refuted two of round 1's headline claims and found the base had moved under it. What
> changed, and what that says about the method, is in §13 — read it before trusting any figure
> here more than the ones it replaced.

---

## 1. How the enumeration was derived

Enumerated from the repo at scan time, not from the task description. The commands, so the
counts can be re-derived and disagreed with:

| family | command | count |
|---|---|---|
| pytest targets | `awk 'f&&/^\)$/{exit} f&&!/^[[:space:]]*#/&&NF' scripts/run-tests.sh` over `TARGET_FLOORS=(` | **29** |
| node suites | same shape over `SUITES=(` in `scripts/run-node-tests.sh` | **5** |
| nix check derivations | `grep -E '^        [a-zA-Z][a-zA-Z0-9_-]*[ ]*=' flake.nix` inside `checks.${system}` | **2** |
| the dev-host gate | `scripts/gate.sh` — no enumeration command; it is one script, counted as one row (C3) | **1** |
| mutation batteries | `ls scripts/tests/mutants-*` | **12** |
| Claude hooks | `ls scripts/claude-hooks/*.py` | **16** |
| drift-check arms | rc legend in `scripts/drift-check.sh` header (drift/actionable codes only) | **13** |
| pre-push gate | `githooks/` — `ls githooks/` lists **6 files**; they are one gate, counted as one row (G1) | **1** |
| Tekton checks | `gh pr view <n> --json statusCheckRollup` | **2** |

**29 + 5 + 2 + 1 + 12 + 16 + 13 + 1 + 2 = 81 rows.**
<!-- inventory-rows: 81 -->
Derived figures: pytest floor-sum **20,348**; node floor-sum **1,367**.

⚠ That HTML comment is **load-bearing, not decoration**: `scripts/check-gate-inventory.py`
reads it and refuses to vouch for the table unless the number of rows it matches equals it.
Change the tables and you must change it in the same commit — a stale marker fails the check
rather than passing quietly.

Two counts differ from the task's own list, and the difference is the finding:

- The task said "~30 pytest targets". There are **29** in `TARGET_FLOORS` and **28** in
  `HERMETIC_TARGETS`; the one in the first and not the second is `scripts/devhost-tests`, and
  that difference is **deliberate and documented** — see row A29.
- "16 Claude hooks" counts `*.py` files. Two of the sixteen are **not hooks**: `guard_core.py`
  is the shared predicate library `bash-guard` imports, and `register-nudge-hook.py` is the
  per-host registrar that writes the others into `settings.json`. Rows are kept for both,
  labelled, because the glob is what the criterion named.

🔴 **A floor is not a test count.** `TARGET_FLOORS` entries are *minimums*, set below the real
collected count. Where this document prints a floor it says "floor"; it does not convert one
into a claim about how many tests exist.

---

## 2. Method, and what each instrument can and cannot see

Four scanners over the session-transcript corpus (**5,892** `.jsonl` files under
`~/.claude/projects/**`), plus `git log`, plus `gh`. **Two sessions are excluded from every
count**: this document's authoring session, and the session that **audited** the PR adding it.
The audit ran the gate with `--set all`, so its verdict lines are in the corpus; citing them
would make the document evidence for itself. Excluding it returns rows A25, A27, A29 and B5 to
zero, which is what they were before the audit ran.

🔴 **Every scanner carries a negative and a positive control, and the controls are reported.**
A table of zeros from a scanner wired to nothing is indistinguishable from a table of zeros
from gates that never fired, so the zero alone is not reportable.

| # | instrument | what it keys on | negative control | positive control |
|---|---|---|---|---|
| 1 | denial scanner | guard message **AND** `toolDenialKind` on the same transcript line | fabricated arm name → **0** ✅ | 12 arms non-zero ✅ |
| 2 | Stop/nudge scanner | each hook's own emitted prefix, **no** `toolDenialKind` requirement | fabricated arm name → **0** ✅ | blind-stage arm → 77 sessions ✅ |
| 3 | target scanner | `run-tests.sh`'s own verdict lines (exact form below) | non-existent target → **0/0** ✅ | 29 real targets all PASS>0 ✅ |
| 4 | drift scanner | `drift-check: <verdict> (rc=N)` — the script's own machine-emitted line | `rc=99`, a code it cannot emit → **0** ✅ | clean-path sentence → 34 sessions ✅ |

### 🔴 The exact needles — quote these, not a paraphrase

Round 1 described instrument 3 as keying on `FAIL <target> (` with **one** space. The runners
emit **two** — `run-tests.sh:3813,3829,3832` and `run-node-tests.sh:480,483`:

```
FAIL  <target>  (          PASS  <target>  (
```

Round 1's *regex* was `FAIL\s+<target>\s*\(`, so its counts were right; its *documentation* was
not, and a reader following it literally would have got **0 for nearly every row** and read that
as confirmation. Measured: the one-space literal finds 33 hits for `scripts/tests` where the
real form finds 1,031. **That is the positive-control failure this document preaches about,
committed in its own appendix.** The needles above are the ones that reproduce the tables.

### 🔴 Instrument 3 cannot separate the two tiers

`run-tests.sh` and `run-node-tests.sh` emit an **identical** `PASS  <dir>  (` shape, and three
directories are both a pytest target and a node suite: `scripts/browser-bridge/tests`,
`scripts/dl-router/tests`, `scripts/collector/browser-ext/tests`. For those, a hit **cannot be
attributed to a tier**. Rows A9, A8 and A6 and rows B1, B2 and B3 therefore report the **same**
underlying measurement and are marked `AMBIGUOUS`; they are not independent evidence, and no
argument in this document may lean on separating them.

**What instrument 1 buys over instrument 2.** On the same string, instrument 2 (text only) finds
77 sessions and instrument 1 (text + `toolDenialKind`) finds 75 at round 1's corpus size. The
gap is sessions that *discussed* the guard without being blocked by it. The co-requirement is
doing real work, which is why hook rows cite the denial-confirmed number where one exists.

### 🔴 Honest limits of this method

- **A red is not a save.** A `FAIL` line proves a target went red in a real session. It does
  **not** prove it stopped a defect reaching `main` — the red could be a test being written, or
  a broken test. Rows report this as **RED-IN-SESSION**, which is weaker than a confirmed save
  and stronger than a run-count. A run-count cannot go red at all.
- 🔴 **A text needle that also appears in the repo's own documentation counts READERS, not
  firings** — and a fabricated-name negative control cannot detect that, by construction. This
  bit row C3 in round 1: see it for the measurement and the correction.
- **Absence of evidence is not a DROP argument.** A guard can be correct and never yet fire.
- **The corpus is not the whole history.** A gate that fired before the corpus starts, or on a
  host whose transcripts were pruned, is invisible here.
- **`git log` evidence is WEAK** and is labelled `(weak)` per the task's own assumption.
- **This document quotes no transcript content.** The repo is public; sessions are cited by id.

---

## 3. Pytest targets — `scripts/run-tests.sh` `TARGET_FLOORS` (29 rows)

`F-sess` = distinct sessions where the gate printed `FAIL  <target>  (`; `P-sess` = same for
`PASS`. Evidence cells name **one example session id** alongside the total.

| # | target | floor | F-sess | P-sess | evidence (example session id ×total) | verdict | reason |
|---|---|---:|---:|---:|---|---|---|
| A1 | `scripts/tests` | 12793 | 225 | 413 | `bfb2ae02-9576-4cb4-bdc3-5bc01b9504bc` ×225 | **KEEP** | Holds the repo-wide content/claim gates (captured-text, rules-size, ci-claim, skill-tiers); a diff cannot be shown not to reach it |
| A2 | `scripts/collector/tests` | 260 | 2 | 323 | `beb749f5-fa85-4221-a2b0-210778db964d` ×2 | **TIER** | Subsystem-local; two reds in the whole corpus |
| A3 | `scripts/collector/keylog/tests` | 79 | 31 | 323 | `1f045584-5d0e-4836-8652-28a51c19037b` ×31 | **TIER** | Earns its keep, but only on collector changes |
| A4 | `scripts/collector/claude/tests` | 219 | 4 | 338 | `dced6c6b-c435-4906-876d-de3f6ccb9ca6` ×4 | **TIER** | Subsystem-local |
| A5 | `scripts/collector/i3/tests` | 12 | 3 | 342 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Subsystem-local |
| A6 | `scripts/collector/browser-ext/tests` | 12 | 3 | 593 | `agent-abd8fca1ce83eef68` ×3 — 🔴 **AMBIGUOUS**, shared with B3 (§2) | **TIER** | Subsystem-local |
| A7 | `scripts/collector/opencode/tests` | 224 | 5 | 361 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A8 | `scripts/dl-router/tests` | 942 | 29 | 592 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×29 — 🔴 **AMBIGUOUS**, shared with B2 | **TIER** | Strong catch record, entirely within one subsystem |
| A9 | `scripts/browser-bridge/tests` | 867 | 75 | 587 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×75 — 🔴 **AMBIGUOUS**, shared with B1 | **TIER** | Third-highest catch count; still subsystem-local |
| A10 | `scripts/validation/tests` | 97 | 3 | 368 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Subsystem-local |
| A11 | `scripts/session-analysis/tests` | 525 | 5 | 373 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A12 | `scripts/session-analysis/session_insight/tests` | 55 | 2 | 381 | `agent-abd8fca1ce83eef68` ×2 | **TIER** | Subsystem-local |
| A13 | `scripts/mail-actions/tests` | 116 | 26 | 386 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×26 | **TIER** | Subsystem-local |
| A14 | `scripts/signal/tests` | 920 | 48 | 293 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×48 | **TIER** | Second-highest catch count; subsystem-local |
| A15 | `scripts/task-spec-drafter/tests` | 135 | 5 | 401 | `agent-abd8fca1ce83eef68` ×5 | **TIER** | Subsystem-local |
| A16 | `scripts/check-clickup-addressed/tests` | 232 | 2 | 215 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×2 | **TIER** | Subsystem-local |
| A17 | `.../test_guard_core.py` | 1260 | 5 | 417 | `agent-abd8fca1ce83eef68` ×5 | **KEEP** | Covers the predicate library behind `bash-guard`, the one hook that denies destructive commands |
| A18 | `.../test_next_step_nudge.py` | 124 | 3 | 371 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Hook-local |
| A19 | `.../test_registrar_activation.py` | 16 | 4 | 354 | `agent-abd8fca1ce83eef68` ×4 | **TIER** | Hook-local |
| A20 | `.../test_agent_ledger_hook.py` | 38 | 4 | 351 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×4 | **TIER** | Hook-local |
| A21 | `.../test_clawgate_writeback_guard.py` | 282 | 2 | 353 | `agent-abd8fca1ce83eef68` ×2 | **TIER** | Hook-local |
| A22 | `.../test_clawgate_task_interview_guard.py` | 285 | 35 | 280 | `e582aa63-1d3d-443d-acda-d332d73055c9` ×35 | **TIER** | Hook-local, strong catch record |
| A23 | `.../test_gh_issue_closing_condition_guard.py` | 451 | 11 | 212 | `f23b37ec-68c0-4ad5-8ecd-659f21a88ae9` ×11 | **TIER** | Hook-local |
| A24 | `.../test_on_disk_artifact_names.py` | 15 | 3 | 346 | `agent-abd8fca1ce83eef68` ×3 | **TIER** | Hook-local |
| A25 | `.../test_handoff_write_guard.py` | 66 | **0** | 94 | `no evidence found` — instrument 3, `FAIL  scripts/claude-hooks/tests/test_handoff_write_guard.py  (`, 0 hits | **KEEP** | Never red, but it covers a Stop guard confirmed to have blocked in **139** sessions (row E11) |
| A26 | `.../test_bg_command_capture.py` | 76 | 1 | 283 | `62565689-1242-4257-99f2-813294656554` ×1 | **TIER** | Hook-local |
| A27 | `.../test_git_add_provenance_nudge.py` | 21 | **0** | 88 | `no evidence found` — instrument 3, `FAIL  .../test_git_add_provenance_nudge.py  (`, 0 hits | **TIER** | Never red; the nudge it covers has fired in 81 sessions, so keep it on hook changes |
| A28 | `scripts/opencode/tests` | 86 | 7 | 401 | `agent-abd8fca1ce83eef68` ×7 | **TIER** | Subsystem-local |
| A29 | `scripts/devhost-tests` | 6 | **0** | **4** | `no evidence found` — instrument 3, `FAIL  scripts/devhost-tests  (`, 0 hits; 4 PASS sessions against 200–590 for its neighbours | **KEEP** | 🔴 Deliberately dev-host-only — see below. Not a finding |

### 🔴 A29 — round 1 called this a finding, and it was WRONG. Recorded so nobody re-derives it.

Round 1 reported `scripts/devhost-tests` as a target that had "silently stopped running,
arrived at by omission rather than by anyone choosing it", and offered "add it to
`HERMETIC_TARGETS`" as a remedy. **All three claims are false**, and the audit refuted each
against the source:

| round 1 claim | measured |
|---|---|
| "arrived by omission" | `run-tests.sh:876-898` — a dedicated `DEVHOST_TARGETS` array under a 🔴 comment explaining the choice: it needs a real `nvim`, added 2026-08-29 |
| "silently stopped running" | `scripts/tests/test_nvim_clipboard_osc52.py` parses that array and **asserts the registration exists** — "drop the line and that test fails, rather than the behavioural suite quietly running in no tier at all" |
| "its floor of 6 is counted into the derived global floor regardless" | **False.** `run-tests.sh:900` sets `TARGETS=("${HERMETIC_TARGETS[@]}")` and the global floor sums over `$TARGETS`. Round 1's own gate output said `floor: 21281 = sum of **30** per-target floors` against 31 targets — the evidence was in hand and misread |

🔴 **And the proposed remedy would have made things worse.** These tests need `nvim`, which the
nix sandbox does not carry; moving them into `HERMETIC_TARGETS` reds the sandbox tier, which is
exactly why they were moved out (`ERROR: 3 UNPINNED skip group(s)`). **No action. The design is
correct as it stands.**

---

## 4. Node suites — `scripts/run-node-tests.sh` `SUITES` (5 rows)

| # | suite | files\|floor | F-sess | P-sess | evidence (example session id ×total) | verdict | reason |
|---|---|---|---:|---:|---|---|---|
| B1 | `scripts/browser-bridge/tests` | 18\|540 | 75 | 587 | `6c8a7662-7de7-4001-94ab-32a6ec272094` ×75 — 🔴 **AMBIGUOUS**, same measurement as A9 | **TIER** | Subsystem-local |
| B2 | `scripts/dl-router/tests` | 13\|500 | 29 | 592 | `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55` ×29 — 🔴 **AMBIGUOUS**, same measurement as A8 | **TIER** | Subsystem-local |
| B3 | `scripts/collector/browser-ext/tests` | 2\|20 | 3 | 593 | `agent-abd8fca1ce83eef68` ×3 — 🔴 **AMBIGUOUS**, same measurement as A6 | **TIER** | Subsystem-local |
| B4 | `claude/skills/clickup/test` | 6\|179 | 4 | 409 | `71983f6d-4a60-4f51-b6c0-2a9dda7e0abb` ×4 | **TIER** | Skill-local; not shared with any pytest target, so this row is unambiguous |
| B5 | `scripts/discord-embed-ext/tests` | 2\|128 | **0** | 281 | `no evidence found` — instrument 3, `FAIL  scripts/discord-embed-ext/tests  (`, 0 hits | **TIER** | Never red, but runs; extension-local so tiering costs nothing |

---

## 5. Tiers and runners (3 rows)

| # | thing | evidence | verdict | reason |
|---|---|---|---|---|
| C1 | `nix build .#checks.x86_64-linux.pytests` (Tekton `devrc-pytests`) | **CONFIRMED CATCH**: PR **#1370** was `FAILURE` on `tekton/devrc-pytests` at scan time. Named in `CLAUDE.md`: PR **#773** passed `gate.sh` four consecutive times and then went red on this tier — a defect only this tier could see | **KEEP** | The only tier that builds from a `.git`-less store copy; documented to see defects the dev-host tier structurally cannot |
| C2 | `nix build .#checks.x86_64-linux.nodetests` (Tekton `devrc-nodetests`) | `no evidence found` of a red — search: `gh pr view <n> --json statusCheckRollup` over the 6 most recent open PRs, all `SUCCESS`. 🔴 The per-suite reds in rows B1–B4 are **not** evidence for this row: §2 establishes the instrument cannot attribute those to a tier | **KEEP** | Half the sandbox-tier claim; dropping it leaves the node layer with no store-copy tier |
| C3 | `scripts/gate.sh` (dev-host tier, rc 90 = could-not-vouch) | **CONFIRMED CATCH**: `agent-a1319026fa57a77c6`; the literal `GATE: RESULT=UNVOUCHED` — which is what `gate.sh:250` actually emits — appears in **18** sessions. `GATE: RESULT=FAIL` in 288 | **KEEP** | The rc-90 arm catches the instrument lying, a different class from a failing test |

### 🔴 C3 — round 1's figures counted READERS, not firings

Round 1 cited **241** sessions for `could-not-vouch` and **787** for `panic: test timed out`.
Both are near-worthless: `gate.sh` emits **neither string**. `gate.sh:250` emits
`GATE: RESULT=UNVOUCHED exit=90`; the phrase `could-not-vouch` exists only in prose — including
`CLAUDE.md`, which carries both needles in one sentence. Re-measured:

| needle | sessions | what it counts |
|---|---:|---|
| `GATE: RESULT=UNVOUCHED` (the real literal) | **18** | firings |
| `could-not-vouch` | 239 | overwhelmingly, sessions that read `CLAUDE.md` |
| `panic: test timed out` | 790 | same |

The `KEEP` verdict survives — 18 firings is real evidence — but the quoted figure overstated by
~13×. 🔴 **A fabricated-name negative control cannot catch this**, because the needle *does*
exist in the corpus; it is just not the gate writing it. Stated as a limit in §2.

---

## 6. Mutation batteries — `scripts/tests/mutants-*` (12 rows)

🔴 **Structural finding: none of these is wired into any gate.**
`grep -rn "mutants-" scripts/gate.sh scripts/run-tests.sh scripts/run-node-tests.sh flake.nix githooks/`
returns **nothing**. They are manual, one-shot verification instruments run when their subject
changes — defensible, but it means "battery" and "gate" are different things, and no battery is
on the critical path of any change.

Evidence per row is the introducing commit's own account of what the battery found — stronger
than `git log` alone (it names the survivors) but still self-reported by the authoring session.

| # | battery | added | evidence | verdict | reason |
|---|---|---|---|---|---|
| D1 | `mutants-audit-dispatch.py` | `1b2117b6` 08-28 | 16 mutants, each naming its exact killer set, all killed; R1 is the reachability control (PR #958) | **KEEP** | Committed negative-control battery for the auditor brief |
| D2 | `mutants-audit-ladder.sh` | `88ede2c0` 08-27 | **CONFIRMED CATCH**: PR #933 — one of PR #900's published numbers was the wrong metric, found by checking in instruments #900 had only run in a scratchpad | **KEEP** | Caught a wrong published figure |
| D3 | `mutants-base-clone.sh` | `10ad288a` 08-24 | **CONFIRMED CATCH**: PR #809 — a path deleted upstream was reported FAILED forever and never removed; coverage shown red on pre-change code | **KEEP** | Real defect, with a watched-fail matrix |
| D4 | `mutants-claim-work.sh` | `2770db2d` 08-26 | **CONFIRMED CATCH**: PR #881 — the claim namespace was per-origin so the lock was inert cross-repo, and a claim that landed reported UNCLAIMED. **9 surviving mutants**, each now with a test that goes red | **KEEP** | 9 survivors = 9 real coverage gaps |
| D5 | `mutants-dead-guard-exclude.sh` | `a5aa037b` 08-26 | **CONFIRMED CATCH**: PR #888 — a registry glob swept up a non-pytest file and published 44 flags off a run that traced nothing | **KEEP** | Caught a 44-flag false-positive publication |
| D6 | `mutants-dead-guard.sh` | `ad8259cf` 08-25 | PR #842 — states the ~180 guards the scan cannot see, in the artifact; identifies dead recognition code | **KEEP** | Its value is the stated blind spot as much as the finding |
| D7 | `mutants-handoff-cap.sh` | `5348a9a1` 08-30 | PR #962 — mutation-verified the handoff write cap; honest limits stated in the module and skill | **KEEP** | Guards a cap that is otherwise prose |
| D8 | `mutants-install-sh.sh` | `648f08c2` 08-27 | **CONFIRMED CATCH ×2**: PR #905/#908 — `install.sh` could not install at all on a home-manager host, **and** fixing it exposed a worse harness bug the audit had not seen | **KEEP** | Caught both a real defect and a defect in the instrument |
| D9 | `mutants-shared-clone-sync.sh` | `180ac5c2` 08-26 | **CONFIRMED CATCH**: PR #869 — 20 mutants, positive control 49 passed/0 failed; **two mutants survived the first pass** | **KEEP** | Survivors are the evidence |
| D10 | `mutants-worktree-prune.sh` | `4c1d6da9` 08-27 | **CONFIRMED CATCH**: PR #895 — a round-2 safety flag that blocked itself; **three test gaps** the sweep exposed | **KEEP** | Caught a self-blocking safety flag |
| D11 | `mutants-diagnose-disk-accounting.sh` | `ffac18f8` 09-08 | **CONFIRMED CATCH**: PR #1366 — a gate for root-only bash nothing had ever run; **three live defects found while writing it**, all fixed. 19 mutants, all killed by their own guard | **KEEP** | Landed while this inventory was open; found real defects in code no tier covered |
| D12 | `mutants-nebula-relay.sh` | `01956bf0` 09-08 | **CONFIRMED CATCH**: PR #1272 — the audit found **three blockers in the apply script**, all downstream of rc 0; two parser mutants and two insert mutants | **KEEP** | Same: landed mid-inventory, caught blockers before a sudo apply shipped |

**All 12 have a named catch.** The recommendation in §12 is a *wiring/documentation* one, not a drop.

⚠ **D11 and D12 did not exist when this inventory started.** They landed on `main` between
round 1 and round 2 — which is the churn rate this document is measured against, and the reason
§13 ends on re-counting rather than on trusting these figures.

---

## 7. Claude hooks — `scripts/claude-hooks/*.py` (16 rows)

`sessions` = distinct sessions in which the hook's own emitted text appears. For `bash-guard`
arms the figure is **denial-confirmed** (instrument 1). For Stop guards and PostToolUse nudges
it is instrument 2, because a Stop block does not produce `toolDenialKind` — instrument
blindness, stated per row, not a weaker gate.

| # | hook | event | sessions | evidence | verdict | reason |
|---|---|---|---:|---|---|---|
| E1 | `agent-ledger-hook.py` | PostToolUse, SessionStart, Stop, UserPromptSubmit | n/a | `no evidence found` — search: instrument 2 on its `write: … measured: … records: …` prefix, 0 hits. **Not a gate**: writes a ledger, blocks nothing | **KEEP** | Telemetry, not verification |
| E2 | `audit-pr-nudge.py` | PostToolUse | **508** | `b91e2065-9da3-42f1-9522-b41d3cb067ac` ×508 | **KEEP** | Advisory; highest-reach nudge |
| E3 | `bash-guard.py` | PreToolUse | **3105** (widest arm) | **CONFIRMED CATCH, 12 of 14 arms** — denial-confirmed; e.g. `agent-a2a677167f6124c33`, with a per-arm id in the table below. Also blocked one of the authoring session's own searches mid-inventory | **KEEP** | The only hook that denies destructive commands; strongest evidence in the repo |
| E4 | `bg-command-capture.py` | PreToolUse, PostToolUse | n/a | `no evidence found` — the module emits no distinctive user-visible string, so this is an instrument limit, not a measured zero. **Not a gate** | **KEEP** | Telemetry |
| E5 | `claude-notify.py` | Stop, SubagentStop, UserPromptSubmit | n/a | `no evidence found` — instrument 2 on `coalesce gate error`, 0 hits. **Not a gate** | **KEEP** | Notification |
| E6 | `clawgate-task-interview-guard.py` | PreToolUse | **67** | **CONFIRMED CATCH**: `8d97d643-d204-45f6-8239-2d6d24a2533c`, 93 denial-confirmed blocks | **KEEP** | Enforces the `## Acceptance criteria` contract |
| E7 | `clawgate-writeback-guard.py` | PostToolUse, Stop | **91** | **CONFIRMED CATCH**: `8d97d643-d204-45f6-8239-2d6d24a2533c`, 443 blocks. Its message cites tasks **#193**/**#194**, dispatched and paid for twice before it existed | **KEEP** | Named, costed defect that recurred until this shipped |
| E8 | `gh-issue-closing-condition-guard.py` | PreToolUse | **24** | **CONFIRMED CATCH**: `eb0f4fe7-5c3e-4d25-9b74-3ce9c46027e5`, 32 denial-confirmed blocks | **KEEP** | Stops un-closable work items |
| E9 | `git-add-provenance-nudge.py` | PostToolUse | **81** | `eb0f4fe7-5c3e-4d25-9b74-3ce9c46027e5` ×81 | **KEEP** | Complements E3: E3 blocks blind-staging, this catches staging someone else's file by path |
| E10 | `guard_core.py` | — (library) | via E3 | `no evidence found` for `guard_core` as an independent actor — **not a hook**. Its catches are E3's 12 confirmed arms, which import from it; its tests are row A17 | **KEEP** | One-rule-one-place |
| E11 | `handoff-write-guard.py` | PostToolUse, Stop | **139** | **CONFIRMED CATCH**: `53d533ef-b73c-4f58-8798-b2964d538913`, 473 blocks. 1 session hit the cannot-measure NOTICE path — correct behaviour, not a block | **KEEP** | Its message cites 22 of 253 `/resume` sessions (8.7%) that never recorded their work |
| E12 | `next-step-nudge.py` | Stop | **482** | `b91e2065-9da3-42f1-9522-b41d3cb067ac` ×482 | **KEEP** | Advisory; wide reach |
| E13 | `register-nudge-hook.py` | — (registrar) | n/a | `no evidence found` — **not a hook**; it emits no block or nudge. Writes the others into the per-host `settings.json`. Its tests are row A19 | **KEEP** | Without it the hooks are symlinked but unregistered on a new host |
| E14 | `search-tool-nudge.py` | PostToolUse | **294** | `e36f9cdb-d845-46ac-8471-26fa2c129337` ×294 — and it fired during this document's own authoring | **KEEP** | Caught a real instance during the work that produced this document |
| E15 | `session-stamp.py` | PreToolUse | n/a | `no evidence found` — emits no distinctive string; instrument limit, not a measured zero. **Not a gate** | **KEEP** | Infrastructure other hooks key on |
| E16 | `shell-env-nudge.py` | PostToolUse | **540** | `a8f13c58-3ccc-485d-adc7-f8a06f606075` ×540 — widest-reach hook measured | **KEEP** | Memory `agent-shell-env-handles` records this moving adoption 0 → 50% |

### E3 arm breakdown — the 14 arms `bash-guard` ACTUALLY runs

🔴 **This set is `guard_core._CLAUDE_CODE_CHECKS`, read from the source — not guessed.**
`bash-guard.py:60` is `POLICY = "claude-code"`, and `guard_core.py:2271` lists exactly 14
functions for that policy. Round 1's table was wrong in both directions: see below.

| arm | blocks | sessions | example session id | verdict |
|---|---:|---:|---|---|
| `cd <path> && git …` → `git -C` | 4993 | **3105** | `agent-a2a677167f6124c33` | **KEEP** — by far the most-fired guard in the repo |
| large heredoc → use Write | 1139 | **1094** | `b91e2065-9da3-42f1-9522-b41d3cb067ac` | **KEEP** |
| **`git commit` on `main`/`master`/`trunk`** | **435** | **270** | `5119f6c9-3585-4de3-831d-12cf5ea6e99b` | **KEEP** — third most-fired arm; enforces this repo's own 🔴 never-commit-to-main rule |
| `pkill -f <pattern>` | 130 | **129** | `646b087e-fbf1-4eac-9520-975bdd98f625` | **KEEP** — the sibling-agent-kill class |
| blind-stage (`git add -A`/`--all`/`.`) | 83 | **77** | `62ad62c9-2c4d-4f5d-9c00-fb12514bc406` | **KEEP** |
| `git reset --hard` (2 arms: parsed + argv) | 26 | **26** | `87a5b52a-e757-45e1-826d-9afd81515f50` | **KEEP** |
| public IP / secret + repo publish | 23 | **18** | `eb0f4fe7-5c3e-4d25-9b74-3ce9c46027e5` | **KEEP** — public-repo leak class |
| `git clean -f` | 10 | **10** | `f80cbf52-2d4e-4fda-a1e5-4665c56cee7a` | **KEEP** |
| `git stash` | 9 | **9** | `6c8a7662-7de7-4001-94ab-32a6ec272094` | **KEEP** — the repo-global stash hazard |
| private key in command | 6 | **6** | `a5069618-8629-4e0e-8af7-f9ecb5606670` | **KEEP** — secret-leak class |
| `talosctl reset` | 1 | **1** | `c7555365-25c5-4932-a845-728cf9626667` | **KEEP** — one block; it prevents wiping a cluster node |
| `mkfs` | **0** | **0** | — | **KEEP** — `no evidence found`; search: instrument 1, `is blocked — it FORMATS a filesystem` |
| `dd` to block device | **0** | **0** | — | **KEEP** — `no evidence found`; search: instrument 1, `writing to a block device overwrites` |

**12 of 14 arms have fired.** The zero-firing set is **two**.

### 🔴 Round 1's arm table swapped a live guard for one `bash-guard` does not run

| | round 1 | measured |
|---|---|---|
| `check_git_commit_to_main` | **absent from the table entirely** | in `_CLAUDE_CODE_CHECKS`; **435 blocks / 270 sessions** — the third most-fired arm |
| `rm -r $HOME` (`check_rm_rf_critical`) | listed as a `bash-guard` arm with 0 firings | **not in `_CLAUDE_CODE_CHECKS`.** It is in `_IRREVERSIBLE_CHECKS`, an **opencode-only** policy, under a 20-line comment arguing the exclusion and ending "Do not 'finish the job'." It has fired **3 blocks / 1 session** — from opencode's guard, not `bash-guard` |

🔴 **Why this mattered, not just that it was wrong.** Round 1's keep-despite-no-evidence argument
read: *"their cost is a regex on a string already being matched for eleven other arms."* For
`rm -r $HOME` under `claude-code` that is false — it is not matched **at all**, so its zero was a
**policy** fact, not a rarity fact. That is precisely the structural-vs-idle distinction this
document draws correctly for F4 and F10 and got wrong here. An operator persuaded by the
expected-value argument might have moved `check_rm_rf_critical` into `_CLAUDE_CODE_CHECKS`,
reversing a deliberate and argued decision.

### The two zero-firing arms are still the clearest KEEP-without-evidence case

For `mkfs` and `dd`-to-block-device — both genuinely in `_CLAUDE_CODE_CHECKS` — the run cost is a
regex on a string already being matched for twelve other arms, and the loss they prevent is total
and unrecoverable. **Frequency is the wrong axis for a guard whose downside is unbounded.** The
`talosctl reset` arm at exactly one firing is the same argument with one data point.

---

## 8. `scripts/drift-check.sh` arms (13 rows)

Evidence via instrument 4 — the script's **own** machine-emitted verdict line, so an observed
`(rc=N)` is a direct record of arm N firing, not prose about it.

⚠ Instrument 4's first positive control **failed**: `rc=0` returned 0 hits. Cause read out of the
source rather than assumed — `drift-check.sh:3554` guards the `(rc=$rc)` line behind
`if [ "$rc" != 0 ]`, so a clean run is **structurally incapable** of emitting that format; it
prints `drift-check: no drift on the host(s) CHECKED` instead. Re-run against that sentence:
**34 sessions**. Control passes.

| # | rc | arm | sessions | evidence | verdict | reason |
|---|---:|---|---:|---|---|---|
| F1 | 8 | `main` DIVERGED / AHEAD (un-pushed commits) | **6** | **CONFIRMED CATCH ×6**, e.g. `agent-abad65297ffbc6474`. `CLAUDE.md`: 2026-08-06 (two un-pushed workbench commits blocked `ship.sh` for hours) and a 2026-08-09 recurrence rescued as **#366** | **KEEP** | A host in this state silently stops receiving every future change |
| F2 | 10 | `main` BEHIND origin/main | **12** | **CONFIRMED CATCH ×12**; `962b6c35-ece3-4a9a-a673-b2a0d3968f46` | **KEEP** | Cheap, high-frequency, directly actionable |
| F3 | 12 | checkout not on branch `main` | **7** | **CONFIRMED CATCH ×7**; `67a8939c-6ce5-44db-95c1-709064834f50` | **KEEP** | Catches a checkout that will silently stop converging |
| F4 | 13 | remote unreachable N consecutive runs | **0** | `no evidence found` — search: instrument 4, `(rc=13)`, 0 hits | **KEEP** | An escalation ladder; zero firings means hosts have stayed reachable — the arm working, not idling |
| F5 | 14 | managed symlinks resolving to nothing | **1** | **CONFIRMED CATCH**: `agent-a6069f54fbee9b955`. `CLAUDE.md`: 2026-08-11, **46 of 139** managed links on the laptop dangled into a GC'd store path while the checkout was byte-identical to `origin/main` | **KEEP** | The failure git is structurally blind to |
| F6 | 15 | host parity — `settings.json` keys / `enabledPlugins` | **6** | **CONFIRMED CATCH ×6**; `971208ab-013b-4b01-8e27-6f86fd832f3e` | **KEEP** | `settings.json` is per-host and unmanaged; nothing else compares it |
| F7 | 16 | fuzzyclaw phase-2 gate opened (ACTIONABLE, not drift) | **2** | **CONFIRMED ×2**; `agent-acca1ea01eb070f0f` | **KEEP** | Least-severe code; reports a safe deletion, not drift |
| F8 | 17 | built-source subtree not current | **16** | **CONFIRMED CATCH ×16 — most-fired drift arm**; `bd508253-0f12-49bd-9afb-95d6be09e733`. `CLAUDE.md`: 2026-08-14, the laptop's `homelab-talos` was 24 commits behind and shipped a `clawgatectl` with no `task status`; it printed help and **exited 0** while drift-check was green | **KEEP** | Git parity is not source parity; the only detector |
| F9 | 18 | built-source scope UNMEASURABLE N runs | **3** | **CONFIRMED CATCH ×3**: `agent-a5558f4e0ce724da4`. `CLAUDE.md`: 2026-08-18, `tmux-fuzzyclaw` on a local branch with no upstream, `unmeasured=1`, rc 0 — concealing a divergent build | **KEEP** | Closes the "we could not look, so we escalated never" gap rc 17 left |
| F10 | 22 | deployed `skillOverrides` vs `skill-tiers.json` | **0** | `no evidence found` — search: instrument 4, `(rc=22)`, 0 hits. **Expected**: adopted-then-drifted only, and the ledger is applied to **no** host by default | **KEEP** | Cannot fire until a host adopts; the zero is the documented design |
| F11 | 23 | untracked file in a nix-read path, N runs | **2** | **CONFIRMED CATCH ×2**: `agent-aeaab911fc0e2fbad`. `CLAUDE.md`: 2026-08-25, one such file sat on the workbench ~3 weeks with every check green | **KEEP** | Catches deployed-but-unbacked files |
| F12 | 24 | `main` has no required checks while declared on | **8** | **CONFIRMED CATCH ×8**; `a76d7e7a-aa4d-4c43-a7c9-2540b28345c5` | **KEEP** | Asks whether `origin/main` is still worth matching |
| F13 | 25 | the declaration itself is stale | **3** | **CONFIRMED CATCH ×3**: `agent-a7d557043e832ae21` — the detector for rc 24 being disarmed | **KEEP** | Guards the guard |

**11 of 13 arms have fired.** The two zero rows each have a structural reason in the source.

---

## 9. Pre-push and Tekton (3 rows)

| # | thing | evidence | verdict | reason |
|---|---|---|---|---|
| G1 | `githooks/` pre-push gate | **CONFIRMED CATCH ×3, all three about the gate itself**: `648f08c2` (#905/#908) — `install.sh` could not install **at all** on a home-manager host; `db896dae` (#858) — the push audit was grading pytest fixture repos, 4 runs and 167,977 output tokens, **none about real code**; `3ad7f66a` (#830) — git exported `GIT_DIR` from a linked worktree | **KEEP (with the standing warning)** | 🔴 Its installed state is **volatile per clone**; `core.hooksPath` is the only answer. `CLAUDE.md` records incident **#322**: a push from a worktree returned with the branch REWRITTEN |
| G2 | `tekton/devrc-pytests` | **CONFIRMED CATCH**: `FAILURE` on open PR **#1370** at scan time | **KEEP** | Currently the only automated red anyone sees; it RUNS but does not BLOCK — protection is declared off |
| G3 | `tekton/devrc-nodetests` | `no evidence found` of a red — search: `gh pr view <n> --json statusCheckRollup` over 6 open PRs, all `SUCCESS` | **KEEP** | See C2 |

---

## 10. Proposed selection rules for every `TIER` verdict

🔴 **Proposed only. None of this is implemented, and implementing it is explicitly out of this
task's scope.** Read §11 before landing any of it.

**Always-run set (never tiered), because a diff cannot be shown not to reach them:**

```
scripts/tests                                     # repo-wide content/claim gates (A1)
scripts/claude-hooks/tests/test_guard_core.py     # the destructive-command predicate library (A17)
```

**Always-run TRIGGERS — any diff touching these runs the FULL suite, tiering disabled:**

```
flake.nix  flake.lock  nix/**                     # changes the build for every target
scripts/lib/**                                    # shared library imported across subsystems
scripts/testlib/**                                # shared TEST library — see the note below
scripts/run-tests.sh  scripts/run-node-tests.sh  scripts/gate.sh   # the runners themselves
**/conftest.py
```

🔴 **`scripts/testlib/**` is on that list because leaving it off is the exact failure §11
warns about.** It is referenced by **122 tracked files** spanning at least 8 pytest targets
(browser-bridge, claude-hooks, collector/opencode, dl-router, opencode, session-analysis,
signal, scripts/tests). A rule omitting it would tier away every one of them on a `testlib`
change. Round 1's list omitted it — the proposal contained a live instance of its own risk.
⚠ Round 1's list also named `pytest.ini` and `pyproject.toml`; **neither file exists in this
repo**, so they are dropped rather than left as decoration.

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
| A15 | task-spec-drafter | `scripts/task-spec-drafter/**` |
| A16 | check-clickup-addressed | `scripts/check-clickup-addressed/**` |
| A18–A24, A26–A27 | per-hook suites | `scripts/claude-hooks/<that-hook>.py`, **or** any `scripts/claude-hooks/*.py` (they share `guard_core`) |
| A28 | opencode | `scripts/opencode/**` |
| B3 | collector browser-ext | `scripts/collector/browser-ext/**` |
| B4 | clickup skill | `claude/skills/clickup/**` |
| B5 | discord-embed-ext | `scripts/discord-embed-ext/**` |

**Estimated saving, stated as arithmetic on floors and not as a measurement:** the always-run
set is 14,053 of the 20,348 pytest floor-sum (**69.1%**). Tiering therefore cannot save more than
**~30.9%** of the pytest leg on a single-subsystem diff, and saves **nothing** on a diff touching
`nix/**`, a runner, `scripts/lib/**` or `scripts/testlib/**`. 🔴 **Floors are not wall-clock**,
and no per-target wall-clock figure exists anywhere. **Measure it before deciding this is worth
building** — a ~30% test-count reduction on a minority of diffs may not repay the risk in §11.

---

## 11. 🔴 Two risks to weigh before implementing any of §10

1. **A path rule is a guard, and a guard can be wrong silently.** A target that stops running
   because a rule missed a path looks exactly like a target that passed. Any implementation
   needs a positive control: a case that MUST select the target, asserted in a test, so
   "0 targets selected" is distinguishable from "selection is broken". The `scripts/testlib/**`
   omission in round 1's own proposal is what this looks like in practice.
2. **Tiering interacts badly with the derived global floor.** `run-tests.sh` derives the global
   floor as the sum of the selected targets' floors. If selection becomes conditional, the floor
   must be recomputed per run from the selected set — otherwise a tiered run either fails
   spuriously against the full-suite floor, or the floor is lowered to accommodate it, which is
   the exact move `run-node-tests.sh:186` calls out: *"NEVER lower one to get green."*
   ⚠ The existing `--set all` path already does this correctly (`TARGETS` then
   `MIN_TESTS_COMPUTED` sums over it), so the mechanism exists; the risk is a second selector
   that forgets to use it.

---

## 12. Summary

| verdict | count | which |
|---|---:|---|
| **KEEP** | **51** | A1, A17, A25, A29 · C1–C3 · D1–D12 · E1–E16 · F1–F13 · G1–G3 |
| **TIER** | **30** | A2–A16, A18–A24, A26–A28 · B1–B5 |
| **DROP** | **0** | — |

<!-- inventory-tally: KEEP=51 TIER=30 DROP=0 -->

(51 + 30 = 81, matching §1. The A family splits 4 KEEP / 25 TIER.)

⚠ That second marker is load-bearing too: `check-gate-inventory.py` counts the verdicts in the
tables and fails if they disagree with it. **Both this tally and the round-1 one were written
wrong by hand** — 47/32 here, 51/30 in round 1 — while the *lists* beside them were correct
both times. A hand-summed total next to the thing it counts is exactly the claim that drifts, so
it is now machine-checked rather than proofread.

**No row earned a DROP.** Of 81 rows, **67 carry a named catch** — a session id, a CI run or a
commit sha — and **14 read `no evidence found`**, each with the search recorded:
A25, A27, A29, B5, C2, E1, E4, E5, E10, E13, E15, F4, F10, G3.

None of those 14 is an idleness argument. Six are modules that are **not gates** and emit
nothing to find (E1, E4, E5, E10, E13, E15); two are drift arms structurally unable to fire yet
(F4, F10); three are never-red targets covering guards that *have* fired, or a deliberately
dev-host-only one (A25, A27, A29); three have their evidence in a sibling tier or lack a red
(B5, C2, G3).

🔴 **Guard-hook accounting, stated once so it cannot drift.** Of the 16 E rows: **6** are not
gates (E1, E4, E5, E10, E13, E15) and **10** are. Of those 10, **5 can BLOCK** — `bash-guard`,
`clawgate-task-interview-guard`, `gh-issue-closing-condition-guard`, `handoff-write-guard`,
`clawgate-writeback-guard` — and **5 are advisory nudges** (E2, E9, E12, E14, E16). **All 10
have a named catch.** (Round 1 said "8 of 8 hooks that are actually guards", which matched no
partition of the rows and contradicted its own next paragraph.)

**Two things worth acting on, both smaller than round 1 claimed:**

1. 🔴 **No mutation battery is wired into any gate** (§6). Defensible as manual one-shot
   instruments, but undocumented — say so in each battery's header so the next reader does not
   assume they run.
2. 🔴 **§10's saving is unmeasured, and 69.5% of the pytest floor-sum is unavoidably
   always-run** — capping the benefit near 30%, on a minority of diffs. Measure per-target
   wall-clock before building the selection layer.

⚠ **Round 1's headline finding — that `scripts/devhost-tests` had silently stopped running — is
RETRACTED.** It is deliberate, documented and test-pinned, and the fix round 1 proposed would
have redded the sandbox tier. See A29.

**Follow-on, satisfying criterion 6:** clawgate task **#528** — add `emit_invocation` to
`gate.sh`, `run-tests.sh`, `run-node-tests.sh`, `drift-check.sh` and the five guard hooks that
can block, recording the **outcome** (pass / fail / could-not-vouch / block) and, for
`bash-guard`, **which arm** fired. An event recording only "the gate ran" would reproduce
exactly the gap this document had to work around.

---

## 13. 🔴 What the audit changed, and what that says about the method

This document was rewritten after an adversarial audit of the PR that introduced it. The
corrections are recorded rather than quietly applied, because the pattern in them is the useful
part.

**Two claims were refuted outright**, both by reading a *registry* the document had described
without opening:

- The `bash-guard` arm table (§7) was assembled from the guard's deny *messages* rather than
  from `_CLAUDE_CODE_CHECKS`. It therefore missed the third most-fired arm in the repo and
  listed one the policy does not run.
- Row A29's finding was assembled from `TARGET_FLOORS` vs `HERMETIC_TARGETS` without reading the
  40 lines of comment directly above them, or the test that pins the registration.

**The shape is the same in both: a set was inferred from its members' behaviour instead of read
from the thing that defines it.** Every remaining error was a variant — a needle inferred from
prose rather than from the emitting `echo` (§2, C3), a tier separation assumed rather than
checked against the two runners' output format (§2).

**What held up.** The mechanical arithmetic verified exactly: all floors, both floor-sums, the
row/verdict tallies, 10/10 battery shas (at round 1's base), 11/13 drift arms, and every quoted line number. The
method's controls did their job where they were pointed. **What they could not do is tell the
method it was pointed at the wrong thing** — a fabricated-name negative control cannot detect a
needle that matches the repo's own documentation, and no control at all was placed on "is this
the real set?".

**The transferable rule:** when a row describes a member of an enumerated set, cite the
enumeration, not an instance of its behaviour.

---

## Appendix — reproducing this

The scanners are not committed; they are ~60 lines each and are specified by §2 — corpus root,
the **exact two-space needles**, the control pair, the `toolDenialKind` co-requirement for
instrument 1, and the two excluded sessions. Re-derive the enumeration counts with the commands
in §1; they should agree with the numbers here at base `01956bf0`. They will drift as `main`
moves — round 1 was anchored at `112a5225` and two subsystems (`initiatives`, `repo-cos`) were
retired out from under it within a day, taking `TARGET_FLOORS` from 31 to 29. That is why the
sha is recorded, and why a re-count is worth more than this document's own numbers.
