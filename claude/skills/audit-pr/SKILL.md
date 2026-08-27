---
name: audit-pr
description: "Dispatch a subagent to adversarially audit a PR (or the current diff) for risks, regressions, assumptions, gaps, bugs, issues, behaviour changes, leaks, and second-order consequences. Use before merging."
argument-hint: "[PR number | 'current' | empty] — defaults to the current branch's diff vs base"
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# /audit-pr — adversarial PR audit

Case histories and the measurements behind the ladder rules:
`~/.claude/skills/audit-pr/reference/round-ladder-evidence.md`.

Target: `$ARGUMENTS`:
- A number → that GitHub PR (`gh pr diff <n>`, `gh pr view <n>`).
- `current` / empty → the current branch's diff vs its base/trunk.
- Several numbers → audit each, one subagent per PR **with `isolation: "worktree"`** so they don't collide.

## What to do

Dispatch a subagent (read-only — it must NOT modify files or merge) to audit the change against this checklist. Have it read the diff and the code it touches, not just the PR description.

**Always run this on high-yield change-classes** — web/HTTP endpoints, concurrency reworks, filesystem/quarantine/trash moves, DB migrations, anything security/auth/path-gating. These hide deploy-blocking bugs (shutdown data-loss, trash-path overwrite, an unauthenticated arbitrary-path scan and a git `core.fsmonitor` RCE all surfaced this way). A branch with a **private Go module dep** may need `GOPRIVATE` — a sum-db `500` there is env, not a defect.

**Brief the auditor on the environment, or it will report false findings.** A fresh worktree is not
a working checkout, and an auditor hitting this cold reports it as a defect in the PR. Tell it,
whichever apply: **submodules are unpopulated** in a new worktree (one made 4 test files "fail to
collect" — pure environment); **monorepo `node_modules`** may need linking per package, not just at
the root; **whether the base branch is already red** and *at which file*, so a baseline error is not
blamed on the PR; and that **zsh does not word-split unquoted parameters**, so `eslint $FILES`
checks **zero** files and prints a confident PASS. Have it mutate only in a `cp -a` copy and verify
your worktree clean at the end.

**Audit for:**
1. **Risks** — what could break in production.
2. **Regressions** — existing behaviour this silently alters or removes.
3. **Assumptions** — unstated preconditions the code relies on that may not hold.
4. **Gaps** — error handling, edge cases, tests, migrations, rollback.
5. **Bugs** — logic/correctness defects, with file:line.
6. **Issues** — code quality, maintainability, conventions.
7. **Behaviour changes** — observable changes in output/API/UX, intended or not. If the PR claims to revert prior behaviour, confirm it restores the pre-change state.
8. **Leaks** — secrets, PII, resource/handle/memory leaks, over-broad permissions.
9. **Second-order consequences** — ripple effects on other services, callers, data, cost.

## After the fixes: RE-AUDIT THE DELTA (don't assume closure)

**A fix round frequently introduces the next finding** — one feature took **five rounds**, each
caused by the previous fix, none caught by the mechanical gate. So once the findings are fixed,
dispatch a **delta re-audit** against the **previously-audited tip**, not the whole PR again.

Ask the re-auditor to:
- state **per prior finding**: actually fixed / partially / not / **made worse**;
- hunt specifically for **regressions the fix round itself introduced** (the new guard that's too
  strict, the new branch that's unreachable, the narrowed type check that now rejects a legitimate
  case);
- **label every finding `behaviour` or `guard`, and separate shipped behaviour from scaffolding.**
  Tests an earlier round wrote are in its diff *by construction*; report them only where the defect
  lets a real regression through;
- treat "the author says it's fixed" as a claim to check against the diff.

**Carry the ledger in every round's summary**: `round N · payload lines changed THIS round: X (since
round 1: Y) · elapsed: Z`. X is what the gate below reads; without it the flattening shows only in
hindsight — on #498 the session diagnosed its own plateau at round 9, six rounds late.

### 🔴 A clean round ENDS the ladder. Never run another round to confirm a clean round.

Rounds continue **only** while the previous round produced a finding that required a fix. The first
round that returns no findings is the last one — stop there, and do not re-confirm it. Stop on that,
not on the author saying it's done.

🔴 **A "safe to merge" VERDICT is not the stop signal — the FINDINGS are.** #804's rounds **5, 6 and
7 each returned "safe to merge" and each still reported real defects** that were then fixed — the
last a latch that read as pinned and was vacuous in both directions. A ladder keyed to the verdict
stops at round 5 and ships it.

⚠ **#804 is NOT an example of a wasted round, and neither is any other PR cited here.** Every one of
its eight rounds produced findings that needed fixing. This is a forward rule with a demonstrated
near-miss — do not cite it as a fix for measured waste.

🔴 **This is NOT a round cap, and a cap was rejected.** The count is set by
FINDINGS, never by a number, and #505 is why: its round 2 opens *"Round 1 fixed six findings and
introduced two of its own"*, and its round 4 caught a **ReDoS that round 3's own fix introduced** —
three 40-char shas did not return in 30 s, hanging `/handoff` with no output — plus a terminator
requirement that round 3 had added and that silently dropped ten marker shapes, *"the failure this
detector exists to prevent, reintroduced by the fix for the previous one"*. A cap at 2 or 3 ships
both. Keep going while rounds keep finding things — `claude/RULES.md` still says to budget for
several — and stop the moment one does not.

**When a round's fix is mostly renumbering your own prose, fix the FORM, not the number** — number
the list and tell the reader to count it; a total kept beside what it counts drifts.

**Say the stop rule to the re-auditor explicitly** ("a clean round is the stop condition; do not
invent findings") — otherwise late rounds manufacture nits.

🔴 **A FRAMED AUDIT VERIFIES THE FRAME. When a PR has already been audited, dispatch the next one
BLIND** — the diff and the checklist, *not* your conclusions or the prior findings' answers. Three
successive framed audits **confirmed** a claim; one blind audit refuted it in a pass. A delta
re-audit must name the prior findings, so frame it as *what was claimed fixed* — never *why it is
correct*.

### 🔴 ATTRIBUTION: a round that changes no PAYLOAD is auditing the LADDER, not the PR

A fix round writes new guards and the next delta round diffs them, so **the ladder manufactures its
own next round's findings** and the stop rule above, keyed to findings, cannot fire. Measured on
`civitai/cli` #498: **ten rounds, 5 h 32 m, 77% of the session's output; rounds 4–10 changed 1,051
test lines and ZERO payload lines.** No round was ever clean.

So gate on what each round CHANGES, not on what it finds. After a round's fixes land, count the
payload lines **that round** changed:

```
git log --numstat --format= --remerge-diff <the sha you audited THAT round>..HEAD --not <base>
```

🔴 **The unit is THIS PR's PAYLOAD, never a file extension.** Payload = what the PR exists to ship;
scaffolding = the tests, fixtures and notes a round wrote to guard it. For a code change the payload
is source and a `.md` is not — but **for a docs or skill PR the payload IS the `.md`**, and **most of this repo's
merged PRs ship no source file at all** (measured; the reference file dates it), so a rule keyed to
file type reads every round of those as zero and stops a ladder that is working. Nor will a pathspec do it: `':!*test*'` swallows
`attestation/` and `latest/`, `':!*spec*'` swallows `inspector/`, and both keep `FooTest.java` and
`*.cy.ts` (measured). A round's fix touches a handful of files — read the list and name each one
payload or scaffolding. **Ambiguous is not zero**: the gate does not fire, and the ladder continues.

🔴 **Per-round, and every commit the round actually made.** Anchored at round 1 the count stays
non-zero forever once an early round touched payload — on #498 that prints the same number for
rounds 4 through 10 and never fires. Every flag above earns its place, measured across four ladder
shapes (table in the reference file): `--not <base>` excludes the bring-in a `merge main` drags
along; `--remerge-diff` makes payload hand-written into a **merge-conflict resolution** visible. Do
**not** reach for `--no-merges --first-parent`: it looks equivalent and reads **0** for a fix
committed on a side branch and merged `--no-ff` — the shape agent worktrees produce.

🔴 **`<base>` is the CURRENT tip you would merge into — `git fetch` it first.** A local
`origin/main` is exactly as current as your last fetch, and a stale one re-reports upstream work as
this round's payload: at the fork point the entire bring-in (201 where the truth was 1), one commit
behind, its tail. Re-anchor on the new sha after a mid-round rebase. And **a failed command is not
zero — require rc 0 AND silent stderr.** A missing ref or a git without `--remerge-diff` exits 128
with empty output; an unwritable object store is worse, because `--remerge-diff` then under-counts,
**exits 0 and prints a plausible number**, saying so only on stderr.

**Two consecutive rounds whose fixes changed zero payload lines ⇒ the ladder has left the PR.
Stop.** File the remaining scaffolding findings as one follow-up task naming the file, closed when
its PR merges or a named reader dismisses it in writing. A round that touches payload never trips
this, however deep.

⚠ **This does not retract the two rules above, and is not a cap in disguise.** #498's rounds were
not wasted in the sense those rules deny — every one found something real. The waste is on a
different axis: real findings *about scaffolding the ladder itself had just written*. The gate
measures the fixes; it never counts the rounds.

## Mutation testing: deletion-mutants are the EASY half

When a PR claims a guard is "mutation-verified", check **what kind**. Deletion is the obvious
mutant and the weakest: four variants that delete NOTHING — swapped operands, inverted branches, the
guard commented out, a stale value re-bound — once passed a suite its author had just
"mutation-verified" (all four in the reference file). The two rules that decide most cases: **when
you can only assert on TEXT, pin the WHOLE normalised statement** — a partial regex is satisfied by
inverted code, and `--` / `/* */` make "the token is present" and "the clause is live" different
facts; and **a fixture of empty or default values collapses distinct implementations into identical
output**, so give fixtures non-default sibling values. **A review fix RESETS the gate**
(`claude/RULES.md`): re-run the FULL battery after every round and reformat.

**Price a defect from the CONSUMING code, not the producing site: verifying that a value is USED is
not verifying what its ABSENCE costs.** Read the consuming code before repeating any costed
consequence an audit asserts. Sanity-check frequency too — "routine" and "rare" are asserted far
more often than they are measured.

**A finding about the PR *description* gets corrected PUBLICLY.** If the audit shows the PR body
misstates what the change does, post a **PR comment** saying so rather than silently editing the
body — a reviewer may already have read (and believed) the wrong version.

## Output

Findings by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), each with file:line, a
`behaviour`/`guard` label and one line on why it matters. Then the round ledger, then a **verdict**:
safe to merge / merge after fixing 🔴 / needs rework — advisory for the human, never the ladder's
stop signal. Flag uncertainty. Do not merge — report only.
