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

Stop when a round produces no new findings — not when the author says it's done.

**A finding about the PR *description* gets corrected PUBLICLY.** If the audit
shows the PR body misstates what the change does, post a **PR comment** saying so
rather than silently editing the body — a reviewer may already have read (and
believed) the wrong version.

## Output

Findings grouped by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), each with file:line and a one-line "why it matters". End with a clear **verdict**: safe to merge / merge after fixing 🔴 / needs rework. No marketing language; flag uncertainty honestly. Do not merge — report only.
