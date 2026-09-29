---
name: cairn-hygiene
description: "The HYGIENE door onto the cairn subsystem store: validate, audit, prune. The lifecycle rules are `prune-index`."
allowed-tools: Bash, Read, Grep, Glob
---

# cairn-hygiene — the HYGIENE door onto the store

**This file is a router, and it does NOT own the lifecycle.**
`claude/skills/prune-index/SKILL.md` owns what may be evicted (`OPEN:` never),
the budgets, the classification buckets and the confirm gate. Load that. This is the
door those decisions go through.

```
$DEVRC/scripts/cairn-ops/hygiene.sh --help
```

## 🔴 rc 22 — NOTHING WAS CHECKED, and it is the reason this door exists

Measured on this host 2026-09-27, two instances configured, same scope, same second:

| command | said | exited |
|---|---|---|
| the bare checker, `--scope <a non-default-instance scope>` | `NOTHING WAS CHECKED — no entry files were found` | **0** |
| the same check via `hygiene.sh validate` | `checked: 4 entry file(s)` · `4 of 4 parse` | 0 |

The first walked the DEFAULT instance's cache, which holds nothing for that scope.
Both exit 0, so a flow that branches on the exit code records a pass over an empty
directory — and that is what happened in a real `/handoff` run. The honest banner was
never the problem; the exit code beside it was.

🔴 **So: there is one read-through cache PER CONFIGURED INSTANCE, and no tool that
defaults to a path can see a scope on another one.** `hygiene.sh` resolves the store
for the SCOPE and refuses the zero. devrc PR #1872 carries the split's measurement;
clawgate cg#563 owns the `subsystem_touch.py` default this does not touch, and
`~/.claude/analyze-service-index` stays what it is — the frozen pre-cutover mirror,
refreshed by nothing.

## The rest

- `validate --scope S` — the write-protocol parse check. Read the `entry shape:`,
  `marker reachability:` and `dropped lines:` blocks, not only the exit code: all
  three are advisory and the last means content is **already lost**. Why each, and
  what a zero in it does not claim: `subsystem-index/SKILL.md`.
- `audit --scope S` — the budget/pointer audit, READ-ONLY. `NOT CHECKED` is not a
  pass.
- `prune … --confirm` — takes its own backup of the resolved instance cache first,
  then writes through the write door, so the post-write check applies to a prune like
  any other write. It refuses without `--confirm` (rc 2).
- ⚠ **the WHOLE-STORE audit has no per-instance form** and is still the bare tool,
  once per cache root `health.sh instances` prints. That gap is named rather than
  half-built.
