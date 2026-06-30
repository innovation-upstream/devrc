---
name: resume
description: "Re-enter work from the latest handoff doc: read it, re-verify it against live state, and propose ranked next steps. Use when starting a session, returning after a few days, or told to pick up where we left off."
argument-hint: "[topic-slug] — optional; defaults to the most recently modified handoff doc"
allowed-tools: Bash, Read, Grep, Glob
---

# /resume — re-enter from a handoff

Goal: rebuild context fast and **verify it's still true** before acting (a handoff reflects what was true when written — live state may have moved).

Topic argument (optional): `$ARGUMENTS`.

## Steps

1. **Locate the handoff**: if a topic is given, read `claudedocs/handoff-<topic>.md`; otherwise find the most recently modified `claudedocs/handoff-*.md` in the active repo (`ls -t claudedocs/handoff-*.md | head`). If none exists, say so and offer to reconstruct state from git/PRs instead.

2. **Read it fully.**

3. **Re-verify against live state — do not assume the doc is still accurate:**
   - `git status -sb`, `git log --oneline -5` — has the branch/PR moved since the doc?
   - Were the "in flight" items completed or merged already?
   - If the doc claims a deploy/state, check it live (pod/HelmRelease/PR status as relevant).
   - Flag any line of the handoff that now contradicts reality.

4. **Report**:
   - One-paragraph "where things stand" (reconciled with what you just verified).
   - **Ranked next steps**, with the single highest-leverage action first.
   - Any drift you found between the handoff and live state.

Then wait for direction. Pair: `/handoff`.
