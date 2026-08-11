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

1. **Locate the handoff**: if a topic is given, read `claudedocs/handoff-<topic>.md`; otherwise find the most recently modified `claudedocs/handoff-*.md` in the active repo (`ls -t claudedocs/handoff-*.md | head`). **Not every repo uses that lowercase shape** — civitai-manager names its handoff `claudedocs/SESSION-HANDOFF.md` — so if the glob comes back empty, fall back to `ls -t claudedocs/*HANDOFF*.md | head` before concluding there is no handoff (`resume-state.sh` resolves it in exactly that order). If BOTH come back empty, say so and offer to reconstruct state from git/PRs instead — and say plainly that nothing was reconciled, rather than reporting the absence of drift as a clean bill of health.

2. **Read it fully.**

3. **Re-verify against live state — run the deterministic reconciler, don't hand-roll it:**
   ```bash
   bash ~/workspace/devrc/scripts/resume-state.sh "$ARGUMENTS"
   ```
   This is the initiative-scoped, on-demand collector (modeled on `standup.sh`). It resolves the handoff, then reconciles it against FRESH live state in one call and prints a compact digest: `GIT/PR` (branch ahead/behind, dirty, referenced PR states + CI, branch existence), `WORKLOAD` (handoff-named deployment readiness + canary phase — v1: datapacket), `ALERTS` (firing alerts scoped to the initiative's namespace), and a `DRIFT` block. **Interpret the digest — especially `DRIFT`** (the lines where live state contradicts the handoff, e.g. a PR the doc calls in-flight has already merged). Do NOT re-derive this by hand-rolling `git`/`kubectl`/`gh`. It degrades gracefully (git-only) when a source is unreachable or the repo isn't datapacket; only reach for a targeted `kubectl`/`gh` drill-down if the digest flags something needing one. 🔴 **An empty `DRIFT` is only good news if something was actually reconciled — check that FIRST, in two places.** (a) The `handoff:` line: `(none found — git-only)` means no doc was loaded and nothing was reconciled at all. (b) Any `DRIFT` line starting with `!` — those are *gaps*, sources that did not answer (e.g. `gh answered for 0 of 3 referenced PR(s)`), and they print alongside real findings too, so a list of `-` findings is complete only if no `!` line sits beside it. The digest states both conditions itself: `(no handoff loaded — nothing to reconcile…)` and `(nothing detected, but a source did not answer — NOT a clean bill of health)`. Only `(none detected — live state matches the handoff's claims)` is an actual all-clear. ⚠ And even that is not yet airtight: an unreachable cluster, or an explicit handoff path outside a git repo, still reaches it having checked nothing — tracked as a follow-up on PR #326.

4. **Report**:
   - One-paragraph "where things stand" (reconciled with what you just verified).
   - **Ranked next steps**, with the single highest-leverage action first.
   - Any drift you found between the handoff and live state.

Then wait for direction. Pair: `/handoff`.
