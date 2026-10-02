# Handoff: cairn-recall-value-instrumentation — 2026-10-02

## Run this first — the index, one command
```bash
$DEVRC/scripts/cairn-ops/read.sh recall --repo "/home/zach/workspace/devrc"
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make "was this cairn bullet actually USED" a mechanically answerable question, by
INSTRUMENTING the read path rather than mining transcripts for proxies. Two pieces: a recall
**receipt** (one telemetry row per invocation) and an opaque **per-bullet citation id** that
exists nowhere else in the corpus, so a later appearance of that token is evidence rather
than an echo.

🔴 **A SUCCESSOR ARC, NARROWLY SCOPED, AND THE PARENT IS CLOSED.**
`claudedocs/handoff-handoff-corpus-to-cairn.md` is **CLOSED — verdict NOT ADDRESSED**
(operator, 2026-10-02): its condition required an evicted block's own TEXT to reach cairn and
the transport cannot carry it. This arc inherits **only** that arc's rank 2. It does **not**
inherit the eviction/`--prune` question (shipped: rule (r)/(s), `--archive-write`), the
automigration question (**DECIDED: no**, four independent reasons), or the corpus-overlap
measurements (done, and they held).

- **closing-condition:** `check` — both clauses, run in one session:
  ```bash
  # (a) THE RECEIPT: a recall writes a row that the recall itself can be seen in.
  $DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc >/dev/null
  # then: the activity store returns >=1 row with source='tool' naming that invocation
  # (status, scope, entries, bullets_printed, output_bytes, session), where the same
  # query returned 0 cairn rows at round 1.
  # (b) THE CITATION ID: a printed bullet carries an opaque token, and the token is unique.
  $DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc \
    | grep -oE '\[cb:[0-9a-f]{4,}\]' | head -3     # non-empty
  ```
  🔴 **AND A POSITIVE CONTROL ON EACH, because a zero here is the exact failure mode this arc
  exists to escape:** for (a) the query must be shown returning a NON-ZERO count for a run
  that definitely happened — a reassuring 0 is indistinguishable from a query wired to
  nothing; for (b) the token must be shown ABSENT from the corpus before the recall and
  PRESENT after, or it is measuring its own output. ADDRESSED ⇒ arc CLOSED.
- ⚠ **FROZEN AT ROUND 1 — this line is the whole arc.** It deliberately does NOT require that
  the instrumentation prove the value claim. Shipping the instrument is the deliverable;
  reading it is a later arc with its own `n`. The parent arc spent three rounds unclosable
  because its condition encoded an outcome nobody could reach.

## State now
- **Branch / PR: none. Nothing is claimed and nothing is implemented** — this doc is the
  arc's first round. Derive the slug and `claim-work` it before touching code.
- 🔴 **THE DEADLINE IS THE ONLY LIVE CLOCK AND IT IS NOT A SCHEDULING PREFERENCE.** Claude
  Code transcripts span **2026-09-01 → 2026-10-01 exactly** — nothing older, no
  `cleanupPeriodDays` set — so the corpus **rolls at 30 days and does not accumulate**.
  Waiting does not grow `n`; every day deletes a day of the only existing evidence. That is
  why instrumenting outranks measuring: an instrument captures tomorrow's reads, and the
  transcript-mining route is losing its inputs daily.
- ✅ **THE READ HALF OF THE VALUE CLAIM IS ALREADY ANSWERED, AND THE ANSWER IS YES.** 61
  acted-on recall invocations — **23.5% against a 1.8% cross-project control floor** (40× on
  mean fraction), 5 of 6 hand-verified; transcripts retain recall output **1,425/1,425 =
  100%**. This arc is not re-litigating that. What is unreachable is the counterfactual.
- 🔴 **WHAT THIS ARC MUST NOT ASSUME IT INHERITS: the evidence does not cover the content type
  anyone wanted to move.** Every one of those 61 firing instances was a **`## Pointers`
  path**. The 289 closed investigation blocks are prose lessons that often name no path — the
  population the available observable can see LEAST. A citation id is what would close that
  gap, which is the real argument for this arc.
- **Carried forward — a REPLACE heading would drop these values.** `source='tool'` holds **0**
  cairn rows against **1,425** transcript-proven invocations; the documented spelling
  `$DEVRC/scripts/cairn-ops/read.sh recall` is **93 of 1,425 (~6%)** of real traffic (almost
  all of it is bare `cairn recall --repo <path> 2>&1 | head -60`); **746 of 1,425** carry no
  parseable status line because of `| head`/`| tail`.

## Open investigations — live diagnosis state

### 🔴 OPEN — every proxy available today is a SPELLED guard, and the naive one is saturated by a mandate rather than by use
- as-of: 2026-10-01
- **Observed (with values):** the naive proxy "a printed ref reappears later in the session"
  fires **451/454 = 99.3%** — because `/resume` step 5 *requires* the report to carry what
  step 4 recalled. It measures COMPLIANCE with the skill, not use of the bullet. `via: measurement`
- **Observed — the discriminating observable, and it is narrow:** a rare (`df<=3`) *path*
  absent from the transcript BEFORE the recall, later appearing in a **read-shaped** tool
  input. That is what yielded the 61 / 23.5% figure. `via: measurement`
- **Ruled out: the count as first computed (151).** Three artifact classes inflated it, each
  invisible to the count — recall output spilled to a `tool-results/` file the session then
  greps (re-reading recall itself); bare `SCREAMING_CONST` tokens matching unrelated Go
  strings; paths inside `Edit`/`Write` bodies, which are echo into an artifact rather than
  use. `via: measurement`
- 🔴 **Ruled out: that the counterfactual is reachable by sampling.** The `scope-absent`
  control arm is **n=1** (9 corpus-wide) — empty by SELECTION, not sampling. The only
  available comparator (344 ran recall / 68 skipped) needs **≈510/arm** to see a 10% shift in
  message count and **≈10,751/arm** for duration; the small arm has 68. The outcome variable
  is a 19-hour session while the effect is one work item. `via: measurement`
- 🔴 **Ruled out: the parent arc's own next probe.** *"`find-session --skill resume` then ask
  whether the bullet changed what the session did"* cannot falsify anything — that surface
  disagrees with the transcripts by 24%, returned a session that merely READ the skill as its
  top hit, and has no opencode attribution. Do not re-run it. `via: measurement`
- **Leading hypothesis:** a spelled proxy is walkable by rewording and a mandated echo
  saturates it, so no transcript-mined observable can carry this claim. An opaque token minted
  per printed bullet is STRUCTURAL: it cannot be reworded into existence, and its appearance
  anywhere else in the corpus has exactly one explanation.
- **Next probe:** before building, confirm the receipt's destination can actually be written
  and read by the client — i.e. that the activity path reachable from
  `scripts/cairn-ops/read.sh` can emit a `source='tool'` row at all, with a positive control
  showing a non-zero count. That is clause (a)'s instrument, and the parent arc never
  exercised it.

### 🔴 OPEN — the receipt's own measurement surface has two known traps, both of which produced a confident wrong number already
- as-of: 2026-10-01
- **Observed:** `session-summary` rows all pin `ts` to the SESSION START, so
  `argMax(payload, ts)` is a **tie** and returns an arbitrary snapshot. Measured: a session
  read as 26 assistant messages / 2 minutes; its final snapshot says **1,968 / 2,098** — a 75×
  understatement that looked entirely plausible. Key on
  `argMax(payload, JSONExtractString(payload,'end_ts'))`. ⚠ This belongs in the `activity`
  skill and is recorded here because this arc will hit it first. `via: measurement`
- 🔴 **Observed — and the first version of this bullet was the OPPOSITE and WRONG.** The
  `activity` skill's SOPS reader-cred recipe **works**. A prior round recorded it as broken on
  this host; the real cause was `--input-type yaml` placed **after** the `<(git show …)`
  positional, which `sops` ignores before failing to parse ciphertext as JSON — and it prints
  the diagnosis in as many words (*"Flags must always be provided before the first positional
  argument!"*) into a `2>/dev/null`. A/B: flag AFTER ⇒ unmarshal error; flag BEFORE ⇒
  30-character secret. **Do NOT reach for the collector's `activity_writer` file as a
  workaround, and do not "fix" the skill — put the flag before the positional.**
  `via: measurement`
- **Leading hypothesis:** both traps are read-path only; neither blocks emitting a row. They
  are recorded so the arc's first query is not the one that gets believed.
- **Next probe:** none standing — apply both when writing clause (a)'s query.

## Next steps (ranked)

1. **SHIP THE RECALL RECEIPT (clause (a)) — one `source='tool'` row per invocation**, carrying
   `{status, scope, entries, bullets_printed, output_bytes, session}`. Repo: `devrc`; the
   emitting edge is `scripts/cairn-ops/read.sh` and whatever it calls to reach the activity
   store. 🔴 **Instrument the BARE spelling too, or it measures ~6% of traffic** — `read.sh
   recall` is 93 of 1,425 invocations and the rest is bare `cairn recall`. Land it with the
   positive control from the closing condition, not just a green test.
   forcing: deadline — the transcript corpus rolls at 30 days (2026-09-01 → 2026-10-01,
   nothing older, no `cleanupPeriodDays`), so the only evidence that could justify any further
   automation here is deleted daily and waiting does not grow `n`.
2. **STAMP EACH PRINTED BULLET WITH AN OPAQUE CITATION ID (clause (b))** — `[cb:7f3a]`-shaped,
   derived so it is stable per bullet and collides with nothing in the corpus. Both renderers
   must agree, which makes this a **cairn** change, not a devrc one:
   `internal/report` + `lib/subsystem_recall.py`, pinned equal by the cross-language fixture
   (`internal/report/testdata/reader_fixtures.json` — regenerate and diff, never hand-edit).
   ⚠ **This changes rendered bytes, so it will break every devrc guard that pins the pinned
   client's output** — exactly how cairn#162's `tasks`→`refs` rename reddened devrc `main`.
   Bump the devrc pin and update those expectations in the same round.
   forcing: deadline — same clock as item 1; and the citation id is the only observable that
   reaches the prose-lesson population at all, which is what the parent arc's evidence could
   not see.

## Gotchas / decisions / dead-ends
- 🔴 **DECISION (operator, 2026-10-01): DO NOT AUTO-MIGRATE HANDOFF CONTENT INTO CAIRN.** Four
  measured reasons, each fatal alone: (a) the `devrc` scope README forbids it — *"Pointers, not
  copies"*; (b) there is no derivable address — at `DEFAULT_MIN_PATHS=2` **95.8% of closed
  blocks resolve to ZERO entries**, 69.9% name no existing repo path, and where derivation
  fires it lands on the `scripts`/`tests` catch-alls that README disowns; (c) the transport
  cannot carry it — `write.sh` refuses a newline or `>2000` chars at rc **21 before the
  network** and **288 of 289 closed blocks are multi-line**; (d) the value evidence covers
  `## Pointers` paths, not prose. **This arc must not quietly re-open it** — a receipt and a
  citation id are instrumentation, not intake.
- 🔴 **DECISION: the narrow aligned version is a POINTER on eviction, and it is NOT this arc's
  item.** When a run both evicts and already has a resolved cairn entry, emit one line naming
  the archive location. Recorded in the parent doc; listed here only so nobody re-derives it as
  new.
- ⚠ **THE ENFORCED 2000-RUNE BULLET CEILING IS WALKED AND `put` IS THE HOLE.** 83 bullets
  exceed it, largest 4,794 chars; `_put_entry` has no per-bullet check while the append path
  does. Any bounded rule that must be ENFORCED rather than requested has to cover `put`. A
  citation id added per bullet inherits this surface.
- ⚠ **A devrc gate reads the PINNED cairn client, and `cairn_pin` route 2 resolves the
  DEPLOYED one.** `shutil.which("cairn"|"cairn-py")` → `realpath` → `<store>/libexec/cairn/lib`
  reads whatever home-manager last switched to, which can be many commits behind
  `flake.lock`. To reproduce a CI failure locally, set `CAIRN_LIB=<locked lib>` (route 1) or
  build the locked rev — otherwise a passing local run reads as "CI is wrong". Measured
  2026-10-02: deployed `cairn-cdf6fae` vs locked `5c96ffda`, nine commits apart.
- ⚠ **Run devrc's suite via its own dev shell, not an ad-hoc `nix-shell -p`.**
  `scripts/run-tests.sh` GUARD 1 aborts (exit 3, **zero tests run**) when any of
  `REQUIRED_TOOLS` is missing, because ~55 `skipif`s would otherwise silently skip. Correct
  invocation, which it prints itself:
  `nix develop <repo> --command bash <repo>/scripts/run-tests.sh <repo>`.

## How to verify
```bash
# (a) the receipt — the row must exist AND the query must be shown able to return non-zero
$DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc >/dev/null
# then query the activity store for source='tool' cairn rows for this session;
# POSITIVE CONTROL: the same query over a window containing a known-present row type
# must return a non-zero count, or the zero means nothing.

# (b) the citation id — absent before, present after, and unique
$DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc \
  | grep -oE '\[cb:[0-9a-f]{4,}\]' | sort -u | head
# then grep the token across the corpus and confirm exactly one origin.

# the renderers must agree byte-for-byte (clause (b) touches both)
cd /home/zach/workspace/cairn && go test ./internal/report/... && python3 tests/parity/harness.py

# the parent arc, for context only — CLOSED, verdict NOT ADDRESSED
sed -n '/^## State now/,/^## Open/p' $DEVRC/claudedocs/handoff-handoff-corpus-to-cairn.md | head -20
```
