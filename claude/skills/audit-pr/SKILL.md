---
name: audit-pr
description: "Dispatch a subagent to adversarially audit a PR (or the current diff) for risks, regressions, assumptions, gaps, bugs, issues, behaviour changes, leaks, and second-order consequences. Use before merging."
argument-hint: "[PR number | 'current' | empty] — defaults to the current branch's diff vs base"
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# /audit-pr — adversarial PR audit

Every case history and number below is sourced in
`~/.claude/skills/audit-pr/reference/round-ladder-evidence.md`.

Target: `$ARGUMENTS`. Resolve it:
- A number → that GitHub PR (`gh pr diff <n>`, `gh pr view <n>`).
- `current` / empty → the current branch's diff vs its base/trunk.
- Multiple numbers → audit each; if several, dispatch one subagent per PR **with `isolation: "worktree"`** so they don't collide.

## What to do

Dispatch a subagent (read-only — it must NOT modify files or merge) to audit the change against this checklist. Have it read the diff and the code it touches, not just the PR description.

**Always run this on high-yield change-classes** — web/HTTP endpoints, concurrency reworks, filesystem/quarantine/trash moves, DB migrations, and anything security/auth/path-gating. These reliably hide real, deploy-blocking bugs (shutdown data-loss, trash-path overwrite, scanner scope-creep, an unauthenticated arbitrary-path scan and a git `core.fsmonitor` RCE all surfaced this way). If the branch has a **private Go module dep**, the auditor may need `GOPRIVATE` — a sum-db `500` there is env, not a code defect.

**Brief the auditor on the environment, or it will report false findings.** A fresh worktree is not
a working checkout, and an auditor that hits this cold reports it as a defect in the PR. Tell it, up
front, whichever apply: **git submodules are unpopulated** in a new worktree (one made 4 test files
"fail to collect" — pure environment); **monorepo `node_modules`** may need linking per package, not
just at the root; **whether the base branch is already red** on typecheck/lint/tests and *at which
file*, so a baseline error is not attributed to the PR; and that **zsh does not word-split unquoted
parameters**, so `eslint $FILES` silently checks **zero** files and prints a confident PASS. Also tell it to work in a `cp -a` copy if it wants to
mutate code, leave your worktree untouched, and verify it clean at the end.

**Audit for:**
1. **Risks** — what could break in production from this change.
2. **Regressions** — existing behaviour this silently alters or removes.
3. **Assumptions** — unstated preconditions the code relies on that may not hold.
4. **Gaps** — missing error handling, edge cases, tests, migrations, rollback.
5. **Bugs** — concrete logic/correctness defects, with file:line.
6. **Issues** — code quality, maintainability, convention violations.
7. **Behaviour changes** — observable changes in output/API/UX, intended or not. If the PR claims to revert prior behaviour, confirm it actually restores the pre-change state.
8. **Leaks** — secrets, PII, resource/handle/memory leaks, over-broad permissions.
9. **Second-order consequences** — ripple effects on other services, callers, data, cost, load.

## After the fixes: RE-AUDIT THE DELTA (don't assume closure)

**A fix round frequently introduces the next finding.** One `civitai-manager` feature took **five
rounds**, each caused by the previous fix, and **none of them caught by the mechanical gate**.

So once an audit's findings are fixed, dispatch a **delta re-audit** — diff the fix commits against
the **previously-audited tip** (`git diff <audited-sha>..HEAD`), not the whole PR again. Especially
when the fix touched the same code path.

Ask the re-auditor to:
- state **per prior finding**: actually fixed / partially / not / **made worse**;
- hunt specifically for **regressions the fix round itself introduced** (the new guard that's too
  strict, the new branch that's unreachable, the narrowed type check that now rejects a legitimate
  case);
- **label every finding `behaviour` or `guard`, and separate shipped behaviour from scaffolding.**
  Tests an earlier round of this same ladder wrote are in its diff *by construction*; report on them
  only where the defect would let a real behaviour regression through;
- treat "the author says it's fixed" as a claim to verify against the diff.

**Carry the ledger in every round's summary**: `round N · payload lines changed THIS round: X (since
round 1: Y) · elapsed: Z`. X is what the gate below reads; without it the flattening is visible only
in hindsight — on #498 the session diagnosed its own plateau at round 9, six rounds late.

### 🔴 A clean round ENDS the ladder. Never run another round to confirm a clean round.

Rounds continue **only** while the previous round produced a finding that required a fix. The first
round that returns no findings is the last one — stop there, and do not re-confirm it. Stop on that,
not on the author saying it's done.

🔴 **A "safe to merge" VERDICT is not the stop signal — the FINDINGS are.** #804's rounds **5, 6 and
7 each returned "safe to merge" and each still reported real defects** that were then fixed — the
last a latch that read as pinned and was vacuous in both directions (all three in the reference
file). A ladder keyed to the verdict stops at round 5 and ships it.

⚠ **#804 is NOT an example of a wasted round, and neither is any other PR cited here.** Every one of
its eight rounds produced findings that needed fixing, round 8 included. This is a forward rule with
a demonstrated near-miss — do not cite it as a fix for measured waste.

🔴 **This is NOT a round cap, and a cap was rejected.** The count is set by
FINDINGS, never by a number, and #505 is why: its round 2 opens *"Round 1 fixed six findings and
introduced two of its own"*, and its round 4 caught a **ReDoS that round 3's own fix introduced** —
three 40-char shas did not return in 30 s, hanging `/handoff` with no output — plus a terminator
requirement that round 3 had added and that silently dropped ten marker shapes, *"the failure this
detector exists to prevent, reintroduced by the fix for the previous one"*. A cap at 2 or 3 ships
both. Keep going while rounds keep finding things — `claude/RULES.md` still says to budget for
several — and stop the moment one does not.

**When a round's fix is mostly renumbering your own prose, fix the FORM, not the number.** Number
the list and tell the reader to count it; a total maintained in parallel with the thing it counts
will drift.

**Say the stop rule to the re-auditor explicitly** ("a clean round is the stop condition; do not
invent findings to justify the round") — otherwise late rounds manufacture nits to look productive.

🔴 **A FRAMED AUDIT VERIFIES THE FRAME. When a PR has already been audited, dispatch the next one
BLIND** — give it the diff and the checklist, *not* your conclusions or the prior findings' answers.
Three successive framed audits **confirmed** a claim; one blind audit refuted it in a single pass.
For a delta re-audit you must name the prior findings (that is the point), so keep the framing to
*what was claimed fixed* — never *why it is correct*.

### 🔴 ATTRIBUTION: a round that changes no PRODUCTION code is auditing the LADDER, not the PR

A fix round writes new guards and the next delta round diffs them, so **the ladder manufactures its
own next round's findings** and the stop rule above, keyed to findings, can never fire. Measured on
`civitai/cli` #498 (2026-08-26): **ten rounds, 5 h 32 m, 77% of the session's output tokens; rounds
4–10 changed 1,051 lines of test code and ZERO lines of the thing the PR shipped.** No round was
ever clean.

So gate on what each round CHANGES, not on what it finds. After a round's fixes land, count the
payload lines **that round** changed:

```
git log --numstat --format= --no-merges --first-parent <the sha you audited THAT round>..HEAD
```

🔴 **The unit is THIS PR's PAYLOAD, never a file extension.** Payload = what the PR exists to ship;
scaffolding = the tests, fixtures and notes a round wrote to guard it. For a code change the payload
is source and a `.md` is not — but **for a docs or skill PR the payload IS the `.md`**, and 24 of
devrc's last 40 merged PRs ship no source at all, so a rule keyed to file type reads every round of
those as zero and stops a ladder that is working. Nor will a pathspec do it: `':!*test*'` swallows
`attestation/` and `latest/`, `':!*spec*'` swallows `inspector/`, and both keep `FooTest.java` and
`*.cy.ts` (measured). A round's fix touches a handful of files — read the list and name each one
payload or scaffolding. **Ambiguous is not zero**: the gate does not fire, and the ladder continues.

🔴 **Per-round and the round's OWN commits.** Anchored at round 1 the count stays non-zero forever
once an early round touched payload — on #498 that prints the same number for rounds 4 through 10
and never fires. A plain two-dot `git diff` breaks the other way: after a `merge main` or a rebase
it sweeps upstream lines in as payload, so a scaffolding-only round scores non-zero and the counter
resets. Hence `--first-parent --no-merges` above; if the branch was rebased mid-round, re-anchor on
the new sha rather than trusting the old one.

**Two consecutive rounds whose fixes changed zero payload lines ⇒ the ladder has left the PR.
Stop.** File the remaining scaffolding findings as one follow-up task naming the file, closed when
its PR merges or a named reader dismisses it in writing. A round that touches payload never trips
this, however deep the ladder is.

⚠ **This does not retract the two rules above, and is not a cap in disguise.** #498's rounds were
not wasted in the sense those rules deny — every one found something real. The waste is on a
different axis: real findings *about scaffolding the ladder itself had just written*. The gate
measures the fixes; it never counts the rounds.

## Mutation testing: deletion-mutants are the EASY half

When a PR claims a guard is "mutation-verified", check **what kind**. Deleting the guard is the
obvious mutant and the weakest: four variants that delete NOTHING — swapped operands, inverted
branches, the guard commented out, a stale value re-bound — once passed a suite its author had just
"mutation-verified". The two rules that decide most cases: **when you can only assert on TEXT, pin
the WHOLE normalised statement** (a partial regex is satisfied by inverted code, and `--` / `/* */`
make "the token is present" and "the clause is live" different facts — a cosmetic reformat then
fails the test, which is the trade); and **a fixture of empty or default values collapses distinct
implementations into identical output**, so give fixtures non-default sibling values. **A review fix
RESETS the gate** (`claude/RULES.md`): re-run the FULL battery after every round and reformat.

## Price a defect from the CONSUMING code, not the producing site

**Verifying that a value is USED is not verifying what its ABSENCE costs.** Read the code that
consumes it before repeating any consequence an audit asserts, especially a costed one (worked
example in the reference file). Sanity-check the frequency side too — "routine" and "rare" are
asserted far more often than measured.

**A finding about the PR *description* gets corrected PUBLICLY.** If the audit shows the PR body
misstates what the change does, post a **PR comment** saying so rather than silently editing the
body — a reviewer may already have read (and believed) the wrong version.

## Output

Findings grouped by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), each with file:line, a
`behaviour`/`guard` label, and a one-line "why it matters". Then the round ledger, then a clear
**verdict**: safe to merge / merge after fixing 🔴 / needs rework — advisory for the human, never
the ladder's stop signal. No marketing language; flag uncertainty. Do not merge — report only.
