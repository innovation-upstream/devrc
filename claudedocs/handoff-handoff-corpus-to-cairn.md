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
  block lands the lesson in **cairn** as a `RESOLVED:` journal bullet, not only in
  `claudedocs/archive/`. Verified by: the evicted lesson is returned by
  `$DEVRC/scripts/cairn-ops/read.sh recall --repo <repo> --search '<a phrase from it>'`
  with a non-zero hit, AND the archive file still carries the verbatim text (eviction must
  add a reader, never move the text out of reach). ADDRESSED ⇒ arc CLOSED.

## State now
- ✅ **THIS DOC IS MERGED.** devrc#1950 squashed as `6c317f7b0c2a` (2026-10-01T05:43:07Z), branch
  deleted, base clone re-synced. Verified **by content, not ancestry** — a squash never makes the
  branch head an ancestor: the doc is present on `origin/main` at 16,624 B and carries the SOPS
  retraction, with an absent-path negative control confirming the check can report absent. All
  four Tekton legs were green BEFORE the merge, read from the runners' own counts rather than
  from `mergeStateStatus` (`CLEAN` only ever meant "no conflict"): pytests
  **collected=24744 passed=24737 failed=0** against a 21,193 floor, gotests **461/0**,
  nodetests **1720/0**, plus the pinned-cairn-client leg.
- 🔴 **FOUR SCOPING AGENTS ARE IN FLIGHT AND THEIR RESULTS ARE NOT IN THIS DOC.** Dispatched
  read-only, one per ranked item, 2026-10-01 ~05:45Z. **If this doc is read before they are
  recorded, the briefs below are what was asked — not what was found:**
  - **rank 1** — the `--prune` insertion point in `handoff_doc.py`, add-vs-replace, the RED
    proof, and the crux: **how an arc-scoped evicted block acquires a per-subsystem
    `<scope>/<slug>`**, plus which eviction directions the public/confidential split forbids.
  - **ranks 2+4 paired** (they pull opposite ways, so splitting them yields two half-answers):
    is the one-bullet cap prose or code; does the "~14 tokens per index row" cost claim survive
    measurement; where is the `recall` readability cliff; **and is the binding constraint the cap
    at all, or `nominate()`'s ≥2-path requirement.**
  - **rank 3** — is `## Requirements` failed, never-shipped or redundant; decisive test is
    whether it appears in `--template` at all, and whether the `OPEN:` journal markers already won.
  - **rank 5** — a falsifiable design for the value claim, or an honest "not measurable with
    these surfaces"; the hard part is an observable separating *read and used* from *printed and
    ignored*.
- ⚠ **NO `claim-work` CLAIM IS HELD.** Nothing is being implemented — these are scoping reads.
  A claim belongs on whichever item is actually worked, and the ranked list is a shared queue.
- **Carried forward — a REPLACE heading would drop these values.** 187 docs / 7,514,989 B, 28
  over the 65,536 B base ceiling, largest 6.1× (402,767 B, 114 investigation blocks); 69.9% of
  bytes in the two APPEND-only sections; **store 331 entry files / 3,527,479 B / 3,129 dated
  journal bullets, intake 19–80 bullets/day sustained through 2026-09-30 — the store is actively
  written, NOT starved** (that measurement is what inverted this arc's original hypothesis, so it
  must not be compressed away); 83–98% of durable handoff content unrepresented in cairn;
  **366 CLOSED investigation blocks / 700,630 B** as the eviction candidate; devrc's
  `claudedocs/archive/` at **37 files / 311,415 B** against homelab-talos's 0.
- **No clawgate task** — `resolve` exited 5 (`0 tasks`), and an unknown session id answers 200
  with an empty array, so that zero cannot separate "touched no task" from "wrong id".

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

## Next steps (ranked)

1. **Repoint `--prune`'s destination at cairn for CLOSED investigation blocks only.** 366
   blocks / 700,630 B — the one population where eviction is unambiguously safe. Keep the
   archive file (the text must stay verbatim somewhere); ADD the cairn bullet. This is the
   closing condition, and it needs no new concept: `RESOLVED:` already exists and
   `handoff_doc.py` already imports the grammar.
   forcing: gate — the doc corpus has a byte gate with an 82-entry grandfather ladder, so
   growth is currently absorbed by raising ceilings rather than by moving text.
2. **Drop the one-bullet-per-entry cap in the subsystem-index protocol, or state the cost it
   defends.** It contradicts that protocol's own cost argument, and it is the valve that
   makes the doc the default sink.
   forcing: none
3. **Diagnose why `## Requirements` died before building any new home for OPEN
   investigations.** It appears in **4 of 467** store entries. There is already a designed
   surface for declared-open business (`OPEN:` markers, the `🔴 N OPEN` and `🔴 N REQ OPEN`
   badges, `## Requirements`) and it is effectively unused; shipping a second one beside it is
   the permanently-red-gate shape in a different hat.
   forcing: none
4. **Do NOT backfill Gotchas wholesale — recorded as a decision so a later session does not
   re-open it.** 6,711 items against a 3,129-bullet store would more than double it in one
   move and make `cairn recall` unreadable (the index already prints 14 rows for one scope).
   Route NEW lessons and let the backlog age out.
   forcing: none
5. **Measure the value claim** (the UNMEASURED block above). Until then this arc's premise is
   assumed, not shown.
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
