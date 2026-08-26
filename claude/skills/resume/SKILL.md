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

1. **Locate the handoff**: if a topic is given, read `claudedocs/handoff-<topic>.md`; otherwise find the most recently modified `claudedocs/handoff-*.md` in the active repo (`ls -t claudedocs/handoff-*.md | head`). **Not every repo uses that lowercase shape** — civitai-manager names its handoff `<civitai-manager>/claudedocs/SESSION-HANDOFF.md` — so if the glob comes back empty, fall back to `ls -t claudedocs/*HANDOFF*.md | head` before concluding there is no handoff (`resume-state.sh` resolves it in exactly that order). If BOTH come back empty, say so and offer to reconstruct state from git/PRs instead — and say plainly that nothing was reconciled, rather than reporting the absence of drift as a clean bill of health.

   🔴 **If you know the doc, put its PATH in the argument.** `resume-state.sh` reads a `claudedocs/handoff-*.md` / `claudedocs/*HANDOFF*.md` path out of a prose topic (`"…the listing work; handoff: <path>"`), which is the form this skill passes through verbatim — and only that shape. A bare `README.md` mentioned **inside a prose topic** is prose, not a handoff reference. ⚠ That is about the SCAN, not about the whole argument: an argument that IS a path to an existing file is taken as the handoff whatever it is named — `resume-state.sh README.md` reconciles `README.md` — because the explicit-path form accepts any filename by design. **If you supply an argument and it resolves nothing — a mistyped topic, or a path that is not there — the run says so as a `!` gap** naming what you asked for and what it read instead, and withdraws the DRIFT all-clear. **That gap means the digest is about a different initiative than you named** — re-run with the path. A run with NO argument stays silent, because there "newest" is the contract rather than a guess.

   The gap carries one extra clause — *"the newest of N … MOVES between runs"* — only when the fallback actually had two or more docs to choose between, because only then is it true.

   🔴 **The working-tree copy is a GUESS about what the handoff says — run step 2 FIRST and read the copy it names.** Measured 2026-08-20: a datapacket-talos clone served a handoff **276 lines behind `origin/trunk`**, and the whole resume was framed on it; it was caught by luck. That repo's `CLAUDE.md` records the same class twice more (a clone once served a skill file **692 commits** stale) because a shared clone's checked-out branch is unpredictable and its local refs are routinely far behind. **A missing file is not evidence either** — `git log origin/<default-branch> -- <path>` before concluding a handoff does not exist. 🔴 **And a third manifestation: a stale clone makes a merge TOOL pick the wrong base and report success** — measured 2026-08-21, `handoff_doc.py` on a clone 313 behind would have rebuilt an 891-line doc from a 290-line base, ~601 lines discarded, exit 0. It warns about that now; the same staleness that misleads you misleads anything reading the tree.

   You do not have to remember any of this: `resume-state.sh` fetches and compares before it reconciles, and prints the answer on its own line. **Do not read the doc until you have read that line.**
   - `handoff-read: working-tree copy (identical to origin/trunk)` — read the file in the tree; they are the same.
   - `handoff-read: 🔴 origin/trunk copy (the working-tree copy is STALE: 412 lines local vs 688 on origin/trunk)` — the tree copy is **not** what the last session wrote. A `handoff-other-copy: /tmp/resume-handoff-XXXX.md` line gives you the authoritative text as a file; **read that path**, and say in your report which copy you read. The digest has already reconciled against it, so the digest and the doc agree.
   - `handoff-read: ⚠ working-tree copy, which has UNCOMMITTED edits and differs from origin/trunk` — this session's work-in-progress wins, but it is unpushed; `handoff-other-copy:` holds the origin text for comparison.
   - `handoff-read: working-tree copy — origin freshness UNCHECKED (…)` — the comparison could **not** be made (no remote, no `origin/<default-branch>` ref, not a git repo). This is not a verification; do not report it as one.

2. **Re-verify against live state FIRST — run the deterministic reconciler, don't hand-roll it.** It runs before you read the doc on purpose: it is what decides *which copy of the doc is authoritative*, and reading the wrong one first is the failure this ordering exists to stop.
   ```bash
   bash ~/workspace/devrc/scripts/resume-state.sh "$ARGUMENTS"
   ```
   This is the initiative-scoped, on-demand collector (modeled on `standup.sh`). It resolves the handoff, then reconciles it against FRESH live state in one call and prints a compact digest: `SKILL` (is the copy of THIS skill you are executing current with `origin/main`? — see below), `GIT/PR` (branch ahead/behind, dirty, referenced PR states + CI, branch existence), `WORKLOAD` (handoff-named deployment readiness + canary phase — v1: datapacket), `ALERTS` (firing alerts scoped to the initiative's namespace), `CLAWGATE` (the task the handoff records, reconciled against the live board), and a `DRIFT` block. **Interpret the digest — especially `DRIFT`** (the lines where live state contradicts the handoff, e.g. a PR the doc calls in-flight has already merged). Do NOT re-derive this by hand-rolling `git`/`kubectl`/`gh`. It degrades gracefully (git-only) when a source is unreachable or the repo isn't datapacket; only reach for a targeted `kubectl`/`gh` drill-down if the digest flags something needing one. 🔴 **An empty `DRIFT` is only good news if something was actually reconciled — check that FIRST, in two places.** (a) The `handoff:` line: `(none found — git-only)` means no doc was loaded and nothing was reconciled at all. (b) The **`!! GAPS (N)` banner** inside `DRIFT` — a ruled-off block listing sources that did not answer, each line still prefixed `!` (e.g. `gh answered for 0 of 3 referenced PR(s)`). **It prints alongside real findings too**, so a list of `-` findings is complete only if no gap block sits beside it. The digest states both conditions itself: `(no handoff loaded — nothing to reconcile…)` and `(nothing detected, but a source did not answer — NOT a clean bill of health)`. Only `(none detected — live state matches the handoff's claims)` with **no** gap block is an actual all-clear. A handoff path outside any git repo now reports the gap instead of the all-clear; an unreachable **cluster** still does not, so a datapacket resume that says `(cluster unreachable — skipped)` has not checked workloads or alerts whatever `DRIFT` says.

   **The `CLAWGATE` block reads the handoff's own `clawgate-task:` front matter** (written by `/handoff`; the shared parser is `scripts/lib/clawgate_handoff.sh`) and fetches that task with `clawgatectl task get`, printing its live `status` and how many comments postdate the doc — see the clock note below, which is NOT always its mtime. Its DRIFT lines are the two that change what you do next: a task the doc is being resumed as open work that the board calls `complete` or `ready_for_review`, and N comments written since the doc — **read those comments before acting on the doc**, they are the newest statement about this work. ⚠ **The comment count names its own clock, and there are FOUR** — `by last commit`, `by last commit on <ref>`, `by file mtime`, and `UNDATED (<ref> carries no commit for this path)`. Read which one you got: the doc's last COMMIT date is preferred, because a fresh `git worktree add` or `clone` stamps every file at checkout and mtime would then make every comment read as older than the doc. **`on <ref>`** means the digest read the origin copy (the working tree was stale) and dated THAT copy — the local branch's history describes the file that was discarded. **`file mtime`** and **`UNDATED`** each print a `!` gap saying so; under `UNDATED` every comment is counted as newer on purpose, because the only other date available belongs to the copy that was NOT read. A status outside clawgate's four (`open` / `in_progress` / `ready_for_review` / `complete`) is a `!` gap too, never a quiet pass. 🔴 **Read this block under the same `!` rule as everything else.** `clawgatectl` missing, an auth failure, an unreachable server, a task that no longer exists and an answer with no readable status ALL emit a `!` gap saying the task's state is UNKNOWN — never a reassuring zero. The one case that is deliberately *not* a gap is a handoff carrying no `clawgate-task:` field at all: nothing was asked, so nothing went unanswered, and the block says so in its own words — `(no clawgate-task: field in this handoff …)` is **not** a statement that the task is fine, and a field that is present but unreadable IS a gap.

   🔴 **The `SKILL` block is about THESE INSTRUCTIONS, so read it before you follow them.** A skill is loaded ONCE, from `~/.claude/skills/<name>/SKILL.md` — a nix-managed path that tracks the last `home-manager switch`, never `origin/main`, and that `git pull` does not move. Measured 2026-08-25: a session ran this skill's step 6 (`gh pr list` + push-the-branch-early) two hours after #847 had replaced it with `scripts/claim-work.sh`, and nothing could say so. The block now prints one `skill-read:` line per checked skill (`resume` by default; `RESUME_STATE_SKILL` overrides with a space-separated list, and an EMPTY value means "check none" and says so):
   - `skill-read: resume — deployed copy is CURRENT with origin/main (…)` — the text you loaded is what `origin/main` holds. **A claim about the instant the digest ran, not about the rest of the session** — a skill that changes mid-session is NOT covered by this.
   - `skill-read: 🔴 resume — deployed copy is N commit(s) BEHIND origin/main for claude/skills/resume/SKILL.md (newest it lacks: <sha> <subject>) […]` — **you are executing a superseded procedure.** The DRIFT line hands you the exact `git -C <repo> show origin/main:<path>` to read; read THAT text and follow it, not what was loaded. Getting the deployed copy fixed is a `home-manager switch` (or `scripts/ship.sh`), never a `git pull`.
   - `skill-read: ⚠ resume — deployed copy is the working tree, which has UNCOMMITTED edits …` — the skill is being edited right now; you are running unpushed instructions.
   - `skill-read: … COULD NOT MEASURE (…)` — **seven** reasons, each printing a `!` gap: `no deployed copy at <path>`; the path `resolves nowhere` (a dangling symlink into a GC'd `/nix/store` path — it happens); `no git checkout of the skill source found`; the checkout `has no origin remote`; `no origin/<default-branch> ref in <repo>`; the file `is not on` `origin/main`; or git `could not hash the deployed copy or the` `origin/main` blob. **None of these is an all-clear** — the age of what you loaded is simply UNKNOWN, which is a different finding from "current". (This list is not prose: `test_resume_state_skill_freshness.py` scrapes the reasons out of the script and fails when one is missing here.)
   - `skill-read: 🔴 resume — deployed copy is older than the newest N commit(s) …; the scan stopped at its cap, so the DISTANCE was not measured` — it is **not** current, and this run declined to compute by how much. Raise `RESUME_STATE_SKILL_SCAN_CAP` if you want the number.
   - `skill-read: 🔴 resume — deployed copy matches NO commit of <path> on origin/main` — the walk could not place what you loaded in that path's history. **Three** causes, and the line asserts none of them: uncommitted; on a branch that has not merged (which is what `home-manager switch --flake ~/workspace/devrc` off a feature branch produces, and CLAUDE.md recommends that for validating a nix edit); or **older than a rename of the path** — the walk has no `--follow`, so pre-rename content is invisible to it and is nonetheless present on `origin/main` under the old name. Either way it is not what `origin/main` says today.

   🔴 **A `PR` line's repo is part of the finding — do not re-qualify it yourself.** Findings for a cross-repo ref are printed as `PR owner/repo#N …`; a bare `PR #N …` means this repo. Until 2026-08-20 the reconciler stripped the qualifier off `owner/repo#N` and looked every number up in the LOCAL repo, which on one real datapacket-talos handoff produced **18 DRIFT lines** telling the reader to do follow-ons for talos-infra PRs that had nothing to do with the work. If you see `PR #N UNATTRIBUTED`, the doc wrote a bare `#N` while also naming other repos — the number is genuinely ambiguous and was deliberately **not** resolved. Ask, or read the doc's own context; do not guess a repo for it. (Writing `owner/repo#N` in handoffs is what makes them reconcilable — a habit worth keeping in `/handoff`.)

3. **Read the handoff in full — the copy the `handoff-read:` line named**, not reflexively the one in
   the working tree — **but treat its "Open investigations" section as RECALL, not live state.**

   🔴 **A handoff's open-investigation block is exactly as stale-able as an index bullet, and nothing marks it.** The status header is obviously dated; a mid-diagnosis block reads as current forever, because it is written in the present tense by someone who was mid-diagnosis. MEASURED 2026-08-19: a doc's leading hypothesis for an intermittent CI failure was **superseded one day after the doc was written** — root-caused, with a classifier, tests and a PR-comment integration already shipped in the same repo — and a session re-derived the retracted hypothesis, refuted a variant of it, measured a failure rate, and was about to build a capture mechanism **that already existed**.

   MEASURED AGAIN 2026-08-20, and the block was *well* written — values, eliminations, a named "Next probe": every ruled-out candidate was still true, yet the **framing** was wrong. It reported the unattributed rows as an unidentified live producer growing at ~21/h; they were the repo's own test suite, 100% synthetic. A session that trusted the framing would have hunted a caller that does not exist. 🔴 **Eliminations age well; the question they serve does not** — so re-ask what the block is trying to explain before adopting its hypothesis, and prefer the block's own *values* over its narrative.

   **Before working any open item, check whether the repo moved under it:**
   ```bash
   git -C <repo> log --since=<doc-date> --oneline -- <the pipeline/script/dir the item is about>
   ```
   A hit means read those commits before re-deriving anything. The cost is one command; the cost of skipping it is a whole session.

4. **Surface what the subsystem index already records for this repo** (read-only, ~1 command, no network):

   ```bash
   python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo <repo>
   ```

   This is the **read half** of the store `/analyze-service` and `/handoff` write to — the terse pointer sheet that *outlives the handoff doc you just read*. It had two writers and no reader, so nothing ever opened it at resume time.

   **What the bare command prints (the digest):** the caveat, then a one-line **INDEX of every entry in the scope** — ref, `N nuance` (its `## Nuance / work-history` bullet count, *not* entry size), `sensitivity=`, and — only when they fire — the badges `🔴 N OPEN`, `🔴 N NEAR-MISS`, `⚠ N UNVERIFIABLE`, `🔴 NO <heading>` — never truncated, then **exactly ONE entry in full** (`## What it is` + `## Pointers` + `## Nuance / work-history`), then a line saying the other bodies were listed but not printed. Measured 2026-08-13 on `datapacket-talos`, the scope holding 25 of the store's 29 entries: **4,876 B / ~1,219 tokens**, against **31,485 B / ~7,871 tokens** for the old default — which *also* hid 13 of the 25 entries. So it is now both cheaper and complete; the earlier claim that it "costs a page, not a dump" was false for the only scope big enough to matter, and this is the corrected, measured version.

   ⚠ **Those byte figures are from 2026-08-13 and the scope has grown since** — re-measure rather than quoting them. Two deltas measured 2026-08-21, when `## What it is` was added to the printed body: the digest grew **+266 B** on `datapacket-talos` (37 entries), **+244 B** on `civitai`, **+394 B** on `devrc` — one body's worth, not one per entry — while `--list` grew by a flat **+18 B** on all three, which is the footer sentence and nothing else. **The per-entry index rows carry no `## What it is` at all**, deliberately: that surface is printed once per entry on every read.

   🔴 **`🔴 N OPEN` means N bullets in that entry DECLARE unfinished business — re-check each against live state before acting on the entry.** A remedy that has since landed reads exactly like one that has not: one entry proposed a one-line fix that shipped **two minutes later** and went on being served as outstanding for 22 days. **The absence of the badge means nothing was declared, NOT that nothing is open** — the marker is opt-in and predates almost none of the corpus.

   🔴 **Three further badges say the row's own numbers are not measurements. Read them before you read the counts beside them.**
   - `🔴 N NEAR-MISS` — N bullets **tried** to write a marker and missed the grammar (emphasis, a parenthetical before the colon, a missing date colon), so they declare nothing and `N OPEN` is **short by up to N**. Measured 2026-08-19 over the live store (53 entries, 323 nuance bullets): **8 bullets declare `OPEN:` and parse; 2 more attempted a marker and missed.** This is the population most likely to hold a stale open action — `--ref` the entry and read the bullets themselves.
   - `⚠ N UNVERIFIABLE` — N `RESOLVED:` bullets name no sha, so the closure cannot be checked with `git cat-file -e`. Advisory: closing is the point, and the sha is what makes the claim checkable rather than asserted.
   - `🔴 NO <heading>` — that heading is **absent or renamed**, so `N nuance` and every openness count on that row are **0 by parse failure, not by measurement**. It fires for `## Pointers` and `## Nuance / work-history` only — the two a count depends on. A missing `## What it is` is surfaced under that entry's own body instead, and never badges a row, because no number on the row is derived from it. Heading matching is exact-string at column 0, so a rename, a trailing colon or an indent all land here. The content is on disk and invisible to this read: open the file, or run `subsystem_touch.py --validate --scope <scope>`, which reports the populations the row cannot. **Do not read such a row as an empty entry** — a renamed heading is exactly how an entry with a declared `OPEN:` renders as `0 nuance` with no badge at all.

   **The featured entry is a PICK, and the output names the basis.** It is chosen by running the writer's own path→subsystem matcher over the paths quoted in this repo's newest `claudedocs/handoff-*.md` (`resolved via <doc> — N quoted path(s) name it`), and otherwise by the newest entry file (`most-recent fallback`). Read that phrase before you read the entry: on the real store the fallback fires more often than not (15 of the 40 most recent datapacket handoffs resolved to an entry), and **a fallback pick says nothing whatsoever about relevance.**

   **Drill down instead of dumping.** `--list` prints the index alone (2,580 B on that same scope, measured 2026-08-13) — use it when you only need to know what is on record. `--ref <name>` prints any single entry in full; that is the right follow-up to a line in the index, and an ambiguous ref is reported with its candidates, never picked. `--limit N` restores the old print-N-bodies behaviour with its loud truncation notice — reach for it only when you genuinely want the dump.

   **The index is capped at 100 lines per page, newest-first by entry-file mtime.** Below that cap (every scope today — the largest holds 25) nothing changes and the header still says `none omitted`. Above it the header switches to `entries 1–100 of N` and a notice names the remainder and the flag: `--page 2`, `--page 3`, … reach the older ones, oldest last. The order is stated in the output on purpose — cutting an *alphabetical* index hides entries by an accident of their names, cutting a *recency* index hides the stale ones.

   **`--search <query>` reads by MATCH instead of by whole entry** — reach for it when you want one fact rather than an orientation, and as scopes grow past the point where a body is affordable. It prints HUNKS, each carrying its own `scope/ref`, section, `file:line`, `sensitivity=`, and its **score beside the threshold**, so a weak match is visibly weak. 🔴 **Matching is ONE-WAY** — a query term is matched by corpus words that EXTEND it (`postgres` → `postgresql`), never by ones it merely contains, so type the SHORTER form when unsure: `kube`, not `kubeconfig`. A no-match then means the term really is absent, and the printed near-miss + `--threshold` tells you which. Measured on `datapacket-talos`: `--search minio` 5,267 B / ~1,316 tok, `--search 'nginx ratelimit'` 1,711 B / ~427 tok, and a full-store scan of all 29 entries takes ~40 ms (stdlib only — it shells out to nothing).

   - Matching is fuzzy and **coverage-based**: each query term contributes its share, so a two-word query needs both words and `nginx kryptonite` returns nothing rather than `nginx`'s hits. Typos are tolerated (`conection` → `connection`); concatenations work (`ratelimit` finds `rate-limit`); tokens shorter than 4 characters must match exactly.
   - 🔴 **A no-match is not an empty screen.** It says how many entries were scanned, and either names the closest sub-threshold candidate with the exact `--threshold` that would surface it, or says plainly that nothing scored above zero — i.e. an *absent* term, not a weak one. Read which of the two you got before rephrasing.
   - `basis=entry-name` on a hunk means the ENTRY's name matched and none of its lines did — the hunk is a worked example, not the thing you searched for.
   - **Context is the enclosing bullet by default; `-C N` overrides with N raw lines** and that choice is yours. Entries are structured and their bullets wrap, so a raw window can cut one in half and emit a fragment that reads like a complete instruction. The bullet is the safe thing to quote; a raw window shows you what SURROUNDS the match (the heading above it, the next bullet), which is what you want when orienting rather than quoting.
   - `--all-scopes` searches the whole store, not just this repo's scope; `--max-hits N` raises the display cap (default 10, truncation always printed).

   **`sensitivity=client-confidential (declared: internal)` means the file wrote a marker the schema does not know and the fail-safe overrode it.** The schema knows exactly three — `client-confidential`, `personal`, `public` — and anything else, or nothing at all, folds to `client-confidential`. An absent marker is annotated with nothing (nobody claimed anything); an *overridden* one is shown so the override is never silent. Honour the effective value either way.

   🔴 **Everything it prints is `from index` — RECALL, NEVER LIVE OBSERVATION.** It was curated by *past* sessions, was not re-derived just now, and was not matched against anything in this session. Read the `caveat:` line and carry that label into your report: an index bullet is a **pointer to verify**, and it may describe a gotcha already fixed (pruning is manual). Never fold it into the live-state findings from step 2 — those were measured, these were remembered. Entries carry client-identifying detail: honour the printed `sensitivity=` and never copy a line into a public repo.

   **`scope-absent` / `scope-empty` means NOTHING RECORDED YET — that is the ordinary case, not an error and not a clean bill of health.** The store is young (a couple of scopes against work spanning ~12 repos), so most repos will print this. Say plainly "the index has nothing for this repo yet" and move on; do **not** report it as an absence of drift, and do not go create an entry — that is `/handoff`'s confirm-gated job at the *end* of a session, not this step's.

   🔴 **A `🔴 MALFORMED` block means entry files exist that could NOT be indexed — the output is short, and it says so.** One bad entry used to abort the whole scope (measured: 2 good entries + 1 malformed served **0** and exited 3); it now serves the good ones and names each rejected file with its reason on its own `malformed index entry ...` row. Read those rows: the content in them is real, it is invisible to `--list`/`--ref`/`--search`, and the index header stops claiming `none omitted` for exactly that reason. Report it as a **store defect to fix**, never as an absence of content — and never assume a name is unrecorded just because `--ref` missed it while a reject is listed. The fix is `python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py --validate <path>` (front matter is parsed line by line, so the usual cause is a value wrapped across two lines — an `aliases: [...]` list must be on ONE line).

   **`scope-unreadable` is NOT `scope-empty`.** It means the scope holds entry files and *not one* of them could be indexed, so nothing was read at all — the command exits non-zero for it, and it is the one "empty screen" you must not report as "nothing recorded yet".

   **Non-blocking, always.** It never prompts and never writes. **It exits non-zero only when NOTHING readable came back** (missing store, unreadable entry, or `scope-unreadable`/`search-unreadable`) — a scope that served some entries alongside a `MALFORMED` block exits **0**, because recall was available and was also honest about its gaps. If it does exit non-zero, print the stderr line verbatim, note that recall was unavailable, and **continue the resume** — a broken index is not a reason to stop re-entering the work. Never fall back to recollection about what the index "probably says".

5. **Report**:
   - One-paragraph "where things stand" (reconciled with what you just verified).
   - **Ranked next steps**, with the single highest-leverage action first.
   - Any drift you found between the handoff and live state.
   - Anything the index recalled that bears on the next steps — labelled `from index`, kept separate from what step 2 measured.

6. 🔴 **BEFORE ACTING ON A NEXT-STEP, CLAIM IT — the ranked list is a SHARED
   QUEUE, and `claim-work` is the lock. This is a COMMAND, not a habit.**

   ```bash
   claim-work --list                                            # what is already taken
   SLUG=$(claim-work --slug-for <handoff-doc> <rank>)            # the canonical id — both sessions derive the SAME one
   claim-work "$SLUG" --subject "<the item, in your own words>"  # 0 = yours · 10 = taken · 11 = taken but stale
   ```

   **rc 10 ⇒ STOP** — it prints who holds it, since when, and what they called
   it. Pick another item, or coordinate. **rc 11 ⇒ the claim is past its TTL and
   may be abandoned**: decide explicitly, then `claim-work --steal "$SLUG"` or
   `--release "$SLUG"`. **`claim-work --release "$SLUG"` when you finish or
   abandon the item** — an unreleased ref is the one way this blocks work.

   🔴 **It FAILS OPEN.** No origin, no network, no auth ⇒ a loud stderr warning
   and exit 0. A degraded run means you are UNCLAIMED, not that you hold it —
   fall back to the manual half (`gh pr list --state open` before you start and
   again before `gh pr create`, and push the branch the moment you create it).

   **Why a claim and not a check:** whoever moves FIRST cannot see the second
   session at all — it does not exist yet — so no pre-flight check can protect
   them. The claim happens at DRAW time, before any work, and the push to
   `claim/<slug>` is git's own atomic ref compare-and-swap, so two simultaneous
   first movers resolve to exactly one winner.
   🔴 **Worktree isolation does NOT prevent this and is not the answer.** Every
   colliding session isolated correctly and no file was ever clobbered — this is
   a TASK-ALLOCATION collision, and isolation is what HIDES it.
   ⚠ **The exact-slug match is the HARD lock; `--list`'s SUBJECT column is a SOFT
   signal.** It does not catch a reworded duplicate — read the list yourself.
   📖 Measurements, rejected alternatives and the limitation:
   `~/.claude/skills/handoff/reference/shared-queue.md`.

Then wait for direction. Pair: `/handoff` (it writes the index entries this step reads).
