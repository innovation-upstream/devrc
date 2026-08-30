# Round-ladder evidence

Case histories behind the ladder rules in `~/.claude/skills/audit-pr/SKILL.md` (source:
`devrc/claude/skills/audit-pr/`). Evidence and worked examples — the RULES live in the skill body,
and nothing here decides whether one applies. Dated, because each is a measurement, not a principle.

---

## 2026-08-26 · `civitai/cli` #498 — ten rounds, and the axis the stop rule cannot see

The measurement behind **ATTRIBUTION: a round that changes no PAYLOAD is auditing the LADDER, not
the PR**. Session `4719a2f0`, `feat/whoami-json-profile-fields`.

| | |
|---|---|
| rounds | 10 (first audit 17:50 → round 10 dispatched 23:22) |
| elapsed | 5 h 32 m |
| share of session | 1,312 of 1,756 transcript rows (75%) |
| main-thread output tokens | 321k of 419k (77%); 100M of 115M cache reads |
| after the last source change | 171k output tokens, 4 h 10 m |
| rounds that returned CLEAN | **zero** |

Churn attribution, from the branch's own fix commits. **Method, because the classifier decides the
answer**: `git show --numstat <sha>`, summing `additions + deletions`, each path classed by an
explicit rule — TEST if a path component is `test(s)`/`spec(s)`/`e2e`/`testdata`/`__tests__`/
`cypress` or the filename matches `*_test.*` / `*.{test,spec,cy}.*` / `*Test.*`; DOC if `*.md`/
`*.txt`/`*.rst` or under `doc(s)/`; SOURCE otherwise.

| rounds | test | doc | source |
|---|---|---|---|
| feature + rounds 1–3 (`bce0c0c`…`d2ec92d`) | 961 | 110 | 222 |
| **rounds 4–10** (`a82718b`…`7541bc1`) | **1,051** | **0** | **0** |

🔴 This table has been wrong **three times**, each time in the direction that flatters the argument,
which is why it now carries its shas and its classifier. `1,061 / 332`: the test column was
mis-added, and the source column folded 110 lines of markdown (68 of a release-notes draft, 42 of
`README.md`) into "production". Then `1,002`: that is rounds 4–**9**; round 10's fix `7541bc1` (49
test lines) landed after the first snapshot and was never re-counted. **The lesson is the rule** —
a number re-derived from a moving branch is a measurement with a timestamp, not a constant, and
"which column does a `.md` belong in" is the same question the gate itself turns on.

The last source change was round 3's `d2ec92d` — **18 lines of `internal/appapi/appblocks.go`**
(a fifth render path, structurally unreachable from the golden table: a real find). Rounds 4–10 each
found something real too; all of it concerned guards that an earlier round of the same ladder had
written.

Two things this case establishes, and one it does not:

- **The ladder manufactures its own audit surface.** A delta round diffs `<audited-sha>..HEAD` and
  each fix round added ~130 lines of new guard code, so there is always new un-audited material.
  A stop condition keyed to *findings* therefore has no exit in the guard-hardening regime.
- **The session already knew.** At round 9 it wrote: *"the shipped behaviour has been stable,
  gate-green and live-verified since round 2. Rounds 3–10 have all been about the guards, not the
  feature… the returns are now in test quality, not in what ships"* — and asked the operator whether
  to stop. The judgement was available five hours before it was acted on; what was missing was a
  measurement that surfaces it at round 4.
- **It does NOT contradict the "not a wasted round" retraction.** No #498 round ran and found
  nothing, so it is not an instance of the waste that retraction denies. Different axis.

Ladder depth across all sessions, measured the same day over `~/.claude/projects/**/*.jsonl`
(numbered delta re-audit dispatches, `subagents/` excluded): **110 sessions, 84 of them in the
preceding 14 days**; mean deepest round 4.0; 34% ran ≥5 rounds; distribution 3→38, 4→22, 5→21, 6→9,
7→4, 8→2, 10→1. The ladder is a dominant workflow, not an occasional one — which is what makes a
per-round gate worth its bytes.

🔴 **The "440 rounds" this paragraph used to quote was the SUM OF DEPTHS, and it did not say so.**
Two different counts are available and they are far apart: rounds *observed* (the distinct numbered
rounds a walk matches) and rounds *implied* (each session's deepest round summed, on the reasoning
that a ladder reaching round 8 ran eight). Re-run 2026-08-27 with the checked-in instrument: 127
sessions, **306 observed / 541 implied**, mean deepest 4.26, 39% ≥5 — and that reading includes the
ten-round ladder this file documents, which is what "the window moves" means in practice. Quote one
count, name which, and date it. 🔴 **That reading was stale within hours of being written** — an
independent re-run the same day gave 319 observed / 556 implied / mean 4.38, because the corpus
includes the very session dispatching the ladder this file describes. A figure from this instrument
is a reading, not a fact about the world.

**Re-derive rather than quote**: `scripts/ladder-depth-sweep.py` is the instrument, it prints both
counts with the run date, and it REFUSES to report a zero it cannot distinguish from a broken
filter — which is what its first version returned, having filtered on the wrong tool name.

---

## `civitai-manager` — the five-round chain, each round caused by the last

Behind **"a fix round frequently introduces the next finding"**. A dead button → fixing it exposed a
silent wrong-file install → fixing *that* introduced a type-check regression that refused legitimate
LoCon installs. Five rounds, every one caught pre-merge, and **none of them by the mechanical
gate** — which is the whole argument for re-auditing the delta rather than trusting a green suite.

---

## devrc #804 — the verdict is not the stop signal

Behind **"a 'safe to merge' VERDICT is not the stop signal"**. Rounds 5, 6 and 7 each returned "safe
to merge" and each still reported a real defect that was then fixed:

1. a module-scope `installAutoStart` whose deletion left the entire lightbox inert in Brave, with
   the suite 99/99 green;
2. four checked-in numbers that disagreed with each other about one measurement;
3. a `didDrag` latch pinned on its SET but not its RELEASE — deleting the reset, deleting the clear,
   and deleting **both** were all green.

A ladder keyed to the verdict stops at round 5 and ships (3).

**#804 is not an example of a wasted round.** All eight rounds produced findings that needed fixing,
round 8 included (three 🟢, two of them shipped features that could be unwired with the suite
green). The stop rule was never exercised on it.

It is also where **"fix the FORM, not the number"** comes from: round 5 found that "four
`.toLowerCase()` guards" was seven, round 6 found four checked-in numbers for one measurement, and
round 8's own commit message records the count regrowing wrong twice *in the paragraph about
counts*.

---

## 2026-08-26 · what a devrc PR actually ships — why the unit is PAYLOAD, not file type

The measurement behind **"the unit is THIS PR's PAYLOAD, never a file extension"**. Classifier as
above, applied to each PR's file list from `gh pr view <n> --json files`:

| selection | no SOURCE at all | docs-only | tests-only | both |
|---|---|---|---|---|
| `gh pr list --state merged --limit 40` (sorts by CREATED) | 24 | 16 | 7 | 1 |
| the 40 most recently MERGED (`--json mergedAt`, sorted) | 24 | 16 | 6 | 2 |

🔴 **This is a moving window, and it moved while this PR was open**: the same two commands read
**25** two hours earlier, because PRs merged in between — including this one's siblings. That is why
the skill body carries the qualitative claim (*most of this repo's merged PRs ship no source file at
all*) and the number lives here with its date, its selection command and its classifier. An earlier
version of the body pinned `24` taken from a subagent's report without re-derivation; the next
pinned `25` measured correctly but decaying by the hour. **A number that changes with the clock does
not belong in a rule** — only in a dated measurement beside it.

Either selection supports the point: **well over half** of what this repo merges ships no source
file, so a gate keyed to "docs are not production" reads zero payload for every round of those PRs
and stops a ladder that is working. **Re-derive before quoting** — the selection is one `gh pr list`
plus the classifier above, and the answer changes with the window.

---

## 2026-08-26 · why the classifier is a JUDGEMENT, not a pathspec

Behind **"the unit is THIS PR's PAYLOAD, never a file extension"**. Measured on a scratch repo
(git 2.55.0), `git ls-files -- ':!*_test.*' ':!*test*' ':!*spec*'` counts as PRODUCTION only
`cypress/e2e/login.cy.ts`, `src/main/java/FooTest.java` and `main.go` — so `':!*test*'` swallows
`pkg/attestation/verify.go` and `api/latest/handler.go`, `':!*spec*'` swallows
`internal/inspector/scan.go`, and the two genuine tests survive as "production". Wrong in both
directions, on directory names any repo might have. `':!*_test.*'` is entirely subsumed by
`':!*test*'`, and docs are excluded by none of them.

---

## 2026-08-26 · which range form counts a ROUND's payload — measured across four shapes

The measurement behind the gate's command. Each shape is a throwaway repo (git 2.55.0); the numbers
are `additions + deletions` summed over the numstat output.

| shape | what the round actually wrote | `git diff A..HEAD` | `--no-merges --first-parent` | `--remerge-diff … --not <base>` |
|---|---|---|---|---|
| A. clean `merge main` brings 200 upstream lines; the round's own fix is 1 test line | 1 line, and it is a TEST ⇒ 0 payload | **201** | 1 | 1 |
| B. one branch-side commit (1 line), then 30 payload lines hand-written into the merge-CONFLICT resolution | 31 | 30 | **1** ⇐ the branch commit only; the resolution is invisible | 37 |
| C. the round's fix is 50 payload lines on a side branch, merged `--no-ff` | ~50 | 51 | **0** | 51 |
| D. control: 12 payload lines, linear, no merges | 13 | 13 | 13 | 13 |

Fixtures: four throwaway repos, each `main` + a `feat` branch off one base commit, built by
`scratchpad/ranges.sh` in the authoring session — A merges 200 upstream lines into a branch whose
only commit edits `app_test.go`; B makes both sides edit `app.go` and hand-writes 30 lines while
resolving; C commits the fix on a `side` branch and merges `--no-ff`; D commits 12 lines directly.
Re-derive rather than quote: the numbers are small enough to rebuild in a minute, and B's `1` is
only legible next to what the branch-side commit contributed.

Bold is wrong. Three lessons, and every one of them was shipped as a rule before it was measured:

- **A** is why the range needs `--not <base>` — a two-dot diff attributes the whole upstream
  bring-in to this round, the gate never fires, and the ladder runs forever.
- **B and C** are why `--no-merges --first-parent` is NOT the fix, though it looks like one and
  shipped as one for a round: it reads **0** for a fix committed on a side branch and merged
  `--no-ff` — the shape agent worktrees produce — so the gate fires and stops a ladder whose payload
  is still moving. `git show --numstat <merge>` is not the remedy either: it prints the first-parent
  diff, so on shape A it reports every upstream line as this round's work.
- **D** is the positive control. A form that gets D wrong is not measuring churn at all.

---

## Mutation variants that delete NOTHING

Behind **"deletion-mutants are the EASY half"**. Across one PR, four semantically broken variants
that delete nothing all passed a suite its author had just "mutation-verified":

- **swap the operands** of a merge/concat — inverts which side wins;
- **invert the branches** of a CASE/ternary — here it turned a merge into an unconditional WIPE,
  strictly worse than the bug being fixed;
- **comment the guard out** — the clause is dead but the TEXT is still present (`--`, `/* */`), so
  every regex looking for it still matches;
- **re-bind a stale value** — literally the original defect, reintroduced.

Two rules the skill body points here for, because they bind the AUTHOR of a guard more than the
reader of a PR:

- **A fixture of empty or default values collapses distinct implementations into identical output.**
  One mutant survived *only* because the fixture was `{}`, which made "bind just the patch" and
  "rebind the whole stale snapshot" byte-identical. Give fixtures non-default sibling values.
- **A review fix RESETS the gate** (`claude/RULES.md`): re-run the FULL mutant battery after every
  fix round *and* after any reformat — not just the mutant for the thing you changed.

What would have caught all four: enumerating mutants from the expression's own semantic failure
modes — operand order, branch order, comment-out, wrong bind, off-by-one — rather than from "delete
the thing I was already thinking about". The `{}` fixture that hid one of them made "bind just the
patch" and "rebind the whole stale snapshot" byte-identical.

---

## Pricing a defect from the CONSUMING code — the worked example

Behind **"verifying that a value is USED is not verifying what its ABSENCE costs"**. An audit
correctly established that a watermark was written, that losing it reset the watermark, and that a
query read it — then priced the loss as "re-judges the whole backlog at LLM cost". The same `WHERE`
clause carried an independent `NOT EXISTS` dedupe that excluded every already-processed row
regardless of the watermark, so the true cost was a wider index scan and **zero** LLM calls. The
wrong figure reached a PR body, a public comment and two code notes before anyone read the full
clause.

---

## High-yield change-classes — what each one actually hid

Demoted from the skill body to pay for the assembler router line; the CLASS LIST stays in the body
because it is what decides whether to run an audit at all, and only these anecdotes moved.

Deploy-blocking bugs that surfaced from exactly these classes: a **shutdown data-loss** on a
concurrency rework; a **trash-path overwrite** on a filesystem/quarantine move; an
**unauthenticated arbitrary-path scan** on an HTTP endpoint; and a git **`core.fsmonitor` RCE**
reachable from a repo-local config.

The false positive to expect on the same road: a branch with a **private Go module dep** may need
`GOPRIVATE`, and a sum-db `500` there is an ENVIRONMENT failure, not a defect in the PR. An auditor
that reports it as a finding has read a broken toolchain as a broken change.

---

## 2026-08-28 · devrc #958 — TWELVE rounds, ZERO clean ones, and a stop rule that never fired

`feat/audit-dispatch-assembler`, squash-merged `1b2117b6`, shipped to both hosts. Odd rounds were
adversarial audits, even rounds were fixes. **No round ever returned clean.** The stop rule in the
skill body — *the first round that returns no findings is the last* — was never exercised, because
its precondition never occurred. The ladder was ended on a different criterion, stated below.

This is the companion case to #498 above. #498 measured a ladder that ran on **scaffolding**; this
one ran on payload every round and still would not converge.

### The recurring shape — eight instances, each one level up from the last

Every round found the same defect: **a predicate read as a STRONGER fact than it carries.**

| round | the predicate | what it establishes | what the code read it as |
|---|---|---|---|
| 3 | the checkout being inspected | the operator's tree | the tree under audit |
| 5 | the assembler's cwd | where the brief was BUILT | where the auditor STANDS |
| 7 | `HEAD` at assembly | the tip verified at assembly time | the tip the auditor resolves later |
| 8 | `--repo owner/name` | reroutes `gh` | reroutes `git` as well (it does not) |
| 8 | `newest is not None` | a claims block PARSED | there is something to DIFF FROM |
| 9 | `repo_relation == "cross"` | two slugs differ | three separate stronger facts |
| 10 | `baseRefName or "main"` | a DEFAULT was substituted | the base was MEASURED |
| 12 | `not data.get("baseRefName")` | the payload lacked the key | the base was measured (again, one field over) |

Two lessons the table carries that no single round could:

- **Each fix was applied where the defect was found and NOT at the sibling consumers of the same
  predicate.** That is why sweeping for the next instance predicted it five times running. `git grep`
  every consumer of any predicate you touch, and say how many you found.
- **"A default stated as a fact" recurred twice** (rounds 10 and 12), the second time invisible to
  the guard written for the first — because the mode that hardcoded the field was the one mode three
  separate comments called permanently assumed. A fallback is a guess; a predicate that cannot tell
  a guess from a measurement will report the guess as measured.

### Why the ATTRIBUTION gate could not rescue this ladder

The gate from #498 stops after two consecutive rounds that change no payload. It never fired here,
and correctly so — round 9 measured 314 payload lines. But the reason is worth recording:

🔴 **This PR's payload IS PROSE.** The script's product is a text brief, so "fixed a real defect" and
"reworded a warning" are frequently the SAME EDIT. Payload-vs-scaffolding cannot separate them.
**The attribution gate has a structural blind spot on any PR whose deliverable is text** — a
generator, a docs tool, a prompt assembler. Do not expect it to terminate those ladders.

### The criterion actually used to stop

Since "a clean round" was unreachable, the ladder was ended on three checkable conditions, all met:

1. no finding is 🔴;
2. no finding's blast radius exceeds *"the brief contains a false sentence"*;
3. the recurring shape has been swept at **every** consumer of the predicate the round touched.

Decide a criterion like this **in advance** when entering a guard-hardening ladder. Inventing one
under fatigue at round 11 is how a ladder either runs forever or stops on the wrong signal.

### What the late rounds actually caught — the case against stopping early

- **Round 10's own report of its work was wrong.** It said a mutation row *"lost seven of round 8's
  thirteen"* killer names. Round 11 measured it by AST diff: **nine departed of nineteen**; thirteen
  was what REMAINED. Both numbers in a claim about counting, in the file whose thesis is that
  arithmetic nobody re-runs rots.
- **A guard with ZERO battery rows, and walkable.** `test_the_ledger_says_the_base_was_not_fetched`
  was listed in a ledger whose declared evidence IS the battery, yet no mutant killed it. It still
  worked — deleting the warning failed that test and only that test — but a reword that gutted the
  load-bearing qualifier left the suite **fully green at 109 passed**. Round 10 had removed the
  evidence, not the detector.
- **Two instrument defects, found by the ladder and not by any gate.** `_swap` asserted its mutation
  target was PRESENT but not UNIQUE, and a same-round edit created a duplicate at a deeper indent
  (8 spaces ⊂ 12), so a mutation landed on a branch where the variable was not in scope. Separately,
  a prose guard normalised wrapped STRINGS but not wrapped `#` COMMENTS while its docstring claimed
  comments — and the battery's own mutant planted the phrase on ONE line, so the shape was never
  exercised. **A green sweep is a claim about the mutations you imagined.**

### The generalisation

In the guard-hardening regime — where each fix round writes new guards that become the next round's
audit surface — **a findings-keyed stop rule does not terminate.** That is not a reason to cap the
count (see the rejected cap, below), and not a reason to trust the verdict instead of the findings.
It is a reason to name the stopping condition before you start, and to make it something a round can
actually satisfy.

---

## 2026-08-30 — devrc #1109 / #1111: two structural blind spots, both measured in one session

### A. The delta-range blind spot — three rounds walked past a false claim in the file they were editing

`_wait_events`' docstring in `scripts/browser-bridge/tests/test_server.py` carried
`(+ 11 op-selected)`. The true value at the PR head was **12** (`_wait_ops` 9 + `_wait_payload` 3,
by `classify_wait_calls()`).

- `git log -S` attributed the line to **`2579e2f3`, the PR's own first commit**, where it was
  CORRECT.
- **`e4777c58`, the round-1 audit fix, staled it** — it added a `_wait_ops` call while sequencing
  `test_a_neighbours_row_of_the_same_op_is_not_selected_as_one_of_ours`.
- Round 1 audited `..2579e2f3` (true then). Rounds 2 and 3 audited `e4777c58..` and `621ef6c2..`
  (below the range). **No round's range contained it**, and three rounds edited the paragraph four
  lines away from it.
- It was found by the round-2 audit of **a different PR** (#1111), which quoted the same number
  from its handoff doc and re-derived it.
- Nothing pinned it: `git grep op-selected` at that head returned only prose lines; no assertion
  read the value, so the full suite (477 passed locally, `RESULT: PASS (exit=0)` in the sandbox
  tier) was green throughout.

The generalisation is in the skill body. The cheap remedy is a **once-per-ladder, range-free**
re-derivation of every count/version/cross-reference the PR's files assert, against the current
head.

### B. The gate is inert on a prose PR — #1111 needed the stated criterion

#1111 was a handoff-doc PR. Payload = the `.md`, so the two-zero-payload-rounds gate could never
fire: round 1 changed 197 lines, round 2 changed 96. Meanwhile round 2's findings were largely
about text round 1 had written — a retraction that had been APPENDED under one heading while the
original claim still stood under another (`handoff_doc.py` merges those sections by appending, so
a delta cannot remove a sentence there), and a corrected count that had itself gone stale.

It was closed on the stated criterion — 0 🔴, no blast radius beyond "the doc contains a false
sentence", recurring shape swept at every site — with the two remaining 🟢 recorded on the PR as
open rather than silently dropped.

### C. Round-by-round tally, both PRs

| PR | round | findings | fix changed |
|---|---|---|---|
| #1109 | 1 | 3 (incl. the GUARD test carrying the identical race) | 78 lines, behaviour |
| #1109 | 2 | 1 (census verb widened, old count kept) | 37 lines, comment-only |
| #1109 | 3 | 1 🟡 + 1 🟢, **both artifacts of round 2's own edit** | 37 lines, comment-only |
| #1111 | 1 | 1 🔴 + 6 🟡 | 197 lines |
| #1111 | 2 | 4 🟡 (1 prior claim NOT fixed) | 96 lines |

**Every round found something real** — the ladder was not idling. But #1109 rounds 2 and 3 changed
**zero executable lines** (verified by `ast.dump()` equality modulo docstrings, not by eye), and
round 3's auditor independently reached the stop verdict: *"the marginal defect being found is now
generated by the process rather than detected by it."* That is the gate firing as designed.

🔴 **Three of the four blind auditors declined to run the sandbox tier**, and the brief is a
PLAUSIBLE cause — not a demonstrated one. It told them to run `nix build …#checks…` and "read each
runner's own `RESULT:` line", with **no `-L`**, which prints nothing for a build that succeeds.
⚠ **But "declined to run a tier" is an EMPTY RESULT, and those do not identify a mechanism**: a
~20-minute build, the brief's own "if this repo has one" hedging, store contention and budget all
produce the same observable. No signal distinguishes them here, so the attribution is unproven and
is recorded as such. Fixed in `scripts/audit-dispatch.py` regardless, because the defect is real
independently of whether it caused those three declines; see that file's TOOLCHAIN block.

---

## Where the rest lives

- The **rejected numeric cap** and devrc #505's ReDoS-introduced-by-the-fix evidence: stated inline
  in the skill body (it is load-bearing there) and in `~/.claude/RULES-ARCHIVE.md` →
  `audit-fix-resets-gate`.
- The retraction of the original "measured waste" justification: `~/.claude/RULES-ARCHIVE.md` →
  `audit-fix-resets-gate`, and pinned by `devrc/scripts/tests/test_audit_ladder_stop_rule.py`.
