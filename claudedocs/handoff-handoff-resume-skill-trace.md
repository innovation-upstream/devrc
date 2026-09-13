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
- Branch: work landed via PR from worktrees; base clone is currently on ANOTHER session's
  branch `fix/tmux-osc8-hyperlinks` (PR #1622 open) — do not commit into it.
- Both hosts converged and VERIFIED at `16bfd2cb` (`ship.sh`: 593/552 managed artifacts
  resolve, 0 dangling, 0 stale, cross-host agreement COMPARED).
- **Four PRs merged, shipped and verified by content this session:**
  - `#1606` `44d8847d` — docs correction: the zero-laptop-contribution claim REFUTED, and
    `handoff-handoff-search-index.md`'s residual marked EXPIRED.
  - `#1608` `7e000e6b` — `q_browser_by_domain` rewritten from a quadratic `CROSS JOIN`
    into a linear boundary sweep; `activity-scan.py --days 30` now completes.
  - `#1614` `e6a5ee0d` — `activity-scan.py` degrades ONE section on `CHQueryError`
    instead of aborting the report; exit 3 = PARTIAL.
  - `#1618` `16bfd2cb` — open-investigation blocks carry a machine-visible AGE;
    `resume-state.sh` prints an `INVESTIGATIONS` block and files `EXPIRED` past 14 days.
- **Gated on the MERGED tree, not just the branches** — integration branch with #1614 and
  #1618 merged onto `main`, both `nix build` sandbox tiers run SEQUENTIALLY:
  `pytests TOTAL collected=22909 passed=22907 failed=0`, `SCOPE: FULL (29 of 29 targets)`,
  `RESULT: PASS`; `nodetests SCOPE: FULL, RESULT: PASS`. Read from `nix log`, not from the
  build's stdout (see Gotchas).
- What's IN FLIGHT: nothing of this session's. All claims released.
- 🔴 NO `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** (nothing
  resolved). That cannot distinguish "touched no task" from "wrong session id" — it is
  not a statement that the board is fine.

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

## Next steps (ranked)
<!-- Renumbered 2026-09-12: the previous rank 1 is DONE and all claims against this doc
     were released, so no live claim points at these numbers. -->
1. **Archive + cap the handoff corpus** — the unfinished half of the operator's chosen
   direction ("expire + archive + cap"; expire shipped as #1618). Move the 35 devrc docs
   untouched >30d to `claudedocs/archive/` keeping them indexed, and add a test-enforced
   byte ceiling in the house style (max doc is 314 KB, 24x the 13.2 KB median; 117 docs,
   3.33 MB, ~2 new/day). Touches `claudedocs/**`, `scripts/lib/handoff_index.py`,
   `claude/skills/handoff/SKILL.md` (under its own ceiling — evict in the same commit).
   forcing: user — operator chose this direction explicitly on 2026-09-12
2. **Build the `tool`-emission watcher** designed above — a per-session cross-source
   consistency check (`scripts/collector/tool_emission_watch.py`), sibling to
   `deadman.py`, NOT folded into it: "we independently know this happened and it wasn't
   recorded" is a different claim from "this went silent". Per-session windows, not
   per-day (grouping by `toDate(max(ts))` misattributes multi-day sessions). Verdicts
   `ok` / `gap` / `cannot-tell`, with `cannot-tell` distinct from `ok`. Validate with BOTH
   controls: negative = a synthetic session with 0 claimed and 0 rows must read `ok`;
   positive = the 09-05 laptop/`obs-read` case must produce exactly one `gap`.
   forcing: none
3. **Retract the expired Tekton-capacity claim in `CLAUDE.md`.** It records capacity as
   "not the constraint" (measured 2026-09-10, devrc-ci node at 14% CPU requests). Measured
   2026-09-12: the scheduler refused to place `devrc-ci-gxsd6-gate-pod` for 16 minutes —
   `0/5 nodes are available: 1 Insufficient cpu, 1 node(s) didn't match Pod's node
   affinity/selector, 3 node(s) didn't match PersistentVolume's node affinity`, with
   `tekton-ci-1` at 98% CPU and 8 concurrent `devrc-ci` runs. It drained unaided, so this
   is congestion not breakage — but that sentence is cited as the reason a
   branch-protection question is settled, so a session will reason from it.
   forcing: none
4. **Port the sweep to the two remaining `CROSS JOIN` sites** — `derived_attention_consistent`
   in `scripts/validation/invariants.py`, and the hand-run query in
   `claude/skills/activity/reference/queries.md`. Same quadratic shape #1608 removed;
   narrower windows today. Verify with the same old-vs-new output-equality check.
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

## How to verify
- The four PRs landed, by CONTENT not ancestry (a squash merge never makes the branch head
  an ancestor):
  ```bash
  git -C ~/workspace/devrc show origin/main:scripts/session-analysis/activity-scan.py | grep -c SECTION_UNAVAILABLE_MARK   # 2
  git -C ~/workspace/devrc show origin/main:scripts/resume-state.sh | grep -c INVESTIGATIONS                              # 2
  git -C ~/workspace/devrc cat-file -e origin/main:scripts/tests/test_resume_state_investigations.py                      # exit 0
  ```
- The expiry mechanism fires and stays silent correctly (both directions):
  ```bash
  bash ~/workspace/devrc/scripts/resume-state.sh claudedocs/handoff-browser-bridge-2026-07-31.md   # EXPIRED 43d x2
  bash ~/workspace/devrc/scripts/resume-state.sh claudedocs/handoff-handoff-resume-skill-trace.md  # within window
  ```
- The OOM fix, against the live server (creds via the `activity` skill's SOPS recipe):
  ```bash
  python3 ~/workspace/devrc/scripts/session-analysis/activity-scan.py --days 30   # exit 0, populated
  ```
- The laptop resume figures that refuted this doc's original claim: query
  `activity.events` for distinct sessions where `skills_used`/`skills_invoked`/
  `commands_typed` contains `resume`, grouped by host, over 30 days — laptop 17,
  workbench 158.
