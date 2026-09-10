# Handoff: journald-26-11-migration — 2026-09-08

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
`nix-channel --update` moved the workbench to nixos-26.11, which removed
`services.journald.extraConfig` and broke `sudo nixos-rebuild switch` on a failed
assertion. Fix that, and land the ops scripts that were sitting untracked in the
workbench working tree where a `git checkout` would have deleted them unreported.

## State now
- 🔴 **#1412 MERGED** as squash `f06b106f` (2026-09-09). Verified by CONTENT, never by
  ancestry (a squash is never an ancestor of its base): all five files present in
  `origin/main`, the two payload files byte-identical to the branch head, and the final
  round-5 `SWITCH_ATTEMPTED` guard present — so the last revision landed, not an earlier one.
- **#1436 OPEN, head `a1a37280`, branch `chore/journald-followup-corrections`, worktree
  `~/workspace/devrc-followup`, `mergeable: MERGEABLE`.**
- 🔴 **The head MOVED this session, and the previous session's evidence does NOT cover it.**
  #1436 was **19 commits behind** `origin/main` at `807f98f0`. `origin/main` was merged in
  (clean, no conflicts) and pushed as `a1a37280`, so the branch is now **0 behind / 3 ahead**
  and the tree being gated IS the PR head. Those 19 commits added **~7,283 lines across 36
  files**, mostly new tests (`test_subsystem_store_api.py` +1246, `test_fans_detail.py` +694,
  `test_tmux_oom_protection_staged.py` +492, `test_handoff_index.py` +623). Any verdict taken
  against `807f98f0` — including the previous session's targeted 861-test run — is a claim
  about a tree that no longer exists.
- **Semantic-conflict check on the merge: done, negative.** The two files #1436 touches
  (`claudedocs/handoff-journald-26-11-migration.md`, `scripts/diagnose-nix-disk.sh`) are
  touched by **none** of the 19 commits (`git log HEAD..origin/main -- <the two paths>` →
  empty). The one plausible disjoint-file interaction was checked by hand rather than assumed:
  `main` modified `scripts/tests/test_runtime_shebangs.py` (+16) while our side modifies a
  `.sh` file — the change is **allowlist entries for `test_nebula_relay_apply.py` only**, and
  `diagnose-nix-disk.sh` carries the required `#!/usr/bin/env bash`. No interaction.
- **IN FLIGHT: `scripts/gate.sh --tier both` on the merged tree `a1a37280`**, background id
  `bca7cvlbp`, run log `<scratchpad>/gate-run.txt`, tier logs `<scratchpad>/gate-logs/`.
  Launched with **`--timeout 14400`** (4h) instead of the 3600s default. Invocation avoids a
  `cd` by using gate.sh's ROOT positional:
  `nix develop <worktree> --command <worktree>/scripts/gate.sh --tier both --timeout 14400 --log-dir <dir> <worktree>`.
- **The sandbox tier has NOT been run on this branch.** `nix build
  .#checks.x86_64-linux.pytests` and `…nodetests` are still pending, and must run **one at a
  time and after gate.sh finishes** — concurrent nested `nix` contends on the store and
  produces measured FALSE failures.
- **The journald migration is APPLIED and VERIFIED on the workbench** (unchanged): live
  `/etc/systemd/journald.conf` = `[Journal]` / `Audit=keep` / `SystemMaxUse=2G`;
  `/run/current-system` → `nixos-system-nixos-26.11pre1068949.dc5d91f84032`.
- **Claim held: `journald-26-11-migration-1`** (`claim-work`), subject "rank 1: gate both
  tiers on the MERGED tree for devrc#1436 once the box is quiet, then merge". **Release it**
  (`claim-work --release journald-26-11-migration-1`) when #1436 merges or the work is abandoned.
- **No clawgate task.** `clawgate_handoff.sh resolve` → **rc 5**, positive control green (8
  links for another session, so the board was genuinely read). An unknown session id also
  answers 200 with an EMPTY ARRAY, so this cannot distinguish "touched no task" from "wrong
  id" — not a clean bill of health, and no `clawgate-task:` field is recorded.

## Next steps (ranked)
1. **Read the running gate's verdict, then run the two sandbox derivations ONE AT A TIME, then
   merge #1436.** `<scratchpad>/gate-run.txt` (`GATE_RC=`) and `<scratchpad>/gate-logs/{pytest,node}.log`;
   then `nix build .#checks.x86_64-linux.pytests` and `…nodetests` separately — never in one
   invocation. Name the tier **and the base sha `a1a37280`** in the claim. On merge, release
   `claim-work --release journald-26-11-migration-1`. `IN FLIGHT: devrc#1436`.
   forcing: gate — #1436 carries a retraction of a false claim currently live on `main`
   (`diagnose-nix-disk.sh`'s header understates its runtime by ~an order of magnitude), and
   `main` is protected in name only, so nothing else blocks the merge.
2. **Decide whether `scripts/diagnose-nix-disk.sh` should exist at all — BEFORE measuring its
   runtime.** The index records it as superseded by `scripts/diagnose-disk-accounting.sh`,
   and that successor's header names it as the thing being corrected (its unprivileged `find`
   yields floors read as totals). Deleting it is the likely right answer; if it stays, its
   header must point at the successor. Only if it stays is a clean runtime run worth doing.
   forcing: none — but this INVERTS an earlier version of this list, which said to measure
   the runtime. Measuring a script that should be deleted is the wrong work.
3. **Migrate the laptop's journald config.** It carries
   `services.journald.extraConfig = "SyncIntervalSec=30s";` at `configuration.nix:370`
   (the ONE-LINE form) on `26.11pre1058091.ffb3c9b700e7`, so its next `nixos-rebuild` hits
   the same assertion the workbench hit. The rewriter handles that form and is unit-tested
   for it, but has NEVER been RUN on that host — different claims. ⚠ Measured 2026-09-08 by
   an audit subagent over ssh; the laptop was UNREACHABLE on both nebula (10.42.0.100) and
   LAN at close 2026-09-09, so this was not re-verified. Its `devrc` checkout must also be
   current or the script will not exist there.
   forcing: none
4. **Give the shell half of `apply-journald-settings-migration.sh` automated coverage.**
   Five audit rounds of trap-message fixes rest entirely on reading; a throwaway harness
   built by an auditor caught a branch-ordering hazard in seconds and does not exist in the
   repo. This is why that defect class recurred four times.
   forcing: none
5. **Fix the three deprecated-option warnings** surfaced by the 26.11 eval of
   `/etc/nixos/configuration.nix`: `services.dnsmasq.servers` → `.settings.server`,
   `services.gnome.tracker.enable` → `.tinysparql.enable`,
   `services.gnome.tracker-miners.enable` → `.localsearch.enable`. They still work today.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **`git ls-files` reads the INDEX, not `HEAD`.** Used to ask "is this file already on
  `main`?" *after* staging it, it answers "yes" for a file you just added. `git cat-file -e
  origin/main:<path>` is the question you meant. Cost one wrong conclusion this session.
- 🔴 **zsh ate a `git show ${ref}:<path>` measurement.** Written unbraced as
  `$ref:claudedocs/...`, the `:c` was consumed as a history modifier and the grep returned a
  confident `0` for BOTH refs — a well-formed, wrong answer with no error. Braced, the same
  command gives 1 hit at `4f49f5dc` and 0 at `03d7e0ad`. This is the documented trap in
  `claude/RULES.md` → "Shell & Tooling Gotchas"; it was read this session and hit anyway.
- **`gate.sh` from a bare shell fails `exit=3` with "required tool(s) missing: logrotate dash"
  and runs ZERO tests.** That is a missing environment, not a code failure — the worktree has
  no `.envrc`, and `.envrc` is `use opencode` which carries no pytest anyway. Run it as
  `nix develop <worktree> --command bash -c 'cd <worktree> && ./scripts/gate.sh --tier both'`.
- **A red gate on a fresh branch was NOT the branch.** `test_no_client_subdomain_literal_is_committed`
  failed on a client subdomain in `claudedocs/handoff-civitai-app-fleet.md:204`, a file this
  session never touched. Control on pristine `origin/main`: 18 passed. It existed at the branch
  point `4f49f5dc` and was fixed on `main` by #1405 — rebasing cleared it. Run the control before
  debugging your own diff.
- 🔴 **…and then this doc RE-COMMITTED that same literal while describing it.** Round 1 of
  `/audit-pr 1412` caught it: the bullet above originally quoted the hostname verbatim, which
  reds the very gate it is about. **Naming a banned literal in order to warn about it is still
  committing it** — `scripts/tests/test_no_client_hostnames.py` has an EMPTY allowlist, so there
  is no "but it's a quote" exemption. Describe the shape; never paste the value.
- **Decision: the two nebula scripts were dropped from the PR**, not committed. They were
  untracked in the workbench tree but byte-identical to blob `e7f2e45a` — an intermediate
  commit of the branch that merged as #1272 — while `main` already carried strictly
  further-along versions (480 vs 205 lines, 360 vs 317). Committing them would have reverted
  ~318 lines. `git hash-object <file>` against the file's own history is what proves a
  working-tree copy is a stale orphan rather than WIP.
- `output.txt` at the repo root is a truncated capture from the earlier disk dig. Left
  untracked deliberately; it is not part of #1412.

- **LAPTOP, the measured facts — kept HERE under an APPEND heading on purpose.** They lived
  only under `State now`/`Next steps`, which are REPLACED on every update, so the next
  edit would have silently dropped them (the write gate flagged exactly that). Measured
  2026-09-08 by an audit subagent over ssh: `/etc/nixos/configuration.nix:370` on the laptop
  reads `services.journald.extraConfig = "SyncIntervalSec=30s";` — the ONE-LINE
  double-quoted form, not a `''`-block — on `26.11pre1058091.ffb3c9b700e7`. That is what
  proved the first rewriter implementation would have refused on half the fleet. ⚠ At close
  on 2026-09-09 the laptop was UNREACHABLE on both nebula (10.42.0.100) and LAN
  (192.168.50.155), so none of it was re-verified; treat it as a reading with a date.
- 🔴 **THE INDEX ALREADY HAD THE ANSWER AND I READ IT LAST.** `cairn recall` — the command
  at the TOP of this very doc, whose caption says run it first — records
  `diagnose-nix-disk.sh` as a superseded predecessor, and a 2026-09-01 bullet on that entry
  already said *"a `find` over this host's `/tmp` takes over an hour, and a loop piping
  through `sort` prints nothing until it ends — so 'empty output' and 'dead job' are
  indistinguishable."* That is verbatim the lesson I re-derived over four hours of wall
  clock and a corrupted run. Reading the index costs one command. **Run it BEFORE the work,
  not at handoff time.**
- 🔴 **Five audit rounds never asked "should this file exist?"** — an audit scopes to the
  diff, and will never suggest deleting the thing under review. That question is the
  operator's or the index's, and it is cheapest before the first hardening round, not after
  five. (`~/.claude/.../do-we-need-it-before-hardening.md` says exactly this.)
- 🔴 **A merged claim was WRONG and is now retracted (#1436).** `diagnose-nix-disk.sh`'s
  header shipped "THIS TAKES TENS OF MINUTES" against a measured 4h05m-to-section-3. The
  run that would have caught it before merge was still running when the operator said
  "merge now" — so the false claim landed. The lesson is not "wait longer": it is that a
  claim whose verifying run has not returned is UNVERIFIED, and shipping it means shipping
  a claim, not a fact. Say which of the two you have.
- 🔴 **Do not EDIT a shell script while a copy of it is executing.** Bash reads by BYTE
  OFFSET, so an edit mid-run shifts the file underneath the interpreter and produces a
  syntax error on a line that is fine on disk. It cost a 4-hour measurement and looked
  exactly like a script defect. Copy it aside first and run the copy.
- 🔴 **Do not run an I/O-heavy job concurrently with the gate.** A `/nix` walk (~80M
  inodes) plus `gate.sh` starved BOTH: the diagnostic sat at 3% CPU for four hours, and the
  gate's pytest tier hit its 3600s wall twice. Distinguish load from a real failure by WALL
  TIME and by WHOSE time moved — node passed 1449/1449 throughout, and every pytest target
  that completed passed.
- **`git ls-files` reads the INDEX, not `HEAD`** — asked "is this file already on main?"
  *after* staging it, it answers yes for a file you just added. `git cat-file -e
  origin/main:<path>` is the question you meant.
- **`$?` after a pipe is the LAST command's status.** `cmd | head -2; echo $?` printed 0
  for a tool that had exited 1. Capture with `out=$(cmd 2>&1); rc=$?`.
- **zsh does not word-split — hit TWICE in one session, the second time after writing it
  down here.** `set -- $flags` with `flags="0 0 0 0"` sets `$1` to the whole string, so a
  five-state harness reported all five states identically. Use bash, a real array, or
  `${=var}`. Reading the gotcha did not prevent repeating it.
- **A `-f`-pattern process scan matches YOUR OWN shell.** An orphan sweep for `find /nix`
  returned exactly one "orphan": the scanning command itself. Resolve PIDs and confirm via
  `/proc/<pid>/cmdline`.
- 🔴 **Decision: the two gnome renames get NO `apply-*.sh` script.** Exact edit, lines
  361-362 of `/etc/nixos/configuration.nix`:
  ```nix
  services.gnome.tracker.enable = true;         ->  services.gnome.tinysparql.enable = true;
  services.gnome.tracker-miners.enable = true;  ->  services.gnome.localsearch.enable = true;
  ```
  They are warning-only (`mkRenamedOptionModule`). A second root-privileged `/etc/nixos`
  mutator would ship a COPY of a rebuild-and-rollback trap that took five audit rounds to
  get right — three of its four blockers introduced by the previous round's own fix.
  `claude/RULES.md` → "One rule, one place": a duplicated predicate is typically wrong at
  N−1 sites in the same direction. If a THIRD such change appears, extract the trap into a
  shared `nix/system/lib/` helper with its own tests FIRST. This is a judgement to
  re-make on the evidence, not a gap to fill.
- **The rename list was wrong once already**: it said three, but `services.dnsmasq.servers`
  is commented out at `configuration.nix:71`. It came from an eval warning true when
  captured — `/etc/nixos` changed underneath it — and a too-narrow first grep missed it.

- 🔴 **A gate red at `exit=124` / `RESULT: FAIL (exit=143)` is `Terminated`, NOT a verdict on
  the change.** It means the tier hit `gate.sh`'s wall-clock cap. Reporting it as a failing
  test sends the next session to debug a diff that was never evaluated. The cap is a supported
  knob — `--timeout SECS` or `DEVRC_GATE_TIMEOUT`, `0` disables — so a loaded box is a reason
  to **raise the cap**, not to conclude anything about the code.
- **Waiting for a quiet box is not a plan on this host, and that is now MEASURED, not assumed.**
  Load sat at a ~88 plateau with 12 other full suites running; the 15-min average was still
  climbing. Sample `/proc/loadavg` several times and read the 1-min against the 5-min before
  deciding a load figure is a spike worth waiting out.
- **`gate.sh` takes a ROOT positional**, so it can be run against a worktree with no `cd` —
  which matters because the Bash guard blocks `cd <path> && …`. Same for `--log-dir`, which
  keeps the tier logs somewhere you can still read after the run.
- **Decision: #1436 was brought up to date by MERGE, not rebase, and pushed.** Rationale: it
  makes the gated tree and the PR head the same object, so the two-tier claim is about the
  tree that actually merges rather than about a branch 19 commits behind it. The merge commit
  disappears in the squash anyway. Cost: the PR's own diff is unchanged (2 files), but its
  head is new, so Tekton re-runs.
- 🔴 **A `.sh` change plus a `main` that edited `test_runtime_shebangs.py` is exactly the
  "disjoint files are not safety" shape** — no shared file, and still a possible break, because
  one side can widen a scan while the other adds something for it to catch. It was clean here,
  but it was *checked*, not assumed; `git log HEAD..origin/main -- <paths>` answers only the
  textual half of that question.
- **`core.hooksPath` was re-measured immediately before the push** (empty, local and global)
  rather than trusted from earlier in the session — a pre-push hook that runs the suite inside
  the worktree would have collided with the gate running in that same worktree.

## How to verify
```bash
# the running gate's verdict — read the CONTENT, never an exit code alone
S=<scratchpad>
grep -E 'GATE_RC=|RESULT:|panic: test timed out' $S/gate-run.txt $S/gate-logs/*.log

# the sandbox tier Tekton gates on — ONE AT A TIME, never in one invocation
nix build ~/workspace/devrc-followup#checks.x86_64-linux.pytests
nix build ~/workspace/devrc-followup#checks.x86_64-linux.nodetests

# the gated tree is the PR head (expect the same sha on both sides)
git -C ~/workspace/devrc-followup rev-parse HEAD
gh pr view 1436 --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid

# the branch is not behind main (expect "0<TAB>3")
git -C ~/workspace/devrc-followup rev-list --left-right --count origin/main...HEAD

# the migration is live on this host (all three are separate claims)
grep -A2 'services\.journald' /etc/nixos/configuration.nix
cat /etc/systemd/journald.conf                 # expect SystemMaxUse=2G
readlink -f /run/current-system                # expect nixos-system-nixos-26.11pre…

# the rewriter's suite (37 tests, every audit-found edge case)
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_journald_migrate.py -q

# #1412 landed by CONTENT (a squash is never an ancestor — do NOT check ancestry)
git -C ~/workspace/devrc cat-file -e origin/main:scripts/lib/journald_migrate.py && echo present
```
## Open investigations — live diagnosis state

### 🔴 `scripts/diagnose-nix-disk.sh` is a SUPERSEDED script that was merged anyway
- **Found at close-out, from `cairn recall` — which this doc's own header tells you to run
  FIRST, and which I did not run until the end.** `devrc/diagnose-disk-accounting.md:14`
  records: *"`scripts/diagnose-nix-disk.sh` — its predecessor, superseded. Untracked in the
  working tree as of writing."* It is no longer untracked: #1412 committed it to `main`.
- **Verified against the repo, not taken on the index's word:**
  `scripts/diagnose-disk-accounting.sh` is **1016 lines** with `test_diagnose_disk_accounting.sh`
  and `mutants-diagnose-disk-accounting.sh`; `diagnose-nix-disk.sh` is 181 lines with none.
  The successor's header states WHY it exists: the predecessor's unprivileged
  `find "$d" 2>/dev/null` silently skips every root-only tree (`/root`, `/var/lib/docker`,
  `/var/lib/kubelet`, `/var/lib/private`, `/var/lib/rancher/k3s/storage`), so its inode and
  byte counts are **floors, not totals** — and the 2026-08-31 handoff read the resulting
  shortfall as "ext4 metadata overhead", which cannot be right because ext4 does not consume
  *used* inodes for metadata.
- **Ruled out:** "the two scripts are complementary siblings" — the successor's header names
  the predecessor by path and describes it as the thing being corrected.
  via: code
- **Leading hypothesis:** committing it was wrong. Its round-1 fix even ADDED a
  "(counts are a FLOOR as non-root)" caveat — re-deriving half the supersession reason
  without noticing the successor. Five audit rounds never asked "should this file exist?",
  because an audit scopes to the diff.
- **Next probe:** decide delete-vs-keep. If keeping, the header must point at
  `diagnose-disk-accounting.sh` and say when to prefer which. `git log --diff-filter=A --
  scripts/diagnose-disk-accounting.sh` dates the supersession.

### `scripts/diagnose-nix-disk.sh` has never been observed to finish; its runtime is unknown
- ⚠ SECONDARY to the block above — do not measure a script that may be deleted.
- **Symptom + exact repro:** `bash scripts/diagnose-nix-disk.sh` on the workbench. Sections
  3 and 4 each walk `/nix` recursively (section 4 also stats every regular file);
  `df -i /` reports ~80M inodes in use. Each section pipes through `sort`, which buffers,
  so nothing prints between the start of a walk and its end.
- **Observed (with values):** one run reached **section 3 of 10 in 4h05m wall clock**, at
  **3% CPU** (95s user + 452s system across four hours) while the test gate ran
  concurrently. It ended on `line 64: syntax error near unexpected token ')'`. A second
  attempt on a frozen copy was killed by me after ~20 min because it starved the gate.
- **Ruled out:** "the syntax error is a defect in the script" — `bash -n` on
  `origin/main`'s copy is clean and line 64 there is `df -hT /`. Bash reads a script by
  BYTE OFFSET as it executes, and the file was edited five times during those four hours,
  shifting underneath the running interpreter.
  via: command
- **Ruled out:** "4h05m is the runtime" — it is a FLOOR only. The process was I/O-starved
  at 3% CPU and did not stop of its own accord.
  via: measurement
- **Leading hypothesis:** the true runtime on an idle box is hours but unknown; the
  script's own closing `all 10 sections attempted` banner is untested end to end.
- **Next probe:** on a box at load < 5, with nothing else running and NO editing of the
  file during the run:
  `cp scripts/diagnose-nix-disk.sh /tmp/diag-frozen.sh && time bash /tmp/diag-frozen.sh > /tmp/diag.txt 2>&1; grep -c '^=== ' /tmp/diag.txt`
  Expect 10. Then replace the runtime paragraph in the script's header with the real number.

### Can #1436's gate be run to completion on this box at all?
- **Symptom + exact repro:** two prior `gate.sh --tier both` runs died at the **3600s default
  cap** — `exit=124`, `RESULT: FAIL (exit=143)`, i.e. `Terminated`, not a test failure.
- **Observed (with values):** load is a **sustained plateau, not a spike**. Sampled every 20s
  over two minutes: `86.70 / 97.43 / 94.76 / 93.00 / 92.34 / 87.02`, with 1-min ≈ 5-min ≈ 88
  and the 15-min average *climbing* 75.23 → 77.19. Box has **24 cores**. Concurrent workload
  measured at the same moment: **12** full-suite runs matching `pytest scripts/tests -q`,
  **92** `python3.12` processes, **131** processes matching `claude`. Node tier passed
  `1449/1449` under this load on **both** prior attempts; every pytest target that completed
  passed. So the failure is the wall-clock cap, not the change.
- **Ruled out:** "wait for the box to go quiet" as a viable plan — each of the 12 competing
  sessions is itself running a ~1h suite, so the plateau is self-sustaining and load never
  approached the ~5 the operator asked for. `via: measurement`
- **Ruled out:** the change itself as the cause of the red — node passed twice under identical
  load and no pytest target reported a failure. `via: measurement`
- **Leading hypothesis:** the suite simply takes longer than 3600s at ~3.4x contention
  slowdown, so raising the cap is sufficient. `DEVRC_GATE_TIMEOUT` / `--timeout SECS` is a
  supported knob (`0` disables); the current run uses 14400.
- **Next probe:** read `<scratchpad>/gate-run.txt` for the `GATE_RC=` line and the two
  `RESULT:` lines. 🔴 **Three outcomes are NOT "the tests failed"**: `exit=124` /
  `RESULT: FAIL (exit=143)` = hit the 4h cap (still could-not-gate, report as such, do not
  debug the diff); gate **exit 90** = status/content disagreement or a truncated run, meaning
  "read the log", not a verdict; a `panic: test timed out` line anywhere. Only a clean
  `RESULT: PASS (exit=0)` on both tiers is a verdict.
