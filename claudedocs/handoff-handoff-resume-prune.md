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
- Branch: `handoff-resume-prune-proposal`, **pushed to `origin`** — three commits:
  `fcd0ad12` (the proposal doc), `bea9138d` (an `adoption-scan` gotcha), `8d7a3bfd` (this
  handoff doc, landed by `handoff_doc.py --confirm --push`). 🔴 **No PR is open** — opening
  one was not requested and is an outward action left to the operator.
- DONE: `claudedocs/proposal-handoff-resume-prune.md` written and committed (`fcd0ad12`).
  It carries the full measurement set, the four proposed changes P1–P4 and what is
  deliberately NOT proposed.
- DONE: `claude/skills/adoption-scan/SKILL.md` gained the dedup-by-`tool_use_id` gotcha
  (`bea9138d`) — the skill already owned the PROVENANCE half of transcript sweeps but not
  the copy half. 7,306 B, within the 12,038 B skill budget, so no eviction was needed.
  🔴 **Not deployed** — `~/.claude/skills/` is a nix-store symlink, so this needs a
  `home-manager switch` (or `scripts/ship.sh` after merge) before any session loads it.
- DONE: three measurements, each with controls (commands in "How to verify"):
  - **70% of the live corpus is in the three `APPEND_PREFIXES` buckets** — `gotchas`
    1,389,966 B (42.6%), `open investigations` 879,515 B (26.9%); APPEND total 2,279,835 B
    of 3,264,227 B section bytes across 97 live docs. The `REPLACE` buckets self-limit.
  - **All 15 `handoff_doc.py` statuses have fired** over **7,023 deduped invocations**
    (1,328 transcripts). Rarest `doc-per-effort` 10 (0.2%), `no-advance` 31 (0.6%).
    The unanswerable warning is the finding: `DROPS N line(s) that look DURABLE` fired
    **915× = 18.0% of runs**; budget-over 185 (3.6%); budget-near 28 (0.5%).
  - **82% of live docs (80 of 97) declare no `closing-condition`**, holding 73% of bytes;
    14 of the 20 biggest are undeclared. Of the 17 declared: 12 `check`, 5 `judgement`.
- NOT DONE: no implementation of P1–P4. Nothing in `scripts/lib/` changed, and neither
  `handoff/SKILL.md` nor `resume/SKILL.md` was touched.
- Deploy/verify status: nothing deployed. No `home-manager switch` was run this session.
- Subsystem index: BOTH windows read (`--session`, then `--commit` auto-run) — each
  returned `status=no-match` with nothing nominated, so **no index write was proposed**.
  The one durable lesson was routed to `adoption-scan` instead, per the skill's route rule.
- No `clawgate-task:` field: `clawgate_handoff.sh resolve` exited **5** (nothing resolved).
  Its positive control showed the board answered 2 links for a DIFFERENT session, so the
  board is reachable — but a wrong id also answers 200 with an empty array, so this is a
  narrow reading, not a clean bill of health.

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
1. Implement **P4 + P2** — route both SKILL.md bodies to `scripts/handoff-audit.py` (one line
   each), then stamp + age the `gotchas` / `findings` APPEND buckets in
   `$DEVRC/scripts/lib/handoff_doc.py` and report their ages from
   `$DEVRC/scripts/resume-state.sh` using the existing `EXPIRED`/`UNDATED` vocabulary. P2 is
   the prerequisite for P1 choosing WHAT to demote. Each PR must carry its own SKILL.md
   eviction in the same commit — both bodies are already over budget.
   forcing: user — the operator approved "proposal doc + then implement" this session.
2. Implement **P1** — slice-and-demote oversized APPEND sections into
   `claudedocs/refs/<topic>.md` by verbatim python line-range slice, leaving one routing
   line. Needs a backup + union gap-audit before any write, and a mutation battery shaped
   like `scripts/tests/mutation_battery_handoff_archive_and_cap.py` because its reassuring
   answer is a zero ("0 lines lost").
   forcing: user — same approval; this is the item the closing-condition names.
3. Implement **P3** — `resume-state.sh`'s `DOD` block emits a paste-ready
   `closing-condition:` line pre-filled from the doc's `## Goal`. Run the "Next probe" above
   FIRST: if the grandfathering hypothesis is confirmed, P3 is right; if not, delete the
   offer rather than strengthen it.
   forcing: user — same approval.
4. Re-measure DoD coverage (82% today) two weeks after P3 lands; if it has not moved, the
   prose was never the problem and P3 gets deleted.
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
