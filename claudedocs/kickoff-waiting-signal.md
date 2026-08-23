# Kickoff: close the "is anything waiting on me" gap

**Date:** 2026-08-12
**Origin:** two blind dogfood runs of `session-manager`, same prompt, before and after #412/#413
**Status:** ready to dispatch — 3 units, one open decision

---

## Why these four

Both dogfood runs were given only the question *"what is being worked on, what's the status,
is anything waiting on me?"* and the nudge *"prefer existing purpose-built tooling."* Neither
was told which tools exist.

**Run 2 (post-fix) confirmed the fixes landed** — the `idle` bucket no longer merges agents
with bare shells, and the agent never had to fall back to a `/proc` walk to separate the two
populations. Cost fell 94k → 84k tokens.

**It also showed the headline question is still unanswered.** Every actionable finding in run
2's answer came from **13 manual `tail` calls reading English prose** — ~30% of its tokens —
because no tool exposes "this window is waiting on a human." The agent could only afford 13
of 40 windows, so the answer is a sample, not a sweep.

And run 2 **missed the single highest-risk item run 1 found**: 11 pending clawgate approvals,
four of them credential-exposure or cross-user-data-leak. Verified still pending at the time
of writing (`~/.cache/bar-status/clawgate.json` → `count: 11`).

---

## Unit A — a `waiting` signal in `session-manager` (the whole ballgame)

`status: idle` currently merges four states that need different actions:

| real state | example from run 2 | what you'd do |
|---|---|---|
| finished cleanly, awaiting next instruction | `workbench scratch17:1` | give it work |
| **asked a direct question** | `laptop 14:1` — *"Want me to write the PHI-track handoff doc?"* | answer it |
| **hard-blocked on a modal** | `laptop scratch7:5` — parked on `/rate-limit-options` | press a key |
| **out of context** | `laptop 14:2` — `ctx 0%`, 610k reclaimable | `/clear` |

The last three are *stuck*; none is idle in any useful sense.

**Build:** a `waiting_probable` boolean per row, plus **the matched line** so a consumer can
judge it. Cheap signals that were sufficient for a human reading the same panes: a trailing
`?` on the last assistant line, a `❯ 1./2./3.` selection menu, `ctx: 0%`.

Even a crude version replaces ~30% of an agent's calls *and produces a better answer*, because
it covers all 40 windows instead of the 13 a tail-budget allows.

### 🔴 Open decision — resolve BEFORE building
Run 2 found four windows showing **dimmed text at the `❯` prompt** — each a short imperative
instruction — and reported them as unsent instructions one Enter away. It flagged honestly
that it could not distinguish typed-but-unsubmitted from placeholder text.

> 🔴 Two of those four were quoted here verbatim until 2026-08-17. They were **real operator
> drafts**, and this repo is PUBLIC. See
> `claude/skills/session-manager/reference/waiting-signal.md` → *NEVER PASTE A CAPTURED DRAFT
> INTO A COMMITTED FILE*: a count and a shape is the whole of what may be written down.

There is a **third reading it did not consider**: Claude Code renders *queued* messages dimmed
— typed while the agent is working, already submitted, processed when the turn ends. If that
is what these are, they are **not waiting on anyone** and a detector keying on them would
manufacture false "waiting" rows at exactly the moment the operator is most likely to trust it.

**Settle what dimmed prompt text means before keying any detector on it.** If it cannot be
settled from outside the pane, say so and exclude it from the signal rather than guessing.

### 🔴 Verification — a classifier that returns 0 is indistinguishable from a broken one
- **Positive control is mandatory.** Construct a fixture that MUST produce a non-zero
  `waiting` count, watch the number move, and report the pair — "N on the positive control,
  M under test". A bare zero is not evidence.
- **Negative control**: a window that is genuinely just idle must NOT be flagged.
- Mutation-test each signal separately (question-mark, menu, ctx-zero) and confirm each dies
  on its own assertion, reachable rather than shadowed by an earlier check.
- `scripts/session-manager` is extensionless and loaded via `SourceFileLoader`, whose `.pyc`
  cache keys on `(int(mtime), size)` — a fast mutate→restore loop silently runs the previous
  mutant's bytecode. `PYTHONDONTWRITEBYTECODE=1`, `python3 -B`, `-p no:cacheprovider`, fresh
  tree per mutant.

---

## Unit B — restore a path to the clawgate queue

Run 2 rejected `agent-ops` on the strength of `claude/skills/session-manager/SKILL.md:8-10`:

> the **queryable counterpart to `agent-ops`**, which is the always-on local tmux popup with
> no JSON API and no cross-host reach.

Every word is true. `agent-ops` is also the **only** tool that surfaces the clawgate approval
queue, via its BLOCKED-ON-ME section — so an accurate cross-reference steered the agent away
from the highest-stakes signal on the machine. Run 1 found `agent-ops` only by listing
`scripts/` on a hunch.

**Fix either way, but fix it:** have `session-manager` surface the clawgate pending count
itself (the bar cache at `~/.cache/bar-status/clawgate.json` is already a cheap read), **or**
rewrite the cross-reference so it points *toward* `agent-ops` for blocked-on-me rather than
away. Do not leave a doc line whose measured effect is losing signal.

⚠ If you read the bar cache: its `detail` string truncates (`"11 task(s) awaiting: #171,
#170, #169, #168, #160, #165"` names 6 of 11), and the dropped set has previously included
`ready_for_review` items — finished work awaiting review. Use `count`, not `detail`, or call
the API.

---

## Unit C — `standup` reports a fleet verdict from a 9-repo scan

`claude/skills/standup/standup.sh` prints:

```
STATUS: PRs 70 open (0 ready, 0 red)
```

Both zeroes are true **within its 9 local repos** and false as the fleet claim the skill's own
description makes (*"One-shot fleet-wide status sweep"*). Run 2 went outside the tooling with
`gh search prs --author @me` and found **35 flagged PRs across 10 repos standup cannot see** —
including `vetr-app`, where **all 5 open PRs are red** and one is literally *"unbreak the
permanently-red playwright gate."*

A reassuring zero that is wrong is the dangerous direction: it reads as "nothing to do."

**Fix:** either widen the scan, or print the scope inline (`PRs 70 open across 9 repos …`). If
widening, note `ZacxDev/homebrew-tap` contributed 31 of run 2's 100 hits and is almost
certainly release-bot noise — filter it or it will swamp the signal.

---

## Unit D — four small ones

1. **Default fuzzyclaw off** (`--fuzzyclaw` to opt in). Run 2 measured `29 live of 401 files,
   363 stale, 9 slot-mismatched`, and **every live row read `paused`** — including
   `scratch2:4`, which was demonstrably running an agent. 29 table rows, zero contribution to
   the answer, on a source `CLAUDE.md` marks UNTRUSTED.
2. **`session-manager tail` emits raw ANSI; add `--plain`.** Every consumer pipes it through
   the same `sed` strip. This is the same defect class fixed in `agent-ops` in #413 — the fix
   did not generalise to the sibling.
3. **Fix the `initiative-scan` skill's path.** It names a command that does not exist; the
   real file is `scripts/session-analysis/initiative-scan.py`, in a *different tree* from the
   `scripts/initiatives/` the skill index points at. Run 2 burned **7 of ~26 calls** finding
   it, by grepping `standup.sh` for `ISCAN=`. Also note in the skill that `momentum: active`
   means *a handoff doc was touched*, not *work is moving* — run 2 measured `open_prs: []` and
   `commits: 0` for ~all 33 "active" initiatives.
4. **Document the JSON shape.** `SKILL.md` details `summary` and never says rows live at
   `hosts.<host>.windows`. One line; cost run 2 a wasted call.

**Also worth doing while in there:** the skill body is 261 lines and a consumer needs ~10
(invocation, row path, the two caveats, exit codes). The rest — exit-code rationale,
slot-conflict archaeology — is maintainer history that belongs in `reference/`. Run 2 read all
261 and said so.

---

## Suggested dispatch split

| unit | files | why separate |
|---|---|---|
| **A** | `scripts/session-manager`, its tests + skill | the substantive one; has an open decision and a mandatory positive control |
| **B + D** | `scripts/session-manager`, `claude/skills/{session-manager,initiative-scan}/` | doc + small-flag work, low risk |
| **C** | `claude/skills/standup/` | different tool, different reviewer context |

A and B+D touch the same file — **sequence them or expect a conflict**, and note
`TARGET_FLOORS`' `scripts/tests` entry is re-pinned by every branch that adds a test, so
whichever lands second must re-measure on the merged tree rather than compute across the two
sides. `rerere` is enabled globally and has mis-replayed exactly that conflict before.

## What NOT to do
Do not extend `session-manager` to *answer* questions, kill windows, or send keys. It is
read-only by construction and that is why both dogfood runs could use it safely on a live
machine with 40+ windows of real work. A `waiting` flag is a read; acting on it is not.
