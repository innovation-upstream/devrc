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

## Output

Findings grouped by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), each with file:line and a one-line "why it matters". End with a clear **verdict**: safe to merge / merge after fixing 🔴 / needs rework. No marketing language; flag uncertainty honestly. Do not merge — report only.
