# Classification — the sub-rules under each verdict

Routed from the prune-skill core (§3) — `~/.claude/skills/prune-skill/SKILL.md`,
source `~/workspace/devrc/claude/skills/prune-skill/SKILL.md`. The core carries the five
verdicts and the bias; this carries the traps under them, each of which has silently
produced a bad prune. Moved out 2026-08-27, verbatim.

## Before you split — enumerate what cites INTO the skill

🔴 **Before you split, enumerate what cites INTO this skill** — grep the always-loaded files, sibling skills, `scripts/`, `claudedocs/`. Resolve **distinct PATHS, not citation sites** (they differ 2–3×); the one the always-loaded file cites is what a path gate blocks on. Citations can also be **by number** ("trap 9"), which no path gate sees.

## Under EVICT_HISTORY

  🔴 **A date in a heading is NOT this verdict** — durable guidance citing a date is not work-status, and evicting it guts the skill. The auditor reports the two separately (in one 67-skill corpus, ONE skill held 45 work-status headings and every other ZERO). Read "dated lessons" as DEMOTE candidates, never eviction.

## Under DEMOTE_TO_REFERENCE

  🔴 **If the block is NUMBERED and cited by number, the numbers are an API — FREEZE them.** Renumbering shards 1..n breaks every citation while leaving all PATHS valid, so no gate sees it. Keep the global numbers (non-contiguous in a shard is correct), banner it, never renumber.
  🔴 **No COUNT in a routing line** — "(18 gotchas)" is a number no gate checks and every append invalidates. Say what the file is *for*.
  🔴 **Never put `session`, `history`, `changelog`, `work log`, `what shipped` or `release notes` in a heading you CREATE** — a later pass reads it as evictable work-status and deletes the pointer you just made.

## Dispatching prune subagents

🔴 **Prune (not classifier) subagents: one worktree each, ≤2–3 concurrent, PER-AGENT scratch paths, and before pushing assert siblings' commits are ancestors of your HEAD** — a stale base silently reverts a sibling's prune. Detail: `~/.claude/skills/prune-skill/reference/always-loaded-and-landing.md`.
