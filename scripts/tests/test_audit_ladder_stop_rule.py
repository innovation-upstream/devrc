"""The adversarial-audit ladder must keep its STOP condition — and must not
acquire a round CAP instead.

WHAT THE RULE IS
----------------
`claude/RULES.md` mandates SEVERAL delta re-audit rounds after a review fix, and
for a measured reason: "every delta audit found something the previous round's
fix had introduced". Nothing said when to STOP, and the obvious stop signal --
the auditor's "safe to merge" verdict -- is the wrong one.

Verified against devrc #804's own commit bodies (`gh pr view 804 --json commits`,
then the message bodies): **rounds 5, 6 and 7 each returned "safe to merge" AND
each reported real defects that were then fixed.** Round 7's was a `didDrag`
latch pinned on its SET but not its RELEASE -- deleting the reset, deleting the
clear, and deleting BOTH were all green. A ladder keyed to the VERDICT stops at
round 5 and ships a guard that reads as pinned and is vacuous in both directions.

The rule that closes it is one sentence, keyed to findings rather than to the
verdict or to a count: **a clean round ends the ladder; never run another round
to confirm a clean round.**

🔴 A RETRACTION, KEPT BECAUSE THE RETRACTED VERSION SOUNDS RIGHT
----------------------------------------------------------------
This module first justified the rule as a fix for measured WASTE -- "#804's
rounds 5-8 all returned safe to merge, so four rounds ran after the stop
condition was met", plus round counts for #482/#505/#390/#393. **Every
load-bearing part of that is false.** #390 has ONE commit and no ladder; #393's
five commits are independent follow-on fixes, not numbered rounds; #482's rounds
are unnumbered; and critically, **no cited PR contains a single round that ran
and found nothing** -- so none of them evidences a wasted round at all. Even
#804's round 8, whose auditor called the clean state the stop condition, still
carried three 🟢 (two were shipped features that could be unwired with the suite
green). The ladder ran to 8 legitimately and this stop rule was never exercised
on it.

So the rule is FORWARD-LOOKING with a demonstrated near-miss. It is not a remedy
for observed waste, and must not be cited as one.

🔴 THE ATTRIBUTION GATE -- THE AXIS THE FINDINGS-KEYED RULE CANNOT SEE
----------------------------------------------------------------------
Added 2026-08-26. The stop rule above is keyed to findings, and there is a whole
regime in which it can never fire: a fix round WRITES new guards, the next delta
round diffs `<audited-sha>..HEAD`, so those guards are its audit surface. The
ladder manufactures its own next round's findings.

Measured on `civitai/cli` #498 (session 4719a2f0, 2026-08-26): **10 rounds over
5 h 32 m**, 75% of the session's transcript rows, **321k of 419k main-thread
output tokens**. The fix commits for **rounds 4-10 changed 1,051 lines of test
code and ZERO lines of what the PR SHIPPED** -- the last such change was round
3's `d2ec92d` (18 lines of `internal/appapi/appblocks.go`). **No round was ever
clean**, so the stop rule was never once eligible to fire, and the session
escalated to the operator at round 9 with its own diagnosis ("rounds 3-10 have
all been about the guards, not the feature").

Scale, measured the same day over `~/.claude/projects/**/*.jsonl` (numbered delta
re-audit dispatches, `subagents/` excluded): **110 sessions, 440 rounds, 84 of
them in the preceding 14 days**; mean deepest round 4.0; 34% ran >= 5 rounds.

So the gate is: two consecutive rounds whose FIXES changed zero PAYLOAD lines
=> the ladder has left the PR, stop. Payload = what the PR exists to ship, never
a file type: keyed to "docs are not production" the gate reads zero for every
round of a docs or skill PR and stops a working ladder -- and most of what this
repo merges is exactly that. The measurement is in the reference file with its
date, its selection command and its classifier, because it MOVES: it read 25 of
40 and then 24 of 40 within two hours as PRs merged, and an earlier `24` in the
body had come from a subagent's report without being re-derived. A number that
changes with the clock does not belong in a rule.

**Per-round, and every commit the round actually made.** Anchored at round 1 the
count is non-zero forever once an early round touched payload -- the first
version of this rule shipped exactly that, and on #498 it prints the same number
for rounds 4-10 and never fires. The second version shipped
`--no-merges --first-parent`, which is WRONG in the other direction. Measured
across four shapes (full table in the reference file; git 2.55.0):

  shape                                    truth  two-dot  --no-merges   --remerge-diff
                                                           --first-parent  --not <base>
  A clean `merge main`, fix = 1 test line    0      201        1              1
  B payload in a merge-CONFLICT resolution  >0       30        1 (!)         37
  C fix on a side branch, merged --no-ff    ~50      51        0 (!)         51
  D control: 12 payload lines, linear        13      13       13             13

So: `--not <base>` is what excludes the upstream bring-in, `--remerge-diff` is
what makes conflict-resolution payload visible, and `--no-merges --first-parent`
reads ZERO for a fix committed on a side branch and merged `--no-ff` -- the shape
agent worktrees produce -- which fires the gate on a ladder whose payload is
still moving. `git show --numstat <merge>` is not a remedy either: it prints the
first-parent diff, so on shape A it reports every upstream line as this round's.

It is not a cap (it counts payload lines, never rounds; a round that touches
payload never trips it however deep the ladder is) and it does not retract the
retraction below -- #498 contains no round that ran and found nothing, which is
the waste that retraction denies. Different axis: real findings about scaffolding
the ladder itself had written.

🔴 WHY THIS MODULE ALSO GUARDS THE COUNTER-EVIDENCE
---------------------------------------------------
The obvious fix -- cap the rounds at N -- was written first and REJECTED, on this
repo's own verifiable evidence. devrc #505's round 2 opens "Round 1 fixed six
findings and introduced two of its own", and its round 4 caught a ReDoS that
round 3's OWN fix introduced -- `{7,40}` inside a `*` loop, three 40-char shas
not returning in 30 s, hanging `scan_open_actions` and so `/handoff` with no
output -- plus a terminator requirement round 3 added that silently dropped ten
marker shapes, "the failure this detector exists to prevent, reintroduced by the
fix for the previous one". A cap at 2 or 3 ships both. So the count is set by
FINDINGS, never by a number.

That makes the "not a cap" clause load-bearing, not decoration: without it the
next person reading "stop earlier" reaches for the cap again, and the rule that
replaces this one is strictly worse than no rule. Both halves are pinned.

WHY THE ASSERTIONS LOOK BRITTLE
-------------------------------
The artifact under test is PROSE. `claude/RULES.md`: "when the artifact under
test IS prose, a guard on WORDS is walkable by REWORDING -- pin the WHOLE
normalised string. A cosmetic reword then fails the test -- pay it, for a
machine-readable claim."

So every constant below is a whole normalised sentence (or heading), asserted to
occur EXACTLY once. A keyword match would be satisfied by a document that names
"clean round" while telling you to run one more; the whole-string pin is not.
A reword SHOULD fail here, and the fix is to update the constant in the same
commit -- which is exactly the moment someone should notice they are rewriting a
rule rather than reformatting a paragraph.

🔴 WHAT THIS MODULE DOES **NOT** ENFORCE -- read before trusting it as coverage
------------------------------------------------------------------------------
1. **It pins these SENTENCES, not the CONCEPT.** A semantically identical
   restatement in different words is invisible to it, exactly as
   `test_closing_condition_single_source.py` records for its own pins. There is
   no known mechanical fix.
2. **It cannot observe BEHAVIOUR.** Nothing here proves any session actually
   stopped at the clean round; it proves the instruction is present, whole, and
   in the place a reader hits it. The behavioural claim would need transcript
   measurement over future PRs and is NOT made.
3. **It does not detect a cap added in NEW words elsewhere.** It pins the
   rejection where the rule lives; a cap invented in a third file is out of
   scope.
4. **`test_the_guarded_files_exist_and_are_substantial` is an INVARIANT GUARD,
   not regression coverage.** It was green at `origin/main` and green at HEAD --
   it never caught the bug this module is about. It is labelled here rather than
   counted as a catch. It IS reachable: watched to fail by stubbing SKILL.md.
5. **Nothing here proves the attribution gate is RUN.** Same blind spot as (2),
   and it bites harder for this one: the gate is a `git log --numstat` an
   operator has to issue between rounds, so the only evidence it fired is a
   ladder that stopped. The behavioural claim needs transcript measurement over
   future ladders -- `scripts/ladder-depth-sweep.py` is that instrument, and it
   prints both round counts with the date because the two differ -- and is NOT
   made here. SOME of the rows quoted below are re-derivable with `nix develop
   ~/workspace/devrc -c bash scripts/tests/mutants-audit-ladder.sh` -- and that
   harness, not this sentence, is the authority on WHICH: it names every row it
   runs, and it runs some rows (the worktree hazards from #922) that appear in
   no matrix here at all. 🔴 NO COUNT IS QUOTED HERE ON PURPOSE. Two independent
   counts of these same two matrices disagreed -- 44 and 48 -- because "a row"
   is ambiguous between labelled lines, distinct labels, and mutants-excluding-
   controls. A total kept beside what it counts drifts, which is this file's own
   rule; count them in the harness, where they are the executable list.
   Before that harness was checked in, every matrix here was a claim nobody else
   could re-run at all.
6. **The CLASSIFIER is a human judgement, deliberately -- and only its WORDING
   is pinned.** The rule tells the reader to name each changed file payload or
   scaffolding rather than run a pathspec, because a pathspec is wrong in both
   directions on ordinary names (`':!*test*'` excludes `pkg/attestation/` and
   `api/latest/`, `':!*spec*'` excludes `internal/inspector/`; both keep
   `FooTest.java` and `login.cy.ts` -- measured). Three constants now pin the
   definition, the method and that counter-evidence, after FOUR mutants that
   rewrote the method -- including one flipping "Ambiguous is not zero" to its
   opposite, and one reinstating the pathspec as the method with the 🔴 warning
   left intact -- passed a green 11-test suite with only the prohibition pinned.
   (A fifth survivor of that same run, Z8, belongs to the evidence family and is
   caught by `EVIDENCE_BASELINE_ROW`, not by these three.)
   What remains unenforceable: whether a given round's files were named
   CORRECTLY. Nothing mechanical can answer that, which is why the residual
   error is aimed at the safe side (ambiguous => the gate does not fire).
7. **The evidence file is pinned ROW BY ROW, so a row nobody pinned is
   unguarded.** The `EVIDENCE_*` constants below are the whole of it -- count
   them; shape D (the positive control) and shape B's row are NOT among them,
   and neither is any prose outside those sentences. A row added later is unguarded until someone pins
   it, and nothing here notices. The pattern to follow: if the body delegates a
   number to that file, the row carrying it gets a constant in the same commit.
8. **The THRESHOLD is pinned, not justified.** "Two consecutive rounds" comes
   from one measured ladder (#498, n=1), where the plateau began at round 4 and
   ran to 10. One round is knowingly too tight: a round may legitimately fix only
   a guard. Whether two is right is a judgement, and this module cannot tell a
   deliberate re-tune from a quiet weakening -- it only makes the change visible
   in the diff, which is the point of pinning a number in prose at all.

WATCHED TO FAIL -- the matrix, so this module is not taken on trust
-------------------------------------------------------------------
Red at `origin/main` (the three guarded documents restored via `git show
origin/main:<path>` into a scratch tree, this module copied in unchanged):
every stop-rule assertion red, the invariant guard green, as its label above
predicts.

🔴 The counts originally recorded here -- "4 failed, 1 passed" red and "5 passed"
green -- were WRONG when written: the module shipped in that same commit with
**6** tests, so no run of it can produce a 5-test total. Re-measured 2026-08-26
at `6509702b`: **6 passed**. Corrected rather than left, because this file's own
thesis is that a total maintained in parallel with the thing it counts drifts --
and an unverified self-reported matrix inside the module that pins other
people's claims is the worst place for it to happen.

Mutation controls, each run on a copy of the HEAD tree, under
`PYTHONDONTWRITEBYTECODE=1` with the pytest cache disabled. The unmutated copy
is the positive control, so a red below is the mutant and not the harness.
**Re-measured 2026-08-26 against the current 11-test module** -- the counts
first recorded here were taken against a 5-test module that never existed, and
M2/M5 have since gained assertions that also fire:

  POS  unmutated copy ......................... 11 passed
  M1   cosmetic reword of the RULES clause
       ("FINDINGS, never by a number" -> "findings,
       not by a number") ....................... 2 failed -- the string pin AND
                                                 the relationship pin
  M2   delete the SKILL heading ................ 2 failed -- heading pin, plus
                                                 the order test's both-present
                                                 precondition
  M3   replace the not-a-cap paragraph with an
       actual cap ("Cap the ladder at three
       rounds") ................................ 1 failed -- not-a-cap pin
  M4   move the stop clause into its OWN bullet,
       text byte-identical ..................... 1 failed -- relationship pin
                                                 ONLY
  M5   truncate SKILL.md to a stub ............. 9 failed -- the invariant guard
                                                 fires with its own message
  M6   drop the rejected-cap evidence from the
       archive ................................. 1 failed -- archive pin

🔴 M4 is the one that matters for reachability. The clause survives verbatim, so
every whole-string pin stays GREEN and only
`test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds` goes red -- which is
what proves that assertion executes and is not a second spelling of the string
pin it sits beside.

THE ATTRIBUTION-GATE PINS -- their own matrix (2026-08-26, 5 tests added, 6 -> 11;
22 mutants at the fourth round of review)
---------------------------------------------------------------------------------
Same method: each run on its OWN scratch tree built from HEAD, never the
worktree, under `PYTHONDONTWRITEBYTECODE=1` with the pytest cache disabled, and
every mutant asserts its target string is present before editing. That assert is
not ceremony -- two mutants below silently failed to apply on a first pass
because their target had been re-wrapped, and without it both would have been
recorded as SURVIVED off an 11-passed run.

  POS  unmutated copy ......................... 11 passed  <- harness control
  BASE origin/main's SKILL.md, reference file
       absent (i.e. pre-change) ............... 6 failed, 5 passed
  PREV the round-1 tip `ed38490b` ............. 5 failed, 6 passed  <- the tree
                                                the second audit read; these are
                                                the pins added after it

  the five that SURVIVED a green 11-test suite until the classifier was pinned:
  Z1   classifier METHOD sentence deleted ..... 1 failed
  Z3   "the payload IS the `.md`" -> "docs are
       never payload" ......................... 1 failed
  Z4   fail-safe inverted ("Ambiguous counts as
       zero: the gate fires") ................. 1 failed
  Y9   method replaced by the pathspec, the 🔴
       warning left intact .................... 1 failed
  Z8   corrected baseline row -> 9999s ........ 1 failed

  the regressions earlier rounds shipped:
  X8   command reverted to the cumulative
       two-dot diff ........................... 1 failed
  Y4   evidence truncated to 1,400 B (passed
       under the old 1,000 B floor) ........... 2 failed
  Z9   churn row reverted to the stale 1,002 .. 1 failed

  added after the third audit:
  W1   heading reverted to "no PRODUCTION code" .. 2 failed  <- the heading was
                                                  the one sentence the payload
                                                  fix missed, and it was PINNED,
                                                  so the pin held the
                                                  contradiction in place
  W2   dated measurement -> vague "many" ....... 1 failed
  W3   the wrong-flag prohibition inverted
       ("do reach for --no-merges
       --first-parent") ....................... 1 failed  <- SURVIVED until the
                                                  trap was pinned
  W4   evidence truncated to 1,520 B ......... 1 failed  <- the FLOOR passes;
                                                  the pin's message names the
                                                  file size, which is what
                                                  separates truncated from
                                                  reworded at any size
  W5   truncation SPLITTING a multibyte
       character (1,500 B) .................... 1 failed  <- and it still prints
                                                  that message rather than a
                                                  UnicodeDecodeError traceback,
                                                  which is what `errors=
                                                  "replace"` in `_read` buys

  added after the fifth audit -- every one of these was a rule the previous
  round shipped UNPINNED, and each mutant below was green before it:
  V1   `<base>` redefined as the fork point ... 1 failed
  V2   "a failed command is not zero" inverted  1 failed
  V3   shape-C row rewritten to erase the
       argument for the current command ....... 1 failed
  V4   PR-population row rewritten to 2 of 40 . 1 failed

  added after the sixth audit -- the evidence file was pinned ROW BY ROW, so the
  rows and lessons nobody had pinned were still rewritable:
  U1   shape-A row blanked ................... 1 failed
  U1b  shape-A row INVERTED so the table
       argues FOR the flag the body
       prohibits .............................. 1 failed  <- the sharp one
  U6   shape-A lesson negated ................ 1 failed
  U7   shape-B/C lesson negated ............... 1 failed

  added after the seventh audit:
  T1   the empty-range clause deleted ......... 1 failed  <- the check the
                                                previous rewrite dropped
  T2   the B/C lesson's TAIL inverted ......... 1 failed  <- green until the
                                                lesson was pinned WHOLE

  added after the eighth audit -- the first two are the CHECKLIST, unpinned for
  eight rounds while every round FROM THE FIFTH ON shaved words out of it to
  stay under budget (measured: 4 of the 8 pre-fix commits touched the block):
  S1   "rollback" shaved out of the Gaps item  1 failed  <- the actual regression
  S2   the whole 9-item checklist replaced
       with "whatever seems off" ............. 1 failed
  S3   stderr-capture rule INVERTED ("fold it
       into the sum so nothing escapes") ..... 1 failed
  S4   reworded-rule regression shape deleted   1 failed

  added after the ninth audit -- one pin that certified PRESENCE without
  certifying the instruction around it, and one block with no pin at all:
  N1   delta-bullet HEAD inverted to "do NOT
       hunt", pinned phrase byte-identical ... 1 failed
  N2   the other regression shapes deleted,
       phrase intact ......................... 1 failed
  P1   worktree duty shifted back to the
       auditee (round 8's regression, exactly)  1 failed
  P2   environment-brief lead gutted ......... 1 failed
  P3   zsh word-split warning deleted ........ 1 failed

  the round-1 set, re-run against current strings:
  X3   gate command -> `echo 0` ............... 1 failed
  X4   ledger instruction deleted ............. 1 failed
  X5   behaviour/guard labelling bullet
       deleted ................................ 1 failed
  M8   decision reworded TWO -> THREE ......... 1 failed  (decision pin only)
  X6   evidence file truncated to 0 B ......... 2 failed
  M10  section moved ABOVE the stop rule,
       text byte-identical .................... 1 failed  (order pin only)

🔴 M10 is this half's reachability control, and the analogue of M4. The
attribution section is MOVED above the stop rule with its text byte-identical:
every whole-string pin stays GREEN and only
`test_the_attribution_gate_comes_after_the_rule_it_bounds` goes red (1 failed) --
which proves that assertion executes rather than restating the pins beside it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RULES_MD = REPO_ROOT / "claude" / "RULES.md"
ARCHIVE_MD = REPO_ROOT / "claude" / "RULES-ARCHIVE.md"
SKILL_MD = REPO_ROOT / "claude" / "skills" / "audit-pr" / "SKILL.md"
EVIDENCE_MD = (
    REPO_ROOT / "claude" / "skills" / "audit-pr" / "reference"
    / "round-ladder-evidence.md"
)

# --------------------------------------------------------------------------- #
# THE PINS -- whole normalised strings, never keywords.
# --------------------------------------------------------------------------- #

# `claude/RULES.md`, inside the "An audit/review fix RESETS the verification
# gate" bullet. Both halves in one constant on purpose: the stop condition and
# its not-a-cap qualifier are a single claim, and pinning only the first half
# would let the second be dropped -- which is how the rejected fix comes back.
RULES_STOP_CLAUSE = (
    "🔴 **A CLEAN round ENDS the ladder — never run another to confirm one.** "
    "Rounds continue only while the previous round produced a finding that "
    "needed fixing; the first that returns none is the last. **Not a cap** — "
    "the count is set by FINDINGS, never by a number."
)

# The sentence in the SAME bullet that MANDATES several rounds. The stop rule is
# only safe next to it; see `test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds`.
RULES_SEVERAL_ROUNDS = (
    "**Budget for SEVERAL rounds and re-audit the DELTA each time**"
)

# `claude/skills/audit-pr/SKILL.md` -- the operational spelling of the same rule.
SKILL_HEADING = (
    "### 🔴 A clean round ENDS the ladder. Never run another round to confirm "
    "a clean round."
)
SKILL_STOP_BODY = (
    "Rounds continue **only** while the previous round produced a finding that "
    "required a fix. The first round that returns no findings is the last one — "
    "stop there, and do not re-confirm it. Stop on that, not on the author "
    "saying it's done."
)
SKILL_NOT_A_CAP = (
    "🔴 **This is NOT a round cap, and a cap was rejected.** The count is set by "
    "FINDINGS, never by a number, and #505 is why: its round 2 opens *\"Round 1 "
    "fixed six findings and introduced two of its own\"*, and its round 4 caught "
    "a **ReDoS that round 3's own fix introduced** — three 40-char shas did not "
    "return in 30 s, hanging `/handoff` with no output — plus a terminator "
    "requirement that round 3 had added and that silently dropped ten marker "
    "shapes, *\"the failure this detector exists to prevent, reintroduced by the "
    "fix for the previous one\"*. A cap at 2 or 3 ships both."
)

# 🔴 The load-bearing EVIDENCE sentence: verdict != findings. This is what makes
# the rule findings-keyed rather than verdict-keyed, and it is the half that
# survived verification when the original "measured waste" justification did not.
SKILL_VERDICT_NOT_STOP = (
    "🔴 **A \"safe to merge\" VERDICT is not the stop signal — the FINDINGS are.** "
    "#804's rounds **5, 6 and 7 each returned \"safe to merge\" and each still "
    "reported real defects** that were then fixed"
)

# 🔴 The RETRACTION itself is pinned. Without this, deleting the retraction is a
# silent edit that leaves the false "four wasted rounds" story free to be
# rewritten from memory by the next author -- which is exactly how it got into
# five files the first time.
SKILL_NOT_WASTE = (
    "⚠ **#804 is NOT an example of a wasted round, and neither is any other PR "
    "cited here.**"
)

# 🔴 The PROSE-payload escape hatch is the ONE criterion that can END a ladder
# without a clean round, and until this pin it was the only stop-related clause
# in the file that nothing asserted on. That asymmetry is how it drifted: the
# findings-keyed rule around it carries five pins, so a reword there fails
# loudly, while a reword HERE was silent.
#
# What is load-bearing is the DELIVERABLE, not the auditor's confidence. "Can
# NAME" is a private mental state and no reader can check it; the rationale has
# to land in the round's summary, because a report that ended the ladder on this
# hatch is otherwise INDISTINGUISHABLE from one that converged -- same findings
# section, same verdict, same ledger line -- and those mean opposite things.
#
# Measured: #1133's round-2 fix added the NAME precondition and deleted the
# summary obligation in the SAME edit (wider on one axis, narrower on another --
# the shape this very skill tells auditors to hunt). It reached `main` and was
# caught only by a later blind round, four commits downstream.
SKILL_PROSE_ESCAPE_STOP = (
    "**Stop on this — but ONLY once you can NAME, IN THE ROUND'S SUMMARY AND NOT "
    "LEFT IMPLICIT, why the rounds will not stop on their own; read the next "
    "paragraph before acting on it.**"
)

# `claude/RULES-ARCHIVE.md`, anchor `audit-fix-resets-gate` -- the evidence the
# core bullet's `→ archive:` tag routes to. Pinned so the rejected-cap reasoning
# cannot be evicted into nothing: RULES.md is byte-capped, so this anchor is
# where a future editor is TOLD to move detail, and an eviction that loses the
# rejection loses the only record of why the narrow rule was chosen.
ARCHIVE_REJECTED_CAP = (
    "A numeric CAP was the first version of this fix and was **rejected**, on "
    "evidence that IS verifiable: #505's round 2 opens *\"Round 1 fixed six "
    "findings and introduced two of its own\"*, and its round 4 caught a ReDoS "
    "that **round 3's own fix** introduced"
)

# The archive's copy of the retraction. Same reasoning as SKILL_NOT_WASTE, and
# more important here: the archive is where a future editor is sent to LOOK UP
# why this rule exists, so a retraction that rots out of it is worse than one
# that rots out of the skill.
ARCHIVE_RETRACTION = (
    "**Every load-bearing part of that is false**, and it was checked against "
    "`gh pr view <n> --json commits` only after it had been written into five "
    "places."
)

# --------------------------------------------------------------------------- #
# THE ATTRIBUTION GATE -- the axis the findings-keyed stop rule cannot see.
# --------------------------------------------------------------------------- #

SKILL_ATTRIBUTION_HEADING = (
    "### 🔴 ATTRIBUTION: a round that changes no PAYLOAD is auditing the "
    "LADDER, not the PR"
)

# The gate itself. Pinned whole because every number in it is load-bearing: TWO
# consecutive rounds (one is noise -- a round can legitimately fix only a guard),
# ZERO payload lines (not "few"), and the action is STOP rather than "consider
# stopping".
SKILL_ATTRIBUTION_GATE = (
    "**Two consecutive rounds whose fixes changed zero payload lines ⇒ the "
    "ladder has left the PR. Stop.**"
)

# 🔴 The clause that keeps this from being read as the rejected cap, or as a
# retraction of the two rules it sits under. Without it the next reader has three
# stop rules and no idea how they compose -- and the cheapest way to "simplify"
# that is to collapse them into a count, which is the rejected fix.
SKILL_ATTRIBUTION_NOT_A_CAP = (
    "⚠ **This does not retract the two rules above, and is not a cap in "
    "disguise.** #498's rounds were not wasted in the sense those rules deny — "
    "every one found something real. The waste is on a different axis: real "
    "findings *about scaffolding the ladder itself had just written*. The gate "
    "measures the fixes; it never counts the rounds."
)

# 🔴 THE MECHANISM, not just the decision. Pinning only the gate sentence leaves
# the command that produces its input free to be replaced with one that computes
# a different quantity -- which is not hypothetical: the first version of this
# gate shipped a CUMULATIVE range (`<first-audited-sha>..HEAD`) that a per-round
# condition cannot consume, and on the very ladder that motivated the rule it
# printed the same non-zero number for rounds 4 through 10 and never fired.
# Caught in review; these three pins are what stop it coming back.
SKILL_GATE_COMMAND = (
    "git log --numstat --format= --remerge-diff "
    "<the sha you audited THAT round>..HEAD --not <base>"
)
SKILL_GATE_PER_ROUND = (
    "🔴 **Per-round, and every commit the round actually made.** Anchored at "
    "round 1 the count stays non-zero forever once an early round touched "
    "payload"
)

# 🔴 THE CLASSIFIER, pinned as the INSTRUCTION it is -- not as the prohibition
# beside it. Measured: with only the "do not use a pathspec" sentence pinned,
# FOUR mutants rewrote the method and passed a green 11-test suite (a fifth
# survivor of that run, Z8, is the evidence family, caught by
# EVIDENCE_BASELINE_ROW) -- including one that deleted "read
# the list and name each one payload or scaffolding", one that flipped
# "Ambiguous is not zero" to its opposite (reversing the fail-safe direction),
# and one that reinstated the pathspec as the method while leaving the warning
# in place. A prohibition is not a method.
#
# The DEFINITION is the half that decides: keyed to file TYPE ("docs are not
# production") the gate reads zero for every round of a docs or skill PR and
# stops a working ladder -- and most of what this repo merges is exactly that
# (dated measurement, both selections, in the reference file; the figure moved
# 25 -> 24 within two hours as PRs merged, which is why the body states it
# qualitatively). Keyed to PAYLOAD it asks the question that matters: did this
# round change what the PR ships, or only the guards the ladder wrote?
SKILL_PAYLOAD_DEFINITION = (
    "🔴 **The unit is THIS PR's PAYLOAD, never a file extension.** Payload = "
    "what the PR exists to ship; scaffolding = the tests, fixtures and notes a "
    "round wrote to guard it. For a code change the payload is source and a "
    "`.md` is not — but **for a docs or skill PR the payload IS the `.md`**"
)
# 🔴 The WRONG-FLAG trap, pinned because reaching for it is the natural edit: a
# reader who wants to exclude a `merge main` reaches for `--no-merges
# --first-parent`, which looks equivalent and reads ZERO for a fix committed on
# a side branch and merged `--no-ff` -- the shape agent worktrees produce -- so
# the gate fires on a ladder whose payload is still moving. It also misses
# payload hand-written into a merge-conflict resolution. Both measured; the
# four-shape table is in the reference file. This constant shipped TWICE with
# wording that credited `--no-merges` with the job `--not <base>` actually does,
# which is why the sentence pinned here is the prohibition and not a rationale.
SKILL_WRONG_FLAGS_TRAP = (
    "Do **not** reach for `--no-merges --first-parent`: it looks equivalent and "
    "reads **0** for a fix committed on a side branch and merged `--no-ff` — "
    "the shape agent worktrees produce."
)
# 🔴 QUALITATIVE on purpose. This claim carried a number through two rounds and
# the number was wrong both times -- first `24` copied from a subagent's report
# without re-derivation, then a correctly-measured `25` that read `24` again two
# hours later because PRs kept merging. The dated measurement, both selection
# commands and the classifier live in the reference file; what is pinned here is
# the part that does not decay.
# 🔴 `<base>` and the failed-command rule. Both were found by measurement, not
# reasoning, and both were WIDENED by the next round's measurement:
#   - `origin/main` is only as current as the last fetch, so defining <base> as
#     that ref without saying to fetch it leaves the same defect one level down.
#     A base at the FORK POINT re-reports the whole bring-in (201 vs a truth of
#     1); one commit stale re-reports its tail -- "the whole bring-in" was a
#     property of the first fixture, not of staleness.
#   - "confirm it printed something" is NOT sufficient: a missing ref and a git
#     without `--remerge-diff` do give rc 128 and empty stdout, but an unwritable
#     object store makes `--remerge-diff` under-count, exit 0 and print a
#     PLAUSIBLE number, announcing the failure only on stderr. Hence rc 0 AND
#     silent stderr.
SKILL_BASE_DEFINITION = (
    "🔴 **`<base>` is the CURRENT tip you would merge into — `git fetch` it "
    "first.** A local `origin/main` is only as current as your last fetch, and "
    "a stale one re-reports upstream work as this round's payload: the whole "
    "bring-in from the fork point (201 where the truth was 1), its tail from "
    "one commit behind."
)
SKILL_FAILED_IS_NOT_ZERO = (
    "And **a zero you did not watch the command EARN is not a zero — require rc "
    "0, silent stderr, and a non-empty range.** A missing ref or a git without "
    "`--remerge-diff` exits 128 with empty output; an unwritable object store is "
    "worse, because `--remerge-diff` then under-counts, **exits 0 and prints a "
    "plausible number**, saying so only on stderr; and a range whose commits are "
    "simply not in this checkout yet prints nothing, silently, with rc 0."
)
SKILL_PAYLOAD_MEASUREMENT = (
    "**most of this repo's merged PRs ship no source file at all** (measured; "
    "the reference file dates it)"
)
SKILL_CLASSIFIER_METHOD = (
    "A round's fix touches a handful of files — read the list and name each one "
    "payload or scaffolding. **Ambiguous is not zero**: the gate does not fire, "
    "and the ladder continues."
)
# The pathspec prohibition. The MEASUREMENT behind it moved to the reference
# file (pinned there as EVIDENCE_PATHSPEC_MEASUREMENT) when the body needed the
# bytes for two worktree hazards; what stays here is the rule a reader acts on.
# Keep both: an earlier version of the measurement attributed all three
# directory names to `*test*` and was wrong on its own third example, which is
# why the numbers are pinned rather than paraphrased.
SKILL_GATE_NO_PATHSPEC = (
    "Nor will a pathspec do it — measured wrong in both directions on ordinary "
    "names (reference file)."
)

# The operator instructions this rule ships -- count them below rather than
# trusting a number here. Every one was added after a mutant deleted or inverted
# it with the suite green.
# 🔴 THE CHECKLIST ITSELF. Nine numbered items, pinned as one whole string --
# this is the operational core of the skill and it was UNPINNED through eight
# rounds. Measured: replacing the entire block with "**Audit for:** whatever
# seems off." left the suite green.
#
# It is also the MECHANISM behind the worst regression of this PR. Every round
# paid for new rules by shaving words out of exactly this region to stay under
# the byte budget, and one of those shaves deleted "rollback" from item 4 while
# the commit message claimed to have RESTORED it -- a multi-edit script whose
# later assertion failed before its write, so the restore never landed and the
# claim was written from intent rather than from the diff. Nothing observed it.
# A reader of that message would believe the word was there and not look.
#
# Pinning the block whole means the next shave fails loudly, in the same commit.
SKILL_AUDIT_CHECKLIST = (
    "**Audit for:** 1. **Risks** — what breaks in production. 2. "
    "**Regressions** — behaviour this silently alters or removes. 3. "
    "**Assumptions** — unstated preconditions that may not hold. 4. **Gaps** — "
    "error handling, edge cases, tests, migrations, rollback. 5. **Bugs** — "
    "logic/correctness defects, with file:line. 6. **Issues** — quality, "
    "maintainability, conventions. 7. **Behaviour changes** — observable "
    "changes in output/API/UX, intended or not. If the PR claims to revert "
    "behaviour, confirm it restores the pre-change state. 8. **Leaks** — "
    "secrets, PII, resource/handle/memory, over-broad permissions. 9. "
    "**Second-order consequences** — ripple effects on services, callers, data, "
    "cost."
)
# The stderr-capture rule: deleting it, AND inverting it to "fold stderr into the
# sum with 2>&1 so a failure cannot escape the count" (which reads plausible and
# defeats the silent-stderr third of the rule beside it), were both green.
SKILL_STDERR_CAPTURE = (
    "Keep stderr on the terminal — folding it into the sum with `2>&1` makes "
    "the one loud failure invisible."
)
# The regression shape this PR itself produced twice. 🔴 Pinned as the WHOLE
# bullet, not the phrase: a mid-sentence pin certifies the phrase is PRESENT,
# not that the instruction around it still tells the re-auditor to hunt.
# Measured with only the phrase pinned -- inverting the bullet head to "do NOT
# spend the round hunting regressions the fix round itself introduced", and
# deleting the other three regression shapes, were both GREEN with the phrase
# byte-identical. Same lesson as EVIDENCE_LESSON_BC, one file over.
SKILL_REWORD_REGRESSION = (
    "- hunt for **regressions the fix round itself introduced** — the guard "
    "that's too strict, the branch that's unreachable, the narrowed check that "
    "now rejects a legitimate case, **the rule reworded wider on one axis and "
    "narrower on another**;"
)
# 🔴 THE ENVIRONMENT BRIEF, pinned whole. It is payload -- what the operator is
# told to put in front of the auditor -- and it was reworded in SIX of #900's
# eleven commits (four of the eight FIX commits), every one a shave until the
# last, which restored what a shave had taken. Measured unpinned: gutting
# its lead to "do not bother briefing the auditor", deleting the zsh word-split
# warning, and re-applying the exact worktree-duty regression round 8 diagnosed
# (shifting "verify your worktree clean YOURSELF" back to "leave your worktree
# clean", i.e. from the operator to the auditee) were ALL green.
# 🔴 The two worktree hazards, pinned because each is a rule whose DELETION is
# silent and whose inversion reads plausible. `isolation: "worktree"` builds
# from the CWD's repo, so dispatching it for a PR in another repo worktrees the
# wrong one -- and the failure is quiet: the agent either reports a briefed file
# missing, or silently audits the wrong tree. Measured: with the caveat
# unpinned, replacing it with "use `isolation: \"worktree\"` for any repo" was
# green.
# 🔴 ROUND 13 REWORDED IT, and the reword is the finding rather than a shave.
# The old sentence paraphrased the recipe as "`git -C <that-repo> worktree add
# …`", which is a recipe that CANNOT BE RUN: `worktree add` resolves the PR's
# head branch against refs the clone already has, so it is `fatal: invalid
# reference`, rc 128, in any clone that has not fetched since the PR opened —
# and unconditionally for a FORK PR, whose branch is never in that clone's
# `origin`. Measured on real scratch repos, git 2.55.0. The generated brief
# now prints a namespaced `refs/pull/<n>/head` fetch plus a DETACHED
# `worktree add`, so this line stops carrying a second, stale copy of the
# recipe and routes to the one place that owns it.
SKILL_CROSS_REPO_WORKTREE = (
    "🔴 `isolation: \"worktree\"` worktrees the **cwd's** repo, not the PR's — "
    "for a PR in another repo run the recipe the brief's WHERE TO WORK section "
    "PRINTS, never a remembered one. It is a namespaced `refs/pull/<n>/head` "
    "fetch then a **detached** `worktree add`: naming the PR's head branch "
    "instead fails `rc 128` in any clone that has not fetched it, and always "
    "for a fork PR."
)
# 🔴 THE ROUTER TO THE ASSEMBLER. `scripts/audit-dispatch.py` exists because the
# invariant sections of a brief were being retyped every time and MEASURABLY
# lost: over one session's 14 dispatches, "do NOT git fetch" appeared in 5,
# "a clean round ending is correct" in 6, and the payload/scaffolding label in
# 10 — and the auditor at dispatch 8 fetched in a repo the brief had called
# read-only, because the clause first appears at dispatch 9.
#
# A `flows/`-style tool that nothing NAMES does not fire, so the router line is
# the whole delivery mechanism and deleting it costs the tool. Pinned WHOLE,
# including the refusal clause: a router that survives while "REFUSED" is
# reworded to "warns" describes a different tool — the silent downgrade of a
# delta re-audit into a blind full audit is precisely what the refusal exists to
# prevent, and a reader who believes it warns will not check.
#
# The path is the `~/workspace/devrc/...` form every other skill uses, for the
# reason test_doc_path_rot.py records: a skill is read with the cwd in some
# unrelated project, where a bare `scripts/x.py` resolves to nothing.
SKILL_ASSEMBLER_ROUTER = (
    "🔴 **Assemble the brief with "
    "`~/workspace/devrc/scripts/audit-dispatch.py <pr> [--round N]`** — it "
    "generates the range, the cross-repo worktree directive, checkout state "
    "and toolchain, reads the prior round's claims from the fenced "
    "`audit-claims` block ONLY, and carries the invariant clauses verbatim. "
    "**A delta round with no parseable block is REFUSED.**"
)
SKILL_ENVIRONMENT_BRIEF = (
    "**Brief the auditor on the environment, or it will report false findings** "
    "— a fresh worktree is not a working checkout, and an auditor hitting this "
    "cold blames the PR. Whichever apply: **submodules are unpopulated** in a "
    "new worktree (one made 4 test files \"fail to collect\"); **monorepo "
    "`node_modules`** may need linking per package, not just at the root; "
    "**whether the base branch is already red** and *at which file*; and that "
    "**zsh does not word-split unquoted parameters**, so `eslint $FILES` checks "
    "**zero** files and prints a confident PASS. Have it mutate only in a `cp "
    "-a` copy — **`rm -f <copy>/.git` first**, since a worktree's is a FILE "
    "pointing at the real git dir, so a commit in the copy lands on your branch "
    "— and verify your worktree clean yourself at the end."
)
SKILL_LEDGER = (
    "**Carry the ledger in every round's summary**: `round N · payload lines "
    "changed THIS round: X (since round 1: Y) · elapsed: Z`."
)
SKILL_FINDING_LABELS = (
    "**label every finding `behaviour` or `guard`, and separate shipped "
    "behaviour from scaffolding.**"
)

# The deployed path, not a repo-relative one: a devrc skill is READ from
# ~/.claude/skills/<name>/ by an agent whose cwd is some unrelated project, so a
# bare `reference/x.md` resolves against that cwd. Same rule as
# scripts/tests/test_doc_path_rot.py (1c).
#
# 🔴 Asserted PRESENT, not present-exactly-once. Every other pin here is a
# once-pin because a duplicated RULE is a rule that can drift; a duplicated
# POINTER is just a second door to the same file, and once-pinning it would turn
# "route to the evidence from the section that needs it" into a test failure.
SKILL_ROUTES_TO_EVIDENCE = (
    "`~/.claude/skills/audit-pr/reference/round-ladder-evidence.md`"
)

# The measurement the gate rests on, pinned in the evidence file so an eviction
# cannot leave the rule standing on an anecdote. The churn row IS the argument:
# rounds 4-10 changed 1,051 test lines and 0 payload lines.
EVIDENCE_CHURN_ROW = (
    "| **rounds 4–10** (`a82718b`…`7541bc1`) | **1,051** | **0** | **0** |"
)
# 🔴 The rows that ARE the argument for the current command, and the sentences
# that read them. The body delegates these numbers to this file, so the file has
# to be pinned too -- measured: with only the #498 pins in place, rewriting shape
# C's `0` to `51` (which deletes the entire reason the command changed), blanking
# shape A's row, INVERTING shape A's row so the table argues FOR the flag the
# body prohibits, rewriting the PR-population count, and negating either lesson
# bullet were ALL green. The first fix pinned two of those; the rest are here.
EVIDENCE_SHAPE_C_ROW = (
    "| C. the round's fix is 50 payload lines on a side branch, merged "
    "`--no-ff` | ~50 | 51 | **0** | 51 |"
)
EVIDENCE_PR_POPULATION_ROW = (
    "| `gh pr list --state merged --limit 40` (sorts by CREATED) | 24 | 16 | 7 "
    "| 1 |"
)
# 🔴 The pathspec measurement, demoted from the body in the worktree-hazards
# follow-up. The body now carries only the prohibition, so this file is where a
# reader checks WHY -- and an unpinned measurement in this file has already
# drifted twice (Z8, and shape A before U1).
EVIDENCE_PATHSPEC_MEASUREMENT = (
    "so `':!*test*'` swallows `pkg/attestation/verify.go` and "
    "`api/latest/handler.go`, `':!*spec*'` swallows `internal/inspector/"
    "scan.go`, and the two genuine tests survive as \"production\""
)
EVIDENCE_SHAPE_A_ROW = (
    "| A. clean `merge main` brings 200 upstream lines; the round's own fix is "
    "1 test line | 1 line, and it is a TEST ⇒ 0 payload | **201** | 1 | 1 |"
)
# The two sentences that tell a reader what the table MEANS. A table can be
# correct and useless if the lesson beside it is negated -- both of these were
# rewritable to the opposite claim with every row pin still green.
EVIDENCE_LESSON_A = (
    "**A** is why the range needs `--not <base>` — a two-dot diff attributes the "
    "whole upstream bring-in to this round, the gate never fires, and the ladder "
    "runs forever."
)
# 🔴 Pinned WHOLE, tail included. The first version stopped at "merged
# `--no-ff`" -- and the tail could then be rewritten to "a shape that
# essentially never occurs, so prefer it" with the suite green, which is the
# same inversion U1b exists to prevent, one clause later in the same bullet.
EVIDENCE_LESSON_BC = (
    "**B and C** are why `--no-merges --first-parent` is NOT the fix, though it "
    "looks like one and shipped as one for a round: it reads **0** for a fix "
    "committed on a side branch and merged `--no-ff` — the shape agent "
    "worktrees produce — so the gate fires and stops a ladder whose payload is "
    "still moving. `git show --numstat <merge>` is not the remedy either: it "
    "prints the first-parent diff, so on shape A it reports every upstream line "
    "as this round's work."
)
# 🔴 Z8's own constant: the corrected #498 baseline row. Pinned because rewriting
# it to 9999s was green while the correction sentence below still narrated having
# fixed it. (This comment sat orphaned from its constant for one round, which is
# the defect it exists to record -- do not insert new constants between them.)
EVIDENCE_BASELINE_ROW = (
    "| feature + rounds 1–3 (`bce0c0c`…`d2ec92d`) | 961 | 110 | 222 |"
)

# 🔴 The classifier is stated in the evidence file because it DECIDES the numbers
# above -- and because the first version of that table was wrong twice in the
# direction that flattered the argument (a mis-added test column, and 110 lines
# of release-notes markdown counted as production). Pinning the correction keeps
# the honest version from being tidied away into a clean-looking table.
EVIDENCE_TABLE_CORRECTION = (
    "🔴 This table has been wrong **three times**, each time in the direction "
    "that flatters the argument, which is why it now carries its shas and its "
    "classifier."
)

# 🔴 The half a reader will want to delete as "redundant with the retraction it
# does not contradict". #498 is NOT evidence of a round that found nothing; it is
# evidence of rounds that found real things about the ladder's own scaffolding.
# Losing this distinction re-opens the retracted "measured waste" story.
EVIDENCE_DIFFERENT_AXIS = (
    "**It does NOT contradict the \"not a wasted round\" retraction.** No #498 "
    "round ran and found nothing, so it is not an instance of the waste that "
    "retraction denies. Different axis."
)


def _norm(text: str) -> str:
    """Whitespace-normalised, so a re-wrap is not a failure but a reword is."""
    return " ".join(text.split())


def _read(path: Path) -> str:
    # errors="replace", not strict: a truncation landing mid-character (likely
    # in a file full of em-dashes and 🔴) would otherwise raise
    # UnicodeDecodeError before any assert runs, and the reader gets a traceback
    # instead of the message saying the file was truncated. Measured: truncating
    # the evidence file to exactly 1,500 B does this.
    return path.read_text(encoding="utf-8", errors="replace")


def _assert_pinned_once(path: Path, claim: str, what: str) -> None:
    body = _norm(_read(path))
    count = body.count(_norm(claim))
    # 🔴 The size goes in the MESSAGE, not only in a floor. A byte floor can
    # only ever sit below the LAST string it guards, and every pin added later
    # moves that boundary -- so no floor value distinguishes "reworded" from
    # "truncated" for long. (The previous attempt quoted exact offsets; they
    # were a commit out of date within one round, and the claim they carried was
    # false at the tip.) Reporting the file's actual size here distinguishes
    # them at any size, and needs no maintenance.
    size = len(path.read_bytes())
    assert count == 1, (
        f"\n\n{path.relative_to(REPO_ROOT)}: {what} is not present exactly once "
        f"as written (found {count}). The file is {size:,} B -- if that is far "
        "short of what it should hold, this is a TRUNCATION, not a reword.\n"
        f"  Expected verbatim (whitespace-normalised):\n    {_norm(claim)}\n\n"
        "  This is a WHOLE-STRING pin because the artifact is prose and a "
        "keyword guard is walkable by rewording (claude/RULES.md, "
        "spelled-guards).\n"
        "  If you reworded it deliberately, update the constant in "
        "scripts/tests/test_audit_ladder_stop_rule.py in the SAME commit.\n"
        "  If you DELETED it: the ladder loses its stop condition, and the only "
        "signal left is the auditor's VERDICT -- which devrc #804 proves is the "
        "wrong one (its rounds 5, 6 and 7 each returned 'safe to merge' while "
        "still reporting real defects).\n"
        "  If you replaced it with a round CAP: a cap was written first and "
        "rejected. devrc #505's round 1 introduced two of its own findings and "
        "round 3's fix introduced a ReDoS, both caught by later rounds -- a cap "
        "at 2 or 3 ships them. Read the `audit-fix-resets-gate` section of "
        "claude/RULES-ARCHIVE.md before re-deriving that."
    )


# --------------------------------------------------------------------------- #
# GUARD THE GUARD
# --------------------------------------------------------------------------- #

def test_the_guarded_files_exist_and_are_substantial():
    """A moved or emptied file must fail loudly, not pass vacuously.

    Every assertion below is a substring count. Against a missing file the read
    raises (which is a failure), but against a TRUNCATED one a count of 0 is
    indistinguishable from a reword, and the messages above would send a reader
    hunting for a sentence in a file that no longer holds anything. Size floors
    are deliberately crude -- they only have to separate "the document" from
    "a stub".
    """
    for path, floor in (
        (RULES_MD, 20_000),
        (ARCHIVE_MD, 20_000),
        (SKILL_MD, 3_000),
        # Added with the attribution gate. Measured without it: truncating the
        # evidence file to 0 B reported "the #498 churn row ... is not present
        # exactly once as written (found 0)" and told the reader they had
        # REWORDED a sentence in a file that was empty.
        #
        # 🔴 The floor is a SANITY check, not the truncation guard, and it sits
        # far below the last string it guards ON PURPOSE. Chasing that offset is
        # a losing game: every pin added later moves it, and an earlier attempt
        # to quote exact byte positions here was a commit out of date within one
        # round -- the numbers it named were the PREVIOUS tip's, and the claim
        # they carried was false at HEAD. `_assert_pinned_once` reports the
        # file's size in its own failure message instead, which separates
        # "reworded" from "truncated" at any size and needs no maintenance. This
        # floor only has to separate "the document" from "a stub".
        (EVIDENCE_MD, 1_500),
    ):
        assert path.is_file(), (
            f"{path} not found -- every pin in this module would fail with a "
            "misleading 'reworded' message. If the file MOVED, re-point the "
            "constant at the top of this module."
        )
        size = len(path.read_bytes())
        assert size >= floor, (
            f"{path.relative_to(REPO_ROOT)} is {size:,} B, under the {floor:,} B "
            "sanity floor -- it looks truncated, so the pins below would report "
            "a reword when the real problem is a missing document."
        )


# --------------------------------------------------------------------------- #
# THE STOP RULE
# --------------------------------------------------------------------------- #

def test_rules_md_states_the_stop_condition():
    _assert_pinned_once(
        RULES_MD, RULES_STOP_CLAUSE, "the audit-ladder stop clause"
    )


def test_audit_pr_skill_states_the_stop_condition():
    _assert_pinned_once(SKILL_MD, SKILL_HEADING, "the stop-rule heading")
    _assert_pinned_once(SKILL_MD, SKILL_STOP_BODY, "the stop-rule body")


def test_the_prose_escape_hatch_demands_its_rationale_IN_THE_SUMMARY():
    """🔴 The escape hatch must produce an ARTIFACT, not a state of mind.

    This is the only clause that ends a ladder WITHOUT a clean round, so a
    reader has to be able to tell that it was used. "Can NAME" alone is
    unobservable: the report looks identical whether the ladder converged or
    the hatch was invoked over unfixed 🟡s.

    Pinned as a WHOLE normalised string, per `claude/RULES.md` (spelled-guards):
    the artifact is prose, so a keyword guard is walkable by rewording -- which
    is precisely what happened. #1133 reworded this sentence, adding the NAME
    precondition while deleting "stated in the round's summary and not left
    implicit", and no test noticed.
    """
    _assert_pinned_once(
        SKILL_MD,
        SKILL_PROSE_ESCAPE_STOP,
        "the prose-payload escape hatch's summary requirement",
    )


def test_the_rejected_cap_is_recorded_where_the_rule_lives():
    """🔴 The counter-evidence is part of the rule, not commentary.

    "Stop earlier" without "and not by capping the count" is an invitation to
    re-derive the rejected fix. The skill carries the rejection inline; the
    archive carries the measurement the rejection rests on.
    """
    _assert_pinned_once(SKILL_MD, SKILL_NOT_A_CAP, "the not-a-cap clause")
    _assert_pinned_once(
        ARCHIVE_MD, ARCHIVE_REJECTED_CAP, "the rejected-cap evidence"
    )


def test_the_rule_is_justified_by_the_verdict_gap_not_by_claimed_waste():
    """🔴 The justification is guarded because it was WRONG once already.

    The first version of this work justified the rule as a fix for measured
    waste ("#804's rounds 5-8 all returned safe to merge, four ran anyway"). It
    was false, and by the time it was checked it sat in five files. What is true
    and verifiable is narrower: a "safe to merge" VERDICT is not the stop signal,
    because #804's rounds 5, 6 and 7 each returned one while still reporting real
    defects.

    Both halves are pinned: the surviving evidence, and the retraction. Pinning
    only the evidence would let the retraction be deleted, and a deleted
    retraction is how the false story gets rewritten from memory.
    """
    _assert_pinned_once(
        SKILL_MD, SKILL_VERDICT_NOT_STOP, "the verdict-is-not-the-stop-signal claim"
    )
    _assert_pinned_once(SKILL_MD, SKILL_NOT_WASTE, "the not-a-wasted-round retraction")
    _assert_pinned_once(ARCHIVE_MD, ARCHIVE_RETRACTION, "the archive's retraction")


def test_the_attribution_gate_is_stated_with_its_not_a_cap_qualifier():
    """🔴 The gate the findings-keyed stop rule cannot supply on its own.

    A delta round diffs `<audited-sha>..HEAD`, and a fix round writes new guards
    INTO that range -- so the ladder manufactures its own next round's findings
    and a stop condition keyed to findings has no exit. Measured on `civitai/cli`
    #498 (2026-08-26): ten rounds, 5 h 32 m, 77% of the session's output tokens,
    and the fix commits for rounds 4-10 changed 1,051 lines of test code and ZERO
    lines of payload. No round was ever clean, so the rule above was
    never once eligible to fire.

    All three constants are pinned together on purpose. The gate without its
    qualifier reads as a third stop rule of unknown standing next to two others,
    and the cheapest way to reconcile three is to collapse them into a count --
    which is the cap this module already rejects.
    """
    _assert_pinned_once(
        SKILL_MD, SKILL_ATTRIBUTION_HEADING, "the attribution-gate heading"
    )
    _assert_pinned_once(SKILL_MD, SKILL_ATTRIBUTION_GATE, "the attribution gate")
    _assert_pinned_once(
        SKILL_MD,
        SKILL_ATTRIBUTION_NOT_A_CAP,
        "the attribution gate's not-a-cap / not-a-retraction qualifier",
    )


def test_the_attribution_gate_comes_after_the_rule_it_bounds():
    """A RELATIONSHIP pin, and the reachability control for the one above.

    Order carries meaning here: the attribution gate BOUNDS the findings-keyed
    stop rule, it does not replace it. A reader who meets it first meets "stop
    when the fixes stop touching payload" with no "keep going while
    rounds keep finding things" in view -- which is the one-audit-is-enough
    failure `audit-fix-resets-gate` exists to prevent.

    This assertion is what fails when the section is MOVED with its text
    byte-identical; every whole-string pin above stays green in that mutant, so a
    red here proves this test executes rather than restating its neighbour.
    """
    body = _norm(_read(SKILL_MD))
    stop_at = body.find(_norm(SKILL_HEADING))
    attribution_at = body.find(_norm(SKILL_ATTRIBUTION_HEADING))

    assert stop_at != -1 and attribution_at != -1, (
        "one of the two headings is missing -- the pins above report which; this "
        "test only orders them."
    )
    assert stop_at < attribution_at, (
        "\n\nclaude/skills/audit-pr/SKILL.md: the ATTRIBUTION gate now precedes "
        "the clean-round stop rule.\n"
        "  The gate bounds that rule; it does not replace it. Read first, it "
        "says 'stop when the fixes stop touching payload' with no "
        "'keep going while rounds keep finding things' in view -- and a single "
        "audit round is how a completely inert feature shipped past 428 green "
        "tests (claude/RULES-ARCHIVE.md, audit-fix-resets-gate).\n"
        "  Restore the order, or re-point this test deliberately and say in the "
        "commit how a reader still meets the bounding rule first."
    )


def test_the_attribution_measurement_survives_where_the_skill_routes_to_it():
    """🔴 The rule is only as good as the measurement it rests on.

    The skill body carries one line of it; the rest was demoted to the skill's
    reference/ dir to pay for the rule's bytes. Two ways that goes wrong, both
    pinned: the evidence file is deleted or emptied (the rule then rests on an
    anecdote), or the body routes to it with a path that does not RESOLVE for
    the reader -- a devrc skill is read from ~/.claude/skills/<name>/ by an agent
    whose cwd is some unrelated project, so a bare `reference/x.md` opens
    nothing (scripts/tests/test_doc_path_rot.py, rule 1c).
    """
    assert EVIDENCE_MD.is_file(), (
        f"{EVIDENCE_MD.relative_to(REPO_ROOT)} is missing -- the attribution "
        "gate's measurement lives there and the SKILL.md body only summarises "
        "it in one line. Restore it, or move the measurement back into the "
        "body and re-point this test."
    )
    body = _norm(_read(SKILL_MD))
    assert body.count(_norm(SKILL_ROUTES_TO_EVIDENCE)) >= 1, (
        "\n\nclaude/skills/audit-pr/SKILL.md no longer routes to "
        f"{EVIDENCE_MD.name} by its DEPLOYED path.\n"
        "  The body summarises the measurement in one line; without a path a "
        "reader cannot reach the rest. It must be the ~/.claude/skills/... "
        "form -- this skill is read with the cwd in some unrelated project, "
        "where a bare `reference/x.md` opens nothing.\n"
        "  More than one route is fine and is why this is not a once-pin."
    )
    _assert_pinned_once(EVIDENCE_MD, EVIDENCE_CHURN_ROW, "the #498 churn row")
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_BASELINE_ROW, "the #498 baseline churn row"
    )
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_SHAPE_C_ROW, "the shape-C range measurement"
    )
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_SHAPE_A_ROW, "the shape-A range measurement"
    )
    _assert_pinned_once(
        EVIDENCE_MD,
        EVIDENCE_PATHSPEC_MEASUREMENT,
        "the pathspec counter-measurement",
    )
    _assert_pinned_once(EVIDENCE_MD, EVIDENCE_LESSON_A, "the shape-A lesson")
    _assert_pinned_once(EVIDENCE_MD, EVIDENCE_LESSON_BC, "the shape-B/C lesson")
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_PR_POPULATION_ROW, "the PR-population measurement"
    )
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_TABLE_CORRECTION, "the churn-table correction"
    )
    _assert_pinned_once(
        EVIDENCE_MD, EVIDENCE_DIFFERENT_AXIS, "the different-axis distinction"
    )


def test_the_gate_pins_its_MECHANISM_not_only_its_decision():
    """🔴 A decision pin does not protect the command that feeds it.

    Measured on this module before these three pins existed: replacing the whole
    `git diff --numstat ...` instruction with `echo 0` left all 9 tests GREEN.
    The gate's verdict sentence was pinned; the quantity it consumes was not, so
    the instruction could be swapped for one that never fires and the suite
    would say the rule was intact.

    That is not a hypothetical. The first version of this gate shipped a
    CUMULATIVE range, which a per-round condition cannot consume -- on #498 it
    prints the same non-zero number for rounds 4 through 10, and the second
    shipped `--no-merges --first-parent`, which reads ZERO for a fix merged from
    a side branch. One pin per constant below -- count them rather than trusting
    a total here, which is this PR's own rule and was violated twice: the number
    said THREE when the test made seven, was corrected to SEVEN in the same
    commit that made it nine, and each part it names -- the range, what `<base>`
    is, what counts as payload, how lines are classified, which flags are the
    trap, the measurement under it -- has been found wrong on its own.
    """
    _assert_pinned_once(SKILL_MD, SKILL_GATE_COMMAND, "the gate's command")
    _assert_pinned_once(SKILL_MD, SKILL_GATE_PER_ROUND, "the per-round rule")
    _assert_pinned_once(
        SKILL_MD, SKILL_PAYLOAD_DEFINITION, "the payload definition"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_CLASSIFIER_METHOD, "the classifier METHOD"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_PAYLOAD_MEASUREMENT, "the dated payload measurement"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_WRONG_FLAGS_TRAP, "the --no-merges/--first-parent trap"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_GATE_NO_PATHSPEC, "the pathspec counter-evidence"
    )
    _assert_pinned_once(SKILL_MD, SKILL_BASE_DEFINITION, "what `<base>` is")
    _assert_pinned_once(
        SKILL_MD, SKILL_FAILED_IS_NOT_ZERO, "the failed-command-is-not-zero rule"
    )


def test_the_operator_instructions_the_gate_depends_on_are_pinned():
    """The instructions a reader ACTS on -- count the asserts, not this sentence.

    Each was added after a mutant deleted or inverted it with the suite green.

    Neither is decorative. The ledger's X IS the number the gate reads -- it
    carries the per-round count into the summary a human actually sees, which is
    what turns the gate from a command someone must remember to run into a field
    they would notice missing. (An earlier format carried only the cumulative
    figure; that is the quantity the gate cannot consume.) The `behaviour`/
    `guard` labels are what let a reader see that a round's findings were all
    about scaffolding. Measured: deleting either left all 9 tests green before
    these pins.
    """
    _assert_pinned_once(SKILL_MD, SKILL_LEDGER, "the per-round ledger")
    _assert_pinned_once(SKILL_MD, SKILL_FINDING_LABELS, "the finding labels")
    _assert_pinned_once(
        SKILL_MD, SKILL_AUDIT_CHECKLIST, "the nine-item audit checklist"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_STDERR_CAPTURE, "the stderr-capture rule"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_REWORD_REGRESSION, "the reworded-rule regression shape"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_ENVIRONMENT_BRIEF, "the environment brief"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_CROSS_REPO_WORKTREE, "the cross-repo worktree hazard"
    )
    _assert_pinned_once(
        SKILL_MD, SKILL_ASSEMBLER_ROUTER, "the audit-dispatch.py router"
    )


def test_the_stop_rule_shares_a_bullet_with_the_rule_it_bounds():
    """A RELATIONSHIP pin, not a component pin.

    The stop clause and "Budget for SEVERAL rounds" are counterweights: read
    apart, each is wrong. The several-rounds mandate alone leaves the ladder with
    no stop signal but the verdict; the stop condition alone reads as "one audit
    is enough", which
    is the failure `audit-fix-resets-gate` exists to prevent (a fix that shipped
    a completely inert feature past 428 green tests and a second clean audit).

    RULES.md is one bullet per line, so "same line" IS "same bullet". This fails
    if either clause is moved into a bullet of its own -- deliberately. If you
    restructure the section, re-point this test rather than deleting it, and say
    in the commit how a reader still meets both claims together.
    """
    lines = [_norm(ln) for ln in _read(RULES_MD).splitlines()]
    stop = _norm(RULES_STOP_CLAUSE)
    several = _norm(RULES_SEVERAL_ROUNDS)

    holding_both = [ln for ln in lines if stop in ln and several in ln]
    assert len(holding_both) == 1, (
        "\n\nclaude/RULES.md: the audit-ladder stop clause and the "
        "'Budget for SEVERAL rounds' mandate are no longer in the same bullet "
        f"(found {len(holding_both)} lines holding both).\n"
        "  They are counterweights. Several-rounds alone leaves the ladder with "
        "no stop signal but the auditor's verdict, which devrc #804 shows is the "
        "wrong one; the stop condition alone reads as 'one audit is enough', "
        "which is the exact failure the bullet exists to prevent.\n"
        "  Restore them to one bullet, or re-point this test deliberately and "
        "explain in the commit how a reader still meets both claims together."
    )
