---
name: cairn
description: "The hosted subsystem store (pod in ns `subsystem-store`), client `cairn` on PATH. Use for: `cairn doctor`, cairn sync/recall/search/ls-entries, cairn-who, a stale or unstamped store, a `cairn` exit 4, seeding the pod, a scope a token cannot reach. Writes are `subsystem-index`; pruning is `prune-index`."
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
| parse-check the cached entries | `cairn validate` — ⚠ see below, it is NOT the write-protocol check |
| the WRITE-protocol parse check | `cairn-validate --scope <scope>` — a SEPARATE binary, see below |
| which sessions/windows/transcripts worked a task | `cairn-who <task>` (`--json`, `--host`, `--no-windows`) — a SEPARATE binary |
| diagnose anything above going wrong | `cairn doctor` |

🔴 **`cairn validate` IS NOT THE WRITE-PROTOCOL CHECK, and it stops being one
silently, at exit 0.** Once a host has run `home-manager switch`, the client on
PATH is the pinned OSS package, which reimplements `validate` on the reader's
resolver instead of shelling the writer. MEASURED on one scope of the live cache,
both clients at the locked rev: the package writes **0 bytes to stdout** (a
77-byte state banner on stderr) and exits 0, where the writer prints **5,766
bytes on stdout** with `entry shape:`, `marker reachability:` and `dropped
lines:` — the last meaning content is ALREADY LOST. Both are "green"; one of them
is empty on the stream you read. **The mandated post-write check is
`cairn-validate --scope <scope>`** — the SAME spelling `subsystem-index` names,
which is the point: two skills naming one mandated command in two ways is how
one of them goes unpinned and drifts. It is a devrc-only launcher over
`scripts/lib/subsystem_touch.py` and lands on PATH in the same switch that swaps
`cairn`. It cannot be a `cairn` subcommand: `cairn` is the pinned OSS package,
and the writer it runs is devrc-only and deliberately absent from that repo.
⚠ Exit codes differ too: the writer exits **3** on a malformed entry, the packaged
client **5** (`EXIT_CORRUPT`; `3` is `EXIT_UNREACHABLE_NO_CACHE` for the client).

🔴 **`cairn-who` is a separate command, not a `cairn` subcommand.** It is about a
**task**, not a store entry: it touches no store and never syncs, takes none of
the `--scope`/`--repo`/`--no-sync` flags, and has its own longer `--timeout`
because it shells into tmux on two hosts rather than fetching an HTTP snapshot.
Typing it as a `cairn` subcommand is no longer valid: argparse exits 2 with an
`invalid choice` naming the verbs that remain.

**Writes are not this skill's.** `subsystem-index` owns the one protocol for
every writer; `prune-index` owns deletion, with its own confirmation gate. Load
whichever applies rather than reconstructing their steps here.

🔴 **A CREATE route EXISTS — `cairn create --scope S --ref R --file F` (`PUT`
with `If-None-Match: *`, devrc#1254). It still cannot create a scope's FIRST
entry, and the reason is NOT the one this file used to give.** The old text said
there was no create verb at all; that has been false since #1254, and the
conclusion it drew survived only by accident. The real mechanism is that the
index is built by WALKING THE STORE ROOT narrowed by your allowlist
(`subsystem_recall.load_store`), so a scope with no directory on the pod's disk
resolves to nothing and the write is refused at the index, before any filesystem
write. A new scope's first entry is therefore still an operator step — seeding —
and until somebody takes it the record exists nowhere the pod can serve.

MEASURED 2026-09-11, with the control that makes the reading mean something:

| `cairn create` target | answer |
|---|---|
| absent + non-allowlisted scope | **rc 6** `[not-found] — not found` |
| allowlisted, present scope, ref that already exists | **rc 9** `[already-exists]` |

The two codes differ, so the `not-found` is a fact about that scope rather than a
probe wired to nothing. ⚠ Both wrote nothing.

🔴 **That 404 is byte-identical for "outside your allowlist", "never existed" and
"ref resolves to nothing"** — deliberately, so an error cannot enumerate the
store. `reference/operator-surface.md` carries the two ways an OPERATOR can still
tell absent from refused; no client can.

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

`scripts/lib/subsystem_read_store.py` is the ONE answer for devrc's OWN readers
(the `subsystem-index` writer and `cairn-who`), and `cairn doctor`'s
`reader-resolution` check prints it. ⚠ The deployed `cairn` is the pinned flake
package and carries its OWN copy of that module; the two agree today and are
consolidated in a later slice — so if they ever disagree, `doctor`'s printed
path is the authority for the CLIENT, and this file's for the writer.

Two directories exist and they are not interchangeable:
`~/.cache/subsystem-store` is the synced
read-through cache, stamped by `cairn sync`; `~/.claude/analyze-service-index`
is the pre-cutover per-host mirror, frozen and refreshed by nothing.
**The discriminator is the stamp, not the path** — a store that cannot date
itself is refused rather than served.

## Adding a consumer that reads the store

Call `subsystem_read_store.resolve_read_store()`. Do **not** compute a path.
`scripts/tests/test_store_root_ledger.py` enumerates the files that resolve a
store root and requires each to route through that module or carry a written
exemption; it fails when the set grows **or** shrinks, and pins WHICH KIND of
resolution each ledgered file performs. Read its ledger reasons before adding a
row — that test, not this paragraph, is what an addition has to satisfy.

⚠ **It is not total, and its own docstring is the authority on how far it
reaches** — it scans `scripts/` only (so `nix/` is uncovered, and `nix/home.nix`
names the frozen mirror today), it parses Python and shell but not `.nix`, and a
path assembled at run time out of `os.environ` is invisible to it. Treat a green
run as "no *detectable* new open-coded reader", never as proof there is none.

## Operator surface

`~/.claude/skills/cairn/reference/operator-surface.md` — the pod, `seed.sh`, `verify-byte-identity.sh`
and its measured coverage gap, `build-push.sh`, `cairn-cutover.py`, the backup
CronJob, and 🔴 **the token file is read ONCE at startup**: editing the secret
changes nothing until the pod is replaced, and the wrong replacement command
costs two rollouts. Load it before touching the deployment.
