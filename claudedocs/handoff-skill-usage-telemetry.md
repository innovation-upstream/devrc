# Handoff: skill-usage-telemetry — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make Claude Code **skill usage** measurable, because it was not: `adoption-scan` sees only
the 9 tools that emit through `invocation.py`, and skills emit nothing. An investigation
asked *"was the signal skill ever used operationally?"*, searched keywords, and answered
**"never"** — the reverse of the truth. This work ships the measurement AND removes the
doc claim that made the wrong answer look right.

## State now — 🟢 MERGED, SHIPPED, AND VERIFIED LIVE (2026-08-29)

- **PR #1000** (the measurement) merged `538370f5`; **#1053** (handoff) merged;
  **#1059** (G4 routing) merged `bc0809f6`; **#1057** (cleanup-disk rescue + gate)
  merged `f27c67b7`. All verified **by content** on `origin/main` — a squash never
  makes the branch head an ancestor, so `--is-ancestor` reports "not merged" and lies.
- **Both hosts at `f27c67b7`**, cross-host **compared** (not the one-host
  `NOT COMPARED` case). Laptop verified at the consumer: G4 routing lines present,
  `skills_used` tailer present, resolving to the **same `/nix/store` path** as the
  workbench — identical derivation, therefore identical bytes.
- **Emitter live on both hosts.** Trailing 7d: workbench 410 rows / 21 identities,
  laptop 103 rows / 13 identities, **26 distinct fleet-wide**, `unusable_skill_names`
  total **0** — nothing is being silently rejected.
- **Infra fix shipped:** `homelab-infra` trunk `686d6ff0` exempts the `tekton-ci`
  namespace from PodSecurity `baseline`. Without it every `devrc-ci` gate pod failed
  ADMISSION and both required checks posted `COULD NOT RUN`, so nothing could merge.

## Open investigations — live diagnosis state
### ✅ CLOSED — the emitter works, and the headline claim is now MEASURED
Ranked item 2 is closed. First two post-deploy rows, with their **positive control**
(a bare zero would not have been distinguishable from a query wired to nothing):

| ingested_at (UTC) | host | `skills_used` | `skills_invoked` |
|---|---|---|---|
| 21:31:30 | workbench | `{"audit-pr":8,"handoff":7,"subsystem-index":20}` | 1 each |
| 21:41:41 | workbench | `{"resume":72}` | `{"resume":1}` |

`unusable_skill_names` = **0** on both — nothing is being silently rejected. All post-deploy
rows *have* the field (`have_field=2`), so a future empty one reads as "no skills used",
never "field absent". Control: 16 `session-summary` rows in the 2h before the deploy, **0**
with `skills_used` — the forward-only boundary is visible in the data.

🔴 **The headline, now measured rather than asserted:** `find-session signal --claude-only`
returns **678** sessions; `find-session --skill signal` returns **6**. And **5 of those 6 are
`[claude-remote]` (the laptop)** — so the originating investigation was wrong *twice over*:
it matched TEXT instead of USE, and it searched ONE host. Either defect alone produces
"never used".

Spot-checked one match rather than trusting the count — `6fb90d0d` (vetr) is a **true
positive**: `attributionSkill: ['audit-pr','clawgate','handoff','signal']` and a `Skill`
tool_use with `input.skill == 'signal'`. Worth checking because its prompt text mentions a
"signal-send-path", which is vetr's own feature and exactly the false positive this feature
exists to avoid. It matched on use, not on that string.

### ✅ CLOSED — does `--skill` reach the laptop? (was blocked on ship, not on code)
**Resolved: yes.** The refusal message below no longer appears; laptop sessions come back
tagged `[claude-remote]`. The leading hypothesis was right — the probe was working as
designed and only needed the peer to carry the new code. Original diagnosis retained below.

<details><summary>original (pre-ship) diagnosis</summary>
- **Symptom + exact repro:** `find-session.py --skill signal` on the workbench.
- **Observed (verbatim):**
  `find-session: peer laptop (10.42.0.100): peer is running an older transcript_search with no --skill support (run ship.sh) — its Claude sessions are NOT in these results`
- **Ruled out:** the peer leg being broken. **Positive control:** the workbench holds
  **zero** vetr sessions, and
  `find-session.py "qa-coverage-and-device-access" --claude-only` returns
  `1. [2026-08-28 22:09] vetr (main) [claude-remote] · 6 hits` +
  `2. [2026-08-29 00:49] vetr (main) [claude-remote] · 3 hits` — both from the laptop.
  So SSH, discovery, merge and tagging all work; only the capability probe is refusing.
- **Leading hypothesis:** working as designed. The probe (`hasattr(ts, "canonical_skill_name")`)
  refuses rather than searching WITHOUT the filter, which would return every term match as
  though it used the skill.
- **Next probe:** after merge, `scripts/ship.sh`, then re-run
  `find-session.py --skill signal --limit 20` and expect the laptop's 5 signal sessions.

</details>

### 🔴 CI ran `sandbox = false`, making the "hermetic" tier IMPURE — fixed, but verify before trusting a red CI run again
- **Symptom:** every PR failed `tekton/devrc-pytests` with ~43 failures concentrated in
  `scripts/tests`, on trees that passed locally with `failed=0`.
- **Observed (verbatim):** derivation `ydzfas1zzmm446y07ivymknxmirdzavi-devrc-pytests.drv`
  produced `TOTAL collected=18695 passed=18692 skipped=2 failed=1` on the workbench and
  `collected=18695 passed=18650 skipped=2 failed=43` in the CI pod. **Identical
  derivation hash, different output** — impurity by definition.
- **Root cause:** `nix config show | grep '^sandbox '` → **`sandbox = false`** in the CI
  pod vs **`sandbox = true`** on the workbench. `patchShebangs` and the tier's whole
  hermeticity assumption only hold where the sandbox is on.
- **Ruled out:** load (wall-time discriminator — `scripts/tests` 570s CI vs 488s local,
  1.17×, and `dl-router`/`browser-bridge` were *faster* in CI); the diff (both PRs failed
  with identical counts); clean-vs-dirty tree (a clean clone produced the **same
  derivation hash**, so untracked files never entered the flake source).
- **Status: FIXED** — the pod now reports `sandbox = true` and #1069 merged green.
- **Next probe if it recurs:** `kubectl exec -n tekton-ci <gate-pod> -c step-pytests --
  sh -c 'nix config show | grep "^sandbox "'` before debugging any diff.

### CI concurrency — UNRESOLVED, and the hostPath change is the suspect
- **Symptom:** nine `devrc-ci` PipelineRuns wedged in `Running` (oldest >70 min), six
  gate pods `Pending` on `ExceededNodeResources`, queue growing not draining.
- **Observed:** all admitted pods landed on the same node `talos-xr6-r7p`; recent gate
  pods split **6 Completed / 6 Error**; 12 runs in one hour.
- **Leading hypothesis:** `homelab-infra` `6bec075e` replaced the gate's **RWO PVC** with
  a **hostPath** nix cache. An RWO PVC is mountable by one pod at a time and was silently
  **serialising** gate runs; hostPath removes that, so many runs now mount one cache dir
  concurrently. NOT proven — the node hypothesis was tested and **refuted** (pods still
  land on `xr6-r7p`).
- **Next probe:** let the queue drain fully, push once with nothing else running. A
  solitary run coming back green implicates concurrency.

### `dl-router` live-fixture flake — UNRESOLVED
- **Symptom:** `scripts/dl-router/tests/test_server.py::test_match_returns_the_contract_shape`
  failed CI on #1059, whose diff is **two markdown files**.
- **Ruled out (structurally):** the test is unreachable from that diff; it passes on clean
  `origin/main` and on the branch locally; #1057 (same base) passed it in CI minutes earlier.
- **Mechanism:** the test drives a `live` fixture that starts a server and POSTs to
  `/match` — a readiness race that CI load can lose.
- **Next probe:** harden the fixture's readiness wait. `RULES.md` says fix a flake rather
  than re-run it; re-running was the expedient call for a docs PR, not the right fix.

### `homelab-talos/containers/clawgate` built source is STALE on the workbench — rc 17
- **Observed (drift-check, verbatim):** `🔴 DRIFT — BUILT SOURCE
  homelab-talos/containers/clawgate is NOT current: 2 behind / 0 ahead of
  refs/remotes/origin/trunk`. Both source repos are also **DIRTY** (homelab-talos 9 paths,
  tmux-fuzzyclaw 4) and `nix/pkgs` builds from the working TREE, so those paths are in the
  binary.
- **Why it matters:** `clawgatectl`'s deployed binary is older than its version string
  suggests — the exact 2026-08-14 failure mode that shipped a `clawgatectl` with no
  `task status` that exited 0.
- **Fix (on that host):** `git -C ~/workspace/homelab-talos pull --ff-only` then a
  `home-manager switch`. Not done here — it is another repo and not this effort's.

### ✅ CLOSED — rank 7's premise, re-measured: the doc's framing overstated the blast radius
The handoff said a forged line is read "where `test_gate_exit_truthfulness.py`'s
first-match regex reads it — a red run then reports green through the gate's own
truth-telling channel." Measured, the production consumer was never exposed:

- **`gate.sh:186` was already correct**: `verdict="$(grep -aE '^RESULT: (PASS|FAIL)' "$log" | tail -1 || true)"` — LAST match, column-anchored, carrying a comment
  explaining the anchor. A forged line always *precedes* the EXIT-trap verdict, so
  `tail -1` never selects it.
- **The exposed reader was the TEST**: `test_gate_exit_truthfulness.py:290` used
  `re.search(r"^RESULT: (PASS|FAIL) \(exit=(\d+)\)$", proc.stdout, re.M)` — FIRST match,
  *and* it required the exit-carrying form.
- **The real vacuous-green path** (this is the one worth keeping): that test's regression
  claim is "the runner emits a verdict carrying its own exit code". Requiring
  `\(exit=\d+\)` for SELECTION means a regressed bare `RESULT: FAIL` — the exact shape
  `origin/main` emitted before #1057 — is **skipped**, and the search continues until it
  finds a forged exit-carrying line from a registry entry. The guard then certifies the
  deliverable against a line the runner never wrote. Fixed by selecting on the LOOSE
  grammar and *then* asserting the shape.
- **Why a registry entry can inject at all:** `run-tests.sh` inlines `HOOK_TESTS` /
  `SHELL_TESTS` stdout straight into its own stream — no capture, no prefixing — and
  `testlib/runner_patch.py:110-119` leaves both registries ALONE unless a caller names
  them, so `test_the_verdict_line_carries_the_exit_code` drives the REAL ones.

### 🔴 A LIVE near-miss nobody had recorded — `test_bash_guard.py` already emits the prefix at column 0
- **Observed (verbatim), by RUNNING the file, not reading it:**
  `nix develop … -c python3 scripts/claude-hooks/tests/test_bash_guard.py | tail -4` →
  a blank line then `RESULT: all good` at column 0.
- **Source:** `scripts/claude-hooks/tests/test_bash_guard.py:490` —
  `print("\nRESULT:", "all good" if not fail else f"{fail} failure(s)")`. It is a
  **HOOK_TESTS entry**, so that line lands in the runner's stream on every gate run.
- **Why it is benign TODAY:** the payload is never `PASS`/`FAIL`, so it cannot match the
  reserved `^RESULT: (PASS|FAIL)`. `gate.sh`'s own comment cites this exact line as the
  reason its grep is anchored and narrow.
- **Why it matters:** the obvious refactor to
  `print("RESULT:", "PASS" if not fail else "FAIL")` collides instantly. It is now pinned
  two-way in `NEAR_MISSES`, so that refactor fails twice — the pin goes stale AND the
  collision scan fires.

### 🔴 UNRESOLVED — the merged tree has not been gated, and `run-tests.sh` moved under this branch
- **Observed:** `origin/main` advanced `72c786c4` → `ebbe5eaa` mid-session. `git diff
  <base>..origin/main -- scripts/run-tests.sh` = 37 insertions / 18 deletions, all in the
  `scripts/tests` **floor** block and its comments.
- **Why it matters here:** this change ADDS 15 tests to `scripts/tests`, and #1065's own
  commit message records that the drift **ceiling** "FIRED ONLY ON THE MERGED TREE" —
  neither side over alone, the SUM crossed it.
- **Measured headroom (so this is probably fine, but it is not verified):** floor
  `scripts/tests|10269`, drift ceiling `max(60, floor/4)` = 2567 → ceiling 12836. #1065
  measured the merged tree at 10546; +15 ⇒ ~10561. Inside by a wide margin.
- **Ruled out:** a textual conflict in the registries — the upstream diff does not touch
  `HOOK_TESTS`/`SHELL_TESTS`, which is what this guard parses.
- **Next probe:** build the integration branch and gate it, then the sandbox tier:
  `git -C <wt> merge origin/main` → `nix develop ~/workspace/devrc --command bash scripts/gate.sh --tier both --set hermetic <wt>` → `nix build .#checks.x86_64-linux.pytests` (alone — a combined invocation produces false failures).

### ✅ CLOSED — the merged tree was gated, and the floor/ceiling worry did not materialise
Previous entry asked whether this branch's +15 tests could push `scripts/tests` past its
drift ceiling on the merged tree, since #1065's own commit records that the ceiling "FIRED
ONLY ON THE MERGED TREE". **Measured, not reasoned:** the merged tree collects
`19408` repo-wide against a summed floor of `18145`, `failed=0`, and the run printed no
floor-replacement line — which the gate emits only when a floor actually drifts. No
`TARGET_FLOORS` edit was needed. The merge itself was textually clean; the upstream
`run-tests.sh` diff touched only the floor block and its comments, never
`HOOK_TESTS`/`SHELL_TESTS`, which is what this guard parses.

### ✅ CLOSED — the near-miss is confirmed in PRODUCTION gate output, not just by running the file
The sandbox `pytests` log for this very branch contains, two lines apart:
```
devrc-pytests> RESULT: all good
devrc-pytests>   TOTAL collected=19408  passed=19406  skipped=2  failed=0  (floor: 18145)
devrc-pytests> RESULT: PASS (exit=0)
```
The first line is `scripts/claude-hooks/tests/test_bash_guard.py:490` emitting into the
gate's own stream, immediately ahead of the runner's real verdict. That is the hazard's
exact geometry, observed in a real gate run rather than a fixture: had its payload been
`PASS` instead of `all good`, a first-match reader would have taken it.

## 🔴 The one thing to read before doing items 3 and 4
🟢 **UPDATE 2026-08-30 — the blocker below has LARGELY CLEARED. Read this first; the
original text is kept underneath because its ARGUMENT is still the right one.**
Re-measured, trailing 7d: **26 distinct identities fleet-wide** against **35** devrc-managed
skills, with **both hosts reporting** (workbench 410 rows / 21 ids, laptop 103 / 13) and
`unusable_skill_names` totalling **0**. So the `via: "skill"` arm would now report ~9 as
DEAD rather than 30 of 34, and several of those are plausibly *true* DEADs — which is the
report working, not a permanently-red gate. The accumulation condition the section below
asks for is met: both hosts appear, which was the specific gap it named.
⚠ Re-run the query before building — this is a reading from one day, not a plateau proven
over several. And note ranks 3 and 4 now declare `forcing: none`: the blocker clearing
makes them *possible*, not *asked for*.

<details><summary>original (2026-08-29) argument — still correct about the shape</summary>

**Their literal closing condition is met, and the hazard it was written to protect against
is NOT yet cleared. Do not treat the non-zero as a green light.**

The followups doc says both close when the item-2 query "returns non-zero". It returns **2**.
But those 2 rows carry **4 distinct skill identities** (`audit-pr`, `handoff`,
`subsystem-index`, `resume`) against **34** devrc-managed skills
(`ls -d claude/skills/*/ | wc -l`, measured 2026-08-29 — the scope is devrc-managed only;
plugin-provided skills are not counted). `adoption-scan` raises a loud `DEAD` at zero uses,
so adding the `via: "skill"` arm against this corpus reports **30 of 34 as DEAD** — which is
precisely the permanently-red-gate outcome the deferral existed to prevent. The deadman has
the same shape.

The gate was written as a boundary check (`> 0`) when what it actually needs is
**accumulation**. Suggested replacement, to be pinned when the arm lands — proceed when a
trailing-7d window shows a plateauing distinct-identity count on both hosts, e.g.:
```sql
SELECT host, uniqExact(arrayJoin(JSONExtractKeys(payload,'skills_used'))) AS ids, count()
FROM activity.events
WHERE source='claude' AND kind='session-summary' AND ts > now() - INTERVAL 7 DAY
GROUP BY host
```
🔴 Note `host` — at hand-off **both rows are workbench**; the laptop had not yet ticked, so
a fleet-wide claim cannot be made from this data yet. Re-check both hosts appear.

</details>

## Next steps (ranked)
1. **Audit and merge #1119.** `/audit-pr 1119` first — it is a gate-integrity change, which
   is the class where a wrong fix is least visible. Then merge, `scripts/ship.sh`, and
   `claim-work --release skill-usage-telemetry-7`.
   forcing: regression — MEASURED: `test_cleanup_disk_gate.sh` forged
   `RESULT: PASS (exit=0)` and the truthfulness guard read the forged PASS off a run whose
   own verdict was FAIL. The guard is written and green but protects nothing until merged
   AND shipped.
2. **Rotate the leaked `activity_reader` credential.**
   forcing: security — a LIVE exposure, an `activity_reader` password in cleartext in the
   opencode session store. Zach's to do; the only item here with an EXTERNAL forcing
   function.
3. **Bring the laptop's `homelab-talos/containers/clawgate` built source current** —
   `git -C ~/workspace/homelab-talos pull --ff-only` then a `home-manager switch`, on the
   laptop. Measured by `drift-check.sh` this session: laptop rc 17, 1 behind; the workbench
   is CURRENT, so the two hosts build DIFFERENT source (`c919cd32c230` vs `11fde963e9e9`)
   under the same version string.
   forcing: regression — this is the 2026-08-14 failure mode's exact shape (a `clawgatectl`
   whose binary is older than its label). Not this effort's repo, which is why it is ranked
   below the two above.
4. **The `adoption-scan` `via: "skill"` registry arm.** Files:
   `scripts/session-analysis/adoption-scan.py`, `claude/skills/adoption-scan/SKILL.md`.
   forcing: none — unchanged. The incident that forced this effort is already closed by
   #1000 + #1059. Do not work it on the strength of being written down.
5. **The `attributionSkill` deadman.** File: `scripts/validation/invariants.py`.
   forcing: none — guards a hypothetical silent zero; nothing has regressed.
6. **`claudedocs/followups-skill-usage-telemetry.md`** — `audit-dispatch.py`'s
   wrong-toolchain brief (⚠ likely CLOSED by #1104, which merged this session — check
   before working it) and G5, the ClickHouse creds/query helper.
   forcing: none — same reasoning as 4 and 5.

## Gotchas / decisions / dead-ends
- 🔴 **`find-session`'s "both hosts" claim was HALF FALSE for weeks** and is the root cause of
  the original wrong answer. `opencode_search` went cross-host 2026-08-26; `transcript_search`
  never did. Fixed in `bd869dc5` by adding the leg, **not** by weakening the sentence.
- 🔴 **A guard measured only by what it REJECTS is not measured.** Twice: reverting one
  argparse default (`--skill default=None` → `""`) made the guard fire on every plain keyword
  search — `find-session signal` exit 2, zero output, tool's primary mode dead — with the
  **whole CLI suite green**. Negative controls added both times.
- 🔴 **A retraction in a commit message is not a retraction.** I "retracted" a wrong figure in
  a commit while three code sites still asserted it; the delta audit caught it. Re-measured, it
  was wrong about the **corpus** too (the shape lives only in `subagents/`, which neither
  reader walks).
- **Three routes, not two.** A `Skill` tool_use carrying `input.skill` was missed initially —
  undercounted `next-lever` by 87.5% (1 of 8). `input.args` beside it is operator free-text and
  is never kept.
- **The bound is duplicated on purpose**, pinned byte-identical by a test: `nix/home.nix`
  deploys `scripts/collector/claude` **alone** to the daemon's runtime path, so an import from
  `scripts/lib` would pass every test and break the running service on both hosts.
- **Path-derived prefixes are dropped** (`<path>:<skill>` → `<skill>`) to stop a per-run-unique
  filesystem path becoming an unbounded ClickHouse map key in a PUBLIC repo. Forward-looking:
  **0** namespace-qualified identities in the 837 session transcripts either reader walks.
- **`--skill` forces the archive leg.** #989's `--live` short-circuits the archive when it
  matches; without forcing, the skill filter would never run and live rows chosen on TERMS
  ALONE would print under a heading read as a skill answer. A clean `git merge` cannot see that.
- **`--skill` is exact on the CANONICAL form**, so `apps/api:deploy` and `apps/web:deploy` both
  match `deploy`. Deliberate; the identity measured is the skill, not where it loaded from.

- 🔴 **`skills_used` answered the question that started this: 678 keyword matches vs 6 real
  uses of the `signal` skill — and 5 of the 6 were on the LAPTOP.** The original "never
  used" verdict was wrong twice over: it matched TEXT instead of USE, and it searched ONE
  host. Either error alone produces it.
- 🔴 **Merged ≠ deployed, and it bit this very session.** After #1053/#1057/#1059 merged,
  `drift-check.sh` reported `[laptop] BEHIND origin/main by 5 commit(s)`. "G4 is closed" was
  true of `main` and the workbench and **false of the laptop** — the same half-the-fleet
  error the whole effort exists to fix. `ship.sh` after every merge, then verify at the
  consumer, not at `git log`.
- **The audit ladder on #1057 ran five rounds and every finding was in the PREVIOUS round's
  fix, never in the payload.** Notable ones, all worth knowing independently:
  a guard that printed `RESULT: PASS (exit=0)` and made a red run report green; a guard that
  shipped the exact `#!/usr/bin/env` defect `test_runtime_shebangs.py` exists to catch; and
  comments asserting coverage the code did not have (`"no real binaries are reachable"` was
  false — an absolute path bypasses PATH and a mutant really deleted a canary mid-run).
  **The ladder was stopped by the ATTRIBUTION GATE** — two consecutive rounds whose fixes
  changed zero payload lines — not by a clean round.
- 🔴 **Three instruments lied in one session, each in a documented way:** `| tail; echo $?`
  reported 0 over `RESULT: FAIL`; a stub harness logging to **stderr** was swallowed by the
  script's own `2>/dev/null` and reported a false negative; and `grep -c` prints `0` **and**
  exits 1, so `grep -c … || echo 0` emitted `0\n0` and every `-eq` blew up. Validate the
  instrument before reading its verdict.
- **`CDPATH` is set on this host**, so `cd` echoes its target and `$(cd … && pwd)` returns
  the path **twice**, newline-separated. Use `CDPATH= cd -- … >/dev/null`.
- **A restricted `PATH` does not stop an absolute-path command**, and a stub-based harness
  cannot see a shell redirection (`: > file`) at all. Both are named in
  `test_cleanup_disk_gate.sh`'s header rather than papered over.
- **Decision (Zach's, `9a09ad58`): the two mention drafts were DROPPED, not landed** — three
  of their load-bearing premises were measured false while implementing #1011, and
  `claudedocs/mention-detection-as-built.md` supersedes them. I had recommended the
  opposite (keep the docs, drop the script); the deciding question is not effort-spent but
  **what would be wrong on `main`**.
- ⚠ **The combined `nix build .#checks…pytests .#checks…nodetests` invocation used early in
  this session is now documented as producing FALSE failures** (CLAUDE.md, #1088). That run
  was **GREEN**, and a combined green is trustworthy — but run them one at a time from now on.

- 🔴 **`gate.sh` was right and the handoff's prose was wrong about which reader was
  exposed.** Worth knowing generally: the doc named the production channel; the defect was
  in the TEST that certifies it. Read the consumer before believing a hazard's stated blast
  radius — `grep -n 'RESULT' scripts/gate.sh` was the whole investigation.
- 🔴 **A selection predicate open-coded twice, with DIFFERENT semantics, was the bug** —
  `tail -1` in bash, `re.search` in Python. Consolidating them into
  `testlib/result_grammar.select_verdict` is what made the disagreement audible;
  `RULES.md` → "One rule, one place" as a bug-finding instrument, not hygiene.
- 🔴 **Selecting on the STRICTER grammar is what made the guard forgeable.** Requiring
  `\(exit=\d+\)` to SELECT means the regressed bare form is skipped rather than reported —
  the reader walks past the real line looking for a prettier one. Select loosely, assert
  the shape.
- **`xargs -0 command grep` exits 127** — `command` is a shell BUILTIN and xargs needs a
  real executable. Cost one confusing empty result that looked like "no matches" (the
  documented parsing trap, in a new shape). Use `/run/current-system/sw/bin/grep`.
- **The `| tail` trap fired again, exactly as documented:** `bash scripts/gate.sh … | tail -30; echo "GATE_RC=$?"` printed `GATE_RC=0` over a run whose own line said
  `GATE: RESULT=FAIL exit=1`. The verdict line is what survives; read it, never the rc.
- **`gate.sh` exit 3 is a MISSING ENVIRONMENT, not a code failure** — run it as
  `nix develop ~/workspace/devrc --command bash scripts/gate.sh …`. The pytest tier needs
  the flake's `gateTools` on PATH; `.envrc` is `use opencode` and does not provide them.
- **Mutation battery, all six watched red with this guard's own message and a green control
  after each restore** (`PYTHONDONTWRITEBYTECODE=1`, the stale-`.pyc` trap):
  M1 planted collision → `forges_a_verdict_line`; M2 unpinned near-miss →
  `prefix_emission_is_either_pinned`; M3 near-miss payload changed → BOTH ledger arms;
  M4 `tail -1`→`head -1` → `shell_reader_still_agrees`; M5 `hits[-1]`→`hits[0]` →
  `takes_the_last_line_not_the_first`; M6 runner → bare `RESULT: FAIL` →
  `carries_the_exit_code`.
- **No `clawgate-task:` recorded, deliberately.** `clawgate_handoff.sh resolve` exited 5
  (0 tasks for this session) with its positive control confirming the board is reachable.
  Per the skill that is NOT a clean bill of health — an unknown session id also answers 200
  with an empty array — so no field was written.

- **The base clone `~/workspace/devrc` was sitting on ANOTHER session's branch**
  (`docs/handoff-bb-resume-0830`), not `main`. The handoff was therefore landed with
  `--repo <worktree>` so it rode this branch instead. Check `branch --show-current` before
  letting any tool commit into the shared checkout — `handoff_doc.py` runs git from inside
  Python, so no PreToolUse hook would have caught it.
- **A duplicate-sweep zero was validated before being believed:** the title filter returned
  0 under test and **18** on a positive control using the same filter shape. A zero from an
  unvalidated filter is indistinguishable from a filter wired to nothing.
- **`nix build <worktree>#checks…` works** — a linked worktree resolves as a flake ref, and
  the derivation still builds from tracked files only, so newly `git add`ed files are
  included and untracked ones are not.

## How to verify
```bash
# 1. the guard, and the six mutations it was watched red against
nix develop ~/workspace/devrc -c python3 -m pytest \
  scripts/tests/test_result_grammar_is_reserved.py -q

# 2. the near-miss, in PRODUCTION gate output — grep the sandbox log, not the source
nix build .#checks.x86_64-linux.pytests --no-link -L 2>&1 | grep -B1 -A2 'RESULT: all good'

# 3. the two readers still agree — gate.sh must still say `tail -1`
grep -n 'verdict=' ~/workspace/devrc/scripts/gate.sh

# 4. after merge: it is not live on a host until a switch
scripts/ship.sh && bash ~/workspace/devrc/scripts/drift-check.sh
```
## State now — rank 7 BUILT and mutation-verified on a branch; gate IN FLIGHT (2026-08-30)

- **Ranks 1, 2, 6 remain DONE.** Reconciled this session by `resume-state.sh`: clean
  digest, no gap block, all 8 referenced PRs MERGED, both hosts converged.
- **Rank 7 is built, committed and pushed — NOT merged, NOT gated.**
  Branch `fix/result-grammar-scan` (devrc), based on `72c786c4`, 2 commits:
  - `8d25c726` — `scripts/testlib/result_grammar.py` (new) + `scripts/tests/test_result_grammar_is_reserved.py` (new) + `scripts/tests/test_gate_exit_truthfulness.py` (modified).
  - `fa257611` — names what the scan structurally cannot see.
- **Claim `skill-usage-telemetry-7` is HELD** (`claim-work --release skill-usage-telemetry-7` when done/abandoned).
- 🔴 **NOT VERIFIED YET.** The dev-host pytest tier was still running when this doc was
  written; the **sandbox tier (`nix build .#checks.x86_64-linux.pytests`) has NOT been run
  at all**, and that is the tier Tekton gates on. No PR opened.
- 🔴 **`origin/main` MOVED during the session** (`72c786c4` → `ebbe5eaa`; #1065, #1081,
  #1104 landed). One of them touches `scripts/run-tests.sh` — the file this guard PARSES.
  The merged tree has not been gated.
## State now — rank 7 SHIPPED to PR #1119, both tiers green on the MERGED tree (2026-08-30)

- **Ranks 1, 2, 6 remain DONE.** Reconciled by `resume-state.sh`: clean digest, no gap
  block, all 8 referenced PRs MERGED, both hosts converged.
- **Rank 7 is COMPLETE and IN REVIEW — `innovation-upstream/devrc#1119`**, branch
  `fix/result-grammar-scan`. Not merged; no audit run yet.
  - `scripts/testlib/result_grammar.py` (new) — the grammar + `select_verdict`.
  - `scripts/tests/test_result_grammar_is_reserved.py` (new) — the registry scan.
  - `scripts/tests/test_gate_exit_truthfulness.py` (modified) — both readers now share
    `select_verdict`.
- **Gated on the MERGED tree, all four runs green** (`origin/main` moved under the branch
  mid-session and touched `scripts/run-tests.sh`, so the merge is in — clean, no conflicts):
  - dev-host `gate.sh --tier both` → `GATE: RESULT=PASS exit=0`
  - sandbox `nix build .#checks…pytests` → `RESULT: PASS (exit=0)`,
    `collected=19408 passed=19406 skipped=2 failed=0 (floor: 18145)`
  - sandbox `nix build .#checks…nodetests` → `RESULT: PASS (exit=0)`,
    `tests=1420 pass=1420 fail=0 (floor: 1367)`
  - the two sandbox derivations were built ONE AT A TIME.
- **Claim `skill-usage-telemetry-7` is still HELD** — deliberately, so nobody re-does the
  work while #1119 is in review. Release it when #1119 merges.
- 🔴 **NOT deployed and NOT verified past the merge.** A merged PR changes nothing that
  nix manages: this guard only runs on a host after `scripts/ship.sh`.
