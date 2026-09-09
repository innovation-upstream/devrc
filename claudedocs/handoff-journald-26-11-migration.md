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
- 🔴 **DONE — #1412 MERGED** as squash `f06b106f` (2026-09-09). Verified by CONTENT, never
  by ancestry (a squash is never an ancestor of its base): all five files present in
  `origin/main`, the two payload files byte-identical to the branch head, and the final
  round-5 `SWITCH_ATTEMPTED` guard present — so the last revision landed, not an earlier one.
- **PR #1412 (MERGED)** — https://github.com/innovation-upstream/devrc/pull/1412. Five files, all
  new, none modifying anything that existed on `main` (checked with
  `git cat-file -e origin/main:<path>` — NOT `git ls-files`, see the gotcha below): the two ops scripts
  `nix/system/apply-journald-settings-migration.sh` and `scripts/diagnose-nix-disk.sh`, the
  extracted rewriter `scripts/lib/journald_migrate.py` and its suite
  `scripts/tests/test_journald_migrate.py` (37 tests), and this doc.
- **The journald migration is APPLIED and VERIFIED on the workbench** — separate claims, both made:
  - `/etc/nixos/configuration.nix:745` now reads `services.journald.settings.Journal = { SystemMaxUse = "2G"; };`
  - live `/etc/systemd/journald.conf` = `[Journal]` / `Audit=keep` / `SystemMaxUse=2G`
    (`Audit=keep` is 26.11's new explicit default, not something this change set)
  - `/run/current-system` → `nixos-system-nixos-26.11pre1068949.dc5d91f84032`
  - The **laptop is UNTOUCHED** — it has its own `/etc/nixos`. MEASURED there during the
    audit: `configuration.nix:370` carries
    `services.journald.extraConfig = "SyncIntervalSec=30s";` — the ONE-LINE double-quoted
    form, not a `''`-block — on `26.11pre1058091.ffb3c9b700e7`. So it will hit the same
    assertion, and the first implementation of the rewriter could not have migrated it.
- Pre-flight evidence gathered *before* the script was ever run, against a copy in the
  scratchpad with `/etc/nixos/*` symlinked in — no writes to `/etc/nixos`:
  `nix-instantiate '<nixpkgs/nixos>' -A config.system.build.toplevel` →
  `nixos-system-nixos-26.11pre1068949.dc5d91f84032.drv` (assertion gone), and building
  `config.environment.etc."systemd/journald.conf"` showed `SystemMaxUse=2G` survives.
- **GATE: BOTH TIERS PASS** on the merged tree `53d8b962` (branch merged with `origin/main`
  at `efa9a0fc`) — the tree that became the squash. Dev-host `gate.sh --tier both`: pytest
  `collected=21164 passed=21162 skipped=2 failed=0` (floor 20342), node `1449/1449`,
  `GATE: RESULT=PASS`. Sandbox `nix build .#checks.x86_64-linux.{pytests,nodetests}`, run ONE
  AT A TIME: both `RESULT: PASS (exit=0)`, 0 timeout panics. That sandbox tier is what Tekton
  gates on and had never run on this branch until then.
- **AUDIT: 5 rounds, `/audit-pr 1412`, all findings fixed or recorded open.** Four blockers,
  and THREE of them were introduced by the previous round's fix — each a safety mechanism
  producing the confusion it existed to prevent. Ladder stopped on the prose-payload
  criterion (payload trend 489 → 148 → 90 → 42), stated in commit `4bd146a3`.
- **No clawgate task.** `clawgate_handoff.sh resolve` → rc 5, positive control green
  (2 links for another session), so the board was genuinely read — but a wrong session id
  also answers 200 with an empty array, so this is not a clean bill of health and no
  `clawgate-task:` field is recorded.
- No claims held (`claim-work`).

## Next steps (ranked)
1. **Get ONE clean run of `scripts/diagnose-nix-disk.sh` on an idle box and put the real
   runtime in its header.** The only measurement is 4h05m to reach section 3 of 10, at 3%
   CPU under gate load, ended by an artefact — a floor, not a runtime. Nobody has ever seen
   the script finish, so its own "all 10 sections attempted" banner is untested end to end.
   forcing: none — but it is the last unverified claim shipped by this effort.
2. **RESOLVED — no action.** This list previously ranked "unblock the base clone", predicting
   `merge --ff-only` would refuse there because two untracked nebula scripts sat at paths
   `main` now tracks. MEASURED afterwards: the base clone is at `origin/main`, `--ff-only`
   says `Already up to date`, and both files are tracked and byte-identical to `main`
   (`ec9c6363`, `e061d844`). Another session cleared it mid-run. forcing: none
3. **`/audit-pr 1412`** before merging — offered to the operator, not yet answered. forcing: none
4. **Two deprecated-option renames in `/etc/nixos/configuration.nix`** — WARNING-ONLY, they
   still work (`mkRenamedOptionModule`). Lines 361-362, exactly:
   ```nix
   services.gnome.tracker.enable = true;         ->  services.gnome.tinysparql.enable = true;
   services.gnome.tracker-miners.enable = true;  ->  services.gnome.localsearch.enable = true;
   ```
   🔴 **Deliberately NOT given a `nix/system/apply-*.sh` script, and that is a judgement to
   re-make, not a gap to fill.** The journald migration's rebuild-and-rollback trap took FIVE
   audit rounds to get right — three of its four blockers were introduced by the previous
   round's own fix — and a second root-privileged mutator would ship a COPY of that trap.
   `claude/RULES.md` → "One rule, one place": a predicate duplicated across call sites is
   typically wrong at N−1 of them, in the same direction. Paying that for two warnings that
   change no behaviour is the wrong trade. If a THIRD such change appears, extract the trap
   into a shared `nix/system/lib/` helper first, with its own tests, and drive all of them
   through it — do not hand-copy it a second time.
   ⚠ Was listed as THREE renames; `services.dnsmasq.servers` is now commented out at
   `configuration.nix:71`, so it is not live. The original list came from an eval warning
   that was true when captured — `/etc/nixos` changed underneath it.
   forcing: none
5. **Apply the same journald migration to the laptop** when it next takes a channel update.
   ⚠ As of 2026-09-09 the laptop is UNREACHABLE on both nebula (10.42.0.100) and LAN, so its
   state could not be re-verified at close; the reading below is from earlier in that session.
   The rewriter now handles the laptop's one-line `"..."` form as well as the workbench's
   `''`-block (both are unit-tested), but it has only ever been RUN on the workbench —
   "the tests pass" and "it ran on that host" are different claims. forcing: none

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

## How to verify
```bash
# the migration is live on this host (all three are separate claims)
grep -A2 'services\.journald' /etc/nixos/configuration.nix
cat /etc/systemd/journald.conf                 # expect SystemMaxUse=2G
readlink -f /run/current-system                # expect nixos-system-nixos-26.11pre…

# the PR's file list (five: two ops scripts, the rewriter, its tests, this doc)
gh pr diff 1412 --name-only

# the rewriter's own suite (37 tests, incl. every audit-found edge case)
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_journald_migrate.py -q

# the base clone can still fast-forward (non-destructive: it advances or refuses)
git -C ~/workspace/devrc merge --ff-only origin/main
```
