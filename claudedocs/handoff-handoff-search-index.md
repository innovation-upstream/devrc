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
🔴 **THE EFFORT IS COMPLETE AND LIVE. Everything below supersedes the earlier "NOT deployed"
status, which was true when written and is now false.**

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

## Next steps (ranked)
1. **Watch whether `/resume` actually uses it.** The index is live and wired, but nothing yet
   shows a session's behaviour changed. This is the only real test of whether the effort was
   worth building; everything else is machinery. Re-read after a few `/resume` runs.
   forcing: none
2. **The label/derivation granularity residual** — `scripts/lib/handoff_index.py`,
   `rebuild_delete_labels`. Its own round, because the fix moves the delete scope. The tripwire
   test above fails the day someone does it, which is the intended signal.
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

## How to verify
```bash
# 1. The timer's OWN run — the only thing that tests the unit's environment:
systemctl --user show handoff-index-sync.service -p Result -p ExecMainStatus
journalctl --user -u handoff-index-sync.service --no-pager -n 20
#    expect Result=success, ExecMainStatus=0, "wrote N section row(s) … one transaction".

# 2. The DB path answers (backend= is the discriminator, NOT the row count):
KUBECONFIG=$KC_HOMELAB python3 ~/workspace/devrc/scripts/lib/handoff_search.py --query fsync --limit 3
#    expect backend=postgres. backend=memory means it silently fell back and you verified nothing.

# 3. The consumer is LIVE, not merely merged (readlink is the arbiter):
readlink -f ~/.claude/skills/resume/SKILL.md          # must resolve into /nix/store
grep -c 'handoff_search.py --offline' ~/.claude/skills/resume/SKILL.md   # must be 1

# 4. No database needed for the offline path:
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "drift-check" --limit 2
```
