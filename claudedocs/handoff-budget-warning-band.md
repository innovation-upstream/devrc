<!-- delta: State now only; every other section omitted and therefore untouched -->

## Run this first — the index, one command
```bash
$DEVRC/scripts/cairn-ops/read.sh recall --repo "/home/zach/workspace/devrc"
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Stop sessions burning tokens fighting the handoff-document size ceiling. **The operator
reported this repeatedly and said earlier fixes "either hadn't landed or weren't working".
Five had landed; the diagnosis is that the newest one is arithmetically unable to fire.**

🔴 **A NEW ARC, AND ITS NEIGHBOUR IS `claudedocs/handoff-handoff-doc-prune-exit.md` —
WHICH MUST NOT BE RE-OPENED.** That arc gave the append-only sections an **EXIT** from the
refusal (`--prune`, rule (q)/(r), then `--archive-write` and `--autoevict`). This one is the
other half: **the session should never reach the refusal**, because by then the content is
already composed and the second pass is the cost. Same subsystem, different mechanism,
different closing condition. The related docs — `handoff-handoff-resume-prune.md` (CLOSED),
`handoff-evictable-note-ungated.md`, `handoff-skill-listing-budget.md`,
`handoff-skill-prune-and-gate-floor.md` — are each their own arc too; **do not fold this into
any of them, and do not mint a sixth slug for the same ground.**

- **closing-condition:** `check` — all three, in one session:
  ```bash
  # (a) the fix is on main
  git -C $DEVRC cat-file -e origin/main:scripts/lib/handoff_budget.py && \
    git -C $DEVRC show origin/main:scripts/lib/handoff_budget.py | grep -c 'BUDGET_NEAR_BYTES'
  # (b) the band fires BEFORE a median append can blow the budget, and stays silent below it
  #     (MAX-STEP+1 warns; MAX-STEP is silent) — see `## How to verify` for the exact probe
  # (c) the digest tells a session its budget before it composes: a BUDGET block appears in
  #     `resume-state.sh` output, and flags a doc inside the band
  ```
  ADDRESSED ⇒ arc CLOSED. 🔴 **FROZEN AT ROUND 1.** The `--autoevict`-by-default question
  and the two follow-ups below are **NOT** part of this line; they are separate arcs.

## State now
- **devrc#2001 OPEN** — `fix/handoff-budget-warning-band`, head `26bb403c`, 6 commits,
  +1228/−15 across 9 files, `mergeable=MERGEABLE`. **CI PENDING on all four Tekton legs**
  at the time of writing. Worktree `/home/zach/workspace/devrc-budget-band-a19fb`.
- ✅ **THE DEFECT, MEASURED: `BUDGET_NEAR_BYTES` WAS 4,096 — NARROWER THAN THE MEDIAN
  APPEND IT EXISTS TO CATCH.** Bisected empirically: `budget_warning` was **completely
  silent below 61,441 B (93.75% of the 65,536 ceiling)**. Median handoff append ≈ **5,271 B**
  (median 65.5 net lines × 80.4716 B/line, over 285 commits touching a `handoff-*.md` of
  which **242 grew one**, `git log origin/main -300 --numstat -- claudedocs/`). So a median
  write that entered the band **jumped clean over it** and the first signal was the
  **refusal**, after the content was composed. **The second pass is the token burn.**
- ✅ **FIXED: the band is now `GRANDFATHER_STEP` = 16,384** — one existing constant, not a
  new magic number, and ≈ the measured p90 append (**15,531 B**, 853 B of slack). First
  warning moves **61,441 B (93.75%) → 49,153 B (75%)**.
  **Verified in both directions by the dispatching session, not inherited:** 49,151 silent ·
  49,152 (the boundary) silent · **49,153 warns** · **54,938 warns** · 39,152 silent.
  🔴 **54,938 B is the direct test: it is the size this very session grew a real handoff doc
  to, in total silence, under the old band.** Docs warned fleet-wide: **10 → 24**.
- ✅ **FIXED: the resume digest now has a `BUDGET` block**, wired between `INVESTIGATIONS`
  and `DOD`, reading `MAX_BYTES`/`BUDGET_NEAR_BYTES` from `handoff_budget.py` rather than
  retyping them. Verified live:
  `handoff-cairn-recall-value-instrumentation.md: 47,719 B of 65,536 B … 17,817 B of
  headroom, warning band 16,384 B` / `✅ clear of the warning band.` A grandfathered doc
  reports its **allowance**, not an overage. Degraded paths each print a **named gap**.
- 🔴 **THE FULL `scripts/tests` RUN ON THE FINAL TREE WAS NEVER COMPLETED** — SIGTERM-killed
  at ~55 min (**exit 143, which is a kill and not a failure**) before emitting a verdict.
  What *was* run: all **18 enumerated readers** (`collected=2391 passed=2385 skipped=6
  failed=0`), the base red-proof (`773 collected, 27 failed → 0 at HEAD`), a merged-tree run
  against devrc#1997 (`1166 passed, 0 failed`), and the **shell tier**
  `scripts/tests/test_resume_state.sh` (`ALL PASS`, 22 `ok` lines) — a `SHELL_TESTS` target
  no `--files`/`--targets` invocation can name. **CI is the only authority that has not
  reported.**
- ⚠ **Five defects were found DURING verification, three of which the brief never
  contemplated. All fixed in #2001** — the detail is in `## Gotchas`.
- ⚠ **NO `clawgate-task:` FIELD.** `clawgate_handoff.sh resolve` exited **5**
  (`NOTHING RESOLVED — 0 tasks`); `field <doc>` exited 1 (none present). An unknown session
  id answers 200 with an EMPTY ARRAY, so that zero **cannot** distinguish "touched no task"
  from "wrong id". Not a clean bill of health. No field written, none created.
- ✅ **THIS DOC LANDED: commit `6b630e59`, branch `docs/handoff-budget-warning-band`, opened as
  devrc#2006.** Recorded here because the write-back guard measures "a handoff write since the
  last read of this doc" and the honest order was write → read → PR, so the guard fired on the
  ordering rather than on unrecorded work. ⚠ **AND IT WAS NOT LEAK-SCANNED BY THE GATE:**
  `handoff_doc.py` reported `NO SCANNER FOUND in <repo> — looked for tests/leakscan.py`, which is
  **a PASS BY ABSENCE, not a clean result** (devrc has no such scanner; cairn does). Hand-scanned
  instead — no IPs, no credentials, no prohibited names; every long-string hit is one of devrc's
  own public doc slugs. **Any future handoff written into devrc gets the same non-result** — do
  not read that line as a pass.
- ✅ **THE SUBSYSTEM INDEX WAS WRITTEN — one bullet appended to `cairn/report.md`**, dated
  2026-10-03, covering the inert-guard shape (a guard whose discriminating input cannot reach the
  code it guards) and the printed-token contract's missing mirror half. 🔴 **It took THREE windows
  and the first two were not results:** `--session` on devrc refused outright
  (`transcript cwd does not match` — the session ran in cairn, and the skill forbids falling back
  to the git window there because it is structurally empty too); `--session` on cairn returned
  **100% of paths outside the session cwd**, the subagent-worktree blind spot; `--pr 174,175`
  resolved 46 paths and is the only window that sees a subagent's work. **A well-delegated session
  is exactly the one `--session` sees least of.**
- ⚠ **NO `clawgate-task:` FIELD, AND THE ZERO IS NOT A CLEAN BILL.** `clawgate_handoff.sh resolve`
  exited **5** (`NOTHING RESOLVED — 0 tasks`); `field <doc>` exited 1. An unknown session id
  answers 200 with an EMPTY ARRAY, so that zero cannot distinguish "touched no task" from "wrong
  id". No field written, none created.

## Open investigations — live diagnosis state

### 🔴 OPEN (operator decision, not work) — should `--autoevict` become the DEFAULT rather than a flag discovered after a refusal?
- as-of: 2026-10-03
- **Symptom + exact repro:** the write is refused at `size-ratchet` (exit 14); the agent must
  then pick an exit (`--prune`, `--archive-write`, `--autoevict`) and re-run. That re-run is
  the second pass, and the second pass is what costs tokens. Making `--autoevict` the default
  would remove it.
- **Observed (with values) — it would NOT remove the second pass.** In its own trial
  `--autoevict` reached **3 of 5** docs; **two have zero evictable candidates and still hit
  exit 14**. It **freezes rather than heals** (~20–30 more sessions on the worked doc before
  the problem returns), and the **archive destination is governed by the same ceiling**, so it
  relocates the pressure rather than removing it. `via: measurement`
- **Observed — the module already records four independent reasons against it**, in
  `scripts/lib/handoff_doc.py`: it would make the module the actor for a judgement rule (p)
  itself says it cannot make (*"nothing in this rule can tell a deletion from an eviction —
  the arithmetic is identical"*); a refusal is a no-op while an eviction **writes and commits
  two files**; the proposal run exists to put the diff in the transcript; and rule (r)
  requires an editorial note precisely because a generated one is worse than none.
  `via: code`
- **Ruled out: that this is the cheapest next experiment.** #2001 attacks the cause upstream
  — the session not knowing in time — without auto-deleting anything, so its effect must be
  measured first or an auto-delete default is being justified against a baseline that no
  longer exists. `via: measurement`
- **Leading hypothesis:** the answer is **no**, and the recommendation of both the
  implementing agent and the dispatching session is no.
- **Next probe:** instrument the **outcome distribution of ratchet-refused writes** — second
  pass vs `--override-size-ratchet` vs abandoned record; only the first is the cost. Then
  measure the **human approval rate** of `autoevict_selection` run in proposal mode over the
  corpus, plus the residual it cannot clear. **Run both AFTER #2001 has soaked.**

### 🔴 OPEN — whether the wider band actually changes session behaviour is UNMEASURED, and the thing it fixes is a feedback loop
- as-of: 2026-10-03
- **Observed:** the mechanism is verified (the band fires at 75% instead of 93.75%, both
  directions, and the digest prints the headroom) but **no session has yet run under it**.
  The claim "sessions stop burning tokens" is a behavioural claim and this is a mechanism
  change. `via: measurement`
- **Observed — the failure mode this replaces is on record and is exactly this shape.**
  `handoff_doc.py` already documents that the #1648 pre-write warning *"has been printed on
  every over-budget write since #1648 and the mechanism it names went on regardless: MEASURED
  on one arc, a document a prune had landed UNDER the ceiling more than DOUBLED inside a
  week"*. **A warning that is ignored is the precedent, not the exception** — so a wider
  warning is necessary and may not be sufficient. `via: doc`
- **Ruled out: that the fleet is already safe.** Re-derived over 108 docs: **11 over
  `MAX_BYTES` but all grandfathered (0 over their own allowance)**, and **14** were silent
  while within one p90 append of the ceiling — 7 non-grandfathered
  (`index-store-claims-accuracy` 59,764 · `mention-picker-instrumentation-and-tui` 59,007 ·
  `bridge-unbounded-waits` 58,054 · `handoff-search-index` 57,572 · `mention-detection`
  57,526 · `handoff-resume-skill-trace` 55,915 · `analyze-service-index-backup` 53,242) plus
  7 grandfathered. All 14 now warn. `via: measurement`
- **Leading hypothesis:** the band is necessary; the digest block is the half more likely to
  change behaviour, because it arrives *before* composing rather than at write time.
- **Next probe:** after #2001 merges, pick the next session that touches a doc in the band
  and read whether it evicted *before* composing or after being refused. One observation is
  not a trend — name the session and the doc.

## Next steps (ranked)
1. **MERGE devrc#2001 once its four Tekton legs are green.** Read BOTH surfaces and confirm
   the full set, not just that the present ones passed. 🔴 **CI is the only authority that
   has not reported on this tree** — the local full-target run was killed at 55 minutes.
   Hand-off check if you want it locally first:
   `nix develop /home/zach/workspace/devrc-budget-band-a19fb --command bash
   /home/zach/workspace/devrc-budget-band-a19fb/scripts/run-tests.sh --targets "scripts/tests"
   /home/zach/workspace/devrc-budget-band-a19fb` — budget ≥40 min and **read the per-target
   summary lines, never the exit code; 143 is a SIGTERM**.
   forcing: user — the operator reported this failure repeatedly and said the prior fixes had
   not worked; the fix is built and verified and only the gate is outstanding.
2. **ANSWER THE `--autoevict`-BY-DEFAULT QUESTION.** Evidence is in the investigation block
   above; the recommendation is **no**. This is a decision, not work.
   forcing: user — it is an operator call about an irreversible auto-delete default, and no
   further measurement separates the options until #2001 has soaked.
3. **DOCUMENT THE `BUDGET` BLOCK IN THE RESUME SKILL — blocked on an eviction first.**
   `claude/skills/resume/SKILL.md` has **4 bytes** spendable before breaching
   `MIN_HEADROOM_BYTES` (21,596 of 22,400; headroom 800), so this needs a content eviction
   into `reference/` — a judgement call deliberately excluded from #2001.
   *Closing condition (mechanical): `SKILL.md` names `BUDGET` in its block list and
   `test_resume_skill_size.py` exits 0 with it present.*
   forcing: none
4. **THE `scripts/tests` COLLECTED FLOOR IS 145 TESTS FROM ITS HARD DRIFT CEILING.** It
   collects **16,137** against floor 13,026, whose ceiling (`floor + max(60, floor/4)`) is
   **16,282**; #2001's +29 ate into that. Re-pinning the line conflicts across ~40 open PRs,
   so it is left alone deliberately.
   *Closing condition: a run reports `collected > 16,282`, at which point the gate forces the
   bump and prints the replacement number.*
   forcing: gate — the gate itself will force it; until then a bump conflicts with every open PR.

## Gotchas / decisions / dead-ends
- 🔴 **FIVE FIXES HAD ALREADY LANDED BEFORE THIS ONE — "it hasn't landed" was wrong and "it
  isn't working" was right.** The chain: the pre-write warning (#1648) → rule (p), an
  over-budget doc may not GROW (#1871) → three exits from that refusal, `--prune` (#1960),
  `--archive-write` (#1963), `--autoevict` (#1966). **They gave the wall a door; nothing told
  a session it was approaching the wall.** Do not re-derive this chain — `git log` on
  `scripts/lib/handoff_budget.py` and `scripts/lib/handoff_doc.py` is the record.
- 🔴 **`BUDGET_NEAR_BYTES = 4_096` WAS A BARE CONSTANT.** The long comment above it justifies
  why the warning does not *refuse*; **nothing anywhere justified 4,096.** The replacement is
  derived from the append distribution and says so at the constant, in ONE place, with the
  command that re-derives it.
- 🔴 **THE IMPLEMENTING AGENT SHIPPED A WRONG MEDIAN AND THEN CORRECTED IT** (`54dc2cea`):
  242 is **even**, so the median is the mean of the two middle values (65 and 66) = **65.5**,
  not 66. Rounding first overstates the median append by 40 B — **in the paragraph written to
  stop exactly that.** The trap is now named at the constant.
- ⚠ **THREE OF THE DISPATCHING SESSION'S OWN FIGURES DID NOT REPRODUCE**, and the agent
  re-derived rather than inheriting them: 299/254 commits → **285/242**; median 60 → **65.5**;
  p90 196 → **193**. The window moved between measurements. The conclusion is unchanged and
  robust (4,096 < ~5,271 either way) — but **re-derive before acting on a number this doc
  states.**
- 🔴 **THE BUDGET PROBE MUST NOT IMPORT `handoff_doc` — IT CALLS `cairn_pin.ensure()`.** So
  reading a byte budget required the **pinned cairn client on PATH**, and without it the
  digest reported the budget as an UNKNOWN gap **on exactly the hosts the fallback exists
  for**. Control: `handoff_budget` is stdlib-only and imports fine in the same stripped env.
  `BUDGET_NEAR_BYTES`/`is_handoff_doc`/`gate_enforces` moved there; `handoff_doc` re-exports,
  the precedent `BUDGET_GATE_RELPATH` already set. ⚠ **Found by another file's
  `assert not gaps(out)`, not by anything written for this change.**
- ⚠ **A LEDGER KEY TAKEN FROM A BASENAME INSTEAD OF GIT HANDED ONE DOC ANOTHER'S ALLOWANCE.**
  A fabricated `claudedocs/<basename>` gave a 99,998 B `docs/handoff-<topic>.md` another
  doc's **180,224 B** allowance and a confident ✅. Mutation-killed 1 of 25, with the guard's
  own assertion and a checksum-verified restore.
- ⚠ **`printf "%'d"` IS LOCALE-DEPENDENT** (`LC_ALL=C` → `65536`; UTF-8 → `65,536`).
  Formatting moved to Python; the suite now pins **both** locales.
- ⚠ **A MULTI-LINE TRACEBACK BROKE THE LINE-ORIENTED GAP CHANNEL**, and `_SANDBOX_TOOLS`
  lagged the script so those tests silently measured "no python3". Both fixed.
- ⚠ **THE BRIEF'S `-name '*.py'` READER SWEEP WAS STRUCTURALLY BLIND TWICE** — it missed the
  **shell tier** (`test_resume_state.sh`, a `SHELL_TESTS` target no `--files`/`--targets` can
  name) and the resume skill's own docs. Widen the enumeration repo-wide, not to `*.py`.
- ⚠ **Three ledgers fired correctly and had to be updated**: block order, the `$HANDOFF`
  path-use ledger (which failed on **grow AND shrink**), and `PINNED_PATH_CLOBBERS`.
- 🔴 **devrc's BASE CLONE CARRIES ANOTHER SESSION'S UNCOMMITTED `flake.lock`** — it moves
  **three** inputs (cairn, home-manager, nixpkgs), i.e. somebody's full `nix flake update`.
  Not to be committed, reverted or stashed. Two untracked `claudedocs/scope-chief-*.md` from
  2026-09-19 are stranded there too. **`refs/stash` has 2 pre-existing entries, which proves
  the stack is shared — never `git stash` in this repo.**

## How to verify
```bash
# (a) the fix is on main
git -C $DEVRC show origin/main:scripts/lib/handoff_budget.py | grep -n 'BUDGET_NEAR_BYTES'

# (b) the band fires before a median append, and stays SILENT below it — BOTH directions,
#     because a guard that fires on everything is as useless as one that fires on nothing
nix develop $DEVRC --command python3 - <<'PY'
import sys; sys.path.insert(0, "scripts/lib")
import handoff_budget as hb, handoff_doc as hd
MAX, NEAR = hb.MAX_BYTES, hb.BUDGET_NEAR_BYTES
warns = lambda n: bool(hd.budget_warning("claudedocs/handoff-x.md", "x"*n, "x"*(n-1), gated=True))
for n in (MAX-NEAR-1, MAX-NEAR, MAX-NEAR+1, 54938, 39152):
    print(f"{n:>7,} ({100*n/MAX:5.1f}%) warns={warns(n)}")
# expect: silent, silent, True, True, silent   — and band == GRANDFATHER_STEP
print("band == one GRANDFATHER_STEP:", NEAR == hb.GRANDFATHER_STEP)
PY

# (c) the digest tells a session its budget BEFORE it composes
bash $DEVRC/scripts/resume-state.sh "<path to any handoff doc>" | grep -A3 '^BUDGET'
```
