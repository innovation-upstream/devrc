# Handoff: cairn-oss-multi-instance — 2026-09-05

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

- **`ZacxDev/cairn` is PUBLIC** (2026-09-05) and now has **two** merged PRs: #1 the SIGHUP
  hot-reload, #2 the ledger narrowing (`c8aee7203`, 2026-09-06). Suite **1651 passed**,
  `leakscan.py` rc 0 with controls green. CI ran both jobs on #2 — leakscan 6s, tests 8m1s,
  `mergeStateStatus: CLEAN` — so **cairn's GitHub Actions gate is now a demonstrated
  instrument**, not an untested badge. That is worth knowing: a brand-new check in this
  ecosystem has been red-on-arrival before.
- **Session capture is DESIGNED, DECIDED and MERGED as a proposal, and BUILT NOWHERE.**
  `claudedocs/proposal-cairn-session-capture.md`, devrc `e16f9609a` (#1326). Sixteen operator
  decisions in its §2. Rank 8 carries the detail.
- **The opencode exporter SHIPPED** — devrc `f58d2df04` (#1338), clawgate #511 `complete`.
  `scripts/collector/opencode/export.py` + 32 tests. **It has NO CALLER**; it is a command,
  not a pipeline.
- **`ZacxDev/homelab-infra` #714 merged** (`ed2c4a0db`) and Flux-applied — verified a no-op
  (pod `…-59x6v` unchanged, `restarts=0`).
- Branch: devrc `main`, clean but for five pre-existing untracked files that are not mine
  (`nix/system/apply-nebula-relay.sh`, `check-nebula-relays.sh`, `output.txt`,
  `scripts/diagnose-nix-disk.sh`, `scripts/tmux-restore-observe.sh`).
- **All worktrees from this session are removed**; devrc and cairn base clones re-synced.
- 🔴 **STILL NOT DEPLOYED ANYWHERE — carried forward, and re-verified 2026-09-06.** No civitai
  instance exists; **no cairn image is published**; devrc does **NOT** consume cairn (`flake.nix`
  has zero cairn references, `scripts/cairn` is still an out-of-store symlink). The homelab pod
  still runs its own copy of the code. Everything above is source and design, not deployment.

🔴 **THE PROPOSAL'S AUDIT LADDER ENDED BY OPERATOR INSTRUCTION AT ROUND 9, NOT ON A CLEAN
ROUND — and round 9's own fixes were never audited.** Rounds 8 and 9 each found a real design
defect in the immediately preceding fix. The three unaudited prescriptions are §5.1's ledger
constraints, §8's control 8, and §5.3's cost correction. Treat those as the likeliest wrong
thing in the document; "merged after nine rounds" otherwise reads as "settled".

🔴 **`claim-work` WAS NOT USED FOR RANKS 1 AND 2** (it was used, correctly, for rank 5). The
ranked list is a shared queue with no lock and the claim must be taken BEFORE acting. Nothing
collided and there is no claim to release — but the protection was absent while 20 live claims
from other sessions showed the mechanism in active use around this work.

## Open investigations — live diagnosis state

### A full-suite-only intermittent in cairn's test suite, unattributed
- **Symptom + exact repro:** no reliable repro.
  `TestTheDeployedEntrypoint::test_a_TWO_LINE_token_file_authorises_BOTH_lines` failed
  **once in ~25 full-suite runs**, only ever in a full run.
- **Observed (with values):** first full run at PR head `90d30ab` → `1614 passed, 1 failed`.
  Same test then passed **3/3 alone**, **735/735 in its own file**, **5/5 paired with the
  new SIGHUP tests**, and the immediately following full run → `1615 passed, 0 failed`.
  Every subsequent full run (rounds 2-4, five more) was clean.
- **Ruled out:** that the SIGHUP work caused it — under `-p no:randomly` the new tests
  execute ~11,400 lines AFTER it in the file, the only PR change ordered before it is a
  `running()` → `serving()` extraction that this test does not use (it uses
  `running_subprocess`), plus an autouse signal-disposition fixture. via: measurement
- **Ruled out:** a load flake of the ordinary kind — wall time did not show the ~15×
  inflation that marks contention; the failing run was 531 s against a 412-522 s band.
  via: measurement
- **Leading hypothesis:** genuinely pre-existing and order/timing dependent, inherited from
  the origin repo rather than introduced here. Not confirmed.
- **Next probe:** run the full suite N times on a quiet box and get a RATE, e.g.
  `for i in $(seq 10); do cd ~/workspace/cairn && nix develop ~/workspace/devrc -c python3 -m pytest tests -q -p no:randomly 2>&1 | tail -1; done`
  A rate is what turns this into either "fix it" or "it does not exist".

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

### The full-suite intermittent in cairn — STILL UNRESOLVED, and the population moved under it
- **Symptom + exact repro:** no reliable repro.
  `TestTheDeployedEntrypoint::test_a_TWO_LINE_token_file_authorises_BOTH_lines` failed once in
  ~25 full-suite runs, only ever in a full run.
- **Observed (with values):** this session ran the full cairn suite once more on the rank-5
  branch — **1651 passed, 0 failed, 447.77s** — and CI ran it again on #2: **pass, 8m1s**. So
  the denominator is now ~27 runs with 1 failure, and neither of this session's runs
  reproduced it. via: measurement
- **Ruled out:** that the +4 tests from #2 perturb it — they are pure AST/collector helpers
  with no subprocess and no signal handling, and both post-#2 runs were clean. via: code
- **Leading hypothesis:** unchanged — genuinely pre-existing and order/timing dependent,
  inherited from the origin repo. Not confirmed.
- **Next probe:** unchanged and still the right one, now cheaper because CI runs it for free —
  `for i in $(seq 10); do cd ~/workspace/cairn && nix develop ~/workspace/devrc -c python3 -m pytest tests -q -p no:randomly 2>&1 | tail -1; done`
  ~75 min wall clock at 7.5 min/run. A RATE is what turns this into either "fix it" or "it
  does not exist".

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

## Next steps (ranked)

🔴 **Numbering is STABLE and is half a claim's identity** (`claim-work --slug-for <this doc>
<rank>`). Items are marked done IN PLACE; new items APPEND.

1. ✅ **DONE 2026-09-05 — `ZacxDev/cairn` IS PUBLIC.** Verified by the ACTUAL public path
   (anonymous API 200, anonymous raw `LICENSE` 200), never by the command's exit status.
   🔴 The pre-publication audit covered **12** commits, not the 7 on `main` — GitHub serves
   `refs/pull/1/head` on a public repo, so PR #1's five pre-squash commits publish too,
   including the states BEFORE the round-1 and round-2 credential-leak fixes. All 12 scanned
   rc 0 under the CURRENT `leakscan.py`.
   🔴 **The first sweep of that was WRONG and looked right** — it `cp`'d the scanner in before
   each checkout, so `git checkout` aborted on the dirtied file and four commits silently
   re-scanned one stale tree. Caught by printing a per-commit `git ls-files` count and noticing
   it did not move. **Any per-revision sweep must print a per-revision quantity that CHANGES.**
   ⚠ Residual, accepted: the 7 `main` commit messages carry `Claude-Session:` URLs;
   `licenseInfo` still reads null despite a stock MIT `LICENSE`.
   forcing: none — done

2. ⚠ **DONE 2026-09-05, BUT NOT AS WRITTEN — THIS ITEM'S OWN PREMISE WAS FALSE.**
   `ZacxDev/homelab-infra` **#714**, merged `ed2c4a0db`, Flux-applied and verified a no-op.
   🔴 "Cairn PR #1 makes that false" is a claim about cairn's SOURCE; the comment describes the
   DEPLOYED artifact, and nothing builds an image from cairn. Measured on the running pod:
   `grep -rl SIGHUP /app` → no matches, with `grep -rn "def load_tokens" /app` matching as the
   positive control. The image's `server.py` is byte-identical (`sha256 917936db…`) to devrc
   `origin/main`'s copy, which holds **0** occurrences of SIGHUP. Doing it as written would
   have told the next operator a secret edit takes effect on SIGHUP.
   **The transferable rule: "X makes Y false" must name WHICH ARTIFACT Y describes.**
   forcing: none — done

3. **Phase A3 — devrc consumes cairn as a pinned flake input.** `nix/home.nix` currently
   deploys `scripts/cairn` as an out-of-store symlink (edits are live, no switch). A flake
   input changes that: client edits will need a `home-manager switch`. Real ergonomic trade,
   decided deliberately, and `readlink -f` stays the only arbiter of which state a path is in.
   ⚠ **Re-verified 2026-09-06 as NOT started:** `flake.nix` contains **zero** cairn
   references and `scripts/cairn` is still an `mkOutOfStoreSymlink`.
   forcing: none

4. **Merge or close `civitai/talos-infra` #1414** (the instance proposal). It has four open
   questions in §11 — teammate count and identities, hostname, who else administers the token
   file, and whether the OSS repo accepts outside contributions from day one. None blocks A3.
   ⚠ **Re-verified 2026-09-06: still OPEN.**
   forcing: none

5. ✅ **DONE 2026-09-06 — `ZacxDev/cairn` #2, squash `c8aee7203`.** Both ledger 🟢s closed,
   each as its OWN docstring prescribed. Claimed via `claim-work` and released on merge.
   🔴 **Neither is regression coverage, and the commit says so.** Guard A's live dropped-count
   is **0**, so its assertion is an **INVARIANT GUARD, labelled one** — backed by a positive
   control feeding the collector a source that MUST yield three shapes, so the zero is not the
   reassuring kind. Guard B's narrowing is **behaviour-neutral today**: measured `checked=8`
   and every one of the 8 is `print`/`emit` by bare NAME, so the attr arm contributes zero
   matches; the only `.write` receivers in `server.py` are binary `fh.write` ×2 and
   `self.wfile.write`. What it prevents is a future module-level writer making the ledger
   demand `reload_safe` on BYTES.
   Two-way control on the narrowing: `sys.stdout.write` → 1, binary `fh.write` → 0, and
   `checked` still 8 on the real source. Mutants: reverting the narrowing (= the pre-fix code,
   i.e. red at base) and disabling the bare-re-raise arm each killed exactly their own test.
   forcing: none — done

6. **Get a RATE for the full-suite intermittent** (see the open investigation above), then
   either fix it or record that it does not reproduce. ⚠ The denominator moved this session:
   ~27 runs, 1 failure, two clean runs added (one local at 447.77s, one in CI at 8m1s).
   **~75 min wall clock**, and CI now runs the suite on every PR for free, so the cheapest
   version of this is to read the next N CI runs rather than burn a local hour.
   forcing: none

7. **Retire `deployment.yaml`'s no-reload paragraph IN THE SAME COMMIT that moves the store's
   `image:` tag to one built from cairn at or past `b25abb5`.** This is what rank 2 was
   reaching for, correctly sequenced: the comment is true until that tag moves and false the
   moment it does. The comment now states this trigger itself, so this item is a backstop, not
   the only thing holding it.
   **Closing condition:** a merged `ZacxDev/homelab-infra` PR in which the `image:` line and
   that paragraph change together — mechanical, checkable from the diff alone.
   ⚠ Blocked on there being a cairn-built image at all, which nothing schedules today; A3
   (rank 3) is the nearest thing that would force one.
   forcing: none — it cannot fire before the image exists

8. **Session capture — DESIGNED AND DECIDED, NOT BUILT.** Ship a session's transcript to
   object storage at handoff time and attach it to the cairn entries the session touched.
   **`claudedocs/proposal-cairn-session-capture.md`, merged 2026-09-06 as `e16f9609a`
   (#1326).** Sixteen operator decisions in §2, NOT to be re-litigated — a session ships as a
   SET of objects (subagent transcripts are 64% of the bytes), the pointer is an opaque id,
   retention is indefinite with **no retraction path** by policy, the entry carries a block
   list of ids with the digest on the object.
   🔴 **Read §10 first: four things are genuinely undecided**, led by *who READS* the recorded
   fan-out sets — a recorded set nothing compares against detects nothing.
   ⚠ **The audit ladder ended by operator instruction at round 9, NOT on a clean round**, and
   round 9's own fixes (§5.1's three ledger constraints, control 8, the §5.3 cost correction)
   were never audited. Likeliest wrong thing in the document.
   **Closing condition:** none yet — this is a design, and the first implementation PR is what
   would earn one. Do not treat "the proposal merged" as the work being done.
   forcing: none

9. ✅ **DONE 2026-09-06 — clawgate #511 `complete`, devrc `f58d2df04` (#1338).**
   `scripts/collector/opencode/export.py`, 32 tests, two audit rounds.
   🔴 **Pre-verification refuted the proposal's framing** — the opencode reader ALREADY EXISTS
   (`scripts/collector/opencode/_shared.py`), so the task was the DELTA and "do not add a
   second reader" became a tested criterion.
   🔴 **The task body's own ASSUMPTION was false and its stop condition is what caught it:**
   `text` is populated on 25% of parts and on **NONE** of the 378 tool parts sampled (0 of
   21,749 store-wide). An exporter built to its letter ships an artifact three-quarters empty
   with zero tool calls, while looking correct.
   🔴 **I MADE BOTH GATE TIERS RED AND FOUND IT BY TESTING INSTEAD OF GATING** — the suite
   collected 223 against a floor of 162, above the drift ceiling. `pytest <dir>` said "223
   passed"; the drift check lives ONLY in the runner. Floor set to **224**, copied verbatim
   from the gate's own output.
   ⚠ **The module has NO CALLER.** Wiring it in is rank 8's work.
   forcing: none — done

## Gotchas / decisions / dead-ends

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

## How to verify

```bash
# cairn: the suite and the security gate, both from a clean checkout
cd ~/workspace/cairn && nix develop ~/workspace/devrc -c python3 -m pytest tests -q -p no:randomly
cd ~/workspace/cairn && python3 tests/leakscan.py            # rc 0, controls green

# the opencode exporter, end to end on a real session (no content printed)
cd ~/workspace/devrc && nix develop . -c python3 - <<'PY'
import sys, hashlib; sys.path.insert(0,'scripts/collector/opencode')
import _shared as S, export as E
db=S.get_db(); sid=list(S.iter_sessions(db))[3]["id"]
a=E.export_session(db,sid); b=E.export_session(db,sid)
print("lines",len(a.splitlines()),"identical",
      hashlib.sha256(a.encode()).hexdigest()==hashlib.sha256(b.encode()).hexdigest(),
      "ascii",a.isascii())
PY

# the devrc gate — BOTH tiers, on the MERGED tree, never a test subset
nix develop ~/workspace/devrc -c bash scripts/gate.sh --tier both
nix build .#checks.x86_64-linux.pytests --no-link      # one at a time
nix build .#checks.x86_64-linux.nodetests --no-link
```
Expected: cairn `1651 passed`, leakscan `0 findings across 33 files`; the exporter identical
across runs and pure ASCII; `PASS scripts/collector/opencode/tests (collected=235 floor=224)`.
