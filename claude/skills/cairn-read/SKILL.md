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

## The one thing to know about rc 20

🔴 **rc 20 is "no scope resolved", it applies to `recall` and `search` ONLY, and what
it buys is a DISTINGUISHABLE CODE — not a hole the client leaves open.** With no
`--scope`, no `--repo` and a working directory outside a git repository there is
nothing to derive a scope from. The client refuses that too, at **rc 2**, naming the
remedy (`could not derive a scope from '.': … pass --scope explicitly`); rc 20 is the
same refusal in this directory's own ≥19 band, so a caller branching on the status can
tell "the wrapper refused before doing anything" from "the store refused" — which the
client's 2, shared with every usage error, cannot.

⚠ **Measured, and it corrects what this file said first.** An earlier version of this
section claimed the client answers with an EMPTY REPORT here and cited
*"An EMPTY RESULT cannot distinguish two mechanisms."* That is false — the client
errors, and its own source records the older behaviour as rc 1 plus a traceback, so no
version of it ever returned an empty report for this. The rule still applies to plenty
in this store; it does not apply to this.

🔴 **`ls-entries` CANNOT RETURN 20 AND IS NEVER GATED ON A SCOPE.** It asks what the
caches hold, not what one scope holds: the client ignores `--scope` for it and lists
every entry on every instance. It was briefly in `recall`'s pre-flight arm, which
refused a listing from a non-repo cwd that the bare client answers fine — so
`read.sh ls-entries` needs no repo, no scope, and nothing else.

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
