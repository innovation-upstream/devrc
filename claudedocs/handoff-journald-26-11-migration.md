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
- Branch: `chore/commit-staged-system-scripts`, rebased onto `origin/main` `03d7e0ad`.
  (No head sha here on purpose — it moves every fix round; `gh pr view 1412 --json headRefOid`.)
- **PR #1412 OPEN** — https://github.com/innovation-upstream/devrc/pull/1412. Five files, all
  new, none modifying anything on `main` (checked with `git cat-file -e origin/main:<path>` —
  NOT `git ls-files`, see the gotcha below): the two ops scripts
  `nix/system/apply-journald-settings-migration.sh` and `scripts/diagnose-nix-disk.sh`, the
  extracted rewriter `scripts/lib/journald_migrate.py` and its suite
  `scripts/tests/test_journald_migrate.py` (25 tests), and this doc.
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
- **IN FLIGHT:** `gate.sh --tier both` on the rebased tree, background id `b8tu5owud`,
  output → `<scratchpad>/gate-rebased.txt`. Prior run on the pre-rebase base: node
  `1449/1449 PASS`; pytest `collected=21127 passed=21124 failed=1`, the one failure foreign
  (see below). The `nix build .#checks.x86_64-linux.{pytests,nodetests}` tier — the one
  Tekton gates on — has **NOT been run** for this branch.
- **No clawgate task.** `clawgate_handoff.sh resolve` → rc 5, positive control green
  (2 links for another session), so the board was genuinely read — but a wrong session id
  also answers 200 with an empty array, so this is not a clean bill of health and no
  `clawgate-task:` field is recorded.
- No claims held (`claim-work`).

## Next steps (ranked)
1. **Read the gate verdict for `b8tu5owud`** (`<scratchpad>/gate-rebased.txt`) and post it to
   PR #1412. If merging, also run `nix build .#checks.x86_64-linux.pytests` and
   `…nodetests` ONE AT A TIME — that tier has not been run on this branch, and it is the one
   Tekton gates on. `IN FLIGHT: devrc#1412`. forcing: gate — `main` is protected in name only
   (`required_status_checks` absent, measured 2026-09-02), so nothing else blocks this merge.
2. **RESOLVED — no action.** This list previously ranked "unblock the base clone", predicting
   `merge --ff-only` would refuse there because two untracked nebula scripts sat at paths
   `main` now tracks. MEASURED afterwards: the base clone is at `origin/main`, `--ff-only`
   says `Already up to date`, and both files are tracked and byte-identical to `main`
   (`ec9c6363`, `e061d844`). Another session cleared it mid-run. forcing: none
3. **`/audit-pr 1412`** before merging — offered to the operator, not yet answered. forcing: none
4. **Fix the three deprecated-option warnings** surfaced by the 26.11 eval of
   `/etc/nixos/configuration.nix`: `services.dnsmasq.servers` → `.settings.server`,
   `services.gnome.tracker.enable` → `.tinysparql.enable`,
   `services.gnome.tracker-miners.enable` → `.localsearch.enable`. They still work today.
   forcing: none
5. **Apply the same journald migration to the laptop** when it next takes a channel update.
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

# the PR carries only the two intended files
gh pr diff 1412 --name-only

# the rewriter's own suite (25 tests, incl. every audit-found edge case)
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_journald_migrate.py -q

# the base clone can still fast-forward (non-destructive: it advances or refuses)
git -C ~/workspace/devrc merge --ff-only origin/main
```
