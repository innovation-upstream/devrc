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

🔴 **That refusal covers NO block at all — it does NOT cover a MISSING INTERMEDIATE one, which
proceeds and SILENTLY WIDENS the range.** The anchor is the newest block the script can PARSE, not
the previous round's, so when round N−1 posted nothing, round N anchors on round N−2's tip and the
"delta" silently spans two rounds' fixes. It is the mirror of the empty-range trap below — that one
under-covers, this one over-covers — and it is worse to spot, because a wider range reads as a
perfectly ordinary delta and the extra commits look like work the round was meant to see.
**It is announced on stderr, once, and nowhere in the brief**: `the newest claims block says
round=3, and you asked for round 5`. Read that line before you dispatch; if it fires, say in the
dispatch which rounds the range actually spans, and hold the un-blocked round's commit to the same
per-claim standard by reading its commit message for what it claimed. Measured 2026-09-02 on
`ZacxDev/civitai-block-generate-from-model#7`: round 4 posted no block, so round 5's range came
back `acce7a2..02c07f1f`, spanning both R3's and R4's fixes. 🔴 **One reason a block goes missing is
not forgetfulness: `gh pr view --json comments` does NOT return REVIEW comments**, so a block posted
as a review is invisible to the script even though a human can see it on the PR. The script says so
in the same stderr line — post the block as an ISSUE comment.

🔴 **Post the round's block with `--round N --emit-claims --audited <the tip that round's audit
READ> --payload <the payload lines this round's fixes changed>`.** `--emit-claims` runs after the
fixes land, so every sha it can see is a FIX tip; omit `--audited` and HEAD is *assumed* — it says
so on stderr, because the next round then diffs a range that is empty by construction and a
finding-free pass over it reads as a clean round. **`--payload` is what arms the attribution gate
below**: omit it and the block carries `payload=<count>`, which the next round reads as UNMEASURED
and cannot stop on — the field is the only machine-readable record of the number, and the ledger's
`X` in a summary is prose nothing parses.

🔴 **EMITTING IS NOT POSTING — the two halves fail independently, and a handoff that records the
`--emit-claims` command as the next step reads as if the step were done.** The flag PRINTS a
skeleton; it does not post, and the same run *refuses its own brief* for the missing block ("the two
halves are independent"), so a session can run the documented command, see output, and leave the
ladder with no anchor. Measured 2026-09-16 on `civitai/civitai#4886`: round 1's block was never
posted, and `--round 2` refused with `no audit-claims block in any of the 5 comment(s) read`. **The
tell is a refusal naming a COMMENT COUNT rather than an error.** Check both surfaces before
concluding it is absent — `gh api repos/<o>/<r>/issues/<n>/comments` *and* `.../pulls/<n>/comments`,
since the script reads only the first.

🔴 **`--claims-file <path>` is the offline seam, and its cost is INVISIBLE in the brief's own
commands.** It unblocks a delta round with no public write — useful when the repo is public and the
block has not been cleared for posting — but it consults no `gh`, so the run never learns
`headRefOid` or `baseRefName`. The brief then carries the literal string `<the PR's head sha>` in the
range **and in the payload-attribution command that gates the ladder's own stop condition**, plus
`origin/main` as an assumed default it flags but cannot check. An auditor that does not substitute
measures an empty range and reads it as zero payload — the ladder then never ends. **Resolve both
yourself and substitute before dispatching** (`gh pr view <n> --repo <o>/<r> --json
headRefOid,baseRefName`), then assert **zero** placeholders remain. It is a workaround, not a
replacement: the block still lives nowhere durable, so the next session pays the reconstruction
again.

🔴 **Reconstructing a lost block: derive it from the DIFF, never from a handoff's prose.** Prose says
why a fix is correct, which is exactly the framing a blind round must not receive — three framed
audits confirmed a claim that one blind pass refuted. `git diff <from> <to>` yields what was
*claimed*, and it surfaces items the prose never separated.

Dispatch a subagent (read-only — it must NOT modify files or merge) to audit the change against this checklist. Have it read the diff and the code it touches, not just the PR description.

🔴 **RUN `--round 0` FIRST — the requirements & deletion pass (its own section below).** It asks
whether the change should EXIST, which no item on the checklist asks; it is the only round that can
conclude *close this PR, do not audit it*. **No longer on trial: the trial CLOSED at `ran: 6 ·
changed the outcome: 3`** (evidence in the section itself). 🔴 **Run it at PR-CREATE time, not when
you get round to auditing** — that is the one finding the trial produced, and the
`audit-pr-nudge.py` PostToolUse hook now routes it there for you.

**Always run this on high-yield change-classes** — web/HTTP endpoints, concurrency reworks, filesystem/quarantine/trash moves, DB migrations, anything security/auth/path-gating. What each hid, and `GOPRIVATE`: reference file.

🔴 **Do NOT re-type the auditor's environment and cleanup warnings into the prompt — `audit-dispatch.py` now carries them as invariant clauses (`cold-checkout-is-not-the-diff`, `own-what-you-spawn`), so every brief has them in every round.** They lived here as prose telling YOU to remember them, and a probe of two real briefs found them absent from both (0/0) — the precise failure that module exists to end. The rules they rest on — the process-pattern ban, killing by resolved PID, per-agent scratch names, unpopulated submodules, zsh's lack of word-splitting — are in `claude/RULES.md`, which every subagent already receives; only the audit-specific consequences moved. **Still yours, because no brief can do it:** sweep for leaked processes yourself afterwards and verify your own worktree is clean at the end — an auditor's "cleaned up" claim is not evidence.

## ROUND 0 — QUESTION THE REQUIREMENT, THEN DELETE (runs BEFORE the checklist)

✅ **A STANDING RULE — the trial is CLOSED. Read the TRIAL RECORD at the end of this section for
what it cost and what it found; you do not have to re-litigate whether to run it.**

Every axis below asks whether the change is CORRECT. None asks whether it should EXIST, and an
audit scoped to a diff will never raise it on its own: that is how a 145 KB webhook listener
nothing had ever run survived every round that read it. Round 0 is the only round that can
conclude **close this PR, do not audit it**.

The order is the mechanism, not a preference — steps 3–4 spent on something step 2 would have
deleted is the waste this exists to catch. Work them in order; do not skip ahead.

1. **Question every requirement, and NAME its author.** For each behaviour the diff introduces,
   record the requirement and its **author of record**: Zach (quote the ask), a **prior audit
   round** (`#N round R`), a `RULES.md`/`CLAUDE.md` bullet (quote it), or **unattributed** — which
   is itself a finding. 🔴 **A requirement whose author is a PRIOR ROUND OF THIS LADDER is the
   highest-scrutiny class, not the safest.** It arrives carrying a measured incident and a case
   history, so re-opening it reads as ignoring evidence and nobody does — the "requirements from
   smart people are the most dangerous" case exactly. Then make it less dumb: name the requirement
   you would drop or weaken, not only the code that implements it.
2. **Delete.** List what could go — from the diff AND from the code it touches. For each
   candidate ask: **(a) is it RUNNING** — configured, installed, reachable? **(b) has it ever
   caught a real problem, or only fired falsely? (c) does something else already check this
   property against reality?** A "no" to (a) retires it outright; `/adoption-scan` answers (a) and
   (b) for anything already shipped. Where the payload is prose the deletion instruments already
   exist — `/prune-skill`, `/prune-memory` — name the one that applies rather than hand-rolling a
   cut.
   🔴 **NOT the REVERT TEST below — that answers a DIFFERENT question and is wrong here in both
   directions.** It decides whether a file is payload or scaffolding, not whether a thing should
   exist: applied to a PR's own payload it always answers *keep* (reverting it is exactly what
   stops the deliverable shipping), and applied to the surrounding code it always answers
   *deletable* (the deliverable ships regardless). Round 0 is the only round that can conclude
   *close this PR* and a borrowed predicate that can never say so is worse than none. Question (a)
   is the one that did the work in the incident this section cites — the 145 KB listener had no
   token configured on either host and had never run.
3. **Simplify — only what survived step 2.** Owned by `/simplify` and `/code-review`: NAME them,
   do not restate them here. 🔴 Both of those MUTATE (`/simplify` applies its fixes; `/code-review`
   takes `--fix`), and you are dispatched READ-ONLY — so this is a recommendation for the OPERATOR
   to run afterwards, never something you invoke. 🔴 Do not reach for it before steps 1–2 have run;
   simplifying a part that should not exist is the failure this ordering prevents.
4. **Do not accelerate or automate anything steps 1–2 have not cleared.** If this round proposes
   either, name what it applies to and confirm the requirement was questioned and the deletion
   considered FIRST. The ladder's own cycle time is in scope to REPORT; the attribution gate below
   is what acts on it.
   ⚠ This was two steps — *accelerate* then *automate* — and they were merged because nothing read
   them: the ledger counts only steps 1–2, all four verdicts are step-1/2 outcomes, and step 4 said
   "report, do not act" while pointing at a gate that already acts. What survives is the ORDERING
   claim, which is the half that does work. Recorded so the pair is not re-derived as ceremony.

✅ **TRIAL RECORD — CLOSED 2026-09-12 at `ran: 6 · changed the outcome: 3`. The section STAYS; do
not re-open the question.** 🔴 **The finding was the ROUTING, not the pass** — every zero was a
dispatch that arrived after the merge decision was already taken, so the fix was a TRIGGER
(`audit-pr-nudge.py` routes round 0 at `gh pr create`), not an edit to this section. ⚠ **Keep
reporting the pair on each PR** — not to re-decide this section, but because it is now the only
signal for whether that trigger works; `C` alone cannot distinguish "it ran and was useless" from
"nobody invoked it". Decomposition, the per-PR timings, the fourth corroborating instance and the
trigger's first pre-decision catch: `~/.claude/skills/audit-pr/reference/round-ladder-evidence.md`.

🔴 **ROUND 0 REPORTS; IT DOES NOT MOVE THE LADDER.** Its verdict is one of `proceed to the
checklist` / `requirement questioned — <which>` / `deletion candidate — <what>` / `close, do not
audit`. It is **not** a finding for the findings-keyed stop rule, it cannot end a ladder, and it
cannot license skipping a round. Every stop rule below is unchanged by it.

**Ledger:** `round 0 · requirements: N (unattributed: U) · deletion candidates: D`. Deleting
nothing at all is reportable — say what you examined to get there.

## THE CHECKLIST — the nine axes (runs AFTER round 0)

<!-- 🔴 LOAD-BEARING HEADING, NOT NAVIGATION. `_read_round_zero` in
     scripts/audit-dispatch.py captures the ROUND 0 section up to the next
     `## `, and this is that heading. Delete or demote it and the next `## ` is
     `## After the fixes`, so the round-0 brief silently gains the nine
     correctness axes it exists to withhold — measured: 3,367 chars -> 4,057.
     Pinned by test_the_round_zero_section_the_script_reads_is_the_one_the_
     skill_ships. Reword it freely; keep it a `## `. -->

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

### 🔴 THE FIX ROUND'S OWN PROSE IS THE LIKELIEST NEXT FINDING — a false claim replaced by a differently false one

The delta bullet above says to hunt regressions the fix round introduced. **The one it actually
produces, over and over, is not code — it is the SENTENCE the fix wrote to explain itself.** Measured
on `homelab-infra` #702: six rounds, **zero 🔴**, the code correct from round 1, and **round after
round the finding was a claim the previous round had written while fixing the round before it.** One guard's
rationale went through FIVE drafts — each retracted by the next round, each composed in the commit
that fixed the last.

So, when a round's fix rewrites an explanation:

- 🔴 **If a guard has lost its reason, WRITE THAT IT HAS NONE. Do not go looking for a better one.**
  Reaching for a fresh justification is the thing that regenerates the error. That ladder ended only
  when the comment said "nothing justifies this" and recorded all five dead drafts so nobody derived
  a sixth. **"I could not find a purpose" is a finding; a purpose you found while under pressure to
  supply one is a hypothesis.**
- 🔴 **A sentence that NAMES its own missing variable and then asserts a value for it.** *"Which is
  less wrong depends on the population, and the measured one favours X"* — the population was measured
  nowhere, and where both sides were observable it **inverted**. **Delete the comparative; do not
  reverse it.** An unestablished direction stated as established is worse than silence, and the
  giveaway is a clause that concedes the uncertainty in its first half.
- 🔴 **A sweep applied to ONE claim and not the others in the same commit.** One round grep-swept the
  tree for a retracted string and hand-counted a second claim's sites in the same breath — only the
  hand-counted one was wrong (2 of 5 sites). **Sweep every claim in the commit the way you swept the
  hardest one**, and prove the sweep with a positive control, not a bare zero.
- 🔴 **Sweep the surface a HUMAN reads FIRST.** The retracted claim survived longest in the
  operator-facing doc and in the docstring of the test that PINNED the guard — so the code said "this
  question is open" while the README said "this is deliberate" and the test said "it has a purpose".
  **A cross-reference is a claim**: a comment quoting another file's wording goes stale when that
  wording changes, and nothing will tell you.
- ⚠ **A count in prose is a claim.** "FIVE *later* rationales" totals six when the retraction is
  itself #1 — which sends the reader hunting for one nobody wrote. That is how the sixth gets invented.

**Ask each round: what did this fix ASSERT, and is every assertion true?** — the same standard the
round applies to the code. Full case history: reference file.

### 🔴 A clean round ENDS the ladder. Never run another round to confirm a clean round.

Rounds continue **only** while the previous round produced a finding that required a fix. The first
round that returns no findings is the last one — stop there, and do not re-confirm it. Stop on that,
not on the author saying it's done.

🔴 **A round that reports only NITS THAT CHANGE NOTHING A READER DOES is a stopping round** — the
same set `audit-dispatch.py` already keeps out of every brief (`nit-is-not-a-finding`), not a wider
one. **A 🟢 that DOES change what a reader does is a finding and the ladder continues**: #804's round
8 carried three, two of them shipped features that could be unwired with the suite green. File the
stopping kind as one follow-up task naming the file, closed when its PR merges or a named reader
dismisses it in writing — filed rather than fixed, so the round that files them is still the last.

⚠ **That subset is the only class of FINDING that cannot extend a ladder — no SEVERITY is, and
"deploy-blocking only" was rejected.** The attribution gate and the prose escape hatch below end a
ladder for reasons that are not findings at all; this sentence is about findings only.
`homelab-infra` #702 ran six rounds carrying **zero deploy-blockers** while its later rounds kept
catching false claims the previous round's own fix had written: one guard's rationale went through
five successive drafts, each retracted by the next round. A blocker-keyed ladder ends after round 1,
so rounds 2–6 never run — draft 1 ships as the code's stated reason, and nobody ever asks the
retire-or-accept question that ladder ended on. Should-fix findings are what keep it running.

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

🔴 **AND THE FRAME INCLUDES WHETHER THE WORK SHOULD EXIST AT ALL.** Every round asks *"is this change
correct?"*; none asks *"should this change exist?"* — so a ladder can verify a mechanism to exhaustion
while its PREMISE is false, and each clean round makes the object look **more** solid. MEASURED: six
rounds on a CI gate found real defects every time — a rule **inert on the very corpus it was written
for**, a guard exiting 0 under a PASS asserting something it had not established, a ledger covering 3
of 10 cases, a trigger that cancelled its own runs — and the gate was then closed **unmerged**, because
the recurring event it existed to catch had **never happened once** and was becoming impossible. One
question from the operator ended it; six rounds could not, because all six were pointed at the
mechanism. The rounds were not wasted as *findings*, only as *work* — a different axis, and the one
that decides whether to run round seven.
**So: state the PREMISE in the ticket body beside the acceptance criteria, so an auditor can attack it**
— a closing condition proves an object is CLOSABLE, never that it should EXIST. And when a ladder runs
long, spend one round asking what would have to be true for this work to be unnecessary.

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

🔴 **DECIDE ONCE, AT ROUND 1, AND WRITE IT IN THE CLAIMS BLOCK.** A class that can be re-decided
each round disarms the gate without anyone choosing to — measured on devrc #1132, where a shared
test library was named scaffolding early and payload later, in a ladder whose summary claimed it
stopped on this gate. The tie-breaker for a shared helper is the **REVERT TEST**: if this file's
diff were reverted, would the PR's stated deliverable still ship? Yes ⇒ scaffolding — however big
and however reusable the helper is. No ⇒ payload — however deep under `tests/` it sits. Standing
call for devrc: **`scripts/testlib/**` is SCAFFOLDING**, except a scanner that IS a repo gate on a
PR whose deliverable is that gate. Reasons and the worked case: reference file.

🔴 **ONE NUMBER, ONE NAME.** Report exactly one payload count per round and call it the same thing
every time. #1132's ledger carried *"payload lines"* and *"executable payload"* on the same line
with different values, so its rounds could be read as zero or non-zero at will and the stop needed
no reclassification to become unfalsifiable. A second count is fine — under a different name, and
the summary must say which one the stop was taken on.

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

🔴 **The gate is ENFORCED, and the number comes from YOU.** Post each round's block with
`--payload N` — the payload lines THAT round's fixes changed, from your own classification of the
ledger's file list — and the assembler REFUSES the next round (exit 5) when the two most recent
blocks record `payload=0` for CONSECUTIVE rounds. **It fails OPEN:** a block with no readable
`payload=` field is not a zero, so an unstated count never stops a ladder, and
`--override-attribution-gate "<why>"` continues one — with the reason required, and recorded in the
brief and above the block so it lands on the PR.

⚠ **It was prose here until 2026-09-17, and prose lost.** MEASURED on `civitai/talos-infra` #1531:
the condition was met **and stated in writing at the end of round 3** — round 2's ledger reads "zero
payload lines changed" verbatim and rounds 3–11 each repeat it — and the ladder ran **nine more
rounds**: 12 rounds, 33 findings, every one prose in a comment, the functional payload (two `image:`
references) unmoved since round 0, and the last five commits rendering a byte-identical `kustomize
build` (10,843 B, `cmp` rc 0). devrc #1712 reached **round 24** with `payload lines changed THIS
round: 0`. A stop condition a runner can re-read and decline is a suggestion; this one now costs an
explicit flag and leaves a record on the PR.

⚠ **This does not retract the two rules above, and is not a cap in disguise.** #498's rounds were
not wasted in the sense those rules deny — every one found something real. The waste is on a
different axis: real findings *about scaffolding the ladder itself had just written*. The gate
measures the fixes; it never counts the rounds.

### 🔴 A DELTA ROUND CANNOT SEE A CLAIM THE PR'S OWN EARLIER COMMIT STALED

Every round after the first diffs `<previously-audited tip>..HEAD`. A claim that was TRUE when an
early commit wrote it and was FALSIFIED by a LATER commit of the same PR is inside no round's
range — not the first (it was true then), not any delta (it is below the range). It does not look
missable: it sits in the file everyone is editing, reading as precise.

⚠ **Precisely: the STALE LINE is out of range; the FALSIFYING EDIT is not.** The commit that
staled it is inside the very next delta, so the information needed is in front of that round —
what is missing is anything POINTING at the line it invalidated. So the cheap per-round habit is
real and worth having: *what claim elsewhere in these files could this edit have moved?* And note
the cause is not only a sibling commit — **a rebase onto a moved base stales counts identically**,
so do not let the heading narrow your search.

Measured on devrc #1109: `(+ 11 op-selected)` was correct at commit 1 and staled by commit 2,
which added a `_wait_ops` call. **Three delta rounds walked past it, four lines from the paragraph
all three were editing.** It was caught only from OUTSIDE — by the audit of a different PR that
quoted the same number. Nothing pinned it either: no assertion read `op-selected`, so a fully
green suite was silent.

**So, once per ladder and NOT per round — cheap, and it is the only thing that closes this:**
re-derive every COUNT, VERSION and CROSS-REFERENCE the PR's files assert, against the CURRENT
head, ignoring ranges entirely. Ask the LAST round to do it, or do it before merging. A number
nothing asserts on is unpinned by construction; either pin it or stop quoting it.

### 🔴 WHEN THE PAYLOAD IS PROSE THE GATE CANNOT FIRE — STOP ON A STATED CRITERION INSTEAD

For a docs/skill/prompt PR the `.md` **is** the payload, so every round is non-zero by
construction and the two-zero-rounds gate is structurally inert — while the ladder does exactly
what the gate exists to catch, because "fixed a defect" and "reworded a warning" are the same
edit. Left alone it does not terminate. Measured on devrc #1111: round 2's findings were mostly
about text round 1 had written, and the gate could not see it.

**Stop on this — but ONLY once you can NAME, IN THE ROUND'S SUMMARY AND NOT LEFT IMPLICIT, why
the rounds will not stop on their own; read the next paragraph before acting on it.** No 🔴 · no
blast radius beyond "the document contains a false sentence" · and the recurring SHAPE swept at
every site rather than at the one that was reported. Record what you are NOT fixing, on the PR,
so the next reader knows it is open rather than absent.

⚠ **THIS DOES NOT OVERRIDE THE FINDINGS-KEYED STOP RULE, AND IT IS NOT A LICENCE TO STOP ON A
ROUND THAT FOUND THINGS.** That rule still governs: a round returning findings that needed fixing
is followed by another round. This criterion answers a different question — *what ends a ladder
whose rounds keep finding real things forever, because the payload is prose and the attribution
gate cannot fire?* It is the escape hatch for a non-terminating ladder, not a shortcut out of a
converging one. **"Does not terminate" above means the GATE cannot fire, not that findings never
run out** — a prose ladder that returns a clean round ends there, exactly like any other. If in
doubt, run the next round: this criterion is for the case where you can already name why the
rounds will not stop on their own.

🔴 **"WILL NOT STOP ON THEIR OWN" IS AN OBSERVATION ABOUT THE LINES THIS ROUND'S FIX TOUCHED,
COUNTED RATHER THAN DELIBERATED — and never available before ROUND 2. The ⚠ caveat directly
above demands a distinction; this paragraph is the only thing that makes it.** **This count
governs a PR whose WHOLE DIFF is prose, never merely one whose PAYLOAD is** — that is the
population it was derived on, and across ALL ladders two-thirds is an ordinary point on a flat
distribution rather than a gap. A PR that also ships scaffolding is OUTSIDE it, and there the
ordinary attribution gate is REACHABLE: a round whose fix touches only the scaffolding changes
ZERO payload lines, so two CONSECUTIVE such rounds fire it. ⚠ That is why the narrowing costs
less than it looks — not that it is free, and the uncovered set is WIDER than a ladder whose
every round touches payload. ANY mixed-diff ladder in which no TWO CONSECUTIVE rounds are
payload-free has neither mechanism — one alternating payload and scaffolding rounds included,
which is a shape a runner would otherwise read as covered by a gate that can never fire — and
ends only by converging. The unit is
LINES, never findings: a findings count needs a `file:line` per item and what a round RECORDS
about its own fixes almost never carries one, so the denominator is chosen by whoever counts —
and the one the PR record actually supports FORBIDS this section's founding case, `#1111`.
Take the range THIS round's own `audit-claims` block records, `<from>..<to>` — the tip your
audit READ, to the head your fixes produced — and count its **PRE-IMAGE** lines, the ones the
fix MODIFIED or DELETED. A purely ADDED line has no pre-image and is in neither column. Blame
each at `<from>` and ask ONE thing of the commit that wrote it: is it an ancestor of the tip
ROUND 1 audited — the PR's own prose, still converging on what it ships — or is it the
ladder's own text, which means the round is auditing what the ladder wrote? **The reason is
nameable when at least TWO-THIRDS of the attributable pre-image lines are ladder-authored**,
and the count goes in the summary as `<ladder>/<attributable>` so a reader sees the
denominator and not just the ratio. The commands sit below the ⚠ paragraph that follows
this one. Round 1 has no previous round to attribute to, so it can never satisfy this: **two
rounds is the floor, and no rule may move it.**

⚠ **EVERY UNCERTAINTY RESOLVES TOWARDS THE NEXT ROUND, this replaces the JUDGEMENT and not
the other preconditions, and five states are NOT MEASURED rather than a number.** (a) A
pre-image line you cannot blame, or a round you cannot blame in FULL — **there is no cap and
no sample**; capping the corpus measurement at 400 lines moved the share on 8 of 159 rounds
and in BOTH directions, so a partial count is a silent truncation and not a conservative one.
(b) A round whose ladder anchor IS its own `<from>` — what a missing or bare `round=1` block
leaves behind, 3 of 159 corpus rounds, every one of them a round 2: the share is then **0 BY
CONSTRUCTION**, and a structural zero is not a measured zero. (c) An anchor THE LEDGER reports
NOT MEASURED. (d) A round whose fix only ADDED text — an added line has no pre-image, so the
round has NO attributable line and 0/0 is not a share. (e) A round carrying a PURELY REWRAPPED
hunk inside its range — the same words on both sides and nothing else changed. Each of those
means run the next round. **`-w` and `-M` are part of the rule,
not a refinement** — without them a whitespace-only reindent counts as the round having edited
that text; MEASURED, they moved the share on 34 of 159 rounds, on one from 0.25 to 0.89, and
flipped the stop verdict on 3. 🔴 **A REFLOW IS THE ONE BIAS IN THIS RULE THAT POINTS AT
STOPPING, and the command below catches its PURE case ONLY**: rewrapping a paragraph re-blames
every line of it to the rewrapper, which inflates ladder-authored, and no blame flag can see
it — `--word-diff` sees it only where the hunk carries the SAME words on both sides, which is
why that command carries `-U0`: at default context a rewrapped paragraph and an EDITED
neighbour merge into ONE hunk, the words stop matching, and state (e) never fires. That is
state (e). 🔴 **A round that rewraps a paragraph AND edits a word inside it — the ordinary
shape of a ladder fix — is NOT state (e), stays SCOREABLE, and carries the FULL bias. For that
case WRITING THE COUNT DOWN IS THE MITIGATION, not a backstop, and a `--word-diff` run that
reports no pure reflow is NOT clearance.** ⚠ Both (d) and (e) NARROW which rounds are scoreable
and neither moves the number: the corpus derivation below was not re-run with either of them
excluded, so it differs from the population it governs on TWO axes, and because both states
resolve towards the next round, shipping it un-re-derived is conservative.
The severity, blast-radius and swept-at-every-site preconditions above are
unchanged and all still have to hold.

```
git diff -U0 -w -M <from>..<to>                     # its `-<start>,<len>` hunks, len > 0
git blame -w -M --porcelain <from> -- <file>        # once PER FILE, not once per line
git merge-base --is-ancestor <blame-sha> <the tip round 1 audited>   # rc 0 ⇒ PR-authored
git diff -U0 -w -M --word-diff=porcelain <from>..<to>   # -U0 or a neighbouring EDIT merges in
```

🔴 **THE THRESHOLD IS DERIVED FROM THE POPULATION IT GOVERNS, AND THE EVIDENCE FOR IT IS AN
EMPTY BAND, NOT A PROOF.** Measured over every `audit-claims` carrier in this repo — 600 PRs scanned, 90
carriers, 159 delta rounds measured, 157 with an attributable pre-image line: across ALL
ladders two-thirds is an ordinary point on a flat distribution — nearest share below it 0.657,
nearest above it two-thirds exactly, 12 rounds within ±0.05. Across the rounds this rule
actually governs — the PRs whose whole diff is prose, 29 rounds over 7 PRs — it is a **gap**:
nearest below **0.571**, nearest above **0.727**, and **zero** rounds within ±0.05, identical
with and without `-w`/`-M`. 20 of those 29 are at or above it; of the 9 below, one (`#1108`
round 2) is the structural zero the caveat above reports NOT MEASURED rather than as a count.
`#1111` sits at **19/26 = 0.731**, on the far side of the gap. ⚠ **DO NOT READ THE GAP AS PROOF
THE NUMBER IS RIGHT.** At n=29 an empty ±0.05 band is weak evidence: under a uniform null it
happens about 5% of the time (0.9^29), this distribution is concentrated high so the density
near two-thirds is BELOW uniform, and the two shares bounding the band are ratios of SMALL
integers (0.571 = 4/7, 0.727 = 8/11), where a gap near 2/3 is expected. ⚠ That last clause is
weaker than an earlier wording made it: it said EVERY share is such a ratio, and that is false
three sentences up — `#1111` is 19/26, and at denominator 26 both 17/26 = 0.654 and
18/26 = 0.692 fall INSIDE the ±0.05 band. So the band was not empty for want of an available
rational, which makes the evidence somewhat stronger than the sentence claimed. The conclusion
does not move: it shows two-thirds is not sitting on a cluster, and nothing stronger. ⚠ **The honest cost, and
it is not small:** at round 2 only 3 of the 7 prose ladders reach it (their round-2 median share
is 0.267), so for most prose PRs this rule does NOT fire at round 2 either. **Two rounds is the
floor.** How many ladders reach it by round 4 or 5 is UNMEASURED — this paragraph said `4 of 5`
and `5 of 5`, against a denominator that is neither 7 nor recorded in the commit or the handoff,
so the clause is DELETED rather than repaired. And n=7 PRs is a small
population: re-derive before moving the number, do not tune it.

🔴 **WRITING IT DOWN IS THE WHOLE POINT, AND AN EARLIER REWORD DELETED IT.** "Can NAME" is a
private mental state; a reader cannot check it. Without the rationale in the summary a report
that ENDED the ladder on this escape hatch is **indistinguishable from one that converged** — the
findings, verdict and ledger line all look the same — so an operator cannot tell "no findings
remain" from "real 🟡s are deliberately unfixed". Those are opposite meanings. Measured: this
requirement was dropped by `#1133`'s round-2 fix, which added the NAME precondition and removed
the summary obligation in the same edit — **wider on one axis, narrower on another**, the exact
shape this skill tells you to hunt for. 🔴 **This paragraph sits BELOW the NOT-A-LICENCE caveat
on purpose: the "read the next paragraph" pointer is in the CRITERIA paragraph — the one
beginning "Stop on this" — and it means the ⚠ caveat IMMEDIATELY AFTER IT, never a count of
paragraphs, which goes stale the moment one is inserted. ⚠ This sentence said "the CRITERIA
paragraph that OPENS this section" and that was itself a stale positional reference: the
section opens with the "For a docs/skill/prompt PR" paragraph and the criteria are second. Name
the paragraph by its words.** An earlier draft of this very fix inserted this history between the two and silently
re-pointed it at itself — found by the next round. Both of those paragraphs are pinned WHOLE by
`scripts/tests/test_audit_ladder_stop_rule.py`, and their ADJACENCY is asserted separately,
because both pins pass while a paragraph sits between them. This paragraph is pinned by nothing —
edit it freely.

🔴 **RETRACTED DRAFT — "a STRUCTURAL reason is nameable at ROUND 1". Shipped as `#1678`
(`e8fa6fca`), retracted within hours. DO NOT RE-DERIVE IT; the next four paragraphs are why.**
The operator reported prose ladders still grinding (round 1: 3🟡/3🟢, round 2: the same, every
finding in prose the previous round's fix had written) and asked for a stop keyed on zero
high-severity findings. That ask was declined as the `deploy-blocking only` rule rejected at the
top of this section — and then the replacement **re-implemented it by accident**, which is the
part worth remembering.

🔴 **WHY IT WAS WRONG — the substitute collapsed INTO the rule it was avoiding.** The draft's
precondition had two parts: *the payload is entirely prose* and *no 🔴*. The first is true **by
construction for the whole prose class**, so the only discriminating test left was the 🔴 count.
MEASURED: **90 of the last 119 first-parent commits on `main` (75%) touch only `.md`**, so that
was the dominant class, not a carve-out. 🔴 **And it would have killed its own founding
evidence** — this section rests on devrc **#1111**, which touches exactly one file,
`claudedocs/handoff-audit-pr-ladder.md`. Under the draft #1111 qualifies for the round-1
shortcut and stops before producing the round-2 finding the hatch exists on.

🔴 **THE DEEPER REASON, AND THE ONE TO KEEP: "watch it repeat" IS THE ONLY OBSERVABLE THAT
SEPARATES A NON-TERMINATING PROSE LADDER FROM A CONVERGING ONE.** Both have a 100%-prose
payload; the class property cannot tell them apart. The draft replaced the observable with the
property, so the distinction the ⚠ caveat above still demands became unmakeable. Anything
proposing to shorten this ladder must say what it observes, not what the diff IS.

⚠ **Two supporting claims the draft made were also false, checked by audit and reproduced.**
(a) *"the attribution gate cannot fire at any round"* — it can: on a prose-payload PR a round
whose fix touches only the test module changes **zero payload lines**, and two consecutive such
rounds fire the gate; that is the `#498` shape this section already cites. (b) *"with the payload
classification round 1 already owes"* — no artefact carries it: `render_ledger` tells round 1
*"Start the ledger at your round 2"* and `emit_claims_skeleton` emits no classification field, so
the permission depended on something the pipeline never produces.

⚠ **The measured guard gap, recorded so the next attempt budgets for it:** with the draft in
place, widening its scope from `ENTIRELY prose` to `MOSTLY prose or otherwise hard to gate` and
refreshing the pinned constant scored **17 passed AND `✅ 20 row(s), all as expected`**. The
whole-paragraph pin and the battery are both blind to that word. A future version of this
shortcut needs a battery row on its SCOPE, not only on its presence.

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
