# The endgame — what the report must contain, and claiming the item before you act

Routed from the `/resume` core — `~/.claude/skills/resume/SKILL.md`, source
`~/workspace/devrc/claude/skills/resume/SKILL.md`. Sliced out VERBATIM; the step
numbers below are that core's steps.

Load this for the report contract in full, and for the shared-queue lock: rc meanings,
ownership scope, legacy refs, and why a check alone cannot protect the first mover.

5. **Report**:
   - 🔴 **THE DoD VERDICT FIRST, before the ranked list** — one line, and it is the report's headline: **ADDRESSED** (the `DOD` block's closing condition is met — say what met it, say the arc is **CLOSED**, and propose nothing further) · **NOT ADDRESSED** (name the ONE item that is not met) · **UNMEASURABLE** (the doc declares no closing condition — say so as a gap, and make adding one the first item). Anything outstanding that is not that line belongs to a **NEW arc**; say which arc you are proposing work for. **An inventory of what remains is not a verdict** — MEASURED across the 185 close-checks that landed on an arc session, 164 (89%) re-opened it within a median 1.0 h, and an inventory is what they were answered with.
   - One-paragraph "where things stand" (reconciled with what you just verified).
   - **Ranked next steps**, with the single highest-leverage action first. 🔴 **An audit or review finding is a DEFECT, not a rank** — it belongs on the handoff's `## Defects (batched)` list, and `/handoff` step 5 now REFUSES an update whose `forcing: none` count grows (`status=rank-growth`). Measured over 299 arcs: rounds minted 2–6 new ranks each and the worst queues reached 54–84 items, which is why those arcs never closed.
   - Any drift you found between the handoff and live state.
   - Anything **either step-4 surface** recalled that bears on the next steps, kept separate from what step 2 measured. 🔴 **Carry each one's OWN label, because they are different corpora:** `cairn recall` prints `from index` (curated subsystem pointers), `handoff_search` prints `from handoff docs` (sections of past handoffs, possibly from another repo). Reporting a corpus hit as `from index`, or omitting it because step 5 named only the index, loses the provenance that makes it checkable.

6. 🔴 **BEFORE ACTING ON A NEXT-STEP, DO BOTH OF THESE — the ranked list is a
   SHARED QUEUE, and neither half covers what the other one does.**

   ```bash
   # (a) THE LOCK — a COMMAND, not a habit.
   claim-work --list                                            # what is already taken
   SLUG=$(claim-work --slug-for <handoff-doc> <rank>)            # the canonical id — both sessions derive the SAME one
   claim-work "$SLUG" --subject "<the item, in generic words>"   # 0 = yours · 10 = taken, STOP · 11 = taken but stale · 12 = ALREADY yours, carry on

   # (b) THE SWEEP — UNCONDITIONAL, not a fallback. Before you start…
   gh pr list --repo <r> --state open --json number,title,headRefName,files
   ```

   🔴 **(b) IS NOT A DEGRADED-RUN FALLBACK AND MUST NOT BE TREATED AS ONE.** The
   lock only ever sees work somebody CLAIMED; a duplicate that was never claimed
   is invisible to it and visible to `gh pr list`. That class is explicitly listed
   as NOT covered in `claudedocs/design-claim-by-push.md` → "What is NOT covered".
   **Run the sweep again immediately before `gh pr create`** — two moments,
   because the window is ~20 minutes and the second one is where the sunk cost is
   highest (measured: `#774` public **22 min** before its duplicate, `#388`
   **18 min** before the other side's first commit; each visible, neither
   checked). **And push the branch the moment you create it**, before doing the
   work — an empty commit is enough; a branch is visible to `git ls-remote` the
   instant it lands.

   **rc 10 ⇒ STOP.** Usually somebody else holds it — but see the LEGACY note
   below: on a pre-2026-08-26 `cwd:`-format ref it can also mean *you* hold it
   from a different directory. Either way STOP is the safe reading; check the
   printed `where:` against your own before assuming a peer. It prints who, since when,
   **where** (host + owner-id, because one git identity covers both hosts and
   every agent on them), and what they called it. Pick another item, or
   coordinate. **rc 11 ⇒ the claim is past its TTL and may be abandoned**: decide
   explicitly, then `claim-work --steal "$SLUG"` or `--release "$SLUG"`.
   🔴 **rc 12 ⇒ YOU already hold it — CARRY ON.** Not a refusal: it is what a
   re-run of `/resume`, or a session resuming after a context reset, gets for its
   own item. It used to be rc 10, so the same output said "you already hold it"
   and "DO NOT start this item" three lines apart and the honest reading was
   *stop*. **`claim-work --release "$SLUG"` when you finish or abandon the item**
   — an unreleased ref is the one way this blocks work. ⚠ `--release`/`--steal` of
   a LIVE claim that is not yours is **refused** (rc 10); `--force` overrides,
   deliberately.

   🔴 **Ownership is per HOST and per WORKTREE, and it does NOT depend on your cwd.**
   The token is `/etc/machine-id` + `git rev-parse --git-dir`, so any
   subdirectory of the worktree you claimed from can release it, at any depth. A
   SIBLING WORKTREE of the same clone cannot — it is a different agent, and for
   one day it was told rc 12 "carry on" about a peer's live claim because the
   token used `--git-common-dir`, which every linked worktree of a clone shares.
   A different clone, or the other host, cannot either.

   ⚠ **LEGACY refs (`cwd:` format, created before 2026-08-26+1) use a SECOND
   predicate, and it differs BY VERB.** For the claim/check verdict they are
   strict per-worktree — so a subdirectory of the claiming clone reads **rc 10**,
   not rc 12, even though you hold it. For `--release`/`--steal` the old
   clone-wide accept still applies, so a sibling worktree CAN release one without
   `--force`. That asymmetry is deliberate: narrowing the destructive side would
   have made already-published refs unreleasable by anyone. It ages out as those
   refs are released. Re-derive which refs are affected rather than trusting a
   count here: `git ls-remote --heads origin 'refs/heads/claim/*'`, then
   `claim-work --check <slug>` — a legacy one says so in its output.
   It was `uname -n` + a hash of `$PWD` until 2026-08-26+1, which was wrong in
   BOTH directions at once: both hosts are called `nixos` (so each read the
   other's claims as its own), and `cd scripts/` made you a stranger to your own
   claim for the rest of the TTL.

   🔴 **The claim namespace is GLOBAL.** Every claim lands on ONE canonical
   remote, taken from `claim-work`'s own location — **not** from the repo you are
   standing in. That is the point: handoff docs live in devrc while the work
   happens in other repos, so a per-repo namespace would let the same item be
   claimed once per remote. It was `$PWD` until 2026-08-26 and did exactly that.
   Run the bare command from wherever you are; do **not** pass `--repo`.

   🔴 **What you claim is PUBLIC.** A claim commit is pushed to the canonical
   origin and this repo is PUBLIC: keep the subject generic — no client names,
   real hostnames, paths or captured text. A newline or control character in
   `--subject` is rc 2, because free text sits above the ownership trailers in the
   commit body.

   🔴 **It FAILS OPEN.** No canonical remote, no network, no auth ⇒ a loud stderr
   warning and exit 0. A degraded run means you are UNCLAIMED, not that you hold
   it — say so, and lean on (b), which you were running anyway.

   **Why a claim and not only a check:** whoever moves FIRST cannot see the second
   session at all — it does not exist yet — so no pre-flight check can protect
   them. The claim happens at DRAW time, before any work; two true concurrent
   first movers both send `old=0000…` and git's ref transaction is a
   compare-and-swap on that value, so exactly one create lands.
   🔴 **Worktree isolation does NOT prevent this and is not the answer.** Every
   colliding session isolated correctly and no file was ever clobbered — this is
   a TASK-ALLOCATION collision, and isolation is what HIDES it.
   ⚠ **The exact-slug match is the HARD lock; `--list`'s SUBJECT column is a SOFT
   signal.** It does not catch a reworded duplicate — read the list yourself.
   📖 Measurements, rejected alternatives and the limitation:
   `~/.claude/skills/handoff/reference/shared-queue.md`.

Then wait for direction. Pair: `/handoff` (it writes the index entries this step reads).
