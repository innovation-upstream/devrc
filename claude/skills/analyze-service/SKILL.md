---
name: analyze-service
description: "Recon a service/subsystem fast: locate where it lives, load its config, check its live state and recent changes — then optionally do the follow-on task. Replaces hand-typed 'analyze the {redis,minio,flux,bastion,monitoring,…} setup, then …' recon."
argument-hint: "<service> [then <follow-on task>] — e.g. 'redis', 'externaldns then bump the chart', 'monitoring'"
allowed-tools: Bash, Read, Write, Edit, Grep, Agent
---

# /analyze-service — one deterministic recon call

Input: `$ARGUMENTS`. Split it into:
- **service** — the subsystem to recon (first token / quoted phrase).
- **follow-on** — anything after `then` / `,` / `&&` (optional). Empty → recon only.

## Step 1 — run the recon script. One call, not six.

```
cairn sync; python3 ~/workspace/devrc/scripts/lib/service_recon.py <service>
```

🔴 **`;` — NOT `&&`. The separator is load-bearing and `&&` silently deletes the
whole brief.** `cairn sync` exits **4** whenever the pod is unreachable but a
usable cache survives (that is `sync`'s contract: its job is to REFRESH, so a
stale cache is a failed refresh). Under `&&` that non-zero short-circuits, the
recon never runs, and `/analyze-service` produces **nothing at all** — during an
outage, when the stamped cache would have served the index block at full
fidelity. The paragraph below promises "a failed sync costs you the index block,
never the brief"; `;` is what makes that true.

It performs, in ONE process: resolve the search roots → locate the service →
read the index (via `subsystem_recall`, the store's one reader) → extract the
load-bearing config knobs → `git log` the located directories. The recon itself
is read-only and touches no cluster.

🔴 **The `cairn sync` prefix is what makes the `index:` block current, and the
recon REFUSES an undateable store rather than serving a stale one.** Since the
Cairn cutover the pod is the datastore and `~/.claude/analyze-service-index` is a
FROZEN mirror; the recon now reads the synced cache (`~/.cache/subsystem-store`)
and, if that cache carries no `.sync-stamp`, prints
`index: store-unstamped — the index could not be read: …` and names `cairn sync`.
That degrades ONE section — roots, config and git log still run — so a failed
sync costs you the index block, never the brief. `--store <path>` reads a
directory deliberately and is not refused.

Flags you may need — everything else has a default that is deliberate:

| flag | when |
|---|---|
| `--repo <path>` | the service is in a repo the `$HOMELAB`/`$DATAPACKET` handles don't cover. Repeatable; suppresses the defaults. |
| `--live --context <ctx>` | 🔴 the user asked about **cluster state**. Off by default (see below). `--context` is never inferred. |
| `--files N` / `--knobs N` / `--log N` | the brief truncated something you need. Every cut is COUNTED in the output. |
| `--json` | you are feeding another tool, not reading it. |

🔴 **Live cluster state is OPT-IN.** It was 124 of the 359 Bash calls in the
measured baseline, and a static question never needed it. Pass `--live` only when
the user's question is about what the cluster is *doing right now*. Without it
the brief says `live: OFF … UNVERIFIED` — report that, never infer.

🔴 **Read the script's status words; they are load-bearing.** `not-searched` ≠
`no-match`, `not-attempted` ≠ `ref-absent`, `walk-capped` ≠ finished. Each pair is
"did not look" vs "looked and found nothing", and the brief prints its
denominator (`N files examined`) so a zero can be read. Exit `3` means nothing
could be examined — it is **not** "the service does not exist".

## Step 2 — present the brief

Pass the script's output through, tightened, in this order. Do NOT re-run its
steps by hand; if something is missing, raise the matching limit and re-run.

- **Service** + one-line "what it is". 🔴 **When the `index:` block hit, its `## What it is` IS that line — use it, do not re-invent one.** Until 2026-08-21 no briefing path printed that section (not this block, not `--ref`, not the digest — only `--search`, which briefs nobody), so this bullet was asking for something the tool never handed over and the brief was reconstructed from paths. If the block shows the degrade notice instead — `(no parsable ## What it is — absent, empty, or not parsed as a heading [renamed, indented, fenced, among others]`, then a re-derive instruction — relay it **as written**: it says the PARSE found nothing, not that the entry is silent, and a heading the parser did not match is a real cause — the answer may be on disk one rename away, so grep the entry before deriving it live. (That quotation is pinned to the string the modules emit by `scripts/tests/test_service_recon.py::TestTheSkillQuotesTheDegradeNotice`; it derives the expected text from the code, so reword either side and it goes red.)
- **What it is / pointers / nuance** (`from index`) — the three sections the script's `index:` block surfaces, in that order. Omit if it missed, and when the line carries a `[scope via …]` marker say WHICH scope answered: that scope was not confirmed to own the service.
- **Lives at** — repo + paths, namespace, owning kustomization.
- **Config** — the load-bearing knobs (version, scale, resources, key values, routes, deps).
- **Live** — only if `--live` ran and returned `ran`; otherwise say `unverified (static recon)`.
- **Recent changes** — the log, calling out anything marked `⚠ MOVED` (a revert/bump is usually why you're looking).
- **Gotchas** — anything non-obvious you hit, plus any `⚠` note the script emitted (`MULTI-DIRECTORY`, an ownership tie, a `THIN MARGIN` under `lives at:`, a capped walk).

**Provenance honesty:** nuance/pointers are `from index`; roots/locate/config/log
are `re-derived live`; cluster state is `live @ <context>` or `unverified`. Never
present index recall as live observation. Keep it dense — `file:line` over prose.

🔴 **When the script is not enough.** It locates by path component and reads
YAML knobs; it does not read code. If the answer is in Go/Python/TS, use the
**Grep tool** (not `bash grep`) on the paths the brief already located, or
dispatch an **Explore** subagent returning paths + excerpts. Both are scoped by
the brief, which is the point — an unscoped sweep is the cost this replaces.
(The previous version of this line told you to prefer Grep/Glob for the LOCATE
step. Measured over 20 real runs: `Grep` = 0, `Glob` = 0, because locating is
better done deterministically. The script does it now; Grep is for reading code.)

## Step 3 — then

- **No follow-on** → stop after the brief and wait for direction.
- **Follow-on is investigate/check/explain** → continue inline; the recon is cached in context.
- **Follow-on is implement/build/fix** → dispatch a subagent on a feature branch with test coverage, ending in a PR (unless it's a one-liner). The brief is the subagent's grounding.

## Reference (load only when you need it)

- `~/.claude/skills/analyze-service/reference/index-store.md` — where the index lives, how a ref resolves, the entry schema, 🔴 **store safety** (curated, client-confidential: never stash/reset/push there — and it IS backed up, hourly local + daily off-machine, so read that section before calling a loss unrecoverable), and what each `index:` status means.
- `~/.claude/skills/analyze-service/reference/write-back.md` — **what is worth recording**: what counts as notable, auto-discovered pointers, bloat discipline, and the `created_by: analyze-service` stamp. Read it before writing anything to the store. 🔴 **It does NOT carry the write protocol.** Since 2026-08-31 there is exactly ONE, shared with `/handoff`, in `~/.claude/skills/subsystem-index/SKILL.md` — read the mechanism THERE and nowhere else; it is deliberately not summarised here, because a summary is a copy and copies drift.

## Measurement + pairs

The baseline this replaces, and the harness that re-measures it:
`claudedocs/analyze-service-baseline/` + `scripts/session-analysis/recon_cost.py`
(`--compare claudedocs/analyze-service-baseline/BASELINE.json`).

Pair with `/handoff` (the index is the terse pointer sheet, the handoff carries
the detail — don't duplicate) and `/find-session` (recover a past session).
