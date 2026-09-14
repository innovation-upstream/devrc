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

---

## DEMOTED 2026-09-14 — the cost of consolidating onto the pin (rank 3, DELIVERED)

Kept verbatim because its *method* outlives its numbers: it is the worked example of
rendering two copies docstring-free before diffing them, with BOTH controls watched
(a file against itself -> 0; one renamed identifier -> 4). Rank 3 slice 3 shipped
(#1508 `44bd8b0e`), so the decision it informed is closed.

### The cost of consolidating onto the pin is MEASURED — it is 2 real deltas, not 5 modules' worth
🔴 This supersedes the fork block's per-module **raw-line** figures as the basis for planning
slice 3. Those counts (`host_identity` 122, `cairn_doctor` 43, `subsystem_recall` 38 …) are
RAW diffs and are dominated by the extraction's docstring rewrites; they say almost nothing
about what devrc would gain or lose. Do not re-derive this — verify it still holds.
- **Symptom + exact repro:** not a bug — the unmeasured half of a decided piece of work.
  Repro: render both copies docstring- and comment-free and diff those.
  `python3 -c 'import ast,sys; …'` — strip every `Module/FunctionDef/ClassDef` docstring, then
  `ast.unparse`. **Both controls were watched**: the same file against itself → **0** diff
  lines; one renamed identifier (`def this_host` → `this_hostX`) → **4**. An instrument that
  cannot go red, and cannot see a rename, would have produced the same reassuring numbers.
- **Observed (with values), 2026-09-11** — devrc `scripts/lib/` vs cairn `lib/`,
  code-only diff lines (raw `diff -u` lines in parentheses):
  `subsystem_resolver` **0** (164) · `subsystem_read_store` **0** (20) ·
  `host_identity` **19** (175) · `cairn_doctor` **42** (73) · `subsystem_recall` **98** (318) ·
  `timeouts` **8** (60). Two of the five modules are **behaviourally identical**; `subsystem_resolver`
  is 2,814 lines in devrc and every one of the 164 differing lines is prose. via: measurement
- **Observed: where the three non-zero modules differ, the PINNED side is the superset.**
  `host_identity` adds `HOST_LABEL_ENV = ("CAIRN_HOST","ASIB_HOST","ACTIVITY_HOST")` and reads
  it in `host_label()`; `cairn_doctor` takes `mirror_root: Path | None` and reports
  `NOT_OBSERVABLE` instead of crashing when no mirror is configured; `subsystem_recall`
  factors `main` into `recall_selection()` / `reject_recall_flags()` and takes its shared
  vocabulary `from entry_shape import …` where devrc's takes the same names
  `from subsystem_touch import …`. `timeouts` differs only by an unused `DEFAULT_TIMEOUT = 60`.
  via: measurement
- 🔴 **Observed: the WRITER's vocabulary is almost free, and the two exceptions are the whole
  job.** Comparing `scripts/lib/subsystem_touch.py` against cairn's `lib/entry_shape.py`
  per-name, normalised the same way: `STORE_IS_PER_HOST`, `SHAPE_HEADINGS`, `store_host`,
  `store_host_line`, `derive_scope`, `scope_for_repo`, `_git`, `_toplevel` are **byte-identical**.
  Only two move: (a) the exception base — cairn's is `CairnError` with `TouchError = CairnError`
  as a compatibility alias, while devrc's `TouchError(Exception)` is the base that ~25 writer
  errors subclass; (b) `repo_path_missing_message`. via: measurement
- 🔴 **The one KNOWING REGRESSION, named rather than discovered later:** entry_shape's
  `repo_path_missing_message` drops devrc's sentence naming the pre-exported handles
  (`REPO_PATH_HANDLES = ("$DEVRC","$HOMELAB","$DATAPACKET","$CIVITAI")`) and hints
  `Did you mean --scope X?` only when that scope dir exists. Because `scope_for_repo` — which
  is byte-identical and IS imported from the pin — calls it, taking the pin takes the weaker
  message with it. The brief's preferred remedy is devrc-side: catch `RepoPathMissingError` at
  devrc's own CLI boundary and re-append the handles sentence, so nothing is lost and
  `scope_for_repo` still comes from the pin. via: code
- **Ruled out: that class identity can be left alone.** The pinned `subsystem_recall` catches
  `entry_shape.StoreMissingError`; a writer that raises its own look-alike of the same name
  would not be caught. Importing the vocabulary is not tidiness here — it is the thing that
  makes the two halves interoperate. via: code
- **Next probe:** none for the measurement. The open question is the agent's: whether devrc's
  test files that assert the *unsanitised* strings (``subsystem_touch.py --validate`` where the
  pin says ``a writer --validate``) should be updated or deleted as cairn-owned. The brief says
  update the expectation to the PINNED string and never weaken an assertion to a substring.

