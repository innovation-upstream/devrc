---
name: resume
description: "Re-enter work from the latest handoff doc: read it, re-verify it against live state, and propose ranked next steps. Use when starting a session, returning after a few days, or told to pick up where we left off."
argument-hint: "[topic-slug] — optional; defaults to the most recently modified handoff doc"
allowed-tools: Bash, Read, Grep, Glob
---

# /resume — re-enter from a handoff

Goal: rebuild context fast and **verify it's still true** before acting (a handoff reflects what was true when written — live state may have moved).

Topic argument (optional): `$ARGUMENTS`.

**Reference topics** — deployed at `~/.claude/skills/resume/reference/`, source `~/workspace/devrc/claude/skills/resume/reference/`:

| Load when | File |
|---|---|
| The argument resolved nothing, the doc is in a linked worktree, or you need every `handoff-read:` verdict | `~/.claude/skills/resume/reference/handoff-resolution.md` |
| A digest block needs interpreting in more depth than step 2 gives — `DRIFT`, `CLAWGATE`, `SKILL`, a `PR` finding | `~/.claude/skills/resume/reference/digest-blocks.md` |
| Before adopting any framing out of an `## Open investigations` block | `~/.claude/skills/resume/reference/open-investigations.md` |
| `handoff_search` in depth — proving `--exclude-slug` landed, and its exit-code vocabulary | `~/.claude/skills/resume/reference/handoff-search.md` |
| Reading what `cairn recall` printed — every flag, the index badges, the featured pick, `MALFORMED`, its exit codes | `~/.claude/skills/resume/reference/cairn-recall.md` |
| The shared-queue lock in depth — ownership scope, legacy refs, why a check alone cannot protect the first mover | `~/.claude/skills/resume/reference/report-and-claim.md` |

## Steps

1. **Locate the handoff.** With a topic, read `claudedocs/handoff-<topic>.md`; otherwise the most recently modified `claudedocs/handoff-*.md` in the active repo (`ls -t claudedocs/handoff-*.md | head`). **Not every repo uses that lowercase shape** — if the glob is empty, fall back to `ls -t claudedocs/*HANDOFF*.md | head` before concluding there is no handoff (`resume-state.sh` resolves it in exactly that order). If BOTH are empty, say so, offer to reconstruct from git/PRs, and say plainly that **nothing was reconciled** rather than reporting the absence of drift as a clean bill of health.

   🔴 **If you know the doc, put its PATH in the argument** — an absolute one when it lives in a sibling worktree. A mistyped topic slug still falls back to the newest doc, so **the digest underneath is then about a different initiative than you named**; a handoff path that is not there reconciles NOTHING. Either way the run says so and withdraws the DRIFT all-clear. Argument shapes, linked-worktree re-anchoring and the ambiguity case: 📖 `~/.claude/skills/resume/reference/handoff-resolution.md`.

2. **Re-verify against live state FIRST — run the deterministic reconciler, don't hand-roll it.** It runs before you read the doc on purpose: it is what decides *which copy of the doc is authoritative*, and reading the wrong one first is the failure this ordering exists to stop (measured: a clone served a handoff 276 lines behind `origin/trunk`, and the whole resume was framed on it).
   ```bash
   bash ~/workspace/devrc/scripts/resume-state.sh "$ARGUMENTS"
   ```
   It resolves the handoff, reconciles it against FRESH live state in one call, and prints a compact digest: `SKILL`, `GIT/PR`, `WORKLOAD`, `ALERTS`, `CLAWGATE`, `INVESTIGATIONS`, `DOD`, and a `DRIFT` block. **Interpret the digest, especially `DRIFT`.** Do NOT re-derive it by hand-rolling `git`/`kubectl`/`gh`; it degrades gracefully to git-only, and you drill down only where the digest flags something.

   🔴 **An empty `DRIFT` is only good news if something was actually reconciled — check FIRST, in two places.** (a) The `handoff:` line: `(none found — git-only)` means no doc was loaded and nothing was reconciled at all. (b) The **`!! GAPS (N)` banner** inside `DRIFT`, listing sources that did not answer, each line prefixed `!`. **It prints alongside real findings too**, so a list of `-` findings is complete only if no gap block sits beside it. Only `(none detected — live state matches the handoff's claims)` with **no** gap block is an actual all-clear — and an unreachable cluster is *not* reported as a gap, so `(cluster unreachable — skipped)` means workloads and alerts went unchecked whatever `DRIFT` says. 📖 `~/.claude/skills/resume/reference/digest-blocks.md`.

   🔴 **`handoff-read:` names the copy to open — do not read the doc until you have read that line.** If it says the working-tree copy is STALE, read the `handoff-other-copy:` path instead and say in your report which copy you read; `origin freshness UNCHECKED` is **not** a verification.

   🔴 **The `SKILL` block is about THESE INSTRUCTIONS, so read it before you follow them.** `deployed copy is N commit(s) BEHIND origin/main` ⇒ **you are executing a superseded procedure**: read the `git -C <repo> show origin/main:<path>` text the DRIFT line hands you and follow THAT (fixing the deployed copy is a `home-manager switch`, never a `git pull`).
   - `skill-read: … COULD NOT MEASURE (…)` — **seven** reasons, each printing a `!` gap: `no deployed copy at <path>`; the path `resolves nowhere` (a dangling symlink into a GC'd `/nix/store` path — it happens); `no git checkout of the skill source found`; the checkout `has no origin remote`; `no origin/<default-branch> ref in <repo>`; the file `is not on` `origin/main`; or git `could not hash the deployed copy or the` `origin/main` blob. **None of these is an all-clear** — the age of what you loaded is simply UNKNOWN, which is a different finding from "current".

   **The `CLAWGATE` block reads the handoff's own `clawgate-task:` front matter** and fetches that task with `clawgatectl task get`, printing its live `status` and how many comments postdate the doc. Two DRIFT lines change what you do next: a task being resumed as open work that the board calls `complete` or `ready_for_review`, and N comments written since the doc — **read those comments before acting on the doc**. ⚠ **The comment count names its own clock, and there are FOUR** — `by last commit`, `by last commit on <ref>`, `by file mtime`, and `UNDATED`. The doc's `last commit` date is preferred, because a fresh `git worktree add` stamps every file at checkout and mtime would make every comment read as older than the doc; `on <ref>` means the origin copy was read and dated. `file mtime` and `UNDATED` each print a `!` gap, and under `UNDATED` every comment counts as newer on purpose. 🔴 A missing `clawgatectl`, an auth failure, an unreachable server, a vanished task or an unreadable status ALL emit a `!` gap saying the task's state is UNKNOWN — never a reassuring zero.

   🔴 **The `INVESTIGATIONS` block answers "is this diagnosis still LIVE?" — it is the one block about the doc's own PROSE rather than about live infrastructure.** An `## Open investigations` block is written in the PRESENT TENSE and `/handoff` APPENDS it forever: nothing retracts one, so it reads as current for the life of the document. It prints one row per `### ` block — age in days, heading, and **which clock produced that age**. Read the clock: an `as-of stamp` is the most precise and the only one an author can correct; `first commit carrying this block` (or `first commit carrying this block on <ref>`) is the content-derived fallback and survives a `git worktree add`; `the doc's last commit` and `file mtime` each print a `!` gap — the first dates the DOCUMENT rather than the block, so treat that age as a floor, and the second is reset by any checkout or copy.
   🔴 **`EXPIRED` and `UNDATED` are different findings and must be read differently.** **`EXPIRED`** is a `-` DRIFT finding: the block WAS dated and is past the window. It does not mean the diagnosis is wrong — it means **re-measure before adopting its framing**, or retire the block. **`UNDATED`** is a `!` gap: no clock could place it, so whether it is current is UNKNOWN, which is not the same as fine. 📖 `~/.claude/skills/resume/reference/open-investigations.md`.

   🔴 **The `DOD` block is the one that can END this session instead of filling it.** It prints the handoff's own `closing-condition:` — the finish line its round 1 froze — and you **read it BEFORE the ranked list**: a rank is a candidate, and the closing condition says whether any of them should be worked at all.
   - `closing-condition: check — <a command/PR/alert>` ⇒ **RUN IT FIRST.** Green means this arc is CLOSED: say so, say what closed it, and stop.
   - `closing-condition: judgement — <a NAMED person reads NAMED evidence>` ⇒ nothing you do closes it. Put the evidence in front of that person.
   - `declares NO closing-condition` ⇒ "is this arc finished?" is **UNANSWERABLE**, which is not the same as "unfinished" — say that, and offer to add one as the first item.
   🔴 **FROZEN AT ROUND 1 means what it says.** Outstanding work that is not that line is a **NEW arc**, not another round of this one. Measured over 299 arcs, the round-1 objective was met by round 1–7 in all five deep-read arcs, which then ran 13–23 rounds off a rank queue growing 2–6 items a round.

   🔴 **A `PR` line's repo is part of the finding — do not re-qualify it yourself.** `PR owner/repo#N` is cross-repo; a bare `PR #N` means this repo; `PR #N UNATTRIBUTED` was deliberately not resolved, so ask rather than guess a repo for it.

3. **Read the handoff in full — the copy the `handoff-read:` line named**, not reflexively the one in
   the working tree — **but treat its "Open investigations" section as RECALL, not live state.** Measured three times, a session adopted a framing that had already been superseded. 🔴 **Eliminations age well; the question they serve does not** — re-ask what the block is trying to explain before adopting its hypothesis, and prefer the block's own *values* over its narrative. Before working any open item, check whether the repo moved under it:
   ```bash
   git -C <repo> log --since=<doc-date> --oneline -- <the pipeline/script/dir the item is about>
   ```
   A hit means read those commits before re-deriving anything. The cost is one command; the cost of skipping it is a whole session.

4. **Surface what past sessions already recorded — TWO recall surfaces, BOTH UNCONDITIONAL. Run both now, before the report:**

   ```bash
   cairn recall --repo "<path>"
   python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "<this handoff's topic, in plain words>" --limit 3 --exclude-slug "<the handoff: basename from step 2>"
   ```

   **Query the TOPIC, not an open item** — every resume has a topic; not every resume has an open item, and a step that can only run in the second case is a conditional. When a specific open item is not covered by the topic query, run the command again with that item's own words.

   🔴 **`--exclude-slug` IS NOT OPTIONAL POLISH — WITHOUT IT THE TOP HIT IS THE DOC YOU JUST READ.** Measured over 20 real runs: the session's own handoff was the #1 hit in 13 of 20. Pass the value step 2 printed on its `handoff:` line. How to tell it landed: the run prints `excluded=<slug>` on its scope line and an `in_scope_docs` **lower than** `indexed_docs` — 🔴 `excluded=` proves the flag PARSED, not that it MATCHED, so read the pair.

   🔴 **THE TWO SURFACES ARE NOT SCOPED ALIKE, AND THIS ONE IS CORPUS-WIDE.** `cairn recall --repo` is scoped to one repo; `handoff_search` takes no `--repo` here and searches **every** repo in `handoff_index.REPO_ENV_HANDLES` — on this host `devrc`, `homelab-talos`, `datapacket-talos`, `civitai`, two of which are client repos. A devrc-topic query routinely returns client-repo sections above the devrc ones, and the tool's banner carries a staleness posture but **no** sensitivity language. So: read hits from another repo as recall, and **never paste one into a public repo or a client-facing artifact without checking which repo it came from** — the `<repo>/<slug>` on each hit is what tells you. Pass `--repo <label>` to scope it when that matters more than reach.

   **Keep `--offline`** — it answers from git refs with **no database**. Every response carries a recall banner and the literal `indexed_docs=N indexed_sections=M`: a hit is a **POINTER TO VERIFY**, never a current reading. 🔴 **A zero is not automatically an answer** — the tool names which zero it got and exits non-zero for the four that are not readings: **3** broken index · **4** empty scope (your filter selected no rows) · **6** unmeasurable corpus · **7** the repos resolved and derived zero handoff docs. 🔴 A fifth non-zero is not a zero at all — rc **2**, usage: the `--exclude-slug` value named NO slug, so the search never ran; fix the value and re-run. Only `NO MATCH` at rc **0** means the corpus was asked and is silent. **Non-blocking:** on any non-zero, print the stderr line, say retrieval was unavailable, and carry on with the item. 📖 `~/.claude/skills/resume/reference/handoff-search.md`.

   **The rest of this step is about `cairn recall`, the first command.** It is the **read half** of the store `/analyze-service` and `/handoff` write to — the terse pointer sheet that *outlives the handoff doc you just read*. It drives `subsystem_recall`, syncing the cache first so the answer is dateable; run the module bare (`subsystem_recall.py`) against an unstamped store and it refuses instead, which `cairn sync` fixes.

   🔴 **`--repo` takes a PATH, not a repo name.** A bare name is resolved against your **cwd**, so `--repo datapacket-talos` becomes `$PWD/datapacket-talos` and the run **exits 2** naming the mistake. Pass an absolute path, one of the pre-exported handles (`$DEVRC`, `$HOMELAB`, `$DATAPACKET`, `$CIVITAI`), or **`--scope <name>`**, which names the store directory directly and skips git derivation entirely. ⚠ Re-measured 2026-09-17: this used to be an uncaught **exit 3** with a raw `git ... cannot change to` traceback and no remedy; the sidecar's account of that incident is history, not current behaviour.

   🔴 **Everything it prints is `from index` — RECALL, NEVER LIVE OBSERVATION.** It was curated by *past* sessions, was not re-derived just now, and was not matched against anything in this session. Read the `caveat:` line and carry that label into your report: an index bullet is a **pointer to verify**, and it may describe a gotcha already fixed. **Never fold it into the live-state findings from step 2** — those were measured, these were remembered. Entries carry client-identifying detail: honour the printed `sensitivity=` and never copy a line into a public repo.

   **`scope-absent` / `scope-empty` means NOTHING RECORDED YET — that is the ordinary case, not an error and not a clean bill of health.** Say plainly "the index has nothing for this repo yet" and move on; do **not** report it as an absence of drift, and **do not go create an entry** — that is `/handoff`'s job at the *end* of a session, not this step's. **`scope-unreadable` is NOT `scope-empty`**: the scope holds entry files and not one could be indexed, so nothing was read at all — the one "empty screen" you must not report as "nothing recorded yet".

   **Non-blocking, always.** If it exits non-zero, print the stderr line verbatim, note that recall was unavailable, and **continue the resume** — a broken index is not a reason to stop re-entering the work. **Never fall back to recollection** about what the index "probably says".

   📖 Every flag (`--list`, `--ref`, `--limit`, `--page`, `--search`, `-C`, `--all-scopes`, `--max-hits`), what the bare command prints, the index badges, the featured-entry pick, `MALFORMED` and the exit codes: `~/.claude/skills/resume/reference/cairn-recall.md`.

5. **Report**:
   - 🔴 **THE DoD VERDICT FIRST, before the ranked list** — one line, and it is the report's headline: **ADDRESSED** (the `DOD` block's closing condition is met — say what met it, say the arc is **CLOSED**, and propose nothing further) · **NOT ADDRESSED** (name the ONE item that is not met) · **UNMEASURABLE** (the doc declares no closing condition — say so as a gap, and make adding one the first item). Anything outstanding that is not that line belongs to a **NEW arc**; say which arc you are proposing work for. **An inventory of what remains is not a verdict** — measured across 185 close-checks, 164 (89%) re-opened the arc within a median 1.0 h, and an inventory is what they were answered with.
   - One-paragraph "where things stand" (reconciled with what you just verified).
   - **Ranked next steps**, with the single highest-leverage action first. 🔴 **An audit or review finding is a DEFECT, not a rank** — it belongs on the handoff's `## Defects (batched)` list, and `/handoff` step 5 REFUSES an update whose `forcing: none` count grows (`status=rank-growth`).
   - Any drift you found between the handoff and live state.
   - Anything **either step-4 surface** recalled that bears on the next steps, kept separate from what step 2 measured. 🔴 **Carry each one's OWN label, because they are different corpora:** `cairn recall` prints `from index` (curated subsystem pointers), `handoff_search` prints `from handoff docs` (sections of past handoffs, possibly from another repo).

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
   is invisible to it and visible to `gh pr list`. **Run the sweep again
   immediately before `gh pr create`** — two moments, because the window is ~20
   minutes and the second one is where the sunk cost is highest. **And push the
   branch the moment you create it**, before doing the work — an empty commit is
   enough; a branch is visible to `git ls-remote` the instant it lands.

   **rc 10 ⇒ STOP** — it prints who, since when, **where** (host + owner-id) and what they
   called it, so check the printed `where:` against your own before assuming a peer.
   **rc 11 ⇒ past its TTL and may be abandoned**: decide explicitly, then
   `claim-work --steal "$SLUG"` or `--release "$SLUG"`. 🔴 **rc 12 ⇒ YOU already
   hold it — CARRY ON**: it is what a re-run of `/resume`, or a session resuming
   after a context reset, gets for its own item. **`claim-work --release "$SLUG"`
   when you finish or abandon the item** — an unreleased ref is the one way this
   blocks work.

   🔴 **Ownership is per HOST and per WORKTREE, and it does NOT depend on your cwd.**
   Any subdirectory of the worktree you claimed from can release it; a SIBLING
   WORKTREE of the same clone cannot, and neither can a different clone or the
   other host.

   🔴 **The claim namespace is GLOBAL.** Every claim lands on ONE canonical
   remote, taken from `claim-work`'s own location — **not** from the repo you are
   standing in. Run the bare command from wherever you are; do **not** pass `--repo`.

   🔴 **What you claim is PUBLIC.** A claim commit is pushed to the canonical origin and this repo is PUBLIC: keep the subject generic — no client names, real hostnames, paths or captured text. A newline or control character in `--subject` is rc 2.

   🔴 **It FAILS OPEN.** No canonical remote, no network, no auth ⇒ a loud stderr
   warning and exit 0. A degraded run means you are UNCLAIMED, not that you hold
   it — say so, and lean on (b), which you were running anyway.

   📖 Legacy `cwd:`-format refs, the ownership token, and why a check alone cannot protect the
   first mover: `~/.claude/skills/resume/reference/report-and-claim.md`. Measurements and
   rejected alternatives: `~/.claude/skills/handoff/reference/shared-queue.md`.

Then wait for direction. Pair: `/handoff` (it writes the index entries step 4 reads).
