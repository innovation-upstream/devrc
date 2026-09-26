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
- ✅ **Scope: SHIPPED** in `#1870` (`e48eebac`) — `--arc` / `--session` / `--ids-file`, the
  resolver imported rather than re-spelled, loud-empty exit codes. 322x smaller on a
  2-session arc (27 records, 172 KiB, against 13,768 records / 54.1 MiB corpus-wide).
- 🔶 **Route: `#1883` OPEN, not merged.** `render_arc` prints the extractor command under
  every resolved arc. Round 0 of the audit ladder ran before the merge decision and
  questioned the requirement; its findings are applied:
  - **`next_command` DROPPED from `--json`** — measured reach **0 of 6**. Five of the six
    sessions called `--arc --json` and each parsed `members` and discarded the rest, so the
    field never entered an agent's context. It shipped on a hypothesised caller that does
    not exist. Re-add it when one is named.
  - **The seed guard now crosses the seam it claimed.** It asserted
    `arc_seed_to_doc(doc) == doc` without importing the extractor, so it was blind to the
    only way that seam breaks. It now loads `extract_user_msgs` through the extractor's own
    loader and asserts the resolver is SHARED. It was also **the one guard with no mutant**;
    `F8` now covers it.
  - **"Two seam guards" was wrong — there is ONE**, plus the path-existence check.
  - Guards: **7** (was 8; the JSON test went with its field). Battery: **8 mutants**, C0
    re-pointed because the field its old anchor named is gone.
- ⚠ **Five routes now point at this one tool**, not two: the `/resume` row, the reference
  doc, a cairn task board entry, this footer, and `find-session`'s own SKILL.md. `RULES.md`
  "One rule, one place" points the other way; not resolved here, recorded.

## The routing measurement — why a second mechanism was needed
🔴 **SUPERSEDED 2026-09-26 — EVERY NUMBER AND THE MECHANISM IN THIS SECTION WERE WRONG.
Read `## The routing measurement — RE-DERIVED, and the mechanism RETRACTED` below instead.**

Nothing from this section survives except the bare fact that a routing gap exists. Briefly,
so a reader who lands here does not go looking:

- `3` post-merge sessions was really **6**; `1 of 3` reference reads was **3 of 6**;
  `2 of 3` extractor uses was **5 of 6**. The counts were stale within minutes of being
  taken — two of the six sessions landed 3 and 11 minutes after this arc's first commit.
- The mechanism it asserted — *"the prose route could only fire when `/resume` fired, and
  this question arrives at the END of an arc"* — is **retracted**, along with two later
  candidates. All three are listed in the new section. The cause is UNKNOWN.

⚠ **This block was left asserting the old reading for one commit**, because the corrected
section was written under a DIFFERENT heading, so the write gate appended it instead of
replacing this one — exactly the failure `handoff/reference/supersede.md` documents
("append preserves the old block VERBATIM… a reader meets whichever comes first in the
file"). Caught by a tree-wide sweep for the retracted string, not by reading the doc. The
gate's section-delta keys on the heading, so the heading above cannot be struck through
through the gate; this body is the retirement.

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
1. **Investigate the 352-of-617 gap** (its own section above). Do not fix it on a
   mechanism — find a distinguishing signal first.
2. **Re-read the reach comparison at n≥10.** Already reachable: 6 post-merge sessions
   existed by 2026-09-26 04:42Z and the population grows ~1/h under active work, ~5/day at
   baseline. **Closing condition:** the operator reads the count. 🔴 Do not re-tune off n=6.
3. **Decide the answering mode.** One 7-session arc extracts to 967,441 bytes / 199
   messages, so synthesis is still a subagent's job. Options: leave it, or emit ask-shaped
   rows. **Operator's call — do not build it unasked.**

## UNMEASURED — do not report these as absences
- **The peer host (laptop) was never walked** for any figure in this doc.
- **The opencode corpus is excluded** from arc reader resolution by design.
- The reference-read count (3 of 6) counts a transcript mentioning `user-messages.md`. A
  session that had the row in context and reasoned from it without opening the file reads as
  a miss.
- **Neither `nix build` sandbox tier was run locally** on `#1883`. A `gate.sh --tier all`
  run was launched; if this doc does not record its verdict, it did not finish.
- The `8/8 KILLED` battery result, the `160 passed` figure and the 967,441-byte probe were
  verified for STRUCTURE by round 0, not re-run by it.
## Verification of #1883 — what was and was NOT run
- ✅ **Mutation battery `mutation_battery_arc_extractor_footer.py`: 8/8 KILLED, positive
  control fired.** It paid for itself on its first run: C0 — dropping `"next_command"` from
  `run_arc`'s JSON dict — scored **SURVIVED**, because
  `test_the_JSON_carries_the_command_and_NULL_when_there_is_none` asserted against
  `extractor_next_command(...)` directly and never touched the JSON path. Its NAME claimed a
  relationship its BODY did not check (the `guards-narrower` shape). The test now goes
  through `run_arc` and parses the output. Registered two-way in
  `test_mutation_battery_anchors.py`.
- ✅ **160 passed** across `test_find_session_arc.py`, `test_find_session_skill_contract.py`,
  `test_find_session_skill_cli.py`; **38 passed / 4 skipped** on the anchor ledger.
- ✅ **Live positive control:** `--arc handoff-clawgate-to-muster-extraction.md` renders the
  footer, and the exact command it printed runs at **rc 0** — 7 sessions, 199 messages,
  967,441 bytes. The claim that the printed line WORKS is measured, not inferred.
- ⏳ **`scripts/scoped-tests.sh` — IN FLIGHT, NO VERDICT YET.** Its first run reported
  `RESULT: FAIL (exit=3)` having run **nothing**: `logrotate`/`dash` missing from PATH,
  because a fresh worktree copies `.envrc` (`use opencode`) which carries no gate toolchain.
  🔴 **The background wrapper reported exit 0 over that failure** — the runner's own
  `RESULT:` line is what said otherwise. Re-running under `nix develop`; it selected 6 of
  368 files, a superset of the three modules already green plus `test_find_session_live.py`,
  `test_transcript_search.py` and `check-clickup-addressed/tests/test_shared_walk.py`.
  **Those three have NOT been observed green — do not report this change as fully gated.**
- ❌ **Neither `nix build` sandbox tier was run locally.** The dev-host tier and the sandbox
  tier are blind to different things; Tekton runs the sandbox one.
- ⚠ **The handoff write gate reported `leakscan: NO SCANNER FOUND`** — a pass by absence,
  not a clean result. Checked by hand instead: no IPs, URLs, tokens, real media paths or
  client names in this doc.
## The routing measurement — RE-DERIVED, and the mechanism RETRACTED
🔴 **Both the original justification and its first replacement were wrong. Read this section
before quoting any number from this doc's history.** The surviving claim is about two
surfaces' REACH and deliberately says nothing about *why* the narrower one misses.

**Re-derived 2026-09-26 by round 0 of the audit ladder, then independently reproduced:**

| claim as shipped | re-derived | verdict |
|---|---|---|
| question appears in **254** sessions | 259 with a user-typed occurrence; 285 with the phrase anywhere | ballpark, immaterial |
| **3** post-merge sessions | **6** | ❌ doubled |
| reference read **1 of 3** | **3 of 6** | ❌ |
| extractor used **2 of 3** | **5 of 6** | ❌ |
| `--arc` used **3 of 3** | **6 of 6** | ✅ |

Two of those six landed **3 and 11 minutes after** this arc's first commit, and a sixth
during the audit. `n=3` was honest when taken and stale before the branch was pushed.
Arrival rate ≈ **1/h** under active work, ≈ 0.22/h at baseline — so `n=10` was **~4 hours**
away, not the "~10 more sessions" horizon this doc's NEXT #1 used to imply. 🔴 **The PR body
said "do not re-tune the routing off n=3" and then built off n=3.** Recorded because the
disclaimer was doing decoration work, not decision work.

**What survives as the justification:** `find-session --arc` reached **6 of 6** of those
sessions; the `/resume` reference row reached **3 of 6**. The footer rides the wider surface.
That is a reach comparison at n=6 — a COUNT, not a rate — and it needs no mechanism.

### 🔴 RETRACTED MECHANISMS — three, do not derive a fourth
1. ~~"The question arrives at the END of an arc, in sessions that never ran `/resume`."~~
   Built on ONE session (`6ef53792`). That session's first message **is** an indented
   `/resume` kickoff paste, so it *was* invoked.
2. ~~"The kickoff paste does not expand — 0 of 617."~~ **True by construction.** The
   population is "first user message starts with `/resume`"; a session where the command
   actually fires opens with `<command-message>` instead, so the filter excludes every
   expanded case. The zero is a selection effect, not evidence.
3. ~~"Indentation prevents expansion."~~ **Refuted by its own control.** All **4**
   flush-left pastes equally failed to expand, while several *indented* ones do carry a
   resume `<command-name>`. De-indenting the kickoff block would fix nothing.

🔴 **An absence is the observable the most causes share** (`claude/RULES.md`, "an EMPTY
RESULT cannot distinguish two mechanisms"). Three mechanisms for this one absence have been
asserted and withdrawn. If you are reaching for a fourth, you are the fourth — name the
upstream signal that would distinguish it first, or write that there is none.

## The 352-of-617 gap — SPLIT OUT, not fixed here
🔴 **MEASURED and unexplained: of 617 sessions opening with a `/resume` kickoff paste, 352
(57%) never load `/resume`'s SKILL.md body at all.** Independently reproduced twice with
different markers (audit: ≥351; this session: 352).

⚠ **The first probe for this was wired to nothing** and is worth recording: it searched for
the resume skill's `description`, which ships in the always-on skill LISTING in *every*
session, so it matched 100% of transcripts and "proved" there was no gap. A body-only marker
is what produced 352. Any re-measurement must use a string absent from the frontmatter.

- **This is NOT this arc's work and must not be fixed on a guess** — all three candidate
  mechanisms above are retracted, so the cause is genuinely unknown.
- **Closing condition:** a named upstream signal that distinguishes at least two candidate
  mechanisms is identified and measured, OR the operator reads the measurement and closes it
  as won't-fix. Mechanical half: `<0.57` on a re-run of the same probe after any change.
- **Blast radius if real:** every route that rides `/resume`'s body, not just this one row —
  which is why it is worth its own investigation rather than a patch here.
