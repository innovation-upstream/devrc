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
- **Branch / PR:** both MERGED to `main`. No branch outstanding for this effort.
  - `devrc#1209` → squash `45930d644` — section-grained full-text index (P1)
  - `devrc#1244` → squash `1b769b64b` — cairn I/O-stall classifier
  - `homelab-infra` `d2c9c49a` — rescued an untracked handoff doc (below)
- **Shipped:** `scripts/lib/handoff_index.py` (derive + write), `scripts/lib/handoff_search.py`
  (query CLI), `scripts/tests/test_handoff_index.py`, `scripts/testlib/hang_mechanism.py`,
  and a `handoff-index-sync` systemd unit in `nix/home.nix`.
- **Deploy/verify status — be precise:**
  - Gate green on the MERGED tree at base `de677683`: dev-host `GATE: RESULT=PASS exit=0`;
    sandbox `devrc-pytests> RESULT: PASS (exit=0)`; `devrc-nodetests> RESULT: PASS (exit=0)`.
  - 🔴 **NOT deployed.** No `home-manager switch` was run. The unit exists in the repo only.
  - 🔴 **The live-Postgres path has NEVER executed.** No test accepts the DDL, computes the
    generated `tsv`, builds the GIN index, or orders by `ts_rank`. Green means the SQL was
    constructed, never that a server accepted it.
  - The timer ships **disarmed** (`enableHandoffIndexSync = false`) for exactly that reason.
- **Works today with no database at all:** `handoff_search.py --offline` derives from git refs.
  Measured 439 docs / ~4,008 sections across four repos.
- **In flight:** an agent is fixing the unreadable-docs delete path (Open investigations below);
  no PR number yet at time of writing.

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

## Next steps (ranked)
1. **Arm the index for real** — supervised `handoff_index.py --rebuild` (dry-run is the
   default) then `--rebuild --write` against the live Postgres, watching the DDL be accepted,
   `tsv` computed and `ts_rank` order results. Only then flip `enableHandoffIndexSync = true`
   in `nix/home.nix` and `home-manager switch`. Nothing in any sandbox can do this step.
   forcing: none
2. **Land the unreadable-docs delete fix** (Open investigations block 1). Touches
   `scripts/lib/handoff_index.py` + `scripts/tests/test_handoff_index.py` in `devrc`.
   IN FLIGHT: an agent was dispatched for it this session; check `gh pr list` before starting.
   forcing: none
3. **Decide whether one invariant closes the four-spelling shape** (block 2) rather than
   patching a fifth instance later. `devrc`, same two files.
   forcing: none
4. **Wire a consumer.** Nothing calls `handoff_search.py` yet — no skill, no script. Until
   `/resume` or a subagent queries it, the index is shipped and unused.
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

## How to verify
```bash
# 1. The search works with no database at all, and reports its own scope:
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --query "fsync" --limit 3
#    expect: a recall banner, `indexed_docs=N indexed_sections=M backend=memory`, ranked hits.
#    A zero beside indexed_docs=0 is a BROKEN INDEX, not an absent answer.

# 2. --repo takes a LABEL, not a path (an unknown value is refused, not silently empty):
python3 ~/workspace/devrc/scripts/lib/handoff_search.py --offline --repo devrc --query drift-check

# 3. Durability holes are reported, never indexed:
python3 ~/workspace/devrc/scripts/lib/handoff_index.py --repo ~/workspace/devrc   # dry-run is the default

# 4. The shipped code is on main (verify by CONTENT — a squash is never an ancestor):
git -C ~/workspace/devrc cat-file -e origin/main:scripts/lib/handoff_index.py && echo present
```
