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
- **#1412, #1436, #1455 all MERGED** (squashes `f06b106f`, `4e26ec9a`, `c68750f6`), each
  verified by CONTENT rather than ancestry. **#1490 is OPEN** — the round-2 prose fixes for
  #1455 — `MERGEABLE`, 0 behind `origin/main`.
- **Both hosts converged and switched**, `ship.sh` → `converged + verified — 2 hosts compared,
  both at 0150d71f` (a genuine two-host agreement claim, not a one-host run). Each host:
  `VERIFIED — on branch main at origin/main (clean tree) + switched`, 0 dangling and 0 stale
  managed artifacts.
- **Rank 1 (runaway-menu) is DONE, and its premise was WRONG** — see the retired block below.
  The laptop's copy was a stale orphan, not unsaved work; it was deleted, not committed.
- **Rank 2 (round-2 delta audit of #1455) is DONE and produced #1490.** Five 🟡, no 🔴, and
  **four of the five were sentences the round-1 fix wrote about itself.** 🔴 **The ladder is
  NOT finished**: a round returning findings that needed fixing is followed by another round,
  so round 3 on #1490's delta is the stop condition.
- **Drift: rc=17, one item, pre-existing and not this arc's.** Both hosts `untracked: 0` and
  `clean — main == origin/main`. The remaining rc17 is `homelab-talos/containers/clawgate`
  being 1 behind `origin/trunk` — the `clawgatectl` build source. ⚠ It MOVED from the
  workbench to the laptop when the ship advanced the laptop while its homelab-talos checkout
  stayed put; it is the clawgate initiative's, not this one's.
- **No clawgate task.** `resolve` → rc 5, positive control green (9 links for another session).
  An unknown session id also answers 200 with an EMPTY ARRAY, so not a clean bill of health.
- **Claim held: `journald-26-11-migration-2`.** Release it when #1490 merges and the ladder
  terminates, or when the remainder is filed.

## Next steps (ranked)
1. **Merge #1490, then run ROUND 3 on its delta.** Merge **without `--delete-branch`** (see
   the gotcha) and delete the branch after the checks post. Round 3 is the ladder's stop
   condition — round 2 was not clean. If round 3 returns only prose findings with no 🔴, stop
   on the stated criterion and **write the reason into the summary**.
   forcing: gate — the ladder has not terminated and #1455's code is already on `main` with no
   CI verdict of its own.
2. **Migrate the laptop's journald config.** It carries
   `services.journald.extraConfig = "SyncIntervalSec=30s";` at `configuration.nix:370` (the
   ONE-LINE form) on `26.11pre1058091.ffb3c9b700e7`, so its next `nixos-rebuild` hits the same
   assertion the workbench hit. The rewriter handles that form and is unit-tested for it, but
   has NEVER been RUN on that host — different claims. ⚠ The `configuration.nix:370` reading
   is from 2026-09-08 and has NOT been re-verified. The laptop IS reachable
   (`zach@10.42.0.100`; the LAN address is same-network-only) and is now current with
   `origin/main`.
   forcing: none
3. **Give the shell half of `apply-journald-settings-migration.sh` automated coverage.**
   Five audit rounds of trap-message fixes rest entirely on reading; a throwaway harness built
   by an auditor caught a branch-ordering hazard in seconds and does not exist in the repo.
   forcing: none
4. **Fix the three deprecated-option warnings** from the 26.11 eval of
   `/etc/nixos/configuration.nix`: `services.dnsmasq.servers` → `.settings.server`,
   `services.gnome.tracker.enable` → `.tinysparql.enable`,
   `services.gnome.tracker-miners.enable` → `.localsearch.enable`. They still work today.
   forcing: none
5. **`diagnose-disk-accounting.sh`: the enumerator's `find` feeds no `DENIED_LOG`.** A
   top-level entry root cannot stat is dropped from section 2 with no tally, and section 3's
   residual absorbs it silently. Now recorded in the `_depth1_nul` site ledger as the FIFTH
   same-shape site. Not a regression — the glob it replaced did the same — so this is a debt,
   not a defect.
   forcing: none
6. **A top-level directory that is a MOUNTPOINT for another fs is counted as root-fs.**
   Flagged by round 2 as out of range and unaudited: `/boot` is emitted by the enumerator,
   then walked by `find "$d" -xdev`, whose `-xdev` anchors to *that* filesystem — so its
   inodes land in section 2's totals. Predates this arc and is unchanged by it.
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

- 🔴 **`gh pr merge --delete-branch` while checks are PENDING guarantees they ERROR.** Measured
  on #1455: merged `19:36:43Z`, Tekton started `19:40:14Z`, both legs
  `step clone failed (rc 128)`. The branch was gone before CI could clone it. The result is
  `ERROR`, not `FAILURE` — a broken gate, not a bad change — and it is indistinguishable at a
  glance from a real red. **Either wait for checks to post before merging, or merge without
  `--delete-branch` and delete the branch afterwards.**
- 🔴 **A mutation sweep is an instrument, and BOTH of mine were wrong before they were right.**
  (a) A mutant SURVIVED a fully green suite because my own assertion could not see it:
  `lacks "//"` passes on EMPTY output, so deleting `[ -n "$root" ] || root=/` — which makes
  `find ""` fail and emit nothing — was invisible. The fix was in the TEST: a positive control
  now asserts the root enumerates ≥5 entries first. (b) A battery row scored SURVIVED against a
  guard that was fine, because `-type d` placed after `-maxdepth 1` is INERT — in
  `\( … \) -prune -o -print0` a non-directory fails that test and falls through to `-o -print0`
  and is printed anyway. **Two sweeps of the same guard disagreed, and both times the
  instrument was at fault, not the code.**
- 🔴 **Retract a false rationale; do not replace it.** Two reasons given for excluding symlinks
  and non-regular entries were both measured false — `find` without `-L` does not descend a
  symlink (1 line vs 21953), and `-printf %b` is 0 for a fifo AND a symlink. Since section 3
  reconciles INODES and each is a real used inode, the exclusion made the residual WORSE. The
  type filter was deleted outright and both dead reasons are recorded in the source as
  retracted, because reaching for a third justification is what produces the next false one.
- 🔴 **Name WHICH NUMBER moved.** The first version of #1455 said "section 3's residual was
  short by 48 GiB". Section 3 is `INODES_USED - TOTAL_INODES` — a count of INODES; there is no
  byte residual at all (the TOTAL row prints an empty byte column). The omission cost section
  2's BYTE COLUMN 48 GiB and moved the residual by exactly ONE. As written it taught an
  operator to read a four-digit inode residual as gigabytes, in the one script whose section 3
  exists to stop that misreading. Corrected in `ae2c2427` and publicly on the PR.
- **The pre-merge full-suite ritual is RETIRED** (`CLAUDE.md`, changed 2026-09-09 mid-session):
  the two-tier run before every merge is deleted, replaced by a change-scoped subset plus
  reading CI. It was retired for exactly the contention this effort hit — 27–50 concurrent
  full-suite runs on one 24-core box, the dev-host tier repeatedly hitting its own 3600s cap
  and producing no verdict at all.
- **A loaded box is a reason to RAISE the gate's cap, not to conclude anything.** Two
  `gate.sh` runs died at `exit=124` / `RESULT: FAIL (exit=143)` — `Terminated`, not a test
  failure. The successful run took **3882s** against a 3600s default, so both earlier attempts
  died ~280s short of green. `--timeout SECS` / `DEVRC_GATE_TIMEOUT`, `0` disables.
- **Do not write a handoff into a worktree that has a gate running in it.** `/handoff` commits,
  and that mutated the tree mid-run, leaving the doc-reading content gates ambiguous for that
  run. Same family as the byte-offset trap above. Draft in the scratchpad; land it after.

- 🔴 **A "rescue this unsaved work" item can be exactly backwards — HASH IT FIRST.** Rank 1
  said to rescue a 145-line laptop-only `scripts/runaway-menu` into a PR. It hashed to
  `7ccb3d03`, **byte-identical to the blob added by wip commit `90ab4b61`**, which
  `e2b338f2` then deliberately DELETED when the pill was refactored to render syshealth's
  verdict instead of re-deriving it. `main` carries the post-refactor design. Committing it
  would have reverted that refactor — the same trap this arc already hit with the two nebula
  scripts. `git hash-object <file>` against the file's own history is what tells the two
  apart, and `drift-check`'s "in no commit and no backup" cannot: it is true of an orphan too.
  The delete was done with two guards that had to pass at the moment of acting (hash still
  matching, file still untracked) and after confirming recovery from
  `origin/zach/i3-runaways-bar-block`.
- 🔴 **`gh pr merge --delete-branch` while checks are PENDING guarantees they ERROR.**
  Measured on #1455: merged `19:36:43Z`, Tekton started `19:40:14Z`, both legs
  `step clone failed (rc 128)`. `ERROR` is not `FAILURE` — a broken gate, not a bad change —
  and it looks like a red at a glance. Merge without `--delete-branch` and delete afterwards.
- 🔴 **A DELTA round is REFUSED without a claims block, and posting one retroactively is the
  fix.** `audit-dispatch.py <pr> --round N` reads the prior round's fenced `audit-claims`
  block from the PR's ISSUE comments; with none it refuses rather than silently degrading the
  delta into a blind full audit that would then read as covered. Emit the skeleton with
  `--round <prev> --emit-claims --audited <the tip that round READ>`, fill it in, post it as
  an issue comment (a REVIEW comment is invisible to the script). ⚠ A MISSING INTERMEDIATE
  block does NOT refuse — it silently widens the range across two rounds' fixes, and says so
  only on stderr, once.
- 🔴 **The dispatcher refuses `..HEAD` when the checkout is not standing on the PR**, and that
  is load-bearing: this session's checkout was on `0150d71f` while the PR head was
  `ae2c2427`. It pinned the explicit sha range instead. Branch deletion does not make the shas
  unreachable — all three were still `git cat-file -t`-able after the branch was gone.
- 🔴 **WHEN A RATIONALE TURNS OUT FALSE, WRITE THAT THERE IS NONE.** Measured across this
  arc: the enumerator's exclusions got two rationales, both false; the sweep ledger then got a
  THIRD ("`|| true` would mask a short enumeration"), also false. The subsumption ledger got
  two drafts, both wrong. The ladder only stops regenerating them when the comment records the
  retractions and declines to supply a replacement.
- 🔴 **A sweep that fixes a claim must reach EVERY site, and the test file is a site.** Round
  1 corrected the false residual-units phrase at its two script sites; the test file carried
  the same claim in other words and was left, producing a comment that contradicted an
  assertion eleven lines below it.
- **"The laptop is unreachable" was FALSE and is retracted — it is reachable on nebula, and
  the LAN address is same-network-only by design.** A prior version of the ranked list said
  the laptop was unreachable on BOTH nebula (`10.42.0.100`) and LAN (`192.168.50.155`) at
  close on 2026-09-09. `drift-check.sh` reached it on 2026-09-10 and again on 2026-09-11 at
  the nebula address; `192.168.50.155` not answering is the documented same-network-only
  caveat, not an outage. Recorded here rather than in the ranked list because a status line
  gets replaced on the next update and this keeps being re-derived as an outage. **Every
  laptop fact in this doc came over nebula.**

## How to verify
```bash
# the three merged PRs, by CONTENT (a squash is never an ancestor)
for n in 1412 1436 1455; do gh pr view $n --repo innovation-upstream/devrc \
  --json number,state,mergeCommit --jq '"#\(.number) \(.state) \(.mergeCommit.oid)"'; done
git -C ~/workspace/devrc cat-file -e origin/main:scripts/diagnose-nix-disk.sh 2>/dev/null \
  && echo "STILL PRESENT — #1455 did not land" || echo "absent, as expected"

# the guards, on whatever main is now (expect 222 ok / 0 FAIL / rc 0)
nix develop ~/workspace/devrc -c bash ~/workspace/devrc/scripts/tests/test_diagnose_disk_accounting.sh

# #1455's CI is ERROR, not FAILURE — confirm before treating it as a red
gh pr checks 1455 --repo innovation-upstream/devrc

# the open round-2 PR
gh pr view 1490 --repo innovation-upstream/devrc --json state,mergeable,mergeStateStatus

# host + source drift (expect rc 17: the clawgate subtree only, now on the laptop)
bash ~/workspace/devrc/scripts/drift-check.sh

# the orphan is gone from the laptop, and is recoverable if that was wrong
ssh zach@10.42.0.100 'ls ~/workspace/devrc/scripts/runaway-menu 2>&1'
git -C ~/workspace/devrc show origin/zach/i3-runaways-bar-block:scripts/runaway-menu | head -5

# the migration is live on the workbench (three separate claims)
grep -A2 'services\.journald' /etc/nixos/configuration.nix
cat /etc/systemd/journald.conf                 # expect SystemMaxUse=2G
readlink -f /run/current-system                # expect nixos-system-nixos-26.11pre…
```
## Open investigations — live diagnosis state

### ✅ RETIRED 2026-09-10 (answered by #1455, squash `c68750f6`) — `scripts/diagnose-nix-disk.sh` is a SUPERSEDED script that was merged anyway
🔴 **CLOSED. The file is DELETED from `main`; do not act on the "Next probe" below.** The
delete-vs-keep question it poses was decided DELETE and executed. Kept verbatim because the
reasoning is the record of how it was decided, not because anything here is still open.
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

### ✅ RETIRED 2026-09-10 (moot — the file is deleted) — `scripts/diagnose-nix-disk.sh` has never been observed to finish; its runtime is unknown
🔴 **CLOSED, and the "Next probe" below is MOOT — do NOT run it.** It asks for a clean
end-to-end timing run on an idle box in order to replace the script's header paragraph with a
real number. There is no header left to correct: #1455 deleted the file. The durable half of
this block is the byte-offset trap (editing a script while a copy of it runs shifts the file
underneath the interpreter), which is recorded in the Gotchas section and in the
`diagnose-disk-accounting` index entry.
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

### ✅ RETIRED 2026-09-10 (ANSWERED: yes, at 3882s) — Can #1436's gate be run to completion on this box at all?
🔴 **CLOSED.** It can, and the leading hypothesis below was right: raising the cap was
sufficient. The run took **3882s** against the 3600s default, so both earlier attempts died
~280s short of a green finish. Both tiers passed, and Tekton later posted GREEN on both legs
independently. Kept for the load-vs-assertion reasoning; nothing here is open.
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

### #1455 merged with NO automated signal, and the audit ladder was stopped mid-flight
- **Symptom:** `gh pr checks 1455` reports both Tekton legs as
  `BROKEN GATE: … step clone failed (rc 128) before a verdict. Not a code failure.`
- **Observed (with values):** checks started `2026-09-09T19:40:14Z`; the merge landed
  `19:36:43Z` — **3m31s earlier**, with `--delete-branch`. Tekton had no branch left to clone.
  State is `ERROR`, not `FAILURE`.
- **Ruled out:** a defect in the change. The merged code passes on current `main` —
  `test_diagnose_disk_accounting.sh` → 222 ok, 0 FAIL, rc 0, re-run 2026-09-10. `via: command`
- **Also true, and separate:** round 1 of `/audit-pr 1455` returned **nine** findings, all
  addressed in `ae2c2427`. **Round 2 was never run** — the ladder was stopped by operator
  instruction, not by a clean round. So #1455 has neither CI nor a converged audit.
- **Next probe:** dispatch a blind delta re-audit of `c68750f6` against `e8551af6`, framed as
  *what was claimed fixed* and never *why it is correct*. Round 1's own findings were
  disproportionately about prose the previous commit wrote while explaining itself — three of
  nine — so that is where round 2's finding most likely sits.

### A 145-line script exists only on the laptop, in no commit and no backup
- **Symptom + exact repro:** `scripts/drift-check.sh` →
  `[laptop] untracked: 1 file(s) — present on this host only, in no commit and no backup:
  scripts/runaway-menu`.
- **Observed (with values):** `-rwxr-xr-x 1 zach users 3909 Sep 9 11:47`, 145 lines, python3.
  Docstring: *"Interactive fzf menu for the i3status-rust `runaways` block. Reads the cache file
  written by bar-status-poll … Left-click on the pill opens this menu; right-click opens
  syshealth in a float terminal."*
- **Ruled out:** that it is covered by the nix-read set — `untracked-in-nix-read-paths: 0 of 1
  … against 166 nix-read path(s)`, so nix does not read it and the flake is not shipping it.
  `via: command`
- **Leading hypothesis:** genuine unsaved work from the bar-pill effort, authored on the laptop
  and never committed. One routine `git checkout` on that host loses it unreported.
- **Next probe:** before committing it, check whether `main` already carries a successor — the
  bar work has moved since Sep 9. `git -C $DEVRC log --oneline --all -- scripts/runaway-menu`
  and `git grep -n runaway` on `origin/main`.

### The audit ladder on the disk-accounting change has not terminated
- **Where it stands:** round 1 on #1455 → 9 findings, fixed in `ae2c2427`. Round 2 on
  `e8551af6..ae2c2427` → 5 🟡 / 0 🔴, fixed in #1490 (`f0172420`). **Round 3 has not run.**
- **Observed (with values):** across both rounds, **7 of 14 findings were about prose the
  previous round wrote while explaining itself** — not logic. Round 2's headline: the test
  file said a top-level symlink *"must not"* be listed, **eleven lines above an assertion
  requiring that it is**, because round 1 swept the false phrase at its two SCRIPT sites and
  never looked in the TEST file, which carried the same claim in different words.
- **Ruled out:** that the code is wrong. Both retracted rationales and all four mutation
  claims were independently re-measured; #1490 changes no executable line (verified
  mechanically: no non-comment, non-`echo` line in the diff). `via: measurement`
- **Ruled out:** `|| true` on the enumerator's find as a masking hazard — MEASURED bash
  5.3.15, a `find` emitting 2 records then exiting non-zero is consumed identically with and
  without it (2 records, run continues, rc 0 both ways). Nothing reads the status, and masking
  needs a reader. This was the THIRD rationale supplied for that one function after two were
  retracted, and it was also false. `via: measurement`
- **Leading hypothesis:** the remaining defect surface is prose, not behaviour, and the
  `attribution gate` (two consecutive rounds changing zero PAYLOAD lines) cannot fire here
  because for a comment-only PR the `.md`-equivalent — the comment text — IS the payload.
  That is the documented non-terminating-ladder shape.
- **Next probe:** run round 3 on #1490's delta. If it returns findings that are again only
  prose with no 🔴 and no blast radius beyond "the document contains a false sentence", the
  stated-criterion stop applies — **and the reason must be written into the round's summary,
  or a report that ENDED on the escape hatch is indistinguishable from one that converged.**

### #1455 carries no CI verdict and never will
- **Symptom:** `gh pr checks 1455` → both legs `BROKEN GATE: … step clone failed (rc 128)
  before a verdict. Not a code failure.` State is `ERROR`, not `FAILURE`.
- **Observed (with values):** checks started `2026-09-09T19:40:14Z`; the merge landed
  `19:36:43Z` with `--delete-branch` — **3m31s earlier**. Tekton had no branch to clone.
- **Ruled out:** a defect in the change — the merged code passes on `main`, 222 assertions,
  0 failures, re-run 2026-09-11. `via: command`
- **Next probe:** none for #1455 (a merged PR's checks cannot be re-run without a fresh push).
  The forward fix is procedural and is in the Gotchas: do not pass `--delete-branch` while
  checks are pending.
