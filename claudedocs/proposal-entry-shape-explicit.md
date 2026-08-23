# Proposal — make the `/analyze-service` entry shape explicit

**Status:** proposal, 2026-08-19. Nothing here is built.
**Scope:** the entry grammar of the `/analyze-service` index, and the tooling that reads and
writes it. **Read-only with respect to the store** — no entry was written, moved or reformatted
to produce this document.

🔴 **Sanitization.** The store is client-confidential; this repo is PUBLIC. Everything below is
structure, counts and field names. Every example is synthetic (`payments-api`, `example-scope`).
No entry prose, service name, client path, hostname or individual is quoted or paraphrased.
Scope directories are referred to by size ("the largest scope"), never by name.

---

## 0. How this was measured, and what the instrument cannot see

Three structural surveys over `~/.claude/analyze-service-index/`, plus one **controlled probe of
the validator itself**. Scripts and raw output are reproduced in Appendix A so a later reader can
re-run rather than trust.

- **Positive control on every survey**: each prints the file count it walked before printing any
  finding. A `0` from a scan that walked nothing is the failure mode these numbers exist to avoid,
  so the denominator is always printed beside the result.
- **The validator probe is the important one.** Eight synthetic fixtures, each isolating one
  property, each named `payments-api.md` in its own directory so the filename↔`service:` check
  could not mask the property under test. My first attempt got this wrong: every fixture tripped
  the filename check first and returned an identical error, which would have read as "the
  validator catches everything". The second attempt also read `rc` through a pipe and captured
  `sed`'s status instead of Python's. Both are recorded because the corrected run is the source of
  §2.1, and the uncorrected one would have supported the opposite conclusion.

**Deltas from the brief's figures.** Independently re-derived: **596** bullets (brief: 595),
**207 KB** total (brief: 205 KB), **max entry 16.4 KB** (brief: 14.2 KB). Median 2.6 KB, 8 scopes,
45% of bullets dated — all confirmed. The differences are small and do not move any conclusion;
they are noted so the numbers here are not silently reconciled against a different measurement.

**The "universal spine" claim survives, and is stronger than stated.** The brief reports the spine
holding for 53 of 53 real entries via modal header *sequence*. Measuring section *presence*
independently — a regex for each of the three headings, not the ordered sequence — also returns
**53/53, 100%, for all three**. The 5 non-conforming files are per-scope `README.md` policy docs,
correctly excluded: `subsystem_touch --census` reports **53 entries** and the loader skips
`README.md` in every scope by name. The claim holds.

---

## 1. The shape, stated explicitly

This is the grammar as it exists **today**, reconstructed from the corpus, the documented schema
in the skill, and the three libraries that read and write it. Where the corpus, the doc and the
code disagree, the disagreement is stated rather than resolved.

### 1.1 Identity and the file's address

An entry is `<store>/<scope>/<slug>.md`, optionally `<slug>.<kind>.md` where
`kind ∈ service | process | org | doc`. The loader walks **exactly two levels** — a nested
directory under a scope is never walked and its files are invisible.

The address is resolved in two tiers, filename before alias, and **more than one hit in a tier is
an error that lists candidates, never a silent pick**. Slugs normalize identically on read and
write and on aliases before comparison: lowercase, `_`→`-`, anything outside `[a-z0-9.-]`→`-`,
collapsed and trimmed.

### 1.2 Front matter — and which fields are actually load-bearing

The parser is hand-rolled and line-based, not YAML: a value must fit on **one physical line**, a
line without a colon is silently dropped, and `#` comments are skipped. A wrapped `aliases:` is
the documented store-killer.

| field | corpus | what it actually does on the read path |
|---|---|---|
| `service` | 91% | **Required.** Must agree with the filename slug — disagreement removes the entry from the index, `--ref` and `--search` entirely. It is a *redundancy check*: the filename is authoritative for the slug. |
| `aliases` | 89% | Tier-2 ref resolution, path→entry association, and search name-scoring. Never displayed. A malformed one **deletes the entry** from the read surface. |
| `scope` | 55% | **Inert on any disk load.** The loader overwrites it from the directory name unconditionally and pops `repo`. A `scope:` that disagrees with its directory is discarded with no warning. |
| `repo` (legacy) | 36% | Same — the fallback exists in the mapping code but is unreachable from disk. Labelled REDUNDANT-BUT-KEPT in the source. |
| `sensitivity` | 55% | Fail-safe: absent or unrecognized ⇒ `client-confidential`, never `public`. **Renders only** — it never filters, redacts, reorders, gates, or changes an exit code. |
| `created_by` | 55% | Read by `--census` alone. Nothing routes on it. |
| `namespace` | 39% | **Read by nothing.** Not by either module, and not reachable by `--search` either, because front matter is excluded from the search corpus. A complete no-op. |
| `kind` | **0%** | Consistency check only. Matching the filename is a no-op; contradicting it deletes the entry. It can never change which file a ref reaches. |

**The load-bearing set is `service` + `aliases`.** Everything else is provenance, a label, or
inert. That is not a criticism — it is the fact any proposed new field has to beat.

### 1.3 The three-section spine — and the two the code knows

Written by **53/53** entries:

```
## What it is
## Pointers
## Nuance / work-history
```

The code defines **two**: `SURFACED_HEADINGS = (POINTERS_HEADING, NUANCE_HEADING)`.
`## What it is` appears in the reader only as a comment explaining its deliberate exclusion —
printing one line of durable boilerplate per entry turns a recall block into a dump. It is
reachable only if `--search` happens to hit a block inside it.

So the first section of the universal spine is, on the default read path, **write-only**. 53
entries maintain it; the digest never shows it.

Heading matching is exact-string after `rstrip()`, case-sensitive, column 0. `## pointers`,
`##Pointers` and `## Pointers:` all miss. A section runs to the next ATX heading of any level or
EOF; fenced code cannot end one; **duplicate headings silently merge**, concatenating both blocks
and dropping whatever sat between them.

### 1.4 The bullet grammar inside `Nuance / work-history`

Applied **only** to that section — `## Pointers` bullets are never bullet-parsed.

```
- YYYY-MM-DD: OPEN: one line, ≤2, newest-first
- YYYY-MM-DD: RESOLVED <7-40 hex sha>: what closed it
```

- A bullet **starts at column 0**; an indented `-` is a continuation. (Search uses a *different*
  bullet rule — any indent — so one file has two bullet grammars over it.)
- The date is optional and validated as a real calendar date. **45% of bullets carry one.**
- The openness marker sits immediately after the date, is ALL-CAPS, carries no emphasis or
  parenthetical, and terminates in a colon. The date's own colon is required before it.
- **Prune-on-resolve** is documented policy — the index is a live pointer sheet, not an
  append-only log — and is implemented by nothing. No code deletes, rewrites, archives or
  compacts a bullet, ever. The only bounds are display caps that always print the remainder.

The resolver classifies every bullet into disjoint populations: `open`, `resolved`,
`near-miss` (tried the marker, missed the grammar), `unmarked` (matched one of two measured
phrasings — an explicit **floor with unknown recall**), and `unverifiable` (a `RESOLVED:` naming
no sha).

🔴 **The reader consumes exactly one bit of this.** `open_count` drives a `🔴 N OPEN` badge on the
index line. Dates are parsed, validated — and then ignored: **reader recency is file mtime**.
Near-miss, unmarked and unverifiable are computed on every bullet and read by nothing on the read
path. They surface only in `subsystem_touch --validate`, which `/resume` never runs.

### 1.5 The `Pointers` sub-grammar

Documented as a path/slug + one-clause why, **never a copy**, with three labelled forms:
`manage-* skill:`, `MEMORY.md slug(s):`, `claudedocs handoff(s):`.

**Nothing machine-parses this section.** No path extraction, no link resolution, no key/value
split, no existence check, no recognition of the three labels. The reader stores it verbatim and
prints it indented. The labels are conventions for an LLM reader only.

Measured adoption across **244 pointer bullets in 53 entries**:

| form | bullets | share |
|---|---|---|
| `MEMORY.md slug(s):` | 41 | 16% |
| `claudedocs handoff(s):` | 9 | 3% |
| `manage-* skill:` | 2 | 0.8% |
| **any documented label** | **52** | **21%** (present in 17/53 entries) |

By free shape rather than by label: 38% are `` `thing` — prose ``, 11% `` `thing` `` plus
something else, and **49% are free prose with no leading code span at all**.

### 1.6 The consumption surface, in one place

A proposed field that nothing would read is not a proposal, so this is the list of things that
could read one.

| surface | what it reads | writes? |
|---|---|---|
| `subsystem_recall.py` (read half) | `sensitivity`; `## Pointers` + `## Nuance / work-history` verbatim; `open_count`; **mtime** for ordering | never — `TestRecallNeverWrites` |
| `subsystem_resolver.py` | front matter, slug/alias resolution, section split, bullet populations | never |
| `subsystem_touch.py` (write half) | `created_by` (census), the nuance section, all bullet populations | 🔴 **never** — it is a read-only *probe*; see below |
| `/resume` step 4 | runs `subsystem_recall --repo <repo>`; branches on `status=`, the index row, the featured-entry basis, `🔴 N OPEN` | never; explicitly forbidden from creating entries |
| `/handoff` step 4 | runs `subsystem_touch` probes (`--session`/`--pr`/`--commit`), then **writes the entry by hand** | yes — ungated since 2026-08-15 |
| handoff docs' first section | embeds `subsystem_recall --repo <repo>` at the top of every doc | never |
| `subsystem-store-api/server.py` | serves `render_text()`/`render_search()` verbatim over HTTP | GET-only |
| `analyze-service-index-commit` timer | hourly git autocommit, no remote | git only |
| `/analyze-service` skill | 🔴 invokes **no** command for the read; reads and writes the store by hand | yes |

🔴 **The single most important structural fact for everything below: no code writes an entry.**
`subsystem_touch.py` says so at module scope and `TestNeverWrites` hashes a store tree either side
of every mode to keep it that way. The bytes are written by an LLM agent following prose in
`/handoff` step 4 and `/analyze-service`. The shape is emitted **once**, by a Python string
literal (`new_entry_template`), restated in two skill markdown files, and checked afterwards by a
validator that — as §2.1 shows — does not look at the spine at all.

**There is no hook.** Grep across `scripts/claude-hooks/` finds no recall or touch invocation. The
index is reached purely by prose in two skills. That fragility is measured, not hypothesized: the
handoff skill's own reference notes that prose and `/resume`-prefixed kickoffs each produced
**zero** recall calls, which is why the command was moved to the top of every handoff doc.

---

## 2. Grounded gaps

Each gap states what breaks today and what closing it costs.

### 2.1 🔴 The validator is a front-matter parser, not a shape checker

The strongest finding, and it is a measurement rather than a reading. Eight synthetic fixtures
through `subsystem_touch --validate`:

| fixture | verdict | exit |
|---|---|---|
| fully conforming (positive control) | `OK — 1 of 1 parse` | 0 |
| **no front matter** (negative control) | `🔴 MALFORMED … missing or empty service:` | **3** |
| **all three spine headings renamed** | `OK — 1 of 1 parse` | **0** |
| **no headings at all**, prose body | `OK — 1 of 1 parse` | **0** |
| legacy `repo:`, no `sensitivity:`, no `created_by:` | `OK — 1 of 1 parse` | **0** |
| `RESOLVED:` with no sha | `OK` + advisory | **0** |
| undated bullets only | `OK — 1 of 1 parse` | **0** |
| perfect spine, all three sections **empty** | `OK — 1 of 1 parse` | **0** |

The instrument works — the negative control goes red, the positive control goes green. The finding
is what sits between them: **a file with zero headings and a file with a perfect spine are
indistinguishable to the validator.** Also silently accepted: unknown or typo'd front-matter keys
(`sensitvity:` passes clean), any `sensitivity` value, any size, any bullet count.

The advisory populations *are* reported well — a sha-less `RESOLVED` gets a precise ⚠ naming why
it matters. But the verdict is deliberately unchanged, on stated and correct reasoning: an entry
with unfinished business is well-formed, and failing it would be a permanently-red gate nobody
could turn green by fixing the file.

**What breaks.** The 100% spine is enforced by nothing. It holds because two skills happen to
describe it and one template happens to emit it. And the failure is not merely cosmetic — see
§2.2.

**Cost to close.** Small, and it must not touch the verdict. Add a **shape** population to
`--validate` alongside the existing open-actions advisory: report entries whose surfaced headings
are absent, renamed or duplicated. One function, reusing `extract_sections`, which is already
imported. The template already emits the correct spine, so a new entry starts conforming.

### 2.2 🔴 A renamed `Nuance / work-history` is silent data loss on the index line

This is why §2.1 is not cosmetic, and it is the one place the shape's implicitness has teeth.
**Measured, not read off the source** — a differential control over a synthetic store holding two
entries that differ in exactly one variable, the nuance heading:

```
INDEX (from index) — ALL 2 entries in `example-scope/`, none omitted:
  orders-api      0 nuance   client-confidential              <- heading renamed
  payments-api    2 nuance   client-confidential   🔴 1 OPEN  <- heading correct
```

Both files carry the same two bullets and the same declared `OPEN:` marker. The renamed entry's
index row reports **0 nuance** and **no badge**, and `--validate` calls it `OK` at exit 0.

The mechanism: the reader computes `bullets = sections.get(NUANCE_HEADING, "")`, so a heading that
is renamed — or given a trailing colon, or shifted off column 0 — yields empty. Then:

- `bullet_count` reads **0**, so the entry's size signal on the index row says it holds nothing;
- `open_count` reads **0**, so the `🔴 N OPEN` badge **disappears**;
- and the "missing section" note that would explain this is attached to a **printed body**, not
  to the index row — and the digest prints exactly **one** body out of N.

So an entry with genuine open actions renders on the index as a well-formed entry with nothing in
it, and `/resume` — which consumes exactly that index row — cannot tell the difference. Content
under any other heading is still searchable, so the prose is intact on disk and invisible to every
default read.

🔴 **Note what the caveat says in this state.** The reader's standing caveat explains that the
absence of a badge "means nothing was declared, NOT that nothing is open" — which is true, and in
this case actively misleading: something *was* declared, in the schema's own syntax, and the parser
never reached it. The caveat covers the writer who never marked a bullet; it does not cover a
marked bullet made unreachable by its heading.

This is the `#505` failure mode with a different trigger: `#505` was an entry serving a resolved
remedy as outstanding for 22 days, and the fix was to make openness typed. A renamed heading walks
straight past that fix, because the typed marker is never parsed at all.

**Cost to close.** Small. Surface `missing_sections` on the **index row**, not only on a printed
body — the field is already computed and already carried in JSON. One conditional, in the same
shape as the existing `🔴 N OPEN` badge, which is likewise conditional so the common case stays
byte-identical.

### 2.3 The "three stalled migrations" are **one cohort boundary**, and it is nearly free to close

The brief describes three migrations frozen mid-flight: `scope` 55% vs legacy `repo` 36%,
`sensitivity` 55%, `created_by` 55%. Measuring them as a **joint state** rather than three
independent ones gives a materially different picture. Across 53 entries there are exactly **two**
combinations:

| `scope`-or-`repo` | `sensitivity` | `created_by` | entries |
|---|---|---|---|
| `scope` | present | present | **32** |
| `repo` | absent | absent | **21** |

There is no entry with `scope` but no `sensitivity`, and none with `sensitivity` but no
`created_by`. This is not three half-finished migrations; it is **one clean bisect** — the 32
entries written after the schema repair, and the 21 that predate it. `--census` corroborates it
independently: 31 `handoff` + 1 `analyze-service` = 32 stamped, 21 unstamped.

**What breaks today: much less than "three stalled migrations" implies.**
- `scope`/`repo` — **nothing**. The directory name is authoritative on every disk load; both
  fields are inert. This migration is already complete in effect.
- `sensitivity` — nothing *unsafe*: absent folds to `client-confidential`, the fail-safe. The 21
  entries are correctly handled; they simply rely on the default rather than declaring.
- `created_by` — the 21 are reported as their own census bucket forever, deliberately, because
  attributing them to either writer would be an inference. That is the correct behaviour, not a
  gap.

**So the honest recommendation is: do not backfill.** The 21 unstamped entries are load-bearing
evidence — they are the pre-instrumentation anchor the reopening gate was measured against.
Backfilling `created_by` would destroy that anchor to fix nothing. Backfilling `scope:` would
rewrite 21 curated files in an unbacked-up store to change no behaviour at all.

The one thing worth doing is **stating the boundary** rather than leaving three percentages that
read as decay. A one-line note in the census output — that the unstamped cohort is closed, dated,
and deliberately not backfilled — costs nothing and stops the next reader proposing this again.
That is the whole fix.

### 2.4 The openness schema's ~4% adoption is real, but the sharper number is the near-miss rate

Raw text counts across 596 bullets: 10 `OPEN:`, 11 `RESOLVED <sha>:`, 3 malformed `RESOLVED`.
Running the tool's **own parser** over every scope gives what actually registers:

| population | count |
|---|---|
| declared `OPEN:` (parses) | **8** |
| near-miss — attempted a marker, missed the grammar | **2** |
| unmarked but reads like an open action (a floor, unknown recall) | **3** |

So of 10 textual `OPEN:` occurrences, **2 do not parse** — a 20% near-miss rate on a schema with
~4% adoption. Both live in the largest scope. Five scopes declare nothing at all.

**What breaks.** Per §1.4, a near-miss is byte-identical to no marker on the read surface: the
badge simply does not render. The advisory that would catch it lives in `--validate`, which
`/resume` does not run and `/handoff` runs only against the file it just wrote. So the population
most likely to contain a stale open action — a marker someone *tried* to write — is the one no
routine surface reports.

**Cost to close.** Small, and it is the highest-value small change in this document: give the
reader's index line the near-miss and unverifiable counts the resolver already computes, in the
same conditional style as `🔴 N OPEN`. No new field, no new parse, no store change — the data is
computed on every bullet today and discarded.

### 2.5 Staleness is **not** a gap — do not build for it

Recorded because it is the obvious next proposal and the measurement refuses it. Days since each
entry's newest dated bullet, as of 2026-08-19:

- entries with **no dated bullet at all: 0**
- median **2 days**, mean 9, max 80
- older than 30d: **4 entries (7%)**; older than 60d: **1**; older than 90d: **0**

The store is actively maintained, and `--census` agrees from an independent signal (file mtime
rather than bullet dates): 41 of 53 entries touched within 7 days, newest write minutes old. A
30-day staleness detector would fire on **4 entries out of 53** — too few to justify a schema
field, and each one already visible by reading its newest bullet.
**No staleness field, no decay flag, no review-by date.** The problem does not exist.

One genuine sub-gap survives: reader recency is **mtime**, and mtime is reset by `cp`, `git clone`
and `git checkout --`, any of which fakes a freshness burst. Bullet dates are parsed, validated,
and then not used for ordering. Since 100% of entries carry at least one dated bullet, the
better-grounded signal is already there — see §3.2, where it is deliberately filed as speculative
because the payoff is unmeasured.

### 2.6 `kind:` has zero adoption in both of its forms

Measured: **0 of 53** entries carry `kind:` in front matter, and **0 of 53** use a
`<slug>.<kind>.md` filename. The field is documented, sits inside a hash-pinned region, and is
implemented in the resolver with real validation.

This is worth stating precisely because of what it is **not**: it is not a bug, and it is not an
argument for removing it. `kind` exists to break an addressing collision that was measured at
n=1 during the 2026-08-11 strain test — one slug naming both a code subsystem and a ritual about
it. It is a **latent disambiguator**, correctly built and correctly unused, and its cost is zero
because bare `<slug>.md` is the default and no file is renamed.

It is also an **independent second confirmation of the 2026-08-11 rejection**. That decision
rejected a `type:` taxonomy on the grounds that it would have exactly one value, measured against
a 20-entry corpus. The store has since grown to 53 — **33 additional entries, across 7 scopes that
did not exist at rejection time** — and the enum that actually shipped has **zero** instances in
either form. Anyone proposing a class-based taxonomy over this store now has two measurements to
beat, not one, and the second was taken against a corpus 2.65× larger.

**Cost.** None. Leave it. Do not extend it, do not backfill it, do not remove it.

### 2.7 `namespace` is written by 39% of entries and read by nothing

Not by the reader, not by the resolver, not by `--census`, and not even by `--search` — the search
corpus starts at the first heading, so front matter is structurally unsearchable. Twenty-three
entries maintain a field with no consumer on any surface.

**What breaks.** Nothing breaks; something is wasted. The cost is the writer's belief that
recording it accomplishes something, which is the same class of error as reading a type
declaration as a code path.

**Cost to close.** Two options, and the choice is the operator's, not mine:
1. **Make it read** — include front-matter values in the `--search` corpus, so a namespace query
   reaches its entries. Small, but it changes search semantics for every field at once and would
   need its own positive control.
2. **Say plainly in the schema that it is a note to a human reader**, not an index key. Free.

I lean to (2). The measured demand for namespace *lookup* is zero; the field's value is that a
person reading the entry sees it. Option (1) solves a problem nobody has reported.

### 2.8 `## What it is` is written 53/53 and never surfaced

Per §1.3. The exclusion is deliberate and well-reasoned — including one line of boilerplate per
entry across a whole scope turns recall into a dump, and there is a test pinning that it stays
excluded.

The gap is not the exclusion; it is that **the schema does not say so**. The skill documents the
section as part of the entry with no indication that the default read path never shows it, so
every writer maintains it under the impression it is recall surface. It is, in practice, a
description for a human opening the file directly and a search target.

**Cost to close.** One clause in the schema prose. Free, and it prevents writers investing effort
in a section proportional to its perceived reach.

---

## 3. Speculative

🔴 **Everything from here is unbuilt and unmeasured.** Each item states what it enables, what it
costs, and **how I would verify it was worth it** — including, in two cases, a cheap measurement
that would *refuse* the idea. Items are marked **[cheap]** (a flag or a conditional on an existing
tool) or **[architecture]** (a new surface, a new dependency, or a store change).

### 3.1 [cheap] A conformance census — `--census` reports coverage, nothing reports shape

**Enables.** `--census` answers "does the index cover the repos where work happens", which the
2026-08-11 decision established as *the* binding question. It cannot answer "are the entries
well-shaped" — it only counts, and never opens a body. Nothing does. A shape census would report,
per scope: spine conformance, front-matter cohort, undated-bullet share, and the openness
populations — the numbers in §1 and §2 of this document, on demand instead of by a throwaway
script.

**Costs.** One flag on an existing tool, reusing `extract_sections` and the resolver's bullet
populations, both already imported. No store change. The real cost is the standing risk that a
conformance number becomes a target and someone "fixes" 21 curated files to move it — which §2.3
argues would be a net loss. Mitigate by reporting cohorts with their dates, not a single score.

**How I would verify it was worth it.** It is worth it only if it changes an action. Concretely:
run it monthly for three months and count how many times a reported non-conformance led to an
edit that a reader would have noticed. If that count is zero — as §2.5 suggests it might be for a
well-maintained store — delete the flag. This is cheap enough that the verification costs more
than the build, which is itself an argument for building it and an argument against building
anything larger on the same premise.

**Adjacency check.** This is one flag on an existing read-only tool, not a step toward the
13-verb `subsys` CRUD rejected on 2026-08-11. That rejection turned on opt-in adoption — "opt-in
survives when it rides an existing ritual and dies when it *is* the ritual". A `--census` sibling
rides `/handoff`, which already invokes this exact binary on every run.

### 3.2 [cheap] Derive reader recency from bullet dates, with mtime as a labelled fallback

**Enables.** Index ordering and the featured-entry fallback currently use file mtime, which `cp`,
`git clone` and `git checkout --` all reset — a real hazard in a store that is a git working tree
under an hourly autocommit, and one the source already enumerates as a known blind spot. **100%
of entries carry at least one valid dated bullet** (§2.5), so a better signal is present in every
single file. Ordering by newest bullet date would make "page 1 is the freshest" mean *the content
is freshest* rather than *the file was touched most recently*.

**Costs.** Small in code — `newest_date` already exists in the write half. Larger than it looks in
consequence: index order is a documented output contract that `/resume` reads, and the reader is
deliberately byte-deterministic with mtime excluded from output for exactly that reason. Changing
the sort key changes which single body the digest prints. It also needs an explicit rule for the
44% of *bullets* that are undated (the entries are all dated; individual bullets are not).

**How I would verify it was worth it.** Before building: compute both orderings over the live store
and **diff the featured-entry pick**. If the two selectors nominate the same entry in nearly every
scope, the change buys nothing and should be dropped. If they diverge, the divergent cases are the
evidence — inspect whether the date-ordered pick is the better answer. This is a half-hour script
and it can refuse the whole idea, which is why it comes first.

### 3.3 [cheap] Teach the reader the near-miss populations

Already argued in §2.4 as a grounded gap; restated here because the *generalization* is
speculative. The specific change — near-miss and unverifiable counts on the index line — is cheap
and grounded. The speculative extension is **making the reader the place where all six populations
surface**, retiring the split where the read path knows one bit and the write path knows six.

**Enables.** One vocabulary across both halves, and the openness data reaching `/resume`, the
surface that actually runs every session.

**Costs.** Every added badge competes for the index line's ~60 bytes, and the digest's cost model
is measured and deliberate. More badges is not obviously better; the `🔴 N OPEN` badge is
conditional precisely so the common case stays byte-identical.

**How I would verify it was worth it.** Count, over a month of real sessions, how many near-miss
bullets exist at any time. Today it is **2**. If it stays in the low single digits, the badge is
not worth its bytes and the right answer is the §2.4 change alone plus leaving the rest in
`--validate`. The measurement is the same survey already written.

### 3.4 [architecture] Machine-parse `## Pointers` and check for link rot

**Enables.** 244 pointer bullets name paths, slugs and docs. Nothing verifies any of them still
exists. A pointer to a moved handoff doc or a deleted memory slug degrades silently, and the entry
still reads as authoritative — which is precisely the "recall, not live observation" hazard the
store's own caveat warns about, except here the *pointer itself* is the stale thing.

**Costs.** Genuinely architecture, and the corpus argues against it. 49% of pointer bullets are
free prose with no code span (§1.5), and only 21% use any documented label. A parser would
therefore have **unknown recall over half the corpus** — the same "floor with unknown recall"
property the unmarked-action detector is careful to label. Making it work would mean either a
restructure of 244 curated bullets in an unbacked-up store, or accepting a checker that silently
sees half the pointers. It also adds a filesystem dependency to a reader that today touches no
network and reads only the store.

🔴 **Adjacency to a rejected design — declared.** This is adjacent to the `depends_on` dependency
graph rejected on 2026-08-11, and I am not re-proposing that. The distinction I would argue: the
rejection found `depends_on` drew **zero honest edges** because the candidate relations
(runtime-requires, hosted-in, contains, consumes-output-of) were incommensurable and one `contains`
edge returned an entire subsystem list for any blast-radius query. Link-rot checking asserts **no
semantic relation at all** — it asks only "does this path still exist", which is a decidable
question with no taxonomy behind it. That said, the rejection's deeper lesson — that the payoff
was hypothesized rather than measured — applies to this idea unchanged, which is why it is gated
below rather than proposed.

**How I would verify it was worth it.** Run a throwaway script that extracts only the
unambiguously-shaped pointers (the 38% `` `thing` — prose `` form) and stats each path.
**If the dead-pointer count is at or near zero, drop the idea entirely.** Only a materially
non-zero rot rate justifies any of the cost above, and that measurement is an afternoon.

### 3.5 [architecture] A home for cross-cutting lessons that are not about any subsystem

**Enables.** This is not my observation — it is on the store's own roadmap, recorded in the
subsystem-store handoff: roughly **ten cross-cutting engineering lessons** from a single session
were filed into *subsystem-scoped* entries "for want of a better home", and several have nothing
to do with the subsystem they landed in. The shape has no slot for a lesson whose scope is
"engineering", so such lessons land wherever the session happened to be. That makes them
unfindable by the one query that would want them, and it inflates entries with content that is not
about their subsystem.

**Costs.** This is the "does a generic journal belong" question, and it is genuinely open. The
obvious candidates each have a real objection: a `cross-cutting` scope competes with `RULES.md`,
which already exists for exactly this and has an enforced byte ceiling *because* it loads every
session; a new store is a second place to look; and doing nothing keeps the misfiling.

**How I would verify it was worth it.** The store now records enough to answer this by counting
rather than arguing. Sample the bullets in the largest scope and classify each as *about this
subsystem* vs *a general lesson that landed here*. If the second category is a few percent, the
misfiling is noise and the answer is "no new home — put it in `RULES.md` or drop it". If it is a
substantial share, the shape has a real missing slot and the design question is worth opening.
**I would run that count before proposing any structure**, and I have not run it here — it
requires reading entry content at a depth this read-only, sanitized task should not produce
output about.

### 3.6 Declared and NOT proposed

Listed so a later reader can see these were considered against the record rather than overlooked.

- **A `type:`/class taxonomy** — rejected 2026-08-11 on the measurement that it would have one
  value. §2.6 supplies a second, independent confirmation: the `kind` enum that *did* ship has
  zero instances 33 entries later. Not proposed.
- **`depends_on` / a dependency graph** — rejected on zero honest edges. §3.4 is adjacent and says
  so explicitly; the graph itself is not proposed.
- **A multi-verb `subsys` CRUD** — rejected on adoption ("opt-in dies when it *is* the ritual").
  Every tooling change above is a flag on a binary that `/handoff` already invokes. Not proposed.
- **Migrating `/handoff` docs into per-subsystem journals** — rejected as the most expensive and
  least justified part, on slug-stability grounds that still hold (the filename-derived base slug
  backs `(repo, slug)` primary keys in the initiatives store). Not proposed.
- **A staleness/decay/review-by field** — refused by my own measurement, §2.5.
- **Backfilling the 21 pre-instrumentation entries** — refused by my own measurement, §2.3; the
  cohort is load-bearing evidence.
- **Session→subsystem association surfacing** — already decided and phased (P0/P1/P2) in the
  2026-08-11 record. Not re-proposed; noted because it is the existing answer to "who touched
  this", which is why §4 does not propose a per-bullet writer field.

---

## 4. Missing fields

Justified against a measured failure mode, with the consumer named. A field nothing would read is
not proposed.

### 4.1 For the entry schema: **none, and that is the finding**

I could not justify a single new front-matter field against a measured failure. Every failure mode
in §2 lives in the **body grammar** or in the **tooling**, not in the identity block — and §1.2
shows the existing front matter is already mostly inert, with `service` + `aliases` doing all the
work. Adding to a block where 3 of 8 fields are read by nothing would be symmetry, not need.

Two tempting candidates, and why each is refused:

- **A per-bullet writer/provenance marker.** The real gap is real: `created_by` attributes the
  *entry*, never the newest bullet, and the source warns about this in two places. When a bullet
  turns out to be wrong you cannot tell which writer or session added it. **Refused anyway**,
  because the decided design already answers it from the other side: session→subsystem association
  as a ClickHouse edge, deliberately keeping the markdown store holding nothing new. A per-bullet
  field would put that data in the one place chosen not to hold it, and would cost bytes on all
  596 bullets to answer a question a query answers.
- **A schema-version field.** Superficially attractive given §2.3 — until the joint-state
  measurement shows the cohorts are already **perfectly separable from the fields themselves**
  (`repo`+unstamped vs `scope`+stamped, with no mixed state). A version field would encode what is
  already decidable, on an unbacked-up store, to enable a migration §2.3 argues should not run.

### 4.2 For the interaction surface: five, all reading data that already exists

This is where the gaps are. **None of these requires a store change or a new entry field** — every
one surfaces something the code already computes and discards.

| # | addition | failure mode it closes | what would read it |
|---|---|---|---|
| 1 | **`missing_sections` on the index row**, not only on a printed body | §2.2 — a renamed nuance heading silently zeroes both the bullet count and the `OPEN` badge, on the one row `/resume` consumes | `/resume` step 4, which already branches on the index row's fields; the HTTP `recall` route, which serves the same render verbatim |
| 2 | **near-miss + unverifiable counts on the index row** | §2.4 — 2 of 10 textual `OPEN:` markers do not parse, and a near-miss is byte-identical to no marker on the read surface | `/resume` step 4; the same conditional-badge slot as `🔴 N OPEN` |
| 3 | **a shape population in `--validate`** (advisory, verdict unchanged) | §2.1 — renamed spine, absent spine and empty sections all return `OK` and exit 0 | `/handoff` step 4, which already runs `--validate` on the file it just wrote, in the same turn; `/resume`'s repair path |
| 4 | **a conformance line in `--census`** | §2.1/§2.3 — nothing reports shape; the coverage instrument never opens a body | whoever runs the coverage check; it is the natural place, since census already walks every file |
| 5 | **scope-README presence in `--validate` output** | the store-root README and the skill both call the per-scope README authoritative, while `POLICY_NONE` is a live third case the tool already handles internally | `/handoff` step 4, which already reads and reports the `policy:` line |

**Why these five and not more.** Each reads a value the code computes today: `missing_sections` is
already a field on the entry record and already in the JSON; the near-miss and unverifiable
populations are already computed on every bullet; `extract_sections` is already imported by the
validator; census already stats every file; and the policy-resolution function already
distinguishes three cases. The gap in all five is not derivation — it is that the value stops one
layer short of the surface that would act on it.

**The ordering I would build them in**, by value per unit of risk: **2, 1, 3, 5, 4.** Item 2 closes
the `#505` class on the surface that actually runs. Item 1 closes the silent-loss path. Item 3
makes the shape checkable at the moment of writing. Items 5 and 4 are reporting polish.

**What would falsify the whole section.** If, after items 1–3 ship, a month of sessions produces no
index row carrying any of the new signals, then the shape was already conforming for reasons that
did not need enforcing, and the honest move is to delete them rather than keep three green
indicators that have never once been non-green — a permanently-green gate teaches as little as a
permanently-red one.

---

## Appendix A — reproducing the measurements

All five scripts are read-only with respect to the store and print their denominator before any
finding. They are deliberately **not** committed: none should run on a schedule, none should be
mistaken for a gate, and a script in this PUBLIC repo that walks the confidential store is a
surface worth not having. ⚠ **They live in a session scratchpad, which is session-scoped and will
not survive** — the table below is the specification to rebuild them from, and the two commands
underneath re-derive the store-side counts with no script at all. If any of this becomes something
worth re-running, §3.1 is the proposal to make it a flag on the existing tool rather than a file.

| script | what it produces |
|---|---|
| `index_shape.survey.py` | front-matter key counts, header sequences, the modal spine, raw openness marker counts, per-scope sizes |
| `index_gaps.survey.py` | spine presence per heading, the front-matter **joint** state (§2.3), the `Pointers` free-shape distribution, alias density, staleness (§2.5) |
| `pointers_grammar.py` | adoption of the three documented `Pointers` labels; `kind:` adoption in both forms (§2.6) |
| `probe_validate2.sh` | the eight-fixture validator control (§2.1) — fixtures are synthetic, named `payments-api.md` in per-fixture directories, and live outside the store |
| `probe_renamed_heading.sh` | the §2.2 differential control — a two-entry synthetic store passed via `--store`, the entries differing only in the nuance heading, showing the index row lose both the bullet count and the `OPEN` badge |

Two tool-level cautions found while writing them, both of which produced a confidently wrong
intermediate result:

- **Fixture design masked the property under test.** The first validator probe named each fixture
  after its defect (`renamed_spine.md`, `undated.md`), so every one failed the filename↔`service:`
  check and returned the same error — which reads as "the validator catches everything". The
  property only became visible once each fixture was named `payments-api.md` to match its own
  `service:` field.
- **`rc` read through a pipe.** `python3 … | sed -n '1,12p'; echo $?` reports `sed`'s status. Every
  fixture printed `rc=0`, including the one that genuinely exits 3. Capture the status directly
  from the command whose status you mean.

The store-side counts can be re-derived without any of these scripts:

```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py --census
python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py --validate --scope <scope>
```

---

## Pointers

- `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` — the rejected generalization, its
  four measurements, and the superseded reopening gate. Read before proposing anything structural.
- `claudedocs/handoff-subsystem-store.md` — live state; the `#505` paragraph is the origin of the
  openness schema, and "next steps" item 4 is the origin of §3.5.
- `claude/skills/analyze-service/SKILL.md` — the documented schema, in two hash-pinned regions.
  🔴 `sensitivity`, the section spine, the `Pointers` sub-grammar and the prune rules sit
  **outside** both hashed regions and outside the substring pins, so they can move materially and
  stay green. Most of §1 describes prose in that unguarded band.
- `claude/skills/handoff/SKILL.md` — step 4, the only documented writer protocol, and the only
  place `OPEN:` / `RESOLVED <sha>:` is documented at all.
- `scripts/lib/subsystem_recall.py` · `subsystem_resolver.py` · `subsystem_touch.py` — read half,
  shared parser/resolver, read-only write-side probe.
