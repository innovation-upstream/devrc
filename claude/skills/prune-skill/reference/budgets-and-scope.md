# Budgets — the numbers, why they hold, and what they do NOT govern

Routed from the prune-skill core (§Budgets) — `~/.claude/skills/prune-skill/SKILL.md`,
source `~/workspace/devrc/claude/skills/prune-skill/SKILL.md`. Load this when a budget
number is doing work in your plan, when the file you are pruning may not be a skill, or
when you are deciding whether to land over target.

The core keeps the numbers and the three rules that change what you DO. Everything below
is the reasoning behind them, moved out 2026-08-27 and otherwise unchanged.

- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts.
- 12 KB is **proven and enforced**: a sidecar costs 0 until opened (verified — invoking a skill loads ONLY `SKILL.md`), and `scripts/browser-bridge/SKILL.md` routes ~11× its own weight from under it, gated by `scripts/browser-bridge/tests/test_skill_size.py`, which **owns** the ceiling and headroom floor — read the numbers there. 🔴 **A low core-to-reference ratio means you have not demoted enough — that is the tell, not the byte count.**
- 🔴 **This skill is itself over target, pinned by a ratchet** — `scripts/tests/test_prune_skill_size.py` OWNS the ceiling and the reason.
- 🔴 **Landing OVER target is allowed once, deliberately, and never by drift** — only when getting under it would cut the core's *routing value itself*; then stop under the hard cap and **record the reason and the number in the commit message**.
- 🔴 **Check what governs THIS file first — 12 KB is a SKILL budget**, and a non-skill file may be under a different gate or none; where a gate exists it OWNS its numbers, so read them there. 🔴 **An eviction SINK (`claude/RULES-ARCHIVE.md`, a `claudedocs/` doc) is ungated and demand-loaded, so PRUNING IT DELETES WHAT A PRIOR PRUNE PUT THERE — never prune a sink**; §3's evict-bias does not apply to one.
- 🔴 **ALWAYS-LOADED files (`CLAUDE.md`, `claude/RULES.md`) are a DIFFERENT problem — this playbook does not apply, and such a pass may legitimately end LARGER.** Read `~/.claude/skills/prune-skill/reference/always-loaded-and-landing.md` BEFORE touching one.

## 🔴 The core-to-reference ratio has a SHAPE-DEPENDENT floor — measured, 2026-08-27

The ratio tell above is stated absolutely and should not be. Measured across the two
skills the core names:

| skill | core | reference | ratio |
|---|---|---|---|
| `browser-bridge` | 11,961 B | 162,076 B | **13.55x** |
| `prune-skill` | ~10 KB | ~26 KB | **~2.5x** |

`browser-bridge` is a TOOL with a wide surface — per-site pages, per-capability topics —
so almost everything is demand-loaded depth. `prune-skill` is a seven-step METHOD: the
steps themselves are what a reader needs in hand, and §3's own KEEP_HOT protects exactly
that. Driving the ratio to 11x here would mean demoting the method into sidecars and
leaving a router that routes to the procedure it is supposed to BE.

**So read the ratio as a tell that points you at candidates, not as a target.** A low
ratio on a tool-shaped skill means undemoted depth; on a method-shaped one it may be
correct. What is NOT excused either way: rationale, worked measurements and history —
those are depth in any shape, and they are what this file now holds.
