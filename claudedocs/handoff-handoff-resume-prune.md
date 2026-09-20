# Handoff: handoff-resume-prune — 2026-09-20

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Port the transferable parts of `prune-memory`, `prune-skill` and `the-algorithm` into
`/handoff` + `/resume` — scoped by the operator to the **doc-corpus lifecycle** and **the
guard stack**, with deletion on the table and every change landing inside the two existing
skills (no fourth `prune-*` sibling).

🔴 **A NEW ARC, and `handoff_doc.py --new-effort` was passed deliberately.** Three neighbours
were read before asserting that: `handoff-agent-overguarding.md` is the arc that SHIPPED
`the-algorithm` as a skill (frozen at round 1; this one APPLIES it to a named target and
finds the opposite — the guard stack is defended); `handoff-handoff-resume-skill-trace.md`
is CLOSED, verdict ADDRESSED 2026-09-17, and pruned the `resume` skill BODY, which this arc
explicitly does not touch; `handoff-guard-existence-gate.md` is the Stop-hook fixture-string
bug, unrelated.
- **closing-condition:** `check` — P1 (slice-and-demote of the APPEND buckets into
  `claudedocs/refs/<topic>.md`) is merged to `origin/main`, AND a re-run of
  `python3 $DEVRC/scripts/handoff-audit.py $DEVRC/claudedocs` reports the live corpus
  APPEND share **below 70%**. P2–P4 merging does NOT close this arc.

## State now
- Branch: `handoff-resume-prune-proposal`, pushed to `origin`. **No PR open** — an outward
  action left to the operator.
- **P4 is DONE and merged to the branch (`b50709ca`)** — `handoff/SKILL.md` step 5 now names
  `scripts/handoff-audit.py`, and that tool's own banner was corrected (below). Body went
  20,088 → 20,267 B, inside the 20,300 B enforced budget. **1,900 tests green** across all
  ten modules that read the handoff/resume bodies.
- 🔴 **P2 is REFUTED AND DELETED — do not build it.** Measured, twice over; see Gotchas.
  `claudedocs/proposal-handoff-resume-prune.md` carries the struck-through section and the
  numbers (`bb531558`).
- **P4's `/resume` half was deleted too** — `/resume` is read-only re-entry, so a
  corpus-maintenance pointer there is prose nobody acts on (the-algorithm §1).
- NOT DONE: **P1** (slice-and-demote) and **P3** (DoD offer). P1 is what the closing
  condition names; nothing else closes this arc.
- ⚠ **The ranked list below was RENUMBERED** when P2 died: P1 is now rank 1. No `claim-work`
  claim was ever taken on this doc, so no live claim was re-pointed — but re-rank with that
  in mind, since the rank is half a claim's identity.
- Deploy/verify status: **nothing deployed.** `claude/skills/handoff/SKILL.md` is a nix-store
  symlink, so step 5's new line reaches no session until a `home-manager switch` (or
  `scripts/ship.sh` after merge). The `handoff-audit.py` fix is a repo script and is live on
  this checkout immediately.
- Subsystem index: both windows read (`--session`, then `--commit`), both `no-match`, nothing
  nominated, no index write. The one durable lesson went to `adoption-scan` (`19e5849e`).
- No `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5**. Its positive control
  showed the board answered for a DIFFERENT session, so the board is reachable — a narrow
  reading, not a clean bill of health.

## Open investigations — live diagnosis state

### Why 82% of live handoff docs carry no `closing-condition` despite a refusal that requires one
- as-of: 2026-09-20
- **Symptom + exact repro:** `/resume` step 5 mandates a DoD verdict as its headline, but for
  80 of 97 live devrc docs the only honest verdict is UNMEASURABLE. Reproduce:
  `python3 -c "…closing_condition(doc).is_declared…"` over `$DEVRC/claudedocs/handoff-*.md`
  (full command in "How to verify").
- **Observed (with values):** 80/97 undeclared = 82%, holding 2,414,878 B of 3,299,446 B
  (73%). Among the 20 biggest docs, 14 undeclared. Declared kinds: `check` 12, `judgement` 5.
  Meanwhile `undefined-done` (the refusal that requires the field) HAS fired — 51 times,
  1.0% of 7,023 invocations.
- **Ruled out:** "the refusal is broken / never fires" — it fired 51 times in the deduped
  transcript sweep; `via: measurement`.
- **Ruled out:** "my parser is undercounting" — the first pass used a regex of mine and WAS
  wrong (scored `handoff-cairn-phase3.md` as undeclared while that doc names the field 19
  times); re-derived through `handoff_doc.closing_condition().is_declared`, the authority
  `resume-state.sh:2146` names, with controls built from real corpus text;
  `via: measurement`.
- **Leading hypothesis:** the refusal is **grandfathered for pre-existing docs by design**
  (`undefined_done_report(… base_had_one, is_new_doc)`), so it only bites new documents.
  The corpus therefore never converges: every doc written before the rule stays undeclared
  forever, and those are the big old ones. Not yet confirmed against the grandfathering code
  path.
- **Next probe:** read `undefined_done_report` and the `is_new_doc` / `base_had_one` arms in
  `$DEVRC/scripts/lib/handoff_doc.py`, then correlate declared-ness against each doc's FIRST
  commit date:
  ```bash
  for f in $DEVRC/claudedocs/handoff-*.md; do
    printf '%s %s\n' "$(git -C $DEVRC log --diff-filter=A --format=%cs -- "$f" | tail -1)" "$f"
  done
  ```
  If every declared doc postdates the rule's landing, the hypothesis is confirmed and P3 is
  the right shape; if not, the prose is being ignored for some other reason and P3 should be
  deleted rather than strengthened.

## Next steps (ranked)
1. **P1 — slice-and-demote the APPEND buckets** into `claudedocs/refs/<topic>.md` by verbatim
   python line-range slice, leaving one routing line. 70% of the corpus is in those three
   buckets and they cannot shrink by design. Needs: a backup, a union gap-audit (every source
   line present in doc ∪ refs) BEFORE any write, and a mutation battery shaped like
   `scripts/tests/mutation_battery_handoff_archive_and_cap.py` — its reassuring answer is a
   zero ("0 lines lost"), which is indistinguishable from a detector wired to nothing.
   ⚠ P1 does NOT need P2: it runs inside `handoff_doc.py`, which can `git blame` the doc
   itself for the per-bullet ages it needs.
   forcing: user — the operator approved implementation this session.
2. **P3 — the paste-ready `closing-condition:` offer** in `resume-state.sh`'s `DOD` block.
   🔴 Run the "Next probe" in Open investigations FIRST: if the grandfathering hypothesis is
   confirmed, P3 is the right shape; if it is not, the prose was never the problem and P3
   should be DELETED rather than strengthened.
   forcing: user — same approval.
3. Re-measure DoD coverage (82% today) two weeks after P3 lands; if it has not moved, delete
   the offer.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **Do not re-propose what is already built.** `scripts/handoff-audit.py` (the
  `skill-audit.py` analogue, measurement-only), `claudedocs/archive/` (35 docs, #1627),
  `claudedocs/refs/` (592 KB sink), the 65,536 B ceiling + 11-entry grandfather ratchet, and
  `handoff_doc.budget_warning()`'s eviction ladder ALL exist. `claudedocs/proposal-handoff-doc-bloat.md`
  (2026-08-29) already argued "prune-skill's method, one level down". **The gap is routing:**
  grep-verified, neither SKILL.md names `handoff-audit.py`, `archive/`, `refs/` or any budget.
- 🔴 **RETRACTED — "the archive sink exists and nothing routes to it" is FALSE.** Only 6 of 97
  live docs are untouched >30d and **zero** >60d; the age-based archive rule is working. Dead
  docs are not the problem — live ones growing are (biggest is 194,314 B, touched 5 days ago).
  Do not re-derive this.
- 🔴 **RETRACTED — the first closing-condition figure came from my own regex and was wrong.**
  It required the field at line-start; the authority (`handoff_doc.closing_condition`) matches
  it anywhere per `resume-state.sh:2190`. The 82% survived re-measurement, the derivation did
  not. **Use the authority, never a second regex.**
- 🔴 **A naive transcript grep for `status=<x>` counts SKILL LOADS, not firings** — the
  strings live in `claude/skills/handoff/SKILL.md`, injected into every transcript that loads
  `/handoff`. Count only from `tool_result`/`toolUseResult`, and **dedupe by `tool_use_id`**:
  raw was ~8,500 matches, 40% of them copies from resumes/compaction. Same lesson as
  `scripts/audit-rule-firing-sweep.py`, which says so in its own header.
- 🔴 **A synthetic fixture failed the control for `closing_condition()`** — it walks
  `split_sections`, so text with no `## ` heading can never declare anything. Build controls
  from REAL corpus text (positive: `handoff-audit-pr-ladder.md`; negative: `handoff-laptop-freezes.md`).
- **`test_handoff_doc_size.py` already decided archive-is-not-exempt** — I checked this as a
  suspected sink-vs-gate conflict with `prune-skill`'s "never prune a sink" rule. It is not
  one; the reasoning is in that test's header. Don't re-open it.
- **Decision: no `/prune-handoff` skill.** the-algorithm §5 (the fix for over-guarding is
  never another guard) plus the skill-listing budget, which would need a tier entry and an
  eviction in the same commit.
- **Decision: delete no refusal.** the-algorithm §1 asks each requirement to name its maker
  and recurrence; all 15 can, by firing count. That was NOT the expected answer going in.
- **Decision: do not promote `durable-drop` to a refusal** despite its 18% firing rate. P1
  removes its cause (a moved-and-routed line is not a line about to be deleted) instead of
  adding a gate people would learn to click through.
- ⚠ **Observation for a DIFFERENT arc, recorded not claimed:** the handoff write-back Stop
  hook armed this session on `handoff-cairn-phase3.md`, `handoff-laptop-freezes.md` and
  `handoff-source-repo-parity.md` — all three "read" only by a **bulk corpus scan** (every
  doc's bytes parsed to compute the APPEND share), never opened as context and never worked.
  That is a different false-arming class from the fixture-string one
  `claudedocs/handoff-guard-existence-gate.md` closed. Not investigated here, and NOT filed
  as work on that arc — its closing-condition is frozen.
- **Out of scope by the operator's answer:** pruning the two SKILL.md bodies themselves
  (handoff 20,088 B, resume 20,731 B against a 12,038 B budget). Note `resume`'s ceiling is
  pin-bound — ten test modules pin literal strings in that body — so trimming it means MOVING
  pins, not cutting text.

- 🔴 **P2 (stamp + age the APPEND buckets) is REFUTED. Do not rebuild it.** Two independent
  findings, and the second is the decisive one. (a) **Stamping was the wrong mechanism and
  the code I was about to mirror said so**: `resume-state.sh`'s own comment records that the
  introducing-commit pickaxe dated **478 of 478** investigation blocks — a stamp is the most
  PRECISE clock, never the only one. Stamping the 3,732 gotcha bullets at ~12 B each would
  have **ADDED 44,784 B to the 3.3 MB corpus this whole effort exists to shrink.** (b) **The
  premise itself is false in this corpus**: aged all 3,732 bullets by `git blame` — p50
  16.5d, p90 28d, **max 39d, ZERO over 90d**. At the 14-day investigations window **55%
  would flag**, against the **3%** that design accepted and the **18%** it explicitly
  rejected as "a gate that fires on a fifth of every doc is one everybody clicks through".
  The reason the corpus is young is that **the archive rule already works** (0 live docs
  >60d), so P2 would have duplicated a mechanism that is already doing the job.
- **`git blame` is the cheap clock for handoff-doc content, and it was CONTROLLED.** One
  `git blame --line-porcelain` per doc is **85 ms on the largest doc (1,906 lines)** and
  yields per-line dates, where the pickaxe costs one `git log -S` per bullet (median 20, max
  259 per doc). Blame reports LAST-TOUCHED, so it is a lower bound on true age — validated
  against the pickaxe on the oldest content in the corpus: **38d vs 38d, exact agreement on
  4 of 4**. Use blame; state the floor semantics.
- 🔴 **`claude/skills/handoff/SKILL.md` is PIN-BOUND, NOT PROSE-BOUND — budget an eviction
  that FAILS.** Twice this session a block I had verified as redundant (the
  `OPENCODE_SESSION_ID`/`ROLES UNAVAILABLE` sentence; the `--exclude` paragraph) turned out
  to be required verbatim by a test ledger — `test_resume_state_clawgate.py`'s
  `test_handoff_skill_pins` (14 phrases) and `test_subsystem_touch.py`. **Citing where the
  content also lives is NOT sufficient to delete it**, which is exactly what
  `prune-skill` §3 warns and what I did anyway. The working method: grep the candidate's
  distinctive fragments against **every test that reads the body** (10 modules) before
  cutting, and if nothing is cuttable, **shrink your own addition** rather than raise
  `MAX_BYTES`.
- 🔴 **`scripts/handoff-audit.py` was MISINFORMING every reader, and P4's routing line would
  have amplified it.** It printed *"No gate in this repo measures a handoff doc"* — true when
  it shipped 2026-09-01, false from 2026-09-13 when #1648 added a real 65,536 B per-doc
  ceiling (`test_handoff_doc_size.py`) that goes red on `main` for everyone. Fixed in
  `b50709ca`: the banner now distinguishes the unenforced 12,288 B target from the enforced
  ceiling and reads the latter from `handoff_budget`, loaded **defensively** (returns None)
  because the tool audits other repos' corpora where that gate legitimately does not exist.
  Both controls run: devrc prints 65,536 B, a missing module omits the line.
- **Decision: P4 shipped for `/handoff` only.** `/resume` is read-only re-entry; a
  corpus-maintenance pointer there is prose nobody acts on (the-algorithm §1 — question the
  requirement, and this one could not name who would act on it).

## How to verify
Re-derive every number in the proposal:
```bash
# (1) APPEND-bucket share — uses the tool's own classifier, not a heading grep
python3 - <<'PY'
import importlib.util, pathlib
from collections import Counter
R = pathlib.Path("/home/zach/workspace/devrc")
spec = importlib.util.spec_from_file_location("hd", R / "scripts/lib/handoff_doc.py")
hd = importlib.util.module_from_spec(spec); spec.loader.exec_module(hd)
tot = Counter(); n = 0
for d in sorted((R / "claudedocs").glob("handoff-*.md")):
    _pre, secs = hd.split_sections(hd.split_front_matter(d.read_text(errors="replace"))[1])
    for heading, body in secs:
        b = len(("\n".join(body) if isinstance(body, list) else str(body)).encode())
        tot["APPEND" if hd.append_bucket(heading) else "REPLACE"] += b; n += b
print(tot, f"APPEND share {100*tot['APPEND']/n:.0f}%")
PY

# (2) closing-condition coverage — through the AUTHORITY, with real-text controls
#     positive control: handoff-audit-pr-ladder.md MUST be declared
#     negative control: handoff-laptop-freezes.md MUST NOT be

# (3) guard firing — tool output only, deduped by tool_use_id
find ~/.claude/projects -name '*.jsonl' -print0 | xargs -0 grep -l 'handoff_doc\.py' | wc -l   # 1,328
```
Controls that MUST hold before any of these numbers is readable: negative control
`status=zzz-not-a-real-status` → 0; positive control `proposed`+`written` → 3,124.

The proposal itself: `git -C $DEVRC show fcd0ad12 --stat` and
`$DEVRC/claudedocs/proposal-handoff-resume-prune.md`.
