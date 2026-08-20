---
name: handoff
description: "Write a canonical session-handoff doc and a copy-paste kickoff message so work resumes cleanly in a new session. Use at end of session, before a context reset, or when told to write the handoff."
argument-hint: "[topic-slug] — optional; defaults to the current work's topic"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# /handoff — canonical session handoff

Goal: capture everything needed to continue this work in a fresh session with **zero re-discovery**, then hand back a kickoff block to paste.

Topic argument (optional): `$ARGUMENTS`. If empty, infer a short kebab-case topic from the current work.

## Steps

1. **Snapshot live state** (don't trust memory — observe):
   - `git -C <repo> status -sb` and `git -C <repo> log --oneline -8`
   - Uncommitted diff summary (`git diff --stat`), current branch, any open PR (`gh pr view` if relevant)
   - Any in-flight deploy/build/job state relevant to this work
   - **For every UNRESOLVED bug/investigation, capture the live diagnosis state** (the next section). This is the single highest-value part of the handoff: without it, the next session re-runs every probe you already ran. Record observed *values* and *eliminations*, not narrative — paste the actual error string, the actual header/response, the exact failing request, the command whose output you read. "We looked into the CSP issue" is worthless; "`frame-ancestors` on app.example.test = `https://example.test https://*.example.test` — does NOT include `gen-matrix.embed.example.test`, confirmed via response header on GET /apps/run/dogfood-manual" is the whole point.

2. **Write the handoff doc** to `claudedocs/handoff-<topic>.md` in the active repo (create `claudedocs/` if absent). 🔴 **If that file ALREADY EXISTS this is an UPDATE, not a rewrite — do not touch it here.** Draft your new/changed sections into a scratch file instead (`## ` headings, a *delta* — omit a section and it is left alone) and land it in **step 5**, which owns the merge and the gate. Use this structure — be concrete, link exact file paths and commands, no vague prose:

   ````markdown
   # Handoff: <topic> — <YYYY-MM-DD>

   ## Run this first — the index, one read-only command
   ```bash
   python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo <repo>
   ```
   Terse pointers this doc does not carry, curated by past sessions and outliving it.
   🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
   reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
   nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
   Non-blocking: if it exits non-zero, print the stderr line and carry on.

   ## Goal
   What we're trying to achieve and why (1–3 lines).

   ## State now
   - Branch / PR: ...
   - What's DONE this session (with commit hashes / file paths)
   - What's IN FLIGHT (started, not finished)
   - Deploy/verify status: deployed? verified against the real path? (be honest)

   ## Open investigations — live diagnosis state
   <!-- One block PER unresolved bug/investigation. Omit the whole section only if nothing is mid-diagnosis. -->
   ### <bug/symptom in one line>
   - **Symptom + exact repro:** what breaks, and the precise click-path / request / command that triggers it.
   - **Observed (with values):** the actual evidence gathered — error strings, response headers, log lines, query outputs, span timings. Real values, copy-pasted, not paraphrased.
   - **Ruled out:** hypotheses already eliminated and the evidence that killed each (so they're not re-tried).
   - **Leading hypothesis:** current best theory, and why.
   - **Next probe:** the single most useful command/observation to run next, written so it can be executed verbatim.

   ## Next steps (ranked)
   1. ...
   2. ...

   ## Gotchas / decisions / dead-ends
   - Things already tried that didn't work; constraints; why X over Y.

   ## How to verify
   Exact command(s) / click-path that prove the work is correct.
   ````

3. **Output a kickoff block** (fenced, ready to copy-paste into the next session) of the form:
   ```
   /resume — continue the <topic> work. Canonical handoff (read first): <repo>/claudedocs/handoff-<topic>.md
   <one-line of the single most important next action>
   ```

   🔴 **Keep the literal `/resume` prefix, and do NOT rely on it.** Measured twice on 2026-08-13: a prose kickoff got the doc read and the index skipped entirely (zero `subsystem_recall.py` calls), and the `/resume`-prefixed replacement did **exactly the same** to a *dispatched agent* — a subagent gets the kickoff as prompt TEXT, with no CLI slash-command parsing, so the prefix reads as a topic label. **The deterministic hook is step 2's doc, not this block**: both sessions read the doc first, immediately, which is why the index command lives at the TOP of it. The prefix costs nothing and does work in an interactive session; it is not a mechanism. 📖 Both measurements: `~/.claude/skills/handoff/reference/kickoff-prefix.md`.

   🔴 **Emit this BEFORE step 4's confirm gate, unconditionally.** The kickoff block is the deliverable; step 4 blocks on a y/N, and a user who walks away from that prompt must still have got it.

4. **Record what this session touched in the subsystem index** — a read-only probe, then an opt-in write. The index is the terse *pointer sheet* that outlives this handoff doc, and this step is its second writer.

   ✅ **Run the probe FIRST, unconditionally — do not research anything before running it.** It **never writes**; it resolves the changed paths against the store and reports. Its first two output lines state the `scope=` it derived and the `store:` path it read, **so the two facts you would otherwise go looking up are printed by the command you are deciding whether to run.** Nothing below needs to be settled beforehand. The write half is at the END of this step, behind an explicit y/N.

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --session <session-uuid> --exclude claudedocs/handoff-<topic>.md
   ```

   🔴 **`<session-uuid>` is the basename of your scratchpad directory.** Your system prompt names a scratchpad path of the form `/tmp/claude-<n>/<project>/<session-uuid>/scratchpad` — pass that `<session-uuid>` segment, nothing else. There is no environment variable for it, so it can only come from you.

   **Pass it whenever you can.** It reads what *this session* actually edited from its own transcript, **independent of git**. Without `--session` the tool falls back to git's **branch** window — still honest, still tested, but blind to work that has already merged.

   🔴 **Never pass a UUID you are unsure of.** It is validated — the transcript must exist, have been written in the last 30 minutes, and either record a `cwd` equal to `--repo` or name absolute paths under it — and a wrong one **fails with a named error rather than silently reporting another session's paths**. Never retry with a different UUID to make it pass.

   **A session that ran in ANOTHER repo is no longer refused outright.** If its turns named absolute paths that resolve under `--repo`, you get the `session-absolute` window over exactly those; the run is normal and the `caveat:` line says which window you got. Read it — that window is a **floor**, not a list: every path the session named *relatively* belongs to its own cwd and is excluded, so how much is missing is unknown rather than counted.

   🔴 **Which fallback is right depends on WHICH validation failed — and the wrong choice is a second dead source.** A missing, stale, unreadable or simply wrong UUID ⇒ drop `--session` and use the git window instead. But **`transcript cwd does not match` ⇒ do NOT fall back to the git window**: that session ran in a *different* repo *and named nothing absolute under this one*, so this repo's branch window is empty too, and you would be reading a second source that structurally cannot answer. Go to `--pr`/`--commit` over what you landed here. Never work around the cwd guard — a **relative** path in a transcript is relative to its own session's cwd, so re-anchoring one here would file another repo's work under this one.

   `--exclude` drops the doc **you just wrote in step 2** — it is in *both* windows (untracked in git's, a `Write` tool call in the session's), and without it `claudedocs` is a nomination on every single run. 🔴 **Scope follows `--repo`, NOT where the work happened** — those coincide only when you worked in the repo you are sitting in. See the cross-repo rule below before you conclude anything from a single run.

   **Read the `caveat:` line before you write anything** — it states what the chosen window structurally cannot see, and the sources are blind in *opposite* directions. The session window in particular does **not** include what a **subagent** edited, or files written by a `Bash` command; if the session's real work happened in a dispatched subagent, expect a thin path set and say so rather than inventing entries.

   🔴 **If you landed any PRs this session, run it a SECOND time over them** — you know exactly which ones, and nothing else in the toolchain does:

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --pr <n>[,<n>...]
   ```

   This is the **only** source that sees a **subagent's** work — a PR's file list does not care which agent, session or tool wrote the bytes, and delegating implementation to a subagent is the standing default here.

   🔴 **A PR you landed in ANOTHER repo needs its OWN run with `--repo <that repo>`.** Scope follows `--repo`, so a window run from the repo you are sitting in **cannot see it** and the store never learns the work happened. This is not the rare case: MEASURED over the 40 most recent `datapacket-talos` sessions, 13 ran a window and **7 of them left at least one PR's repo unscoped** — PRs in `civitai`, `civitai-spine-controller`, `flipt-state` and `devrc` all filed against the wrong scope or no scope at all. **List the repos you opened PRs in, then run the window once per repo.** A hub session that dispatches work outward is the normal shape here, not an exception.

   🔴 **A lesson about YOUR OWN TOOLING, learned while sitting in a client repo, does not belong in that client's `claudedocs/`.** Route it to the `devrc` scope, or to the owning skill. Measured 2026-08-17: three generic gotchas with zero client content — a `git diff --quiet <ref> -- <path>` that exits 0 when the path exists on NEITHER side, `stat` reporting a home-manager symlink's *size* rather than its content, and devrc's own pre-existing-failure test baseline — were recorded **only** in a client-confidential handoff doc that no devrc session will ever read. Ask which repo the lesson is *about*, not which one you were standing in.

   🔴 **A PR's file list is what the BRANCH LANDED, not what this session touched — never describe it as this session's work.** It is the union of every commit on the branch, so it includes another session's commits, hand-made ones, and older work on a long-lived branch; and it omits anything you did that never reached a PR. When you write a journal bullet from a `--pr` run, attribute it to the branch or the PR, not to "this session".

   🔴 **Run it twice; never merge the two path sets.** The flags are mutually exclusive and the tool refuses the combination on purpose: a single merged set would carry one caveat that is wrong about half its members, and you would lose the one fact that decides whether a bullet is honest. Two runs, two caveats, each read on its own.

   Closed-unmerged PRs are refused by name — their files exist in no tree. `OPEN` and `MERGED` are both accepted, so a PR still in review counts. The `gh` call can fail in ways nothing local can (`gh` missing, not authenticated, network down, rate-limited, a truncated file list); every one exits non-zero with its own named line, and **none of them ever returns an empty path set**, so an empty result from this source always means the PRs genuinely listed no files.

   🔴 **The discriminator is whether the work LANDED AS A PR — never whether a worktree was involved.** A throwaway worktree goes both ways and the clause below is one *example* of "no PR", not an independent trigger: in the repo holding most of this store, 144 of its last 200 mainline commits carry no `(#N)`, so `--commit` is right there; elsewhere the same worktree rule lands PRs and `--pr` is the only source that sees them. MEASURED 2026-08-14: a session read "worktree" as "use `--commit`", ran it over five of its own shas, got `no-match` on one `claudedocs/` path — the base-clone shas were just the handoff doc — and reported "a real zero, index unchanged, correctly". Real for the window read; the window was wrong. **Ask which way yours landed.** 📖 `~/.claude/skills/handoff/reference/index-write.md` §3.

   🔴 **If the work became a commit but NO PR — a direct push, or a branch not opened yet — run it over the SHAS YOU CREATED:**

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --commit <sha>[,<sha>...]
   ```

   A commit is the primitive the other two reduce to — a PR is a set of commits, worktree-authored work becomes a mainline commit, a direct push *is* a commit — so this reaches repos where neither of the others can. **You know the shas you just made; nothing else in the toolchain does.**

   🔴 **This window is what those COMMITS changed** — neither a session nor a branch. It excludes uncommitted work and anything you did that never became one of these commits (a *sibling* commit on the same branch is not in it), and it over-reports when a commit carried more than the work you are recording (a formatting sweep, a stray `git add`). Attribute a bullet to the commits, never to "this session". Hex shas only: a revision expression such as `HEAD`, an ambiguous short sha, a non-commit object and a merge commit are each **refused by name; pass a merge's side commits, or use `--pr`**. Same rule as the two runs above — **run it separately, never merge the path sets**.

   🔴 **Any non-zero exit ⇒ print the stderr line verbatim and write NOTHING.** Exit 3 covers a missing store, a malformed entry, an unusable path, a git failure and every `gh`/pull-request/commit failure alike; none of them is a reading. Do **not** fall back to recollection — an entry written from memory is exactly the unverifiable content this store must not accumulate.

   **…but "write nothing" is not "do nothing": if the message prints a `RECOVER —` block, run the command it gives you.** A malformed entry refuses the probe by design (a writer must not act on a partially-read store), and the refusal now names every unreadable file, says whether it is in *this* repo's scope, and emits a runnable `--validate` per affected scope. 🔴 **Run the command it printed, not one you compose** — the blocking file is often in a *different* scope, and `--validate` on your own scope would report it clean while the probe keeps failing. Fix the files it names, re-run the probe, and only then decide about writing. A store left broken because a refusal had no route out is how it stays broken until a human happens to look.

   Otherwise read `status=` and act on that case:

   - **`resolved`** — for each entry listed, propose appending **one dated bullet** in the existing style (`- YYYY-MM-DD: …`, ≤2 lines) as the **FIRST** bullet under `## Nuance / work-history`. Write a *durable lesson* — a gotcha, a decision, why it was touched — never routine status, never a config value, never a copy of what the handoff doc already says.

     🔴 **First read the `already there` lines the tool prints under that entry, and check your proposed bullet against them — this is a comparison, not a feeling.** Restating a lesson the entry already carries is the failure this display exists to prevent: it is likeliest right after work you feel good about, and a repeat `/handoff` the same day is the common case (the tool prints `ALREADY dated <today>` with a count when one has run). **A bullet that adds nothing to what is on screen ⇒ propose nothing and say `index unchanged`** — declining is a normal, frequent outcome. If the entry has no `## Nuance / work-history` section at all, the tool says so: the heading has to be created as part of the append, because the `Edit` below anchors on it.

     🔴 **If your bullet proposes work that has NOT been done, it must start `OPEN:`; if you are closing one that has, rewrite that bullet as `RESOLVED <sha>:` in the SAME edit.** The marker goes after the date (`- 2026-08-15: OPEN: …`). MEASURED, and this is the failure it exists for: an entry in the `datapacket-talos` scope was written at **15:00:18** on 2026-07-24 proposing a one-line remedy as future work — and that remedy landed at **15:02:21**, two minutes later, in the same effort. The entry then served the remedy as outstanding for **22 days**. Nobody was careless; this step runs mid-session, so the writer is gone by the time the work finishes and the store had no way to say "still open". 🔴 **A proposed remedy with no marker is the default failure, not an edge case** — it reads exactly like a current one forever.

     🔴 **Before appending, act on the `OPEN:` block the tool prints for that entry.** It lists, as SEPARATE populations that never overlap: every declared-open bullet with its age (`oldest unverified for N days`); every bullet that **tried to write a marker and missed the grammar**, which declares nothing and shows no badge — fix the line; and every bullet that merely *looks* like an open action, which is **AT LEAST this many**, a two-phrasing floor with unknown recall, never a count. Re-check each against the repo *now*: you are the next writer, and the next write is the only moment anyone is looking. Closing one is a one-line edit and is worth more than the bullet you came to add.

   🔴 **Decline on CONTENT, never on cost — an index entry does not cost a session anything.** MEASURED 2026-08-13: entries load on demand, read only by `/resume` step 4, never at session start. What loads every session is the skill *listing* (name + description), not entries and not skill bodies. On the largest real scope the whole digest is ~914 tokens because it prints **one** body and indexes the rest, so an additional entry adds **one ~56-byte index row, ~14 tokens**, to a `/resume` that chooses to read it. A session declined a write reasoning that a bullet "would restate them at per-session cost"; that premise is false, and it matters because suppressing writes on a cost that does not exist is how the store stays empty and the emptiness then gets read as nobody wanting it — the exact circular argument `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` had to retract. **"Already in the handoff doc" is also not a reason**: this store exists to OUTLIVE handoff docs, which are per-topic and get overwritten. A durable `.claude/skills/<name>/SKILL.md` *is* a legitimate alternative home; a handoff is not.

   🔴 **A subsystem with NO FILE FOOTPRINT is invisible to every window — that is not the same as "nothing to record".** All four windows read file paths, `nominate()` clusters paths and needs two, and the `NO ENTRY` prompt only appears when something was nominated. So a session whose work was a production database, a cluster, a DNS record or an external service resolves nothing, nominates nothing, and — before this — was offered nothing. MEASURED 2026-08-14: a session did every step right and closed with *"the work happened in the production database, which no path window can see."* On a dead end with no nominations the tool now prints a **`NO PATH FOOTPRINT?`** block with the exact `--template <slug> --scope <scope>` command, which needs no paths at all. 🔴 State the trade when you use it — and state it accurately: nothing matches such an entry automatically **today**, but it is not permanently unresolvable. It **gains normal path resolution** the moment some path is named for the slug (verified: an entry written with zero paths resolved as soon as a matching path appeared), so **prefer a slug a future path would carry**. Until then it is listed in the scope index and found by `--search`, which is enough to read at `/resume`. 🔴 The `NO ENTRY` block carries the same offer, because a nomination is path-derived and names the directory you touched rather than the database you worked on — estimated ~65% of dead ends nominate something (a proxy over 287 commits, not the real windows), so that is where this case usually hides. The bias this closes is the one that matters: the subsystems living OUTSIDE the repo are exactly the ones whose knowledge is tribal. Still **at most one, or none**.

   🔴 **"It belongs in a skill" is a ROUTE, not a disposal — finish it in THIS turn or it is lost.** The sentence above authorises declining the index; on its own it also discharges the obligation, and the lesson then lives only in a transcript, which is the medium this store exists to outlive. MEASURED 2026-08-14: a session declined both windows correctly, concluded correctly that its gotchas belonged in a skill, named `.claude/skills/pyroscope/SKILL.md`, and stopped — **that skill does not exist**, `obs-read` already owns Pyroscope in its description, so acting on the note later would have created a duplicate and split the domain. The gotcha it carried (Pyroscope's `max_query_length: 1d`) was recorded nowhere. So: **take the owning skill from the tool's `SKILL HOMES` block, never from memory** — it ranks by term specificity, and a hit is a lead, not an answer. If nothing matched, run the `grep -ril` it prints with *your* domain term, because the tool derives terms from paths and **cannot see what the session was about**. Then append there under the same gate as the index write — one compact diff, a single y/N, discard on decline. 🔴 Edit the **repo source** (`~/workspace/devrc/claude/skills/…`), never `~/.claude/…` (a read-only store symlink), and a **new** file must be `git add`ed or the flake silently omits it from the deploy. If genuinely nothing owns it, say **UNFILED** and name the term you searched — an unfiled item that names its search is recoverable; "belongs in a skill" is not.
   - **`ambiguous` listed** — report the candidates and **write nothing** for that ref. The resolver refuses to pick; so do you.
   - **`no-match` / `scope-absent`** — no existing entry was touched. `scope-absent` means this repo has no scope directory yet: the **first-entry case, not a failure**, and the reason this step exists. Nothing to append either way; go to the NO ENTRY clause below.
   - **`looked-at-nothing`** — say so plainly and write nothing. This is *not* "nothing touched an entry": no path was examined at all. Never report the two as the same result.

   🔴 **On either dead end the tool now prints a `ROUTE OUT` block naming the windows it did NOT read** — read it before concluding "a real zero". A zero is a fact about the window, and the four windows are blind in different directions; the block excludes the source that just failed, so every line in it is a move you have not made. It appears on `looked-at-nothing` and `no-match` only, never on a resolved run.

   🔴 **ESCALATE BEFORE CONCLUDING — a thin or empty session window is not a result, it is a prompt to read a second one.** If `--session` returns `looked-at-nothing`, `no-match`, or `BELOW THRESHOLD` with nothing proposed, **run `--pr` (or `--commit`) NOW, in this same step, before you report anything.** The stop condition is **"a second window was read and also came back empty"** — never "the first window was empty". MEASURED 2026-08-16: the session that designed, built, tested and deployed the `subsystem-store-api` service across **nine merged PRs** got **1 path under the cwd and 5 outside** from `--session` ⇒ `no-match` ⇒ "Propose no write" — while `--pr` over those same nine PRs saw **17 paths** and nominated `subsystem-store-api` at 7, well above threshold. **~94% of the work was invisible to the preferred window**, and the store went without an entry for the largest thing that session produced.

   🔴 **This does NOT violate "the windows never compose."** That rule forbids **MERGING the two path sets** into one report carrying one caveat that would be wrong about half its members. It says nothing about **reading** a second window: two runs, two reports, each read on its own with its own caveat, is the composition that *is* available — it is what "run it twice" means, and the tool refuses only the merged invocation. Reading one window and stopping is not compliance with that rule; it is a different failure.

   🔴 **Expect the session window to be thin here, by construction.** The standing default is to DELEGATE non-trivial work to a subagent, and any file-modifying subagent gets its own WORKTREE — a subagent's turns are a *separate transcript* and a worktree is *outside the session cwd*, so the two defaults hit two of this window's three blind spots at once. **The better the session followed the rules, the less of it `--session` can see.** When most of the paths it named are outside the cwd the tool now prints a computed **`WRONG WINDOW?`** block with that run's own numbers and the exact flags to run instead — it fires on the measured condition, not on every run, so **when it appears, act on it**; treat it as the escalation being handed to you rather than as a caveat to note.

   **Separately, and independently of `status=`: if a `NO ENTRY` block is printed, consider a new entry.** It appears alongside `resolved` too — a session normally touches a known subsystem *and* an unrecorded one, and treating nominations as a `no-match`-only concern is what would stop entries ever accruing in a repo that already has one. Pick **at most one** nomination that is a real durable subsystem, or none — they are candidates, not answers, and rejecting all of them is a normal outcome. `--template <slug>` prints the entry to write — identity front matter (`scope`, `sensitivity: client-confidential`, `created_by: handoff`) plus `## What it is` and `## Pointers`. **Do not demand the full schema** — a thin entry that exists beats a rich one that doesn't. Uncomment the template's `aliases:` line if the subsystem is spelled more than one way, in particular the `test_<slug>` stem: matching is exact, so without it a module and its own test count as one path and stay under the threshold forever.

   **— the write half; everything above this line only reads —**

   🔴 **The target is `~/.claude/analyze-service-index/<scope>/<slug>.md`** — an absolute path outside every repo. Never anywhere in the working tree: these entries carry client-identifying infrastructure detail and `devrc` is PUBLIC. **Read the policy file the probe named on its `policy:` line before writing there, and do not go looking for one it did not name.** A scope may have no README of its own — that was true of 4 of 5 scopes when this was built, and all 5 have one as of 2026-08-13; a *new* scope starts without one, so the gap recurs by construction — and the probe therefore resolves it deterministically and prints which of the three cases you are in — the scope's own README (`scope README`, authoritative for that scope), the store-root README (`this scope has none of its own`, so it is the store's general policy and not a statement by this scope), or neither. Do **not** create a scope README to fill the gap: each one is a human policy statement, and writing it yourself would be manufacturing authority.

   🔴 **After writing an entry — new file or appended bullet — validate it in the SAME turn:**

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --validate <path-you-just-wrote>
   ```

   🔴 **READ THE `entry shape:` BLOCK, NOT ONLY THE EXIT CODE.** It is advisory and deliberately does **not** move the verdict — an entry whose spine is broken still parses, still loads, and still exits 0 — so branching on the exit code alone is how it goes unread. It reports the two headings any reader actually parses (`## Pointers`, `## Nuance / work-history`) as ABSENT, RENAMED, DUPLICATED or present-and-EMPTY, and names what you wrote instead. A RENAMED nuance heading is **silent data loss**: the entry's index row then shows `0 nuance` with no `🔴 N OPEN` badge while the bullets sit intact on disk, and `/resume` consumes exactly that row. Fix the heading in the same turn — it is one edit, and nothing else will tell you.

   It exits 0 with `OK — N of N entry file(s) parse`, or **3** with a `malformed index entry` row per bad file. This exists because the store's front matter is parsed **line by line**: an `aliases: [...]` list wrapped across two physical lines reads as an unterminated bare string and the entry is rejected — and the confirm-gate diff you just approved *contained* that defect while being structurally incapable of revealing it. Without this check the failure surfaces hours later, in a different session, from a different tool. `--validate` with **no path** checks every entry in the scope instead (and only that form can catch a duplicate ref, which is a relationship between two files). It reuses the reader's own parser and validator, so a pass here is the reader's verdict, not a second opinion.

   📖 **The measured evidence behind this whole step is in `~/.claude/skills/handoff/reference/index-write.md`** (deployed `~/.claude/skills/handoff/reference/`, source `~/workspace/devrc/claude/skills/handoff/reference/`) — the two-session concurrency simulation and the retraction it forced, the autocommit timer's behaviour, each path source's blind spots with their numbers, and the `gh` file-list truncation cap. Read it when a rule looks arbitrary or you are about to work around one. It is rationale only: every rule you must follow is here, and none of it needs reading before the probe.

   🔴 **Write it — no question. SHOW the diff, then Edit.** Operator decision 2026-08-15: the y/N here was always answered `y`, so it bought a round trip and no safety. What it never was is the content filter — that is the `already there` comparison above, which still decides whether a bullet is written at all. **Declining on content stays a normal, frequent outcome; declining by prompt is gone.**

   🔴 **The asymmetry is deliberate, so do not generalise this to step 5.** This write is ONE local markdown file under `~/.claude/analyze-service-index/`, on this machine, that **nothing pushes** — its own out-of-band autocommit versions it, so a bad bullet is recoverable and costs ~14 tokens on a `/resume` that chooses to read it. Step 5 writes a **tracked file and pushes it to a shared branch**, where a bad write is visible to everyone and is the incident `~/.claude/skills/handoff/reference/write-gate.md` records. **Step 5 keeps its y/N.** Blast radius is what earns a gate, not symmetry.

   **Still print the unified diff before writing** — the transcript is the only record of what landed, and a reader scanning it later needs to see the bullet without opening the store. Then: **re-read the file and re-apply to current bytes** so a concurrent append isn't clobbered, and **use `Edit` anchored on `## Nuance / work-history`, not `Write`** — a whole-file retype of a curated, unbacked-up entry risks losing content the diff never showed. (`Write` only for a first-ever file, which has no prior content to lose.) 🔴 **The re-read is the actual safeguard, not the anchor: do it every time, and do not treat "no error" as evidence you were alone** — a concurrent append on this anchor is measured to land silently.

   Carry the store's invariants: **pointers, not copies**; **never persist live status** (no counts, no Ready/NotReady, no current version) — for anything with no live probe, persist the *derivation method and what a stale reading looks like*, never the reading. Never silent-mutate. 🔴 **Write the file and run no git command** — the store is versioned by its own out-of-band autocommit. Never add a remote, never copy a line of it into a public repo. **This holds for a brand-new scope directory too: the hourly timer creates and commits its repository — do not create the repository yourself.** So a just-written entry in a new scope is **unversioned for up to an hour**; that is the normal window, not a failure, and not something to fix by hand.

5. **Land the handoff doc — the write+push gate.** MEASURED: a session re-entered from a handoff, did ten minutes of real analysis, then wrote and **pushed** an updated handoff to a shared branch that nobody approved. `/resume` is read-only and followed its contract; step 4's *index* write is gated; the doc's own write+push was not.

   🔴 **Do NOT forbid updating the handoff** — that one was correct and valuable (it answered the doc's open question *and* corrected a prior misreading), and suppressing it costs the next session the same ten minutes. Make the update **safe**, not rare.

   **Answer first, in one line: what changed since the doc was written?** If the honest answer is *nothing*, **say so and write nothing** — a handoff that still describes reality is not stale. Otherwise merge it; this **writes nothing** and prints the diff you are about to ask about:

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/handoff_doc.py --repo <repo> --topic <topic> --update <scratch-file> --advanced '<what changed since the doc was written>'
   ```

   🔴 **Status header REPLACED, findings APPENDED — which is why the tool merges rather than you rewriting the file.** `State now`/`Next steps`/`How to verify` are current state and are overwritten; `Open investigations`/`Findings`/`Gotchas` append and the earlier text survives **verbatim**, even when your block supersedes an old one — the value is seeing a prior reading was *corrected*, not finding it gone. A section your delta omits is left untouched.

   `status=proposed` ⇒ the diff is on screen and nothing has been written: not the doc, not a commit, not a ref. `no-advance` (4) and `no-change` (5) print **no diff at all** — a session that went nowhere gets no write offer, not an empty one — so report the line and stop.

   🔴 **Two more statuses exist and both mean NOTHING WAS WRITTEN OR IS SAFE — read them, do not retry blindly.**
   - **`status=behind` (exit 6)** — `--push` was asked for and the remote has commits this checkout does not, so the push would be rejected and the commit would be **stranded on a shared branch**: the state that silently blocks `ship.sh`. Nothing was written. It prints the exact `merge --ff-only` to run, and the preserve→verify→`reset --keep` path if that refuses. Fast-forward, then re-run the identical command.
   - **`status=push-failed`** — the pre-check passed and the push still failed (the remote can move in between; that race cannot be designed away). 🔴 **Unlike every other failure here, the COMMIT EXISTS.** The message names it and hands over preserve→verify→`reset --keep` **in that order**. Do not leave it: an un-pushed commit on a shared branch is invisible until `ship.sh` skips that host.

   🔴 **Gate the PUSH — one compact diff, a single y/N, and on decline, discard.** 🔴 **Step 4's index write is NOT gated and this one is**: that write is a local, unpushed, autocommitted file; this one lands on a shared branch. Do not "harmonise" them. Ask exactly *"update the handoff doc and push it? (y/N)"*. On **n** run nothing else; the tree is already byte-identical. On **y** re-run the identical command with **`--confirm`** (plus `--push`): exactly one commit, path-limited to the doc, carrying exactly the diff shown. Whether to push at all where trunk is the deploy branch is a per-repo policy question. 📖 The incident and the shape of each half: `~/.claude/skills/handoff/reference/write-gate.md`.

Keep the doc tight and high-signal — it is read first thing next session, so every line must earn its place. The "Open investigations" blocks are the exception to brevity: a mid-diagnosis bug is worth verbatim evidence, because re-deriving it next session costs far more than the lines do. Pair: `/resume`.
