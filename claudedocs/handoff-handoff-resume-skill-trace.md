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
- Base clone on `main`; all work lands via PRs from worktrees. `origin/main` moves ~30 commits
  a day — **re-fetch and re-measure before trusting any number in this doc, including one
  written minutes ago.** Four of this doc's ranked lists have gone stale within hours.
- ✅ **BOTH HOSTS CONVERGED AND DEPLOYED at `c9e9867f`** (`ship.sh` rc 0): workbench 616
  managed artifacts resolve / 0 dangling / 0 stale, laptop 575 / 0 / 0, cross-host agreement
  COMPARED. ⚠ The laptop's LAN address did not answer; it converged over nebula
  (`zach@10.42.0.100`). That is the documented fallback, not a failure.
- ✅ **The `resume` skill prune is SHIPPED AND LIVE, verified on the DEPLOYED artifact** —
  not merely merged. `readlink -f ~/.claude/skills/resume/SKILL.md` resolves into
  `/nix/store/r6rbi9r1…-devrc-claude-skills/` on **both** hosts at **20,731 B** (from
  51,356 B, −59.6%), with all six `reference/` sidecars present. ~30 KB / ~8k tokens no
  longer loads on every `/resume`.
- ✅ **All FOUR reds are closed.** The three this arc targeted (doc-ceiling `#1650`, needle
  `#1655`, battery anchors `#1665`) plus the runtime-shebang guard, which was closed by
  **`#1692`** — somebody else, while this doc still listed it as rank 1.
- ✅ **A FIFTH red, found and closed after those:** `test_audit_rule_firing_sweep.py` went red
  the moment `#1739` landed, and `#1746` (`c9e9867f`) closed it. All three of its 🔴
  paragraphs were **registered as rules, none exempted**.
- **Merged this arc:** `#1655` `18a95e23` · `#1650` `d4c7d5b7` · `#1651` `22ae0817` ·
  `#1665` `c1600c93` · `#1663` `f9ef66e4` · `#1675` `70c4a006` · `#1745` `2d462bd6` ·
  `#1746` `c9e9867f`.
- ⚠ **One thing is NOT settled:** `tekton/devrc-pytests` was red on `#1746` for
  `test_REAL_INTERACTIVE_fzf_puts_the_eponymous_repo_under_the_cursor`, and it was merged
  through deliberately. That test **passes on the dev-host tier** at `c9e9867f` — but CI
  failed it in the **`nix build` sandbox tier**, which is blind to different things. Those
  are different claims and only the sandbox tier settles it.
- 🔴 NO `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** — 0 tasks, its
  positive control confirming the board is reachable. A REAL reading, but it does NOT prove
  the session id is right, so it is not a statement that the board is fine.

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

### Do the six re-anchored battery rows still KILL, or only APPLY?
- as-of: 2026-09-14
- **Symptom + exact repro:** `#1648` (`a2c84a1c`) moved `MAX_BYTES`, `GRANDFATHERED` and
  `tightest_allowance` out of `scripts/tests/test_handoff_doc_size.py` into
  `scripts/lib/handoff_budget.py`. Six rows of
  `scripts/tests/mutation_battery_handoff_archive_and_cap.py` stayed anchored on the old
  file, where they now occur **0x**. Reproduce on plain `main`:
  ```bash
  git -C ~/workspace/devrc worktree add -f /tmp/anc --detach origin/main
  nix develop ~/workspace/devrc -c python3 -m pytest /tmp/anc/scripts/tests/test_mutation_battery_anchors.py -q
  ```
- **Observed (with values):** `6 anchor(s) do not occur EXACTLY ONCE` — C1
  (`MAX_BYTES = 65_536`), C4 (`handoff-tmux-webapp.md` entry), C5
  (`handoff-handoff-search-index.md` entry), C6 (`GRANDFATHERED: dict[str, int] = {`),
  C8 (`return max(step, math.ceil(size / step) * step)`), C11 (`handoff-cairn-phase3.md`
  entry) — **all 0x**. Per the battery's own docstring a 0x anchor prints `NOT-APPLIED` and
  **scores as a SURVIVOR while testing nothing**, so `main` has carried six silently-inert
  mutants since #1648.
- **Ruled out:** that my merge of `main` into `#1650` caused it — the same test fails on
  `9e83af8a` (pre-`#1651`) and on `22ae0817`, both untouched by me.
  via: measurement (control run at two mainline shas)
- **Ruled out:** that it is either merged PR's doing — neither `#1650` nor `#1651` touches
  `test_handoff_doc_size.py` or the battery. via: code (`gh pr view --json files`)
- **Ruled out:** a simple re-target for C5 — its ledger entry was legitimately DELETED by
  `#1650` once the doc was pruned under `MAX_BYTES`, so there is nothing to re-anchor onto.
  Re-anchored to `handoff-cairn-task-linkage.md` (76,743 B → quantises to 81,920, so 114,688
  is exactly the same two steps of slack the row's description names). via: code + measurement
- **Leading hypothesis:** the fix is complete and the rows will kill — C1/C4/C6/C8/C11 are
  verbatim moves, so only their target file changed. **C5 is the one to doubt**: it is a
  DIFFERENT entry, and nothing yet proves `test_no_handoff_doc_exceeds_its_budget` fails on
  it the way it did on the old one.
- 🔴 **Next probe:** read the battery verdict. A green anchors test proves only that the
  anchor is APPLIED — the battery's own failure message demands the other half:
  ```bash
  cd <worktree> && nix develop ~/workspace/devrc -c python3 \
    scripts/tests/mutation_battery_handoff_archive_and_cap.py
  ```
  Expect every C row `KILLED` by the test its row names; a `KILLED-WRONG-REASON` or
  `SURVIVED` on C5 means the re-anchor is wrong. ⚠ It takes **>10 min** — run it detached,
  never under a 10-minute foreground cap (see Gotchas).

### Do the six re-anchored battery rows still KILL, or only APPLY? — CLOSED 2026-09-14
- as-of: 2026-09-14
- ✅ **ANSWERED: they kill.** Full battery re-run against the re-anchored table —
  `CONTROL test_handoff_doc_size.py: 9 passed`, `CONTROL
  test_resume_state_handoff_resolution.py: 200 passed`, **`21/21 killed for the named
  reason`**. C4 (re-anchored twice), C5 (re-anchored onto a live entry) and C11 (the positive
  control) are each `KILLED(attributed)` — by the test their own row names, not a neighbouring
  guard.
- 🔴 **The FIRST `21/21` did not cover the final table and would have been quoted as if it
  did.** A round-0 audit moved C4's anchor after that run; both runs print the identical
  string `21/21 killed for the named reason`, so nothing about the number would have revealed
  it was measured against a superseded table. **A mutation result is a claim about the table
  that was in the file when it ran.** via: measurement (two full battery runs)
- **Ruled out:** that the anchors test could stand in for the battery — it reports an anchor
  is APPLIED (1x), never that its mutant KILLS. It was green on a table whose C4 anchor was
  about to go 0x. via: code + measurement
- **Next probe:** none — closed.

### `main` red on `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` — NOT this arc's work
- as-of: 2026-09-14
- **Symptom + exact repro:**
  ```bash
  git -C ~/workspace/devrc worktree add -f /tmp/sb --detach origin/main
  nix develop ~/workspace/devrc -c python3 -m pytest /tmp/sb/scripts/tests/test_runtime_shebangs.py -q -k usr_bin_env
  ```
- **Observed (with values):** `AssertionError: a test writes its own shebang — use
  testlib.mockbin.write_exec` — **12 sites, all in one file**,
  `scripts/claude-hooks/tests/test_guard_core.py` at lines 4676, 4691, 4786, 4803, 4810, 4832,
  4834, 4836, 4892, 4902, 4903 and 5019. Eleven are `#!/usr/bin/env bash`, one
  (`:5019`) is `#!/usr/bin/env python3`.
- **Ruled out:** that it is this arc's doing — reproduced on plain `origin/main` at
  `e6f05e04` BEFORE `#1665`/`#1663` merged, and again at `f9ef66e4` after. Neither PR touches
  `scripts/claude-hooks/` or `scripts/tests/test_runtime_shebangs.py`. via: measurement
  (control run at two mainline shas)
- **Leading hypothesis:** the guard and the offending sites were written by different changes
  and never met on one tree — the same DISJOINT-FILE merge shape this doc already records
  twice. `test_runtime_shebangs.py`'s last three touching commits are `af943906`, `cfdb3899`,
  `b79ccfbe`; the writer sites live in a file none of them names. UNCONFIRMED — nobody has
  bisected it.
- **Next probe:** confirm the direction before fixing anything — `git log -S'#!/usr/bin/env
  bash' --oneline -- scripts/claude-hooks/tests/test_guard_core.py` against the first commit
  adding the guard, to establish which side arrived second. Then convert the 12 sites to
  `testlib.mockbin.write_exec`, which is what the assertion names.

### Is the `resume` body's 8 KB above target reachable, or do the prose pins floor it?
- as-of: 2026-09-17
- **Symptom + exact repro:** the prune stopped at 20,731 B against a 12,038 B enforced
  target. Reproduce the floor:
  ```bash
  nix develop ~/workspace/devrc -c python3 -m pytest ~/workspace/devrc/scripts/tests -q \
    -p no:cacheprovider -k "pin or RESUME"
  ```
- **Observed (with values):** **35 prose pins across 10 test modules** assert literal
  sentences of this skill's body, most scraping the expected set FROM the tool (two-way
  ledgers). A 15,179 B draft went **13 tests red**. 20,731 B sits ~2 KB above the floor those
  pins impose; the new ceiling is `MAX_BYTES = 22_400` with 800 B headroom, i.e. 869 B of
  slack ≈ two mean 🔴 rules.
- **Ruled out:** that the pins can simply be moved — ONE pin was split
  (`RESUME_SENTENCES` → `RESUME_SENTENCES_REFERENCE`) and it cost a full mutation battery to
  do safely. The remaining ~50 pins span three more modules. via: measurement (the split
  landed in `#1745`)
- **Ruled out:** that the split weakened its guard — verified by CONTENT mutation in BOTH
  directions: rewording a pinned sentence in the sidecar goes red, and in the body goes red;
  the split is set-identical (35 → 15 + 20, AST-compared, 0 lost). via: measurement
- **Leading hypothesis:** the pins are in `the-algorithm`'s named delete-class ("tests that
  pin PROSE (doc claims)") and are the real lever, not the ceiling. They survive step 1 on a
  measured incident (their class docstring cites "six commands never invoked once"), so they
  were flagged rather than deleted.
- **Next probe:** decide, do not measure further. Either accept 20,731 B as the floor and
  leave the ceiling where it is, or run `the-algorithm` over the 35 pins as a batch — 🔴 as
  ONE decision, not pin-by-pin, because a pin removed in isolation reads as a coverage loss
  while the class question goes unasked.
- **Carried forward, the measurement that motivated the prune** (its rank is now closed, the
  number is still the reason the cut worked): MEASURED 2026-09-12, step 4 — the two recall
  surfaces — was **19,536 of 47,684 bytes, 41%** of what was then the largest skill body in
  the repo, which had **no `reference/` dir** (19 other skills did) and **no ceiling**. All
  three of those facts are now false, which is the point.

## Next steps (ranked)
<!-- Renumbered 2026-09-17: the previous ranks 1 (shebang guard, closed by #1692) and 2
     (resume prune, shipped as #1745 and DEPLOYED) are both done. This is the FOURTH
     renumbering of this list, each stale within hours — see the Gotchas entry. -->
1. **Build the `source='tool'` emission watcher** designed in the block below — per-session
   cross-source consistency (`scripts/collector/tool_emission_watch.py`), sibling to
   `deadman.py`, verdicts `ok`/`gap`/`cannot-tell`. Positive control is the confirmed
   2026-09-05 laptop/`obs-read` case, a REAL fixture rather than a synthetic one.
   forcing: none
2. **Port the boundary sweep to the two remaining `CROSS JOIN` sites** —
   `derived_attention_consistent` in `scripts/validation/invariants.py`, and the hand-run
   query in `claude/skills/activity/reference/queries.md`.
   forcing: none
3. **Retract the expired Tekton-capacity claim in `CLAUDE.md`.** It records capacity as "not
   the constraint" (measured 2026-09-10, node at 14% CPU requests). MEASURED 2026-09-12: the
   scheduler refused to place `devrc-ci-gxsd6-gate-pod` for 16 minutes — `0/5 nodes are
   available: 1 Insufficient cpu, …`, `tekton-ci-1` at 98% CPU, 8 concurrent `devrc-ci` runs.
   It drained unaided, so this is congestion not breakage — but that sentence is cited as the
   reason a branch-protection question is settled, so a session will reason from it.
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

- 🔴 **THIS DOC'S OWN RANKED LIST WAS WRONG IN BOTH ITEMS, AND THE KICKOFF COPIED IT
  VERBATIM.** Rank 2 was closed before the session opened; rank 1 was an open PR and 4x
  bigger than recorded. That is the THIRD consecutive session whose kickoff asserted a
  retracted or stale claim (`d35c8655` records the last one). The mechanism is structural:
  a rank is written from a measurement taken *before* the doc is committed, and **nothing
  re-reads it**. **Re-measure every rank before acting on it** — `git show origin/main:<path>
  | wc -c` costs one command and both of these would have been caught by it.
- 🔴 **`gh pr list --state open` IS THE STEP THAT SAVED THE WORK HERE, AND `claim-work` ALONE
  WOULD NOT HAVE.** The slug `claim-work` derives is `<doc>-<rank>`; the other session had
  claimed the same work under a slug derived from its own topic
  (`handoff-doc-oversize-claims-accuracy`), so the two never collided and my claim was
  granted. The PR sweep is what showed `#1650` already did rank 1 — including a second
  finding this doc never mentions. **Sweep open PRs even when your claim succeeds.**
- 🔴 **A RED CHECK ON A PR IS NOT EVIDENCE ABOUT THAT PR — RUN THE CONTROL ON `main` FIRST.**
  Both `#1650` and `#1651` showed red pytests on the same test. It was red on `origin/main`
  all along; `#1651` touches ONE doc the test cannot reach. I nearly debugged #1650's diff.
  The control is one command (run the failing test at `origin/main` in a detached worktree)
  and it inverted the diagnosis. Verify staleness STRUCTURALLY, not by timestamp:
  `git merge-base --is-ancestor <fix-sha> <pr-head>` answers "was the fix even in this tree".
- 🔴 **A SCOPED GATE RUN CANNOT SUPPORT AN UNSCOPED CLAIM, AND I MADE THAT ERROR.** I said
  "`main` is green except doc-size" having run only the two gates I was targeting. The
  anchors red had been sitting there since `#1648`. The scoped run was the right tool for
  iterating and the wrong basis for a sentence about `main`. **Name the tests you ran, or run
  the tier.**
- 🔴 **A `MAX_BYTES`-style CONSTANT EXTRACTION HAS A THIRD LEDGER: THE MUTATION BATTERY'S
  TARGETS.** `#1648` moved three constants into `scripts/lib/handoff_budget.py` and left six
  battery rows pointing at the file they came from. The rows did not fail loudly — they went
  **0x**, which the battery scores as SURVIVED. So the tell is not a red battery; it is the
  separate `test_mutation_battery_anchors.py`. Add this to the ledger-class list this doc
  already carries: `test_no_public_ips.py`'s ALLOWLIST, `census_scan.py`/`ledger-check.sh`,
  `test_mutation_battery_anchors.py`'s `BATTERIES` — **and now its `TARGETS` map.**
- 🔴 **A BATTERY ROW ANCHORED ON A LEDGER ENTRY IS ONLY AS DURABLE AS THAT ENTRY, AND THIS
  LEDGER IS DESIGNED TO SHRINK.** C5 anchored on `handoff-handoff-search-index.md`'s
  grandfather entry; `#1650` deleted it — correctly, that is the ratchet working. Prefer
  anchoring a row on something structural, or expect to re-anchor each time the ledger sheds
  an entry.
- 🔴 **THE MUTATION BATTERY TAKES >10 MIN AND A TIMEOUT KILL SKIPS ITS `finally` RESTORE.**
  Measured: a foreground run hit a 10-minute cap and the SIGTERM left `scripts/resume-state.sh`
  carrying row A6's mutant — tracked source, silently modified. Its docstring says "check
  `git status` anyway if it dies hard" and that is not boilerplate. **Run it detached**, and
  `git status` the worktree afterwards regardless. Confined to a worktree here; in a base
  clone it would have been a mutated tracked file nobody was looking for.
- **The needle false positive, for the next person who trips it:** `"no create route"` (a
  retracted claim) is a deliberately short needle whose own comment predicts false positives
  🔴 **and writing this very bullet tripped it — twice.** Quoting the needle to EXPLAIN it is
  itself an unmarked occurrence, so a doc about the gate reddens the gate. Both sites here
  carry a marker for that reason. It prescribes the fix
  ("reword, or carry a retraction marker within `_MARKER_WINDOW`"). Two of the three sites
  were a gotcha QUOTING a grep pattern, and both straddled a line wrap — so `grep -n` showed
  **one** hit where the normalising scanner saw **two**. **Use the repo's own scanner
  (`_normalise_for_scan` + `_RETRACTION_MARKERS` + `_MARKER_WINDOW`) behind a positive
  control**; a raw grep understates the count and reads as precise.

- 🔴 **A DOC ABOUT A GUARD TRIPS THE GUARD — this doc reddened `main` on the very test it was
  documenting.** The Gotchas bullet below QUOTES the retracted needle in order to explain it,
  and the How-to-verify bullet spells out the positive-control fixture, which is a BARE
  ASSERTION by construction — that is exactly what makes it a usable control and exactly what
  makes it a finding once it is in a tracked file. **Caught by CI, not by me**, on the PR that
  was landing this very doc. 🔴 **My own scanner probe missed both because it scanned the two
  index-store docs and not the doc I was writing: a scanner is a claim about the corpus you
  POINTED IT AT.** When you write prose about a content gate, run that gate over your own
  prose before pushing.
- 🔴 **THE RANKED LIST WENT STALE TWICE IN ONE DAY, AND THAT IS THE DOC'S OWN RECURRING
  DEFECT.** The kickoff that opened this arc asserted two ranks: one had been closed by
  `#1640` BEFORE the session started, the other was already an open PR (`#1650`) and had grown
  from the recorded 1,677 B to 7,062 B. Then the replacement rank 1 was merged within hours
  and the list was stale again. Three consecutive sessions now. The mechanism is structural: a
  rank is written from a measurement taken *before* the doc is committed and **nothing
  re-reads it**. 🔴 **Re-measure every rank before acting on it** — `git show
  origin/main:<path> | wc -c`, or run the named test, costs one command and would have caught
  every one of these.
- 🔴 **`gh pr list --state open` IS WHAT PREVENTED DUPLICATE WORK, AND `claim-work` ALONE
  WOULD NOT HAVE.** `claim-work` derives its slug from `<doc>-<rank>`; the other session had
  claimed the same work under a slug from its own topic
  (`handoff-doc-oversize-claims-accuracy`), so the two never collided and my claim was
  granted. **Sweep open PRs even when your claim succeeds.**
- 🔴 **A RED CHECK ON A PR IS NOT EVIDENCE ABOUT THAT PR — RUN THE CONTROL ON `main` FIRST.**
  Measured four times this arc. `#1650` and `#1651` were both red on a test neither diff could
  reach; `#1665`'s red was a guard red on `main`; and the one red that WAS mine (the needle,
  on `#1663`) was distinguished from the others by exactly the same control. Verify staleness
  STRUCTURALLY, not by timestamp: `git merge-base --is-ancestor <fix-sha> <pr-head>` answers
  "was the fix even in this tree".
- 🔴 **A SCOPED GATE RUN CANNOT SUPPORT AN UNSCOPED CLAIM.** I said "`main` is green except
  doc-size" having run only the two gates I was targeting; the anchors red had been sitting
  there since `#1648`, and the shebang red still is. **Name the tests you ran, or run the
  tier** — and a green subset is never a statement about `main`.
- 🔴 **A CONSTANT EXTRACTION HAS A THIRD LEDGER: THE MUTATION BATTERY'S `TARGETS`.** `#1648`
  moved `MAX_BYTES`/`GRANDFATHERED`/`tightest_allowance` into `scripts/lib/handoff_budget.py`
  and left six rows pointing at the file they came from. They did not fail loudly — they went
  **0x**, which the battery scores as SURVIVED. The tell is the separate
  `test_mutation_battery_anchors.py`, never the battery. Ledger-class list so far:
  `test_no_public_ips.py`'s ALLOWLIST · `census_scan.py`/`ledger-check.sh` ·
  `test_mutation_battery_anchors.py`'s `BATTERIES` · **and its `TARGETS` map.**
- 🔴 **A BATTERY ROW ANCHORED ON LEDGER DATA BINDS TO THE TRAILING MEASURED COMMENT TOO, SO
  ANCHOR ON THE ENTRY WITH THE MOST HEADROOM.** The mutation deletes the whole line, so the
  comment cannot be trimmed out; that comment is re-measured whenever the allowance is bumped,
  and an allowance is bumped when a doc runs out of headroom. C4 was anchored on the entry
  with the LEAST headroom of the eleven — `handoff-tmux-webapp.md`, **930 B**, after growing
  314,233 → 323,642 → 326,750 in two commits. Days from going 0x. Found by a round-0 audit,
  re-verified, moved to `handoff-nix-disk-cleanup.md` (15,473 B).
  ⚠ **Open DEFECT, declined on purpose and recorded rather than dropped** (not a rank — an
  audit finding is a defect, and a rank queue that grows every round does not drain):
  deriving C4/C5/C11's anchors from `handoff_budget.GRANDFATHERED` at import time would end
  this class, but makes the anchors test's 0x direction **near-vacuous** for those rows —
  it would compare a string against the file it was generated from. The defensible middle the
  audit named is to derive those three and keep **C6**'s structural anchor
  (`GRANDFATHERED: dict[str, int] = {`) as the literal tripwire. Operator-level call.
- 🔴 **TWO MUTATION RUNS PRINTED THE IDENTICAL `21/21 killed for the named reason` AGAINST
  DIFFERENT TABLES.** The first predated the C4 re-anchor. Nothing in the number says which
  table it measured, so quoting the earlier one after the change would have been a false
  claim that looked like evidence. **Re-run the battery after ANY edit to its table, and say
  which table the number belongs to.**
- 🔴 **THE MUTATION BATTERY TAKES >10 MIN AND A TIMEOUT KILL SKIPS ITS `finally` RESTORE.**
  A foreground run hit a 10-minute cap and the SIGTERM left `scripts/resume-state.sh` carrying
  row A6's mutant — tracked source, silently modified. **Run it detached**, and `git status`
  the worktree afterwards regardless. Related: the outer `saved` snapshot is the only net for
  a run killed BETWEEN rows, and it did not list the new target until `#1665` added it.
- ⚠ **I committed from a worktree while that battery was actively mutating tracked files in
  it.** `git status` showed `scripts/resume-state.sh` modified mid-run. Staging EXPLICIT paths
  is the only reason a mutant did not land in the commit — `git add -A` would have committed
  one as though it were source. This is the concrete case the never-blind-stage rule exists
  for.
- **The needle false positive, for the next person who trips it:** the retracted
  `no create route` claim is matched by a deliberately short needle whose own comment predicts
  false positives and prescribes the fix ("reword, or carry a retraction marker within
  `_MARKER_WINDOW`"). Sites that merely QUOTE it — a gotcha reproducing the grep pattern, a
  heading naming the sweep — are not assertions, and two of them straddled a line wrap, so
  `grep -n` showed ONE hit where the normalising scanner saw TWO. **Use the repo's own scanner
  behind a positive control**; a raw grep understates the count and reads as precise.

- 🔴 **I MADE THE SAME SCOPED-RUN ERROR THREE TIMES IN ONE ARC, INCLUDING AFTER WRITING IT
  INTO THIS DOC AS A GOTCHA.** The third instance is the instructive one: I test-merged
  `#1745` with `#1738`, ran THREE named modules, got `1423 passed`, and reported it as though
  it gated the merge. `test_cairn_skill_verb_ledger.py` was not among them, and that is
  precisely where the merge broke. **A run over modules you chose cannot support a claim
  about a tree.** Writing the lesson down demonstrably did not prevent the repeat — what
  would is naming, in the claim itself, which modules ran and which did not.
- 🔴 **`gh pr list --state open` CAUGHT A DUPLICATE TWICE IN ONE ARC; `claim-work` CAUGHT
  NEITHER.** `claim-work` derives its slug from `<doc>-<rank>`, so two sessions naming the
  same work differently never collide and BOTH claims are granted. (1) rank 1 was already
  `#1650`, claimed under `handoff-doc-oversize-claims-accuracy`. (2) the `main`-red fix was
  already `#1746`, opened ~3 h earlier; the agent had written its own before sweeping and
  discarded it. **Sweep open PRs even when your claim succeeds** — it is the only thing that
  sees an unclaimed duplicate.
- 🔴 **A GUARD'S CONTROL CAN CERTIFY NOTHING WHILE READING AS RIGOROUS.** `#1745` shipped a
  "positive control" whose docstring claimed rewording "must make the SAME predicate the real
  test uses go red" — it never invoked that predicate. MEASURED: gutting the real pin test's
  assertion to `assert True` left the control green and all 55 tests green; the identical
  mutation on its HANDOFF twin failed correctly. Found by an audit MUTATING it, never by
  reading it. **A control is a claim; mutate it.**
- 🔴 **PREFER REGISTERING OVER EXEMPTING WHEN A TWO-WAY LEDGER GOES RED.** `#1739` added three
  🔴 paragraphs to `audit-pr/SKILL.md` with neither a `RULES` probe nor a `NOT_A_RULE` anchor.
  The cheap fix was to anchor all three as not-rules; all three were instead REGISTERED,
  because each ends in an imperative an audit round performs or fails to. Precedent:
  `NOT_TABLE_DRIVEN` in `test_mutation_battery_anchors.py`, where exempting "with a reason
  true only as a naming accident" was the wrong call and registering bought real protection.
- 🔴 **WIDENING A LEDGER'S SCAN CAN BE THE FIX, AND IT MAKES THE GUARD WIDER NOT WEAKER.**
  `#1738`'s ledger scanned `SKILL.md` files only; `#1745` moved a mandated sentence into a
  `reference/` sidecar, so the SHRINK arm fired with zero shared files. Fixed by teaching the
  scan that a skill's body includes its `reference/`+`flows/` sidecars — which closes a REAL
  blind spot (a skill could teach the WRONG form in a sidecar, invisibly). Deliberately not
  every `*.md` under a skill dir: `scripts/browser-bridge/` holds `README.md` and test
  fixtures, and a fixture tripping a guard about what a skill TEACHES is a false red with no
  correct fix.
- 🔴 **A CENSUS QUOTED FROM ONE GREP IS A CLAIM, AND `CLAUDE.md`'s CEILING BULLET HAS NOW BEEN
  WRONG FOUR TIMES.** Re-derived 2026-09-17 by TWO independent methods: the union grep returns
  12 paths → 8 real gates + 4 over-matches; a second sweep for module-level `MAX_*BYTES`
  constants found a **ninth the union grep structurally cannot see**
  (`test_validation_prompt.py`'s `MAX_DOC_BYTES`, over a skill SIDECAR). **Nine ceilings, six
  on skill bodies.** Two methods, two different misses — which is the actual lesson, not the
  number.
- 🔴 **TWO STANDING CONSTRAINTS ON THE `resume` SKILL SURVIVE ITS PRUNE — carried here because
  the rank that held them is now closed and they would otherwise vanish with it.** (1) **Do
  NOT merge the two recall commands into one wrapper**: `cairn recall` is per-repo while
  `handoff_search` sweeps all four repos including two client ones, and much of that prose
  exists because getting it wrong leaks client content into a PUBLIC repo. (2) **Do NOT
  retire `handoff_search`** — its measured yield was ~0 and the "1 of 20" figure was retracted
  as an instrument artifact, but the 2026-09-12 session is a genuine yield instance: a
  `skill-usage-telemetry` hit ("`find-session`'s 'both hosts' claim was HALF FALSE") is what
  prompted the instrument-validation control that cracked that investigation. Both
  constraints now live in `~/.claude/skills/resume/reference/handoff-search.md`.
- ⚠ **`ship.sh` falls back from LAN to nebula silently-but-visibly.** 2026-09-17 the laptop's
  `192.168.50.155` did not answer and it converged over `10.42.0.100`, printing both lines.
  That is the designed behaviour; do not read the "unreachable" line as a failed ship — read
  the per-host VERIFIED lines and the final cross-host comparison.

## How to verify
- Both hosts are converged AND the prune is actually DEPLOYED (merged ≠ deployed; `readlink`
  is the arbiter, never a diff):
  ```bash
  readlink -f ~/.claude/skills/resume/SKILL.md     # → /nix/store/…-devrc-claude-skills/…
  wc -c ~/.claude/skills/resume/SKILL.md           # 20,731  (was 51,356)
  ls ~/.claude/skills/resume/reference/ | wc -l    # 6
  ssh zach@10.42.0.100 'wc -c < ~/.claude/skills/resume/SKILL.md'   # 20,731
  ```
- All five reds, at whatever `origin/main` is now (re-fetch first — it moves constantly):
  ```bash
  git -C ~/workspace/devrc fetch origin -q
  git -C ~/workspace/devrc worktree add -f /tmp/mchk --detach origin/main
  nix develop ~/workspace/devrc -c python3 -m pytest \
    /tmp/mchk/scripts/tests/test_handoff_doc_size.py \
    /tmp/mchk/scripts/tests/test_mutation_battery_anchors.py \
    /tmp/mchk/scripts/tests/test_runtime_shebangs.py \
    /tmp/mchk/scripts/tests/test_audit_rule_firing_sweep.py \
    /tmp/mchk/scripts/tests/test_resume_skill_size.py -q -p no:cacheprovider
  nix develop ~/workspace/devrc -c python3 -m pytest \
    /tmp/mchk/scripts/tests/test_subsystem_store_api.py -q -k RETRACTED_two_entry_boundary
  git -C ~/workspace/devrc worktree remove /tmp/mchk --force
  ```
- 🔴 **The unsettled one** — the fzf test failed in the SANDBOX tier and passes on the
  dev-host tier. Only the sandbox tier answers it:
  ```bash
  gh api /repos/innovation-upstream/devrc/commits/<sha>/statuses --jq '.[]|"\(.context) \(.state)"'
  ```
