# Round-ladder evidence

Case histories behind the ladder rules in `../SKILL.md`. Facts only — the RULES live in the skill
body, and nothing here decides whether one applies. Dated because each is a measurement, not a
principle.

---

## 2026-08-26 · `civitai/cli` #498 — ten rounds, and the axis the stop rule cannot see

The measurement behind **ATTRIBUTION: a round that changes no PRODUCTION code is auditing the
LADDER, not the PR**. Session `4719a2f0`, `feat/whoami-json-profile-fields`.

| | |
|---|---|
| rounds | 10 (first audit 17:50 → round 10 dispatched 23:22) |
| elapsed | 5 h 32 m |
| share of session | 1,312 of 1,756 transcript rows (75%) |
| main-thread output tokens | 321k of 419k (77%); 100M of 115M cache reads |
| after the last production change | 171k output tokens, 4 h 10 m |
| rounds that returned CLEAN | **zero** |

Churn attribution, from the branch's own fix commits (`git show --numstat`, test files vs the rest):

| rounds | test/guard lines | production lines |
|---|---|---|
| feature + rounds 1–3 | 1,061 | 332 |
| **rounds 4–10** | **1,002** | **0** |

The last production change was round 3's `d2ec92d` (32 lines — a fifth render path that was
structurally unreachable from the golden table, a real find). Rounds 4–10 each found something real
too; all of it concerned guards that an earlier round of the same ladder had written.

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
(numbered delta re-audit dispatches, `subagents/` excluded): **110 sessions, 440 rounds, 84 of those
sessions in the preceding 14 days**; mean deepest round 4.0; 34% ran ≥5 rounds; distribution
3→38, 4→22, 5→21, 6→9, 7→4, 8→2, 10→1. The ladder is a dominant workflow, not an occasional one —
which is what makes a per-round gate worth its bytes.

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

## Where the rest lives

- The **rejected numeric cap** and devrc #505's ReDoS-introduced-by-the-fix evidence: stated inline
  in `../SKILL.md` (it is load-bearing there) and in `claude/RULES-ARCHIVE.md` →
  `audit-fix-resets-gate`.
- The retraction of the original "measured waste" justification: `claude/RULES-ARCHIVE.md` →
  `audit-fix-resets-gate`, and pinned by `scripts/tests/test_audit_ladder_stop_rule.py`.
