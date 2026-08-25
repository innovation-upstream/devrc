# Proposal: tier the skill listing instead of shrinking it — 2026-08-24

Companion to `handoff-skill-listing-budget.md`. That handoff concluded the listing "cannot be
closed by editing text" and that closing it needs **fewer skills**. Reading the installed
Claude Code binary changes that conclusion: there is a supported mechanism that makes listing
cost **per-skill opt-in**, and it fixes the growth rate rather than the current total.

Measured against `claude-code-2.1.232` as installed
(`/nix/store/43fmrchzsg9g110qqk9n7a5z0icsf0qh-claude-code-2.1.232/bin/.claude-wrapped`), the
live usage counters in `~/.claude.json`, and one live A/B against a throwaway instance.
Nothing here is from documentation alone.

⚠ **Every devrc total here measures the tree BEFORE PR #785** (which retires `ux-sweep`,
`gpu-operator-check`, `session-audit`): **39 entries, 13,491 by the gate / 13,685 real.**
After #785: **36 entries, 12,679 by the gate / 12,858 real**, ratchet `12_929`.

> This document was adversarially refuted before adoption. Corrections from that pass are
> folded in below and marked **[R]**. Every error found ran in the direction of *understating*
> the pressure.

---

## 1. How the budget actually works

🔴 **The budget is characters. No tokenizer is involved.**

```js
Ker(e, t) { if (SLASH_COMMAND_TOOL_CHAR_BUDGET) return it
            return Math.floor((e ?? 200000) * t * fraction) }
apv = 0.01 (skillListingBudgetFraction)   cpv = 1536 (skillListingMaxDescChars)
```

🔴 **[R] The chars-per-token multiplier is NOT the constant 4.** It is `zx(model)`:

```js
zx(e){ if(!e) return 4; … return Tm_.has(normalized) ? 4 : 3 }
```

`Tm_` is a 14-model set ending at 4.6. `claude-opus-5` is **not** in it ⇒ **3, not 4.**
An earlier draft of this document asserted ×4 as a constant; every downstream figure inherited
a 25 % overstatement of the budget.

| model class | 200k | 1M |
|---|---|---|
| Claude 3.x–4.6 (`Tm_`) | 8,000 | 40,000 |
| **`claude-opus-5` and newer** | **6,000** | **30,000** |

Two further overrides, both absent from the earlier draft:
- **`SLASH_COMMAND_TOOL_CHAR_BUDGET` short-circuits the whole computation** — it returns before
  the fraction or the context window are read. (Verified unset on this host.)
- **`kT` silently returns 200,000 instead of 1e6 when 1M credits are blocked**, so a 1M session
  can drop to the 6,000-char budget with no visible change.

**Entry cost** is `len(name) + 4 + min(len(desc), 1536)` — the listing renders as
`- name: description`, joined by one newline per entry. `whenToUse`, when present, is appended
as `description - whenToUse` and counts toward the same cap. **[R] 0 of 39 devrc skills define
it**, checked across all spellings (the key normalizer collapses `when_to_use` / `when-to-use` /
`whenToUse`), so it adds nothing here.

🔴 **The gate undercounts.** `test_skill_descriptions.py` measures `len(name) + len(desc)`,
missing the per-entry 4 plus the newline separators — **the general form is `5n − 1`**, so
**194 at 39 entries** (13,491 measured vs 13,685 real) and **179 at 36**, post-#785
(12,679 vs 12,858). Quote the form, not the figure: an earlier draft of this document carried
`194` into a 36-entry context, where it is wrong. Conservative direction either way — the gate
can only ever be stricter than reality.

⚠ **[R] A latent desync:** the budget pass measures with `.length`, the renderer with
`Bun.stringWidth`. They agree today (the only non-ASCII across 39 descriptions is `—`×30 and
`…`×2, all width 1). **One emoji in a description would desync devrc's gate from what Claude
Code actually charges.**

### What overflow does

Assume every non-exempt skill is reduced to name-only, compute the leftover, then walk skills in
**descending priority** buying descriptions back. **[R] The greedy does not stop at the first
entry that does not fit** — it continues, so a cheap low-priority skill can be bought after an
expensive higher-priority one is skipped. It is all-or-nothing per skill: a description survives
whole or vanishes whole. (The 1,536 cap is a genuinely partial truncation, but it is a separate,
earlier pass — the binary keeps `cappedSkills` and `budgetTruncatedSkills` distinct. No devrc
description is over the cap today.)

**Priority decays** — `usageCount × max(0.5^(days/7), 0.1)`, a **7-day half-life**, floored at
0.1×; never used ⇒ 0. Units verified as milliseconds. **[R]** Writes are throttled to once per
60 s per skill, so bursts undercount.

### Who is protected

🔴 **[R] "Bundled" ≠ "built-in", and the distinction matters.** The exemption is
`dpv(e) = e.type==="prompt" && e.source==="bundled"`. `source` is an enum with *separate*
`"builtin"` and `"bundled"` arms — **`init` and `security-review` are `builtin` and are NOT
protected**; they compete on priority exactly like devrc's. Plugin skills are not protected
either.

**[R] Measured, not estimated:** the 16 non-devrc entries cost **7,007 chars** (5,583 extracted
from the binary, 1,424 measured off a live rendered listing for three runtime-templated ones).
The protected subset is ≈ **6,850**. An earlier draft estimated ~6,000 — low, in the direction
that understates the problem.

---

## 2. The mechanism the handoff missed

`skillOverrides` in `settings.json`, keyed by skill name:

| value | listing cost | model routing | `/name` |
|---|---|---|---|
| absent / `on` | `name + 4 + desc` | full description | yes |
| **`name-only`** | **`name + 2`** | **name only — no routing prose, but still model-callable** | **yes** |
| `user-invocable-only` | 0 | hidden from model | yes |
| `off` | 0 | hidden | no |

**[R] Correction to an earlier draft:** `name-only` does not remove the skill from the model's
reach — the name stays in the listing and the Skill tool can still call it. What it loses is the
*routing prose* that makes it fire from a described symptom. And its membership in the
"exempt from truncation" set is **vacuous**: a name-only entry's full form already *is*
`- name`, so exemption costs and changes nothing. It is not a protection.

**[R] Two limits on applicability:**
- **`skillOverrides` can never touch a plugin skill** — the resolver hard-returns `"on"` for
  `source === "plugin"`. Tiering is not a universal lever. (Moot today: the three enabled LSP
  plugins contribute no skills. Note this also means CLAUDE.md's "cloudflare plugin is a quarter
  of the total" no longer holds on this host.)
- **`~/.claude/settings.json` is the lowest-precedence ordinary scope.** Merge order is
  user → project → local → flag → policy, later wins, per-key deep merge (no wholesale clobber).
  A project or local entry for the same skill name beats a devrc-managed user file.

devrc's skills load as `type:"prompt", source:"userSettings"`, so the override applies to all 39.

---

## 3. The argument that decides it

**Skills are added ~15×/month.** Post-migration organic additions: clickup (08-13), signal
(08-17), window-triage (08-19), prune-index (08-21), check-clickup-addressed (08-22) — 5 in 10
days. At a 350-char mean that is **+5,250 chars/month**.

Retiring the three dead skills buys 772 chars ≈ **4.5 days of growth**. The ratchet's
"evict in the same commit" rule, at that rate, demands ~15 evictions/month. That is a treadmill.

**Tiering changes the growth rate, which is the only thing that scales:**

| | per new skill | per month at 15/mo |
|---|---|---|
| full description | ~350 chars | ~5,250 |
| name-only default | ~12 chars | ~180 |

**29× cheaper.** A new skill stops being a budget event.

### 🔴 [R] Correction: truncation is NOT happening today

An earlier draft's strongest claim — *"the truncation is already happening; tiering only makes
an accidental loss deliberate"* — is **false for the live configuration.** Measured: the whole
listing is **20,708 chars against a 30,000 budget** on this session's `claude-opus-5` @ 1M —
**0.69×. It fits. Nothing is being truncated.**

It becomes true the moment a session runs at 200k, or 1M credits are blocked (§1). So the honest
framing is **runway, not emergency**:

- headroom today ≈ 9,300 chars ⇒ at +5,250/month, **~1.8 months** before a 1M session truncates.
- at 200k on `claude-opus-5` the budget is 6,000 and the protected built-ins alone are ~6,850 —
  **devrc's descriptions do not survive at all**, whatever their priority.

That is a weaker urgency claim than the draft made, and it should be adopted on the runway
argument, not on a false emergency.

---

## 4. Live evidence for the split

Decayed priority vs listing cost, all 39 (`usageCount` from `~/.claude.json`):

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

Cost of devrc's whole listing at each Tier-A size (rest `name-only`), **ratios corrected [R]**
to this model's 6,000 / 30,000 budgets — and note built-ins spend ~6,850 *before* any of this:

| Tier A | A chars | B chars | devrc TOTAL | vs 6,000 (200k) | vs 30,000 (1M) |
|---|---|---|---|---|---|
| 0 | 0 | 471 | 509 | 0.08× | 0.02× |
| 8 | 2,699 | 389 | 3,126 | 0.52× | 0.10× |
| **14** | **4,622** | **309** | **4,969** | **0.83×** | **0.17×** |
| 20 | 7,035 | 240 | 7,313 | 1.22× | 0.24× |
| 39 (today) | 13,647 | 0 | 13,685 | 2.28× | 0.46× |

⚠ **Two caveats that must travel with this table:**
- `never invoked` ≠ dead. `dl-router` runs as a live systemd service and routed a file on
  2026-08-20; `adoption-scan` has 20,494 tool-invocation events. Both are LIVE and neither has
  ever been invoked *as a skill*. Tier by whether it must **auto-fire**, not by this counter.
- The counter is **global across every repo** and is Claude Code's own — a different instrument
  from the handoff's transcript scan. It reports `session-audit` at **1**, not 0.

---

## 5. Recommendation

**Adopt tiering as the structural fix; treat everything else as a dial.**

1. **Tier the 39** — Tier A ≈ 14 skills that must auto-fire without being named; everything else
   `name-only`. Choose Tier A by "does this need to fire when Zach describes a symptom rather
   than names the tool", using §4 as evidence, not as the rule.
2. **Make the tier ledger devrc-owned and enforced.** `~/.claude/settings.json` is per-host and
   unmanaged by design, so a hand-edit drifts. Add `scripts/sync-skill-tiers.py` on the shape of
   the existing `scripts/sync-claude-permissions.py` (idempotent, additive, curated), plus a
   `drift-check.sh` arm. **[R] Design it knowing project/local scopes win per-key.**
3. **Re-point the ratchet at Tier A**, and fix both measurement errors while touching it: the
   194-char undercount **and [R] the `zx(model)` multiplier** — gate against 6,000 / 30,000 for
   `claude-opus-5`, not 8,000 / 40,000.
4. **Then pick a dial for 200k**, if 200k sessions matter:
   - `skillListingBudgetFraction: 0.02` — **[R] yields 12,000 at 200k on this model, not
     16,000.** Costs ~2,000 extra always-on tokens. Simplest, reversible, loses nothing.
   - `disableBundledSkills: true` — frees ~6,850, at the cost of `dataviz`, `artifact-*`,
     `code-review`, `claude-api` et al.

**Do not** merge or delete active skills for budget reasons. Even retiring three *and* collapsing
three whole clusters 9→3 reaches only 1.22× of the old 8,000 figure — worse against 6,000.

## 6. Verification status

✅ **`name-only` is verified LIVE** (2026-08-24). An isolated instance via `CLAUDE_CONFIG_DIR`
with two throwaway probe skills, one overridden and one held as an internal control:

| | `zzalpha` (overridden) | `zzbeta` (control) |
|---|---|---|
| baseline | `zzalpha: ALPHADESCRIPTION marker for…` | `zzbeta: BETADESCRIPTION marker for…` |
| `"zzalpha": "name-only"` | **`zzalpha`** — description gone | unchanged |

And invoking it still returned its full body (`Body of the alpha probe skill.`). So the
description is stripped from the listing while the skill stays fully invocable and loads
completely. The live config was never modified; no probe leaked into `~/.claude.json`.

Still **not** verified:
- The `zx(model)` model set was read from the binary, not exercised across models.
- Whether `/doctor` reports listing cost.
- The 7,007-char non-devrc figure includes 1,424 chars measured off a rendered listing rather
  than extracted statically.
