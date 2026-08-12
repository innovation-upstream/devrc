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

4. **Record what this session touched in the subsystem index** (read-only probe, then an opt-in write). The index is the terse *pointer sheet* that outlives this handoff doc; `/analyze-service` was its only writer, so entries existed for infra services in one scope while work spans ~12 repos. This step is the other writer. Run:

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --exclude claudedocs/handoff-<topic>.md
   ```

   `--exclude` drops the doc **you just wrote in step 2** — without it the handoff doc is untracked in its own window and `claudedocs` is a nomination on every single run. It **never writes**; it resolves the changed paths against the store and reports. Scope is derived from the repo you are in (worktree-stable), so entries accrue where the work happened.

   🔴 **Any non-zero exit ⇒ print the stderr line verbatim and write NOTHING.** Exit 3 covers a missing store, a malformed entry, an unusable path and a git failure alike; none of them is a reading. Do **not** fall back to recollection — an entry written from memory is exactly the unverifiable content this store must not accumulate.

   Otherwise read `status=` and act on that case:

   - **`resolved`** — for each entry listed, propose appending **one dated bullet** in the existing style (`- YYYY-MM-DD: …`, ≤2 lines) as the **FIRST** bullet under `## Nuance / work-history`. Write a *durable lesson* — a gotcha, a decision, why it was touched — never routine status, never a config value, never a copy of what the handoff doc already says. Nothing notable ⇒ propose nothing and say `index unchanged`.
   - **`ambiguous` listed** — report the candidates and **write nothing** for that ref. The resolver refuses to pick; so do you.
   - **`no-match` / `scope-absent`** — no existing entry was touched. `scope-absent` means this repo has no scope directory yet: the **first-entry case, not a failure**, and the reason this step exists. Nothing to append either way; go to the NO ENTRY clause below.
   - **`looked-at-nothing`** — say so plainly and write nothing. This is *not* "nothing touched an entry": no path was examined at all. Never report the two as the same result.

   **Separately, and independently of `status=`: if a `NO ENTRY` block is printed, consider a new entry.** It appears alongside `resolved` too — a session normally touches a known subsystem *and* an unrecorded one, and treating nominations as a `no-match`-only concern is what would stop entries ever accruing in a repo that already has one. Pick **at most one** nomination that is a real durable subsystem, or none — they are candidates, not answers, and rejecting all of them is a normal outcome. `--template <slug>` prints the entry to write — identity front matter (`scope`, `sensitivity: client-confidential`, `created_by: handoff`) plus `## What it is` and `## Pointers`. **Do not demand the full schema** — a thin entry that exists beats a rich one that doesn't. Uncomment the template's `aliases:` line if the subsystem is spelled more than one way, in particular the `test_<slug>` stem: matching is exact, so without it a module and its own test count as one path and stay under the threshold forever.

   🔴 **The target is `~/.claude/analyze-service-index/<scope>/<slug>.md`** — an absolute path outside every repo. Never anywhere in the working tree: these entries carry client-identifying infrastructure detail and `devrc` is PUBLIC. **Read `~/.claude/analyze-service-index/<scope>/README.md` before writing there** — each scope's own README states the policy governing it, and it is authoritative over this file.

   🔴 **Write only on explicit confirm, diff first, exactly as `analyze-service`'s write-back specifies** — show one compact unified diff, ask a single *"append this to the index? (y/N)"*, and **on decline, discard**. On confirm **re-read the file and re-apply to current bytes** so a concurrent append isn't clobbered, then **use `Edit` anchored on `## Nuance / work-history`, not `Write`** — a whole-file retype of a curated, unbacked-up entry risks losing content the diff never showed. (`Write` only for a first-ever file, which has no prior content to lose.) Never silent-mutate. Carry its invariants: **pointers, not copies**; **never persist live status** (no counts, no Ready/NotReady, no current version) — for anything with no live probe, persist the *derivation method and what a stale reading looks like*, never the reading. 🔴 **Write the file and run no git command** — the store is versioned by its own out-of-band autocommit. Never add a remote, never copy a line of it into a public repo.

Keep the doc tight and high-signal — it is read first thing next session, so every line must earn its place. The "Open investigations" blocks are the exception to brevity: a mid-diagnosis bug is worth verbatim evidence, because re-deriving it next session costs far more than the lines do. Pair: `/resume`.
