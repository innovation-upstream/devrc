---
# No clawgate-task field — exit 5, nothing resolved for this session.
---
# Handoff: handoff-resume-skill-trace — 2026-09-12

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. Non-blocking: if it exits
non-zero, print the stderr line and carry on.

## Goal
Trace the handoff and resume skills end-to-end: their flows, cross-references,
shared infrastructure, and usage patterns. No code changes — pure analysis.

## State now
- Base clone sits on `main`; all work landed via PRs from worktrees. Both hosts converged
  and VERIFIED at `bf72e04e` (`ship.sh`: 594/553 managed artifacts resolve, 0 dangling,
  0 stale, cross-host agreement COMPARED).
- 🔴 **`main` IS RED on two tests, NEITHER of them this session's work.** Measured against
  `origin/main` in a detached worktree, not inferred:
  - `test_no_handoff_doc_exceeds_its_budget` — `claudedocs/handoff-index-store-claims-accuracy.md`
    is 67,213 B, over the 65,536 B ceiling by 1,677 B, and **not in the grandfather ledger**.
  - `test_the_skill_did_not_grow` — `claude/skills/clawgate/SKILL.md` is 18,421 B against a
    15,665 B ceiling, 2,756 B over. From `#1615` / `05f42bd1`.
  Both remedies are "evict content from a document somebody else wrote", which is why this
  session did not do them. **The 4-hourly main-green deadman will fire on both.**
- **Seven PRs merged, shipped and verified by content this session:**
  - `#1606` `44d8847d` — the zero-laptop-contribution claim REFUTED; the source doc's
    residual marked EXPIRED.
  - `#1608` `7e000e6b` — `q_browser_by_domain` quadratic `CROSS JOIN` → linear boundary
    sweep; `activity-scan.py --days 30` completes again.
  - `#1614` `e6a5ee0d` — `activity-scan.py` degrades ONE section on `CHQueryError`; exit
    3 = PARTIAL.
  - `#1618` `16bfd2cb` — open-investigation blocks carry a machine-visible AGE;
    `resume-state.sh` prints `INVESTIGATIONS` and files `EXPIRED` past 14 days.
  - `#1623` `e73c84ee` — this doc's previous update.
  - `#1627` `6fa3e13f` — 35 docs archived to `claudedocs/archive/` + a test-enforced
    per-document byte ceiling (`MAX_BYTES = 65_536`, quantised grandfather ledger).
  - `#1634` `30bad70c`, `#1644` `bf72e04e` — the two regressions #1627 caused (below).
- 🔴 NO `clawgate-task:` field. `clawgate_handoff.sh resolve` exited **6**: one task linked
  (`#371`, role=`read`, Cairn phase 3) and NONE worked. Reading a task is not doing its
  work, so this doc belongs to none of them. That is not a statement that the board is fine.

## Open investigations — live diagnosis state

### Resume skill: zero laptop contribution — CLOSED, REFUTED 2026-09-12
🔴 **REFUTED**, re-measured directly against ClickHouse `activity.events` on 2026-09-12. The
claim below is wrong in **two independent ways** — a wrong number, and a fused-scopes error
that produced the wrong number. Kept below as originally written, not silently rewritten.

- **Symptom + exact repro (as originally claimed):** `handoff-search-index.md` reports 0
  `/resume` runs from the laptop during the 6-day measurement window (284 sessions, two
  hosts). The workbench accounts for all 85 attributed sessions.
- **Observed (with values) (as originally claimed):** skill-usage-audit.md line 105:
  `resume | A | 85 | 80 | 5`. The `commands_typed` column (5) suggests minimal manual
  invocation. The laptop gap may be a measurement artifact (short window) or a real
  behavioral pattern (workbench is the primary dev host).
- 🔴 **Error 1 — wrong number at the stated scope.** Measured 2026-09-12: 8-12 laptop resume
  sessions in the skill-usage-audit's own 6-day window, depending on where the boundary
  falls (12 for a 2026-08-29 start, 9 for 08-30, 8 for 08-31, all ending 2026-09-05 00:00
  UTC) — every day from 2026-08-28 through 2026-09-04 has at least one laptop resume
  session. Never zero.
- 🔴 **Error 2 — fused scopes: the two halves of the original claim come from different
  documents measuring different things, glued into an attribution neither doc makes.** The
  `resume | A | 85 | 80 | 5` row is `claudedocs/handoff-skill-usage-audit.md` line 105, in a
  table that **has no host column at all** — it was never split by host, so "the workbench
  accounts for all 85 attributed sessions" is an inference this session invented, not
  something measured. The "0 laptop" figure instead comes from
  `claudedocs/handoff-handoff-search-index.md` (its line 120 and lines ~263-281), which
  measured a **different quantity** (`resume-state.sh` shell invocations found by grepping
  transcripts, not the skill-usage-audit's attributed/invoked/typed ClickHouse fields), over
  a **different window** (since PR #1332 merged, 2026-09-06T17:51:19Z, ~8.6 h — not the
  6-day window this block cites), with a **different instrument** (ssh + transcript grep,
  not ClickHouse). Gluing the 85-row table to the "0 laptop" figure produced a claim neither
  source document makes.
- **Ruled out:** that the instrument is blind to the laptop — 76 laptop sessions carry
  non-empty skill fields in the last 30 days, and laptop `source='claude'` rows are current
  (19,200 rows, latest 2026-09-12 18:48 UTC). via: measurement, ClickHouse `activity.events`,
  2026-09-12
- 🔴 **Leading hypothesis, also refuted.** "The laptop is used for lighter tasks" does not
  survive a behavioural check: `activity-scan.py --days 14`, `host='laptop'` (its i3/attention
  sections are laptop-scoped by default) — 4,013.6 min (~67 h) Alacritty attention, 75
  deep-work blocks >=10 min, 34 >=25 min in the trailing 14 days. The laptop is a primary
  machine, not a light-task machine.
- **Measured for the record (ClickHouse `activity.events`, 2026-09-12, `source='claude'` +
  `kind='session-summary'` rows, distinct sessions = union of `skills_used` /
  `skills_invoked` / `commands_typed`):**
  - Laptop `/resume`, trailing 30 days: **17 sessions** (15 via `skills_invoked`, 2 via
    `commands_typed`). Workbench: **158**. Laptop total Claude sessions 161, workbench 976 —
    resume rates 10.6% (laptop) vs 16.2% (workbench). Lower than the workbench, not zero.
  - Laptop 30-day skill leaderboard (sessions): handoff 51, audit-pr 43, subsystem-index 41,
    resume 17, login 10, signal 9, obs-read 7, clawgate 5.
- **Next probe:** none — closed. The `--days 30` probe this block originally proposed could
  not be run; see "Next steps" rank 1 for why and for the in-flight fix.

### `source='tool'` telemetry: one CONFIRMED emission failure, mechanism unexplained
- as-of: 2026-09-12
- **Symptom + exact repro:** on 2026-09-05 the laptop ran `obs-read` repeatedly and NOT ONE
  `source='tool'` row reached `activity.events`. Reproduce the evidence with reader creds
  (`activity` skill has the SOPS recipe; `--input-type yaml` is required for the process
  substitution):
  ```sql
  SELECT JSONExtractInt(JSONExtractRaw(payload,'skills_used'),'obs-read')
  FROM activity.events WHERE source='claude' AND kind='session-summary'
    AND host='laptop' AND session='ba488ff1-97a3-4c3b-9095-759ca535928c';        -- 24
  SELECT count() FROM activity.events WHERE host='laptop' AND source='tool'
    AND ts>='2026-09-05 00:00:00' AND ts<'2026-09-06 00:00:00';                  -- 0
  ```
- **Observed (with values):** `skills_used={"obs-read":24}` for that session, from the
  transcript tailer — a code path completely separate from `emit_invocation`, documented
  and independently confirmed to only ever UNDERCOUNT, never fabricate. Laptop `tool` rows
  that day: **0**. Workbench the same day: **236**. Transcript exists at
  `~/.claude/projects/-home-zach-workspace-scratch-vetr/ba488ff1….jsonl` on the laptop,
  `entrypoint:"cli"`, real `SessionStart` hook fired, real Prometheus data returned.
- **Ruled out:** the instrument is blind to the laptop — 76 laptop sessions carry non-empty
  skill fields in 30 days and laptop `source='claude'` rows are current (19,200 rows).
  via: measurement
- **Ruled out:** the whole `tool` pipeline was down that day — the workbench emitted 236
  rows over the same window. via: measurement
- **Ruled out:** transport/shipping — `activity-collector` on the laptop is `active`,
  shipping every ~10s with `0 malformed dropped`, spool 8 KB / 1 segment.
  via: command (`systemctl --user`, `journalctl --user -u activity-collector`)
- **Ruled out:** host collision (both hosts are hostname `nixos`) — `collector.py:190-191`
  force-overwrites `host` from `ACTIVITY_HOST` at ingest, so it cannot produce a
  missing-day pattern. via: code
- **Ruled out:** SIGPIPE from `obs-read … | head -N` — `_emit_adoption()` fires at
  `scripts/obs-read:1087`, BEFORE `print(stdout)` at `:1094`, so telemetry is spooled
  before any pipe-truncation risk. via: code
- **Ruled out:** a code regression in the window — `git log` on `invocation.py`,
  `keylog/spool_emit.py`, `collector.py` and `obs-read` shows zero commits between
  2026-08-25 and 2026-09-08. via: command
- 🔴 **CORRECTED framing (an earlier reading of this was wrong):** `source='tool'` is NOT
  emitted by a Claude Code Pre/PostToolUse hook. It is self-instrumentation in ~6 scripts
  that each emit once per own invocation — `obs-read` is ~95% of volume on both hosts, plus
  `opencode-dispatch`, `mention-open.py`, `verify-agent-work`,
  `task-spec-drafter/ticket-status`, `playwright-nixos`. So most "whole days at zero" are
  simply "none of those six ran", NOT a defect. via: code (`git grep -l emit_invocation`)
- 🔴 **CORRECTED number:** an earlier note listed laptop `tool` = 0 on 2026-09-02. It is
  **54**, all in a 19:01:44–19:07:45 burst. The zero was an artifact of a
  `now() - INTERVAL 10 DAY` window whose FIRST BUCKET WAS A PARTIAL DAY (opened ~19:30Z);
  all 54 fell before it. The other zero-days (09-03, 09-04, 09-05, 09-09, 09-11) are full
  days in that window and stand. via: measurement
- 🔴 **CORRECTED, and it was stale rather than wrong:** `deadman.py` no longer reports
  `laptop`/`tool` as `skip:insufficient-baseline` — live it is `ok` (baseline 29, budget
  32.6 active-hours, silent 0.1h). The earlier claim came from a 2026-09-04 doc.
  via: command (read-only `deadman.py` run)
- **Leading hypothesis:** none worth defending. `emit_invocation`/`_emit_adoption` swallow
  every exception by design (`except Exception: pass` — best-effort telemetry must never
  break its caller), so the failure left NO trace anywhere. The laptop's devrc checkout was
  fast-forwarding roughly hourly that day (`git reflog`), so a `git merge --ff-only`
  landing mid-`import` is a candidate — UNCONFIRMED and not reproduced. For 09-03, 09-04,
  09-09 and 09-11 there is no `skills_used` evidence either way, so nothing is diagnosed
  for those days at all.
- **Next probe:** build the watcher in "Next steps" rank 2 and run it over history — it
  turns this from a one-off anecdote into a detector, and the 09-05 case is a REAL
  positive-control fixture rather than a synthetic one. Diagnosing the 09-05 mechanism
  directly is likely a dead end: the swallowed exception is by design and left no artifact.
- ⚠ **Structural gap, independent of the cause:** deadman's silence-budget model is the
  wrong SHAPE for this source. Its budget for the pair is 32.6 ACTIVE hours, so a bounded
  same-day failure bracketed by good days can never trip it, whatever state label the pair
  carries. Nothing on either host can currently catch this class.

### Does the new per-document ceiling redden `main` on the ORDINARY path?
- as-of: 2026-09-13
- **Symptom + exact repro:** `#1627` added `scripts/tests/test_handoff_doc_size.py`, a
  65,536 B per-document ceiling with an enumerated grandfather ledger. Within hours of it
  landing, `main` went red twice on it — once on a grandfathered doc crossing its quantum,
  once on a doc crossing the ceiling for the first time. Reproduce:
  ```bash
  git -C ~/workspace/devrc worktree add -f /tmp/mchk --detach origin/main
  nix develop ~/workspace/devrc -c python3 -m pytest /tmp/mchk/scripts/tests/test_handoff_doc_size.py -q -k budget
  ```
- **Observed (with values):** measured 2026-09-13 over all tracked
  `claudedocs/**/handoff-*.md` at `origin/main` — **13 documents over the 65,536 B ceiling,
  5 more within 8 KiB BELOW it** (63,158 / 62,117 / 59,007 / 58,054 / 57,526 B), 1 more
  within 8–16 KiB. `/handoff` MANDATES append-verbatim for Gotchas and Open investigations,
  so those 5 grow monotonically toward the ceiling.
- **Ruled out:** that this is the "11 red on day one" failure the gate's own docstring
  guards against — it was green at HEAD when it landed, by construction (11 enumerated
  entries). This is a different shape: a trickle, not a wall. via: measurement
- **Ruled out:** that the ledger's quantisation absorbs it — `GRANDFATHER_STEP` (16,384 B)
  only applies to docs ALREADY in the ledger. A doc crossing 65,536 B for the FIRST time
  has no entry and goes straight to red. That is exactly what
  `handoff-index-store-claims-accuracy.md` did. via: code + measurement
- **Leading hypothesis:** the pressure is intended, but it arrives POST-merge rather than
  pre-merge, because the doc that crosses is usually edited by a PR whose own CI ran before
  the gate existed or whose red was read as inherited. The gate's own docstring warns that
  "a permanently-red gate is worse than no gate"; this is that failure reached from a
  different direction — not permanently red, but red often, on the ordinary path.
- **Next probe:** decide, do not measure further. Either (a) accept the trickle and treat a
  ceiling breach as normal pre-merge work, (b) add a `MIN_HEADROOM_BYTES` warning band —
  which the module explicitly declined, with a stated reason about 119 documents, so
  re-read that argument before reversing it, or (c) raise `MAX_BYTES` to p95 (95,922 B),
  which cuts the near-ceiling population but weakens the gate. Read the module's own
  "WHERE THE NUMBER COMES FROM" and "A DELIBERATE DEVIATION" sections first.

## Next steps (ranked)
<!-- Renumbered 2026-09-13: the previous rank 1 (archive+cap) shipped as #1627 and all
     claims against this doc were released, so no live claim points at these numbers. -->
1. **Unbreak `main`: the handoff-doc ceiling.** `claudedocs/handoff-index-store-claims-accuracy.md`
   is 67,213 B, 1,677 B over, unlisted. The gate prints a remedy ladder — evict closed items,
   then demote dated evidence to `claudedocs/refs/<topic>.md`, then split, and only then add a
   ledger entry. Prefer eviction: 1,677 B is small and the doc is a handoff, so closed ranks
   and merged-PR plans are the likely payload. Touches that doc and possibly
   `scripts/tests/test_handoff_doc_size.py`.
   forcing: gate — `main` is red on this test right now; the main-green deadman fires 4-hourly
2. **Unbreak `main`: the clawgate skill ceiling.** `claude/skills/clawgate/SKILL.md` is
   18,421 B against 15,665 B. Its own failure message carries the history and the rule:
   "Any addition needs an eviction in the SAME commit." The addition came from `#1615`
   (`05f42bd1`), so the eviction is that change's debt. Consider whether the content belongs
   in `claude/skills/clawgate/flows/` or `reference/` rather than the body.
   forcing: gate — `main` is red on this test right now
3. **Demote `resume/SKILL.md` step 4 to `reference/`, and give the body a ceiling.**
   MEASURED 2026-09-12: step 4 (the two recall surfaces) is **19,536 of 47,684 bytes — 41%**
   of the largest skill body in the repo, which has **no `reference/` dir** (19 other skills
   do) and **no size ceiling**. This is the ONLY one of the operator's three stated drivers
   for consolidation that "expire + archive + cap" does not touch. 🔴 Do NOT merge the two
   commands into one wrapper: `cairn recall` is per-repo and `handoff_search` sweeps all four
   repos including two client ones, and much of that prose exists because getting that wrong
   leaks client content into a PUBLIC repo. 🔴 Do NOT retire `handoff_search`: its measured
   yield was ~0 and the "1 of 20" figure was retracted as an instrument artifact, but the
   2026-09-12 session is a genuine yield instance — a `skill-usage-telemetry` hit
   ("`find-session`'s 'both hosts' claim was HALF FALSE") is what prompted the
   instrument-validation control that cracked that investigation.
   forcing: none
4. **Build the `source='tool'` emission watcher** designed in the block below — per-session
   cross-source consistency (`scripts/collector/tool_emission_watch.py`), sibling to
   `deadman.py`, verdicts `ok`/`gap`/`cannot-tell`. Positive control is the confirmed
   2026-09-05 laptop/`obs-read` case, a REAL fixture rather than a synthetic one.
   forcing: none
5. **Port the boundary sweep to the two remaining `CROSS JOIN` sites** —
   `derived_attention_consistent` in `scripts/validation/invariants.py`, and the hand-run
   query in `claude/skills/activity/reference/queries.md`. Same quadratic shape `#1608`
   removed; narrower windows today. Verify with the same old-vs-new equality check.
   forcing: none
6. **Retract the expired Tekton-capacity claim in `CLAUDE.md`.** It records capacity as "not
   the constraint" (measured 2026-09-10, node at 14% CPU requests). MEASURED 2026-09-12: the
   scheduler refused to place `devrc-ci-gxsd6-gate-pod` for 16 minutes — `0/5 nodes are
   available: 1 Insufficient cpu, …`, `tekton-ci-1` at 98% CPU, 8 concurrent `devrc-ci` runs.
   It drained unaided, so this is congestion not breakage — but that sentence is cited as the
   reason a branch-protection question is settled.
   forcing: none

## Gotchas / decisions / dead-ends
- The handoff skill is 174 lines; the resume skill is 264 lines — resume is longer because it carries the cairn recall interface (the store's read surface) and the claim-work lock protocol
- Both skills share `clawgate_handoff.sh` as a parser for the `clawgate-task:` front matter
- The handoff skill's step 5 (`handoff_doc.py`) is the only step that commits — a measured hazard where a session wrote the doc directly and it ended untracked
- The resume skill's step 2 (`resume-state.sh`) runs BEFORE reading the doc to determine which copy is authoritative — a stale clone can serve a handoff 276+ lines behind origin

- 🔴 **The 09-10 laptop inflection is DIAGNOSED and is NOT a bug.** Laptop `keys` rose
  432 -> 1075 -> 2027 -> 3070 and `i3` 209 -> 898 -> 849 -> 1145 (09-09..09-12) while
  `claude` fell 114 -> 60 -> 18 -> 30 and `tmux` 153 -> 88 -> 44 -> 42. The discriminator
  is the INVERSE pattern on the other host over identical dates: workbench `keys`
  2075 -> 0 -> 0 -> 0 and `i3` 1097 -> 0 -> 5 -> 4, while its `claude` and `tmux` kept
  running (1044 -> 248 -> 719 -> 1017 and 1435 -> 246 -> 981 -> 2028). Corroborated by a
  non-telemetry source: `last -F | grep 10.42.0.100` on the workbench shows a session
  starting **2026-09-10 09:18:41** running 1d 6h 35m, with long sessions since; and live,
  `deadman.py` reports `workbench/keys DEAD (16.8h)` and `workbench/browser DEAD (12.9h)`.
  **GUI presence did not increase — it MOVED**: the operator began working physically at
  the laptop while driving Claude Code/tmux on the workbench over SSH. Ruled out: a deploy
  changing emission (no commits in `keylog/` or `i3/i3source.py` near the window),
  duplicate daemons (exactly one `keylog` and one `i3-source` per host), a batching change
  (no code changed). Do not re-derive this.
- 🔴 **The original claim this doc was opened to investigate was wrong in TWO ways, and the
  second is the reusable lesson.** Not just a wrong number — a FUSED SCOPE. The
  `resume | A | 85 | 80 | 5` row is from a table with **no host column at all**, so
  "the workbench accounts for all 85" was an attribution nobody measured; the "0 laptop"
  figure came from a different doc measuring a different quantity (`resume-state.sh` runs)
  over a different window (~8.6h post-`#1332`) with a different instrument (ssh transcript
  grep, not ClickHouse). **Both source docs were individually correct.** When a handoff
  block cites two numbers, check they came from the same measurement before combining them.
- 🔴 **`activity-scan.py`'s `--days N` window is `now() - INTERVAL N DAY`, so the FIRST
  day bucket is a PARTIAL DAY.** Any per-day breakdown off that window shows a spuriously
  low (often zero) count for its oldest day. Use calendar-day bounds when the per-day
  numbers are the finding. This produced a wrong "0" that was reported before being caught.
- 🔴 **`nix build`'s own stdout is not a reliable place to read a verdict — use
  `nix log <flakeref>#checks.x86_64-linux.pytests`.** Two concurrent invocations
  interleaved into the same redirect files and left NO pytest verdict in either; `nix log`
  returned the authoritative stored build log with `RESULT: PASS (exit=0)`,
  `SCOPE: FULL (29 of 29 hermetic target(s))` and the collected counts. Also: a cached
  derivation re-builds nothing and therefore prints nothing, so an empty build log is not
  a failure signal.
- 🔴 **A backgrounded `nohup … &` nested inside an already-backgrounded shell call is NOT
  reliably detached — but it is also not reliably killed.** One such wrapper reported
  `exit 0` having produced no verdict at all (killed as pytest started); a second,
  relaunched cleanly, then ran CONCURRENTLY with the first, which had survived. Both
  symptoms were invisible in the exit status and visible only in the log CONTENTS. This is
  also how the "build the two nix check derivations ONE AT A TIME" rule gets violated by
  accident.
- **Decision, with the reason, so it is not re-proposed:** the handoff corpus is NOT being
  migrated into cairn. The operator's drivers were too many retrieval surfaces, claims that
  never expire, and raw volume — explicitly NOT public-repo leak surface. Against
  migration: cairn's digest is read at EVERY resume while handoff docs are read one at a
  time (so it moves bytes onto a per-session surface); per-subsystem/durable is a different
  grain and lifecycle from per-initiative/time-bounded; git is replicated to every clone
  while cairn is one pod whose failure mode is serving a stale cache at exit 0; it would
  delete `handoff_search --offline`'s substrate (it answers from git refs with no DB); and
  it does not fix staleness, which cairn has identically. Chosen instead: expire (#1618,
  shipped) + archive + cap.
- **Not done deliberately:** no retro-stamp of the 478 existing investigation blocks. The
  mechanism dates them from git already (measured: 478/478 dateable via pickaxe on the
  block's own heading; independently reproduced at 147/147 over a 30-doc sample with a
  positive control), so a backfill would write the pickaxe's own answer and add no
  information.
- ⚠ **`ship.sh`'s dirty-path classifier is the ONLY thing that surfaces a tracked-but-
  modified file that nix bakes into the built generation.** It caught `.tmux.conf` deployed
  on the workbench but not on the laptop while both reported the same sha.
  `drift-check.sh` cannot see this class — git parity is clean for a tracked-and-modified
  file, and its host-divergence arm compares `settings.json` keys only. (That specific file
  turned out to be another session's work, since committed and pushed as PR #1622.)

- 🔴 **THE DISJOINT-FILE MERGE BREAK HAPPENED TWICE IN ONE SESSION, AND THE MERGED-TREE GATE
  IS THE ONLY THING THAT SEES IT.** Both times: zero shared files, both sides green, merged
  tree red. (1) `#1627` pinned `handoff-audit-pr-ladder.md`'s allowance off a 194,004 B
  measurement; the doc grew 3,387 B on `main` while the PR was in flight and crossed one
  16 KiB quantum → `main` red, fixed by `#1634`. (2) `#1618` dates an investigation block
  with a **path-scoped** pickaxe; `#1627` renamed 35 docs into `claudedocs/archive/`, so the
  first commit touching each new path is the rename → **every archived doc's blocks reported
  `0d`**, the freshest possible reading on the oldest docs in the corpus. Fixed by `#1644`.
  🔴 **I had the merged-tree gate RUNNING and merged before it reported.** It came back
  `collected=23175 passed=23169 failed=3`. Do not treat a running gate as an optional
  formality because both branches are green — that is precisely the state this class hides in.
- 🔴 **A THIRD LEDGER CLASS, three instances in one PR: the gate that catches an unregistered
  instrument is itself a ledger you must register with.** `test_no_public_ips.py`'s ALLOWLIST
  (found by going red in both directions on the archive move); `census_scan.py` /
  `ledger-check.sh` (the new gate was ABSENT from the fast screen built for its own failure
  class, because that scanner needs a `Path(__file__)`-derived root and the new one threads a
  parameter); and `test_mutation_battery_anchors.py`'s `BATTERIES` (found by CI, after the
  battery was committed). **Each found by a different mechanism, none by reading.** The count
  of such ledgers is not knowable from any one file.
- 🔴 **`NOT_TABLE_DRIVEN` in `test_mutation_battery_anchors.py` is a trap worth naming.** A
  new battery whose table is called anything but `MUTANTS` will pass the exemption test
  (`test_the_EXEMPTION_list_is_not_a_hiding_place` only greps `^MUTANTS`), so the cheap fix
  is to exempt it — with a reason that is true only as a naming accident. **Register instead:
  rename the table to `MUTANTS` and put `old` in the 4th position.** `#1627` did this and got
  real protection out of it — 21 anchors across 3 target files now checked on every push,
  where duplicate `old` strings had previously only been reasoned about by hand.
- 🔴 **`--follow` and `-S` DO NOT COMPOSE — a measured dead end, do not retry it.**
  `git log --follow -S"<heading>" -- <renamed path>` returns **nothing at all**, which is
  worse than a wrong date because an empty result reads as UNDATED rather than expired.
  Verified against a control (the same form on a NON-renamed doc returns its true first
  date). `#1644`'s fix uses `--follow` for NAME RESOLUTION ONLY, then runs an ordinary
  path-scoped pickaxe over the resolved set.
- 🔴 **A repo-wide pickaxe was measured and REJECTED, with the numbers, so nobody re-proposes
  it.** Over all 119 tracked docs: 490 blocks, 487 distinct headings, **0** headings in more
  than one doc — so the collision objection does NOT rule it out. It was rejected on two
  other grounds: ~8× slower per block (0.63 s vs 0.08 s) on a per-block hot path, and it
  converts every FUTURE collision from a non-event into a wrong answer.
- 🔴 **MERGED ≠ DEPLOYED bit me inside this session.** After merging `#1644` I re-verified by
  running `scripts/resume-state.sh` from the base clone and got the OLD (`0d`) behaviour — the
  clone was still at the previous sha. The fix only showed `EXPIRED 43d` after `ship.sh`
  converged. **Verify against the deployed artifact, and say which one you read.**
- ⚠ **`ship.sh` rc 19 (`HOSTS DISAGREE`) is expected under an active repo and is not a
  failure.** `origin/main` moved between the two hosts' fetches, so each host landed on a
  different (internally consistent) commit. The remedy is literally to re-run it; the second
  pass converged both.
- 🔴 **EIGHT instrument errors in one session, all ONE family: reading a silenced, malformed
  or mis-scoped result as a finding.** `2>/dev/null` hid `error: option 'pickaxe-regex' takes
  no value` (rc 129 → read as "0 of 31 blocks dateable"; truth 147/147); a heading retyped
  from TRUNCATED display output; a `count=1` regex that hit a docstring instead of the
  constant; `^def test_` that missed 36 methods indented inside classes; a `nohup &` wrapper
  reporting **exit 0 having produced no verdict** while a "cleanly relaunched" second copy ran
  concurrently with the first, which had survived; two `handoff_search` runs compared against
  the same mainline ref (necessarily identical, proving nothing); `DEVRC=<clone>` silently
  renaming the scope label so `--repo devrc` selected **0 sections** and an error block was
  read as hits; and `set -- $t` not splitting, because **zsh does not word-split**, reported
  as `no tests ran`. **When a check returns "nothing", the first hypothesis is that you broke
  the check.**

## How to verify
- `main`'s two reds, and whether they are still there:
  ```bash
  git -C ~/workspace/devrc worktree add -f /tmp/mchk --detach origin/main
  nix develop ~/workspace/devrc -c python3 -m pytest /tmp/mchk/scripts/tests/test_handoff_doc_size.py -q -k budget
  nix develop ~/workspace/devrc -c python3 -m pytest /tmp/mchk/scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py -q -k did_not_grow
  git -C ~/workspace/devrc worktree remove /tmp/mchk --force
  ```
- The archived-block age fix, on the DEPLOYED copy (not the repo):
  ```bash
  bash ~/workspace/devrc/scripts/resume-state.sh claudedocs/archive/handoff-browser-bridge-2026-07-31.md
  # expect: 🔴 EXPIRED 43d on both blocks.  `0d` means you are running a pre-#1644 copy —
  # run scripts/ship.sh and re-read.
  ```
- Archiving did not hide docs from the corpus index — count docs, not sections, across a
  mainline that actually contains the archive (a clone whose `refs/remotes/origin/main` is
  moved; `handoff_search --offline` reads the MAINLINE REF, so comparing two working trees
  proves nothing): pre-archive 118 docs / 1518 sections, post-archive **118** / 1523.
- The seven PRs, by CONTENT not ancestry (a squash merge never makes the branch head an
  ancestor):
  ```bash
  git -C ~/workspace/devrc ls-tree --name-only origin/main claudedocs/archive/ | wc -l   # 35
  git -C ~/workspace/devrc show origin/main:scripts/session-analysis/activity-scan.py | grep -c SECTION_UNAVAILABLE_MARK
  git -C ~/workspace/devrc show origin/main:scripts/resume-state.sh | grep -c INVESTIGATIONS
  ```
