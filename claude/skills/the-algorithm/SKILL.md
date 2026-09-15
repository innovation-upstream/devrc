---
name: the-algorithm
description: "Apply the 5-step algorithm — question requirements, delete, simplify, accelerate, automate, in that order — before adding or changing any guard, test, rule, process, or automation. Use for: 'do we need this test/guard/rule', over-engineered or wasteful verification, pruning tests/rules, or a PR that adds a guard. Not product features."
---

# The algorithm — guards, tests, rules, processes

Five steps, IN ORDER. The order is the whole point: most wasted verification
exists because someone simplified, accelerated, or automated a thing that
should have been deleted. One pass, applied to the change in front of you —
this is an in-the-moment gate, not a bulk retroactive audit.

## 1. Question every requirement — name the maker
- Every requirement names the incident it prevents AND who asked. RULES.md
  bullets carry archive anchors; a guard TEST must carry the same in its
  docstring. A requirement with neither is unowned.
- "A prior agent session required it" is not a maker — that is a department.
  The most dangerous requirements are the smart-sounding self-issued ones.
- Ask recurrence vs. always-on cost: how often does the incident happen, and
  what does one occurrence cost versus the guard's standing cost (maintenance,
  the doc-edit tax it creates, re-verification loops, operator attention)?
  A guard for a once-a-year nuisance that taxes every change is a net loss.

## 2. Delete
- Delete everything step 1 did not defend. Deletion is reversible (git);
  re-adding is cheap. Do not soften into "simplify" what should not exist.
- Expect to add back ~10% of what you deleted. If you add nothing back, you
  did not delete enough.
- Recurring delete-classes in devrc: tests that pin PROSE (doc claims, HTML
  markers, path lists), mutation batteries for meta-guards, guards for
  retired subsystems, per-target floors the scoped runner already suspends.

## 3. Simplify what survived
- One predicate, one place — consolidate near-duplicates (skills, floors,
  checkers) before tuning any of them.

## 4. Accelerate what survived
- Test runs default to `scripts/scoped-tests.sh`; a full `scripts/gate.sh`
  run is a judgement call, not a habit. Speed comes after shrinkage — never
  tune a runner to make a guard you should have deleted cheaper.

## 5. Automate last
- The survivors (deadman, drift-check, main-green-check) earned their place
  through named incidents. A NEW automation must answer the same step-1
  questions: what incident, what recurrence, who hears it, what does it cost
  every run?
- 🔴 The fix for over-guarding is NEVER another guard. No ratchet on test
  growth, no guard-audit guard, no meta-meta layer. If step 2 did its job,
  step 5 has almost nothing left to automate.
