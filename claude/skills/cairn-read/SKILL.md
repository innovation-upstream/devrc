---
name: cairn-read
description: "The READ door onto the cairn subsystem store: recall, search, ls-entries. The store and client themselves are `cairn`."
allowed-tools: Bash, Read, Grep, Glob
---

# cairn-read — the READ door onto the store

**This file is a router with nothing in it to copy.** The command answers everything
a command can answer — `claude/RULES.md`, "prefer deterministic/structural fixes
over prose".

```
$DEVRC/scripts/cairn-ops/read.sh --help
```

That prints the subcommands, the flags that pass through, and the exit codes. Read
it there; a second copy here is the one that goes stale, which is exactly what
happened to the post-write check stated in three skills at once.

## The one thing to know before you read an empty answer

🔴 **rc 20 is "no scope resolved", and it exists because the client's honest answer
to that question is an EMPTY REPORT.** With no `--scope`, no `--repo` and a working
directory outside a git repository there is nothing to derive a scope from, and a
report with no entries in it is indistinguishable from "nothing was ever recorded".
`claude/RULES.md`: *"An EMPTY RESULT cannot distinguish two mechanisms."* So the
wrapper refuses first and names the remedy.

⚠ **`--if-available` turns an absent client into a rc-0 SKIP.** Pass it in a
best-effort preflight (that is what `clawgate` and `obs-read` do) and never in a
step whose result you are going to report.

## Where the surrounding knowledge lives — read on demand, not here

- the client, the pod, `cairn doctor`, the two different exit 4s: **`cairn`**
- how to read a recall's badges, `MALFORMED`, `scope-absent` vs `scope-unreadable`:
  **`resume/reference/cairn-recall.md`**
- 🔴 **which instance a scope lives on, and its cache root:**
  `$DEVRC/scripts/cairn-ops/health.sh instances --scope <scope>`. There is one
  read-through cache per configured instance, so an absence in one is not an absence
  from the fleet.
