# Handoff: handoff-corpus-to-cairn — 2026-10-01

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
Decide where a handoff doc's DURABLE content should live, measured over the ENUMERATED
corpus rather than a sample. The operator's question: handoff docs are getting big — what
in them is already a fit for cairn, why is it not there, and what should cairn support?

🔴 **A NEW ARC, and `--new-effort` is deliberate. Three neighbours were read first and none
of them owns this question.** `handoff-handoff-resume-prune.md` is **CLOSED** (verdict
ADDRESSED at `b1bee35b`) and ported prune *concepts* into the two skills.
`handoff-handoff-doc-prune-exit.md` is **OPEN** and owns whether a delete path EXISTS — it
built rule (q) `--prune`; its closing condition is devrc#1919 merged plus SKILL.md step 5
naming `--prune` first, and this arc does not touch either clause.
`proposal-handoff-doc-bloat.md` (2026-08-29) is a READ-ONLY audit that sampled **18 of 413
docs (4.4%)**. This arc is the orthogonal half all three leave open: **not whether text can
be removed, but where the removed text should GO** — and it measures 187 of 187 docs plus a
cairn-overlap comparison nobody has run.

- **closing-condition:** `check` — a `--prune` eviction of a CLOSED `Open investigations`
  block lands the lesson in **cairn**, not only in `claudedocs/archive/`. Verified by:
  ```bash
  $DEVRC/scripts/cairn-ops/read.sh search '<a phrase from the recorded bullet>'
  ```
  returning a hit, AND the archive file still carrying the block verbatim (eviction must add
  a reader, never move the text out of reach). ADDRESSED ⇒ arc CLOSED.
- 🔴 **THE COMMAND ABOVE IS A CORRECTION, NOT A RE-SCOPE — ROUND 1 WROTE ONE THAT CANNOT
  RUN.** It said `read.sh recall --repo <r> --search '<phrase>'`; `recall` has no `--search`
  flag and exits **2** with a usage line, which a later session reads as "no hit". The verb is
  `read.sh search`. Only the spelling changed. `via: measurement`
- 🔴 **AND THE CONDITION IS NOW KNOWN TO BE UNSATISFIABLE AS CONCEIVED — AN OPERATOR
  DECISION, DELIBERATELY NOT TAKEN HERE.** Round 1 assumed the evicted block's own text
  reaches cairn. It cannot: `cairn append` refuses a newline or >2000 chars **before the
  network** (rc 21) and **288 of 289 closed blocks are multi-line**, 37.7% also over length;
  the `cairn put` route fragments one block into 3–4 bullets and **swallows its `### `
  heading** (the documented `dropped-lines` defect). So "the lesson lands in cairn" can only
  mean a **POINTER** to the archive file. Resolving verbatim-vs-pointer changes what this arc
  is FOR, and the round-1 freeze says that is not a later session's call. **Until an operator
  rules, this arc cannot be closed by anyone.** `via: measurement`

## State now
- ✅ **THE ANALYSIS DOC IS MERGED** — devrc#1950 squashed `6c317f7b0c2a`, and the first
  correction devrc#1952 (`246074ca`). Verified by content, not ancestry.
- 🔴 **FOUR SCOPING AGENTS HAVE ALL REPORTED, AND THEY REFUTE SIX CLAIMS THIS DOC MADE.** The
  corrections are in the Open investigations blocks below. **Do not act on round 1's reasoning
  without reading them** — the refutations are load-bearing, not cosmetic.
- 🔴 **RANK 1 IS DEMOTED. It is a DESIGN PROJECT, not a small change, and it is blocked
  UPSTREAM** on the store's "pointers, not copies" rule and on the intake question (old rank
  2). Building the cairn destination first means designing against a protocol about to move.
  Two agents converged on that independently.
- ✅ **THE VALUE CLAIM IS HALF-CLOSED AND THE ANSWER IS YES** — a cairn bullet has
  demonstrably been read and acted on: **61 invocations, 23.5% against a 1.8% cross-project
  control floor**, 5 of 6 hand-verified. The *counterfactual* half stays unreachable.
- 🔴 **A DEADLINE NOBODY KNEW ABOUT: the transcript corpus ROLLS AT 30 DAYS.** Claude Code
  transcripts span **2026-09-01 → 2026-10-01 exactly**, nothing older, no `cleanupPeriodDays`
  set. Waiting does not grow `n`; every day deletes a day of the only existing evidence. That
  is why instrumenting is now ranked above measuring.
- **Carried forward — a REPLACE heading would drop these values.** 187 docs / 7,514,989 B, 28
  over the 65,536 B base ceiling, largest 6.1× (402,767 B); 69.9% of bytes in the two
  APPEND-only sections; **store 331 entry files / 3,129 dated journal bullets, intake 19–80
  bullets/day sustained through 2026-09-30 — actively written, NOT starved** (the measurement
  that inverted this arc's original hypothesis); 83–98% of durable handoff content
  unrepresented in cairn; devrc's `claudedocs/archive/` at **37 files / 311,415 B** against
  homelab-talos's 0.
- ⚠ **CLOSED-BLOCK POPULATION, RESTATED SO IT IS RE-DERIVABLE:** **289 blocks / 558,672 B**
  across 188 docs, from the in-repo detector `scripts/handoff-audit.py`. Round 1's
  **366 / 700,630 B does NOT reproduce** and its detector lived in a scratchpad and is gone.
  Quote the 289 figure.
- ⚠ **NO `claim-work` CLAIM IS HELD** and no implementation has started.
- **No clawgate task** — `resolve` exited 5 (`0 tasks`); an unknown session id answers 200
  with an empty array, so that zero is not a measured absence.

## Open investigations — live diagnosis state

### 69.9% of the handoff corpus sits in the two sections that are defined as never being replaced
- as-of: 2026-10-01
- **Observed (with values)** — byte weight per `## ` section over all 187 docs, attribution
  checked against file size with a zero-residual control (attributed + preamble == total):
  | section | bytes | % | bucket |
  |---|---|---|---|
  | Gotchas / decisions / dead-ends | 3,201,095 | 42.6% | APPEND |
  | Open investigations | 2,049,919 | 27.3% | APPEND |
  | State now | 520,962 | 6.9% | REPLACE |
  | Next steps (ranked) | 518,325 | 6.9% | REPLACE |
  | How to verify | 311,488 | 4.1% | REPLACE |
  | Goal + closing-condition | 82,958 | 1.1% | REPLACE |
  | non-template headings | 551,099 | 7.3% | mixed |
  The four bounded sections total **19.0%**. `via: measurement`
- **Ruled out:** that the growth is verbosity per session. The APPEND/REPLACE split is the
  write contract in `handoff_doc.py` — two thirds of the document is *defined* as permanent,
  so size is a property of the schema, not of any author. `via: code`

### The Gotchas section is already cairn's declared content type, and cairn can physically hold it
- as-of: 2026-10-01
- **Observed (with values):** the subsystem-index protocol defines a journal bullet as "a
  durable lesson — a gotcha, a decision, why it was touched". A random 14 of 5,868 Gotchas
  bullets each name a specific file, tool or API (`audit-dispatch.py` resolves the repo from
  CWD; `resume-state.sh` takes a PATH and `~` in quotes is not expanded; `GET /servers` omits
  `datacenter` for some servers) — **path-anchored, which is cairn's addressing model.**
  | | handoff Gotchas bullets | cairn journal bullets |
  |---|---|---|
  | count | 6,711 | 3,129 |
  | median | 400 B | **823 B** |
  | p90 / max | 761 B / 10,609 B | 1,931 B / 4,898 B |
  Store bullets are **2× the size** of the handoff bullets they would absorb, and only
  **8 of 6,711 (0.1%)** exceed the largest bullet the store already holds. `via: measurement`
- **Ruled out:** that the ≤2-line cap in the protocol is what keeps the content out. The
  store's own median bullet is 823 B, far past two lines — the cap is already not binding on
  what is written. `via: measurement`

### …and 83–98% of it has no cairn counterpart, with the negative control at 0.0%
- as-of: 2026-10-01
- **Observed (with values):** word-5-shingle containment of every durable handoff item
  against its OWN scope's journal bullets, threshold 0.30 — cairn 12/498 Gotchas and 0/11
  closed investigations; devrc 79/3,263 and 1/169; homelab-talos 79/2,831 and 0/143.
  **Total 171 of 6,915 (2.5%).** `via: measurement`
- 🔴 **One threshold is not a claim, so it was swept, and the control is what makes it
  readable:** 5-gram at 0.30/0.15/0.08/0.04 → 2.4/5.8/9.0/10.8%; 3-gram at the same →
  5.2/10.2/12.4/16.9%. The same bullets matched against an **unrelated scope** score
  **0.0%** at every setting but the loosest (2.8%), so the matcher reads content, not shared
  vocabulary. Honest bound: **83–98% unrepresented**, and the measure is near-verbatim reuse
  rather than semantic coverage. `via: measurement`
- **Best-defined subset:** **366 CLOSED investigation blocks, 700,630 B** (34.5% of all
  investigation bytes; 692 blocks / 1,329,279 B remain open). Already resolved, already
  durable, nothing live depends on them, and they are the exact shape of a `RESOLVED:`
  bullet. `via: measurement`

### 🔴 OPEN — the eviction route works and is used, and its destination has LESS readership than its source
- as-of: 2026-10-01
- **Observed (with values):** `claudedocs/archive/` — **devrc 37 files / 311,415 B**,
  cairn 1 / 22,127 B, **homelab-talos 0** against 3,392 KiB of handoff docs and the two
  largest documents in the corpus (402,767 B and 325,644 B). So the selection work — deciding
  which closed text is durable — **has already been done 37 times**, and every time it landed
  in a sibling markdown file. `/resume` step 4 reads cairn on every resume in every repo; it
  reads `claudedocs/archive/` never. `via: measurement`
- 🔴 **RETRACTED, MINE, THIS SESSION: "devrc and homelab-talos have ZERO archive docs."** I
  globbed `claudedocs/archive-*.md` at maxdepth 1; the real location is the
  `claudedocs/archive/` SUBDIRECTORY, so the true devrc figure is 37 files / 311,415 B, not
  0. Caught only because a `merge --ff-only` of the base clone listed
  `claudedocs/archive/handoff-mesh-blackouts-2026-10-01.md` as an added file. **A glob's zero
  is a claim about the glob's shape.** The corrected finding is STRONGER than the one it
  replaces. `via: measurement`
- **Ruled out:** that `handoff_doc.py` has no cairn coupling to build on. It already does
  `cairn_pin.ensure()` and `from subsystem_resolver import parse_journal_bullets` — it
  borrows the store's `OPEN:`/`RESOLVED <sha>:` marker grammar and writes nothing back. The
  coupling is grammatical and one-way. `via: code`
- **Leading hypothesis:** this is the cheapest-legal-response failure, not neglect. The
  ceiling has an escape valve — `handoff_budget.MAX_BYTES` is 65,536 with **82 grandfathered
  ceilings on record**, `GRANDFATHER_STEP` 16,384, max **425,984 B**, and 72 of the 82 are
  `foreign:` handles for repos other than the one holding the gate file (only devrc carries
  `scripts/tests/test_handoff_doc_size.py`). Raising one doc's ceiling costs the writer
  nothing; routing a lesson to cairn costs a decision. `via: code`
- **Next probe:** read `handoff_doc.py`'s `--prune` implementation around the archive-path
  construction and decide whether the cairn write is a second destination or a replacement —
  then watch it go RED on a prune that writes only the archive file.

### The asymmetry that makes the doc the default sink is written into the two skills
- as-of: 2026-10-01
- **Observed (with values):** `/handoff` step 5 (land the DOC) carries nine refusals, two
  ratchets, a leak gate and a push gate. Step 4 (write CAIRN) says in as many words: *"Its
  outcome is a REPORT, not a gate… Declining to write is a normal, frequent result."* The
  enforced path is the document; the optional path is the store. `via: doc`
- **Observed:** the protocol caps cairn at **one dated bullet per touched entry** and **"at
  most one, or none"** new entry per run, while the doc's append sections are uncapped per
  run. A session producing twenty lessons can route one or two to cairn and all twenty to the
  doc. `via: doc`
- 🔴 **Ruled out: that the cap defends a real cost.** The same protocol says *"Decline on
  CONTENT, never on cost — an index entry does not cost a session anything… an extra entry
  adds one index row, ~14 tokens."* The cap and that sentence contradict each other.
  `via: doc`
- **Leading hypothesis:** the cap is the throughput valve. Remove it and the enforcement
  asymmetry stops mattering, because the writer no longer has to choose.

### UNMEASURED and named so nobody reads it as covered
- as-of: 2026-10-01
- **What is NOT established:** whether anyone has ever READ a cairn bullet they would
  otherwise have re-derived. Every number above is about the corpora and the tooling; the
  value claim ("routing to cairn saves re-derivation") is **unmeasured**. `via: assumed`
- **Next probe:** `find-session --skill resume` over sessions that ran step 4, then ask
  whether the recalled bullet changed what the session did. That is the only measurement that
  could falsify the whole arc.

### ❌ REFUTED — "`--prune` evicts closed text to `claudedocs/archive/`, which is read by nothing". BOTH HALVES ARE FALSE
- as-of: 2026-10-01
- **Observed:** `handoff_doc.py` (6,887 lines) contains **one** file write (`:6794`),
  path-limited to the doc, and exactly two `archiv` hits — a comment (`:3068`) and rule (p)'s
  **remedy string** (`:3582`), i.e. prose telling a human to move text by hand. No script in
  `scripts/` or `claude/` creates `claudedocs/archive/`. devrc's 37 files came from a one-off
  bulk `git mv` (`6fa3e13f`, #1627, 35 docs, all `R100`) plus hand-written files since.
  Positive control: `grep -c prune` → 93 in the same file. `via: measurement`
- **Observed:** the archive is **indexed and searchable** — 37 of 143 indexed devrc handoff
  docs sit under it; `resume-state.sh:452` accepts it as a handoff directory,
  `find-session.py:1152` probes it. The honest claim is `resume-state.sh:415-427`'s own: they
  *"remain INDEXED … so `handoff_search` keeps returning them"*, and only the no-argument
  `/resume` fallback globs are non-recursive — **named explicitly resolves; guessed at does
  not.** A real readership asymmetry, far weaker than round 1's premise. `via: measurement`
- **Consequence:** the change is not "repoint a destination"; it is **"give `--prune` a
  destination at all"**, which is strictly larger, and rank 1's justification must be restated
  on the narrower asymmetry.

### ❌ REFUTED — "the one-bullet cap contradicts the protocol's own cost sentence". A CATEGORY ERROR, MINE
- as-of: 2026-10-01
- **Observed:** the *~14 tokens* sentence is about index **ROWS** (entries); the cap governs
  **BULLETS**. Measured at five scopes, a row costs **16.6–18.8 tokens** (so ~14 is understated
  19–34%, otherwise sound). A bullet on the **featured** entry costs **223–326 tokens —
  13–16×** — and the featured entry is always the one just worked on, by construction
  (`FocusMinPaths = 1`). The two figures differ by an order of magnitude and measure different
  objects. `via: measurement`
- 🔴 **Observed — the cap is PROSE ONLY and is already walked with no consequence.** It lives
  in one file (`subsystem-index/SKILL.md:126,140,155`); `subsystem_touch.py:4871` renders it as
  advice with no branch, `:4135` as a comment; `JOURNAL_BULLET_MAX_LINES=6` truncates the
  *quote*, not the write; no test pins any of it. From the pod's attribution trailer:
  **26.6% of 582 sessions wrote more bullets than entries touched** (worst case today, 8
  bullets to one entry). De-facto intake is **2.01 bullets/session** against a stated cap of 1.
  `via: measurement`
- 🔴 **Observed — and this explains a puzzle round 1 could not.** The cap is walked by
  CONCATENATION: 145 bullets pack `(a) … (b) …` sub-lessons into one, median **1,588 B** vs
  790 B. **The cap does not reduce intake; it reshapes it into fewer, larger, less-retrievable
  bullets** — and `cairn search` scores the whole body, so five lessons share one relevance
  score. That is why store bullets measured 2× the handoff bullets they would absorb.
  `via: measurement`
- **Ruled out: that the cap is the binding constraint.** Over 90 recent commits, **54% reach
  the write step with nothing writable** (no resolved entry, no nomination) and a further 24%
  named an entry below `min_paths=2`. The cap can bite on at most the 36% that resolve
  something, and in 20% of runs it already permits ≥2 because it is per-*entry*. **The
  nomination floor is upstream of it.** `via: measurement`

### 🔴 OPEN — the slug cannot be derived, and where derivation DOES fire it fires WRONG
- as-of: 2026-10-01
- **Observed:** resolving every closed block's backticked repo-existing paths through the real
  resolver, at two thresholds: at `DEFAULT_MIN_PATHS=2` **95.8% resolve to 0 entries** and
  1.7% to exactly one; at the most generous `min_paths=1`, 76.8% / 16.3%. **69.9% name no
  existing repo path at all.** Instrument positive-, negative- and extractor-controlled.
  `via: measurement`
- 🔴 **The failure is not sparsity, it is misdirection.** A lesson about the handoff toolchain
  resolves to `scripts` + `tests` — catch-all directory entries the devrc scope README
  explicitly disowns (*"Not entries: bare directory names…"*) — while the entry a human would
  pick, `devrc/handoff-index.md`, exists and is **unreachable by path derivation**.
  `via: measurement`
- **Leading hypothesis:** there is no defensible mechanical rule; 84–98% of the population
  needs a human choice per block. `--prune` can at most accept a `--cairn-ref` the caller
  names, which makes the feature a thin wrapper around work a human already does and drops the
  implied 366-block scale to one block at a time.
- **Next probe:** none for derivation — it is answered. The open question is the operator's
  verbatim-vs-pointer ruling in `## Goal`.

### ✅ HALF-RESOLVED 2026-10-01 — a cairn bullet IS read and acted on; the counterfactual is not reachable
- as-of: 2026-10-01
- **What this settles:** round 1's UNMEASURED block asked whether anyone has ever read a cairn
  bullet they would otherwise have re-derived. **The READ half is YES.** 61 acted-on recall
  invocations — **23.5% against a 1.8% cross-project control floor** (40× on mean fraction),
  5 of 6 hand-verified. Transcripts retain recall output **1,425/1,425 = 100%**.
  `via: measurement`
- 🔴 **The naive proxy is saturated BECAUSE THE SKILL MANDATES THE ECHO.** `/resume` step 5
  requires the report to carry what step 4 recalled, so "a printed ref reappears later" fires
  **451/454 (99.3%)** and measures compliance. The discriminating observable separates ACTION
  from ECHO: a rare (df≤3) *path* absent from the transcript before the recall, later appearing
  in a **read-shaped** tool input. `via: measurement`
- **Ruled out:** the count as first computed (151). Three artifact classes inflated it, each
  invisible to the count — recall output spilled to a `tool-results/` file the session then
  greps (re-reading recall itself); bare `SCREAMING_CONST` tokens matching unrelated Go
  strings; paths inside `Edit`/`Write` bodies, which are echo into an artifact. `via: measurement`
- 🔴 **Ruled out: that the counterfactual is reachable.** The `scope-absent` control arm is
  **n=1** (9 corpus-wide) — empty by SELECTION, not sampling. The only available comparator
  (344 ran recall / 68 skipped) needs **≈510/arm** to see a 10% shift in message count and
  **≈10,751/arm** for duration; the small arm has 68. And the outcome variable is a 19-hour
  session while the effect is one work item. `via: measurement`
- 🔴 **Ruled out: round 1's own next probe.** *"`find-session --skill resume` then ask whether
  the bullet changed what the session did"* cannot falsify anything — that surface disagrees
  with the transcripts by 24%, returned a session that merely READ the skill as its top hit,
  and has no opencode attribution. `via: measurement`
- ⚠ **BEARS DIRECTLY ON RANK 1 AND IS THE LEAST WELCOME FINDING:** every firing instance was a
  **`## Pointers` path**. The 289 closed blocks are **prose lessons often naming no path** —
  the population this observable can see LEAST. The evidence that bullets get used does not
  transfer to the content rank 1 proposes moving. `via: measurement`
- **Next probe:** instrument rather than measure — the recall receipt and the citation ids in
  ranked item 2. The 30-day roll makes that the only order that works.

### 🔴 OPEN — a `/resume` in devrc pays ~50,000 tokens for step 4 TODAY, and nothing observes it
- as-of: 2026-10-01
- **Observed:** of a 192,047 B / 50,772-token default recall read, **97.6% is ONE featured
  body** — `devrc/tests.md` at 151 bullets (`civitai/blocks.md` 112 next). Index view is 1.2%.
  `--list` renders the same scope **38× smaller** (5,079 B), yet `/resume` and `/handoff` both
  run the bare default. `--page` caps the listing, not the body. `via: measurement`
- **The cliff is per-ENTRY bullet count, not per-scope**: the index scales at ~17 tok/row and
  pages at 100, which the largest scope (77) has not reached. `via: measurement`
- **Leading hypothesis:** this is the real cost surface, it is nobody's item, and it is worth
  more than either intake rank. Remedy is one badge beside the existing `🔴 N OPEN`:
  `⚠ 151 nuance — OVER 60, prune or split`.
- ⚠ **Ordering consequence for rank 1:** 289 evicted blocks become **bullets**, landing in
  featured bodies (~+450 tokens/read over ~190 entries). **Land the badge before any prune.**

### 🔴 OPEN — `## Requirements` is SHIPPED READ-ONLY: a working reader, no writer, and a silent validation hole
- as-of: 2026-10-01
- **Observed:** 4 of 467 entries carry it, all in the `cairn` scope, all `created_by: handoff`,
  7 bullets total. Reader exists in BOTH implementations and renders correctly (verified live:
  four badge rows agreeing bullet-for-bullet with disk). **Nothing writes it** —
  `new_entry_template()` emits only `What it is`/`Pointers`/`Nuance`; zero mentions in the
  append path, the server write path, all 37 skills, and all 11 store schema READMEs, each with
  a positive control. `internal/store/requirements.go:18-20` says so itself: *"nothing writes a
  requirement yet."* `via: measurement`
- **Ruled out: that it is a rival to the journal markers.** `Requirement` **embeds**
  `JournalBullet` — same grammar, different section. The journal won openness weeks earlier and
  at scale: **545 markers / 146 of 319 entries / 23 scopes** vs **7 / 4 / 1**. Re-measured in
  the post-ship window: 60 entries took journal bullets across 11 scopes; the same 4 kept
  Requirements. `via: measurement`
- **What it uniquely has, and it is nearly unexercised:** provenance — `grep provenance
  internal/store/journal.go` → **0 hits**. The journal can say a thing is open, never who
  asked. But 9 of 10 provenance tokens sit inside those 4 entries, and 3 of 7 bullets are
  `(inferred)` — agent-self-issued, the class cairn finding #10 measures as **deletion-immune**
  in the audit tooling. `via: measurement`
- 🔴 **The real hazard, and it is the permanently-red-gate shape:** the caveat legend invites a
  writer to use the section, and `validate.go:161` + `ShapeHeadings` **exclude** it — so a
  mis-spelled marker there gets **no signal from any surface**, silently, on the deployed pod.
  Filed as cairn finding #9. `via: code`
- **Next probe:** land finding #9, or declare the section operator-hand-written-only in one
  sentence of the write protocol. Do not build a new open-business surface beside it.

## Next steps (ranked)

🔴 **NUMBERING RESTARTS HERE BECAUSE ROUND 1's RANKS WERE BUILT ON REFUTED PREMISES.** Old
rank 1 is now item 6; old ranks 2/3/4/5 are items 5/7/4/2. `claim-work` slugs derived against
round 1's numbering are STALE — re-derive before claiming.

1. **FIX THIS DOC'S OWN CORRECTIONS — the closing condition cannot be run and the headline
   population cannot be re-derived.** The `search`-verb fix is in `## Goal` above; what remains
   is the operator's verbatim-vs-pointer ruling, without which **nobody can close this arc**.
   forcing: gate — a close-check against the round-1 command exits 2 and reads as "no hit", so
   the arc's own gate is currently broken rather than merely unmet.
2. **INSTRUMENT the value claim instead of measuring it — a recall receipt and opaque per-bullet
   citation ids.** Have `read.sh recall` emit one `source='tool'` row (`{status, scope, entries,
   bullets_printed, output_bytes, session}`) — that table holds **0** cairn rows against 1,425
   transcript-proven invocations — and stamp each printed bullet `[cb:7f3a]` so "was this bullet
   used" becomes a match on a token existing nowhere else in the corpus: a STRUCTURAL guard
   where every proxy available today is a spelled one walkable by rewording.
   forcing: deadline — the transcript corpus rolls at 30 days (2026-09-01 → 2026-10-01, nothing
   older), so the only existing evidence is being deleted daily and waiting does not grow `n`.
3. **MAKE `--prune` VERIFY RATHER THAN WRITE.** Refuse (exit 16) a prune of durable lines
   unless `--archive <path>` is given, the file exists, and **every pruned non-blank line is
   present in it** — mechanising the hand check `af578a02` performed in prose. No pod, no slug,
   no network; clean RED proof (a prune with no archive is accepted today). ~120 lines + tests.
   forcing: gate — `--prune` can today drop durable text with no archive anywhere, and the only
   thing that has ever checked conservation is one commit message.
4. **Record the no-backfill decision with the CORRECTED reason, and the per-entry bullet badge
   with it.** A bullet backfill adds **zero index rows**; the cost is the featured body
   (+21 bullets/entry ≈ +4,700 tokens/read). Round 1's "14 rows for one scope" was cairn's
   scope; live today devrc 36, civitai 44, homelab-talos 46, datapacket-talos 77.
   forcing: none
5. **Drop ONLY the new-ENTRY half of the intake cap.** That is the one place the ~17-token cost
   sentence genuinely applies and the cap genuinely contradicts it; zero effect on the featured
   read. Hold the per-bullet cap at 1→3 until the nomination floor is measured — landing it as
   *"the cap was the throughput valve"* would write a refuted diagnosis into the protocol.
   forcing: none
6. **DEMOTED — the cairn write destination.** Blocked on item 1's ruling, on the slug problem
   (95.8% resolve to 0 entries at the real threshold), and on `append`'s newline/2000-char
   refusal. If ever built: **advisory and non-blocking**, running last, printing `cairn: NOT
   RECORDED` with the manual command on rc 6/7/8/21/24 at exit 0 — otherwise an outage blocks
   `/handoff`'s only landing step. Exactly ONE `append` per run, never a `put` (no `If-Match`
   to lose). Scope from `scope_for_repo(args.repo)`, **never an override** — client-repo →
   `devrc` scope is the one real leak path and the protocol actively instructs it.
   forcing: none
7. **Close cairn finding #9 or declare `## Requirements` hand-written-only.** A mis-spelled
   marker there is silent on the deployed pod.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **A GLOB'S ZERO IS A CLAIM ABOUT THE GLOB, NOT ABOUT THE TREE, AND IT COST A WRONG
  FINDING HERE BEFORE A `merge --ff-only` CAUGHT IT.** `claudedocs/archive-*.md` at maxdepth
  1 returns 0 for a corpus holding 37 files, because the real path is the
  `claudedocs/archive/` subdirectory. The retraction is in the Open investigations block; the
  reusable part is that the correction arrived from an unrelated command listing an added
  file, not from re-reading the glob.
- 🔴 **A SELF-MATCH IS NOT A POSITIVE CONTROL.** The first overlap run scored each item
  against itself and reported 100%, which is true of any matcher and proves nothing. The
  control that earned the number was a **cross-scope** run: the same items against an
  unrelated scope's bullets, which returned 0.0%.
- ⚠ **`initiative-scan.py` answers about SESSIONS, not about documents.** Asked for recent
  handoffs it returned 27 cairn initiatives keyed on session titles ("Proceed as
  recommended", "//handoff"), none of which names a doc. The doc-level question needs the
  corpus enumerated directly.
- ⚠ **THE `activity` SKILL'S SOPS READER-CRED RECIPE IS BROKEN ON THIS HOST** — the documented
  `sops -d --extract '["stringData"]["reader-password"]'` returns empty and
  `activity_reader` then fails CH auth with `AUTHENTICATION_FAILED`. The working route is the
  collector's own file, `~/.config/activity-collector/env` (`activity_writer`), which the
  deadman already uses. Positive control on the working creds: 94,858 rows in 7d.
- **Decision: the doc lives in `devrc`, not in `cairn`.** The subject is this toolchain
  (`handoff_doc.py`, `handoff_budget`, the handoff/subsystem-index/resume skills). The
  routing rule is to ask which repo the lesson is ABOUT, not which one the session stood in —
  and `cairn` is PUBLIC.
- **Decision: a new arc rather than an update to `handoff-handoff-doc-prune-exit.md`.** That
  arc owns whether a delete path exists; this one owns where the deleted text goes. Its
  closing condition is untouched by anything here.

- 🔴 **RETRACTED, MINE, SAME SESSION, AND IT WAS SELF-INFLICTED TWICE OVER: "the `activity`
  skill's SOPS reader-cred recipe is broken on this host" IS FALSE.** The recipe works and
  returns the password. My invocation put `--input-type yaml` **after** the `<(git show …)`
  positional argument; `sops` then ignores it, tries to parse the ciphertext as JSON, and
  fails with `Could not unmarshal input data: invalid character 'a'`. It also prints the
  diagnosis in as many words — *"Flags must always be provided before the first positional
  argument!"* — and **I had sent that to `2>/dev/null`**, which turned a named, self-describing
  error into an empty string indistinguishable from a dead credential. A/B measured: flag
  AFTER the positional ⇒ unmarshal error; flag BEFORE ⇒ 30-character secret. **So the
  reusable lesson is the opposite of the one first written here: do NOT reach for the
  collector's `activity_writer` file as a workaround, and do not "fix" the skill — put the
  flag before the positional.** The wider rule, which is what actually failed: `2>/dev/null`
  on a command whose failure mode you have not yet seen converts a diagnosis into an absence,
  and an absence cannot distinguish two mechanisms.

- 🔴 **THE HANDOFF WRITE-BACK GUARD COUNTS A NEGATIVE-CONTROL PATH AS A DOC READ, AND RE-ARMS
  ON IT AFTER THE SESSION HAS ALREADY WRITTEN ITS HANDOFF.** Measured here: this session wrote
  and merged its handoff, then ran `git cat-file -e origin/main:claudedocs/handoff-no-such-doc-xyz.md`
  as the **negative control** proving an absent path reports absent — a filename invented to
  not exist. The guard recorded that as "this session read `handoff-no-such-doc-xyz.md`" at a
  timestamp POSTDATING the real handoff write, and stopped the turn demanding a handoff for it.
  **Two separate defects:** (a) a path that `cat-file -e` proves ABSENT is counted as a read, so
  verifying a doc is missing is indistinguishable from reading it; (b) the satisfied-ness is
  keyed per-doc-read rather than per-session, so a later read re-arms a guard the session has
  already discharged — which is how a correct `--dismiss` becomes routine, and a guard whose
  dismissal is routine has stopped being read. ⚠ **Do not "fix" this by dropping the control** —
  the control is the right practice; the guard's read-detection is what is wrong. The honest
  response this time was an UPDATE (the doc's `State now` had genuinely gone stale on the merge),
  not a dismissal, so the false positive cost nothing here and will not always.

- 🔴 **SIX CLAIMS IN THIS DOC'S FIRST ROUND WERE WRONG, AND THE PATTERN IS ONE THING: I
  MEASURED THE CORPUS AND REASONED ABOUT THE TOOL.** Every refuted claim was about *mechanism*
  (what `--prune` writes, who reads the archive, what the cap governs, what a slug derives
  from); every surviving claim was about *bytes I counted*. The corpus measurements all held.
  **The lesson is not "measure more" — it is that a file-count is not a claim about the code
  that produced it, and reading `handoff_doc.py` would have cost one command each time.**
- 🔴 **A SCRATCHPAD DETECTOR MAKES A HEADLINE NUMBER UNFALSIFIABLE.** Round 1's
  366 / 700,630 B cannot be reproduced: the script was never committed, and the in-repo
  detector gives 289 / 558,672 over *one more* doc. A number whose instrument is gone is not a
  measurement anyone can check — **quote a figure only from a detector that lives in the repo,
  or commit the detector in the same change.**
- 🔴 **`session-summary` ROWS ALL PIN `ts` TO THE SESSION START, SO `argMax(payload, ts)` IS A
  TIE AND RETURNS AN ARBITRARY SNAPSHOT.** Measured: a session read as 26 assistant messages /
  2 minutes; its final snapshot says **1,968 / 2,098** — a 75× understatement that looked
  entirely plausible. Key on `argMax(payload, JSONExtractString(payload,'end_ts'))`. Belongs in
  the `activity` skill.
- ⚠ **STEP 4's DOCUMENTED SPELLING IS ~6% OF REAL TRAFFIC.**
  `$DEVRC/scripts/cairn-ops/read.sh recall` is **93 of 1,425** invocations; essentially all of
  it is bare `cairn recall --repo <path> 2>&1 | head -60`. Any study keyed on the documented
  command string measures a sixth of the population — and `| head`/`| tail` is also why **746
  of 1,425** invocations carry no parseable status line.
- ⚠ **THE ENFORCED 2000-RUNE BULLET CEILING IS WALKED, AND `put` IS THE HOLE.** 83 bullets
  exceed it, largest 4,794 chars; `_put_entry` has no per-bullet check at all while the append
  path does. Any bounded intake rule that is to be enforced rather than requested must cover
  `put`.
- **Decision: do NOT unilaterally re-scope the closing condition.** It is frozen at round 1,
  and the verbatim-vs-pointer question changes what the arc is FOR. The broken *command* was
  fixed (it could never run); the *substance* is left to the operator, and the arc is declared
  unclosable until they rule.
- ⚠ **A guard whose premise I asserted from a REMEDY STRING.** Round 1 read
  `handoff_doc.py:3582` — prose telling a human to move text to an archive — and recorded it as
  what the tool DOES. **A remedy string describes work that is not done; a comment is a claim,
  and so is a help text.**

## How to verify
```bash
# the corpus and section weights (re-derives every number in the first block)
python3 - <<'EOF'
from pathlib import Path
W=Path('/home/zach/workspace'); t=n=0
for r in ('cairn','devrc','homelab-talos'):
    for f in (W/r/'claudedocs').glob('handoff-*.md'):
        t+=f.stat().st_size; n+=1
print(n,'docs',t,'B')
EOF

# the store side
$DEVRC/scripts/cairn-ops/read.sh recall --repo /home/zach/workspace/devrc | head -20
find ~/.cache/subsystem-store -name '*.md' | wc -l

# the eviction destination — the finding that drives rank 1
find ~/workspace/devrc/claudedocs -path '*/archive/*.md' | wc -l    # 37
find ~/workspace/homelab-talos/claudedocs -path '*/archive/*.md' | wc -l  # 0

# the escape valve
python3 -c "
import sys; sys.path.insert(0,'/home/zach/workspace/devrc/scripts/lib')
import handoff_budget as b
print('ceiling',b.MAX_BYTES,'grandfathered',len(b.GRANDFATHERED),'max',max(b.GRANDFATHERED.values()))"
```
