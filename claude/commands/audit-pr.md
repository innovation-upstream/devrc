---
name: audit-pr
description: "Dispatch a subagent to adversarially audit a PR (or the current diff) for risks, regressions, assumptions, gaps, bugs, issues, behaviour changes, leaks, and second-order consequences. Use before merging."
argument-hint: "[PR number | 'current' | empty] — defaults to the current branch's diff vs base"
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# /audit-pr — adversarial PR audit

Target: `$ARGUMENTS`. Resolve it:
- A number → that GitHub PR (`gh pr diff <n>`, `gh pr view <n>`).
- `current` / empty → the current branch's diff vs its base/trunk.
- Multiple numbers → audit each; if several, dispatch one subagent per PR **with `isolation: "worktree"`** so they don't collide.

## What to do

Dispatch a subagent (read-only — it must NOT modify files or merge) to audit the change against this checklist. Have it actually read the diff and the surrounding code it touches, not just the PR description.

**Always run this on high-yield change-classes** — web/HTTP endpoints, concurrency reworks, filesystem/quarantine/trash moves, DB migrations, and anything security/auth/path-gating. These reliably hide real, deploy-blocking bugs (shutdown data-loss, trash-path overwrite, scanner match-path scope-creep, unauthenticated arbitrary-path scan, a git `core.fsmonitor` RCE all surfaced this way). If the branch has a **private Go module dep**, the auditor may need `GOPRIVATE` set (e.g. `GOPRIVATE=github.com/civitai/*`) to build/inspect it — a sum-db `500` there is env, not a code defect.

**Brief the auditor on the environment, or it will report false findings.** A fresh worktree is not a
working checkout, and an auditor that hits this cold reports it as a defect in the PR. Tell it, up
front, whichever apply: **git submodules are unpopulated** in a new worktree (one unpopulated
submodule made 4 test files "fail to collect" — pure environment); **monorepo/workspace
`node_modules`** may need linking per package, not just at the root; **whether the base branch is
already red** on typecheck/lint/tests, and *at which file*, so a baseline error is not attributed to
the PR; and that **zsh does not word-split unquoted parameters**, so `eslint $FILES` silently checks
**zero** files and prints a confident PASS. Also tell it to work in a `cp -a` copy if it wants to
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

**A fix round frequently introduces the next finding.** One `civitai-manager`
feature took **five rounds**: a dead button → fixing it exposed a silent
wrong-file install → fixing *that* introduced a type-check regression that refused
legitimate LoCon installs. Every one was caught pre-merge, and **none of them by
the mechanical gate**.

So once an audit's findings are fixed, dispatch a **delta re-audit** — diff the fix
commits against the **previously-audited tip** (`git diff <audited-sha>..HEAD`),
not the whole PR again. Especially when the fix touched the same code path.

Ask the re-auditor to:
- state **per prior finding**: actually fixed / partially / not / **made worse**;
- hunt specifically for **regressions the fix round itself introduced** (the new
  guard that's too strict, the new branch that's unreachable, the narrowed type
  check that now rejects a legitimate case);
- treat "the author says it's fixed" as a claim to verify against the diff.

Stop when a round produces no new findings — not when the author says it's done. **Say that to the
re-auditor explicitly** ("a clean round is the stop condition; do not invent findings to justify the
round") — otherwise late rounds manufacture nits to look productive.

🔴 **A FRAMED AUDIT VERIFIES THE FRAME. When a PR has already been audited, dispatch the next one
BLIND** — give it the diff and the checklist, *not* your conclusions or the prior findings' answers.
Three successive audits **confirmed** a claim purely because the prompt handed them the thing to
check; a single blind audit refuted it in one pass by finding a second reader nobody had mentioned.
For a delta re-audit you must name the prior findings (that is the point), so keep the framing to
*what was claimed fixed* — never *why it is correct*.

## Mutation testing: deletion-mutants are the EASY half

When a PR claims a guard is "mutation-verified", check **what kind** of mutation was run. Breaking a
guard by *deleting* it is the obvious test and the weakest one. Across one PR, four semantically
broken variants that delete nothing all passed a suite its author had just "mutation-verified":

- **swap the operands** of a merge/concat — inverts which side wins;
- **invert the branches** of a CASE/ternary — here it turned a merge into an unconditional WIPE,
  strictly worse than the bug being fixed;
- **comment the guard out** — the clause is dead but the TEXT is still present, so every regex
  looking for it still matches;
- **re-bind a stale value** — literally the original defect, reintroduced.

Generalisations worth applying as an auditor **and** as an author:

- **When you can only assert on TEXT** (raw SQL, a generated config, a serialised query), **pin the
  WHOLE normalised statement**, not features of it. A partial regex is satisfied by semantically
  inverted code, and `--` / `/* */` make "the token is present" and "the clause is live" different
  facts. Accept that a cosmetic reformat then fails the test — that is the trade.
- **A fixture of empty or default values collapses distinct implementations into identical output.**
  One mutant survived *only* because the fixture was `{}`, which made "bind just the patch" and
  "rebind the whole stale snapshot" byte-identical. Give fixtures non-default sibling values.
- **Enumerate mutants from the expression's semantic failure modes** — operand order, branch order,
  comment-out, wrong bind, off-by-one — not from "delete the thing I was already thinking about".
- **A review fix RESETS the gate.** Re-run the FULL mutant battery after each fix round *and* after
  any reformat, not just the mutant for the thing you changed.

## Price a defect from the CONSUMING code, not the producing site

Before repeating any consequence an audit asserts — especially one with a cost attached — read the
code that **consumes** the value, not just the code that writes it. An audit correctly established
that a watermark was written, that losing it reset the watermark, and that a query read it; it then
priced the loss as "re-judges the whole backlog at LLM cost". The same `WHERE` clause carried an
independent `NOT EXISTS` dedupe that excluded every already-processed row regardless of the
watermark, so the true cost was a wider index scan and zero LLM calls. The wrong figure propagated
into a PR body, a public comment and two code notes before anyone read the full clause.

**Verifying that a value is USED is not verifying what its ABSENCE costs.** Also sanity-check the
frequency side of any risk claim (how often can the two writers actually collide?) — "routine" and
"rare" get asserted far more often than they get measured.

**A finding about the PR *description* gets corrected PUBLICLY.** If the audit
shows the PR body misstates what the change does, post a **PR comment** saying so
rather than silently editing the body — a reviewer may already have read (and
believed) the wrong version.

## Output

Findings grouped by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), each with file:line and a one-line "why it matters". End with a clear **verdict**: safe to merge / merge after fixing 🔴 / needs rework. No marketing language; flag uncertainty honestly. Do not merge — report only.
