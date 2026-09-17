# Ageing a mid-diagnosis block — the `INVESTIGATIONS` digest block, and why the doc's prose lies

Routed from the `/resume` core — `~/.claude/skills/resume/SKILL.md`, source
`~/workspace/devrc/claude/skills/resume/SKILL.md`. Sliced out VERBATIM; the step
numbers below are that core's steps.

Load this before adopting any framing from a handoff's `## Open investigations`
section, or when a row came back `EXPIRED` or `UNDATED`.

   🔴 **The `INVESTIGATIONS` block answers "is this diagnosis still LIVE?" — and it is the one block about the doc's own PROSE rather than about live infrastructure.** An `## Open investigations` block is written in the PRESENT TENSE by a session mid-diagnosis, and `/handoff`'s merge APPENDS it forever: nothing ever retracts one, and the doc's status header is dated while the block is not, so it reads as current for the life of the document. **Measured 2026-09-12: a session read one, adopted its framing, and the framing was wrong** — a claim fusing two documents' measurements over two windows with two instruments, refuted only by a full re-measurement (`claudedocs/handoff-handoff-resume-skill-trace.md` now carries the refutation). This paragraph used to be a warning and warnings did not work — it had already cited two earlier instances (2026-08-19, 2026-08-20) — so the age is on screen instead.
   The block prints one row per `### ` block: its age in days, its heading, and **which clock produced that age**. Read the clock — they are not equally strong. `as-of stamp` is the field `/handoff` writes into every new block (rule (l) in `scripts/lib/handoff_doc.py`); it is the most precise and the only one an author can correct. `first commit carrying this block` (or `first commit carrying this block on <ref>`, when the digest read the origin copy because the working tree was stale) is the fallback for an unstamped block, found with git's pickaxe — content-derived, per-BLOCK, and it survives a `git worktree add`; measured over this repo's corpus it dated **478 of 478** unstamped blocks, which is why an unstamped block is aged like any other rather than reported as un-measurable. `the doc's last commit` and `file mtime` each print a `!` gap: the first dates the DOCUMENT rather than the block (a doc recommitted today makes an ancient block read FRESH, so treat that age as a floor), the second is reset by any checkout, copy or rsync.
   🔴 **`EXPIRED` and `UNDATED` are different findings and must be read differently.** **`EXPIRED`** is a `-` DRIFT finding: the block WAS dated and it is past the window (14 days; `RESUME_STATE_INVESTIGATION_MAX_AGE_DAYS` overrides). It does not mean the diagnosis is wrong — it means **re-measure before adopting its framing**, or retire the block. **`UNDATED`** is a `!` gap and a `?` row: no clock could place it, so whether it is current is UNKNOWN, which is not the same as fine. A handoff with no `## Open investigations` blocks at all prints `nothing to age` and is **not** a gap — nothing was asked — and the line says in its own words that this states nothing about the rest of the doc.


3. **Read the handoff in full — the copy the `handoff-read:` line named**, not reflexively the one in
   the working tree — **but treat its "Open investigations" section as RECALL, not live state.**

   🔴 **A handoff's open-investigation block is exactly as stale-able as an index bullet, and nothing marks it.** The status header is obviously dated; a mid-diagnosis block reads as current forever, because it is written in the present tense by someone who was mid-diagnosis. MEASURED 2026-08-19: a doc's leading hypothesis for an intermittent CI failure was **superseded one day after the doc was written** — root-caused, with a classifier, tests and a PR-comment integration already shipped in the same repo — and a session re-derived the retracted hypothesis, refuted a variant of it, measured a failure rate, and was about to build a capture mechanism **that already existed**.

   MEASURED AGAIN 2026-08-20, and the block was *well* written — values, eliminations, a named "Next probe": every ruled-out candidate was still true, yet the **framing** was wrong. It reported the unattributed rows as an unidentified live producer growing at ~21/h; they were the repo's own test suite, 100% synthetic. A session that trusted the framing would have hunted a caller that does not exist. 🔴 **Eliminations age well; the question they serve does not** — so re-ask what the block is trying to explain before adopting its hypothesis, and prefer the block's own *values* over its narrative.

   **Before working any open item, check whether the repo moved under it:**
   ```bash
   git -C <repo> log --since=<doc-date> --oneline -- <the pipeline/script/dir the item is about>
   ```
   A hit means read those commits before re-deriving anything. The cost is one command; the cost of skipping it is a whole session.

   **Whether another doc already ruled this item out is answered by step 4, which runs the corpus query unconditionally.** No action here.
