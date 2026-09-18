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

---

## DEMOTED 2026-09-17 — closed ranks, closed investigations, and the `#786` audit-rounds body

Moved verbatim from `claudedocs/handoff-cairn-oss-multi-instance.md` per the eviction
playbook's step 2 in `scripts/tests/test_handoff_doc_size.py`. Every block below is closed:
a merged PR, or an investigation the doc itself marked `Next probe: none`. The handoff keeps
a pointer, the transferable lesson, and every open remnant these items left behind.

### DEMOTED 2026-09-17 — investigation: test_check_sops_enc_payloads

### `test_check_sops_enc_payloads.py` failed once in `homelab-infra` CI and has not reproduced
- **Symptom + exact repro:** no repro. One failure observed by a round-2 audit of
  `ZacxDev/homelab-infra#786` at head `993643baa`, passing at the round-1 tip. Its message
  was `exit 2: no tracked *.enc.yaml found under <tmpdir>` — the test's own refusal when its
  fixture setup produced nothing, not an assertion about the tree.
- **Observed (with values):** rc=0, **52 of 52**, in SIX separate full-suite runs on this
  host, including at the same head, and including one run with nothing else executing.
  via: measurement
- **Ruled out: that this PR's diff caused it.** The suite runs at the round-1, round-2,
  round-3, round-4 and round-5 tips all pass it, and no commit in the range touches that
  test's fixture path. via: measurement
- **Leading hypothesis:** contention between two concurrent full-suite runs. The audit ran
  18:50–19:33Z while a suite of mine ran 19:00–19:32Z, and the failure message is the shape
  a git operation in a shared tmpdir produces when another run has moved underneath it.
  Concurrent suites on this box are a documented evidence-corruption shape.
- **Next probe:** none scheduled, and re-running is NOT it — six passes have already
  established it does not reproduce on demand. If it recurs, capture whether another suite
  was running at the same instant BEFORE re-running anything; that is the only observation
  that separates the two mechanisms, and it is unrecoverable afterwards.


### DEMOTED 2026-09-17 — investigation: #1508 round-1 blockers / wrong shell

### The root cause behind BOTH round-1 blockers — an environment claim measured from the wrong shell
🔴 One sentence, and it generalises past this PR: **every environment claim in #1508's body was
measured from a shell that has `cairn` on PATH, and the three environments that decide whether
this repo's SCHEDULED work runs do not.** That is why a 22,000-test green suite and three green
Tekton legs sat on top of two deploy-blockers.
- **Symptom + exact repro:** `env -i PATH=<the unit's own closed PATH> HOME=… python3 -c
  'import handoff_doc'`, and the same for `scripts/analyze-service-index/backup.py`. Read the
  PATH from the LIVE unit — `systemctl --user show <unit> -p Environment` — never from
  `nix/home.nix`, and never from your own shell.
- **Observed (with values):** base `IMPORT OK` / head `CairnPinUnresolved`, both modules, same
  env each arm. The three units' PATHs contain git/age/kubectl/coreutils/nix/bash and **no
  `cairn`**. `handoff-index-sync.timer` fires hourly, so the window was ~5 h at discovery.
  via: measurement
- 🔴 **Ruled out: that a `home-manager switch` was the trigger, i.e. that "we did not switch"
  bounded the risk.** All three units `ExecStart` `%h/workspace/devrc/scripts/…` — the working
  tree — so the break lands on `git pull`, not on switch. via: code
- 🔴 **Ruled out: that widening PATH to `%h/.local/bin` is the fix for all three.**
  `analyze-service-index-backup.service` sets `ProtectHome=tmpfs`, so that symlink does not
  exist inside its namespace. The fix is `CAIRN_LIB=${cairnPackage}/libexec/cairn/lib` in each
  unit's `Environment`. via: measurement
- 🔴 **The guard that let it ship green is the durable lesson.** `_unit_shaped_env`
  (`scripts/tests/test_analyze_service_index_backup.py:2417`) had the docstring *"`env -i` plus
  exactly what nix/home.nix sets … NOT `dict(os.environ)`"* over a body reading
  `{"PATH": os.environ["PATH"], …}` — **a description wider than its implementation, on the only
  probe claiming to model that environment**, so the one dimension that decided the outcome was
  a pass-through. via: code
- **Next probe:** none for the diagnosis. The generalisable check: when a change adds a hard
  import-time requirement, enumerate every **scheduled** consumer (systemd unit, cron, container
  ENTRYPOINT) and re-run the import under that consumer's OWN environment, not yours.


### DEMOTED 2026-09-17 — investigation: the FAILING: line

### The `FAILING:` line is a 140-char status description and CANNOT be read as a complete failure list
- **Symptom + exact repro:** `gh pr checks <n>` prints one failing test while the same line's own
  counts imply more. Observed on `#1525`:
  `FAILING: test_every_kill_server_call_site_in_the_repo_is_classified | TOTAL collected=22167 passed=22163 skipped=2` — arithmetic gives **2 failed**, and the second name was truncated mid-token.
- **Observed (with values):** three distinct bites in one session. (a) It hid
  `test_no_tracked_shell_text_writes_a_kill_this_guard_would_deny` from me; found only by running
  the file locally after fixing the named one. (b) It is why `handoff-gate-flake-store-api.md`
  rank 7's closing condition must **not** key on "no test appears in a `FAILING:` line" — a
  rename, skip or deselect satisfies that with nothing fixed. (c) It appears to have produced a
  regression in another session's PR: `#1522`'s `480b014f` removed two correct ledger rows on the
  premise "a mention that does not exist" — a reasonable inference from a truncated line, and
  false against the file.
- **Ruled out: that the truncation is cosmetic.** It changes conclusions in both directions —
  hiding a live failure, and satisfying an absence-based check. via: measurement
- **Next probe:** none needed for diagnosis. **Read the file, not the status line** —
  `grep -n '<pattern>' <file>` settled the `#1522` case in one command.


### DEMOTED 2026-09-17 — ranks 1, 2, 3, 4

1. ✅ **DONE 2026-09-05 — `ZacxDev/cairn` IS PUBLIC.** Verified by the ACTUAL public path.
   🔴 The pre-publication audit covered **12** commits, not the 7 on `main` — GitHub serves
   `refs/pull/1/head` on a public repo. **Any per-revision sweep must print a per-revision
   quantity that CHANGES.**
   forcing: none — done

2. ⚠ **DONE 2026-09-05, BUT NOT AS WRITTEN — THIS ITEM'S OWN PREMISE WAS FALSE.**
   `ZacxDev/homelab-infra` **#714**, `ed2c4a0db`, Flux-applied, verified a no-op.
   **The transferable rule: "X makes Y false" must name WHICH ARTIFACT Y describes.**
   forcing: none — done

3. ✅ **SLICE 2 MERGED 2026-09-09 — devrc consumes cairn as a pinned flake input.**
   `ZacxDev/cairn` is a pinned flake input and `packages.cairn` is what `nix/home.nix`
   deploys. Squash **`9300f234`** (PR #1406). 🔴 **VERIFIED BY CONTENT, NEVER BY
   ANCESTRY** — a squash makes `merge-base --is-ancestor` false forever, so that check reads
   as "not merged" and is wrong. On `origin/main`: `scripts/cairn-validate` present,
   `flake.nix` names the cairn input, `nix/home.nix` carries `cairnPackage` ×3 and the
   `.local/bin/cairn-validate` entry ×1.
   ✅ **CLOSING CONDITION MET ON THE WORKBENCH, 2026-09-09** — `readlink -f
   ~/.local/bin/cairn` → `/nix/store/…-cairn-c84c142/bin/cairn`. ⚠ **Every earlier
   "still → scripts/cairn" sentence in this doc is superseded.** It was made live by
   `ship.sh --no-remote` and then by generation 713; the deploy asymmetry held —
   `cairn-validate` and `cairn-who` both still resolve out-of-store into the checkout.
   ✅ **RESOLVED 2026-09-12 — THE LAPTOP IS SWITCHED AND CROSS-HOST AGREEMENT IS COMPARED.**
   `ship.sh` (no flags) exits **0** and converges both hosts to devrc `c337765e`; the cairn pin
   was read directly on each and both resolve to the identical `…-cairn-562a6ea/bin/cairn`.
   ⚠ **The observation below was correct when taken and is kept for its diagnostic value, because
   the SCOPE is what was wrong, not the reading.** `ship.sh` did exit **255** with
   `ssh: connect to host 192.168.50.155 port 22: Connection timed out` and no ICMP — **but that is
   the LAN address only.** `ship.sh` now falls back to the nebula address `10.42.0.100`, which
   answers; today's run shows both legs on stderr. So *"blocked on the host"* was the wrong
   conclusion from a right measurement: the host was up and the PATH was down.
   🔴 **`NOT COMPARED — 1 of 2 hosts` therefore aged into a false claim, and this doc carried it in
   two places.** A verdict of absence needs re-measuring before it is cited, and it needs measuring
   against every route, not the first one that fails.
   ⚠ The worktree `~/workspace/devrc-flake-pin` was fully merged and has been **removed**;
   the `[ahead 8]` warning is discharged.
   🔴 **TWO WARNINGS ABOUT THAT WORKTREE WERE PUBLISHED HERE AND BOTH WERE WRONG. RETRACTED
   by the parallel session, and the instrument is the lesson.** First it said six files were
   UNCOMMITTED (they had been committed through audit round 3); the correction then said
   **8 commits were UNPUSHED and the worktree must not be deleted**, and that was wrong too —
   they were pushed AND merged. **The error was the instrument: `git status -sb`'s `[ahead N]`
   compares against the LAST-FETCHED remote ref, and that worktree had never been fetched in,
   so it reported a remote state hours stale.** A second reading, `git log origin/main..HEAD`
   = 9, looked like corroboration and is the SQUASH trap this doc already records — after a
   squash merge a branch's commits are never ancestors of `main`, forever, so that count is
   non-zero for merged work by construction. **Two agreeing readings, both artifacts, pointing
   the same wrong way.** What settled it was CONTENT plus `gh pr view --json state`.
   **Never read ahead/behind without fetching first, and never let it outrank content.**
   - **Half 1 — ✅ MERGED: `ZacxDev/cairn`#4, `218b6c1`.**
   - **Half 2 — slice 1 MERGED (devrc #1381, `baa664e4`, both hosts switched and verified);
     slice 2 ✅ MERGED as #1406 `9300f234`; slice 3 ✅ MERGED 2026-09-12 as #1508, squash
     `44bd8b0e`** (point the writer at the pinned `entry_shape`, delete devrc's five duplicated
     `lib/` modules). ⚠ **This line read "NOT STARTED" for ~9 h after the work merged** — the
     State-now block and this one are separate sentences about the same fact and drifted apart.
     Verified by content, not ancestry; see State now. The fork was DECIDED 2026-09-08 —
     CONSOLIDATE ONTO THE PIN — and is not to be re-asked.
   **What #1406 shipped:** `cairn.url = "github:ZacxDev/cairn"` (lock rev `9213726`),
   deliberately **NOT** `inputs.nixpkgs.follows` — cairn pins `python312` on purpose; the
   package threaded through `extraSpecialArgs` as `cairnPackage` (required, no default, so a
   broken thread is an eval error); `CAIRN_MIRROR_ROOT` in `nix/sessionVariables.nix`; and —
   added by the audit ladder, not in the original scope — **`scripts/cairn-validate`**.
   🔴 **`cairn-who` AND `cairn-validate` KEEP `mkOutOfStoreSymlink`; only `cairn` moved
   into the store.** Both are devrc-only, absent from the OSS package, and resolve
   `scripts/lib/` through `Path(__file__).resolve()`. Do not "tidy" the deploy modes into
   agreement in either direction.
   ⚠ Every `cairn who` spelling elsewhere in this doc — and in
   `handoff-cairn-task-linkage.md` and `proposal-cairn-session-capture.md` — is the DEAD
   spelling and exits 2. **Do not copy a command out of them.**
   **Closing condition:** merged PR ✅; `readlink -f ~/.local/bin/cairn` into `/nix/store`
   ❌ on both hosts. **Re-measure before declaring this done.**
   forcing: none

4. ✅ **CLOSED 2026-09-14 — MERGED. `civitai/talos-infra#1414`, squash `c9b1c4e03`.**
   All four §11 questions answered by the operator: **Zach-only at launch but PER-IDENTITY
   from day one**; **a DEDICATED subdomain on the client apex (not a path on an existing
   host), CF-proxied** — the literal is deliberately not written here, see below; **Zach the
   sole token admin for now**;
   **OSS contributions OPEN from day one** (the one answer that went against the
   recommendation, recorded as such). 🔴 Its §8 blocker table was STALE FOR NINE DAYS —
   blocker 2 (token reload) LANDED while the PR sat open (`server/server.py:5049
   reload_tokens`, 47 test refs) — so the table was re-measured whole, not row by row.
   **That is §8's own lesson landing on itself:** a blocker table is a claim with a shelf
   life; re-measure ALL of it at merge time.
   **The one follow-on it creates:** `ZacxDev/cairn` gains `CONTRIBUTING.md` + issues
   enabled. **Closes when** both exist on `origin/main` and the file names the leak gate
   (`tests/leakscan.py`) and the test command — land it BEFORE advertising the repo, so a
   first contributor meets a documented gate rather than a surprising one.
   🔴 **THE HOSTNAME ANSWER IS SCRUBBED ON PURPOSE — do not "restore" it.** This doc
   originally spelled the client subdomain literally, and **devrc is a PUBLIC repo**, so a
   client's internal topology was committed to it. `test_no_client_hostnames` caught it here
   on 2026-09-14, red on `main`, and the guard's own playbook applies: this value is a
   DECISION RECORD, nothing in tracked source opens it, so the substance stays and the
   literal goes. What the operator actually decided — a dedicated subdomain rather than a
   path, CF-proxied — is above and is the part a reader needs. ⚠ The literal is still in
   this repo's REACHABLE HISTORY (three commits); the content gates enumerate `git ls-files`
   and are structurally blind to history (`SECRETS.md` → "Dead credentials in reachable
   history"), so this scrub stops the leak GROWING and does not remove it. Rewrite-vs-accept
   is an operator call and has not been made.
   (Historical: four open questions in §11; none blocked A3.) **RE-VERIFIED LIVE 2026-09-09: still OPEN** (`state: OPEN`,
   `mergedAt: null`).
   🔴 **AND A RECONCILER FALSE POSITIVE TO NOT FALL FOR AGAIN.** `resume-state.sh` reported
   `PR #1414 MERGED but handoff frames it as open/in-flight`. That is a **devrc** PR — a
   handoff-doc PR that merged days ago — because the digest resolves a BARE `#N` against the
   repo it is run in, and this doc's rank 4 is `civitai/talos-infra#1414`. The same applies to
   its `#1433 MERGED` and `#1417 CLOSED` lines: all three are devrc PRs this doc already
   records. **Write `owner/repo#N` in this doc so the reconciler can attribute it** — that is
   the documented remedy, and a bare number here costs a session a wrong "go do the follow-on".
   forcing: none


### DEMOTED 2026-09-17 — rank 12

12. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #6, squash `9d58f02`.** `leakscan.py`'s
    coverage is now DERIVED from content (a NUL within the first 8000 bytes, git's own rule)
    instead of a hand-written `TEXT_SUFFIXES` enumeration; every enumerated file lands in
    exactly one bucket and `main` names every skip. Red-at-base / green-at-HEAD regression
    matrix, 8/8 mutants killed by their intended test, full suite 1703 passed, CI green on
    all three checks. Claim `cairn-oss-multi-instance-12` **RELEASED**.
    **Closing condition MET, and watched rather than inferred:** at `origin/main` the gate
    prints `SKIPPED tests/leakscan.py — the gate's own fixtures, exempt by name` then
    `38 file(s) scanned, 1 skipped` — a named skip line, in the output a reader of CI sees.
    ⚠ **`/audit-pr 6` was OFFERED and NEVER RUN**, by neither the building session nor the
    merging one. This shipped on its own evidence (regression matrix + 8/8 mutation battery),
    which is real but is not an adversarial read. Recorded so it reads as skipped, not clean.
    ⚠ The building session's `<scratchpad>/wt-leakscan` worktree was left in place — it
    belongs to that session, so it was not removed here.
    forcing: security — the repo is public and this gate is the reason it can be


### DEMOTED 2026-09-17 — ranks 23, 24

23. ✅ **CLOSED ENTIRELY 2026-09-14** — (a)+(b) devrc **#1583** `c1ecc830`; (c) `ZacxDev/cairn`
    **#17** `a2661371`. Claim RELEASED. Body DEMOTED 2026-09-15 to
    `claudedocs/refs/cairn-oss-multi-instance.md` for the size budget — the durable lessons are
    also in `State now` (three audit rounds found zero 🔴 and every finding was a FALSE CLAIM
    ABOUT THE CODE, two of them in that session's own prose; and a brief naming ONE instance of
    a scrub is naming a SAMPLE — there were twelve, over two replacement phrases plus a
    fabricated symbol).
    forcing: none — done
24. ✅ **DONE 2026-09-13 — THE CLASS RANK 23 COULD NOT CLOSE IS CLOSED.** devrc **#1621**,
    squash **`df09a6c2`**. `scripts/tests/test_absolute_handle_paths.py` rejects any absolute
    checkout path in the `claude/**` + `CLAUDE.md` corpus that a handle from
    `nix/agent-handles.nix` already names; the 21 repo-handle sites and 9 `KUBECONFIG=~`
    occurrences are cleared.
    🔴 **THE HANDLE TABLE IS PARSED FROM `nix/agent-handles.nix`, NOT RESTATED.** That file is
    already the generator for both consumers — zsh's `envExtra` and opencode's `plugin/env.js` —
    and its own header says adding a handle by hand is *"exactly the drift this replaced"*. So
    the gate cannot drift from the handles, and emitting the right remedy is a CONSEQUENCE of
    parsing the source rather than a second thing to maintain.
    🔴 **IT REJECTS THE SPELLING AND NEVER RESOLVES THE PATH.** The sibling `test_doc_path_rot.py`
    deliberately skips absolute paths — *"Absolute paths are never claims this repo can settle"* —
    and that exemption is precisely where these sites had been sitting. Matching TEXT is
    host-independent; `stat`ing would not be, and would have made the gate a claim about the
    machine running it.
    ✅ **CLOSING CONDITION MET, VERIFIED BY CONTENT AT `origin/main`** (a squash makes
    `merge-base --is-ancestor` false forever, so ancestry cannot answer this): both
    `scripts/tests/test_absolute_handle_paths.py` and
    `scripts/tests/absolute-handle-path-ignore.list` are present; the gate runs **48 passed** in
    a clean worktree off `origin/main`; and a planted
    `python3 /home/zach/workspace/devrc/scripts/memory-audit.py` turns it **red** with
    ``-> use `$DEVRC/scripts/memory-audit.py` `` — red-then-green watched, not inferred.
    **The documented exceptions are 3 + 1.** Three remain in the corpus, all in
    `claude/skills/clawgate/reference/cross-session-reach.md` — two recorded `clawgatectl` JSON
    payloads at `:127`/`:174` and a table cell at `:98` whose value is an ellipsis — ignore-listed
    with a written reason. The fourth is
    `KUBECONFIG=~/workspace/homelab-infra/workbench-kubeconfig` at
    `claude/skills/auditloop/reference/ui-and-meta-run.md:48`, deliberately untouched: **no handle
    names that file**, and `$KC_WORKBENCH` is the `homelab-talos` spelling, i.e. empty on exactly
    the host where `homelab-infra` is the correct path.
    🔴 **WHAT THE FOUR AUDIT ROUNDS COST IS THE DURABLE OUTPUT, NOT THE GATE. 🔴0 in every
    round; every headline finding was a FALSE CLAIM ABOUT THE CODE, never a logic defect.**
    - **Round 0** found the gate one spelling narrower than the class it claimed —
      `~/workspace/<handle>` walked straight through it, **184 sites**. That is structurally the
      same charge rank 24 levelled at rank 23, one level up: a condition met by its own spelling
      while the class stands.
    - **Round 1** found that a ONE-SEGMENT handle suffix-wildcards into any absolute parent, so
      the gate prints a **wrong-FILE remedy**. The failure line is DESIGNED to be the fix, which
      is exactly why a wrong remedy is the actionable defect rather than cosmetic.
    - **Round 2** found a guard whose stated consequence was measurably false — **on its second
      draft**. The third draft is the true one.
    - **The final sweep** found **six more** false consequence claims, and only a sweep of every
      site would have caught them: each was locally plausible where it stood.
    ⚠ **THE LADDER WAS STOPPED ON THE PROSE CRITERION, NOT ON A CLEAN ROUND.** The payload here
    is prose inside a test module, so the attribution gate is structurally inert — *"fixed a
    defect"* and *"reworded a warning"* are the same edit, and no round can distinguish them. The
    conditions for stopping held: no 🔴 in any round, blast radius bounded to *"the document
    contains a false sentence"*, and the shape swept at every site rather than at the ones a round
    happened to name. 🔴 **The accepted cost, recorded so it reads as OPEN rather than absent: the
    sentences the last fix round wrote have NOT been read by an adversarial round, and that
    round's own first replacement for a boundary claim was itself wrong. The residual error rate
    on that prose is non-zero — not assumed zero.**
    forcing: none — done


### DEMOTED 2026-09-17 — rank 26

26. ✅ **DONE 2026-09-14 — devrc #1657, squash `0808a820`. AND THIS ITEM'S HEADLINE WAS HALF
    FALSE; the correction is the durable half.** It said *"The handle table has TWO
    hand-maintained copies, and both are drifted from `nix/agent-handles.nix` TODAY."* **ONE is.**
    **Closing condition MET, verified BY CONTENT at `origin/main`** (a squash makes
    `merge-base --is-ancestor` false forever, so ancestry cannot answer this): `KC_PROD` present in
    the hook; `scripts/tests/test_shell_env_nudge_handles.py` present; the `norm.startswith("/")`
    guard present; `nix_block` consolidated and `_NIX_SECTION` gone; and `REPO_ENV_HANDLES`
    **deliberately unchanged**, still `("DEVRC","HOMELAB","DATAPACKET","CIVITAI")`.
    ⚠ **Merging does NOT make the hook live** — `nix/home.nix` ships it as a `home.file`
    `/nix/store` copy, so the deployed `~/.claude/hooks/shell-env-nudge.py` carries the OLD table
    until a `home-manager switch`. Verify with `readlink -f`, not with the merge.
    ⚠ **Three audit rounds ran (0, 1, 2); the ladder stopped on the PROSE criterion, not on a clean
    round** — round 2's fixes changed 12 hook lines, 0 executable. Round 1 found a real production
    defect (the basename fallback claimed ABSOLUTE paths, naming the wrong cluster); round 2 found
    that a fix-round comment of mine restated a claim `test_absolute_handle_paths.py` had already
    RETRACTED. What #1657 did NOT close is **rank 28**.
    - ✅ **REAL, and fixed in #1657:** `scripts/claude-hooks/shell-env-nudge.py` carried 9 of 10 —
      **missing `KC_PROD`**, whose kubeconfig exists. Nothing in the tree read its
      `KC_VARS`/`REPO_VARS`, so the copy had no ledger and the failure is silent by construction
      (a `.get()` returning `None` and a nudge that never fires).
    - 🔴 **NOT DRIFT — DO NOT "FIX" IT:** `scripts/lib/handoff_index.py`'s `REPO_ENV_HANDLES`
      omitting `CIVITAI_CLI` is a **deliberate exclusion**, already pinned in BOTH directions with
      its reason recorded in source by
      `scripts/tests/test_handoff_index.py::TestTheUnitEnvironmentMatchesTheHandlesTheIndexerReads`.
      Measured 2026-09-14, and **independently confirmed by #1657's round-0 audit**: that test
      passes. Adding the handle would add a zero-doc repo to the corpus and, because
      `prune_config_refusal` requires EVERY `REPO_ENV_HANDLES` entry to be SET, narrow the hosts
      an operator can `--prune` from.
    🔴 **THE ORIGINAL CLOSING CONDITION IS THEREFORE UNMEETABLE AS WRITTEN** — "both are
    corrected" cannot happen, because one of the two is already correct. **Amended:** the hook's
    two dicts are pinned to `agent-handles.nix` in both directions, shown RED then GREEN, merged;
    `REPO_ENV_HANDLES` is left alone.
    ⚠ **The transferable lesson, which is the durable output:** an oversight and a documented
    decision look IDENTICAL in the table itself — they differ only in whether something else pins
    them. **Before "fixing" a table that omits an entry, grep for a test that ASSERTS the
    omission.** This item's own honest caveat (that the `CIVITAI_CLI` gap was LATENT, 0 docs —
    still true) measured the blast radius and never asked whether the omission was INTENDED.
    ⚠ **Known-open, named rather than left to be rediscovered:** the #1657 ledger pins the hook
    against the nix **DECLARATION**, and a declaration is not an **EXPORT** — both consumers
    existence-guard (`exportIf "-d"`/`"-f"`). Measured: `~/.kube/homelab-nebula.yaml` is absent,
    `$KC_NEBULA` is UNSET, and the hook nudges `KUBECONFIG=$KC_NEBULA` anyway. Pre-existing, not
    introduced by #1657. **Closes when** the hook resolves paths from `os.environ` and a test
    shows it emitting NO suggestion for a declared-but-unexported handle, RED before and GREEN
    after.
    forcing: none


### DEMOTED 2026-09-17 — the #786 six-audit-rounds block

### 2026-09-10 — SIX AUDIT ROUNDS ON `homelab-infra#786`, AND WHAT ENDED THEM
🔴 **A CLASSIFIER GRADED BY READING WILL BE REWRITTEN UNTIL SOMETHING EXECUTES IT.** The
cairn leg's ~20 lines of verdict shell went through FOUR rewrites, and each fix shipped the
OPPOSITE defect of the one before:

| round | change | defect it shipped |
|---|---|---|
| 1 | every non-zero rc → `fail` | a broken gate blamed on the author |
| 2 | marker-less non-zero → `error` | a broken PIN excused as infrastructure |
| 3 | bare drv-name match | nix ANNOUNCES the build before any outcome, so it matched every run that built |
| 4 | markers-first | nix emits `unable to download` at WARNING level while successfully RETRYING |

Rounds 1–3 were each verified by careful reading. The fix was not a fifth reading: a 17-row
table that lifts the SHIPPED shell out of the YAML and runs it under a real `sh`, asserting
VERDICT AND DETAIL. All four historical classifiers were replayed into the pipeline and
caught. **The transferable tell: when a fix and its predecessor keep swapping which
direction they are wrong in, the missing thing is EXECUTION, not care.**

🔴 **THE FIX ROUND'S OWN PROSE WAS THE RECURRING SECOND FINDING.** Across six rounds the
audits caught, in commits written while fixing the previous round: a justification invented
for a fallback that does not exist in that leg; a stale number four lines above the one just
corrected; a generalisation ("per-step requests are sized well under p99") false for the
step that dominates the pod; a trailer described as `last 10 log lines:` and UNPREFIXED when
it is `Last N log lines:` with a `       > ` prefix; and TWO false verification figures in
the PR body — one a filtered test run reported as full coverage, one a `RESULT: PASS` line
belonging to an unrelated shell test. **Every one was caught by an audit, none by me.**
⚠ And a sixth, found only when the operator asked what was outstanding: ranks 11/13/20 were
closed with correct closure notes while their HEADINGS still read `🔴 OPERATOR ACTION` /
`HAS NOT HAPPENED` / `runs only on demand`. **The heading is the surface a reader hits first.**

⚠ **A LEDGER FIGURE PUBLISHED ON THAT PR WAS WRONG AND IS CORRECTED HERE**: the executable
payload series is `22 → 16 → 7 → 5 → 4`, and total payload `95 → 95 → 56 → 49 → 47`. The
"103 → 95 → 56 → 5 → 4" figure conflated the two. The trend that ended the ladder holds; the
number did not.

**How it ended, and why that is not the same as running out of steam:** the stopping
criterion was published on the PR BEFORE the final round ran — no 🔴, no executable payload
change, nothing reachable by CI ⇒ stop. Round 6 met it. Its one substantive finding (an
extractor still narrowable by a preceding `if…fi`) was applied as a two-line structural
guard rather than as a seventh round, which is the auditor's own recommendation and the
difference between a ladder that converges and one that audits itself.


**🔴 THE PATH THIS DOC'S OWN KICKOFF NAMES SERVES A STALE REVISION, AND IT LOOKS CURRENT.**
The 2026-09-08 merge session was told to read `~/workspace/devrc/claudedocs/handoff-cairn-oss-
multi-instance.md` — the PRIMARY CLONE's working copy. That clone was checked out on
`feat/nct6683-fans-bar`, so the file it served was **two revisions behind `origin/main`**
(141 insertions / 209 deletions apart) and **did not know PR #6 existed at all**: it listed
rank 12 as unstarted work with no PR, and described rank 3's fork as an open question the
operator had since answered. Nothing about the read looked wrong — the file was present,
well-formed and internally consistent, which is exactly the failure mode. Acting on it would
have meant re-deriving built-and-green work from scratch. 🔴 **The devrc base-clone refresh
hook does NOT cover this** — it syncs only `CLAUDE.md` and `.claude/skills/**`, and
`claudedocs/` is deliberately outside that set. **Read a handoff from the ref, not the
working tree**: `git -C ~/workspace/devrc fetch origin main && git show
origin/main:claudedocs/<doc>.md`, or work out of a `worktree add --detach <wt> origin/main`.

⚠ **CORRECTION, SAME DAY, AND IT INVERTS WHAT THIS BLOCK CLAIMED TO BE.** The paragraph above
was written as a NEW discovery. It is not one, and presenting it as one is itself the defect:
`/resume` **step 1 already carries this rule** — *"The working-tree copy is a GUESS about what
the handoff says — run step 2 FIRST and read the copy it names"* — backed by two measurements
older than this session (a datapacket clone serving a handoff **276 lines** behind `origin/trunk`
with the whole resume framed on it; a clone serving a skill file **692 commits** stale). 🔴 **And
the tooling that prevents it was never run.** `scripts/resume-state.sh` resolves the doc, fetches,
compares, and prints `handoff-read:` naming the authoritative copy — it exists precisely so this
cannot happen, and step 2 orders it BEFORE the read for that reason. This session hand-rolled
`git`/`gh` instead, which step 2 explicitly forbids, hit the trap the tool was built to prevent,
and then wrote it up as novel. **Run `resume-state.sh` before reading the doc.** Measured when it
was finally run at 2026-09-08T22:11Z: it reported the tree copy STALE at **829 lines local vs 930
on `origin/main`** — still stale, hours after the merge — plus a `!! GAPS` block
(`gh answered for 5 of 6 referenced PR(s)`) that a hand-rolled check reports as nothing at all.
🔴 **A digest with a gap block is NOT an all-clear**, and hand-rolling cannot produce that
distinction. Same class as this repo's existing stale-blocker lesson, moved one level up: there
the FACT inside the doc was stale, here the whole DOCUMENT was — and the remedy already existed.

**🔴 A RATE IS THE WRONG INSTRUMENT WHEN THE ONE OBSERVATION CARRIED NO EVIDENCE.** Rank 6
was written as "get a rate, then fix it or close it", and 43 runs at ≈2.3% cannot
distinguish 2% from 0% — no achievable N would have. What actually blocked it is that the
single failure was read through `pytest -q | tail -1`, so the traceback never existed
anywhere, and *which branch fired* was unknowable. **A one-in-N flake produces its evidence
once; a pipe that keeps the count and throws the traceback away spends that one occurrence
for nothing.** Redirect to a file and keep it. The useful move on a rate that cannot
converge is to make the NEXT occurrence self-diagnosing and close whatever mechanisms are
demonstrably live — not to keep sampling.

**🔴 `gh run rerun` IS A FREE DENOMINATOR, AND IT REALLY RE-EXECUTES.** Nine reruns
requested at once ran concurrently and returned in ~10 min against the ~75 min a local loop
would have cost. Verified rather than assumed: one rerun's log was read end to end and shows
its own later timestamp with `collected=1651 failed=0` — a replayed result would have shown
the original run's clock. Also read `skipped`: a green run that SKIPPED the test under
investigation contributes nothing, and the summary line is where that shows.

**🔴 CHECK WHETHER A MECHANISM *CAN* FIRE BEFORE MEASURING WHETHER IT DID — and give the
probe a control.** `_free_port()`'s TOCTOU was a theory until 3000 trials × 20 binds showed
the kernel recycling the released port 8 times, with the same loop holding the socket OPEN
recycling it 0 times. The control is what makes the 8 a measurement instead of noise. It
still does not make the mechanism the CAUSE of the observed failure, and the PR says so.

**🔴 A FIXTURE THAT CANNOT REACH THE CODE PATH PASSES WITH THE GUARD DELETED.** The first
draft of the `_run_to_completion` retry test used a MALFORMED token file — but the server
returns `EXIT_CONFIG` before `build_server` ever binds, so the occupied port was invisible,
the test made one spawn, and it would have been green with the retry removed. Only the leg
that reaches `bind()` can lose the race. **Ask which line your fixture makes the code
execute, not merely whether the test passes.**

**A FIELD THAT CANNOT VARY IS NOT EVIDENCE.** The new failure message first reported
`child_was_alive=`, and a mutant proved it structurally pinned to `True` — that site is only
reached when the child is running. The assertion on it read as coverage while providing
none, so the field was deleted rather than the assertion weakened.

**Operator decisions this session, all acted on — do not re-litigate:**
- Sanitisation is scoped to **security, not tidiness**. Project names and dates ship as-is.
  🔴 This was a CORRECTION of my own over-engineering: I had built a gate finding **480**
  issues of which **4** mattered. The three cosmetic rules were DELETED, not demoted — a gate
  firing 476 times for nothing is one somebody switches off, and then the 4 ship too.
- Extraction scope: server + client + shared libs + tests. History: **fresh start**, one
  initial commit — the only way "the history is clean" is true by construction rather than by
  audit, since devrc's four content gates read `git ls-files` and are blind to history.
- Comments: **keep the mechanism, drop the particulars.** MIT. GitHub Actions.
- Routing (for the future multi-instance client): an explicit scope→instance registry that
  **FAILS LOUD** on an unregistered scope. A default silently recreates the write-to-a-dead-store
  shape that cost six entries in phase 3.

**🔴 A STALE BLOCKER COSTS MORE THAN AN UNKNOWN ONE.** Two of the proposal's five blockers
evaporated on contact with the code, and both had been written down as facts. "The API has no
create route" is wrong — it was quoted from a handoff note predating the change closing it — `PUT` with
`If-None-Match: *` has created entries for some time, with 14 test references. "The authoring
tool must become instance-aware" assumed a module had to come along that supplied 5.3% of what
was needed. **Nobody re-checks a thing already written down**, so it survives every review and
shapes the schedule. Re-measure a blocker before scheduling work against it.

**🔴 A GREEN LEAK SCAN DOES NOT MEAN THE SANITISATION IS CORRECT.** A scripted pass cleared
430 of 481 findings and was **wrong in two ways the scanner happily passed**: a docstring's
opening `"""` swallowed everything to a date because the "string literal" regex used a negated
class that matches newlines, turning `MEASURED 2026-09-02` into `MEASURED 2000-09-02` — an
incident narrative in costume; and scope substitution produced `BUILT FROM alpha, NOT FROM
beta-infra`, which is meaningless. The tree was reverted byte-identical and done by hand. The
gate checks for tokens; it cannot tell that prose still means something.

**On the audit ladder (4 audit rounds + 4 fix rounds on cairn PR #1):**
- Round 1 found a 🔴: a live bearer token printed **verbatim to stdout** on a refused reload,
  from a process that stays healthy. Unconditional — `MIN_TOKEN_CHARS=43` exceeds
  `MAX_IDENTITY_CHARS=32`, so a token in the identity field always trips that guard.
- Round 2 found **the same defect class alive in a guard round 1 declared closed** (guard 11,
  via the scope field). That is the entire argument for not stopping at the first green.
- 🔴 **Three pre-existing tests actively PINNED the leak** — they asserted the credential
  appeared in the message. The suite did not merely miss it; it required it.
- 🔴 **And the leak predicate itself was blind**: it checked `secret in text` over the RAW
  token while the guard printed it FOLDED, so even a correct fixture would have passed.
- The ladder was stopped on the **attribution gate**, not on a verdict: round 4's fixes changed
  **zero** lines of `server/server.py`, and round 5's would have too.

**Traps paid for, do not re-pay:**
- 🔴 **Do not edit source while a pytest run is in flight** in cairn — the hang classifier greps
  frames' source-line TEXT, which `traceback.format_stack` re-reads from disk at report time, so
  shifted line numbers misclassify and produce false failures.
- A bare `python3 -m pytest` in `~/workspace/cairn` fails `No module named pytest` — that is the
  shell, not the repo. Use `nix develop ~/workspace/devrc -c python3 -m pytest`.
- `cairn ls-entries --scope <x>` **silently ignores `--scope`** and returns the whole store.
- `civitai/talos-infra`'s pre-push gate fails with `python3 pyyaml missing` — that is an
  `error`, not a `failure`. Push from inside
  `nix-shell -p "(python3.withPackages(ps: [ps.pyyaml]))" git` and it passes.

**🔴 A TEST SUBSET IS NOT THE GATE, AND THIS COST A RED `main` NEAR-MISS.** `pytest <dir>`
reported "223 passed" and I read it as success; the per-target **drift ceiling** lives only in
`run-tests.sh`, so the PR turned BOTH tiers red and only an audit caught it. When a floor
needs changing, **copy the number the gate prints** — it emits `"<target>|<n>"` explicitly —
never compute it from the two sides.

**🔴 A CONTROL BUILT OUT OF THE INSTRUMENT CERTIFIES NOTHING.** The exporter's criterion-4
negative control re-implemented the AST matcher inline instead of driving the guard. Measured:
disarming the guard's own pattern left the suite at **11 passed** — the "instrument can go
red" control stayed green while the instrument was fully disarmed, and the copy had already
drifted from the guard it was supposedly validating. Guard and control must share one function.

**🔴 A MEASUREMENT STATED AT A SCOPE IT DOES NOT HOLD ARGUES FOR DELETING THE FIX.** I wrote
"zero ties across 617 messages and 2,907 parts — real data cannot exhibit the bug" from a
**3.7%** sample, stated at host scope. Store-wide: **5 tie-groups, 10 rows, 5 sessions across
77,671 parts**. The direction is what made it dangerous — it invited the next maintainer to
delete the sort the PR exists to add. **The same error recurred in the commit that fixed it**
("the source DB is 0600" — it is **0644**), which is why this is written as a class and not
an incident.

**🔴 CLOSE A HAZARD BY CONSTRUCTION BEFORE REACHING FOR ANOTHER GUARD.** Two mutants survived
the exporter's suite: deleting `O_NOFOLLOW`, and gutting the boundary `except`. `O_NOFOLLOW`
defended a *predictable* temp path that no fixture attacked. Switching to `mkstemp` — unique,
`O_EXCL`, 0600 — removed the predictable-name hazard, the symlink-at-temp hazard AND the
untestable flag together. Prefer removing the need for a guard over adding a test for one.

**🔴 `created` OUTRANKS `worked` IN THE CLAWGATE HANDOFF RESOLVER, AND THAT IS A BLIND SPOT
THIS SESSION HIT.** Filing #511 and then working it left the session's only link as
`created`, so `clawgate_handoff.sh resolve` exits **6** ("NONE of them WORKED") for a task the
session did all the work on. The skill names this; it is real. No field was recorded here
because #511 is *rank 9* of this effort, not the effort itself.

**A DOC'S OWN DOCSTRING OFTEN PRESCRIBES ITS FIX, AND FOLLOWING IT BEATS INVENTING ONE.**
Both cairn ledger 🟢s were closed exactly as their comments already specified — "count the
dropped shapes and assert the count, not widen the phrase match". No design was needed.

**🔴 NINE AUDIT ROUNDS ON A TEST-HARNESS CHANGE, AND THE SHAPE IS THE LESSON.** Every round
found something real; **none of the last four found a defect in what the PR ships.** Rounds
3–9 were about ONE diagnostic message, not the port race the PR exists to close, and each fix
round wrote more prose for the next round to find. Payload was 2 executable lines in round 9
and 62 across all nine. Stopped on the escape-hatch criterion — no 🔴, blast radius bounded by
"a comment contains a false sentence", the recurring shape swept at every site — **with the
rationale posted publicly**, because a report that ends on the escape hatch is otherwise
indistinguishable from one that converged, and those are opposite meanings.

**🔴 TWO OF MY COMMITS MADE FALSE STATEMENTS ABOUT THEIR OWN DIFFS.** One said a value was
"READ BEFORE THE TERMINATE" while the read sat 23 lines below it; another claimed a locator
fix that was byte-identical to base, because a `str.replace()` with no assert matched nothing
and the commit message asserted otherwise. **Assert the match count of every scripted
replacement** — it is the same vacuous-anchor failure the mutation battery keeps catching,
committed in prose instead.

**🔴 SIX ROUNDS RUNNING, MY OWN EDITS SILENTLY MOVED MUTANT ANCHORS.** A mutant whose pattern
matches 0 times scores INVALID, not SURVIVED — and one that matches **2** times is worse,
because it mutates a site nobody chose. The battery must refuse any pattern that does not
apply exactly once, and an INVALID must never be read as a pass.

**🔴 A KILLED BATTERY LEAVES A MUTANT IN THE WORKING TREE.** A SIGKILLed run died mid-mutant
and the copy still held `verdict = None`. Caught only by diffing against the battery's own
pristine snapshot BEFORE doing anything else. **Diff the tree against the snapshot whenever a
battery finishes OR is interrupted** — and restore from the snapshot, never from git.

**🔴 I TRUNCATED MY OWN BATTERY OUTPUT WITH `| tail`** — the trap this repo documents — and
read 4 of 28 verdicts as the whole run. Redirect to a FILE and read the file.

**🔴 AN UNFAITHFUL STUB NEARLY REFUTED A CORRECT RULE.** Verifying the delivery record, my
first stub set `returncode` inside `terminate()`, which real `Popen.send_signal` does not do —
making the delivered and not-delivered cases identical. The stub was wrong, not the rule. A
control that is not faithful certifies nothing **in either direction**.

**🔴 A FIGURE COPIED FROM AN AUDIT'S TABLE IS EXACTLY AS UNVERIFIED AS ONE FROM MEMORY.** I
wrote "~0.227 s per attempt (the `max(0.25, …)` floor)" — a number below the floor it names in
its own sentence. Re-measured: 0.351–0.359 s. Three separate rounds put a number in one
parenthetical and none reproduced; the number is gone now.

**🔴 ONE MEASUREMENT INVERTED BETWEEN LOAD POINTS.** "An unmeetably short budget yields ONE
spawn attempt" was true at load 18–36 and FALSE at load ~6 (24/24 gave two), because a 0.25 s
probe floor hands the child a quarter-second regardless. Measure at ≥2 points and name them —
behaviour can invert, not merely shift.

**🔴 A `cairn create` `[not-found]` DOES NOT MEAN THE SCOPE IS UNSEEDED — AND A PRIOR SESSION'S
RECORDED DIAGNOSIS OF IT WAS UNDERDETERMINED.** `cairn doctor` states the design outright: *"a
refused scope is byte-identical to one the store has never held, deliberately, so that an error
cannot enumerate the store."* That is the empty-result-cannot-distinguish-two-mechanisms rule
with the ambiguity built in ON PURPOSE, so no amount of client-side probing resolves it. The
discriminator is the pod's token file:
`KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec deploy/subsystem-store-api -- cut -d' '
-f2,3 /run/secrets/subsystem-store/token` — fields 2 and 3 are identity and allowlist, field 1
is the secret and must never be printed. Measured: 23 scopes, `cairn` absent. ⚠ The same read
refutes the existing `devrc/cairn` bullet that concluded *"the scope simply did not exist"* for
`civitai-app-playable-collections` — that scope is likewise not in the allowlist. **Run the
allowlist read before concluding a scope is unseeded.** Correction appended to `devrc/cairn`
(revision `4f3cb4da30f648e4`).

**🔴 AN ITEM'S PREMISE CAN BE FALSE IN A WAY THAT MAKES THE WORK BIGGER, NOT SMALLER — AND
"NOT STARTED" READS IDENTICALLY EITHER WAY.** Rank 3 said "devrc consumes cairn as a pinned
flake input" and had been re-verified twice as not started, which is true and says nothing
about *why*. The reason was that **cairn had no `flake.nix`**, so the first move was a PR to
the PUBLIC repo, not to devrc at all. This is the same class as the stale-blocker lesson
already in this doc, inverted: there the blocker had evaporated, here the blocker was never
named. **Before scheduling an item, check that its first step is possible in the repo it
names.**

**🔴 leakscan WAS BLIND TO `.nix`, AND THE FILE THAT EXPOSED IT WAS THE ONE BEING ADDED.**
`TEXT_SUFFIXES` is an enumeration with no `.nix`, so #4's own `flake.nix` — hand-written
prose, in the repo whose single critical property is that private content stays out — would
have been unscanned while the run printed `0 findings across 34 files`. Fixed by adding
`.nix`/`.lock`; **the scanned count moving 34 → 36 is the control that the change took
effect**, and a fix that did not move it would have been indistinguishable from no fix.
The guard written for it then found a SECOND gap nobody had spotted:
`server/Dockerfile.dockerignore` was unscanned on `main`.
⚠ The class is open — see rank 11 (and rank 12, which derived the coverage from content).

**🔴 THE IMAGE WAS RUN, NOT MERELY BUILT, AND THE FIRST RUN FAILED IN A WAY THAT WAS CORRECT.**
`docker run` of the nix-built image exited **78** with `no trusted proxies: set
$SUBSYSTEM_STORE_TRUSTED_PROXIES` — fail-closed, and the **Dockerfile does not set it either**,
so it is supplied by the deployment, not the image. Reading that as a packaging defect would
have been wrong. With it set, the pod served an authorised snapshot whose tar member
`demo/widget.md` extracted **byte-identical** to the entry on disk; no-credential and
wrong-credential both returned **401**. Synthetic token and entry; container and image removed
after. **"The image builds" and "the image serves" are different claims — make both.**

**🔴 A `--help` SMOKE TEST WOULD HAVE PASSED A PACKAGE THAT COULD NOT WORK — AND ALMOST DID.**
The hazard packaging introduces is the sibling-import mechanism: `cairn` finds its modules via
`Path(__file__).resolve().parent / "lib"`, and `.resolve()` follows symlinks, so the directory
that must hold `lib/` is the one holding the REAL file. The check therefore runs `doctor`, which
drives the deep closure (`cairn_doctor`, `subsystem_read_store`, `entry_shape`), not `--help`.
**Its exit code is deliberately NOT asserted** — in a build sandbox with no store, token or pod,
a non-zero `doctor` verdict is the CORRECT answer, and demanding zero would either pin a wrong
expectation or push the check into faking an environment. What is asserted is that it produced
a report at all. **Negative control watched to fail:** with `lib/` not installed it goes red with
its OWN message (`could not import its own lib/` → `ModuleNotFoundError: No module named
'timeouts'`), not a bystander's.

**🔴 PYTHONPATH WOULD HAVE "WORKED" AND KILLED THE MECHANISM.** Setting it in a wrapper makes
the modules reachable by a SECOND mechanism that shadows the first, leaving the file's own
stated one silently dead — so the next person to move the layout sees nothing break until they
also drop the wrapper. The package installs script and `lib/` together under `libexec` instead.
Same shape as the DTO-field rule: a second path to the same outcome hides that the first is gone.

**🔴 ADDING A SECOND WAY TO BUILD ONE ARTEFACT NEEDS A PIN, NOT CARE.** `server/Dockerfile` is
deployed; `packages.server-image` is new. The runtime contract (env, port, uid, entrypoint) is
stated in BOTH and either can move alone, invisibly until a pod is running.
`tests/test_flake_image_matches_dockerfile.py` pins them. The MODULE SET is deliberately not
duplicated — the Dockerfile enumerates its `COPY`s because a docker context must not be slurped
wholesale, the flake copies all of `lib/`, so there is nothing to disagree about.
**7/7 mutants killed by the intended test**, harness control green FIRST, tree restored
byte-identical after.

**🔴 A TEST THAT PARSES TWO FILE FORMATS MAKES BOTH FORMATS A DEPENDENCY, AND AN EMPTY SET
EQUALS AN EMPTY SET.** A parser that quietly matches nothing reports perfect agreement, which
reads exactly like a pass. Every extractor is therefore paired with a positive control
asserting it saw something, plus a negative control that drives the **shipped** extractors —
not a copy — over text where the subject is absent. A re-implemented control is the exact
failure this doc already records for the exporter's criterion-4 control.

**`--replace-fail`, NEVER `--replace`, IN A NIX SUBSTITUTION.** A shebang rewrite that matches
nothing leaves `/usr/bin/env python3` in a store path: it then works on the machine that built
it and fails on one with no system python, landing nowhere near the cause. This is the
"assert the match count of every scripted replacement" rule with a tool that does it for you.

**THE VERSION IS `self.shortRev`, NEVER A LITERAL** — the same rule `clawgatectl.nix` already
carries. A dirty tree builds as `<rev>-dirty`, which is itself the fact a reader of the tag
wants. Observed: `cairn-8e4ef84-dirty` while `flake.nix` was uncommitted.

**leakscan's NARROWNESS CONTROLS ARE THE AUTHORITY ON WHAT IS A LEAK, NOT `CLAUDE.md`.**
`server/Dockerfile` names `devrc`, `homelab-talos` and `clusters/homelab/apps/mailbox`, which
cairn's `CLAUDE.md` bullet ("never commit a real project, client, customer, repository or scope
name") appears to forbid. It is not a leak: the scan's own self-test prints `allowed: loopback,
and a project name — project names are NOT policed here` and `project names in fixtures —
cosmetic, deliberately allowed`, matching the operator's recorded decision that sanitisation is
scoped to **security, not tidiness**. ⚠ The prose and the gate disagree in wording; the gate and
the decision agree. **Do not "fix" the Dockerfile.**

**⚠ `nix flake show` EVALUATES EVERY SYSTEM; `nix flake check` WOULD TOO.** CI therefore builds
the three x86_64-linux outputs explicitly, **one `nix build` per step** — a combined invocation
is the contention hazard devrc's own CLAUDE.md records, and separate steps make the checks list
name which output broke instead of "the nix job is red".

**🔴 A FOUR-ROUND AUDIT LADDER, ENDED ON THE ATTRIBUTION GATE — NOT ON A CLEAN ROUND, AND THE
DISTINCTION IS THE POINT.** Rounds 3 and 4 both changed **ZERO payload lines** (`flake.nix`,
`flake.lock`, `lib/`, `server/`, `cairn` untouched; diffs entirely test files and doc prose).
Two consecutive zero-payload rounds means the ladder is auditing the scaffolding it wrote
rather than the change under review. **Every round found something real** — this is a claim
about where the rounds had MOVED, not that they were wasted. The round-4 auditor reached the
same conclusion independently. Rationale posted publicly on the PR, because a report ending on
the gate is otherwise indistinguishable from one that converged.

**🔴 THE ONE DEFECT CLASS THAT RECURRED IN ALL FOUR ROUNDS: A GUARD THAT CLAIMS TO OWN A VALUE
AND READS A DIFFERENT SITE.** Not one bug — four instances, each found only after the previous
was fixed:
- the image had no `PATH` and no `sh`/`tar` while env/uid/port/entrypoint all agreed — a pod
  that starts, serves, and can be neither seeded nor rotated;
- the guard for that pinned `serverTools` and `serverPath` as BINDINGS and never checked
  `serverTools` reached `contents`, so two mutants restored the 🔴 behind a green test;
- `test_the_pod_does_not_run_as_root` read the `serverUid` let binding; NOTHING read the
  image's `config.User`. `User = "0:0"` with `serverUid = 65532` → 15 passed, image runs as
  root;
- counting `contents` bindings caught a decoy but not MOVING the real one into the `let` —
  built image had an empty `/app` and would not start at all.
**The fix that finally held was structural: brace-match the `buildLayeredImage { … }` block
and read arguments from INSIDE it**, so a binding of the right name in the wrong place is
unrepresentable rather than merely counted. Ask of every guard: *what does it READ, and is
that the thing that SHIPS?*

**🔴 I MADE THREE FALSE STATEMENTS ABOUT MY OWN DIFFS IN ONE PR, AND EACH WAS A DIFFERENT
SHAPE.** (a) A measured negative control that my own LATER commit staled — the control needed
`doInstallCheck = false`, which the PR body never said. (b) A claim that a false comment was
"deleted rather than reworded" when it was still there verbatim, twelve lines below a block
calling it false. (c) `nix flake show` cited as confirming the outputs, when it exited **1**
the whole time — I had read it through `2>/dev/null | sed`, which ate the error AND the exit
status. **All three were corrected publicly on the PR rather than by editing the body**, since
a reviewer may already have read the wrong version.

**🔴 FIXING A NIT DROPPED A GUARD, AND THE SUITE STAYED GREEN.** Changing a uid assertion to
count-only — correct about the misleading message — DROPPED the `65532` literal instead of
MOVING it. For one commit no test in the repo asserted a non-root pod; measured, `USER 0:0`
plus `serverUid = 0` ran the full suite to 1695 passed, on a pod that mounts a PVC and a
bearer token. **When a fix removes an assertion to improve a message, ask what that assertion
was the only one checking.**

**🔴 I RE-COMMITTED THE FIRST-WINS BUG INSIDE THE FIX FOR IT.** Having just fixed the
Dockerfile extractors to read the LAST `USER`/`CMD`, I wrote an unanchored
`re.search(r'serverPath\s*=\s*"([^"]+)"')` — which matched my own COMMENT quoting the mutant,
so the guard read a value out of prose and failed on a correct tree. Caught by the battery's
control, not by review. **A guard matching a WORD another line can spell is not structural.**

**🔴 A WRONG REMEDY IS WORSE THAN A MISSING ONE.** My coverage guard copied leakscan's flags
but not its `-z`, so an untracked `café.md` made one test say "add `.md\"` to TEXT_SUFFIXES"
and another say "the flags have diverged" — the flags were identical; the OUTPUT ENCODING was
not. Both messages would have sent a maintainer to change something already correct.

**🔴 A SANDBOX PINS DIMENSIONS, AND MY CHECK WAS BLIND ON TWO OF THEM.**
`checks.client-resolves-its-lib` runs `doctor` in a nix sandbox whose HOME has no cache root —
which is exactly why it did not notice that `cairn doctor` CRASHED on any host that had one
(`AttributeError: 'NoneType' object has no attribute 'iterdir'`, zero stdout, exit 1, whenever
`CAIRN_MIRROR_ROOT` was unset — the default). Pre-existing on `main` since the mirror became
optional; fixed in #4 because #4 is what advertises the command. Separately, the quoted-path
test's premise depended on `core.quotePath`, which it inherited rather than set — with it
false, the `-z` mutant SURVIVED. **Ask which dimension your fixture leaves free.**

**🔴 A KICKOFF BLOCK IS A SNAPSHOT, NOT A LIVE INSTRUCTION — AND ITS ASSIGNMENT CAN ALREADY BE
DONE.** The 2026-09-08 `/resume` arrived saying "rank 6 is the cheapest real item: the
intermittent now sits at ~27 runs / 1 failure — read the next N CI runs for a RATE rather than
burning a local hour". Rank 6 had been **merged the previous day** at ≈43 runs, and this doc
already carried a gotcha rejecting a rate as the instrument for it. The doc's own rank table is
the authority; the kickoff is what someone typed when they wrote it. **Reconcile the kickoff
against the ranks before acting.** ⚠ And do the work anyway when it is cheap: reading CI as
instructed is exactly what surfaced rank 14, which nobody knew existed.

**🔴 THE SAME SYMPTOM CLASS IN THE SAME SUITE IS NOT THE SAME BUG — CHECK WHICH TEST.** "The
cairn full-suite intermittent" had been one named test through three investigation blocks. A
fresh read of the failure logs showed the only two failures in the repo's CI history are a
**different** test entirely. Had the run been scored as "the flake, still at ~2%", the rate
would have been attributed to a test that has not failed since. **Read the failing test NAME out
of the log before folding a failure into an existing investigation.**

**🔴 A RATE INSTRUMENT IS NOT WRONG IN GENERAL — IT WAS WRONG FOR THAT ONE OBSERVATION.** This
doc already records "a rate is the wrong instrument when the one observation carried no
evidence", and that stands for rank 6. Rank 14 is the inverse from the same command: two
observations, both carrying a self-diagnosing assertion naming exactly why they failed. **The
discriminator is whether the failure carries evidence, not whether counting is a good idea.**

**🔴 GATING THE OBVIOUS HALF OF A TIMING FIX MAKES THE ASSERTION VACUOUS.** The natural fix to
"the budget starves the minimum-reload control" is to let the driver run past the deadline until
it reaches the minimum. Done alone that is worse than the bug: the samplers still stop on the
clock, so the minimum gets reached after every observer has gone home and `reloads >= 2` passes
over a window nobody watched. **Both the producer and the observers have to be gated on the same
constant.** Same family as this doc's existing "a fixture that cannot reach the code path passes
with the guard deleted".

**A SURVIVING MUTANT REPORTED IS WORTH MORE THAN A GREEN SWEEP.** #5's battery left one mutant
alive (clear-then-refill with no widened window). It was not hidden and nothing was adjusted to
kill it: it was proven live against a structural sibling test that DOES catch it, identified as
a pre-existing documented limit, and deliberately not filed as a work item because no closing
condition separates it from the guard already covering it.

**🔴 `cairn recall`'s FOOTER PRESCRIBES FLAGS THE `cairn` WRAPPER DOES NOT HAVE.** `--ref` exits
**2**. The featured-entry pick also fell back to `most-recent fallback` (it chose `tests`, which
had nothing to do with this effort) because the newest datapacket handoff supplied no matching
path window — so the one body printed in a 98.7 KB digest was irrelevant by construction. Use
`cairn search`, or `python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --scope <s> --ref
<name>`, which does accept it. Rank 15.

**✅ CLOSED 2026-09-08 — A STORE ENTRY'S `OPEN:` BULLET WAS STALE IN THE WAY THE BADGE WARNS
ABOUT.** `devrc/cairn` carried `2026-08-29: OPEN: no entry in this store carries a task, PR or
session ref, so nothing joins an entry to the work that produced it`. The remedy had landed and
the bullet had not moved — exactly the "a remedy that has since landed reads exactly like one
that has not" case the index badge names. Now rewritten as `RESOLVED caec932e:` via `cairn put`
(revision `da318a4c6a96f9d8`), naming the implementing site rather than asserting closure:
`ATTRIBUTION = " [cairn: {actor}/{session}]"` in `scripts/subsystem-store-api/server.py`,
appended SERVER-side from the authenticating token so a body cannot forge someone else's
attribution. **Control that the write did what it claimed, not merely that bytes landed:** the
`devrc/cairn` index row moved `17 nuance / 🔴 2 OPEN` → `18 nuance / 🔴 1 OPEN`, i.e. both
dimensions touched. ⚠ **The surviving `🔴 1 OPEN` is a DIFFERENT bullet** (`2026-09-01: OPEN: "N
overlapping SCOPES need a merge rule"`) and was deliberately left — no evidence either way was
gathered, and closing an unverified marker is worse than leaving it. 🔴 The bullet closed only
because a handoff doc named it; **nothing mechanical would have**, which is the actual lesson.

**🔴 THE KICKOFF NAMED AN ITEM ANOTHER LIVE SESSION HAD ALREADY CLAIMED — AND THE LOCK, NOT
THE PROSE, IS WHAT CAUGHT IT.** The 2026-09-08 `/resume` said rank 3 half 2 was "now
unblocked" and told this session to do it. That was TRUE when written and FALSE 38 minutes
later: another session took the slug at 13:26 and had 617 lines committed by 13:59. Nothing
in the doc could have said so — the doc is not the lock. **`claim-work <slug>` before
touching a ranked item is not ceremony; it is the only thing that sees a concurrent session**,
and it returned rc 10 (taken by another owner) rather than rc 12 (already mine), which is the
distinction that matters. This is the second time a kickoff block for THIS effort has been a
stale snapshot — the doc already records the first, on rank 6. **Reconcile the kickoff against
the ranks AND against the live claim list before acting.**

**🔴 AN UNPUSHED COMMIT MAKES A CLOSING CONDITION READ "NOT MET" WHEN THE WORK IS NEARLY
DONE.** Rank 3's condition is checked with `readlink -f ~/.local/bin/cairn` and `grep -c cairn
flake.nix`, and both still report the pre-change state — correctly, because the other
session's commit is local and unpushed. **A closing condition measured on the mainline cannot
see work in someone else's worktree**, so "not met" is not evidence nobody is on it. Check
`git worktree list` and `claim-work --list` before concluding an item is untouched.

**🔴 A `parametrize` LIST THAT READS THE MODULE UNDER TEST IS EVALUATED AT IMPORT, AND IT HID
THE RED.** The first draft built its boundary fixtures from `leakscan.BINARY_SNIFF_BYTES` in
the decorator. Against the merge base that constant does not exist, so the whole module failed
to **COLLECT** — and a collection error is not a test result. The regression I was trying to
demonstrate was invisible until the fixtures moved into the test body. **A test module must be
importable against the OLD code, or it cannot be used to show a regression at all.**

**🔴 A TEST FOR A LEAK GATE CANNOT SPELL ITS OWN PAYLOAD.** The coverage tests prove a file
was READ by planting a realistic leak and watching the gate refuse — but this module is itself
scanned by that gate, so a whole literal turns the gate red on its own suite. The payload is
assembled at runtime from fragments, and a new control asserts the assembled string is still
refused **and refused as a hostname**: without it, a mis-joined fragment leaves every coverage
test passing while asserting nothing, because a file that is read and a file that is skipped
both produce zero findings for content that is not a leak.

**THE RESIDUAL ERROR DIRECTION IS THE DESIGN, AND IT IS STATED RATHER THAN DISCOVERED.** A
binary file whose first 8000 bytes hold no NUL is SCANNED (decoded with `errors="replace"` —
at worst a false positive a human resolves). No text file can be skipped. The boundary is
tested on both sides, including the accepted case of a NUL past the sniff window.

- 🔴 **`test_nebula_relay_apply.py`'s result is keyed to the HOST, not the tree.** This
  session measured **20 failed** in it on BOTH a branch and a plain-`main` control at
  `01956bf0`, and concluded "main is red on its own"; an audit measured **30 passed / 0
  failed** at the SAME commit hours later. Both readings are real — the suite reads
  `/etc/nebula/ca.crt` and `/etc/nixos/configuration.nix`, and #1272 shipped "a read-only
  check **and the sudo apply beside it**", so applying the relay flips it with no commit
  involved. **Never quote a devrc red-baseline; re-derive it with a control run.** The same
  baseline also went stale a second way the same day: the `age`/`opencode` 7 that were red
  in the morning were fixed by main by the afternoon.
- 🔴 **Two skill ratchets have ZERO headroom and both were breached this session.** The
  skill-listing total-chars ratchet (`assert 11256 <= 11192`, plus `MEASURED_ALL_TIER_A_CHARS`
  in `test_skill_tiers.py`) went red on a **64-char** description growth — four failures, one
  cause. Fix it **length-neutrally** rather than re-pinning the constants: `client
  `scripts/cairn`` → `client `cairn` on PATH` are both exactly 22 chars. And
  `scripts/testlib/skills_mapping.py` has a **7,400-byte** ceiling that was at 7,292 — an
  explanatory comment added 742 and blew it. That ceiling's own message says an overage is a
  question about which ambition crept back, never about raising the number.
- 🔴 **A required `home.nix` argument breaks anything that evaluates it STANDALONE.**
  `cairnPackage` has no default on purpose. `scripts/testlib/skills_mapping.py:56` imports
  `home.nix` with a stub arg set and reported `nix cannot evaluate nix/home.nix` — a message
  that blames `home.nix` for a defect in the STUB SET. It is the ONLY such evaluator in the
  repo (verified: the other three build synthetic flake fixtures). Add every new required arg
  to that stub set in the same commit.
- **The lock's `cairn → 'nixpkgs'` is DEDUPLICATION, not a `follows`** — cairn's own lock
  pins the same rev `42f17a57f4f6`. Positive control in the same file: home-manager, which
  really does follow, records the LIST `['nixpkgs']`; cairn records the STRING `'nixpkgs'`.
- **`nix develop -c pytest` REWRITES `flake.lock`** when `flake.nix`'s inputs have changed,
  so a lock-only mutant is silently reverted before pytest reads it and scores SURVIVED
  without ever running. Use a bare interpreter or `--no-write-lock-file` for such batteries.
- **The `who` split's deploy asymmetry is deliberate and was measured in both directions.**
  Between merge and switch: `cairn who` exit 2, `cairn-who` command-not-found, deployed skill
  still saying `cairn who`. `mkOutOfStoreSymlink` names the link's TARGET, not who creates
  the link — activation does.

**🔴 A HANDOFF-WRITE GUARD FIRED ON A TEST FIXTURE, AND THE FIXTURE LOOKED EXACTLY LIKE A
HANDOFF.** The Stop hook reported this session had read `handoff-focus.md` and written no
handoff. That file is not a handoff — it is the pytest fixture created at
`<tmp>/widget-cfg/claudedocs/handoff-focus.md` to exercise the focus-window resolver, which by
construction must be named `claudedocs/handoff-*.md` to be found at all. **A guard that
matches a PATH SHAPE cannot tell a document from a fixture of a document**, and the fixture is
mandatory — the thing under test is "does the reader find `claudedocs/handoff-*.md`". The
guard's substance was right anyway (real work WAS unrecorded), so it was obeyed rather than
dismissed. Worth knowing before someone "fixes" the resolver's fixture naming: it cannot change.

**🔴 THE PRESCRIBED TOOL WAS WORSE THAN THE DEPRECATED ONE, AND ONLY A SIDE-BY-SIDE SHOWED
IT.** `cairn recall` and `subsystem_recall.py` were never compared on the same repo at the
same moment, so a whole class of "the digest featured something irrelevant" was absorbed as
normal for a year of sessions. **When a wrapper and the thing it wraps both still work, diff
their OUTPUT on one input** — neither one's output is suspicious alone.

**🔴 THE FORK I PUT TO THE OPERATOR WAS THE WRONG FORK.** Rank 13 was framed as "nix image vs
Dockerfile" and I asked which. Measuring first would have shown the real blocker: the deployed
image is built by DEVRC's script from the PRE-EXTRACTION server, and cairn had no publish
script at all — so the question was never which image, it was that this repo could not publish
one. **A recorded fork can be stale in its PREMISE, not just its answer; re-measure before
putting it to a human.**

**A RAW LINE DIFF CANNOT TELL PROSE FROM CODE, AND THE EXTRACTION MADE THAT THE WHOLE
QUESTION.** `server.py` differs from the deployed copy by 659 lines, which supports any story
you like. Stripping comments and docstrings and diffing the executable TOKEN stream — with a
file-against-itself positive control returning 0 — resolved it: 638 tokens cairn has and the
deployed copy lacks, against 17 the other way, every one of those 17 a fragment of a reworded
error-message string. **Strict superset, measured in ten minutes; unmeasurable by eye.**

**A MUTANT MUST BE RUN WHERE THE CODE LIVES.** The `--no-push` control for #8's SIGHUP guard
was first run from `/tmp`, where `ROOT` resolved to `/` and the BUILD failed — rc 1, but for a
bystander's reason. Re-run in place it failed with the guard's OWN message, AFTER the two
earlier controls printed OK, which is what proves it reachable rather than shadowed.

**🔴 EDITING SOURCE DURING A PYTEST RUN INVALIDATES THAT RUN — this repo says so and I did it
anyway.** A full suite was in flight while `cairn` and a test file were edited; the result was
discarded and re-run rather than read. Cost ~9 minutes, and reading it would have cost a wrong
belief about the tree.

**🔴 `git status -sb`'s `[ahead N]` IS A CLAIM ABOUT YOUR LAST FETCH, NOT ABOUT THE REMOTE —
AND I PUBLISHED A SAFETY WARNING OUT OF ONE.** Measured 2026-09-09: a worktree that had never
been fetched in reported `[ahead 8]`, and that became a pushed doc line saying 8 commits
existed nowhere else and the worktree must not be deleted. They were pushed AND the PR was
merged. 🔴 **What made it stick was a SECOND reading that agreed:** `git log origin/main..HEAD`
= 9, which is the squash trap this doc already records — after a squash merge a branch's
commits are never ancestors of `main`, forever, so a non-zero count there is what MERGED work
looks like. **Two independent-looking readings, both artifacts of the same stale/By-design
mechanism, agreeing on the wrong answer.** Neither is evidence about the remote. The
discriminators are CONTENT (`git grep -c <marker> origin/main -- <path>`) and
`gh pr view --json state,mergeCommit`. **Fetch before reading ahead/behind, and never let it
outrank content.**

**🔴 A CORRECTION CAN BE WRONG IN THE SAME CLASS IT CORRECTS.** The "six files are
uncommitted" line was retired and replaced with the "8 unpushed" line above — a fix that
carried the identical defect (a stale reading, published as a live safety claim) one step
further, and did so *inside a paragraph lecturing the reader about which check to trust*.
Both are retracted in rank 3. **Re-measure at the moment of writing the correction, not from
the survey that motivated it.**

**🔴 A `checks.` OUTPUT IS NOT A GATE, AND NOTHING WARNS YOU.** `nix build` succeeds, the
output looks exactly like `pytests` and `nodetests`, and CI never touches it — devrc's
pipeline hardcodes `LEG` ∈ {pytests, nodetests} with no `nix flake check` and no loop. A check
added without wiring reads like coverage and can never fail, which is the same
declarations-vs-instances error as counting guards instead of what they cover. **Before adding
a check anywhere, grep the thing that INVOKES checks and confirm your name appears in it.**

**🔴 "UPSTREAM'S OWN CI WILL CATCH A BROKEN DEPENDENCY" — MEASURED FALSE, ONCE, WHICH IS ALL
IT TAKES.** The best argument against a gate that executes a pinned dependency is that the
dependency's own gate already runs its full suite. cairn's did: 1709 tests green while
`cairn validate` was completely inert, because the covering test asserted only `rc == 0` and
the absence of an error string. The consumer-side check found it on its FIRST run. **A
dependency's green suite is a claim about the tests it has, not about the verbs it ships.**

**🔴 `home-manager switch --flake <path>` BUILDS FROM THAT WORKING TREE, AND THE SHARED CLONE
IS NOT ON `main`.** Measured 2026-09-09: `$DEVRC` was checked out at another session's
`feat/audit-pr-round-0-algorithm`, so `--flake $DEVRC` would have ACTIVATED THEIR UNMERGED WIP
system-wide — and it would have looked successful, because the cairn pin was present in that
tree too, so every post-switch check would have passed. **Switch from a worktree at
`origin/main`, not from the clone.** Same shared-checkout hazard as the stale handoff and the
stale tracking ref, landing this time on a command that actually changes the machine.

**⚠ `home-manager switch --flake` DEFAULTS TO PURE EVALUATION AND THIS FLAKE NEEDS `--impure`.**
A hand-rolled invocation died with `access to absolute path '/home/zach/workspace/…' is
forbidden in pure evaluation mode` — the flake deliberately references out-of-store paths via
`mkOutOfStoreSymlink`. `ship.sh` documents the correct line (`… --flake $repo --impure`); it
was not read first. The failure was clean (evaluation, before activation, nothing changed), but
it is the standing "prefer the repo's own invocation over a hand-built one" lesson, unlearned.
⚠ `ship.sh` itself was deliberately NOT used: its landing ritual runs git operations against
the primary clone, which was sitting on someone else's branch.

**🔴 "FAILED SERVICES" IN A SWITCH'S OUTPUT IS NOT NECESSARILY THE SWITCH'S DOING.** Activation
reported `Failed services: analyze-service-index-commit.service`, which reads as fallout. It is
timer-triggered, failed 603 times over 3 days, and its most recent failure PREDATED this
switch's completion. Check `journalctl --since` and the unit's trigger before attributing a
reported failure to the thing that reported it. See rank 21.

**🔴 A TOOL THAT COMMITS TO "THE CURRENT BRANCH" IS A LOADED GUN IN A SHARED CLONE — AND I
FIRED IT ONE PARAGRAPH AFTER DOCUMENTING IT.** `handoff_doc.py --confirm --push` commits to
whatever `$DEVRC` is checked out at. It was on another session's `feat/audit-pr-round-0-algorithm`,
so a handoff update landed and PUSHED there. The gotcha warning that the shared clone is not on
`main` had been written into this very doc minutes earlier, about `home-manager switch` — the
hazard was understood, the *class* was not generalised from "builds from the tree" to "commits
to the tree's branch". **Ask of every tool: which branch does this WRITE to, and did I check it
this minute?**
🔴 **THE REMEDIATION SHAPE, because it is reusable and non-destructive:** cherry-pick the commit
onto the branch it belonged on and push; then `git revert` it on the branch it polluted and push
that. **No force-push, no history rewrite** — safe even if the other session has already pulled.
Then PROVE the restoration rather than asserting it: `git diff --stat <their-last-commit> HEAD`
must be EMPTY. Confirm first that the intruding commit touched nothing of theirs
(`git show --stat`) and that no PR is open on that branch, because a revert on a branch with an
open PR shows up in their review.

**⚠ `git worktree add <path> main` IS AVAILABLE PRECISELY BECAUSE THE CLONE IS ELSEWHERE.** A
branch can only be checked out in one worktree; the shared clone squatting on a feature branch
is what leaves `main` free to check out. The stale-base risk goes with it — the clone's copy of
a doc can be behind, or as here, at a reverted state — so a worktree on `main` fixes the write
target and the read base in one move.

**🔴 ORDER TWO MERGES SO ONE POD REPLACEMENT SERVES BOTH.** The deployed store image at or
before `0.7.0` loads its token file ONCE at startup and has NO reload, so the rank-11
allowlist edit was INERT until a pod replacement — and the Deployment is `Recreate` at
`replicas: 1`, so every replacement is a brief hard read outage. #787 (the image bump) IS
that replacement, so #785 was merged FIRST and one replacement picked up both. The reverse
order costs a second outage for a change already sitting in the Secret doing nothing.
⚠ From `0.8.0` onward this stops applying to secret edits: it handles SIGHUP, so an
allowlist change wants `kubectl exec … -- kill -HUP 1`, not a pod replacement. Startup
stays deliberately fatal (`exit 78`) on a malformed file.

**🔴 THE DOC MOVED UNDER A BRANCH MID-SESSION, AND `mergeable=UNKNOWN` WAS NOT "FINE".**
`#1444` added ranks 22 and 23 to `main` after this session's branch was cut; the branch's
copy had ZERO of them and carried a 223-line rewrite. Rebasing was not the lesson — READING
the overlapping region afterwards was: the rebase was textually clean and rank 7's heading
still said "awaiting merge" after #787 had merged. A clean rebase means "no textual
conflict", never "coherent result". Ranks 22/23 were confirmed present and byte-untouched
before merging.

**Two instrument traps that cost real time:** `docker manifest inspect` reports a LIVE,
currently-deployed Harbor tag as ABSENT from this host (client-side trust store lacks the CA
the daemon has) — the one-command discriminator that downloads nothing is to pull a
certainly-absent tag and read the error SHAPE (`not found` = reachable, `x509` = not). And
`sops` resolves `.sops.yaml` from the INVOKING CWD, not the file path, so running it from
another checkout dies with `no matching creation rules found` on a file this repo's catch-all
covers; pin it with `--config`, do not `cd`.

- **The pinned package's layout is an assumption worth a test, not a comment.** `$out/bin/cairn`
  is a `makeWrapper` shell wrapper; the real script and its siblings are
  `$out/libexec/cairn/cairn` and `$out/libexec/cairn/lib/*.py`. Anything deriving the lib dir
  from `which cairn` is depending on that shape, so a cairn layout change must fail a devrc
  test rather than the operator's next `recall`.

- 🔴 **A `worktree` isolation flag dispatched from ANOTHER repo cuts a worktree of the WRONG
  repo — and `audit-dispatch.py`'s `WHERE TO WORK` tells you to use it anyway.** The flag
  worktrees the CALLER's cwd; the script reports on the checkout IT was run in. Every session
  in this arc had cwd `datapacket-talos` while the work was in devrc, so every implementation
  and audit agent was given an explicit override and a hand-written `refs/pull/<n>/head` fetch
  + detached `worktree add`. The failure mode it avoids is quiet: the agent either reports a
  briefed file missing, or silently works in a worktree of the wrong tree. The brief generator
  cannot know the caller's cwd, so this override is permanent, not a one-off.
- **A handoff branch can merge under you mid-session.** #1492 merged as `21f2c162` while this
  work was in flight and its branch was deleted upstream, so a `worktree add` on that branch
  silently checked out the stale PRE-SQUASH local ref. `git fetch origin <branch>` failing with
  `couldn't find remote ref` is the tell — always re-base a doc update on fresh `origin/main`.
- ⚠ **Left behind deliberately:** `refs/remotes/origin/pr/1508` in `~/workspace/devrc` (created
  by the audit worktrees; inert, `git update-ref -d` when the arc closes), and the worktree
  `/tmp/wt-cairn-slice3`, kept in case CI comes back red.

- 🔴 **`--emit-claims` must be run as part of CLOSING a round, and the block must be an ISSUE
  comment.** `gh pr view --json comments` does not return REVIEW comments, so a claims block
  posted as a review is invisible to `audit-dispatch.py` while looking perfectly present to a
  human — and a delta round with no parseable block is REFUSED.
- 🔴 **Check `claim-work` AND `gh pr list` before "just fixing" a red `main`.** Twice this session
  the obvious one-line fix was already owned by another session with a BETTER diagnosis: the first
  time #1534 had found the scanner was matching `kill-session` inside `skill-session` — my planned
  ledger row would have papered over a real scanner defect; the second time #1543 was already
  open. The branch name being taken was the only tell.
- ⚠ **Left behind deliberately:** `refs/remotes/origin/pr/1508` in `~/workspace/devrc` (inert,
  `git update-ref -d` when the arc closes), the worktrees `/tmp/wt-cairn-slice3` (holds the
  rebased-but-unpushed `6205faec`) and `/tmp/wt-mainctl` (the `main` control checkout).

- ✅ **RANK 22 IS NOW FULLY CLOSED — the verification below landed 2026-09-12**: the flake rate is
  0/99 on heads carrying `ce9b55c3` against 12/298 that do not (P(0) ≈ 0.017), measured by
  ancestry with a positive-controlled collector. 🔴 **What the reading found INSTEAD is the part
  worth carrying forward: the gate's remaining red is not a flake.** Deterministic ledger
  censuses over tracked text account for **27 of 99** post-fix verdicts (22 the kill-mention
  ledger, all of them before `#1561` exempted `claudedocs/`; **5 the runner-bound ledger, 4 of
  those AFTER it** — the same design class in a second ledger, the SECOND instance enumerated). ⚠ **Both instances are now fixed** (`#1561` `c0bbd6d9`, `#1567` `6f1867b1`); this bullet
  called the second "the live one" and that was true for about an hour. ⚠ **It also called this the
  THIRD instance while saying "both instances are now fixed" two lines later; only TWO are
  enumerated anywhere** (rank 1 of the gate-flake doc). **The CLASS is what survives: nothing stops
  the NEXT census reddening `main` for everyone.** Table and residuals in
  `handoff-gate-flake-store-api.md` rank 1.
  The pre-verification wording, kept for the provenance it names:
- ~~**RANK 22 CLOSED-PENDING-VERIFICATION, 2026-09-11.**~~ **SUPERSEDED 2026-09-12 — the
  verification it asks for HAS BEEN RUN; see the bullet directly above.** Retained only for the
  provenance shas it uniquely names: `#1458` squash **`ce9b55c3`** merged and content-verified;
  recorded by `#1462` (`60033d1e`) and corrected by `#1525` (`018e483b`). The second flake it
  uncovered is filed as **`handoff-gate-flake-store-api.md` rank 7** (`#1477`, `50e8a71a`), which
  also corrects that doc's rank 1. 🔴 **Its closing sentence — an INSTRUCTION to go run the
  flake-rate read — is DELETED rather than preserved**, per
  `claude/skills/handoff/reference/supersede.md`: keeping a corrected *reading* is the point,
  keeping a corrected *instruction* arms a landmine for whoever greps `rank 22` in a 2,200-line doc
  and lands on this hit instead of the bullet above. The predicate it stated was correct and
  survives above — ancestry, never a date.
- 🔴 **`cairn recall --repo <cairn>` is `scope-absent`; the scope is `devrc`.** The OSS repo has no
  store scope. `cairn search --scope devrc '<term>'` is what surfaced `ci-repro/` and the
  `#1211`/`#1219`/`#1239` history that made rank 22's whole diagnosis possible.
- 🔴 **The OSS `cairn` repo carries the IDENTICAL 18-open-coded / 5-sited store split** and the
  same one-fixture guard (`tests/test_subsystem_store_api.py:19716`). Deliberately not fixed: its
  CI is GitHub-hosted with no single-node pin, and the fork consolidates ONTO that copy (rank 3
  slice 3). **Decide it with slice 3, not by default.**
- ⚠ **A PR merged with a red gate is not a PR that passed.** `#1458` and `#1462` both merged with
  `tekton/devrc-pytests` RED on an attributed, unreachable flake. The gates are **advisory** —
  measured twice: no required status checks, no rulesets, `enforce_admins: false`.
  `claude/skills/tekton/SKILL.md` asserts the opposite and is STALE; `#1452` retracts it, and two
  sites its sweep missed are commented there.
- 🔴 **Four audit rounds across two PRs found essentially ONE defect class: a claim wider than
  what was measured, written by the fix round correcting the previous one.** The provenance
  sentence on gate-flake rank 7 was wrong **three consecutive times** — original, retraction, and
  the retraction's correction — before being deleted rather than corrected a fourth time. **If a
  sentence cannot be made true and precise, delete the claim.**
- ⚠ **I took an auditor's timings on report and wrote them into a doc as measurements.** Round 2
  caught it; re-measuring gave `:418` **43.48 s** against its 70.94 s, and the file **137.69 s**
  against its 428.40 s. Nothing reproduced — and **that** became the finding: a 3.11x observed
  spread means no point wall time from that file is quotable.
- 🔴 **The pre-create sweep only works as a SEPARATE step.** I piped `gh pr list` into the same
  command as `gh pr create` and shipped `#1529`, a duplicate of `#1522`; closed it. The sweep ran
  and I never read it.

- 🔴 **A census over PROSE turns every write-up of the census into a new entry.** Six docs red-lined
  `main` in roughly two hours from at least four sessions, including one quoting the scanner's own
  `offenders=` output and one that was my own merged handoff. The fix is scope
  (`_PROSE_ONLY_PREFIXES`), never classification. **If you are about to add the seventh row, stop.**
- 🔴 **`main` moved under me four separate times this session** — twice invalidating a doc I was
  editing (`#1540` conflicted after `#1546`; `#1549`'s base moved past its own fix), once merging my
  `#1548` while I was elsewhere, once landing `#1561`. **Re-read the conflict instead of resolving
  it mechanically**: that is what caught the `#1529` duplicate, the stale `#1540`, and the `#1549`
  regression. A `-X theirs` on the last one would have deleted the landed fix.
- ⚠ **I over-removed while trying to hand another session a verified recipe** — stripped every
  `"claudedocs/…",` line programmatically, which also hit `quoting_is_the_point` and took the suite
  to `2 failed`. I reported the census's own output instead and said plainly it was not a working
  recipe. **Do not hand over a fix you have not watched pass.**
- 🔴 **The pre-create sweep only works as a SEPARATE step** — see `#1529`, a duplicate of `#1522`
  that I opened because I piped `gh pr list` into the same command as `gh pr create`. The sweep ran;
  I never read it.

- ⚠ **RETIRED — the two "Left behind deliberately" bullets above are now WRONG, and their
  instruction is spent.** The arc has closed: `refs/remotes/origin/pr/1508` was deleted with
  `git update-ref -d`, and the worktree **`/tmp/wt-cairn-slice3` has been REMOVED**. Before
  removing it I checked it held nothing unique — clean tree, `cairn_pin.py` / `cairn-validate` /
  `flake.nix` byte-identical to `origin/main`. 🔴 **Its `git log origin/main..HEAD` showed five
  "unique" commits and that is the SQUASH ARTIFACT, not stranded work**: #1508 landed as
  `44bd8b0e`, so its commits are non-ancestors of `main` forever. Do not read that list as work
  to rescue. `/tmp/wt-mainctl` is ANOTHER session's and was left alone.

- 🔴 **A GUARD'S OWN DOCSTRING IS A COVERAGE CLAIM, AND MINE WAS FALSE — check it by running the
  mutants against the OTHER file alone.** I added a seam ledger whose docstring said "nothing else
  asserts it … which is a silent green". Control: each of its three mutations run against
  `test_cairn_flake_pin.py` with `test_subsystem_touch.py` **deselected** — `--validate` prepend
  dropped → 3 failed; `--store` dropped → 1 failed; caller argv dropped → 2 failed; pristine
  control green at 15 first. All three already covered, behaviourally, in both tiers. The guard
  was deleted. **A second, weaker copy of an existing guard reads as coverage while providing
  none.**

- 🔴 **A SPELLED GUARD CATCHES THE COMMENT THAT DESCRIBES IT.** After widening
  `test_the_refusal_NAMES_NO_running_copy_path`, the comment explaining the withdrawn draft could
  not quote the token it was about — the guard reads that function's own source, so spelling it
  reds the suite. Measured. The comment says so instead of quoting it.

- 🔴 **A FIXTURE WHOSE TWO SIDES CAN BE EQUAL CANNOT SEE THE MUTANT THAT COLLAPSES THEM.** My
  first battery scored "drop `--store`" as SURVIVED because the test modelled the launcher's
  default with the SAME value the command emits — the parse yielded the right root either way. It
  dies only against a sentinel the caller's store can never equal. Pick fixture values pairwise
  distinct, and distinct from any constant the assertion names.

- 🔴 **TWO INSTRUMENTS LIED ABOUT A RUNNING TEST SUITE, IN OPPOSITE DIRECTIONS, IN ONE SESSION.**
  (a) `pgrep -f 'run-tests.sh'` matched **another session's** runs out of `devrc-gate-base`, so a
  wait-loop never exited. (b) `grep -q` on `/proc/<pid>/cmdline` returns NOTHING because the file
  is NUL-separated and therefore "binary", so a liveness guard reported my healthy suite as
  **GONE**. Use `tr '\0' ' ' < /proc/<pid>/cmdline | grep -q`, and wait on a RESOLVED PID whose
  cmdline you have confirmed — never a box-wide pattern.

- ⚠ **`scripts/run-tests.sh` REFUSES outside its dev shell, and that refusal is correct.** Missing
  `logrotate`/`dash` → exit **3**, "the suites SKIP the tests that need these, so the run would go
  green while testing less". Run it as
  `nix develop <repo> --command bash <repo>/scripts/run-tests.sh <repo>`. Also: a wrapper's
  trailing `echo`/`tail` eats the status — the harness printed `[exited with code 0]` over a real
  exit 3.

- 🔴 **`audit-dispatch.py`'s WHERE TO WORK section can be WRONG, and it is wrong in the dangerous
  direction.** Run via a subshell `cd` from another repo, it concludes "the repository this
  session is standing in" and tells you to dispatch with `isolation: "worktree"` — which worktrees
  the **dispatching session's cwd repo**, not the PR's. Hit three times here (this is a dispatch
  hub; cwd was `datapacket-talos` while every PR was in `devrc`). Override it: have the agent
  `git -C <repo> fetch origin refs/pull/<n>/head:refs/audit/prN` then `worktree add --detach`
  itself.

- ✅ **The claims-block refusal and the HEAD check both EARNED their keep.** `audit-dispatch.py`
  refused a round-2 brief because no `audit-claims` block existed — rather than silently
  degrading a delta re-audit into a blind full audit that would then read as covered. And it
  detected that the shared checkout's HEAD was not the PR head and refused to use `..HEAD` in the
  range. **Post the block as an ISSUE comment** (`--json comments` does not return review
  comments).

- ⚠ **A rebase re-parents commits, so a sha-anchored claim in an audit ladder goes stale.** Round 1
  read `e4e3b930`; the rebase made it unreachable, so round 2 was anchored on `899f0de8`, its
  post-rebase equivalent. Say so in the claims block rather than letting the sha silently stand in.
  Same class bit a figure IN this doc: "6,693 lines at that PR's first commit" was re-parented by a
  rebase one commit later **in the same range**.

- 🔴 **A HANDOFF EDIT CANNOT RECORD ITS OWN MERGE.** #1583 fixed the doc's stale status lines and
  then left its own entry reading "🔨 BUILT" after merging — the same class, one entry lower,
  reintroduced by the PR that fixed it. A doc edited in the same commit as the work it describes
  is stale **by construction** until someone comes back for it. **Do not treat a merged handoff
  edit as self-updating** (#1597 is the follow-up that closed it).

---

## DEMOTED 2026-09-17 (pass 2) — the rank-24 process notes, `#1522`, the superseded `cairn validate` narratives, and the `--auto` worked example

Second eviction pass on the same day, for the same reason: pass 1 left the document 1,178 B under a
`GRANDFATHER_STEP` boundary, which is no working margin at all. Every block below is closed — `#1522`
MERGED 2026-09-12, rank 24 demoted in pass 1, and the two `cairn validate` narratives consolidated into
one live block in the handoff. The handoff keeps each block's surviving imperative.

### DEMOTED 2026-09-17 — pass 2 — `cairn validate` narrative 1 (superseded)

### `cairn validate` prints nothing on the PINNED client — fixed in the worktree, NOT committed, and the class is still open
- **Symptom + exact repro:** after #1406 merges and a `home-manager switch`, the mandated
  post-write check in `subsystem-index` returns exit 0 and prints only a state banner.
  Repro: `CAIRN_MIRROR_ROOT=$HOME/.claude/analyze-service-index
  /nix/store/5zlb4zpk91b2ypppadg1d80s7y3wanh8-cairn-9213726/bin/cairn validate --scope devrc --no-sync`
  against `python3 ~/workspace/devrc/scripts/cairn validate --scope devrc --no-sync`.
- **Observed (with values):** packaged client **rc 0, 76 bytes, 0 contract blocks**; the
  in-repo fork **rc 0, 5,842 bytes, 4 blocks**. The writer invoked directly
  (`subsystem_touch.py --store ~/.cache/subsystem-store --validate --scope devrc`) gives
  **rc 0, 5,765 bytes**, `entry shape:` / `marker reachability:` / `dropped lines:` and
  `OK — 31 of 31`. The OSS client reimplements `validate` on the reader's resolver instead of
  shelling the writer. via: measurement
- **Ruled out:** that anything programmatic breaks — the only caller is a human via the
  skill; every script, hook, skill and systemd unit was grepped. via: command
- **Ruled out:** that the exit-code change 3 → 5 is a regression — `3` is
  `EXIT_UNREACHABLE_NO_CACHE` in the client's own table and `5` is `EXIT_CORRUPT`, so the
  fork was leaking the writer's namespace and the packaged code is more coherent. via: code
- **Leading hypothesis:** RESOLVED for `validate` — both skills now route the post-write
  check at the writer, and `test_subsystem_touch.py`'s pinned-sentence ledger was moved in
  the same change (it went red and caught this, which is the mechanism working). What is
  NOT resolved is the CLASS: an audit measured 5 of 6 verbs byte-identical to the fork, so
  `validate` was the only diverging verb TODAY, and nothing in devrc's gate would notice the
  next one. That is rank 16.
- **Next probe:** none for `validate`. For the class, run rank 16's check:
  `nix build github:ZacxDev/cairn/<rev>#cairn` then exercise each verb against a fixture
  cache and diff against `scripts/cairn`.


### DEMOTED 2026-09-17 — pass 2 — `cairn validate` narrative 2 (consolidated)

### 🔴 2026-09-09 — `cairn validate` is NO LONGER SILENT, and it is STILL NOT the write-protocol check
🔴 **CORRECTION TO A LINE IN THIS DOC'S OWN `State now`.** It records
*"`cairn validate --scope devrc` → `devrc: 33 of 33 entry file(s) parse, 0 malformed`
(cairn #11; was SILENT, **and this verb is the mandated post-write check**)"*. The first half
is true. **The clause after the semicolon is false, and it is the dangerous half** — acting on
it routes the mandated check back at a client that does not run it, re-opening the 🔴 that
#1406's round-1 audit closed.
- **Symptom + exact repro:** on the CURRENTLY DEPLOYED pin (`cairn-c84c142`, generation 713),
  same scope, same moment:
  `cairn validate --scope devrc` vs `cairn-validate --scope devrc`.
- **Observed (with values), 2026-09-09:** packaged client → **56 B stdout**, 187 B stderr,
  rc 0, and **0 of 3 contract blocks**; its entire stdout is
  `cairn: devrc: 33 of 33 entry file(s) parse, 0 malformed`. The launcher → **6,119 B**,
  rc 0, **3 of 3** blocks — `entry shape:`, `marker reachability:`, `dropped lines:` — and
  `OK — 32 of 32`. via: measurement
- **What cairn #11 actually changed:** it made the verb print a **parse count** where it
  printed nothing. That removes the *silence*, not the *blindness*. The
  `dropped lines:` advisory — the one whose non-zero means content is **ALREADY LOST** — still
  never runs on the packaged client, and neither do the other two.
- **Ruled out: that the differing totals (33 vs 32) indicate a defect.** The packaged client
  syncs live (232 entries) and the launcher reads the local cache; they are counting different
  stores. via: measurement
- 🔴 **This is the round-1 🔴 reasserting itself IN THE DOCUMENTATION rather than in the
  code** — a check that was *silent* becoming a check that *looks like it worked* is strictly
  harder to notice, which is why the sentence matters more than the bug would.
- **Next probe:** none needed for the fact. Fix the sentence wherever it appears, and keep the
  mandated post-write check pointed at `cairn-validate`. If someone wants ONE binary again,
  the closing condition is the packaged `validate` emitting all three blocks — measure it,
  do not read a changelog.


### DEMOTED 2026-09-17 — pass 2 — the `#1522` double kill-guard red

### `#1522` (not mine) is red on BOTH kill guards, and its latest commit made it worse
- **Symptom + exact repro:** at head `480b014f`, detached worktree, `__pycache__` cleared,
  `PYTHONDONTWRITEBYTECODE=1`:
  `nix develop <wt> --command python3 -m pytest <wt>/scripts/claude-hooks/tests/test_guard_core.py -q`
  → **`2 failed, 1534 passed`**.
- **Observed (with values):** census — `added: ['claudedocs/handoff-tmux-webapp.md'], removed: []`;
  scanner — `offenders: [('claudedocs/handoff-tmux-webapp.md', '<the wide-kill verb>')]`. The
  mention is real, at `handoff-tmux-webapp.md:3409`, and is on `origin/main` too.
  🔴 **THE VERB IS ELIDED HERE ON PURPOSE — AND ELISION ALONE WAS NOT ENOUGH, WHICH IS THE
  LESSON.** An earlier revision of this bullet quoted it literally and made THIS doc an offender,
  red on `origin/main` (`f3e27aa3`: `added: ['claudedocs/handoff-cairn-oss-multi-instance.md']`).
  Eliding it dropped the count from 2 failures to 1 — and the doc **still** matched, at `:43` and
  `:2020`, both written by OTHER sessions documenting this same breakage. **A census over prose
  that mentions a command turns every write-up of the census into a new entry**, and with several
  sessions writing about it at once, no single author can elide their way out. So this doc IS
  ledgered (both allowlists), and the elision stays as the cheap half: don't add the sixth,
  seventh and eighth mention while the row already covers you.
- **Ruled out: that the rows it deleted were wrong.** Its commit says they "recorded a mention
  that does not exist"; `grep -n` finds it at `:3409` at that same head. via: measurement
- **Ruled out: that `f346ba28` was already green.** It was **1 failed** — only its own new doc
  missing from `quoting_is_the_point`. So `480b014f` went 1 → 2. via: measurement
- **Leading hypothesis:** the shape is **two allowlists, one file** — `_KILL_MENTION_LEDGER` and
  `quoting_is_the_point` must BOTH be edited, and three separate attempts today each populated
  one. The durable fix is to have the scanner read the ledger directly: an entry classified
  `prose:` IS the set `quoting_is_the_point` names.
- **Next probe:** restore both `handoff-tmux-webapp.md` rows and add
  `handoff-ci-flakes-and-misattribution.md` to `quoting_is_the_point`. That exact combination
  measured **1536 passed, 0 failed** locally.


### DEMOTED 2026-09-17 — pass 2 — `gh pr merge --auto` worked example (#1635)

### 🔴 2026-09-13 — `gh pr merge --auto` MERGED IMMEDIATELY through a pending gate, because devrc's checks are ADVISORY
- **What happened:** #1635's three Tekton checks were `pending`. `gh pr merge 1635 --squash --delete-branch --auto` was run *specifically* to defer the merge until they went green. It exited **rc 0 with no output**, and the PR was **already `MERGED`** — at `05:46:46Z`, while all three statuses still read `pending` as of `05:45:14Z`.
- **Mechanism:** `--auto` arms GitHub's auto-merge, which waits on **REQUIRED** checks. devrc's `tekton/devrc-*` are **commit statuses that are not required**, so there was nothing to wait on and the request degenerated to an immediate merge. `autoMergeRequest` reads `null` afterwards — it never armed.
- **Why it is expensive:** it fails by **merging**, not by erroring, and `rc 0` + empty output looks exactly like success. The tell is only visible after the fact: `gh pr view <n> --json autoMergeRequest,state` → `autoMergeRequest=null` **and** `state=MERGED` in the same read.
- **Do instead:** on a repo with no required checks, `--auto` is a no-op — poll the checks to terminal yourself and merge only then (a `Monitor` until-loop over `gh pr checks --json name,bucket`, asserting a **minimum check count** so an unregistered rollup cannot settle it instantly). Do not reach for `--auto` as a safety.
- **Recovery when it does fire:** the gate is not lost, only re-ordered. Verify the MERGED tree directly instead of waiting on a status attached to an already-merged commit — `git worktree add --detach /tmp/x origin/main` then run the gates there. Done here: **124 passed** (`test_absolute_handle_paths.py` + `test_doc_path_rot.py`) on `origin/main` after the merge, plus a planted violation watched red. The tree is verified; the ORDER was wrong.


### DEMOTED 2026-09-17 — pass 2 — the rank-24 arc's process notes

### 2026-09-13 — the rank-24 arc's own process notes
- **`claim-work --slug-for <doc> <rank>` was used on a rank that did not yet exist as a numbered item.** Rank 24 lived only as prose inside rank 23's body, so the slug had to be inferred. It worked, but the numbering is half a claim's identity — **file the ranked item first, then claim it**, or two sessions can derive different slugs for the same work. Ranks 24–27 are now numbered in `## Next steps (ranked)`.
- **`audit-dispatch.py` resolves the PR against the CWD's repo.** Run from a different clone it fails with `Could not resolve to a PullRequest with the number of <n>` — which reads as a bad PR number, not a wrong cwd. Run it as `(cd <the PR's worktree> && python3 $DEVRC/scripts/audit-dispatch.py <n> …)`.
- **`--emit-claims` PRINTS a skeleton; it does not post.** The block must be pasted into an **issue** comment — `gh pr view --json comments` does not return REVIEW comments, so a block posted as a review is invisible to the next round's brief.
- **The audit briefs' `WHERE TO WORK` said `isolation: "worktree"` and that was wrong for every dispatch in this arc** — the flag worktrees the *dispatching session's* cwd repo, which was `datapacket-talos`, not devrc. Every audit agent was given an explicit override to build its own detached worktree off `refs/pull/<n>/head`. This is the documented cross-repo trap; the brief generator cannot know the caller's cwd.

- 🔴 **2026-09-14 — A RANKED ITEM CAN NAME A SECOND DEFECT THAT IS ACTUALLY A GATED DECISION, AND
  THE ITEM'S OWN "MEASURED" CAVEAT IS NOT ENOUGH TO CATCH IT.** Rank 26 asserted two drifted
  handle tables. It even measured the second one's blast radius honestly — *"the `CIVITAI_CLI` one
  is LATENT — that checkout holds 0 handoff docs today, so it has no victim"* — and that
  measurement was correct (re-measured 2026-09-14: still 0). **What it never asked was whether the
  omission was INTENDED.** It was: `test_handoff_index.py::TestTheUnitEnvironmentMatchesTheHandlesTheIndexerReads`
  pins `declared - REPO_ENV_HANDLES == {"CIVITAI_CLI"}` with the reason in source, and passes.
  - **Ruled out: that the item was merely stale and the guard is newer.** The guard's own comment
    records that the one-way version of it shipped while nix declared five handles and the module
    read four — i.e. the guard was written BECAUSE of this exact class and predates the item.
    via: code
  - **Ruled out: that adding the handle would be harmless anyway.** It would add a zero-doc repo to
    the corpus and, because `prune_config_refusal` requires EVERY `REPO_ENV_HANDLES` entry to be
    SET, narrow the hosts an operator can `--prune` from. via: code
  - **The transferable rule:** before "fixing" a table that omits an entry, `grep` for a test that
    ASSERTS the omission. A deliberate exclusion and an oversight look identical in the table
    itself; they differ only in whether something else pins them. One grep separates them.

- 🔴 **2026-09-14 — `grep -cF` WITH A MULTI-LINE PATTERN SPLITS IT ONE-PATTERN-PER-LINE, SO A
  TRAILING NEWLINE MATCHES EVERY LINE.** A mutation battery guarded each mutation with
  `n=$(grep -cF -- "$old" "$f"); [ "$n" = 1 ] || INVALID`. Three of seven mutants — all valid —
  were scored `INVALID: pattern matched 150 times` / `38 times`, because the patterns ended in a
  newline and the empty final pattern matched every line of the file. **The harness failed, not
  the mutations**, and it failed in the reassuring direction: it looked like the mutations were
  ill-formed. Count multi-line patterns in Python (`s.count(old)`), never with `grep -c`.
  This is the instrument-validation rule landing on the *battery's own* guard rather than on the
  code under test. via: measurement

- 🔴 **2026-09-14 — AN EMPTY `gh pr checks` ON A PR SECONDS OLD IS EVIDENCE OF NOTHING, AND THIS
  SESSION WATCHED IT FLIP.** `gh pr checks 1657` returned `no checks reported on the branch`
  immediately after `gh pr create`; minutes later the same command listed **three** Tekton gates
  (`devrc-pytests`, `devrc-nodetests`, `devrc-cairn-client-runs`), all `pending`. Recorded as a
  worked example because the failure mode is to write the empty read into a PR body or a handoff
  as "no CI here". via: measurement

- ⚠ **2026-09-14 — `How to verify` had gone stale against `State now` IN THE SAME DOC, and in the
  direction that understates progress.** Its rank-3 block said *"slice 3 — NOT started; all five
  duplicated modules still present"* while `State now` recorded slice 3 merged as #1508. Measured
  at `origin/main`: all five ABSENT. `State now` was right. **A REPLACE section and an APPEND
  section drift apart precisely because only one of them is rewritten each pass** — re-read the
  REPLACE sections against each other before confirming an update. via: measurement

- 🔴 **2026-09-14 — MY INSTRUMENT FAILED FOUR TIMES IN ONE SESSION, ALWAYS IN THE
  REASSURING DIRECTION, AND NEVER THE CODE UNDER TEST.** Four distinct shapes, each of
  which would have been written up as a fact about the code if not re-checked:
  (a) `grep -cF` with a MULTI-LINE pattern splits it one-pattern-per-line, so a trailing
  newline matched every line and three VALID mutants were scored `INVALID: 150 matches`;
  (b) a revert patch that silently NO-LONGER-MATCHED (a comment had been inserted between
  its two lines), so the "RED control" ran against the FIXED tree and printed `5 passed` —
  a control that reads exactly like a test failing to fail; (c) a latin-1 fixture written
  with an em-dash, which is not latin-1 encodable, so the file was never created and the
  mutant scored SURVIVED; (d) reading the WRONG pipelinerun — "newest failed" was a guess
  and returned a **65-byte** log, i.e. a fast setup failure, whose emptiness read as
  "nothing named". **The cure that worked every time: assert the instrument did its job
  before reading its verdict** — match count exactly 1, file exists and has the encoding
  you think, log is bigger than a banner, run attributed by timestamp not by recency.
  via: measurement

- 🔴 **2026-09-14 — A NARROWNESS CONTROL WHOSE FIXTURES LACK THE FEATURE UNDER TEST IS
  STRUCTURALLY BLIND, AND READS AS COVERAGE.** A guard I wrote flagged CORRECT prose:
  `` `[^`]*PHRASE[^`]*` `` opens at the CLOSING backtick of one inline code span and closes
  at the OPENING backtick of the next, so prose BETWEEN two spans was matched. It shipped
  with a "does not flag legitimate prose" control whose three fixtures were all
  **backtick-free** — it could not observe the class at all. Measured on a line that very
  PR had authored. **Ask what FEATURE the defect needs, then check your negative fixtures
  HAVE it.** via: measurement

- 🔴 **2026-09-14 — A BRIEF NAMING ONE INSTANCE OF A MECHANICAL DEFECT IS NAMING A SAMPLE,
  NOT A POPULATION.** Rank 23(c) described ONE scrubbed remedy in `ZacxDev/cairn`. There
  were **twelve**, across **two** different replacement phrases (`a writer` AND
  `the writer half`) plus a fabricated symbol (`entry_shape.build_report`, which exists
  nowhere in that package). Enumerated, not estimated: `git grep` at the base listed 12
  lines; 4 were fixed in the first commit and 8 in the second. The same session's rank 26
  had the mirror-image error in the other direction — the brief claimed TWO drifted tables
  and one was a deliberate gated exclusion. **Count the population before scoping, in both
  directions.** via: measurement

- ⚠ **2026-09-14 — THREE AUDIT ROUNDS ON `ZacxDev/cairn` #17 FOUND ZERO DEFECTS IN THE
  PACKAGE AND FIVE IN THE SCAFFOLDING I WROTE.** The payload was verified by DRIVING it
  (both remedies printed end-to-end, the named command run, its output confirmed) and did
  not move after round 0. Every finding after that was a guard: a pattern with no positive
  control while the commit message claimed one existed; the prose false-positive above; a
  self-exemption pin blind to its own upstream widening path; a verb guard reading 2 files
  while claiming "every"; a silently-dropped non-UTF-8 bucket. **That is the attribution
  gate's shape — the ladder auditing itself — so it was stopped BY DECISION after round 1,
  not on a clean round.** Recorded because a report that stops on the prose criterion is
  otherwise indistinguishable from one that converged. via: measurement

## DEMOTED 2026-09-18 (pass 3) — closed ranked items, moved VERBATIM

Evicted from `claudedocs/handoff-cairn-oss-multi-instance.md` under step 1 of the eviction
playbook in `scripts/tests/test_handoff_doc_size.py` (EVICT WHAT HAS CLOSED), because the doc
had 159 B of headroom against its 98,304 B allowance and could not absorb another audit round.
Every block below is byte-identical to what the doc held at `5b32dd4f`; the doc keeps a
one-line pointer plus each item's durable lesson.

🔴 **RANK 22'S BLOCK BELOW CONTAINS THREE `⚠ STILL OPEN` BULLETS, AND THIS FILE'S OWN PREAMBLE SAYS
AN OPEN THREAD MUST NEVER LIVE HERE. THAT WAS A MISTAKE IN THIS PASS.** It was corrected on
2026-09-18 by **restoring all three to the indexed handoff doc**, which is their home; they are
**not** removed from the block below, because these blocks are a byte-identical snapshot whose
sha256 stamps ARE the preservation proof, and editing one would falsify it.
**So: the copies below are DATED EVIDENCE as of `5b32dd4f`, not the live items** — they have already
drifted (the copy below still defers residual 2 "until slice 3 is planned"; slice 3 merged
2026-09-12, which the indexed copy records and this one cannot). **Read rank 22 in the handoff doc,
never here.**

### rank 7 (demoted 2026-09-18, sha256 1ba9a5145c8e820a)

7. ✅ **CLOSED 2026-09-10 — `ZacxDev/homelab-infra` #787, squash `936692ec7`.** The
   no-reload paragraph is retired in the SAME commit that moves `image:` to `0.8.0`, which is
   what its own expiry clause required: retiring it early leaves the next operator waiting for
   a reload the image will never perform, retiring it late has them replace the pod for
   nothing. What replaces it names the SIGHUP command, keeps the pre-flight advice rescoped to
   pod REPLACEMENTS (startup is still `exit 78` on a malformed file, deliberately), and keeps
   the rule that the running container answers the question — with the positive control the
   original had, plus the `sh -c` that stops your own shell expanding the glob.
   ✅ **CLOSED 2026-09-10.** #787 squash `936692ec7`; the store serves `0.8.0` and the
   RUNNING container carries SIGHUP (0 → 1, positive control held). The retired
   paragraph's own rule survives it: which behaviour an image has is answered by the
   running container, never by a comment's age.
   forcing: none — done

### rank 13 (demoted 2026-09-18, sha256 c81c232a310e118a)

13. ✅ **CLOSED 2026-09-10 — BOTH HALVES. The publish path shipped (`ZacxDev/cairn` #8,
    squash `3167e44`) and the publish happened:**
    `harbor.homelab.lan/library/subsystem-store-api:0.8.0`, digest
    `sha256:55cbd1d6c186142c5fd5e4f3ca37ad0dfc3836db5e603374def041778080c7fd`, built from
    `c84c142`; verified by pulling the tag BACK and re-running the script's controls (11 SIGHUP
    occurrences, `/data` empty), not by trusting the push. `homelab-infra`'s `image:` names it
    as of #787 squash `936692ec7`, with the store serving it.
    ⚠ **The "decide nix vs Dockerfile" premise was the WRONG FORK** and is retired — Harbor is a
    LAN host, so this is a local `docker build` + push, not CI. #8 ports devrc's
    `scripts/subsystem-store-api/build-push.sh` in with the registry as a REQUIRED parameter
    (`CAIRN_REGISTRY`) rather than a hardcoded internal hostname, which in a public repo is both
    a leak and wrong for any other operator.
    🔴 **THE DURABLE LESSONS, kept because each is a shape rather than a fact about #8:**
    - **A raw diff cannot characterise an extraction.** The raw `server.py` diff against the
      deployed copy is 659 lines and says nothing, because the extraction rewrote docstrings
      wholesale. Stripping comments+docstrings and diffing the executable token stream
      (file-against-itself control = 0) gives **638 tokens cairn HAS and the deployed copy
      lacks** against **17 the deployed copy has and cairn lacks, every one a fragment of a
      reworded error-message STRING** — so the cairn server is a strict behavioural SUPERSET.
    - **A publish control must assert the BEHAVIOUR, not just the artefact.** The script's
      controls are `/data` empty (a public repo must not ship a store), the code IMPORTs (the
      positive half — an image with no filesystem reports the same reassuring zero), and **the
      image's `server.py` carries SIGHUP**, so "we published the new server" and "the new server
      does the thing" are not one unchecked claim. That guard was WATCHED to fail in place, for
      its own reason; a first attempt ran the mutant from `/tmp`, where `ROOT` became `/` and the
      BUILD failed instead — a mutant dying for a bystander's reason, not counted.
    - 🔴 **THE PRE-PUBLISH TAG CHECK FAILED ITS POSITIVE CONTROL.**
      `docker manifest inspect …:0.7.0` reported the LIVE, CURRENTLY-DEPLOYED tag ABSENT, so the
      reassuring `0.8.0 absent — safe to publish` beside it carried NO information. Cause: the
      client-side trust store does not carry harbor's CA while the DAEMON's does. **Never probe
      harbor with `docker manifest inspect` from this host** — see the Gotchas entry for the
      one-command discriminator.
    forcing: none — done

### rank 15 (demoted 2026-09-18, sha256 4e0cad089218953d)

15. ✅ **DONE AND LIVE 2026-09-09 — `ZacxDev/cairn` #7 `059ec17`, reaching this host via the
    pin bump in devrc #1433 and a `home-manager switch` (generation 713).**
    **Closing condition MET, exercised on this host:** `cairn recall --ref cairn --scope devrc`
    exits **0** and prints ONE entry (71 lines). It exited **2** for the whole life of this
    item. All four flags the digest's footer prescribes now work (`--ref`, `--list`,
    `--limit`, `--page`), and the refusals are the module's own, shared not copied.
    forcing: none — done

### rank 16 (demoted 2026-09-18, sha256 30935725e6ad5c78)

16. ✅ **DONE — devrc #1433, squash `4a362c8d`.** `checks.cairn-client-runs` builds the pinned
    package and RUNS it: `validate` against a one-entry fixture cache must report
    `1 of 1 entry file(s) parse`, and `doctor --no-sync` must produce a report (its exit code
    deliberately NOT asserted — with no pod, token or network a non-zero verdict is CORRECT).
    The same PR bumped the pin `9213726` → `c84c1429`.
    **Closing condition MET and EXERCISED, not asserted:** green against the real client, and
    RED with the client stubbed to `exit 0`, failing with the check's OWN message rather than
    a bystander's; tree restored byte-identical after.
    🔴 **WHAT IT FOUND ON ITS FIRST RUN, and the reason the item was worth doing:** the pinned
    client's `validate` printed NOTHING on a clean store and exited 0 — and that verb is the
    post-write check the index protocol MANDATES, so every store write validated by the
    packaged client was passing vacuously. **cairn's OWN CI did not catch it** (1709 tests
    green while the verb was inert, because its covering test asserted only `rc == 0` and the
    absence of an error string). Fixed upstream in `ZacxDev/cairn` #11 `c84c1429`. That is the
    empirical answer to "won't upstream catch a broken client" — no, it did not.
    ⚠ **It is an OUTPUT, not yet a GATE — see rank 20.**
    forcing: gate

### rank 17 (demoted 2026-09-18, sha256 6c262c22899716e3)

17. ✅ **DONE AND MERGED 2026-09-09 — `ZacxDev/cairn` #10, squash `934ec38e`.** The
    `--timeout` comment promised a second resolver (`who` + its helper) that was removed
    before publication, so it named two symbols that never existed in this repo. It now names
    `_store_timeout`, the only resolver. **Closing condition MET on `origin/main`: both
    removed names grep to 0.** ⚠ A first draft explained the history by NAMING them, which
    fixed the defect while making the mechanical check report it UNFIXED — a false negative
    manufactured by the fix. The explanation survives without the spelling.
    forcing: none — done

### rank 19 (demoted 2026-09-18, sha256 aedfd14b908aa93b)

19. ✅ **DONE, MERGED AND LIVE 2026-09-09 — `ZacxDev/cairn` #9, squash `a3c84db1`.** The
    client never built a focus window, so its digest could ONLY ever say `most-recent
    fallback` — the wrapper every skill prescribes was strictly WORSE than the raw module it
    says not to use, and its parenthetical ("no handoff doc to read a path window from") was
    WRONG ABOUT THE WORLD, not merely unhelpful.
    **Closing condition MET, exercised on this host after the switch:** `cairn recall --repo
    <devrc>` now reports `resolved via claudedocs/handoff-cairn-oss-multi-instance.md — 16 of
    64 quoted path(s) name it`. The condition used is the MODULE'S own
    (`mode == DEFAULT_MODE and args.scope is None`), so `--scope` still falls back — correct,
    not a bug.
    forcing: none — done

### rank 22 (demoted 2026-09-18, sha256 de5b6987b9af03d1)

22. ✅ **CLOSED 2026-09-12 — the store-api fsync flake. REMEDIED AND MERGED (`#1458`, squash
    `ce9b55c3`, 2026-09-10) AND VERIFIED BY THE FLAKE RATE, which was the half that actually
    closes it: the test is named in **0 of 99** `tekton/devrc-pytests` verdicts on heads
    CARRYING the sha against **12 of 298** that do not (4.03%), P(0) ≈ **0.017**.**
    🔴 **VERIFIED BY CONTENT ON `origin/main`, NEVER BY ANCESTRY** — a squash makes
    `merge-base --is-ancestor` false forever: `sited_root`, `_DISK_ROOTED_ALLOWLIST`,
    `test_the_operand_NODE_TYPE_is_not_what_decides_either` and `slowfsync.c`'s
    `skip_tmpfs_enabled` are all present. 🔴 **The zero is not what establishes the fix — the
    mechanism being gone is.** via: measurement
    ⚠ **It was merged with `tekton/devrc-pytests` RED**, on the second, unrelated flake below.
    "merged" and "merged green" are different claims and only the first is true here.
    🔴 **THE DIAGNOSIS STANDS AND IS THE DURABLE HALF. `server.py:_replace_bytes` issues TWO
    `fsync`s — the file, then the parent directory — inside the request and before the response
    is written.** `fsync` blocks in uninterruptible D-state, is bounded by nothing, and burns no
    CPU, so it is invisible to every CPU-shaped metric; the handler's `timeout = 15` is a SOCKET
    timeout and does not reach a syscall. Four occurrences, all in the write path, all
    `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_CONTROL…`.
    **Grep `MECHANISM =` FIRST on any recurrence** — the instrument already exists and three
    occurrences were spent before anyone read it.
    🔴 **RETRACTED, MEASURED 2026-09-10: THE TEKTON CHECKS ARE NOT REQUIRED, AND THIS ITEM
    ASSERTED THE OPPOSITE FOR ITS WHOLE LIFE.** Earlier revisions — and the rounds of PR
    commentary built on them — said devrc requires both checks with `enforce_admins: true`, so a
    red gate "blocks everyone". Two independent surfaces read the same minute disagree: classic
    branch protection on `main` returns **no required status checks and `enforce_admins:
    false`**, and the repository has **no rulesets and no rules applying to `main`**. The gates
    are **advisory**. That does not make a red gate harmless — it makes it the *other* hazard,
    the one nobody is forced to look at — but "nobody can merge" was false, and it inflated the
    urgency of every gate item in this doc. ⚠ A protection setting is a point-in-time reading:
    **re-read it, do not cite this line.** via: measurement
    🔴 **A GREEN GATE ON `#1458` IS NOT THE VERIFIER** — the gate validating a gate fix is not
    independent evidence, and one green cannot separate "the fix worked" from "this run would
    not have flaked". The verifier is the flake RATE against a fresh baseline:
    `claudedocs/handoff-gate-flake-store-api.md` rank 1.
    ⚠ **STILL OPEN — NOT VERIFIED IN CI, and this is the whole residual:** nothing was measured
    in CI. The dev host has `/tmp` on ext4 and `/dev/shm` on tmpfs; **if the gate container has
    no usable tmpfs, `store_root` falls back to disk BY DESIGN and this changes nothing there.**
    First thing to check if it recurs, and checkable directly — the `store:` path in a failure
    log separates the two by construction (`devrc-store-*` = sited, `pytest-of-*` = fell back).
    ⚠ **STILL OPEN BY DECISION — THE SAME GAP EXISTS IN THE OSS REPO.** Measured 2026-09-09:
    `ZacxDev/cairn`'s `tests/test_subsystem_store_api.py` has the identical **18 open-coded / 5
    sited** split and the same one-fixture guard (`:19716`). Its CI is GitHub-hosted with no
    single-node pin, so the trigger is weaker — but it is the same defect, in the copy the fork
    consolidates ONTO (rank 3 slice 3). Not fixed here to avoid duplicating work the
    consolidation may delete; **decide it when slice 3 is planned, not by default.**
    ⚠ **A SECOND, DISTINCT TIMEOUT FLAKE REDDENED THIS PR AND IT IS NOT THIS ONE.** Tests in
    `scripts/tests/test_run_tests_targets.py` spawn a nested `run-tests.sh` bounded at **120 s**
    and are SIGKILLed at it (`subprocess.TimeoutExpired`, rc `-9`) — **not** an assertion
    failure, and no part of `#1458`'s diff can reach that file. 🔴 **Four claims this item made
    about it are RETRACTED, measured false 2026-09-11; the live item is
    `claudedocs/handoff-gate-flake-store-api.md` rank 7. READ THAT, NOT THIS.**
    📄 **The demoted evidence — the 18-vs-5 siting measurement, the one-site-wide guard, what
    `#1458` ships, the `slowfsync.c` red-before-green, the independent mutation re-run, the
    flake-rate population/predicate/residuals and the four retracted claims verbatim — is
    `claudedocs/refs/cairn-oss-multi-instance.md`.**
    forcing: gate — it has turned a Tekton check red on four PRs, including a docs-only one.
    Advisory, not blocking (see the retraction above)

