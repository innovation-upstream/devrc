# Handoff: cairn-oss-multi-instance — 2026-09-06

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Spin `cairn` out into a public OSS repo that both a personal and a **civitai team**
instance build from, then stand up that second instance so client notes live on client
infrastructure. Decided by the operator over three rounds of questions; the full design
is the PRIVATE proposal, not this doc.

## State now

- ✅ **2026-09-10 — THE THREE OPERATOR-BLOCKED RANKS ARE MERGED, DEPLOYED AND EXERCISED.**
  Four PRs landed, each verified on its mainline BY CONTENT (a squash is never an
  ancestor, so `merge-base --is-ancestor` reads false forever and is not the check):
  - `ZacxDev/homelab-infra` **#785** squash `37b5a71f8` — rank 11, the `cairn` scope.
  - `ZacxDev/homelab-infra` **#787** squash `936692ec7` — rank 13's deploy half **+ rank 7**.
  - `ZacxDev/homelab-infra` **#786** squash `4c890c7ac` — rank 20, the third CI leg.
  - `innovation-upstream/devrc` **#1447** squash `719519fa9` — this doc + the cross-repo
    `flake.nix` retraction.
  **IN FLIGHT:** `innovation-upstream/devrc` **#1472** — corrects three rank HEADINGS that
  contradicted their own closure notes. Open, checks running.

- **Closing conditions EXERCISED, not inferred from the merge:**
  - rank 11 — `cairn create --scope cairn --ref ci-leg --file <f>` → `created scope=cairn
    ref=ci-leg revision=dc4d8212`, **rc 0**. It returned rc 6 `[not-found]` for the item's
    entire life. The scope now holds a real first entry, round-tripped from the pod.
  - rank 13/7 — the store serves `0.8.0` and the RUNNING container carries SIGHUP: measured
    `0 → 1`, with `def load_tokens` held at `1` throughout as the positive control proving
    the search could see the tree. New pod 1/1 Ready, 0 restarts.
  - rank 20 — **HALF.** The leg is live (`devrc-ci-gate` steps are now `clone capture-etc
    seed-nix pytests nodetests cairn-client-runs verdict`), is listed on real PRs, and RAN
    IN-CLUSTER FOR THE FIRST TIME AND PASSED, emitting the exact string the classifier was
    written to emit: `cairn-client-runs verdict=pass nix_rc=0 :: the pinned cairn client
    ran: validate and doctor both produced output`. 🔴 The OTHER half — the leg must go RED
    when the pinned client is stubbed — is **NOT met** and needs a deliberate red on shared
    CI, which is an operator call.

- 🔴 **STILL OPEN AND UNTOUCHED THIS SESSION, stated so the merge flurry above does not read
  as completeness:** rank 4 (`civitai/talos-infra#1414`, re-verified **OPEN**), rank 8
  (§10 session-capture decisions), rank 18 (three deferred findings), rank 21 (re-verified
  **still failing hourly**, `ExecMainStatus=1`), rank 23. **Rank 22 is claimed by ANOTHER
  SESSION** (`cairn-oss-multi-instance-22`) — do not take it.

- 🔴 **RANK 3 SLICE 3 IS THE ONE AGENT-UNBLOCKED ITEM AND IT WAS NOT DONE.** Consolidating
  onto the pin was decided by the operator 2026-09-08 and explicitly must not be re-asked.
  Re-verified 2026-09-10: **all five duplicated modules are still present** in
  `devrc/scripts/lib/` (`host_identity`, `subsystem_resolver`, `subsystem_recall`,
  `cairn_doctor`, `subsystem_read_store`). The fork widens on its own while this sits.

- **Carried forward (durable facts the REPLACE would otherwise drop):** the pinned client
  went live via `home-manager switch --flake <origin/main worktree>#zach --impure` on
  2026-09-09, **generation 713, rollback point 712** — that is the only record of which
  generation to roll back to. `ZacxDev/cairn` stands at **ELEVEN merged PRs, NONE open**
  (re-verified 2026-09-09). ⚠ The **laptop is a SECOND, INDEPENDENT switch** and is still
  NOT verified — this host is not evidence about it, and that is rank 3's last residual
  beside slice 3.

- **Claims:** `cairn-oss-multi-instance-11` and `-13` RELEASED. **`-20` still HELD** — half
  two of its closing condition is unmet, and an unreleased claim is the only thing that
  blocks another session, so release it if you decide not to pursue that half.

## Open investigations — live diagnosis state

### Two ledger guards in cairn are narrower than their own sentences — left OPEN by decision
- **Symptom + exact repro:** read `tests/test_subsystem_store_api.py:20239` and `:20271`
  against their docstrings.
- **Observed (with values):** (a) the fail-closed raise-site walk is
  `if isinstance(exc, ast.Call) and exc.args:` — `raise TokenError`, `raise ValueError()`
  and a bare re-raise are still **silently dropped**, while the docstring says an unreadable
  message is "reported as UNCLAIMED rather than dropped". (b) `_EMITTER_ATTRS` matches
  `.write`/`.writelines` on **any receiver**; `server/server.py:2287` and `:2338` are
  **binary** `fh.write(data)` calls one module-level caller away, so a future module-level
  startup helper that writes would make the ledger demand `reload_safe` on bytes.
- **Ruled out:** that either is a hole in the property the ledger guards — a message-less
  raise cannot echo a field value, and nothing reaches the binary writers today. via: code
- **Leading hypothesis:** both are the same class the whole audit ladder was about (a
  description claiming coverage the body does not provide), one notch smaller, and neither
  ships a defect. Recorded on cairn PR #1 as open-by-decision so they read as open, not absent.
- **Next probe:** none needed. Narrow `_EMITTER_ATTRS` to named sinks (`sys.stdout`/`sys.stderr`)
  and extend the fail-closed arm to non-`Call` raises, in one commit, when someone is next in
  that file.

### The full-suite intermittent in cairn — RATE MEASURED, DID NOT REPRODUCE; one live mechanism closed
🔴 This block SUPERSEDES the two earlier ones on the same subject (both retired in this
edit — do not resurrect them from git history and re-derive their "next probe").
- **Symptom + exact repro:** no reliable repro, and there never was one.
  `TestTheDeployedEntrypoint::test_a_TWO_LINE_token_file_authorises_BOTH_lines` failed
  **once**, in a full run, at PR #1 head `90d30ab`.
- **Observed (with values), 2026-09-06:** **18 CI full-suite runs, 0 failures** — the 9 that
  existed plus 9 reruns requested this session, `failed=0` at collected counts 1593..1651,
  **0 skipped in every one**, so the test genuinely executed in all 18 rather than a green
  run skipping it. Plus **2 local full runs** on the rank-6 branch: `1656 passed / 460.17s`
  and `1657 passed / 473.85s`. Denominator ≈ **43 runs, 1 failure (≈2.3%)**; it did not
  reproduce once. via: measurement
- 🔴 **Ruled OUT as recoverable: the one failure carries no traceback and never will.** The
  transcript that recorded it (`a0759a10-…`) holds only the `-q` short summary — the run was
  read through `pytest -q | tail -1`. So which of three branches fired (`server exited N`,
  `never became healthy`, or a 401 during the overlap) is unknown and unrecoverable. **That,
  not the rate, is why ~43 runs of evidence closed nothing.** via: measurement
- **Measured, and it is a LIVE mechanism, not a theory:** `_free_port()` binds port 0, reads
  the number and CLOSES the socket; the child needs an interpreter startup (~0.2-0.4 s) to
  `bind()` it. The kernel recycles a released ephemeral port inside that window — 3000 trials
  × 20 subsequent `bind(("127.0.0.1", 0))` reused it **8 times**, while the control (same
  loop, socket still OPEN) reused it **0 times**. The health probe itself `connect()`s in
  that window and an outbound connect draws its local port from the same range, so the
  racer is frequently the test process. On collision: rc 1,
  `OSError: [Errno 98] Address already in use`. via: measurement
- **NOT a diagnosis of the observed failure.** The mechanism is live and it fits every
  observed property (full-suite-only, order/timing dependent, pre-existing), but nothing
  ties it to the one failure — see the missing traceback above. Do not write it down as
  the cause.
- **Closed by construction:** `ZacxDev/cairn` **#3** — `_spawn_serving` re-picks and
  respawns on EADDRINUSE only, bounded; `_run_to_completion`'s binding leg does the same;
  and the never-healthy message now names the port, the budget, the last probe exception
  verbatim, earlier lost races, and both drained streams.
- **Next probe:** none scheduled. If it recurs, the message is now self-diagnosing — read
  it rather than re-running. Re-running to a green is what the rules call training everyone
  to click through.

### Whether the opencode exporter's artifact is USEFUL as receipts — never judged
- **Symptom + exact repro:** not a bug; an unclosed question the shipped work deliberately
  did not answer.
- **Observed (with values):** tool parts carry their payload — **0 of 21,749** tool parts
  store-wide have `text`, and **21,749 of 21,749** have `_data`. A real session exports to 210
  records / 1.13 MB, two runs byte-identical. via: measurement
- **Ruled out:** that `text` alone suffices — measured false at 58× the sample the task
  assumed. via: measurement
- **Leading hypothesis:** `_data` carries enough, but nobody has read an artifact end to end
  and said so.
- **Next probe:** export one real session and read it. That is a human judgement over named
  evidence, not a command.

### CLOSED 2026-09-07 — the cairn full-suite intermittent, and the nine-round ladder on its fix
🔴 **This SUPERSEDES and RETIRES the block above titled "The full-suite intermittent in cairn
— RATE MEASURED, DID NOT REPRODUCE; one live mechanism closed".** That block's "Next probe:
none scheduled" still stands; everything else in it is now history. Do not re-run its probes.

- **Outcome:** `ZacxDev/cairn` #3 merged as `8e4ef84`. The intermittent is recorded as
  NOT REPRODUCING; it is **not** claimed fixed, and the PR says so.
- **Observed (with values):** 18 CI runs / 0 failures (9 pre-existing + 9 reruns, `failed=0`,
  **0 skipped in all 18**, collected 1593..1651), plus 5 local full runs across the fix
  rounds. Combined ≈43 runs, 1 failure. via: measurement
- 🔴 **Ruled out as recoverable — the one failure has no traceback and never will.** The run
  was read through `pytest -q | tail -1`; the transcript (`a0759a10-…`) holds only the short
  summary. Which of three branches fired is unknowable. **That, not the rate, is why 43 runs
  closed nothing.** via: measurement
- **Ruled out:** that reordering `poll()`/`terminate()` closes the signal window — it MOVES
  it and inverts the error direction to over-credit. Delivery is now RECORDED
  (`delivered_sigterm = was_running and proc.returncode is None`), exact in all three states.
  via: measurement
- **Left OPEN by decision, recorded in the code so it reads as open rather than absent:**
  (a) the residual over-credit INSIDE `send_signal`, between its poll and its `os.kill` —
  irreducible from outside CPython; (b) `was_running` is provably redundant and its mutant
  survives — the simplification is available and the note says so; (c) the suite-level "no
  stray race warnings" property, which no test inside the suite can assert about itself
  (`filterwarnings = error` was weighed and REJECTED — it would redden every *successfully*
  retried race); (d) several historical figures in comments (~100 µs, 0 flips in 500, 784
  tests) that no future round can re-check, scoped as past measurements.
- **Next probe:** none. If it recurs, the message is self-diagnosing — READ IT rather than
  re-running. Re-running to a green is what trains everyone to click through.

### The devrc and OSS cairn CLIENTS HAVE FORKED — measured, and it is what rank 3's second half must resolve
🔴 This is not a bug. It is the fact that makes rank 3 bigger than its one-line description,
and re-deriving it costs a session an hour. **Do not re-measure it; verify it still holds.**
- **Symptom + exact repro:** `diff -u ~/workspace/devrc/scripts/cairn ~/workspace/cairn/cairn`
  and, per module, `diff ~/workspace/devrc/scripts/lib/<m>.py ~/workspace/cairn/lib/<m>.py`.
- **Observed (with values), 2026-09-07:** the client diff is **259 lines (43 added / 116
  removed)** going devrc→OSS. The removals are almost entirely **`cairn who`** — the
  subcommand, `cmd_who`, `_who_timeout`, `_who_default_timeout`, `_cairn_who` and the parser
  registration; `lib/cairn_who.py` is **absent from the OSS repo** (`git ls-files | grep who`
  → nothing). The additions are `lib/timeouts.py`, a `CAIRN_MIRROR_ROOT` env var, and
  `validate` re-implemented on the resolver instead of shelling a writer's `--validate`.
  Per-module changed-line counts: `host_identity` **113**, `subsystem_resolver` **40**,
  `subsystem_recall` **38**, `cairn_doctor` **21**, `subsystem_read_store` **4**.
  devrc additionally has `scripts/lib/subsystem_touch.py` at **6,654 lines** — the whole
  writer half — against the OSS `lib/entry_shape.py` at **264**, which holds only the shared
  vocabulary. devrc has **15** cairn/subsystem test files; OSS has **7**. via: measurement
- **Ruled out: that the fork is behavioural and therefore expensive to reconcile.** I read
  every module diff: **~90% is sanitisation prose** — docstrings rewriting `subsystem_touch`
  to "the writer half"/`entry_shape` and removing named hosts and dates. The only real
  behaviour change is `cairn_doctor`'s `mirror_root: Path | None` plus a `NOT_OBSERVABLE`
  frozen-mirror check, which is a strict improvement. via: code
- **Ruled out: that `cairn who`'s absence is an oversight to be undone.**
  `claudedocs/proposal-cairn-session-capture.md:569` states it explicitly — the extraction
  removed `cairn who` because session forensics on named hosts is *"not an operation on a
  store"*. It is a decision, not drift. via: doc
- **Leading hypothesis:** the OSS client is a near-superset of devrc's minus one
  deliberately-excluded subcommand, so devrc can consume it if `cairn who` moves to its own
  `cairn-who` binary — which is the seam the OSS cut already chose — and devrc's writer takes
  its shared vocabulary from the pinned `entry_shape` instead of its own copies.
- 🔴 **DECIDED BY THE OPERATOR, 2026-09-08: CONSOLIDATE ONTO THE PIN.** devrc deletes its five
  duplicated `lib/` modules and takes them from the pinned flake; the writer takes its shared
  vocabulary from the pinned `entry_shape` instead of its own copies. **The fork is CLOSED —
  do not re-ask it**, and do not read the paragraph above as a live question. It was put with
  the re-measured numbers below and with this trade named: the cost is that devrc's 6,654-line
  `subsystem_touch.py` must take its vocabulary from the 264-line `entry_shape`, which is the
  real work and the part that can surprise us.
- **RE-MEASURED 2026-09-08, after `cairn-who` merged (devrc #1381). The figures above are from
  09-07 and have moved in BOTH directions.** This item says verify rather than re-derive; this
  is that verification, and it changed the picture:
  - the CLIENT diff **shrank**: 259 lines (43+/116−) → **142 (52+/74−)**. Most of the removals
    were `cairn who`, which now lives in its own binary on both sides.
  - the LIBRARY drift **widened**: `cairn_doctor` **21 → 43** changed lines, `host_identity`
    **113 → 122**. `subsystem_resolver` (40), `subsystem_recall` (38) and
    `subsystem_read_store` (4) are unmoved. The OSS side keeps taking PRs while devrc's copies
    sit still, so this gap grows on its own — which is the argument for consolidating, and it
    is stronger than it was yesterday.
  - 🔴 **there are now TWO `timeouts.py`, one per side, 41 changed lines apart, created hours
    apart on 2026-09-08** — devrc's by #1381, the OSS one by the extraction. The duplication
    this item is about reproduced itself while the item sat open. The OSS copy also defines a
    `DEFAULT_TIMEOUT = 60` that nothing imports; devrc's defines none.
- **Next step:** rank 3's remaining slices, in order — pin the input in `flake.nix`, move
  `~/.local/bin/cairn` into `/nix/store`, then the consolidation above. `cairn-who` stays
  devrc-only and out-of-store; it is deliberately not part of the pin.

### Two ledger guards in cairn are narrower than their own sentences — STILL OPEN by decision
Unchanged this session. `tests/test_subsystem_store_api.py:20239` and `:20271`. Neither ships
a defect; recorded on cairn PR #1 as open-by-decision. Fix when someone is next in that file.

### The devrc/OSS cairn client fork — RE-MEASURED and DECIDED 2026-09-08
⚠ This block previously said the fork was UNCHANGED and unmeasured this session. Both halves
are now out of date: devrc #1381 moved the client, and the numbers were re-run. The client
diff SHRANK to 142 lines while the library drift WIDENED (`cairn_doctor` 21 → 43). 🔴 **The
operator fork is ANSWERED: CONSOLIDATE ONTO THE PIN** — devrc deletes its five duplicated
`lib/` modules and the writer takes its vocabulary from the pinned `entry_shape`. Full
decision, trade and figures in the fork block above; do not re-ask it.

### CLOSED-PENDING-MERGE 2026-09-08 — a SECOND cairn intermittent, distinct from the one rank 6 closed
🔴 **This is NOT the flake the rank-6 investigation was about.** That one was
`TestTheDeployedEntrypoint::test_a_TWO_LINE_token_file_authorises_BOTH_lines`. This is a
different test, found by reading CI for a rate exactly as the stale kickoff asked — and unlike
rank 6's, **this one carries its evidence**, which is why it was fixable rather than merely
countable.
- **Symptom + exact repro:** no local repro needed; it is in CI history.
  `tests/test_subsystem_store_api.py::TestAReloadIsAtomicUnderLoad::test_no_observer_EVER_sees_a_table_that_is_neither`
  fails with `AssertionError: only 1 reload(s) were driven inside the 3s budget, so at most one
  swap was available to observe and the verdict below is about a static table` / `assert 1 >= 2`.
- **Observed (with values), 2026-09-08:** the repo's **entire** CI history is **26 runs, 24
  success / 2 failure** (published 2026-09-05). **Both failures are this same assertion**:
  `e2cf6fe` 2026-09-07T04:21Z and `492191f` 2026-09-08T01:05Z — and the second **postdates #3's
  merge** (2026-09-07T18:01Z), so it is not residue of the rank-6 work. ≈**2/26 (7.7%)**.
  via: measurement
- **Ruled out: that this is general runner load.** Wall-time discriminator per RULES — the
  failing run took **497.47s** against a passing run's **477.52s**, ~4%. Load inflates every test
  in a run; this inflated exactly one test's own budget, so it is a narrow timing dependency in
  that one test. via: measurement
- **Ruled out: that it is a product defect.** The assertion is a *positive control* the test
  makes about itself, and it was CORRECT to refuse — with one swap there is nothing to observe.
  The defect was that `ATOMICITY_SAMPLE_BUDGET_S = 3.0` bounded **both** the samplers and the
  reload driver, so the deadline could stop the driver at `reloads == 1` and starve the control
  it was written to police. Mechanism: one `reload_tokens` call outlasting the whole budget while
  four sampler threads contend for the GIL. via: code
- **Fixed in #5, and the fix is NOT just the obvious half.** `ATOMICITY_MIN_RELOADS = 2` is read
  by **both** the loop and the assertion so they cannot drift. The driver breaks only on
  `reloads >= MIN and deadline passed`, `range(400)` retained as the runaway bound. 🔴 **The
  samplers are gated on the reload count too** — gating only the driver would let the minimum be
  reached after every observer had stopped, satisfying `reloads >= 2` while making the "no third
  state" verdict vacuous. Both original controls unchanged in strength.
- **Control, before/after** (budget 0.35s, 0.5s injected per `reload_tokens` call): before →
  **1** reload, FAIL; after → **2**, pass; after with MIN overridden to 5 → **5**, pass; before
  with MIN overridden to 5 → **1**, same FAIL. The loop tracks the constant, not the clock.
  via: measurement
- 🔴 **A SURVIVING MUTANT, reported rather than hidden:** clear-then-refill *with no widened
  window* **survives this test**. It was proven live by watching the structural sibling
  `test_a_successful_reload_REBINDS_and_leaves_the_old_tuple_INTACT` go red on it. This is a
  pre-existing limit that sibling's own docstring already states; #5 neither causes nor fixes it,
  and nothing was adjusted to hide it. **Not filed as a work item** — no closing condition
  distinguishes it from the sibling guard that already covers it. via: measurement
- **Next probe:** none. Merge #5. If it recurs after that, the assertion now names the constant
  it fell short of rather than the budget, so read the message.

### CLOSED 2026-09-08 — rank 12, leakscan's coverage was an enumeration (MERGED as `9d58f02`)
- **Symptom + exact repro:** not a failure anyone saw — a silent gap. `git show
  9213726:tests/leakscan.py` line 110: `TEXT_SUFFIXES` is a hand-written set, and
  `tracked_files()` drops any file whose suffix is absent from it. The run then prints
  `0 findings across N file(s)` where N is files SCANNED, never files present, so nothing in
  the output distinguishes "clean" from "did not look".
- **Observed (with values), 2026-09-08:** the tree is **39 tracked files**, suffix census
  `.py` 26, `` (none) 4, `.md` 2, `.sh` 2, `.yml`/`.lock`/`.nix`/`.dockerignore`/`.json` 1
  each. **Zero files contain a NUL byte and all 39 decode as valid UTF-8**, so the
  enumeration happened to cover everything *today* — the hazard was entirely about the next
  new type. Baseline run: `38 file(s) scanned`, 0 findings, rc 0 (39 minus the self-exempt
  `tests/leakscan.py`). via: measurement
- **Ruled out: that the existing guard test already closed it.**
  `test_leakscan_covers_every_tracked_file.py` pinned the enumeration against the tracked
  tree, which catches "a new type nobody added" at TEST time — but its own docstring said
  *"THIS DOES NOT MAKE THE COVERAGE DERIVED, and that is still the better fix. A genuinely
  derived scanner would not need this file."* The scanner itself still skipped silently.
  via: code
- **Ruled out: that a suffix fast-path was worth keeping alongside the derivation.** Keeping
  it leaves the enumeration load-bearing, so the class stays open; the sniff is bounded at
  8000 bytes, so a huge binary is not read in full anyway. via: code
- **Fixed in `ZacxDev/cairn` #6, MERGED 2026-09-08 as `9d58f02`** (PR head was `b5bd231`).
  `partition_tracked_files()`
  buckets every enumerated file — including the directory skips — so
  `set(scanned) | set(skipped)` equals the enumeration by construction and the test asserts
  it without re-implementing any filtering. `enumerate_repo(root)` is parameterised so the
  test module DRIVES it instead of keeping the copy it used to justify at length.
  `main(argv=None)` was added because the new tests could not call `main()` at all —
  `parse_args()` read pytest's argv and exited 2.
- **Regression matrix, measured both ways:** RED at merge base `9213726` —
  `test_a_tracked_text_file_of_an_UNFAMILIAR_TYPE_is_scanned` fails on its OWN assertion,
  `assert 'notes.rst' in set()`: the base scanner returned an EMPTY scan set for a tracked
  `.rst` holding a real hostname. GREEN at HEAD. ⚠ The sibling
  `..._is_actually_REFUSED` also fails at base but for an API reason (`main()` takes no
  argv there), so it is **NOT** regression evidence and was not counted as any.
  via: measurement
- **Mutation battery: 8/8 killed BY THEIR INTENDED TEST**, harness control watched green on
  the pristine tree first, `PYTHONDONTWRITEBYTECODE=1`, every pattern required to match
  exactly once (0 or 2 matches ⇒ INVALID, never a pass). Mutants: `is_binary` always False /
  always True; sniff window one byte short; a suffix allowlist creeping back in; the fixture
  file dropped instead of bucketed; `main` no longer printing skips; enumeration losing
  `-z`; enumeration narrowed to cached-only. Tree diffed byte-identical against the
  battery's snapshot afterwards. via: measurement
- **Full suite 1703 passed / 0 failed, 547s local; CI `collected=1703 failed=0 floor=1648`.**
  via: measurement
- **Next probe:** none. Merged; the closing condition is met by content and was watched on
  the merged tree (38 scanned / 1 skipped, the skip named, rc 0).

### The devrc and OSS cairn CLIENTS HAVE FORKED — status unchanged this session, ONE FIGURE NOW STALE
⚠ Not re-measured this session; the decision (CONSOLIDATE ONTO THE PIN) stands and is not
re-asked. 🔴 But the figures in that block predate `4c77daab`, the other session's unpushed
commit, which already moves `flake.nix`, `nix/home.nix` and `nix/sessionVariables.nix`.
**Re-measure the client and library diffs AFTER that lands**, not before — a measurement
taken now describes a tree that is about to change.

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

### The `cairn` client never built a FOCUS WINDOW — FIXED on a branch, suite result unread
- **Symptom + exact repro:** run both readers against the same store at the same instant.
  `cairn recall --repo ~/workspace/devrc --no-sync | grep 'FEATURED IN FULL'` versus
  `python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc | grep 'FEATURED IN FULL'`.
- **Observed (with values), 2026-09-08:** client →
  `most-recent fallback — newest entry file in \`devrc/\` (no handoff doc to read a path
  window from)`; module → `resolved via claudedocs/handoff-cairn-oss-multi-instance.md — 11
  of 48 quoted path(s) name it: devrc/scripts/cairn, scripts/lib/timeouts.py …`.
  🔴 The parenthetical was **wrong about the world**, not merely unhelpful: the handoff doc
  was there and the client never looked. `grep -c focus_window <client>` → **0**, while
  `focus_window` is in the module's `__all__`. via: measurement
- **Ruled out: that this is cosmetic.** It inverts the advice every skill gives — the wrapper
  `/resume` step 4 PRESCRIBES was strictly worse than the raw module it says not to use. This
  session ate it: the featured entry came back as `tests`, unrelated to the effort. via: measurement
- **Ruled out: that the fix needs a new condition.** The module already has one
  (`mode == DEFAULT_MODE and args.scope is None`); the client now mirrors it rather than
  inventing a rule, so `--scope` still falls back — correct, not a bug. via: code
- **Leading hypothesis:** none needed; cause and fix are both known. Branch
  `fix/client-focus-window` passes `focus_paths`/`focus_source` and its output is byte-for-byte
  the module's.
- **Next probe:** read `/tmp/focus-suite.log` for `PYTEST_RC=`. Green ⇒ open the PR. The
  regression test is RED at base `3167e44` on its own assertion; the fixture uses a repo NAMED
  for its scope, because passing `--scope` would suppress the very window under test.

### CLOSED 2026-09-09 — `tekton/devrc-pytests` red on a test the diff never touched: TIER, not tree
🔴 **This SUPERSEDES the "NOT YET ATTRIBUTED" reading in the previous revision.** The
discriminator arrived from an unrelated PR, not from more sampling.
- **Symptom + exact repro:** `TestARefusedWriteIsIndistinguishableFromAnAbsentOne.test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_dif`
  fails in Tekton. The class is `scripts/tests/test_subsystem_store_api.py:13100`.
- 🔴 **THE CONTROL, and it is decisive: devrc PR #1417 failed on the SAME assertion, and
  #1417 changes exactly ONE file — `claudedocs/handoff-cairn-oss-multi-instance.md`, a
  markdown doc.** A docs-only diff cannot break a server-API test. Two PRs, disjoint diffs,
  one failing test ⇒ the failure is in the **tier**, not the tree. via: measurement
- **Observed (with values):** passes locally — **5 passed, 739 deselected, 7.09 s** — and
  inside the hermetic `scripts/tests` run in the same window. The tier was never broken:
  #1411 passed it at `collected=21163 failed=0`. via: measurement
- **Ruled out: that the other red devrc runs share this cause.** Same window, five failed
  SHAs, **four distinct verdict classes** — two genuine single-test failures on *different*
  tests, two `KILLED: … the gate pod died at or after step pytests`, one `BROKEN GATE: step
  clone failed (rc 128)`. Per the `tekton` skill the last three are congestion, and the
  discriminator is whether the step emitted a verdict at all. via: measurement
- **NOT established: the root cause.** The runs are pruned (`keep: 20` per pipeline, hourly),
  so which assertion fired is unrecoverable. "Tier, not tree" is an attribution, not a
  mechanism. The test spawns a real server and binds a port, and the box ran at load 50–72.
- **Next probe:** none scheduled. If it recurs, read the log **before the hourly prune**
  rather than re-running — the run that carried the evidence is already gone twice.

### CLOSED 2026-09-09 — the three reds only the MERGED tree could find
🔴 Each is a different lesson, and none would have appeared on the branch alone.
- **A seam ledger doing its job, not an obstacle.** `test_store_root_ledger` went red because
  `scripts/cairn-validate` is a **new** router through `subsystem_read_store`. That ledger
  fails when the router set **GROWS** as well as when it shrinks, precisely so a new reader
  cannot quietly start answering "where do I read?" for itself. A row was added. via: code
- 🔴 **A guard structurally incapable of passing in one of the two tiers, and dev-host green
  is what hid it.** `test_cairn_validate_defaults_its_store_to_the_SYNCED_CACHE_not_the_mirror`
  asserted on **stdout**, which holds where the cache exists and the tool takes its success
  path. The `nix build` tier's `$HOME` is `/build/home` with no `~/.cache/subsystem-store`, so
  the tool exits down the not-found path and names the resolved root on **stderr**. The claim
  is WHICH store the launcher chose, never whether one exists. ⚠ The implementing round wrote
  *"I believe they are sandbox-safe, but that is reasoning, not a measurement."* It was
  reasoning, and it was wrong. via: measurement
- **Re-measured at TWO points, because one is not a general claim:** green with a real cache
  root and green under a `$HOME` verified to have none; the mutant that drops the `--store`
  prepend is KILLED at **both**, on the guard's own message. The negative half — the frozen
  mirror's path must NOT appear — did not exist before and is what kills that mutant when both
  paths happen to be printed. via: measurement
- **A scan hit fixed by pinning a RELATIONSHIP rather than allowlisting a string.**
  `test_runtime_shebangs` flagged a spelled `"#!"`. Its header reserves the allowlist for
  sites solving the problem a verified way, **not for going green** — so the assertion now
  pins that `cairn-validate`'s interpreter line equals `cairn-who`'s. Both are
  `mkOutOfStoreSymlink` launchers run as bare commands from PATH, so they must agree; the
  literal disappeared as a consequence rather than as the goal. via: code
- **Next probe:** none. Fixed in `29f16402`, gate green on `48bb44e3` from two runners.

### Round 1 and round 3's guards — the mutation evidence, kept so nobody re-derives it
- **Round 1's 🟡 was a guard NARROWER THAN ITS OWN DOCSTRING, not an inert one.** The decoy
  `cairnPackage = pkgs.hello;` **plus** `cairnUnused = cairn.packages.${system}.cairn;`
  **SURVIVED at `f98be263` (8 passed)** while `home.file.".local/bin/cairn".source` became
  `${pkgs.hello}/bin/cairn` — home-manager's `insertFileEntry` does an unconditional `ln -s`,
  so that BUILDS and deploys a **dangling symlink**. `pkgs.hello` **alone** was already
  killed: the failure needed a decoy carrying the string the second assertion looked for.
  via: measurement
- 🔴 **Round 1 fixed a false RED and opened a path to a false GREEN.** Its bracket walk fell
  off the end when depth never returned to 0, leaving `header` at the **whole file**;
  `cairnPackage` occurs twice in the module body, so the assertion passed vacuously. MEASURED
  at `b79cf63a`: a legal multi-line header that drops `cairnPackage` and carries one
  unbalanced `(` in a prose comment → **8 passed**. via: measurement
- 🔴 **A mutant SURVIVED round 3's first battery and is recorded rather than hidden.**
  Reinstating the whole-file fallback survived: once comments stop carrying depth the
  vacuous-case fixture closes correctly, so it never reached the fall-off-the-end branch — an
  **unreachable guard**, green for the wrong reason. A third test with a header having no
  closing `}` at all — a case no earlier assertion rejects — killed it. via: measurement
- **A control in the other direction:** a cosmetic **rewrap** of the pinned command across
  three lines stays green, so the whitespace normalisation is doing work rather than the pin
  being brittle. via: measurement
- **Ruled out: that the ledger pin was ever binding.** Before round 3 it named FLAGS only —
  routing the same flags at the packaged client left the suite **green at 79 passed**, fully
  re-opening round 1's 🔴. via: measurement
- **Next probe:** none. All matrices are on PR #1406's round-2 and round-3 comments.

### STILL OPEN by decision — five round-2 🟡s the operator chose not to block the merge on
- 🟡2 `count == 1` false-reds two legal nix spellings, and a one-line
  `{ cairnPackage = real; } // { cairnPackage = pkgs.hello; }` override **still walks it**
  (8 passed, deploying `${pkgs.hello}/bin/cairn`). The multi-line form IS killed.
- 🟡5 `SECRETS.md:26` states the pinned package **is** the deployed client and `scripts/cairn`
  is "no longer deployed" — false until both hosts switch. Same false tense at
  `claude/skills/cairn/SKILL.md:89-92`.
- 🟡7 `claude/skills/subsystem-index/SKILL.md` is **41,591 B** against `HARD = 40_960`
  (`scripts/skill-audit.py:168`). Round 3 cut 678 B; under the cap is **arithmetically
  unreachable** from round 1's block alone — the file was 31 B over before round 1 touched it.
  🔴 `HARD` is exercised only against tmp fixtures, **never against the tree**, so no gate
  will ever go red on this. ⚠ Merge-brought: `claude/skills/handoff/SKILL.md` sits at
  **34 B of headroom** against an enforced ratchet — the next commit to touch it reds a gate
  that will blame the wrong change.
- 🟡8 the pinned package's own `🔴 MALFORMED —` remedy prints ``check a file with `a writer
  --validate <path>` `` (the extraction scrub). Lives in `ZacxDev/cairn`, not devrc.
- 🟢 the prose pin cannot distinguish "mandated" from "merely mentioned" — demoting the
  command to an aside **while keeping the full string intact** survives it.
- **Next probe:** these are rank 18's successors. None blocks anything today.

### 2026-09-09 — the recurring CI intermittent finally CARRIED ITS EVIDENCE: a socket READ TIMEOUT
🔴 **This SUPERSEDES the "attributed to the TIER, not the tree — root cause unknown" reading.**
The attribution stands; the mechanism is now measured. Three occurrences were spent before
this one, because each run was pruned before anyone read it.
- **Symptom + exact repro:** no local repro.
  `test_subsystem_store_api.py::TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference`
  fails in the Tekton `pytests` tier. Occurrences: #1406 at `f98be263`, #1417 (docs-only),
  #1425 at `f3bdca9e`.
- 🔴 **THE TRACEBACK, read from `devrc-ci-qxf9n`'s `step-pytests` log BEFORE the hourly prune
  — which is the whole reason it exists this time.** It is **not** an assertion failure:
  `_post` → `post_bullet` → `fetch` → `urlopen` → `http.client` → `socket.recv_into` →
  **`TimeoutError: timed out`** at `socket.py:720`. The HTTP request to the spawned test
  server was **established and then never answered**. via: measurement
- 🔴 **Ruled out: general runner load — by the wall-time discriminator, at two points.** Load
  inflates EVERY test in a run; a failed assertion inflates exactly one. In the FAILING CI run
  `scripts/collector/tests` took **14.27 s**, against **36.03 s** for the same target on the
  loaded dev host, and the whole `scripts/tests` target ran **1091.87 s** against the dev
  host's **1181.72 s**. CI was *faster* than the box that passed. The run was not inflated;
  one socket read timed out while everything around it ran quickly. via: measurement
- 🔴 **Ruled out: that it is caused by any diff.** PR **#1417 changed exactly one file** — a
  markdown handoff doc — and failed on the identical assertion. A docs-only diff cannot break
  a server-API test. via: measurement
- 🔴 **A REAL, INDEPENDENT GAP FOUND WHILE DIAGNOSING — devrc's fork never received cairn #3.**
  `scripts/tests/test_subsystem_store_api.py:6910`'s `_free_port()` is the **pre-#3** version:
  it binds port 0, reads the number, closes the socket, and returns — with **no retry, no
  `SPAWN_ATTEMPTS`, no `_lost_the_port_race`**. `ZacxDev/cairn` closed that TOCTOU in **#3
  (`8e4ef84`)** and its copy now carries the measurement in the docstring (3000 trials × 20
  binds recycled the released port **8** times; the control, socket still OPEN, **0**). devrc
  has carried the unfixed copy the whole time. This is the client fork showing up in CI rather
  than in the client. via: code
- ⚠ **NOT a diagnosis, and the distinction matters.** The TOCTOU's known signature is the
  child dying with **EADDRINUSE**, which surfaces as a connection *refused* — not as an
  established connection that never answers. A read timeout means something accepted and did
  not reply. The port race is a **plausible contributor** and an unfixed gap worth closing on
  its own merits; nothing measured ties it to THIS failure. Do not write it down as the cause.
- **Next probe:** port cairn #3's retry into devrc's copy (see the new ranked item) and see
  whether the rate moves. 🔴 **If it recurs first, pull the log IMMEDIATELY** —
  `KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipelineruns -o json`, filter
  `.spec.params[] | select(.name=="revision")`, then
  `kubectl -n tekton-ci logs pod/<run>-gate-pod -c step-pytests`. The pruner is `keep: 20`
  **per pipeline**, hourly; three occurrences were already lost to it.

### CLOSED 2026-09-09 — the CI intermittent is `SERVER_BLOCKED_IN_FSYNC`, named by the instrument built for it
🔴 **THIS SUPERSEDES BOTH EARLIER READINGS IN THIS DOC** — "attributed to the TIER, not the
tree, root cause unknown", and the block that offered the missing cairn-#3 port race as a
"plausible contributor". **The port race is NOT the mechanism. That hypothesis is RETRACTED**;
it remains a real unfixed gap on its own merits, and nothing more.
- **Symptom + exact repro:** `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference`
  fails in the Tekton `pytests` tier. **Four occurrences**: #1406 at `f98be263`, #1417
  (docs-only), #1425 at `f3bdca9e`, #1425 at `9a5b883d`.
- 🔴 **THE VERDICT, IDENTICAL IN BOTH LOGS I PULLED BEFORE THE PRUNER TOOK THEM:**
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC (handler threads=1 [Thread-815
  (process_request_thread)=SERVER_BLOCKED_IN_FSYNC], accept loop parked=True)`.
  via: measurement
- 🔴 **THE INSTRUMENT ALREADY EXISTED AND NOBODY HAD READ ITS OUTPUT.**
  `_why_the_server_did_not_answer()` (`scripts/tests/test_subsystem_store_api.py:460`) emits
  that `MECHANISM =` line *precisely* so a CI log can be grepped for it without a human
  reading stacks — its own docstring says the store-api hang "stayed open for weeks" because
  **a client-side read timeout is the observable the most mechanisms share, so on its own it
  identifies none of them.** Three occurrences were spent re-deriving that ambiguity. **Grep
  the log for `MECHANISM =` FIRST.** via: code
- **The mechanism, from that docstring:** `server.py:_replace_bytes` issues **two** `fsync`s —
  the file, then the parent directory — **inside the request and before the response is
  written**. `fsync` blocks in uninterruptible D-state, is bounded by nothing, and **burns no
  CPU**. The handler's `timeout = 15` does not bound it: that is a SOCKET timeout and does not
  reach a syscall. So the write path stalls on disk and the client's read times out.
- 🔴 **Ruled out: general CPU load — and the ruling-out is CONSISTENT with the mechanism, not
  in tension with it.** Wall-time discriminator, CI-to-CI: the failing runs' `scripts/tests`
  took **818.69 s** and **1091.87 s**, and `scripts/collector/tests` **11.21 s** and
  **14.27 s** — *faster* than the dev host that passed (1181.72 s / 36.03 s). Nothing was
  inflated. That is exactly what an `fsync` stall looks like: it consumes no CPU, so it cannot
  appear in a CPU-shaped measurement. via: measurement
- 🔴 **Ruled out: that it is caused by any diff.** #1417 changed **exactly one markdown file**
  and failed identically. via: measurement
- **Precondition corroborated:** the cluster is saturated. `talos-xr6-r7p` — the single node
  both pipelines `nodeSelector`-pin to — is emitting `Insufficient cpu`, `FailedScheduling`
  and `Preempted`, and **`main`'s OWN gate is `KILLED`** (`the gate pod died at or after step
  pytests`). Disk contention on that node is the load this test cannot tolerate.
  via: measurement
- ⚠ **NOT established:** the disk-level numbers. I did not measure `talos-xr6-r7p`'s device
  utilisation or PSI-io at the moment of failure, so "disk contention" is inferred from the
  fsync park plus the node's scheduling state, not read off a disk metric.
- **Next probe — and it is NOT a re-run.** Three options, none of them "run it again":
  (a) bound the write path so a stalled `fsync` fails fast instead of hanging past the client
  timeout; (b) raise this test's client timeout, which trades a red gate for a slow one and
  does not make the server correct; (c) unpin the CI pipelines from one node so the disk is
  not shared. 🔴 **Re-running to green is what `claude/RULES.md` calls training everyone to
  click through, and with `enforce_admins: true` on devrc a permanently-red required check
  blocks everyone.** Whichever is chosen, `MECHANISM =` is now the first thing to grep.

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

## Next steps (ranked)

🔴 Numbering is STABLE and is half a claim's identity (`claim-work --slug-for <this doc>
<rank>`). Items are marked done IN PLACE; new items APPEND.

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
   🔴 **THE LAPTOP IS STILL UNSWITCHED, and it is BLOCKED ON THE HOST, not on a
   decision.** `ship.sh` (no flags) exited **255**: `ssh: connect to host 192.168.50.155
   port 22: Connection timed out`, and it answers no ICMP either. Cross-host agreement is
   therefore `NOT COMPARED — 1 of 2 hosts reported a landed sha`. Until it is powered on and
   converged, every OSS-client fix is absent from the binary THAT machine runs.
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
     slice 2 ✅ MERGED as #1406 `9300f234`; slice 3 (point the writer at the pinned
     `entry_shape`, delete devrc's five duplicated `lib/` modules) NOT STARTED.** The fork was
     DECIDED 2026-09-08 — CONSOLIDATE ONTO THE PIN — and is not to be re-asked.
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

4. **Merge or close `civitai/talos-infra` #1414** (the instance proposal). Four open questions
   in §11; none blocks A3. **RE-VERIFIED LIVE 2026-09-09: still OPEN** (`state: OPEN`,
   `mergedAt: null`).
   🔴 **AND A RECONCILER FALSE POSITIVE TO NOT FALL FOR AGAIN.** `resume-state.sh` reported
   `PR #1414 MERGED but handoff frames it as open/in-flight`. That is a **devrc** PR — a
   handoff-doc PR that merged days ago — because the digest resolves a BARE `#N` against the
   repo it is run in, and this doc's rank 4 is `civitai/talos-infra#1414`. The same applies to
   its `#1433 MERGED` and `#1417 CLOSED` lines: all three are devrc PRs this doc already
   records. **Write `owner/repo#N` in this doc so the reconciler can attribute it** — that is
   the documented remedy, and a bare number here costs a session a wrong "go do the follow-on".
   forcing: none

5. ✅ **DONE 2026-09-06 — `ZacxDev/cairn` #2, `c8aee7203`.**
   forcing: none — done

6. ✅ **DONE AND MERGED 2026-09-07 — `ZacxDev/cairn` #3, `8e4ef84`.**
   forcing: none — done

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

8. **Session capture — DESIGNED AND DECIDED, NOT BUILT.**
   `claudedocs/proposal-cairn-session-capture.md`, `e16f9609a`. Read §10 first.
   **Closing condition:** none yet — the first implementation PR would earn one.
   forcing: none

9. ✅ **DONE 2026-09-06 — clawgate #511, devrc `f58d2df04`.** ⚠ The module has NO CALLER.
   forcing: none — done

10. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #5, `9213726`.**
    forcing: none — done

11. 🔴 **OPERATOR ACTION — add a `cairn` scope to the store token's allowlist.**
    **RE-VERIFIED LIVE 2026-09-08, still refused:** `cairn create --scope cairn --ref
    rank11-probe --file <f>` → **rc 6**, `🔴 cairn: the store REFUSED the write [not-found]`.
    Nothing was written. The local cache still holds **23** scopes with `cairn` absent, and
    `subsystem_recall.py --repo ~/workspace/cairn` reports `status=scope-absent`.
    ⚠ This is what made THIS session's `/handoff` step 4 dead-end: the rank-12 lessons could
    not be recorded under a `cairn` scope and live in this doc's Gotchas instead — the exact
    "cairn-repo lessons keep landing elsewhere" cost this item names.
    ⚠ Note the invocation: `--file` is REQUIRED, and omitting it exits **2** (argparse) —
    which is NOT the refusal and must not be read as one.
    🔨 **DECIDED AND IN A PR 2026-09-09 — `ZacxDev/homelab-infra` #785**, `tekton/gitops-validate`
    **pass**. Adds `cairn` to the token's scope allowlist: 23 → 24 scopes, all 23 originals
    still present (sorted set difference `removed: []` / `added: [cairn]`), decrypted
    before/after diff a SINGLE line. Live pod and the tracked secret agreed on the before
    state, so this was not a git-only claim.
    ⚠ **A `sops` trap worth keeping:** `sops` resolves `.sops.yaml` from the INVOKING CWD, not
    from the file path. Run from another checkout it loads that repo's rules and dies with
    `no matching creation rules found` on a file this repo's catch-all covers perfectly well.
    Pin it with `--config`, do not `cd`.
    ✅ **CLOSED 2026-09-10 — CONDITION EXERCISED, NOT INFERRED.** After #785 merged and
    the pod rolled, `cairn create --scope cairn --ref ci-leg --file <f>` returned
    `created scope=cairn ref=ci-leg revision=dc4d8212`, **rc 0** — it had returned rc 6
    `[not-found]` for this item's entire life. The rc was CAPTURED, not piped (a pipe
    returns `tail`'s status and reads a refusal as a write). The scope now holds a real
    first entry, verified round-tripping from the pod: `1 of 1 entry in cairn/`.
    forcing: none — done

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

13. 🔨 **PUBLISH PATH BUILT AND MERGED — `ZacxDev/cairn` #8, squash `3167e44`. THE PUBLISH
    ITSELF IS AN OPERATOR STEP AND HAS NOT HAPPENED.** ⚠ **The "decide nix vs Dockerfile"
    premise was the WRONG FORK** and is retired: the deployed image is
    `harbor.homelab.lan/library/subsystem-store-api:0.7.0`, built by **devrc's**
    `scripts/subsystem-store-api/build-push.sh` from the **pre-extraction** server — and
    cairn had no publish script at all. Harbor is a LAN host, so GitHub Actions cannot reach
    it; this is a local `docker build` + push, not CI. #8 ports the script in, with the
    registry as a REQUIRED parameter (`CAIRN_REGISTRY`) rather than a hardcoded internal
    hostname, which in a public repo is both a leak and wrong for any other operator.
    **Measured, so the upgrade is characterised rather than assumed:** the raw `server.py`
    diff against the deployed copy is 659 lines and says nothing, because the extraction
    rewrote docstrings wholesale. Stripping comments+docstrings and diffing the executable
    token stream (file-against-itself control = 0) gives **638 tokens cairn HAS and the
    deployed copy lacks** — `import signal`, `RELOAD_PREFIX`/`RELOAD_LOADED`/`RELOAD_REFUSED`,
    `redacted_field()`, `MAX_SCOPE_CHARS` — against **17 the deployed copy has and cairn
    lacks, every one a fragment of a reworded error-message STRING, not a construct.** So the
    cairn server is a strict behavioural SUPERSET, and the SIGHUP reload is exactly what
    rank 7 waits on.
    **Controls in the script, one of them new:** `/data` must be empty in the image (a public
    repo must not ship a store); the code must IMPORT (the positive half — an image with no
    filesystem reports the same reassuring zero); and **the image's `server.py` must carry
    SIGHUP**, so "we published the new server" and "the new server does the thing" are not
    one unchecked claim. That guard was WATCHED to fail in place, for its own reason, after
    the earlier controls printed OK — a first attempt ran the mutant from `/tmp`, where
    `ROOT` became `/` and the BUILD failed instead, which is a mutant dying for a bystander's
    reason and was not counted.
    ✅ **THE TWO STRINGS WERE GIVEN 2026-09-09 — `harbor.homelab.lan`, `0.8.0` — AND THE
    PUBLISH HAPPENED.** `harbor.homelab.lan/library/subsystem-store-api:0.8.0`, digest
    `sha256:55cbd1d6c186142c5fd5e4f3ca37ad0dfc3836db5e603374def041778080c7fd`, built from
    `c84c142` (`b25abb5` is an ancestor). All three of the script's controls green, then
    **re-run against the copy pulled BACK from the registry** — 11 SIGHUP occurrences,
    `/data` empty — because a push reporting success is a claim about the push.
    🔴 **THE PRE-PUBLISH TAG CHECK FAILED ITS POSITIVE CONTROL, AND THAT IS THE LESSON.**
    `docker manifest inspect …:0.7.0` reported the LIVE, CURRENTLY-DEPLOYED tag ABSENT — so
    the reassuring `0.8.0 absent — safe to publish` beside it carried NO information. Cause:
    `docker manifest inspect` and `curl` use a client-side trust store that does not carry
    harbor's CA, while the DAEMON's `/etc/docker/certs.d/harbor.homelab.lan/ca.crt` does. The
    discriminator that settles it in one command, without downloading anything: pull a tag
    that certainly does not exist and read the ERROR SHAPE — `not found` means the daemon
    reaches and authenticates; `x509` means it cannot. **Never probe harbor with
    `docker manifest inspect` from this host.**
    ✅ **CLOSED 2026-09-10 — BOTH HALVES.** The tag is in the registry (verified by
    pulling it BACK and re-running the controls, not by trusting the push), and
    `homelab-infra`'s `image:` names it as of #787 squash `936692ec7`, with the store
    serving it.
    forcing: none — done

14. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #5, `9213726`** (same PR as rank 10).
    forcing: gate — it turned the public repo's only CI gate red on 2 of its first 26 runs

15. ✅ **DONE AND LIVE 2026-09-09 — `ZacxDev/cairn` #7 `059ec17`, reaching this host via the
    pin bump in devrc #1433 and a `home-manager switch` (generation 713).**
    **Closing condition MET, exercised on this host:** `cairn recall --ref cairn --scope devrc`
    exits **0** and prints ONE entry (71 lines). It exited **2** for the whole life of this
    item. All four flags the digest's footer prescribes now work (`--ref`, `--list`,
    `--limit`, `--page`), and the refusals are the module's own, shared not copied.
    forcing: none — done
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

17. ✅ **DONE AND MERGED 2026-09-09 — `ZacxDev/cairn` #10, squash `934ec38e`.** The
    `--timeout` comment promised a second resolver (`who` + its helper) that was removed
    before publication, so it named two symbols that never existed in this repo. It now names
    `_store_timeout`, the only resolver. **Closing condition MET on `origin/main`: both
    removed names grep to 0.** ⚠ A first draft explained the history by NAMING them, which
    fixed the defect while making the mechanical check report it UNFIXED — a false negative
    manufactured by the fix. The explanation survives without the spelling.
    forcing: none — done
18. **Three deferred findings from #1406's round-1 audit, none blocking.** (a)
    `nix/sessionVariables.nix` hardcodes `.claude/analyze-service-index`, a SECOND `.nix`
    spelling of `subsystem_touch.DEFAULT_STORE_ROOT`, which `test_store_root_ledger.py`
    structurally cannot see because its own residuals section puts `.nix` out of scope. (b)
    The packaged `lib/host_identity.py` honours `CAIRN_HOST`; devrc's copy does not — dormant
    today (nothing sets it), and it would make `cairn recall`'s host banner disagree with the
    writer's. (c) `claude/skills/cairn/SKILL.md`'s "consolidated in a later slice" names no
    owner and no mechanism. (a) and (b) both disappear if rank 3 slice 3 lands.
    **Closing condition:** rank 3 slice 3 merges, or a PR that addresses (a)–(c) explicitly.
    forcing: none


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
20. **Wire `checks.cairn-client-runs` into CI — it currently runs only on demand.**
    `devrc-ci-pipeline.yaml` (in the infra repo, NOT devrc) hardcodes exactly two legs:
    `LEG` ∈ {`pytests`, `nodetests`}, built as `.#checks.x86_64-linux.${LEG}`. Measured
    2026-09-09: **2** static `value:` assignments, no `nix flake check`, no loop — so a third
    output is never built by CI and the check cannot fail a PR. The check itself says this in
    `flake.nix` rather than reading like a gate it is not.
    ⚠ **Deliberately not bundled into #1433:** that pipeline lives in a GitOps-reconciled repo
    where committing to the mainline IS deploying, which is an operator decision, not a rider
    on a devrc PR. Cost is not the obstacle — the check rebuilds in **~1.4 s** and the cairn
    package is the same derivation home-manager already builds, so it adds no build.
    🔨 **DECIDED AND IN A PR 2026-09-09 — `ZacxDev/homelab-infra` #786.** The measurement above
    was re-verified before building: still exactly 2 static `LEG` values, still 0 references to
    `cairn-client-runs`, and both `nix flake check` occurrences in the file are comments.
    🔴 **IT WAS NOT A ONE-LINE CHANGE, and the description above under-sold it.** The legs are
    STEPS inside one `devrc-ci-gate` Task, so a third leg also needs a context param on notify,
    on report and on the Pipeline, threaded from the TriggerTemplate, plus the verdict loop and
    `post_leg` — twelve sites, not one `value:`.
    🔴 **AND IT DERIVES ITS VERDICT DIFFERENTLY, WHICH IS THE PART THAT WOULD HAVE SHIPPED
    BROKEN.** The other two legs parse a `RESULT: PASS|FAIL` line their runners emit; this
    check is a `runCommandLocal` that emits no such line, so copying their logic scores every
    GREEN run `error`. The build's exit status is the verdict, and `unknown` (the `.rc` file
    absent ⇒ the step was killed) stays the separate `error` third state.
    🔴 **THE REPO'S OWN TESTS CAUGHT A REAL DEFECT: 54 of 119 went red on `CAIRN_CTX: unbound
    variable`**, because the report harness builds the Task's env and did not know about the
    third var. Fixed in the harness, never by weakening the fail-closed guard. The leg ledger
    now asserts SET EQUALITY over three legs, so it fails when the set GROWS as well as shrinks.
    ⚠ **Deliberately NOT a required check.** Making a brand-new leg required the day it lands
    would let its first infrastructure hiccup block every merge on a repo with
    `enforce_admins: true`. Promoting it in branch protection is a later, reversible operator
    action needing no change to the file.
    🔨 **MERGED 2026-09-10 (squash `4c890c7ac`) AND LIVE IN-CLUSTER**: `devrc-ci-gate`'s
    steps are now `clone capture-etc seed-nix pytests nodetests cairn-client-runs
    verdict`, and `devrc-ci-notify` carries `cairn-context`. Flux applied
    `trunk@4c890c7ac`.
    🔴 **THE CLOSING CONDITION IS STILL ONLY HALF MET, AND THE REMAINING HALF NEEDS A
    REAL RUN.** Wired is not gating: the leg must be SEEN on `gh pr checks <a devrc
    PR>` and must go RED when the pinned client is stubbed to print nothing. The first
    devrc PR to run after this merge is the one that answers half one.
    ⚠ And the leg has never executed in-cluster: every measurement across six audit
    rounds is one dev host plus a local `nixos/nix:2.24.15` container.
    forcing: none

21. **`analyze-service-index-commit.service` is VESTIGIAL and fails on every firing — 603
    failures in 3 days.** It tries to `git config` inside the local mirror, which the cairn
    cutover deliberately FROZE (`555` on scope dirs, `444` on entries), so it gets
    `could not lock config file .git/config: Permission denied` per scope and exits 1. Since
    the cutover the POD is the authority and does its own versioning, so a local job
    committing a read-only mirror can never succeed and has nothing to commit.
    ⚠ **Not data loss and not caused by any switch** — first seen 2026-09-06, timer-triggered;
    a `home-manager switch` merely REPORTS the already-failing unit ("Failed services: …"),
    which is easy to misread as switch fallout. It is cutover fallout.
    🔴 "Delete the unit" vs "point it at the pod" is a DECISION, not a cleanup — the second
    only makes sense if anything still wants local versioning, and nothing obviously does.
    **Closing condition:** the unit is removed from the home-manager config, OR its next timer
    firing exits 0.
    forcing: none

22. ⚠ **REMEDIED AND MERGED — `#1458`, squash `ce9b55c3`, 2026-09-10. NOT YET VERIFIED BY THE
    FLAKE RATE, which is the half that actually closes this. And the three remedies this item
    recommended were all aimed at the wrong layer.**
    🔴 **VERIFIED BY CONTENT ON `origin/main`, NEVER BY ANCESTRY** — a squash merge makes
    `merge-base --is-ancestor` false forever, so that check reads "not merged" and is wrong.
    On `origin/main`: the `sited_root` fixture is present, `_DISK_ROOTED_ALLOWLIST` is present,
    `test_the_operand_NODE_TYPE_is_not_what_decides_either` is present, and `slowfsync.c`
    carries `skip_tmpfs_enabled`. The one surviving `tmp_path / "store"` in the api file is at
    `:367`, inside a docstring — prose, not a site. via: measurement
    ⚠ **It was merged with `tekton/devrc-pytests` RED**, on the second, unrelated flake
    described below — attributed, unreachable from the diff, and on an advisory rather than a
    required check. Recorded because "merged" and "merged green" are different claims and only
    the first is true here.
    The DIAGNOSIS below stands and is unretracted. `server.py:_replace_bytes` issues **two**
    `fsync`s — the file, then the parent directory — **inside the request and before the
    response is written**. `fsync` blocks in uninterruptible D-state, is bounded by nothing,
    and burns no CPU, so it is invisible to every CPU-shaped metric; the handler's
    `timeout = 15` is a SOCKET timeout and does not reach a syscall. Four occurrences, all in
    the write path, all
    `TestARefusedWriteIsIndistinguishableFromAnAbsentOne::test_POSITIVE_CONTROL…`.
    🔴 **THIS IS A REAL GATE RISK: devrc requires both Tekton checks with
    `enforce_admins: true`, so when it fires nobody can merge.** **Grep `MECHANISM =` FIRST on
    any recurrence** — the instrument already exists and three occurrences were spent before
    anyone read it.
    🔴 **WHAT THIS ITEM GOT WRONG, MEASURED 2026-09-09 — THE REMEDY ALREADY EXISTED AND HAD
    NEVER REACHED THE FAILING SITE.** It offered (a) bound the write path — *recommended, and
    the only one that makes the SERVER correct* — (b) raise the client timeout, (c) unpin CI
    from one node. **(a) is a production change to `server.py`'s crash-durability semantics
    made to close what is a TEST-HARNESS SITING GAP**, and the in-file docstring argues neither
    fsync is removable (without the directory fsync, a node losing power after `os.replace`
    returns can come back with the old name on the old inode, **having already answered
    `200 appended`**). (b) is banned in-file. What the tree actually said:
    - `TestARefusedWriteIsIndistinguishableFromAnAbsentOne._phases` built its store at a bare
      `tmp_path / "store"` — it never called `store_siting.store_root()`. **5** sites in
      `scripts/tests/test_subsystem_store_api.py` were sited; **18** were not, and the failing
      one was among the 18. The tmpfs fix (#1211/#1219/#1239) never covered it. via: code
    - **The mechanism predicts WHICH test fails, which is what makes this more than
      compatible-with-the-evidence.** Every sibling in that class asserts a 404 (refused or
      absent), and a 404 never reaches `_replace_bytes`. `test_POSITIVE_CONTROL…` is the only
      test in the class that gets `200 appended`, so it is the only one that executes the two
      in-request fsyncs. via: code
    - **`scripts/ci-repro/README.md` already carried the confirming measurement and nobody had
      reconciled it against the siting fix:** the real CI traceback stalls on
      `…/pytest-of-nixbld13/pytest-0/popen-gw3/…/store` — a `tmp_path`-derived path on the step
      container's ephemeral layer, **not** a `devrc-store-*` tmpfs holder. The failing writer is
      an unsited root. via: measurement
    - ✅ **This also retires the `_HUNG_SERVER_RULES` path-sensitivity caveat FOR CI** (that
      classifier matches the substring `fsync` against rendered filenames, so a worktree named
      `*fsync*` makes it report `SERVER_BLOCKED_IN_FSYNC` unconditionally): the CI path contains
      no `fsync`, so the verdict is genuine. The classifier defect itself is untouched and is
      still `handoff-gate-flake-store-api.md` rank 2.
    🔴 **THE GUARD THAT SHOULD HAVE CAUGHT THIS WAS ONE SITE WIDE — a description claiming
    coverage the body did not provide.** `TestTheStoreIsSitedOffTheContendedDisk` says "a
    fixture that silently fell back to disk **everywhere** would leave the suite exactly as
    flaky while every test still passed", and its positive control takes only the `store`
    fixture. The old ratchet was a COUNT (`_DISK_ROOTED_SITES = 33`) — a count of declarations,
    not of what they cover.
    **What `#1458` ships:** all 18 sites take a new `sited_root` fixture; the count-ratchet
    becomes `_DISK_ROOTED_ALLOWLIST`, an enumerated set keyed `<Class.function> :: <expr>`
    (never line numbers) asserted in BOTH directions, plus `_SITED_STORE_ROOT_CALLERS` pinning
    the other side. 15 sites stay allowlisted with reasons — argued write-free **from the call
    graph, not from a runtime trace**.
    ⚠ **`slowfsync.c` in its shipped form CANNOT measure a siting fix** — it interposes on libc
    `fsync`, so it stalls tmpfs too (65.0 s on ext4 *and* on tmpfs). `#1458` adds an opt-in
    `SLOWFSYNC_SKIP_TMPFS=1`. Red-before-green with it: `origin/main` **1 failed in 63.96s**
    (`TimeoutError` @ `socket.py:720`, `MECHANISM = SERVER_BLOCKED_IN_FSYNC`); branch **1 passed
    in 3.67s**; branch with the fallback forced to disk **1 failed in 64.29s** — so the green is
    the SITING, not an inert reproducer.
    **Census guard mutation RE-RUN INDEPENDENTLY, not taken on the implementing agent's
    report:** reverting the failing test to `tmp_path / "store"`, `__pycache__` cleared,
    `PYTHONDONTWRITEBYTECODE=1` → `test_the_disk_rooted_census_matches_the_allowlist_EXACTLY`
    RED **with its own message**, naming the exact site, **22 others still passing** — reachable
    and specific, not a suite-wide break. Mutant reverted; tree clean.
    ⚠ **NOT VERIFIED, and this is the whole residual:** nothing here was measured in CI. The dev
    host has `/tmp` on ext4 and `/dev/shm` on tmpfs; **if the gate container has no usable
    tmpfs, `store_root` falls back to disk BY DESIGN and this changes nothing there.** That is
    the first thing to check if it recurs, and it is checkable directly — the `store:` path in a
    failure log distinguishes the two by construction (`devrc-store-*` = sited, `pytest-of-*` =
    fell back).
    🔴 **A GREEN GATE ON `#1458` IS NOT THE VERIFIER, AND THIS TRAP IS ALREADY RECORDED ONCE** —
    the gate validating a gate fix is not independent evidence, and one green cannot separate
    "the fix worked" from "this run would not have flaked". The verifier is the flake RATE
    against a fresh baseline: **`handoff-gate-flake-store-api.md` rank 1**, which this doc should
    have been citing all along and was not.
    ⚠ **THE SAME GAP EXISTS IN THE OSS REPO AND IS DELIBERATELY LEFT OPEN.** Measured
    2026-09-09: `ZacxDev/cairn`'s `tests/test_subsystem_store_api.py` has the identical **18
    open-coded / 5 sited** split and the same one-fixture guard (`:19716`). Its CI is
    GitHub-hosted with no single-node pin, so the trigger is weaker — but it is the same defect,
    in the copy the fork consolidates ONTO (rank 3 slice 3). Not fixed here to avoid duplicating
    work the consolidation may delete; **decide it when slice 3 is planned, not by default.**
    🔴 **RETRACTED, MEASURED 2026-09-10: THE TEKTON CHECKS ARE NOT REQUIRED, AND THIS DOC HAS
    BEEN ASSERTING THE OPPOSITE.** Earlier revisions of this item — and the rounds of PR
    commentary built on them — said devrc requires both Tekton checks with
    `enforce_admins: true`, so a red gate "blocks everyone". Two independent surfaces, read the
    same minute, disagree: classic branch protection on `main` returns **no required status
    checks and `enforce_admins: false`**, and the repository has **no rulesets and no rules
    applying to `main`**. The gates are **advisory**. That does not make a red gate harmless —
    it makes it the *other* hazard, the one nobody is forced to look at — but "nobody can merge"
    was false, and it was inflating the urgency of every gate item in this doc. ⚠ A protection
    setting is a point-in-time reading and can be changed without touching this repo: **re-read
    it, do not cite this line.** via: measurement
    **Closing condition:** `#1458` merged, AND a flake-rate reading against a baseline whose PR
    heads postdate the merge — **not a single green run**. The OSS half closes separately, with
    rank 3 slice 3.
    ⚠ **A SECOND, DISTINCT TIMEOUT FLAKE NOW REDDENS THIS PR, AND IT IS NOT THIS ONE.**
    `test_run_tests_targets.py::test_the_subset_note_reports_N_of_the_FULL_set_not_N_of_N`
    (added hours earlier by #1445's own audit ladder — its docstring reads "🟡 round-2 F5")
    spawns a nested full `run-tests.sh` and bounds it at **120 s**; on `a6dd11eb` that
    subprocess was **SIGKILLed at the bound** (`subprocess.TimeoutExpired`, returncode `-9`,
    `test_run_tests_targets.py:756`). It is **not an assertion failure**, no part of #1458's
    diff can reach that file, and #1462 — same base, same test — **passed minutes earlier**, so
    it is non-deterministic. Wall-time discriminator, CI-to-CI: the failing and passing runs are
    near-identical (1030.34 s vs 1065.64 s; 155.60 s vs 154.73 s — the failing run was
    *faster*), so the node was not inflated; one bounded operation stalled while everything
    around it ran normally. **Same signature, one layer up: a wall-clock bound inside a test on
    a contended node.** Belongs with `handoff-gate-flake-store-api.md` rank 1, not with a
    re-run. via: measurement
    forcing: gate — it has turned a Tekton check red on four PRs, including a docs-only one.
    Advisory, not blocking (see the retraction above)

23. **Two exit-127 / stale-spelling residues the round-3 fix round did not cover.**
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
    forcing: none

## Gotchas / decisions / dead-ends

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
create route" was quoted from a handoff note that predated the change closing it — `PUT` with
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

**`gh run rerun` IS A FREE DENOMINATOR, AND IT REALLY RE-EXECUTES.** Nine reruns ran
concurrently in ~10 min against the ~75 min a local loop would cost. Verified rather than
assumed: one rerun's log shows its own later timestamp with `collected=1651 failed=0`. Also
read `skipped` — a green run that SKIPPED the test under investigation contributes nothing.

**A RATE IS THE WRONG INSTRUMENT WHEN THE ONE OBSERVATION CARRIED NO EVIDENCE.** No achievable
N distinguishes 2% from 0%. A one-in-N flake produces its evidence once; a pipe that keeps the
count and throws the traceback away spends that occurrence for nothing.

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
⚠ The class is open — see rank 11.

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

**🔴 `leakscan`'s COVERAGE IS AN ENUMERATION, AND IT WAS BLIND TO THE FILE BEING ADDED.**
`.nix` was absent from `TEXT_SUFFIXES`, so #4's own `flake.nix` — hand-written prose in a
public repo — would have been unscanned while the run printed `0 findings across 34 files`.
The guard written for it then found a SECOND gap nobody had spotted:
`server/Dockerfile.dockerignore` was unscanned on `main`. The class is still open — rank 12.

**A `[not-found]` FROM `cairn create` DOES NOT MEAN THE SCOPE IS UNSEEDED** — the API refuses
to distinguish, deliberately. See the `devrc/cairn` store entry (revision `4f3cb4da30f648e4`)
and rank 11.

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

**🔴 A CLASSIFIER GRADED BY READING GETS REWRITTEN UNTIL SOMETHING EXECUTES IT.** #786's
~20 lines of verdict shell went through FOUR rewrites across a six-round audit ladder, each
fix shipping the OPPOSITE defect of the one before: every non-zero rc `fail` (a broken gate
blamed on the author) → marker-less `error` (a broken PIN excused as infrastructure) → a
bare drv-name match, which nix's PRE-OUTCOME `building '…drv'...` makes true on every run
that builds → markers-first, which nix's RECOVERED `warning: unable to download … retrying`
makes true while the build succeeds. Rounds 1–3 were each verified by careful reading. The
fix was a 17-row table that lifts the SHIPPED shell out of the YAML and runs it under a real
`sh`; all four historical classifiers were replayed into the pipeline and caught.
**Transferable tell: when a fix and its predecessor keep swapping which direction they are
wrong in, the missing thing is EXECUTION, not care.**

**🔴 THE FIX ROUND'S OWN PROSE WAS THE RECURRING SECOND FINDING — five false claims across
six rounds, every one caught by an audit and none by me:** a justification invented for a
fallback that does not exist in that leg; a stale number four lines above the one just
corrected; a generalisation ("per-step requests are sized well under p99") false for the
step that dominates the pod; a trailer described as `last 10 log lines:` and UNPREFIXED when
it is `Last N log lines:` with a `       > ` prefix; and TWO false verification figures in
the PR body — one a filtered test run reported as full coverage, one a `RESULT: PASS` line
belonging to an unrelated shell test. ⚠ And a sixth, found only when the operator asked what
was outstanding: ranks 11/13/20 were closed with correct closure notes while their HEADINGS
still read `🔴 OPERATOR ACTION` / `HAS NOT HAPPENED` / `runs only on demand`. The heading is
the surface a reader hits first.

**⚠ A LEDGER FIGURE PUBLISHED ON #786 WAS WRONG AND IS CORRECTED HERE:** the executable
payload series is `22 → 16 → 7 → 5 → 4` and total payload `95 → 95 → 56 → 49 → 47`. The
"103 → 95 → 56 → 5 → 4" figure conflated the two. The trend that ended the ladder holds; the
number did not.

**Two instrument traps that cost real time:** `docker manifest inspect` reports a LIVE,
currently-deployed Harbor tag as ABSENT from this host (client-side trust store lacks the CA
the daemon has) — the one-command discriminator that downloads nothing is to pull a
certainly-absent tag and read the error SHAPE (`not found` = reachable, `x509` = not). And
`sops` resolves `.sops.yaml` from the INVOKING CWD, not the file path, so running it from
another checkout dies with `no matching creation rules found` on a file this repo's catch-all
covers; pin it with `--config`, do not `cd`.

## How to verify

🔴 **Verify a merge by CONTENT, never ancestry — a squash is never an ancestor.**

```bash
# the four merges (all MERGED; content checks, not ancestry)
gh pr view 785 -R ZacxDev/homelab-infra --json state,mergeCommit   # 37b5a71f8
gh pr view 786 -R ZacxDev/homelab-infra --json state,mergeCommit   # 4c890c7ac
gh pr view 787 -R ZacxDev/homelab-infra --json state,mergeCommit   # 936692ec7
gh pr view 1447 -R innovation-upstream/devrc --json state,mergeCommit  # 719519fa9

# rank 13/7 — read the RUNNING container, never the manifest, and keep the control
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get deploy subsystem-store-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'          # expect 0.8.0
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec deploy/subsystem-store-api -- \
  sh -c 'grep -rl SIGHUP /app | wc -l; grep -rc "def load_tokens" /app/server/server.py'
#   expect 1 and 1 — the second is the POSITIVE CONTROL; a bare 0 on the first without it
#   is indistinguishable from a grep that walked nothing. The `sh -c` is load-bearing.

# rank 11 — the scope is writable, and the entry is really there
cairn recall --ref ci-leg --scope cairn        # 1 of 1 entry in `cairn/`

# rank 20 — half one only
KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get task devrc-ci-gate \
  -o jsonpath='{range .spec.steps[*]}{.name}{" "}{end}{"\n"}'
#   expect: clone capture-etc seed-nix pytests nodetests cairn-client-runs verdict
gh pr checks <any open devrc PR>               # expect tekton/devrc-cairn-client-runs listed
#   🔴 HALF TWO IS UNMET: nothing here shows the leg goes RED when the client is stubbed.

# rank 21 — still failing, re-verified 2026-09-10
systemctl --user show analyze-service-index-commit.service -p Result -p ExecMainStatus
#   expect Result=exit-code ExecMainStatus=1

# rank 3 slice 3 — NOT started; all five duplicated modules still present
ls ~/workspace/devrc/scripts/lib/{host_identity,subsystem_resolver,subsystem_recall,cairn_doctor,subsystem_read_store}.py
```
Expected: four MERGED shas; store on `0.8.0` with SIGHUP `1` and control `1`; the cairn
scope holding one entry; a seven-step gate Task; rank 21 still `ExecMainStatus=1`; five
module files still present.
