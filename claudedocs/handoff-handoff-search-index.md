# Handoff: handoff-search-index — 2026-09-03

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
Give the handoff corpus a queryable index, because git gave it redundancy but no retrieval.
424+ docs / 8.6 MB across four repos were readable only by knowing the slug.

## State now
🔴 **THE MACHINERY IS COMPLETE AND LIVE. THE CONSUMER IS INERT.** Both halves are true and
they are separate claims — the earlier "NOT deployed" status is still superseded, and
"live" was never the same as "used".

- **Merged:** `devrc#1209` (`45930d644`) index · `#1244` (`1b769b64b`) cairn I/O-stall classifier ·
  `#1264` (`baa95854`) this doc · `#1267` (`d86b4e45`) incomplete-read delete authority ·
  `#1295` (`3e7d79a4`) the `/resume` consumer · `#1307` (`bb6e46ee`) ARM the timer.
  Plus `homelab-infra` `d2c9c49a` — the rescued untracked handoff doc.
- **Deployed and verified, 2026-09-04.** `ship.sh` converged BOTH hosts at `bb6e46ee`
  ("converged + verified — 2 hosts compared"). `readlink -f ~/.claude/skills/resume/SKILL.md`
  resolves into a NEW store path carrying the wiring — merged AND live are separate claims and
  both were checked.
- 🔴 **The live-Postgres path is EXERCISED — the gap that stood from the first commit is closed.**
  `--rebuild --write` → `wrote 4647 section row(s) … (after DELETE of 4 repo label(s) — one
  transaction)`. The `GENERATED … STORED` `tsv` column was accepted and the GIN index built for
  the first time; both had only ever been pinned as SQL text.
- 🔴 **The TIMER has run on its own**, which is the only thing that tests the unit's environment:
  `Result=success ExecMainStatus=0`, `wrote 4651 section row(s)`, `warnings: none`, 21 s,
  next fire ~6 h. Not inferred from the flag — read from `journalctl --user -u
  handoff-index-sync.service`.
- **Query path live:** `backend=postgres`, `indexed_sections=4651`. 🔴 `backend=` IS the
  discriminator; a silent fall-back to `memory` renders identically otherwise.
- 🔴 **NEW 2026-09-06 — RANK 1 IS ANSWERED AND THE ANSWER IS NO.** In the 34 h between `#1295`
  merging and this measurement, **1 of 14 `/resume` runs across BOTH hosts** invoked
  `handoff_search.py`, and that one was not the step firing. Nothing calls the index. The
  diagnosis is placement, not motivation — full evidence in "Open investigations" below.
- 🔴 **THE RANK-1 FIX IS BUILT — `devrc#1332`, audited over two rounds.** The query moved out of
  step 3's prose into step 4's fence, beside `cairn recall`, re-keyed from an open item to the
  handoff's TOPIC (an item-keyed step can only run when an item exists — that was the
  conditional). Guarded structurally by
  `test_the_query_shares_a_FENCE_with_cairn_recall`. ⚠ **Built and merged is NOT adopted:** the
  5/6 rate was measured with ONE command in that step, so co-locating a second is a PREDICTION.
  Re-run check 1 below after ~10 more runs; until then nothing here says the fix worked.
- **Claim STILL HELD:** `handoff-search-index-1`. 🔴 **Release it explicitly — merging `#1332`
  does NOT release it:** `claim-work --release handoff-search-index-1`. The namespace is global,
  so an unreleased claim reads as rc 10/11 "taken" to a later session under a different identity
  and blocks the *new* rank 1 with a description of work that is already finished.

## Open investigations — live diagnosis state

### A repo that MEASURES but whose every doc is unreadable has its rows deleted, rc 0, no PARTIAL notice
- **Symptom + exact repro:** two repos, one healthy, one whose doc blob is deleted from
  `.git/objects`; then
  `handoff_index.py --repo <good> --repo <bad> --rebuild --write`.
- **Observed (with values):** bad repo derives `unmeasured=None docs=0
  unreadable=('claudedocs/handoff-b.md',)`; `partial_scope_warnings() == ()`;
  `rebuild_refusal() is None`; run exits **rc 0** with `DELETE params =
  [['badrepo','goodrepo']]` and `wrote N section row(s)`. The success line does **not** say
  PARTIAL. The only signal is one `⚠ UNREADABLE` stderr line per doc.
- **Ruled out:** that the new `RepoDerivation.unreadable` field already covers it — it is
  read only on the `handoff_search` rc-7 path, never by `rebuild_delete_labels`.
  via: code
- **Ruled out:** that this was introduced by the P1 work — the classification predates it and
  was filed rather than patched, deliberately. via: measurement
- **Leading hypothesis:** a repo whose docs could not be read is not a repo that MEASURED;
  classifying it UNMEASURED makes the existing partial/refusal machinery cover it.
- **Next probe:** `git grep -n "unmeasured is None" scripts/lib/handoff_index.py` — the two
  sites (`rebuild_delete_labels`, `partial_scope_warnings`) plus the global zero-rows refusal
  in `rebuild_refusal` are the whole surface.

### The same hazard has now appeared in FOUR spellings — the shape, not the instances, is the open question
- **Symptom + exact repro:** each round of review found one more way for a rebuild to delete
  rows it should not.
- **Observed (with values):** (1) unpredicated `TRUNCATE` — emptied the table when every repo
  failed to resolve, exit 0. (2) delete scope computed over *stored* labels while the warning
  reasoned over *configured* labels — deleted `civitai` under a renamed checkout. (3) the
  refusal checked *unreadable* when the risk was *unconfigured* — with `$DATAPACKET`/`$CIVITAI`
  unset, `--rebuild --prune --write` bound `DELETE ['civitai','datapacket-talos','devrc',
  'homelab-talos']` at rc 0. (4) the unreadable-docs case above.
- **Ruled out:** that these are independent bugs — each was created or left half-closed by the
  previous round's fix. via: measurement
- **Leading hypothesis:** the delete scope is derived from a *config* view while the table
  holds a *stored* view, and every fix so far has patched one crossing of that boundary.
- **Next probe:** ask whether any single invariant ("never delete a label this run did not
  itself measure and re-insert") would have prevented all four.

### RESOLVED — the four-spelling shape: one invariant does close it, at one level
- **Answer:** yes for `derive_repo`'s outputs, no for the level above. The shape was named in
  `#1267`: *DELETE authority was granted by a NEGATIVE predicate — the absence of whichever
  failure had already been seen — instead of a positive demonstration that the run holds a
  complete replacement.* A blacklist re-opens the hole for every unmet failure, which is why
  four rounds never converged. The fix inverts the default in one function.
- **Observed (with values):** a round-3 blind audit enumerated every way a run can fail to hold a
  complete replacement — `unmeasured`, `unreadable`, disk incompleteness, zero-row docs, the
  `errors="replace"` decode — and could construct no fifth spelling through that path.
- **Ruled out:** that a fifth spelling exists in `derive_repo`'s own outputs — the enumeration
  above is exhaustive over its return values. via: measurement
- **Ruled out:** that the label-computation fix widened the residual — collision groups measured
  byte-identical across 15 input shapes before and after. via: measurement
- **Residual, still open:** authority is demonstrated per DERIVATION and exercised per LABEL, so
  two repos sharing a basename let the healthy twin's completeness authorise deleting the broken
  twin's rows. Pre-existing, measured identical at base, NOT a regression. Pinned by
  `test_through_main_the_residual_is_recorded_and_the_report_is_true`, a tripwire that FAILS the
  day it is fixed.

### RESOLVED — the unreadable-docs delete path
- **Answer:** fixed in `#1267` after five audit/fix rounds. A repo whose docs could not be read no
  longer obtains delete authority.
- **Ruled out:** that setting `unmeasured` was the right fix — it would conflate "nothing came
  back" with "not everything came back" (the conflation this module was burned by three times)
  and would hide readable docs from `--offline` search over one bad blob. via: code

### RESOLVED — does `/resume` actually query the index? 1 of 14 runs, and that one was not the step firing
- **Answer:** no. The index is live, correct and unused. This was the effort's own rank-1 test —
  *"the only real test of whether the effort was worth building; everything else is machinery"* —
  and it fails at the call site, not in the machinery.
- **Symptom + exact repro:** count `/resume` runs since `#1295` merged (2026-09-04T17:11:50Z)
  against those that actually invoked the tool, on both hosts. 🔴 **A MENTION IS NOT AN
  INVOCATION** — the deployed `SKILL.md` body *contains* the command string, so
  `grep -l handoff_search` over transcripts matched **24** files and is entirely false. Only a
  Bash `tool_use` whose `command` contains `handoff_search.py --` counts. See "How to verify".
- **Observed (with values):**
  - **14 `/resume` runs** (ran `resume-state.sh`): **8** workbench, **6** laptop.
  - **1 invocation**, workbench session `e3dc23e9`. Laptop: 6 runs, **0** invocations — its one
    `grep`-level match has 0 Bash invocations, verified.
  - 🔴 **That one hit was driven by the staleness alarm, not by the step.** The `SKILL` block told
    that session its loaded copy was 1 commit behind; it then read the step off `origin/main` and
    stated it was following that text rather than the copy it had loaded. Its result — 3 hits,
    best `rank=1.1667`, generic gotcha sections — it judged irrelevant, finding nothing any prior
    session had ruled out. **Yield to date: 0.**
  - ⚠ **The denominators, stated so they reconcile** (an audit found 8 − 1 = 7, not 6): the
    workbench **8** = **1** that fired + **6** analysed as non-firing + **1** that was the
    measuring session itself, excluded as the instrument. All figures are as of
    **2026-09-06T04:00Z**; the corpus grows, so a later re-run reports larger numbers rather
    than contradicting these — a re-derivation ~1 h later measured 10/2 on the same needle.
  - **The 6 workbench non-firing runs all met the trigger.** All 6 ran `claim-work` (1–20×) and 5
    made edits (2–52 `Edit`/`Write` calls) — actively working ranked items, not reporting and
    waiting. (The six docs are client-repo topics and are deliberately not named here; this repo
    is public. Re-derive them from the transcripts with the "How to verify" query.)
  - **5 of those 6 handoffs carry an `## Open investigations` section** — precisely the case the
    step exists for.
  - 🔴 **THE DISCRIMINATOR:** step 3's *sibling* check `git log --since=<doc-date>` — same trigger,
    same block, different tool — fired **0 of 6**. Step 4's `cairn recall` — a numbered,
    unconditional step with a fenced command — fired **5 of 6**.
- **Ruled out:** that the non-firing is correct restraint (the step is conditional on "before
  working any open item", and a resume that reports and waits never reaches it). All six went
  well past that: every one ran `claim-work`, five made edits. via: measurement
- **Ruled out:** that the sessions lacked open items to work — 5 of 6 resumed a doc with an
  `## Open investigations` section. via: measurement
- **Ruled out:** that the deployed skill was stale in those runs, i.e. that they never saw the
  step — the `SKILL` block reports CURRENT, and the one session that WAS behind is the only one
  that fired. via: measurement
- **Ruled out:** that it is specific to `handoff_search` (tool unfamiliarity, cost, output
  distrust) — the co-located `git log --since` check, an ordinary command every session already
  uses, fired 0/6 in the same block. via: measurement
- **Leading hypothesis:** **placement and conditionality, not motivation.** An agent executing
  this skill reliably performs numbered unconditional steps and reliably skips conditionals buried
  in a step's narrative prose, however loud the 🔴. Two independent conditional checks in step 3:
  0/6. One unconditional numbered step next door: 5/6.
- **Next probe:** none needed to establish the finding. 🔴 **The promotion ALREADY LANDED in
  `#1332`** — do not re-do it; what remains is the re-run. Re-run check 1 under "How to verify"
  after ~10 further runs, **raising its `CUT` to `#1332`'s merge time first** (left at `#1295`'s
  it counts the 14 pre-fix runs in the denominator, so a fully successful fix reports ~10/24 and
  reads as a failure). The prediction is that it tracks `cairn recall`'s 5/6, not step 3's 0/6.
- **Residual, NOT measured:** whether the index, once actually queried, *yields* anything. n=1
  query returned nothing useful, which is no evidence either way about hit quality. Adoption and
  yield are separate questions and only the first is answered.

## Next steps (ranked)
1. **RE-MEASURE ADOPTION — the fix shipped as `#1332` and nothing yet shows it worked.** Run
   check 1 under "How to verify" on BOTH hosts after ~10 further `/resume` runs. The prediction
   is that the query tracks `cairn recall`'s 5/6, not step 3's 0/6; the residual is that the 5/6
   was measured with ONE command in that step. ⚠ **A green test is not adoption** — the guard
   pins WHERE the command sits, which is a claim about placement, never about firing.
   🔴 Two mutants are known to survive every guard and are named in the test docstring: a gating
   sentence above the fence, and a conditional comment inside it. Both re-create the hazard
   without moving the command. ⚠ **Correction:** an earlier version of this item said
   `SKILL.md` is byte-capped and an eviction was needed. **That was false** — the caps cover
   `browser`, `handoff`, `prune-skill` and `RULES.md`, not `resume`; `#1332` added **+3,688 B**
   (41,852 → 45,540, measured by `git cat-file -s` at base `10d437c9` and head `00e803a2`) with
   no eviction, correctly. ⚠ An earlier draft of this correction said "~1.9 KB", which was itself
   wrong — that was one commit's delta, not the PR's.
   🔴 **Raise check 1's `CUT` to `#1332`'s merge time before re-running it.** Left at `#1295`'s,
   the 14 pre-fix runs stay in the denominator and a fully successful fix reads as a failure.
   forcing: none
2. **The label/derivation granularity residual** — `scripts/lib/handoff_index.py`,
   `rebuild_delete_labels`. Its own round, because the fix moves the delete scope. The tripwire
   test `test_through_main_the_residual_is_recorded_and_the_report_is_true` fails the day someone
   does it, which is the intended signal. ⚠ Ranks below 1: machinery on a consumer nothing calls.
   forcing: none
3. **The empty-label display residual** — an empty label renders blank on FOUR surfaces; the
   operator sees THAT rows were deleted, not WHICH. 🔴 The fix belongs at `main` as an input
   rejection (`RC_USAGE`), NEVER in the renderers — a `label or "(unnamed)"` there would
   re-introduce the exact falsy-string shape three audit rounds swept out of the decision path.
   forcing: none

## Gotchas / decisions / dead-ends
- **`--offline` is the useful surface today** — it answers from git refs with no database, and
  a different ranker. It is how everything in this doc was verified.
- **The index is DERIVED and DISPOSABLE** — `--rebuild` truncates by design; git stays the
  system of record. Never treat the table as authoritative.
- **Indexed from git refs, not the working tree.** This box carries ~640 worktrees across four
  repos; a disk scan would index mid-edit branches, the same doc N times, and stale orphans.
- **A doc on disk but NOT in the mainline ref is reported as a durability hole and deliberately
  never indexed** — indexing it would let the search answer *from* the hole and conceal it.
  That is what surfaced `handoff-limewire-torrent-comps.md`, untracked for six days as the sole
  copy; rescued as `homelab-infra` `d2c9c49a`.
- **Rejected: S3/MinIO for the corpus.** 8.6 MB total. Object storage adds a fourth copy and
  key-lookup, not query — git already gives redundancy. A Postgres text column + GIN gives the
  thing that was actually missing.
- **Rejected: adding `.md` to `captured_text_scan.SCANNED_SUFFIXES`.** Its premise is free text
  where a data field should be; handoff docs are prose throughout, so it would fire on every
  doc and be disabled.
- **Rejected: a "will actually write" boolean** to fix plan sentences printed on refusing runs.
  That boolean *is* the gate's verdict in a second spelling, free to drift. Fixed by ordering —
  the plan prints only after every gate passes.
- **Prose is pinned as a WHOLE NORMALISED STRING** (`_EXPECTED_ORPHAN_WARNING`), not
  substrings — a substring pin let a reword mutant survive a green sweep.
- **The cairn CI red was an I/O stall, not a code failure.** `run_cairn` passes `--timeout 5`
  against a `_replace_bytes` that fsyncs file *and* parent dir inside the request — 12× tighter
  than the store-api's 60 s `HANG_TIMEOUT`, which is why that file was the frequent casualty.
  Fixed upstream by store siting (`b4fde334`); `#1244` adds the classifier so a stall stops
  reading as a code failure. Diagnosis, not tolerance — no bound moved, nothing retries.
- **Branch protection is currently OFF on `devrc`** by deliberate operator decision, so nothing
  blocks a merge and the human is the gate. Run both tiers on the MERGED tree and name the base
  sha in the claim.

- 🔴 **A HAND-RUN NEEDS `KUBECONFIG=$KC_HOMELAB`.** This repo leaves `KUBECONFIG` unset on purpose
  so a bare `kubectl` cannot hit prod, so the DSN read dies with `CalledProcessError` on
  `kubectl -n mailbox get secret` — loudly, before touching the database. The UNIT sets it; only
  the hand-run path lacks it. Measured while arming: the first `--write` failed exactly this way.
- **`indexed_docs` and the derivation's `docs=` count different things, both correctly.** 464
  derived vs 381 indexed is NOT slug collisions: sections match EXACTLY at every level, and an
  overwritten doc would have taken its sections with it. The gap is docs that yield zero sections.
- **`indexed_*` are GLOBAL totals; `in_scope_*` are the filtered counts.** Reading a truncated
  line and seeing only the global pair looks exactly like a broken `--repo` filter. It is not.
- **The audit ladder ran 5 rounds on `#1209` and 3 on `#1267`, and EVERY round's findings were
  created or half-closed by the previous round's fix.** Both ladders were stopped on the
  prose-payload criterion, not on a clean round: by the end the payload was ~32 executable lines
  out of 244 added, and most findings were prose claiming more than the code delivered. When a
  defect fix and a reword are the same edit the round-over-round gate is structurally inert.
- **Rejected: S3/MinIO.** 8.6 MB corpus. Object storage adds a fourth copy and key-lookup, not
  query; git already gives redundancy.
- **Rejected: adding `.md` to `captured_text_scan.SCANNED_SUFFIXES`.** Its premise is free text
  where a data field should be; handoff docs are prose throughout, so it would fire on every doc
  and be disabled.

- 🔴 **MEASURING ADOPTION OF A SKILL STEP: A MENTION IS NOT AN INVOCATION, AND THE OVERCOUNT IS
  ~24×.** The deployed `SKILL.md` body contains the very command string you are looking for, so
  every session that merely LOADED the skill matches `grep -l`. Measured 2026-09-06: 24 transcript
  files matched the string, **1** contained a real invocation. Match on a Bash `tool_use`
  `command` field, and tighten the needle to `handoff_search.py --` — a bare `handoff_search`
  also catches the `grep` you are running to do the measurement, which is how the first pass
  reported 2 hits instead of 1. **Generalises to any "is this step being followed" question.**
- 🔴 **AN UNCONDITIONAL NUMBERED STEP IS FOLLOWED; A CONDITIONAL BURIED IN PROSE IS NOT — AND
  🔴 EMPHASIS DOES NOT CLOSE THE GAP.** Measured across 6 sessions in one skill on one day:
  step 4 (`cairn recall`, numbered, unconditional, fenced) **5/6**; step 3's two embedded
  conditional checks (`handoff_search`, `git log --since`, both fenced, both marked 🔴)
  **0/6 each**. The variable is not the tool, not its cost and not how loudly it is marked — the
  two step-3 checks differ from each other in every way except placement. **When a skill step is
  not firing, move it before rewording it.**
- 🔴 **"Live and verified" and "used" are different claims, and the gap between them is
  invisible to every check this effort built.** Six merged PRs, both hosts converged, the timer's
  own run green, the DB path exercised, `backend=postgres` confirmed — and 13 of 14 consumers
  never called it. Deployment verification cannot see adoption; only reading real runs can.

## How to verify
```bash
# 1. ADOPTION — the rank-1 measurement, re-runnable. THIS HOST only; run on both.
#    Controls, both watched to work 2026-09-06: positive = 1 hit at the real cutoff
#    (the metric CAN be non-zero); negative = raise CUT past 2026-09-04T19:00 and it
#    reports 0 hits with runs still 7 (it is not hardwired).
python3 - <<'PY'
import json, glob, os
CUT = "2026-09-04T17:11:50"   # #1295 merge, UTC — raise this when re-measuring
runs, hits = set(), set()
for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
    sid = os.path.basename(f)[:-6]
    for line in open(f, errors="replace"):
        if "resume-state.sh" not in line and "handoff_search.py --" not in line:
            continue
        try: r = json.loads(line)
        except Exception: continue
        if r.get("timestamp", "")[:19] < CUT: continue
        c = (r.get("message") or {}).get("content")
        if not isinstance(c, list): continue
        for b in c:
            if not isinstance(b, dict) or b.get("type") != "tool_use": continue
            cmd = (b.get("input") or {}).get("command", "")
            if not isinstance(cmd, str): continue
            if "resume-state.sh" in cmd: runs.add(sid)
            if "handoff_search.py --" in cmd: hits.add(sid)
print(f"resume runs={len(runs)}  invoked handoff_search={len(hits & runs)}")
PY
#    2026-09-06 baseline: workbench 8/1, laptop 6/0.

# 2. The timer's OWN run — the only thing that tests the unit's environment:
systemctl --user show handoff-index-sync.service -p Result -p ExecMainStatus
journalctl --user -u handoff-index-sync.service --no-pager -n 20
#    expect Result=success, ExecMainStatus=0, "wrote N section row(s) … one transaction".

# 3. The DB path answers (backend= is the discriminator, NOT the row count):
KUBECONFIG=$KC_HOMELAB python3 ~/workspace/devrc/scripts/lib/handoff_search.py --query fsync --limit 3
#    expect backend=postgres. backend=memory means it silently fell back and you verified nothing.

# 4. The consumer is LIVE, not merely merged (readlink is the arbiter):
readlink -f ~/.claude/skills/resume/SKILL.md          # must resolve into /nix/store
grep -c 'handoff_search.py --offline' ~/.claude/skills/resume/SKILL.md   # must be 1
#    🔴 This proves the text is DEPLOYED. It says NOTHING about whether it is FOLLOWED — that
#    is check 1, and the two answered differently: deployed yes, followed 1 time in 14.

# 5. No database needed for the offline path:
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "drift-check" --limit 2
```
