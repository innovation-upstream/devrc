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

3. **Record what this session touched in the subsystem index** (read-only probe, then an opt-in write). The index is the terse *pointer sheet* that outlives this handoff doc; `/analyze-service` was its only writer, so entries existed for infra services in one scope while work spans ~12 repos. This step is the other writer. Run:

   ```
   python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo>
   ```

   It **never writes** — it resolves the changed paths against the store and reports. Scope is derived from the repo you are in (worktree-stable), so entries accrue where the work happened. Read its `status=` and act on exactly that case:

   - **`resolved`** — for each entry listed, propose appending **one dated bullet** in the existing style (`- YYYY-MM-DD: …`, ≤2 lines) as the **FIRST** bullet under `## Nuance / work-history`. Write a *durable lesson* — a gotcha, a decision, why it was touched — never routine status, never a config value, never a copy of what the handoff doc already says. Nothing notable ⇒ propose nothing and say `index unchanged`.
   - **`no-match` / `scope-absent`** — the tool lists **nominations** (candidate names, ranked). Pick **at most one** that is a real durable subsystem, or none. Propose a **minimal** new entry: `--template <slug>` prints it (identity front matter incl. `scope`, `sensitivity: client-confidential`, `created_by: handoff`, plus `## What it is` and `## Pointers`). **Do not demand the full schema** — a thin entry that exists beats a rich one that doesn't. Nominations are candidates, not answers; rejecting all of them is a normal outcome.
   - **`ambiguous` listed** — report the candidates and **write nothing** for that ref. The resolver refuses to pick; so do you.
   - **`looked-at-nothing`** — say so plainly and write nothing. This is *not* "nothing touched an entry": no path was examined at all. Never report the two as the same result.

   🔴 **Write only on explicit confirm, diff first, exactly as `analyze-service`'s write-back specifies** — show one compact unified diff, ask a single *"append this to the index? (y/N)"*, and on confirm **re-read the file and re-apply to current bytes** so a concurrent append isn't clobbered. Never silent-mutate. Carry its invariants: **pointers, not copies**; **never persist live status** (no counts, no Ready/NotReady, no current version) — for anything with no live probe, persist the *derivation method and what a stale reading looks like*, never the reading. 🔴 **Write the file and run no git command** — the store is client-sensitive, has no backup, and is versioned by its own out-of-band autocommit. Never add a remote, never copy a line of it into a public repo.

4. **Output a kickoff block** (fenced, ready to copy-paste into the next session) of the form:
   ```
   Continue the <topic> work. Canonical handoff (read first):
     <repo>/claudedocs/handoff-<topic>.md
   <one-line of the single most important next action>
   ```

Keep the doc tight and high-signal — it is read first thing next session, so every line must earn its place. The "Open investigations" blocks are the exception to brevity: a mid-diagnosis bug is worth verbatim evidence, because re-deriving it next session costs far more than the lines do. Pair: `/resume`.
