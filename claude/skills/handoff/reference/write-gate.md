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
