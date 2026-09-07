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

- **`ZacxDev/cairn` is PUBLIC** with three merged PRs — #1 SIGHUP hot-reload, #2 the ledger
  narrowing (`c8aee7203`), #3 `8e4ef84` (the spawn-port TOCTOU + startup diagnostics).
- 🔴 **`ZacxDev/cairn` #4 IS OPEN — `feat/flake`, commit `0e6c7cb`. THE FIRST HALF OF RANK 3.**
  cairn had **no `flake.nix` at all**, so rank 3 as written could not start: there was nothing
  for devrc to pin. #4 adds `packages.cairn`, `packages.server-image` (Linux only),
  `apps.cairn`, `checks.client-resolves-its-lib`, a dev shell, and a `nix` CI job.
  Suite **1678 passed / 0 failed** (was 1667); leakscan **0 findings across 36 files**, both
  controls green. `mergeable=MERGEABLE`, `mergeStateStatus=UNSTABLE` (checks still running at
  write time). **NOT merged, NOT audited** — `/audit-pr 4` was offered and not yet run.
- ✅ **THE `nix` CI JOB PASSED ON ITS FIRST-EVER RUN** (PR #4, run `34160233941`), alongside
  `leakscan: pass`. Per the rules a brand-new check is not an instrument until it has passed
  once — `devrc-ci` was red on its first 5 of 5 runs for a reason unrelated to any diff — so
  this is that one pass, and no more: **one green run on one PR**, not a track record. If it
  goes red later, read the step log before believing the verdict.
- 🔴 **STILL NOT DEPLOYED ANYWHERE — carried forward, re-verified 2026-09-07.** No civitai
  instance exists, and **no cairn image is PUBLISHED** — #4 makes one buildable for the first
  time, which is not the same as one existing in a registry. The homelab pod still runs its own
  copy. Everything below is source, design and a PR, not deployment.
- **devrc consumes NOTHING yet.** `flake.nix` still has zero cairn references and
  `scripts/cairn` is still an `mkOutOfStoreSymlink`. Rank 3's stated closing condition is
  **not** met. devrc `main` is clean but for the same four pre-existing untracked files that
  are not mine (`nix/system/apply-nebula-relay.sh`, `check-nebula-relays.sh`, `output.txt`,
  `scripts/diagnose-nix-disk.sh`).
- **Session capture** remains DESIGNED, DECIDED, MERGED as a proposal, and BUILT NOWHERE
  (`claudedocs/proposal-cairn-session-capture.md`, `e16f9609a`). Rank 8.
- **The opencode exporter** shipped (`f58d2df04`, #1338) and still has **no caller**.
- Worktree `~/workspace/cairn-flake` (branch `feat/flake`) is **still present** — it holds the
  PR branch. Remove it after #4 merges: `git -C ~/workspace/cairn worktree remove
  ~/workspace/cairn-flake`, then re-sync the base clone with `merge --ff-only`.
- 🔴 **`claim-work` slug `cairn-oss-multi-instance-3` IS HELD by this session** and is NOT
  released — rank 3 is only half done. Release it when the devrc side lands, or steal it
  deliberately if you are picking rank 3 up fresh.

🔴 **NO `clawgate-task:` FIELD IS RECORDED, AND THAT IS NOT A CLEAN BILL OF HEALTH.**
`clawgate_handoff.sh resolve` exited **5** — 0 tasks for this session. Its positive control
answered 11 links for a different session, so the board is reachable and the token works; but a
WRONG session id also answers `200` with an empty array, so this cannot distinguish "this
session touched no task" from "the id is wrong". No task was created to fill the blank.

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

## Next steps (ranked)

🔴 Numbering is STABLE and is half a claim's identity (`claim-work --slug-for <this doc>
<rank>`). Items are marked done IN PLACE; new items APPEND.

1. ✅ **DONE 2026-09-05 — `ZacxDev/cairn` IS PUBLIC.** Verified by the ACTUAL public path
   (anonymous API 200, anonymous raw `LICENSE` 200). 🔴 The pre-publication audit covered
   **12** commits, not the 7 on `main` — GitHub serves `refs/pull/1/head` on a public repo.
   🔴 The first sweep of that was WRONG and looked right: it `cp`'d the scanner in before each
   checkout, so `git checkout` aborted and four commits re-scanned one stale tree. **Any
   per-revision sweep must print a per-revision quantity that CHANGES.**
   forcing: none — done

2. ⚠ **DONE 2026-09-05, BUT NOT AS WRITTEN — THIS ITEM'S OWN PREMISE WAS FALSE.**
   `ZacxDev/homelab-infra` **#714**, `ed2c4a0db`, Flux-applied, verified a no-op.
   **The transferable rule: "X makes Y false" must name WHICH ARTIFACT Y describes.**
   forcing: none — done

3. 🔨 **IN FLIGHT: `ZacxDev/cairn`#4 — Phase A3, devrc consumes cairn as a pinned flake input.**
   🔴 **THIS ITEM'S PREMISE WAS INCOMPLETE AND THE WORK SPLIT IN TWO.** cairn had **no
   `flake.nix`**, so there was nothing for devrc to pin; the first half is a PR to the PUBLIC
   repo, not to devrc.
   - **Half 1 — IN FLIGHT: `ZacxDev/cairn`#4** (`feat/flake`, `0e6c7cb`). Open, unaudited,
     unmerged. Worktree at `~/workspace/cairn-flake`.
   - **Half 2 — NOT STARTED: the devrc side.** Pin the input in `nix/../flake.nix`, move
     `~/.local/bin/cairn` into `/nix/store`, re-home `cairn who` as its own `cairn-who`
     binary, point the writer at the pinned `entry_shape`, and decide the fate of devrc's five
     duplicated `lib/` modules (see the fork investigation above — **put that fork to the
     operator before building**).
   🔴 **The ergonomic trade is real and unchanged:** `nix/home.nix:1474` deploys `scripts/cairn`
   as an `mkOutOfStoreSymlink` *deliberately* — its comment at `:1467` says it is REQUIRED, not
   preferred, because `.resolve()` must land beside `lib/`. **#4 solves exactly that** by
   installing script and `lib/` together under `libexec`. But client edits will then need a
   `home-manager switch`, and `readlink -f` stays the only arbiter of which state a path is in.
   **Closing condition (unchanged):** a merged devrc PR in which `flake.nix` names cairn as an
   input and `readlink -f ~/.local/bin/cairn` resolves into `/nix/store`.
   ⚠ Note the original condition said `~/.claude/…/cairn`; the actual deploy path is
   `~/.local/bin/cairn`.
   forcing: none

4. **Merge or close `civitai/talos-infra` #1414** (the instance proposal). Four open questions
   in §11 — teammate count and identities, hostname, who else administers the token file, and
   whether the OSS repo accepts outside contributions from day one. None blocks A3.
   ⚠ **Not re-verified this session** — last checked 2026-09-06, still OPEN then.
   forcing: none

5. ✅ **DONE 2026-09-06 — `ZacxDev/cairn` #2, squash `c8aee7203`.** Both ledger 🟢s closed.
   🔴 **Neither is regression coverage, and the commit says so** — guard A's live dropped-count
   is 0 (an INVARIANT GUARD, labelled one, backed by a positive control); guard B's narrowing
   is behaviour-neutral today.
   forcing: none — done

6. ✅ **DONE AND MERGED 2026-09-07 — `ZacxDev/cairn` #3, squash `8e4ef84`.** Rate measured, did
   NOT reproduce; **not "fixed"**, and the PR says so. Claimed and released via `claim-work`.
   Verified by CONTENT (11 new symbols present in `origin/main`), never by ancestry.
   ⚠ **Nine audit rounds** — every one found something real, but none of the last four found a
   defect in what the PR ships. Ended on the stated escape-hatch criterion, rationale posted
   on the PR.
   forcing: none — done

7. **Retire `deployment.yaml`'s no-reload paragraph IN THE SAME COMMIT that moves the store's
   `image:` tag to one built from cairn at or past `b25abb5`.** The comment is true until that
   tag moves and false the moment it does; the comment now states this trigger itself.
   **Closing condition:** a merged `ZacxDev/homelab-infra` PR in which the `image:` line and
   that paragraph change together — checkable from the diff alone.
   🔴 **THE BLOCKER IS NARROWER THAN RECORDED, AND #4 ADDRESSES IT.** This said "blocked on
   there being a cairn-built image at all, which nothing schedules today". The concrete reason
   was that **`build-push.sh` was never extracted** — `server/Dockerfile`'s comment points at a
   file that does not exist in the OSS repo, so cairn could not build its own image by any
   route. #4 adds `packages.server-image`, which was **built, loaded and RUN** (see gotchas).
   ⚠ Still blocked until #4 merges AND somebody publishes a tag to a registry — #4 produces a
   loadable tarball, it does not push anything anywhere.
   forcing: none — it cannot fire before a published image exists

8. **Session capture — DESIGNED AND DECIDED, NOT BUILT.** Ship a session's transcript to object
   storage at handoff time and attach it to the cairn entries the session touched.
   `claudedocs/proposal-cairn-session-capture.md`, `e16f9609a` (#1326). Sixteen operator
   decisions in §2, NOT to be re-litigated.
   🔴 **Read §10 first: four things are genuinely undecided**, led by *who READS* the recorded
   fan-out sets — a recorded set nothing compares against detects nothing.
   ⚠ Its audit ladder ended by operator instruction at round 9, NOT on a clean round; round 9's
   own fixes were never audited.
   **Closing condition:** none yet — the first implementation PR is what would earn one. Do not
   treat "the proposal merged" as the work being done.
   forcing: none

9. ✅ **DONE 2026-09-06 — clawgate #511 `complete`, devrc `f58d2df04` (#1338).**
   `scripts/collector/opencode/export.py`, 32 tests, two audit rounds.
   🔴 The task body's own ASSUMPTION was false and its stop condition caught it: `text` is
   populated on **0 of 21,749** tool parts store-wide.
   ⚠ **The module has NO CALLER.** Wiring it in is rank 8's work.
   forcing: none — done

10. **Raise cairn's CI collected-test floor.** `.github/workflows/ci.yml` pins `FLOOR = 200`
    against a suite that now collects **1678** (was 1667; #4 adds 11) — it cannot see a suite
    that silently narrows to 300, the exact failure its own comment says it prevents. devrc's
    convention for the replacement number is `m - min(50, max(1, m/20))`.
    ⚠ **Rebase on #4 before computing it** — the number moved this session and will move again.
    **Closing condition:** a merged `ZacxDev/cairn` PR moving that literal — checkable from
    the diff alone.
    forcing: none

11. 🔴 **OPERATOR ACTION — add a `cairn` scope to the store token's allowlist.** Work in
    `~/workspace/cairn` cannot be recorded in the subsystem store at all today: `cairn create
    --scope cairn` is refused `[not-found]`, exit 6. **Measured, not inferred** — the token
    allowlist holds 23 scopes and `cairn` is not among them:
    `KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store exec deploy/subsystem-store-api --
    cut -d' ' -f2,3 /run/secrets/subsystem-store/token` (fields 2,3 only; field 1 is the
    secret). This session's cairn-repo lesson was routed to the `devrc/cairn` entry instead,
    which works but files repo-specific knowledge under the wrong scope.
    ⚠ Adding a scope is an edit to the pod's token file — an operator act, not a client one.
    **Closing condition:** `cairn create --scope cairn --ref <slug> --file <f>` exits 0.
    forcing: none

12. **Derive `leakscan.py`'s file coverage instead of enumerating it.** #4 closed the
    immediate hole (`.nix`/`.lock` added to `TEXT_SUFFIXES`) but not the class: coverage is
    still a hand-written suffix list, so the NEXT new file type in this public repo is
    unscanned while the run prints a confident `0 findings`. The fix is to derive the scanned
    set from `git ls-files` and refuse — or at minimum report — a tracked file the scan did
    not read.
    **Closing condition:** a merged `ZacxDev/cairn` PR in which a tracked, non-binary file
    that the scan skips causes a non-zero exit or an explicit `SKIPPED` line naming it.
    forcing: security — the repo is public and the gate is the reason it can be

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

## How to verify

```bash
# cairn PR #4 — the flake, on the PR branch
git -C ~/workspace/cairn-flake log --oneline -1          # 0e6c7cb
cd ~/workspace/cairn-flake && nix build .#packages.x86_64-linux.cairn --no-link
cd ~/workspace/cairn-flake && nix build .#checks.x86_64-linux.client-resolves-its-lib --no-link
cd ~/workspace/cairn-flake && nix build .#packages.x86_64-linux.server-image --no-link
# ^ ONE AT A TIME. A combined invocation contends on the store and can report a FALSE red.

# the packaged client resolves lib/ from /nix/store — the mechanism, not --help
P=$(cd ~/workspace/cairn-flake && nix build .#cairn --no-link --print-out-paths)
HOME=$(mktemp -d) $P/bin/cairn doctor | head -3     # a real report; exit 9 here is CORRECT

# the suite and the leak gate
cd ~/workspace/cairn-flake && nix develop ~/workspace/devrc -c python3 -m pytest tests -q -p no:randomly
cd ~/workspace/cairn-flake && python3 tests/leakscan.py --self-test && python3 tests/leakscan.py

# CI — read the nix job's step log, not just the verdict; it had never run before #4
gh pr checks 4 -R ZacxDev/cairn
gh pr view 4 -R ZacxDev/cairn --json mergeable,mergeStateStatus

# rank 3 is NOT done until this resolves into /nix/store (it does not today)
readlink -f ~/.local/bin/cairn
grep -c cairn ~/workspace/devrc/flake.nix            # 0 today
```
Expected today: cairn **1678 passed**, leakscan `0 findings across 36 files` with both controls
green, all three nix outputs build, `readlink -f ~/.local/bin/cairn` resolves into
`~/workspace/devrc/scripts/cairn` (**not** `/nix/store` — that is rank 3's second half).
