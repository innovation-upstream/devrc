# Prior-work recall — what the store already knows about clawgate

Loaded from SKILL.md's `prior work` step. Measured 2026-09-07; re-measure rather than trusting
this file's age.

## What it answers

`cairn` is the read-through client for the hosted subsystem store. The `homelab-talos` scope holds
four clawgate entries — `clawgate.md`, `clawgate-agents.md`, `clawgate-e2e.md`, `clawgatectl.md` —
written by `/handoff` and `/analyze-service` at the END of past sessions. They are **dated bullets
of what one session MEASURED, in one PR**: which CI flake class was fixed and how, which guard
turned out to be walkable, which remedy is still `OPEN:`. No live query answers that, and
`HANDOFF.md` records current state rather than the reasoning that got there.

🔴 Everything returned is `RECALL, NOT LIVE OBSERVATION`. A fix that has since landed reads exactly
like one that has not — verify against the live pin (`clawgatectl health`) before acting.

⚠ The entries are `sensitivity=client-confidential`. Read them; never paste their prose into a
public repo — **devrc is public**, and `scripts/tests/test_store_content_not_copied.py` derives the
comparison from the store at run time precisely because a hand-written denylist cannot catch what
it was not written for.

## Retrieval — the keys have to MEET

The store is keyed by **subsystem**, so search the identifier a future reader would type, not only
the entry you happen to be standing in. Measured against `--scope homelab-talos` (10 is the
reader's own per-query cap — the header prints `N of 107 hunks`, so a `10` means "capped", not
"exhausted"):

| query | hunks | bytes |
|---|---|---|
| `clawgate` | 10 (cap) | 9,981 |
| `clawgatectl` | 10 (cap) | 10,723 |
| `clawgate-agents` | 10 (cap) | 11,423 |
| `e2e` | 10 (cap) | 10,107 |
| `task api` | 10 (cap) | 12,289 |
| `deploy` | 10 (cap) | 13,380 |
| `agent dispatch` | 5 | 5,237 |
| `runbook` | 3 | 5,479 |

Unlike the `datapacket-talos` alert catalog — where 26 of 35 titles miss entirely — **every term
tried here hit**. This scope is dense, so one query on the subsystem you are about to touch is
normally enough; add a second term only when the first misses.

## Flags and failure modes

- **No `cairn sync;` prefix.** `cairn search` syncs by itself; prefixing it fetches the whole store
  twice.
- **Pass `--scope homelab-talos`.** `--all-scopes` still derives a scope from the **cwd's git
  repo**: measured from `/tmp` it returns 0 hunks and **rc 2**. It does name the reason
  (`could not derive a scope from '.'`), so read the banner instead of reading the empty result as
  "nothing recorded".
- **An unreachable pod is not an empty store.** It degrades to the local cache and says so in its
  banner (`SERVED FROM CACHE, cache Nm old`). An absence under a stale cache is an absence *there*.
- **The reader's banner carries stale prose** — it still claims the store is "PER-HOST and
  unreplicated", which predates the pod cutover. Don't relay that verbatim.

## 🔴 Why the guard is spelled `if … ; then … ; fi`

```bash
if command -v cairn >/dev/null; then cairn search 'clawgate' --scope homelab-talos; else echo "skipped: cairn unavailable"; fi
```

Two independent reasons, both measured:

1. **Never `&&`.** With cairn absent, `command -v cairn >/dev/null && cairn search …` exits
   **non-zero** — 1 in bash/zsh, **127 in dash** — and a caller can only read that as the recall
   step having FAILED. The `if` form exits **0**. Measured here under
   `env PATH=/usr/bin:/bin sh -c`: guarded form printed `skipped: cairn unavailable`, rc **0**; the
   `&&` form rc **1**.
2. **The `else` echo is load-bearing.** A bare `if …; then …; fi` with no else branch skips in
   **silence**, which for an autonomous run is indistinguishable from having run and found nothing
   — the exact failure the guard exists to prevent.

⚠ Do not "simplify" it to `command -v cairn >/dev/null; cairn search …` either: the `;` invokes
`cairn` unconditionally and reinstates the rc 127 failure.

⚠ **Reachability is host-local.** `cairn` is a devrc home-manager artifact on Zach's two machines;
an agent running in-cluster has neither the binary nor an allowlisted identity. That is what the
guard makes survivable.

## Writing back: you don't, directly

This file is a READER. A clawgate session that learns something durable **nominates a bullet for
`/handoff`**; the one append protocol for every writer lives in `subsystem-index/SKILL.md` — go
there rather than acting on anything restated here. Do not invent a second write path: a writer
bypassing the one protocol is how the store accumulates unverifiable content.
