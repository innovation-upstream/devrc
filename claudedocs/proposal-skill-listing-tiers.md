# Proposal: tier the skill listing instead of shrinking it — 2026-08-24

Companion to `handoff-skill-listing-budget.md`. That handoff concluded the listing "cannot be
closed by editing text" and that closing it needs **fewer skills**. Reading the installed
Claude Code binary changes that conclusion: there is a supported mechanism that makes listing
cost **per-skill opt-in**, and it fixes the growth rate rather than the current total.

Everything below was measured against `claude-code-2.1.232` as installed
(`/nix/store/43fmrchzsg9g110qqk9n7a5z0icsf0qh-claude-code-2.1.232/bin/.claude-wrapped`) and
against the live usage counters in `~/.claude.json`. Nothing here is from documentation alone.

---

## 1. Corrections to the handoff's model

🔴 **The budget is characters, computed with a fixed ×4 constant. There is no tokenizer.**

```js
Ker(ctx, o=_4p) { … return Math.floor((ctx ?? 200000) * o * fraction) }
_4p = 4        // chars per token, a CONSTANT
apv = 0.01     // default skillListingBudgetFraction
cpv = 1536     // default skillListingMaxDescChars
lpv = 200000   // context fallback
```

So the budget is exactly `contextWindow × 0.04` characters:

| context | budget |
|---|---|
| 200k | **8,000 chars** |
| 1M | **40,000 chars** |

The handoff's central argument — a break-even divisor of >10.0 chars/token, "no tokenizer makes
it fit" — is **moot**. Claude Code never tokenizes the listing; it counts characters against a
constant. The conclusion (it does not fit at 200k) survives; the reasoning behind it does not,
and should not be re-derived.

🔴 **The gate under-measures.** Real entry cost is `len(name) + 4 + min(len(desc), 1536)`, joined
by one newline per entry — the listing renders as `- name: description`.
`test_skill_descriptions.py` measures `len(name) + len(desc)`, so it undercounts by
**194 chars** (4/entry + 38 separators). Live total is **13,685**, not 13,491 — **1.71×** over
the 200k budget, not 1.55×. The error is in the conservative direction, but the docstring states
a number that is not the one the harness uses.

🔴 **Bundled (built-in) skills are PROTECTED and spend the budget first.** In the truncation
pass, `f(b) = isBundled(b) || isNameOnly(b)` marks entries exempt; only the remainder competes
for what is left. The handoff treated built-ins as "additional, not devrc's to edit" — they are
worse than additional, they are *senior*. Estimated ~6,000 chars, leaving ~2,000 of the 8,000
for all 39 devrc skills on a 200k session.

**What overflow actually does** (`b4p`): assume every non-exempt skill is reduced to name-only,
compute the leftover `g`, then walk skills in **descending priority** buying descriptions back
while they fit. It is all-or-nothing per skill — a skill keeps its full description or loses it
entirely.

**Priority decays**, which the handoff did not know:

```js
Jer(name) = usageCount × max(0.5 ^ (daysSinceLastUse / 7), 0.1)
```

A **7-day half-life**, floored at 0.1×. Never used ⇒ 0.

---

## 2. The mechanism the handoff missed

`skillOverrides` in `settings.json`, keyed by skill name, four states:

| value | listing cost | model can auto-fire it | `/name` works |
|---|---|---|---|
| absent / `on` | `name + 4 + desc` | yes | yes |
| **`name-only`** | **`name + 2`** | no — it sees only the name | **yes** |
| `user-invocable-only` | 0 | no | yes |
| `off` | 0 | no | no |

`name-only` is the load-bearing one: the skill stays installed, stays fully functional, stays
`/name`-invocable, and the model still sees that it exists — it just does not carry routing
prose. **~12 chars instead of ~350.**

Also present and verified in the binary: `skillListingBudgetFraction`, `skillListingMaxDescChars`,
`disableBundledSkills` (+ `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS`), `disable-model-invocation`
frontmatter, and `SLASH_COMMAND_TOOL_CHAR_BUDGET`.

> Instrument note: a first grep of the install returned **0** for every one of these keys — it
> was reading the 20 KB wrapper stub, not the 323 MB `.claude-wrapped` payload. The positive
> control (`enabledPlugins`, `statusLine`) also returned 0, which is what exposed it. Do not
> quote a zero from that path without the control.

---

## 3. The argument that decides it

**Skills are added ~15×/month.** Post-migration organic additions: clickup (08-13), signal
(08-17), window-triage (08-19), prune-index (08-21), check-clickup-addressed (08-22) — 5 in 10
days. At a 350-char mean that is **+5,250 chars/month**; the listing would double in ~3 months.

Retiring the three dead skills buys 772 chars ≈ **4.5 days of growth**. The ratchet's
"evict in the same commit" rule, at that rate, demands ~15 evictions/month. That is a treadmill.

**Tiering changes the growth rate, which is the only thing that scales:**

| | per new skill | per month at 15/mo |
|---|---|---|
| full description | ~350 chars | ~5,250 |
| name-only default | ~12 chars | ~180 |

**29× cheaper.** A new skill stops being a budget event.

🔴 **And the strongest argument: the truncation is already happening.** On a 200k session today,
~2,000 chars remain after built-ins, so most of devrc's 39 skills already lose their entire
description — chosen arbitrarily by a decayed usage counter, silently, with no error. Tiering
does not introduce a loss. It converts a loss that is **already occurring by accident** into one
made **deliberately, legibly, and by evidence**.

---

## 4. Live evidence for the split

Decayed priority vs listing cost, all 39 (`usageCount` from `~/.claude.json`, decay applied):

```
    pri  uses   days  cost  skill            |     pri  uses   days  cost  skill
  540.8   541    0.0   215  audit-pr         |     0.9     9   55.2   352  devrc-dx
  349.4   357    0.2   205  handoff          |     0.9     1    1.4   489  prune-index
   79.9    91    1.3   420  browser          |     0.8     8   90.4   415  i3
   73.8    76    0.3   699  clickup          |     0.7     5   19.4   245  verify-agent
   67.7    75    1.0   348  clawgate         |     0.7     1    3.4   497  signal
   66.9    67    0.0   220  resume           |     0.7     7   33.3   350  sglang
   46.9    47    0.0   326  obs-read         |     0.6     6   24.5   371  standup
   40.0    40    0.0   266  analyze-service  |     0.5     1    6.5   425  initiative-scan
   13.1    35   10.0   250  find-session     |     0.5     5   60.2   317  close-the-loop
   13.0    13    0.0   385  tekton           |     0.2     2   41.6   294  repo-cos
   11.0    18    5.0   387  activity         |     0.1     1   85.4   259  session-audit
    8.0    10    2.3   298  prune-skill      |     0.1     1   26.2   331  prune-memory
    4.1     5    1.9   240  check-clickup-…  |     0.1     1   25.2   389  auditloop
    3.8     4    0.4   363  opencode         |     0.0     0   never   545  window-triage
    3.4     5    4.0   198  espanso-audit    |     0.0     0   never   373  ux-audit-loops
    2.6     6    8.3   394  bar              |     0.0     0   never   316  dl-router
    1.9    19   24.2   424  initiatives      |     0.0     0   never   278  gpu-operator-check
    1.8     3    5.2   601  session-manager  |     0.0     0   never   247  ux-sweep
    1.1     6   17.2   444  mailbox          |     0.0     0   never   240  vetr-mailbox
                                             |     0.0     0   never   231  adoption-scan
```

Cost of the whole listing at each Tier-A size (rest `name-only`):

| Tier A | A chars | B chars | TOTAL | vs 8,000 (200k) | vs 40,000 (1M) |
|---|---|---|---|---|---|
| 0 | 0 | 471 | 509 | 0.06× | 0.01× |
| 8 | 2,699 | 389 | 3,126 | 0.39× | 0.08× |
| **14** | **4,622** | **309** | **4,969** | **0.62×** | **0.12×** |
| 20 | 7,035 | 240 | 7,313 | 0.91× | 0.18× |
| 39 (today) | 13,647 | 0 | 13,685 | 1.71× | 0.34× |

⚠ **Three caveats on this table, all of which must be stated with it:**
- `never invoked` ≠ dead. `dl-router` runs as a live systemd service and routed a file on
  2026-08-20; `adoption-scan` has 20,494 tool-invocation events. Both are LIVE and neither has
  ever been invoked *as a skill*. Tier by whether it must **auto-fire**, not by this counter.
- The counter is **global across every repo**, not devrc-scoped, and it is Claude Code's own —
  a different instrument from the handoff's transcript scan.
- These totals are **devrc only**. Built-ins are protected and spend first, so at 200k a
  Tier-A of 14 still does not fully fit beside ~6,000 chars of built-ins — roughly the top 5
  Tier-A descriptions survive. Closing 200k *completely* additionally needs one of §5's levers.

---

## 5. Recommendation

**Adopt tiering as the structural fix; treat everything else as a dial.**

1. **Tier the 39** — Tier A ≈ 14 skills that must auto-fire without being named; everything else
   `name-only`. Choose Tier A by "does this need to fire when Zach describes a symptom rather
   than names the tool", using the table above as evidence, not as the rule.
2. **Make the tier ledger devrc-owned and enforced.** `~/.claude/settings.json` is per-host and
   unmanaged by design, so a hand-edit drifts. Add `scripts/sync-skill-tiers.py` on the exact
   shape of the existing `scripts/sync-claude-permissions.py` (idempotent, additive, curated),
   plus a `drift-check.sh` arm so a host whose tiers have drifted is reported.
3. **Re-point the ratchet at Tier A.** `LISTING_TOTAL_CEILING_CHARS` currently gates a total that
   tiering makes ~irrelevant. It should gate **Tier A chars**, which is the number that actually
   competes for budget — and fix the 194-char undercount while touching it.
4. **Then pick a dial for 200k**, if 200k sessions matter:
   - `skillListingBudgetFraction: 0.02` — doubles the budget to 16,000 at 200k. Costs ~2,000
     extra always-on tokens. Simplest, reversible, loses nothing.
   - `disableBundledSkills: true` — frees ~6,000 chars, at the cost of `dataviz`,
     `artifact-*`, `code-review`, `claude-api` et al.

**Do not** merge or delete active skills for budget reasons. §3's arithmetic shows it cannot
work: even retiring three *and* collapsing three whole clusters 9→3 reaches only 1.22×.

## 6. What is NOT verified

- `name-only` behaviour is read from the implementation, **not exercised live**. Before trusting
  it, set one skill to `name-only`, start a session, and confirm via `/context` that the listing
  shrank and `/name` still runs it.
- The ~6,000-char built-in figure is an **estimate** from the rendered listing; built-in skills
  are not readable from disk.
- Whether `/doctor` reports listing cost is still unconfirmed here.
