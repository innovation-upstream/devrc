# Why step 5 gates the handoff doc's write+push

Rationale for step 5 of `claude/skills/handoff/SKILL.md`. Evidence only — every
rule you must follow is in the skill body, and none of this needs reading before
running the merge.

## The incident

A session was handed a `/resume` kickoff and re-entered work from a handoff doc.
Its first four actions were correct: read the canonical handoff; find it absent
from the primary clone and cite that repo's own rule that absence there is not
evidence; fetch and read it from the shared branch; check the clock. Then ~10
minutes of real analysis. **At the end it wrote an updated handoff and pushed it
to a shared branch. The operator never approved that.**

Timeline, as it appeared in the scrollback:

```
13:14  the operator's own /handoff commit
       ↓ resume kickoff pasted
13:30  the session's own handoff update — unapproved
```

The two read as one event, which is why the first reading of the incident ("the
resume started by updating and pushing the handoff") was wrong. The push came
last, and it is the only defective part.

## Why it was a GAP, not a rule already broken

Both skills were correct on their own terms:

* **`resume`** is read-only by contract — "it never prompts and never writes",
  ends with "Then wait for direction", and says that creating index entries is
  "`/handoff`'s confirm-gated job at the end of a session, not this step's."
  It followed all of that.
* **`/handoff`** gated its **index** write: "Write only on explicit confirm,
  diff first… on decline, discard", and the step blocked on a y/N.
  ⚠ **Past tense on purpose — that prompt no longer exists.** It was retired at
  the index write 2026-08-15, at step 5 on 2026-08-23, and at the last remaining
  door (`/analyze-service`'s) on 2026-08-31, all by operator decision on the same
  evidence: the answer was always `y`. **The argument in this document is
  unaffected** — what it turns on is that the write was SPECIFIED somewhere, not
  that a human typed a letter, and both writes still show a diff first and are
  still declinable on content.

What nothing covered: the handoff **doc's own** write+push, and the behaviour of
the session that runs *after* a resume — which inherits no constraint at all. It
performed the end-of-session ritual without the gate that ritual carries.

`resume` was deliberately NOT changed. Widening a read-only re-entry step to
govern a whole session's write behaviour is the wrong seam; the constraint
belongs where the writing is specified.

## Why the update itself was RIGHT

The pushed update answered the doc's open question *and corrected a prior
misreading* — an adapter was serving a 110m window against the raw source's 32m,
so the earlier interpretation of at-max time was simply wrong. Suppressing that
update would cost the next session ten minutes of rediscovery. Optimising for
doc stability over state accuracy is backwards, which is why step 5 makes
updating safe rather than rare, and why there is no "don't update" path in
`scripts/lib/handoff_doc.py`.

## Why the gate is on the PUSH and not the write

Writing the FILE locally is cheap and reversible — `git checkout -- <path>`
undoes it. Pushing to a shared branch as a side effect of unrelated work is the
act that needs consent. So the tool's default
mode writes nothing at all — not the doc, not a commit, not a ref — and landing
it takes a second invocation carrying `--confirm` (and `--push`), which is the
action that happens after the `y`. A decline is therefore not a code path that
has to behave correctly; it is the absence of one.

`scripts/tests/test_handoff_doc.py` hashes the whole repo tree either side of a
default-mode run, because a gate that has only ever been watched to accept is
not a gate.

⚠ SKILL.md's opening sentence for step 5 used to add *"step 4's index write is
gated"* as the contrast. That gate was retired 2026-08-15 and step 4 is now a
pointer to `subsystem-index`, so the contrast had become false in two ways; it
was demoted here rather than deleted, and the skill body is where its bytes were
reclaimed from.

### …and the half of that sentence that was FALSE

"Cheap and reversible" was written of the **file** and read as if it covered the
**commit**. It does not. `--confirm` without `--push` makes a real commit, and an
un-pushed commit is not a cheap local state: no reviewer can see it, on a shared
branch it is precisely what `ship.sh` skips over silently (this repo's
`CLAUDE.md` records that incident twice), and on a feature branch it is a handoff
nobody outside this one checkout can read — what `claude/RULES.md` calls UNSAVED
WORK. The tool's own `status=push-failed` path spends nine alarmed lines on that
exact end state; reaching it by the ordinary success path used to earn a
one-line `status=written commit=<sha>` and nothing else.

Measured 2026-08-20: across the transcript corpus, 69 distinct shas came out of
`status=written commit=`, from 58 transcripts, and only 19 of those transcripts
ever printed `status=pushed`. Of the handoff commits still in this repo's object
store, roughly a third are contained by **no** remote branch — every one of them
on a feature branch, none on `main`.

So the gate did not move: `--confirm` without `--push` is still a SUCCESS, still
ungated, still exit 0. What changed is that it now says what it left behind —
`status=written commit=<sha> branch=<b>`, a one-line `NOT PUSHED`, and the
command to land it. On a shared branch that command is deliberately **not** a
push to that branch: several repos (devrc among them) forbid committing there at
all, so it names the preserve-on-a-topic-branch route instead. A wrong pasteable
command is worse than a descriptive one.

## §C — one doc per effort, and the forcing function (2026-08-28)

Meta-work was re-measured at ~23% → ~29% of output tokens. Notably **`devrc`'s
own share FELL** (19.5% → 17.4%), so the tooling is not the runaway; the growth
is in **documenting of work**. 20 of 70 commits to `homelab-talos` in three days
were handoff docs, and a prior audit found 538 docs created in 15 days, 98 of
them rewritten 3+ times. Operator's call: **no cap on meta-work — tooling IS the
product — but cap the documenting.** Two rules, both enforced in
`scripts/lib/handoff_doc.py` (rules i and j there) rather than stated here.

### Why the topic slug is the key, and why nothing fuzzy-matches

`--topic` already decides the path, so "same effort" is answered by "same slug".
MEASURED over the 123 real `claudedocs/handoff-*.md` in devrc + homelab-talos:

```
55 of 123 (44%) carry a full ISO date in the slug
collapsing by date:  remix-session x8 (homelab-talos)
                     browser-bridge x3 (devrc)
                     activity-telemetry, agent-setup-audit,
                     insights-telemetry-unify, repo-cos-precision-iteration  x2
```

Every one is the same effort wearing a new filename. So a dated topic is refused
with **no flag bypass** — under a one-doc-per-effort rule a date in the slug has
no legitimate use, and a flag would be taken every time.

🔴 **"No bypass" was too strong, and the sentence is now scoped to what the code
does** (devrc#964 item 2). No FLAG bypasses rule (i-a); a **SPELLING** still
does. What `_TOPIC_DATE` catches is the ISO date hyphenated (`2026-08-01`) or
compact (`20260801`, the `date +%Y%m%d` form an agent reaches for). What it
knowingly does **not** catch, each pinned by a named test so the gap is a
recorded decision rather than an accident:

| spelling | why it is left | backstop |
|---|---|---|
| `q3-2026-cleanup`, `remix-2026-07-session` — a bare YEAR | the bare-year arm was **deleted 2026-08-31** on an operator decision: across 147 real handoff docs it had **zero hits in both directions**, while refusing `rfc-1918-addressing`, `rsa-2048-keys`, `cve-2024-3094-xz` and seven more, handing back unreadable slugs (`rfc--addressing`) with no flag to say "that is a number" | rule (i-b) |
| `remix-1756339200` — an epoch | a 10-digit arm buys one spelling and re-opens the false-positive surface just closed | rule (i-b) |
| `26-08-01-remix` — a 2-digit year | same trade: `\d{2}-\d{2}-\d{2}` eats ordinary numbers | rule (i-b) |

The second arm cannot be made crisp and is not pretended to be. Whether
`remix-session` and `remix-hardening-session` are one effort is a judgement, and
🔴 **no similarity heuristic is attempted** — it would be the clever-inference
guard the standing rules forbid, and wrong in both directions on that exact
pair. What IS deterministic: creating the N+1th doc stops being the *silent
default*. The caller is shown the list and must pass `--new-effort`.

### Why `forcing: none` is accepted rather than refused

Rule (j) breaks the self-generating loop: each session's handoff manufactures
the next session's queue, so the work never runs out and none of it was ever
asked for by anything outside the loop. The vocabulary is a **closed
allowlist**, which is what a rewording cannot walk — `RULES.md` is right that a
*blocklist* of self-referential phrases would be defeated by any synonym, but an
allowlist refuses anything outside it by default. There is deliberately no
`followup`, `cleanup`, `polish` or `tech-debt`; their absence is what forces a
self-generated item onto `none`, where it is counted.

Refusing `none` outright would not delete those items — it would teach sessions
to type `incident` falsely, moving the population underground where nothing can
measure it. Measured baseline for that population: of **384 ranked items across
83 docs**, only **89 (23%)** cite a PR, issue or `IN FLIGHT` marker of any kind.

🔴 **Three things this does NOT do.** It cannot check that a cited forcing
function is real or genuinely external — the enumeration is structural, the
evidence beside it is prose. The *"does not get worked"* half is **not
enforced**: this module writes the doc, it does not consume the queue. The skip
belongs in `/resume` step 6 and `claim-work`, and is not implemented.

And 🔴 **rule (j) only gates a section headed `Next steps`** — the spelling the
template mandates. Cosmetic variants of that heading ARE covered (case,
repeated whitespace, and leading ornament, so `▶ Next steps (ranked)` counts);
a **REWORDING** is not. MEASURED across the 147-doc corpus: 20 sections
carrying **96 items** read to a human as the ranked queue and are exempt —
`Ranked next steps`, `Open items, ranked`, `Backlog — the highest-ROI UNBUILT
items (ranked)`, `Resume next session`, `Next session — priority order`, and 15
more. Chasing those by synonym is refused for the same reason the forcing
vocabulary is an allowlist: a guard on WORDS is walkable by rewording, and a
synonym list makes rule (j)'s coverage unpredictable at the moment a session is
trying to obey it. **Head the section `## Next steps` and it is gated; head it
anything else and it is not.** Pinned by
`TestTheNextStepsSelectorHasOneOwner`, whose non-goal rows are green in both
directions so a later widening has to argue with a test rather than slip past
one.

### An item is a BLOCK, and the refusal diagnoses instead of assuming absence

The first version of rule (j) searched for the field on the **numbered line
only**. MEASURED over the committed corpus: **179 of 257** ranked items in
devrc's `claudedocs/` and **99 of 181** in homelab-talos' wrap onto continuation
lines — i.e. the majority shape was structurally unable to pass, and a
correctly-tagged item was refused *and told* `[no forcing: field]`. That is the
worst kind of refusal: the printed remedy was already satisfied, so the obvious
fix was a no-op and the re-run was byte-identical. `/handoff` step 5 is the doc's
**sole writer**, so the session's handoff simply could not land.

Two changes, and both are needed — the message fix alone would leave the
majority shape refused, and the behaviour fix alone would still lie about the
near-misses it does not admit:

* **`_item_blocks` searches the item's whole block.** The boundary is *not* "up
  to the next numbered item": that attributes a section's trailing paragraph to
  its last item, and 7 of the 10 corpus blocks with such a paragraph carry the
  copied `🔴 **This list is a WORK QUEUE …**` boilerplate — whose template block
  now also contains "`forcing: none` is the honest opt-out". Appending that
  boilerplate verbatim under two untagged items and asking the naive boundary
  returns `kind='none'` for item 2, silently declaring it self-generated on text
  its author pasted from the instructions. So the block ends at the next
  numbered item **or** at the first unindented non-blank line after a blank one
  — ordinary markdown list semantics. Fenced lines never end it and never count
  as a tag.
* **Every remedy is conditional on the cause.** `[no forcing: field]` (add one),
  `[unknown kind: …]` (pick from the vocabulary), `[unparsed forcing field on:
  …]` (the field is there and misspelled), `[fenced]` (it is inside a code
  fence). The near-miss detector is the `subsystem_resolver._NEAR_MISS_MARKER`
  idiom: keep the grammar strict and **report** what it turns away rather than
  loosening it into prose. Precision control: over the **438** real ranked items
  in both repos' `claudedocs/` — every one of them legacy and untagged — it
  fires **0** times.

One spelling *was* admitted rather than reported: **`**forcing:** gate`**, i.e.
emphasis characters between the key and the colon. What follows the colon must
be a member of a seven-word closed vocabulary, so a "false positive" requires
prose that literally reads `forcing` + punctuation + one of those kinds — which
is the tag. Refusing it would be a refusal over emphasis, in a skill body that
bolds its field names. `forcing function:` and `forcing = gate` are **not**
admitted: those are guesses at the grammar, and they stay near-misses.

#### The two holes that widening opened, and what the boundary still cannot do

Both were **introduced** by the widening above and found by a delta re-audit of
it. Both are now pinned by `TestTheWIDENINGDidNotOpenTwoHOLES` — **7 of its 13
cases red at `503d7136`**, and its docstring says which, because the other 6 are
invariant guards rather than coverage of these defects.

* **A fence erased the boundary's memory — the ACCEPT direction.** `_item_blocks`
  cleared its "a blank line has intervened" flag on every line inside a fence, so
  the first *visible* line after a fence close could never be a boundary.
  Measured: an item whose own correctly-**indented** fence follows a blank line
  swallowed the trailing `🔴 **This list is a WORK QUEUE …**` boilerplate and
  `ranked_items` returned `kind='none'` — an untagged item accepted, counted as
  self-generated, and rule (j) passing. That is the counterfactual this section
  cites as the reason the naive boundary was rejected, re-entered through the
  fence path. A fence with *no* preceding blank still absorbs the following
  unindented line, because this walk's boundary needs a blank to have intervened
  and none has. 🔴 **That is this walk's rule, not markdown's** — the sentence
  here used to justify it as "genuine markdown lazy continuation" and that is
  wrong: in CommonMark lazy continuation covers a *paragraph's* continuation
  lines, not a line following a fenced code block inside a list item, where the
  block has ended and the unindented line is outside the item. The behaviour is
  kept and only its justification changed — it is the permissive direction (it
  can only hand back a tag the author wrote, never invent one) and no corpus
  item depends on the strict reading.

  **The fix's own cost, deliberate and measured.** Item → blank → the item's
  **own indented** fence → a tag at **column 0** parsed at `503d7136`
  (`kind='gate'`) and does not here: the blank's memory now survives the fence,
  so that col-0 line is the boundary and the tag is dropped —
  `kind=None, near_miss=None, fenced=False`, i.e. `[no forcing: field]` at an
  author who *did* write the field on a continuation line. **Not reversed, and
  it must not be:** the walk cannot tell that col-0 tag from col-0 pasted
  boilerplate, and falsely ACCEPTING an untagged item is worse than refusing a
  tagged one. Corpus impact **0 of 442** ranked items. What pays for it is
  `MISSING_FIELD_REMEDY`, which now says the field must be **indented** — that
  is what turns this arm from unrecoverable into clearable.
* **`\b` cannot see past an underscore.** `_` is a word character, so
  `\bforcing` has no boundary to match in `_forcing: gate_`. Measured at
  `503d7136`: `**forcing: gate**` → `gate`; `_forcing: gate_`,
  `__forcing: gate__` and `_forcing_: gate` → `kind=None, near_miss=None`, i.e.
  `[no forcing: field]` and a remedy already carried out — for one of markdown's
  two emphasis characters, in the class the widening existed to admit.
  `_FORCING_ATTEMPT` shared the anchor, so the safety net had the same hole.
  Both patterns now anchor on `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])`, which keeps
  the one job `\b` was doing — `enforcing:`, `reinforcing:` and `forcings:` are
  still excluded, verified on both patterns — but for **ASCII** letters and
  digits only, at *every* position. 🔴 **What it newly admits is a GRID, not a
  list**, and stating it as a list undercounted it twice: this section first
  named one admission, a delta audit raised it to four, and a second delta audit
  measured **ten**. The structure is why. `\b` differs from `(?<![A-Za-z0-9])` /
  `(?![A-Za-z0-9])` for exactly **two** character classes — `_`, and any
  non-ASCII word character (Python's `\w` is unicode, so `\b` excluded those and
  the lookaround does not) — and the two patterns carry **five** lookaround
  positions between them:

  | # | position | `_` probe | non-ASCII probe |
  |---|----------|-----------|-----------------|
  | P1 | `_FORCING` key, leading | `some_forcing: none` | `éforcing: gate` |
  | P2 | `_FORCING_ATTEMPT` key, leading | `my_forcing = gate` | `éforcing = gate` |
  | P3 | `_FORCING_ATTEMPT` key, trailing | `the forcing_fn returns none` | `the forcingé returns none` |
  | P4 | …its KIND, leading | `forcing = _gate` | `forcing = égate` |
  | P5 | …its KIND, trailing | `forcing the user_id column` | `forcing = gateé` |

  There is no P6: `_FORCING`'s own KIND, `([A-Za-z-]+)`, carries no trailing
  lookaround at all. MEASURED 2026-08-28, **all ten cells alike** — each is
  admitted at HEAD and matches nothing under the old `\b` spelling of the same
  pattern. P1 parses to a kind; P2–P5 become near-misses. All ten occur **0**
  times across both corpora (devrc 126 docs, homelab-talos 139), and all ten are
  bounded by the same closed-vocabulary argument as the markup class, so none is
  being fixed — they are recorded. Pinned cell-by-cell by
  `test_the_widened_anchors_admit_these_and_the_comment_says_so`. The module
  comment is **not** allowed to re-state a count:
  `test_the_comment_still_states_the_ASCII_scope` now REFUSES the retired
  "FOUR ADMISSIONS, NOT ONE" literal, because a number in prose is the thing
  that went stale both times.

**Two limits the skill body states in one clause and this section owns in full.**
A tag written **flush-left on its own line under a blank one**, directly beneath
its item, is *outside* the block: it is the boundary line, so it is dropped
before the near-miss scan ever runs. MEASURED 2026-08-28 — `1. Fix A.` + blank +
`forcing: gate — CI red` at column 0 gives
`kind=None, near_miss=None, fenced=False` and the row
`1. Fix A.   [no forcing: field]`, while the same tag INDENTED, or flush-left
with no blank before it, both parse to `gate`. 🔴 **The blank need not be the
line immediately above:** the memory survives the item's own fence, so
item → blank → indented fence → col-0 tag is the same case (that one *did*
parse at `503d7136`; see the cost note above). The fix in every variant is to
**indent it**, which is why `MISSING_FIELD_REMEDY` now says so.

That case is why **SKILL.md no longer claims the tool "never tells you to add a
field you already wrote"** — the sentence was wider than the code. Note the
weaker claim it was replaced with is the honest one in both directions: this walk
cannot support a "never", because nothing scans the dropped tail, and scanning it
would name the pasted `🔴 **This list is a WORK QUEUE …**` boilerplate under
every untagged last item in the corpus — text the author did not write. The
alternative fix was considered and rejected on that ground, not on cost.

Separately, a fence opened at **column 0** after a blank line is a known,
untested gap — markdown ends the list item there and the walk does not.

`FENCED_FIELD_REMEDY` deliberately does **not** say only "move it out of the
fence". The commonest thing a fence under a ranked item quotes is this tool's own
vocabulary line — pasted instructions, or a transcript of an earlier refusal — so
obeying a bare "move it out" promotes a quoted example into a declaration and
produces a **false `forcing: none`**: an item nothing asked for, now counted as
honestly self-generated. The refusal is right; the remedy had to stop assuming
the fenced field was the author's own.

## Why findings append and the status header does not

The status / next-steps block is current state: two of them in one doc is a
contradiction, so it is overwritten. The diagnosis state is the part this skill
already calls "the single highest-value part of the handoff", and the incident
is the argument for append-only — the update *superseded* an earlier reading,
and the value is seeing that it was corrected, not finding it silently gone. So
a new block with the same heading as an old one still appends: supersession is
exactly the case worth keeping both halves of.

## Why "what changed since the doc was written" is mandatory

A resume that goes nowhere, overwriting a good handoff, is the worst case and
the one nobody notices until they try to retry cleanly. `--advanced` forces the
question to be answered before any diff is computed, and an answer that means
"nothing" produces **no offer at all** — not an empty diff, which is still a
prompt. Because that answer comes from the caller, there is a second guard that
does not depend on it: a merge whose result equals what is already on disk exits
`no-change` rather than making an empty commit.

## §D — rule (k): an elimination names HOW it was eliminated (2026-08-30)

`status=unevidenced`, exit **10**. A `Ruled out:` bullet in the update must
carry `via: <kind>` — `command`, `measurement`, `code`, `change`, `doc`, or the
honest opt-out `assumed`.

### The incident

An opencode session running a weaker model wrote this into a handoff's
open-investigations block:

```
- **Ruled out:** Not a per-DIMM issue (all 4 identical)
```

It had run `inxi -CmG` and `inxi -dm`. **Neither prints a part number.** The
sentence was an elimination its own data could not support, and it was false —
one flag away (`inxi -max`) sat two different part numbers from two different
kits, which killed the theory the doc then ranked **first**, with a ⚠ and "free
performance sitting on the table". That rank-1 instruction was to reboot a
26-day-uptime workstation into its BIOS.

Nothing in the session was incompetent. It navigated the repo cleanly, checked
package availability properly, tested under `nix-shell` before installing, and
correctly diagnosed a dispatch failure from a log tail. What it could not do was
separate *what it measured* from *what was merely consistent with what it
measured* — and an elimination is where that gap does the most damage, because
it is the claim a later session trusts most and re-checks least. `/resume`
already warns that a mid-diagnosis block "reads as current forever"; this is the
same hazard one level down, at the individual bullet.

### Why a closed vocabulary and not a content check

The obvious gate is to require the bullet to *look* evidenced — cite a backtick,
a number, a `file:line`. Measured against the bullet above, **every cheap
heuristic accepts it**: it contains a digit, it is fluent, it is the same length
as bullets that do cite evidence. Nothing in its text separates it from a real
elimination, because what it lacks is not a word but a measurement.
`claude/RULES.md` names this directly — "a guard on WORDS is walkable by
REWORDING". So the author declares the KIND instead, from an allowlist. A
blocklist of weasel phrases would be defeated by any synonym; an allowlist you
must pick from cannot be, because a kind outside the set is refused by default.

There is deliberately no `obvious`, `known`, `checked`, `verified` or `tested`.
Those are what an unmeasured elimination reaches for, and their absence is what
forces such a bullet onto `assumed`.

### Why `assumed` is accepted

Same argument as `forcing: none`, and it is not a softening. Refusing it would
not stop anyone reasoning their way to an elimination — it would teach them to
type `command` falsely, which moves the population underground and destroys the
signal. The failure was never that the session reasoned rather than measured; it
was that the doc did not **say so**, so a later reader could not tell the two
apart. `via: assumed` lands, and it is counted in an advisory above the diff.

The gate cannot tell a true elimination from a false one and does not try. It
makes the provenance mandatory and greppable — nothing more.

### Two things that make it not a permanently-red gate

1. **It reads the UPDATE, never the merged doc.** `open investigations` is an
   APPEND heading, so the merged doc accumulates every elimination any session
   ever wrote — **151 of them across 47 of this repo's 93 docs** (measured 2026-09-01; the corpus grows, so this is a dated figure), none of which
   can now be edited to add a field. Checking the merge would refuse on run one,
   forever, which `claude/RULES.md` calls worse than no gate. Rule (j) reads the
   update for a related but weaker reason; here it is the whole design.
2. **The marker needs no punctuation.** The first version demanded a separator
   straight after `Ruled out` and MISSED nine real bullets, every one the same
   shape: a qualifier before the colon — `**Ruled out as writers:**`,
   `**Ruled out (structurally):**`, `**Ruled out, and still true:**`,
   `**Ruled out** (do NOT re-run these):`. Nine is a house style, not noise, and
   a gate blind to it is escaped by typing `Ruled out (obviously):`. So the
   marker alone opens the bullet and the rest of the line is the claim.

### Controls

A pattern that matches nothing is indistinguishable from a gate that passes, so
both directions are pinned. **Positive:** the committed corpus yields ≥90
elimination bullets (measured 2026-09-01: 151) — if the house style drifts, the floor
fails rather than the gate silently going quiet. **False-positive:** `via:`
followed by a member of the vocabulary occurs **0 times** across the corpus
outside a deliberate tag. That is also why the key is `via` and not the more
self-documenting `evidence`: `evidence: the ACCESS_DENIED is positive evidence
it is NOT` is a real shape here, and against it the kind group captures `the`,
refusing a bullet whose author *did* cite a measurement. `via` needs a colon
immediately after it, and prose writes `via the`, never `via:`.

## §E — rule (l): a mid-diagnosis block declares WHEN it was written (2026-09-12)

### The incident

An `## Open investigations` block is written in the PRESENT TENSE by a session
mid-diagnosis, and rule (c) APPENDS it forever: **nothing ever retracts one.**
The doc's status header is visibly dated. A diagnosis block is not, so it reads
as CURRENT for the life of the document.

On 2026-09-12 a session read such a block, adopted its framing, and the framing
was wrong — a claim that fused two documents' measurements, taken over two
different windows with two different instruments, into an attribution neither
source document makes. Refuting it cost a full re-measurement.
`claudedocs/handoff-handoff-resume-skill-trace.md` is the worked example and now
carries its own refutation.

🔴 **Prose had already been tried and had already failed.** The `resume` skill
body warned about exactly this class and cited two earlier instances
(2026-08-19, 2026-08-20). A warning that must be remembered is a warning that
gets skipped, which is why the field is WRITTEN BY THE TOOL rather than asked
for in a checklist — the same reason rule (i) resolves the topic slug here
instead of telling the author to think about it.

### The field

`as-of: YYYY-MM-DD`, the block's first bullet, immediately under its `### `
heading. The grammar is rules (j) and (k)'s — key, optional `_MARKUP` emphasis,
colon, value — because an author who has learned `forcing:` and `via:` should
not have to learn a third spelling. The VALUE is an ISO date rather than a
closed vocabulary: the question is *when*, not *which kind*.

An unparseable value reads as **ABSENT**, not as a stamp. `as-of: 2026-09-12-rev2`
would otherwise parse as a valid date and mark the block stamped — a date nobody
can place, silently preferred over a clock that works.

### What is stamped, and what is not

* Only `### ` blocks inside the **update's** `## Open investigations` section —
  the text THIS session is writing. The base document is never touched. Stamping
  the merge would date blocks past sessions wrote as TODAY: the exact false
  freshness the rule exists to prevent, manufactured by the rule itself.
* **An explicit stamp always wins and is never rewritten.** A session recording
  evidence gathered last week must be able to say so; "the tool moved my date"
  is how an author learns to distrust a field.
* Fence-aware. A delta routinely pastes the skill's own template, and a sample
  block is not a claim.
* It is an **advisory, never a refusal**. The tool did the work, so there is
  nothing for the author to fix and a refusal would be unclearable. It still
  prints above the diff: a line the TOOL added to the author's text must be on
  screen before the confirm, not discovered in the committed doc afterwards.

### Why it is not a permanently-red gate, and not inert either

The reader is `scripts/resume-state.sh`'s `INVESTIGATIONS` block. Its one real
design question is what an UNSTAMPED block reports, because almost every block
in the corpus is unstamped. `drift-check.sh` rc 22 (a host with no overrides
prints NOT ADOPTED and sets no rc) and rc 18 (UNMEASURED is not forever) point
in opposite directions — and **neither applies, because an unstamped block is
not undateable.**

MEASURED at `7e000e6b` over this repo's whole corpus: 81 tracked handoff docs
carry the section, holding **478** `### ` blocks, and git's pickaxe — the commit
that first introduced the block's heading line — dated **478 of 478**. So the
stamp is the most PRECISE clock, never the only one, and an unstamped block is
aged exactly like a stamped one. Only the clock NAME differs, and it is printed.

🔴 **The block's own introducing commit, NOT the doc's last commit.** The
doc-level clock is the obvious reuse and it errs the UNSAFE way: a doc
recommitted this morning makes a block written in July read 0 days old — false
freshness, i.e. the defect. The pickaxe answer is per-BLOCK and content-derived,
so it also survives a `git worktree add` (which stamps every file's mtime at
checkout). Where it cannot answer, the doc's last commit is used as an explicit
FLOOR and gapped as one; `file mtime` is gapped too; a block no clock can place
is `UNDATED`, a `!` gap rather than a finding.

The 14-day window is a measurement, not a taste call. Aged at the moment their
own doc was last written — roughly the moment a session resumes it — those 478
blocks are p50 **1.4d**, p90 **11.3d**. 14 days flags 15 of 478 (**3%**); 7 days
would flag 86 (**18%**). A gate firing on a fifth of every doc is one everybody
clicks through, which `claude/RULES.md` calls worse than no gate; one that fires
on nothing is worth nothing.

### Not done here

The 478 existing blocks are **not** retro-stamped. They do not need to be — the
pickaxe dates all of them — and a bulk rewrite of 81 documents would put a
tool-chosen date on prose no session re-read. If a backfill is ever wanted, the
closing condition is a `git grep -c 'as-of:' claudedocs/` reaching the block
count that `investigation_rows` reports, verified by a session that also
re-reads what it stamped.

## §F — rule (m): the arc declares what ENDS it (2026-09-13)

The field is `closing-condition: <kind> — <the thing itself>` in the doc's
`## Goal` section, `<kind>` one of `check` / `judgement`. Written by the step-2
template, refused by `handoff_doc.py` on a new doc that lacks one, and printed
by `resume-state.sh`'s `DOD` block on every later round.

### The measurement

`<homelab-talos>/claudedocs/audit-arc-rabbit-holes-2026-09-13.md` (committed
at `841cf63b3`) read 75 days of session telemetry out of ClickHouse
`activity.events`. Handoff ARCS are directly observable there because both ends
are standardized: a kickoff is `/resume — continue the <topic> work. Canonical
handoff (read first): …/handoff-<topic>.md`, and an operator close-check is
`anything left outstanding from this arc?…`. That window holds **745 doc-linked
kickoff sessions across 299 arcs**. The five longest were deep-read
transcript-by-transcript.

**The headline, and it is not the one the question expected.** Later rounds do
not stop shipping — commits/session stays flat at ~7 and output tokens/commit
flat at ~80–86k all the way to round 7+. What changes is *what* is shipped: from
round 7 on it is increasingly audit-fixes, guards, validators and
re-verification of the arc's own prior work. In **all five** deep-read arcs the
round-1 objective was satisfied within **1–7 rounds**. The arcs ran **13–23**.

Refuted by the same data, and recorded so nobody re-derives them: effort
inflation (tokens/commit is flat), commit collapse (commits/session is flat),
and idle late rounds (audit-heavy sessions commit MORE — 8.3 vs 5.3 per session
at r7+).

**The close-check does not close.** The window holds **224** close-check prompts
across **188** sessions; of the **185** that landed on an arc session, **21
(11%)** ended the arc; the median close-check → next-kickoff gap is
**1.0 h**. The check was being answered with an inventory of what remained,
which by construction re-opens the arc, rather than with a verdict against
anything.

### Why a FIELD and not an instruction

Every one of those five documents was well written. None of them was missing a
Goal; what they were missing was a statement of what would make the Goal DONE,
so "is this finished?" had no object and every round answered it from the
ranked list — which grows (§G). Prose telling a session to write a finish line
is the shape `claude/RULES.md` calls walkable: the sentence gets written, in
different words each time, and nothing can read it back. A NAMED FIELD with a
CLOSED KIND can be read back — by the refusal, and by `resume-state.sh` on every
later round, which is the half that actually confronts a session mid-arc.

The two kinds are not invented here. `claude/RULES.md`'s object-leak paragraph
already draws exactly this line for a filed work item — "a mechanical check (a
merged PR, a cleared alert, a command exiting 0) **or** a named human judgement
over NAMED EVIDENCE ('X reads the transcript' — never 'someone will decide')" —
and refuses a third option. `check` and `judgement` are those two, and the
absence of a third is the point of the vocabulary.

### The grandfathering, and why it is not weakness

Measured with the parser itself on the day the rule landed: **0 of 119** devrc
handoff docs and **0 of 64** homelab-talos ones carry a field it accepts.
Refusing on every one of them would be red-by-construction from run one, which
`claude/RULES.md` names as worse than no gate — and this module has already
been bitten by that exact shape (see `ranked_items` on why rule (j) reads the
update rather than the merge).

So the refusal is scoped to the two cases where it cannot be vacuous:

- **a NEW doc.** Round 1 is the only round at which a finish line can honestly
  be set — by round 7 the arc has already drifted past whatever it would have
  said — and a new document has no history to grandfather.
- **an update that DELETES a field the document had.** That is the one way a
  compliant document stops complying, and nothing else would catch it: `Goal`
  is a REPLACE-bucket heading, so a delta that rewrites it silently drops
  whatever it does not carry.

Everything else gets `legacy_dod_report`, an advisory above the diff on every
update until someone spends a line.

### What "new doc" means, and the false positive that defined it

`not base_text` is NOT the predicate, and using it took **19 of this module's
own tests red in one run**. A STALE BASE presents identically — an empty local
doc — which is the whole shape rule (h) exists for. Under that reading rule (m)
demanded a finish line from an arc whose document already carries one on the
mainline. The predicate is `not base_text.strip() and not
currency.replaces_mainline_doc(base_text)`, reusing the reading rule (i)
already took so the two decisions cannot disagree.

For the same reason both arc rules run BELOW rule (h)'s refusal rather than
beside (j)/(k): a wrong base makes "this arc has no finish line" a statement
about a document nobody is editing.

🔴 **AND THAT ORDERING WAS ONLY HALF A FIX — round 1 of this PR's own audit
measured the other half.** Rule (h)'s stale-base REFUSAL is gated on
`--confirm`. The PROPOSAL run — the default first half of every `/handoff` — is
not, so it fell straight through the ordering to rule (n), which reported *"0
item(s) in the document answer to nothing external"* about a mainline document
carrying **3**, and printed three remedies none of which could be carried out
(*"close one — the ratchet falls freely"* is impossible at a floor of 0). A
guard's POSITION is not its precondition: what the rules actually need is a base
the tool has judged USABLE, and they now test that directly rather than
inheriting it from where they sit. 🔴 The general shape, which is this repo's
own: **a description that claims coverage ("both rules run below rule (h)") must
be checked against what the code does on EVERY path, not on the one the author
had in mind.**

🔴 **A SECOND SOURCE OF A FALSE ZERO, same audit: the base may carry a ranked
queue this module cannot COUNT.** `ranked_items` recognises only the heading
`is_next_steps_heading` names — deliberate, and harmless for rule (j), which
reads only the update. Rule (n) reads BOTH sides, so the same gap turns a
reworded base heading into a false GROWTH: migrating such a queue onto the
canonical heading **while shrinking it** was refused, permanently, clearable
only by `--rank-growth-approved`. The rule now SKIPS when the base carries no
canonical `## Next steps`, because a count that was never taken must not be
printed as 0.

⚠ **THE POPULATION FIGURE IS PREDICATE-DEPENDENT — quote the one that
reproduces.** A first draft said "22 docs"; round 2 could not reproduce it and
got **38** on a wide predicate (any fence-aware numbered list in a doc with no
canonical `## Next steps`) and **28** on a narrow one. What DOES reproduce
exactly is the number that matters: **3 live docs** —
`handoff-analyze-service-index-backup.md`, `handoff-syshealth-skill.md`,
`handoff-minio-credential-hardening.md`. The rest hit `status=dated-topic`
first and are unreachable. Corpus: 183 `handoff-*.md` across devrc and
homelab-talos.

🔴 **RESIDUALS THIS SKIP DOES NOT CLOSE — left open deliberately rather than
met with more machinery.** ⚠ This header carried a COUNT ("TWO") and a round
number until a later round added a third bullet and left both standing — the
very defect the module's three-skips narration had just been fixed for, recreated
one file over. It carries neither now: a list that grows is not a place for a
literal that does not.
- A base carrying **BOTH** a canonical `## Next steps` and a reworded queue is
  still counted from the canonical one alone, so a PARTIAL migration — the
  natural intermediate state of the very fix this section prescribes — can still
  be refused for shrinking. The skip keys on the heading's PRESENCE, not on
  whether the count is complete.
- A single LINE carrying two `closing-condition:` spellings, the first with a
  digit-boundary kind, still diverges: python's `re.search` retries at the
  second occurrence and declares it, awk's `match()` sees one per line and moves
  on. Contrived, pre-existing, and present under both `exit` and `next` — noted
  so the residual list is not read as closed.
- An UPDATE whose queue sits under an unrecognised heading is invisible to
  `ranked_items`, so rule (n) sees zero items, does not refuse, and does not
  disclose either (the disclosure is gated on the update carrying
  self-generated ranks). That is rule (j)'s deliberate synonym gap inherited,
  not something this rule introduced — but it means the relocation hazard below
  is **not** one heading wide.

🔴 **AND "the working copy is empty" IS NOT "there is no document".**
Emptying a TRACKED doc in place left it with a full history and a blank working
copy, which read as a new arc: the ratchet switched off, a queue went 3 → 5 at
exit 0, and rule (m) asserted *"This is a NEW handoff doc"* about it. The
mainline reading alone could not see it — `currency.mainline` is populated only
when the mainline is AHEAD on that doc — so `doc_tracked_at_head` asks HEAD as
well, and `None` (the question could not be answered) is kept distinct from
`False`, so an unreadable repo grandfathers a document rather than refusing one.

### `in_goal` is part of `is_declared`, and leaving it out was a real defect

The rule's first draft accepted a well-formed field wherever it appeared. A
field under `## State now` satisfied the gate while `/resume` — which reads the
Goal section — could not see it: `claude/RULES.md`'s spelled-guard shape
exactly, the guard passing while the hazard exists in a different place. The
field is still FOUND outside `## Goal`, and reported with its heading, because
"move it" and "write one" are different fixes and only one of them is solved by
writing the field again.

### The detail text is not only an emptiness test

`resume-state.sh` prints it every round, so a character eaten by the parser is a
character wrong on screen forever. Two mangles were measured and fixed in the
lead-strip: a greedy separator class ate the opening backtick of
``check — `gate.sh` exits 0`` (leaving ``gate.sh` exits 0``), and an unanchored
separator ate one dash of `check — --dry-run exits 0`. The separator must now be
followed by whitespace or end-of-line, and only whitespace is stripped after it.

## §G — rule (n): the rank queue does not GROW its unforced half (2026-09-13)

### The mechanism

The same study's **strongest** finding, ranked first of five root causes: a
**self-extending rank queue**. Each round's audits and close-checks minted **2–6
new ranked items**, faster than rounds closed them, so the queue could not drain
however much the arc shipped. Measured rank counts, first round → last:

    comic-flex        0 → 76
    qa-coverage       9 → 84
    tmux              1 → 55
    tekton           11 → 54

ANSWERED/closed markers were effectively absent (0.00–0.01 per kickoff). One of
those documents recorded its own state as *"54 items… Of the 29 live items, only
nine carry a forcing function"*, and another self-reported *"rank 53 was false
within ninety minutes of my writing it — the sixth instance of this doc's
ranked-list drift"*.

The second-ranked cause feeds it: **audit-ladder compounding**. audit-pr use per
session rose 17.7% → 30.5% → 42.9% → **53.6%** by round bucket, and audit-skill
loads per commit 0.71 → 2.33. Every sampled delta round found real defects in
the previous round's own fixes. The ladder is not the problem — it is working as
`claude/RULES.md`'s audit-fix-resets-gate rule describes — but each finding was
becoming a RANK, and ranks are what the next session draws work from.

### 🔴 THE RULE IS NARROWER THAN THE REPORT ASKED FOR — say so, do not paper over it

The report's fix 2 reads *"After round 1, ranks may only be **added** by operator
opt-in"* — **all** ranks — and its cited evidence explicitly names *"`forcing:
user` items unclosable by the agent"*. This rule exempts all six EXTERNAL kinds,
`user` included, from the count entirely. The word "external" appears nowhere in
the report; the self-vs-external split is an implementer's choice, and it is a
NARROWING. Round 0 of this PR's own audit raised it; the operator's call
(2026-09-13) was to keep the exemption.

Why keeping it is defensible, and what it costs:

- Blocking work the OPERATOR asked for is the wrong failure mode, and it would
  make the override flag routine rather than exceptional — which is how a gate
  becomes one people route around.
- The tag is unverifiable either way. This module gates a field's EXISTENCE, not
  its truth (rules (j) and (k) take the same posture deliberately), so an author
  who wants a 55th rank can type `forcing: gate` and the ratchet is silent. 🔴
  **The ratchet's real binding force is on an HONEST author** — state that,
  rather than claiming a strength it does not have.
- 🔴 **AND IT DOES NOT ADDRESS THE `forcing: user` FINDING AT ALL.** That
  complaint is about items that never DRAIN — "structurally unclosable by the
  agent" — and an ADDITIONS ratchet cannot touch a drain problem. Nothing here
  closes it; it is open, and recorded as open so the next reader does not mistake
  this rule for a fix to it.

### Why the ratchet is on the `forcing: none` half only

An item with an EXTERNAL forcing kind answers to something outside the loop: an
incident, the operator, a red gate. Blocking that on a queue-length rule would
be wrong, and would make the gate one people route around. What compounds is the
other half. Rule (j) already makes a self-generated item DECLARE itself
(`forcing: none` — "accepted and counted, and not eligible to be worked"); rule
(n) is what makes the count it was being counted for actually bind.

It is an ANTI-REGROWTH RATCHET, the same idiom as this repo's byte gates: the
number may fall freely and may not rise. Closing self-generated items is what
buys room for new ones — which is exactly the behaviour the finding asks for.

### What it deliberately does NOT do

It does not match items across rounds. Rank TEXT is rewritten between rounds and
rank NUMBERS are re-pointed by re-ranking (which the skill already warns
silently re-points every live `claim-work` claim), so any identity test would be
a guess — and a guess here names the WRONG item as the addition, which is worse
than reporting only that the count moved. The refusal says so in its own words
and prints the whole `forcing: none` population instead.

It is also silent on a NEW doc. Round 1 legitimately opens with self-generated
work; the finding is about what happens after it. `declared_forcing_none_report` still
counts them on every run, new doc included.

### The audit-ladder cap that was proposed and NOT taken

The report's third proposed fix was *"max 3 delta rounds per PR, then
ship-with-known-findings + explicit decision record"*. That was put to the
operator against the standing rule it contradicts — `claude/RULES.md`'s
audit-fix-resets-gate: *"A CLEAN round ENDS the ladder… **Not a cap** — the
count is set by FINDINGS, never by a number."* Both cite the same evidence, that
delta rounds keep finding real defects.

**Operator decision 2026-09-13: disclosure, not a cap.** RULES.md is unchanged.
What lands instead is the non-conflicting half: audit findings may no longer
mint ranks, which is this rule. Nothing forbids round 4 when round 3 found a
real defect.

🔴 **AND THE DISCLOSURE HALF IS NOT NEW WORK — IT ALREADY EXISTS, WHICH IS WHY
THIS PR SHIPS NONE.** A first draft added an `Audit ladder: <N> delta round(s)`
line to the `## State now` template. Round 0 of this PR's own audit cut it, and
was right: the ladder's round count already lives machine-readably in the PR's
fenced `audit-claims` block, which `scripts/audit-dispatch.py` PARSES and whose
staleness it announces on stderr. Nothing would have parsed the template line —
no reader, no test, no assertion — so it was a second, hand-copied copy of a
number that already has an owner, and `claude/RULES.md` is explicit that a field
nothing BRANCHES on is not a guard. Three devrc handoffs already record ladder
state in `## State now` in their own prose without being told to.

⚠ What the `audit-claims` block does NOT give you, stated so nobody reads the
paragraph above as complete: it is per-PR, and an ARC spans many PRs. There is
no arc-scoped view of total ladder cost today. That is a gap, not a thing this
PR closed.

### The close-check verdict

The report's fourth fix — *"close-check must end in a verdict, not an
inventory"* — is not a separate mechanism. It is what §F's field gives the
close-check something to be a verdict ABOUT: `ADDRESSED ⇒ the arc is CLOSED`,
`NOT ⇒ name the one item`, and anything else outstanding starts a NEW arc. It is
stated in the step-2 template beside the field, and `/resume` step 5 is required
to render it. Without the field it was unenforceable prose, which is why the two
ship together.

### 🔴 NOTHING COUNTS, GATES OR READS `## Defects (batched)` — the growth can RELOCATE

Stated plainly because it is the sharpest thing round 1 said about this design,
and it is a limitation rather than a defect. The measured pathology is a
self-extending list. Rule (n) stops that list growing **under one heading** and
names `## Defects (batched)` as the relief valve — and that section is invisible
to rule (j), to rule (n), to `/resume`'s digest and to the DRIFT block. So an
arc can satisfy the ratchet by moving its growth one heading down, and **nothing
would measure that it had.**

🔴 **AND THE HOLE IS NOT ONE HEADING WIDE — round 2 corrected this paragraph.**
`## Defects (batched)` is merely the destination this skill NAMES. The actual
set is *every* heading `is_next_steps_heading` does not recognise, which §F
measures at 28–38 existing documents depending on the predicate. ⚠ **Carry §F's
caveat with that number, which an earlier draft dropped on the retelling:** all
but **3** of those are unreachable behind `status=dated-topic`. The 28–38 sizes
the population of EXISTING BASES; what is unbounded is the FUTURE case — any
update that writes its queue under a heading nobody has used yet — and that is
the half this section is really about. Measured end to
end: five `forcing: none` ranks under `## Ranked next steps` in an update exit
**0** with no refusal and no disclosure, while the same five under
`## Next steps` exit **12**. A reader who took this section as "one heading" —
which an earlier draft invited — would have the hazard's size wrong.

That is accepted for now: a bounded, drainable defect list is the behaviour the
report asked for, and gating it too would be building the second mechanism
before the first has been shown to be used at all. The honest follow-up is an
`/adoption-scan` in a month — does any doc gain the section, and does its rank
queue actually stop growing? — not another guard now.

### `## Defects (batched)` is deliberately NOT a canonical heading

`CANONICAL_HEADING_PREFIXES` is what rule (h) uses to decide that a canonical
section arriving NEW in an ESTABLISHED base is a tell for a wrong base. Adding
`defects` to it would make the very first update that introduces the new section
— which is every existing document, once — look like a stale-base tell. The
heading is therefore an ordinary REPLACE section: a delta that omits it leaves
it alone, and a delta that carries it replaces it, which is the behaviour a
drainable list wants.

### The DOD block does NOT raise a `!` gap — a measured correction

`resume-state.sh`'s first version of the block raised a gap when a handoff
declared no field. It fired on **every** run: 0 of 183 handoff docs across the
two corpora carry one. A gap on every document turns the `!! GAPS` banner —
whose only job is to tell a reader that what they just read is INCOMPLETE
because a source did not answer — into furniture, which is
`claude/RULES.md`'s permanently-red-gate objection wearing a different hat. It
took **49 tests red in one run**, every one of them asserting the ordinary
no-gap path.

It was also the wrong CHANNEL by the script's own established rule. The
`CLAWGATE` block already decides that a doc carrying no `clawgate-task:` field
is not a gap — nothing was asked, so nothing went unanswered — and a document
that declares no finish line is that same case. What replaced it is a 🔴 line in
the DOD block's own section saying the question is UNANSWERABLE, which is
explicitly not the same as unfinished.


## Demoted from the core — the worked example for step 1's diagnosis capture

VERBATIM from `/handoff` step 1. What a useless capture and a useful one look like
side by side, which is the shape the imperative in the body states in the abstract:

   "We looked into the CSP issue" is worthless; "`frame-ancestors` on app.example.test = `https://example.test https://*.example.test` — does NOT include `gen-matrix.embed.example.test`, confirmed via response header on GET /apps/run/dogfood-manual" is the whole point.

## Demoted from the core — the merge's own warnings and refusal statuses

Every line below is VERBATIM from `/handoff` step 5.

   **The doc's YAML front matter survives this merge** — `split_front_matter` carries the base's block through, so a delta that starts with prose rather than a `## ` heading can no longer silently drop the `clawgate-task:` field. Put a front-matter block in your delta ONLY when you mean to change the recorded task; an explicit one wins. 🔴 **The NEW-doc case inverts that:** there is no base block to carry, so the delta's own front matter is the doc's only chance at one — if step 1 resolved a task, it must be at line 1 of the scratch file.

   🔴 **Status header REPLACED, findings APPENDED — which is why the tool merges rather than you rewriting the file.** `State now`/`Next steps`/`How to verify` are current state and are overwritten; `Open investigations`/`Findings`/`Gotchas` append and the earlier text survives **verbatim** even when your block supersedes it — the value is seeing a prior reading was *corrected*, not finding it gone. 🔴 **Appending is HALF the job — retire the superseded heading in the SAME delta, and delete any now-wrong INSTRUCTION in it.** 📖 supersede. A section your delta omits is untouched. The append allowlist is **three prefixes wide**, everything else replaces, so the run prints a **`buckets:`** line naming where each section you touched landed — read it; the next paragraph is a consequence of it. (A NEW doc replaces nothing and gets no such line; that absence is not a fault.)

   🔴 **`THE BASE DOCUMENT IS NOT THE NEWEST COMMITTED COPY` / `THIS MERGE LOOKS LIKE IT RESOLVED THE WRONG BASE`.** The base comes from `--repo`'s working tree, so a stale clone merges into an out-of-date document and reports success. It names the mainline's commit count for **this doc** (mainline **derived**, never assumed `main`), both copies' section/line counts, and which tell fired. 🔴 **A FLOOR: silence is not evidence the base is current**, and it never fetches. 📖 rule (h) in `handoff_doc.py` carries the measured incident. Two outcomes, and they differ:

   - **a usable doc here but behind ⇒ WARNING, exit 0.** The merge can still classify its sections, so a knowingly-behind clone is legitimate.

   - **no USABLE doc here (missing, empty or whitespace) while the mainline has one ⇒ on `--confirm`, `status=stale-base` (exit 9), NOTHING WRITTEN.** The proposal run warns and prints the diff; it never prints that line, so its absence is not a clean bill. Every section would arrive NEW and **replace the committed document** with your delta — usually a clone never re-synced after a WORKTREE authored it. Read the real copy via the `git show <ref>:<path>` it prints, then re-run against a current clone; `--allow-replacing-mainline-doc` overrides. 🔴 **`--push`'s `behind` check does NOT cover this** — it compares a different ref, so a current feature branch sails past it.

   🔴 **`This replace DROPS N line(s) that look DURABLE` — a WARNING, never a refusal.** Durable content under a REPLACE heading (usually `State now`) is deleted on the next update, and in a long diff a stale-status `-` line looks exactly like a measured-finding one. It classifies the deletions **above** the diff with base line numbers. Move that line under an APPEND heading, or carry it forward. 🔴 **A FLOOR: a silent run is NOT evidence that nothing durable was dropped** — read the diff anyway.

   🔴 **Seven refusals. All write NOTHING, each prints its own fix, and re-running after fixing your scratch file is safe.** `status=leak-refused` (13) — rule (o): the TARGET repo's own leak scanner would not vouch for the delta, or could not be run at all. **ANY non-zero exit refuses**; it names the scanner and its exit code and reproduces the scanner's OWN lines (the last 20 of EACH stream); the doc is rolled back, and the index is not touched because this run staged nothing. 🔴 **Nothing attributed the refusal to your delta** — fix the SCRATCH file, or, if the tree was ALREADY red for something this handoff did not cause, read those lines and re-run with `--leak-pre-existing-approved`. 🔴 **That flag is the OPERATOR's call, not the agent's**; it is recorded on the run AND stamped `Leak-Gate-Approved: <scanner> exit=<n>` on the commit, it CLEARS an in-process scanner failure (a bad import exits non-zero too — read the output, not the number), and it does NOT reach a hang, a launch failure, or a declared scanner path that is not a runnable file. 📖 write-gate §H. `status=undefined-done` (11) — rule (m): a NEW doc whose `## Goal` carries no `closing-condition:`, or an update that DELETES the one the doc had; the refusal names which of six causes (wrong section, empty, unknown kind, fenced, unparsed, absent) and prints that cause's own fix. `status=rank-growth` (12) — rule (n): this update carries MORE `forcing: none` ranks than the doc does. Batch it under `## Defects (batched)`, or tag it with an external kind, or close one — `--rank-growth-approved` is the operator opt-in. `status=dated-topic` (7) — dated slug ⇒ per-session doc; **no flag bypasses it**, re-run without the date. `status=new-doc` (7) — no doc for this topic, others exist (listed), and none on the mainline (else ⇒ `stale-base`); if one IS this effort re-run with ITS topic — 🔴 `--new-effort` asserts genuine newness, **not a way past the list**. `status=unforced` (8) — a ranked item names no forcing function or an unrecognised kind. `status=unevidenced` (10) — a `Ruled out:` bullet names no `via: <kind>`. Rows read `[no via: field]` add one, INDENTED · `[unknown kind]` pick from the list · `[unparsed …]` re-spell as `via: <kind>` · `[fenced]` unfence YOURS, never promote a quote. 📖 write-gate §D. 🔴 **Read each row's marker — only one means "add a field"**: `[no forcing: field]` add one, INDENTED · `[unknown kind]` pick from the list · `[unparsed …]` re-spell it as `forcing: <kind>` · `[fenced]` **yours ⇒ unfence it; a QUOTE ⇒ tag the item, do NOT promote it** 📖 write-gate §C. ⚠ `forcing: none` and `via: assumed` are ACCEPTED, print an **advisory** above the diff; the write proceeds.

   🔴 **Exit 3 usually means nothing was written — but READ THE MESSAGE, because one arm of it committed.** Usually the rollback unlinks a NEW doc, so the handoff exists only in your scratch file. **The exception announces itself**: when the commit landed and a later step failed, the run says so and tells you not to re-run — re-running appends your findings twice. 🔴 **So `status=failed` is not by itself "nothing happened", and exit 3 is not a reliable tell** — a bad `--repo` or an unreadable `--update` exits 3 with no `status=` line at all, and `push-failed` uses exit 3 too. **Keep the scratch file until you have seen a commit sha**, name its path if step 5 never lands, and delete it once the commit exists.

   🔴 **Two more statuses exist and both mean NOTHING WAS WRITTEN OR IS SAFE — read them, do not retry blindly.**

   - **`status=behind` (exit 6)** — `--push` was asked for and the remote has commits this checkout does not, so the push would be rejected and the commit would be **stranded on a shared branch**: the state that silently blocks `ship.sh`. Nothing was written. It prints the exact `merge --ff-only`, and the preserve→verify→`reset --keep` path if that refuses. Fast-forward, then re-run the identical command.

   - **`status=push-failed`** — the pre-check passed and the push still failed (the remote can move in between; that race cannot be designed away). 🔴 **The COMMIT EXISTS** — true of this and of the one `failed` arm above, and of nothing else here. The message names it and hands over preserve→verify→`reset --keep` **in that order**. Do not leave it — an un-pushed commit on a shared branch is invisible until `ship.sh` skips that host.

   🔴 **`--confirm` WITHOUT `--push` leaves a real commit in this checkout only — and it says so.** `status=written commit=<sha> branch=<b>` is followed by `NOT PUSHED` plus the exact command: a `git push` on a feature branch, or the preserve-on-a-topic-branch route on a shared one (several repos forbid committing to theirs). A **SUCCESS, not a refusal** — exit 0 — but push it or open a PR **in this session**: an un-pushed handoff is one only you can read. 🔴 **Do NOT retry by re-running with `--push`**: the doc already carries the update, so a second run exits 5 `no-change` or **appends your findings twice**.

## §H — rule (o): the TARGET repo's own leak scanner reads the delta (2026-09-22)

### Why this is code and not a sentence

`handoff_doc.py` **commits AND pushes in one call** under `--confirm --push`, so
there is no window in which a human can scan between the two. Four
`denied-identifier` leak events have landed on handoff deltas and **one reached
`main` of a PUBLIC repository**. The standing remedy had been written as a
SENTENCE in the handoff document three times, in three wordings, and **no code
ran it**: `grep -c leakscan` over `scripts/lib/handoff_doc.py` was `0`, and `0`
across all seven files of this skill, against a positive control (`handoff`)
matching 7/7.

### The scanner is the TARGET repo's, and a repo with none PASSES

What counts as sensitive is a property of the repository, not of this tool: one
repo's denied-identifier set is another's ordinary vocabulary. So the gate
resolves a scanner out of `--repo`, from a **closed set of ONE declared relative
path** — `<target-repo>/tests/leakscan.py` — a lookup, never a glob, because a
glob finds a fixture or a README and then runs it as your repo's gate.

🔴 **THE SET DECLARED THREE AND NOW DECLARES ONE, AND THAT IS A MEASUREMENT.**
Across 175 checkouts, a `scripts/`-level scanner and a ROOT-level one existed in
**zero** of them: they bought no repository any coverage. The root entry also
carried a smaller version of the hazard the no-glob rule is about — a
root-level scanner file is exactly where a *fixture* or an *example* sits, and
this tool EXECUTES whatever it resolves, in someone else's repo, and attributes
the exit code to your delta. Adding a candidate is adding a program this tool
will run; measure that it exists first.

⚠ THE `<target-repo>/` PREFIX IS LOAD-BEARING PROSE, NOT DECORATION, and a gate
in this repository is what taught it — **the reason outlives the list that
provoked it**, which is why this paragraph survives the deletion of the two
candidates it was originally about. Written bare, a `<dir>/leakscan.py` token
reads as a path *here*; this repo has no such file, so `test_no_new_dead_paths`
failed with `a doc claims a file that does not exist`. It was right twice over:
it caught a real ambiguity, and the sentence it caught is the one whose entire
point is that the scanner belongs to the OTHER repo. `doc-path-ignore.list`
offers a silent exemption and says to prefer fixing the doc; this is why.

🔴 AND THE FIRST DRAFT OF THIS VERY PARAGRAPH FAILED THE SAME GATE, by spelling
the bare token in order to explain it. Describe the shape, never instantiate it
— an example that IS the thing it forbids is the thing it forbids.

A repo with no scanner **passes**, and says so: `NO SCANNER FOUND … PASS BY
ABSENCE, not a clean result`. Refusing there would make the tool unusable in
most repos and would be the permanently-red gate everyone learns to click
through.

🔴 **AND "NO SCANNER" IS A NARROWER QUESTION THAN `is_file()` ANSWERS.** That
predicate is False for three different worlds and only one of them is an
absence: it is also False for a **directory** at the declared path and for a
**dangling symlink**. Both were MEASURED to print `NO SCANNER FOUND` — a false
statement — and to let a delta carrying a denied identifier land unscanned. Both
are reachable without anyone doing anything odd: a sparse or partial checkout
that never materialises the scanner's directory, a scanner inside a **submodule**
(`git worktree add` populates none — and this document's own `status=behind`
advice tells you to write from a throwaway worktree), or a broken symlink after
a tree move. So the lookup now branches: a regular file is a scanner, a genuine
absence passes by absence, and **anything else present at that path is a
REFUSAL** — the same arm as a hang, and the opt-in does not reach it either.

⚠ `exists()` is not the discriminator, because it FOLLOWS the link and is False
for a dangling symlink too. `is_symlink()` is the only question that separates
"nothing here" from "a link to nothing".

### 🔴 Zero is the only pass — and exit 2 is the half a reader will re-narrow

**Any non-zero exit refuses.** There is no `== 1` comparison in the gate and
there must not be one. cairn's `tests/leakscan.py` exits **2** for "could not
vouch": one of its OWN controls misbehaved, and its docstring says in as many
words that 2 is not a clean result. Under a flat refuse that falls out by
construction and there is nothing left to test — which is exactly why the claim
is written here instead. A reader who has not been told it is the reader who
narrows the check to the code a scanner "normally" uses.

A scanner that cannot be **RUN** at all (a hang, a launch failure) is also a
refusal: `run_leak_scanner` raises, and a gate that cannot read is not a pass.
The operator opt-in below does **not** reach that arm — there is no verdict for
anyone to have read and approved, and approving an absence is the reassuring
zero this whole gate exists to refuse to print.

🔴 **"COULD NOT BE RUN" IS A SMALLER SET THAN IT SOUNDS, AND THE OPT-IN'S SCOPE
IS DECLARED RATHER THAN IMPLIED.** Exactly four things reach the no-verdict arm:
an `OSError` raising the process, a timeout, and the two present-but-unrunnable
paths above. Every **in-process** failure — an `ImportError`, a `SyntaxError`, a
missing dependency, an interpreter that will not run the file — starts fine and
then exits non-zero, which is **indistinguishable from "I ran and found
something"**. MEASURED with a scanner whose whole body was a bad import: without
the flag rc 13; **with** the flag rc 0, `status=written`, and `🔴 LEAK GATE
APPROVED THROUGH` printed for a gate that never read one byte.

⚠ **THE FLAG THEREFORE CLEARS IT, AND THAT IS STATED RATHER THAN FIXED.** A
scanner's exit code genuinely cannot separate those two cases in every instance,
and the only thing that could — parsing the scanner's output — is a dependency on
a format this gate has to work without, against scanners it has never seen. **So
the classification has no reliable form; what is closed instead is the reporting.**
The approved-through note now says on screen that the scanner **may never have
scanned**, with its own output (a traceback, if that is what happened) under it,
and the flag's `--help` states the scope in the same words. An earlier version of
that help said the flag "Does NOT apply when the scanner could not be RUN at all",
which read as covering every way a scanner fails to run and covered two.

🔴 **A non-zero exit is therefore not self-describing: read the OUTPUT, not the
number.**

### The gate does NOT attribute the refusal to your delta, and that is the decision

A whole-tree scanner cannot be asked about one file — `tests/leakscan.py` takes
**no paths**, it enumerates the repo from its own location. So any "was it THIS
delta?" answer can only be a comparison between two scans, one with the delta
and one without. **That comparison is a guess**, and three things make it the
wrong guess: a concurrent writer in a shared checkout, a scanner whose rule set
grew between the runs, and a new finding whose line is byte-identical to one the
scan was already printing.

🔴 **An earlier shape took that guess and, when it could not decide, printed
`LEAK GATE COULD NOT ATTRIBUTE — and this write was NOT blocked` and then
committed AND pushed anyway.** That arm existed only because an earlier
requirement forbade any bypass flag: with no escape hatch, the gate had to guess
or become unclearable on an already-red tree. The requirement is gone; the guess
went with it. **The gate never ships a delta while the scanner is refusing.**

### The already-red tree is an OPERATOR decision: `--leak-pre-existing-approved`

A target tree can be red for something your call did not cause. With no way
past, this would be the permanently-red gate `claude/RULES.md` says trains
everyone to route around — so there is one, and it is a **decision, not a
guess**: read the scanner's lines in the refusal, and if the finding is
pre-existing, re-run with `--leak-pre-existing-approved`.

It is rule (n)'s `--rank-growth-approved` shape reused rather than a second
spelling of the same idea: a deliberately long `--…-approved` flag, `store_true`,
held in a module constant and named by the refusal it overrides.

🔴 **IT IS THE OPERATOR'S CALL, NOT THE AGENT'S, AND THAT IS A DESIGN PROPERTY
RATHER THAN A PREFERENCE.** `/handoff` is driven by an agent, so this escape is
a flag an agent can reach on its own — for a gate whose stated stake is a public
repository, and in a PR whose own premise is that prose agents can ignore failed
four times. The operator's decision (2026-09-23) was to **keep naming the flag in
the refusal** — an unclearable gate is the permanently-red one everyone routes
around — and to close the gap on the **recording** side instead. The skill body
states the imperative: report the refusal and STOP.

🔴 **The flag is RECORDED IN TWO PLACES, AND THEY ARE NOT EACH OTHER'S BACKUP.**

- **On the run.** `LEAK GATE APPROVED THROUGH by --leak-pre-existing-approved`,
  the scanner's exit code and its own output, above the same `status=written` a
  clean run ends with. ⚠ The note says in its own words that **nothing checked
  the "pre-existing" claim**, and that **the scanner may never have scanned at
  all** — what was approved is everything it printed, whatever produced it.
- **On the COMMIT**, as a `Leak-Gate-Approved: <scanner> exit=<n>` trailer. This
  is the durable half. stdout survives only in a session transcript, and
  `scripts/transcript-push.sh` exports a bounded **tail** — so an approval early
  in a long session is unrecoverable from it, while a pushed commit carries the
  decision in `git log` forever. A **clean** run and a **no-scanner** run are not
  stamped: the absence is what makes the presence readable.

⚠ The trailer reuses `session_trailer.append_trailer` with a second key rather
than a second appender, so it composes with the `prepare-commit-msg` hook and
with `Claude-Session-Id:` exactly as that one does. One rule, one place.

### 🔴 The rollback unstages only what THIS run staged

The scan runs **after the write and before the `git add`**, because the scanner
reads the working tree. So on the leak path **nothing was ever staged** — and
the rollback helper, written for the failed-COMMIT arm where the tool really had
`git add`ed, still ran `git restore --staged -- <path>` unconditionally. MEASURED
with another session's staged edit to the same doc present beforehand: staged
before `[<the doc>]` → rc 13 → staged after `[]`. Silently unstaged, while the
run printed *"nothing from this run is left staged or written"* — **true as
written**, and concealing a change that was not from this run.

The helper now takes whether this run staged anything, as a required argument so
the next caller has to answer it rather than inherit an answer; and the line it
prints splits the same way, claiming only the half that happened. A path-limited
`restore --staged` is not enough on its own: the index entry for **our own path**
can be someone else's.

### 🔴 What the refusal SHOWS, and the two ways it stopped showing it

**The scanner's own lines are reproduced.** A refusal that does not say which
line and which rule is one the operator cannot act on, so the trade is
deliberate. ⚠ **Its justification is narrower than it was first written**, and
both halves matter before anyone widens what is printed:

- the flagged text is **not necessarily the operator's own about-to-be-committed
  content**. The scanner reads the whole TREE, so a finding can come from an
  untracked, unrelated file this handoff never displayed and was never going to
  commit. Measured.
- **the transcript is not a private channel either**: `scripts/transcript-push.sh`
  exports a bounded tail of every transcript. Self-hosted and authenticated, so
  this is a comment-accuracy point rather than a leak — but "the transcript is
  not where it becomes public" was a claim about a channel this repo actively
  exports, and it is now stated as what it is.

The honest statement of the trade: this moves flagged text out of the repository
and into a session transcript that is itself exported, in exchange for a refusal
the operator can act on — and it stops the **commit**, which is the public one.

🔴 **BOTH STREAMS ARE TAILED, SEPARATELY, AND A CONCATENATED TAIL IS WHAT THAT
REPLACED.** The refusal shows the last `LEAKSCAN_SHOWN_MAX` lines. When stdout
and stderr were concatenated with stderr last, a scanner writing that many
warning lines to stderr pushed **every line of stdout out of the tail**. MEASURED
in cairn's own shape — finding and `REFUSING` on stdout, 25 `COULD NOT READ …`
warnings on stderr, which its scanner really does emit: the refusal contained
**neither the finding nor the verdict**, only broken-symlink warnings. A
repository with 20+ unreadable files therefore produced a refusal nobody could
act on. ⚠ The docstring that justified the tail claimed "every scanner here ends
with its verdict" — a claim about the SCANNER that the concatenation made false
about the TEXT. Each stream now gets its own labelled tail, and neither is
dropped: the `could not read` warnings are what explain an INCOMPLETE scan.

🔴 **THE OUTPUT IS DECODED WITH `errors="replace"`, AND A STRICT DECODER WAS A
WRITE-LEAKING DEFECT.** This gate runs whatever the target repo ships and captures
both its streams, so the bytes are not under this tool's control. A scanner
emitting one latin-1 byte raised `UnicodeDecodeError` — a `ValueError` — which
escaped the gate *and* `main`'s `except (GitError, OSError)`: the run ended at rc
1, a code the exit model does not define, with a bare traceback and **the
unvouched doc still written**. ⚠ Reachability stated honestly: no scanner was
found in the wild that does this, and cairn's own reads with `errors="replace"`.
The claim that holds is the narrow one — this gate does not choose the bytes it
decodes, and a mangled line the operator can act on beats a traceback.

### 🔴 What this gate structurally CANNOT see

- **Anything sensitive already in the target tree that the delta did not
  change** — now a REFUSAL rather than a blind spot, but the gate still cannot
  tell you it was pre-existing. Only you can, and the flag is how you say so.
- **The DIFF THE TOOL ALREADY PRINTED.** The gate stops the COMMIT; by the time
  it runs, the unified diff — including whatever the scanner is about to refuse
  — is already in the transcript. Moving the scan earlier would mean writing the
  doc on the proposal run, whose whole contract is that it writes nothing. So
  the gate bounds what gets *published*, not what gets *displayed*.
- **A concurrent writer.** In a shared checkout another session's edit landing
  before the scan is refused alongside yours — a LOUD false refusal with the
  scanner's own lines on screen, which is the safe direction and the case the
  flag exists for.

### The timeout is 300 s, and that number is unattributed

Nobody derived it. The only measurement beside it is the ~2.0 s the real scanner
takes on the tree this was built against. It is kept because a generous ceiling
fails in the safe, loud direction — on a hang rather than on a slow machine —
which is a reason to keep the number, not a reason it is the right one. Said
here rather than dressed up as a derivation.

## §I — rule (p): a doc already over its ceiling may not GROW (2026-09-25)

### What it refuses

`status=size-ratchet`, exit **14**, nothing written. The predicate is two facts about
the MERGED document, both read out of one `budget_position()` call:

* the merge is **over its allowance** (`handoff_budget.MAX_BYTES`, or the doc's
  `GRANDFATHERED` entry when it has one); **and**
* the **net byte delta is positive** — the merge is bigger than the document it
  replaces.

A doc **under** its allowance is not this rule's population at all, however much the
update adds: that one stays `budget_warning`'s, and that function still refuses
nothing. Over the line, the delta must be `<= 0`.

### Why a refusal, where the warning deliberately refuses nothing

The warning has printed on every over-budget write since #1648 and the mechanism it
names went on regardless. MEASURED on one arc:

| | at the prune (`3c4a1c6`) | at the next peak (`219d58e`) | |
|---|---:|---:|---|
| whole doc | 64,097 B | 139,371 B | x2.17 |
| `Gotchas` | 45,984 B / 89 bullets | 83,618 B / 147 bullets | +58 |

🔴 **THIS TABLE IS THE SINGLE SOURCE OF TRUTH FOR THE WHOLE-DOCUMENT ROW.**
`scripts/lib/handoff_doc.py` and `scripts/tests/test_handoff_doc.py` point here
instead of restating the literals — the same ruling `scripts/tests/test_handoff_doc_size.py`
makes about the ceiling it owns, applied one arc down, and applied because restating
them is exactly how they went wrong. **Re-measure rather than believe them.** Two
commands, run inside the measured repo (`<cairn>`, whose doc this was):

```bash
git cat-file -s 3c4a1c6:claudedocs/handoff-cairn-control-plane.md   # 64097
git cat-file -s 219d58e:claudedocs/handoff-cairn-control-plane.md   # 139371
```

⚠ **THE FIRST PAIR WRITTEN HERE WAS WRONG, AND ONLY THE WHOLE-DOCUMENT ROW HAS BEEN
RE-MEASURED.** It read 63,433 B → 134,563 B, x2.1. Neither figure reproduces at **any**
revision of that file: all 107 of them were sized, and the corrected 64,097 hits two of
them while 63,433 and 134,563 hit none — so the scan is an instrument with a positive
control, not a guess. The `Gotchas` row above was **not** re-derived by that scan and is
neither confirmed nor retracted here; do not quote it as measured alongside the row that
was.

🔴 **The conclusion is unchanged, which is why this is a correction and not a
retraction.** The prune **worked** — 64,097 B is under the 65,536 B ceiling — and the
document then more than DOUBLED: 75,274 B of growth over the 6.1 days between those
two commits (2026-09-18 → 2026-09-25), roughly **12 KB a day**; every other section
combined would have fitted under the ceiling on its own.

⚠ **NO `Gotchas` RATIO IS QUOTED IN THAT SENTENCE, AND THE OMISSION IS THE POINT.** It
used to end "with `Gotchas` at 62% of the file" — a figure out of the row the paragraph
above declares NOT re-derived, offered inside the sentence that presents the conclusion
as measured, which is the exact thing that paragraph tells you not to do. Re-measured,
it reproduces neither the ratio implied by the table's own bytes nor the one an
independent span extraction gives, and the two disagree with each other; no replacement
figure is written here because a second unverified one is the same defect. Re-derive it
in the measured repo if a ratio is what you need. The conclusion does not rest on one:
a prune that worked, undone inside a week, is the whole of it.

🔴 **The growth is STRUCTURAL, not careless.** The bucket rules forbid durable content
in a REPLACE section, so the correct remedy for a finding is "move it to `Gotchas`" —
which APPENDS. That section has an entry rule and **no exit rule**; it is monotonic by
construction, and a warning is not a counterweight to a construction. One PR did
exactly that twice in one day, correctly by the bucket rule and harmfully by the size
rule, and two blind audit rounds missed the tension because each was scoped to one
axis.

### What was ruled out before building, so nobody re-derives it

* **Detection was never the gap.** `evictable_note()` already reports what has closed
  per-document. Corpus-wide: 468,110 B (14.1%) already evictable — 284,262 B of
  resolved investigations alone — while 28 docs sat over the hard cap.
* **The advice surface stays deleted.** #1821 removed it from `evictable_note()` after
  7 of 10 audit findings across four rounds came from it. This rule reuses that
  function's three-way branch exactly as it stands and widens nothing.
* **A prose rule was tried and did not hold.** The motivating document already carried
  a written prune discipline; it was read, and the section regrew within seven days.
* **A TTL / staleness stamp was designed and rejected before building.** A check that
  reddens because a stamp aged goes red on a day nobody changed anything, and
  `claude/RULES.md` calls a permanently-red gate worse than none.

### How you clear it

Two ways, and the first is usually the right one:

1. **Shrink a REPLACE section in the same delta.** `State now`, `Next steps` and
   `How to verify` are rewritten wholesale, so what they no longer need to say costs
   nothing to drop.
2. **Move what has closed out of the document first**, in its own commit, to the arc's
   archive file — then re-run the update unchanged. `Gotchas` and `Open investigations`
   APPEND through this tool, so it cannot shrink them for you.

🔴 **Eviction means MOVE, leaving a pointer.** Nothing in this rule can tell a deletion
from an eviction — the arithmetic is identical — so the refusal says so in its own
words. A ratchet whose cheapest escape is deleting a gotcha or a ruled-out theory has
made things worse than it found them.

### The override, and why it is not a convenience

`--override-size-ratchet "<why>"`. The reason is **required**; an empty one is refused
at argument-validation time with **exit 2**, not 14 — an empty flag is a complaint
about an ARGUMENT, and returning the rule's own verdict code would tell a caller its
document grew when the truth is that a flag was blank.

🔴 **The escape is what makes the refusal safe.** `/handoff`'s write path is the only
step that records a session, and `handoff-write-guard.py` blocks Stop until a handoff
is written; a refusal with no escape could cost a session its record, which
`handoff_budget.py`'s own header measures as the worse trade (22 of 253 sessions never
recorded, ZERO of them because a gate correctly declined). For the same reason
`size_ratchet_report()` **never raises** — any failure of its own code degrades to "no
ratchet", never to a crash in the landing step.

An overridden run is recorded **twice, in two channels, neither a backup for the
other**: a block above the diff at the moment the decision is taken, and
`Size-Ratchet-Override: <why>` on the commit, which is what survives a transcript
shipped as a bounded tail. The reason is whitespace-collapsed and clipped for the
trailer, because `session_trailer.valid_id()` rejects a value over 256 chars or
carrying a newline and `append_trailer` then returns the message **unchanged** — a
silent failure that would leave the run claiming a durable record that does not exist.

### What it does NOT do

* It is **not gated on `gate_enforces_budget()`**. Unlike the RED-gate claim in
  `budget_warning`, this refusal asserts nothing about anyone's CI — it is this tool's
  own verdict, true in any repo, and a repo shipping no `test_handoff_doc_size.py` is
  exactly where nothing else would ever notice.
* It **does not check where the bytes came from or went**. A net-zero delta that
  deleted a gotcha to pay for a new one satisfies it.
* It **reaches a doc's first write**, which is not the case it is named for. `before`
  is 0 for a new doc, so a first write over the ceiling is refused. That is the stated
  predicate rather than an oversight: rule (n) grandfathers round 1 because it compares
  a COUNT across rounds and a new doc has no previous round to have grown since, while
  this rule compares BYTES against a fixed ceiling a new doc can be over on day one.
* It **prunes no document**. This is the mechanism only.
