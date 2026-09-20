---
name: handoff
description: "Write a canonical session-handoff doc and a copy-paste kickoff message so work resumes cleanly in a new session. Use at end of session, before a context reset, or when told to write the handoff."
argument-hint: "[topic-slug] — optional; defaults to the current work's topic"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# /handoff — canonical session handoff

Goal: capture everything needed to continue this work in a fresh session with **zero re-discovery**, then hand back a kickoff block to paste.

Topic argument (optional): `$ARGUMENTS`. If empty, infer a short kebab-case topic from the current work.

**Reference topics** — deployed at `~/.claude/skills/handoff/reference/`, source `~/workspace/devrc/claude/skills/handoff/reference/`:

| Load when | File |
|---|---|
| Landing the doc: every refusal status, the stale-base/durable-drop warnings, the append-vs-replace buckets | `~/.claude/skills/handoff/reference/write-gate.md` |
| Resolving or recording the `clawgate-task:` field, and its exit codes | `~/.claude/skills/handoff/reference/clawgate-task.md` |
| Retiring a heading your delta supersedes — appending is only HALF the job | `~/.claude/skills/handoff/reference/supersede.md` |
| The ranked list is a SHARED QUEUE and `claim-work` is its lock | `~/.claude/skills/handoff/reference/shared-queue.md` |
| Capturing an exit status a pipe would eat | `~/.claude/skills/handoff/reference/exit-code.md` |
| Why the `/resume` kickoff prefix is not a mechanism (both measurements) | `~/.claude/skills/handoff/reference/kickoff-prefix.md` |

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

      Act on the exit code — and on NOTHING else. **0** one WORKED task ⇒ record it in step 2's front matter. **6** ⇒ **ASK the user which one**; do not guess, and note the **no worked task at all** case (only `created`/`read` links) most likely belongs to NONE of them. **5** nothing resolved, or **3/4** the board did not answer ⇒ 🔴 **write NO field and say so plainly in your report** — an unknown session id answers 200 with an EMPTY ARRAY, so a zero cannot distinguish "touched no task" from "wrong id". It is not a clean bill of health. 📖 role ranking, the pipe-eats-the-status trap, and the CANDIDATE caveat: `~/.claude/skills/handoff/reference/clawgate-task.md`.

      ⚠ **It reads `OPENCODE_SESSION_ID`, else `CLAUDE_CODE_SESSION_ID`**; inside opencode (`$OPENCODE` set) with no opencode id it REFUSES rather than borrow an ancestor's. With no `role` on any row it prints `ROLES UNAVAILABLE` and falls back to "exactly one task" — read the rows yourself.

     🔴 **NEVER create a task here.** `/handoff` records what already exists; a task minted to fill a blank field is a fact nobody asserted, and it will be reconciled against for the life of the doc. Authoring a task is its own interviewed flow (`claude/skills/clawgate/flows/task-authoring.md`, enforced by a PreToolUse hook).

   - **For every UNRESOLVED bug/investigation, capture the live diagnosis state** (the next section). This is the highest-value part of the handoff: without it the next session re-runs every probe you already ran. Record observed *values* and *eliminations*, not narrative — the actual error string, the actual header/response, the exact failing request, the command whose output you read. "We looked into the CSP issue" is worthless; the header value you actually read, and the request you read it on, is the whole point. 📖 the worked pair: write-gate.

2. **Draft the handoff doc into a SCRATCH FILE.** 🔴 **`claudedocs/handoff-<topic>.md` is written by step 5 and by nothing else — whether or not it already exists.** Draft `## ` headings into a scratch file **under your scratchpad directory, never inside the repo** — an in-repo one lands in step 4's session AND git windows, and `--exclude` names the handoff doc, not it — then land it in **step 5**, which owns the merge, the gate and the commit. When the doc EXISTS your scratch file is a *delta*: omit a section and it is left alone. When it does NOT, the delta becomes the doc verbatim, so write the whole structure below into it. Be concrete — exact file paths and commands, no vague prose:

   🔴 **Never `Write` the doc yourself, and the NEW-doc case is the one this is about.** MEASURED: step 5 is the only step that commits, and against a doc you already wrote in full it returns `status=no-change` (exit 5) — *report the line and stop*. The doc then ends the session **untracked**, which `claude/RULES.md` names as unsaved work one routine `checkout` from silent deletion. `handoff_doc.py` handles the no-base case itself with the same diff, warnings and commit+push; writing the file first is what takes them away.

   🔴 **The `clawgate-task:` field from step 1 goes in YAML front matter at the VERY TOP — `---` on LINE 1, nothing above it, and the closing `---` is load-bearing.** `/resume` only parses a block whose `---` is line 1, because a `---` further down is a horizontal rule and letting one open front matter would let body prose mint a task id. On a NEW doc that means the top of your SCRATCH file. Omit the block entirely when step 1 resolved nothing. 🔴 **On an UPDATE, check before you add:** `bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh field <doc>` exits **0** (readable field already there — leave it), **1** (none; add it), **2** (present but unreadable — a non-id value or an unclosed block; stderr says which). Repair *that* block, never add a second. 📖 the DROPS-the-task remedy (which INVERTS rule (f)): `~/.claude/skills/handoff/reference/clawgate-task.md`.

   ````markdown
   ---
   clawgate-task: 193
   ---
   # Handoff: <topic> — <YYYY-MM-DD>

   ## Run this first — the index, one command
   ```bash
   cairn recall --repo <path>
   ```
   Terse pointers this doc does not carry, curated by past sessions and outliving it.
   🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
   reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
   nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
   Non-blocking: if it exits non-zero, print the stderr line and carry on.

   ## Goal
   What we're trying to achieve and why (1–3 lines).
   - **closing-condition:** `check` — a command/PR/alert a later session can RUN · or
     `judgement` — a NAMED person reading NAMED evidence. 🔴 **FROZEN AT ROUND 1**:
     later audits and asks do NOT extend it, they open a NEW arc. A close-check answers
     THIS line with a VERDICT — ADDRESSED ⇒ arc CLOSED · NOT ⇒ name the one item —
     never an inventory. Step 5 refuses a new doc without it, or an update dropping it.

   ## State now
   - Branch / PR: ...
   - What's DONE this session (with commit hashes / file paths)
   - What's IN FLIGHT (started, not finished)
   - Deploy/verify status: deployed? verified against the real path? (be honest)

   ## Open investigations — live diagnosis state
   <!-- One block PER unresolved bug/investigation. Omit the section only if nothing is mid-diagnosis. Step 5 STAMPS each new block `as-of: <today>` and never rewrites one you wrote, so date it YOURSELF when the evidence is older. 📖 write-gate §E. -->
   ### <bug/symptom in one line>
   - **Symptom + exact repro:** what breaks, and the precise click-path / request / command that triggers it.
   - **Observed (with values):** the actual evidence gathered — error strings, response headers, log lines, query outputs, span timings. Real values, copy-pasted, not paraphrased.
   - **Ruled out:** a hypothesis eliminated, the evidence that killed it, and `via: <kind>` — `command`/`measurement`/`code`/`change`/`doc`, or `assumed` when you reasoned rather than measured. 🔴 Step 5 REFUSES an untagged one (`status=unevidenced`); INDENT the field if it wraps.
   - **Leading hypothesis:** current best theory, and why.
   - **Next probe:** the single most useful command/observation to run next, written so it can be executed verbatim.

   ## Next steps (ranked)
   1. ... forcing: incident — <the external signal, with its evidence>
   2. ... forcing: none
   🔴 **EVERY item MUST carry `forcing: <kind>`** or step 5 refuses (`status=unforced`). CLOSED vocabulary: `incident`, `user`, `gate`, `deadline`, `regression`, `security`, `none` — an unrecognised kind is refused, so there is no `followup`/`tech-debt` to hide under. **EXTERNAL — NOT the previous session's list; the refusal spells it out.** `forcing: none` is the honest opt-out: **accepted and counted, and not eligible to be worked.** ⚠ The tool cannot check a cited forcing function is real or external — it makes the claim mandatory and greppable, nothing more.
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
   🔴 **AND IT IS RATCHETED: an audit finding is a DEFECT, not a rank.** Step 5 refuses
   an update whose `forcing: none` count EXCEEDS the doc's. Batch findings under
   `## Defects (batched)` and fix them in ONE round; closing one buys room for one.
   📖 write-gate §G.

   ## Defects (batched)
   - Audit/review findings, one line each — fixed as a BATCH, never one rank per finding.

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

   🔴 **Keep the literal `/resume` prefix, and do NOT rely on it** — measured twice, it changed nothing; the deterministic hook is the DOC, which is why the index command sits at its top. 📖 `~/.claude/skills/handoff/reference/kickoff-prefix.md`.

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
   python3 $DEVRC/scripts/lib/handoff_doc.py --repo <repo> --topic <topic> --update <scratch-file> --advanced '<what changed since the doc was written>'
   ```

   🔴 **Status header REPLACED, findings APPENDED — which is why the tool merges rather than you rewriting the file.** `State now`/`Next steps`/`How to verify` are current state and are overwritten; `Open investigations`/`Findings`/`Gotchas` append and the earlier text survives **verbatim** even when your block supersedes it — the value is seeing a prior reading was *corrected*, not finding it gone. 🔴 **Appending is HALF the job — retire the superseded heading in the SAME delta, and delete any now-wrong INSTRUCTION in it.** 📖 supersede. A section your delta omits is untouched. The append allowlist is **three prefixes wide**, everything else replaces, so the run prints a **`buckets:`** line naming where each section you touched landed — read it; the next paragraph is a consequence of it. (A NEW doc replaces nothing and gets no such line; that absence is not a fault.)

   `status=proposed` ⇒ the diff is on screen and NOTHING has been written. `no-advance` (4) / `no-change` (5) print no diff at all — report the line and stop. 🔴 **Six refusals write nothing and each prints its own fix** (`undefined-done` 11 · `rank-growth` 12 · `status=dated-topic` (7) · `status=new-doc` (7) · `status=unforced` (8) · `unevidenced` 10); re-running after fixing your scratch file is safe. 🔴 **`status=push-failed`, and one arm of `failed`, mean the COMMIT EXISTS** — read the message rather than assuming exit 3 wrote nothing, and keep the scratch file until you have seen a sha. 🔴 **`status=behind` (exit 6) wrote nothing**: ff-merge, then re-run. 🔴 **`status=stale-base` (exit 9)** — no usable doc HERE while the mainline has one; NOTHING WRITTEN, and the proposal run never prints that line, so its absence is not a clean bill. 📖 every status and the bucket rules: `~/.claude/skills/handoff/reference/write-gate.md`.

   🔴 **Read each row's marker — only one means "add a field"**: `[no forcing: field]` add one, INDENTED · `[unknown kind]` pick from the list · `[unparsed …]` re-spell it as `forcing: <kind>` · `[fenced]` **yours ⇒ unfence it; a QUOTE ⇒ tag the item, do NOT promote it** 📖 write-gate §C. ⚠ `unevidenced` reads the same, `[no via: field]` for a missing one. 📖 write-gate §D. ⚠ `forcing: none` and `via: assumed` are ACCEPTED, print an **advisory** above the diff; the write proceeds.

   🔴 **`This replace DROPS N line(s) that look DURABLE` — a WARNING, never a refusal**, and **a FLOOR: a silent run is NOT evidence that nothing durable was dropped.** Durable content under a REPLACE heading (usually `State now`) is deleted on the next update; move it under an APPEND heading or carry it forward, and read the diff anyway.

   📊 **MEASURE before cutting:** `python3 $DEVRC/scripts/handoff-audit.py <doc>` — byte weights per section; never edits. 70% of a doc is APPEND buckets that cannot shrink.

   🔴 **Land it — no question. SHOW the diff, then push.** Operator decision 2026-08-23: always answered `y` — a round trip and no safety. Re-run the identical command with **`--confirm --push`**: one commit, path-limited, carrying exactly the diff shown. **The two-run shape STAYS**: the proposal run is what puts the diff in the transcript, the only record of what landed.

   🔴 **`--confirm` WITHOUT `--push` leaves a real commit in this checkout only, and says so**: `status=written commit=<sha> branch=<b>` is followed by `NOT PUSHED` and the exact command. A SUCCESS (exit 0), but push it or open a PR **in this session**. 🔴 **Do NOT retry by re-running with `--push`**: the doc already carries the update, so a second run exits 5 `no-change` or **appends your findings twice**.

   🔴 **The refusals and the warnings are the whole protection now — read them before the confirm.** They were advisory when a human answered a prompt; **they are the only reader now.**

   ⚠ **This pushes wherever the checkout sits, `main` included** — operator's explicit call. `branch_is_shared()` picks remedy text only — it blocks nothing, and the tool runs git from inside Python, so no PreToolUse hook sees the inner commit either. Where a repo forbids committing to its shared branch, **check `branch --show-current` yourself first.** 📖 write-gate.

Keep the doc tight and high-signal — it is read first thing next session, so every line must earn its place. The "Open investigations" blocks are the exception to brevity: a mid-diagnosis bug is worth verbatim evidence, because re-deriving it next session costs far more than the lines do. Pair: `/resume`.
