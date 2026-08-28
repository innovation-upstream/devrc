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
* **`/handoff`** gates its **index** write: "Write only on explicit confirm,
  diff first… on decline, discard", and the step blocks on a y/N.

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
with **no bypass** — under a one-doc-per-effort rule a date in the slug has no
legitimate use, and a bypass would be taken every time.

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

🔴 **Two things this does NOT do.** It cannot check that a cited forcing function
is real or genuinely external — the enumeration is structural, the evidence
beside it is prose. And the *"does not get worked"* half is **not enforced**:
this module writes the doc, it does not consume the queue. The skip belongs in
`/resume` step 6 and `claim-work`, and is not implemented.

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
  still excluded, verified on both patterns. 🔴 **Four things it newly admits,
  not one.** A delta audit found three more after this section named only the
  first: (1) a snake_case identifier *ending* in `_forcing` (`some_forcing:
  none` parses); (2) `_FORCING_ATTEMPT`'s key admits one *starting* with the key
  (`the forcing_fn returns none` is now a near-miss); (3) that pattern's KIND
  admits a kind followed by `_` (`forcing the user_id column`, `forcing the
  gate_keeper to retry`); (4) the class is `[A-Za-z0-9]`, so a **non-ASCII**
  word character before the key excludes nothing — `éforcing: gate` and
  `強forcing: gate` parse, and did not under `\b`, whose `\w` is unicode. So
  "the character before the key is a letter" holds for **ASCII** letters only.
  All four occur **0** times across both corpora (devrc 126 docs, homelab-talos
  139), and all four are bounded by the same closed-vocabulary argument as the
  markup class.

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
