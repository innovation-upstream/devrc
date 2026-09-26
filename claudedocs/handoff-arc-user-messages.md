# Handoff: arc-user-messages — 2026-09-26

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make it cheap to answer the operator's standing end-of-arc question — *"anything left
outstanding from this arc? Find all sessions associated with this handoff and check my
messages then determine if all addressed shipped and closed out"* — without hand-rolling
the archaeology each time. Two halves: **scope** an extraction to one arc's sessions, and
**route** it so an agent reaches for it unprompted.

- **closing-condition:** `check` — a session that receives that question reaches
  `extract_user_msgs.py --arc <slug>` from the tool output it is already reading, without
  a filesystem hunt for the script and without being told the tool exists. Measured the
  way the gap was measured: over the sessions that receive the question after the routing
  lands, count how many invoke the extractor. **Judgement over named evidence:** the
  operator reads that count.

## State now
- ✅ **Scope: SHIPPED.** `#1870` (`e48eebac`, merged 2026-09-25T22:48:19Z) gave
  `scripts/session-analysis/extract_user_msgs.py` three selectors — `--arc SEED`,
  repeatable `--session ID`, `--ids-file PATH` (`-` = stdin) — with markdown default and
  `--jsonl` canonical. The arc resolver is **imported, never re-implemented**: the four
  steps were extracted to `find-session.arc_report` and both consumers call it.
  Loud-empty exit codes separate *"the seed named nothing"* from *"the arc resolved with
  zero members"*, because a bare zero cannot tell those apart.
  - Measured before: 974 transcripts, 13,768 records, 54.1 MiB, 12 s, **no session id on
    any record** — so the obvious compose (enumerate via `--arc --json`, extract
    corpus-wide, grep the ids) could not be completed at all, there was nothing to grep on.
  - Measured after, on a 2-session arc: 27 records, 172 KiB — **322x smaller**.
- ✅ **Route: SHIPPED, then MEASURED NOT TO FIRE, then replaced.** #1870's routing was a
  "Load when" row in `/resume`'s SKILL.md pointing at
  `claude/skills/handoff/reference/user-messages.md`. Both halves deploy correctly
  (`readlink -f` → `/nix/store/…-devrc-claude-skills/`, switch ran 2026-09-25 21:41), so
  this was never a merged-≠-deployed miss. It still went 1-for-3 — see the next section.
  The deterministic replacement is the arc footer, below.

## The routing measurement — why a second mechanism was needed
🔴 **The prose route could only fire when `/resume` fired, and this question arrives at the
END of an arc.** Measured over the local Claude corpus (peer host NOT walked — see
UNMEASURED): the question appears in **254 sessions**, of which only **3** came after
#1870 merged.

| session | `/resume` ran | reference read | extractor used | `--arc` used |
|---|---|---|---|---|
| `d80f8849` | yes | **2x** | yes, immediately, no hunting | 3x |
| `4861069d` | yes | **0x** | yes — but re-found by `find $DEVRC/scripts -name 'extract_user_msgs*'` | 2x |
| `6ef53792` | **no** | 0x | **no** | 6x |

- `find-session --arc` fired in **3 of 3**. The reference file was read in **1 of 3**.
- `6ef53792` never invoked `/resume`, so the row was never in its context at all. That is
  the structural half: a row in one skill's body cannot route a question that arrives in
  sessions which never load that skill.
- `4861069d` had the row in context and still hunted the filesystem for the script.
- 🔴 **So the routing now rides the surface that fired every time.** `find-session.py --arc`
  prints the `extract_user_msgs.py --arc <doc>` line under every resolved arc
  (`extractor_next_command`), below the coverage line on purpose. Deterministic rather
  than prose, per `claude/RULES.md` → "Deterministic Over Prose".

## Gotchas measured here
- 🔴 **A doc created now does NOT retro-resolve its own arc.** `resolve_arc` reads
  `Claude-Session-Id:` trailers on commits **touching the doc**. This doc did not exist
  while the work happened, so `#1870`'s commit does not touch it, and
  `find-session.py --arc handoff-arc-user-messages.md` will report **this commit's writer
  only** — not the three sessions below. Their ids are recorded as prose here precisely
  because the resolver cannot see them, and back-dating a trailer would be a false
  authorship claim in a machine-read field. **Do not read a 1-member arc here as the
  history.**
- 🔴 **The extractor scopes the corpus; it does not answer the question.** Measured on
  `handoff-clawgate-to-muster-extraction` (7 sessions): rc 0, 199 messages, **967,441
  bytes**. That is 322x better than a corpus dump and still too large to read in one pass —
  `4861069d` dispatched a subagent to synthesise the ask ledger from it, which is a
  legitimate delegation, not a misuse. The extractor has four output flags and no
  status/answer mode. **Open question, not a defect:** the question asked 254 times is
  "what is outstanding", not "give me my messages". Whether an answering mode belongs in
  this tool is the operator's call — see NEXT.
- ⚠ The arc walk is `--claude-only` (a resume command is runtime-specific), so an
  extraction scoped to those sessions inherits that gap. `arc_report` states this once, in
  its unmeasured note; the footer deliberately does not restate it.

## The arc's own sessions — prose, because the resolver cannot see them
- `ses_f2925a2e4ffeS4sq0qg70iGC52` (opencode, devrc, 2026-09-25) — **originated.** Traced
  the handoff/resume infra and produced Rec 2 (the selectors) + Rec 3 (the routing).
  **Committed nothing:** its bash tool broke mid-session ("every bash call was
  schema-rejected before reaching the shell"), so it could not land this doc and said so
  explicitly in the same breath as its kickoff. Good disclosure; the findings then lived
  only in that transcript for a day.
- `785fb10c-16a3-4632-aa2f-910a21bf8b3d` (Claude, devrc, 2026-09-25 05:07Z→22:51Z) — built
  and merged **#1870**. Landed no handoff doc either.
- `4861069d-dbe7-4cc7-9bd4-ef9d2e5e471c` (Claude, homelab-talos) — first session to use the
  shipped tool in anger; the source of the routing measurement above.

## NEXT — ranked
1. **Let the routing measurement mature, then read it.** The footer is one PR old; the 3
   post-merge sessions are a count, not a rate. Re-run the measurement once ~10 more
   sessions have received the question. **Closing condition:** the operator reads the
   count. 🔴 Do not re-tune the routing off n=3.
2. **Decide the answering mode** (the open question above). Options seen so far: leave it
   (subagent synthesis is working), or add a mode that emits ask-shaped rows rather than
   raw messages. **Operator's call — do not build it unasked.**

## UNMEASURED — do not report these as absences
- **The peer host (laptop) was not walked** for the 254-session count or the 3-session
  split. Both are local-Claude-corpus figures.
- **The opencode corpus is excluded** from arc reader resolution by design.
- The `1 of 3` reference-read figure counts a transcript mentioning `user-messages.md`. A
  session that had the row in context and reasoned from it without opening the file would
  read as a miss here.
