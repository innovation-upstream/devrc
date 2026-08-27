# Budgets — the numbers, why they hold, and what they do NOT govern

Routed from the prune-skill core (§Budgets) — `~/.claude/skills/prune-skill/SKILL.md`,
source `~/workspace/devrc/claude/skills/prune-skill/SKILL.md`. Load this when a budget
number is doing work in your plan, when the file you are pruning may not be a skill, or
when you are deciding whether to land over target.

The core keeps the numbers and the three rules that change what you DO. Everything below
is the reasoning behind them, moved out 2026-08-27 and otherwise unchanged.

- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts.
- 12 KB is **proven and enforced**: a sidecar costs 0 until opened (verified — invoking a skill loads ONLY `SKILL.md`), and `scripts/browser-bridge/SKILL.md` routes many times its own weight from under it (**13.55×** by the method below — the *~11×* this sentence carried when it was sliced here is the third disagreeing copy the round-3 audit found, and is superseded by the table; the pinned form in `test_prune_skill_size.py`'s BB_CLAIM gates the RELATIONSHIP "browser-bridge meets the target", which is the load-bearing half and is unaffected), gated by `scripts/browser-bridge/tests/test_skill_size.py`, which **owns** the ceiling and headroom floor — read the numbers there. ⚠️ *(This bullet's original ending — "a low core-to-reference ratio means you have not demoted enough — that is the tell, not the byte count" — is SUPERSEDED by the section below, which measured its floor as shape-dependent. Left visible rather than silently rewritten, because a verbatim slice is what carried it here and a reader who has seen the absolute form elsewhere needs to know which one won.)*
- 🔴 **This skill's own ceiling is `scripts/tests/test_prune_skill_size.py`, which OWNS it and the reason** — read the numbers there, never from here. It was a RATCHET ABOVE the target until 2026-08-27 (13,056, while the body was 12,812 B and 524 B over the bar it asks others to meet); the body is now well under it and the ceiling is the target itself (the exact size is in the table below and in the gate — deliberately NOT restated here, because the round that wrote this bullet left a `12,027` in it that the same round's own trimming made stale within minutes, contradicting the table 16 lines down. A figure stated twice in one file is a figure that will disagree with itself). 🔴 **If that gate fires, the answer is to evict — NOT to raise it.** Raising it is a deliberate re-exemption and the gate says so in its own failure message. *(This bullet said the skill was 'itself over target' until the prune that made it false. It survived because it was carried into this file by a VERBATIM slice — the skill's own §5 rule that slicing preserves rot exactly as faithfully as it preserves content, demonstrated on itself.)*
- 🔴 **Landing OVER target is allowed once, deliberately, and never by drift** — only when getting under it would cut the core's *routing value itself*; then stop under the hard cap and **record the reason and the number in the commit message**.
- 🔴 **Check what governs THIS file first — 12 KB is a SKILL budget**, and a non-skill file may be under a different gate or none; where a gate exists it OWNS its numbers, so read them there. 🔴 **An eviction SINK (`claude/RULES-ARCHIVE.md`, a `claudedocs/` doc) is ungated and demand-loaded, so PRUNING IT DELETES WHAT A PRIOR PRUNE PUT THERE — never prune a sink**; §3's evict-bias does not apply to one.
- *(The ALWAYS-LOADED bullet that stood here is deliberately NOT duplicated: it is a
  safety rule, so it stays in the core where it is read before the decision, not behind
  a load. The skill's own MERGE_DUP verdict, applied in the direction that keeps the
  guard visible.)*

## 🔴 The core-to-reference ratio has a SHAPE-DEPENDENT floor — measured, 2026-08-27

The ratio tell above is stated absolutely and should not be. Measured across the two
skills the core names:

| skill | core | `reference/**/*.md` | ratio |
|---|---|---|---|
| `browser-bridge` | 11,961 B | 162,076 B | **13.55x** |
| `prune-skill` | *run the command* | *self-referential* | *run the command* |

🔴 **The second row deliberately carries no numbers at all.** The core count went with them for the plainest reason: the round that removed the other two left `11,953 B` in place and its own trimming made that 112 B stale in the same commit. The core is perfectly stateable — it was simply stated in a file nothing gates, beside two figures that had just been removed for being unstateable. THIS FILE lives
inside `prune-skill/reference/`, so any total stated here changes the moment the
sentence stating it is edited — it was 51 B stale within one round, and stale again
by the next. A self-referential measurement is not a number you can pin; it is a
command you re-run. 🔴 That includes anything DERIVED from it — the first attempt
removed the byte count and kept the ratio, which was stale (2.51 → 2.61x) by the
next round, because a ratio computed from an unstateable number is just as
unstateable. Removing the input and keeping the output is the rot one step out. The `browser-bridge` row is exact because that skill is external
to this file.

🔴 **State the method or the number is not a measurement.** The `browser-bridge`
figures are exact — but a pre-merge audit could not reproduce them and reported them as
unreproducible, because `browser-bridge/reference/` has a `sites/` subdirectory and
the auditor globbed one level. It retracted that half after re-measuring. The
lesson survives the retraction and is the reason this block exists: a ratio moves
with which files you count, so an unstated method makes a correct number
indistinguishable from a wrong one, in a skill whose central rule is that every
number is re-measured rather than carried forward.

```bash
S=~/workspace/devrc/scripts/browser-bridge          # or claude/skills/<name>
echo "$(wc -c < "$S/SKILL.md")  $(find "$S/reference" -name '*.md' -type f -exec cat {} + | wc -c)"
```

One level only (`reference/*.md`) gives 12.29x for `browser-bridge`; adding its
README gives ~26x. Any of those is defensible; **none is defensible unstated.**

`browser-bridge` is a TOOL with a wide surface — per-site pages, per-capability topics —
so almost everything is demand-loaded depth. `prune-skill` is a seven-step METHOD: the
steps themselves are what a reader needs in hand, and §3's own KEEP_HOT protects exactly
that. Driving the ratio to 11x here would mean demoting the method into sidecars and
leaving a router that routes to the procedure it is supposed to BE.

**So read the ratio as a tell that points you at candidates, not as a target.** A low
ratio on a tool-shaped skill means undemoted depth; on a method-shaped one it may be
correct. What is NOT excused either way: rationale, worked measurements and history —
those are depth in any shape, and they are what this file now holds.
