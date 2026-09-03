---
name: cairn
description: "The hosted subsystem store (pod in ns `subsystem-store`) and its client `scripts/cairn`. Use for: `cairn doctor`, cairn sync/recall/search/ls-entries/who, a stale or unstamped store, a `cairn` exit 4, seeding the pod, a scope a token cannot reach. Writes are `subsystem-index`; pruning is `prune-index`."
allowed-tools: Bash, Read, Grep, Glob
---

# cairn — the hosted subsystem store, and the client that reads it

**This file is a router. Anything a command can answer, a command answers** —
`claude/RULES.md`, "prefer deterministic/structural fixes over prose".

## Start here, always

```
cairn doctor            # pod, credential, cache stamp, counts, scope visibility, reader resolution
cairn doctor --json     # the same facts, machine-readable
cairn doctor --no-sync  # same, without touching the network
```

It prints its own exit-code legend and a state per check. Four states, and the
last two are not the same fact: `OK` · `PROBLEM` (measured, and it is not fine) ·
`UNMEASURED` (it could not look, and says why) · `NOT-OBSERVABLE` (no client can
answer it; the detail names who can). **It installs nothing** — it fetches, reads
headers, and throws the bytes away, so it can be run twice without repairing the
staleness it was run to measure.

## The verbs

| you want | run |
|---|---|
| refresh the local cache from the pod | `cairn sync` |
| this repo's digest | `cairn recall` (`--scope X` / `--repo PATH`) |
| find a hunk by text | `cairn search '<query>'` (`--all-scopes` to search every scope) |
| what does the cache actually hold | `cairn ls-entries` |
| parse-check the cached entries | `cairn validate` |
| which sessions/windows/transcripts worked a task | `cairn who <task>` (`--json`, `--host`, `--no-windows`) |
| diagnose anything above going wrong | `cairn doctor` |

`cairn who` is about a **task**, not a store entry: it touches no store and never
syncs, and it has its own longer `--timeout` because it shells into tmux on two
hosts.

**Writes are not this skill's.** `subsystem-index` owns the one protocol for
every writer; `prune-index` owns deletion, with its own confirmation gate. Load
whichever applies rather than reconstructing their steps here.

## 🔴 The two different exit 4s

Confusing them sends you to re-run the command that just failed.

| whose 4 | means | do |
|---|---|---|
| `cairn sync`'s `EXIT_REFRESH_FAILED` | the store was **not reached**, but a usable cache survived | nothing to re-run — read the banner; the cache still serves reads |
| the **reader's** `EXIT_UNSTAMPED_READ_STORE` | the store it resolved carries no `.sync-stamp`, so it cannot date itself and refuses | `cairn sync` |

`claude/skills/resume/SKILL.md` step 4 carries the long form, including why
`cairn recall` never returns 4 (it reaches the reader as a *library*). Read it
there; it is not restated here.

## Where a host reads from

`scripts/lib/subsystem_read_store.py` is the ONE answer, and
`cairn doctor`'s `reader-resolution` check prints it. Two directories exist and
they are not interchangeable: `~/.cache/subsystem-store` is the synced
read-through cache, stamped by `cairn sync`; `~/.claude/analyze-service-index`
is the pre-cutover per-host mirror, frozen and refreshed by nothing.
**The discriminator is the stamp, not the path** — a store that cannot date
itself is refused rather than served.

## Adding a consumer that reads the store

Call `subsystem_read_store.resolve_read_store()`. Do **not** compute a path.
`scripts/tests/test_store_root_ledger.py` enumerates every file that resolves a
store root and requires each to route through that module or carry a written
exemption; it fails when the set grows **or** shrinks. That test — not this
paragraph — is what stops the fourth open-coded reader, and its ledger reasons
are where the exemptions are argued. Read them before adding a row.

## Operator surface

`reference/operator-surface.md` — the pod, `seed.sh`, `verify-byte-identity.sh`
and its measured coverage gap, `build-push.sh`, `cairn-cutover.py`, the backup
CronJob, and 🔴 **the token file is read ONCE at startup**: editing the secret
changes nothing until the pod is replaced, and the wrong replacement command
costs two rollouts. Load it before touching the deployment.
