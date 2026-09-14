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

🔴 **TWO RESIDUALS THIS SKIP DOES NOT CLOSE, both measured in round 2 and both
left open deliberately rather than met with more machinery.**
- A base carrying **BOTH** a canonical `## Next steps` and a reworded queue is
  still counted from the canonical one alone, so a PARTIAL migration — the
  natural intermediate state of the very fix this section prescribes — can still
  be refused for shrinking. The skip keys on the heading's PRESENCE, not on
  whether the count is complete.
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
work; the finding is about what happens after it. `self_generated_report` still
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
measures at 28–38 real documents depending on the predicate. Measured end to
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
