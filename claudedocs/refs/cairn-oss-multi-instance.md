# refs: cairn-oss-multi-instance — demoted dated evidence

Sibling reference file for `claudedocs/handoff-cairn-oss-multi-instance.md`, created
2026-09-13 when that doc breached the per-document ceiling in
`scripts/tests/test_handoff_doc_size.py` and its eviction playbook's step 2 applied.

🔴 **DATED MATERIAL ONLY — measurements, incident narratives, byte counts, superseded or
retracted reasoning, worked examples.** Nothing here is an open thread. This file is NOT
indexed by `handoff_search` (it is not a handoff doc), which is exactly why an open item must
never be moved here. Every open question, gotcha and ruled-out theory stays in the handoff.

---

## Rank 22 — the store-api fsync flake (`#1458`, squash `ce9b55c3`): the evidence

Demoted 2026-09-13. The item itself, its diagnosis, its retraction about check-required
status and its open residual stay in the handoff at rank 22.

### What the tree actually said, 2026-09-09 — the remedy existed and had never reached the site

The item originally offered three remedies and all three were aimed at the wrong layer:
(a) bound the write path — a production change to `server.py`'s crash-durability semantics
made to close what is a TEST-HARNESS SITING GAP, and the in-file docstring argues neither
fsync is removable (without the directory fsync, a node losing power after `os.replace`
returns can come back with the old name on the old inode, **having already answered
`200 appended`**); (b) raise the client timeout, banned in-file; (c) unpin CI from one node.

- `TestARefusedWriteIsIndistinguishableFromAnAbsentOne._phases` built its store at a bare
  `tmp_path / "store"` — it never called `store_siting.store_root()`. **5** sites in
  `scripts/tests/test_subsystem_store_api.py` were sited; **18** were not, and the failing one
  was among the 18. The tmpfs fix (#1211/#1219/#1239) never covered it. via: code
- **The mechanism predicts WHICH test fails, which is what makes this more than
  compatible-with-the-evidence.** Every sibling in that class asserts a 404 (refused or
  absent), and a 404 never reaches `_replace_bytes`. `test_POSITIVE_CONTROL…` is the only test
  in the class that gets `200 appended`, so it is the only one that executes the two
  in-request fsyncs. via: code
- **`scripts/ci-repro/README.md` already carried the confirming measurement and nobody had
  reconciled it against the siting fix:** the real CI traceback stalls on
  `…/pytest-of-nixbld13/pytest-0/popen-gw3/…/store` — a `tmp_path`-derived path on the step
  container's ephemeral layer, **not** a `devrc-store-*` tmpfs holder. The failing writer is an
  unsited root. via: measurement
- ✅ This also retires the `_HUNG_SERVER_RULES` path-sensitivity caveat FOR CI (that classifier
  matches the substring `fsync` against rendered filenames, so a worktree named `*fsync*` makes
  it report `SERVER_BLOCKED_IN_FSYNC` unconditionally): the CI path contains no `fsync`, so the
  verdict is genuine. The classifier defect itself is untouched and is still
  `handoff-gate-flake-store-api.md` rank 2.

### The guard that should have caught it, and what `#1458` shipped

🔴 **THE GUARD WAS ONE SITE WIDE — a description claiming coverage the body did not provide.**
`TestTheStoreIsSitedOffTheContendedDisk` says "a fixture that silently fell back to disk
**everywhere** would leave the suite exactly as flaky while every test still passed", and its
positive control takes only the `store` fixture. The old ratchet was a COUNT
(`_DISK_ROOTED_SITES = 33`) — a count of declarations, not of what they cover.

`#1458`: all 18 sites take a new `sited_root` fixture; the count-ratchet becomes
`_DISK_ROOTED_ALLOWLIST`, an enumerated set keyed `<Class.function> :: <expr>` (never line
numbers) asserted in BOTH directions, plus `_SITED_STORE_ROOT_CALLERS` pinning the other side.
15 sites stay allowlisted with reasons — argued write-free **from the call graph, not from a
runtime trace**.

### The reproducer and the mutation re-run

⚠ **`slowfsync.c` in its shipped form CANNOT measure a siting fix** — it interposes on libc
`fsync`, so it stalls tmpfs too (65.0 s on ext4 *and* on tmpfs). `#1458` adds an opt-in
`SLOWFSYNC_SKIP_TMPFS=1`. Red-before-green with it: `origin/main` **1 failed in 63.96s**
(`TimeoutError` @ `socket.py:720`, `MECHANISM = SERVER_BLOCKED_IN_FSYNC`); branch **1 passed in
3.67s**; branch with the fallback forced to disk **1 failed in 64.29s** — so the green is the
SITING, not an inert reproducer.

**Census guard mutation RE-RUN INDEPENDENTLY, not taken on the implementing agent's report:**
reverting the failing test to `tmp_path / "store"`, `__pycache__` cleared,
`PYTHONDONTWRITEBYTECODE=1` → `test_the_disk_rooted_census_matches_the_allowlist_EXACTLY` RED
**with its own message**, naming the exact site, **22 others still passing** — reachable and
specific, not a suite-wide break. Mutant reverted; tree clean.

### The flake-rate reading, 2026-09-12 — population, predicate and residuals

The store-api test is named in **0 of 99** `tekton/devrc-pytests` verdicts on heads that CARRY
`ce9b55c3` against **12 of 298** that do not (4.03%), so P(0 | the pre-window rate) ≈ **0.017**
— against 0.23 for the only prior reading (`#1512`, which split on the anchor's TIMESTAMP).
Population: 400 devrc PR heads `#1162`–`#1566`, every state, 397 with a verdict, 0
ancestry-unmeasurable; predicate `git merge-base --is-ancestor ce9b55c3 <head>`; collector
positive-controlled on the three known reds first.

🔴 **The zero is not what establishes the fix — the mechanism being gone is** (`origin/main`:
`sited_root` on 106 lines, 1 surviving `tmp_path / "store"` and it is prose, the test itself
still present at `:14543`). ⚠ The item's `:367` for that surviving prose line went STALE to
`:449` inside its own window — re-derive a line number rather than quoting one.

🔴 **Every per-test count is a LOWER BOUND**: 100 of 101 failure descriptions are truncated at
138 characters, so the true post count is in **[0, 22]** — though the bias runs the right way,
post-window failing runs averaging 1.83 failures against 3.60 pre. ⚠ The date predicate the
item warned about reclassified 5 of 397 verdicts and 0 of 101 failures, so `#1512`'s table was
underpowered rather than corrupted — do not discard it. Full table, classification and
residuals: `claudedocs/handoff-gate-flake-store-api.md` rank 1, which also records what the
reading found INSTEAD — the gate's post-fix red is dominated by deterministic ledger censuses
over tracked text (27 of 99 verdicts), not by any flake.

### 🔴 FOUR RETRACTED CLAIMS about the SECOND timeout flake — measured false 2026-09-11

The live item is `claudedocs/handoff-gate-flake-store-api.md` rank 7; read that. These are
listed rather than deleted because each is the kind a reader re-derives:

- (a) *"added by `#1445`'s audit ladder"* — `#1445` **never touched that file**; `ca088e70`
  (`#289`) created it with the bound, `809486fa` (`#1073`) added the flaking tests.
- (b) *"spawns a nested **full** run"* — both flaking spawns are **one-target** runs (argv
  `--targets`, or `DEVRC_TARGETS` via `ENV_ONLY`).
- (c) *"`#1462` — **same base**, same test — passed minutes earlier"* — the green head predates
  `ce9b55c3` and the red one contains it, so the pair straddles an intervention and isolates
  nothing; the later red was also a **different** test.
- (d) the wall-time pair (1030.34 s vs 1065.64 s) read as *"the node was not inflated"* — it
  compares per-target aggregates ACROSS runs and cannot see contention BETWEEN concurrent runs,
  which is where a 120 s bound lives.

**What survives, re-measured:** six bound sites, four exposed tests, and a file whose total
wall time was observed at **137.69 s / 164.27 s / 205 s / 428.40 s — a 3.11x spread on one
tree**. A fixed 120 s bound sits inside that spread. 🔴 **The cause is NOT established**;
`#1429` was checked and **refuted** for this tier (`limits.cpu: "4"` makes the old and new
worker formulas both yield 4). via: measurement
