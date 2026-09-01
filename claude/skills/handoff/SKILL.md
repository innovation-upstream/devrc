---
name: handoff
description: "Write a canonical session-handoff doc and a copy-paste kickoff message so work resumes cleanly in a new session. Use at end of session, before a context reset, or when told to write the handoff."
argument-hint: "[topic-slug] — optional; defaults to the current work's topic"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# /handoff — canonical session handoff

Goal: capture everything needed to continue this work in a fresh session with **zero re-discovery**, then hand back a kickoff block to paste.

Topic argument (optional): `$ARGUMENTS`. If empty, infer a short kebab-case topic from the current work.

🔴 **ONE DOC PER EFFORT, UPDATED IN PLACE — the slug IS the key** (operator, 2026-08-28). **Never date the topic** (a dated slug is per-session by construction — next session's date differs); **never mint a fresh slug for an effort that already has a doc.** Step 5 refuses both and names the fix. 📖 `~/.claude/skills/handoff/reference/write-gate.md` §C.

## Steps

1. **Snapshot live state** (don't trust memory — observe):
   - `git -C <repo> status -sb` and `git -C <repo> log --oneline -8`
   - Uncommitted diff summary (`git diff --stat`), current branch, any open PR (`gh pr view` if relevant)
   - Any in-flight deploy/build/job state relevant to this work
   - **Resolve which clawgate task this session belongs to** — one read-only command, no network reasoning of your own:

     ```bash
     bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh resolve
     ```

     It reads `GET /api/sessions/{id}/tasks` for the session named by **`CLAUDE_CODE_SESSION_ID`** and prints one verdict. 🔴 **That is the variable's exact name; there is no `CLAUDE_SESSION_ID`.** Reading a name that does not exist ships an INERT feature indistinguishable from a working one — an unset variable and a session that touched nothing produce the same empty result. The tool refuses rather than guessing: `NO SESSION ID` (exit 3) is its own outcome and is never folded into "no task".

     Each linked row carries a **`role`**, and the verdict RANKS by it instead of counting links: `worked` (this session commented on the task or flipped its status), `created` (it FILED the task), `read` (it only fetched it). `worked` is the signal because `claude/skills/clawgate/flows/task-pickup.md` mandates the comment/status write-back on every pickup and a Stop hook blocks the turn without it.

     🔴 **CAPTURE the status — a PIPE EATS it**: `… | tail; echo "rc=$?"` prints 0 for a real 5. Use `out=$(… resolve 2>&1); rc=$?`. 📖 `~/.claude/skills/handoff/reference/exit-code.md`.

     Act on the exit code — and on nothing else:
     - **0, one WORKED task** → record it in step 2's front matter. Still 0 however many `created`/`read` rows sit beside it. With no `role` on ANY row (an older server) it falls back to "exactly one task" and prints `ROLES UNAVAILABLE` — read the rows yourself before recording.
     - **6** → **ASK the user which one.** Do not guess, and do not record more than one. The output says which of four this is: several WORKED tasks; **no worked task at all** beside one or more `created`/`read` links; a role the tool does not recognise; or several tasks with roles unavailable.
       - 🔴 The **no-worked** case is not "pick one" — filing or reading a task is not doing its work, so the doc most likely belongs to **none** of them. Record nothing unless you recognise one. A lone `created` row is this case, not a resolution.
     - **5, nothing resolved** → 🔴 **write no field, and say so plainly in your report.** An unknown session id answers `200` with an EMPTY ARRAY rather than a 404, so an empty result cannot distinguish "this session touched no task" from "the id is wrong". It is not a clean bill of health.
     - **3 or 4, the board did not answer** → same: no field, and say the board was not reached. Never treat silence as "no task".

     🔴 **NEVER create a task here.** `/handoff` records what already exists; a task minted to fill a blank field is a fact nobody asserted, and it will be reconciled against for the life of the doc. Authoring a task is its own interviewed flow (`claude/skills/clawgate/flows/task-authoring.md`, enforced by a PreToolUse hook).

     ⚠ **Even a `worked` row is a CANDIDATE, not proof this session did the work this doc describes** — it records that the board accepted a write, nothing more. Read the title before recording it, and prefer asking over recording a task you do not recognise. One known blind spot: `created` is TERMINAL upstream and outranks `worked`, so a session that FILED a task and then worked it stays `created` and lands in the no-worked case.
   - **For every UNRESOLVED bug/investigation, capture the live diagnosis state** (the next section). This is the highest-value part of the handoff: without it the next session re-runs every probe you already ran. Record observed *values* and *eliminations*, not narrative — the actual error string, the actual header/response, the exact failing request, the command whose output you read. "We looked into the CSP issue" is worthless; "`frame-ancestors` on app.example.test = `https://example.test https://*.example.test` — does NOT include `gen-matrix.embed.example.test`, confirmed via response header on GET /apps/run/dogfood-manual" is the whole point.

2. **Draft the handoff doc into a SCRATCH FILE.** 🔴 **`claudedocs/handoff-<topic>.md` is written by step 5 and by nothing else — whether or not it already exists.** Draft `## ` headings into a scratch file **under your scratchpad directory, never inside the repo** — an in-repo one lands in step 4's session AND git windows, and `--exclude` names the handoff doc, not it — then land it in **step 5**, which owns the merge, the gate and the commit. When the doc EXISTS your scratch file is a *delta*: omit a section and it is left alone. When it does NOT, the delta becomes the doc verbatim, so write the whole structure below into it. Be concrete — exact file paths and commands, no vague prose:

   🔴 **Never `Write` the doc yourself, and the NEW-doc case is the one this is about.** MEASURED: step 5 is the only step that commits, and against a doc you already wrote in full it returns `status=no-change` (exit 5) — *report the line and stop*. The doc then ends the session **untracked**, which `claude/RULES.md` names as unsaved work one routine `checkout` from silent deletion. `handoff_doc.py` handles the no-base case itself with the same diff, warnings and commit+push; writing the file first is what takes them away.

   🔴 **The `clawgate-task:` field from step 1 goes in YAML front matter, at the VERY TOP of the file — before the `# Handoff:` line, nothing above it.** On a NEW doc that means the top of your **scratch file**, which becomes the doc verbatim. That position is load-bearing: `/resume` only parses a block whose `---` is line 1, because a `---` further down a markdown doc is a horizontal rule and letting one open a front-matter block would let body prose mint a task id. Omit the whole block when step 1 resolved nothing.

   🔴 **On an UPDATE, check before you add:** `bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh field <doc>` exits **0** and prints the id when a readable field is already there (leave it alone), **1** when there is none (add it), **2** when the field is there and unreadable — either a value that is not a task id or a front-matter block that is **never closed**; the stderr line says which, and the repair is to *that* block, never a second field. A doc with two `clawgate-task:` fields reconciles against whichever the parser reaches first, which is not a choice anybody made.

   ⚠ **`64` and `66` are about your COMMAND, not the doc**: 64 = no path or unknown verb, 66 = that path could not be read. Neither says anything about a field — fix the invocation. Any other code means the tool did not run.

   🔴 **If step 5's merge reports `This update DROPS the doc's recorded clawgate task`, restore it at LINE 1 — do NOT follow rule (f)'s usual "move it under an APPEND heading" advice for that line.** The field is read only from a closed `---` block at the top of the file; anywhere else it is invisible to every reader, so "moving" it silently disables the thread. The tool prints that remedy itself for this class; the two remedies are opposites and the block header says which one you are looking at.

   🔴 **The closing `---` is load-bearing.** Both readers require it: an unterminated block is not front matter to `handoff_doc.py` either, so it is ordinary preamble and step 5's merge will drop it the next time an update brings its own preamble. That drop is now *reported* rather than silent — but the cheap fix is to close the block.

   ````markdown
   ---
   clawgate-task: 193
   ---
   # Handoff: <topic> — <YYYY-MM-DD>

   ## Run this first — the index, one read-only command
   ```bash
   python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo <path>
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
   1. ... forcing: incident — <the external signal, with its evidence>
   2. ... forcing: none
   🔴 **EVERY item MUST carry `forcing: <kind>`** or step 5 refuses (`status=unforced`). CLOSED vocabulary: `incident`, `user`, `gate`, `deadline`, `regression`, `security`, `none` — an unrecognised kind is refused, so there is no `followup`/`tech-debt` to hide under. **EXTERNAL = an incident, a person's request, a failing gate, a deadline, a measured regression, a security exposure — NOT the previous session's ranked list.** `forcing: none` is the honest opt-out: **accepted and counted, and not eligible to be worked.** ⚠ The tool cannot check a cited forcing function is real or external — it makes the claim mandatory and greppable, nothing more.
   🔴 **The field may sit anywhere on the item, continuation lines included — but INDENT it.** The block ends at the next item, or at the first unindented line once a blank has intervened, which an intervening FENCE does not reset: trailing prose tags nothing, and a FLUSH-LEFT tag there reads ABSENT. Emphasis is OK (`**forcing:** gate`, `_forcing: gate_`); `forcing function:`/`forcing = gate` **with a listed kind** (unlisted reads ABSENT), and a fenced field regardless of kind, are **near-misses, NAMED** not absent. 📖 write-gate §C.
   🔴 **This list is a WORK QUEUE, and `claim-work` is its LOCK** — every
   `/resume` session draws from it, so a *better* ranked list produces *more*
   duplicate work, not less. **NUMBER the items and keep the numbering stable:
   the rank is half a claim's identity** (`claim-work --slug-for <this doc>
   <rank>`), and re-ranking silently re-points every live claim. Make each item
   cheap to check — name the repo and the files it will touch, and mark anything
   in flight `IN FLIGHT: <repo>#<pr>`; that marker is the SOFT half, the lock is
   the command `/resume` step 6 runs before touching an item. Worktrees do NOT
   prevent this. 📖 `~/.claude/skills/handoff/reference/shared-queue.md`.

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

   🔴 **Keep the literal `/resume` prefix, and do NOT rely on it.** Measured twice: both a prose kickoff and the `/resume`-prefixed replacement got the doc read and the index skipped entirely — a subagent gets the kickoff as prompt TEXT, with no CLI slash-command parsing, so the prefix reads as a topic label. **The deterministic hook is the DOC, not this block**: both sessions read it first, immediately, which is why the index command lives at the TOP of it. The prefix costs nothing and does work in an interactive session; it is not a mechanism. 📖 Both measurements: `~/.claude/skills/handoff/reference/kickoff-prefix.md`.

   🔴 **Emit this BEFORE steps 4 and 5, unconditionally.** The kickoff block is the deliverable and everything after it can refuse — step 4 can dead-end, step 5 can exit without writing — so a run that never reaches the end must still have handed it over. ⚠ It names a path step 5 may not land: **if step 5 is declined or refuses, say in the same breath that the doc does not carry this session's findings** — an unqualified kickoff pointing at a stale or absent doc is worse than none.

4. **Record what this session touched in the subsystem index** — follow the **`subsystem-index`** skill, whole, and come back here — `~/.claude/skills/subsystem-index/SKILL.md` if the skill does not fire, because a pointer that only names a skill stops resolving the moment that skill's listing entry is evicted, and this one is a prime candidate (its own description says *rarely run directly*). It owns the protocol: which window to read (`--session` / `--pr` / `--commit` / git branch), how to escalate when the first one comes back thin, what to append, and where the entry is allowed to be written.

   🔴 **Do not improvise a short version of it.** Every rule in there is load-bearing and most were measured after a session got it wrong — the windows are blind in opposite directions, their path sets must never be merged, and the store is client-confidential while `devrc` is PUBLIC.

   ⚠ **Its outcome is a REPORT, not a gate on this one.** Declining to write is a normal, frequent result; so is a dead end that routes the lesson to a skill instead. Say which happened, then carry on to step 5 either way — nothing about the index decides whether the handoff doc lands.

   🔴 **`--exclude claudedocs/handoff-<topic>.md`** — pass it on every run. This step runs BEFORE step 5 lands the doc, so on a first run the doc is usually not there yet, but a `--pr`/`--commit` window over work that already carried one will list it, and a repeat run finds the copy the earlier run committed. Without it `claudedocs` is a nomination on every single run.

5. **Land the handoff doc — the write+push gate.** MEASURED: a session re-entered from a handoff, did ten minutes of real analysis, then wrote and **pushed** an updated handoff to a shared branch that nobody approved. `/resume` is read-only and followed its contract; nothing gated the doc's own write+push. 📖 write-gate.

   🔴 **Do NOT forbid updating the handoff** — that one was correct and valuable (it answered the doc's open question *and* corrected a prior misreading), and suppressing it costs the next session the same ten minutes. Make the update **safe**, not rare.

   🔴 **This step CREATES the doc as well as updating it.** With no base the merge has nothing to classify, so your scratch file becomes the doc verbatim and the run prints it as one added-lines diff — same `status=proposed`, same warnings, same `--confirm`/`--push`. There is no second, ungated path for a first write, and step 2 is where the temptation to invent one lives.

   **Answer first, in one line: what changed since the doc was written?** If the honest answer is *nothing*, **say so and write nothing** — a handoff that still describes reality is not stale. On a NEW doc the question has no "since": answer with what this session produced, because a doc that does not exist cannot still be describing reality. Otherwise merge it; this **writes nothing** and prints the diff you are about to ask about:

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/handoff_doc.py --repo <repo> --topic <topic> --update <scratch-file> --advanced '<what changed since the doc was written>'
   ```

   **The doc's YAML front matter survives this merge** — `split_front_matter` carries the base's block through, so a delta that starts with prose rather than a `## ` heading can no longer silently drop the `clawgate-task:` field. Put a front-matter block in your delta ONLY when you mean to change the recorded task; an explicit one wins. 🔴 **The NEW-doc case inverts that:** there is no base block to carry, so the delta's own front matter is the doc's only chance at one — if step 1 resolved a task, it must be at line 1 of the scratch file.

   🔴 **Status header REPLACED, findings APPENDED — which is why the tool merges rather than you rewriting the file.** `State now`/`Next steps`/`How to verify` are current state and are overwritten; `Open investigations`/`Findings`/`Gotchas` append and the earlier text survives **verbatim** even when your block supersedes it — the value is seeing a prior reading was *corrected*, not finding it gone. A section your delta omits is untouched. The append allowlist is **three prefixes wide**, everything else replaces, so the run prints a **`buckets:`** line naming where each section you touched landed — read it; the next paragraph is a consequence of it. (A NEW doc replaces nothing and gets no such line; that absence is not a fault.)

   🔴 **`THE BASE DOCUMENT IS NOT THE NEWEST COMMITTED COPY` / `THIS MERGE LOOKS LIKE IT RESOLVED THE WRONG BASE`.** The base comes from `--repo`'s working tree, so a stale clone merges into an out-of-date document and reports success. It names the mainline's commit count for **this doc** (mainline **derived**, never assumed `main`), both copies' section/line counts, and which tell fired. 🔴 **A FLOOR: silence is not evidence the base is current**, and it never fetches. 📖 rule (h) in `handoff_doc.py` carries the measured incident. Two outcomes, and they differ:
   - **a usable doc here but behind ⇒ WARNING, exit 0.** The merge can still classify its sections, so a knowingly-behind clone is legitimate.
   - **no USABLE doc here (missing, empty or whitespace) while the mainline has one ⇒ on `--confirm`, `status=stale-base` (exit 9), NOTHING WRITTEN.** The proposal run warns and prints the diff; it never prints that line, so its absence is not a clean bill. Every section would arrive NEW and **replace the committed document** with your delta — usually a clone never re-synced after a WORKTREE authored it. Read the real copy via the `git show <ref>:<path>` it prints, then re-run against a current clone; `--allow-replacing-mainline-doc` overrides. 🔴 **`--push`'s `behind` check does NOT cover this** — it compares a different ref, so a current feature branch sails past it.

   🔴 **`This replace DROPS N line(s) that look DURABLE` — a WARNING, never a refusal.** Durable content under a REPLACE heading (usually `State now`) is deleted on the next update, and in a long diff a stale-status `-` line looks exactly like a measured-finding one. It classifies the deletions **above** the diff with base line numbers. Move that line under an APPEND heading, or carry it forward. 🔴 **A FLOOR: a silent run is NOT evidence that nothing durable was dropped** — read the diff anyway.

   `status=proposed` ⇒ the diff is on screen and nothing has been written: not the doc, not a commit, not a ref. `no-advance` (4) and `no-change` (5) print **no diff at all** — a session that went nowhere gets no offer, not an empty one — report the line and stop.

   🔴 **Three refusals enforce the 2026-08-28 cap. All write NOTHING, each prints its own fix, and re-running after fixing your scratch file is safe.** `status=dated-topic` (7) — dated slug ⇒ per-session doc; **no flag bypasses it**, re-run without the date. `status=new-doc` (7) — no doc for this topic, others exist (listed), and none on the mainline (else ⇒ `stale-base`); if one IS this effort re-run with ITS topic — 🔴 `--new-effort` asserts genuine newness, **not a way past the list**. `status=unforced` (8) — a ranked item names no forcing function or an unrecognised kind. 🔴 **Read each row's marker — only one means "add a field"**: `[no forcing: field]` add one, INDENTED · `[unknown kind]` pick from the list · `[unparsed …]` re-spell it as `forcing: <kind>` · `[fenced]` **yours ⇒ unfence it; a QUOTE ⇒ tag the item, do NOT promote it** 📖 write-gate §C. ⚠ `forcing: none` items print an **advisory** above the diff; the write proceeds.

   🔴 **Exit 3 usually means nothing was written — but READ THE MESSAGE, because one arm of it committed.** Usually the rollback unlinks a NEW doc, so the handoff exists only in your scratch file. **The exception announces itself**: when the commit landed and a later step failed, the run says so and tells you not to re-run — re-running appends your findings twice. 🔴 **So `status=failed` is not by itself "nothing happened", and exit 3 is not a reliable tell** — a bad `--repo` or an unreadable `--update` exits 3 with no `status=` line at all, and `push-failed` uses exit 3 too. **Keep the scratch file until you have seen a commit sha**, name its path if step 5 never lands, and delete it once the commit exists.

   🔴 **Two more statuses exist and both mean NOTHING WAS WRITTEN OR IS SAFE — read them, do not retry blindly.**
   - **`status=behind` (exit 6)** — `--push` was asked for and the remote has commits this checkout does not, so the push would be rejected and the commit would be **stranded on a shared branch**: the state that silently blocks `ship.sh`. Nothing was written. It prints the exact `merge --ff-only`, and the preserve→verify→`reset --keep` path if that refuses. Fast-forward, then re-run the identical command.
   - **`status=push-failed`** — the pre-check passed and the push still failed (the remote can move in between; that race cannot be designed away). 🔴 **The COMMIT EXISTS** — true of this and of the one `failed` arm above, and of nothing else here. The message names it and hands over preserve→verify→`reset --keep` **in that order**. Do not leave it — an un-pushed commit on a shared branch is invisible until `ship.sh` skips that host.

   🔴 **`--confirm` WITHOUT `--push` leaves a real commit in this checkout only — and it says so.** `status=written commit=<sha> branch=<b>` is followed by `NOT PUSHED` plus the exact command: a `git push` on a feature branch, or the preserve-on-a-topic-branch route on a shared one (several repos forbid committing to theirs). A **SUCCESS, not a refusal** — exit 0 — but push it or open a PR **in this session**: an un-pushed handoff is one only you can read. 🔴 **Do NOT retry by re-running with `--push`**: the doc already carries the update, so a second run exits 5 `no-change` or **appends your findings twice**.

   🔴 **Land it — no question. SHOW the diff, then push.** Operator decision 2026-08-23: always answered `y` — a round trip and no safety. Re-run the identical command with **`--confirm --push`**: one commit, path-limited, carrying exactly the diff shown. **The two-run shape STAYS**: the proposal run is what puts the diff in the transcript, the only record of what landed.

   🔴 **The refusals and the warnings are the whole protection now — read them before the confirm.** They were advisory when a human answered a prompt; **they are the only reader now.**

   ⚠ **This pushes wherever the checkout sits, `main` included** — operator's explicit call. `branch_is_shared()` picks remedy text only — it blocks nothing, and the tool runs git from inside Python, so no PreToolUse hook sees the inner commit either. Where a repo forbids committing to its shared branch, **check `branch --show-current` yourself first.** 📖 write-gate.

Keep the doc tight and high-signal — it is read first thing next session, so every line must earn its place. The "Open investigations" blocks are the exception to brevity: a mid-diagnosis bug is worth verbatim evidence, because re-deriving it next session costs far more than the lines do. Pair: `/resume`.
