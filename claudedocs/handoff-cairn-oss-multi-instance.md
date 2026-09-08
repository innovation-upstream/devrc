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

- **`ZacxDev/cairn` has FIVE merged PRs.** #1 SIGHUP hot-reload, #2 the ledger narrowing
  (`c8aee7203`), #3 `8e4ef84` (spawn-port TOCTOU), #4 `218b6c1` (the nix flake), and
  **#5 `9213726` (2026-09-08T05:45:44Z) — the CI floor + the reload-atomicity control.**
  Verified by CONTENT, never ancestry (a squash is never an ancestor): on `origin/main`
  `.github/workflows/ci.yml` reads `FLOOR = 1648` and `ATOMICITY_MIN_RELOADS` resolves **7**
  times in `tests/test_subsystem_store_api.py`.
- **Ranks 10 and 14 are CLOSED by that merge** — both closing conditions named it.
  `claim-work` slug `cairn-oss-multi-instance-10` RELEASED.
- **Rank 3's second half is claimed by ANOTHER SESSION**, not this one: `claim-work --list`
  shows `cairn-oss-multi-instance-3` taken 2026-09-08 ~01:45Z for the *first slice only*
  (re-home `cairn who` as its own binary; extract `unbounded_timeout_reason` into devrc
  `lib/timeouts.py`), explicitly **not** pinning the flake and **not** touching the
  `entry_shape`/writer fork. The operator fork below is therefore still unanswered — that
  session routed around it rather than deciding it.
- **Rank 3 is still not done**, re-verified live 2026-09-08: `readlink -f ~/.local/bin/cairn`
  → `/home/zach/workspace/devrc/scripts/cairn` (not `/nix/store`), `grep -c cairn
  ~/workspace/devrc/flake.nix` → **0**.
- **Rank 4 re-verified live:** `civitai/talos-infra` #1414 still **OPEN**.
- **Rank 11 re-verified live:** the store holds **23** scopes and `cairn` is still not among
  them — `cairn create --scope cairn` was watched to refuse `[not-found]` this session.
- 🔴 **A cairn-built image is still NOT PUBLISHED.** #4 produces a loadable tarball; nothing
  pushes it to a registry. Rank 7 remains blocked on publication (rank 13), not buildability.
- **No civitai instance exists.** The homelab pod still runs its own copy.
- **Session capture** remains DESIGNED, DECIDED and MERGED as a proposal, and BUILT NOWHERE
  (`claudedocs/proposal-cairn-session-capture.md`, `e16f9609a`). Rank 8.
- **The opencode exporter** shipped (`f58d2df04`, #1338) and still has **no caller**.
- **devrc PR #1386** carries this handoff doc.

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
- **Next probe:** none needed to decide; the measurement is done. The open QUESTION is a
  design one for the devrc side: whether devrc deletes its five duplicated `lib/` modules in
  favour of the pinned ones (consolidation, and the drift above is the argument for it) or
  keeps them. That is rank 3's second half, and it is a fork worth putting to the operator
  before building.

### Two ledger guards in cairn are narrower than their own sentences — STILL OPEN by decision
Unchanged this session. `tests/test_subsystem_store_api.py:20239` and `:20271`. Neither ships
a defect; recorded on cairn PR #1 as open-by-decision. Fix when someone is next in that file.

### The devrc/OSS cairn client fork — UNCHANGED, and it is rank 3's second half
The measurement in the block above still stands and was not re-run this session. Nothing in
#4 touched `cairn` or `lib/`, so the fork is exactly as measured. **The operator fork inside
it is still unanswered:** whether devrc DELETES its five duplicated `lib/` modules in favour
of the pinned ones, or keeps them. Put that question before building.

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

3. 🔨 **HALF DONE — Phase A3, devrc consumes cairn as a pinned flake input.**
   🔴 **IN FLIGHT ELSEWHERE — `claim-work` slug `cairn-oss-multi-instance-3` is HELD by another
   session** (taken 2026-09-08 ~01:45Z) for the **first slice only**: re-home `cairn who` as its
   own `cairn-who` binary + extract `unbounded_timeout_reason` into devrc `lib/timeouts.py`.
   That session explicitly did NOT pin the flake and did NOT touch the `entry_shape`/writer fork.
   - **Half 1 — ✅ MERGED 2026-09-08: `ZacxDev/cairn`#4, squash `218b6c1`.**
   - **Half 2 — the rest is still open** after that slice: pin the input in `flake.nix`, move
     `~/.local/bin/cairn` into `/nix/store`, point the writer at the pinned `entry_shape`, and
     decide the fate of devrc's five duplicated `lib/` modules. 🔴 **THE OPERATOR FORK IS STILL
     UNANSWERED and must be put before building** — see the fork investigation above.
   **Closing condition:** a merged devrc PR in which `flake.nix` names cairn as an input and
   `readlink -f ~/.local/bin/cairn` resolves into `/nix/store`. Re-verified NOT met 2026-09-08.
   forcing: none

4. **Merge or close `civitai/talos-infra` #1414** (the instance proposal). Four open questions
   in §11. None blocks A3. **Re-verified live 2026-09-08: still OPEN.**
   forcing: none

5. ✅ **DONE 2026-09-06 — `ZacxDev/cairn` #2, squash `c8aee7203`.** Both ledger 🟢s closed.
   🔴 Neither is regression coverage, and the commit says so.
   forcing: none — done

6. ✅ **DONE AND MERGED 2026-09-07 — `ZacxDev/cairn` #3, squash `8e4ef84`.** Rate measured,
   did NOT reproduce; **not "fixed"**, and the PR says so. Nine audit rounds.
   🔴 **A LATER KICKOFF MESSAGE RE-ISSUED THIS ITEM AS OPEN.** A 2026-09-08 `/resume` arrived
   carrying "the intermittent now sits at ~27 runs / 1 failure — read the next N CI runs for a
   RATE". That was already superseded twice over. **A kickoff block is a snapshot of the moment
   it was written, not a live instruction** — reconcile it against the doc's own rank table
   before acting on it. Reading CI anyway is what surfaced rank 14, so the run was not wasted.
   forcing: none — done

7. **Retire `deployment.yaml`'s no-reload paragraph IN THE SAME COMMIT that moves the store's
   `image:` tag to one built from cairn at or past `b25abb5`.**
   **Closing condition:** a merged `ZacxDev/homelab-infra` PR in which the `image:` line and
   that paragraph change together — checkable from the diff alone.
   ⚠ **Still blocked on PUBLICATION** (rank 13): #4 produces a loadable tarball and nothing
   pushes it to a registry.
   forcing: none — it cannot fire before a published image exists

8. **Session capture — DESIGNED AND DECIDED, NOT BUILT.**
   `claudedocs/proposal-cairn-session-capture.md`, `e16f9609a` (#1326). Sixteen operator
   decisions in §2, NOT to be re-litigated.
   🔴 **Read §10 first: four things are genuinely undecided**, led by *who READS* the recorded
   fan-out sets.
   ⚠ Its audit ladder ended by operator instruction at round 9, NOT on a clean round.
   **Closing condition:** none yet — the first implementation PR would earn one.
   forcing: none

9. ✅ **DONE 2026-09-06 — clawgate #511 `complete`, devrc `f58d2df04` (#1338).**
   ⚠ **The module has NO CALLER.** Wiring it in is rank 8's work.
   forcing: none — done

10. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #5, squash `9213726`.** cairn's CI
    collected-test floor moved from an inert `FLOOR = 200` (against 1698 collected) to
    **1648** = `m - min(50, max(1, m/20))` for the measured `m`.
    **Gate controls watched in both directions:** green at 1698 and at exactly 1648; **red at
    1647 and at 300**; and **green at 300 under the OLD `FLOOR = 200`** — that last one is the
    demonstration the old floor was inert, not an assertion that it was.
    ⚠ **This is an INVARIANT GUARD, not regression coverage** — no bug ever narrowed this suite.
    The workflow comment and the PR body both say so.
    forcing: none — done

11. 🔴 **OPERATOR ACTION — add a `cairn` scope to the store token's allowlist.** Work in
    `~/workspace/cairn` cannot be recorded in the subsystem store: `cairn create --scope cairn`
    is refused `[not-found]`, exit 6 — **watched again 2026-09-08**, and the store still holds
    **23** scopes with `cairn` not among them. cairn-repo lessons keep landing in `devrc/cairn`
    instead. ⚠ When re-checking, do NOT pipe `cairn create` into `tail` — the pipe returns
    `tail`'s status and the refusal reads as `rc=0`.
    **Closing condition:** `cairn create --scope cairn …` exits 0.
    forcing: none

12. **Derive `leakscan.py`'s file coverage instead of enumerating it.** #4 closed two holes
    (`.nix`, then `.dockerignore`) and added a guard pinning the set against `git ls-files`. The
    CLASS is open: coverage is still a hand-written suffix list, so the next new file type in
    this PUBLIC repo is unscanned while the run prints a confident `0 findings`.
    **Closing condition:** a merged `ZacxDev/cairn` PR in which a tracked, non-binary file the
    scan skips causes a non-zero exit or an explicit `SKIPPED` line naming it.
    forcing: security — the repo is public and the gate is the reason it can be

13. **Publish a cairn-built image to a registry.** Rank 7 is blocked on this and nothing
    schedules it. `packages.server-image` produces a loadable tarball; something must push it
    under a tag `homelab-infra` can pin. ⚠ Decide FIRST whether the deployed pod should be the
    nix image or keep the Dockerfile build — `CLAUDE.md` in cairn records a measured
    difference table and explicitly does not settle it.
    **Closing condition:** a tag in the registry built from cairn at or past `b25abb5`, and
    `homelab-infra`'s `image:` line able to name it.
    forcing: none

14. ✅ **DONE AND MERGED 2026-09-08 — `ZacxDev/cairn` #5, squash `9213726`** (the same PR as
    rank 10; two ranks, one PR, deliberately — both are "the gate is weaker than it claims" in
    the same two files). The reload-atomicity positive control is no longer bounded by the
    budget it polices: `ATOMICITY_MIN_RELOADS = 2` is read by BOTH the driver loop and the
    assertion, and the SAMPLERS are gated on it too.
    ⚠ **NOT claimed as "the flake is gone"** — it is claimed that the control can no longer be
    starved by its own budget. A recurrence after `9213726` is a NEW finding, not this one, and
    the assertion now names the constant it fell short of rather than the budget.
    forcing: gate — it turned the public repo's only CI gate red on 2 of its first 26 runs

15. **`cairn recall` prescribes flags its own CLI rejects.** The digest's footer says
    "`--ref <name>` prints any one of them in full; `--limit 31` prints them all", but
    `cairn recall` accepts only `--scope/--repo/--no-sync/--mode` and exits **2** on `--ref`.
    `/resume` step 4 documents the same drill-downs against that command, so a session following
    either verbatim dead-ends and falls back to `cairn search` or the raw reader.
    ⚠ Also measured 2026-09-08: the `devrc` digest is now **98.7 KB**, against the ~5 KB the
    skill documents — 31 entries, `tests` alone carrying 94 nuance bullets. The skill's byte
    figures are from 2026-08-13 and it says to re-measure; this is that measurement.
    **Closing condition:** a merged devrc PR after which `cairn recall --ref <name>` prints one
    entry, OR the footer and the `/resume` skill stop prescribing flags the wrapper lacks.
    forcing: none

## Gotchas / decisions / dead-ends

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

**⚠ A STORE ENTRY'S `OPEN:` BULLET WAS STALE IN THE WAY THE BADGE WARNS ABOUT.** `devrc/cairn`
carries `2026-08-29: OPEN: no entry in this store carries a task, PR or session ref, so nothing
joins an entry to the work that produced it`. Measured 2026-09-08: entries now carry
`[cairn: zach/<uuid>]` refs — several are visible in `cairn search` output. The remedy landed and
the bullet did not move, which is exactly the "a remedy that has since landed reads exactly like
one that has not" case the index badge names. Close it when next in that entry.

## How to verify

```bash
# ranks 10 + 14 landed — by CONTENT, never ancestry (a squash is never an ancestor)
gh pr view 5 -R ZacxDev/cairn --json state,mergedAt,mergeCommit    # MERGED, 9213726
git -C ~/workspace/cairn grep -n 'FLOOR = ' origin/main -- .github/workflows/ci.yml   # 1648
git -C ~/workspace/cairn grep -c ATOMICITY_MIN_RELOADS origin/main -- tests/test_subsystem_store_api.py

# the evidence rank 14 rested on — the repo's CI history at the time
gh run list -R ZacxDev/cairn --limit 100 --json conclusion,headSha,createdAt
# at the merge: 26 runs, 24 success / 2 failure; both failures e2cf6fe + 492191f, same assertion

# rank 3 is NOT done until this resolves into /nix/store (it does not today)
readlink -f ~/.local/bin/cairn                       # -> devrc/scripts/cairn
grep -c cairn ~/workspace/devrc/flake.nix            # 0 today

# rank 4 / rank 11 spot checks
gh pr view 1414 -R civitai/talos-infra --json state  # OPEN
ls ~/.cache/subsystem-store/ | tr '\n' ' '           # 23 scopes, no `cairn`
```
Expected: #5 MERGED at `9213726` with `FLOOR = 1648` and `ATOMICITY_MIN_RELOADS` present on
`origin/main`; `readlink -f ~/.local/bin/cairn` still resolving into
`~/workspace/devrc/scripts/cairn` (**not** `/nix/store` — that is rank 3's second half);
#1414 OPEN; no `cairn` scope.
