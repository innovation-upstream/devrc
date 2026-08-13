# Kickoff: gate the handoff doc's write+push, and make its shape append-for-findings

**Date:** 2026-08-13
**Status:** ready to dispatch — one unit, small
**Target:** `claude/skills/handoff/SKILL.md` (NOT `resume` — see §4)

---

## 1. The incident

The operator ran `/handoff` then `/resume` in `Vapor` (`scratch4`, hotkey `V`), window 1, in
`civitai/talos-infra`. Timeline on `origin/trunk`:

```
13:14  aae5ffc04  docs(handoff): the SSR capacity question is ANSWERED — and the answer is no
       ↓ resume kickoff pasted
13:30  0bf44dff8  docs(handoff): SSR's 72-replica ceiling is an HPA ratchet artifact, not demand
```

The operator's read was that the resume "started by updating and pushing the handoff". It did
not — its first four actions were: read the canonical handoff; find it absent from the primary
clone and correctly cite that repo's rule that absence there is not evidence; fetch and read it
from `origin/trunk`; check the clock. Then ~10 minutes of real HPA analysis. The push came at
the **end**.

Their own `/handoff` commit at 13:14 sits immediately above the kickoff in the scrollback,
which is why the two read as one event.

**But the real defect is underneath, and it is real:** `0bf44dff8` was written and pushed to a
shared trunk **with no confirm gate**. The operator did not approve it.

## 2. Why this is a gap and not a rule already broken

Both skills are already correct on their own terms:

- **`resume`** is read-only by contract — *"it never prompts and never writes"*, ends with
  *"Then wait for direction"*, and states that creating index entries is *"`/handoff`'s
  confirm-gated job at the end of a session, not this step's."* It followed that.
- **`/handoff`** gates its **index** write — *"Write only on explicit confirm, diff first…
  on decline, discard"* (`SKILL.md:129`), and step 4 *"blocks on a y/N"* (`:64`).

The gap: the **handoff DOC's own write+push** carries no equivalent gate, and the session that
runs *after* a resume inherits no constraint at all. It applied the end-of-session ritual
without the gate that ritual is supposed to carry.

## 3. What to change — four rules

**a. Do NOT forbid updating the handoff.** In this case updating was correct and valuable: the
session answered the doc's open question *and corrected a prior misreading* (the adapter
serving 110m against raw Prometheus's 32m — the earlier interpretation of at-max time was
wrong). Suppressing that means the next session re-runs ten minutes of analysis to rediscover
it. Optimising for doc stability over state accuracy is backwards.

**b. Gate the PUSH, not the write.** Writing locally is cheap and reversible; pushing to a
shared branch as a side effect of unrelated work is what needs consent. Reuse the gate
`/handoff` already specifies for the index write — one compact diff, a single `y/N`, discard on
decline. Do not invent a second gate shape.

**c. Replace the status header; APPEND the findings.** The status / next-steps block is current
state and should be overwritten. Findings and diagnosis state append. This incident is the
argument: `0bf44dff8` *superseded* an earlier interpretation, and the value is seeing that the
prior reading was corrected — not finding it silently gone. Note `SKILL.md:20` already calls
the live diagnosis state "the single highest-value part of the handoff"; an append-only shape
is what protects it.

**d. Never write when the session did not advance state.** A resume that goes nowhere
overwriting a good handoff is the worst case and the one nobody notices until they try to retry
cleanly. Require an explicit "what changed since the doc was written" before any write is
offered; if the answer is nothing, say so and write nothing.

## 4. Do NOT change `resume`

Its contract is right and was followed. Widening it to cover "what the session does afterwards"
would make a read-only re-entry step responsible for the whole session's write behaviour, which
is the wrong seam. The constraint belongs where the writing is specified.

## 5. Verification

- **The gate must be watched to work in both directions.** Decline → nothing written, nothing
  pushed, and the working tree byte-identical afterwards. Accept → exactly one commit, exactly
  the shown diff. A gate only ever tested on accept is not a gate.
- **Append-vs-replace needs a fixture with real prior content**: a handoff carrying two earlier
  findings, updated by a session with a third. Assert both earlier findings survive verbatim
  *and* the status header is the new one. A test that only checks the new text is present would
  pass a wholesale rewrite.
- **The no-advance case**: a session that changed nothing must produce no write offer at all —
  not an empty diff, not a no-op commit.
- 🔴 **`claude/skills/handoff/SKILL.md` has a size budget** like its siblings. Check before
  adding; if it is near the ceiling, evict or demote to `reference/` in the same change rather
  than growing it.
- Gate: `nix build .#checks.x86_64-linux.pytests`. **Read counts, not exit codes** — a cached
  build prints an *empty* log, use `nix log`. Re-pin the relevant `TARGET_FLOORS` entry from the
  file's own `_suggested_floor`; show the arithmetic.

## 6. Out of scope
- The `resume` skill (§4).
- Whether `/handoff` should push at all in repos where trunk is the deploy branch — that is a
  per-repo policy question, not this change.
- The subsystem-index write path — already gated, already correct, do not disturb it.
