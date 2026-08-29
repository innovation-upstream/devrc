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
- Several numbers → audit each, one subagent per PR so they don't collide. 🔴 `isolation:
  "worktree"` worktrees the **cwd's** repo, not the PR's — for a PR in another repo run the
  recipe the brief's WHERE TO WORK section PRINTS, never a remembered one. It is a namespaced
  `refs/pull/<n>/head` fetch then a **detached** `worktree add`: naming the PR's head branch
  instead fails `rc 128` in any clone that has not fetched it, and always for a fork PR.

## What to do

🔴 **Assemble the brief with `~/workspace/devrc/scripts/audit-dispatch.py <pr> [--round N]`** — it
generates the range, the cross-repo worktree directive, checkout state and toolchain, reads the
prior round's claims from the fenced `audit-claims` block ONLY, and carries the invariant clauses
verbatim. **A delta round with no parseable block is REFUSED.**

🔴 **Post the round's block with `--round N --emit-claims --audited <the tip that round's audit
READ>`.** `--emit-claims` runs after the fixes land, so every sha it can see is a FIX tip; omit
`--audited` and HEAD is *assumed* — it says so on stderr, because the next round then diffs a range
that is empty by construction and a finding-free pass over it reads as a clean round.

Dispatch a subagent (read-only — it must NOT modify files or merge) to audit the change against this checklist. Have it read the diff and the code it touches, not just the PR description.

**Always run this on high-yield change-classes** — web/HTTP endpoints, concurrency reworks, filesystem/quarantine/trash moves, DB migrations, anything security/auth/path-gating. What each hid, and `GOPRIVATE`: reference file.

**Brief the auditor on the environment, or it will report false findings** — a fresh worktree is
not a working checkout, and an auditor hitting this cold blames the PR. Whichever apply:
**submodules are unpopulated** in a new worktree (one made 4 test files "fail to collect");
**monorepo `node_modules`** may need linking per package, not just at the root; **whether the base
branch is already red** and *at which file*; and that **zsh does not word-split unquoted
parameters**, so `eslint $FILES` checks **zero** files and prints a confident PASS. Have it mutate
only in a `cp -a` copy — **`rm -f <copy>/.git` first**, since a worktree's is a FILE pointing at the
real git dir, so a commit in the copy lands on your branch — and verify your worktree clean
yourself at the end.

🔴 **Tell it to reap its LOAD GENERATORS by resolved PID, and sweep for them yourself afterwards —
an auditor's own "cleaned up" claim is not evidence.** Measured twice in ONE session, from two
different rounds: a timing/stress probe spawned `while :; do :; done` shells whose cleanup
(`kill %1 %2 …` in one, `kill $LOADPIDS` in the other) reaped nothing, so they reparented to init
and ran on — **74 orphans saturating ~11 cores for 45 minutes**, then **20 more at ~87% CPU each
for 6h17m**. Both rounds reported cleanly. 🔴 The cost is not the CPU: the first batch was still
running during the NEXT round, which measured its timings under that load and reported the
degraded numbers as a finding — **a leak from round N silently corrupts round N+1's evidence**.
So brief it to record each PID it spawns and kill those exact PIDs, and at session end sweep
yourself: `ps -eo pid,ppid,comm` for `ppid==1` shells, confirm each via `/proc/<pid>/cmdline`,
kill by **resolved PID**. Never let a pattern reach `pkill -f` — it matches your own shell.

🔴 **And give it a UNIQUE name/port for any container or scratch dir it creates.** Subagents share
one scratchpad path and the branch namespace, so two audit rounds that both pick `cgpg` or port
55432 collide silently and one reports a green computed against the other's database.

**Audit for:**
1. **Risks** — what breaks in production.
2. **Regressions** — behaviour this silently alters or removes.
3. **Assumptions** — unstated preconditions that may not hold.
4. **Gaps** — error handling, edge cases, tests, migrations, rollback.
5. **Bugs** — logic/correctness defects, with file:line.
6. **Issues** — quality, maintainability, conventions.
7. **Behaviour changes** — observable changes in output/API/UX, intended or not. If the PR claims to revert behaviour, confirm it restores the pre-change state.
8. **Leaks** — secrets, PII, resource/handle/memory, over-broad permissions.
9. **Second-order consequences** — ripple effects on services, callers, data, cost.

## After the fixes: RE-AUDIT THE DELTA (don't assume closure)

**A fix round frequently introduces the next finding** — one feature took **five rounds**, each
caused by the previous fix, none caught by the mechanical gate. Then dispatch a **delta re-audit**
against the **previously-audited tip**, not the whole PR again.

Ask the re-auditor to:
- state **per prior finding**: actually fixed / partially / not / **made worse**;
- hunt for **regressions the fix round itself introduced** — the guard that's too strict, the branch
  that's unreachable, the narrowed check that now rejects a legitimate case, **the rule reworded
  wider on one axis and narrower on another**;
- **label every finding `behaviour` or `guard`, and separate shipped behaviour from scaffolding.**
  Tests an earlier round wrote are in its diff *by construction*; report them only where the defect
  lets a real regression through;
- treat "the author says it's fixed" as a claim to check against the diff.

**Carry the ledger in every round's summary**: `round N · payload lines changed THIS round: X (since
round 1: Y) · elapsed: Z`. X is what the gate below reads; without it the flattening shows only in
hindsight — on #498 the plateau was diagnosed six rounds late.

### 🔴 A clean round ENDS the ladder. Never run another round to confirm a clean round.

Rounds continue **only** while the previous round produced a finding that required a fix. The first
round that returns no findings is the last one — stop there, and do not re-confirm it. Stop on that,
not on the author saying it's done.

🔴 **A "safe to merge" VERDICT is not the stop signal — the FINDINGS are.** #804's rounds **5, 6 and
7 each returned "safe to merge" and each still reported real defects** that were then fixed — the
last a latch that read as pinned and was vacuous both ways. A verdict-keyed ladder stops at round 5
and ships it.

⚠ **#804 is NOT an example of a wasted round, and neither is any other PR cited here.** Every one of
its eight rounds produced findings that needed fixing. A forward rule with a demonstrated
near-miss — not a fix for measured waste.

🔴 **This is NOT a round cap, and a cap was rejected.** The count is set by
FINDINGS, never by a number, and #505 is why: its round 2 opens *"Round 1 fixed six findings and
introduced two of its own"*, and its round 4 caught a **ReDoS that round 3's own fix introduced** —
three 40-char shas did not return in 30 s, hanging `/handoff` with no output — plus a terminator
requirement that round 3 had added and that silently dropped ten marker shapes, *"the failure this
detector exists to prevent, reintroduced by the fix for the previous one"*. A cap at 2 or 3 ships
both. Keep going while rounds keep finding things — `claude/RULES.md` still says to budget for
several — and stop the moment one does not.

**When a round's fix is mostly renumbering your own prose, fix the FORM, not the number** — number
the list and tell the reader to count it; a total kept beside what it counts will drift.

**Say the stop rule to the re-auditor** ("a clean round is the stop condition; do not invent
findings") — otherwise late rounds manufacture nits.

🔴 **A FRAMED AUDIT VERIFIES THE FRAME. When a PR has already been audited, dispatch the next one
BLIND** — the diff and the checklist, *not* your conclusions or the prior findings' answers. Three framed
audits **confirmed** a claim; one blind audit refuted it in a pass. A delta re-audit must name the
prior findings — frame it as *what was claimed fixed*, never *why it is correct*.

### 🔴 ATTRIBUTION: a round that changes no PAYLOAD is auditing the LADDER, not the PR

A fix round writes new guards and the next delta round diffs them, so **the ladder manufactures its
own next round's findings** and the stop rule above, keyed to findings, cannot fire. Measured on
`civitai/cli` #498: **ten rounds, 5 h 32 m, 77% of the session's output; rounds 4–10 changed 1,051
test lines and ZERO payload lines.** No round was clean.

Gate on what each round CHANGES, not what it finds. After a round's fixes land, count the payload
lines **that round** changed:

```
git log --numstat --format= --remerge-diff <the sha you audited THAT round>..HEAD --not <base>
```

🔴 **The unit is THIS PR's PAYLOAD, never a file extension.** Payload = what the PR exists to ship;
scaffolding = the tests, fixtures and notes a round wrote to guard it. For a code change the payload
is source and a `.md` is not — but **for a docs or skill PR the payload IS the `.md`**, and **most of this repo's
merged PRs ship no source file at all** (measured; the reference file dates it), so a rule keyed to
file type reads every round of those as zero and stops a ladder that is working. Nor will a pathspec do it — measured wrong in both
directions on ordinary names (reference file). A round's fix touches a handful of files — read the list and name each one
payload or scaffolding. **Ambiguous is not zero**: the gate does not fire, and the ladder continues.

🔴 **Per-round, and every commit the round actually made.** Anchored at round 1 the count stays
non-zero forever once an early round touched payload — on #498 that prints the same number for
rounds 4 through 10 and never fires. Every flag earns its place, measured across four ladder shapes
(table in the reference file): `--not <base>` excludes the bring-in a `merge main` drags along;
`--remerge-diff` makes payload hand-written into a **merge-conflict resolution** visible. Do
**not** reach for `--no-merges --first-parent`: it looks equivalent and reads **0** for a fix
committed on a side branch and merged `--no-ff` — the shape agent worktrees produce.

🔴 **`<base>` is the CURRENT tip you would merge into — `git fetch` it first.** A local
`origin/main` is only as current as your last fetch, and a stale one re-reports upstream work as
this round's payload: the whole bring-in from the fork point (201 where the truth was 1), its tail
from one commit behind. Re-anchor on the new sha after a mid-round rebase. And **a zero you did not
watch the command EARN is not a zero — require rc 0, silent stderr, and a non-empty range.** A
missing ref or a git without `--remerge-diff` exits 128 with empty output; an unwritable object
store is worse, because `--remerge-diff` then under-counts, **exits 0 and prints a plausible
number**, saying so only on stderr; and a range whose commits are simply not in this checkout yet
prints nothing, silently, with rc 0. Keep stderr on the terminal — folding it into the sum with
`2>&1` makes the one loud failure invisible.

**Two consecutive rounds whose fixes changed zero payload lines ⇒ the ladder has left the PR.
Stop.** File the remaining scaffolding findings as one follow-up task naming the file, closed when
its PR merges or a named reader dismisses it in writing. A round that touches payload never trips
this.

⚠ **This does not retract the two rules above, and is not a cap in disguise.** #498's rounds were
not wasted in the sense those rules deny — every one found something real. The waste is on a
different axis: real findings *about scaffolding the ladder itself had just written*. The gate
measures the fixes; it never counts the rounds.

## Mutation testing: deletion-mutants are the EASY half

When a PR claims a guard is "mutation-verified", check **what kind**. Deletion is the obvious
mutant and the weakest: four variants that delete NOTHING once passed a suite its author had just
"mutation-verified" (all four in the reference file). The rule that decides most cases: **when you can
only assert on TEXT, pin the WHOLE normalised statement** — a partial regex is satisfied by inverted
code, and a pin that stops mid-sentence leaves the tail free to argue the opposite. Fixture and
re-run rules: reference file.

**Price a defect from the CONSUMING code: verifying that a value is USED is not verifying what its
ABSENCE costs.** Read the consuming code before repeating any costed consequence an audit asserts,
and sanity-check frequency — "routine" and "rare" are asserted far more often than measured.

**A finding about the PR *description* gets corrected PUBLICLY.** If the audit shows the PR body
misstates what the change does, post a **PR comment** saying so rather than silently editing the
body — a reviewer may already have read (and believed) the wrong version.

## Output

Findings by severity (🔴 deploy-blocking / 🟡 should-fix / 🟢 nit), each with file:line, a
`behaviour`/`guard` label and one line on why it matters. Then the ledger, then a **verdict**: safe
to merge / merge after fixing 🔴 / needs rework — advisory for the human, never the ladder's stop
signal. Flag uncertainty. Do not merge — report only.
