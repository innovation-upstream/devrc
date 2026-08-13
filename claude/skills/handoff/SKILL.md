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

2. **Write the handoff doc** to `claudedocs/handoff-<topic>.md` in the active repo (create `claudedocs/` if absent; overwrite the file if a handoff for the same topic exists). Use this structure — be concrete, link exact file paths and commands, no vague prose:

   ```markdown
   # Handoff: <topic> — <YYYY-MM-DD>

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
   ```

3. **Output a kickoff block** (fenced, ready to copy-paste into the next session) of the form:
   ```
   Continue the <topic> work. Canonical handoff (read first):
     <repo>/claudedocs/handoff-<topic>.md
   <one-line of the single most important next action>
   ```

   🔴 **Emit this BEFORE step 4's confirm gate, unconditionally.** The kickoff block is the deliverable; step 4 blocks on a y/N, and a user who walks away from that prompt must still have got it.

4. **Record what this session touched in the subsystem index** — a read-only probe, then an opt-in write. The index is the terse *pointer sheet* that outlives this handoff doc, and this step is its second writer.

   ✅ **Run the probe FIRST, unconditionally — do not research anything before running it.** It **never writes**; it resolves the changed paths against the store and reports. Its first two output lines state the `scope=` it derived and the `store:` path it read, **so the two facts you would otherwise go looking up are printed by the command you are deciding whether to run.** Nothing below needs to be settled beforehand. The write half is at the END of this step, behind an explicit y/N.

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --session <session-uuid> --exclude claudedocs/handoff-<topic>.md
   ```

   🔴 **`<session-uuid>` is the basename of your scratchpad directory.** Your system prompt names a scratchpad path of the form `/tmp/claude-<n>/<project>/<session-uuid>/scratchpad` — pass that `<session-uuid>` segment, nothing else. There is no environment variable for it, so it can only come from you.

   **Pass it whenever you can.** It reads what *this session* actually edited from its own transcript, **independent of git**. Without `--session` the tool falls back to git's **branch** window — still honest, still tested, but blind to work that has already merged.

   🔴 **Never pass a UUID you are unsure of.** It is validated — the transcript must exist, have been written in the last 30 minutes, and record a `cwd` equal to `--repo` — and a wrong one **fails with a named error rather than silently reporting another session's paths**. Never retry with a different UUID to make it pass.

   🔴 **Which fallback is right depends on WHICH validation failed — and the wrong choice is a second dead source.** A missing, stale, unreadable or simply wrong UUID ⇒ drop `--session` and use the git window instead. But **`transcript cwd does not match` ⇒ do NOT fall back to the git window**: that session ran in a *different* repo, so this repo's branch window is empty too, and you would be reading a second source that structurally cannot answer. Go to `--pr`/`--commit` over what you landed here. Never work around the cwd guard — every path in a transcript is relative to its own session's cwd, so accepting them would file another repo's work under this one.

   `--exclude` drops the doc **you just wrote in step 2** — it is in *both* windows (untracked in git's, a `Write` tool call in the session's), and without it `claudedocs` is a nomination on every single run. Scope is derived from the repo you are in (worktree-stable), so entries accrue where the work happened.

   **Read the `caveat:` line before you write anything** — it states what the chosen window structurally cannot see, and the sources are blind in *opposite* directions. The session window in particular does **not** include what a **subagent** edited, or files written by a `Bash` command; if the session's real work happened in a dispatched subagent, expect a thin path set and say so rather than inventing entries.

   🔴 **If you landed any PRs this session, run it a SECOND time over them** — you know exactly which ones, and nothing else in the toolchain does:

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --pr <n>[,<n>...]
   ```

   This is the **only** source that sees a **subagent's** work — a PR's file list does not care which agent, session or tool wrote the bytes, and delegating implementation to a subagent is the standing default here.

   🔴 **A PR's file list is what the BRANCH LANDED, not what this session touched — never describe it as this session's work.** It is the union of every commit on the branch, so it includes another session's commits, hand-made ones, and older work on a long-lived branch; and it omits anything you did that never reached a PR. When you write a journal bullet from a `--pr` run, attribute it to the branch or the PR, not to "this session".

   🔴 **Run it twice; never merge the two path sets.** The flags are mutually exclusive and the tool refuses the combination on purpose: a single merged set would carry one caveat that is wrong about half its members, and you would lose the one fact that decides whether a bullet is honest. Two runs, two caveats, each read on its own.

   Closed-unmerged PRs are refused by name — their files exist in no tree. `OPEN` and `MERGED` are both accepted, so a PR still in review counts. The `gh` call can fail in ways nothing local can (`gh` missing, not authenticated, network down, rate-limited, a truncated file list); every one exits non-zero with its own named line, and **none of them ever returns an empty path set**, so an empty result from this source always means the PRs genuinely listed no files.

   🔴 **If the work did not land through a PR — a direct push, or a repo whose own rules force every edit into a throwaway worktree — run it over the SHAS YOU CREATED:**

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --commit <sha>[,<sha>...]
   ```

   A commit is the primitive the other two reduce to — a PR is a set of commits, worktree-authored work becomes a mainline commit, a direct push *is* a commit — so this reaches repos where neither of the others can. **You know the shas you just made; nothing else in the toolchain does.**

   🔴 **This window is what those COMMITS changed** — neither a session nor a branch. It excludes uncommitted work and anything you did that never became one of these commits (a *sibling* commit on the same branch is not in it), and it over-reports when a commit carried more than the work you are recording (a formatting sweep, a stray `git add`). Attribute a bullet to the commits, never to "this session". Hex shas only: a revision expression such as `HEAD`, an ambiguous short sha, a non-commit object and a merge commit are each **refused by name; pass a merge's side commits, or use `--pr`**. Same rule as the two runs above — **run it separately, never merge the path sets**.

   🔴 **Any non-zero exit ⇒ print the stderr line verbatim and write NOTHING.** Exit 3 covers a missing store, a malformed entry, an unusable path, a git failure and every `gh`/pull-request/commit failure alike; none of them is a reading. Do **not** fall back to recollection — an entry written from memory is exactly the unverifiable content this store must not accumulate.

   Otherwise read `status=` and act on that case:

   - **`resolved`** — for each entry listed, propose appending **one dated bullet** in the existing style (`- YYYY-MM-DD: …`, ≤2 lines) as the **FIRST** bullet under `## Nuance / work-history`. Write a *durable lesson* — a gotcha, a decision, why it was touched — never routine status, never a config value, never a copy of what the handoff doc already says.

     🔴 **First read the `already there` lines the tool prints under that entry, and check your proposed bullet against them — this is a comparison, not a feeling.** Restating a lesson the entry already carries is the failure this display exists to prevent: it is likeliest right after work you feel good about, and a repeat `/handoff` the same day is the common case (the tool prints `ALREADY dated <today>` with a count when one has run). **A bullet that adds nothing to what is on screen ⇒ propose nothing and say `index unchanged`** — declining is a normal, frequent outcome. If the entry has no `## Nuance / work-history` section at all, the tool says so: the heading has to be created as part of the append, because the `Edit` below anchors on it.
   - **`ambiguous` listed** — report the candidates and **write nothing** for that ref. The resolver refuses to pick; so do you.
   - **`no-match` / `scope-absent`** — no existing entry was touched. `scope-absent` means this repo has no scope directory yet: the **first-entry case, not a failure**, and the reason this step exists. Nothing to append either way; go to the NO ENTRY clause below.
   - **`looked-at-nothing`** — say so plainly and write nothing. This is *not* "nothing touched an entry": no path was examined at all. Never report the two as the same result.

   **Separately, and independently of `status=`: if a `NO ENTRY` block is printed, consider a new entry.** It appears alongside `resolved` too — a session normally touches a known subsystem *and* an unrecorded one, and treating nominations as a `no-match`-only concern is what would stop entries ever accruing in a repo that already has one. Pick **at most one** nomination that is a real durable subsystem, or none — they are candidates, not answers, and rejecting all of them is a normal outcome. `--template <slug>` prints the entry to write — identity front matter (`scope`, `sensitivity: client-confidential`, `created_by: handoff`) plus `## What it is` and `## Pointers`. **Do not demand the full schema** — a thin entry that exists beats a rich one that doesn't. Uncomment the template's `aliases:` line if the subsystem is spelled more than one way, in particular the `test_<slug>` stem: matching is exact, so without it a module and its own test count as one path and stay under the threshold forever.

   **— the write half; everything above this line only reads —**

   🔴 **The target is `~/.claude/analyze-service-index/<scope>/<slug>.md`** — an absolute path outside every repo. Never anywhere in the working tree: these entries carry client-identifying infrastructure detail and `devrc` is PUBLIC. **Read `~/.claude/analyze-service-index/<scope>/README.md` before writing there** — each scope's own README states the policy governing it, and it is authoritative over this file.

   📖 **The measured evidence behind this whole step is in `reference/index-write.md`** (deployed `~/.claude/skills/handoff/reference/`, source `~/workspace/devrc/claude/skills/handoff/reference/`) — the two-session concurrency simulation and the retraction it forced, the autocommit timer's behaviour, each path source's blind spots with their numbers, and the `gh` file-list truncation cap. Read it when a rule looks arbitrary or you are about to work around one. It is rationale only: every rule you must follow is here, and none of it needs reading before the probe.

   🔴 **Write only on explicit confirm, diff first, exactly as `analyze-service`'s write-back specifies** — show one compact unified diff, ask a single *"append this to the index? (y/N)"*, and **on decline, discard**. On confirm **re-read the file and re-apply to current bytes** so a concurrent append isn't clobbered, then **use `Edit` anchored on `## Nuance / work-history`, not `Write`** — a whole-file retype of a curated, unbacked-up entry risks losing content the diff never showed. (`Write` only for a first-ever file, which has no prior content to lose.) 🔴 **The re-read is the actual safeguard, not the anchor: do it every time, and do not treat "no error" as evidence you were alone** — a concurrent append on this anchor is measured to land silently.

   Carry the store's invariants: **pointers, not copies**; **never persist live status** (no counts, no Ready/NotReady, no current version) — for anything with no live probe, persist the *derivation method and what a stale reading looks like*, never the reading. Never silent-mutate. 🔴 **Write the file and run no git command** — the store is versioned by its own out-of-band autocommit. Never add a remote, never copy a line of it into a public repo. **This holds for a brand-new scope directory too: the hourly timer creates and commits its repository — do not create the repository yourself.** So a just-written entry in a new scope is **unversioned for up to an hour**; that is the normal window, not a failure, and not something to fix by hand.

Keep the doc tight and high-signal — it is read first thing next session, so every line must earn its place. The "Open investigations" blocks are the exception to brevity: a mid-diagnosis bug is worth verbatim evidence, because re-deriving it next session costs far more than the lines do. Pair: `/resume`.
