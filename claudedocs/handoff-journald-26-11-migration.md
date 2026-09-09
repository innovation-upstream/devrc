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
- **#1436 OPEN, head `b3f8bcc3`, branch `chore/journald-followup-corrections`, worktree
  `~/workspace/devrc-followup`** — the follow-up corrections. 🔴 **Do NOT merge it on the
  previous session's say-so: its gate is RED and could not be cleared.** See the ranked list.
- **The journald migration is APPLIED and VERIFIED on the workbench** (unchanged): live
  `/etc/systemd/journald.conf` = `[Journal]` / `Audit=keep` / `SystemMaxUse=2G`;
  `/run/current-system` → `nixos-system-nixos-26.11pre1068949.dc5d91f84032`.
- **#1412's gate: BOTH TIERS PASS** on the merged tree `53d8b962` (branch merged with
  `origin/main` at `efa9a0fc`) — the tree that became the squash. Dev-host: pytest
  `collected=21164 passed=21162 skipped=2 failed=0` (floor 20342), node `1449/1449`.
  Sandbox `nix build .#checks.x86_64-linux.{pytests,nodetests}` ONE AT A TIME: both
  `RESULT: PASS (exit=0)`, 0 timeout panics.
- **#1436's gate: RED, on LOAD, not on the change.** Two consecutive `gate.sh --tier both`
  runs hit the 3600s timeout and were `Terminated` (`exit=124`, `RESULT: FAIL (exit=143)`).
  Evidence it is load: node `1449/1449 PASS` on both; every pytest target that completed
  passed; load average **72 → 92** across the two runs with 257 concurrent `python3.12`
  and 54 other Claude session wrappers on the box. Targeted substitute run: **861 passed,
  0 failed** across every test in `scripts/tests/` that reads either path the diff touches
  (found by `grep -rl`, not hand-picked). That is a NARROWER claim than "the gate passed".
- **Audit: 5 rounds on #1412, `/audit-pr`.** Four blockers, THREE introduced by the
  previous round's own fix. Ladder stopped on the prose-payload criterion (payload trend
  489 → 148 → 90 → 42), reasoning recorded in commit `4bd146a3`.
- **No clawgate task.** `clawgate_handoff.sh resolve` → rc 5. An unknown session id answers
  200 with an EMPTY ARRAY, so this cannot distinguish "touched no task" from "wrong id" —
  not a clean bill of health, and no `clawgate-task:` field is recorded.
- No claims held (`claim-work`).

## Next steps (ranked)
1. **Run `scripts/gate.sh --tier both` on a QUIET box, then `nix build
   .#checks.x86_64-linux.pytests` and `…nodetests` ONE AT A TIME, then merge #1436.**
   Check `cat /proc/loadavg` first — both prior attempts died at load 72-92 from other
   sessions. `IN FLIGHT: devrc#1436`.
   forcing: gate — #1436 carries a retraction of a false claim currently live on `main`
   (`diagnose-nix-disk.sh`'s header understates its runtime by ~an order of magnitude),
   and nothing else blocks the merge since `main` is protected in name only.
2. **Decide whether `scripts/diagnose-nix-disk.sh` should exist at all — BEFORE measuring
   its runtime.** The index records it as superseded by `scripts/diagnose-disk-accounting.sh`,
   and that successor's header names it as the thing being corrected (its unprivileged `find`
   yields floors read as totals). Deleting it is the likely right answer; if it stays, its
   header must point at the successor. Only if it stays is a clean runtime run worth doing.
   forcing: none — but this INVERTS the previous version of this list, which said to measure
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
5. **Measure the `sudo $HOME` question** — `sudo printenv HOME` on the workbench. Round 4's
   NEW-3 fix (resolving section 8's home from `SUDO_USER`) is inference from `sudoers(5)`
   plus nixpkgs not building sudo with `--with-always-set-home`, NOT a measurement. The fix
   is correct either way; only the justification is unverified.
   forcing: none
6. **The two live gnome renames** at `/etc/nixos/configuration.nix:361-362`, exact edit in
   the Gotchas section. Deliberately NOT scripted — see that entry before reversing it.
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

## How to verify
```bash
# the migration is live on the workbench (three separate claims)
grep -A3 'services\.journald' /etc/nixos/configuration.nix
cat /etc/systemd/journald.conf                 # expect SystemMaxUse=2G and Audit=keep
readlink -f /run/current-system                # expect nixos-system-nixos-26.11pre…

# the rewriter's suite (37 tests, every audit-found edge case)
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_journald_migrate.py -q

# #1412 landed by CONTENT (a squash is never an ancestor — do not check ancestry)
git -C ~/workspace/devrc cat-file -e origin/main:scripts/lib/journald_migrate.py && echo present

# before merging #1436 — check the box is quiet FIRST, both prior attempts died on load
cat /proc/loadavg
nix develop ~/workspace/devrc-followup -c bash -c \
  'cd ~/workspace/devrc-followup && ./scripts/gate.sh --tier both'
nix build ~/workspace/devrc-followup#checks.x86_64-linux.pytests --no-link -L
nix build ~/workspace/devrc-followup#checks.x86_64-linux.nodetests --no-link -L
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
