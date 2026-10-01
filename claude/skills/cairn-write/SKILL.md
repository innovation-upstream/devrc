---
name: cairn-write
description: The WRITE door onto the cairn store: append, put, create.
allowed-tools: Bash, Read, Grep, Glob
---

# cairn-write — the WRITE door onto the store

**This file is a router, and it does NOT own the protocol.**
`claude/skills/subsystem-index/SKILL.md` is the one protocol for every writer — what
counts as notable, which ref, the `already there` comparison, the diff you show
first. Load that. This is the door it goes through.

```
$DEVRC/scripts/cairn-ops/write.sh --help
```

## The two refusals that are this door's own

🔴 **rc 21 — the append protocol, checked BEFORE anything is sent.** A `--text` that
carries a leading bullet marker, a leading date, a newline or more than 2000
characters is refused locally. The server adds the marker and the date; a text that
carries its own is ACCEPTED by the pod and renders double-prefixed, and no exit code
upstream reports it. The first production append through this route reads
`- <date>: <date>: …` because a caller typed one and nothing checked. A 21 is a
guarantee that nothing was written.

🔴 **rc 24 — the write LANDED and the mandated post-write check did not confirm it.**
Not a failed write: **do not retry**, re-read the scope. The check runs by default
because an unrun check is indistinguishable from a passed one, and the `sync` inside
it is load-bearing — `append` writes to the POD and does not touch the local cache,
so an unsynced check cleanly parses the PRE-WRITE bytes and passes on exactly the
defect it exists to catch.

⚠ `--no-verify` skips it, for a caller running its own. Say so when you use it: the
rc 0 is then about the write only.

## `tags:` — a CLOSED set of three

🔴 **Front matter carries an optional `tags:` key, and the vocabulary is exactly
`infra`, `product`, `tooling`.** The axis is **what kind of work**, not *whose* — the
scope name already answers whose. A fourth term naming the customer was tried and
removed for exactly that reason.

🔴 **A term outside the set is a defect in BOTH directions, and the second is the
dangerous one.** Once the write-time gate ships, the pod REFUSES it — 422,
`X-Store-Status: entry-shape`. Before that it is accepted silently and creates a
category of one that no `--tag` query will ever find.

🔴 **One unwrapped flow list: `tags: [tooling]`.** Front matter is parsed LINE BY
LINE, so a wrapped list makes the whole entry MALFORMED — out of the index, out of
`--ref` and `--search`, and unwritable.

⚠ **Changing front matter is a `put`, never an `append`** — an `append` adds a
journal bullet and cannot touch front matter.

🔴 **WHICH tag a given scope takes is NOT in this repo, and that is deliberate: this
repo is PUBLIC and the scope names are not.** The per-scope table is a store entry,
`cairn/tag-vocabulary`; reach it with the read door —
`recall --scope cairn --ref tag-vocabulary`. It matches a scope name **EXACTLY,
never by prefix**, so a tag cannot be inferred from a scope's shape. Do not guess one.

## What the door does not decide

Which instance a write lands on is **not** obvious from the scope — read the
`instance=` the verb echoes, or ask
`$DEVRC/scripts/cairn-ops/health.sh instances --scope <scope>` first. One scope can
exist on more than one instance, so the sibling entries a future reader expects
beside yours may be on the other one.
