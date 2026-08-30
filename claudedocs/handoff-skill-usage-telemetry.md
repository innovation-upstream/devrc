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
1. ✅ **DONE** — PR #1000 merged (`538370f5`) + `scripts/ship.sh`, both hosts at that sha.
   forcing: none  (complete — nothing left to work)
2. ✅ **DONE** — emitter verified live; see the closed investigation above.
   forcing: none  (complete — nothing left to work)
3. **Add the `adoption-scan` `via: "skill"` registry arm.** 🔴 **The blocker has largely
   cleared** — measured 2026-08-30, trailing 7d: **26 distinct identities fleet-wide** of
   35 devrc-managed skills, **both hosts reporting** (workbench 21, laptop 13), 0 rejects.
   When this was deferred it was 4 identities on one host, which would have reported 30 of
   34 as DEAD. Re-run the gate query below before starting; if it still reads ~26, the
   remaining DEAD verdicts are plausibly *true* and this is ready to build.
   Files: `scripts/session-analysis/adoption-scan.py`, `claude/skills/adoption-scan/SKILL.md`.
   forcing: none  — 🔴 the honest answer, not a formality. The INCIDENT that forced this
   effort (an investigation answering "was `signal` ever used?" with "never") is ALREADY
   CLOSED by #1000 + #1059: the interactive answer exists and both skills route to it.
   Nobody outside this loop has asked for the AGGREGATE view. Do not work it on the
   strength of its being written down here.
4. **Add the `attributionSkill` deadman.** Same gate as 3, now largely met. File:
   `scripts/validation/invariants.py`.
   forcing: none  — guards a HYPOTHETICAL silent zero (an upstream rename of an
   undocumented field). Nothing has regressed and no one has asked. Same caveat as 3.
5. **Work `claudedocs/followups-skill-usage-telemetry.md`** — **G4 is DONE and shipped**
   (#1059). Three remain: `audit-dispatch.py`'s wrong-toolchain brief; **the credential
   rotation, which is Zach's and is the highest-severity item in this thread** (an
   `activity_reader` password sits in cleartext in the opencode session store); and G5, the
   ClickHouse creds/query helper.
   forcing: security  — the credential rotation is a LIVE exposure (an `activity_reader`
   password in cleartext in the opencode session store) and is the ONLY item in this doc
   with a real external forcing function. It is Zach's to do. The other two items in that
   file are `forcing: none` on the same reasoning as 3 and 4.
6. ✅ **DONE** — claim `skill-usage-telemetry-1` released.
   forcing: none  (complete — nothing left to work)
7. **Structural guard for the reserved `RESULT:` grammar.** Nothing stops a future
   `SHELL_TESTS`/`HOOK_TESTS` entry printing `RESULT: PASS (exit=0)` at column 0, where
   `test_gate_exit_truthfulness.py`'s **first-match** regex reads it — a red run then
   reports green through the gate's own truth-telling channel. `scripts/tests/test_cleanup_disk_gate.sh`
   did exactly that until audit round 4. **Closes when** a scan of those sources for
   `^\s*echo "RESULT:` exists and has been watched red against a planted offender.
   Repo: `devrc`.
   forcing: regression  — MEASURED, not hypothetical: `test_cleanup_disk_gate.sh` forged
   that line and `test_gate_exit_truthfulness.py` read the forged PASS off a run whose own
   verdict was FAIL. Fixed in that one file by prose only; the next copy-paste repeats it.

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

## How to verify
```bash
# 1. the question that started this — keyword hits vs real uses
python3 ~/workspace/devrc/scripts/find-session.py signal --claude-only | head -1
python3 ~/workspace/devrc/scripts/find-session.py --skill signal --limit 20

# 2. the ranks 3/4 gate — re-run before building the adoption-scan arm
#    (expects BOTH hosts present; a one-host reading is not a fleet reading)
SELECT host, count() AS rows,
       uniqExact(arrayJoin(JSONExtractKeys(payload,'skills_used'))) AS ids
FROM activity.events
WHERE source='claude' AND kind='session-summary'
  AND ts > now() - INTERVAL 7 DAY AND JSONLength(payload,'skills_used') > 0
GROUP BY host

# 3. fleet parity — READ EVERY PER-HOST LINE, not the final verdict
bash ~/workspace/devrc/scripts/drift-check.sh

# 4. the rescued script's gate (green here, red under an APPLY=0 -> APPLY=1 mutation)
bash ~/workspace/devrc/scripts/tests/test_cleanup_disk_gate.sh
```
