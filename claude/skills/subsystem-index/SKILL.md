---
name: subsystem-index
description: "Record what a session touched in the analyze-service index store, and write or validate an entry. The protocol /handoff follows at end of session; rarely run directly."
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# subsystem-index — record what a session touched

The store is the terse **pointer sheet** that OUTLIVES a handoff doc: handoff docs
are per-topic and get overwritten, this does not.

🔴 **THIS IS THE APPEND PROTOCOL FOR EVERY WRITER OF THIS STORE — `/handoff` AND
`/analyze-service` BOTH.** There is one protocol and one document; there is no
per-skill variant to look for.

🔴 **Since the Cairn cutover the write is `cairn append` / `cairn put`, and the
local store is FROZEN read-only (`0444`).** The pod is the authority; the local
copy has none. An `Edit` or `Write` against an entry file now fails with
`EACCES` — by design, because a local-only bullet lived on one host and the next
seed replaced the served copy with a file that never had it. The full mechanism
is in the write half at the end of this document.

⚠ **It was genuinely forked until 2026-08-31, so a stale memory of that is not
paranoia — read this before re-deriving it.** `/analyze-service`'s door,
`claude/skills/analyze-service/reference/write-back.md`, carried a second copy of
the protocol that MATERIALLY CONFLICTED with this one: it gated the append behind
`append this to the index? (y/N)`, which this file had already declared retired,
and it said `Write` where this file mandates `Edit` anchored on
`## Nuance / work-history` (measured: a whole-file retype silently loses a
concurrent append). Both read as *the* protocol and neither named a winner.
**Operator decision, 2026-08-31: the y/N is retired EVERYWHERE and this file
wins.** `write-back.md` was reduced to a POINTER at this one plus the
`/analyze-service`-specific content that was never duplicated (what counts as
notable, auto-discovered pointers, bloat discipline). The reconciliation is
mechanically defended by `scripts/tests/test_index_append_protocol.py`, which
goes red if either door grows a second protocol.

🔴 **Consequence, and the reason the template below no longer hardcodes a
writer:** this file has TWO callers, so `created_by:` is the CALLER's id, not
this module's. Pass `--writer handoff` (you, `/handoff`) or `--writer
analyze-service`; `--template` refuses to print without it rather than stamping a
default that would be silently wrong for one of the two. `--census` reads the
split back, which is the only reason the field exists.

🔴 **This lived inside `/handoff` step 4 until 2026-08-24, and that was the wrong
home in a way worth stating.** It is a separate subsystem: its own tool
(`scripts/lib/subsystem_touch.py`), its own store outside every repo, its own
reference doc, and the sentences below pinned verbatim by
`scripts/tests/test_subsystem_touch.py`. It made a skill about writing handoffs
56% index protocol by weight, paid in full every time `/handoff` ran — which is
at the END of a session, when the window is already tight. Nothing about the
protocol changed in the move; the pins are the evidence.

⚠ **Callers: this is a PROCEDURE, not a decision.** Run it where your own flow
says to, and hand its outcome back — do not re-litigate whether to write.

---

**Record what a session touched in the subsystem index** — a read-only probe, then an opt-in write.

✅ **Run the probe FIRST, unconditionally — do not research anything before running it.** It **never writes**; it resolves the changed paths against the store and reports. Its first two output lines state the `scope=` it derived and the `store:` path it read, **so the two facts you would otherwise go looking up are printed by the command you are deciding whether to run.** Nothing below needs to be settled beforehand. The write half is at the END of this step, and it asks nothing — see the write rule there.

```
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --session <session-uuid> --exclude claudedocs/handoff-<topic>.md
```

🔴 **`<session-uuid>` is the basename of your scratchpad directory.** Your system prompt names a scratchpad path of the form `/tmp/claude-<n>/<project>/<session-uuid>/scratchpad` — pass that `<session-uuid>` segment, nothing else. There is no environment variable for it, so it can only come from you.

**Pass it whenever you can.** It reads what *this session* actually edited from its own transcript, **independent of git**. Without `--session` the tool falls back to git's **branch** window — still honest, still tested, but blind to work that has already merged.

🔴 **Never pass a UUID you are unsure of.** It is validated — the transcript must exist, have been written in the last 30 minutes, and either record a `cwd` equal to `--repo` or name absolute paths under it — and a wrong one **fails with a named error rather than silently reporting another session's paths**. Never retry with a different UUID to make it pass.

**A session that ran in ANOTHER repo is no longer refused outright.** If its turns named absolute paths that resolve under `--repo`, you get the `session-absolute` window over exactly those; the run is normal and the `caveat:` line says which window you got. Read it — that window is a **floor**, not a list: every path the session named *relatively* belongs to its own cwd and is excluded, so how much is missing is unknown rather than counted.

🔴 **Which fallback is right depends on WHICH validation failed — and the wrong choice is a second dead source.** A missing, stale, unreadable or simply wrong UUID ⇒ drop `--session` and use the git window instead. But **`transcript cwd does not match` ⇒ do NOT fall back to the git window**: that session ran in a *different* repo *and named nothing absolute under this one*, so this repo's branch window is empty too, and you would be reading a second source that structurally cannot answer. Go to `--pr`/`--commit` over what you landed here. Never work around the cwd guard — a **relative** path in a transcript is relative to its own session's cwd, so re-anchoring one here would file another repo's work under this one.

`--exclude` drops **the handoff doc itself** — the caller runs this BEFORE landing its handoff doc, so on a first run it is usually not there yet, but a `--pr`/`--commit` window over work that already carried one will list it, and a repeat run finds the copy the earlier run committed. Without it `claudedocs` is a nomination on every single run. 🔴 **Scope follows `--repo`, NOT where the work happened** — those coincide only when you worked in the repo you are sitting in. See the cross-repo rule below before you conclude anything from a single run.

**Read the `caveat:` line before you write anything** — it states what the chosen window structurally cannot see, and the sources are blind in *opposite* directions. The session window in particular does **not** include what a **subagent** edited, or files written by a `Bash` command; if the session's real work happened in a dispatched subagent, expect a thin path set and say so rather than inventing entries.

🔴 **If you landed any PRs this session, run it a SECOND time over them** — you know exactly which ones, and nothing else in the toolchain does:

```
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --pr <n>[,<n>...] --exclude claudedocs/handoff-<topic>.md
```

This is the **only** source that sees a **subagent's** work — a PR's file list does not care which agent, session or tool wrote the bytes, and delegating implementation to a subagent is the standing default here.

🔴 **A PR you landed in ANOTHER repo needs its OWN run with `--repo <that repo>`.** Scope follows `--repo`, so a window run from the repo you are sitting in **cannot see it** and the store never learns the work happened. **This is not the rare case** — measured, more than half the sessions that ran a window left at least one PR's repo unscoped. **List the repos you opened PRs in, then run the window once per repo.** A hub session that dispatches work outward is the normal shape here, not an exception. 📖 `~/.claude/skills/subsystem-index/reference/index-write.md` §7.1 (every bare `§N` below is that file).

🔴 **A lesson about YOUR OWN TOOLING, learned while sitting in a client repo, does not belong in that client's `claudedocs/`.** Route it to the `devrc` scope, or to the owning skill — **ask which repo the lesson is *about*, not which one you were standing in.** Measured: three generic gotchas with zero client content were recorded only in a client-confidential handoff doc that no devrc session will ever read. 📖 §7.2.

🔴 **A PR's file list is what the BRANCH LANDED, not what this session touched — never describe it as this session's work.** It is the union of every commit on the branch, so it includes another session's commits, hand-made ones, and older work on a long-lived branch; and it omits anything you did that never reached a PR. When you write a journal bullet from a `--pr` run, attribute it to the branch or the PR, not to "this session".

🔴 **Run it twice; never merge the two path sets.** The flags are mutually exclusive and the tool refuses the combination on purpose: a single merged set would carry one caveat that is wrong about half its members, and you would lose the one fact that decides whether a bullet is honest. Two runs, two caveats, each read on its own.

Closed-unmerged PRs are refused by name — their files exist in no tree. `OPEN` and `MERGED` are both accepted, so a PR still in review counts. The `gh` call can fail in ways nothing local can (`gh` missing, not authenticated, network down, rate-limited, a truncated file list); every one exits non-zero with its own named line, and **none of them ever returns an empty path set**, so an empty result from this source always means the PRs genuinely listed no files.

🔴 **The discriminator is whether the work LANDED AS A PR — never whether a worktree was involved.** A throwaway worktree goes both ways and the clause below is one *example* of "no PR", not an independent trigger: in one repo most mainline commits carry no `(#N)` and `--commit` is right; elsewhere the same worktree rule lands PRs and `--pr` is the only source that sees them. A session that read "worktree" as "use `--commit`" got a zero that was real for the window it read, and the window was wrong. **Ask which way yours landed.** 📖 `~/.claude/skills/subsystem-index/reference/index-write.md` §3.

🔴 **If the work became a commit but NO PR — a direct push, or a branch not opened yet — run it over the SHAS YOU CREATED:**

```
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --repo <repo> --commit <sha>[,<sha>...] --exclude claudedocs/handoff-<topic>.md
```

A commit is the primitive the other two reduce to — a PR is a set of commits, worktree-authored work becomes a mainline commit, a direct push *is* a commit — so this reaches repos where neither of the others can. **You know the shas you just made; nothing else in the toolchain does.**

🔴 **This window is what those COMMITS changed** — neither a session nor a branch. It excludes uncommitted work and anything you did that never became one of these commits (a *sibling* commit on the same branch is not in it), and it over-reports when a commit carried more than the work you are recording (a formatting sweep, a stray `git add`). Attribute a bullet to the commits, never to "this session". Hex shas only: a revision expression such as `HEAD`, an ambiguous short sha, a non-commit object and a merge commit are each **refused by name; pass a merge's side commits, or use `--pr`**. Same rule as the two runs above — **run it separately, never merge the path sets**.

🔴 **Any non-zero exit ⇒ print the stderr line verbatim and write NOTHING.** Exit 3 covers a missing store, a malformed entry, an unusable path, a git failure and every `gh`/pull-request/commit failure alike; none of them is a reading. Do **not** fall back to recollection — an entry written from memory is exactly the unverifiable content this store must not accumulate.

**…but "write nothing" is not "do nothing": if the message prints a `RECOVER —` block, run the command it gives you.** A malformed entry refuses the probe by design (a writer must not act on a partially-read store), and the refusal now names every unreadable file, says whether it is in *this* repo's scope, and emits a runnable `--validate` per affected scope. 🔴 **Run the command it printed, not one you compose** — the blocking file is often in a *different* scope, and `--validate` on your own scope would report it clean while the probe keeps failing. Fix the files it names, re-run the probe, and only then decide about writing. A store left broken because a refusal had no route out is how it stays broken until a human happens to look.

Otherwise read `status=` and act on that case:

- **`resolved`** — for each entry listed, propose appending **one dated bullet** in the existing style (`- YYYY-MM-DD: …`, ≤2 lines) as the **FIRST** bullet under `## Nuance / work-history`. Write a *durable lesson* — a gotcha, a decision, why it was touched — never routine status, never a config value, never a copy of what the handoff doc already says.

  🔴 **First read the `already there` lines the tool prints under that entry, and check your proposed bullet against them — this is a comparison, not a feeling.** Restating a lesson the entry already carries is the failure this display exists to prevent: it is likeliest right after work you feel good about, and a repeat `/handoff` the same day is the common case (the tool prints `ALREADY dated <today>` with a count when one has run). **A bullet that adds nothing to what is on screen ⇒ propose nothing and say `index unchanged`** — declining is a normal, frequent outcome. If the entry has no `## Nuance / work-history` section at all, the tool says so: **create the heading first with `cairn put`, then append** — the store refuses a bullet for an entry with no such section (422 ⇒ exit 6), so the heading is its own write and is no longer part of the append.

  🔴 **If your bullet proposes work that has NOT been done, it must start `OPEN:`; if you are closing one that has, rewrite that bullet as `RESOLVED <sha>:` in the SAME TURN.** 🔴 That is **two writes now, and the order is load-bearing**: the rewrite is a `cairn put` and goes FIRST, then the new bullet's `cairn append`. Reversed, your own append moves the entry out from under the put's `If-Match` and it 412s against you. The marker goes after the date (`- 2026-08-15: OPEN: …`). Measured: one entry's proposed remedy landed **two minutes** after the entry was written, and the entry then served it as outstanding for **22 days**. Nobody was careless — this step runs mid-session, so the writer is gone by the time the work finishes and the store had no way to say "still open". 🔴 **A proposed remedy with no marker is the default failure, not an edge case** — it reads exactly like a current one forever. 📖 §5.

  🔴 **Before appending, act on the `OPEN:` block the tool prints for that entry.** It lists, as SEPARATE populations that never overlap: every declared-open bullet with its age (`oldest unverified for N days`); every bullet that **tried to write a marker and missed the grammar**, which declares nothing and shows no badge — fix the line; and every bullet that merely *looks* like an open action, which is **AT LEAST this many**, a two-phrasing floor with unknown recall, never a count. Re-check each against the repo *now*: you are the next writer, and the next write is the only moment anyone is looking. Closing one is a one-line edit and is worth more than the bullet you came to add.

  ⚠ **The looks-like-open population has FALSE POSITIVES too, and one of them is unclearable — so do not edit blind to make the warning go away.** The detector is `_UNMARKED_ACTION = \bFIX\s*[(:]|\bnot (yet )?addressed\b`, and `\bFIX\s*[(:]` matches the `fix:` inside *"Rotating the token is **NOT the fix:** …"* — a sentence asserting the opposite of an open action. MEASURED: a session read the flag, edited the bullet's genuinely-stale *"still OPEN below, needs a rebase"* clause, re-validated, got the **identical** warning back verbatim, wrote "index recorded" and moved on — the clause it fixed was never the trigger. The bullet cannot be cleared without rewording correct prose. **So diff the flagged bullet against the two phrasings before you touch it**, and if the match is a negation, say so and leave it. Scale, so nobody over-reads this: ~9 flagged bullets across 114 entry files, one confirmed negation. Minor — but a warning that cannot be cleared is how the whole advisory stops being read.

🔴 **Decline on CONTENT, never on cost — an index entry does not cost a session anything.** Entries load on demand, read only by `/resume` step 4, never at session start; what loads every session is the skill *listing* (name + description), not entries and not skill bodies. An extra entry adds **one index row, ~14 tokens**, to a `/resume` that chooses to read it. Suppressing writes on a cost that does not exist is how the store stays empty and the emptiness then gets read as nobody wanting it. **"Already in the handoff doc" is also not a reason**: this store exists to OUTLIVE handoff docs, which are per-topic and get overwritten. A durable `.claude/skills/<name>/SKILL.md` *is* a legitimate alternative home; a handoff is not. 📖 §7.3.

🔴 **A subsystem with NO FILE FOOTPRINT is invisible to every window — that is not the same as "nothing to record".** All four windows read file paths, `nominate()` clusters paths and needs two, and the `NO ENTRY` prompt only appears when something was nominated — so a session whose work was a production database, a cluster, a DNS record or an external service resolves and nominates nothing. On a dead end with no nominations the tool prints a **`NO PATH FOOTPRINT?`** block with the exact `--template <slug> --scope <scope> --writer <caller>` command, which needs no paths at all. 🔴 State the trade when you use it — and state it accurately: nothing matches such an entry automatically **today**, but it is not permanently unresolvable. It **gains normal path resolution** the moment some path is named for the slug, so **prefer a slug a future path would carry**. Until then it is listed in the scope index and found by `--search`, which is enough to read at `/resume`. 🔴 The `NO ENTRY` block carries the same offer, because a nomination is path-derived and names the directory you touched rather than the database you worked on — and most dead ends DO nominate something, so that is where this case usually hides. The bias this closes is the one that matters: the subsystems living OUTSIDE the repo are exactly the ones whose knowledge is tribal. Still **at most one, or none**. 📖 §7.4.

🔴 **"It belongs in a skill" is a ROUTE, not a disposal — finish it in THIS turn or it is lost.** The sentence above authorises declining the index; on its own it also discharges the obligation, and the lesson then lives only in a transcript, which is the medium this store exists to outlive. 📖 §7.7. So: **take the owning skill from the tool's `SKILL HOMES` block, never from memory** — it ranks by term specificity, and a hit is a lead, not an answer. If nothing matched, run the `grep -ril` it prints with *your* domain term, because the tool derives terms from paths and **cannot see what the session was about**. Then append there under the same rule as the index write — show one compact diff, then edit; no question. 🔴 Edit the **repo source** (`~/workspace/devrc/claude/skills/…`), never `~/.claude/…` (a read-only store symlink), and a **new** file must be `git add`ed or the flake silently omits it from the deploy. If genuinely nothing owns it, say **UNFILED** and name the term you searched — an unfiled item that names its search is recoverable; "belongs in a skill" is not.
- **`ambiguous` listed** — report the candidates and **write nothing** for that ref. The resolver refuses to pick; so do you.
- **`no-match` / `scope-absent`** — no existing entry was touched. `scope-absent` means this repo has no scope directory yet **in THIS HOST's store**: the **first-entry case, not a failure**, and the reason this step exists. 🔴 **The store is PER-HOST and unreplicated, so that is never a claim about the fleet** — measured 2026-08-27 the workbench held 115 entries / 14 scopes and the laptop 33 / 11, seven scopes existed only on the laptop, and one probe reported a scope "absent" that had four entries on the other machine. Write the first entry here anyway; just say "on this host", and read the `host:` line the tool prints. Nothing to append either way; go to the NO ENTRY clause below.
- **`looked-at-nothing`** — say so plainly and write nothing. This is *not* "nothing touched an entry": no path was examined at all. Never report the two as the same result.

🔴 **On either dead end the tool now prints a `ROUTE OUT` block naming the windows it did NOT read** — read it before concluding "a real zero". A zero is a fact about the window, and the four windows are blind in different directions; the block excludes the source that just failed, so every line in it is a move you have not made. It appears on `looked-at-nothing` and `no-match` only, never on a resolved run.

🔴 **ESCALATE BEFORE CONCLUDING — a thin or empty session window is not a result, it is a prompt to read a second one.** If `--session` returns `looked-at-nothing`, `no-match`, or `BELOW THRESHOLD` with nothing proposed, **run `--pr` (or `--commit`) NOW, in this same step, before you report anything.** The stop condition is **"a second window was read and also came back empty"** — never "the first window was empty". Measured: a session that built and deployed a whole service across nine merged PRs got a `no-match` from `--session` and proposed no write, while `--pr` over those same PRs nominated the service well above threshold — **~94% of the work was invisible to the preferred window**. 📖 §7.5.

🔴 **This does NOT violate "the windows never compose."** That rule forbids **MERGING the two path sets** into one report carrying one caveat that would be wrong about half its members. It says nothing about **reading** a second window: two runs, two reports, each read on its own with its own caveat, is the composition that *is* available — it is what "run it twice" means, and the tool refuses only the merged invocation. Reading one window and stopping is not compliance with that rule; it is a different failure.

🔴 **Expect the session window to be thin here, by construction.** The standing default is to DELEGATE non-trivial work to a subagent, and any file-modifying subagent gets its own WORKTREE — a subagent's turns are a *separate transcript* and a worktree is *outside the session cwd*, so the two defaults hit two of this window's three blind spots at once. **The better the session followed the rules, the less of it `--session` can see.** When most of the paths it named are outside the cwd the tool now prints a computed **`WRONG WINDOW?`** block with that run's own numbers and the exact flags to run instead — it fires on the measured condition, not on every run, so **when it appears, act on it**; treat it as the escalation being handed to you rather than as a caveat to note.

**Separately, and independently of `status=`: if a `NO ENTRY` block is printed, consider a new entry.** It appears alongside `resolved` too — a session normally touches a known subsystem *and* an unrecorded one, and treating nominations as a `no-match`-only concern is what would stop entries ever accruing in a repo that already has one. Pick **at most one** nomination that is a real durable subsystem, or none — they are candidates, not answers, and rejecting all of them is a normal outcome. `--template <slug> --writer <caller>` prints the entry to write — identity front matter (`scope`, `sensitivity: client-confidential`, `created_by: <caller>`) plus `## What it is` and `## Pointers`. 🔴 **`--writer` is REQUIRED and has no default**, because this store has two writers and a default would stamp one of them wrongly and silently: as `/handoff` you pass `--writer handoff`, which puts `created_by: handoff` in the file; an `/analyze-service` run passes `--writer analyze-service`. Omitting it exits 2 and prints the choices rather than guessing. **Do not demand the full schema** — a thin entry that exists beats a rich one that doesn't. Uncomment the template's `aliases:` line if the subsystem is spelled more than one way, in particular the `test_<slug>` stem: matching is exact, so without it a module and its own test count as one path and stay under the threshold forever.

**— the write half; everything above this line only reads —**

🔴 **The entry lives at `~/.claude/analyze-service-index/<scope>/<slug>.md`** — an absolute path outside every repo, and since the Cairn cutover a **read-only** mirror of what the pod holds: entry files are `0444`, and the write verbs below go to the pod, never to this path. Never anywhere in the working tree: these entries carry client-identifying infrastructure detail and `devrc` is PUBLIC. **Read the policy file the probe named on its `policy:` line before writing there, and do not go looking for one it did not name.** A scope may have no README of its own — a *new* scope starts without one, so the gap recurs by construction — and the probe therefore resolves it deterministically and prints which of the three cases you are in: the scope's own README (`scope README`, authoritative for that scope), the store-root README (`this scope has none of its own`, so it is the store's general policy and not a statement by this scope), or neither. Do **not** create a scope README to fill the gap: each one is a human policy statement, and writing it yourself would be manufacturing authority.

🔴 **After writing an entry — new file or appended bullet — validate it in the SAME turn:**

```
cairn sync && cairn validate --scope <scope>                     # after `cairn append` / `cairn put`
python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py --validate <path-you-just-wrote>   # a NEW file you created locally
```

🔴 **Which of the two depends on WHERE THE BYTES LANDED, and picking wrong reads a file the write never touched.** An API write lands on the pod; the local mirror is read-only and does not move, so `--validate <path-you-just-wrote>` after an append parses the *pre-append* bytes and reports a clean entry that is not the one you wrote — `cairn sync` first and validate the refreshed cache. A brand-new entry file is the mirror image: it exists only locally and the pod has never seen it, so the path you wrote is the only thing there is to validate.

🔴 **READ THE `entry shape:` BLOCK, NOT ONLY THE EXIT CODE.** It is advisory and deliberately does **not** move the verdict — an entry whose spine is broken still parses, still loads, and still exits 0 — so branching on the exit code alone is how it goes unread. It reports the two headings a COUNT depends on (`## Pointers`, `## Nuance / work-history`) as ABSENT, RENAMED, DUPLICATED or present-and-EMPTY, and names what you wrote instead. `## What it is` is deliberately not among them — `subsystem_recall` does surface it, but it feeds no count and no badge, so the reader names a missing one under the entry's own body rather than as a validator finding. A RENAMED nuance heading is **silent data loss**: the entry's index row then shows `0 nuance` with no `🔴 N OPEN` badge while the bullets sit intact on disk, and `/resume` consumes exactly that row. Fix the heading in the same turn — it is one edit, and nothing else will tell you.

🔴 **AND READ THE `marker reachability:` BLOCK — reason token `unreachable-marker`.** Also advisory, also exits 0. A marker typed on a bullet's **continuation line** is spelled correctly and reaches **no** surface: `_bullet_openness` reads a bullet's OPENING line only, so it raises neither the `OPEN` badge nor `NEAR-MISS`. It is **not** a near-miss and is **not** counted as one — a near-miss is mis-spelled where the parser looks and is fixed by editing that line; this is fixed by **promoting the line to a top-level bullet of its own**. Measured: such a bullet had only ever raised a badge *by accident*, through a broken marker sitting above it — so repairing that one would have **silenced a still-open action**. If this block names a bullet you are about to tidy, re-check that action against its repo first.

🔴 **AND READ THE `dropped lines:` BLOCK — reason token `dropped-line`.** Advisory, exits 0, and it is the one that means **content is already lost**. `parse_journal_bullets` drops text that precedes the first bullet, so a bullet whose OPENING line was lost or indented leaves its body on disk inside **no** bullet — invisible to `--ref`, `--search`, the digest and every openness count. It prints **before** the two blocks above on purpose: those never see the dropped text, so their `0 declared` and `0 out-of-reach` are facts about content the parser never reached, and read first they are simply false. The fix is to **restore the bullet opening line, never to delete the text** — each scope is its own git repo, so the previous version is one `git -C <store>/<scope> log -p -- <file>` away; reconstructing it by hand invents a date the store never had. ⚠ It is **partial by construction and says so**: a bullet that lost its head while another bullet sat ABOVE it is absorbed into that one and is byte-identical to a legitimate wrap, so no check can see it — a `0` here is not a claim about that case. MEASURED: 7 historical versions across two `datapacket-talos` entries carried dropped lines for 2–8 days, one of them holding an `OPEN:`, with `--validate` green throughout.

It exits 0 with `OK — N of N entry file(s) parse`, or **3** with a `malformed index entry` row per bad file. This exists because the store's front matter is parsed **line by line**, so a wrapped `aliases: [...]` list rejects the entry — and the confirm-gate diff you just approved *contained* that defect while being structurally incapable of revealing it. Without this check the failure surfaces hours later, in a different session, from a different tool. `--validate` with **no path** checks every entry in the scope instead (and only that form can catch a duplicate ref, which is a relationship between two files). It reuses the reader's own parser and validator, so a pass here is the reader's verdict, not a second opinion.

📖 **The measured evidence behind this whole step is in `~/.claude/skills/subsystem-index/reference/index-write.md`** (deployed `~/.claude/skills/subsystem-index/reference/`, source `~/workspace/devrc/claude/skills/subsystem-index/reference/`) — the two-session concurrency simulation and the retraction it forced, the autocommit timer's behaviour, each path source's blind spots with their numbers, and the `gh` file-list truncation cap. Read it when a rule looks arbitrary or you are about to work around one. It is rationale only: every rule you must follow is here, and none of it needs reading before the probe. ⚠ **Its `Edit`-anchor measurements describe the RETIRED local protocol** — they are why the API's `flock` and `If-Match` exist, not an instruction to go back.

🔴 **Write it — no question. SHOW the diff, then `cairn append`.** Operator decision: the y/N here was always answered `y`, so it bought a round trip and no safety. What it never was is the content filter — that is the `already there` comparison above, which still decides whether a bullet is written at all. **Declining on content stays a normal, frequent outcome; declining by prompt is gone.**

🔴 **`/handoff` ASKS NOTHING, at either write — retired here 2026-08-15, at step 5 on 2026-08-23, both by operator decision on the same evidence: the answer was always `y`.** What survived is the part that was doing the work. Here it is the `already there` comparison, which still decides whether a bullet is written at all; in `/handoff` step 5 it is the four refusing statuses and the warnings above the diff. **Both steps still SHOW the diff before writing** — that was never the prompt's job, and the transcript is the only record of what landed. 🔴 So a *decline* is still a normal, frequent outcome; declining **by prompt** is what is gone. Blast radius earns a REFUSAL, not a question.

**Still print the unified diff before writing** — the transcript is the only record of what landed, and a reader scanning it later needs to see the bullet without opening the store. Then append **through the store API**, never with an editor:

```
cairn append --scope <scope> --ref <entry> --session <session-uuid> \
  --text '<the bullet, ONE line, NO leading "- ", NO leading date>'
```

🔴 **The server adds the `- ` and the date — do not type either.** The first production append through this route reads `- <date>: <date>: …` because a caller did. `--ref` is the entry's filename stem or one of its aliases and `--scope` is the scope the probe printed. It prints `appended` or `duplicate` with the new revision: the server recognises a bullet by CONTENT HASH, so a retry after a timeout is idempotent — and `duplicate` means nothing was written, which is a different outcome from `appended` and is stated rather than swallowed.

🔴 **A non-zero exit wrote NOTHING and queued NOTHING — say so and stop; never fall back to editing the local mirror.** 6 = the request was refused (malformed bullet, unknown ref, unseen scope, an entry with no `## Nuance / work-history`, or a read-only image, which is an OPERATOR problem and not yours to work around); 7 = the store was unreachable; 8 = the entry moved under the edit. A write during an outage is REFUSED, not queued — that is the accepted cost, because a caller told "queued" believes the record exists. Reads keep working offline; writes do not.

🔴 **Rewriting an EXISTING bullet is not an append — `OPEN:` → `RESOLVED <sha>:`, or a `## Pointers` fix, goes through `cairn put`:**

```
cairn sync                                                        # the live bytes
cp ~/.cache/subsystem-store/<scope>/<entry>.md <scratchpad>/entry.md
#   edit <scratchpad>/entry.md — the scratch copy, never the mirror
cairn put --scope <scope> --ref <entry> --file <scratchpad>/entry.md
```

🔴 **`cairn put`'s `If-Match` is derived from a LIVE sync, and THAT IS WHAT REPLACES the two rules this step used to carry — it is a replacement, not an omission.** The retired pair, in one sentence: *re-read the entry and re-apply to its current bytes, then `Edit` anchored on `## Nuance / work-history` rather than `Write`.* Both halves existed because nothing arbitrated two concurrent writers: a whole-file retype was measured to lose a concurrent append, and a concurrent `Edit` on that anchor **succeeded silently**, so "no error" was never evidence you were alone. There is an arbiter now — the API appends under a per-entry `flock`, and a `put` whose base revision has moved is REFUSED with exit 8 instead of clobbering. 🔴 **So exit 8 IS the other writer, not a transient error:** `cairn sync`, re-read, re-apply your change to the NEW bytes, diff again, put again. Never retry the same file, and never pass `--if-match` by hand to make it go away — that is the clobber the precondition exists to stop.

🔴 **The local mirror is READ-ONLY: entry files are `0444` and an `Edit`/`Write` against one fails with `EACCES`.** That refusal is the design, not a broken store. (`Write` only for a first-ever file, which has no prior content to lose — and which the API cannot create, since `POST` and `PUT` both resolve an *existing* ref. Scope directories stay writable so that still works. ⚠ Known follow-up, not a break: a locally-created entry is **local-only** until the next cutover run classifies it `ADD` and pushes it.)

Carry the store's invariants: **pointers, not copies**; **never persist live status** (no counts, no Ready/NotReady, no current version) — for anything with no live probe, persist the *derivation method and what a stale reading looks like*, never the reading. Never silent-mutate. 🔴 **Write the file and run no git command** — this is the first-ever-file case, the one write that is still local; the store is versioned by its own out-of-band autocommit. Never add a remote, never copy a line of it into a public repo. **This holds for a brand-new scope directory too: the hourly timer creates and commits its repository — do not create the repository yourself.** So a just-written entry in a new scope is **unversioned for up to an hour**; that is the normal window, not a failure, and not something to fix by hand.
