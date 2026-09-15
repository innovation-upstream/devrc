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

---

## DEMOTED 2026-09-15 — rank 23's body (CLOSED: devrc #1583 `c1ecc830`, cairn #17 `a2661371`)
Moved out of the handoff for the size budget; the rank now carries a one-line pointer here. Nothing below is an open thread.

23. ✅ **CLOSED ENTIRELY 2026-09-14.** (a)+(b) devrc #1583 `c1ecc830`; **(c) `ZacxDev/cairn`
    #17 `a2661371`** — see `State now`. The item said ONE scrubbed remedy; there were TWELVE
    sites across two replacement phrases and a fabricated symbol.
    **Two exit-127 / stale-spelling residues the round-3 fix round did not cover.**
    (a) `claude/skills/resume/SKILL.md:128` still spells the post-write check as a bare
    `subsystem_touch.py --validate --scope <scope>` — **not on PATH, exits 127** — and `:150`
    carries the absolute `python3 ~/workspace/devrc/scripts/lib/subsystem_touch.py` spelling.
    Both now have a one-word remedy (`cairn-validate`) and neither is pinned by any test, so
    nothing will catch them drifting again. (b) `subsystem_touch.validate_command()`
    (`scripts/lib/subsystem_touch.py`) still emits the absolute checkout-path spelling in the
    `RECOVER —` block the skill tells writers to run verbatim. (c) 🟡8 from #1406's round-2
    audit: the pinned package's own `🔴 MALFORMED —` remedy prints ``check a file with
    `a writer --validate <path>` `` — the extraction scrub — which lives in `ZacxDev/cairn`,
    not devrc, so it needs an upstream PR.
    **Closing condition:** `grep -c 'subsystem_touch.py --validate' claude/skills/` → 0 on
    `origin/main`, and an upstream PR for (c).
    ✅ **(a) AND (b) MERGED 2026-09-12 — devrc #1583, squash `c1ecc830`. (c) is NOT in it and
    stays OPEN: it lives in `ZacxDev/cairn` and needs an upstream PR.**
    **Closing condition MET, verified by CONTENT at `origin/main`** (a squash makes
    `merge-base --is-ancestor` false forever, so ancestry cannot answer this):
    `grep -c 'subsystem_touch.py --validate' claude/skills/` → **0**; `validate_command` emits
    `cairn-validate --store … --scope …`; the RECOVER remedy carries
    `--flake ~/workspace/devrc --impure`; both new guards present.
    ⚠ **This line read "🔨 BUILT" for the first hours after the merge** — the same
    status-drift class this very PR existed to fix, reintroduced by the PR that fixed it.
    Written down rather than quietly corrected: a doc edited in the same commit as the work
    it describes cannot record that work's own merge, so the status line is stale by
    construction until someone comes back for it. **Do not treat a merged handoff edit as
    self-updating.**
    ⚠ **The line numbers in this item were STALE** — the two sites are `:135` and `:157`, not
    `:128`/`:150`. Found by grepping the string, not by opening the named line.
    - **(a)** both now spell `cairn-validate`. The `:157` site is the single-FILE form, so it
      reads `cairn-validate --validate <path>`: the launcher prepends `--validate` with no value
      and argparse's last occurrence wins, which its own docstring states.
    - **(b)** `validate_command()` now emits `cairn-validate --store <root> --scope <scope>`.
      🔴 **`--store` is emitted explicitly and that is NOT redundant** — the launcher's own
      prepend is the SYNCED CACHE, while this function's contract is to check the store the
      refusal actually came from. **Measured both ways** on the deployed pin: with an explicit
      `--store`, the run's `store:` line names it, not the launcher's default; and a malformed
      entry still exits **3**, untranslated.
    - 🔴 **THE COST RANK 23 DID NOT ANTICIPATE, and it is the reusable part.** Six neighbouring
      tests broke, because `test_the_recovery_command_ACTUALLY_RUNS_and_reproduces_the_diagnosis`
      **executes** the emitted command. `python3 <abs path>` is runnable in BOTH tiers; a bare
      `cairn-validate` is on `home.sessionPath` and the `nix build` tier has no reason to carry
      it. **Exec'ing the real launcher would have made that guard structurally incapable of
      passing in one tier while staying green on this host — the defect this repo already shipped
      once.** So the launcher's prepend is MODELLED in one helper (`_writer_argv`), and the seam
      is pinned by a new ledger test that reds if the launcher stops prepending `--validate`,
      stops prepending `--store`, or stops appending the caller's argv.
    - **Mutation battery: 6/6 killed BY THEIR INTENDED TEST**, each required to fail with its own
      assertion's message; harness positive-control watched green on the pristine tree first;
      `PYTHONDONTWRITEBYTECODE=1`; tree restored byte-identical.
      🔴 **One mutant SURVIVED the first run and the fix is the lesson:** dropping `--store` was
      invisible because the test modelled the launcher's default with the SAME value the command
      emits, so the parse yielded the right root either way. It dies only against a sentinel the
      caller's store can never equal. A fixture whose fields are not pairwise distinct cannot see
      the mutant that collapses them.
    🔴 **ROUND 0 OF THIS PR'S OWN AUDIT REFUTED THIS ITEM'S STATED RATIONALE. The fix stands; the
    REASON printed on it was wrong, and it is retracted in the code, the PR and here.** The claim
    was that `Path(__file__)` makes the command *"true for the machine that printed it and false
    for anyone who pastes it elsewhere"*. Both halves fail:
    - the old spelling emitted an **absolute** path, so cwd was never the failure mode; and
    - `nix/home.nix` deploys the launcher as an `mkOutOfStoreSymlink` into
      `${homePath}/workspace/devrc`, so **any host where `cairn-validate` resolves at all
      necessarily has this checkout at that same absolute path** — the old command would have
      worked there too. The new spelling's precondition is if anything **stronger**: it needs a
      home-manager switch and a deployed pin, where the old one needed only python.
    **The real defect, measured:** `Path(__file__).resolve()` names the **running copy**. Run from
    a throwaway worktree — this repo's standing default for any file-modifying agent — it emitted
    `python3 /tmp/wt-cairn-rank23/scripts/lib/subsystem_touch.py …`, a path about to be
    `worktree remove`d. The recovery command went stale the moment the session that printed it
    ended. **That is the durable reason; do not re-derive the portability one from this doc.**
    🔴 **AND A GUARD I WROTE WAS DELETED BY THAT ROUND, ON MEASUREMENT.**
    `test_the_LAUNCHER_still_prepends_what_this_command_omits` grepped the launcher's SOURCE TEXT
    and its docstring asserted *"nothing else asserts it … which is a silent green"* — **false**.
    Control: each of its three mutations run against `test_cairn_flake_pin.py` ALONE, with
    `test_subsystem_touch.py` deselected — `--validate` prepend dropped → **3 failed**; `--store`
    prepend dropped → **1 failed**; caller argv dropped → **2 failed**; pristine control green at
    **15 passed** first. Those tests run the REAL launcher as a subprocess and read BOTH streams,
    so they hold in the `nix build` tier; mine was SPELLED (baked double quotes ⇒ falsely red on a
    legal refactor, green on a literal in a comment). **A second, weaker copy of a guard that
    already exists reads as coverage while providing none.**
    ⚠ **`--store` SURVIVED the round but is no longer claimed to be free.** `store` here is
    `args.store`, whose default is `DEFAULT_STORE_ROOT` — the **frozen pre-cutover mirror**, not
    the synced cache the launcher would otherwise pick (measured: mirror **161** entries, cache
    **244**), and the mandated invocations in `subsystem-index/SKILL.md` pass no `--store`. Keeping
    it is FAITHFUL (the malformed file really is in the store that was read) but it inherits an
    unanswered question — why does the writer default to the frozen mirror at all? — which this
    change must not be read as settling.
    🔴 **ROUND 0's OTHER FINDING, FILED NOT FIXED: this item's closing condition is SPELLED, and
    the CLASS is still open.** `grep -c 'subsystem_touch.py --validate' claude/skills/` → 0 is
    genuinely met, but `command grep -rn '/home/zach/workspace/devrc' claude/skills/` returns **21
    occurrences across 9 files**, including **four literal
    `python3 /home/zach/workspace/devrc/scripts/lib/subsystem_touch.py …` invocations in
    `claude/skills/subsystem-index/SKILL.md:73, 93, 113, 218`** — the write-protocol skill itself,
    the primary consumer. No scanner gates this class. **Closing condition:** a mechanical gate
    over `claude/skills/**` rejecting any quoted or emitted command that embeds an absolute
    checkout path, plus those 21 sites cleared — merged, and watched red-then-green on a planted
    violation. **Owner: unassigned; this is a new ranked item, not part of rank 23.**
    ⚠ **A THIRD SITE OF THE SAME CLASS, FOUND WHILE FIXING (b) AND DELIBERATELY NOT FIXED:**
    `scripts/lib/subsystem_touch.py:3991` and `:4712` emit
    `python3 {SELF_PATH} --template <slug> --scope …` — the same absolute-checkout-path spelling,
    in the `--template` command rather than `--validate`. It is outside this item's stated scope,
    and unlike `--validate` it has **no one-word remedy**: there is no `cairn-template` launcher
    to move it to, so closing it means first deciding whether to add one. **Closing condition:**
    either a launcher exists and both sites name it, or a decision is recorded here that the
    writer's `--template` path is meant to stay checkout-absolute. Named so it reads as
    known-and-open rather than missed.
    forcing: none
