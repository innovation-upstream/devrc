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

Writing locally is cheap and reversible. Pushing to a shared branch as a side
effect of unrelated work is the act that needs consent. So the tool's default
mode writes nothing at all — not the doc, not a commit, not a ref — and landing
it takes a second invocation carrying `--confirm` (and `--push`), which is the
action that happens after the `y`. A decline is therefore not a code path that
has to behave correctly; it is the absence of one.

`scripts/tests/test_handoff_doc.py` hashes the whole repo tree either side of a
default-mode run, because a gate that has only ever been watched to accept is
not a gate.

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
