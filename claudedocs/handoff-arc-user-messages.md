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
- 🔴 **SHIPPED AND VERIFIED.** `#1883` squash-merged as **`c0fd28e3`** (2026-09-26T16:23:53Z),
  branch deleted. Merge confirmed **by CONTENT** — the footer, this doc and the battery are all
  on `origin/main` — never by ancestry, because a squash is never an ancestor of its base.
- **CI was green on the exact merged head `1b87ad81`**, all four legs, sandbox tier:
  `pytests` 24,257 passed / 0 failed, `nodetests` 1,720, `gotests` 461, `cairn-client-runs`.
- **Deployed to BOTH hosts** via `scripts/ship.sh` (rc 0). Every per-host line read, not the
  verdict: workbench `63361f48 → c0fd28e3`, 624 artifacts resolve / **0 dangling**, 442
  repo-sourced / **0 stale**; laptop the same sha, 590 resolve / 0 dangling, 437 / 0 stale.
  Cross-host agreement asserted — both at `c0fd28e3`. Neither host skipped.
- ✅ **VERIFIED AGAINST THE SYMPTOM, not the rollout.** `readlink -f
  ~/.claude/skills/find-session/SKILL.md` → `/nix/store/pz6bbghd…-devrc-claude-skills/…` — a
  NEW store path (was `jappawqv…`), so the switch genuinely swapped the `home.file` copy — and
  it carries the routing line. The live tool on a real arc printed `NEXT — the operator's own
  messages across 2 sessions of this arc:` with the runnable command.
- ⚠ **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** again on
  2026-09-26T17:3xZ — 0 tasks for this session; its positive control fired (1 link for
  `bb1489f3`), so the board is reachable, but a WRONG id also answers 200 with an empty array.
  A real reading, **not** a clean bill of health.

### 2026-09-26, later session — the arc's own DoD is at n=0, and the split-out gap now HAS a discriminator
- 🔴 **The closing condition was RUN and is NOT met, because its population is EMPTY.** Counting
  sessions with a user-typed occurrence of the operator's end-of-arc question whose first
  occurrence postdates `c0fd28e3` (16:23:53Z): **1**, and that one is the measuring session
  itself (`77c0a3a7`), which matched only because it read this doc. **Real post-merge n = 0.**
  NEXT #2 stands verbatim; do not re-tune off n=0 any more than off n=6. (Pattern counted:
  `anything (left )?outstanding|sessions associated with this handoff|check my messages`,
  user-typed only — looser than the shipped one, hence 445 corpus-wide against 259. Different
  instrument, not a contradiction.)
- ✅ **The 352-of-617 gap is MEASURED to its discriminator** — see the new Open-investigations
  block, which RETIRES the `Leading hypothesis` and `Next probe` lines of the block above it.
  Headline: the body loads **iff the model elects to call the `Skill` tool**; every
  environmental signal upstream of that election is identical across the two buckets. The
  first half of that item's closing condition ("a named upstream signal separating ≥2 candidate
  mechanisms is measured") is met; the operator-reads-it half is not.
- ⚠ **Nothing was changed, so the gap's MECHANICAL half (`<0.57` on a re-run) is untested** and
  will stay untested until somebody changes something. 56% is the CURRENT reading, not a
  post-fix one.

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

- 🔴 **A SQUASH MERGE *does* carry every squashed commit's `Claude-Session-Id:` trailer, so a
  doc created mid-effort CAN resolve its own arc.** This doc asserted the opposite — that
  `--arc handoff-arc-user-messages.md` would report "this commit's writer only" because
  `resolve_arc` reads trailers on commits *touching the doc* and the doc did not exist while
  the work happened. MEASURED after the merge: it resolves **2** sessions, role-tagged —
  `785fb10c` RESUMED (built #1870) and `ad781c3f` ORIGINATED (`commits: c0fd28e3`) — with
  `0 of 1 commit(s) on this doc carry no session id`. The mechanism missed: GitHub's squash
  body concatenates every commit message, and `handoff_arc` scans the whole BODY for trailers
  rather than only git's final trailer block (which is exactly why that reader was written that
  way — see `test_gits_own_trailer_parser_MISSES_what_this_reader_finds`). **Still absent:** the
  opencode originator `ses_f2925a2e4ffeS4sq0qg70iGC52`, which committed nothing, and no
  mechanism can recover a session that never wrote a commit.
- 🔴 **The gate's own timeout is not a test failure, and it reads exactly like one.** The local
  `gate.sh --tier all` printed `GATE: RESULT=FAIL exit=1` with `pytest exit=124 (timeout after
  3600s)` / `RESULT: FAIL (exit=143)` = SIGTERM — the pytest tier was KILLED at its cap with
  three concurrent full suites from other checkouts on the box. That leg is **UNMEASURED**, not
  failed; its `node` and `go` tiers passed at `SCOPE: FULL`. CI's sandbox tier is what actually
  covered pytest.
- ⚠ **`| tail` ate an exit code again, in this very session**: `audit-dispatch.py --round 3 …
  | tail -6` printed `exit=0` for a run whose real status was **5** (the attribution gate
  firing). Re-run redirecting to a file and read `$?` — the trap is documented and was still
  hit.
- 🔴 **A `mutation_battery_*.py` copy placed in `scripts/tests/` to run a control will fail the
  two-way ledger** (`test_the_battery_ledger_names_every_python_instrument` globs
  `mutation_battery_*.py` and `mutants-*.py`). Name a throwaway control something matching
  neither, and delete it in the same command. Also: a battery resolves its own root from
  `__file__`, so a scratchpad copy cannot run — it must sit beside the real one.

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
1. **Land + close the `/resume`-body gap on the first half of its own closing condition.** The
   named upstream signal exists and is measured (block above); what remains is the operator
   reading it and deciding whether the "why does it elect not to" half stays open. Repo `devrc`;
   no code change is proposed and none should be made on a guess.
   **Closing condition:** the operator reads the measurement and either closes it, or names the
   one thing still wanted. 🔴 Do NOT re-derive the 622/349 — it is reproduced three times.
   forcing: none
2. **Read the reach comparison at n≥10.** The footer is live on both hosts as of `c0fd28e3`.
   Real post-merge receipts of the question: **0** as of 2026-09-26T17:1xZ. Population growth,
   carried forward so this item stays self-contained: **≈1/h under active work, ≈0.22/h
   (~5/day) at baseline**, and **6** PRE-merge sessions already existed by 2026-09-26 04:42Z
   (that 6 is the n the reach comparison was taken at — it is not a post-merge receipt).
   So n≥10 is hours away under load, ~a day idle. Re-run the count and compare extractor use
   before/after this deploy.
   **Closing condition:** the operator reads that count. 🔴 Do not re-tune the routing off n=6,
   and do not read n=0 as a failure of the routing.
   forcing: none
3. **Decide the answering mode.** One 7-session arc extracts to 967,441 bytes / 199 messages,
   so synthesis is still a subagent's job and the footer routes to a tool that cannot directly
   answer the question motivating it. Options: leave it, or emit ask-shaped rows.
   **Operator's call — do not build it unasked.**
   forcing: none

## UNMEASURED — do not report these as absences
- **The peer host (laptop) was never walked** for any figure in this doc.
- **The opencode corpus is excluded** from arc reader resolution by design.
- The reference-read count (3 of 6) counts a transcript mentioning `user-messages.md`. A
  session that had the row in context and reasoned from it without opening the file reads as
  a miss.
- **Neither `nix build` sandbox tier was run locally** on `#1883`, at any head.
- 🔴 **The local `gate.sh --tier all` DID finish and its pytest tier is UNMEASURED, not
  failed** — killed at its own 3600s cap (`exit=124`, `RESULT: FAIL (exit=143)` = SIGTERM)
  under three concurrent full suites from other checkouts. `GATE: RESULT=FAIL exit=1` is that
  timeout. Its **node** and **go** tiers passed at `SCOPE: FULL`. ⚠ This bullet used to say
  "if this doc does not record its verdict, it did not finish" — a conditional that was
  already false when the verdict landed in the section below.
- ⚠ **The battery and test counts that USED to sit in this bullet are gone on purpose.** It
  read "the `8/8 KILLED` battery result, the `160 passed` figure … were verified for STRUCTURE
  by round 0" — and both numbers were wrong (9 and 159), which the section below says
  explicitly. Round 2 found the stale pair **still standing two lines above its own
  correction**, inside that round's own diff hunk as unchanged context: the same
  meets-the-false-one-first defect the correction claimed to close, at 39 lines instead of 80.
  🔴 **So no count lives here any more.** Current figures live once, in
  `## Verification of #1883`, and a number quoted in two places is a number that will disagree
  with itself.
## Verification of #1883 — what was and was NOT run
🔴 **REWRITTEN 2026-09-26 after round 1. The previous version of this section carried three
wrong numbers and an account of `C0` that its own doc contradicted 80 lines earlier — read
this one.** Round 1 found them; they are listed at the bottom so the shape is recorded.

- ✅ **Mutation battery: 10 mutants (`C0`, `F1`–`F8`, `X1`), and it is now MULTI-FILE.**
  Round 1 measured that `F8` kills the seam guard's old extractor-blind form and its widened
  form *identically*, so a `9/9 KILLED` that read as "every guard's ability to go red is
  verified" vouched for the widening **not at all** — and the battery structurally could not,
  because it mutated `find-session.py` while every discriminating mutant lives in
  `extract_user_msgs.py`. It now declares `TARGETS`, keeps each target's pristine text, and
  asserts the restore **by digest** (a silently failed restore scores borrowed kills).
- ✅ **The seam guard is on its THIRD version, and v1 and v2 were both wrong.** v1 never
  imported the extractor. v2 added `assert "arc_seed_to_doc" in body` — a SPELLED guard that
  mutant `X1` walks straight past by leaving the name in a *comment* while deleting the call.
  v3 injects a recording stub through `arc_sessions`' own `find_session` parameter and asserts
  the call HAPPENED with the seed, so it pins a RELATIONSHIP rather than a word. v2's
  docstring also claimed the resolver was asserted "by IDENTITY" — false about the objects:
  `_load_find_session()` returns a third module instance.
- ✅ **Guards: 7** in `TestTheArcNamesTheExtractor`.
- ✅ **Live positive control:** the footer's printed command runs at rc 0 against a real arc,
  and round 1 independently confirmed the footer's session count matches the extractor's.
- ✅ **Merged tree MEASURED by round 1**, not assumed: the base moved to `63361f48`, one
  commit touching **none** of this PR's files; merged-tree runs were green across the modules
  that could reach it.
- ✅ **CI: all four Tekton legs passed** — but on `1b2bcb3e`, **before** the round-0 and
  round-1 fixes. Every later head needs its own run.
- 🔴 **The local `gate.sh --tier all` pytest tier is UNMEASURED, not failed.** It was
  **killed at its own 3600s cap** (`exit=124`, `RESULT: FAIL (exit=143)` = SIGTERM) with two
  other full suites from another checkout on the same box — the contention truncation
  `CLAUDE.md` documents, where the dev-host tier produces no verdict. `GATE: RESULT=FAIL
  exit=1` is that timeout, **not a failing test**. The **node** and **go** tiers both passed
  at `SCOPE: FULL`.
- ❌ **Neither `nix build` sandbox tier was run locally** at any head.

### What round 1 found wrong in the PREVIOUS version of this section
Recorded because all four are the same shape — a verification claim that reads as checked:
- "Battery: **8 mutants**" and "**8/8 KILLED**" — it was **9** at that head, and `8/8` was
  listed among figures explicitly said to have been *verified for structure*.
- "**160 passed**" — it was **159**. The delta is the JSON test the round-0 fix deleted,
  which the doc knew about elsewhere.
- The ✅ bullet still described `C0` as "dropping `next_command` from `run_arc`'s JSON dict"
  and the JSON test as "now goes through `run_arc`" — both deleted by then, and stated 80
  lines *after* the corrected account, so a reader scrolling here met the stale one first
  with no supersede marker. The routing section got a banner; this one did not.
- "asserts the resolver is SHARED" — see the seam-guard bullet above; there was no such
  assertion.

🔴 **The lesson this section is now the third instance of: a fix round's own PROSE is the
likeliest next finding.** Rounds 0 and 1 each found false claims written by the round before
it, none of them logic bugs. If you are writing a verification sentence here, the number in it
is a claim like any other — re-derive it at the head you are describing.
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
🔴 **MEASURED TO ITS DISCRIMINATOR 2026-09-26 — this section used to end "the cause is
genuinely unknown", and it is not unknown any more.** Of **622** sessions opening with a
`/resume` kickoff paste, **349 (56%)** never load `/resume`'s SKILL.md body — reproduced three
times (audit ≥351; 352; 349 over a wider corpus and a 3-marker union). **The body loads iff the
model elects to call the `Skill` tool:** `Skill(resume)` ⟺ `Launching skill: resume` ⟺ body
present, 255/255 with zero disagreements — and **every environmental signal upstream of that
election is identical across the two buckets.** Full values and controls in the
Open-investigations block below.

⚠ **The first probe for this was wired to nothing** and is still worth recording: it searched
the resume skill's `description`, which ships in the always-on skill LISTING in *every* session,
so it matched 100% of transcripts and "proved" there was no gap. **Any re-measurement must use a
string absent from the frontmatter — and present in every revision of the body.**

- **This is still NOT this arc's work**, and no fix should be made on a guess: what the model's
  election depends on is UNMEASURED, and the seven prompt-side features tested do not separate.
- **Closing condition — first half MET:** a named upstream signal separating ≥2 candidate
  mechanisms is measured (it separates "something upstream blocks the load", now refuted, from
  "the model does not call the tool", now confirmed). Remaining: the operator reads it.
  Mechanical half `<0.57` is **untested and will stay so until something is changed**.
- **Blast radius, now quantified:** a route riding `/resume`'s body reaches **41%** of
  kickoff-paste sessions. That is the number that justifies routing from tool output instead.
## Open investigations — live diagnosis state

### 57% of `/resume` kickoff-paste sessions never load `/resume`'s SKILL.md body — cause UNKNOWN
as-of: 2026-09-26
- **Symptom + exact repro:** a session opened by pasting the kickoff block `/handoff` emits
  proceeds without `/resume`'s body in context, so every route that lives in that body (the
  `user-messages.md` "Load when" row among them) is silently absent. Repro: open any session
  from a pasted kickoff block and grep the transcript for a body-only string.
- **Observed (with values):** over the local Claude corpus — **617** sessions whose first user
  message starts with `/resume`; **613** of them indented; **0** carrying
  `<command-name>resume</command-name>` within 6 records; **352 (57%)** with no `/resume` body
  marker anywhere in the transcript. Independently measured twice (audit round 0: ≥351; this
  session: 352) with *different* markers.
- **Ruled out:** ~~"the question arrives at the END of an arc, in sessions that never ran
  `/resume`"~~ — `6ef53792`'s first message IS an indented `/resume` paste, so it was invoked.
  `via: measurement`
- **Ruled out:** ~~"the paste never expands — 0 of 617"~~ — that zero is true BY CONSTRUCTION:
  the population filter is *"first user message starts with `/resume`"*, and a session where
  the command actually fires opens with `<command-message>` instead, so the filter excludes
  every expanded case. A selection effect. `via: measurement`
- **Ruled out:** ~~"indentation prevents expansion"~~ — all **4** flush-left pastes equally
  failed to expand, while several *indented* sessions do carry a resume `<command-name>`.
  `via: measurement`
- **Ruled out:** ~~"there is no gap; the body always loads"~~ — this was my FIRST probe and it
  was wired to nothing: it searched for the skill's `description`, which ships in the always-on
  skill LISTING in every session, so it matched 100% of transcripts. Any re-measurement must
  use a string absent from the frontmatter. `via: measurement`
- **Leading hypothesis:** none worth the name. Three mechanisms have been asserted and
  withdrawn. 🔴 **"I could not find a cause" is the finding here** — reaching for a fourth is
  what regenerated the error three times (`claude/RULES.md`, "an EMPTY RESULT cannot
  distinguish two mechanisms": name the upstream signal that would separate two candidates
  BEFORE proposing one).
- **Next probe:** compare, for the SAME operator-pasted block, one session where the body
  loaded against one where it did not, and diff what precedes the first user record — the
  discriminator must be an *upstream* signal (a system-reminder, a listing state, a settings
  difference), not another reading of the absence. Start:
  `python3 -c` over the 617 transcripts, bucketing by whether the body marker is present, then
  diff the pre-first-user records of one member of each bucket.

### RESOLVED to its discriminator — the `/resume` body loads iff the model calls the `Skill` tool; nothing upstream differs
as-of: 2026-09-26
🔴 **This block SUPERSEDES the `Leading hypothesis:` and `Next probe:` lines of
`### 57% of /resume kickoff-paste sessions never load /resume's SKILL.md body — cause UNKNOWN`
above.** That block's symptom, values and four eliminations all still stand; its
`Leading hypothesis: none worth the name` and its `Next probe` are **retired — the probe has
been run, and running it again is wasted work.** The heading itself could not be struck
through: `## Open investigations` is an APPEND heading and `handoff_doc.py` appends a same-named
block rather than replacing it, so this body is the retirement (`handoff/reference/supersede.md`
names exactly this failure — a reader meets whichever comes first in the file, which is the old
one).

- **Symptom + exact repro:** unchanged from the block above.
- **Observed (with values):** 977 Claude transcripts on the workbench; **622** whose first
  non-sidechain user message starts with `/resume` (was 617 — the corpus grew); **349 (56%)**
  carry no body marker. Third independent reproduction, this one over a 3-marker union.
  Three-way cross-tab over all 622, with **zero** disagreements —
  `A` = a `Skill` tool_use with `input.skill == "resume"`, `B` = the literal
  `Launching skill: resume`, `C` = a body marker:

  | A | B | C | sessions |
  |---|---|---|---|
  | yes | yes | yes | **255** |
  | no | no | yes | 18 |
  | no | no | no | **349** |

  `A and not B = 0`; `A and not C = 0`. **Whenever the tool was called it worked, 255/255 — there
  is no harness failure to find.** The **pre-first-user records are structurally identical**
  between buckets (the diff the old block asked for, run in aggregate rather than one-vs-one):
  `mode` 100%/100%, `permission-mode` 100%/100%, the three hook attachments 74%/74%,
  `bridge-session` 58%/63%, mean pre-records **5.53 vs 5.57**; `version` `2.1.232` 100%/99%,
  `entrypoint` `cli` 100%/100%, `permissionMode` `default` ~100%, model `claude-opus-5`
  100%/100%. **The divergence starts at the model's first tool call:** fired ⇒ 68%
  `Skill(resume)` / 28% `Read`; never-fired ⇒ **0%** `Skill` / **82%** `Read` / 17% `Bash`.
  The 18 body-without-tool sessions all reference the resume `SKILL.md` path explicitly, 6 of
  them via `Read`/`Bash`/`Agent` on it — they fetched the body BY HAND, the same signature as
  the two sessions that `find`-ed the extractor off the filesystem. `6ef53792`, the single
  session retracted mechanism #1 was built on, is one of the 18.
- **Ruled out:** ~~"the marker is younger than the bodies it is testing, so an older body scores
  as a miss"~~ — all three body-only markers (`rebuild context fast`, the `# /resume — …` H1,
  `Then wait for direction`) are present in **38 of 38** revisions of the body since
  2026-06-30, `claude/commands/resume.md` era included. Positive control: a HEAD-only string
  (`cairn recall`) was correctly reported ABSENT from `5dd2a644`, so the check can say no.
  `via: measurement`
- **Ruled out:** ~~the whole CLASS "something upstream of the model prevents the skill from
  loading"~~ — which is where all three previously-retracted mechanisms live. Refuted
  POSITIVELY, not by another absence: every environmental record is identical across buckets,
  and 255/255 invocations succeeded. Also 2 of 622 DO carry
  `<command-name>resume</command-name>` and both loaded the body, so expansion works — it is
  merely rare in a population defined to exclude it. `via: measurement`
- **Ruled out:** ~~"a prompt-side feature of the kickoff block decides it"~~ — fire-rate sits at
  **39–53%** across every split tested: block length, whether the block names a next action,
  "read first"/"canonical handoff" wording, an absolute doc path, a `do NOT` imperative, a 🔴.
  Nothing separates. `via: measurement`
- **Leading hypothesis:** the election itself. A route living in a skill BODY reaches **41%**
  (255/622) of kickoff-paste sessions, because the body is downstream of a choice the model
  makes with no environmental input that differs. **This is a measurement of #1883's premise,
  not a new mechanism** — and it is where the evidence stops. Why the model elects not to is
  UNMEASURED; the seven features above are ruled out and an eighth is not.
- **Next probe:** none for the mechanism — 🔴 **do not open a fourth.** The remaining probe is
  the operator reading this. If a probe IS wanted, the only honest one left is prospective: log
  the election (fired / not) per kickoff session going forward and see whether it correlates
  with anything not already tested, rather than mining the same 622.

## Defects (batched)
- 🔴 **The squash subject on `main` permanently asserts the retracted claim**:
  `feat(find-session): a resolved arc names the extractor, because the prose route fired 1 of 3
  (#1883)`. GitHub took the first commit's subject; "1 of 3" was re-derived to 3 of 6, and "the
  prose route fired" rests on a mechanism retracted three times. Not editable without rewriting
  a shared `main`, so it stands. The retraction is in the code docstring, the test docstring,
  this doc and two PR comments — a reader following the title into the code meets it at once,
  but `git log --oneline` will keep asserting the false version.
- ⚠ **This doc predicted the arc would resolve to ONE session, and it resolves to TWO** — see
  the Gotchas entry. The prediction is corrected there rather than deleted, because the reason
  it was wrong is the reusable part.

## How to verify
```bash
# 1. the footer renders on a real arc, from the DEPLOYED path
python3 $DEVRC/scripts/find-session.py --arc handoff-arc-user-messages.md   # expect a `NEXT —` block
# 2. the command it prints actually runs (the positive control that matters)
python3 $DEVRC/scripts/session-analysis/extract_user_msgs.py --arc handoff-arc-user-messages.md >/dev/null; echo "rc=$?"
# 3. the guards, and the battery whose no-op control must SURVIVE
nix develop $DEVRC -c python3 -m pytest $DEVRC/scripts/tests/test_find_session_arc.py -q -k ArcNamesTheExtractor
PYTHONDONTWRITEBYTECODE=1 python3 $DEVRC/scripts/tests/mutation_battery_arc_extractor_footer.py  # expect 10/10 + C0 KILLED
# 4. both hosts carry it
bash $DEVRC/scripts/drift-check.sh   # read every per-host line, not the verdict
```

**Rebuilding the body-load measurement** (no script is committed for it; the probes lived in a
session scratchpad and are gone). Over `~/.claude/projects/*/*.jsonl`:
1. **Population** — first record with `type=="user"`, falsy `isSidechain`, non-empty text;
   keep the session iff `text.lstrip().startswith("/resume")`. Expect ~622 and rising.
2. **Bucket** — `C` = any of the three body-only markers above appears anywhere in the file.
   🔴 A marker must be absent from the SKILL.md **frontmatter** (the `description` ships in the
   always-on skill LISTING in every session and matches 100% of transcripts) **and** present in
   every revision of the body — check the second with `git show <rev>:<path>` over
   `git log -- claude/skills/resume/SKILL.md claude/commands/resume.md`, plus a HEAD-only
   control string that must come back absent.
3. **Discriminator** — `A` = a `Skill` tool_use with `input.skill == "resume"`;
   `B` = the literal `Launching skill: resume`. Cross-tab `A × B × C`; expect `A⟺B⟺C|A` with
   zero disagreements.
4. **Upstream diff** — bucket by `B`, then compare the records BEFORE the first user message in
   aggregate (`type`, and `attachment.type` for attachments), plus `version` / `entrypoint` /
   `permissionMode` / first-assistant `message.model`. Expect them identical; that identity is
   the finding.
🔴 **zsh has no word-splitting** — `for rev in $revs` over a newline-joined string loops ONCE
and every per-revision check reports a vacuous clean. Hit in this session; write these in
Python, or use `${=revs}`.
